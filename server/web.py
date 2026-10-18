"""웹 화면 — AuthKit 로그인(OAuth 2.1 공개 클라이언트 + PKCE), 서명 쿠키 세션, 화면 API.

로그인 흐름: /login → AuthKit /oauth2/authorize (resource=<PUBLIC_URL>/mcp) → /auth/callback → 토큰 교환 →
받은 액세스 토큰을 MCP 와 같은 AuthKitVerifier·user_id_for 로 검증·연결 → 세션 쿠키.
웹 사용자 = MCP 사용자(같은 sub). 클라이언트는 AuthKit DCR 로 한 번 등록한 공개 클라이언트(AUTHKIT_WEB_CLIENT_ID).

상태는 서버 메모리에 두지 않는다(Render Free 는 유휴 때 죽는다). PKCE verifier·state 는 10분짜리 서명 쿠키, 세션은 30일 서명 쿠키.
환경변수: AUTHKIT_WEB_CLIENT_ID · CANON_SESSION_SECRET (둘 다 없으면 웹 로그인만 503, MCP 는 산다).
"""
import base64
import hashlib
import hmac
import html as _html
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import auth
import db

SESSION_COOKIE = "canon_session"
FLOW_COOKIE = "canon_login"
SESSION_TTL = 30 * 24 * 3600
FLOW_TTL = 600
APP_HTML = Path(__file__).parent / "app.html"
START_HTML = Path(__file__).parent / "start.html"
ACTIONS = ("confirm", "exclude", "pending", "edit", "edit_confirm")
# 도움말 정본은 레포의 guides/. 웹 /help 와 MCP help_get 이 같은 것을 읽는다. CANON_GUIDES_DIR 가 있으면 그 폴더.
# Render 는 레포 루트에서 빌드·실행한다(render.yaml 에 rootDir 없음) — rootDir=server 면 guides/ 가 안 올라간다.
GUIDES_DIR = Path(os.environ.get("CANON_GUIDES_DIR") or Path(__file__).parent.parent / "guides")
HELP_FILES = {"start": "09-hosted-start.md", "words": "10-words.md"}
_HELP_ANCHORS = {**{f: f"#{k}" for k, f in HELP_FILES.items()}, "11-privacy.md": "/privacy"}
# 개인정보 처리방침 /privacy — 정본은 guides/11-privacy.md. help_get(MCP)에는 넣지 않는다(HELP_FILES 밖).
# 문안의 {{CONTACT}} 는 시작 때 env CANON_CONTACT 로 바꾼다(레포에 연락처를 적지 않는다). 없으면 대체 문구.
PRIVACY_FILE = "11-privacy.md"
PRIVACY_CONTACT_FALLBACK = "대시보드 도움말의 문의 경로"
_PRIVACY_ANCHORS = {**{f: f"/help#{k}" for k, f in HELP_FILES.items()}, PRIVACY_FILE: "#privacy"}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# 웹 HTML 응답(/, /app, /help, /privacy, _page)에만 붙인다. MCP·/health·JSON API·리다이렉트는 그대로.
# script-src 는 아직 없다 — app.html·start.html 인라인 스크립트에 nonce 를 붙여야 넣을 수 있다.
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
    "referrer-policy": "same-origin",
}


def _html_resp(content: str, status: int = 200, headers: dict | None = None) -> HTMLResponse:
    return HTMLResponse(content, status_code=status, headers={**SECURITY_HEADERS, **(headers or {})})


def _esc(v) -> str:
    """외부·동적 값(쿼리·토큰 서버 응답·예외 문구)을 HTML 에 넣기 전에 반드시 거친다."""
    return _html.escape(str(v))


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """title 은 escape 한다. body 는 HTML 조각 — 동적 값은 호출하는 쪽이 _esc 로 감싸서 넘긴다."""
    t = _esc(title)
    return _html_resp(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title><style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 20px;color:#2B2824;background:#FBF8F4;line-height:1.6}}
a{{color:#E4705A}}</style></head><body><h2>{t}</h2>{body}</body></html>""", status)


# ── 도움말: guides 마크다운 → HTML ─────────────────────
# 입력은 우리 문서(09·10·11)뿐이다. 제목·문단·표·굵게·목록·인라인 코드·링크만. 줄마다 먼저 escape 하고 변환한다.

def read_guides(d: Path) -> dict | None:
    """{"start": 09 원문, "words": 10 원문}. 하나라도 없으면 None."""
    try:
        return {k: (d / f).read_text(encoding="utf-8") for k, f in HELP_FILES.items()}
    except OSError:
        return None


def _safe_href(href: str) -> bool:
    """http(s)·#앵커·상대경로만. 상대경로는 스킴(:)·// 로 시작하지 않는 것."""
    if href.startswith(("http://", "https://", "#")):
        return True
    return not href.startswith("//") and ":" not in href.split("/", 1)[0] and ":" not in href


def _inline(s: str, anchors: dict | None = None) -> str:
    """anchors: 다른 가이드 파일 링크를 이 페이지 안 주소로 바꾸는 표(기본 /help 용)."""
    anchors = _HELP_ANCHORS if anchors is None else anchors
    codes: list[str] = []

    def keep(m):
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", keep, _html.escape(s.replace("\x00", "")))   # 모든 글자를 먼저 escape — raw HTML 은 글자로만 보인다
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

    def link(m):
        href = anchors.get(m.group(2), m.group(2))
        if not _safe_href(href):
            return m.group(1)   # javascript:·data: 등은 링크로 만들지 않고 글만 남긴다
        return f'<a href="{href}">{m.group(1)}</a>'

    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, s)   # ]( 가 있어야 링크 — [로그인] 같은 버튼 이름은 그대로
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_to_html(md: str, key: str, anchors: dict | None = None) -> tuple[str, list[tuple[str, str]], str]:
    """(본문 HTML, [(h2 id, h2 글)], h1 글). h2 id 는 순번(key-1, key-2 …). anchors 는 _inline 에 그대로."""
    def _il(x: str) -> str:
        return _inline(x, anchors)

    out: list[str] = []
    toc: list[tuple[str, str]] = []
    h1 = ""
    para: list[str] = []
    lst: tuple[str, list[str]] | None = None
    table: list[list[str]] = []

    def flush():
        nonlocal lst
        if para:
            out.append("<p>" + " ".join(_il(x) for x in para) + "</p>")
            para.clear()
        if lst:
            tag, items = lst
            out.append(f"<{tag}>" + "".join(f"<li>{_il(x)}</li>" for x in items) + f"</{tag}>")
            lst = None
        if table:
            head, *rows = table
            out.append('<div class="tbl"><table><thead><tr>' + "".join(f"<th>{_il(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_il(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table></div>")
            table.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if line.startswith("|"):
            if para or lst:
                flush()
            if not re.fullmatch(r"\|[\s:|-]+\|", line.strip()):   # |---|---| 구분 줄은 버린다
                table.append(_cells(line))
            continue
        if table:
            flush()
        m = re.match(r"(#{1,3}) (.+)", line)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1:
                h1 = text
                out.append(f"<h1>{_il(text)}</h1>")
            elif level == 2:
                hid = f"{key}-{len(toc) + 1}"
                toc.append((hid, text))
                out.append(f'<h2 id="{hid}">{_il(text)}</h2>')
            else:
                out.append(f"<h3>{_il(text)}</h3>")
            continue
        m = re.match(r"(\d+)\. (.+)", line) or re.match(r"([-*]) (.+)", line)
        if m:
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            if para or (lst and lst[0] != tag):
                flush()
            if not lst:
                lst = (tag, [])
            lst[1].append(m.group(2))
            continue
        if lst:
            flush()
        para.append(line.strip())
    flush()
    return "\n".join(out), toc, h1


_HELP_CSS = """
.top { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1.6rem 0 .8rem; }
.top .name { font-family: var(--serif); font-size: 1.15rem; font-weight: 500; }
.top .name a { color: inherit; text-decoration: none; }
.top .name span { color: var(--muted); }
.btn { font: inherit; font-weight: 600; font-size: .84rem; min-height: 36px; padding: .3rem 1rem; border-radius: 6px; background: var(--card);
  color: var(--ink); border: 1px solid var(--line); display: inline-flex; align-items: center; text-decoration: none; white-space: nowrap; }
.btn.primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.toc { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.3rem; margin: 0 0 1rem;
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .4rem 1.6rem; font-size: .84rem; }
.toc b { display: block; margin-bottom: .2rem; font-size: .8rem; color: var(--muted); font-weight: 600; }
.toc ol { margin: 0; padding-left: 1.1rem; }
.toc li { margin: .1rem 0; }
.toc a { text-decoration: none; }
.toc a:hover { text-decoration: underline; }
.doc { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 1.6rem 2rem; margin: 0 0 1rem; }
.doc h1 { font-family: var(--serif); font-size: 1.45rem; line-height: 1.3; margin: 0 0 .7rem; font-weight: 500; letter-spacing: -.01em; }
.doc h2 { font-size: 1.02rem; margin: 1.7rem 0 .5rem; padding-top: .9rem; border-top: 1px solid var(--line); scroll-margin-top: 1rem; }
.doc h3 { font-size: .92rem; margin: 1.2rem 0 .4rem; }
.doc p, .doc li { margin: .45rem 0; }
.doc ol, .doc ul { padding-left: 1.3rem; }
.doc code { font-family: var(--mono); font-size: .82em; background: var(--paper); border: 1px solid var(--line); border-radius: 4px; padding: 0 .3em; }
.doc strong { font-weight: 650; }
.tbl { overflow-x: auto; margin: .6rem 0; -webkit-overflow-scrolling: touch; }
.doc table { border-collapse: collapse; width: 100%; font-size: .84rem; }
.doc th, .doc td { border-bottom: 1px solid var(--line); padding: .45rem .6rem; text-align: left; vertical-align: top; }
.doc td:first-child { min-width: 5.5em; }   /* 「오늘」 같은 짧은 첫 칸이 한 글자씩 꺾이지 않게 */
.doc th { color: var(--muted); font-weight: 600; font-size: .78rem; border-bottom-color: var(--soft); white-space: nowrap; }
.foot { color: var(--soft); font-size: .72rem; padding: .4rem 0 2.4rem; }
@media (max-width: 640px) {
  .wrap { padding: 0 1rem; }
  .toc { grid-template-columns: 1fr; }
  .doc { padding: 1.1rem 1.1rem; }
  .doc h1 { font-size: 1.25rem; }
  .doc table { min-width: 34rem; }   /* 좁은 화면에선 표를 짜부리지 않고 .tbl 안에서 가로 스크롤 */
}
"""


def _doc_page(title: str, toc_html: str, body_html: str, foot: str) -> str:
    """/help·/privacy 공통 껍데기. 맨 위 링크 자리는 <!--nav--> — 요청마다 세션 보고 바꾼다. 토큰·글꼴은 start.html 의 :root 블록을 그대로 쓴다.
    title·foot 은 우리 상수(HTML 조각), toc_html·body_html 은 md_to_html 결과(escape 끝난 것)."""
    style = START_HTML.read_text(encoding="utf-8").split("<style>", 1)[1]
    css = style.split(".bands {", 1)[0]   # 주석·:root 토큰(라이트·다크)·기본(body·a·focus·.wrap)까지
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>대시보드 — {title}</title>
<style>{css}{_HELP_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top"><div class="name"><a href="/">대시보드</a> <span>— {title}</span></div><!--nav--></header>
  <nav class="toc" aria-label="목차">{toc_html}</nav>
  <main>
{body_html}
  </main>
  <p class="foot">{foot}</p>
</div>
</body>
</html>"""


def _toc_block(key: str, toc: list[tuple[str, str]], h1: str, anchors: dict | None = None) -> str:
    items = "".join(f'<li><a href="#{hid}">{_inline(re.sub(r"^[0-9]+[.] ", "", t), anchors)}</a></li>' for hid, t in toc)
    return f'<div><b><a href="#{key}">{_html.escape(h1)}</a></b><ol>{items}</ol></div>'


def render_help(docs: dict) -> str:
    """도움말 페이지 전체(09·10)."""
    toc_html, body_html = [], []
    for key in HELP_FILES:
        body, toc, h1 = md_to_html(docs[key], key)
        body_html.append(f'<section class="doc" id="{key}">{body}</section>')
        toc_html.append(_toc_block(key, toc, h1))
    return _doc_page("도움말", "".join(toc_html), chr(10).join(body_html),
                     '대화에서 물어도 AI 가 이 문서를 읽고 답해요. · <a href="/privacy">개인정보 처리방침</a>')


def render_privacy(md: str) -> str:
    """처리방침 페이지 전체(11). md 는 {{CONTACT}} 를 이미 바꾼 원문 — 렌더러가 모든 글자를 escape 한다."""
    body, toc, h1 = md_to_html(md, "privacy", _PRIVACY_ANCHORS)
    return _doc_page("개인정보 처리방침", _toc_block("privacy", toc, h1, _PRIVACY_ANCHORS),
                     f'<section class="doc" id="privacy">{body}</section>', '<a href="/help">도움말</a>')


def privacy_contact() -> str:
    """env CANON_CONTACT(한 줄로 접음). 없으면 대체 문구."""
    return " ".join(os.environ.get("CANON_CONTACT", "").split()) or PRIVACY_CONTACT_FALLBACK


class Web:
    def __init__(self, issuer: str, public_url: str, verifier, dev: bool, dev_user: str):
        self.issuer = issuer.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.verifier = verifier
        self.dev = dev
        self.dev_user = dev_user
        self.client_id = os.environ.get("AUTHKIT_WEB_CLIENT_ID", "").strip()
        self.secret = os.environ.get("CANON_SESSION_SECRET", "").encode()
        if self.dev and not self.secret:
            self.secret = b"dev-only-" + secrets.token_bytes(16)   # 개발은 재시작마다 바뀌어도 된다
        p = urlparse(self.public_url)
        self.secure = p.scheme == "https"
        self.origins = {f"{p.scheme}://{p.netloc}"}
        if self.dev:
            self.origins |= {"http://127.0.0.1:8800", "http://localhost:8800", f"http://127.0.0.1:{p.port or 8800}", f"http://localhost:{p.port or 8800}"}
        self.load_help(GUIDES_DIR)
        if not self.guides:
            print(f"[web] 도움말 문서가 없어요({GUIDES_DIR} 에 {' · '.join(HELP_FILES.values())}) — /help 503, help_get 오류", flush=True)
        if not self.privacy_html:
            print(f"[web] 처리방침 문서가 없어요({GUIDES_DIR} 에 {PRIVACY_FILE}) — /privacy 503", flush=True)

    def load_help(self, d: Path):
        """도움말 원문(help_get)과 페이지(/help), 처리방침 페이지(/privacy)를 시작 때 한 번 읽는다.
        도움말 파일이 없으면 guides·help_html 이 None — /help 503, help_get 오류. 처리방침 파일이 없으면 privacy_html 이 None — /privacy 503."""
        self.guides = read_guides(d)
        self.help_html = render_help(self.guides) if self.guides else None
        try:
            md = (d / PRIVACY_FILE).read_text(encoding="utf-8")
        except OSError:
            md = None
        self.privacy_html = render_privacy(md.replace("{{CONTACT}}", privacy_contact())) if md else None

    @property
    def login_ready(self) -> bool:
        return bool(self.client_id and self.secret and self.issuer and self.verifier)

    # ── 서명 쿠키 ──────────────────────────────────────
    def sign(self, data: dict, ttl: int) -> str:
        body = _b64(json.dumps({**data, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
        return body + "." + _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())

    def unsign(self, value: str | None) -> dict | None:
        if not value or "." not in value or not self.secret:
            return None
        body, sig = value.rsplit(".", 1)
        want = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        try:
            data = json.loads(_unb64(body))
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("exp", 0) < time.time():
            return None
        return data

    def set_cookie(self, resp: Response, name: str, value: str, ttl: int):
        resp.set_cookie(name, value, max_age=ttl, httponly=True, secure=self.secure, samesite="lax", path="/")

    def clear_cookie(self, resp: Response, name: str):
        resp.delete_cookie(name, path="/")

    # ── 세션 ───────────────────────────────────────────
    async def uid_of(self, request) -> str | None:
        s = self.unsign(request.cookies.get(SESSION_COOKIE))
        if s and s.get("uid"):
            return s["uid"]
        if self.dev and self.dev_user:
            return (await run_in_threadpool(db.user_by_email, self.dev_user))["id"]
        return None

    def same_origin(self, request) -> bool:
        return request.headers.get("origin", "") in self.origins

    # ── 화면 ───────────────────────────────────────────
    async def app(self, request):
        if await self.uid_of(request) is None:
            return RedirectResponse("/", status_code=302)
        return _html_resp(APP_HTML.read_text(encoding="utf-8"), headers={"cache-control": "no-store"})

    async def root(self, request):
        """시작 화면. 세션 있으면 /app. ?out=1(로그아웃 직후)일 때만 <!--out-->…<!--/out--> 띠를 남기고 [로그인]을 /login?fresh=1 로."""
        if await self.uid_of(request) is not None:
            return RedirectResponse("/app", status_code=302)
        html = START_HTML.read_text(encoding="utf-8")
        if request.query_params.get("out") == "1":
            html = html.replace('href="/login"', 'href="/login?fresh=1"', 1)
        else:
            html = re.sub(r"<!--out-->.*?<!--/out-->", "", html, flags=re.S)
        return _html_resp(html, headers={"cache-control": "no-store"})

    async def help(self, request):
        """도움말. 로그인 불필요. DB 를 건드리지 않는다 — 세션은 쿠키 서명만 본다(DEV 는 dev_user 가 있으면 세션으로 친다)."""
        if not self.help_html:
            return _page("도움말을 찾지 못했어요", "<p>서버에 도움말 문서(guides/09-hosted-start.md · 10-words.md)가 없어요. 운영자에게 알려 주세요.</p>", 503)
        return _html_resp(self.help_html.replace("<!--nav-->", self._nav(request), 1), headers={"cache-control": "no-store"})

    async def privacy(self, request):
        """개인정보 처리방침. /help 와 같다 — 로그인 불필요, DB 를 건드리지 않는다."""
        if not self.privacy_html:
            return _page("처리방침을 찾지 못했어요", "<p>서버에 처리방침 문서(guides/11-privacy.md)가 없어요. 운영자에게 알려 주세요.</p>", 503)
        return _html_resp(self.privacy_html.replace("<!--nav-->", self._nav(request), 1), headers={"cache-control": "no-store"})

    def _nav(self, request) -> str:
        """/help·/privacy 맨 위 버튼. 세션은 쿠키 서명만 본다(DEV 는 dev_user 가 있으면 세션으로 친다)."""
        s = self.unsign(request.cookies.get(SESSION_COOKIE))
        signed_in = bool(s and s.get("uid")) or bool(self.dev and self.dev_user)
        return '<a class="btn" href="/app">대시보드로</a>' if signed_in else '<a class="btn primary" href="/login">로그인</a>'

    # ── 로그인 ─────────────────────────────────────────
    async def login(self, request):
        if not self.login_ready:
            return _page("아직 로그인을 열지 않았어요", "<p>운영자가 로그인 설정(AUTHKIT_WEB_CLIENT_ID·CANON_SESSION_SECRET)을 마치면 열려요.</p>", 503)
        verifier = _b64(secrets.token_bytes(48))
        state = _b64(secrets.token_bytes(24))
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        q = {"client_id": self.client_id, "redirect_uri": f"{self.public_url}/auth/callback", "response_type": "code",
             "scope": "openid email", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
             "resource": f"{self.public_url}/mcp"}
        if request.query_params.get("fresh") == "1":   # 로그아웃 직후 — AuthKit 세션이 살아 있어도 로그인 폼을 띄운다
            q["prompt"] = "login"
        resp = RedirectResponse(f"{self.issuer}/oauth2/authorize?{urlencode(q)}", status_code=302)
        self.set_cookie(resp, FLOW_COOKIE, self.sign({"v": verifier, "s": state}, FLOW_TTL), FLOW_TTL)
        return resp

    async def token_post(self, data: dict) -> tuple[int, dict]:
        """토큰 교환. 시험에서 바꿔 끼운다."""
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.post(f"{self.issuer}/oauth2/token", data=data)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    async def callback(self, request):
        if not self.login_ready:
            return _page("아직 로그인을 열지 않았어요", "", 503)
        flow = self.unsign(request.cookies.get(FLOW_COOKIE))
        qp = request.query_params
        again = '<p><a href="/login">다시 로그인하기</a></p>'
        if qp.get("error"):
            return _page("로그인이 취소됐거나 실패했어요", f"<p>{_esc(qp.get('error_description') or qp['error'])}</p>{again}", 400)
        if not flow or not qp.get("code") or qp.get("state") != flow.get("s"):
            return _page("로그인 절차가 맞지 않아요", "<p>로그인 창을 10분 안에 끝내지 않았거나, 다른 탭에서 시작한 로그인이에요.</p>" + again, 400)
        status, tok = await self.token_post({
            "grant_type": "authorization_code", "code": qp["code"], "redirect_uri": f"{self.public_url}/auth/callback",
            "client_id": self.client_id, "code_verifier": flow["v"], "resource": f"{self.public_url}/mcp"})
        access = tok.get("access_token") if isinstance(tok, dict) else None
        if status != 200 or not access:
            return _page("로그인 서버가 토큰을 주지 않았어요", f"<p>{_esc(tok.get('error_description') or tok.get('error') or status)}</p>{again}", 502)
        verified = await self.verifier.verify_token(access)
        if verified is None:
            return _page("받은 토큰을 믿을 수 없어요", "<p>토큰의 발급자·대상이 이 서버와 맞지 않아요.</p>" + again, 502)
        try:
            uid = await run_in_threadpool(auth.user_id_for, verified)
        except Exception as e:   # ToolError·CanonError — 메시지만 보인다
            msg = getattr(e, "message", None) or str(e)
            return _page("사용자를 연결하지 못했어요", f"<p>{_esc(msg)}</p>{again}", 502)
        resp = RedirectResponse("/app", status_code=302)
        self.set_cookie(resp, SESSION_COOKIE, self.sign({"uid": uid}, SESSION_TTL), SESSION_TTL)
        self.clear_cookie(resp, FLOW_COOKIE)
        return resp

    async def logout(self, request):
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        # AuthKit 세션은 끝낼 엔드포인트가 없다 — 다음 로그인은 /login?fresh=1(prompt=login)로 폼을 다시 띄운다
        resp = JSONResponse({"ok": True, "redirect": "/?out=1"})
        self.clear_cookie(resp, SESSION_COOKIE)
        return resp

    # ── API ────────────────────────────────────────────
    async def _need(self, request):
        uid = await self.uid_of(request)
        if uid is None:
            return None, JSONResponse({"code": "UNAUTHORIZED", "msg": "로그인이 필요해요."}, status_code=401)
        return uid, None

    async def api_state(self, request):
        uid, err = await self._need(request)
        if err:
            return err
        st, me = await run_in_threadpool(lambda: (db.state(uid), db.user_get(uid)))
        st["user"] = {"email": me["email"]}
        return JSONResponse(st, headers={"cache-control": "no-store"})

    async def api_journal(self, request):
        uid, err = await self._need(request)
        if err:
            return err
        rid = request.query_params.get("id", "")
        try:
            r = await run_in_threadpool(db.record_get, uid, rid)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message}, status_code=404)
        return JSONResponse({"id": r["id"], "title": r["title"], "body": r["body"],
                             "meta": {"created": r["created"], "source": r["source"], "question_id": r["question_id"]}},
                            headers={"cache-control": "no-store"})

    async def api_decide(self, request):
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict)
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문이 필요해요."}, status_code=400)
        cid, action, text = str(body.get("candidate_id") or ""), str(body.get("action") or ""), body.get("text")
        if action not in ACTIONS:
            return JSONResponse({"code": "ARG-INVALID", "msg": "action 은 confirm·exclude·pending·edit 중 하나예요."}, status_code=400)
        try:
            out = await run_in_threadpool(db.candidate_decide, uid, cid, action, str(text) if text is not None else None)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out)

    async def api_item(self, request):
        """항목 하나 + 관계 대상 + 들어오는 관계 + 이력 + 승격 상태(서랍)."""
        uid, err = await self._need(request)
        if err:
            return err
        try:
            out = await run_in_threadpool(db.item_get, uid, request.query_params.get("id", ""))
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message}, status_code=404)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    async def api_item_decide(self, request):
        """항목 결정 — 제안 적용(apply)·제외(discard), 기준 남기기·남기지 않기·되돌리기. expected_revision 이 맞을 때만."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict)
            rev = body.get("expected_revision")
            assert isinstance(rev, int) and not isinstance(rev, bool)
            hid = body.get("history_id")
            assert hid is None or (isinstance(hid, int) and not isinstance(hid, bool))
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문(item_id·expected_revision·action)이 필요해요."}, status_code=400)
        action = str(body.get("action") or "")
        if action not in db.ITEM_ACTIONS:
            return JSONResponse({"code": "ARG-INVALID", "msg": "action 이 틀렸어요."}, status_code=400)
        try:
            out = await run_in_threadpool(db.item_decide, uid, str(body.get("item_id") or ""), rev,
                                          str(body.get("proposal_id") or "") or None, action, hid)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out)

    def routes(self) -> list[Route]:
        return [
            Route("/", self.root),
            Route("/app", self.app),
            Route("/help", self.help),
            Route("/privacy", self.privacy),
            Route("/login", self.login),
            Route("/auth/callback", self.callback),
            Route("/logout", self.logout, methods=["POST"]),
            Route("/api/state", self.api_state),
            Route("/api/journal", self.api_journal),
            Route("/api/candidate/decide", self.api_decide, methods=["POST"]),
            Route("/api/item", self.api_item),
            Route("/api/item/decide", self.api_item_decide, methods=["POST"]),
        ]
