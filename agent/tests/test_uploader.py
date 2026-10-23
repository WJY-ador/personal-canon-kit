"""업로드 — 가짜 /api/imports 서버로 멱등 재전송·응답 유실·충돌·인증·한도·서버 오류."""
from __future__ import annotations

import datetime as _dt
import json
import os
import unittest

from agent import contract, osutil, runner, store as _store
from agent.tests.helpers import (STREAM_ID, TOKEN, FakeImportServer, TempHome, connected_config,
                                 keystore_with_secrets, run_cli, synth_row, write_jsonl)
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

    # ---- 서버에서 삭제(휴지통·영구 삭제)한 원본: 200 + state=deleted ----
    # 서버 꼴: 기존 접수 응답 키 + source_deleted(trash|purged). target 은 항상 null, import_id 는 null 일 수 있다
    DELETED = (200, {"import_id": None, "state": "deleted", "target_kind": None, "target_id": None, "duplicate": False,
                     "received_at": None, "expires_at": None, "source_deleted": "trash"})

    def _deleted_rows(self, st):
        return st.conn.execute("SELECT * FROM revisions WHERE server_state IN ('deleted','purged')").fetchall()

    def test_deleted_response_mixed_batch(self):
        # 첫 전송만 삭제됨 — 그 전송은 끝나고, 같은 실행의 다른 전송은 계속 접수된다
        self.srv.script = [self.DELETED]
        res = self.go()
        up = res["upload"]
        self.assertEqual((up["deleted"], up["accepted"], up["duplicate"], up["stopped"]), (1, 2, 0, None))
        self.assertEqual((up["rejected"], up["conflict"], up["halted"]), (0, 0, 0))
        self.assertEqual(os.listdir(self.th.paths.pending), [])
        st = self.store()
        c = st.counts()
        rows = self._deleted_rows(st)
        st.close()
        self.assertEqual((c["accepted"], c["deleted"], c["pending"], c["halted"], c["quarantined"]), (2, 1, 0, 0, 0))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["status"], "accepted")
        self.assertIsNone(r["import_id"])
        self.assertIsNone(r["target_id"])          # 삭제 대상을 활성 ID 로 쓰지 않는다
        self.assertIsNone(r["last_error"])
        self.assertIsNone(r["next_attempt_at"])
        code, out = run_cli(["--home", self.th.home, "status"], ks_factory=lambda service: self.ks,
                            launch_agents_dir=os.path.join(self.th.root, "LaunchAgents"))
        self.assertEqual(code, 0, out)
        self.assertIn("접수 2", out)
        self.assertIn("삭제됨 1", out)                # 수만 — 본문·식별자 없음
        self.assertNotIn("SYNPOST", out)

    def _change(self, oid):
        # 원본 내용이 바뀐 것처럼 — 마지막 판의 지문만 바꾼다(다음 수집이 새 개정을 만든다)
        st = self.store()
        st.conn.execute("UPDATE revisions SET projection_sha='changed' WHERE original_id=?", (oid,))
        st.close()

    def _revs(self, oid):
        st = self.store()
        rows = st.conn.execute("SELECT * FROM revisions WHERE original_id=? ORDER BY seq", (oid,)).fetchall()
        c = st.counts()
        st.close()
        return rows, c

    def test_deleted_same_revision_never_resent(self):
        self.srv.script = [self.DELETED]
        self.go()
        n = len(self.srv.requests)
        st = self.store()
        oid = self._deleted_rows(st)[0]["original_id"]
        raw_id = st.conn.execute("SELECT raw_id FROM events WHERE original_id=?", (oid,)).fetchone()[0]
        st.close()
        res = self.go()                                   # 같은 내용 = 다시 보내지 않는다
        self.assertEqual((res["upload"]["sent"], res["collect"]["staged"], len(self.srv.requests)), (0, 0, n))
        st = self.store()
        self.assertTrue(os.path.isfile(st.resolve_raw_path(raw_id)))   # PC 원문은 지우지 않는다
        st.close()
        rows, c = self._revs(oid)
        self.assertEqual(len(rows), 1)
        self.assertEqual((c["deleted"], c["pending"], c["halted"], c["rejected"]), (1, 0, 0, 0))

    def test_deleted_then_changed_still_deleted_then_restored(self):
        self.srv.script = [self.DELETED]
        self.go()
        st = self.store()
        oid = self._deleted_rows(st)[0]["original_id"]
        st.close()
        # 휴지통에 있는 동안 내용이 바뀜 → 새 개정을 보낸다. 이전 판 = 삭제 응답을 받은 판(서버가 그 판을 기록한다)
        self._change(oid)
        self.srv.script = [self.DELETED]
        res = self.go()
        self.assertEqual((res["collect"]["staged"], res["upload"]["deleted"], res["upload"]["stopped"]), (1, 1, None))
        rows, c = self._revs(oid)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["previous_revision"], rows[0]["revision"])
        self.assertEqual([r["server_state"] for r in rows], ["deleted", "deleted"])
        self.assertEqual((c["deleted"], c["halted"], c["quarantined"]), (1, 0, 0))   # 원본 수 — 개정 수 아님
        sent = json.loads(self.srv.requests[-1][1])
        self.assertEqual(sent["previous_revision"], rows[0]["revision"])
        # 서버에서 복원 → 다음 변경은 정상 접수, 「삭제됨」 이 풀린다
        self._change(oid)
        res = self.go()
        self.assertEqual((res["collect"]["staged"], res["upload"]["accepted"], res["upload"]["deleted"]), (1, 1, 0))
        rows, c = self._revs(oid)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["previous_revision"], rows[1]["revision"])
        self.assertEqual(rows[2]["server_state"], "applied")
        self.assertEqual((c["deleted"], c["accepted"]), (0, 3))

    def test_old_server_responses_unchanged(self):
        # 삭제 상태가 없는 이전 서버 응답(applied·noop·pending, duplicate) — 기존 동작 그대로
        self.srv.script = [(200, {"import_id": "imp_1", "state": "noop", "target_kind": "activity",
                                  "target_id": "a_1", "duplicate": True}),
                           (201, {"import_id": "imp_2", "state": "pending", "target_kind": None,
                                  "target_id": None, "duplicate": False})]
        res = self.go()
        up = res["upload"]
        self.assertEqual((up["accepted"], up["duplicate"], up["deleted"]), (3, 1, 0))
        st = self.store()
        c = st.counts()
        states = sorted(r[0] or "" for r in st.conn.execute("SELECT server_state FROM revisions"))
        noop = st.conn.execute("SELECT * FROM revisions WHERE server_state='noop'").fetchone()
        st.close()
        self.assertEqual((c["accepted"], c["deleted"]), (3, 0))
        self.assertNotIn("deleted", states)
        self.assertIn("noop", states)
        self.assertIn("pending", states)
        self.assertEqual((noop["import_id"], noop["target_id"]), ("imp_1", "a_1"))

    def test_deleted_duplicate_and_purged(self):
        # 같은 개정 재전송(duplicate=true, 대응표 id 있음)·영구 삭제 표식(purged) — 둘 다 삭제됨, duplicate 로 세지 않는다
        self.srv.script = [(200, {"import_id": "imp_1", "state": "deleted", "target_kind": None, "target_id": None,
                                  "duplicate": True, "received_at": None, "expires_at": None, "source_deleted": "trash"}),
                           (200, {"import_id": None, "state": "deleted", "target_kind": None, "target_id": None,
                                  "duplicate": False, "received_at": None, "expires_at": None, "source_deleted": "purged"})]
        res = self.go()
        up = res["upload"]
        self.assertEqual((up["deleted"], up["accepted"], up["duplicate"]), (2, 1, 0))
        st = self.store()
        rows = self._deleted_rows(st)
        c = st.counts()
        st.close()
        self.assertEqual(sorted((r["import_id"], r["target_id"]) for r in rows), [(None, None), (None, None)])
        self.assertEqual(sorted(r["server_state"] for r in rows), ["deleted", "purged"])   # 휴지통 · 영구 삭제 표식
        self.assertEqual((c["accepted"], c["deleted"]), (1, 2))

    def test_deleted_state_on_success_only(self):
        # 새 4xx 없음 — 삭제 상태는 성공 응답에서만 읽는다(201 이어도 같게). 오류 응답에 state 가 있어도 기존 분기
        self.srv.script = [(201, {"import_id": "imp_1", "state": "deleted"}),
                           (400, {"code": "CONTRACT-INVALID", "state": "deleted"})]
        res = self.go()
        up = res["upload"]
        self.assertEqual((up["deleted"], up["accepted"], up["rejected"]), (1, 1, 1))
        st = self.store()
        rows = self._deleted_rows(st)
        st.close()
        self.assertEqual([r["import_id"] for r in rows], [None])

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
