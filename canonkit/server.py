"""대시보드 서버 — 읽기 전용.

127.0.0.1 에만 붙는다. GET 만 받는다. 어떤 요청도 파일을 쓰거나 모델을 부르지 않는다 —
새로고침·탭 전환이 비용·중복 쓰기를 만들지 않게 하는 것이 이 모듈의 경계다.
원문은 데이터 폴더의 journal/ 안 파일만, ID 형식 검사를 통과한 것만 돌려준다.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import VERSION, data as D, paths, schema, store
from .store import CanonError


def build_state(data):
    """화면에 필요한 모든 것을 읽어 모은다. 실패한 부분은 오류로 싣고 나머지는 보여 준다."""
    data = Path(data)
    st = {"version": VERSION, "errors": [], "generated": store.now()}
    try:
        st["meta"] = D.load(data, "meta.json")
    except CanonError as e:
        st["errors"].append({"code": e.code, "msg": e.message, "action": e.action})
        return st

    def safe(name, key, default):
        try:
            return D.load(data, name).get(key, default) if key else D.load(data, name)
        except CanonError as e:
            st["errors"].append({"code": e.code, "msg": e.message, "action": e.action})
            return default

    prof = safe("profile.json", None, {})
    st["profile"] = {k: {"label": f.get("label", k), "value": f.get("value"),
                         "status": schema.STATUS_KO.get(f.get("status"), f.get("status"))}
                     for k, f in (prof.get("fields") or {}).items()}
    st["questions"] = safe("questions.json", "questions", [])
    tabs_doc = safe("tabs.json", None, {})
    st["tabs_ok"] = bool(tabs_doc)
    st["tabs"] = tabs_doc.get("tabs", [])
    st["requests"] = tabs_doc.get("requests", [])
    st["candidates"] = safe("candidates.json", "candidates", [])
    st["blocks"] = schema.BLOCKS
    st["journal"] = [{"id": r["id"], "title": r["title"], "created": r["meta"].get("created", ""),
                      "question": r["meta"].get("question", ""), "broken": r.get("broken", False)}
                     for r in D.list_journal(data)]
    st["sheets"] = {}
    for q in st["questions"]:
        try:
            st["sheets"][q["id"]] = D.sheet_info(data, q["id"])
        except CanonError as e:
            st["sheets"][q["id"]] = {"exists": False, "error": e.message}
    runs = store.read_runs(data)
    st["runs"] = runs
    try:
        st["next"] = D.next_action(data)
    except CanonError as e:
        st["next"] = e.action or e.message
    st["has"] = D.availability(data)
    return st


def make_handler(data):
    web = paths.WEB

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):          # 콘솔·파일 로그를 남기지 않는다
            pass

        def _send(self, code, body, ctype):
            b = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(200, (web / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._json(200, build_state(data))
            if u.path == "/api/journal":
                jid = (parse_qs(u.query).get("id") or [""])[0]
                try:
                    r = D.get_journal(data, jid)
                except CanonError as e:
                    return self._json(404, {"code": e.code, "msg": e.message, "action": e.action})
                return self._json(200, {"id": r["id"], "title": r["title"], "meta": r["meta"], "body": r["body"]})
            return self._json(404, {"code": "NOT-FOUND", "msg": "없는 주소입니다."})

        def do_POST(self):
            self._json(405, {"code": "READ-ONLY", "msg": "대시보드는 읽기 전용입니다. 쓰기는 canon.py 명령으로 합니다."})

        do_PUT = do_DELETE = do_PATCH = do_POST

    return H


def serve(data, port):
    D.require_data(data)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Path(data)))
    except OSError:
        raise CanonError("PORT-BUSY", f"포트 {port} 을(를) 이미 쓰고 있습니다.",
                         f"이미 열린 대시보드가 있는지 보고, 아니면 --port {port + 1} 로 다시 실행하세요.")
    return httpd
