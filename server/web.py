"""웹 화면 — AuthKit 로그인(OAuth 2.1 공개 클라이언트 + PKCE), 서명 쿠키 세션, 화면 API.

로그인 흐름: /login → AuthKit /oauth2/authorize (resource=<PUBLIC_URL>/mcp) → /auth/callback → 토큰 교환 →
받은 액세스 토큰을 MCP 와 같은 AuthKitVerifier·user_id_for 로 검증·연결 → 세션 쿠키.
웹 사용자 = MCP 사용자(같은 sub). 클라이언트는 AuthKit DCR 로 한 번 등록한 공개 클라이언트(AUTHKIT_WEB_CLIENT_ID).

상태는 서버 메모리에 두지 않는다(Render Free 는 유휴 때 죽는다). PKCE verifier·state 는 10분짜리 서명 쿠키, 세션은 7일 서명 쿠키.
세션 쿠키 v2(β2): {uid, iat, ra?} — 요청마다 users 행 1건으로 차단·웹 끊기(web_revoked_before)를 본다. ra = 마지막 재인증 시각.
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
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import auth
import db

SESSION_COOKIE = "canon_session"
FLOW_COOKIE = "canon_login"
SESSION_TTL = 7 * 24 * 3600   # 세션 쿠키 7일(PR①, 30일에서 줄임)
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
# 권리 행사 경로는 대시보드 「문의」 하나. 문안의 {{CONTACT_LINE}} 은 env CANON_CONTACT(선택)가 있으면 보조 메일 한 줄, 없으면 빈 줄로 바꾼다(레포에 연락처를 적지 않는다).
PRIVACY_FILE = "11-privacy.md"
# 동의 문서(α2) — 첫 20줄 머리글 `vN · 날짜` 에서 버전을 뽑아 db.CONSENT_VERSIONS 에. terms 는 파일이 있을 때만.
CONSENT_FILES = {"privacy": PRIVACY_FILE, "terms": "12-terms.md"}
CONSENT_LABELS = {"privacy": ("개인정보 처리방침", "/privacy"), "terms": ("이용약관", "/terms")}
_VERSION_RE = re.compile(r"^`v(\d+) · ")   # 줄 머리 고정 — 본문 중간의 예시(`v9`)는 머리글이 아니다
_PRIVACY_ANCHORS = {**{f: f"/help#{k}" for k, f in HELP_FILES.items()}, PRIVACY_FILE: "#privacy"}

# ── 화면 자산(배경 루프·데모·테마 토글) ─────────────────
# 허용 목록만 서빙한다. 요청 name 은 이 dict 키 조회만 — 경로를 잇지 않는다. 그림 만드는 법: tools/start-visuals/README.md
ASSETS_DIR = Path(__file__).parent / "assets"
ASSET_FILES = {
    "start-bg.webp": "image/webp",            # 배경 루프(animated)
    "start-bg-still.webp": "image/webp",      # 배경 정지 — reduced-motion·멈춤
    "start-demo.webp": "image/webp",          # 샘플 데이터 데모(animated)
    "start-demo-poster.webp": "image/webp",   # 데모 정지 — reduced-motion·멈춤
    "start-bg-dark.webp": "image/webp",       # 어두운 화면 배경 루프
    "start-bg-dark-still.webp": "image/webp", # 어두운 화면 배경 정지
    "start-demo-dark.webp": "image/webp",     # 어두운 화면 데모(다크 대시보드 촬영)
    "start-demo-dark-poster.webp": "image/webp",
    "theme.js": "text/javascript; charset=utf-8",   # ☀/☾ 토글 — 시작·도움말·대시보드 <head> 가 읽는 유일한 외부(self) 스크립트
}
ASSET_IMMUTABLE = "public, max-age=31536000, immutable"


def load_assets(d: Path) -> dict:
    """{name: (path, content_type, sha10)}. 시작 때 1회. 파일이 없으면 그 이름만 빠진다(→ 404, 화면은 배경색만)."""
    out = {}
    for name, ctype in ASSET_FILES.items():
        p = d / name
        if p.is_file():
            out[name] = (p, ctype, hashlib.sha256(p.read_bytes()).hexdigest()[:10])
        else:
            print(f"[web] 시작 화면 자산이 없어요: {p}", flush=True)
    return out


ASSETS = load_assets(ASSETS_DIR)


def asset_url(name: str) -> str:
    a = ASSETS.get(name)
    return f"/assets/{name}?v={a[2]}" if a else f"/assets/{name}"


def _fill_assets(html: str) -> str:
    """{{asset:NAME}} → /assets/NAME?v=<sha10>. 시작 화면(root)·대시보드(app)가 부른다. /help 는 render_help 가 theme.js 만 직접 넣는다."""
    return re.sub(r"\{\{asset:([A-Za-z0-9._-]+)\}\}", lambda m: asset_url(m.group(1)), html)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# 웹 HTML 응답(/, /app, /help, /privacy, /consent, _page)에만 붙인다. MCP·/health·JSON API·리다이렉트는 그대로.
# script-src 'self' — 외부(self) theme.js 만. /app 은 응답 본문의 인라인 스크립트 1개 해시를 더한다(app_csp).
# 'unsafe-inline'·'unsafe-eval' 은 어떤 경우에도 넣지 않는다. style-src·default-src 는 아직 없다(인라인 style).
CSP_BASE = "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; script-src 'self'"
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": CSP_BASE,
    "referrer-policy": "same-origin",
}
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_SRC_ATTR_RE = re.compile(r"\bsrc\s*=", re.I)


def inline_script_hashes(html: str) -> list[str]:
    """src 없는 <script> 의 태그 사이 내용(공백·개행 그대로, UTF-8) sha256 → 표준 base64(패딩 포함). CSP 해시는 urlsafe 가 아니다."""
    return [base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
            for attrs, body in _SCRIPT_RE.findall(html) if not _SRC_ATTR_RE.search(attrs)]


def app_csp(html: str) -> str:
    """/app CSP. 보낼 바로 그 문자열에서 계산한다. 인라인이 정확히 1개가 아니면 닫는 쪽 — 해시 없이 'self' 만 + 로그 1줄."""
    hs = inline_script_hashes(html)
    if len(hs) != 1:
        print(f"[web] /app 인라인 스크립트가 {len(hs)}개라 CSP 해시를 넣지 않았어요(script-src 'self' 만 — 화면 스크립트가 막힌다)", flush=True)
        return CSP_BASE
    return f"{CSP_BASE} 'sha256-{hs[0]}'"


def _html_resp(content: str, status: int = 200, headers: dict | None = None) -> HTMLResponse:
    return HTMLResponse(content, status_code=status, headers={**SECURITY_HEADERS, **(headers or {})})


def _esc(v) -> str:
    """외부·동적 값(쿼리·토큰 서버 응답·예외 문구)을 HTML 에 넣기 전에 반드시 거친다."""
    return _html.escape(str(v))


def ua_family(ua: str | None) -> str:
    """User-Agent 에서 브라우저 계열만(버전·OS 없음). 순서가 중요하다 — Edge·Chrome 의 UA 에도 Safari/ 가 들어 있다."""
    ua = ua or ""
    if not ua.strip():
        return ""
    for mark, fam in (("Edg/", "edge"), ("Firefox/", "firefox"), ("FxiOS/", "firefox"), ("Chrome/", "chrome"), ("CriOS/", "chrome"),
                      ("Safari/", "safari")):
        if mark in ua:
            return fam
    return "other"


def _write_status(e) -> int:
    """웹 쓰기 API 의 CanonError → HTTP 상태. 동의 필요는 403, 나머지는 400(기존 그대로)."""
    return 403 if e.code == "CONSENT-REQUIRED" else 400


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
.top-r { display: flex; align-items: center; gap: .5rem; }
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


# ☀/☾ 토글 — theme.js 가 hidden 을 떼고 글자·누름 상태를 채운다(스크립트가 없으면 안 보인다).
THEME_TOGGLE = '<button type="button" class="theme-toggle" data-theme-toggle hidden aria-pressed="false" aria-label="어두운 화면">\u263E</button>'


def _doc_page(title: str, toc_html: str, body_html: str, foot: str) -> str:
    """/help·/privacy 공통 껍데기. 맨 위 링크 자리는 <!--nav--> — 요청마다 세션 보고 바꾼다. 토큰·글꼴은 start.html 의 :root 블록을 그대로 쓴다.
    title·foot 은 우리 상수(HTML 조각), toc_html·body_html 은 md_to_html 결과(escape 끝난 것)."""
    style = START_HTML.read_text(encoding="utf-8").split("<style>", 1)[1]
    css = style.split(".bands {", 1)[0]   # 주석·:root 토큰(라이트·다크 토글)·기본(body·a·focus·.wrap·☀/☾)까지 — 배경·데모(start 전용)는 안 온다
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>대시보드 — {title}</title>
<script src="{asset_url("theme.js")}"></script>
<style>{css}{_HELP_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top"><div class="name"><a href="/">대시보드</a> <span>— {title}</span></div><div class="top-r">{THEME_TOGGLE}<!--nav--></div></header>
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
    """처리방침 페이지 전체(11). md 는 {{CONTACT_LINE}} 을 이미 바꾼 원문 — 렌더러가 모든 글자를 escape 한다."""
    body, toc, h1 = md_to_html(md, "privacy", _PRIVACY_ANCHORS)
    return _doc_page("개인정보 처리방침", _toc_block("privacy", toc, h1, _PRIVACY_ANCHORS),
                     f'<section class="doc" id="privacy">{body}</section>', '<a href="/help">도움말</a>')


def read_consent_versions(d: Path, dev: bool) -> dict:
    """{kind: "vN"}. terms 는 문서가 없으면 빠진다. privacy 는 필수 — 비DEV 에서 없으면 기동 거부.
    문서가 없는 것(FileNotFoundError)과 못 읽는 것(그 밖 OSError)을 가른다. 못 읽거나, 첫 20줄 머리글(`vN · 날짜`)이
    없거나 둘 이상이면 비DEV 는 기동 거부(SystemExit, 파일명), DEV 는 로그 1줄 + 그 문서 제외(둘 이상이면 첫 것 사용)."""
    out = {}
    for kind, name in CONSENT_FILES.items():
        try:
            head = (d / name).read_text(encoding="utf-8").splitlines()[:20]
        except FileNotFoundError:
            if kind == "privacy" and not dev:
                raise SystemExit(f"동의 문서 {PRIVACY_FILE} 가 없어요. 운영은 처리방침 없이 뜨지 않아요.")
            continue
        except OSError as e:
            if not dev:
                raise SystemExit(f"동의 문서 {name} 을 읽지 못했어요({type(e).__name__}). 운영은 동의 문서를 못 읽은 채 뜨지 않아요.")
            print(f"[consent] {name} 을 읽지 못해 동의 문서에서 뺐어요({type(e).__name__}, DEV)", flush=True)
            continue
        ms = [m for m in (_VERSION_RE.match(line) for line in head) if m]
        if not ms:
            if not dev:
                raise SystemExit(f"동의 문서 {name} 첫 20줄에 버전 머리글(`vN · 날짜`)이 없어요. 운영은 동의 버전 없이 뜨지 않아요.")
            print(f"[consent] {name} 에 버전 머리글이 없어 동의 문서에서 뺐어요(DEV)", flush=True)
            continue
        if len(ms) > 1:
            if not dev:
                raise SystemExit(f"동의 문서 {name} 첫 20줄에 버전 머리글(`vN · 날짜`)이 {len(ms)}개예요. 하나만 두세요.")
            print(f"[consent] {name} 에 버전 머리글이 {len(ms)}개라 첫 것({ms[0].group(0).strip()})을 썼어요(DEV)", flush=True)
        out[kind] = f"v{ms[0].group(1)}"
    if not out and dev:
        print("[consent] 동의 문서 없음 — 게이트 없음", flush=True)
    return out


def privacy_contact_line() -> str:
    """env CANON_CONTACT(선택, 한 줄로 접음)가 있으면 보조 메일 한 줄. 없으면 빈 문자열 — 처리방침에 메일 줄이 없다."""
    contact = " ".join(os.environ.get("CANON_CONTACT", "").split())
    return f"로그인할 수 없으면 메일로도 요청할 수 있습니다(보조 경로). 가입한 이메일 주소에서 보낸 메일만 처리합니다. 메일: {contact}" if contact else ""


_CODE_RE = re.compile(r"^[A-Z][A-Z-]*$")


def _usage(name: str, handler):
    """/api/* 사용 집계 래퍼(α2) — 핸들러 하나를 감싸 db.usage_count(uid, name, code). DB 호출 없음.
    uid = 핸들러가 uid_of 로 찾아 request.state 에 둔 값(없으면 '' — 401 등). code = 2xx·3xx 는 OK,
    4xx·5xx 는 응답 JSON 의 code(대문자·하이픈 열거형만, 아니면 EXC). 예외는 EXC 로 세고 다시 던진다.
    200 응답의 code(항목 코드 C-07 등)는 읽지 않는다 — 자유 텍스트를 집계에 넣지 않는다."""
    async def run(request):
        try:
            resp = await handler(request)
        except Exception:
            db.usage_count(getattr(request.state, "uid", None), name, "EXC")
            raise
        code = "OK"
        if resp.status_code >= 400:
            try:
                code = json.loads(resp.body).get("code")
            except Exception:
                code = None
            code = code if isinstance(code, str) and _CODE_RE.match(code) else "EXC"
        db.usage_count(getattr(request.state, "uid", None), name, code)
        return resp
    return run


# ── 재인증 창 (β2) ─────────────────────────────────────
# 경로가 아니라 동작에 붙인다. 세션 쿠키의 ra(마지막 재인증 시각, 정수 초)로 0 ≤ now − ra ≤ 창 일 때만 통과.
# ra 는 /admin/reauth → /login?fresh=1&reauth=1 → 콜백(같은 uid)에서만 생긴다. 일반 로그인 쿠키엔 없다.
REAUTH_WINDOWS = {
    "admin.view": 12 * 3600,        # /admin 화면·감사 로그
    "ticket.read_all": 12 * 3600,   # /app 문의함 전체 조회(GET /api/state 의 admin_tickets · GET /api/feedback 전체)
    "ticket.update": 12 * 3600,     # /app 문의 상태·메모(POST /api/feedback/status·note)
    "role.grant": 15 * 60,
    "role.revoke": 15 * 60,
    "user.web_signout": 15 * 60,
    "user.block": 15 * 60,
    "user.unblock": 15 * 60,
}
AUTH_TIME_SKEW = 60                 # 콜백 토큰 auth_time 은 재인증 시작(t0) − 60초 이후여야 ra 를 준다
ADMIN_FORM_MAX = 4 * 1024           # /admin POST form 본문 상한(바이트) — BodyLimit(/mcp·/api/*) 밖이라 핸들러가 직접 센다


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def reauth_ok(ra, action: str, now: float | None = None) -> bool:
    """ra(정수 초)가 그 동작의 창 안인지. ra 없음·정수 아님·미래·창 밖이면 False(닫는 쪽)."""
    if not _is_int(ra):
        return False
    d = (time.time() if now is None else now) - ra
    return 0 <= d <= REAUTH_WINDOWS[action]


def _not_found() -> Response:
    """/admin 의 비관리자·무세션·DEV 응답 — 없는 경로와 같은 모양."""
    return Response("Not Found", status_code=404, media_type="text/plain; charset=utf-8")


ADMIN_LABELS = {"bootstrap": "첫 지정", "recover": "복구(CLI)", "role_grant": "관리자 부여", "role_revoke": "관리자 회수",
                "web_signout": "웹 로그인 끊기", "block": "차단", "unblock": "차단 해제"}
_ADMIN_CSS = """body{font-family:system-ui,sans-serif;margin:0;color:#2B2824;background:#FBF8F4;line-height:1.5;font-size:14px}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
a{color:#B4462F}.bar{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:10px 12px;border:1px solid #E4DCD2;border-radius:8px;background:#fff}
.bar .warn{color:#B4462F;font-weight:600}.tbl{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #E4DCD2}
th,td{border-bottom:1px solid #EEE7DE;padding:6px 8px;text-align:left;vertical-align:top;white-space:nowrap}
th{font-size:12px;color:#6B635A;font-weight:600}td.email{white-space:normal;word-break:break-all}
.acts{display:flex;flex-wrap:wrap;gap:4px}form{margin:0}
button{font:inherit;font-size:12px;padding:3px 8px;border:1px solid #CFC4B7;border-radius:5px;background:#FBF8F4;color:#2B2824;cursor:pointer}
button.danger{border-color:#D9A496;color:#9A3520}.tag{font-size:12px;padding:1px 6px;border-radius:4px;background:#EEE7DE}
.tag.admin{background:#DCEBE3;color:#186A4B}.tag.blocked{background:#F6DDD6;color:#9A3520}.muted{color:#8A8178}"""


def _admin_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """/admin 껍데기 — 스크립트 0(인라인·외부·이벤트 속성 없음). 보안 헤더(CSP script-src 'self')는 _html_resp 가 붙인다.
    title 은 escape 한다. body 는 HTML 조각 — 동적 값은 호출하는 쪽이 _esc 로 감싼다."""
    return _html_resp(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title><style>{_ADMIN_CSS}</style></head><body><main class="wrap">{body}</main></body></html>""",
                      status, headers={"cache-control": "no-store"})


def _kst(d) -> str:
    return d.astimezone(db.KST).strftime("%Y-%m-%d %H:%M") if d else ""


def _left(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}시간 {sec % 3600 // 60}분" if sec >= 3600 else f"{max(sec, 0) // 60}분"


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
        self.load_consent(GUIDES_DIR)
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
        self.privacy_html = render_privacy(md.replace("{{CONTACT_LINE}}", privacy_contact_line())) if md else None

    def load_consent(self, d: Path):
        """동의 문서 버전(α2)을 시작 때 한 번 읽어 db 에 넣는다. 버전이 바뀔 때만 db 의 동의 통과 캐시가 비워진다."""
        db.set_consent_versions(read_consent_versions(d, self.dev))

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
    def session_value(self, uid: str, ra: int | None = None) -> str:
        """세션 쿠키 v2 값 {uid, iat, ra?}. exp 는 sign 이 붙인다(7일). ra 는 재인증 콜백만 넘긴다."""
        data = {"uid": uid, "iat": int(time.time())}
        if ra is not None:
            data["ra"] = ra
        return self.sign(data, SESSION_TTL)

    async def _session_row(self, s: dict):
        """서명 통과한 세션 쿠키의 users 행 — iat 정수·미래 아님 → 행 있음 → 차단 아님 → iat ≥ web_revoked_before.
        하나라도 실패·DB 오류면 None(닫는 쪽). 옛 쿠키(iat 없음)는 여기서 무효."""
        iat = s.get("iat")
        if not _is_int(iat) or iat > time.time():
            return None
        try:
            row = await run_in_threadpool(db.session_user, str(s["uid"]))
        except Exception as e:
            print(f"[web] 세션 확인 실패 — 세션 없음으로: {type(e).__name__}", flush=True)
            return None
        if not row or row["blocked_at"] is not None:
            return None
        wrb = row["web_revoked_before"]
        if wrb is not None and iat < wrb.timestamp():
            return None
        return row

    async def uid_of(self, request) -> str | None:
        """세션 uid. 찾으면 request.state.uid 에도 둔다 — /api/* 사용 집계 래퍼가 DB 조회 없이 읽는다.
        request.state.role·ra 도 둔다(관리자 판정·재인증 창은 이 값만 읽는다 — 두 번째 조회 없음).
        DEV(dev_user) 경로는 쿠키 없이 그대로 — 단 차단이면 None. ra 는 없다."""
        request.state.role, request.state.ra = None, None
        s = self.unsign(request.cookies.get(SESSION_COOKIE))
        uid = None
        if s and s.get("uid"):
            row = await self._session_row(s)
            if row:
                uid, request.state.role = row["id"], row["role"]
                request.state.ra = s.get("ra") if _is_int(s.get("ra")) else None
        elif self.dev and self.dev_user:
            u = await run_in_threadpool(db.user_by_email, self.dev_user)
            if not u.get("blocked_at"):
                uid, request.state.role = u["id"], u.get("role")
        if uid is not None:
            request.state.uid = uid
        return uid

    def _ticket_admin(self, request, action: str) -> bool:
        """/app 관리자 경로 — uid_of 가 둔 role 이 admin 이고 ticket.* 재인증 창 안일 때만."""
        return getattr(request.state, "role", None) == "admin" and reauth_ok(getattr(request.state, "ra", None), action)

    def same_origin(self, request) -> bool:
        return request.headers.get("origin", "") in self.origins

    # ── 화면 ───────────────────────────────────────────
    async def app(self, request):
        uid = await self.uid_of(request)
        if uid is None:
            return RedirectResponse("/", status_code=302)
        if not await run_in_threadpool(db.has_consent, uid):   # 미동의 — 동의 화면 먼저(MCP 전용 사용자도 여기로 온다)
            return RedirectResponse("/consent", status_code=302)
        html = _fill_assets(APP_HTML.read_text(encoding="utf-8"))   # 요청마다 읽는다 — 헤더 해시와 본문이 같은 내용에서 나온다
        return _html_resp(html, headers={"cache-control": "no-store", "content-security-policy": app_csp(html)})

    async def root(self, request):
        """시작 화면. 세션 있으면 /app. ?out=1(로그아웃 직후)일 때만 <!--out-->…<!--/out--> 띠를 남기고 [로그인]을 /login?fresh=1 로."""
        if await self.uid_of(request) is not None:
            return RedirectResponse("/app", status_code=302)
        html = _fill_assets(START_HTML.read_text(encoding="utf-8"))
        if request.query_params.get("out") == "1":
            html = html.replace('href="/login"', 'href="/login?fresh=1"', 1)
        else:
            html = re.sub(r"<!--out-->.*?<!--/out-->", "", html, flags=re.S)
        return _html_resp(html, headers={"cache-control": "no-store"})

    async def asset(self, request):
        """시작 화면 자산. ?v 가 현재 sha10 과 같으면 1년 immutable, 없거나 다르면 no-cache(매번 ETag 재검증)."""
        a = ASSETS.get(request.path_params["name"])
        if a is None:
            return Response("없는 자산이에요.", status_code=404, media_type="text/plain; charset=utf-8")
        path, ctype, sha = a
        cc = ASSET_IMMUTABLE if request.query_params.get("v") == sha else "no-cache"
        return FileResponse(path, media_type=ctype, headers={"cache-control": cc, "x-content-type-options": "nosniff"})

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

    # ── 동의 (α2) ─────────────────────────────────────
    async def consent(self, request):
        """동의 화면. 세션 없으면 /, 이미 동의했으면 /app. 스크립트 없이 form POST(/api/consent)."""
        uid = await self.uid_of(request)
        if uid is None:
            return RedirectResponse("/", status_code=302)
        if await run_in_threadpool(db.has_consent, uid):
            return RedirectResponse("/app", status_code=302)
        rows = "".join(
            f'<p><label><input type="checkbox" name="{_esc(kind)}" value="{_esc(ver)}" required> '
            f'<a href="{CONSENT_LABELS[kind][1]}">{CONSENT_LABELS[kind][0]}</a>({_esc(ver)})에 동의합니다.</label></p>'
            for kind, ver in db.CONSENT_VERSIONS.items())
        body = f'<form method="post" action="/api/consent">{rows}<p><button type="submit">동의하고 시작</button></p></form>'
        resp = _page("시작하기 전에 동의해 주세요", body)
        resp.headers["cache-control"] = "no-store"
        return resp

    async def api_consent(self, request):
        """동의 기록. 세션(401) → Origin(403) → form 또는 JSON {kind: version} → db.consent_grant
        → 302 /app(form) 또는 {ok, redirect}(JSON). 문서·버전이 맞지 않으면 400 ARG-INVALID. write_gate 를 지나지 않는다(동의 전 사용자가 부른다)."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        is_json = request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"
        try:
            if is_json:
                body = await request.json()
                assert isinstance(body, dict)
            else:
                body = dict(parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True))
            assert all(isinstance(v, str) for v in body.values())
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "동의할 문서·버전이 필요해요."}, status_code=400)
        try:
            await run_in_threadpool(db.consent_grant, uid, body)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        if is_json:
            return JSONResponse({"ok": True, "redirect": "/app"})
        return RedirectResponse("/app", status_code=302)

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
        flow = {"v": verifier, "s": state}
        if request.query_params.get("reauth") == "1":   # β2 재인증 — 지금 세션 uid 를 r 로. 돌아갈 곳 URL 은 담지 않는다(콜백이 /admin 고정)
            uid = await self.uid_of(request)
            if uid is not None:
                flow.update(r=uid, t0=int(time.time()))
        resp = RedirectResponse(f"{self.issuer}/oauth2/authorize?{urlencode(q)}", status_code=302)
        self.set_cookie(resp, FLOW_COOKIE, self.sign(flow, FLOW_TTL), FLOW_TTL)
        return resp

    @staticmethod
    def _reauth_fresh(claims: dict, t0) -> bool:
        """재인증 콜백 토큰이 새 로그인인지. auth_time 이 있으면 t0 − 60초 이후여야, 없으면 통과(Google 자동 통과 한계 — README).
        t0 가 정수가 아니면 False."""
        if not _is_int(t0):
            return False
        if "auth_time" not in claims:
            return True
        at = claims["auth_time"]
        return isinstance(at, (int, float)) and not isinstance(at, bool) and at >= t0 - AUTH_TIME_SKEW

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
        claims = verified.claims or {}
        print(f"[web] 로그인 토큰 claims 키: {sorted(str(k) for k in claims)}", flush=True)   # 키 이름만 — 값 금지(auth_time·iat 실측용)
        r = flow.get("r")
        ra = None
        if r is not None and r == uid and self._reauth_fresh(claims, flow.get("t0")):
            ra = int(time.time())   # 재인증 = 같은 사람이 방금 다시 로그인. 다른 uid 면 일반 로그인(ra 없음)
        resp = RedirectResponse("/admin" if r is not None else "/app", status_code=302)
        self.set_cookie(resp, SESSION_COOKIE, self.session_value(uid, ra), SESSION_TTL)
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
        if self._ticket_admin(request, "ticket.read_all"):   # 관리자 + 재인증 12시간 안만 키가 있다 — 아니면 비관리자와 같은 모양. MCP state_get 에는 넣지 않는다
            st["admin_tickets"] = await run_in_threadpool(db.tickets_admin_counts)
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
            rev = body.get("expected_revision")   # 선택. 주면 지금 revision 과 다를 때 CONFLICT
            assert rev is None or (isinstance(rev, int) and not isinstance(rev, bool))
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문이 필요해요(expected_revision 은 정수)."}, status_code=400)
        cid, action, text = str(body.get("candidate_id") or ""), str(body.get("action") or ""), body.get("text")
        if action not in ACTIONS:
            return JSONResponse({"code": "ARG-INVALID", "msg": "action 은 confirm·exclude·pending·edit 중 하나예요."}, status_code=400)
        text = str(text) if text is not None else None
        wording_by = "self" if action in ("edit", "edit_confirm") else None   # 웹 편집 = 사용자가 직접 쓴 문장

        def write():   # 쓰기 진입 — MCP guard 와 같은 db.write_gate(길이·전역 DB 크기) → revision 검사는 db 의 행 잠금 안에서
            db.write_gate("candidate_decide", {"candidate_id": cid, "action": action, "text": text}, uid)
            return db.candidate_decide(uid, cid, action, text, rev, wording_by)
        try:
            out = await run_in_threadpool(write)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=_write_status(e))
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
        iid, pid = str(body.get("item_id") or ""), str(body.get("proposal_id") or "") or None

        def write():   # 쓰기 진입 — MCP guard 와 같은 db.write_gate(길이·전역 DB 크기)
            db.write_gate("item_decide", {"item_id": iid, "proposal_id": pid, "action": action}, uid)
            return db.item_decide(uid, iid, rev, pid, action, hid)
        try:
            out = await run_in_threadpool(write)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=_write_status(e))
        return JSONResponse(out)

    async def api_feedback(self, request):
        """문의(desk-0). GET = 「내 문의」(본인 글·상태, 운영자 메모 없음) — 관리자면 「문의함」(전체 최근 100건·이메일·메모 + admin_tickets)
        · POST {kind, text, page} = 접수. POST 는 세션 + Origin. 길이는 write_gate(전역 DB 크기는 면제 — QUOTA_EXEMPT), 하루 한도는 db.ticket_add."""
        uid, err = await self._need(request)
        if err:
            return err
        if request.method == "GET":
            if self._ticket_admin(request, "ticket.read_all"):   # 재인증 창 밖이면 비관리자 응답과 같은 모양
                out, counts = await run_in_threadpool(lambda: (db.tickets_all(), db.tickets_admin_counts()))
                return JSONResponse({"tickets": out, "admin": True, "admin_tickets": counts}, headers={"cache-control": "no-store"})
            out = await run_in_threadpool(db.tickets_mine, uid)
            return JSONResponse({"tickets": out}, headers={"cache-control": "no-store"})
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict)
            kind, text, page = body.get("kind"), body.get("text"), body.get("page", "other")
            assert isinstance(kind, str) and isinstance(text, str) and isinstance(page, str)
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문(kind·text·page 문자열)이 필요해요."}, status_code=400)
        fam = ua_family(request.headers.get("user-agent"))

        def write():
            db.write_gate("feedback_send", {"kind": kind, "text": text, "page": page}, uid, quota="feedback_send" not in db.QUOTA_EXEMPT)
            return db.ticket_add(uid, kind, text, "web", page=page, ua_family=fam)
        try:
            out = await run_in_threadpool(write)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse({**out, "notice": db.NOTICE})

    async def _admin_write(self, request, fields, fn):
        """관리자 문의 쓰기 공통 — 세션(401) → Origin(403) → 관리자(403 FORBIDDEN) → 재인증 ticket.update 창(403 REAUTH-REQUIRED)
        → JSON 본문 문자열 필드 → fn(...). 문의 변경은 감사 동작이 아니다 — tickets.updated 만 바뀐다."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        if getattr(request.state, "role", None) != "admin":
            return JSONResponse({"code": "FORBIDDEN", "msg": "운영자만 할 수 있어요."}, status_code=403)
        if not self._ticket_admin(request, "ticket.update"):
            return JSONResponse({"code": "REAUTH-REQUIRED", "msg": "다시 로그인한 뒤 할 수 있어요.", "action": "/admin/reauth"},
                                status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict)
            args = [body.get(f) for f in fields]
            assert all(isinstance(a, str) for a in args)
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": f"JSON 본문({'·'.join(fields)} 문자열)이 필요해요."}, status_code=400)
        try:
            out = await run_in_threadpool(fn, *args)
        except db.CanonError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=404 if e.code == "NOT-FOUND" else 400)
        return JSONResponse(out)

    async def api_feedback_status(self, request):
        """관리자 — 문의 상태 바꾸기 {id, status}. 종결이면 closed_at, 다시 열면 NULL(db.ticket_set_status)."""
        return await self._admin_write(request, ("id", "status"), db.ticket_set_status)

    async def api_feedback_note(self, request):
        """관리자 — 운영자 메모 {id, admin_note}(2KB 바이트). 사용자에게 안 보인다."""
        return await self._admin_write(request, ("id", "admin_note"), db.ticket_set_note)

    # ── /admin (β2) ────────────────────────────────────
    # 서버 렌더 + form POST 만(스크립트 0). 비관리자·무세션·DEV → 404. 재인증 창은 REAUTH_WINDOWS(동작 기준).
    async def _admin_who(self, request) -> str | None:
        """관리자 세션 uid. DEV·무세션·비관리자(차단 포함 — uid_of 가 이미 거른다)면 None."""
        if self.dev:
            return None
        uid = await self.uid_of(request)
        return uid if uid is not None and getattr(request.state, "role", None) == "admin" else None

    async def admin_index(self, request):
        """/admin → /admin/setup(overview 는 PR 4). 화면 창(admin.view) 밖이면 바로 /admin/reauth."""
        if await self._admin_who(request) is None:
            return _not_found()
        if not reauth_ok(request.state.ra, "admin.view"):
            return RedirectResponse("/admin/reauth", status_code=302)
        return RedirectResponse("/admin/setup", status_code=302)

    async def admin_reauth(self, request):
        """재인증 시작 — 로그인 폼을 강제(fresh=1)하고 콜백이 같은 uid 면 ra 를 준다."""
        if await self._admin_who(request) is None:
            return _not_found()
        return RedirectResponse("/login?fresh=1&reauth=1", status_code=302)

    async def admin_setup(self, request):
        uid = await self._admin_who(request)
        if uid is None:
            return _not_found()
        ra = request.state.ra
        if not reauth_ok(ra, "admin.view"):
            return RedirectResponse("/admin/reauth", status_code=302)
        users, audit = await run_in_threadpool(lambda: (db.admin_users(), db.admin_audit_list()))
        now = time.time()
        risky = REAUTH_WINDOWS["role.grant"] - (now - ra)
        view = REAUTH_WINDOWS["admin.view"] - (now - ra)
        risky_txt = f"위험 동작 {_left(risky)} 남음" if risky >= 0 else '<span class="warn">위험 동작(부여·회수·끊기·차단)은 다시 로그인해야 해요</span>'
        bar = (f'<div class="bar"><span>재인증: {risky_txt} · 화면 {_left(view)} 남음</span>'
               f'<a href="/admin/reauth">다시 로그인</a><a href="/app">대시보드로</a></div>')
        ver = db.CONSENT_VERSIONS.get("privacy")

        def btn(action, label, fields, danger=False):
            hidden = "".join(f'<input type="hidden" name="{_esc(k)}" value="{_esc(v)}">' for k, v in fields.items())
            cls = ' class="danger"' if danger else ""
            return f'<form method="post" action="{action}">{hidden}<button type="submit"{cls}>{_esc(label)}</button></form>'

        rows = []
        for u in users:
            is_admin = u["role"] == "admin"
            role_tag = '<span class="tag admin">관리자</span>' if is_admin else '<span class="tag">사용자</span>'
            consent = "—" if u["consented"] is None else ("동의" if u["consented"] else '<span class="muted">미동의</span>')
            blocked = f'<span class="tag blocked">차단 {_esc(_kst(u["blocked_at"]))}</span>' if u["blocked_at"] else ""
            acts = [btn("/admin/role", "관리자 회수", {"uid": u["id"], "role": "user"}, True) if is_admin
                    else btn("/admin/role", "관리자 부여", {"uid": u["id"], "role": "admin"}),
                    btn("/admin/signout", "웹 로그인 끊기", {"uid": u["id"]}),
                    btn("/admin/unblock", "차단 해제", {"uid": u["id"]}) if u["blocked_at"]
                    else btn("/admin/block", "차단", {"uid": u["id"]}, True)]
            rows.append(f'<tr><td class="email">{_esc(u["email"])}</td><td>{_esc(_kst(u["created"]))}</td><td>{role_tag}</td>'
                        f'<td>{consent}</td><td>{blocked}</td><td>{_esc(_kst(u["web_revoked_before"]))}</td>'
                        f'<td><div class="acts">{"".join(acts)}</div></td></tr>')

        def who(email, uid_):
            if email:
                return _esc(email)
            return f'<span class="muted">{_esc(uid_[:8])}</span>' if uid_ else '<span class="muted">—</span>'

        arows = "".join(f'<tr><td>{_esc(_kst(a["at"]))}</td><td class="email">{who(a["actor_email"], a["actor_id"])}</td>'
                        f'<td>{_esc(ADMIN_LABELS.get(a["action"], a["action"]))}</td><td class="email">{who(a["target_email"], a["target_id"])}</td></tr>'
                        for a in audit)
        body = (f'<h1>관리 — Setup</h1>{bar}'
                f'<h2>사용자 {len(users)}명</h2><div class="tbl"><table><thead><tr><th>이메일</th><th>가입일</th><th>역할</th>'
                f'<th>처리방침{" " + _esc(ver) if ver else ""}</th><th>차단</th><th>웹 끊은 시각</th><th>동작</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
                f'<h2>감사 로그 최근 {db.AUDIT_LIST}건</h2><div class="tbl"><table><thead><tr><th>시각</th><th>실행자</th><th>동작</th>'
                f'<th>대상</th></tr></thead><tbody>{arows or "<tr><td colspan=4 class=muted>없음</td></tr>"}</tbody></table></div>')
        return _admin_page("관리 — Setup", body)

    async def _admin_form(self, request) -> dict | None:
        """form-urlencoded 본문만, ADMIN_FORM_MAX 바이트까지. 아니면 None."""
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
            return None
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > ADMIN_FORM_MAX:
                return None
        try:
            return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        except (UnicodeDecodeError, ValueError):
            return None

    async def _admin_post(self, request, op: str):
        """/admin POST 공통 — 관리자 세션(아니면 404) → Origin 정확히 일치(403) → form(400) → 재인증 창(403 REAUTH-REQUIRED)
        → db.admin_act(한 트랜잭션) → 303 /admin/setup."""
        uid = await self._admin_who(request)
        if uid is None:
            return _not_found()
        back = '<p><a href="/admin/setup">관리 화면으로</a></p>'
        if not self.same_origin(request):
            return _admin_page("다른 사이트에서 온 요청이에요", f"<h1>다른 사이트에서 온 요청이에요</h1><p>ORIGIN</p>{back}", 403)
        form = await self._admin_form(request)
        role = (form or {}).get("role") if op == "role" else None
        if not form or not form.get("uid") or (op == "role" and role not in ("admin", "user")):
            return _admin_page("요청이 틀렸어요", f"<h1>요청이 틀렸어요</h1><p>ARG-INVALID</p>{back}", 400)
        action = {"signout": "user.web_signout", "block": "user.block", "unblock": "user.unblock"}.get(op) \
            or ("role.grant" if role == "admin" else "role.revoke")
        if not reauth_ok(request.state.ra, action):
            return _admin_page("다시 로그인이 필요해요", '<h1>다시 로그인이 필요해요</h1><p>REAUTH-REQUIRED — 이 동작은 15분 안에 다시 로그인한 '
                               '세션에서만 돼요.</p><p><a href="/admin/reauth">다시 로그인</a></p>', 403)
        try:
            await run_in_threadpool(db.admin_act, uid, op, form["uid"], role)
        except db.CanonError as e:
            status = {"NOT-FOUND": 404, "FORBIDDEN": 403, "LAST-ADMIN": 409}.get(e.code, 400)
            return _admin_page("처리하지 못했어요", f"<h1>처리하지 못했어요</h1><p>{_esc(e.code)} — {_esc(e.message)}</p>"
                               f"{'<p>' + _esc(e.action) + '</p>' if e.action else ''}{back}", status)
        return RedirectResponse("/admin/setup", status_code=303)

    async def admin_role(self, request):
        return await self._admin_post(request, "role")

    async def admin_signout(self, request):
        return await self._admin_post(request, "signout")

    async def admin_block(self, request):
        return await self._admin_post(request, "block")

    async def admin_unblock(self, request):
        return await self._admin_post(request, "unblock")

    def routes(self) -> list[Route]:
        u = _usage
        return [
            Route("/", self.root),
            Route("/app", self.app),
            Route("/help", self.help),
            Route("/privacy", self.privacy),
            Route("/consent", self.consent),
            Route("/assets/{name}", self.asset),
            Route("/login", self.login),
            Route("/auth/callback", self.callback),
            Route("/logout", self.logout, methods=["POST"]),
            Route("/api/state", u("web:state", self.api_state)),
            Route("/api/journal", u("web:journal", self.api_journal)),
            Route("/api/candidate/decide", u("web:candidate_decide", self.api_decide), methods=["POST"]),
            Route("/api/item", u("web:item", self.api_item)),
            Route("/api/item/decide", u("web:item_decide", self.api_item_decide), methods=["POST"]),
            Route("/api/feedback", u("web:feedback", self.api_feedback), methods=["GET", "POST"]),
            Route("/api/feedback/status", u("web:feedback_status", self.api_feedback_status), methods=["POST"]),
            Route("/api/feedback/note", u("web:feedback_note", self.api_feedback_note), methods=["POST"]),
            Route("/api/consent", u("web:consent", self.api_consent), methods=["POST"]),
            Route("/admin", self.admin_index),
            Route("/admin/reauth", self.admin_reauth),
            Route("/admin/setup", self.admin_setup),
            Route("/admin/role", self.admin_role, methods=["POST"]),
            Route("/admin/signout", self.admin_signout, methods=["POST"]),
            Route("/admin/block", self.admin_block, methods=["POST"]),
            Route("/admin/unblock", self.admin_unblock, methods=["POST"]),
        ]
