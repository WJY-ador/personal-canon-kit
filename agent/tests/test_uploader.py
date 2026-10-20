"""업로드 — 가짜 /api/imports 서버로 멱등 재전송·응답 유실·충돌·인증·한도·서버 오류."""
from __future__ import annotations

import datetime as _dt
import json
import os
import unittest

from agent import contract, osutil, runner, store as _store
from agent.tests.helpers import (STREAM_ID, TOKEN, FakeImportServer, TempHome, connected_config,
                                 keystore_with_secrets, synth_row, write_jsonl)
from agent.uploader import Uploader, next_kst_midnight


class UploadFlowTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeImportServer()
        self.th = TempHome()
        self.path = os.path.join(self.th.root, "likes.jsonl")
        self.ks = keystore_with_secrets()
        write_jsonl(self.path, [synth_row(i) for i in range(1, 4)])
        connected_config(self.th, self.srv.url, self.path)

    def tearDown(self):
        self.srv.close()
        self.th.cleanup()

    def run(self, *a, **kw):  # noqa: D401 — unittest.TestCase.run 과 이름 충돌 피함
        return super().run(*a, **kw)

    def go(self, **kw):
        return runner.run_once(self.th.paths, ks=self.ks, **kw)

    def store(self):
        return _store.Store(self.th.paths)

    def test_headers_and_body(self):
        res = self.go()
        self.assertEqual(res["upload"]["accepted"], 3)
        for hdrs, body in self.srv.requests:
            self.assertEqual(hdrs["Authorization"], "Bearer " + TOKEN)
            self.assertEqual(hdrs["Content-Type"], "application/json")
            obj = contract.validate_bytes(body)
            self.assertEqual(hdrs["Idempotency-Key"],
                             contract.idempotency_key(STREAM_ID, obj["original_id"], obj["revision"]))
        self.assertEqual(os.listdir(self.th.paths.pending), [])
        st = self.store()
        c = st.counts()
        st.close()
        self.assertEqual((c["accepted"], c["pending"]), (3, 0))

    def test_rerun_sends_nothing(self):
        self.go()
        n = len(self.srv.requests)
        res = self.go()
        self.assertEqual(len(self.srv.requests), n)
        self.assertEqual(res["upload"]["sent"], 0)

    def test_dropped_response_then_same_bytes_retry(self):
        self.srv.script = ["drop-after-apply"]
        res = self.go()
        self.assertEqual(res["upload"]["stopped"], "network")
        first_hdrs, first_body = self.srv.requests[0]
        # 다음 실행: 지수 대기 시각을 지나게 만든 뒤 재전송
        st = self.store()
        st.conn.execute("UPDATE revisions SET next_attempt_at=NULL")
        st.close()
        res = self.go()
        hdrs, body = self.srv.requests[1]
        self.assertEqual(body, first_body)                       # 같은 바이트
        self.assertEqual(hdrs["Idempotency-Key"], first_hdrs["Idempotency-Key"])
        self.assertEqual(res["upload"]["duplicate"], 1)           # 서버는 200 + 기존 접수
        self.assertEqual(res["upload"]["accepted"], 3)
        self.assertEqual(len(self.srv.by_key), 3)                 # 서버 쪽 접수 3건(중복 없음)

    def test_backoff_defers(self):
        self.srv.script = [(503, {"code": "IMPORTS-OFF"})]
        res = self.go()
        self.assertEqual(res["upload"]["stopped"], "server")
        res = self.go()
        # 실패한 판만 지수 대기, 나머지는 다음 실행에 보낸다
        self.assertEqual(res["upload"]["deferred"], 1)
        self.assertEqual(res["upload"]["accepted"], 2)

    def test_idempotency_conflict_quarantined(self):
        self.srv.script = [(409, {"code": "IDEMPOTENCY-CONFLICT"})]
        res = self.go()
        self.assertEqual(res["upload"]["conflict"], 1)
        st = self.store()
        self.assertEqual(st.counts()["conflict"], 1)
        self.assertGreaterEqual(st.counts()["quarantined"], 1)
        st.close()

    def test_source_revision_conflict_halts_original(self):
        self.srv.script = [(409, {"code": "SOURCE-REVISION-CONFLICT"})]
        res = self.go()
        self.assertEqual(res["upload"]["halted"], 1)
        st = self.store()
        self.assertEqual(st.counts()["halted"], 1)
        st.close()
        self.assertEqual(res["upload"]["accepted"], 2)

    def test_auth_halts_everything(self):
        self.srv.token = "imp_" + STREAM_ID + ".other.XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
        res = self.go()
        self.assertEqual(res["upload"]["stopped"], "auth")
        self.assertEqual(len(self.srv.requests), 1)
        res = self.go()
        self.assertEqual(res["upload"]["stopped"], "auth-halted")
        self.assertEqual(len(self.srv.requests), 1)
        st = self.store()
        self.assertEqual(st.counts()["pending"], 3)
        st.clear_halt("auth")
        st.close()
        self.srv.token = TOKEN
        res = self.go()
        self.assertEqual(res["upload"]["accepted"], 3)

    def test_rate_limit_and_daily_limit(self):
        self.srv.script = [(429, {"code": "RATE-LIMIT"})]
        res = self.go()
        self.assertEqual(res["upload"]["stopped"], "rate-limit")
        st = self.store()
        nxt = st.pending_revisions()[0]["next_attempt_at"]
        st.conn.execute("UPDATE revisions SET next_attempt_at=NULL")
        st.close()
        self.assertIsNotNone(nxt)
        self.srv.script = [(429, {"code": "IMPORT-QUOTA"})]
        self.go()
        st = self.store()
        nxt = _dt.datetime.fromisoformat(st.pending_revisions()[0]["next_attempt_at"])
        st.close()
        self.assertEqual((nxt.hour, nxt.minute), (0, 0))
        self.assertEqual(nxt.utcoffset(), _dt.timedelta(hours=9))

    def test_server_codes_map_to_halt_scopes(self):
        for code, status, scope in (("NAME-REQUIRED", 403, "account"), ("STREAM-MISMATCH", 403, "config"),
                                    ("POLICY-STALE", 409, "policy"), ("AUTH-BLOCKED", 403, "auth"),
                                    ("DEVICE-REVOKED", 409, "auth"), ("SOMETHING-NEW", 403, "forbidden")):
            with self.subTest(code):
                self.srv.script = [(status, {"code": code})]
                res = self.go()
                self.assertEqual(res["upload"]["stopped"], scope)
                res = self.go()
                self.assertEqual(res["upload"]["stopped"], scope + "-halted")
                st = self.store()
                self.assertTrue(st.clear_halt(scope))
                st.close()

    def test_policy_denied_and_kind_changed(self):
        self.srv.script = [(403, {"code": "POLICY-DENIED"}), (409, {"code": "KIND-CHANGED"})]
        res = self.go()
        self.assertEqual((res["upload"]["rejected"], res["upload"]["conflict"], res["upload"]["accepted"]), (1, 1, 1))

    def test_people_quota_uses_resume_at(self):
        ra = (osutil.kst_now() + _dt.timedelta(hours=5)).replace(microsecond=0)
        self.srv.script = [(429, {"code": "PEOPLE-QUOTA", "msg": "x", "resume_at": ra.isoformat()})]
        res = self.go()
        self.assertEqual(res["upload"]["stopped"], "rate-limit")
        st = self.store()
        nxt = _dt.datetime.fromisoformat(st.pending_revisions()[0]["next_attempt_at"])
        st.close()
        self.assertEqual(nxt, ra)

    def test_bad_resume_at_falls_back_to_midnight(self):
        from agent.uploader import resume_at_or_midnight
        now = _dt.datetime(2026, 10, 20, 15, 0, tzinfo=osutil.KST)
        mid = _dt.datetime(2026, 10, 21, 0, 0, tzinfo=osutil.KST)
        for bad in ({}, {"resume_at": "soon"}, {"resume_at": "2026-10-20T10:00:00+09:00"},
                    {"resume_at": "2026-10-30T00:00:00+09:00"}, {"resume_at": "2026-10-21T00:00:00"}):
            self.assertEqual(resume_at_or_midnight(bad, now), mid, bad)

    def test_inbox_full_waits_an_hour(self):
        self.srv.script = [(429, {"code": "INBOX-FULL"})]
        before = osutil.kst_now()
        self.go()
        st = self.store()
        nxt = _dt.datetime.fromisoformat(st.pending_revisions()[0]["next_attempt_at"])
        st.close()
        self.assertGreaterEqual((nxt - before).total_seconds(), 3500)

    def test_rejected_quarantined(self):
        self.srv.script = [(400, {"code": "CONTRACT-INVALID"})]
        res = self.go()
        self.assertEqual(res["upload"]["rejected"], 1)

    def test_request_body_never_logged_in_errors(self):
        self.srv.script = [(500, {"code": "X"})]
        self.go()
        st = self.store()
        errs = [r["last_error"] for r in st.conn.execute("SELECT last_error FROM revisions")]
        st.close()
        for e in errs:
            if e:
                self.assertRegex(e, r"^[a-z0-9:-]+$", e)


class UploaderUnitTest(unittest.TestCase):
    def test_https_only(self):
        from agent import config as _config
        _config.check_server_url("https://crm.example.invalid")
        _config.check_server_url("http://127.0.0.1:8000")
        for bad in ("http://crm.example.invalid", "https://u:p@x.invalid", "https://x.invalid/api",
                    "ftp://x.invalid", "https://x.invalid?a=1"):
            with self.assertRaises(_config.ConfigError, msg=bad):
                _config.check_server_url(bad)

    def test_repr_hides_token(self):
        self.assertNotIn(TOKEN, repr(Uploader("https://x.invalid", TOKEN)))

    def test_next_midnight_kst(self):
        t = _dt.datetime(2026, 10, 20, 23, 59, tzinfo=osutil.KST)
        self.assertEqual(next_kst_midnight(t), _dt.datetime(2026, 10, 21, 0, 0, tzinfo=osutil.KST))
        u = _dt.datetime(2026, 10, 20, 15, 30, tzinfo=_dt.timezone.utc)   # = 10-21 00:30 KST
        self.assertEqual(next_kst_midnight(u), _dt.datetime(2026, 10, 22, 0, 0, tzinfo=osutil.KST))

    def test_no_redirect_follow(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            up = Uploader(f"http://127.0.0.1:{httpd.server_address[1]}", TOKEN)
            status, _, _ = up.post("ci1_" + "a" * 64, b"{}")
            self.assertEqual(status, 307)
            self.assertEqual(seen, ["/api/imports"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
