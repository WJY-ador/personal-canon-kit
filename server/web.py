"""웹 화면 — AuthKit 로그인(OAuth 2.1 공개 클라이언트 + PKCE), 서명 쿠키 세션, 화면 API.

로그인 흐름: /login → AuthKit /oauth2/authorize (resource=<PUBLIC_URL>/mcp) → /auth/callback → 토큰 교환 →
받은 액세스 토큰을 MCP 와 같은 AuthKitVerifier·user_id_for 로 검증·연결 → 세션 쿠키.
웹 사용자 = MCP 사용자(같은 sub). 클라이언트는 AuthKit DCR 로 한 번 등록한 공개 클라이언트(AUTHKIT_WEB_CLIENT_ID).

상태는 서버 메모리에 두지 않는다(Render Free 는 유휴 때 죽는다). PKCE verifier·state 는 10분짜리 서명 쿠키, 세션은 7일 서명 쿠키.
세션 쿠키 v2(β2): {uid, iat, exp} — 요청마다 users 행 1건으로 차단·웹 끊기(web_revoked_before)를 본다. 옛 쿠키에 남은 ra 키는 읽지 않는다.
환경변수: AUTHKIT_WEB_CLIENT_ID · CRM_SESSION_SECRET (둘 다 없으면 웹 로그인만 503, MCP 는 산다).
"""
import asyncio
import base64
import datetime as _dt
import hashlib
import hmac
import html as _html
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import activity
import admin_db
import auth
import contract
import db
import imports
import service_schema
import structure_svg
from envnames import env

SESSION_COOKIE = "crm_session"
FLOW_COOKIE = "crm_login"
SESSION_TTL = 7 * 24 * 3600   # 세션 쿠키 7일(PR①, 30일에서 줄임)
FLOW_TTL = 600
APP_HTML = Path(__file__).parent / "app.html"
START_HTML = Path(__file__).parent / "start.html"
ACTIONS = ("confirm", "exclude", "pending", "edit", "edit_confirm")
# 도움말 정본은 레포의 guides/. 웹 /help 와 MCP help_get 이 같은 것을 읽는다. CRM_GUIDES_DIR 가 있으면 그 폴더.
# Render 는 레포 루트에서 빌드·실행한다(render.yaml 에 rootDir 없음) — rootDir=server 면 guides/ 가 안 올라간다.
GUIDES_DIR = Path(env("GUIDES_DIR") or Path(__file__).parent.parent / "guides")
HELP_FILES = {"start": "09-hosted-start.md", "words": "10-words.md"}
_HELP_ANCHORS = {**{f: f"#{k}" for k, f in HELP_FILES.items()}, "11-privacy.md": "/privacy"}
# 개인정보 처리방침 /privacy — 정본은 guides/11-privacy.md. help_get(MCP)에는 넣지 않는다(HELP_FILES 밖).
# 권리 행사 경로는 대시보드 「문의」 하나. 문안의 {{CONTACT_LINE}} 은 env CRM_CONTACT(선택)가 있으면 보조 메일 한 줄, 없으면 빈 줄로 바꾼다(레포에 연락처를 적지 않는다).
PRIVACY_FILE = "11-privacy.md"
# 처리방침(·약관) 버전 — 첫 20줄 머리글 `vN · 날짜` 에서 뽑아 db.CONSENT_VERSIONS 에(/privacy·화면 표시용, 쓰기 게이트 없음). terms 는 파일이 있을 때만.
CONSENT_FILES = {"privacy": PRIVACY_FILE, "terms": "12-terms.md"}
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


# 웹 HTML 응답(/, /app, /help, /privacy, _page)에만 붙인다. MCP·/health·JSON API·리다이렉트는 그대로.
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
    """env CRM_CONTACT(선택, 한 줄로 접음)가 있으면 보조 메일 한 줄. 없으면 빈 문자열 — 처리방침에 메일 줄이 없다."""
    contact = " ".join(env("CONTACT", "").split())
    return f"로그인할 수 없으면 메일로도 요청할 수 있습니다(보조 경로). 가입한 이메일 주소에서 보낸 메일만 처리합니다. 메일: {contact}" if contact else ""


_CODE_RE = re.compile(r"^[A-Z][A-Z-]*$")
JOURNAL_PAGE = 50   # S.journal = db.records(uid) 기본 limit 과 같은 값


def record_cursor(row):
    """기록 목록 커서 — 불투명 "<created iso>|<id>". /api/state journal_next_before 와 /api/records next_before 가 이 함수 하나로 만든다.
    화면은 이 값을 그대로 /api/records?before= 로 되돌려 보낸다(db.records 의 keyset (created,id))."""
    return f"{row['created']}|{row['id']}"


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


# ── /admin form 상한 (β2) ─────────────────────────────
TICKET_FORM_MAX = 16 * 1024         # /admin/tickets POST — 답변 4KB(UTF-8 바이트)를 퍼센트 인코딩하면 최대 3배라 따로 둔다
ADMIN_FORM_MAX = 4 * 1024           # /admin POST form 본문 상한(바이트) — BodyLimit(/mcp·/api/*) 밖이라 핸들러가 직접 센다


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _not_found() -> Response:
    """/admin 의 비관리자·무세션·DEV 응답 — 없는 경로와 같은 모양."""
    return Response("Not Found", status_code=404, media_type="text/plain; charset=utf-8")


ADMIN_LABELS = {"bootstrap": "첫 지정", "recover": "복구(CLI)", "role_grant": "관리자 부여", "role_revoke": "관리자 회수",
                "web_signout": "웹 로그인 끊기", "block": "차단", "unblock": "차단 해제",
                "delete_notify": "삭제 통지(CLI)", "delete_run": "계정 삭제(CLI)", "delete_cancel": "삭제 취소(CLI)",
                # board 이전(2026-10-20) — 장치 토큰은 사용자 본인이 웹에서, 이전은 원작자가 CLI 로. 감사 detail 에 토큰·본문 없음
                "device_connect": "장치 연결", "device_reissue": "장치 토큰 재발급", "device_revoke": "장치 연결 해제",
                "device_policy": "장치 허용 규칙 변경", "migration_apply": "board 이전 적용(CLI)",
                "migration_extend": "병행 기간 연장(CLI)", "migration_close": "병행 종료(CLI)"}
# 연결 장치 허용 규칙의 변환 이름 — 화면 표시용(키 = imports.TRANSFORMS). 모르는 키는 키 그대로 보인다
POLICY_MAX_BYTES = 4 * 1024   # 장치 허용 규칙(policy) JSON 상한 — WRITE_LIMITS 밖이라 웹이 write_gate 전에 직접 센다
TRANSFORM_LABELS = {"board-likes/1": "좋아요 활동(바로 저장)", "self-text/1": "내 글(승인 대기)", "self-summary/1": "AI 요약(승인 대기)"}
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
.tag.admin{background:#DCEBE3;color:#186A4B}.tag.blocked{background:#F6DDD6;color:#9A3520}.muted{color:#8A8178}
.tag.over{background:#F6DDD6;color:#9A3520}.tag.due{background:#FBEBD0;color:#8A5A12}.tag.pub{background:#DCEBE3;color:#186A4B}
.tag.int{background:#E6E1F2;color:#4D3F86}.tag.st-done,.tag.st-wontfix{background:#EEE7DE;color:#6B635A}.tag.st-open{background:#FBEBD0;color:#8A5A12}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:12px 0;padding:10px 12px;background:#fff;border:1px solid #E4DCD2;border-radius:8px}
dl.meta dt{color:#6B635A;font-size:12px}dl.meta dd{margin:0;word-break:break-all}
pre.txt{white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid #E4DCD2;border-radius:8px;padding:12px;font:inherit;margin:0}
.path{display:flex;flex-wrap:wrap;gap:4px}.path button{font-size:13px;padding:5px 12px}
.path button.cur{background:#186A4B;border-color:#186A4B;color:#fff;cursor:default}
form.edit textarea{width:100%;box-sizing:border-box;font:inherit;padding:8px;border:1px solid #CFC4B7;border-radius:6px}
form.edit div{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin-top:4px}form.bar label{font-size:13px}
.shell{display:grid;grid-template-columns:232px minmax(0,1fr);gap:0 24px;max-width:1320px;margin:0 auto;padding:20px 16px 48px}
.shell>.wrap{max-width:none;margin:0;padding:0;min-width:0}
nav.side{align-self:start;position:sticky;top:12px;border:1px solid #E4DCD2;border-radius:8px;background:#fff;padding:12px}
nav.side .brand{font-size:12px;color:#6B635A;font-weight:600;letter-spacing:.02em;margin:0 0 8px}
form.qf{display:flex;flex-wrap:wrap;gap:4px;margin:0 0 10px}form.qf label{width:100%;font-size:12px;color:#6B635A}
form.qf input{flex:1;min-width:0}nav.side ul{list-style:none;margin:0;padding:0}
nav.side li a{display:block;padding:6px 8px;border-left:3px solid transparent;border-radius:0 5px 5px 0;text-decoration:none;color:#2B2824}
nav.side li a:hover{background:#FBF8F4}nav.side li a[aria-current=page]{border-left-color:#B4462F;background:#F7ECE7;color:#9A3520;font-weight:600}
nav.side a.home{display:block;margin:10px 0 0;padding:8px 8px 0;border-top:1px solid #EEE7DE;color:#B4462F;text-decoration:none;font-size:13px}
nav.crumbs{font-size:12px;color:#6B635A;margin:0 0 10px}nav.crumbs a{color:#6B635A}nav.crumbs span[aria-current]{color:#2B2824;font-weight:600}
td.api,span.api{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:#4A443E}
.tbl.fit th,.tbl.fit td{white-space:normal}.tbl.fit td.api{overflow-wrap:anywhere;min-width:7em}.tbl.fit td.num{white-space:nowrap}
ul.found{list-style:none;padding:0;margin:8px 0}ul.found li{padding:8px 10px;margin:0 0 6px;border:1px solid #E4DCD2;border-radius:8px;background:#fff}
ul.found .tag{margin-right:6px}ul.found .note{display:block;margin-top:2px}
@media (max-width:760px){.shell{grid-template-columns:1fr;padding:12px 12px 40px}nav.side{position:static;margin:0 0 16px}}
.bar b{font-variant-numeric:tabular-nums}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.barw{display:inline-block;width:160px;max-width:30vw;height:10px;background:#EEE7DE;border-radius:3px;vertical-align:middle}
.barf{display:block;height:100%;background:#D98B76;border-radius:3px}
.chips{display:flex;flex-wrap:wrap;gap:6px 8px;margin:8px 0}.chips a{padding:3px 9px;border:1px solid #E4DCD2;border-radius:5px;background:#fff;text-decoration:none}
.chips a[aria-current=page]{border-color:#B4462F;color:#B4462F;font-weight:600}
form.rq{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:flex-end;margin:12px 0;padding:10px 12px;border:1px solid #E4DCD2;border-radius:8px;background:#fff}
form.rq label{display:flex;flex-direction:column;gap:2px;font-size:12px;color:#6B635A}
select,input{font:inherit;font-size:13px;padding:3px 6px;border:1px solid #CFC4B7;border-radius:5px;background:#fff;color:#2B2824}
ul.tree{list-style:none;padding:0;margin:0}ul.tree>li{margin:0 0 10px;padding:8px 10px;border:1px solid #E4DCD2;border-radius:8px;background:#fff}
ul.tree ul{margin:6px 0 0;padding-left:18px}ul.tree ul li{margin:2px 0}.nm{font-family:ui-monospace,Menlo,monospace;font-weight:600}
.lbl{color:#186A4B}.note{font-size:12px;color:#6B635A}.note.warn{color:#B4462F;font-weight:600}
.dmo{background:#fff;border:1px solid #E4DCD2;border-radius:8px;padding:8px}.dmo svg{display:block;width:100%;height:auto;max-width:760px;margin:0 auto}
.dmo a{text-decoration:none}.dmo a:hover circle,.dmo a:focus-visible circle{stroke-width:3}.dmo a:hover line,.dmo a:hover path{stroke-width:2.4}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:#6B635A;margin:8px 0}
.legend i{display:inline-block;width:11px;height:11px;border-radius:50%;border:1.5px solid;vertical-align:-1px;margin-right:4px}
.legend .o-core{background:var(--dmo-core-bg);border-color:var(--dmo-core-line)}.legend .o-standard{background:var(--dmo-standard-bg);border-color:var(--dmo-standard-line)}
.legend .o-template{background:var(--dmo-template-bg);border-color:var(--dmo-template-line)}.legend .o-custom{background:var(--dmo-custom-bg);border-color:var(--dmo-custom-line)}
ul.tree li:target,tr:target td{background:#FFF5E8}ul.tree>li:target{border-color:#B4462F;box-shadow:0 0 0 1px #B4462F}
.err{color:#9A3520;font-weight:600}td.long{white-space:normal;word-break:break-word;min-width:16em}.tag.lock{background:#DCEBE3;color:#186A4B}
@media (max-width:600px){.wrap{padding:16px 12px 40px}th,td{padding:5px 6px}}"""
_ADMIN_CSS += structure_svg.tokens_css(dark_media=False)   # DMO 그래프 토큰(--dmo-*) — 운영 화면은 밝게 고정(OS 미추종, 스크립트 0 이라 토글 없음)

# 왼쪽 세로 메뉴(PR A — Salesforce Setup 꼴) — 메뉴 정본. (현재 표시 키, 경로, 표시명, 설명, Quick Find 고정 별칭).
# 키는 화면 핸들러의 cur 값이다(바꾸지 않는다). 상세 화면은 메뉴를 늘리지 않고 상위 메뉴 활성 + breadcrumb 로 위치를 알린다.
ADMIN_MENU = (("overview", "/admin", "Setup Home", "개요·확인할 오류·문의", ("개요", "홈", "home", "setup home")),
              ("schema", "/admin/schema", "Object Manager", "서비스 표준(코어 + std@1) 정의 조회",
               ("스키마", "오브젝트", "표준", "구조", "필드", "관계", "schema", "object manager")),
              ("users", "/admin/users", "Users", "사용자 목록·사용자 구조 상세", ("사용자", "계정", "구조", "dmo", "users")),
              ("reports", "/admin/reports", "Reports", "데이터셋 리포트", ("리포트", "보고서", "통계", "reports")),
              ("tickets", "/admin/tickets", "Support", "문의·개인정보 요청 처리", ("문의", "개인정보", "답변", "support")),
              ("setup", "/admin/setup", "Administration", "관리자 부여·차단·감사 로그", ("관리자", "차단", "감사", "권한", "administration")))
ADMIN_HOME = ("Setup Home", "/admin")


def _admin_nav(cur: str = "") -> str:
    """왼쪽 메뉴 — Quick Find(GET form, 검색어를 되비추지 않는다 — value 없음) + 메뉴 6개 + 내 대시보드로."""
    links = "".join(f'<li><a href="{href}"{" aria-current=page" if key == cur else ""}>{_esc(text)}</a></li>' for key, href, text, _, _ in ADMIN_MENU)
    qf = (f'<form class="qf" method="get" action="/admin/find" role="search"><label for="qf">Quick Find</label>'
          f'<input id="qf" type="search" name="q" maxlength="{service_schema.FIND_MAX}" placeholder="메뉴·오브젝트·필드" autocomplete="off">'
          f'<button type="submit">찾기</button></form>')
    return f'<nav class="side" aria-label="운영 메뉴"><p class="brand">운영 Setup</p>{qf}<ul>{links}</ul><a class="home" href="/app">내 대시보드로</a></nav>'


def _admin_crumbs(cur: str, crumbs) -> str:
    """breadcrumb — crumbs = [(글자, 링크|None)] (마지막 = 현재 화면). 없으면 cur 메뉴 하나(Setup Home 은 없음)."""
    if crumbs is None:
        m = next((x for x in ADMIN_MENU if x[0] == cur), None)
        if m is None or cur == "overview":
            return ""
        crumbs = [(m[2], None)]
    parts = [f'<a href="{ADMIN_HOME[1]}">{_esc(ADMIN_HOME[0])}</a>']
    parts += [f'<a href="{_esc(href)}">{_esc(text)}</a>' if href else f'<span aria-current="page">{_esc(text)}</span>' for text, href in crumbs]
    return f'<nav class="crumbs" aria-label="위치">{" › ".join(parts)}</nav>'


def _admin_page(title: str, body: str, status: int = 200, cur: str = "", crumbs=None) -> HTMLResponse:
    """/admin 껍데기 — 스크립트 0(인라인·외부·이벤트 속성 없음). 보안 헤더(CSP script-src 'self')는 _html_resp 가 붙인다.
    title 은 escape 한다. body 는 HTML 조각 — 동적 값은 호출하는 쪽이 _esc 로 감싼다. 왼쪽 메뉴(cur = 현재 화면 키) + breadcrumb(crumbs, 선택).
    좁은 화면은 메뉴가 본문 위로 온다(CSS 만, 여닫는 스크립트 없음)."""
    return _html_resp(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title><style>{_ADMIN_CSS}</style></head><body><div class="shell">{_admin_nav(cur)}<main class="wrap">{_admin_crumbs(cur, crumbs)}{body}</main></div></body></html>""",
                      status, headers={"cache-control": "no-store"})


def _kst(d) -> str:
    return d.astimezone(db.KST).strftime("%Y-%m-%d %H:%M") if d else ""


# ── /admin 개요·스키마·리포트 (P4) — 화면 조립. DB 접근은 admin_db 만 ─────────────
import datetime as _dt  # noqa: E402

import template_author  # noqa: E402

SCHEMA_SQL = Path(__file__).parent / "schema.sql"
# 코어 표준 오브젝트 — 사용자가 쓰는 코어 테이블(정의 층이 아닌 것). 근거: db.TARGET_KINDS(record·candidate)·records.question_id·
# app.html LABELS(질문·기록·후보)·문의 섹션(문의, 원작자 2026-10-19)·records.source_id(출처, PR #20). 필드·관계는 schema.sql 에서 읽는다.
# 설정(profile·tabs)·이력·DB 내부(감사·사용량·동의)는 뺀다. 이력 테이블은 오브젝트로 보이지 않고 부모 쪽 관계(CORE_CHILD_LINKS)로만 보인다.
CORE_OBJECTS = (("questions", "질문"), ("records", "기록"), ("candidates", "후보"), ("tickets", "문의"), ("sources", "출처"),
                ("users", "나"), ("people", "사람"), ("source_reflections", "출처 요약/반응"),   # 뒤 셋 = PR B 코어
                ("activities", "활동"))   # board 이전 코어(2026-10-20) — 입력원 열(stream_id)·그 FK 는 CORE_HIDDEN 으로 뺀다
# PR B: 「나」 = users 행이지만 운영 Object Manager 엔 이 열만 보인다(이메일·인증·역할·차단 열은 코어 오브젝트 정의가 아니다)
CORE_ONLY = {"users": frozenset({"display_name", "status", "nav_accepted_at", "created"})}
CORE_LABEL = {"questions": "질문", "records": "기록", "candidates": "후보", "tickets": "문의", "items": "항목"}   # 코어 이름 정본(service_schema 표시명이 이것과 같다 — 시험)
# 표준 구조에 보이지 않는 열 — 운영자 전용(문의 내부 메모 admin_note · 개인정보 의심 표식 hint). 소유자 열 user_id 는 모든 오브젝트에서 뺀다.
CORE_HIDDEN = {"tickets": frozenset({"admin_note", "hint"}),
               "activities": frozenset({"stream_id"})}   # board 이전: 입력원(장치·이전)은 오브젝트가 아니다 — 열과 그 FK 관계를 함께 뺀다
# 부모 쪽에 보일 자식(이력) 관계 — 자식 테이블의 열 단위 REFERENCES 를 schema.sql 에서 읽는다.
CORE_CHILD_LINKS = {"tickets": ("ticket_history",)}
_REF_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s+\w+.*\bREFERENCES\s+(\w+)\s*\([^)]*\)(.*)$", re.I)
_FK_RE = re.compile(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)(.*)", re.I)
_COL_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s+([a-z][a-z0-9_]*)\b")   # 열 정의 줄(소문자 이름·타입). 제약·이어지는 줄은 대문자로 시작한다
_IN_RE = re.compile(r"CHECK\s*\(\s*([a-z_][a-z0-9_]*)\s+IN\s*\(([^)]*)\)", re.I)   # 열거 CHECK(열 IN (…)) — 선택지 개수만 쓴다


def core_schema(sql_text: str) -> list[dict]:
    """schema.sql 에서 CORE_OBJECTS 의 열·관계를 뽑는다. [{table, label, fields: [{name, type, required, choices}], links: [{field, target, kind, required}]}].
    user_id(소유자 열)·users 참조는 뺀다. 관계 kind = ON DELETE CASCADE 면 M-D, 아니면 Lookup. choices = 열거 CHECK 의 값 개수(없으면 None).
    links.required = 참조 열이 전부 NOT NULL(기록 → 항목 연결은 정의에 없어 None)."""
    out = []
    for table, label in CORE_OBJECTS:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql_text, re.S)
        if not m:
            raise ValueError(f"schema.sql 에 {table} 이 없어요")
        fields, links, enums = [], [], {}
        for line in m.group(1).splitlines():
            line = line.split("--", 1)[0].strip().rstrip(",")
            if not line:
                continue
            fk = _FK_RE.search(line)
            col = _COL_RE.match(line)
            en = _IN_RE.search(line)
            if not col:
                if en:
                    enums[en.group(1)] = len(re.findall(r"'[^']*'", en.group(2)))
                if fk and fk.group(2) != "users":
                    cols = [c.strip() for c in fk.group(1).split(",") if c.strip() != "user_id"]
                    if cols and all(c in CORE_HIDDEN.get(table, ()) for c in cols):   # 숨긴 열의 관계는 정의에 보이지 않는다
                        continue
                    req = {f["name"]: f["required"] for f in fields}
                    links.append({"field": ", ".join(cols), "target": fk.group(2),
                                  "kind": "M-D" if "ON DELETE CASCADE" in fk.group(4).upper() else "Lookup",
                                  "required": all(req.get(c, False) for c in cols)})
                continue
            name, typ = col.group(1), col.group(2)
            if name == "user_id" or name in CORE_HIDDEN.get(table, ()) or (table in CORE_ONLY and name not in CORE_ONLY[table]):
                continue
            fields.append({"name": name, "type": typ, "required": "NOT NULL" in line.upper() or "PRIMARY KEY" in line.upper(),
                           "choices": len(re.findall(r"'[^']*'", en.group(2))) if en and en.group(1) == name else None})
        for f in fields:
            if f["name"] in enums:
                f["choices"] = enums[f["name"]]
        for child in CORE_CHILD_LINKS.get(table, ()):   # 자식(이력) → 이 오브젝트. ON DELETE CASCADE 면 M-D
            cm = re.search(rf"CREATE TABLE IF NOT EXISTS {child} \((.*?)\n\);", sql_text, re.S)
            if not cm:
                raise ValueError(f"schema.sql 에 {child} 이 없어요")
            for cl in cm.group(1).splitlines():
                rf = _REF_RE.match(cl.split("--", 1)[0].strip().rstrip(","))
                if rf and rf.group(2) == table:
                    links.append({"field": f"{child}.{rf.group(1)}", "target": table,
                                  "kind": "M-D" if "ON DELETE CASCADE" in rf.group(3).upper() else "Lookup",
                                  "required": "NOT NULL" in cl.upper()})
        if table == "records":   # 기록 출발 관계는 item_links(field_key = attach_to·axes) — db.RECORD_LINK_KEYS
            links.append({"field": " · ".join(db.RECORD_LINK_KEYS), "target": "items", "kind": "Lookup(item_links)", "required": None})
        out.append({"table": table, "label": label, "fields": fields, "links": links})
    return out


def template_schema() -> dict:
    """동결 템플릿(author@1.json) — 코드 정의. 이름(label)은 코드에 있는 기본 이름이다(사용자가 바꾼 이름이 아니다)."""
    return json.loads(template_author.FROZEN_PATH.read_text(encoding="utf-8"))


def _yn_mark(v) -> str:
    return "예" if v else ""


def _vlabel(dim: str, v) -> str:
    """리포트 차원 값 → 화면 글자(escape 전)."""
    if v is None:
        return "—"
    if dim.endswith("week"):
        return f"{v} 주"
    return admin_db.VALUE_LABELS.get(dim, {}).get(v, str(v))


def _bar(v: int, mx: int) -> str:
    pct = 0 if not mx else max(1, round(100 * v / mx)) if v else 0
    return f'<span class="barw"><span class="barf" style="width:{pct}%"></span></span>'


def _report_href(q: dict) -> str:
    return "/admin/reports?" + urlencode(q)


def preset_links(today=None) -> list[tuple[str, str, str]]:
    """[(키, 글자, URL)] — 개요·리포트 상단 프리셋. 날짜 창은 오늘 기준."""
    return [(k, label, _report_href(admin_db.preset_params(p, today))) for k, label, p in admin_db.REPORT_PRESETS]


def _preset(key: str) -> dict:
    return next(p for k, _, p in admin_db.REPORT_PRESETS if k == key)


def _table(head: list[str], rows: list[str], empty: str = "없음", cls: str = "") -> str:
    """head 는 이미 HTML(<th> 조각) · rows 는 <tr> 조각. cls = 래퍼 추가 클래스(fit = 칸 줄바꿈 — 넓은 화면에서 가로 넘침 0)."""
    body = "".join(rows) or f'<tr><td colspan="{len(head)}" class="muted">{_esc(empty)}</td></tr>'
    return f'<div class="tbl{" " + cls if cls else ""}"><table><thead><tr>{"".join(head)}</tr></thead><tbody>{body}</tbody></table></div>'


def render_report_table(out: dict) -> str:
    """aggregate 결과 → 표 + CSS 막대(첫 측정, 최대값 대비). 출력은 전부 _esc."""
    spec = admin_db.report_spec(out["ds"])
    dims, ms = out["dims"], out["measures"]
    mx = max((r["values"][ms[0]] for r in out["rows"]), default=0)
    head = [f"<th>{_esc(spec['dims'][d])}</th>" for d in dims] or ["<th>범위</th>"]
    head += [f'<th class="num">{_esc(spec["measures"][m]["label"])}</th>' for m in ms] + ["<th></th>"]
    rows = []
    for r in out["rows"]:
        keys = "".join(f"<td>{_esc(_vlabel(d, k))}</td>" for d, k in zip(dims, r["keys"])) or "<td>전체</td>"
        vals = "".join(f'<td class="num">{_esc(r["values"][m])}</td>' for m in ms)
        rows.append(f"<tr>{keys}{vals}<td>{_bar(r['values'][ms[0]], mx)}</td></tr>")
    cap = (f'<p class="note warn">상위 {admin_db.REPORT_ROWS}행만 보여요(전체 {_esc(out["total"])}행).</p>' if out["capped"]
           else f'<p class="note">{_esc(out["total"])}행</p>')
    return cap + _table(head, rows, "해당하는 행이 없어요")


def render_measure_notes(ds: str) -> str:
    """지표 정의 표 — 산출 근거·기준 시각·결측·집계식."""
    spec = admin_db.report_spec(ds)
    rows = [f'<tr><td>{_esc(m["label"])}</td><td>{_esc(m["src"])}</td><td>{_esc(m["time"])}</td><td>{_esc(m["missing"])}</td>'
            f'<td>{_esc(m["agg"])}</td></tr>' for m in spec["measures"].values()]
    t = spec["time"] or "지금 상태(기간 없음)"
    return (f'<h2>지표 정의</h2><p class="note">기간 기준: {_esc(t)}</p>'
            + _table(["<th>측정</th><th>산출 근거</th><th>기준 시각</th><th>결측</th><th>집계식</th>"], rows))


def render_report_form(ds: str, spec: dict, q: dict, out: dict) -> str:
    """리포트 form(GET, 스크립트 0) — 그 데이터셋의 허용 차원·필터만 고를 수 있다."""
    def sel(name, label, opts, cur, blank):
        o = f'<option value="">{_esc(blank)}</option>' + "".join(
            f'<option value="{_esc(v)}"{" selected" if v == cur else ""}>{_esc(t)}</option>' for v, t in opts)
        return f'<label>{_esc(label)}<select name="{_esc(name)}">{o}</select></label>'
    dims = list(spec["dims"].items())
    parts = [f'<input type="hidden" name="ds" value="{_esc(ds)}">', sel("g1", "묶기 1", dims, q.get("g1"), "—"),
             sel("g2", "묶기 2", dims, q.get("g2"), "—")]
    parts += [sel(k, spec["dims"][k], [(v, _vlabel(k, v)) for v in vals], q.get(k), "전체") for k, vals in spec["filters"].items()]
    if spec["time"]:
        parts += [f'<label>from<input type="date" name="from" value="{_esc(out["from"])}"></label>',
                  f'<label>to<input type="date" name="to" value="{_esc(out["to"])}"></label>']
    return f'<form class="rq" method="get" action="/admin/reports">{"".join(parts)}<button type="submit">보기</button></form>'


# ── /admin Object Manager(PR A) — 서비스 정의 표시 자료(service_schema) → 화면. 사용자 DB 를 읽지 않는다 ──
STD_PATH = Path(__file__).parent / "templates" / "std@1.json"
_SERVICE = None


def service_def() -> dict:
    """코어(schema.sql) + std@1 + author@1 → 표시 자료. 정의 파일만 읽는다(프로세스당 한 번). 같은 파일 → 같은 자료."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = service_schema.build(core_schema(SCHEMA_SQL.read_text(encoding="utf-8")),
                                        json.loads(STD_PATH.read_text(encoding="utf-8")), template_schema())
    return _SERVICE


_FIND_INDEX = None


def find_index() -> list:
    """Quick Find 색인 — ADMIN_MENU + 서비스 표준 오브젝트(프로세스당 한 번, 정의만)."""
    global _FIND_INDEX
    if _FIND_INDEX is None:
        _FIND_INDEX = service_schema.index([(href, text, desc, aliases) for _, href, text, desc, aliases in ADMIN_MENU], service_def()["objects"])
    return _FIND_INDEX


def _om_yn(v) -> str:
    return _esc(service_schema.UNKNOWN) if v is None else ("예" if v else "아니오")


def _om_choices(n) -> str:
    return "정의 없음" if n is None else f"{_esc(n)}개(값은 보이지 않아요)"


def _om_card(c) -> str:
    return _esc(c) if c else _esc(service_schema.UNKNOWN)


def _om_origin(o: dict) -> str:
    return _esc(structure_svg.ORIGIN_TEXT[o["origin"]])


def _om_link(oid, names: dict, tid=None, text=None) -> str:
    """대상 오브젝트 링크 — 공개 ID 허용목록 안(서비스 오브젝트 또는 그 템플릿 오브젝트)만. 그 밖은 글자만."""
    if oid is None:
        return _esc(text or "")
    href = service_schema.object_href(oid, tid if oid.startswith("tpl-") else None)
    return f'<a href="{_esc(href)}">{_esc(names[oid])}</a>'


def _om_legend(origins=("core", "standard")) -> str:
    return ('<div class="legend">' + "".join(f'<span><i class="o-{k}"></i>{_esc(structure_svg.ORIGIN_TEXT[k])}</span>' for k in origins)
            + f'<span>점선 고리 = {_esc(structure_svg.LOCK_TEXT)}(정의상 잠금)</span>'
            + '<span>선 = 관계(화살표 없음) · 세 갈래 = 여러(N) · 두 줄 = 하나(1) · 참조를 가진 쪽이 N</span></div>')


def _om_objects_table(objs: list, tid=None) -> str:
    rows = [f'<tr><td>{_om_link(o["id"], {o["id"]: o["name"]}, tid)}</td><td class="api">{_esc(o["api"])}</td><td>{_om_origin(o)}</td>'
            f'<td>{_esc(o["version"] or service_schema.UNKNOWN)}</td><td class="num">{len(o["fields"])}</td>'
            f'<td class="num">{len(o["relations"])}</td><td>{_om_yn(o["lock"]["state"])}</td>'
            f'<td class="long">{_esc(o["desc"] or service_schema.UNKNOWN)}</td></tr>' for o in objs]
    return _table(['<th>오브젝트</th><th>API 이름</th><th>구분</th><th>판</th><th class="num">필드</th><th class="num">관계</th><th>정의상 잠금</th><th>서비스 설명</th>'],
                  rows, cls="fit")


def _om_rel_row(o: dict, r: dict, names: dict, tid, n: int, rid: str = "") -> str:
    frm = _om_link(r["from"], names, tid, r["from_text"])
    to = _om_link(r["to"], names, tid, r["to_text"])
    line = "있음" if r["graph"] else "없음(대상이 서비스 오브젝트가 아니에요)"
    attr = f' id="{rid}"' if rid else ""
    return (f'<tr{attr}><td class="num">{n}</td><td>{frm}</td><td>{_esc(r["name"])}</td><td class="api">{_esc(r["api"])}</td><td>{to}</td>'
            f'<td>{_om_card(r["cardinality"])}</td><td>{_om_yn(r["required"])}</td><td>{_esc(r["kind"] or service_schema.UNKNOWN)}</td>'
            f'<td>{_om_yn(r["locked"])}</td><td>{line}</td></tr>')


_OM_REL_HEAD = ['<th class="num">#</th><th>출발</th><th>관계</th><th>API 이름</th><th>대상</th><th>카디널리티</th><th>필수</th><th>관계 종류</th><th>정의상 잠금</th><th>그래프 선</th>']


def render_object_manager() -> str:
    """Object Manager 첫 화면 — 안내 → 코어 + std@1 그래프 → 같은 대상 목록 → 관계 목록 → 별도 템플릿 구역."""
    sd = service_def()
    objs = sd["objects"]
    names = service_schema.names_of(objs)
    resp, hrefs, _ = service_schema.graph(objs)
    rows, g = [], 0
    for o in objs:
        for r in o["relations"]:
            rid = ""
            if r["graph"]:
                rid = structure_svg.rel_anchor(g)
                g += 1
            rows.append(_om_rel_row(o, r, names, None, len(rows) + 1, rid))
    tpl = "".join(f'<li><a href="/admin/schema/templates/{_esc(t["id"])}">{_esc(t["title"])}</a> '
                  f'<span class="muted">오브젝트 {len(t["objects"])}개</span></li>' for t in sd["templates"].values())
    n_core = sum(1 for o in objs if o["origin"] == "core")
    return (f'<h1>Object Manager</h1><p class="note">조회 전용 — 서비스 표준(코어 + std@1)의 정의를 보여 줘요. 편집·설치·편입은 여기서 하지 않아요.</p>'
            f'<p class="note warn">서비스 정의이며, 개별 계정에 설치된 구조와 다를 수 있습니다.</p>'
            f'<p class="note">계정별 구조(설치·편입 상태)는 <a href="/admin/users">Users</a> 상세에서 봐요. '
            f'API 이름은 서비스가 정한 제품 이름(코어 열·표준 key)이에요 — 사용자 데이터가 아니에요.</p>'
            f'<h2>서비스 표준 그래프 — 코어 {n_core} + std@1 {len(objs) - n_core}</h2>{_om_legend()}'
            f'<div class="dmo">{structure_svg.render(resp, hrefs)}</div>'
            f'<p class="note">원을 누르면 그 오브젝트 상세로, 선을 누르면 아래 관계 목록의 그 행으로 가요.</p>'
            f'<h2>오브젝트 {len(objs)}</h2>{_om_objects_table(objs)}'
            f'<h2>관계 {len(rows)}</h2><p class="note">관계 = 참조 하나. 정의에서 확인할 수 없는 값은 「{_esc(service_schema.UNKNOWN)}」로 보여요.</p>'
            + _table(_OM_REL_HEAD, rows, "관계 없음", "fit")
            + f'<h2>선택 템플릿</h2><p class="note">서비스 표준과 따로 보는 선택 정의예요. 현재 표준이 아니에요.</p><ul class="tree">{tpl}</ul>')


def render_object(o: dict, names: dict, tid=None) -> str:
    """오브젝트 상세 — 기본 정보 · 필드 · 관계(나가는·들어오는) · 선택지 · 정의상 잠금. key·선택지 값·사용자 값 없음."""
    lk = o["lock"]
    meta = [("표시명", _esc(o["name"])), ("API 이름", f'<span class="api">{_esc(o["api"])}</span>'), ("서비스 설명", _esc(o["desc"] or service_schema.UNKNOWN)), ("구분", _om_origin(o)),
            ("판", _esc(o["version"] or service_schema.UNKNOWN)), ("정의상 잠금", _om_yn(lk["state"]))]
    if lk["state"] is not None:
        meta += [("잠금 대상", _esc(lk["target"] or "—")), ("잠금 사유", _esc(lk["reason"] or service_schema.UNKNOWN))]
    dl = "".join(f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in meta)
    frows = [f'<tr id="f{j + 1}"><td class="num">{j + 1}</td><td>{_esc(f["name"])}</td><td class="api">{_esc(f["api"])}</td><td>{_esc(f["type"])}</td><td>{_om_yn(f["required"])}</td>'
             f'<td>{_om_yn(f["readonly"])}</td><td>{_om_choices(f["choices"])}</td><td>{_om_yn(f["locked"])}</td></tr>'
             for j, f in enumerate(o["fields"])]
    rrows = [_om_rel_row(o, r, names, tid, j + 1, f"r{j + 1}") for j, r in enumerate(o["relations"])]
    pool = [x for x in (service_def()["templates"][tid]["objects"] if tid else service_def()["objects"]) if x["id"] != o["id"]]
    inc = [(x, r) for x in pool for r in x["relations"] if r["to"] == o["id"]]
    irows = [_om_rel_row(x, r, names, tid, j + 1) for j, (x, r) in enumerate(inc)]
    ch = [f for f in o["fields"] if f["choices"] is not None]
    chs = (" · ".join(f'{_esc(f["name"])} {_esc(f["choices"])}개' for f in ch) + " — 선택지 값은 보이지 않아요.") if ch else "선택지 정의가 있는 필드가 없어요."
    return (f'<h1>{_esc(o["name"])}</h1><dl class="meta">{dl}</dl>'
            f'<p class="note">이 화면은 조회 전용이에요(편집 버튼 없음). 「정의상 잠금」은 계정에서 바꿀 수 없게 정의된 것으로, 조회 전용과 다른 뜻이에요.</p>'
            f'<h2>필드 {len(frows)}</h2>'
            + _table(['<th class="num">#</th><th>표시명</th><th>API 이름</th><th>자료형</th><th>필수</th><th>읽기 전용</th><th>선택지</th><th>정의상 잠금</th>'], frows, "필드 없음", "fit")
            + f'<h2>관계 {len(rrows)}</h2>' + _table(_OM_REL_HEAD, rrows, "나가는 관계 없음", "fit")
            + f'<h2>들어오는 관계 {len(irows)}</h2>' + _table(_OM_REL_HEAD, irows, "들어오는 관계 없음", "fit")
            + f'<h2>선택지</h2><p class="note">{chs}</p>')


def render_template(t: dict) -> str:
    names = {**service_schema.names_of(service_def()["objects"]), **service_schema.names_of(t["objects"])}
    rels = [(o, r) for o in t["objects"] for r in o["relations"]]
    rows = [_om_rel_row(o, r, names, t["id"], n + 1) for n, (o, r) in enumerate(rels)]
    return (f'<h1>{_esc(t["title"])}</h1><p class="note">선택 템플릿 — 서비스 표준(코어 + std@1)이 아니에요. 숙제·발행·접근 방식 같은 선택 정의는 여기서만 봐요. 조회 전용이에요.</p>'
            f'<h2>오브젝트 {len(t["objects"])}</h2>{_om_objects_table(t["objects"], t["id"])}'
            f'<h2>관계 {len(rows)}</h2>' + _table(_OM_REL_HEAD, rows, "관계 없음", "fit"))


# ── /admin/users 운영자 사용자 뷰(PR #20 Q5) — 구조 글자는 전부 contract.respond(…, admin_*) 출력에서 온다 ──
# 이름 = 이름 공유 유효 동의자의 현재 label(admin_consent) · 그 밖엔 자리표(「오브젝트 1」·「필드 a」)와 편입된 표준명뿐.
# key·선택지 값·출처 name·location·사용자 id 는 계약 출력에 없어 화면에도 없다. 그래프 = structure_svg.render(같은 응답).
UV_INTEGRITY = {"REF-DANGLING": "없는 대상 참조", "REF-MISMATCH": "대상 정의 불일치", "MD-ORPHAN": "M-D 위반"}
UV_WHY = {"target_def": "관계 정의의 대상 오브젝트가 없다(보관됨)", "target": "대상 항목이 보관·적용 전이거나 대상 기록이 지워졌다",
          "kind": "실제 도착 종류가 정의와 다르다", "object": "도착 항목의 오브젝트가 정의의 대상과 다르다",
          "candidate": "근거 기록이 지워진 후보", "no_def": "정의 밖 연결 — 보관된 필드·오브젝트에서 출발",
          "any_item": "대상 오브젝트를 정하지 않은 항목 필드 — 오브젝트 일치를 볼 수 없다"}
UV_LOCK = f'<span class="tag lock">{_esc(structure_svg.LOCK_TEXT)}</span>'


def uv_build(v: dict, onames=(), fnames=()) -> tuple[dict, dict]:
    """사용자 뷰 원행 → (계약 응답, 관계별 연결·무결성). 유효 동의(names)면 admin_consent, 아니면 admin_noconsent.
    이름은 이름 분기 행(동의를 같은 문장에서 다시 본다)에서만 온다 — 두 조회 사이에 철회되면 label 이 None 으로 남아 보이지 않는다."""
    inp = admin_db.contract_input(v, onames, fnames)
    viewer = "admin_consent" if v["names"] else "admin_noconsent"
    return contract.respond(inp, viewer), admin_db.link_stats(v, inp)


def _uv_std(std: dict) -> str:
    """표준 상태 — 코드만. 표식 없으면 version·state 는 —."""
    objs = " · ".join(f"{k} {v}" for k, v in std["objects"].items())
    return f'{_esc(std["version"] or "—")} {_esc(std["state"] or "—")} · {_esc(objs)}'


def _uv_name(d: dict) -> str:
    """오브젝트·필드 한 칸 — 자리표 + 편입된 표준명 + (동의면) 현재 label. 동적 글자는 _esc."""
    out = f'<span class="nm">{_esc(d["ref"])}</span>'
    if d.get("std_name"):
        out += f' <span class="tag">표준 {_esc(d["std_name"])}</span>'
    if d.get("label") is not None:
        out += f' <span class="lbl">「{_esc(d["label"])}」</span>'
    return out


def _uv_where(resp: dict, rel, why: str = "") -> str:
    if rel is None:
        return "코어 후보" if why == "candidate" else "관계 밖"
    r = resp["relations"][rel]
    return f'관계 {rel + 1}({_esc(r["from"])}.{_esc(r["field"])})'


def _uv_target(resp: dict, st: dict, i: int) -> str:
    r = resp["relations"][i]
    if r["to"] in structure_svg.CORE_TEXT:
        return f'코어 {_esc(structure_svg.CORE_TEXT[r["to"]])}'
    if r["to"] is not None:
        o = next(x for x in resp["objects"] if x["ref"] == r["to"])
        return _uv_name({k: o.get(k) for k in ("ref", "std_name", "label")})
    gone = any(e["rel"] == i and e["why"] == "target_def" for e in st["errors"])
    return '<span class="err">대상 정의 없음</span>' if gone else '<span class="muted">항목(오브젝트 미지정)</span>'


def render_userview(u: dict, opaque: str, named: bool, resp: dict, st: dict, tickets: list) -> str:
    """상세 본문 — 계정 요약 → DMO 그래프 → 관계별 집계 → 무결성 → 표준 상태 → 구조 트리 → 문의 요약."""
    rels, cnt = resp["relations"], resp["counts"]
    err_n = [sum(e["n"] for e in st["errors"] if e["rel"] == i) for i in range(len(rels))]
    unc_n = [sum(e["n"] for e in st["unchecked"] if e["rel"] == i) for i in range(len(rels))]
    m = sum(1 for n in st["linked"] if n)
    summary = (f'<dl class="meta"><dt>이메일</dt><dd>{_esc(u["email"])}</dd><dt>가입일</dt><dd>{_esc(_kst(u["created"])[:10])}</dd>'
               f'<dt>역할</dt><dd>{"관리자" if u["role"] == "admin" else "사용자"}{" · 차단" if u["blocked_at"] else ""}'
               f'{" · 삭제됨" if u.get("status") == "deleted" else ""}</dd>'
               f'<dt>구조 이름 공유</dt><dd>{"동의 — 현재 이름을 함께 보여요" if named else "동의 없음 — 자리표와 편입된 표준명만 보여요"}</dd>'
               f'<dt>불투명 id</dt><dd>{_esc(opaque)}</dd>'
               f'<dt>구조</dt><dd>오브젝트 {_esc(cnt["objects"])} · 필드 {_esc(cnt["fields"])} · 관계 {_esc(cnt["relations"])} · '
               f'그중 연결 있음 {_esc(m)} · 출처 {_esc(cnt["sources"])}</dd></dl>')
    legend = ('<div class="legend">' + "".join(f'<span><i class="o-{k}"></i>{_esc(v)}</span>' for k, v in structure_svg.ORIGIN_TEXT.items())
              + f'<span>점선 고리 = {_esc(structure_svg.LOCK_TEXT)}</span>'
              + '<span>선 = 관계(화살표 없음) · 세 갈래 = 여러(N) · 두 줄 = 하나(1) · 참조를 가진 쪽이 N</span></div>')
    rrows = []
    for i, r in enumerate(rels):
        o = next(x for x in resp["objects"] if x["ref"] == r["from"])
        f = next(x for x in o["fields"] if x["ref"] == r["field"])
        rrows.append(f'<tr id="{structure_svg.rel_anchor(i)}"><td class="num">{i + 1}</td>'
                     f'<td><a href="#{structure_svg.node_anchor(resp["objects"].index(o))}">{_uv_name(o)}</a></td><td>{_uv_name(f)}</td>'
                     f'<td>{_uv_target(resp, st, i)}</td><td>{_esc(r["cardinality"])}</td>'
                     f'<td>{_esc(structure_svg.ORIGIN_TEXT.get(r["origin"], r["origin"]))}</td>'
                     f'<td>{UV_LOCK if r["locked"] else ""}</td>'
                     + ('<td class="num">—</td><td class="num">—</td><td class="num">—</td></tr>' if st["linked"][i] is None else   # 사람·출처·활동 대상 = 개수 비공개
                        f'<td class="num">{_esc(st["linked"][i])}</td>'
                        f'<td class="num">{f"<span class=err>{err_n[i]}</span>" if err_n[i] else "0"}</td><td class="num">{_esc(unc_n[i])}</td></tr>'))
    erows = []
    for code, lab in UV_INTEGRITY.items():
        es = [e for e in st["errors"] if e["code"] == code]
        tot = sum(e["n"] for e in es)
        where = " · ".join(f'{_uv_where(resp, e["rel"], e["why"])} {_esc(e["n"])}건 — {_esc(UV_WHY[e["why"]])}' for e in es)
        erows.append(f'<tr><td>{_esc(code)}</td><td>{_esc(lab)}</td><td class="num">{f"<span class=err>{tot}</span>" if tot else "0"}</td>'
                     f'<td class="long">{where or "—"}</td></tr>')
    un = st["unchecked"]
    where = " · ".join(f'{_uv_where(resp, e["rel"], e["why"])} {_esc(e["n"])}건 — {_esc(UV_WHY[e["why"]])}' for e in un)
    erows.append(f'<tr><td>{_esc(admin_db.UNCHECKED)}</td><td>검사 불가(정상으로 세지 않음)</td><td class="num">{_esc(sum(e["n"] for e in un))}</td>'
                 f'<td class="long">{where or "—"}</td></tr>')
    std = resp["std"]
    std_t = _table(['<th>표준 오브젝트</th><th>상태</th>'],
                   [f'<tr><td class="nm">{_esc(k)}</td><td>{_esc(v)}</td></tr>' for k, v in std["objects"].items()])
    items = []
    for i, o in enumerate(resp["objects"]):
        fl = "".join(f'<li>{_uv_name(f)} <span class="muted">{_esc(structure_svg.ORIGIN_TEXT.get(f["origin"], f["origin"]))}</span>'
                     f'{" " + UV_LOCK if f["locked"] else ""}'
                     f'{" <span class=lbl>선택지 " + _esc(" · ".join(str(x) for x in f["options"])) + "</span>" if f.get("options") else ""}</li>'
                     for f in o["fields"])
        items.append(f'<li id="{structure_svg.node_anchor(i)}">{_uv_name(o)} <span class="tag">{_esc(o["semantic_kind"])}</span> '
                     f'<span class="muted">{_esc(structure_svg.ORIGIN_TEXT.get(o["origin"], o["origin"]))}</span>'
                     f'{" " + UV_LOCK if o["locked"] else ""}'
                     f'<ul>{fl or "<li class=muted>필드 없음</li>"}</ul></li>')
    items += [f'<li id="{structure_svg.core_anchor(c["kind"])}"><span class="nm">코어 {_esc(structure_svg.CORE_TEXT.get(c["kind"], c["kind"]))}</span>'
              f' <span class="muted">{_esc(structure_svg.ORIGIN_TEXT["core"])} — 참조 필드가 가리키는 공통 테이블</span></li>' for c in resp["core"]]
    tk = _table(['<th>종류</th><th>상태</th><th class="num">n</th>'],
                [f'<tr><td>{_esc(admin_db.VALUE_LABELS["kind"].get(r["kind"], r["kind"]))}</td>'
                 f'<td>{_esc(admin_db.VALUE_LABELS["status"].get(r["status"], r["status"]))}</td><td class="num">{_esc(r["n"])}</td></tr>'
                 for r in tickets], "문의 없음")
    return (f'<p><a href="/admin/users">← 사용자 목록</a></p><h1>사용자 — {_esc(u["email"])}</h1>{summary}'
            f'<h2>DMO 그래프</h2>{legend}<div class="dmo">{structure_svg.render(resp)}</div>'
            f'<p class="note">원을 누르면 구조 트리의 그 오브젝트로, 선을 누르면 관계 집계 행으로 가요. 대상이 없는 관계(오브젝트 미지정·대상 정의 없음)는 선이 없어요.</p>'
            f'<h2>관계별 집계</h2><p class="note">관계 = 참조 필드 하나. 연결 = 살아 있는 항목에서 출발한 정상 연결 수(오류·검사 불가 제외). '
            f'기록에서 출발하는 분류 연결은 관계가 아니라 세지 않아요.</p>'
            + _table(['<th class="num">#</th><th>출발</th><th>필드</th><th>대상</th><th>카디널리티</th><th>구분</th><th>보호</th>'
                      '<th class="num">연결</th><th class="num">오류</th><th class="num">검사 불가</th>'], rrows, "관계 없음")
            + '<h2>무결성</h2><p class="note">검사 불가는 정상이 아니에요 — 정의만으로는 맞는지 볼 수 없는 연결이에요.</p>'
            + _table(['<th>코드</th><th>검사</th><th class="num">건</th><th>위치</th>'], erows)
            + f'<h2>표준 상태</h2><p class="note">설치 버전·표식 상태·표준 오브젝트별 상태 코드.</p>'
            f'<p><span class="nm">{_esc(std["version"] or "—")}</span> · 표식 {_esc(std["state"] or "—")}</p>{std_t}'
            f'<h2>구조 트리</h2><ul class="tree">{"".join(items) or "<li class=muted>적용된 오브젝트 없음</li>"}</ul>'
            f'<h2>문의 요약</h2><p class="note">원문은 <a href="/admin/tickets">문의 화면</a>에서만 봐요.</p>{tk}')


class Web:
    def __init__(self, issuer: str, public_url: str, verifier, dev: bool, dev_user: str):
        self.issuer = issuer.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.verifier = verifier
        self.dev = dev
        self.dev_user = dev_user
        self.client_id = os.environ.get("AUTHKIT_WEB_CLIENT_ID", "").strip()
        self.secret = env("SESSION_SECRET", "").encode()
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
        """처리방침(·약관) 버전을 시작 때 한 번 읽어 db 에 넣는다(/privacy·화면 표시용)."""
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
    def session_value(self, uid: str) -> str:
        """세션 쿠키 v2 값 {uid, iat}. exp 는 sign 이 붙인다(7일)."""
        return self.sign({"uid": uid, "iat": int(time.time())}, SESSION_TTL)

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
        if not row or row["blocked_at"] is not None or row["status"] == "deleted":   # PR B: 삭제된 계정 세션 무효
            return None
        wrb = row["web_revoked_before"]
        if wrb is not None and iat < wrb.timestamp():
            return None
        return row

    async def uid_of(self, request) -> str | None:
        """세션 uid. 찾으면 request.state.uid 에도 둔다 — /api/* 사용 집계 래퍼가 DB 조회 없이 읽는다.
        request.state.role 도 둔다(관리자 판정은 이 값만 읽는다 — 두 번째 조회 없음). 옛 쿠키에 남은 ra 키는 읽지 않는다.
        DEV(dev_user) 경로는 쿠키 없이 그대로 — 단 차단이면 None."""
        request.state.role = None
        s = self.unsign(request.cookies.get(SESSION_COOKIE))
        uid = None
        if s and s.get("uid"):
            row = await self._session_row(s)
            if row:
                uid, request.state.role = row["id"], row["role"]
        elif self.dev and self.dev_user:
            u = await run_in_threadpool(db.user_by_email, self.dev_user)
            if not u.get("blocked_at"):
                uid, request.state.role = u["id"], u.get("role")
        if uid is not None:
            request.state.uid = uid
        return uid

    def same_origin(self, request) -> bool:
        return request.headers.get("origin", "") in self.origins

    # ── 화면 ───────────────────────────────────────────
    async def app(self, request):
        uid = await self.uid_of(request)
        if uid is None:
            return RedirectResponse("/", status_code=302)
        html = _fill_assets(APP_HTML.read_text(encoding="utf-8"))   # 요청마다 읽는다 — 헤더 해시와 본문이 같은 내용에서 나온다
        # Q1: 관리자는 <!--user-->(문의·이름 공유 메뉴·문의 섹션)를, 그 밖은 <!--admin-->(운영 콘솔)를 보내지 않는다. 표식도 남기지 않는다
        drop, keep = ("user", "admin") if getattr(request.state, "role", None) == "admin" else ("admin", "user")
        html = re.sub(rf"<!--{drop}-->.*?<!--/{drop}-->", "", html, flags=re.S).replace(f"<!--{keep}-->", "").replace(f"<!--/{keep}-->", "")
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

    # ── 동의 ─────────────────────────────────────────
    async def consent(self, request):
        """옛 동의 화면 주소(북마크·옛 안내) — 필수 동의 게이트는 없앴다. 항상 302 /app."""
        return RedirectResponse("/app", status_code=302)

    async def api_optional_consent(self, request):
        """선택 동의 켜기/끄기. 세션(401) → Origin(403) → JSON {kind, on(bool)} → db.optional_consent_set(한 트랜잭션·로그).
        같은 상태로 다시 보내면 changed=false(로그 없음)."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict) and isinstance(body.get("kind"), str) and isinstance(body.get("on"), bool)
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문(kind 문자열·on true/false)이 필요해요."}, status_code=400)
        try:
            out = await run_in_threadpool(db.optional_consent_set, uid, body["kind"], body["on"])
        except db.CrmError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    async def api_desk_visibility(self, request):
        """문의 섹션 숨김(계정 메뉴). 세션(401) → Origin(403) → JSON {hidden(bool)}(400) → db.desk_hidden_set.
        숨겨도 메뉴에서 문의를 열 수 있다(화면 쪽)."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict) and isinstance(body.get("hidden"), bool)
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문(hidden true/false)이 필요해요."}, status_code=400)
        out = await run_in_threadpool(db.desk_hidden_set, uid, body["hidden"])
        return JSONResponse(out, headers={"cache-control": "no-store"})

    # ── 로그인 ─────────────────────────────────────────
    async def login(self, request):
        if not self.login_ready:
            return _page("아직 로그인을 열지 않았어요", "<p>운영자가 로그인 설정(AUTHKIT_WEB_CLIENT_ID·CRM_SESSION_SECRET)을 마치면 열려요.</p>", 503)
        verifier = _b64(secrets.token_bytes(48))
        state = _b64(secrets.token_bytes(24))
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        q = {"client_id": self.client_id, "redirect_uri": f"{self.public_url}/auth/callback", "response_type": "code",
             "scope": "openid email", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
             "resource": f"{self.public_url}/mcp"}
        if request.query_params.get("fresh") == "1":   # 로그아웃 직후 — AuthKit 세션이 살아 있어도 로그인 폼을 띄운다
            q["prompt"] = "login"
        flow = {"v": verifier, "s": state}
        resp = RedirectResponse(f"{self.issuer}/oauth2/authorize?{urlencode(q)}", status_code=302)
        self.set_cookie(resp, FLOW_COOKIE, self.sign(flow, FLOW_TTL), FLOW_TTL)
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
        except Exception as e:   # ToolError·CrmError — 메시지만 보인다
            msg = getattr(e, "message", None) or str(e)
            return _page("사용자를 연결하지 못했어요", f"<p>{_esc(msg)}</p>{again}", 502)
        resp = RedirectResponse("/app", status_code=302)
        self.set_cookie(resp, SESSION_COOKIE, self.session_value(uid), SESSION_TTL)
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
    @staticmethod
    def _crm_err(e) -> JSONResponse:
        """쓰기 API 의 CrmError 응답 — NAME-REQUIRED 403 · NOT-FOUND 404 · 그 밖 400. 본문 {code, msg, action}."""
        status = 403 if e.code == contract.NAME_REQUIRED else 404 if e.code == "NOT-FOUND" else 400
        return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=status)

    async def _need(self, request):
        uid = await self.uid_of(request)
        if uid is None:
            return None, JSONResponse({"code": "UNAUTHORIZED", "msg": "로그인이 필요해요."}, status_code=401)
        return uid, None

    async def api_state(self, request):
        uid, err = await self._need(request)
        if err:
            return err
        st, me, flags, std, srcs, cnt, home, people, refl = await run_in_threadpool(lambda: (
            db.state(uid), db.user_get(uid), db.app_user_flags(uid), db.std_status(uid), db.sources(uid), db.source_counts(uid),
            db.me_home(uid), db.people_all(uid), db.reflections_all(uid)))
        # PR① 홈 「더 보기」 — journal(기본 50편) 마지막 행 커서. 50편 미만이면 null(정확히 50 이면 커서 — 다음 호출이 0편+null 일 수 있음). /app 전용
        st["journal_next_before"] = record_cursor(st["journal"][-1]) if len(st["journal"]) >= JOURNAL_PAGE else None
        st["std"] = std   # PR #20 사용자 표준 상태(contract.std_state 모양) — /app 전용, MCP state_get 에는 넣지 않는다
        st["sources"], st["source_counts"] = srcs, cnt   # PR #20 출처(계약 모양 {id,kind,name,location}) · 출처별 기록 수 — 본인만, /app 전용
        st["structure"] = contract.respond({   # PR #20 Q5 구조 정의 보기 — 본인 계약 응답(origin·locked·cardinality 는 서버 판정). /app 전용
            "definitions": st["definitions"], "sources": srcs, "std_choices": std["objects"],
            "std_marks": {"version": std["version"], "state": std["state"]} if std["version"] else None}, "self")
        st["structure_svg"] = structure_svg.render(st["structure"])   # 같은 응답의 DMO 그래프(서버 결정적 배치) — 목록과 같은 순번 앵커. /app 전용
        st["user"] = {"email": me["email"], "struct_names": flags["struct_names"], "desk_hidden": flags["desk_hidden"],   # /app 계정 메뉴용 — MCP state_get 에는 넣지 않는다
                      "display_name": home["display_name"], "nav": home["nav"]}   # PR B: 표시 이름(null = 이름 입력 화면) · 새 탐색 new|old
        st["switches"] = {"converse": db.job_switch_on("converse")}   # PR ②-b — 「대화」 버튼 표시(켜짐일 때만). /app 전용, MCP state_get 에는 넣지 않는다
        st["stack_session"] = await run_in_threadpool(db.stack_session_current, uid)   # ②-b 새로 고침 복원·두 탭 공유 — {id, expires_at} | null
        st["me"] = {"counts": home["counts"]}   # PR B 「나」 홈 개수(판단·기준·결정·사람·출처·요약/반응) — 본인만, /app 전용
        st["people"], st["reflections"] = people, refl   # PR B 코어 사람·출처 요약/반응(본인 것 전부, 최근 순) — /app 전용
        if getattr(request.state, "role", None) == "admin":   # ⚙ Setup 링크용. 관리자가 아니면 키 자체가 없다. MCP state_get 에는 넣지 않는다
            st["user"]["admin"] = True
        # board 이전 — 「나」 홈 활동 집계(30d)·[승인 대기 N]·연결 장치 수. /app 전용. 이 부분만 실패하면 그 칸만 「읽지 못함」
        # (집계 실패가 대시보드 전체를 막지 않는다). 오류 문구엔 예외 이름만 — 값·본문 없음
        # activity_ui = 화면 표시용 서버 상수(행동·제공자 이름, 대기 보관 일수) — 화면이 따로 하드코딩하지 않는다
        st["activity_ui"] = {"actions": dict(activity.ACTION_LABELS), "providers": dict(activity.PROVIDER_LABELS),
                             "pending_days": imports.PENDING_DAYS}
        try:
            st.update(await run_in_threadpool(activity.state_part, uid))
        except Exception as e:
            code = e.code if isinstance(e, db.CrmError) and _CODE_RE.match(str(e.code)) else "EXC"
            db.usage_count(uid, "web:state_activity", code)   # 사용 집계 관례 — 열거 코드만(EXC = 예외), 자유 텍스트 없음
            print(f"[web] /api/state 활동 부분 실패 — {code}", flush=True)
            st.setdefault("read", {})["activity"] = {"ok": False, "error": {"code": code, "msg": ""}}
            st["activity"] = st["inbox"] = st["devices"] = None
        return JSONResponse(st, headers={"cache-control": "no-store"})

    async def api_journal(self, request):
        uid, err = await self._need(request)
        if err:
            return err
        rid = request.query_params.get("id", "")
        try:
            r = await run_in_threadpool(db.record_get, uid, rid)
        except db.CrmError as e:
            return JSONResponse({"code": e.code, "msg": e.message}, status_code=404)
        return JSONResponse({"id": r["id"], "title": r["title"], "body": r["body"],
                             "meta": {"created": r["created"], "source": r["source"], "question_id": r["question_id"]}},
                            headers={"cache-control": "no-store"})

    async def api_records(self, request):
        """GET ?before=<커서>&limit=<1..100> — 기록 목록 이어 받기(PR① 홈 「더 보기」). 행 모양 = /api/state journal. 읽기만.
        응답 {records, next_before}. next_before = 불투명 커서 "<마지막 created iso>|<마지막 id>"(더 없으면 null) — 화면은 받은 값을
        그대로 before 로 되돌려 보낸다. keyset (created,id) 라 created 동률 블록이 경계에 걸쳐도 겹침·누락 없음. limit 범위 밖 = 끝값(_qint)."""
        raw = request.query_params.get("before", "")
        limit = self._qint(request, "limit", 50, 1, 100)

        def read(uid):
            before = before_id = None
            if raw:
                at, sep, rid = raw.rpartition("|")
                try:
                    before = _dt.datetime.fromisoformat(at) if sep and rid else None
                except ValueError:
                    before = None
                if before is None or before.tzinfo is None:
                    raise db.CrmError("ARG-INVALID", "before 는 받은 next_before 값 그대로 보내 주세요.")
                before_id = rid
            rows = db.records(uid, limit + 1, before, before_id)
            page = rows[:limit]
            nxt = record_cursor(page[-1]) if len(rows) > limit else None
            return {"records": page, "next_before": nxt}
        return await self._core_read(request, read)

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
        except db.CrmError as e:
            return self._crm_err(e) if e.code == contract.NAME_REQUIRED else JSONResponse(
                {"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out)

    async def api_item(self, request):
        """항목 하나 + 관계 대상 + 들어오는 관계 + 이력 + 승격 상태(서랍)."""
        uid, err = await self._need(request)
        if err:
            return err
        try:
            out = await run_in_threadpool(db.item_get, uid, request.query_params.get("id", ""))
        except db.CrmError as e:
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
        except db.CrmError as e:
            return self._crm_err(e) if e.code == contract.NAME_REQUIRED else JSONResponse(
                {"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out)

    async def api_std_preview(self, request):
        """표준 편입 미리보기(R3) — 세션 사용자 본인 것만(다른 사용자를 고르는 인자가 없다). 읽기 전용·플래그 무관. 본인 label 이 들어 있다."""
        uid, err = await self._need(request)
        if err:
            return err
        try:
            out = await run_in_threadpool(db.std_preview, uid)
        except db.CrmError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    async def api_std_choose(self, request):
        """표준 오브젝트 선택 — 보류·거절 기록 · 편입 적용. 세션(401) → Origin(403) → JSON {std_key, state 문자열,
        confirmed_meaning bool?, expected_revision 정수|null?}(400) → write_gate → db.std_choose(ARG-INVALID·NAME-REQUIRED 403·STANDARD-LOCKED·
        CONFLICT·DEF-INVALID 400). 편입은 confirmed_meaning=true 와 미리보기에서 본 revision 을 들고 온다. 대상 사용자 = 세션 uid 뿐."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict) and isinstance(body.get("std_key"), str) and isinstance(body.get("state"), str)
            assert isinstance(body.get("confirmed_meaning", False), bool)
            rev = body.get("expected_revision")
            assert rev is None or (isinstance(rev, int) and not isinstance(rev, bool))
        except Exception:
            return JSONResponse({"code": "ARG-INVALID", "msg": "JSON 본문(std_key·state 문자열, confirmed_meaning true/false, "
                                 "expected_revision 정수 또는 null)이 필요해요."}, status_code=400)
        key, state, meant = body["std_key"], body["state"], body.get("confirmed_meaning", False)

        def write():   # 쓰기 진입 — MCP guard 와 같은 db.write_gate(길이·전역 DB 크기)
            db.write_gate("std_choose", {"std_key": key, "state": state}, uid)
            return db.std_choose(uid, key, state, meant, rev)
        try:
            out = await run_in_threadpool(write)
        except db.CrmError as e:
            return self._crm_err(e) if e.code == contract.NAME_REQUIRED else JSONResponse(
                {"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    # ── PR B 코어 쓰기: 나 · 사람 · 출처 · 출처 요약/반응 ──
    # 공통 = 세션(401) → Origin(403) → JSON 본문 모양(400 ARG-INVALID) → db.write_gate(길이 · NAME-REQUIRED 403 · 전역 크기) → db.
    # 대상 사용자 = 세션 uid 뿐(본문에 사용자 id 인자가 없다). 남의 id·없는 id = NOT-FOUND 404(같은 모양).
    async def _core_write(self, request, op: str, shape: dict, call, json_limits: dict | None = None):
        """shape = {키: (타입들, 필수)}. call(uid, body) → db 결과. op = db.write_gate 의 쓰기 이름(NAME_EXEMPT 면 관문 면제).
        json_limits = {키: 바이트} — 문자열이 아닌 값(객체)의 JSON 길이 상한(WRITE_LIMITS 밖 웹 전용 쓰기). 넘으면 400 PAYLOAD-LIMIT(write_gate 전)."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        try:
            body = await request.json()
            assert isinstance(body, dict)
            for k, (types, need) in shape.items():
                v = body.get(k)
                if v is None:
                    assert not need
                else:
                    assert isinstance(v, types) and not (isinstance(v, bool) and bool not in types)
        except Exception:
            want = ", ".join(f"{k}({'/'.join(t.__name__ for t in ts)}{'' if need else ' 선택'})" for k, (ts, need) in shape.items())
            return JSONResponse({"code": "ARG-INVALID", "msg": f"JSON 본문이 필요해요: {want or '{}'}."}, status_code=400)
        for k, lim in (json_limits or {}).items():
            n = len(json.dumps(body.get(k), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if n > lim:
                return JSONResponse({"code": "PAYLOAD-LIMIT", "msg": f"{k} 가 너무 길어요 (상한 {lim} 바이트, 지금 {n} 바이트).",
                                     "action": "줄이거나 나눠서 다시 보내세요."}, status_code=400)

        def write():
            db.write_gate(op, {k: body.get(k) for k in shape if isinstance(body.get(k), str)}, uid)
            return call(uid, body)
        try:
            out = await run_in_threadpool(write)
        except db.CrmError as e:
            return self._crm_err(e)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    async def api_me_name(self, request):
        """POST {display_name} — 표시 이름 저장. 관문 면제(이름을 정하는 경로)."""
        return await self._core_write(request, "display_name_set", {"display_name": ((str,), True)},
                                      lambda uid, b: db.display_name_set(uid, b["display_name"]))

    async def api_nav_accept(self, request):
        """POST {} — 새 탐색 수락(멱등). 표준 편입과 별개."""
        return await self._core_write(request, "nav_accept", {}, lambda uid, b: db.nav_accept(uid))

    async def api_people(self, request):
        """POST {alias} — 사람 만들기."""
        return await self._core_write(request, "person_save", {"alias": ((str,), True)}, lambda uid, b: db.person_save(uid, b["alias"]))

    async def api_person(self, request):
        """POST /api/people/{id} {alias, expected_revision} — 별칭 고치기."""
        pid = request.path_params["pid"]
        return await self._core_write(request, "person_save", {"alias": ((str,), True), "expected_revision": ((int,), True)},
                                      lambda uid, b: db.person_save(uid, b["alias"], pid, b["expected_revision"]))

    async def api_person_delete(self, request):
        """POST /api/people/{id}/delete {expected_revision}."""
        pid = request.path_params["pid"]
        return await self._core_write(request, "person_delete", {"expected_revision": ((int,), True)},
                                      lambda uid, b: db.person_delete(uid, pid, b["expected_revision"]))

    async def api_sources(self, request):
        """POST {kind, name, location?} — 출처 찾거나 만들기."""
        return await self._core_write(request, "source_create",
                                      {"kind": ((str,), True), "name": ((str,), True), "location": ((str,), False)},
                                      lambda uid, b: db.source_create(uid, b["kind"], b["name"], b.get("location") or ""))

    async def api_reflections(self, request):
        """POST {source_id, kind, body} — 출처 요약/반응 만들기(검토 대기)."""
        return await self._core_write(request, "source_reflection_save",
                                      {"source_id": ((str,), True), "kind": ((str,), True), "body": ((str,), True)},
                                      lambda uid, b: db.source_reflection_save(uid, b["source_id"], b["kind"], b["body"]))

    async def api_reflection(self, request):
        """POST /api/reflections/{id} {source_id, kind, body, expected_revision} — 고치기(검토 완료였으면 검토 대기로)."""
        rid = request.path_params["rid"]
        return await self._core_write(request, "source_reflection_save",
                                      {"source_id": ((str,), True), "kind": ((str,), True), "body": ((str,), True),
                                       "expected_revision": ((int,), True)},
                                      lambda uid, b: db.source_reflection_save(uid, b["source_id"], b["kind"], b["body"], rid,
                                                                               b["expected_revision"]))

    async def api_reflection_review(self, request):
        """POST /api/reflections/{id}/review {reviewed, expected_revision} — 검토 완료 / 검토 대기로."""
        rid = request.path_params["rid"]
        return await self._core_write(request, "source_reflection_review",
                                      {"reviewed": ((bool,), True), "expected_revision": ((int,), True)},
                                      lambda uid, b: db.source_reflection_review(uid, rid, b["expected_revision"], b["reviewed"]))

    async def api_reflection_delete(self, request):
        """POST /api/reflections/{id}/delete {expected_revision}."""
        rid = request.path_params["rid"]
        return await self._core_write(request, "source_reflection_delete", {"expected_revision": ((int,), True)},
                                      lambda uid, b: db.source_reflection_delete(uid, rid, b["expected_revision"]))

    # ── board 이전 (2026-10-20, 구현계획 §2-11 D7 · r3 T2·T3) — 연결 장치(#_devices) · 승인 대기(#_inbox) · 「나」 집계·활동/사람/출처/축 상세 ──
    # 쓰기 = _core_write(세션 401 → Origin 403 → JSON 모양 400 → db.write_gate(표시 이름 관문 · 전역 크기) → imports). 페이로드 상한은 main.BodyLimit(/api/*).
    # 읽기 = 세션 → 서버 함수(본인 uid 만) → no-store. 승인·제외는 웹에서만(MCP 는 읽기만). 대상 사용자 = 세션 uid 뿐.
    # 장치 토큰 원문은 device_connect·device_reissue 응답에서 한 번만(no-store) — 화면도 그 자리에서만 그리고 저장하지 않는다.
    async def _core_read(self, request, call):
        """call(uid) → JSON(no-store). CrmError = NOT-FOUND 404 · NAME-REQUIRED 403 · 그 밖 400(_crm_err)."""
        uid, err = await self._need(request)
        if err:
            return err
        try:
            out = await run_in_threadpool(call, uid)
        except db.CrmError as e:
            return self._crm_err(e)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    @staticmethod
    def _qint(request, name: str, default: int, lo: int, hi: int) -> int:
        """쿼리 정수 — 숫자가 아니면 기본값, 범위 밖이면 끝값."""
        v = request.query_params.get(name, "")
        return max(lo, min(hi, int(v))) if v.isdigit() else default

    async def api_devices(self, request):
        """GET = 연결 장치 목록 + 허용 규칙 선택지(서버 상수 그대로 — 화면이 하드코딩하지 않는다) · POST {label} = 장치 연결(토큰 1회)."""
        if request.method == "POST":
            return await self._core_write(request, "device_connect", {"label": ((str,), True)},
                                          lambda uid, b: imports.device_connect(uid, b["label"]))

        def read(uid):
            out = imports.device_list(uid)
            out["choices"] = {"actions": [{"key": a, "label": activity.ACTION_LABELS.get(a, a)} for a in imports.ACTIONS],
                              "transforms": [{"key": t, "label": TRANSFORM_LABELS.get(t, t), "kinds": list(k)} for t, k in imports.TRANSFORMS.items()],
                              "axis_object_id": db.classify_target(uid), "max_axes": imports.MAX_AXES_PER_ACTIVITY,
                              "token_days": imports.TOKEN_DAYS}
            return out
        return await self._core_read(request, read)

    async def api_device_reissue(self, request):
        """POST /api/devices/{id}/reissue {} — 토큰 재발급(옛 토큰 즉시 무효). 응답에 새 토큰 1회."""
        did = request.path_params["did"]
        return await self._core_write(request, "device_reissue", {}, lambda uid, b: imports.device_reissue(uid, did))

    async def api_device_revoke(self, request):
        """POST /api/devices/{id}/revoke {} — 연결 해제(되살리지 않는다). 이미 받은 활동·기록은 남는다."""
        did = request.path_params["did"]
        return await self._core_write(request, "device_revoke", {}, lambda uid, b: imports.device_revoke(uid, did))

    async def api_device_policy(self, request):
        """POST /api/devices/{id}/policy {policy, expected_revision} — 허용 규칙 교체(검증은 imports.device_policy_set)."""
        did = request.path_params["did"]
        return await self._core_write(request, "device_policy_set", {"policy": ((dict,), True), "expected_revision": ((int,), True)},
                                      lambda uid, b: imports.device_policy_set(uid, did, b["policy"], b["expected_revision"]),
                                      json_limits={"policy": POLICY_MAX_BYTES})

    async def api_inbox(self, request):
        """GET ?state=pending|applied|excluded|expired|all&cursor=&limit= — 승인 대기 목록(본문 없음)."""
        state = request.query_params.get("state", "pending")
        cursor = request.query_params.get("cursor", "")
        limit = self._qint(request, "limit", 50, 1, 100)
        return await self._core_read(request, lambda uid: imports.inbox_list(uid, state, cursor, limit))

    async def api_inbox_one(self, request):
        """GET /api/inbox/{id} — 대기 하나 + 본문(pending 만) + AI 요약 승인 문구."""
        iid = request.path_params["iid"]
        return await self._core_read(request, lambda uid: imports.inbox_get(uid, iid))

    async def api_inbox_approve(self, request):
        """POST /api/inbox/{id}/approve {} — 승인(최신 대기 개정만, 스위치 INBOX_APPROVALS)."""
        iid = request.path_params["iid"]
        return await self._core_write(request, "inbox_approve", {}, lambda uid, b: imports.inbox_approve(uid, iid))

    async def api_inbox_exclude(self, request):
        """POST /api/inbox/{id}/exclude {} — 제외(대기 본문 삭제)."""
        iid = request.path_params["iid"]
        return await self._core_write(request, "inbox_exclude", {}, lambda uid, b: imports.inbox_exclude(uid, iid))

    # ── PR ①-b 페이지 레이아웃 · 「나」 통계 (2026-10-22, 구현계획 §3) ──
    # 레이아웃 쓰기 = PUT(저장)·DELETE(기본 복원) 두 개뿐. 세션 401 → Origin 403 → 모양 400 → db.write_gate → db. 버전 불일치 409 LAYOUT-STALE.
    async def api_me_layout(self, request):
        """GET = {layout, version}(행 없으면 기본 + 0) · PUT {layout, version} = 저장(version +1) · DELETE = 기본으로 되돌리기(version +1)."""
        if request.method == "GET":
            return await self._core_read(request, db.layout_get)
        if request.method == "PUT":
            resp = await self._core_write(request, "layout_set", {"layout": ((dict,), True), "version": ((int,), True)},
                                          lambda uid, b: db.layout_set(uid, b["layout"], b["version"]),
                                          json_limits={"layout": db.LAYOUT_MAX_BYTES})
        else:   # DELETE — 본문 없음(_core_write 는 JSON 본문을 요구한다)
            uid, err = await self._need(request)
            if err:
                return err
            if not self.same_origin(request):
                return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)

            def reset():
                db.write_gate("layout_reset", {}, uid)
                return db.layout_reset(uid)
            try:
                return JSONResponse(await run_in_threadpool(reset), headers={"cache-control": "no-store"})
            except db.CrmError as e:
                resp = self._crm_err(e)
        if resp.status_code == 400 and json.loads(resp.body).get("code") == "LAYOUT-STALE":
            resp.status_code = 409   # _crm_err 공용 매핑은 그대로 두고 여기서만 409
        return resp

    async def api_me_stats(self, request):
        """GET — 「나」 통계(축 띠·히트맵·종류별·기다리는 결정). 전체 범위, 읽기만. 모양은 db.me_stats."""
        return await self._core_read(request, db.me_stats)

    # ── PR ②-a 작업 전달 (2026-10-22, 구현계획 §5·G3) ──
    # 장치 경로 = Bearer 장치 토큰(imports.authenticate)만 — 웹 쿠키·MCP 토큰 아님. 장치 권한에 확정 없음.
    # 폴링 버킷(GET /api/device/jobs · GET /api/jobs/<id>) = imports.POLL_LIMITS · 동시 롱폴 = imports.POLL_CONCURRENT(이 프로세스 메모리).
    # /input·/result·/fail 은 기존 장치 요청 버킷(imports._rate, 분당 10·30). 오류 = {code, msg} + _JOB_HTTP.
    _JOB_HTTP = {"JOB-LEASE": 409, "JOB-VERSION": 409, "JOB-GONE": 410, "SWITCH-OFF": 503, "LIMIT-UNSET": 503, "RATE-LIMIT": 429,
                 "JOB-QUOTA": 429, "JOB-LIMIT": 429, "NOT-FOUND": 404, "PAYLOAD-LIMIT": 413,
                 # PR ②-b 쌓기 단계·확인 1번
                 "JOB-DEFS": 409, "CONTRACT-INVALID": 400, "PREVIEW-NEEDS-RESTACK": 409, "CONFIRM-STALE": 409, "CONFIRM-CONFLICT": 409,
                 "CONFIRM-REQUEST-MISMATCH": 409, "IMPORT-QUOTA": 429}
    _polls: dict = {}   # (scope, key) → 진행 중 롱폴 수

    @classmethod
    def _job_err(cls, e, headers=None, extra=None):
        body = {"code": e.code, "msg": e.message}
        if getattr(e, "resume_at", None):
            body["resume_at"] = e.resume_at
        if extra:
            body = {**extra, **body}
        status = cls._JOB_HTTP.get(e.code) or imports.http_status(e.code)
        h = {"cache-control": "no-store", **(headers or {})}
        if status == 429 and "retry-after" not in h:
            h["retry-after"] = str(max(1, 60 - _dt.datetime.now().second))
        return JSONResponse(body, status_code=status, headers=h)

    @staticmethod
    def _poll_bump(uid, device_id=None):
        """폴링 버킷 +1(별도 트랜잭션 — 뒤가 실패해도 남는다). 장치면 장치·사용자 둘 다, 웹이면 사용자만. 넘으면 RATE-LIMIT."""
        with db.tx() as c:
            for scope, key in ((("device", device_id),) if device_id else ()) + (("user", "user"),):
                lim = imports.POLL_LIMITS[(scope, "minute")]
                if imports._bump(c, uid, key, "minute", "poll")["n"] > lim:
                    raise db.CrmError("RATE-LIMIT", f"작업 확인이 너무 잦아요(분당 {lim}회).", "잠시 뒤 다시 확인하세요.")

    def _poll_enter(self, scope, key):
        k = (scope, key)
        if self._polls.get(k, 0) >= imports.POLL_CONCURRENT[scope]:
            return False
        self._polls[k] = self._polls.get(k, 0) + 1
        return True

    def _poll_leave(self, scope, key):
        k = (scope, key)
        self._polls[k] = self._polls.get(k, 1) - 1
        if self._polls[k] <= 0:
            self._polls.pop(k, None)

    def _wait_arg(self, request):
        return self._qint(request, "wait", imports.POLL_WAIT_MAX, 0, imports.POLL_WAIT_MAX)

    async def _device(self, request):
        return await run_in_threadpool(imports.authenticate, request.headers.get("authorization", ""))

    async def api_device_jobs(self, request):
        """GET /api/device/jobs?wait=0..30 — 롱폴. 200 {id,kind,session_ref,input_ref,input_version,definitions_version,lease:{generation,expires_at}}
        · 204 없음 · 429 폴링 버킷·동시 1(Retry-After). 끊긴 연결엔 할당하지 않는다(1초마다 확인)."""
        code = "EXC"
        try:
            dev = await self._device(request)
            if not self._poll_enter("device", dev["id"]):
                raise db.CrmError("RATE-LIMIT", "이 장치의 작업 확인이 이미 진행 중이에요.", "앞 확인이 끝난 뒤 다시 하세요.")
            try:
                await run_in_threadpool(self._poll_bump, dev["user_id"], dev["id"])
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self._wait_arg(request)
                while True:
                    if await request.is_disconnected():
                        code = "GONE"
                        return Response(status_code=204, headers={"cache-control": "no-store"})
                    job = await run_in_threadpool(db.job_assign, dev)
                    if job:
                        code = "OK"
                        return JSONResponse(job, headers={"cache-control": "no-store"})
                    left = deadline - loop.time()
                    if left <= 0:
                        code = "OK"
                        return Response(status_code=204, headers={"cache-control": "no-store"})
                    await asyncio.sleep(min(1.0, left))
            finally:
                self._poll_leave("device", dev["id"])
        except db.CrmError as e:
            code = e.code
            return self._job_err(e)
        finally:
            db.usage_count(None, "api:device_jobs", code)

    async def api_device_job(self, request):
        """GET .../input?lease_generation=N → {text, input_version}(ensure_ascii=False — Starlette JSONResponse 기본) ·
        POST .../result · POST .../fail. 임대·버전·만료 판정은 db. 409 JOB-LEASE·JOB-VERSION · 410 JOB-GONE · 503 SWITCH-OFF."""
        jid, op = request.path_params["jid"], request.path_params["op"]
        code = "EXC"
        try:
            if (op, request.method) not in (("input", "GET"), ("definitions", "GET"), ("result", "POST"), ("fail", "POST")):
                raise db.CrmError("NOT-FOUND", "그런 경로는 없어요.")
            dev = await self._device(request)
            await run_in_threadpool(imports._rate, dev)
            if op in ("input", "definitions"):   # definitions = ②-b 정의 스냅샷(임대 유효 때만, 최초 발급 때 해시 고정)
                g = request.query_params.get("lease_generation", "")
                gen = int(g) if g.isdigit() else None
                out = await run_in_threadpool(db.job_input if op == "input" else db.job_definitions, dev, jid, gen)
            else:
                raw = await request.body()
                if len(raw) > db.JOB_RESULT_MAX_BYTES:
                    raise db.CrmError("PAYLOAD-LIMIT", f"본문이 너무 커요(상한 {db.JOB_RESULT_MAX_BYTES} 바이트).")
                try:
                    body = json.loads(raw)
                except ValueError:
                    raise db.CrmError("ARG-INVALID", "JSON 본문이 필요해요.") from None
                out = await run_in_threadpool(db.job_result if op == "result" else db.job_fail, dev, jid, body)
            code = "OK"
            return JSONResponse(out, headers={"cache-control": "no-store"})
        except db.CrmError as e:
            code = e.code
            return self._job_err(e)
        finally:
            db.usage_count(None, f"api:device_job_{op}" if op in ("input", "definitions", "result", "fail") else "api:device_job", code)

    async def api_jobs(self, request):
        """POST {kind, text, session_id?, input_kind?, restack?} — 작업 접수(스위치·길이 8,000자/32KiB·한도·열린 작업 수). 201 {id, session_id}.
        ②-b: input_kind(converse|verbatim) = 세션 첫 입력에서만 쓰인다 · restack = 재정리 구조화 입력(db.job_create)."""
        resp = await self._core_write(request, "job_create",
                                      {"kind": ((str,), True), "text": ((str,), True), "session_id": ((str,), False),
                                       "input_kind": ((str,), False), "restack": ((dict,), False)},
                                      lambda uid, b: db.job_create(uid, b["kind"], b["text"], b.get("session_id"), b.get("input_kind"),
                                                                   b.get("restack")),
                                      json_limits={"restack": 3 * db.JOB_TEXT_MAX_BYTES})
        return self._job_remap(resp, ok=201)

    async def api_stack_exclude(self, request):
        """POST /api/stack/previews/<id>/exclude {cids} — 항목 끄기(의존·같은 정리 단위 함께). 200 {preview, removed} · 409 PREVIEW-NEEDS-RESTACK·
        CONFIRM-STALE · 410 JOB-GONE. 스위치 무관(줄이는 쪽) — 확정만 스위치를 본다."""
        resp = await self._core_write(request, "stack_exclude", {"cids": ((list,), True)},
                                      lambda uid, b: db.stack_exclude(uid, request.path_params["pid"], b["cids"]),
                                      json_limits={"cids": 8 * 1024})
        return self._job_remap(resp)

    async def api_stack_session(self, request):
        """GET /api/stack/sessions/<id> — 새로고침 복원(세션 최신 미리보기). 본인만, 남·없음 404. 읽기만."""
        uid, err = await self._need(request)
        if err:
            return err
        try:
            out = await run_in_threadpool(db.stack_session_get, uid, request.path_params["sid"])
        except db.CrmError as e:
            return self._job_err(e) if e.code in self._JOB_HTTP else self._crm_err(e)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    def _job_remap(self, resp, ok=200):
        """_core_write 의 공용 매핑(그 밖 400)을 작업 코드에 맞게 — 성공 상태·503·429·410·409."""
        if resp.status_code == 200:
            resp.status_code = ok
        elif resp.status_code == 400:
            body = json.loads(resp.body)
            code = body.get("code")
            if code in self._JOB_HTTP and code != "PAYLOAD-LIMIT":
                if code == "JOB-QUOTA":   # _crm_err 는 resume_at 을 싣지 않는다 — 장치 경로와 같은 꼴로
                    body["resume_at"] = imports.next_kst_midnight()
                return self._job_err(db.CrmError(code, body.get("msg", ""), body.get("action", "")), extra=body)
        return resp

    async def api_job(self, request):
        """GET /api/jobs/<id>?wait=0..30 — 상태(본인만, 남·없음 404). 살아 있는 동안(queued·leased·running) 롱폴, result_ready 면 result 를 싣는다.
        폴링 버킷(사용자) · 웹 동시 2."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self._poll_enter("web", uid):
            return self._job_err(db.CrmError("RATE-LIMIT", "작업 확인이 이미 여러 개 진행 중이에요.", "잠시 뒤 다시 확인하세요."))
        try:
            await run_in_threadpool(self._poll_bump, uid)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._wait_arg(request)
            while True:
                out = await run_in_threadpool(db.job_get, uid, request.path_params["jid"])
                left = deadline - loop.time()
                if out["status"] not in ("queued", "leased", "running") or left <= 0 or await request.is_disconnected():
                    return JSONResponse(out, headers={"cache-control": "no-store"})
                await asyncio.sleep(min(1.0, left))
        except db.CrmError as e:
            return self._job_err(e)
        finally:
            self._poll_leave("web", uid)

    async def api_job_op(self, request):
        """POST /api/jobs/<id>/cancel — 취소(스위치 무관, 멱등, 본문 없음) · POST /api/jobs/<id>/confirm — ②-b 전 = 503 SWITCH-OFF."""
        uid, err = await self._need(request)
        if err:
            return err
        if not self.same_origin(request):
            return JSONResponse({"code": "ORIGIN", "msg": "다른 사이트에서 온 요청이에요."}, status_code=403)
        op = request.path_params["op"]
        try:
            if op == "confirm":
                raise db.CrmError("SWITCH-OFF", "확인(확정)은 아직 열리지 않았어요.", "다음 판을 기다리세요.")
            if op != "cancel":
                raise db.CrmError("NOT-FOUND", "그런 경로는 없어요.")

            def cancel():
                db.write_gate("job_cancel", {}, uid, quota=False)   # 지우는 쪽이라 전역 크기 관문 면제
                return db.job_cancel(uid, request.path_params["jid"])
            return JSONResponse(await run_in_threadpool(cancel), headers={"cache-control": "no-store"})
        except db.CrmError as e:
            return self._job_err(e) if e.code in self._JOB_HTTP else self._crm_err(e)

    async def api_me_aggregate(self, request):
        """GET ?period=7d|30d|90d|all — 「나」 집계(웹·MCP 같은 서버 함수). /api/state 엔 30d 만 실린다."""
        period = request.query_params.get("period", activity.DEFAULT_PERIOD)
        return await self._core_read(request, lambda uid: activity.me_aggregate(uid, period))

    async def api_activities(self, request):
        """GET ?action=&person=&source=&axis=&dated=&cursor=&limit= — 현재 판 활동 목록."""
        q = request.query_params
        limit = self._qint(request, "limit", 50, 1, 100)
        return await self._core_read(request, lambda uid: activity.activity_list(
            uid, q.get("action", ""), q.get("person", ""), q.get("source", ""), q.get("axis", ""), q.get("dated", ""), q.get("cursor", ""), limit))

    async def api_activity(self, request):
        """GET /api/activities/{id} — 활동 하나(옛 판도 열린다) + 판 이력 · 승인된 메모 · 원문 불투명 ID."""
        aid = request.path_params["aid"]
        return await self._core_read(request, lambda uid: activity.activity_get(uid, aid))

    async def api_person_context(self, request):
        """GET /api/people/{id}/context — 사람 상세 역방향(활동·기록·항목)."""
        pid = request.path_params["pid"]
        return await self._core_read(request, lambda uid: activity.person_context(uid, pid))

    async def api_source_context(self, request):
        """GET /api/sources/{id}/context — 출처 상세의 활동."""
        sid = request.path_params["sid"]
        return await self._core_read(request, lambda uid: activity.source_context(uid, sid))

    async def api_axis_context(self, request):
        """GET /api/axes/{item_id}/context — 축 상세(활동·기록·판단·기준 연결)."""
        iid = request.path_params["iid"]
        return await self._core_read(request, lambda uid: activity.axis_context(uid, iid))

    async def api_feedback(self, request):
        """문의(desk-0). GET = 「내 문의」(본인 글·상태·답변 유무·공개 갱신, 운영자 메모 없음) — 관리자도 같다(운영은 /admin/tickets)
        · POST {kind, text, page} = 접수. POST 는 세션 + Origin. 길이는 write_gate(전역 DB 크기는 면제 — QUOTA_EXEMPT), 하루 한도는 db.ticket_add."""
        uid, err = await self._need(request)
        if err:
            return err
        if request.method == "GET":
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
        except db.CrmError as e:
            return JSONResponse({"code": e.code, "msg": e.message, "action": e.action}, status_code=400)
        return JSONResponse({**out, "notice": db.NOTICE})

    async def api_feedback_one(self, request):
        """본인 문의 하나 — 글·답변·공개 이력. 다른 사람 것·없는 id 는 404(같은 모양). 내부 이력·운영자 메모는 없다."""
        uid, err = await self._need(request)
        if err:
            return err
        try:
            out = await run_in_threadpool(db.ticket_get_mine, uid, request.path_params["tid"])
        except db.CrmError as e:
            return JSONResponse({"code": e.code, "msg": e.message}, status_code=404 if e.code == "NOT-FOUND" else 400)
        return JSONResponse(out, headers={"cache-control": "no-store"})

    # ── /admin (β2) ────────────────────────────────────
    # 서버 렌더 + form POST 만(스크립트 0). 비관리자·무세션·DEV → 404. 관리자 세션이면 모든 화면·동작을 바로 연다.
    async def _admin_who(self, request) -> str | None:
        """관리자 세션 uid. DEV·무세션·비관리자(차단 포함 — uid_of 가 이미 거른다)면 None."""
        if self.dev:
            return None
        uid = await self.uid_of(request)
        return uid if uid is not None and getattr(request.state, "role", None) == "admin" else None

    async def admin_index(self, request):
        """/admin 개요 — 상태 띠 · 사용자 표(+미연결 행) · 오류 표 14일 · 정의 분포. 표 정의는 리포트 프리셋과 같다(admin_db.aggregate)."""
        if await self._admin_who(request) is None:
            return _not_found()
        role = request.state.role
        today = admin_db.kst_today()
        since = today - _dt.timedelta(days=admin_db.RECENT_DAYS - 1)
        band, users, last, usage, struct = await run_in_threadpool(lambda: (
            admin_db.overview_band(role), admin_db.users(role), admin_db.last_active(role),
            admin_db.report_rows(role, "usage", since, today), admin_db.report_rows(role, "structure")))
        err_def = {k: v for k, v in _preset("errors14").items() if k not in ("ds", "g1", "g2", "days")}
        errors = admin_db.aggregate("usage", usage, ("day", "name", "code"), err_def, today)
        dist = admin_db.aggregate("structure", struct, ("objects_bucket", "fields_bucket"), {}, today)
        counts = admin_db.structure_counts(struct)
        calls, lastd = {}, {r["user_id"]: r["last_day"] for r in last}
        for r in usage:
            calls[r["user_id"]] = calls.get(r["user_id"], 0) + r["n"]
        links = {k: href for k, _, href in preset_links(today)}

        def num(label, n, warn=False, href=None):
            v = f'<b class="warn">{_esc(n)}</b>' if warn and n else f"<b>{_esc(n)}</b>"
            inner = f"{_esc(label)} {v}"
            return f'<span><a href="{_esc(href)}">{inner}</a></span>' if href else f"<span>{inner}</span>"
        bar = '<div class="bar">' + num("사용자", band["users"]) + num("이름 공유 동의", band["names"]) + "</div>"
        tbar = ('<div class="bar">' + num("미종결 문의", band["open_tickets"], href="/admin/tickets")
                + num(f"개인정보 기한 임박(≤{admin_db.PRIVACY_SOON_DAYS}일)", band["privacy_soon"], True)
                + num("기한 초과", band["privacy_over"], True) + "</div>")
        urows = []
        for u in users:
            no, _nf = counts.get(u["id"], (0, 0))
            ld = lastd.get(u["id"])
            urows.append(f'<tr><td class="email">{_esc(u["email"])}</td><td>{_esc(_kst(u["created"])[:10])}</td>'
                         f'<td>{_esc(ld.isoformat() if ld else "—")}</td><td class="num">{_esc(calls.get(u["id"], 0))}</td>'
                         f'<td class="num">{_esc(no)}</td></tr>')
        ld = lastd.get("")
        urows.append(f'<tr><td class="muted">미연결 사용량(로그인 전)</td><td>—</td><td>{_esc(ld.isoformat() if ld else "—")}</td>'
                     f'<td class="num">{_esc(calls.get("", 0))}</td><td class="num">—</td></tr>')
        user_t = _table(['<th>이메일</th><th>가입일</th><th>최근 활동일</th><th class="num">14일 호출</th><th class="num">정의 수</th>'],
                        urows)
        erows = sorted(errors["rows"], key=lambda r: (r["keys"][0], r["values"]["calls"]), reverse=True)
        err_t = _table(['<th>일자</th><th>도구·화면</th><th>코드</th><th class="num">n</th>'],
                       [f'<tr><td>{_esc(r["keys"][0])}</td><td>{_esc(r["keys"][1])}</td><td>{_esc(r["keys"][2])}</td>'
                        f'<td class="num">{_esc(r["values"]["calls"])}</td></tr>' for r in erows], "14일 동안 오류 없음")
        dmx = max((r["values"]["users"] for r in dist["rows"]), default=0)
        dist_t = _table(['<th>오브젝트 수</th><th>필드 수</th><th class="num">사용자</th><th></th>'],
                        [f'<tr><td>{_esc(r["keys"][0])}</td><td>{_esc(r["keys"][1])}</td><td class="num">{_esc(r["values"]["users"])}</td>'
                         f'<td>{_bar(r["values"]["users"], dmx)}</td></tr>' for r in dist["rows"]])
        more = lambda key: f' <a class="note" href="{_esc(links[key])}">리포트로</a>'   # noqa: E731
        body = (f'<h1>Setup Home</h1>'
                f'<h2>개요</h2>{bar}<p class="note">서비스 표준: 코어 + std@1 · 표준 정의 조회 전용 — <a href="/admin/schema">Object Manager</a>. '
                f'계정별 std@1 편입 여부는 <a href="/admin/users">Users</a> 상세에서 봐요.</p>'
                f'<h3>사용자 {len(users)}명</h3><p class="note">최근 활동일 = usage_daily 마지막 날 · 14일 = 오늘(KST) 포함 '
                f'{admin_db.RECENT_DAYS}일 · 정의 수 = 적용된 오브젝트(보관 제외)</p>{user_t}'
                f'<h3>정의 분포{more("defs")}</h3><p class="note">사용자별 오브젝트·필드 개수 구간(보관 제외, 이름 없음)</p>{dist_t}'
                f'<h2>확인할 오류</h2><h3>오류 14일{more("errors14")}</h3><p class="note">usage_daily 에서 code ≠ OK</p>{err_t}'
                f'<h2>문의</h2>{tbar}<p class="note">처리 상태별 건수는 이 화면에서 확인할 수 없어요 — '
                f'<a href="/admin/tickets?status=all">Support</a>에서 상태 필터로 봐요.</p>')
        return _admin_page("관리 — Setup Home", body, cur="overview")

    async def admin_reauth(self, request):
        """옛 링크 호환 — 관리자 세션이면 302 /admin. 비관리자·무세션·DEV 는 다른 /admin 경로와 같은 404."""
        if await self._admin_who(request) is None:
            return _not_found()
        return RedirectResponse("/admin", status_code=302)

    async def admin_setup(self, request):
        uid = await self._admin_who(request)
        if uid is None:
            return _not_found()
        role = request.state.role
        users, audit = await run_in_threadpool(lambda: (admin_db.users(role), admin_db.audit(role)))

        def btn(action, label, fields, danger=False):
            hidden = "".join(f'<input type="hidden" name="{_esc(k)}" value="{_esc(v)}">' for k, v in fields.items())
            cls = ' class="danger"' if danger else ""
            return f'<form method="post" action="{action}">{hidden}<button type="submit"{cls}>{_esc(label)}</button></form>'

        rows = []
        for u in users:
            is_admin = u["role"] == "admin"
            role_tag = '<span class="tag admin">관리자</span>' if is_admin else '<span class="tag">사용자</span>'
            blocked = f'<span class="tag blocked">차단 {_esc(_kst(u["blocked_at"]))}</span>' if u["blocked_at"] else ""
            acts = [btn("/admin/role", "관리자 회수", {"uid": u["id"], "role": "user"}, True) if is_admin
                    else btn("/admin/role", "관리자 부여", {"uid": u["id"], "role": "admin"}),
                    btn("/admin/signout", "웹 로그인 끊기", {"uid": u["id"]}),
                    btn("/admin/unblock", "차단 해제", {"uid": u["id"]}) if u["blocked_at"]
                    else btn("/admin/block", "차단", {"uid": u["id"]}, True)]
            rows.append(f'<tr><td class="email">{_esc(u["email"])}</td><td>{_esc(_kst(u["created"]))}</td><td>{role_tag}</td>'
                        f'<td>{blocked}</td><td>{_esc(_kst(u["web_revoked_before"]))}</td>'
                        f'<td><div class="acts">{"".join(acts)}</div></td></tr>')

        def who(email, uid_):
            if email:
                return _esc(email)
            return f'<span class="muted">{_esc(uid_[:8])}</span>' if uid_ else '<span class="muted">—</span>'

        arows = "".join(f'<tr><td>{_esc(_kst(a["at"]))}</td><td class="email">{who(a["actor_email"], a["actor_id"])}</td>'
                        f'<td>{_esc(ADMIN_LABELS.get(a["action"], a["action"]))}</td><td class="email">{who(a["target_email"], a["target_id"])}</td></tr>'
                        for a in audit)
        body = (f'<h1>Administration</h1><p class="note">관리자 부여·회수 · 웹 로그인 끊기 · 차단 · 감사 로그. 서비스 표준을 고치는 권한은 없어요.</p>'
                f'<h2>사용자 {len(users)}명</h2><div class="tbl"><table><thead><tr><th>이메일</th><th>가입일</th><th>역할</th>'
                f'<th>차단</th><th>웹 끊은 시각</th><th>동작</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
                f'<h2>감사 로그 최근 {admin_db.AUDIT_LIST}건</h2><div class="tbl"><table><thead><tr><th>시각</th><th>실행자</th><th>동작</th>'
                f'<th>대상</th></tr></thead><tbody>{arows or "<tr><td colspan=4 class=muted>없음</td></tr>"}</tbody></table></div>')
        return _admin_page("관리 — Administration", body, cur="setup")

    async def _admin_form(self, request, limit: int = ADMIN_FORM_MAX) -> dict | None:
        """form-urlencoded 본문만, limit(기본 ADMIN_FORM_MAX) 바이트까지. 아니면 None."""
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
            return None
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > limit:
                return None
        try:
            return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        except (UnicodeDecodeError, ValueError):
            return None

    async def _admin_post(self, request, op: str):
        """/admin POST 공통 — 관리자 세션(아니면 404) → Origin 정확히 일치(403) → form(400)
        → admin_db.act(한 트랜잭션·감사) → 303 /admin/setup."""
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
        try:
            await run_in_threadpool(admin_db.act, uid, op, form["uid"], role)
        except db.CrmError as e:
            status = {"NOT-FOUND": 404, "FORBIDDEN": 403, "LAST-ADMIN": 409}.get(e.code, 400)
            return _admin_page("처리하지 못했어요", f"<h1>처리하지 못했어요</h1><p>{_esc(e.code)} — {_esc(e.message)}</p>"
                               f"{'<p>' + _esc(e.action) + '</p>' if e.action else ''}{back}", status)
        return RedirectResponse("/admin/setup", status_code=303)

    # 문의 처리(P3) — form POST 4개. 화면(/admin/tickets/<id>)은 P4. 사다리는 _admin_post 와 같다.
    _TICKET_OPS = {   # op → (form 필드, admin_db 함수 이름)
        "status": (("status",), "ticket_status"),
        "reply": (("reply",), "ticket_reply"),
        "note": (("admin_note",), "ticket_note"),
        "reclassify": (("kind", "reason"), "ticket_reclassify"),
    }

    async def _admin_ticket_post(self, request, op: str):
        """/admin/tickets/{tid}/{op} — 관리자 세션(아니면 404) → Origin(403) → form(400)
        → admin_db(변경+사건 한 트랜잭션) → 303 /admin/tickets/{tid}."""
        uid = await self._admin_who(request)
        if uid is None:
            return _not_found()
        tid = request.path_params["tid"]
        home = f"/admin/tickets/{quote(tid, safe='')}"
        back = f'<p><a href="{_esc(home)}">문의로</a></p>'
        if not self.same_origin(request):
            return _admin_page("다른 사이트에서 온 요청이에요", f"<h1>다른 사이트에서 온 요청이에요</h1><p>ORIGIN</p>{back}", 403, cur="tickets")
        fields, fn = self._TICKET_OPS[op]
        form = await self._admin_form(request, TICKET_FORM_MAX)
        if form is None or fields[0] not in form:
            return _admin_page("요청이 틀렸어요", f"<h1>요청이 틀렸어요</h1><p>ARG-INVALID</p>{back}", 400, cur="tickets")
        args = [form.get(f, "") for f in fields]
        try:
            await run_in_threadpool(getattr(admin_db, fn), request.state.role, uid, tid, *args)
        except db.CrmError as e:
            status = {"NOT-FOUND": 404, "FORBIDDEN": 403}.get(e.code, 400)
            return _admin_page("처리하지 못했어요", f"<h1>처리하지 못했어요</h1><p>{_esc(e.code)} — {_esc(e.message)}</p>{back}", status, cur="tickets")
        return RedirectResponse(home, status_code=303)

    async def admin_ticket_status(self, request):
        return await self._admin_ticket_post(request, "status")

    async def admin_ticket_reply(self, request):
        return await self._admin_ticket_post(request, "reply")

    async def admin_ticket_note(self, request):
        return await self._admin_ticket_post(request, "note")

    async def admin_ticket_reclassify(self, request):
        return await self._admin_ticket_post(request, "reclassify")

    async def admin_role(self, request):
        return await self._admin_post(request, "role")

    async def admin_signout(self, request):
        return await self._admin_post(request, "signout")

    async def admin_block(self, request):
        return await self._admin_post(request, "block")

    async def admin_unblock(self, request):
        return await self._admin_post(request, "unblock")

    # ── /admin 스키마·리포트 (P4) ──────────────────────
    def _opaque(self, uid: str) -> str:
        """사용자 불투명 id — HMAC(서버 비밀, 내부 id) 앞 12자. URL 에만 쓴다."""
        return hmac.new(self.secret, str(uid).encode(), hashlib.sha256).hexdigest()[:12]

    @staticmethod
    def _admin_bad(title: str, msg: str, back: str, cur: str) -> HTMLResponse:
        """400 화면 — 모르는 키·조합. 다른 화면으로 대체하지 않는다."""
        return _admin_page(title, f'<h1>{_esc(title)}</h1><p>ARG-INVALID — {_esc(msg)}</p><p><a href="{back}">처음으로</a></p>', 400,
                           cur=cur)

    async def admin_schema(self, request):
        """/admin/schema — Object Manager: 서비스 표준(코어 + std@1) 그래프 → 같은 대상 목록 → 관계 → 별도 템플릿 구역. 사용자 DB 를 읽지 않는다.
        ?u=<불투명 id> — 옛 사용자 트리 주소. 그 사용자의 새 상세(/admin/users/<불투명 id>)로 302, 모르는 id 는 404(입력을 되비추지 않는다)."""
        _, err = await self._admin_view(request)
        if err:
            return err
        items = request.query_params.multi_items()
        if len(items) > 1 or any(k != "u" for k, _ in items):
            return self._admin_bad("Object Manager를 열 수 없어요", "모르는 쿼리 키예요(u 하나만 받아요).", "/admin/schema", "schema")
        if not items:
            return _admin_page("관리 — Object Manager", render_object_manager(), cur="schema")
        users = await run_in_threadpool(admin_db.users, request.state.role)
        u = next((x for x in users if self._opaque(x["id"]) == items[0][1]), None)
        if u is None:
            return _admin_page("사용자를 찾지 못했어요", '<h1>사용자를 찾지 못했어요</h1><p>NOT-FOUND</p><p><a href="/admin/users">Users로</a></p>',
                               404, cur="schema")
        return RedirectResponse(f"/admin/users/{self._opaque(u['id'])}", status_code=302)

    @staticmethod
    def _om_missing() -> HTMLResponse:
        """모르는 공개 ID·판·조합 — 404(입력을 되비추지 않는다)."""
        return _admin_page("정의를 찾지 못했어요", '<h1>정의를 찾지 못했어요</h1><p>NOT-FOUND</p><p><a href="/admin/schema">Object Manager로</a></p>',
                           404, cur="schema")

    async def _om_open(self, request):
        """Object Manager 상세 공통 — 관리자(아니면 404) · 쿼리 키 없음(400). 반환 응답 또는 None."""
        _, err = await self._admin_view(request)
        if err:
            return err
        if request.query_params:
            return self._admin_bad("정의를 열 수 없어요", "쿼리 키를 받지 않아요.", "/admin/schema", "schema")
        return None

    async def admin_schema_object(self, request):
        """/admin/schema/objects/{공개 ID} — 서비스 오브젝트 상세(코어 5 + std@1 3 허용목록). 그 밖은 404."""
        err = await self._om_open(request)
        if err:
            return err
        objs = service_def()["objects"]
        o = next((x for x in objs if x["id"] == request.path_params["oid"]), None)
        if o is None:
            return self._om_missing()
        return _admin_page(f"관리 — {o['name']}", render_object(o, service_schema.names_of(objs)), cur="schema",
                           crumbs=[("Object Manager", "/admin/schema"), (o["name"], None)])

    async def admin_schema_template(self, request):
        """/admin/schema/templates/{공개 ID} — 선택 템플릿 정의(서비스 표준과 분리). 모르는 판은 404."""
        err = await self._om_open(request)
        if err:
            return err
        t = service_def()["templates"].get(request.path_params["tid"])
        if t is None:
            return self._om_missing()
        return _admin_page(f"관리 — {t['title']}", render_template(t), cur="schema", crumbs=[("Object Manager", "/admin/schema"), (t["title"], None)])

    async def admin_schema_template_object(self, request):
        """/admin/schema/templates/{판}/objects/{공개 ID} — 그 판에 속한 오브젝트만. 모르는 조합은 404."""
        err = await self._om_open(request)
        if err:
            return err
        t = service_def()["templates"].get(request.path_params["tid"])
        o = next((x for x in t["objects"] if x["id"] == request.path_params["oid"]), None) if t else None
        if o is None:
            return self._om_missing()
        names = {**service_schema.names_of(service_def()["objects"]), **service_schema.names_of(t["objects"])}
        return _admin_page(f"관리 — {o['name']}", render_object(o, names, t["id"]), cur="schema",
                           crumbs=[("Object Manager", "/admin/schema"), (t["title"], f"/admin/schema/templates/{t['id']}"), (o["name"], None)])

    async def admin_find(self, request):
        """/admin/find?q= — Quick Find. 운영 메뉴 + 서비스 표준(코어 + std@1) 정의의 메모리 색인만(사용자 DB·선택 템플릿 0).
        검색어는 화면·속성에 되비추지 않고 저장하지 않는다. 100자 초과 400 · 결과 최대 50."""
        _, err = await self._admin_view(request)
        if err:
            return err
        items = request.query_params.multi_items()
        if len(items) > 1 or any(k != "q" for k, _ in items):
            return self._admin_bad("찾을 수 없어요", "검색어(q) 하나만 받아요.", "/admin/find", "")
        state, hits, over = service_schema.find(items[0][1] if items else "", find_index())
        crumbs = [("Quick Find", None)]
        if state == "too_long":
            return _admin_page("찾을 수 없어요", f'<h1>찾을 수 없어요</h1><p>ARG-INVALID — 검색어는 {service_schema.FIND_MAX}자까지예요.</p>'
                               '<p><a href="/admin/find">Quick Find 처음으로</a></p>', 400, crumbs=crumbs)
        lis = "".join(f'<li><span class="tag">{_esc(service_schema.FIND_KINDS[e["kind"]])}</span><a href="{_esc(e["href"])}">{_esc(e["title"])}</a>'
                      f'<span class="note">{_esc(e["desc"])}</span></li>' for e in hits)
        if state == "empty":
            head = f'<h2>운영 메뉴 {len(hits)}</h2><p class="note">검색어를 넣으면 운영 메뉴와 서비스 표준 오브젝트·필드·관계에서 찾아요.</p>'
        else:
            head = f'<h2>결과 {len(hits)}개</h2>' + ('<p class="note warn">검색어를 더 구체적으로 입력하세요.</p>' if over else "")
        body = (f'<h1>Quick Find</h1><p class="note">운영 메뉴와 서비스 표준(코어 + std@1) 정의만 찾아요. 사용자 자료·선택 템플릿은 찾지 않아요. '
                f'여러 낱말은 모두 들어간 항목만 보여요.</p>{head}'
                + (f'<ul class="found">{lis}</ul>' if hits else '<p class="muted">찾은 항목이 없어요.</p>'))
        return _admin_page("관리 — Quick Find", body, crumbs=crumbs)

    async def admin_reports(self, request):
        """/admin/reports — GET 쿼리스트링(ds·g1·g2·필터·from·to)만. 열거 키·허용 조합은 admin_db 허용표. 모르는 키·조합은 400 화면."""
        _, err = await self._admin_view(request)
        if err:
            return err
        role = request.state.role
        items = request.query_params.multi_items()
        keys =[k for k, _ in items]

        def bad(msg):
            return self._admin_bad("리포트를 만들 수 없어요", msg, "/admin/reports", "reports")
        if len(keys) != len(set(keys)):
            return bad("같은 키를 두 번 보냈어요.")
        q = dict(items)
        ds = q.get("ds") or None
        today = admin_db.kst_today()
        tabs = "".join(f'<a href="{_report_href({"ds": k})}"{" aria-current=page" if k == ds else ""}>{_esc(t)}</a>'
                       for k, t in admin_db.REPORT_DS_LABELS.items())
        pres = "".join(f'<a href="{_esc(href)}">{_esc(label)}</a>' for _, label, href in preset_links(today))
        top = (f'<h1>Reports</h1><p class="note">데이터셋</p><div class="chips">{tabs}</div>'
               f'<p class="note">프리셋(개요의 표와 같은 정의)</p><div class="chips">{pres}</div>')
        if ds is None:
            if set(q) - {"ds"}:
                return bad("데이터셋(ds)을 먼저 골라요.")
            return _admin_page("관리 — 리포트", top + '<p class="muted">데이터셋이나 프리셋을 고르세요.</p>', cur="reports")
        try:
            spec = admin_db.report_spec(ds)
        except db.CrmError as e:
            return bad(e.message)
        unknown = sorted(set(q) - ({"ds", "g1", "g2", "from", "to"} | set(spec["filters"])))
        if unknown:
            return bad("이 데이터셋에서 모르는 키예요: " + ", ".join(unknown))
        q = {k: v for k, v in q.items() if v != ""}   # form 의 빈 칸 = 안 고름
        if "g2" in q and "g1" not in q:
            return bad("g2 는 g1 과 같이 써요.")
        dims = tuple(q[k] for k in ("g1", "g2") if k in q)
        filters = {k: q[k] for k in spec["filters"] if k in q}
        try:
            out = await run_in_threadpool(admin_db.report, role, ds, dims, filters, q.get("from"), q.get("to"), today)
        except db.CrmError as e:
            return bad(e.message)
        span = f'기간 {out["from"]} ~ {out["to"]}(KST)' if out["from"] else "지금 상태"
        body = (top + render_report_form(ds, spec, q, out) + f'<h2>{_esc(admin_db.REPORT_DS_LABELS[ds])} — {_esc(span)}</h2>'
                + render_report_table(out) + render_measure_notes(ds))
        return _admin_page("관리 — 리포트", body, cur="reports")

    # ── /admin/tickets — 문의 리스트·레코드(P4-4·5) ─────────────
    # 서버 렌더·form 만(스크립트 0). 리스트엔 원문·메모·화면·도구·UA 가 없다 — 원문은 레코드 페이지에서만.
    _TK_KIND = {"bug": "버그", "request": "요청", "question": "질문", "privacy": "개인정보 요청"}
    _TK_STATUS = {"open": "접수", "ack": "확인", "doing": "처리중", "done": "완료", "wontfix": "보류"}
    _TK_LIST_STATUS = {"active": "미종결", "all": "전체", "closed": "종결", **_TK_STATUS}
    _TK_SORT = {"deadline": "기한·접수순", "new": "최근 접수", "updated": "최근 갱신"}
    _TK_COLS = {"via": "경로", "closed": "종결일"}   # 선택 컬럼(cols=via,closed)
    _TK_REASON = {"not_privacy": "개인정보 요청이 아님", "duplicate": "중복", "other": "그 밖"}
    _TK_HIST = {"status": "상태", "reply": "답변", "note": "메모", "reclassify": "재분류", "deleted": "삭제 완료"}

    @staticmethod
    def _tk_time(v) -> str:
        if not v:
            return ""
        d = v if isinstance(v, _dt.datetime) else _dt.datetime.fromisoformat(str(v))
        return _kst(d)

    @staticmethod
    def _tk_deadline(received) -> str:
        """개인정보 기한 칸 — 남은 일수·오늘까지·N일 초과(KST 날짜). received 없으면 빈 칸."""
        due, left, over = admin_db.privacy_days_left(received)
        if due is None:
            return ""
        if over:
            return f'<span class="tag over">기한 초과 {-left}일</span>'
        return f'<span class="tag due">{"오늘까지" if left == 0 else f"{left}일 남음"}</span> <span class="muted">{_esc(due.isoformat())}</span>'

    async def _admin_view(self, request):
        """GET /admin 화면 공통(스키마·리포트·문의) — 관리자 세션(아니면 None·404).
        반환 (uid, None) 또는 (None, 응답). role 은 request.state.role."""
        uid = await self._admin_who(request)
        if uid is None:
            return None, _not_found()
        return uid, None

    async def admin_tickets(self, request):
        """/admin/tickets 리스트 — 쿼리 status·kind·sort·cols(열거 키만, 모르면 400). 기본 = 미종결 · 개인정보 기한 오름차순 → 접수순."""
        uid, resp = await self._admin_view(request)
        if resp is not None:
            return resp
        q = request.query_params
        status, kind, sort = q.get("status", "active"), q.get("kind", "all"), q.get("sort", "deadline")
        cols = [c for c in q.get("cols", "").split(",") if c]
        if any(c not in self._TK_COLS for c in cols) or set(q) - {"status", "kind", "sort", "cols"}:
            return _admin_page("요청이 틀렸어요", "<h1>요청이 틀렸어요</h1><p>ARG-INVALID — 모르는 필터·열이에요.</p>"
                               '<p><a href="/admin/tickets">문의 목록으로</a></p>', 400, cur="tickets")
        role = request.state.role
        try:
            rows, more = await run_in_threadpool(admin_db.tickets_list, role, status, kind, sort)
        except db.CrmError as e:
            return _admin_page("요청이 틀렸어요", f"<h1>요청이 틀렸어요</h1><p>{_esc(e.code)} — {_esc(e.message)}</p>"
                               '<p><a href="/admin/tickets">문의 목록으로</a></p>', 400, cur="tickets")

        def sel(name, opts, cur):
            o = "".join(f'<option value="{_esc(k)}"{" selected" if k == cur else ""}>{_esc(v)}</option>' for k, v in opts.items())
            return f'<label>{_esc({"status": "상태", "kind": "종류", "sort": "정렬"}[name])} <select name="{name}">{o}</select></label>'
        boxes = "".join(f'<label><input type="checkbox" name="cols" value="{_esc(k)}"{" checked" if k in cols else ""}> {_esc(v)}</label>'
                        for k, v in self._TK_COLS.items())
        filt = (f'<form method="get" action="/admin/tickets" class="bar">{sel("status", self._TK_LIST_STATUS, status)}'
                f'{sel("kind", {"all": "전체", **self._TK_KIND}, kind)}{sel("sort", self._TK_SORT, sort)}'
                f'<span class="muted">선택 열</span>{boxes}<button type="submit">보기</button></form>')
        head = ["접수일", "종류", "상태", "보낸 사람", "답변", "최근 갱신", "개인정보 기한", "의심"] + [self._TK_COLS[c] for c in cols]
        trs, hint_tag = [], '<span class="tag over">개인정보?</span>'
        for t in rows:
            href = f"/admin/tickets/{quote(t['id'], safe='')}"
            reply = f'있음 <span class="muted">{_esc(self._tk_time(t["reply_at"]))}</span>' if t["has_reply"] else '<span class="muted">없음</span>'
            deadline = self._tk_deadline(t["privacy_received"]) if t["status"] not in ("done", "wontfix") else \
                ('<span class="muted">종결</span>' if t["privacy_received"] else "")
            extra = {"via": _esc(t["via"]), "closed": _esc(self._tk_time(t["closed_at"]))}
            trs.append(f'<tr><td><a href="{_esc(href)}">{_esc(self._tk_time(t["created"]))}</a></td>'
                       f'<td>{_esc(self._TK_KIND.get(t["kind"], t["kind"]))}</td>'
                       f'<td><span class="tag st-{_esc(t["status"])}">{_esc(self._TK_STATUS.get(t["status"], t["status"]))}</span></td>'
                       f'<td class="email">{_esc(t["email"])}</td><td>{reply}</td><td>{_esc(self._tk_time(t["updated"]))}</td>'
                       f'<td>{deadline}</td><td>{hint_tag if t["hint"] else ""}</td>'
                       + "".join(f"<td>{extra[c]}</td>" for c in cols) + "</tr>")
        empty = f'<tr><td colspan="{len(head)}" class="muted">조건에 맞는 문의가 없어요.</td></tr>'
        note = f'<p class="muted">상위 {admin_db.TICKET_LIST_MAX}건만 보여요. 필터를 좁혀 보세요.</p>' if more else ""
        body = (f'<h1>Support</h1><p class="muted">문의와 개인정보 요청을 여기서 처리해요. 원문과 메모는 레코드 페이지에서만 보여요. 행의 접수일을 누르면 열려요.</p>'
                f'<p class="note">개인정보 요청은 운영자 확인 후 처리하며, {admin_db.PRIVACY_DUE_DAYS}일 안에 처리 결과를 안내합니다.</p>'
                f'<p class="note">계정 삭제 요청은 「요청 문구만 적어 주세요. 다른 정보는 넣지 마세요」로 받아요. 처리 = admin_cli.py delete-notify → 7일 뒤 delete-preview·delete-run.</p>{filt}'
                f'<h2>{_esc(self._TK_LIST_STATUS[status])} {len(rows)}건</h2>{note}<div class="tbl"><table><thead><tr>'
                + "".join(f"<th>{_esc(h)}</th>" for h in head) + f'</tr></thead><tbody>{"".join(trs) or empty}</tbody></table></div>')
        return _admin_page("관리 — Support", body, cur="tickets")

    async def admin_ticket(self, request):
        """/admin/tickets/{tid} 레코드 — 원문 전문·보낸 사람·화면·도구·UA·의심·기한 · Path · 답변·메모·재분류 form · 이력(공개/내부)."""
        uid, resp = await self._admin_view(request)
        if resp is not None:
            return resp
        role, tid = request.state.role, request.path_params["tid"]
        try:
            t, hist = await run_in_threadpool(lambda: (admin_db.ticket_detail(role, tid), admin_db.ticket_history(role, tid)))
        except db.CrmError as e:
            status = 404 if e.code == "NOT-FOUND" else 400
            return _admin_page("문의를 찾지 못했어요", f"<h1>문의를 찾지 못했어요</h1><p>{_esc(e.code)}</p>"
                               '<p><a href="/admin/tickets">문의 목록으로</a></p>', status, cur="tickets")
        base = f"/admin/tickets/{quote(t['id'], safe='')}"
        closed = t["status"] in ("done", "wontfix")
        deadline = self._tk_deadline(t["privacy_received"]) if not closed else ""
        meta = [("종류", _esc(self._TK_KIND.get(t["kind"], t["kind"]))), ("보낸 사람", f'<span class="email">{_esc(t["email"])}</span>'),
                ("접수", _esc(self._tk_time(t["created"]))), ("경로", _esc(t["via"])), ("화면", _esc(t["page"])),
                ("도구", _esc(t["tool"]) or '<span class="muted">—</span>'), ("브라우저", _esc(t["ua_family"]) or '<span class="muted">—</span>'),
                ("개인정보 의심", '<span class="tag over">개인정보?</span>' if t["hint"] else '<span class="muted">아님</span>')]
        if t["privacy_received"]:
            meta.append(("개인정보 기한", f'받은 날 {_esc(t["privacy_received"])} · {deadline or "종결"}'))
        if t["closed_at"]:
            meta.append(("종결", _esc(self._tk_time(t["closed_at"]))))
        dl = "".join(f"<dt>{_esc(k)}</dt><dd>{v}</dd>" for k, v in meta)
        path = "".join(
            f'<form method="post" action="{_esc(base)}/status"><input type="hidden" name="status" value="{_esc(k)}">'
            + (f'<button type="submit" class="cur" disabled aria-current="step">{_esc(v)}</button>' if k == t["status"]
               else f'<button type="submit">{_esc(v)}</button>') + "</form>"
            for k, v in self._TK_STATUS.items())
        reply_at = f'<span class="muted">마지막 답변 {_esc(self._tk_time(t["reply_at"]))}</span>' if t["reply_at"] else \
            '<span class="muted">아직 답변이 없어요.</span>'
        kinds = "".join(f'<option value="{_esc(k)}"{" selected" if k == t["kind"] else ""}>{_esc(v)}</option>' for k, v in self._TK_KIND.items())
        reasons = '<option value="">—</option>' + "".join(f'<option value="{_esc(k)}">{_esc(v)}</option>' for k, v in self._TK_REASON.items())
        rows = []
        for h in hist:
            chg = f'{_esc(self._TK_STATUS.get(h["from_val"]) or self._TK_KIND.get(h["from_val"]) or "")} → ' \
                  f'{_esc(self._TK_STATUS.get(h["to_val"]) or self._TK_KIND.get(h["to_val"]) or "")}' if h["to_val"] else '<span class="muted">바뀜</span>'
            badge = '<span class="tag pub">공개</span>' if h["visibility"] == "public" else '<span class="tag int">내부</span>'
            who = _esc(h["actor_email"]) if h["actor_email"] else f'<span class="muted">{_esc((h["actor_id"] or "—")[:8])}</span>'
            rows.append(f'<tr><td>{_esc(self._tk_time(h["at"]))}</td><td>{badge}</td><td>{_esc(self._TK_HIST.get(h["kind"], h["kind"]))}</td>'
                        f'<td>{chg}</td><td>{_esc(self._TK_REASON.get(h["reason"], "")) if h["reason"] else ""}</td>'
                        f'<td class="email">{who}</td></tr>')
        body = (f'<p><a href="/admin/tickets">← 문의 목록</a></p><h1>문의 {_esc(self._TK_KIND.get(t["kind"], t["kind"]))}</h1>'
                f'<dl class="meta">{dl}</dl>'
                f'<h2>원문</h2><pre class="txt">{_esc(t["text"])}</pre>'
                f'<h2>상태</h2><div class="path" role="group" aria-label="상태 단계">{path}</div>'
                f'<h2>공개 답변</h2><p class="muted">사용자의 「내 문의」에 보여요. 빈 칸으로 저장하면 답변을 지워요.</p>'
                f'<form method="post" action="{_esc(base)}/reply" class="edit"><textarea name="reply" rows="5" maxlength="4096">'
                f'{_esc(t["reply"] or "")}</textarea><div>{reply_at} <button type="submit">답변 저장</button></div></form>'
                f'<h2>내부 메모</h2><p class="muted">운영자만 봐요. 사용자의 갱신 시각을 바꾸지 않아요.</p>'
                f'<form method="post" action="{_esc(base)}/note" class="edit"><textarea name="admin_note" rows="3" maxlength="2048">'
                f'{_esc(t["admin_note"])}</textarea><div><button type="submit">메모 저장</button></div></form>'
                f'<h2>재분류</h2><form method="post" action="{_esc(base)}/reclassify" class="bar"><label>종류 <select name="kind">{kinds}</select></label>'
                f'<label>사유 <select name="reason">{reasons}</select></label><span class="muted">사유는 개인정보 요청에서 뺄 때만 골라요.</span>'
                f'<button type="submit">바꾸기</button></form>'
                f'<h2>이력</h2><div class="tbl"><table><thead><tr><th>시각</th><th>보임</th><th>사건</th><th>변경</th><th>사유</th><th>실행자</th>'
                f'</tr></thead><tbody>{"".join(rows) or "<tr><td colspan=6 class=muted>없음</td></tr>"}</tbody></table></div>')
        return _admin_page("관리 — Support", body, cur="tickets", crumbs=[("Support", "/admin/tickets"), ("문의 상세", None)])

    # ── /admin/users 운영자 사용자 뷰(PR #20 Q5) — 조회는 admin_db.userview·users·schema_names 만, 구조 글자는 contract.respond 출력만 ──
    async def admin_users(self, request):
        """/admin/users — 사용자 목록: 오브젝트 n · 관계 N · 그중 연결 있음 M · 표준 상태 코드. 이름 없음(목록은 언제나 admin_noconsent)."""
        _, err = await self._admin_view(request)
        if err:
            return err
        if request.query_params:
            return self._admin_bad("사용자 목록을 열 수 없어요", "쿼리 키를 받지 않아요.", "/admin/users", "users")
        role = request.state.role
        users, views = await run_in_threadpool(lambda: (admin_db.users(role), admin_db.userview(role)))
        trs = []
        for u in users:
            v = views.get(u["id"])
            if v is None:   # 두 조회 사이에 생긴 계정 — 다음 조회에 나온다
                continue
            resp, st = uv_build({**v, "names": False})
            href = f"/admin/users/{_esc(self._opaque(u['id']))}"
            blk = (' <span class="tag blocked">차단</span>' if u["blocked_at"] else "") \
                + (' <span class="tag deleted">삭제됨</span>' if u["status"] == "deleted" else "")   # PR B — 코드만(표시 이름·사람·요약/반응 0)
            trs.append(f'<tr><td class="email"><a href="{href}">{_esc(u["email"])}</a></td><td>{_esc(_kst(u["created"])[:10])}</td>'
                       f'<td>{"관리자" if u["role"] == "admin" else "사용자"}{blk}</td><td>{"동의" if v["names"] else "—"}</td>'
                       f'<td class="num">{_esc(resp["counts"]["objects"])}</td><td class="num">{_esc(resp["counts"]["relations"])}</td>'
                       f'<td class="num">{_esc(sum(1 for n in st["linked"] if n))}</td><td class="num">{_esc(resp["counts"]["fields"])}</td>'
                       f'<td>{_uv_std(resp["std"])}</td>'
                       f'<td><a href="{href}">상세</a></td></tr>')
        body = (f'<h1>Users — 사용자 {len(trs)}명</h1><p class="note">오브젝트·필드 = 적용된 사용자 정의(보관 제외) · 관계 = 참조 필드 · '
                f'연결 있음 = 정상 연결이 1개 이상인 관계 · 표준 = 설치 버전·표식 상태·표준 오브젝트별 상태 코드 · '
                f'이름 공유 = 유효 선택 동의(현재 버전). 개수만, 이름 없음.</p>'
                + _table(['<th>이메일</th><th>가입일</th><th>역할</th><th>이름 공유</th><th class="num">오브젝트</th><th class="num">관계</th>'
                          '<th class="num">연결 있음</th><th class="num">필드</th><th>표준</th><th></th>'], trs))
        return _admin_page("관리 — Users", body, cur="users")

    async def admin_user(self, request):
        """/admin/users/<불투명 id> — 요약 → DMO 그래프 → 관계 집계 → 무결성 → 표준 상태 → 구조 트리 → 문의 요약.
        이름은 이름 공유 유효 동의자만(admin_consent — admin_db.schema_names 분기 행). 모르는 id 404(입력을 되비추지 않는다)."""
        _, err = await self._admin_view(request)
        if err:
            return err
        if request.query_params:
            return self._admin_bad("사용자를 열 수 없어요", "쿼리 키를 받지 않아요.", "/admin/users", "users")
        role = request.state.role
        users = await run_in_threadpool(admin_db.users, role)
        u = next((x for x in users if self._opaque(x["id"]) == request.path_params["oid"]), None)
        if u is None:
            return _admin_page("사용자를 찾지 못했어요", '<h1>사용자를 찾지 못했어요</h1><p>NOT-FOUND</p><p><a href="/admin/users">사용자 목록으로</a></p>',
                               404, cur="users")
        views, (onames, fnames) = await run_in_threadpool(lambda: (admin_db.userview(role, u["id"]), admin_db.schema_names(role, u["id"])))
        v = views.get(u["id"])
        if v is None:
            return _admin_page("사용자를 찾지 못했어요", '<h1>사용자를 찾지 못했어요</h1><p>NOT-FOUND</p><p><a href="/admin/users">사용자 목록으로</a></p>',
                               404, cur="users")
        resp, st = uv_build(v, onames, fnames)
        body = render_userview(u, self._opaque(u["id"]), v["names"], resp, st, v["tickets"])
        return _admin_page("관리 — Users", body, cur="users", crumbs=[("Users", "/admin/users"), ("사용자 상세", None)])

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
            Route("/api/records", u("web:records", self.api_records)),   # PR① 홈 기록 「더 보기」 — 읽기만
            Route("/api/candidate/decide", u("web:candidate_decide", self.api_decide), methods=["POST"]),
            Route("/api/item", u("web:item", self.api_item)),
            Route("/api/item/decide", u("web:item_decide", self.api_item_decide), methods=["POST"]),
            Route("/api/std/preview", u("web:std_preview", self.api_std_preview)),
            Route("/api/std/choose", u("web:std_choose", self.api_std_choose), methods=["POST"]),
            Route("/api/feedback", u("web:feedback", self.api_feedback), methods=["GET", "POST"]),
            Route("/api/feedback/{tid}", u("web:feedback_get", self.api_feedback_one)),
            Route("/api/optional-consent", u("web:optional_consent", self.api_optional_consent), methods=["POST"]),
            Route("/api/desk-visibility", u("web:desk_visibility", self.api_desk_visibility), methods=["POST"]),
            Route("/api/me/name", u("web:me_name", self.api_me_name), methods=["POST"]),   # PR B 코어 쓰기
            Route("/api/nav/accept", u("web:nav_accept", self.api_nav_accept), methods=["POST"]),
            Route("/api/people", u("web:people", self.api_people), methods=["POST"]),
            Route("/api/people/{pid}", u("web:person", self.api_person), methods=["POST"]),
            Route("/api/people/{pid}/delete", u("web:person_delete", self.api_person_delete), methods=["POST"]),
            Route("/api/sources", u("web:sources", self.api_sources), methods=["POST"]),
            Route("/api/reflections", u("web:reflections", self.api_reflections), methods=["POST"]),
            Route("/api/reflections/{rid}", u("web:reflection", self.api_reflection), methods=["POST"]),
            Route("/api/reflections/{rid}/review", u("web:reflection_review", self.api_reflection_review), methods=["POST"]),
            Route("/api/reflections/{rid}/delete", u("web:reflection_delete", self.api_reflection_delete), methods=["POST"]),
            # board 이전 — 연결 장치 · 승인 대기 · 「나」 집계 · 활동/사람/출처/축 상세
            Route("/api/devices", u("web:devices", self.api_devices), methods=["GET", "POST"]),
            Route("/api/devices/{did}/reissue", u("web:device_reissue", self.api_device_reissue), methods=["POST"]),
            Route("/api/devices/{did}/revoke", u("web:device_revoke", self.api_device_revoke), methods=["POST"]),
            Route("/api/devices/{did}/policy", u("web:device_policy", self.api_device_policy), methods=["POST"]),
            Route("/api/inbox", u("web:inbox", self.api_inbox)),
            Route("/api/inbox/{iid}", u("web:inbox_get", self.api_inbox_one)),
            Route("/api/inbox/{iid}/approve", u("web:inbox_approve", self.api_inbox_approve), methods=["POST"]),
            Route("/api/inbox/{iid}/exclude", u("web:inbox_exclude", self.api_inbox_exclude), methods=["POST"]),
            Route("/api/me/aggregate", u("web:me_aggregate", self.api_me_aggregate)),
            Route("/api/me/layout", u("web:me_layout", self.api_me_layout), methods=["GET", "PUT", "DELETE"]),   # PR ①-b 쓰기 예외(이 라우트만)
            Route("/api/me/stats", u("web:me_stats", self.api_me_stats)),   # PR ①-b — 읽기만
            # PR ②-a 작업 전달 — 장치(Bearer 장치 토큰) 2 · 웹(세션) 3
            Route("/api/device/jobs", self.api_device_jobs),
            Route("/api/device/jobs/{jid}/{op}", self.api_device_job, methods=["GET", "POST"]),
            Route("/api/jobs", u("web:jobs", self.api_jobs), methods=["POST"]),
            Route("/api/jobs/{jid}", u("web:job", self.api_job)),
            Route("/api/jobs/{jid}/{op}", u("web:job_op", self.api_job_op), methods=["POST"]),
            Route("/api/stack/previews/{pid}/exclude", u("web:stack_exclude", self.api_stack_exclude), methods=["POST"]),
            Route("/api/stack/sessions/{sid}", u("web:stack_session", self.api_stack_session)),
            Route("/api/activities", u("web:activities", self.api_activities)),
            Route("/api/activities/{aid}", u("web:activity", self.api_activity)),
            Route("/api/people/{pid}/context", u("web:person_context", self.api_person_context)),
            Route("/api/sources/{sid}/context", u("web:source_context", self.api_source_context)),
            Route("/api/axes/{iid}/context", u("web:axis_context", self.api_axis_context)),
            Route("/admin", self.admin_index),
            Route("/admin/reauth", self.admin_reauth),
            Route("/admin/setup", self.admin_setup),
            Route("/admin/role", self.admin_role, methods=["POST"]),
            Route("/admin/signout", self.admin_signout, methods=["POST"]),
            Route("/admin/block", self.admin_block, methods=["POST"]),
            Route("/admin/unblock", self.admin_unblock, methods=["POST"]),
            Route("/admin/users", self.admin_users),
            Route("/admin/users/{oid}", self.admin_user),
            Route("/admin/schema", self.admin_schema),
            Route("/admin/schema/objects/{oid}", self.admin_schema_object),
            Route("/admin/schema/templates/{tid}", self.admin_schema_template),
            Route("/admin/schema/templates/{tid}/objects/{oid}", self.admin_schema_template_object),
            Route("/admin/find", self.admin_find),
            Route("/admin/reports", self.admin_reports),
            Route("/admin/tickets", self.admin_tickets),
            Route("/admin/tickets/{tid}", self.admin_ticket),
            Route("/admin/tickets/{tid}/status", self.admin_ticket_status, methods=["POST"]),
            Route("/admin/tickets/{tid}/reply", self.admin_ticket_reply, methods=["POST"]),
            Route("/admin/tickets/{tid}/note", self.admin_ticket_note, methods=["POST"]),
            Route("/admin/tickets/{tid}/reclassify", self.admin_ticket_reclassify, methods=["POST"]),
        ]
