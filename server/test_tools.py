"""서버 시험 — Supabase(DATABASE_URL, server/.env)에 붙어 돈다. 비밀값은 출력하지 않는다.

1) 인증 단위 시험: 가짜 RSA 키로 만든 JWT 를 AuthKitVerifier 가 거르는지 (JWKS 는 가짜 키 주입)
2) 도구 흐름 시험: SDK 내장 클라이언트로 서버를 직접 부른다 (HTTP·인증 없이, CANON_DEV 사용자)
3) HTTP 시험: Starlette 앱을 ASGI 로 불러 토큰 → 사용자 결정, /health, PRM, 401 을 본다
4) 웹 시험: 시작 화면 / → /login → 가짜 토큰 교환 → /auth/callback → 세션 쿠키 → /api/* · 결정 저장 · Origin 거절 · 로그아웃(다음 로그인은 prompt=login) · /help·/privacy
5) 정의 층 시험(2단계): 정의 제안→적용·충돌·깨지는 변경 거절 · 항목 제안→적용→확정→되돌리기·이력 · 관계 검증 · 승격 · record_attach
   · question_id "" → NULL · 원작자 템플릿 시드(시험 사용자에게만) · /api/item·/api/item/decide
6) 전역 상한(α1): 요청 본문 256KB(413, Content-Length·chunked) · MCP SSE 응답이 미들웨어 뒤에서 그대로 · 인자 길이 PAYLOAD-LIMIT
   (MCP·웹) · DB 크기 400MB 넘으면 비관리자 쓰기 QUOTA-GLOBAL(관리자·읽기 정상, 측정 실패면 허용)
7) 문의 데스크(desk-0): 401·Origin 403 · 4KB+1 PAYLOAD-LIMIT(웹·MCP) · 사용자 11번째·전역 101번째 RATE-LIMIT · QUOTA-GLOBAL 면제
   · 「내 문의」에 운영자 메모·hint·남의 글 없음 · privacy → privacy_log · hint · page·tool 정규화 · MCP echo · 파기 181/179일·reopen
   · 비DEV+CANON_CONTACT 없음·공백 → 기동 성공(선택 env) · app.html 문의 구간 esc 정적 검사
   · 관리자 문의함: 비관리자 응답에 admin 키 없음 · 상태/메모 API 401·403(Origin·비관리자) · 상태 전이·closed_at · 메모 2KB · 띠 숫자

실행: server/.venv/bin/python server/test_tools.py   (어느 폴더에서든)
시험 사용자 tester@example.com · other@example.com 은 시작·끝에 지운다(cascade).
운영 DB(옛 스키마)에 붙어 새 코드를 시험할 땐 CANON_TEST_SCHEMA=<이름> — 그 이름의 임시 스키마를 만들어
최종형을 init_schema 로 깔고 그 안에서만 돈 뒤 끝에 지운다(운영 테이블은 안 건드린다). CI 는 빈 Postgres 라 안 쓴다.
"""
import asyncio
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load_env(path):
    """KEY=VALUE 줄만 읽는다. 이미 있는 환경변수는 덮지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.removeprefix("export ").strip()
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))


load_env(HERE / ".env")
assert os.environ.get("DATABASE_URL"), "DATABASE_URL 이 없어요 (server/.env)"
FAKE_ISSUER = "https://fake.authkit.test"
PUBLIC = "http://127.0.0.1:8800"
os.environ["CANON_DEV"] = "1"
os.environ["CANON_DEV_USER"] = "tester@example.com"
os.environ["AUTHKIT_ISSUER"] = FAKE_ISSUER          # 토큰 경로를 HTTP 시험에서 실제로 태운다
os.environ["CANON_PUBLIC_URL"] = PUBLIC

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402
import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from mcp.client import Client  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import web  # noqa: E402

TEST_EMAILS = ("tester@example.com", "other@example.com", "def1@example.com", "def2@example.com",
               "core1@example.com", "core2@example.com") + tuple(f"desk{i}@example.com" for i in range(11))   # desk* = 전역 한도 시험
RESOURCE = f"{PUBLIC}/mcp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WRONG_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}


def fake_jwks(verifier):
    verifier.jwks.fetch_data = lambda: {"keys": [JWK]}


_DEFAULT = object()


def make_token(key=KEY, aud=RESOURCE, iss=FAKE_ISSUER, exp_in=600, sub="user_test_sub", alg="RS256", kid="k1",
               scope=_DEFAULT, scp=None, sid=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub, "client_id": "client_test",
              "iat": now, "exp": now + exp_in, "jti": f"t{now}"}
    if scope is _DEFAULT:
        scope = "openid email"
    if scope is not None:
        claims["scope"] = scope
    if scp is not None:
        claims["scp"] = scp
    if sid is not None:
        claims["sid"] = sid
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def cleanup():
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchall()]
    for uid in ids:
        db.user_delete(uid)


def val(res):
    """도구 결과에서 구조화 값 또는 텍스트를 꺼낸다."""
    if getattr(res, "structured_content", None) is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


# ── 1) 인증 단위 ───────────────────────────────────────

async def test_verifier():
    v = auth.AuthKitVerifier(FAKE_ISSUER, RESOURCE)
    fake_jwks(v)
    ok = await v.verify_token(make_token())
    assert ok is not None and ok.subject == "user_test_sub" and ok.resource == RESOURCE, ok
    assert ok.client_id == "client_test" and ok.scopes == ["openid", "email"] and ok.claims["iss"] == FAKE_ISSUER
    assert await v.verify_token(make_token(aud=[RESOURCE, "other"])) is not None, "aud 목록에 포함되면 통과"
    assert await v.verify_token(make_token(key=WRONG_KEY)) is None, "서명 틀림"
    assert await v.verify_token(make_token(aud="https://evil.example/mcp")) is None, "aud 틀림"
    assert await v.verify_token(make_token(aud="client_01ENVCLIENT")) is None, "resource 없이 받은 토큰(aud=client ID)"
    assert await v.verify_token(make_token(exp_in=-60)) is None, "만료"
    assert await v.verify_token(make_token(iss="https://evil.authkit.app")) is None, "iss 틀림"
    assert await v.verify_token(make_token(key="shared-secret-shared-secret-32by", alg="HS256")) is None, "HS256 거절"
    assert await v.verify_token(make_token(kid="unknown")) is None, "모르는 kid"
    assert await v.verify_token("not-a-jwt") is None, "형식 깨짐"
    # scope 표현: 문자열·배열·없음 (없어도 거절 안 함 — 403 은 미들웨어 몫)
    t = await v.verify_token(make_token(scope="openid email profile"))
    assert t.scopes == ["openid", "email", "profile"], t.scopes
    t = await v.verify_token(make_token(scope=None, scp=["openid", "email"]))
    assert t is not None and t.scopes == ["openid", "email"], t
    t = await v.verify_token(make_token(scope=None, scp="openid email"))
    assert t is not None and t.scopes == ["openid", "email"], t
    t = await v.verify_token(make_token(scope=None))
    assert t is not None and t.scopes == [], t
    print("OK — 인증 단위 시험 통과")


# ── 2) 도구 흐름 ───────────────────────────────────────

async def test_tools():
    os.environ["CANON_DEV_USER"] = "tester@example.com"
    async with Client(main.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert {"state_get", "record_add", "candidates_propose", "candidate_decide"} <= tools, tools

        st = val(await c.call_tool("state_get", {}))
        assert st["next"]["kind"] == "record"

        # 도움말: 웹 /help 와 같은 guides 원문
        hw = val(await c.call_tool("help_get", {"topic": "words"}))
        assert hw["topic"] == "words" and "후보" in hw["text"] and hw["text"].startswith("# 10. 말 뜻"), hw["text"][:80]
        hs = val(await c.call_tool("help_get", {"topic": "start"}))
        assert hs["text"].startswith("# 09.") and "후보" in hs["text"], hs["text"][:80]
        hd = val(await c.call_tool("help_get", {}))
        assert hd == hw, "기본 topic 은 words"
        ha = val(await c.call_tool("help_get", {"topic": "all"}))
        assert ha["topic"] == "all" and ha["text"] == hs["text"] + "\n\n" + hw["text"], "all = start + words"
        hdesc = {t.name: t for t in (await c.list_tools()).tools}["help_get"].description
        assert "하나만 읽는다" in hdesc and "문서에서 확인되지 않아요" in hdesc and "제품 밖" in hdesc, hdesc
        assert "help_get 으로 해당 topic(start 또는 words) 하나만" in main.INSTRUCTIONS
        bad_t = await c.call_tool("help_get", {"topic": "faq"})
        assert bad_t.is_error and "validation error" in bad_t.content[0].text, bad_t

        q = val(await c.call_tool("question_add", {"text": "새 도구를 팀에 들일 때 무엇부터 확인해야 하나"}))
        r = val(await c.call_tool("record_add", {"text": "도입 회의에서 입력 담당을 먼저 정했다. 도구 비교는 그다음이었다.",
                                                  "title": "도입 회의", "question_id": q["id"]}))
        # 근거 검사: 원문에 없는 인용은 버림, 있는 것만 저장
        out = val(await c.call_tool("candidates_propose", {
            "record_id": r["id"], "question_id": q["id"], "origin": "ai",
            "candidates": [{"text": "입력 담당을 먼저 정한다", "quote": "입력 담당을 먼저 정했다"},
                           {"text": "지어낸 판단", "quote": "원문에 없는 문장"}]}))
        assert len(out["added"]) == 1 and out["added"][0]["status"] == "pending", out
        assert out["dropped"][0]["why"].startswith("근거 인용이 원문에"), out
        cid = out["added"][0]["id"]
        # 같은 근거 구간은 중복 저장 안 됨 (문장을 바꿔도)
        again = val(await c.call_tool("candidates_propose", {
            "record_id": r["id"], "question_id": q["id"], "origin": "ai",
            "candidates": [{"text": "다른 문장", "quote": "입력 담당을 먼저 정했다"}]}))
        assert not again["added"] and again["dropped"][0]["why"] == "이미 있는 후보", again

        # 결정과 되돌리기, 이력
        c1 = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "edit", "text": "입력 담당부터 정한다"}))
        assert c1["text"] == "입력 담당부터 정한다" and c1["edited_by_user"] is True
        c2 = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "confirm"}))
        assert c2["status"] == "confirmed"
        c3 = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "pending"}))
        assert c3["status"] == "pending"
        tester = db.user_by_email("tester@example.com")["id"]
        hist = db.candidate_history(tester, cid)
        assert [h["action"] for h in hist] == ["edit", "confirm", "pending"] and hist[0]["prev_text"] == "입력 담당을 먼저 정한다"
        assert hist[1]["prev_status"] == "pending" and hist[2]["prev_status"] == "confirmed", hist

        st = val(await c.call_tool("state_get", {}))
        assert st["next"]["kind"] == "review" and len(st["candidates"]) == 1 and len(st["journal"]) == 1

        # (d) 같은 결정 반복은 이력을 늘리지 않는다
        await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "confirm"})
        n = len(db.candidate_history(tester, cid))
        again_c = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "confirm"}))
        assert again_c["status"] == "confirmed" and len(db.candidate_history(tester, cid)) == n == 4
        # (e) 확정된 문장을 고치면 pending 으로
        e = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "edit", "text": "입력 담당을 맨 먼저 정한다"}))
        assert e["status"] == "pending" and e["text"] == "입력 담당을 맨 먼저 정한다", e
        assert db.candidate_history(tester, cid)[-1]["prev_status"] == "confirmed"

        # 오류는 ToolError 로 모델에 전달
        bad = await c.call_tool("candidate_decide", {"candidate_id": "c_nope", "action": "confirm"})
        assert bad.is_error and "ARG-INVALID" in bad.content[0].text, bad

        # (a) 원문에 두 번 나오는 구간 → 2곳
        r2 = val(await c.call_tool("record_add", {"question_id": q["id"], "title": "반복 기록",
            "text": "회의 전에 메모했다. 회의 전에 메모했다고 착각했다. 담당자 표를 먼저 만들었다. 비교표는 미뤘다. 일정은 고정했다."}))
        twice = val(await c.call_tool("candidates_propose", {"record_id": r2["id"], "question_id": q["id"],
            "candidates": [{"text": "메모 습관", "quote": "회의 전에 메모했다"}]}))
        assert not twice["added"] and "2곳" in twice["dropped"][0]["why"], twice
        # (c) 한 번에 4개 → 도구 오류(자르지 않음)
        four = await c.call_tool("candidates_propose", {"record_id": r2["id"], "question_id": q["id"], "candidates": [
            {"text": f"후보{i}", "quote": qt} for i, qt in enumerate(
                ["담당자 표를 먼저 만들었다", "비교표는 미뤘다", "일정은 고정했다", "회의 전에 메모했다고 착각"])]})
        assert four.is_error and "3개까지" in four.content[0].text, four
        assert db.candidates(tester) and all(x["record_id"] != r2["id"] for x in db.candidates(tester)), "4개 요청은 하나도 저장 안 됨"
        # (b) 기록당 누적 4번째 → 3개까지
        three = val(await c.call_tool("candidates_propose", {"record_id": r2["id"], "question_id": q["id"], "candidates": [
            {"text": "담당자 표부터", "quote": "담당자 표를 먼저 만들었다"},
            {"text": "비교는 나중", "quote": "비교표는 미뤘다"},
            {"text": "일정 고정", "quote": "일정은 고정했다"}]}))
        assert len(three["added"]) == 3, three
        fourth = val(await c.call_tool("candidates_propose", {"record_id": r2["id"], "question_id": q["id"],
            "candidates": [{"text": "착각", "quote": "회의 전에 메모했다고 착각"}]}))
        assert not fourth["added"] and "3개까지" in fourth["dropped"][0]["why"], fourth

        # (f) 사용자 격리: 다른 이메일은 빈 상태, 남의 기록에 후보를 못 붙인다
        os.environ["CANON_DEV_USER"] = "other@example.com"
        st2 = val(await c.call_tool("state_get", {}))
        assert st2["questions"] == [] and st2["journal"] == [] and st2["candidates"] == []
        other = db.user_by_email("other@example.com")["id"]
        try:
            db.candidates_propose(other, r["id"], q["id"], [{"text": "x", "quote": "입력 담당"}], "ai")
            raise AssertionError("다른 사용자의 기록에 후보가 붙었다")
        except db.CanonError as err:
            assert err.code == "RECORD-MISSING", err.code
        cross = await c.call_tool("candidates_propose", {"record_id": r["id"], "question_id": q["id"],
            "candidates": [{"text": "x", "quote": "입력 담당"}]})
        assert cross.is_error and "RECORD-MISSING" in cross.content[0].text, cross
        os.environ["CANON_DEV_USER"] = "tester@example.com"
    print("OK — 도구 흐름 통과")
    return q["id"]


# ── 3) HTTP (토큰 → 사용자, 401, PRM, health) ─────────

class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


async def test_http():
    verifier = main._auth_kwargs["token_verifier"]
    fake_jwks(verifier)
    seen = []

    def fake_userinfo(url, headers=None, timeout=None):
        seen.append(url)
        if headers["Authorization"] == f"Bearer {nomail}":
            return _Resp({"sub": "user_nomail_sub"})
        return _Resp({"sub": "user_test_sub", "email": "tester@example.com", "email_verified": True})

    nomail = make_token(sub="user_nomail_sub")
    real_post, auth.httpx.post = auth.httpx.post, fake_userinfo
    purged, real_purge = [], db.tickets_purge

    def boom_purge():   # desk-0: 시작 때 문의 파기가 실패해도 기동은 계속한다
        purged.append(1)
        raise RuntimeError("파기 실패 주입")
    db.tickets_purge = boom_purge
    # DEV 폴백이 쓰이면 other 의 빈 상태가 나온다 → 토큰 경로가 tester 로 가는지 구분된다
    os.environ["CANON_DEV_USER"] = "other@example.com"
    hdr = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "state_get", "arguments": {}}}
    try:
        async with main.lifespan(main.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                r = await h.get("/health")
                assert r.status_code == 200 and r.json() == {"ok": True, "db": True}, r.text
                assert purged == [1], "lifespan 이 시작 때 파기를 한 번 부른다(실패해도 기동)"
                prm = (await h.get("/.well-known/oauth-protected-resource/mcp")).json()
                assert prm["resource"] == RESOURCE and prm["authorization_servers"] == [FAKE_ISSUER], prm
                assert prm["scopes_supported"] == ["openid", "email"], prm
                r = await h.post("/mcp", headers=hdr, json=call)
                assert r.status_code == 401 and "resource_metadata=" in r.headers.get("www-authenticate", ""), r.headers
                r = await h.post("/mcp", headers={**hdr, "authorization": f"Bearer {make_token(key=WRONG_KEY)}"}, json=call)
                assert r.status_code == 401, r.status_code
                r = await h.post("/mcp", headers={**hdr, "authorization": f"Bearer {make_token(aud='https://evil.example/mcp')}"}, json=call)
                assert r.status_code == 401, r.status_code
                # scope 가 없거나 email 이 빠진 토큰 → 403 insufficient_scope
                for bad_scope in (None, "openid"):
                    r = await h.post("/mcp", headers={**hdr, "authorization": f"Bearer {make_token(scope=bad_scope)}"}, json=call)
                    assert r.status_code == 403 and "insufficient_scope" in r.headers.get("www-authenticate", ""), (bad_scope, r.status_code)
                # userinfo 에 email 이 없으면 진단 꼬리표(키 이름·scope 만, 값 없음)
                r = await h.post("/mcp", headers={**hdr, "authorization": f"Bearer {nomail}"}, json=call)
                res = r.json()["result"]
                msg = res["content"][0]["text"]
                assert res.get("isError") and "AUTH-NO-EMAIL" in msg, res
                assert "토큰 scope: ['email', 'openid']" in msg and "userinfo 키: ['sub']" in msg, msg
                assert "user_nomail_sub" not in msg, msg
                seen.clear()
                tok = make_token()
                r = await h.post("/mcp", headers={**hdr, "authorization": f"Bearer {tok}"}, json=call)
                assert r.status_code == 200 and r.headers["content-type"].startswith("application/json"), (r.status_code, r.text[:300])
                res = r.json()["result"]
                assert not res.get("isError"), res
                st = res.get("structuredContent") or json.loads(res["content"][0]["text"])
                assert len(st["questions"]) == 1 and len(st["journal"]) == 2, "토큰의 sub → tester 로 결정돼야 함"
                assert seen == [f"{FAKE_ISSUER}/oauth2/userinfo"], seen
                # 두 번째 호출은 sub 로 바로 찾는다(userinfo 안 부름)
                r = await h.post("/mcp", headers={**hdr, "authorization": f"Bearer {tok}"}, json=call)
                assert r.status_code == 200 and len(seen) == 1, seen
                with db.tx() as c:
                    row = c.execute("SELECT auth_subject FROM users WHERE email='tester@example.com'").fetchone()
                assert row["auth_subject"] == "user_test_sub", row
                # Host 검사(DNS rebinding)
                r = await h.post("/mcp", headers={**hdr, "host": "evil.example", "authorization": f"Bearer {tok}"}, json=call)
                assert r.status_code == 421, r.status_code
                # 웹 상태: DEV 라 응답
                r = await h.get("/api/state")
                assert r.status_code == 200, r.status_code
                await limits_http(h, {**hdr, "authorization": f"Bearer {tok}"})   # 6) 절 — 세션 매니저 run() 은 한 번만 돈다
    finally:
        auth.httpx.post, db.tickets_purge = real_post, real_purge
        os.environ["CANON_DEV_USER"] = "tester@example.com"
    print("OK — HTTP 인증 경로 통과")


# ── 4) 웹 로그인·API ────────────────────────────────────

async def test_web():
    import base64, hashlib
    from urllib.parse import parse_qs, urlparse
    site = main.site
    verifier = main._auth_kwargs["token_verifier"]
    fake_jwks(verifier)
    site.client_id, site.dev, site.dev_user = "client_test", False, ""   # 운영처럼: DEV 폴백 없이 세션만
    exchanged = []
    tok_sid = {"sid": None}   # 가짜 토큰에 넣을 sid claim — 시험 중에 바꾼다(세션에 안 담기는지 본다)

    async def fake_token_post(data):
        exchanged.append(data)
        return 200, {"access_token": make_token(sid=tok_sid["sid"]), "token_type": "Bearer"}

    async def login_once(h):
        r = await h.get("/login")
        st = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = await h.get("/auth/callback", params={"code": "c2", "state": st})
        assert r.status_code == 302 and r.headers["location"] == "/app", (r.status_code, r.text[:200])

    def fake_userinfo(url, headers=None, timeout=None):
        return _Resp({"sub": "user_test_sub", "email": "tester@example.com", "email_verified": True})

    real_post, auth.httpx.post = auth.httpx.post, fake_userinfo
    real_tp, site.token_post = site.token_post, fake_token_post
    origin = {"origin": PUBLIC}
    try:
        if True:   # lifespan(MCP 세션 매니저)은 한 번만 돈다 — 웹 경로는 그것 없이 라우팅된다
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                r = await h.get("/app")
                assert r.status_code == 302 and r.headers["location"] == "/", "세션 없으면 시작 화면 / 으로"
                r = await h.get("/")
                assert r.status_code == 200 and "로그인" in r.text and 'href="/login"' in r.text, (r.status_code, r.text[:200])
                assert r.headers.get("cache-control") == "no-store" and "로그아웃했어요" not in r.text and "fresh=1" not in r.text
                r = await h.get("/", params={"out": "1"})
                assert r.status_code == 200 and "로그아웃했어요" in r.text and 'role="status"' in r.text, r.text[:200]
                assert "다시 로그인하면 계정을 고를 수 있어요" in r.text, r.text[:400]
                assert 'href="/login?fresh=1"' in r.text and 'href="/login"' not in r.text, "로그아웃 직후 [로그인]은 fresh=1"
                assert 'href="/help"' in r.text, "시작 화면 하단에 도움말"
                assert 'href="/privacy"' in r.text, "시작 화면 하단에 처리방침"
                # 도움말: 로그인 없이 200, 표는 <table>, 10 으로의 링크는 #words, 세션 없으면 [로그인]
                r = await h.get("/help")
                assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
                for word in ("말 뜻", "기록", "도움말", "<table>", 'href="#words"', 'id="start"', 'id="words"', "--accent: #186A4B"):
                    assert word in r.text, word
                assert 'href="/login"' in r.text and 'href="/app"' not in r.text and "|---|" not in r.text and "**" not in r.text
                assert 'href="/privacy"' in r.text, "도움말 하단에 처리방침"
                # 처리방침: 로그인 없이 200, 같은 렌더러(escape), 09·10 링크는 /help#…, {{CONTACT_LINE}} 은 env CANON_CONTACT(선택 — 없으면 메일 줄 없음)
                r = await h.get("/privacy")
                assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
                for word in ("개인정보 처리방침", 'id="privacy"', "<table>", "Render Services", "Supabase", "WorkOS", "싱가포르", "미국",
                             "제28조의8", "v1 · 2026-10-18", 'href="/help"', 'href="/login"', "--accent: #186A4B"):
                    assert word in r.text, word
                assert "{{CONTACT" not in r.text and "「개인정보 요청」" in r.text and 'href="/app"' not in r.text and "|---|" not in r.text and "**" not in r.text
                real_contact, real_privacy = os.environ.get("CANON_CONTACT"), site.privacy_html
                try:
                    os.environ["CANON_CONTACT"] = '<b>x</b> [y](javascript:alert(1))'
                    site.load_help(web.GUIDES_DIR)
                    r = await h.get("/privacy")
                    assert r.status_code == 200 and "&lt;b&gt;x&lt;/b&gt;" in r.text and "<b>x" not in r.text, r.text[-800:]
                    assert 'href="javascript' not in r.text.lower() and "{{CONTACT" not in r.text and "메일: " in r.text, "연락처 env 도 렌더러 escape·링크 검사를 탄다"
                    os.environ.pop("CANON_CONTACT")
                    site.load_help(web.GUIDES_DIR)
                    r = await h.get("/privacy")
                    assert r.status_code == 200 and "메일: " not in r.text and "{{CONTACT" not in r.text, "연락처 env 없으면 메일 줄 없음(500 아님)"
                finally:
                    if real_contact is None:
                        os.environ.pop("CANON_CONTACT", None)
                    else:
                        os.environ["CANON_CONTACT"] = real_contact
                    site.privacy_html = real_privacy
                # 렌더러 안전: raw HTML 은 글자로, javascript:·data:·// 링크는 글만, http(s)·#·상대경로는 링크
                bad_md = ('# 제목 <script>alert(1)</script>\n\n<img src=x onerror=alert(1)> [x](javascript:alert(1)) [y](data:text/html,1) '
                          '[z](//evil.example) [w](JaVaScRiPt:alert(1))\n\n| a | <b>b</b> |\n|---|---|\n| [t](javascript:x) | `<i>` |\n\n'
                          '[ok1](https://example.com/a) [ok2](#words) [ok3](10-words.md) [ok4](notes/a.md) [버튼]')
                hb, _, _ = web.md_to_html(bad_md, "t")
                assert "<script" not in hb and "<img" not in hb and "<b>" not in hb and "<i>" not in hb, hb
                assert "&lt;script&gt;" in hb and "&lt;img" in hb and "<code>&lt;i&gt;</code>" in hb, hb
                assert 'href="javascript' not in hb.lower() and 'href="data' not in hb and 'href="//' not in hb, hb
                for ok in ('href="https://example.com/a"', 'href="#words"', 'href="notes/a.md"', "[버튼]"):
                    assert ok in hb, (ok, hb)
                assert hb.count('href="#words"') == 2 and "<table>" in hb, hb
                real_guides, real_help, real_privacy = site.guides, site.help_html, site.privacy_html
                site.load_help(Path("/nonexistent-guides"))
                try:
                    r = await h.get("/help")
                    assert r.status_code == 503 and "도움말" in r.text, r.status_code
                    r = await h.get("/privacy")
                    assert r.status_code == 503 and "처리방침" in r.text, r.status_code
                    async with Client(main.mcp) as mc:
                        miss = await mc.call_tool("help_get", {"topic": "all"})
                    assert miss.is_error and "HELP-MISSING" in miss.content[0].text, miss
                finally:
                    site.guides, site.help_html, site.privacy_html = real_guides, real_help, real_privacy
                r = await h.get("/login", params={"fresh": "1"})
                assert r.status_code == 302 and parse_qs(urlparse(r.headers["location"]).query)["prompt"] == ["login"], r.headers
                assert (await h.get("/api/state")).status_code == 401
                assert (await h.post("/api/candidate/decide", json={}, headers=origin)).status_code == 401
                r = await h.get("/login")
                assert r.status_code == 302 and "canon_login" in r.headers.get("set-cookie", ""), r.headers
                q = parse_qs(urlparse(r.headers["location"]).query)
                assert q["client_id"] == ["client_test"] and q["resource"] == [RESOURCE] and q["code_challenge_method"] == ["S256"], q
                assert q["redirect_uri"] == [f"{PUBLIC}/auth/callback"] and q["scope"] == ["openid email"], q
                assert "prompt" not in q, "일반 /login 엔 prompt 없음"
                state = q["state"][0]
                # state 틀림 → 400, 교환 안 함
                r = await h.get("/auth/callback", params={"code": "c1", "state": "wrong"})
                assert r.status_code == 400 and not exchanged
                r = await h.get("/auth/callback", params={"code": "c1", "state": state})
                assert r.status_code == 302 and r.headers["location"] == "/app", (r.status_code, r.text[:200])
                assert "canon_session" in r.headers.get("set-cookie", "")
                assert "max-age=604800" in r.headers.get("set-cookie", "").lower(), "세션 쿠키 7일(PR①)"
                d = exchanged[0]
                chal = base64.urlsafe_b64encode(hashlib.sha256(d["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                assert chal == q["code_challenge"][0], "PKCE verifier 가 쿠키의 challenge 와 맞아야"
                assert d["grant_type"] == "authorization_code" and d["code"] == "c1" and d["resource"] == RESOURCE
                r = await h.get("/app")
                assert r.status_code == 200 and 'href="/help"' in r.text and 'href="/privacy"' in r.text, "앱 푸터에 도움말·처리방침"
                r = await h.get("/help")
                assert r.status_code == 200 and 'href="/app"' in r.text and 'href="/login"' not in r.text, "세션 있으면 [노트로]"
                r = await h.get("/privacy")
                assert r.status_code == 200 and 'href="/app"' in r.text and 'href="/login"' not in r.text, "처리방침도 세션 있으면 [대시보드로]"
                r = await h.get("/")
                assert r.status_code == 302 and r.headers["location"] == "/app", "세션 있으면 시작 화면 대신 /app"
                st = (await h.get("/api/state")).json()
                assert st["user"]["email"] == "tester@example.com", st.get("user")
                uid = db.user_by_email("tester@example.com")["id"]
                assert (await h.get("/api/journal", params={"id": "r_none"})).status_code == 404
                qq = db.question_add(uid, "웹에서 결정 저장 시험")
                rec = db.record_add(uid, "웹 기록", "회의에서 입력 담당을 먼저 정했다.", qq["id"])
                added, _ = db.candidates_propose(uid, rec["id"], qq["id"], [{"text": "담당 먼저", "quote": "입력 담당을 먼저 정했다"}], "ai")
                cid = added[0]
                j = (await h.get("/api/journal", params={"id": rec["id"]})).json()
                assert j["meta"]["created"] and j["meta"]["source"] and j["body"].startswith("회의에서"), j
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "confirm"})
                assert r.status_code == 403, "Origin 없으면 거절"
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "confirm"}, headers={"origin": "https://evil.example"})
                assert r.status_code == 403
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "bogus"}, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit", "text": "입력 담당을 먼저 정한다"}, headers=origin)
                assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["edited_by_user"] is True, r.text
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "confirm"}, headers=origin)
                assert r.status_code == 200 and r.json()["status"] == "confirmed", r.text
                assert [x["action"] for x in db.candidate_history(uid, cid)] == ["edit", "confirm"]
                # edit_confirm: 고치기와 남기기를 한 트랜잭션으로 (이력 두 줄)
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit_confirm", "text": "입력 담당을 맨 먼저 정한다"}, headers=origin)
                assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["edited_by_user"] is True, r.text
                assert r.json()["text"] == "입력 담당을 맨 먼저 정한다", r.text
                hist = db.candidate_history(uid, cid)
                assert [x["action"] for x in hist][-2:] == ["edit", "confirm"], hist
                assert hist[-2]["prev_status"] == "confirmed" and hist[-1]["prev_status"] == "pending", hist
                st = (await h.get("/api/state")).json()
                assert [x["status"] for x in st["candidates"] if x["id"] == cid] == ["confirmed"], st["candidates"]
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit_confirm", "text": "  "}, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", r.text
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit_confirm", "text": "입력 담당을 맨 먼저 정한다"}, headers=origin)
                assert r.status_code == 200 and r.json()["status"] == "confirmed" and len(db.candidate_history(uid, cid)) == len(hist), r.text
                async with Client(main.mcp) as mc:   # MCP 도구는 edit_confirm 을 모른다(Literal 고정)
                    bad = await mc.call_tool("candidate_decide", {"candidate_id": cid, "action": "edit_confirm", "text": "MCP 로 고침"})
                assert bad.is_error and "validation error" in bad.content[0].text and len(db.candidate_history(uid, cid)) == len(hist), bad
                r = await h.post("/api/candidate/decide", json={"candidate_id": "c_none", "action": "confirm"}, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
                # PR①: expected_revision(선택) — 틀리면 CONFLICT, 정수 아니면 ARG-INVALID, 웹 편집은 wording_by=self
                cur = db.candidate_get(uid, cid)
                assert [x["revision"] for x in st["candidates"] if x["id"] == cid] == [cur["revision"]], st["candidates"]
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "exclude",
                                                               "expected_revision": cur["revision"] - 1}, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "CONFLICT", r.text
                assert db.candidate_get(uid, cid) == cur, "CONFLICT 는 아무것도 안 바꾼다"
                for bad_rev in ("1", True, 1.5):
                    r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "exclude",
                                                                   "expected_revision": bad_rev}, headers=origin)
                    assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad_rev, r.text)
                r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit", "text": "웹에서 고친 문장",
                                                               "expected_revision": cur["revision"]}, headers=origin)
                j = r.json()
                assert r.status_code == 200 and j["revision"] == cur["revision"] + 1 and j["wording_by"] == "self", r.text
                assert j["wording_approved"] is False and j["status"] == "pending", j
                assert (await h.post("/logout")).status_code == 403
                r = await h.post("/logout", headers=origin)
                assert r.status_code == 200 and 'canon_session=""' in r.headers.get("set-cookie", ""), r.headers
                assert r.json() == {"ok": True, "redirect": "/?out=1"}, "로그아웃은 시작 화면으로"
                assert (await h.get("/api/state")).status_code == 401, "로그아웃 뒤 401"
                # 토큰에 sid 가 있어도 세션에 담지 않고, 로그아웃 주소도 같다
                tok_sid["sid"] = "app_consent_test"
                await login_once(h)
                assert (await h.get("/api/state")).status_code == 200
                assert "sid" not in site.unsign(h.cookies.get("canon_session")), "세션에 sid 를 담지 않는다"
                r = await h.post("/logout", headers=origin)
                assert r.status_code == 200 and 'canon_session=""' in r.headers.get("set-cookie", ""), r.headers
                assert r.json() == {"ok": True, "redirect": "/?out=1"}, r.json()
                assert (await h.get("/api/state")).status_code == 401, "로그아웃 뒤 401"
                r = await h.get("/")
                assert r.status_code == 200 and "로그인" in r.text, "로그아웃 뒤 시작 화면"
                # 서명 위조 쿠키
                h.cookies.set("canon_session", site.sign({"uid": uid}, 60)[:-3] + "xxx")
                assert (await h.get("/api/state")).status_code == 401, "서명 깨진 쿠키"
                # 반사 XSS: 쿼리·토큰 서버 응답·예외 문구는 escape 해서 글자로만 보인다
                xss = "<script>alert(1)</script>"
                r = await h.get("/auth/callback", params={"error": "x", "error_description": xss})
                assert r.status_code == 400 and "<script>alert(1)" not in r.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text, r.text
                r = await h.get("/auth/callback", params={"error": xss})
                assert r.status_code == 400 and "<script>alert(1)" not in r.text and "&lt;script&gt;" in r.text, r.text

                async def bad_token_post(data):
                    return 400, {"error_description": '<img src=x onerror="alert(1)">'}
                site.token_post = bad_token_post
                r = await h.get("/login")
                st = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
                r = await h.get("/auth/callback", params={"code": "c3", "state": st})
                assert r.status_code == 502 and "<img" not in r.text and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in r.text, r.text
                site.token_post = fake_token_post

                def bad_link(_verified):
                    raise RuntimeError("<svg onload=alert(1)>")
                real_uif, auth.user_id_for = auth.user_id_for, bad_link
                try:
                    r = await h.get("/login")
                    st = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
                    r = await h.get("/auth/callback", params={"code": "c4", "state": st})
                finally:
                    auth.user_id_for = real_uif
                assert r.status_code == 502 and "<svg" not in r.text and "&lt;svg onload=alert(1)&gt;" in r.text, r.text
                # 보안 헤더: 웹 HTML 응답에만. JSON API·/health 는 그대로
                sec = {"x-content-type-options": "nosniff", "x-frame-options": "DENY",
                       "content-security-policy": "frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
                       "referrer-policy": "same-origin"}
                h.cookies.clear()   # 위에서 넣은 위조 세션 쿠키를 지우고 새로 로그인
                pages = [await h.get("/"), await h.get("/help"), await h.get("/privacy"), await h.get("/auth/callback", params={"error": "x"})]
                await login_once(h)
                pages.append(await h.get("/app"))
                for r in pages:
                    assert r.headers.get("content-type", "").startswith("text/html"), (r.url, r.headers)
                    for k, v in sec.items():
                        assert r.headers.get(k) == v, (r.url, k, r.headers.get(k))
                    assert "script-src" not in r.headers["content-security-policy"], "script-src 는 nonce 작업 뒤에"
                for r in (await h.get("/api/state"), await h.get("/health")):
                    assert r.headers.get("content-type", "").startswith("application/json"), (r.url, r.headers)
                    assert not any(k in r.headers for k in sec), (r.url, r.headers)
                assert (await h.post("/logout", headers=origin)).status_code == 200
    finally:
        auth.httpx.post, site.token_post = real_post, real_tp
        site.dev, site.dev_user = True, "tester@example.com"
    print("OK — 웹 로그인·API 통과")


# ── 5) 정의 층 (2단계) ─────────────────────────────────

def expect(code, fn, *a, **k):
    try:
        fn(*a, **k)
    except db.CanonError as e:
        assert e.code == code, (code, e.code, e.message)
        return e
    raise AssertionError(f"{code} 가 나와야 하는데 통과했다: {fn.__name__}")


def test_definitions():
    A = db.user_by_email("def1@example.com")["id"]
    B = db.user_by_email("def2@example.com")["id"]
    # 재료: 후보 3개(A), 다른 사용자 후보 1개(B)
    qa = db.question_add(A, "정의 층 시험 질문")
    ra = db.record_add(A, "정의 기록", "첫째 근거 문장이다. 둘째 근거 문장이다. 셋째 근거 문장이다.", qa["id"])
    (c1, c2, c3), _ = db.candidates_propose(A, ra["id"], qa["id"], [
        {"text": "하나", "quote": "첫째 근거 문장이다"}, {"text": "둘", "quote": "둘째 근거 문장이다"},
        {"text": "셋", "quote": "셋째 근거 문장이다"}], "ai")
    rb = db.record_add(B, "남의 기록", "남의 근거 문장이다.")
    (cb,), _ = db.candidates_propose(B, rb["id"], "", [{"text": "남", "quote": "남의 근거 문장이다"}], "ai")

    # 정의: 제안→적용(0→1), 기준 정의엔 evidence_claims 자동, 기본 승격 정책
    topics = db.definition_propose(A, None, 0, {"key": "topics", "label": "주제", "semantic_kind": "collection",
                                                "fields": [{"key": "keywords", "label": "검색어", "value_type": "text_list"}]})
    assert topics["base_revision"] == 0 and db.definitions(A) == [], "적용 전엔 definitions 에 없음"
    t = db.definition_apply(A, topics["object_id"], topics["proposal_id"], 0)
    assert t["revision"] == 1 and [f["key"] for f in t["fields"]] == ["keywords"], t
    hap = db.definition_propose(A, None, 0, {"key": "happenings", "label": "일", "semantic_kind": "event", "fields": [
        {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "multiple": True},
        {"key": "main_claim", "label": "대표 판단", "value_type": "reference", "target_kind": "candidate", "multiple": False}]})
    hap = db.definition_apply(A, hap["object_id"], hap["proposal_id"], 0)
    rdef = {"key": "rules", "label": "규칙", "semantic_kind": "principle", "fields": [
        {"key": "register", "label": "레지스터", "value_type": "select", "required": True, "options": [
            {"value": "customer", "label": "고객 업무"}, {"value": "work", "label": "일하는 방식"}]},
        {"key": "events", "label": "근거 사건", "value_type": "reference", "target_kind": "item", "target_object_id": hap["id"], "multiple": True}]}
    rp = db.definition_propose(A, None, 0, rdef)
    assert "evidence_claims" in [f["key"] for f in rp["definition"]["fields"]], rp["definition"]["fields"]
    assert rp["definition"]["policy"]["promotion"]["confirmed_claims_min"] == 2
    rules = db.definition_apply(A, rp["object_id"], rp["proposal_id"], 0)
    R = rules["id"]
    assert rules["revision"] == 1 and len(db.definition_history(A, R)) == 1
    # expected_revision 틀림 → CONFLICT, 낡은 제안 ID → CONFLICT
    expect("CONFLICT", db.definition_propose, A, R, 0, rdef)
    p1 = db.definition_propose(A, R, 1, {**rdef, "label": "규칙1"})
    p2 = db.definition_propose(A, R, 1, {**rdef, "label": "규칙2"})
    expect("CONFLICT", db.definition_apply, A, R, p1["proposal_id"], 1)
    rules = db.definition_apply(A, R, p2["proposal_id"], 1)
    assert rules["label"] == "규칙2" and rules["revision"] == 2 and len(db.definition_history(A, R)) == 2
    # 잘못된 reference 대상 → DEF-INVALID, 다른 사용자의 오브젝트도
    expect("DEF-INVALID", db.definition_propose, A, None, 0, {"key": "bad", "label": "나쁨", "semantic_kind": "collection",
        "fields": [{"key": "x", "value_type": "reference", "target_kind": "item", "target_object_id": "od_nope"}]})
    expect("DEF-INVALID", db.definition_propose, A, None, 0, {"key": "bad", "label": "나쁨", "semantic_kind": "collection",
        "fields": [{"key": "x", "value_type": "number"}, {"key": "x", "value_type": "text"}]})
    expect("DEF-INVALID", db.definition_propose, B, None, 0, {"key": "bad", "label": "나쁨", "semantic_kind": "collection",
        "fields": [{"key": "x", "value_type": "reference", "target_kind": "item", "target_object_id": R}]})

    # 항목: 신규 제안(revision 0, 일반 조회 제외) → apply → confirm → pending → restore
    ev = db.item_propose(A, hap["id"], hap["revision"], None, 0, "D-1", "사건 하나", "", {}, [
        {"field_key": "claims", "target_kind": "candidate", "target_id": c1}])
    assert not db.items(A) and [x["id"] for x in db.item_proposals(A)] == [ev["item_id"]]
    e1 = db.item_decide(A, ev["item_id"], 0, ev["proposal_id"], "apply")
    assert e1["revision"] == 1 and e1["code"] == "D-1" and e1["decision"] is None and len(e1["links"]) == 1, e1
    expect("ARG-INVALID", db.item_decide, A, e1["id"], 1, None, "confirm")   # 기준이 아니면 confirm 없음
    expect("LINK-INVALID", db.item_propose, A, hap["id"], 1, None, 0, None, "둘 연결", "", {}, [   # multiple=false 에 2개
        {"field_key": "main_claim", "target_kind": "candidate", "target_id": c1},
        {"field_key": "main_claim", "target_kind": "candidate", "target_id": c2}])
    expect("LINK-INVALID", db.item_propose, A, R, 2, None, 0, None, "남의 후보", "", {"register": "work"}, [
        {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": cb}])
    expect("LINK-INVALID", db.item_propose, A, R, 2, None, 0, None, "틀린 대상 오브젝트", "", {"register": "work"}, [
        {"field_key": "events", "target_kind": "item", "target_id": "i_nope"}])
    expect("ARG-INVALID", db.item_propose, A, R, 2, None, 0, None, "필수 빠짐", "", {}, [])
    expect("ARG-INVALID", db.item_propose, A, R, 2, None, 0, None, "옵션 밖", "", {"register": "nope"}, [])
    expect("CONFLICT", db.item_propose, A, R, 1, None, 0, None, "낡은 정의", "", {"register": "work"}, [])
    pr = db.item_propose(A, R, 2, None, 0, "R-1", "근거 둘이면 기준", "장면…", {"register": "customer"}, [
        {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c1},
        {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c2},
        {"field_key": "events", "target_kind": "item", "target_id": e1["id"]}])
    it = db.item_decide(A, pr["item_id"], 0, pr["proposal_id"], "apply")
    I = it["id"]
    assert it["revision"] == 1 and it["decision"] == "pending" and it["promotion"]["met"] is False, it
    expect("CONFLICT", db.item_decide, A, I, 0, None, "confirm")
    it = db.item_decide(A, I, 1, None, "confirm")
    assert it["revision"] == 2 and it["decision"] == "confirmed", it
    assert it["promotion"] == {"met": False, "confirmed_claims": 0, "confirmed_claims_min": 2, "linked_claims": 2,
                               "distinct_events": 0, "distinct_events_min": 0}, it["promotion"]
    assert db.item_decide(A, I, 2, None, "confirm")["revision"] == 2, "같은 결정 반복은 no-op"
    it = db.item_decide(A, I, 2, None, "pending")
    assert it["revision"] == 3 and it["decision"] == "pending"
    hist = db.item_get(A, I)["history"]
    assert [h["action"] for h in hist] == ["apply", "confirm", "pending"] and [h["prev_revision"] for h in hist] == [0, 1, 2], hist
    it = db.item_decide(A, I, 3, None, "restore", hist[2]["id"])   # pending 직전(confirmed) 상태로
    assert it["revision"] == 4 and it["decision"] == "confirmed" and len(it["links"]) == 3, it
    expect("ARG-INVALID", db.item_decide, A, I, 4, None, "restore", hist[0]["id"])   # 처음 적용 전으로는 못 감
    # 코드: 재사용·변경 금지
    expect("ARG-INVALID", db.item_propose, A, R, 2, None, 0, "R-1", "같은 코드", "", {"register": "work"}, [])
    expect("ARG-INVALID", db.item_propose, A, R, 2, I, 4, "R-2", "코드 바꾸기", "", {"register": "work"}, [])
    # 수정 제안 → 적용, 제안 버리기(새 항목이면 행 삭제)
    up = db.item_propose(A, R, 2, I, 4, None, "근거 둘이면 기준(고침)", "장면…", {"register": "customer"}, [
        {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c1},
        {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c2}])
    assert db.item_get(A, I)["proposal"]["title"] == "근거 둘이면 기준(고침)"
    it = db.item_decide(A, I, 4, up["proposal_id"], "apply")
    assert it["revision"] == 5 and it["code"] == "R-1" and len(it["links"]) == 2 and it["proposal"] is None, it
    # 코드 경합: 제안 두 개가 같은 코드 → 먼저 적용한 쪽이 이기고, 늦은 쪽은 사전 검사를 지나도 UNIQUE 에서 CONFLICT
    race1 = db.item_propose(A, R, 2, None, 0, "R-9", "경합 하나", "", {"register": "work"}, [])
    race2 = db.item_propose(A, R, 2, None, 0, "R-9", "경합 둘", "", {"register": "work"}, [])
    db.item_decide(A, race1["item_id"], 0, race1["proposal_id"], "apply")
    real_taken, db._code_taken = db._code_taken, lambda *a: None   # 사전 검사를 건너뛴 동시 적용 흉내
    try:
        e = expect("CONFLICT", db.item_decide, A, race2["item_id"], 0, race2["proposal_id"], "apply")
        assert "같은 코드" in e.message, e.message
    finally:
        db._code_taken = real_taken
    assert db.item_get(A, race2["item_id"])["revision"] == 0 and db.item_get(A, race2["item_id"])["history"] == [], "롤백돼 이력 없음"
    db.item_decide(A, race2["item_id"], 0, race2["proposal_id"], "discard")
    db.item_decide(A, race1["item_id"], 1, None, "archive")
    junk = db.item_propose(A, R, 2, None, 0, None, "버릴 제안", "", {"register": "work"}, [])
    expect("CONFLICT", db.item_decide, A, junk["item_id"], 0, None, "discard")
    assert db.item_decide(A, junk["item_id"], 0, junk["proposal_id"], "discard")["deleted"] is True
    expect("ARG-INVALID", db.item_get, A, junk["item_id"])
    up2 = db.item_propose(A, R, 2, I, 5, None, "버릴 수정 제안", "", {"register": "work"}, [])
    it = db.item_decide(A, I, 5, up2["proposal_id"], "discard")   # 적용된 항목: draft 만 비움, revision·이력 그대로
    assert it["proposal"] is None and it["revision"] == 5 and it["title"] == "근거 둘이면 기준(고침)" and len(db.item_get(A, I)["history"]) == 5
    # 승격: 근거 후보 2개 확정 → met, 하나 제외 → 미충족(기준 확정은 유지)
    db.candidate_decide(A, c1, "confirm"); db.candidate_decide(A, c2, "confirm")
    assert db.promotion_status(A, I)["met"] is True
    db.candidate_decide(A, c2, "exclude")
    st = db.item_get(A, I)
    assert st["promotion"]["met"] is False and st["promotion"]["confirmed_claims"] == 1 and st["decision"] == "confirmed", st["promotion"]
    # 깨지는 정의 변경 거절: 필수 필드 추가, 타입 변경, 옵션 축소
    expect("DEF-INVALID", db.definition_propose, A, R, 2, {**rdef, "fields": rdef["fields"] + [{"key": "scene", "value_type": "text", "required": True}]})
    expect("DEF-INVALID", db.definition_propose, A, R, 2, {**rdef, "fields": [{"key": "register", "value_type": "number", "required": True}] + rdef["fields"][1:]})
    expect("DEF-INVALID", db.definition_propose, A, R, 2, {**rdef, "fields": [{"key": "register", "value_type": "select", "required": True,
                                                                                "options": ["work"]}] + rdef["fields"][1:]})
    expect("DEF-INVALID", db.definition_propose, A, R, 2, {**rdef, "semantic_kind": "collection"})
    expect("DEF-INVALID", db.definition_propose, A, R, 2, {**rdef, "fields": rdef["fields"] + [   # 연결 남은 reference → text
        {"key": "evidence_claims", "label": "근거", "value_type": "text"}]})
    expect("DEF-INVALID", db.definition_propose, A, R, 2, {**rdef, "key": "rules2"})
    # 필드 제거는 보관(archived_at), 항목은 그대로
    rp3 = db.definition_propose(A, R, 2, {**rdef, "fields": rdef["fields"][:1] + [{"key": "evidence_claims", "label": "근거",
        "value_type": "reference", "target_kind": "candidate", "multiple": True}]})
    r3 = db.definition_apply(A, R, rp3["proposal_id"], 2)
    assert "events" not in [f["key"] for f in r3["fields"]] and "events" in [f["key"] for f in db.definitions(A, [R], True)[0]["fields"]]
    # archive → 일반 조회 제외 → restore 로 복귀
    it = db.item_decide(A, I, 5, None, "archive")
    assert it["revision"] == 6 and I not in [x["id"] for x in db.items(A)]
    h_arch = db.item_get(A, I)["history"][-1]
    it = db.item_decide(A, I, 6, None, "restore", h_arch["id"])
    assert it["archived_at"] is None and it["revision"] == 7 and I in [x["id"] for x in db.items(A)]

    # record_attach: attach_to·axes 저장, notes, 다른 사용자 item 거절
    tp = db.item_propose(A, t["id"], 1, None, 0, "H", "관심 축 H", "", {"keywords": ["판단"]}, [])
    topic = db.item_decide(A, tp["item_id"], 0, tp["proposal_id"], "apply")
    out = db.record_attach(A, ra["id"], [{"field_key": "attach_to", "target_kind": "item", "target_id": I},
                                         {"field_key": "attach_to", "target_kind": "candidate", "target_id": c3}],
                           [topic["id"]], "다음 글 재료", "새 축 후보: 위임")
    assert out["notes"] == {"use_at": "다음 글 재료", "unattached": "새 축 후보: 위임"} and len(out["links"]) == 3, out
    out = db.record_attach(A, ra["id"], use_at="고친 자리")   # None 은 그대로
    assert out["notes"]["unattached"] == "새 축 후보: 위임" and out["notes"]["use_at"] == "고친 자리" and len(out["links"]) == 3
    expect("LINK-INVALID", db.record_attach, B, rb["id"], [{"field_key": "attach_to", "target_kind": "item", "target_id": I}])
    expect("LINK-INVALID", db.record_attach, A, ra["id"], None, [e1["id"]])   # 사건은 축이 아님
    expect("LINK-INVALID", db.record_attach, A, ra["id"], [{"field_key": "attach_to", "target_kind": "item", "target_id": topic["id"]}])
    expect("RECORD-MISSING", db.record_attach, B, ra["id"], use_at="x")
    st = db.state(A)
    assert {"definitions", "items", "item_proposals", "record_links"} <= set(st), st.keys()
    assert len(st["record_links"]) == 3 and [j["notes"] for j in st["journal"] if j["id"] == ra["id"]][0]["use_at"] == "고친 자리"
    assert {d["key"] for d in st["definitions"]} == {"topics", "happenings", "rules"}
    pr_items = [x for x in st["items"] if x["semantic_kind"] == "principle"]
    assert len(pr_items) == 1 and "promotion" in pr_items[0]
    # 다른 사용자에겐 아무것도 안 보인다
    sb = db.state(B)
    assert sb["definitions"] == [] and sb["items"] == [] and sb["record_links"] == []
    expect("ARG-INVALID", db.item_get, B, I)
    expect("ARG-INVALID", db.item_decide, B, I, 7, None, "archive")

    # question_id "" → NULL
    cand_b = db.candidate_get(B, cb)
    assert cand_b["question_id"] is None and [x["id"] for x in sb["candidates"]] == [cb], sb["candidates"]

    # 원작자 템플릿 시드(시험 사용자에게만) — 같은 코드 경로, 멱등
    import template_author
    got = template_author.seed(A, log=lambda *_: None)
    assert [g[1] for g in got] == ["applied"] * 6, got
    again = template_author.seed(A, log=lambda *_: None)
    assert [g[1] for g in again] == ["skipped"] * 6, again
    defs = {d["key"]: d for d in db.definitions(A)}
    assert defs["principles"]["label"] == "기준" and defs["principles"]["policy"]["promotion"]["distinct_events_min"] == 2
    assert defs["open_questions"]["label"] == "숙제" and defs["craft"]["label"] == "접근 방식" and defs["decision_events"]["label"] == "결정"
    # 원작자 정책: 사건 2개(각 사건의 판단이 이 기준 근거에도 있어야)
    db.candidate_decide(A, c2, "confirm")
    evs = []
    for code, cid in (("D-01", c1), ("D-02", c2)):
        p = db.item_propose(A, "od_events", 1, None, 0, code, f"사건 {code}", "", {"happened_on": "2026-10-01"},
                            [{"field_key": "claims", "target_kind": "candidate", "target_id": cid}])
        evs.append(db.item_decide(A, p["item_id"], 0, p["proposal_id"], "apply")["id"])
    links = [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c1},
             {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c2},
             {"field_key": "evidence_events", "target_kind": "item", "target_id": evs[0]}]
    p = db.item_propose(A, "od_principles", 1, None, 0, "C-01", "기준 하나", "", {"register": "work", "maturity": "seed"}, links)
    C = db.item_decide(A, p["item_id"], 0, p["proposal_id"], "apply")
    assert C["promotion"]["confirmed_claims"] == 2 and C["promotion"]["distinct_events"] == 1 and C["promotion"]["met"] is False, C["promotion"]
    p = db.item_propose(A, "od_principles", 1, C["id"], 1, None, "기준 하나", "", {"register": "work", "maturity": "seed"},
                        links + [{"field_key": "evidence_events", "target_kind": "item", "target_id": evs[1]}])
    C = db.item_decide(A, C["id"], 1, p["proposal_id"], "apply")
    assert C["promotion"]["met"] is True and C["promotion"]["distinct_events"] == 2, C["promotion"]
    # 숙제: 대상 셋 중 정확히 하나
    expect("LINK-INVALID", db.item_propose, A, "od_open_questions", 1, None, 0, None, "대상 없음", "",
           {"registered_on": "2026-10-18", "state": "open"}, [])
    q1 = db.item_propose(A, "od_open_questions", 1, None, 0, None, "이 기준은 고객사 밖에서도 맞나", "",
                         {"registered_on": "2026-10-18", "state": "open"},
                         [{"field_key": "subject_principle", "target_kind": "item", "target_id": C["id"]}])
    db.item_decide(A, q1["item_id"], 0, q1["proposal_id"], "apply")
    inc = db.item_get(A, C["id"])["incoming"]
    assert [x["object_label"] for x in inc] == ["숙제"], inc
    print("OK — 정의 층 db 시험 통과")
    return A


async def test_definition_tools():
    os.environ["CANON_DEV_USER"] = "def2@example.com"
    async with Client(main.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        new = {"definitions_get", "definition_propose", "definition_apply", "item_propose", "item_decide", "record_attach"}
        base = {"state_get", "question_add", "question_list", "record_add", "record_get", "candidates_propose", "candidate_decide",
                "candidate_list", "profile_set"}
        assert base | new | {"help_get", "feedback_send"} == tools and len(tools) == 17, tools   # 기존 15개 그대로 + help_get + feedback_send(desk-0)
        assert val(await c.call_tool("definitions_get", {"object_ids": None, "include_archived": False})) == []
        p = val(await c.call_tool("definition_propose", {"object_id": None, "expected_revision": 0, "definition_json": json.dumps(
            {"key": "notes", "label": "메모", "semantic_kind": "principle", "fields": []})}))
        d = val(await c.call_tool("definition_apply", {"object_id": p["object_id"], "proposal_id": p["proposal_id"], "expected_revision": 0}))
        assert d["revision"] == 1 and [f["key"] for f in d["fields"]] == ["evidence_claims"], d
        bad = await c.call_tool("definition_propose", {"object_id": None, "expected_revision": 0, "definition_json": "[1]"})
        assert bad.is_error and "ARG-INVALID" in bad.content[0].text, bad
        B = db.user_by_email("def2@example.com")["id"]
        cb = db.candidates(B)[0]["id"]
        ip = val(await c.call_tool("item_propose", {"object_id": d["id"], "schema_revision": 1, "item_id": None, "expected_revision": 0,
            "code": None, "title": "MCP 로 낸 기준", "body": "", "data_json": "{}",
            "links": [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": cb}]}))
        it = val(await c.call_tool("item_decide", {"item_id": ip["item_id"], "expected_revision": 0, "proposal_id": ip["proposal_id"],
                                                   "action": "apply", "history_id": None}))
        assert it["revision"] == 1 and it["links"][0]["target_id"] == cb, it
        conf = await c.call_tool("item_decide", {"item_id": it["id"], "expected_revision": 0, "proposal_id": None, "action": "confirm", "history_id": None})
        assert conf.is_error and "CONFLICT" in conf.content[0].text, conf
        it = val(await c.call_tool("item_decide", {"item_id": it["id"], "expected_revision": 1, "proposal_id": None, "action": "confirm", "history_id": None}))
        assert it["decision"] == "confirmed" and it["promotion"]["met"] is False, it
        rid = db.candidates(B)[0]["record_id"]
        ra = val(await c.call_tool("record_attach", {"record_id": rid, "attach_to": [
            {"field_key": "attach_to", "target_kind": "item", "target_id": it["id"]}], "unattached": "축 후보"}))
        assert ra["notes"] == {"unattached": "축 후보"} and len(ra["links"]) == 1, ra
        # question_id "" 경로 (MCP)
        r = val(await c.call_tool("record_add", {"text": "질문 없이 남긴 기록이다. 판단 문장이 있다.", "title": "질문 없음"}))
        out = val(await c.call_tool("candidates_propose", {"record_id": r["id"], "question_id": "",
                                                            "candidates": [{"text": "판단", "quote": "판단 문장이 있다"}]}))
        assert out["added"][0]["question_id"] is None, out
        st = val(await c.call_tool("state_get", {}))
        nq = [x for x in st["candidates"] if x["question_id"] is None and x["status"] == "pending"]
        assert {x["record_id"] for x in nq} >= {r["id"]}, "기록별 묶음 데이터: 질문 없는 미결정 후보 + record_id"
    os.environ["CANON_DEV_USER"] = "tester@example.com"
    print("OK — 정의 층 MCP 도구 통과")


async def test_web_items(A):
    """/api/item·/api/item/decide — DEV 사용자(def1)로. Origin 거절·CONFLICT 400·성공 200·없는 항목 404."""
    site = main.site
    site.dev, site.dev_user = True, "def1@example.com"
    origin = {"origin": PUBLIC}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            st = (await h.get("/api/state")).json()
            assert {"definitions", "items", "item_proposals", "record_links"} <= set(st)
            C = [x for x in st["items"] if x["object_id"] == "od_principles"][0]
            assert (await h.get("/api/item", params={"id": "i_none"})).status_code == 404
            r = await h.get("/api/item", params={"id": C["id"]})
            it = r.json()
            assert r.status_code == 200 and it["object"]["label"] == "기준" and it["promotion"]["met"] is True and it["history"], it
            assert any(t["target_kind"] == "candidate" and t["target"]["quote"] for t in it["targets"]), it["targets"]
            body = {"item_id": C["id"], "expected_revision": C["revision"], "action": "confirm"}
            assert (await h.post("/api/item/decide", json=body)).status_code == 403, "Origin 없으면 거절"
            assert (await h.post("/api/item/decide", json=body, headers={"origin": "https://evil.example"})).status_code == 403
            r = await h.post("/api/item/decide", json={**body, "expected_revision": C["revision"] - 1}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "CONFLICT", r.text
            r = await h.post("/api/item/decide", json={**body, "expected_revision": "1"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", r.text
            r = await h.post("/api/item/decide", json={**body, "action": "bogus"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", r.text
            r = await h.post("/api/item/decide", json=body, headers=origin)
            assert r.status_code == 200 and r.json()["decision"] == "confirmed" and r.json()["revision"] == C["revision"] + 1, r.text
            r = await h.post("/api/item/decide", json={**body, "expected_revision": C["revision"] + 1, "action": "pending"}, headers=origin)
            assert r.status_code == 200 and r.json()["decision"] == "pending", r.text
            # 웹에서 제안 적용·제외
            p = db.item_propose(A, "od_principles", 1, C["id"], C["revision"] + 2, None, "웹에서 고친 기준", "본문", {"register": "work"}, [])
            r = await h.post("/api/item/decide", json={"item_id": C["id"], "expected_revision": C["revision"] + 2, "action": "apply",
                                                       "proposal_id": p["proposal_id"]}, headers=origin)
            assert r.status_code == 200 and r.json()["title"] == "웹에서 고친 기준", r.text
            n = db.item_propose(A, "od_axes", 1, None, 0, None, "웹에서 버릴 축", "", {}, [])
            assert n["item_id"] in [x["id"] for x in (await h.get("/api/state")).json()["item_proposals"]]
            r = await h.post("/api/item/decide", json={"item_id": n["item_id"], "expected_revision": 0, "action": "discard",
                                                       "proposal_id": n["proposal_id"]}, headers=origin)
            assert r.status_code == 200 and r.json()["deleted"] is True, r.text
            site.dev_user = "def2@example.com"   # 다른 사용자는 못 본다
            assert (await h.get("/api/item", params={"id": C["id"]})).status_code == 404
    finally:
        site.dev, site.dev_user = True, "tester@example.com"
    print("OK — 정의 층 웹 API 통과")


# ── 5-1) 템플릿 계보 (template_slot) ───────────────────

def _slots(uid, c=None):
    """사용자 한 명의 오브젝트·필드 슬롯 {id: slot}, {(object_id, key): slot}. c 를 주면 그 연결에서."""
    def read(c):
        objs = {r["id"]: r["template_slot"] for r in c.execute(
            "SELECT id,template_slot FROM object_defs WHERE user_id=%s", (uid,)).fetchall()}
        flds = {(r["object_id"], r["key"]): r["template_slot"] for r in c.execute(
            "SELECT object_id,key,template_slot FROM field_defs WHERE user_id=%s", (uid,)).fetchall()}
        return objs, flds
    if c is not None:
        return read(c)
    with db.tx() as c:
        return read(c)


def test_template_slot(A):
    """시드만 슬롯을 단다 · 사용자 정의는 같은 key 여도 NULL · 사용자 적용이 슬롯을 못 지운다 · 옛 DB 열 추가·백필 멱등 · 동결 파일 = TEMPLATE."""
    import inspect
    import template_author as ta
    # 동결 파일 = 지금 TEMPLATE (바뀌면 TEMPLATE_VERSION 을 올리고 새 판 파일을 만든다)
    frozen = json.loads(ta.FROZEN_PATH.read_text(encoding="utf-8"))
    assert (ta.TEMPLATE_KEY, ta.TEMPLATE_VERSION, ta.TEMPLATE_NAME, db.BACKFILL_TEMPLATE) == ("author", 1, "author@1", "author@1")
    assert frozen == ta.frozen_doc() == db.template_frozen(db.BACKFILL_TEMPLATE), "TEMPLATE 이 동결 author@1 과 다르다"
    want_obj = {d["id"]: f"author@1:{d['key']}" for d in frozen["objects"]}
    want_fld = {(d["id"], f["key"]): f"author@1:{d['key']}.{f['key']}" for d in frozen["objects"] for f in d["fields"]}
    assert (len(want_obj), len(want_fld)) == (6, 25), (len(want_obj), len(want_fld))

    # (1) 시드(test_definitions 가 A 에 적용) → 템플릿 6/25 만 채워지고 A 의 다른 정의는 NULL
    objs, flds = _slots(A)
    assert {k: v for k, v in objs.items() if v} == want_obj, objs
    assert {k: v for k, v in flds.items() if v} == want_fld, flds
    assert objs["od_principles"] == "author@1:principles" and flds[("od_principles", "register")] == "author@1:principles.register"
    assert len(objs) > 6 and len(flds) > 25, "A 에는 템플릿 밖 정의도 있다(시험 재료)"

    # (2) 시드 전용: apply 의 ON CONFLICT … DO UPDATE SET·UPDATE 에 template_slot 없음, MCP·웹 경로는 표시 함수를 안 부른다
    assert "template_slot" not in inspect.getsource(db.definition_apply), "definition_apply 가 template_slot 을 건드린다"
    for f in ("main.py", "web.py"):
        assert "template_slot" not in (HERE / f).read_text(encoding="utf-8"), f"{f} 가 template_slot 을 건드린다"

    # (3) 사용자 적용이 기존 슬롯을 지우지 않는다 — 라벨 변경 + 템플릿 필드 빼기(archived) → 다시 넣기(ON CONFLICT 경로)
    rev = {d["id"]: d for d in db.definitions(A)}["od_axes"]["revision"]
    base = {"key": "axes", "semantic_kind": "collection", "policy": {}, "template_slot": "author@1:hack"}   # 정의에 넣어도 무시
    note = {"key": "note", "label": "메모", "value_type": "text"}
    p = db.definition_propose(A, "od_axes", rev, {**base, "label": "관심축", "fields": [note]})
    db.definition_apply(A, "od_axes", p["proposal_id"], rev)
    objs, flds = _slots(A)
    assert objs["od_axes"] == "author@1:axes" and flds[("od_axes", "keywords")] == "author@1:axes.keywords", (objs, flds)
    assert flds[("od_axes", "note")] is None, "사용자가 더한 필드는 NULL"
    p = db.definition_propose(A, "od_axes", rev + 1, {**base, "label": "축",
                                                      "fields": [{"key": "keywords", "label": "검색어", "value_type": "text_list"}, note]})
    db.definition_apply(A, "od_axes", p["proposal_id"], rev + 1)
    objs, flds = _slots(A)
    assert objs["od_axes"] == "author@1:axes" and flds[("od_axes", "keywords")] == "author@1:axes.keywords", (objs, flds)
    assert flds[("od_axes", "note")] is None and {k: v for k, v in objs.items() if v} == want_obj

    # (4) 다른 사용자가 propose/apply 로 템플릿과 같은 key(id 는 자동) · 같은 id(key 는 다름)를 만들어도 NULL
    B = db.user_by_email("def2@example.com")["id"]
    p = db.definition_propose(B, None, 0, {"key": "principles", "label": "기준", "semantic_kind": "principle", "fields": [],
                                           "template_slot": "author@1:principles"})
    db.definition_apply(B, p["object_id"], p["proposal_id"], 0)
    p = db.definition_propose(B, None, 0, {"id": "od_axes", "key": "my_axes", "label": "축", "semantic_kind": "collection",
                                           "fields": [{"key": "keywords", "label": "검색어", "value_type": "text_list"}]})
    db.definition_apply(B, "od_axes", p["proposal_id"], 0)
    before_b = _slots(B)
    assert not any(before_b[0].values()) and not any(before_b[1].values()), before_b
    before_a = _slots(A)
    db.init_schema()   # 백필이 사용자 정의(id·key 쌍 불일치)를 잡지 않고, 이미 찬 슬롯은 그대로
    assert _slots(B) == before_b and _slots(A) == before_a

    # (5) 옛 DB(열 없음)에 시드된 원작자 → init_schema 가 열을 더하고 6/25 를 백필, 다시 돌리면 0/0. 임시 스키마에서
    name = f"canon_tslot_{secrets.token_hex(3)}"
    create_schema(name)
    try:
        with schema_conn(name) as c:
            db.init_schema(c)
            c.commit()
        with pool_on(name):
            u1 = db.user_by_email("tslot1@example.com")["id"]
            u2 = db.user_by_email("tslot2@example.com")["id"]
            assert [g[1] for g in ta.seed(u1, log=lambda *_: None)] == ["applied"] * 6
            p = db.definition_propose(u2, None, 0, {"key": "craft", "label": "방식", "semantic_kind": "collection",
                                                    "fields": [{"key": "project", "label": "프로젝트", "value_type": "text"}]})
            db.definition_apply(u2, p["object_id"], p["proposal_id"], 0)
            seeded = (_slots(u1), _slots(u2))
        assert seeded[0] == (want_obj, want_fld), seeded[0]
        with schema_conn(name) as c:
            c.execute("ALTER TABLE field_defs DROP COLUMN template_slot")
            c.execute("ALTER TABLE object_defs DROP COLUMN template_slot")
            c.commit()
            db.init_schema(c)
            c.commit()
            assert (_slots(u1, c), _slots(u2, c)) == seeded, "백필 ≠ 시드 표시"
            assert not any(seeded[1][0].values()) and not any(seeded[1][1].values()), "같은 key·다른 id 사용자 정의는 NULL"
            with c.transaction():
                assert db._backfill_template_slots(c) == (0, 0), "두 번째 백필이 뭔가 바꿨다"
            c.execute("UPDATE object_defs SET template_slot=NULL")
            c.execute("UPDATE field_defs SET template_slot=NULL")
            with c.transaction():
                assert db._backfill_template_slots(c) == (6, 25), "백필 대상 = 원작자 1명 6오브젝트/25필드"
            c.rollback()
    finally:
        drop_schema(name)
    print("OK — 템플릿 계보(template_slot: 시드만 표시·사용자 적용 보존·백필 6/25 멱등·동결 파일) 통과")


# ── 6) PR① 코어 보수: 출처·revision·이력 격리 ──────────────

# PR① 전 운영 스키마(2단계 끝 모양) — 마이그레이션 시험용으로 박아 둔다. 런타임에 git 에서 읽지 않는다(CI 의 HEAD 는 새 schema.sql).
OLD_DDL = """
CREATE TABLE users (
  id text PRIMARY KEY, email text NOT NULL UNIQUE, auth_subject text UNIQUE, created timestamptz NOT NULL DEFAULT now());
CREATE TABLE questions (
  id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, text text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created timestamptz NOT NULL DEFAULT now(), updated timestamptz, UNIQUE (user_id, id));
CREATE TABLE records (
  id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, question_id text,
  title text NOT NULL, body text NOT NULL, source text NOT NULL DEFAULT '대화에서 입력',
  created timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz, notes jsonb NOT NULL DEFAULT '{}',
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE SET NULL (question_id));
CREATE INDEX ix_records_user ON records (user_id, created DESC) WHERE deleted_at IS NULL;
CREATE TABLE candidates (
  id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, record_id text NOT NULL,
  question_id text, text text NOT NULL, quote text NOT NULL, quote_start integer NOT NULL, quote_end integer NOT NULL,
  origin text NOT NULL CHECK (origin IN ('ai','self')),
  decision text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','confirmed','excluded')),
  edited_by_user boolean NOT NULL DEFAULT false,
  created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, id), UNIQUE (user_id, record_id, quote_start, quote_end),
  FOREIGN KEY (user_id, record_id) REFERENCES records(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE RESTRICT);
CREATE INDEX ix_candidates_user_q ON candidates (user_id, question_id, decision);
CREATE TABLE candidate_history (
  id bigserial PRIMARY KEY, candidate_id text NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  action text NOT NULL CHECK (action IN ('confirm','exclude','pending','edit')),
  prev_decision text NOT NULL, prev_text text NOT NULL, at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ix_hist_cand ON candidate_history (candidate_id, id);
"""
CORE_TABLES = ("records", "candidates", "candidate_history")


def _seed_old(c, bad_quote=False):
    """옛 모양에 운영과 같은 꼴의 행: 사용자 2·기록 2·후보 2·이력(u1 3줄, u2 1줄)."""
    body1 = "회의에서 입력 담당을 먼저 정했다. 비교는 나중이었다."
    c.execute("INSERT INTO users (id,email) VALUES ('u_m1','m1@example.com'), ('u_m2','m2@example.com')")
    c.execute("INSERT INTO records (id,user_id,title,body) VALUES ('r_m1','u_m1','옛 기록',%s), ('r_m2','u_m2','남의 기록','다른 사람 기록.')",
              (body1,))
    q = "입력 담당을 먼저 정했다"
    s = body1.find(q)
    c.execute("INSERT INTO candidates (id,user_id,record_id,text,quote,quote_start,quote_end,origin,decision,edited_by_user) "
              "VALUES ('c_m1','u_m1','r_m1','새 문장',%s,%s,%s,'ai','pending',true), "
              "('c_m2','u_m2','r_m2','남의 문장','다른 사람',0,5,'ai','confirmed',false)",
              (q, s, s + len(q) + (1 if bad_quote else 0)))
    c.execute("INSERT INTO candidate_history (candidate_id,action,prev_decision,prev_text) VALUES "
              "('c_m1','edit','pending','옛 문장'), ('c_m1','confirm','pending','새 문장'), ('c_m1','pending','confirmed','새 문장'), "
              "('c_m2','confirm','pending','남의 문장')")
    c.commit()


def _shape(c):
    """세 테이블의 컬럼·제약·인덱스(스키마 이름 뺀 정의). 순서는 안 본다."""
    cols = {(r["table_name"], r["column_name"], r["data_type"], r["is_nullable"], r["column_default"]) for r in c.execute(
        "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name = ANY(%s)", (list(CORE_TABLES),)).fetchall()}
    cons = {(r["t"], r["conname"], r["d"]) for r in c.execute(
        "SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
        "WHERE connamespace = current_schema()::regnamespace AND conrelid::regclass::text = ANY(%s)", (list(CORE_TABLES),)).fetchall()}
    idx = {(r["tablename"], r["indexname"], r["indexdef"].replace(f"{r['schemaname']}.", "")) for r in c.execute(
        "SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename = ANY(%s)",
        (list(CORE_TABLES),)).fetchall()}
    return cols, cons, idx


def _data(c):
    return {t: c.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in CORE_TABLES}


class pool_on:
    """db 함수를 잠깐 다른 임시 스키마에 붙인다(운영 마이그레이션 뒤 첫 결정이 사는지 실제 코드로 본다)."""
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        name = self.name

        def cfg(conn):
            conn.execute(f"SET search_path TO {name}")
            conn.commit()
        self.saved = db._pool
        db._pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=2, kwargs={"row_factory": dict_row},
                                  configure=cfg, open=True)

    def __exit__(self, *exc):
        db._pool.close()
        db._pool = self.saved


def test_migration():
    """옛 모양(prev_decision/prev_text 이력, records 에 출처 없음) → init_schema → 최종형·백필·멱등.
    운영 테이블은 안 건드린다 — 시험 전용 임시 스키마 3개(옛·새·실패)를 만들고 끝에 지운다."""
    tag = secrets.token_hex(3)
    old, fresh, bad = f"canon_mig_old_{tag}", f"canon_mig_new_{tag}", f"canon_mig_bad_{tag}"
    try:
        for n in (old, fresh, bad):
            create_schema(n)
        # (1) 새 설치: schema.sql 만으로 최종형
        with schema_conn(fresh) as c:
            db.init_schema(c)
            new_shape = _shape(c)
        # (2) 옛 모양 → 마이그레이션
        with schema_conn(old) as c:
            c.execute(OLD_DDL)
            c.commit()
            _seed_old(c)
            before = _data(c)
            c.commit()   # 운영처럼 유휴 연결에서 시작(init_schema 트랜잭션이 최상위가 되게)
            db.init_schema(c)
            c.commit()
            got = _shape(c)
            assert got == new_shape, ("마이그레이션 결과 ≠ 새 설치", got[0] ^ new_shape[0], got[1] ^ new_shape[1], got[2] ^ new_shape[2])
            cols = {t: [r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                                            "WHERE table_schema=current_schema() AND table_name=%s", (t,)).fetchall()]
                    for t in CORE_TABLES}
            assert "prev_text" not in cols["candidate_history"] and "prev_decision" not in cols["candidate_history"], cols
            after = _data(c)
            # 기존 행 수 그대로, 출처는 unknown(추측 백필 없음), 후보는 revision 1·문장 주체 미상
            assert {t: len(v) for t, v in after.items()} == {t: len(v) for t, v in before.items()} == \
                {"records": 2, "candidates": 2, "candidate_history": 4}
            for a, b in zip(after["records"], before["records"]):
                assert a["body"] == b["body"] and a["author_kind"] == "unknown" and a["text_process"] == "unknown", a
                assert a["provenance_history"] == [], a
            for a, b in zip(after["candidates"], before["candidates"]):
                assert (a["text"], a["decision"], a["quote_start"], a["quote_end"]) == (b["text"], b["decision"], b["quote_start"], b["quote_end"])
                assert a["revision"] == 1 and a["wording_by"] is None and a["wording_approved"] is False, a
            owner = {"c_m1": "u_m1", "c_m2": "u_m2"}
            for a, b in zip(after["candidate_history"], before["candidate_history"]):
                assert a["id"] == b["id"] and a["user_id"] == owner[a["candidate_id"]] and a["prev_revision"] is None, a
                assert a["prev_state"] == {"schema_version": 1, "text": b["prev_text"], "decision": b["prev_decision"]}, a
            # (3) 멱등: 두 번째는 아무것도 안 바꾼다
            c.commit()
            db.init_schema(c)
            c.commit()
            assert _shape(c) == got and _data(c) == after, "두 번째 init_schema 가 뭔가 바꿨다"
            c.commit()
            # (4) 새 쓰기 제약: 다른 사용자 소유로 이력을 못 단다(복합 FK), action 은 restore 까지
            try:
                c.execute("INSERT INTO candidate_history (user_id,candidate_id,action,prev_revision,prev_state) "
                          "VALUES ('u_m2','c_m1','confirm',1,'{\"schema_version\":2,\"text\":\"x\",\"decision\":\"pending\"}')")
                raise AssertionError("다른 사용자 소유 이력이 들어갔다")
            except psycopg.errors.ForeignKeyViolation:
                c.rollback()
            try:
                c.execute("INSERT INTO candidate_history (user_id,candidate_id,action,prev_revision,prev_state) "
                          "VALUES ('u_m1','c_m1','bogus',1,'{\"schema_version\":2,\"text\":\"x\",\"decision\":\"pending\"}')")
                raise AssertionError("모르는 action 이 들어갔다")
            except psycopg.errors.CheckViolation:
                c.rollback()
        # (5) 운영 꼴 행이 머지 뒤 첫 결정·이력·복원·근거 검사에서 사는지 — 실제 db 함수로
        with pool_on(old):
            d = db.candidate_decide("u_m1", "c_m1", "confirm")
            assert d["status"] == "confirmed" and d["revision"] == 2 and d["wording_by"] is None, d
            hist = db.candidate_history("u_m1", "c_m1")
            assert [h["prev_revision"] for h in hist] == [None, None, None, 1], hist
            assert hist[0]["prev_text"] == "옛 문장" and hist[0]["prev_status"] == "pending", hist[0]
            assert hist[-1]["prev_state"]["schema_version"] == 2 and hist[-1]["prev_status"] == "pending", hist[-1]
            expect("CONFLICT", db.candidate_decide, "u_m1", "c_m1", "exclude", None, 1)
            r = db.candidate_restore("u_m1", "c_m1", hist[0]["id"])   # v1 부분 이력 → 문장·결정만, 문장 주체 NULL
            assert (r["text"], r["status"], r["revision"], r["wording_by"], r["wording_approved"]) == ("옛 문장", "pending", 3, None, False), r
            assert db.candidate_history("u_m1", "c_m1")[-1]["action"] == "restore"
            expect("ARG-INVALID", db.candidate_history, "u_m2", "c_m1")
            expect("ARG-INVALID", db.candidate_restore, "u_m2", "c_m1", hist[0]["id"])
            added, dropped = db.candidates_propose("u_m1", "r_m1", "", [{"text": "비교는 나중", "quote": "비교는 나중이었다"}], "ai")
            assert not added and dropped[0]["why"] == db.INELIGIBLE_WHY, dropped   # 옛 기록 = unknown → 근거 불가
        with schema_conn(old) as c:
            db.init_schema(c)   # 새 쓰기 뒤에도 멱등
            c.commit()
            assert _shape(c) == got
        # (6) 실패는 전체 롤백: 인용 범위가 깨진 후보가 있으면 중단, 어느 테이블도 안 바뀐다
        with schema_conn(bad) as c:
            c.execute(OLD_DDL)
            c.commit()
            _seed_old(c, bad_quote=True)
            before_bad = (_shape(c), _data(c))
            c.commit()
            e = expect("DB-MIGRATE", db.init_schema, c)
            assert "인용 범위" in e.message, e.message
            c.rollback()
            assert (_shape(c), _data(c)) == before_bad, "실패한 마이그레이션이 일부를 남겼다"
    finally:
        for n in (old, fresh, bad):
            drop_schema(n)
    print("OK — PR① 마이그레이션(옛 모양→최종형·백필·멱등·실패 롤백) 통과")


def test_core_pr1():
    """인용 적격·출처 정정·revision·이력 호환·복원·승격 근거 집계."""
    A = db.user_by_email("core1@example.com")["id"]
    B = db.user_by_email("core2@example.com")["id"]

    def prov(rid):
        with db.tx() as c:
            return db._record_in(c, A, rid)

    # 출처 기본값 = self·verbatim(MCP record_add), 생성 1줄
    ok = db.record_add(A, "본인 원문", "입력 담당을 먼저 정했다. 비교표는 나중에 만들었다.")
    assert (ok["author_kind"], ok["text_process"]) == ("self", "verbatim"), ok
    ph = prov(ok["id"])["provenance_history"]
    assert len(ph) == 1 and ph[0]["by"] == "tool" and ph[0]["author_kind"] == "self" and ph[0]["at"], ph
    expect("ARG-INVALID", db.record_add, A, "", "본문", None, "x", "nobody", "verbatim")
    # 비적격 기록(unknown·external·rewritten)엔 후보가 하나도 안 붙는다
    un = db.record_add(A, "출처 미상", "남이 정리한 문장이다. 둘째 문장이다.", None, "가져옴", "unknown", "unknown")
    ext = db.record_add(A, "외부", "남이 정리한 문장이다.", None, "가져옴", "external", "verbatim")
    rw = db.record_add(A, "다듬은 글", "남이 정리한 문장이다.", None, "가져옴", "self", "rewritten")
    for rec in (un, ext, rw):
        added, dropped = db.candidates_propose(A, rec["id"], "", [{"text": "가", "quote": "남이 정리한 문장이다"},
                                                                  {"text": "나", "quote": "원문에 없는 말"}], "ai")
        assert not added and [d["why"] for d in dropped] == [db.INELIGIBLE_WHY] * 2, (rec["title"], dropped)
    # 후보 없는 기록: unknown → self·verbatim 정정 OK, 이력 2줄(전후·사유)
    expect("ARG-INVALID", db.record_provenance_set, A, un["id"], "self", "verbatim", "  ")
    expect("ARG-INVALID", db.record_provenance_set, A, un["id"], "self", "bogus", "사유")
    expect("PAYLOAD-LIMIT", db.record_provenance_set, A, un["id"], "self", "verbatim", "가" * (db.LIMIT_TEXT // 3 + 1))   # 사유 1KB(UTF-8)
    expect("RECORD-MISSING", db.record_provenance_set, B, un["id"], "self", "verbatim", "남의 기록")
    fixed = db.record_provenance_set(A, un["id"], "self", "verbatim", "원문을 다시 확인함")
    assert (fixed["author_kind"], fixed["text_process"]) == ("self", "verbatim") and fixed["body"] == un["body"], fixed
    ph = fixed["provenance_history"]
    assert len(ph) == 2 and ph[1]["from"] == {"author_kind": "unknown", "text_process": "unknown"}, ph
    assert ph[1]["reason"] == "원문을 다시 확인함" and ph[1]["by"] == "user" and ph[1]["at"], ph
    assert len(db.record_provenance_set(A, un["id"], "self", "verbatim", "같은 값")["provenance_history"]) == 2, "같은 값은 no-op"
    added, _ = db.candidates_propose(A, un["id"], "", [{"text": "정리한 문장", "quote": "남이 정리한 문장이다"}], "self")
    assert len(added) == 1 and db.candidate_get(A, added[0])["wording_by"] is None, "origin=self 는 문장 주체 미상"
    # 후보 있는 기록의 적격 → 비적격 정정은 거절, 아무것도 안 바뀐다
    for ak, tp in (("self", "structured"), ("external", "verbatim"), ("unknown", "unknown")):
        expect("PROVENANCE-LOCKED", db.record_provenance_set, A, un["id"], ak, tp, "잘못 붙였나")
    assert len(prov(un["id"])["provenance_history"]) == 2 and prov(un["id"])["author_kind"] == "self"
    # 후보 없는 적격 기록은 비적격으로 바꿀 수 있다
    assert db.record_provenance_set(A, rw["id"], "self", "verbatim", "원문이었음")["text_process"] == "verbatim"
    assert db.record_provenance_set(A, rw["id"], "external", "verbatim", "남의 글이었음")["author_kind"] == "external"

    # revision: 결정마다 +1, 이력에 변경 전 스냅샷(v2)·prev_revision, 같은 결정 반복은 no-op
    (cid,), _ = db.candidates_propose(A, ok["id"], "", [{"text": "담당부터 정한다", "quote": "입력 담당을 먼저 정했다"}], "ai")
    c0 = db.candidate_get(A, cid)
    assert (c0["revision"], c0["wording_by"], c0["wording_approved"]) == (1, "ai", False), c0
    c1 = db.candidate_decide(A, cid, "confirm")
    assert c1["revision"] == 2 and c1["status"] == "confirmed"
    h = db.candidate_history(A, cid)
    assert len(h) == 1 and h[0]["prev_revision"] == 1 and h[0]["action"] == "confirm", h
    assert h[0]["prev_state"] == {"schema_version": 2, "text": "담당부터 정한다", "decision": "pending", "wording_by": "ai",
                                  "wording_approved": False, "edited_by_user": False, "question_id": None}, h[0]
    assert h[0]["prev_text"] == "담당부터 정한다" and h[0]["prev_status"] == "pending", "옛 키 호환"
    assert db.candidate_decide(A, cid, "confirm")["revision"] == 2 and len(db.candidate_history(A, cid)) == 1, "no-op"
    assert db.candidate_decide(A, cid, "confirm", None, 2)["revision"] == 2, "맞는 expected_revision + 같은 결정 = no-op"
    expect("CONFLICT", db.candidate_decide, A, cid, "exclude", None, 1)
    expect("CONFLICT", db.candidate_decide, A, cid, "edit", "다른 문장", 3)
    assert db.candidate_get(A, cid) == db.candidate_decide(A, cid, "confirm"), "CONFLICT 는 아무것도 안 바꿨다"
    assert db.candidate_decide(A, cid, "exclude", None, 2)["revision"] == 3
    # 문장 수정: 승인 해제·pending·문장 주체(인자, MCP 는 NULL)
    with db.tx() as c:
        c.execute("UPDATE candidates SET wording_approved=true WHERE user_id=%s AND id=%s", (A, cid))   # 승인 경로는 다음 PR — 직접 세팅
    e1 = db.candidate_decide(A, cid, "edit", "담당을 맨 먼저 정한다")
    assert (e1["revision"], e1["status"], e1["wording_by"], e1["wording_approved"], e1["edited_by_user"]) == \
        (4, "pending", None, False, True), e1
    assert db.candidate_history(A, cid)[-1]["prev_state"]["wording_approved"] is True, "승인 상태도 스냅샷에"
    e2 = db.candidate_decide(A, cid, "edit_confirm", "담당을 가장 먼저 정한다", 4, "self")
    assert (e2["revision"], e2["status"], e2["wording_by"], e2["wording_approved"]) == (6, "confirmed", "self", False), e2
    h = db.candidate_history(A, cid)
    assert [x["action"] for x in h][-2:] == ["edit", "confirm"] and [x["prev_revision"] for x in h] == [1, 2, 3, 4, 5], h
    expect("ARG-INVALID", db.candidate_decide, A, cid, "edit", "x", None, "robot")
    # 복원(v2): 지금 상태를 새 이력으로 남기고 revision+1, 승인은 넘기지 않는다
    target = next(x for x in h if x["prev_revision"] == 3)   # 첫 문장 고치기 전: 「담당부터 정한다」·excluded·ai·승인됨
    r = db.candidate_restore(A, cid, target["id"], 6)
    assert (r["text"], r["status"], r["revision"], r["wording_by"], r["wording_approved"]) == \
        ("담당부터 정한다", "excluded", 7, "ai", False), r
    last = db.candidate_history(A, cid)[-1]
    assert last["action"] == "restore" and last["prev_revision"] == 6 and last["prev_state"]["text"] == "담당을 가장 먼저 정한다", last
    expect("CONFLICT", db.candidate_restore, A, cid, target["id"], 6)
    expect("ARG-INVALID", db.candidate_restore, A, cid, 999999999)
    expect("ARG-INVALID", db.candidate_restore, A, added[0], target["id"])   # 다른 후보의 이력
    expect("ARG-INVALID", db.candidate_restore, B, cid, target["id"])       # 다른 사용자
    expect("ARG-INVALID", db.candidate_decide, B, cid, "confirm")
    expect("ARG-INVALID", db.candidate_history, B, cid)
    # 상태·목록 응답에 revision·문장 주체·승인
    st = db.state(A)
    row = next(x for x in st["candidates"] if x["id"] == cid)
    assert (row["revision"], row["wording_by"], row["wording_approved"]) == (7, "ai", False), row
    assert all({"author_kind", "text_process"} <= set(j) for j in st["journal"]), st["journal"][0]
    # 승격 근거 집계: 출처 미확인 기록의 확정 후보는 세지 않는다(옛 데이터 꼴 — 서버 경로로는 못 만들어 SQL 로 넣는다)
    legacy = db.record_add(A, "옛 기록", "예전에 남긴 판단 문장이다.", None, "옛 데이터", "unknown", "unknown")
    with db.tx() as c:
        c.execute("INSERT INTO candidates (id,user_id,record_id,text,quote,quote_start,quote_end,origin,decision) "
                  "VALUES ('c_legacy_pr1',%s,%s,'옛 판단',%s,0,%s,'ai','confirmed')",
                  (A, legacy["id"], "예전에 남긴 판단 문장이다", len("예전에 남긴 판단 문장이다")))
    db.candidate_decide(A, cid, "confirm")
    links = [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": x} for x in (cid, "c_legacy_pr1")]
    with db.tx() as c:
        p = db._promotion(c, A, {}, links)
    assert p["linked_claims"] == 2 and p["confirmed_claims"] == 1, p
    assert any(x["id"] == "c_legacy_pr1" for x in db.candidates(A)), "목록에서는 안 빠진다"
    print("OK — PR① 인용 적격·출처 정정·revision·이력·복원 통과")


# ── 7) 전역 상한 (α1) ──────────────────────────────────

LIM = db.MAX_BODY_BYTES


def _mcp_body(total: int, name="state_get", arguments=None) -> bytes:
    """정확히 total 바이트인 tools/call 본문 — JSON 끝 공백으로 맞춘다."""
    base = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments or {}}}).encode()
    assert len(base) <= total, (len(base), total)
    return base + b" " * (total - len(base))


async def _chunked(b: bytes, n: int = 5):
    """Content-Length 없이(chunked) 보낸다."""
    step = len(b) // n + 1
    for i in range(0, len(b), step):
        yield b[i:i + step]


async def limits_http(h, hdr):
    """① 요청 본문 상한 — 실제 /mcp 왕복(토큰 인증) · /api/*. test_http 의 lifespan 안에서 부른다."""
    for chunked in (False, True):
        b = _mcp_body(LIM)
        r = await h.post("/mcp", headers=hdr, content=_chunked(b) if chunked else b)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json"), (chunked, r.status_code, r.text[:200])
        assert not r.json()["result"].get("isError"), r.text[:300]
        b = _mcp_body(LIM + 1)
        r = await h.post("/mcp", headers=hdr, content=_chunked(b) if chunked else b)
        assert r.status_code == 413, (chunked, r.status_code)
        e = r.json()
        assert e["jsonrpc"] == "2.0" and e["id"] is None and e["error"]["code"] == -32600 and "[PAYLOAD-LIMIT]" in e["error"]["message"], e
    # 인증 전에 막힌다(큰 본문은 토큰 검사까지 가지 않는다)
    r = await h.post("/mcp", headers={k: v for k, v in hdr.items() if k != "authorization"}, content=_mcp_body(LIM + 1))
    assert r.status_code == 413, r.status_code
    for body in (b"x" * (LIM + 1), _chunked(b"x" * (LIM + 1))):
        r = await h.post("/api/candidate/decide", content=body, headers={"content-type": "application/json", "origin": PUBLIC})
        assert r.status_code == 413 and r.json()["code"] == "PAYLOAD-LIMIT", (r.status_code, r.text[:200])
    # 상한 밖 경로는 판정하지 않는다
    assert (await h.post("/logout", content=b"x" * (LIM + 1), headers={"origin": PUBLIC})).status_code != 413
    print("OK — 요청 본문 상한(256KB) 통과")


async def test_limits_sse():
    """① 미들웨어 뒤에서 MCP SSE 응답(json_response=False)이 깨지지 않는지 — 운영 앱은 json_response=True 라 따로 띄운다."""
    from mcp.server import MCPServer
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.routing import Mount
    probe = MCPServer("sse-probe")

    @probe.tool()
    def echo(text: str) -> str:
        return text[:10]

    sse_app = Starlette(routes=[Mount("/", app=probe.streamable_http_app(stateless_http=True, json_response=False))],
                        middleware=[Middleware(main.BodyLimit)])
    hdr = {"content-type": "application/json", "accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
    async with probe.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sse_app), base_url="http://127.0.0.1:8800") as h:
            for chunked in (False, True):
                b = _mcp_body(LIM, "echo", {"text": "hello-sse"})
                async with h.stream("POST", "/mcp", headers=hdr, content=_chunked(b) if chunked else b) as r:
                    raw = b"".join([c async for c in r.aiter_raw()]).decode()
                assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), (r.status_code, r.headers)
                ev = [ln for ln in raw.splitlines() if ln.startswith("data: ")]
                assert "event: message" in raw and len(ev) == 1, raw
                assert json.loads(ev[0][6:])["result"]["structuredContent"] == {"result": "hello-sse"}, raw
            r = await h.post("/mcp", headers=hdr, content=_mcp_body(LIM + 1, "echo", {"text": "x"}))
            assert r.status_code == 413, r.status_code
    print("OK — 미들웨어 뒤 MCP SSE 응답 통과")


async def test_limits():
    """② 인자 길이 · ③ 전역 DB 크기 — MCP guard 와 웹 쓰기 API 가 같은 db.write_gate 를 지나는지."""
    site = main.site
    os.environ["CANON_DEV_USER"] = "tester@example.com"
    site.dev, site.dev_user = True, "tester@example.com"
    uid = db.user_by_email("tester@example.com")["id"]
    origin = {"origin": PUBLIC}
    real_measure, real_admins = db._measure_db_size, db.ADMIN_USER_IDS
    # 도구 분류: 쓰기 표에 없는 도구는 읽기 — 새 쓰기 도구가 게이트를 비켜 가지 않게 고정
    async with Client(main.mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert set(db.WRITE_LIMITS) <= names, set(db.WRITE_LIMITS) - names
    assert names - set(db.WRITE_LIMITS) == {"state_get", "question_list", "candidate_list", "record_get", "definitions_get", "help_get"}, names
    assert len(names) == 17 and db.QUOTA_EXEMPT == {"feedback_send"} <= set(db.WRITE_LIMITS), (len(names), db.QUOTA_EXEMPT)

    def nrec():
        return len(db.records(uid, limit=1000))

    try:
        db.quota_cache_reset()
        n = db.db_size_cached()
        assert isinstance(n, int) and 0 < n < db.DB_QUOTA_BYTES, n   # 실제 pg_database_size 가 돈다
        async with Client(main.mcp) as c, httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # ② 기록 본문: 정확히 64KB(다바이트 포함) 통과 · +1 거절, 행 안 생김
            body = "가" * (db.LIMIT_BODY // 3) + "a" * (db.LIMIT_BODY % 3)
            assert len(body.encode()) == db.LIMIT_BODY
            r = await c.call_tool("record_add", {"text": body, "title": "64KB 경계"})
            assert not r.is_error, r.content[0].text[:200]
            rid = val(r)["id"]
            before = nrec()
            r = await c.call_tool("record_add", {"text": body + "a", "title": "64KB+1"})
            msg = r.content[0].text
            assert r.is_error and "[PAYLOAD-LIMIT]" in msg and "text" in msg and str(db.LIMIT_BODY) in msg, msg[:300]
            assert nrec() == before, "거절된 기록은 저장되지 않는다"
            for tool, args, field in (
                ("record_add", {"text": "본문", "title": "t" * (db.LIMIT_TEXT + 1)}, "title"),
                ("question_add", {"text": "질" * 342}, "text"),   # 3바이트 × 342 = 1026
                ("profile_set", {"key": "purpose", "value": "v" * (db.LIMIT_PROFILE_VALUE + 1), "status": "guessed"}, "value"),
                ("definition_propose", {"object_id": None, "expected_revision": 0, "definition_json": " " * (db.LIMIT_JSON + 1)}, "definition_json"),
                ("candidates_propose", {"record_id": rid, "question_id": "", "candidates": [{"text": "c" * (db.LIMIT_TEXT + 1), "quote": "가가"}]},
                 "candidates[].text"),
                ("record_add", {"text": "본문", "source": "s" * (db.LIMIT_TEXT + 1)}, "source"),
            ):
                r = await c.call_tool(tool, args)
                assert r.is_error and "[PAYLOAD-LIMIT]" in r.content[0].text and field in r.content[0].text, (tool, r.content[0].text[:200])
            # ② 웹 쓰기 API: 후보 문장 1KB 경계
            qq = db.question_add(uid, "α1 시험 질문")
            rec = db.record_add(uid, "α1", "회의 전에 안건을 먼저 돌렸다.", qq["id"])
            cid = db.candidates_propose(uid, rec["id"], qq["id"], [{"text": "안건 먼저", "quote": "안건을 먼저 돌렸다"}], "ai")[0][0]
            r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit", "text": "x" * (db.LIMIT_TEXT + 1)}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT" and "text" in r.json()["msg"], r.text
            r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "edit", "text": "x" * db.LIMIT_TEXT}, headers=origin)
            assert r.status_code == 200, r.text
            r = await h.post("/api/item/decide", json={"item_id": "i" * (db.LIMIT_TEXT + 1), "expected_revision": 0, "action": "confirm"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT", r.text
            r = await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "edit", "text": "y" * (db.LIMIT_TEXT + 1)})
            assert r.is_error and "[PAYLOAD-LIMIT]" in r.content[0].text, r.content[0].text

            # ③ 전역 DB 크기: 401MB 주입 → 비관리자 쓰기 거절, 읽기 정상
            db._measure_db_size = lambda: 401 * db.MB
            db.quota_cache_reset()
            before = nrec()
            r = await c.call_tool("record_add", {"text": "꽉 찬 때 기록"})
            assert r.is_error and "[QUOTA-GLOBAL]" in r.content[0].text, r.content[0].text
            r = await c.call_tool("question_add", {"text": "꽉 찬 때 질문"})
            assert r.is_error and "[QUOTA-GLOBAL]" in r.content[0].text, r.content[0].text
            r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "confirm"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "QUOTA-GLOBAL", r.text
            r = await h.post("/api/item/decide", json={"item_id": "i_none", "expected_revision": 0, "action": "confirm"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "QUOTA-GLOBAL", r.text
            assert nrec() == before and db.candidate_get(uid, cid)["status"] == "pending"
            for tool, args in (("state_get", {}), ("record_get", {"record_id": rid}), ("question_list", {}), ("candidate_list", {}),
                               ("definitions_get", {"object_ids": None, "include_archived": False}), ("help_get", {"topic": "words"})):
                r = await c.call_tool(tool, args)
                assert not r.is_error, (tool, r.content[0].text[:200])
            assert (await h.get("/api/state")).status_code == 200
            assert (await h.get("/api/journal", params={"id": rid})).status_code == 200
            # 관리자는 쓴다
            db.ADMIN_USER_IDS = frozenset({uid})
            r = await c.call_tool("record_add", {"text": "관리자 기록"})
            assert not r.is_error, r.content[0].text
            r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "confirm"}, headers=origin)
            assert r.status_code == 200 and r.json()["status"] == "confirmed", r.text
            db.ADMIN_USER_IDS = frozenset({"u_someone_else"})
            r = await c.call_tool("record_add", {"text": "다른 사람만 관리자"})
            assert r.is_error and "[QUOTA-GLOBAL]" in r.content[0].text, r.content[0].text
            # 경계: 정확히 400MB 는 허용
            db._measure_db_size = lambda: db.DB_QUOTA_BYTES
            db.quota_cache_reset()
            assert not (await c.call_tool("record_add", {"text": "400MB 정확히"})).is_error

            # 측정 실패 → 허용(가용성 우선)
            def boom():
                raise RuntimeError("측정 실패 주입")
            db._measure_db_size = boom
            db.quota_cache_reset()
            assert not (await c.call_tool("record_add", {"text": "측정 실패 때 기록"})).is_error
            # 캐시: 만료(60초) 전엔 다시 재지 않는다
            calls = []
            db._measure_db_size = lambda: calls.append(1) or 1
            db.quota_cache_reset()
            for i in range(3):
                assert not (await c.call_tool("question_add", {"text": f"캐시 {i}"})).is_error
            assert calls == [1], calls
    finally:
        db._measure_db_size, db.ADMIN_USER_IDS = real_measure, real_admins
        db.quota_cache_reset()
    print("OK — 인자 길이·전역 DB 크기 상한 통과")


# ── 8) 문의 데스크 (desk-0) ─────────────────────────────

def _kst_today():
    import datetime as dt
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("Asia/Seoul")).date()


def _ticket(tid):
    with db.tx() as c:
        return c.execute("SELECT * FROM tickets WHERE id=%s", (tid,)).fetchone()


def _today_count(uid=None):
    q = f"SELECT count(*) AS n FROM tickets WHERE created >= {db._KST_TODAY}" + (" AND user_id=%s" if uid else "")
    with db.tx() as c:
        return c.execute(q, (uid,) if uid else ()).fetchone()["n"]


async def test_feedback():
    site = main.site
    os.environ["CANON_DEV_USER"] = "tester@example.com"
    site.dev, site.dev_user = True, "tester@example.com"
    uid = db.user_by_email("tester@example.com")["id"]
    other = db.user_by_email("other@example.com")["id"]
    origin = {"origin": PUBLIC}
    chrome = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    real_measure = db._measure_db_size
    privacy_ids = []   # privacy_log 는 FK·cascade 밖 — 시험이 만든 행은 끝에 직접 지운다
    with db.tx() as c:
        c.execute("DELETE FROM tickets WHERE user_id = ANY(%s)", ([uid, other],))

    def post(h, body, hdr=None):
        return h.post("/api/feedback", json=body, headers={**origin, **(hdr or {})})

    try:
        async with Client(main.mcp) as mc, httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # 세션 없음 → 401 (DEV 폴백을 끄고)
            site.dev, site.dev_user = False, ""
            try:
                assert (await h.get("/api/feedback")).status_code == 401
                assert (await post(h, {"kind": "bug", "text": "x"})).status_code == 401
            finally:
                site.dev, site.dev_user = True, "tester@example.com"
            # Origin 없음·다름 → 403
            for hd in ({}, {"origin": "https://evil.example"}):
                r = await h.post("/api/feedback", json={"kind": "bug", "text": "x"}, headers=hd)
                assert r.status_code == 403 and r.json()["code"] == "ORIGIN", (hd, r.text)
            # 본문 틀림 → 400 ARG-INVALID
            for bad in ({"kind": "bug"}, {"kind": 1, "text": "x"}, ["bug", "x"], {"kind": "nope", "text": "x"},
                        {"kind": "bug", "text": "  \n "}, {"kind": "bug", "text": "x", "page": 3}):
                r = await post(h, bad)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad, r.text)
            # 4KB+1 → PAYLOAD-LIMIT (웹·MCP), 행 안 생김
            r = await post(h, {"kind": "bug", "text": "a" * (db.LIMIT_TICKET + 1), "page": "today"})
            assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT" and "text" in r.json()["msg"], r.text
            r = await mc.call_tool("feedback_send", {"kind": "bug", "text": "a" * (db.LIMIT_TICKET + 1)})
            assert r.is_error and "[PAYLOAD-LIMIT]" in r.content[0].text, r.content[0].text[:200]
            r = await mc.call_tool("feedback_send", {"kind": "bug", "text": "a", "tool": "t" * (db.LIMIT_TEXT + 1)})
            assert r.is_error and "[PAYLOAD-LIMIT]" in r.content[0].text and "tool" in r.content[0].text, r.content[0].text[:200]
            r = await mc.call_tool("feedback_send", {"kind": "praise", "text": "a"})
            assert r.is_error and "validation error" in r.content[0].text, r.content[0].text[:200]
            assert _today_count(uid) == 0
            # 웹 접수: page·ua 계열·hint, 응답에 고지
            r = await post(h, {"kind": "bug", "text": "기록을 삭제하고 싶은데 버튼이 없어요", "page": "today"}, {"user-agent": chrome})
            j = r.json()
            assert r.status_code == 200 and set(j) == {"id", "kind", "status", "created", "notice"} and j["status"] == "open", j
            assert j["id"].startswith("tk_") and j["notice"] == db.NOTICE, j
            t1 = _ticket(j["id"])
            assert (t1["via"], t1["page"], t1["ua_family"], t1["hint"], t1["tool"], t1["user_id"]) == ("web", "today", "chrome", "privacy?", "", uid), t1
            assert t1["closed_at"] is None and t1["admin_note"] == "", t1
            # page 열거형 밖 → other · 표식 없는 글은 hint ''
            r = await post(h, {"kind": "request", "text": "검색이 있으면 좋겠어요", "page": "<script>"})
            t2 = _ticket(r.json()["id"])
            assert r.status_code == 200 and t2["page"] == "other" and t2["hint"] == "" and t2["ua_family"] == "other", t2   # httpx 기본 UA
            # privacy → hint 없음 + privacy_log 1행(받은 날 = KST 오늘, via=form)
            r = await post(h, {"kind": "privacy", "text": "내 계정을 삭제해 주세요", "page": "help"})
            pid = r.json()["id"]
            privacy_ids.append(pid)
            assert r.status_code == 200 and _ticket(pid)["hint"] == "" and _ticket(pid)["kind"] == "privacy", r.text
            with db.tx() as c:
                pl = c.execute("SELECT * FROM privacy_log WHERE ticket_id=%s", (pid,)).fetchall()
            assert len(pl) == 1 and pl[0]["id"].startswith("pl_"), pl
            assert (pl[0]["kind"], pl[0]["via"], pl[0]["received"]) == ("other", "form", _kst_today()), pl
            assert pl[0]["notified"] is None and pl[0]["result"] is None and pl[0]["identity_checked"] is False, pl
            with db.tx() as c:
                n = c.execute("SELECT count(*) AS n FROM privacy_log WHERE ticket_id = ANY(%s)", ([j["id"], t2["id"]],)).fetchone()["n"]
            assert n == 0, "privacy 가 아닌 문의는 privacy_log 를 만들지 않는다"
            # MCP: 보낸 글 그대로 되돌려 준다 · via=mcp · tool 은 이 서버의 도구 이름만
            text = "  record_add 가 안 돼요\n줄바꿈·앞뒤 공백 그대로  "
            r = await mc.call_tool("feedback_send", {"kind": "bug", "text": text, "tool": "record_add"})
            v = val(r)
            assert not r.is_error and set(v) == {"id", "kind", "status", "text", "notice"} and v["text"] == text, v
            assert v["kind"] == "bug" and v["status"] == "open" and db.NOTICE in v["notice"] and f"{PUBLIC}/privacy" in v["notice"], v
            t4 = _ticket(v["id"])
            assert (t4["via"], t4["tool"], t4["page"], t4["ua_family"], t4["text"]) == ("mcp", "record_add", "other", "", text), t4
            r = await mc.call_tool("feedback_send", {"kind": "question", "text": "이건 왜 이래요", "tool": "no_such_tool"})
            assert not r.is_error and _ticket(val(r)["id"])["tool"] == "", "모르는 도구 이름은 저장하지 않는다"
            # hint 판정: 다섯 표식(띄어 쓰지 않은 「내정보」 포함) / 없음
            for w in ("삭제", "탈퇴", "지워", "열람", "내 정보", "내정보"):
                assert db._PRIVACY_HINT.search(f"앞 {w} 뒤"), w
            for w in ("기록이 안 보여요", "정보 탭이 비어요", "내보내기"):
                assert not db._PRIVACY_HINT.search(w), w
            # 「내 문의」: 본인 글·상태만. 운영자 메모·hint·남의 글 없음
            db.ticket_add(other, "bug", "다른 사람의 문의 글", "web")
            with db.tx() as c:
                c.execute("UPDATE tickets SET admin_note='운영자만 보는 메모', status='doing' WHERE id=%s", (j["id"],))
            r = await h.get("/api/feedback")
            assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
            ts = r.json()["tickets"]
            assert len(ts) == 5, ts   # 웹 3 + MCP 2
            assert all(set(t) == {"id", "kind", "text", "status", "via", "created", "updated"} for t in ts), ts
            assert "운영자만 보는 메모" not in r.text and "privacy?" not in r.text and "다른 사람의 문의 글" not in r.text, r.text
            assert [t["status"] for t in ts if t["id"] == j["id"]] == ["doing"], ts
            assert ts == sorted(ts, key=lambda t: t["created"], reverse=True), "최근 것부터"
            mine_other = db.tickets_mine(other)
            assert len(mine_other) == 1 and mine_other[0]["text"] == "다른 사람의 문의 글", mine_other
            assert 'id="desk-form"' in (await h.get("/app")).text, "/app 에 문의 절"
            # QUOTA-GLOBAL 면제: 401MB 주입 → 기록은 거절, 문의는 웹·MCP 모두 접수
            db._measure_db_size = lambda: 401 * db.MB
            db.quota_cache_reset()
            r = await mc.call_tool("record_add", {"text": "꽉 찬 때 기록"})
            assert r.is_error and "[QUOTA-GLOBAL]" in r.content[0].text, r.content[0].text
            r = await mc.call_tool("feedback_send", {"kind": "bug", "text": "저장이 안 돼요"})
            assert not r.is_error, r.content[0].text
            r = await post(h, {"kind": "bug", "text": "웹에서도 저장이 안 돼요", "page": "record"})
            assert r.status_code == 200, r.text
            db._measure_db_size = real_measure
            db.quota_cache_reset()
            # 파기: 종결 181일 → 삭제 · 179일 → 유지 · 다시 연 문의(closed_at NULL, 오래됨) → 유지
            a = db.ticket_add(other, "bug", "파기 181", "web")["id"]
            b = db.ticket_add(other, "bug", "파기 179", "web")["id"]
            ro = db.ticket_add(other, "bug", "다시 연 문의", "web")["id"]
            with db.tx() as c:
                c.execute("UPDATE tickets SET status='done', closed_at=now() - interval '181 days' WHERE id=%s", (a,))
                c.execute("UPDATE tickets SET status='wontfix', closed_at=now() - interval '179 days' WHERE id=%s", (b,))
                c.execute("UPDATE tickets SET status='done', closed_at=now() - interval '300 days' WHERE id=%s", (ro,))
                c.execute("UPDATE tickets SET status='open', closed_at=NULL, created=now() - interval '400 days' WHERE id=%s", (ro,))
            assert db.tickets_purge() >= 1
            assert _ticket(a) is None and _ticket(b) is not None and _ticket(ro) is not None, "181 삭제 · 179 유지 · reopen 유지"
            try:
                with db.tx() as c:
                    c.execute("UPDATE tickets SET status='done' WHERE id=%s", (ro,))   # 종결인데 closed_at 없음 → CHECK
                raise AssertionError("closed_at 없는 종결이 저장됐다")
            except psycopg.errors.CheckViolation:
                pass
            # 사용자 하루 10건: 11번째 RATE-LIMIT(웹·MCP). 어제(KST) 것은 세지 않는다
            while _today_count(uid) < db.TICKET_DAILY_USER:
                db.ticket_add(uid, "question", "채우기", "web")
            r = await post(h, {"kind": "bug", "text": "11번째"})
            assert r.status_code == 400 and r.json()["code"] == "RATE-LIMIT" and "10건" in r.json()["msg"], r.text
            r = await mc.call_tool("feedback_send", {"kind": "bug", "text": "11번째 MCP"})
            assert r.is_error and "[RATE-LIMIT]" in r.content[0].text and "10건" in r.content[0].text, r.content[0].text
            assert _today_count(uid) == db.TICKET_DAILY_USER
            with db.tx() as c:
                c.execute(f"UPDATE tickets SET created = {db._KST_TODAY} - interval '1 minute' WHERE id=%s", (t2["id"],))
            r = await post(h, {"kind": "bug", "text": "어제 것은 세지 않아요"})
            assert r.status_code == 200, r.text
            assert (await post(h, {"kind": "bug", "text": "다시 11번째"})).json()["code"] == "RATE-LIMIT"
            # 전역 하루 100건: 시험 사용자 여럿으로 채운 뒤 새 사용자의 첫 문의가 RATE-LIMIT
            helpers = [db.user_by_email(f"desk{i}@example.com")["id"] for i in range(11)]
            for hu in helpers[:-1]:
                while _today_count() < db.TICKET_DAILY_ALL and _today_count(hu) < db.TICKET_DAILY_USER:
                    db.ticket_add(hu, "request", "전역 채우기", "web")
            assert _today_count() == db.TICKET_DAILY_ALL, _today_count()
            assert _today_count(helpers[-1]) == 0
            try:
                db.ticket_add(helpers[-1], "bug", "전역 101번째", "web")
                raise AssertionError("전역 101번째가 저장됐다")
            except db.CanonError as e:
                assert e.code == "RATE-LIMIT" and "많아" in e.message, (e.code, e.message)
            r = await mc.call_tool("feedback_send", {"kind": "bug", "text": "MCP 전역 101번째"})
            assert r.is_error and "[RATE-LIMIT]" in r.content[0].text, r.content[0].text
            assert _today_count() == db.TICKET_DAILY_ALL
    finally:
        db._measure_db_size = real_measure
        db.quota_cache_reset()
        site.dev, site.dev_user = True, "tester@example.com"
        with db.tx() as c:
            c.execute("DELETE FROM privacy_log WHERE ticket_id = ANY(%s)", (privacy_ids,))
    print("OK — 문의 데스크(접수·한도·면제·내 문의·privacy_log·파기) 통과")


async def test_feedback_admin():
    """관리자 문의함: 비관리자 응답엔 admin 키 없음 · 상태/메모 API 403(비관리자)·Origin · 상태 전이와 closed_at · 메모 2KB · 띠 숫자."""
    site = main.site
    os.environ["CANON_DEV_USER"] = "tester@example.com"
    site.dev, site.dev_user = True, "tester@example.com"
    uid = db.user_by_email("tester@example.com")["id"]
    other = db.user_by_email("other@example.com")["id"]
    origin = {"origin": PUBLIC}
    real_admins = db.ADMIN_USER_IDS
    # 하루 한도(test_feedback 가 채움)를 피해 직접 넣는다
    xss = '<img src=x onerror=alert(1)> 관리자만 볼 글'
    ids = {k: db.new_id("tk") for k in ("bug", "privacy")}
    with db.tx() as c:
        c.execute("INSERT INTO tickets (id,user_id,kind,text,via) VALUES (%s,%s,'bug',%s,'web'),(%s,%s,'privacy','내 정보 열람 요청','web')",
                  (ids["bug"], other, xss, ids["privacy"], other))
        c.execute("INSERT INTO privacy_log (id,kind,via,ticket_id,received) VALUES (%s,'other','form',%s,(now() AT TIME ZONE 'Asia/Seoul')::date)",
                  (db.new_id("pl"), ids["privacy"]))

    def plog():
        with db.tx() as c:
            return c.execute("SELECT notified, result FROM privacy_log WHERE ticket_id=%s", (ids["privacy"],)).fetchone()

    def counts():
        with db.tx() as c:
            return dict(c.execute("SELECT count(*) FILTER (WHERE status='open') AS open, count(*) FILTER (WHERE kind='privacy' "
                                  "AND status NOT IN ('done','wontfix')) AS privacy_open FROM tickets").fetchone())

    try:
        async with Client(main.mcp) as mc, httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # 비관리자: 키 자체가 없다 · 자기 글만 · 쓰기 403
            db.ADMIN_USER_IDS = frozenset({"u_someone_else"})
            st = (await h.get("/api/state")).json()
            assert "admin_tickets" not in st and "user" in st, list(st)
            r = await h.get("/api/feedback")
            j = r.json()
            assert r.status_code == 200 and set(j) == {"tickets"}, list(j)
            assert xss not in r.text and "other@example.com" not in r.text, "비관리자 응답에 남의 글·이메일 없음"
            for url, body in (("/api/feedback/status", {"id": ids["bug"], "status": "ack"}),
                              ("/api/feedback/note", {"id": ids["bug"], "admin_note": "x"})):
                r = await h.post(url, json=body, headers=origin)
                assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN", (url, r.text)
            assert _ticket(ids["bug"])["status"] == "open" and _ticket(ids["bug"])["admin_note"] == "", "비관리자 쓰기는 반영 안 됨"
            # 세션 없음 401
            site.dev, site.dev_user = False, ""
            try:
                assert (await h.post("/api/feedback/status", json={"id": ids["bug"], "status": "ack"}, headers=origin)).status_code == 401
            finally:
                site.dev, site.dev_user = True, "tester@example.com"
            # 관리자
            db.ADMIN_USER_IDS = frozenset({uid})
            # Origin 없음·다름 → 403 ORIGIN (관리자여도)
            for hd in ({}, {"origin": "https://evil.example"}):
                for url in ("/api/feedback/status", "/api/feedback/note"):
                    r = await h.post(url, json={"id": ids["bug"], "status": "ack", "admin_note": "x"}, headers=hd)
                    assert r.status_code == 403 and r.json()["code"] == "ORIGIN", (url, hd, r.text)
            st = (await h.get("/api/state")).json()
            assert st["admin_tickets"] == counts() and st["admin_tickets"]["privacy_open"] >= 1, st.get("admin_tickets")
            assert "admin_tickets" not in db.state(uid), "MCP state_get(db.state)에는 없다"
            sv = await mc.call_tool("state_get", {})
            assert "admin_tickets" not in val(sv), "MCP state_get 에 관리자 키 없음"
            r = await h.get("/api/feedback")
            j = r.json()
            assert r.status_code == 200 and j["admin"] is True and j["admin_tickets"] == counts(), list(j)
            assert len(j["tickets"]) <= db.TICKET_ADMIN_LIST and j["tickets"] == sorted(j["tickets"], key=lambda t: t["created"], reverse=True)
            row = {t["id"]: t for t in j["tickets"]}.get(ids["bug"])
            assert row and row["email"] == "other@example.com" and row["text"] == xss and row["admin_note"] == "" and "hint" in row, row
            # 상태 전이: ack(closed_at 없음) → done(closed_at) → wontfix(closed_at 유지) → open(NULL)
            before = _ticket(ids["bug"])
            n0 = counts()["open"]
            r = await h.post("/api/feedback/status", json={"id": ids["bug"], "status": "ack"}, headers=origin)
            t = _ticket(ids["bug"])
            assert r.status_code == 200 and r.json()["status"] == "ack" and t["closed_at"] is None and t["updated"] > before["updated"], r.text
            assert counts()["open"] == n0 - 1 and (await h.get("/api/state")).json()["admin_tickets"]["open"] == n0 - 1, "띠 숫자가 줄어든다"
            r = await h.post("/api/feedback/status", json={"id": ids["bug"], "status": "done"}, headers=origin)
            closed = _ticket(ids["bug"])["closed_at"]
            assert r.status_code == 200 and closed is not None and r.json()["closed_at"], r.text
            r = await h.post("/api/feedback/status", json={"id": ids["bug"], "status": "wontfix"}, headers=origin)
            assert r.status_code == 200 and _ticket(ids["bug"])["closed_at"] == closed, "종결끼리 옮기면 closed_at 유지"
            r = await h.post("/api/feedback/status", json={"id": ids["bug"], "status": "open"}, headers=origin)
            assert r.status_code == 200 and _ticket(ids["bug"])["closed_at"] is None and _ticket(ids["bug"])["status"] == "open", r.text
            n1 = counts()["privacy_open"]
            await h.post("/api/feedback/status", json={"id": ids["privacy"], "status": "doing"}, headers=origin)
            assert counts()["privacy_open"] == n1, "처리 중인 개인정보 요청은 아직 센다"
            assert plog() == {"notified": None, "result": None}, plog()
            await h.post("/api/feedback/status", json={"id": ids["privacy"], "status": "done"}, headers=origin)
            assert counts()["privacy_open"] == n1 - 1, "종결하면 빠진다"
            # privacy_log: 종결 → notified=KST 오늘·result done / wontfix → refused / 다시 열면 result 만 NULL(notified 유지)
            assert plog() == {"notified": _kst_today(), "result": "done"}, plog()
            with db.tx() as c:
                c.execute("UPDATE privacy_log SET notified = notified - 3 WHERE ticket_id=%s", (ids["privacy"],))
            kept = plog()["notified"]
            for st_, want in (("wontfix", "refused"), ("open", None), ("done", "done")):
                await h.post("/api/feedback/status", json={"id": ids["privacy"], "status": st_}, headers=origin)
                assert plog() == {"notified": kept, "result": want}, (st_, plog())
            # 틀린 상태·본문·없는 id
            for body, code, http in (({"id": ids["bug"], "status": "closed"}, "ARG-INVALID", 400),
                                     ({"id": ids["bug"], "status": 1}, "ARG-INVALID", 400),
                                     ({"id": ids["bug"]}, "ARG-INVALID", 400),
                                     ({"id": "tk_nope", "status": "ack"}, "NOT-FOUND", 404)):
                r = await h.post("/api/feedback/status", json=body, headers=origin)
                assert r.status_code == http and r.json()["code"] == code, (body, r.text)
            assert _ticket(ids["bug"])["status"] == "open"
            try:
                with db.tx() as c:
                    c.execute("UPDATE tickets SET status='closed' WHERE id=%s", (ids["bug"],))   # 스키마 CHECK 도 막는다
                raise AssertionError("열거형 밖 상태가 저장됐다")
            except psycopg.errors.CheckViolation:
                pass
            # 메모: 2KB 통과 · +1 PAYLOAD-LIMIT · 사용자 「내 문의」엔 안 보임
            note = "메" * (db.LIMIT_ADMIN_NOTE // 3) + "x" * (db.LIMIT_ADMIN_NOTE % 3)
            assert len(note.encode()) == db.LIMIT_ADMIN_NOTE
            r = await h.post("/api/feedback/note", json={"id": ids["bug"], "admin_note": note}, headers=origin)
            assert r.status_code == 200 and r.json()["admin_note"] == note and _ticket(ids["bug"])["admin_note"] == note, r.text[:200]
            r = await h.post("/api/feedback/note", json={"id": ids["bug"], "admin_note": note + "x"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT", r.text
            r = await h.post("/api/feedback/note", json={"id": "tk_nope", "admin_note": "x"}, headers=origin)
            assert r.status_code == 404 and r.json()["code"] == "NOT-FOUND", r.text
            # 글쓴이(비관리자)는 바뀐 상태만 보고 메모는 못 본다
            await h.post("/api/feedback/status", json={"id": ids["bug"], "status": "doing"}, headers=origin)
            site.dev_user = "other@example.com"
            try:
                r = await h.get("/api/feedback")
                mine = {t["id"]: t for t in r.json()["tickets"]}
                assert "admin" not in r.json() and mine[ids["bug"]]["status"] == "doing" and "admin_note" not in mine[ids["bug"]], r.json()
                assert note not in r.text, "메모는 사용자에게 안 보인다"
            finally:
                site.dev_user = "tester@example.com"
    finally:
        db.ADMIN_USER_IDS = real_admins
        site.dev, site.dev_user = True, "tester@example.com"
        with db.tx() as c:
            c.execute("DELETE FROM privacy_log WHERE ticket_id=%s", (ids["privacy"],))   # FK·cascade 밖
    print("OK — 관리자 문의함(admin 키 분기·403·상태 전이·closed_at·메모 2KB·privacy_log 종결 기록) 통과")


def test_feedback_units():
    """ua 계열 판정 · CANON_CONTACT 선택(비DEV 에서 없어도 기동) · app.html 문의 구간의 esc 정적 검사."""
    import subprocess
    for ua, fam in (("", ""), (None, ""), ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", "edge"),
                    ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", "firefox"),
                    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/130.0 Mobile/15E148 Safari/604.1", "chrome"),
                    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15", "safari"),
                    ("curl/8.7.1", "other")):
        assert web.ua_family(ua) == fam, (ua, fam, web.ua_family(ua))
    # 비DEV 에서 CANON_CONTACT 는 선택 — 없음·공백만·있음 모두 import 성공
    env = {k: v for k, v in os.environ.items() if k not in ("CANON_DEV", "CANON_DEV_USER", "CANON_CONTACT")}
    env["AUTHKIT_ISSUER"] = FAKE_ISSUER

    def boot(extra):
        return subprocess.run([sys.executable, "-c", "import main"], cwd=HERE, env={**env, **extra}, capture_output=True, text=True, timeout=120)
    for extra in ({}, {"CANON_CONTACT": "   "}, {"CANON_CONTACT": "help@example.com"}):
        r = boot(extra)
        assert r.returncode == 0, (extra, r.stderr[-400:])
    # app.html: 문의 구간의 모든 보간은 esc(…) · innerHTML 우변은 템플릿 하나(이어 붙이기 없음) · 다른 HTML 삽입 API 없음
    html = (HERE / "app.html").read_text(encoding="utf-8")
    blk = html[html.index("// ── 문의 데스크 (desk-0) ──"):html.index("// ── /문의 데스크 ──")]
    assert blk.count("${") == blk.count("${esc(") >= 4, re.findall(r"\$\{(?!esc\()[^}]*", blk)
    for bad in ("insertAdjacentHTML", "outerHTML", "document.write", "DOMParser", "createContextualFragment"):
        assert bad not in blk, bad
    sites = list(re.finditer(r"\.innerHTML\s*=\s*", blk))
    assert sites, "행은 innerHTML 템플릿으로 만든다"
    for m in sites:
        rest = blk[m.end():]
        assert rest.startswith("`"), rest[:60]
        end = rest.index("`", 1)
        assert rest[end + 1:].lstrip().startswith(";"), rest[end:end + 40]
    sec = html[html.index('<section class="wrap desk"'):html.index("</section>")]
    assert "${" not in sec and db.NOTICE in sec and 'href="/privacy"' in sec, "정적 절 — 고지 문장·처리방침 링크"
    for w in ('value="bug"', 'value="request"', 'value="question"', 'value="privacy"', "개인정보 요청", "내 문의", "4096"):
        assert w in sec, w
    assert 'ticketStatus: {open: "접수", ack: "확인", doing: "처리중", done: "완료", wontfix: "보류"}' in html
    assert "const DESK_MAX = 4096;" in html and db.LIMIT_TICKET == 4096
    # 관리자 문의함: 사용자 글·이메일은 esc, 메모는 textarea.value(HTML 아님), 상태 선택지는 textContent · 띠 숫자도 esc
    for w in ("${esc(t.email)}", "${esc(t.text)}", "${esc(c.open)}", "${esc(c.privacy_open)}", "ta.value = t.admin_note",
              "o.textContent = LABELS.ticketStatus[k]", "j.admin === true"):
        assert w in blk, w
    assert "admin_note}" not in blk and "${t." not in blk and "${c." not in blk
    assert f"const DESK_NOTE_MAX = {db.LIMIT_ADMIN_NOTE};" in html
    assert '<div class="wrap bands" id="admin-band" hidden></div>' in html, "관리자 띠 자리는 기본 숨김"
    print("OK — 문의 단위(ua 계열·CANON_CONTACT 선택·app.html esc 정적 검사) 통과")


async def run():
    cleanup()
    try:
        test_migration()
        test_core_pr1()
        await test_verifier()
        await test_tools()
        await test_http()
        await test_web()
        A = test_definitions()
        await test_definition_tools()
        await test_web_items(A)
        test_template_slot(A)
        await test_limits_sse()
        await test_limits()
        await test_feedback()
        await test_feedback_admin()
        test_feedback_units()
    finally:
        with db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchall()]
        cleanup()   # 후보·질문·정의·항목이 있는 사용자 삭제가 끝까지 되는지도 여기서 확인된다
        with db.tx() as c:
            left = c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchone()["n"]
            rest = {t: c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = ANY(%s)", (ids,)).fetchone()["n"]
                    for t in ("object_defs", "field_defs", "items", "item_links", "item_history", "records", "candidates", "tickets")}
        db.close()
    assert left == 0, left
    assert not any(rest.values()), rest
    print("OK — 시험 사용자 삭제(cascade)")


# ── 임시 스키마 격리 (CANON_TEST_SCHEMA) ─────────────────

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def schema_conn(name):
    """임시 스키마 하나만 보는 별도 연결(search_path 에 public 없음). 운영 테이블을 이름으로 못 찾는다."""
    assert _SCHEMA_RE.match(name) and name != "public", name
    c = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    c.execute(f"SET search_path TO {name}")
    c.commit()
    return c


def create_schema(name):
    assert _SCHEMA_RE.match(name) and name != "public", name
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {name}")   # 이미 있으면 실패 — 남의 스키마를 덮지 않는다


def drop_schema(name):
    assert _SCHEMA_RE.match(name) and name != "public", name
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")


def isolate(name):
    """db.pool 을 임시 스키마 전용으로 바꾸고 최종형 스키마를 깐다.
    풀 객체가 아니라 db.pool 함수를 바꾼다 — main.lifespan 이 끝에 db.close() 해도 다시 열리는 풀이 public 이 아니게."""
    create_schema(name)

    def cfg(conn):
        conn.execute(f"SET search_path TO {name}")
        conn.commit()

    def isolated_pool():
        if db._pool is None:
            db._pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=4,
                                      kwargs={"row_factory": dict_row}, configure=cfg, open=True)
        return db._pool

    db.close()
    db.pool = isolated_pool
    db.init_schema()
    with db.tx() as c:
        assert c.execute("SELECT current_schema() AS s").fetchone()["s"] == name


def main_():
    name = os.environ.get("CANON_TEST_SCHEMA", "").strip()
    if not name:
        asyncio.run(run())
        return
    name = f"{name}_{secrets.token_hex(3)}"
    print(f"임시 스키마 {name} 에서 시험")
    try:
        isolate(name)
        asyncio.run(run())
        with db.tx() as c:   # 끝까지 임시 스키마였는지(lifespan 의 db.close 뒤 다시 연 풀 포함)
            assert c.execute("SELECT current_schema() AS s").fetchone()["s"] == name
    finally:
        db.close()
        drop_schema(name)
        print(f"OK — 임시 스키마 {name} 삭제")


if __name__ == "__main__":
    main_()
