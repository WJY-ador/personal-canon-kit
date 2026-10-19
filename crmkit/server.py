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
from .store import CrmError


def _err(e):
    return {"code": e.code, "msg": e.message, "action": e.action}


def _excerpt(r, n=60):
    """목록용 첫 줄 — 제목·양식 절을 뺀 본문의 첫 문장 앞부분."""
    for line in str(r.get("body") or "").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or t.startswith("-"):
            continue
        if t.startswith("## 이게 어디에 붙나"):
            break
        return t[:n] + ("…" if len(t) > n else "")
    return ""


def build_state(data):
    """화면에 필요한 모든 것을 읽어 모은다.

    읽기 결과는 부분마다 st["read"][이름] = {"ok": True} 또는 {"ok": False, "error": {code, msg, action}} 로 싣는다.
    「0개」와 「못 읽음」을 화면이 가를 수 있게, 실패한 부분을 빈 값으로 숨기지 않는다.
    값 키(questions·tabs 등)는 실패해도 빈 값으로 두어 화면 코드가 깨지지 않게 하되, 판정은 read 로 한다.
    errors 는 실패 목록 전체(이전 판과 같은 모양)다.
    """
    data = Path(data)
    st = {"version": VERSION, "errors": [], "generated": store.now(), "read": {}}

    def fail(part, e):
        er = _err(e)
        st["read"][part] = {"ok": False, "error": er}
        st["errors"].append(dict(er, part=part))

    try:
        st["meta"] = D.load(data, "meta.json")
        st["read"]["meta"] = {"ok": True}
    except CrmError as e:
        fail("meta", e)
        return st

    def part(part_name, name, key, default):
        try:
            d = D.load(data, name)
        except CrmError as e:
            fail(part_name, e)
            return default
        st["read"][part_name] = {"ok": True}
        return d.get(key, default) if key else d

    prof = part("profile", "profile.json", None, {})
    st["profile"] = {k: {"label": f.get("label", k), "value": f.get("value"),
                         "status": schema.STATUS_KO.get(f.get("status"), f.get("status"))}
                     for k, f in (prof.get("fields") or {}).items()}
    st["questions"] = part("questions", "questions.json", "questions", [])
    tabs_doc = part("tabs", "tabs.json", None, {})
    st["tabs_ok"] = st["read"]["tabs"]["ok"]
    st["tabs"] = tabs_doc.get("tabs", [])
    st["requests"] = tabs_doc.get("requests", [])
    st["candidates"] = part("candidates", "candidates.json", "candidates", [])
    st["blocks"] = schema.BLOCKS
    try:
        st["journal"] = [{"id": r["id"], "title": r["title"], "created": r["meta"].get("created", ""),
                          "question": r["meta"].get("question", ""), "broken": r.get("broken", False),
                          "excerpt": _excerpt(r)}
                         for r in D.list_journal(data)]
        st["read"]["journal"] = {"ok": True}
    except OSError as e:
        st["journal"] = []
        fail("journal", CrmError("JOURNAL-UNREADABLE", f"기록 폴더를 읽을 수 없습니다 ({e.__class__.__name__})",
                                   "python3 crm.py doctor 로 확인하세요."))
    st["sheets"] = {}
    if st["read"]["questions"]["ok"]:
        for q in st["questions"]:
            try:
                st["sheets"][q["id"]] = dict(D.sheet_info(data, q["id"]), ok=True)
            except CrmError as e:
                st["sheets"][q["id"]] = {"ok": False, "exists": False, "error": _err(e)}
    # 실행 상태: 파일이 없으면 "아직 실행 안 함"(정상), 있는데 못 읽으면 실패.
    rp = store.runs_path(data)
    st["runs"] = {}
    if rp.exists():
        try:
            st["runs"] = json.loads(rp.read_text(encoding="utf-8"))
            st["read"]["runs"] = {"ok": True}
        except (OSError, ValueError):
            fail("runs", CrmError("FILE-CORRUPT", f"{rp.name} 을(를) 읽을 수 없습니다",
                                    "python3 crm.py doctor 로 확인하세요."))
    else:
        st["read"]["runs"] = {"ok": True}
    try:
        st["next"] = D.next_action(data)
    except CrmError as e:
        st["next"] = {"kind": "blocked", "target": None,
                      "text": "읽지 못한 부분이 있어 다음 할 일을 정하지 못했어요. 그 부분부터 고쳐요.",
                      "command": "python3 crm.py doctor", "error": _err(e)}
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
                except CrmError as e:
                    return self._json(404, {"code": e.code, "msg": e.message, "action": e.action})
                return self._json(200, {"id": r["id"], "title": r["title"], "meta": r["meta"], "body": r["body"]})
            return self._json(404, {"code": "NOT-FOUND", "msg": "없는 주소입니다."})

        def do_POST(self):
            self._json(405, {"code": "READ-ONLY", "msg": "대시보드는 읽기 전용입니다. 쓰기는 crm.py 명령으로 합니다."})

        do_PUT = do_DELETE = do_PATCH = do_POST

    return H


def serve(data, port):
    D.require_data(data)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Path(data)))
    except OSError:
        raise CrmError("PORT-BUSY", f"포트 {port} 을(를) 이미 쓰고 있습니다.",
                         f"이미 열린 대시보드가 있는지 보고, 아니면 --port {port + 1} 로 다시 실행하세요.")
    return httpd
