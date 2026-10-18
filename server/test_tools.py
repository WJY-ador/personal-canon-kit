"""서버 시험 — Supabase(DATABASE_URL, server/.env)에 붙어 돈다. 비밀값은 출력하지 않는다.

1) 인증 단위 시험: 가짜 RSA 키로 만든 JWT 를 AuthKitVerifier 가 거르는지 (JWKS 는 가짜 키 주입)
2) 도구 흐름 시험: SDK 내장 클라이언트로 서버를 직접 부른다 (HTTP·인증 없이, CANON_DEV 사용자)
3) HTTP 시험: Starlette 앱을 ASGI 로 불러 토큰 → 사용자 결정, /health, PRM, 401 을 본다

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

TEST_EMAILS = ("tester@example.com", "other@example.com")
RESOURCE = f"{PUBLIC}/mcp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WRONG_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}


def fake_jwks(verifier):
    verifier.jwks.fetch_data = lambda: {"keys": [JWK]}


_DEFAULT = object()


def make_token(key=KEY, aud=RESOURCE, iss=FAKE_ISSUER, exp_in=600, sub="user_test_sub", alg="RS256", kid="k1",
               scope=_DEFAULT, scp=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub, "client_id": "client_test",
              "iat": now, "exp": now + exp_in, "jti": f"t{now}"}
    if scope is _DEFAULT:
        scope = "openid email"
    if scope is not None:
        claims["scope"] = scope
    if scp is not None:
        claims["scp"] = scp
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


async def run():
    cleanup()
    try:
        await test_verifier()
        await test_tools()
        await test_http()
    finally:
        cleanup()   # 후보·질문이 있는 사용자 삭제가 cascade 로 되는지도 여기서 확인된다
        with db.tx() as c:
            left = c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchone()["n"]
        db.close()
    assert left == 0, left
    print("OK — 시험 사용자 삭제(cascade)")


if __name__ == "__main__":
    asyncio.run(run())
