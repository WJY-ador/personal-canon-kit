"""서버 시험 — Supabase(DATABASE_URL, server/.env)에 붙어 돈다. 비밀값은 출력하지 않는다.

1) 인증 단위 시험: 가짜 RSA 키로 만든 JWT 를 AuthKitVerifier 가 거르는지 (JWKS 는 가짜 키 주입)
2) 도구 흐름 시험: SDK 내장 클라이언트로 서버를 직접 부른다 (HTTP·인증 없이, CANON_DEV 사용자)
3) HTTP 시험: Starlette 앱을 ASGI 로 불러 토큰 → 사용자 결정, /health, PRM, 401 을 본다
4) 웹 시험: 시작 화면 / → /login → 가짜 토큰 교환 → /auth/callback → 세션 쿠키 → /api/* · 결정 저장 · Origin 거절 · 로그아웃(다음 로그인은 prompt=login)
5) 정의 층 시험(2단계): 정의 제안→적용·충돌·깨지는 변경 거절 · 항목 제안→적용→확정→되돌리기·이력 · 관계 검증 · 승격 · record_attach
   · question_id "" → NULL · 원작자 템플릿 시드(시험 사용자에게만) · /api/item·/api/item/decide

실행: server/.venv/bin/python server/test_tools.py   (어느 폴더에서든)
시험 사용자 tester@example.com · other@example.com 은 시작·끝에 지운다(cascade).
"""
import asyncio
import json
import os
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
import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from mcp.client import Client  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402

TEST_EMAILS = ("tester@example.com", "other@example.com", "def1@example.com", "def2@example.com")
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
    # DEV 폴백이 쓰이면 other 의 빈 상태가 나온다 → 토큰 경로가 tester 로 가는지 구분된다
    os.environ["CANON_DEV_USER"] = "other@example.com"
    hdr = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "state_get", "arguments": {}}}
    try:
        async with main.lifespan(main.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                r = await h.get("/health")
                assert r.status_code == 200 and r.json() == {"ok": True, "db": True}, r.text
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
    finally:
        auth.httpx.post = real_post
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
                d = exchanged[0]
                chal = base64.urlsafe_b64encode(hashlib.sha256(d["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                assert chal == q["code_challenge"][0], "PKCE verifier 가 쿠키의 challenge 와 맞아야"
                assert d["grant_type"] == "authorization_code" and d["code"] == "c1" and d["resource"] == RESOURCE
                assert (await h.get("/app")).status_code == 200
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
        assert new <= tools and len(tools) == 15, tools
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



async def run():
    cleanup()
    try:
        await test_verifier()
        await test_tools()
        await test_http()
        await test_web()
        A = test_definitions()
        await test_definition_tools()
        await test_web_items(A)
    finally:
        with db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchall()]
        cleanup()   # 후보·질문·정의·항목이 있는 사용자 삭제가 끝까지 되는지도 여기서 확인된다
        with db.tx() as c:
            left = c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchone()["n"]
            rest = {t: c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = ANY(%s)", (ids,)).fetchone()["n"]
                    for t in ("object_defs", "field_defs", "items", "item_links", "item_history", "records", "candidates")}
        db.close()
    assert left == 0, left
    assert not any(rest.values()), rest
    print("OK — 시험 사용자 삭제(cascade)")


if __name__ == "__main__":
    asyncio.run(run())
