"""원문 뷰어 — 127.0.0.1 전용 로컬 HTTP (r3 T4 「원문 열기」).

GET  /raw/<raw_id>  원문 없는 확인 화면만(존재 여부도 드러내지 않음). 세션 쿠키를 심는다.
POST /raw/token     같은 출처 요청으로 (세션, raw_id) 에 묶인 60초 일회용 토큰 발급
POST /raw/open      Host·Origin 정확 일치 · 세션 · 토큰(원자적 소비) 확인 뒤 원문을 이스케이프해 표시
모든 응답: no-store · no-referrer · 프레임 차단 · CSP(외부 자원 없음). 접근 로그 없음.
요청에서 경로·URL·파일명을 받지 않는다. 원문 위치는 PC 대응표로만 찾는다.
"""
from __future__ import annotations

import html
import json
import secrets
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import contract

BIND_HOST = "127.0.0.1"
TOKEN_TTL = 60.0
SESSION_TTL = 3600.0
MAX_BODY = 4096
COOKIE = "mcrm_sid"


class TokenBook:
    """일회용 토큰 — (세션, raw_id) 에 묶임. 소비는 잠금 안에서 pop(원자적)."""

    def __init__(self, ttl=TOKEN_TTL, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._tokens: dict = {}
        self._sessions: dict = {}

    def new_session(self) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._gc()
            self._sessions[sid] = self.clock() + SESSION_TTL
        return sid

    def has_session(self, sid) -> bool:
        with self._lock:
            exp = self._sessions.get(sid or "")
            return exp is not None and exp > self.clock()

    def issue(self, sid: str, raw_id: str) -> str:
        tok = secrets.token_urlsafe(32)
        with self._lock:
            self._gc()
            self._tokens[tok] = (sid, raw_id, self.clock() + self.ttl)
        return tok

    def consume(self, tok: str, sid: str, raw_id: str) -> bool:
        with self._lock:
            ent = self._tokens.pop(tok or "", None)
        if ent is None:
            return False
        t_sid, t_raw, exp = ent
        return (secrets.compare_digest(t_sid, sid or "") and t_raw == raw_id and exp > self.clock())

    def _gc(self):
        now = self.clock()
        for k in [k for k, v in self._tokens.items() if v[2] <= now]:
            del self._tokens[k]
        for k in [k for k, v in self._sessions.items() if v <= now]:
            del self._sessions[k]


def _page(title: str, body_html: str, nonce: str, script: str = "") -> bytes:
    s = f"<script nonce=\"{nonce}\">{script}</script>" if script else ""
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{html.escape(title)}</title>"
        f"<style nonce=\"{nonce}\">body{{font:15px/1.6 system-ui,sans-serif;margin:24px;max-width:720px;"
        "color:#1d1d1f;background:#fff}@media(prefers-color-scheme:dark){body{color:#eee;background:#161616}}"
        "pre{white-space:pre-wrap;word-break:break-word;padding:12px;border:1px solid #8884;border-radius:6px}"
        "button{font:inherit;padding:8px 14px}dt{font-weight:600;margin-top:10px}</style>"
        f"</head><body>{body_html}{s}</body></html>"
    ).encode("utf-8")


_CONFIRM_JS = (
    "document.getElementById('open').addEventListener('click',async function(){"
    "this.disabled=true;var rid=document.body.dataset.rid;"
    "var r=await fetch('/raw/token',{method:'POST',credentials:'same-origin',"
    "headers:{'Content-Type':'application/json'},body:JSON.stringify({raw_id:rid})});"
    "if(!r.ok){document.getElementById('msg').textContent='열 수 없어요.';return;}"
    "var j=await r.json();var f=document.getElementById('f');"
    "f.elements.token.value=j.token;f.elements.raw_id.value=rid;f.submit();});"
)


def render_raw(raw: dict) -> str:
    """타인 원문은 전부 텍스트로 이스케이프. 링크·이미지·HTML 실행 없음."""
    parts = ["<h1>원문</h1><dl>"]
    row = raw if isinstance(raw, dict) else {"value": raw}
    for k in sorted(row.keys(), key=str):
        v = row[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, indent=2)
        parts.append(f"<dt>{html.escape(str(k))}</dt><dd><pre>{html.escape('' if v is None else str(v))}</pre></dd>")
    parts.append("</dl>")
    return "".join(parts)


def make_handler(*, resolve_raw, book: TokenBook):
    """resolve_raw(raw_id) -> dict (원문) 또는 예외. 호출 측이 대응표·경로 검사를 맡는다.
    기대 Host·Origin 은 실제 바인딩된 포트로 정한다."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "mcrm-viewer"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):    # 접근 로그 없음(토큰·경로·ID 미기록)
            return

        def log_error(self, format, *args):
            return

        def _send(self, status: int, body: bytes, ctype="text/html; charset=utf-8", nonce=None,
                  extra_headers=None, allow_script=False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            style = f"'nonce-{nonce}'" if nonce else "'none'"
            script = f"'nonce-{nonce}'" if (nonce and allow_script) else "'none'"
            connect = "'self'" if allow_script else "'none'"
            self.send_header("Content-Security-Policy",
                             f"default-src 'none'; script-src {script}; style-src {style}; connect-src {connect}; "
                             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; img-src 'none'")
            for k, v in (extra_headers or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _deny(self, status=403, msg="거절했어요."):
            n = secrets.token_urlsafe(12)
            self._send(status, _page("거절", f"<p>{html.escape(msg)}</p>", n), nonce=n)

        def _port(self) -> int:
            return self.server.server_address[1]

        def _host_ok(self) -> bool:
            return self.headers.get("Host") == f"{BIND_HOST}:{self._port()}"

        def _origin_ok(self) -> bool:
            return self.headers.get("Origin") == f"http://{BIND_HOST}:{self._port()}"

        def _sid(self):
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            try:
                c = cookies.SimpleCookie(raw)
            except cookies.CookieError:
                return None
            m = c.get(COOKIE)
            return m.value if m else None

        def _read_body(self):
            try:
                n = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return None
            if n < 0 or n > MAX_BODY:
                return None
            return self.rfile.read(n)

        def do_GET(self):
            if not self._host_ok():
                return self._deny(421, "잘못된 주소예요.")
            path = self.path.split("?", 1)[0]
            if not path.startswith("/raw/"):
                return self._deny(404, "없는 화면이에요.")
            rid = path[len("/raw/"):]
            if not contract.RAW_ID_RE.match(rid):
                return self._deny(404, "없는 화면이에요.")
            sid = self._sid()
            extra = []
            if not book.has_session(sid):
                sid = book.new_session()
                extra.append(("Set-Cookie", f"{COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/raw"))
            n = secrets.token_urlsafe(12)
            body = (
                "<h1>PC 원문 열기</h1>"
                "<p>이 PC 에 저장된 원문을 엽니다. 원문은 서버로 가지 않습니다.</p>"
                "<button id=\"open\" type=\"button\">원문 열기</button> <span id=\"msg\"></span>"
                "<form id=\"f\" method=\"post\" action=\"/raw/open\">"
                "<input type=\"hidden\" name=\"raw_id\"><input type=\"hidden\" name=\"token\"></form>"
            )
            page = _page("PC 원문 열기", body, n, _CONFIRM_JS).replace(
                b"<body>", f"<body data-rid=\"{rid}\">".encode("ascii"), 1)
            self._send(200, page, nonce=n, extra_headers=extra, allow_script=True)

        def do_POST(self):
            if not self._host_ok():
                return self._deny(421, "잘못된 주소예요.")
            if not self._origin_ok():
                return self._deny(403, "다른 곳에서 온 요청이에요.")
            sid = self._sid()
            if not book.has_session(sid):
                return self._deny(403, "세션이 없어요. 확인 화면부터 다시 여세요.")
            path = self.path.split("?", 1)[0]
            raw = self._read_body()
            if raw is None:
                return self._deny(413, "요청이 너무 커요.")
            if path == "/raw/token":
                if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                    return self._deny(415, "형식이 맞지 않아요.")
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    return self._deny(400, "형식이 맞지 않아요.")
                if not isinstance(obj, dict) or set(obj.keys()) != {"raw_id"} or \
                        not isinstance(obj["raw_id"], str) or not contract.RAW_ID_RE.match(obj["raw_id"]):
                    return self._deny(400, "형식이 맞지 않아요.")
                tok = book.issue(sid, obj["raw_id"])
                return self._send(200, json.dumps({"token": tok}).encode("utf-8"), "application/json")
            if path == "/raw/open":
                try:
                    form = parse_qs(raw.decode("ascii"), keep_blank_values=True, strict_parsing=True,
                                    max_num_fields=2)
                except (UnicodeDecodeError, ValueError):
                    return self._deny(400, "형식이 맞지 않아요.")
                if set(form.keys()) != {"raw_id", "token"} or any(len(v) != 1 for v in form.values()):
                    return self._deny(400, "형식이 맞지 않아요.")
                rid, tok = form["raw_id"][0], form["token"][0]
                if not contract.RAW_ID_RE.match(rid):
                    return self._deny(400, "형식이 맞지 않아요.")
                if not book.consume(tok, sid, rid):
                    return self._deny(403, "열기 확인이 만료됐어요. 확인 화면부터 다시 여세요.")
                try:
                    content = resolve_raw(rid)
                except Exception:
                    return self._deny(404, "이 PC 에서 원문을 찾지 못했어요.")
                n = secrets.token_urlsafe(12)
                return self._send(200, _page("원문", render_raw(content), n), nonce=n)
            return self._deny(404, "없는 화면이에요.")

        def do_PUT(self):
            self._deny(405, "허용하지 않아요.")

        do_DELETE = do_PATCH = do_PUT

        def do_OPTIONS(self):    # CORS 사전 요청에 허용 헤더를 주지 않는다
            self._deny(405, "허용하지 않아요.")

    return Handler


def make_server(*, port: int, resolve_raw, book: TokenBook | None = None) -> ThreadingHTTPServer:
    book = book or TokenBook()
    srv = ThreadingHTTPServer((BIND_HOST, port), make_handler(resolve_raw=resolve_raw, book=book))
    srv.daemon_threads = True
    srv.book = book
    return srv


def store_resolver(paths):
    """PC 대응표로 원문을 찾는다. 요청마다 새 연결(스레드 안전)."""
    from . import store as _store

    def resolve(raw_id: str):
        st = _store.Store(paths)
        try:
            real = st.resolve_raw_path(raw_id)
        finally:
            st.close()
        with open(real, "rb") as f:
            return json.loads(f.read().decode("utf-8"))

    return resolve
