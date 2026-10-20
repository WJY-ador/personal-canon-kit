"""원문 뷰어 localhost 방어 — 외부·누락·null Origin · 토큰 재사용·만료·세션 불일치 · Host · 경로 탈출·심링크
· HTML 이스케이프 · 보안 헤더 · 접근 로그 없음 · 127.0.0.1 바인딩."""
from __future__ import annotations

import http.client
import io
import json
import os
import re
import sys
import threading
import unittest
from contextlib import redirect_stderr

from agent import store as _store, viewer
from agent.tests.helpers import TempHome

EVIL = "<script>alert(1)</script><img src=x onerror=alert(2)><iframe src='https://evil.invalid'></iframe>"


class ViewerTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()
        self.st = _store.Store(self.th.paths)
        ev = self.st.event_create("likes", "synthnet:P1", "activity", "fp",
                                  raw_obj={"text": EVIL, "original_url": "https://example.invalid/post/P1"})
        self.raw_id = ev["raw_id"]
        self.clock = [1000.0]
        self.book = viewer.TokenBook(clock=lambda: self.clock[0])
        self.srv = viewer.make_server(port=0, resolve_raw=viewer.store_resolver(self.th.paths), book=self.book)
        self.port = self.srv.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.origin = f"http://127.0.0.1:{self.port}"
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.st.close()
        self.th.cleanup()

    # ---- 저수준 요청 ----
    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": self.host}
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            c.putheader(k, v)
        data = body.encode() if isinstance(body, str) else body
        if data is not None:
            c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def session(self):
        st, hdrs, body = self.req("GET", f"/raw/{self.raw_id}")
        self.assertEqual(st, 200)
        m = re.search(r"mcrm_sid=([^;]+)", hdrs.get("Set-Cookie", ""))
        self.assertIsNotNone(m)
        return m.group(1), body

    def token(self, sid, raw_id=None, origin="default"):
        o = self.origin if origin == "default" else origin
        return self.req("POST", "/raw/token", json.dumps({"raw_id": raw_id or self.raw_id}),
                        {"Origin": o, "Cookie": f"mcrm_sid={sid}", "Content-Type": "application/json"})

    def open(self, sid, tok, raw_id=None, origin="default", host=None):
        o = self.origin if origin == "default" else origin
        h = {"Origin": o, "Cookie": f"mcrm_sid={sid}", "Content-Type": "application/x-www-form-urlencoded"}
        if host:
            h["Host"] = host
        return self.req("POST", "/raw/open", f"raw_id={raw_id or self.raw_id}&token={tok}", h)

    def good_token(self, sid):
        st, _, body = self.token(sid)
        self.assertEqual(st, 200)
        return json.loads(body)["token"]

    # ---- 시험 ----
    def test_bound_to_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertEqual(viewer.BIND_HOST, "127.0.0.1")

    def test_confirm_page_has_no_raw(self):
        sid, body = self.session()
        self.assertNotIn(b"alert", body)
        self.assertNotIn(b"example.invalid", body)
        # 없는 ID 도 같은 확인 화면(존재 여부를 드러내지 않음)
        st, _, body2 = self.req("GET", "/raw/" + "f" * 48)
        self.assertEqual(st, 200)
        self.assertEqual(len(body2) > 0, True)
        self.assertNotIn(b"alert", body2)

    def test_full_flow_escapes_html(self):
        sid, _ = self.session()
        tok = self.good_token(sid)
        st, hdrs, body = self.open(sid, tok)
        self.assertEqual(st, 200)
        self.assertNotIn(b"<script>alert", body)
        self.assertNotIn(b"<img src=x", body)
        self.assertNotIn(b"<iframe", body)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn(b"<a ", body)
        self.assertEqual(hdrs["Cache-Control"], "no-store")
        self.assertEqual(hdrs["Referrer-Policy"], "no-referrer")
        self.assertEqual(hdrs["X-Frame-Options"], "DENY")
        csp = hdrs["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'none'", csp)       # 원문 화면엔 스크립트 없음
        self.assertNotIn("Access-Control-Allow-Origin", hdrs)

    def test_token_single_use(self):
        sid, _ = self.session()
        tok = self.good_token(sid)
        self.assertEqual(self.open(sid, tok)[0], 200)
        self.assertEqual(self.open(sid, tok)[0], 403)

    def test_token_expires_60s(self):
        sid, _ = self.session()
        tok = self.good_token(sid)
        self.clock[0] += 61
        self.assertEqual(self.open(sid, tok)[0], 403)

    def test_token_bound_to_session_and_raw(self):
        sid, _ = self.session()
        sid2, _ = self.session()
        tok = self.good_token(sid)
        self.assertEqual(self.open(sid2, tok)[0], 403)        # 다른 세션
        tok = self.good_token(sid)
        other = self.st.event_create("likes", "synthnet:P2", "activity", "fp", raw_obj={"text": "x"})["raw_id"]
        self.assertEqual(self.open(sid, tok, raw_id=other)[0], 403)   # 다른 원문

    def test_external_missing_null_origin(self):
        sid, _ = self.session()
        for o in ("https://evil.invalid", None, "null", f"http://localhost:{self.port}",
                  f"http://127.0.0.1:{self.port + 1}", self.origin + "/"):
            self.assertEqual(self.token(sid, origin=o)[0], 403, o)
            tok = self.good_token(sid)
            self.assertEqual(self.open(sid, tok, origin=o)[0], 403, o)

    def test_wrong_host_rejected(self):
        st, _, _ = self.req("GET", f"/raw/{self.raw_id}", headers={"Host": f"evil.invalid:{self.port}"})
        self.assertEqual(st, 421)
        sid, _ = self.session()
        tok = self.good_token(sid)
        self.assertEqual(self.open(sid, tok, host=f"localhost:{self.port}")[0], 421)

    def test_no_session_no_token(self):
        self.assertEqual(self.token("forged-session")[0], 403)

    def test_token_endpoint_requires_json(self):
        sid, _ = self.session()
        st, _, _ = self.req("POST", "/raw/token", f"raw_id={self.raw_id}",
                            {"Origin": self.origin, "Cookie": f"mcrm_sid={sid}",
                             "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 415)

    def test_request_cannot_carry_path_or_url(self):
        sid, _ = self.session()
        tok = self.good_token(sid)
        h = {"Origin": self.origin, "Cookie": f"mcrm_sid={sid}"}
        for body in (f"raw_id={self.raw_id}&token={tok}&path=/etc/passwd",
                     f"raw_id=../../etc/passwd&token={tok}",
                     f"raw_id={self.raw_id}&raw_id={self.raw_id}&token={tok}"):
            self.assertEqual(self.req("POST", "/raw/open", body, h)[0], 400, body)
        self.assertEqual(self.req("GET", "/raw/../../etc/passwd")[0], 404)
        self.assertEqual(self.req("GET", "/raw/%2e%2e%2fetc")[0], 404)

    def test_path_escape_in_map_refused(self):
        self.st.conn.execute("UPDATE events SET raw_path=? WHERE raw_id=?", ("../config.json", self.raw_id))
        sid, _ = self.session()
        tok = self.good_token(sid)
        st, _, body = self.open(sid, tok)
        self.assertEqual(st, 404)
        self.assertNotIn(b"config_version", body)

    @unittest.skipIf(sys.platform.startswith("win"), "symlink")
    def test_symlink_escape_refused(self):
        outside = os.path.join(self.th.root, "outside.json")
        with open(outside, "w") as f:
            json.dump({"text": "OUTSIDE-SECRET"}, f)
        p = os.path.join(self.th.paths.raw, self.raw_id + ".json")
        os.unlink(p)
        os.symlink(outside, p)
        sid, _ = self.session()
        tok = self.good_token(sid)
        st, _, body = self.open(sid, tok)
        self.assertEqual(st, 404)
        self.assertNotIn(b"OUTSIDE-SECRET", body)

    def test_options_and_other_methods(self):
        self.assertEqual(self.req("OPTIONS", "/raw/open", headers={"Origin": "https://evil.invalid"})[0], 405)
        self.assertEqual(self.req("PUT", "/raw/open", b"")[0], 405)

    def test_no_access_log(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            sid, _ = self.session()
            tok = self.good_token(sid)
            self.open(sid, tok)
            self.req("GET", "/nope")
        out = buf.getvalue()
        self.assertNotIn(self.raw_id, out)
        self.assertNotIn(tok, out)
        self.assertEqual(out, "")

    def test_cookie_flags(self):
        _, hdrs, _ = self.req("GET", f"/raw/{self.raw_id}")
        c = hdrs["Set-Cookie"]
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Strict", c)


if __name__ == "__main__":
    unittest.main()
