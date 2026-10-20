"""서버 시험 — 로컬 Postgres(DATABASE_URL)에 붙어 돈다. 원격(server/.env)은 CRM_TEST_ALLOW_REMOTE=1 + CRM_TEST_SCHEMA 일 때만. 비밀값은 출력하지 않는다.

1) 인증 단위 시험: 가짜 RSA 키로 만든 JWT 를 AuthKitVerifier 가 거르는지 (JWKS 는 가짜 키 주입)
2) 도구 흐름 시험: SDK 내장 클라이언트로 서버를 직접 부른다 (HTTP·인증 없이, CRM_DEV 사용자)
3) HTTP 시험: Starlette 앱을 ASGI 로 불러 토큰 → 사용자 결정, /health, PRM, 401 을 본다
4) 웹 시험: 시작 화면 / → /login → 가짜 토큰 교환 → /auth/callback → 세션 쿠키 → /api/* · 결정 저장 · Origin 거절 · 로그아웃(다음 로그인은 prompt=login) · /help·/privacy
5) 정의 층 시험(2단계): 정의 제안→적용·충돌·깨지는 변경 거절 · 항목 제안→적용→확정→되돌리기·이력 · 관계 검증 · 승격 · record_attach
   · question_id "" → NULL · 원작자 템플릿 시드(시험 사용자에게만) · /api/item·/api/item/decide
6) 전역 상한(α1): 요청 본문 256KB(413, Content-Length·chunked) · MCP SSE 응답이 미들웨어 뒤에서 그대로 · 인자 길이 PAYLOAD-LIMIT
   (MCP·웹) · DB 크기 400MB 넘으면 비관리자 쓰기 QUOTA-GLOBAL(관리자·읽기 정상, 측정 실패면 허용)
7) 문의 데스크(desk-0): 401·Origin 403 · 4KB+1 PAYLOAD-LIMIT(웹·MCP) · 사용자 11번째·전역 101번째 RATE-LIMIT · QUOTA-GLOBAL 면제
   · 「내 문의」에 운영자 메모·hint·남의 글 없음 · privacy → privacy_log · hint · page·tool 정규화 · MCP echo · 파기 181/179일·reopen
   · 비DEV+CRM_CONTACT 없음·공백 → 기동 성공(선택 env) · app.html 문의 구간 esc 정적 검사
   · 관리자 문의 처리(/admin/tickets POST): /app·/api 에 관리자 키·남의 글 없음 · 옛 /api/feedback/status·note 없음(404) · 404·Origin 403
     · 상태 전이·closed_at · privacy_log 종결 기록 · 메모 2KB
8) 화면 자산: /assets/{name} 허용 목록·캐시 헤더·404 · / 자리표시 치환·스크립트는 외부 self theme.js 하나(인라인 0) · /help 무배경 ·
   외부 셰이더 문자열 0 · 용량 상한(라이트·다크) · 기본 밝은 화면(OS 미추종)+☀/☾ 토글 · 데모가 낡았으면(app.html 바뀜) 경고만
9) α2: 처리방침 버전(guides 머리글) · 필수 동의 게이트 없음(미동의자 MCP·웹 쓰기·/app 200·/consent→/app·/api/consent 404) · 일일 한도(기록·질문·후보·결정·제안)
   · usage_daily(메모리만·flush 합산/로그인 사용자는 그 uid/실패 버림·lifespan 종료 flush→close) · 계정 삭제 뒤 usage_daily·agreements 0
10) β2: /admin 404(무세션·비관리자·DEV) · 재인증 없음(관리자 세션이면 화면·POST 바로, /admin/reauth→/admin, 콜백→/app, 옛 ra 무시) · 쿠키 v2(옛 쿠키·끊기·차단·DB 오류)
   · 부여·회수·LAST-ADMIN·동시 회수 · 차단/해제(웹·MCP AUTH-BLOCKED/REVOKED) · 웹 끊기는 MCP 무관 · /app 에 관리자 데이터 없음
   · CSP·스크립트 0·이스케이프 · admin_cli grant · 감사 파기 · bootstrap(임시 스키마)·lifespan 배선
11) 운영 경계(admin_db): 정적(/admin·관리자 문의 핸들러는 db 조회를 직접 안 부름 · admin_db SQL 은 ALLOW 리터럴만 · SELECT * 0 ·
   본문 열 0 · text·admin_note 는 문의 상세만 · label·options 는 이름 공유 분기만) · ALLOW 키별 행 키 = cols · 조회자·미구현 키 거절
   · 표식(금지 열 값이 /admin·/api 관리자 응답에 없음, 문의 상세엔 있음)
12) 선택 동의·처리방침 v4: 옛 버전 동의자도 쓰기 통과 · /api/optional-consent 왕복·로그·무동작·미동의자·동시 · 옛 버전 무효
   · /privacy v4(처리 근거 표·출처·표준 편입·작성자 표시·계정 메뉴 문의) · 옛 사용량 삭제 표식 멱등(임시 스키마)
13) 문의 완결(P3): 공개/내부 이력 분리(웹 GET /api/feedback/<id>·MCP ticket_*_mine) · 남의 문의 404 · 답변 한 트랜잭션 · 메모 public_updated 불변
   · 재분류(기산점 created·즉시 초과·재진입 1행·해제 사유) · /admin/tickets/<id>/* POST 사다리 · 실행자 재조회 · 파기 cascade · 정기 파기
13b) 문의 화면(P4-4·5): /admin/tickets 리스트·레코드 404·옛 ra 쿠키 통과·CSP·escape · 기본 필터·기한 정렬 · 400 · 상한 · Path·form·이력 배지
14) 운영 화면(P4): /admin 개요·/admin/schema·/admin/reports — 404·옛 ra 쿠키 통과·CSP·스크립트 0·공통 메뉴 · 이름 공유(동의자만, 철회·옛 버전 0)
   · escape · 리포트 400(모르는 키·조합·기간) · 행 상한 200 · 프리셋 전부 200 · 개요 = 프리셋 정의 · 표준 구조(템플릿 + 코어)
15) /app 계정 메뉴(P5): users.desk_hidden 열 · /api/desk-visibility 401·403·400·저장·no-store·동의 게이트 밖(화면은 안 씀 — Q1b)
   · /api/state user 의 struct_names(비동의·동의·철회·옛 버전)·desk_hidden · MCP state_get 엔 두 키 없음
   · app.html 정적(인라인 스크립트 1개·CSP 해시 일치·인라인 이벤트 속성 0·메뉴 항목·⚙ 유지)
16) 통합(P5-6): 권한 회수 뒤 /admin 전부 404·문의 처리 거절 · 문의 소유권(웹·MCP) · 공개/내부 이력 · 파기 cascade
   · 관리자 세션으로도 /app·/api 에 관리자 데이터 0 · 이름 공유(사용자 상세에만·리포트 0·철회 뒤 0) · 「내 문의」 공개 갱신순(웹=MCP)
17) R1(PR #20 2단계): 기능 플래그 CRM_FEATURE_STANDARD·SOURCES 둘 다 미설정(off)에서 로그인(웹·DEV) · /admin 권한(일반 404·관리자 바로·재인증 없음)
   · /admin 문의 처리(상태·답변) · Q1 관리자 /app = ⚙ + 「운영 콘솔」·로그아웃만(문의·이름 공유 메뉴·문의 대화창은 보내지 않음)
   · Q1b 일반 사용자 /app = 하단 문의 섹션 없음 · 「문의 보내기」·「내 문의」는 대화창(desk-dlg) · 「문의 섹션 숨기기」 없음
18) R2(PR #20 3단계, DB 없이): 기능 플래그 해석(on 만 켬) · render.yaml sync:false 선언 · 수신자별 응답(본인·운영자 비동의·동의) 허용 목록
   · 이름 누출 0(비동의: label·key·선택지·출처 name·location·id / 동의: key·선택지 값·출처·id) · origin·관계 파생 · std 모양 · 오류 코드 문구 · 예제 동결 일치
19) R3(PR #20 4단계): 표준 편입 미리보기 판정(누락·일치·확장·충돌 사유·참조 의존·초안·보관) · 쓰기 0(READ ONLY·전 테이블 해시)·플래그 무관
   · std_choices 보류·거절(STANDARD off → FEATURE-OFF · 덮어쓰기 · 편입된 것 STANDARD-LOCKED · 무관한 쓰기 안 막음) · 본인만(/api/std/*)
   · /api/state std 모양(MCP 엔 없음) · definitions() template_slot·std_id → origin · init_schema 옛 DB 2회 멱등
20) Q2(PR #20 5단계): template_std == 동결 파일 · 신규 시드(on)/안 함(off) · 시드 실패 주입 → 로그인 성공·seed_pending·재시도·정의 생기면 중단
   · 동시 재시도 1회 · 기존 사용자(표식 없음) 쓰기 0 · 편입 적용(누락·일치·확장·새 id) · 재검증(revision·대상 상태·이미 편입)·참조 의존·충돌·확인 없음
   · 잠금 표(거절·허용, propose·apply, 플래그 off 에서도, select 선택지) · 일일 한도·template_slot 백필 제외 · init_schema 2회 멱등
21) Q3 축소(PR #20 6단계): 출처(SOURCES off 기록만·on 찾거나 만들기·사용자별·FK·옛 기록 미확인·본인만·MCP 인자) · 분류 역할(이관·멱등·시드·
   늦은 축·record_attach 역할 경유·CLASSIFY-OFF) · 확정 재검사 4사유·review(웹=MCP)·해제 · external 거절·정정 허용 · init_schema 2회 멱등
23) PR A 운영 Setup: 왼쪽 메뉴·Quick Find(GET /admin/find, 서비스 정의만)·Setup Home 3구역·Object Manager(코어 5 + std@1 3, 상세·템플릿 구역)
   — 새 GET 404 · 노출·key 0 · DB 조회 0 · 결정성 · 정의 일치 · std@1·사용자 DMO 바이트 · 옛 URL·POST · cascade 회귀

실행: server/.venv/bin/python server/test_tools.py   (어느 폴더에서든)
시험 사용자 tester@example.com · other@example.com 은 시작·끝에 지운다(cascade).
운영 DB(옛 스키마)에 붙어 새 코드를 시험할 땐 CRM_TEST_ALLOW_REMOTE=1 + CRM_TEST_SCHEMA=<이름> — 둘 중 하나라도 없으면
로컬 아닌 호스트에선 시작 전에 멈춘다. 그 이름의 임시 스키마를 만들어
최종형을 init_schema 로 깔고 그 안에서만 돈 뒤 끝에 지운다(운영 테이블은 안 건드린다). CI 는 빈 Postgres 라 안 쓴다.
"""
import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
import os
import re
import secrets
import sys
import time
import urllib.parse
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
# 로컬 아닌 DB 는 명시 허용 + 임시 스키마일 때만 (운영 public 보호). 호스트·URL 은 출력하지 않는다
if urllib.parse.urlsplit(os.environ["DATABASE_URL"]).hostname not in ("localhost", "127.0.0.1", "::1", None) \
        and not (os.environ.get("CRM_TEST_ALLOW_REMOTE") == "1" and os.environ.get("CRM_TEST_SCHEMA", "").strip()):
    raise SystemExit("중단: DATABASE_URL 호스트가 로컬이 아니에요(server/.env 를 읽었을 수 있어요). "
                     "로컬 DB 를 DATABASE_URL 로 export 하세요. "
                     "원격 임시 스키마 시험은 CRM_TEST_ALLOW_REMOTE=1 + CRM_TEST_SCHEMA=<이름>.")
FAKE_ISSUER = "https://fake.authkit.test"
PUBLIC = "http://127.0.0.1:8800"
os.environ["CRM_DEV"] = "1"
os.environ["CRM_DEV_USER"] = "tester@example.com"
os.environ["AUTHKIT_ISSUER"] = FAKE_ISSUER          # 토큰 경로를 HTTP 시험에서 실제로 태운다
os.environ["CRM_PUBLIC_URL"] = PUBLIC

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402
import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from mcp.client import Client  # noqa: E402

import auth  # noqa: E402
import contract  # noqa: E402
import db  # noqa: E402
import envnames  # noqa: E402
import main  # noqa: E402
import web  # noqa: E402

LIM_KINDS = ("records", "candidates", "decisions", "proposals", "questions")   # α2 일일 한도 — 종류마다 전용 사용자
LIM_EMAIL = {k: f"lim-{k}@example.com" for k in LIM_KINDS}
TEST_EMAILS = ("tester@example.com", "other@example.com", "def1@example.com", "def2@example.com",
               "core1@example.com", "core2@example.com", "noconsent@example.com") + tuple(f"desk{i}@example.com" for i in range(11)) \
    + tuple(LIM_EMAIL.values()) \
    + ("b2a@example.com", "b2b@example.com", "b2c@example.com", "b2m@example.com", "<b>esc</b>@example.com") \
    + ("adb-admin@example.com", "adb-user@example.com") \
    + tuple(f"oc{i}@example.com" for i in range(1, 5)) \
    + ("tk-owner@example.com", "tk-other@example.com", "tk-admin@example.com") \
    + ("tkv-admin@example.com", "<i>tkv</i>@example.com") \
    + ("int-admin@example.com", "int-admin2@example.com", "int-owner@example.com", "int-other@example.com", "int-names@example.com") \
    + ("r1-admin@example.com", "r1-user@example.com") \
    + tuple(f"r3{k}@example.com" for k in ("none", "match", "conf", "edge", "orig")) \
    + tuple(f"q3{k}@example.com" for k in ("src", "srcb", "role", "nor", "refu", "late", "rev", "mcp")) \
    + tuple(f"q2{k}@example.com" for k in ("off", "seed", "dev", "fail", "stop", "race", "old", "inc", "dep", "conf", "lock", "opt", "web")) \
    + tuple(f"p4{k}@example.com" for k in ("a", "g", "n", "w", "o"))   # P4 운영 화면: a 관리자 · g 이름 공유 동의 · n 비동의 · w 철회 · o 옛 버전
# desk* = 전역 한도 시험, noconsent = α2 미동의 전용, lim-* = α2 일일 한도, b2* = β2 관리자·차단, adb-* = 운영 경계, oc* = 선택 동의, tk-* = 문의 완결
# p4* = 운영 화면(개요·스키마·리포트), r1-* = R1 플래그 off 기준선·Q1, r3* = R3 표준 미리보기·선택, q2* = Q2 시드·편입·잠금, q3* = Q3 출처·역할·재검사
TESTER, DEF1, NOCONSENT = TEST_EMAILS[0], TEST_EMAILS[2], TEST_EMAILS[6]
RESOURCE = f"{PUBLIC}/mcp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
WRONG_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}


def fake_jwks(verifier):
    verifier.jwks.fetch_data = lambda: {"keys": [JWK]}


_DEFAULT = object()


def make_token(key=KEY, aud=RESOURCE, iss=FAKE_ISSUER, exp_in=600, sub="user_test_sub", alg="RS256", kid="k1",
               scope=_DEFAULT, scp=None, sid=None, extra=None):
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
    claims.update(extra or {})   # β2: 임의 클레임
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def cleanup():
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchall()]
        # β2 감사는 FK·user_id 열이 없다 — 시험 사용자가 실행자·대상인 행을 따로 지운다
        c.execute("DELETE FROM admin_audit WHERE actor_id = ANY(%s) OR target_id = ANY(%s)", (ids, ids))
    for uid in ids:
        db.user_delete(uid)


def _set_role(uid, role):
    """β2: 관리자 = users.role. 시험이 관리자 여부를 바꾸는 한 곳(감사 없이 직접)."""
    with db.tx() as c:
        c.execute("UPDATE users SET role=%s WHERE id=%s", (role, uid))


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
    os.environ["CRM_DEV_USER"] = "tester@example.com"
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
        os.environ["CRM_DEV_USER"] = "other@example.com"
        st2 = val(await c.call_tool("state_get", {}))
        assert st2["questions"] == [] and st2["journal"] == [] and st2["candidates"] == []
        other = db.user_by_email("other@example.com")["id"]
        try:
            db.candidates_propose(other, r["id"], q["id"], [{"text": "x", "quote": "입력 담당"}], "ai")
            raise AssertionError("다른 사용자의 기록에 후보가 붙었다")
        except db.CrmError as err:
            assert err.code == "RECORD-MISSING", err.code
        cross = await c.call_tool("candidates_propose", {"record_id": r["id"], "question_id": q["id"],
            "candidates": [{"text": "x", "quote": "입력 담당"}]})
        assert cross.is_error and "RECORD-MISSING" in cross.content[0].text, cross
        os.environ["CRM_DEV_USER"] = "tester@example.com"
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
    os.environ["CRM_DEV_USER"] = "other@example.com"
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
        os.environ["CRM_DEV_USER"] = "tester@example.com"
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
                # 처리방침: 로그인 없이 200, 같은 렌더러(escape), 09·10 링크는 /help#…, {{CONTACT_LINE}} 은 env CRM_CONTACT(선택 — 없으면 메일 줄 없음)
                r = await h.get("/privacy")
                assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
                for word in ("개인정보 처리방침", 'id="privacy"', "<table>", "Render Services", "Supabase", "WorkOS", "싱가포르", "미국",
                             "제28조의8", "v4 · 2026-10-20", 'href="/help"', 'href="/login"', "--accent: #186A4B"):
                    assert word in r.text, word
                assert "{{CONTACT" not in r.text and "「개인정보 요청」" in r.text and 'href="/app"' not in r.text and "|---|" not in r.text and "**" not in r.text
                real_contact, real_privacy = os.environ.get("CRM_CONTACT"), site.privacy_html
                try:
                    os.environ["CRM_CONTACT"] = '<b>x</b> [y](javascript:alert(1))'
                    site.load_help(web.GUIDES_DIR)
                    r = await h.get("/privacy")
                    assert r.status_code == 200 and "&lt;b&gt;x&lt;/b&gt;" in r.text and "<b>x" not in r.text, r.text[-800:]
                    assert 'href="javascript' not in r.text.lower() and "{{CONTACT" not in r.text and "메일: " in r.text, "연락처 env 도 렌더러 escape·링크 검사를 탄다"
                    os.environ.pop("CRM_CONTACT")
                    site.load_help(web.GUIDES_DIR)
                    r = await h.get("/privacy")
                    assert r.status_code == 200 and "메일: " not in r.text and "{{CONTACT" not in r.text, "연락처 env 없으면 메일 줄 없음(500 아님)"
                finally:
                    if real_contact is None:
                        os.environ.pop("CRM_CONTACT", None)
                    else:
                        os.environ["CRM_CONTACT"] = real_contact
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
                assert r.status_code == 302 and "crm_login" in r.headers.get("set-cookie", ""), r.headers
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
                assert "crm_session" in r.headers.get("set-cookie", "")
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
                assert r.status_code == 200 and 'crm_session=""' in r.headers.get("set-cookie", ""), r.headers
                assert r.json() == {"ok": True, "redirect": "/?out=1"}, "로그아웃은 시작 화면으로"
                assert (await h.get("/api/state")).status_code == 401, "로그아웃 뒤 401"
                # 토큰에 sid 가 있어도 세션에 담지 않고, 로그아웃 주소도 같다
                tok_sid["sid"] = "app_consent_test"
                await login_once(h)
                assert (await h.get("/api/state")).status_code == 200
                assert "sid" not in site.unsign(h.cookies.get("crm_session")), "세션에 sid 를 담지 않는다"
                r = await h.post("/logout", headers=origin)
                assert r.status_code == 200 and 'crm_session=""' in r.headers.get("set-cookie", ""), r.headers
                assert r.json() == {"ok": True, "redirect": "/?out=1"}, r.json()
                assert (await h.get("/api/state")).status_code == 401, "로그아웃 뒤 401"
                r = await h.get("/")
                assert r.status_code == 200 and "로그인" in r.text, "로그아웃 뒤 시작 화면"
                # 서명 위조 쿠키
                h.cookies.set("crm_session", site.session_value(uid)[:-3] + "xxx")
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
                csp_base = "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; script-src 'self'"
                sec = {"x-content-type-options": "nosniff", "x-frame-options": "DENY",
                       "content-security-policy": csp_base, "referrer-policy": "same-origin"}
                h.cookies.clear()   # 위에서 넣은 위조 세션 쿠키를 지우고 새로 로그인
                pages = [await h.get("/"), await h.get("/help"), await h.get("/privacy"), await h.get("/auth/callback", params={"error": "x"})]
                await login_once(h)
                app_r = await h.get("/app")
                assert app_r.status_code == 200, app_r.status_code
                for r in [*pages, app_r]:
                    assert r.headers.get("content-type", "").startswith("text/html"), (r.url, r.headers)
                    for k, v in sec.items():
                        if k == "content-security-policy" and r is app_r:
                            continue
                        assert r.headers.get(k) == v, (r.url, k, r.headers.get(k))
                    csp = r.headers["content-security-policy"]
                    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp, (r.url, csp)
                # /app: 공통 + 'sha256-…' 하나. 기대값은 응답 본문 인라인 스크립트에서 직접 계산(web 함수를 쓰지 않는다)
                import base64 as _b64m
                import hashlib as _hl
                bodies = [b for a, b in re.findall(r"<script\b([^>]*)>(.*?)</script>", app_r.text, flags=re.S | re.I) if "src=" not in a]
                assert len(bodies) == 1, len(bodies)
                want = _b64m.b64encode(_hl.sha256(bodies[0].encode("utf-8")).digest()).decode()
                assert app_r.headers["content-security-policy"] == f"{csp_base} 'sha256-{want}'", app_r.headers["content-security-policy"]
                # 해시 함수 단위: 한 글자 바꾸면 다름 · 인라인 0개·2개 → 해시 없음('self' 만) · <script src=…> 는 세지 않음
                one = '<script src="/assets/theme.js?v=1"></script><p>x</p><script>\n  let a = 1;\n</script>'
                h1 = web.inline_script_hashes(one)
                assert h1 == [_b64m.b64encode(_hl.sha256(b"\n  let a = 1;\n").digest()).decode()], h1
                assert web.inline_script_hashes(one.replace("a = 1", "a = 2")) != h1, "한 글자 바꾸면 해시가 달라진다"
                assert web.app_csp(one) == f"{csp_base} 'sha256-{h1[0]}'"
                assert web.inline_script_hashes(one.replace("a = 1", "한 = 1")) == [_b64m.b64encode(_hl.sha256("\n  let 한 = 1;\n".encode("utf-8")).digest()).decode()], "UTF-8 바이트"
                for bad in ('<script src="/a.js"></script><p>인라인 없음</p>', one + "<script>b()</script>", "<p>스크립트 없음</p>"):
                    assert web.app_csp(bad) == csp_base, bad
                assert web.inline_script_hashes('<SCRIPT type="module" src="/m.js"></SCRIPT>') == [], "src 있는 태그는 세지 않는다(대소문자 무관)"
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
    except db.CrmError as e:
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
    # 분류(axes)는 역할 매핑 대상 오브젝트의 항목만(§5) — A 의 축 오브젝트 key 는 topics 라 이관 규칙(key axes)에 안 잡힌다
    expect("CLASSIFY-OFF", db.record_attach, A, ra["id"], None, [topic["id"]])
    with db.tx() as c:   # 시험 재료: 분류 대상 = topics (역할 매핑이 정본 — key 와 무관)
        c.execute("INSERT INTO user_roles (user_id,role,object_id) VALUES (%s,'classify',%s)", (A, t["id"]))
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
    os.environ["CRM_DEV_USER"] = "def2@example.com"
    async with Client(main.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        new = {"definitions_get", "definition_propose", "definition_apply", "item_propose", "item_decide", "record_attach"}
        base = {"state_get", "question_add", "question_list", "record_add", "record_get", "candidates_propose", "candidate_decide",
                "candidate_list", "profile_set"}
        assert base | new | {"help_get", "feedback_send", "ticket_list_mine", "ticket_get_mine"} == tools and len(tools) == 19, tools   # 기존 15개 + help_get + feedback_send(desk-0) + 내 문의 읽기 2(P3)
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
    os.environ["CRM_DEV_USER"] = "tester@example.com"
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
    name = f"crm_tslot_{secrets.token_hex(3)}"
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
B2_TABLES = ("users", "admin_audit")   # β2: users 새 열(role·blocked_at·web/mcp_revoked_before)·admin_audit


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


def _shape(c, tables=CORE_TABLES):
    """테이블(기본 세 개)의 컬럼·제약·인덱스(스키마 이름 뺀 정의). 순서는 안 본다."""
    cols = {(r["table_name"], r["column_name"], r["data_type"], r["is_nullable"], r["column_default"]) for r in c.execute(
        "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name = ANY(%s)", (list(tables),)).fetchall()}
    cons = {(r["t"], r["conname"], r["d"]) for r in c.execute(
        "SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
        "WHERE connamespace = current_schema()::regnamespace AND conrelid::regclass::text = ANY(%s)", (list(tables),)).fetchall()}
    idx = {(r["tablename"], r["indexname"], r["indexdef"].replace(f"{r['schemaname']}.", "")) for r in c.execute(
        "SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename = ANY(%s)",
        (list(tables),)).fetchall()}
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
    old, fresh, bad = f"crm_mig_old_{tag}", f"crm_mig_new_{tag}", f"crm_mig_bad_{tag}"
    try:
        for n in (old, fresh, bad):
            create_schema(n)
        # (1) 새 설치: schema.sql 만으로 최종형
        with schema_conn(fresh) as c:
            db.init_schema(c)
            new_shape = _shape(c)
            new_b2 = _shape(c, B2_TABLES)
        # (2) 옛 모양 → 마이그레이션
        with schema_conn(old) as c:
            c.execute(OLD_DDL)
            c.commit()
            _seed_old(c)
            before = _data(c)
            c.commit()   # 운영처럼 유휴 연결에서 시작(init_schema 트랜잭션이 최상위가 되게)
            assert not db._has_table(c, "usage_daily"), "옛 DB 엔 usage_daily 가 없다"
            assert not db._has_table(c, "admin_audit") and not db._has_col(c, "users", "role"), "옛 DB 엔 β2 열·감사가 없다"
            db.init_schema(c)
            c.commit()
            assert db._has_table(c, "usage_daily"), "α2: init_schema 가 옛 DB 에 usage_daily 를 만든다"
            got_b2 = _shape(c, B2_TABLES)
            assert got_b2 == new_b2, ("β2: 옛 users·admin_audit ≠ 새 설치", got_b2[0] ^ new_b2[0], got_b2[1] ^ new_b2[1], got_b2[2] ^ new_b2[2])
            assert {r["id"]: r["role"] for r in c.execute("SELECT id,role FROM users").fetchall()} == {"u_m1": "user", "u_m2": "user"}, \
                "β2: 기존 사용자 role 은 user 로 채워진다"
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
            assert _shape(c, B2_TABLES) == got_b2, "β2: 두 번째 init_schema 가 users·admin_audit 를 바꿨다"
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
            e = expect("CANDIDATE-REVIEW", db.candidate_decide, "u_m1", "c_m1", "confirm")   # §6: 옛 기록 = unknown → 확정 불가
            assert e.message == contract.REVIEW_MSG["author_unknown"] and db.candidate_get("u_m1", "c_m1")["review"] == "author_unknown"
            d = db.candidate_decide("u_m1", "c_m1", "exclude")   # 다른 결정은 된다
            assert d["status"] == "excluded" and d["revision"] == 2 and d["wording_by"] is None, d
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
    ext = db.record_add(A, "외부", "남이 정리한 문장이다.", None, "가져옴", "self", "verbatim")   # external 직접 입력은 SCOPE-SELF-ONLY(§6)
    ext = db.record_provenance_set(A, ext["id"], "external", "verbatim", "남의 글이었음")       # 후보 없는 기록의 external 정정은 된다
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
    os.environ["CRM_DEV_USER"] = "tester@example.com"
    site.dev, site.dev_user = True, "tester@example.com"
    uid = db.user_by_email("tester@example.com")["id"]
    origin = {"origin": PUBLIC}
    real_measure = db._measure_db_size
    # 도구 분류: 쓰기 표에 없는 도구는 읽기 — 새 쓰기 도구가 게이트를 비켜 가지 않게 고정
    async with Client(main.mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert set(db.WRITE_LIMITS) <= names, set(db.WRITE_LIMITS) - names
    assert names - set(db.WRITE_LIMITS) == {"state_get", "question_list", "candidate_list", "record_get", "definitions_get", "help_get",
                                            "ticket_list_mine", "ticket_get_mine"}, names
    assert len(names) == 19 and db.QUOTA_EXEMPT == {"feedback_send"} <= set(db.WRITE_LIMITS), (len(names), db.QUOTA_EXEMPT)

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
            _set_role(uid, "admin")
            r = await c.call_tool("record_add", {"text": "관리자 기록"})
            assert not r.is_error, r.content[0].text
            r = await h.post("/api/candidate/decide", json={"candidate_id": cid, "action": "confirm"}, headers=origin)
            assert r.status_code == 200 and r.json()["status"] == "confirmed", r.text
            _set_role(uid, "user")
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
        db._measure_db_size = real_measure
        _set_role(uid, "user")
        db.quota_cache_reset()
    print("OK — 인자 길이·전역 DB 크기 상한 통과")


# ── 7b) 시작 화면 자산 ─────────────────────────────────
ASSET_MAX = {"start-bg.webp": 400 * 1024, "start-bg-still.webp": 30 * 1024,
             "start-bg-dark.webp": 400 * 1024, "start-bg-dark-still.webp": 30 * 1024,
             "start-demo.webp": 800 * 1024, "start-demo-poster.webp": 60 * 1024,
             "start-demo-dark.webp": 800 * 1024, "start-demo-dark-poster.webp": 60 * 1024, "theme.js": 4 * 1024}


async def test_start_assets():
    import hashlib
    import re
    site = main.site
    real = site.dev, site.dev_user
    site.dev, site.dev_user = False, ""   # 세션 없는 방문자 — / 가 시작 화면을 준다
    adir = HERE / "assets"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # 허용 목록 4개 전부 서빙 · 용량 상한
            assert set(web.ASSETS) == set(web.ASSET_FILES) == set(ASSET_MAX), web.ASSETS.keys()
            for name, mx in ASSET_MAX.items():
                data = (adir / name).read_bytes()
                sha = hashlib.sha256(data).hexdigest()[:10]
                assert web.ASSETS[name][2] == sha, name
                assert len(data) <= mx, (name, len(data), mx)
                r = await h.get(f"/assets/{name}", params={"v": sha})
                assert r.status_code == 200 and r.content == data, (name, r.status_code)
                assert r.headers["content-type"] == web.ASSET_FILES[name] and r.headers.get("x-content-type-options") == "nosniff", r.headers
                assert r.headers.get("cache-control") == "public, max-age=31536000, immutable", r.headers
                assert r.headers.get("etag"), "FileResponse 의 ETag"
                for params in ({}, {"v": "0000000000"}):   # v 없음·불일치 → 200 + no-cache
                    r = await h.get(f"/assets/{name}", params=params)
                    assert r.status_code == 200 and r.headers.get("cache-control") == "no-cache", (name, params, r.headers)
            r = await h.get("/assets/start-bg.webp", headers={"range": "bytes=0-9"})
            assert r.status_code == 206 and len(r.content) == 10, r.status_code
            # 허용 목록 밖 → 404. 탐색 경로는 {name} 이 / 를 안 받아 Mount("/") 로 떨어진다 — 200 아님·web.py 내용 없음만 단정
            for bad in ("x", "start-demo.json", "web.py"):
                assert (await h.get(f"/assets/{bad}")).status_code == 404, bad
            for raw in ("/assets/../web.py", "/assets/..%2Fweb.py", "/assets/%2e%2e/web.py"):
                r = await h.get(raw)
                assert r.status_code != 200 and b"SECURITY_HEADERS" not in r.content, (raw, r.status_code)
            # 시작 화면: 자리표시 0 · 스크립트 0 · v = 현재 sha10 · 로그인 버튼이 데모보다 DOM 앞
            for params in ({}, {"out": "1"}):
                r = await h.get("/", params=params)
                assert r.status_code == 200, r.status_code
                t = r.text
                assert "{{asset:" not in t, params
                # 스크립트는 외부 self 파일 하나(/assets/theme.js?v=…, <head> 안)뿐 — 인라인 0
                tags = re.findall(r"<script\b([^>]*)>(.*?)</script>", t, flags=re.S | re.I)
                assert t.lower().count("<script") == 1 and len(tags) == 1, tags
                assert re.fullmatch(r'\s*src="/assets/theme\.js\?v=[0-9a-f]{10}"\s*', tags[0][0]) and tags[0][1] == "", tags
                assert t.index("<script") < t.index("</head>"), "theme.js 는 head 에서 — 깜빡임 방지"
                assert t.count("data-theme-toggle") == 1, "☀/☾ 토글 하나"
                urls = re.findall(r"/assets/([a-z.-]+)\?v=([0-9a-f]{10})", t)
                assert {n for n, _ in urls} == set(ASSET_MAX), urls
                assert all(web.ASSETS[n][2] == v for n, v in urls), urls
                assert t.count('class="btn-login"') == 1 and t.count('href="/login') == 1, "로그인 버튼 하나"
                assert '<p class="cta-note">시작하면 <a href="/privacy">개인정보 처리방침</a>이 적용돼요.</p>' in t, "버튼 아래 고지 한 줄"
                assert not re.search(r"cta-note[^<]*<a[^>]*>[^<]*</a>[^<]*v\d", t), "고지 줄에 버전 문자열 없음"
                assert t.index('class="btn-login"') < t.index('<section class="demo"'), "로그인 버튼이 데모보다 앞"
                assert '<input type="checkbox" id="still">' in t and "움직임 멈추기" in t, "움직임 멈추기 체크박스"
                assert t.count('alt=""') == 4 and 'class="demo" aria-label="사용 모습 미리 보기' in t, "데모 그림 alt 는 비우고 설명은 section 이름"
                assert t.count('loading="lazy"') == 4, "테마·멈춤에 맞는 한 장만 받는다"
                assert re.search(r"<h1>[^<]*<br><em>[^<]+</em></h1>", t), "h1 = 한 곳, 강조는 <em>"
            # /help: 배경·자리표시 없음
            r = await h.get("/help")
            assert r.status_code == 200 and "{{asset:" not in r.text and "start-bg" not in r.text, r.status_code
            assert re.findall(r'/assets/[^"?]+', r.text) == ["/assets/theme.js"] and "data-theme-toggle" in r.text, "도움말은 theme.js·토글만"
            assert f'/assets/theme.js?v={web.ASSETS["theme.js"][2]}' in r.text
            # 기본 밝은 화면: OS 다크 설정·?theme 를 따르지 않는다. 어두운 화면은 토글(data-theme="dark")로만
            for f in ("app.html", "start.html"):
                src = (HERE / f).read_text(encoding="utf-8")
                assert "prefers-color-scheme" not in src and "dataset.theme" not in src and src.count(':root[data-theme="dark"] {') >= 1, f
                assert '<html lang="ko">' in src and "data-theme" not in src.split("<head>", 1)[0], f"{f}: 기본은 라이트"
            assert "prefers-color-scheme" not in r.text and ':root[data-theme="dark"]' in r.text, "/help 도 같은 토큰·토글"
            # 대시보드도 같은 theme.js·토글
            site.dev, site.dev_user = True, TEST_EMAILS[0]
            r = await h.get("/app")
            tj_tag = f'<script src="/assets/theme.js?v={web.ASSETS["theme.js"][2]}"></script>'
            assert r.status_code == 200 and "{{asset:" not in r.text and tj_tag in r.text, r.status_code
            assert r.text.index("theme.js") < r.text.index("</head>") and r.text.count("data-theme-toggle") == 1
            site.dev, site.dev_user = False, ""
    finally:
        site.dev, site.dev_user = real
    # 외부 셰이더 문자열 0건(바이너리 포함). 이 파일 자신에 걸리지 않게 조립한다
    needles = [b"shaders" + b".com", b"type" + b"gpu"]
    files = [*HERE.glob("*.html"), *HERE.glob("*.py"), *adir.glob("*")]
    hits = [(f.name, n) for f in files for n in needles if n in f.read_bytes()]
    assert not hits, hits
    # theme.js: 외부 주소·네트워크 호출 0, localStorage 는 try 안에서만, OS 설정 미추종
    tj = (adir / "theme.js").read_text(encoding="utf-8")
    assert not re.search(r"https?:|fetch\(|XMLHttpRequest|import\(|sendBeacon|WebSocket|\.src\s*=", tj), "theme.js 외부 요청 금지"
    assert all("try {" in ln for ln in tj.splitlines() if "localStorage." in ln), "localStorage 는 try/catch 안에서"
    assert "prefers-color-scheme" not in tj and "matchMedia" not in tj, "OS 설정 미추종"
    # 데모 낡음: 촬영 때 app.html 과 지금이 다르면 경고만(실패 아님) — tools/start-visuals/README.md 로 다시 찍는다
    meta = json.loads((adir / "start-demo.json").read_text(encoding="utf-8"))
    assert set(meta) == {"light", "dark"}, meta.keys()
    now_sha = hashlib.sha256((HERE / "app.html").read_bytes()).hexdigest()
    for theme, m in meta.items():
        if m.get("app_sha256") != now_sha:
            print(f"경고 — 시작 화면 데모({theme})가 낡았어요: app.html 이 촬영({m.get('shot_at')}) 뒤 바뀜. "
                  "tools/start-visuals/README.md 로 다시 찍을지 PR 에 적으세요.")
    print("OK — 시작 화면 자산·배경·데모 통과")


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
    os.environ["CRM_DEV_USER"] = "tester@example.com"
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
            assert all(set(t) == {"id", "kind", "text", "status", "via", "created", "updated", "has_reply", "reply_at", "public_updated"}
                       for t in ts), ts
            assert "운영자만 보는 메모" not in r.text and "privacy?" not in r.text and "다른 사람의 문의 글" not in r.text, r.text
            assert [t["status"] for t in ts if t["id"] == j["id"]] == ["doing"], ts
            assert ts == sorted(ts, key=lambda t: (t["public_updated"] or t["created"], t["created"]), reverse=True), "최근 공개 갱신(없으면 접수)부터"
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
            except db.CrmError as e:
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
    """관리자 문의 처리(P5 — /app 문의함 제거 뒤): /app·/api/state·/api/feedback 에 관리자 키·남의 글 없음(관리자 세션 포함)
    · 옛 /api/feedback/status·note 없음 · /admin/tickets POST 404(비관리자·DEV)·Origin 403 · 상태 전이·closed_at · privacy_log 종결 기록 · 메모 2KB."""
    site = main.site
    os.environ["CRM_DEV_USER"] = "tester@example.com"
    site.dev, site.dev_user = True, "tester@example.com"
    uid = db.user_by_email("tester@example.com")["id"]
    other = db.user_by_email("other@example.com")["id"]
    origin = {"origin": PUBLIC}
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
            async def post(tid, op, data, hd=origin):
                return await h.post(f"/admin/tickets/{tid}/{op}", data=data, headers=hd)
            # 비관리자(DEV): 관리자 키 없음 · 자기 글만 · 처리 POST 404 · 옛 API 없음(405)
            _set_role(uid, "user")
            st = (await h.get("/api/state")).json()
            assert "admin_tickets" not in st and "user" in st, list(st)
            assert "admin" not in st["user"], "비관리자 user 에 admin 키 자체가 없다(⚙ 링크)"
            r = await h.get("/api/feedback")
            j = r.json()
            assert r.status_code == 200 and set(j) == {"tickets"}, list(j)
            assert xss not in r.text and "other@example.com" not in r.text, "비관리자 응답에 남의 글·이메일 없음"
            for op, data in (("status", {"status": "ack"}), ("note", {"admin_note": "x"})):
                assert (await post(ids["bug"], op, data)).status_code == 404, op
            for url in ("/api/feedback/status", "/api/feedback/note"):
                r = await h.post(url, json={"id": ids["bug"], "status": "ack", "admin_note": "x"}, headers=origin)
                assert r.status_code == 404, (url, r.status_code)   # 라우트 없음 — /api/feedback/{tid} 는 GET 만, 나머지는 Mount("/") 의 404
            assert _ticket(ids["bug"])["status"] == "open" and _ticket(ids["bug"])["admin_note"] == "", "비관리자 쓰기는 반영 안 됨"
            # 관리자 — β2: role=admin 쿠키 세션. DEV 폴백에선 관리자 경로가 닫힌다
            _set_role(uid, "admin")
            site.dev, site.dev_user = False, ""
            h.cookies.set("crm_session", site.session_value(uid))
            for hd in ({}, {"origin": "https://evil.example"}):   # Origin 없음·다름 → 403 (관리자여도)
                for op, data in (("status", {"status": "ack"}), ("note", {"admin_note": "x"})):
                    r = await post(ids["bug"], op, data, hd)
                    assert r.status_code == 403 and "ORIGIN" in r.text, (op, hd, r.status_code)
            # 관리자 세션으로도 /app 쪽엔 관리자 데이터가 없다(⚙ 링크용 admin 만)
            st = (await h.get("/api/state")).json()
            assert "admin_tickets" not in st and st["user"]["admin"] is True, list(st)
            assert "admin" not in db.state(uid).get("user", {}) and "admin_tickets" not in db.state(uid), "MCP state_get(db.state)에는 없다"
            assert "admin_tickets" not in val(await mc.call_tool("state_get", {})), "MCP state_get 에 관리자 키 없음"
            r = await h.get("/api/feedback")
            assert r.status_code == 200 and set(r.json()) == {"tickets"} and xss not in r.text and "other@example.com" not in r.text, list(r.json())
            # 상태 전이: ack(closed_at 없음) → done(closed_at) → wontfix(closed_at 유지) → open(NULL)
            before = _ticket(ids["bug"])
            n0 = counts()["open"]
            r = await post(ids["bug"], "status", {"status": "ack"})
            t = _ticket(ids["bug"])
            assert r.status_code == 303 and t["status"] == "ack" and t["closed_at"] is None and t["updated"] > before["updated"], r.status_code
            assert counts()["open"] == n0 - 1
            r = await post(ids["bug"], "status", {"status": "done"})
            closed = _ticket(ids["bug"])["closed_at"]
            assert r.status_code == 303 and closed is not None
            r = await post(ids["bug"], "status", {"status": "wontfix"})
            assert r.status_code == 303 and _ticket(ids["bug"])["closed_at"] == closed, "종결끼리 옮기면 closed_at 유지"
            r = await post(ids["bug"], "status", {"status": "open"})
            assert r.status_code == 303 and _ticket(ids["bug"])["closed_at"] is None and _ticket(ids["bug"])["status"] == "open"
            n1 = counts()["privacy_open"]
            await post(ids["privacy"], "status", {"status": "doing"})
            assert counts()["privacy_open"] == n1, "처리 중인 개인정보 요청은 아직 센다"
            assert plog() == {"notified": None, "result": None}, plog()
            await post(ids["privacy"], "status", {"status": "done"})
            assert counts()["privacy_open"] == n1 - 1, "종결하면 빠진다"
            # privacy_log: 종결 → notified=KST 오늘·result done / wontfix → refused / 다시 열면 result 만 NULL(notified 유지)
            assert plog() == {"notified": _kst_today(), "result": "done"}, plog()
            with db.tx() as c:
                c.execute("UPDATE privacy_log SET notified = notified - 3 WHERE ticket_id=%s", (ids["privacy"],))
            kept = plog()["notified"]
            for st_, want in (("wontfix", "refused"), ("open", None), ("done", "done")):
                await post(ids["privacy"], "status", {"status": st_})
                assert plog() == {"notified": kept, "result": want}, (st_, plog())
            # 틀린 상태·빈 form·없는 id
            for tid, data, code, http in ((ids["bug"], {"status": "closed"}, "ARG-INVALID", 400), (ids["bug"], {}, "ARG-INVALID", 400),
                                          ("tk_nope", {"status": "ack"}, "NOT-FOUND", 404)):
                r = await post(tid, "status", data)
                assert r.status_code == http and code in r.text, (data, r.status_code)
            assert _ticket(ids["bug"])["status"] == "open"
            try:
                with db.tx() as c:
                    c.execute("UPDATE tickets SET status='closed' WHERE id=%s", (ids["bug"],))   # 스키마 CHECK 도 막는다
                raise AssertionError("열거형 밖 상태가 저장됐다")
            except psycopg.errors.CheckViolation:
                pass
            # 메모: 2KB 통과 · +1 PAYLOAD-LIMIT · 없는 id 404 · 사용자 「내 문의」엔 안 보임
            note = "메" * (db.LIMIT_ADMIN_NOTE // 3) + "x" * (db.LIMIT_ADMIN_NOTE % 3)
            assert len(note.encode()) == db.LIMIT_ADMIN_NOTE
            r = await post(ids["bug"], "note", {"admin_note": note})
            assert r.status_code == 303 and _ticket(ids["bug"])["admin_note"] == note, r.status_code
            r = await post(ids["bug"], "note", {"admin_note": note + "x"})
            assert r.status_code == 400 and "PAYLOAD-LIMIT" in r.text, r.status_code
            r = await post("tk_nope", "note", {"admin_note": "x"})
            assert r.status_code == 404 and "NOT-FOUND" in r.text, r.status_code
            # 글쓴이(비관리자)는 바뀐 상태만 보고 메모는 못 본다
            await post(ids["bug"], "status", {"status": "doing"})
            h.cookies.set("crm_session", site.session_value(other))
            r = await h.get("/api/feedback")
            mine = {t["id"]: t for t in r.json()["tickets"]}
            assert set(r.json()) == {"tickets"} and mine[ids["bug"]]["status"] == "doing" and "admin_note" not in mine[ids["bug"]], r.json()
            assert note not in r.text, "메모는 사용자에게 안 보인다"
            r = await h.get(f"/api/feedback/{ids['bug']}")
            assert r.status_code == 200 and note not in r.text and "admin_note" not in r.json(), "상세에도 메모 없음"
    finally:
        _set_role(uid, "user")
        site.dev, site.dev_user = True, "tester@example.com"
        with db.tx() as c:
            c.execute("DELETE FROM privacy_log WHERE ticket_id=%s", (ids["privacy"],))   # FK·cascade 밖
    print("OK — 관리자 문의 처리(/app 관리자 데이터 0·옛 API 404·/admin/tickets POST 404·403·상태 전이·closed_at·privacy_log 종결 기록·메모 2KB) 통과")


def test_feedback_units():
    """ua 계열 판정 · CRM_CONTACT 선택(비DEV 에서 없어도 기동) · app.html 문의 구간의 esc 정적 검사."""
    import subprocess
    for ua, fam in (("", ""), (None, ""), ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", "edge"),
                    ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", "firefox"),
                    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/130.0 Mobile/15E148 Safari/604.1", "chrome"),
                    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15", "safari"),
                    ("curl/8.7.1", "other")):
        assert web.ua_family(ua) == fam, (ua, fam, web.ua_family(ua))
    # 비DEV 에서 CRM_CONTACT 는 선택 — 없음·공백만·있음 모두 import 성공
    env = {k: v for k, v in os.environ.items() if k not in {"CRM_DEV", "CRM_DEV_USER", "CRM_CONTACT"}}
    env["AUTHKIT_ISSUER"] = FAKE_ISSUER

    def boot(extra):
        return subprocess.run([sys.executable, "-c", "import main"], cwd=HERE, env={**env, **extra}, capture_output=True, text=True, timeout=120)
    for extra in ({}, {"CRM_CONTACT": "   "}, {"CRM_CONTACT": "help@example.com"}):
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
    i = html.index('<dialog id="desk-dlg"')   # Q1b: 문의 폼·목록은 계정 메뉴에서 여는 대화창
    sec = html[i:html.index("</dialog>", i)]
    assert "${" not in sec and db.NOTICE in sec and 'href="/privacy"' in sec, "정적 대화창 — 고지 문장·처리방침 링크"
    for w in ('value="bug"', 'value="request"', 'value="question"', 'value="privacy"', "개인정보 요청", "내 문의", "4096"):
        assert w in sec, w
    assert 'ticketStatus: {open: "접수", ack: "확인", doing: "처리중", done: "완료", wontfix: "보류"}' in html
    assert "const DESK_MAX = 4096;" in html and db.LIMIT_TICKET == 4096
    # 내 문의(P5): 상세는 본인 API 하나 · 글·답변은 esc · 이력은 textContent · 관리자 문의함·띠·옛 API 흔적 0
    for w in ('fetch("/api/feedback/" + encodeURIComponent(id)', "${esc(j.text)}", "${esc(j.reply || ", "li.textContent = ",
              "${esc(t.has_reply ? ", "${esc(md(t.public_updated)"):
        assert w in blk, w
    assert "${t." not in blk and "${j." not in blk
    for gone in ("admin-band", "deskBand", "deskAdmin", "/api/feedback/status", "/api/feedback/note", "admin_tickets", "admin_note",
                 "desk-admin", "DESK_NOTE_MAX", "j.admin"):
        assert gone not in html, ("app.html 에 관리자 문의함 흔적", gone)
    # ⚙ Setup 링크: 기본 hidden · /admin/setup · 인라인 이벤트 속성 0(CSP) · 상태가 admin 일 때만 hidden 해제
    gear = re.search(r'<a\b[^>]*\bid="setup-link"[^>]*>', html)
    assert gear and re.search(r"\shidden(\s|>)", gear.group(0)) and 'href="/admin/setup"' in gear.group(0), gear
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "app.html 인라인 이벤트 속성 0"
    assert '$("setup-link").hidden = !(S.user && S.user.admin);' in html
    print("OK — 문의 단위(ua 계열·CRM_CONTACT 선택·app.html esc 정적 검사) 통과")


# ── 9) α2 — 동의 게이트 · 일일 한도 · usage_daily ─────────────

def _as_user(site, email):
    """MCP(DEV 사용자)와 웹(DEV 폴백)을 같은 사용자로."""
    os.environ["CRM_DEV_USER"] = email
    site.dev, site.dev_user = True, email


def _agreements(uid):
    with db.tx() as c:
        return c.execute("SELECT kind,version FROM agreements WHERE user_id=%s ORDER BY kind", (uid,)).fetchall()


def test_consent_versions():
    """버전 파싱 — 운영에서 게이트가 소리 없이 열리지 않게. 레포 guides 기준 privacy v4."""
    import tempfile
    assert db.CONSENT_VERSIONS and db.CONSENT_VERSIONS.get("privacy") == "v4", db.CONSENT_VERSIONS
    assert web.read_consent_versions(web.GUIDES_DIR, dev=False) == db.CONSENT_VERSIONS
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        assert web.read_consent_versions(d, dev=True) == {}, "DEV: 문서가 없으면 게이트 없음(열린 채 시작)"
        try:
            web.read_consent_versions(d, dev=False)
            raise AssertionError("비DEV 는 처리방침(11-privacy.md) 없이 뜨면 안 된다")
        except SystemExit as e:
            assert str(e) == "동의 문서 11-privacy.md 가 없어요. 운영은 처리방침 없이 뜨지 않아요.", e
        (d / web.PRIVACY_FILE).mkdir()   # 읽기 오류(없음이 아님) — 같은 이름의 디렉터리
        assert web.read_consent_versions(d, dev=True) == {}, "DEV: 못 읽는 문서는 로그 + 제외"
        try:
            web.read_consent_versions(d, dev=False)
            raise AssertionError("비DEV 는 못 읽는 동의 문서로 뜨면 안 된다")
        except SystemExit as e:
            assert web.PRIVACY_FILE in str(e) and "IsADirectoryError" in str(e), e
        (d / web.PRIVACY_FILE).rmdir()
        (d / web.PRIVACY_FILE).write_text("# 처리방침\n\n버전 머리글 없음\n", encoding="utf-8")
        assert web.read_consent_versions(d, dev=True) == {}, "DEV: 버전 없는 문서는 뺀다"
        try:
            web.read_consent_versions(d, dev=False)
            raise AssertionError("비DEV 는 버전 없는 동의 문서로 뜨면 안 된다")
        except SystemExit as e:
            assert web.PRIVACY_FILE in str(e), e
        # 머리글은 줄 머리 `vN · 만 — 줄 중간 예시는 버전이 아니다 · 첫 20줄에 둘이면 비DEV 거부, DEV 는 첫 것
        (d / web.PRIVACY_FILE).write_text("# 처리방침\n\n머리글 예: `v9` 처럼 적는다.\n`v8` 은 줄 머리지만 가운뎃점이 없다.\n"
                                          "`v1 · 2027-01-01` 시행. 예: `v9` 는 예시.\n", encoding="utf-8")
        assert web.read_consent_versions(d, dev=False) == {"privacy": "v1"}
        (d / web.PRIVACY_FILE).write_text("# 처리방침\n\n`v1 · 2027-01-01` 시행.\n\n`v1 · 2027-02-01` 시행.\n", encoding="utf-8")
        try:
            web.read_consent_versions(d, dev=False)
            raise AssertionError("비DEV 는 머리글이 둘인 동의 문서로 뜨면 안 된다")
        except SystemExit as e:
            assert web.PRIVACY_FILE in str(e), e
        (d / web.PRIVACY_FILE).write_text("# 처리방침\n\n`v4 · 2027-01-01` 시행.\n\n`v5 · 2027-02-01` 시행.\n", encoding="utf-8")
        assert web.read_consent_versions(d, dev=True) == {"privacy": "v4"}, "DEV: 둘이면 첫 것"
        (d / web.PRIVACY_FILE).write_text("# 처리방침\n\n`v3 · 2027-01-01` 시행.\n", encoding="utf-8")
        (d / "12-terms.md").write_text("# 약관\n\n`v2 · 2027-01-01`\n", encoding="utf-8")
        assert web.read_consent_versions(d, dev=False) == {"privacy": "v3", "terms": "v2"}, "terms 문서가 생기면 자동으로 둘"
    print("OK — α2 처리방침 버전(guides 머리글 v4·비DEV 기동 거부·privacy 필수·읽기 오류·줄 머리 정규식·머리글 둘·terms 자동) 통과")


async def test_consent_gate():
    """필수 동의 게이트 없음: agreements 행이 없는 사용자도 MCP·웹 쓰기 통과 · /app 200 · /consent 302 /app(세션 있든 없든)
    · POST /api/consent 404 · 사용량은 그 uid 로 flush(로그인 전 요청만 '') · 코드·app.html 에 CONSENT-REQUIRED·/consent 링크 0."""
    site = main.site
    origin = {"origin": PUBLIC}
    nc = db.user_by_email(NOCONSENT)["id"]
    assert not _agreements(nc)
    real_site = (site.dev, site.dev_user)
    today = db._dt.datetime.now(db.KST).date()
    try:
        _as_user(site, NOCONSENT)
        db.usage_flush()
        u0, a0 = _usage_row(today, nc, "record_add", "OK"), _usage_row(today, "", "web:state", "UNAUTHORIZED")
        async with Client(main.mcp) as c, httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # MCP 쓰기 — 동의 행 없이 통과
            r = await c.call_tool("record_add", {"text": "동의 행 없이 기록"})
            assert not r.is_error, r.content[0].text
            r = await c.call_tool("question_add", {"text": "동의 행 없이 질문"})
            assert not r.is_error, r.content[0].text
            assert len(db.records(nc)) == 1 and len(db.questions(nc)) == 1
            # 웹 쓰기 — 403 이 아니라 게이트를 지나 db 판정(없는 후보·항목 → 400)
            r = await h.post("/api/candidate/decide", json={"candidate_id": "c_none", "action": "confirm"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", r.text
            r = await h.post("/api/item/decide", json={"item_id": "i_none", "expected_revision": 0, "action": "confirm"}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] != "CONSENT-REQUIRED", r.text
            # /app 200 · /consent 302 /app · /api/consent 404
            assert (await h.get("/app")).status_code == 200
            r = await h.get("/consent")
            assert r.status_code == 302 and r.headers["location"] == "/app", r.headers.get("location")
            r = await h.post("/api/consent", json={"privacy": "v3"}, headers=origin)
            assert r.status_code == 404, r.status_code
            u = (await h.get("/api/state")).json()["user"]
            assert set(u) == {"email", "struct_names", "desk_hidden"}, u
            # 세션 없음 — /consent 는 /app(→ /), 로그인 전 요청의 사용량은 ''
            site.dev, site.dev_user = False, ""
            r = await h.get("/consent")
            assert r.status_code == 302 and r.headers["location"] == "/app", r.headers.get("location")
            assert (await h.get("/api/state")).status_code == 401
        assert not _agreements(nc), "agreements 는 새로 쓰지 않는다"
        db.usage_flush()
        assert _usage_row(today, nc, "record_add", "OK") == u0 + 1, "동의 행 없는 로그인 사용자 사용량은 그 uid 로"
        assert _usage_row(today, "", "web:state", "UNAUTHORIZED") == a0 + 1, "로그인 전 요청만 ''"
    finally:
        site.dev, site.dev_user = real_site
        os.environ["CRM_DEV_USER"] = TESTER
    # 정적: 코드·화면에 CONSENT-REQUIRED·/consent 링크 0(옛 주소 302 라우트만 남는다)
    for f in [*HERE.glob("*.py"), *HERE.glob("*.html")]:
        if f.name == "test_tools.py":
            continue
        t = f.read_text(encoding="utf-8")
        assert "CONSENT-REQUIRED" not in t and "/api/consent" not in t and "consent_grant" not in t, f.name
        hits = [ln for ln in t.splitlines() if "/consent" in ln]
        assert hits == ([] if f.name != "web.py" else ['            Route("/consent", self.consent),']), (f.name, hits)
    assert not hasattr(db, "require_consent") and not hasattr(db, "consent_grant") and not hasattr(db, "CONSENT_EXEMPT")
    print("OK — 필수 동의 게이트 없음(동의 행 없이 MCP·웹 쓰기·/app 200·/consent→/app·/api/consent 404·사용량 그 uid·로그인 전 ''·정적 0) 통과")


def _bulk(sql, args):
    with db.tx() as c:
        c.execute(sql, args)


async def test_daily_limits():
    """일일 한도 — 종류마다 전용 사용자(앞 시험이 쌓은 행과 섞이지 않게). 경계 = 상한까지 통과, 하나 더면 RATE-LIMIT."""
    site = main.site
    U = {k: db.user_by_email(LIM_EMAIL[k])["id"] for k in LIM_KINDS}
    assert db.DAILY_LIMITS == {"records": 50, "candidates": 150, "decisions": 300, "proposals": 100, "questions": 50}
    try:
        # 기록 50 — 관리자도 같다
        u = U["records"]
        _set_role(u, "admin")
        for i in range(49):
            db.record_add(u, f"한도 {i}", f"기록 {i}")
        r50 = db.record_add(u, "한도 50", "50번째 기록")
        e = expect("RATE-LIMIT", db.record_add, u, "한도 51", "51번째 기록")
        assert e.message == "오늘 기록은 50개까지예요." and "KST" in e.action, (e.message, e.action)
        _as_user(site, LIM_EMAIL["records"])
        async with Client(main.mcp) as c:   # MCP 도 같은 판정(관리자 uid)
            r = await c.call_tool("record_add", {"text": "MCP 51번째"})
            assert r.is_error and "[RATE-LIMIT] 오늘 기록은 50개까지예요." in r.content[0].text, r.content[0].text
        assert len(db.records(u, limit=1000)) == 50
        # 날짜 경계(_KST_TODAY): 하나를 어제로 옮기면 오늘 카운트에서 빠진다
        _bulk("UPDATE records SET created = created - interval '1 day' WHERE user_id=%s AND id=%s", (u, r50["id"]))
        db.record_add(u, "어제로 옮긴 뒤", "다시 50번째")
        expect("RATE-LIMIT", db.record_add, u, "다시 51", "다시 51번째")
        _set_role(u, "user")

        # 질문 50
        u = U["questions"]
        for i in range(50):
            db.question_add(u, f"질문 {i}")
        e = expect("RATE-LIMIT", db.question_add, u, "질문 51")
        assert e.message == "오늘 질문은 50개까지예요.", e.message

        # 후보 150 — 묶음 단위(제출 수로): 148 + 3 거절, 147 + 3 통과
        u = U["candidates"]
        src = db.record_add(u, "후보 원천", "x" * 200)
        _bulk("INSERT INTO candidates (id,user_id,question_id,record_id,text,quote,quote_start,quote_end,origin) "
              "SELECT 'c_lim' || g, %s, NULL, %s, '후보', 'x', g, g + 1, 'ai' FROM generate_series(1, 148) AS g", (u, src["id"]))
        rec = db.record_add(u, "새 기록", "하나 둘 셋 넷")
        three = [{"text": "하나", "quote": "하나"}, {"text": "둘", "quote": "둘"}, {"text": "셋", "quote": "셋"}]
        e = expect("RATE-LIMIT", db.candidates_propose, u, rec["id"], "", three, "ai")
        assert e.message == "오늘 후보는 150개까지예요.", e.message
        _bulk("DELETE FROM candidates WHERE user_id=%s AND id='c_lim1'", (u,))
        added, dropped = db.candidates_propose(u, rec["id"], "", three, "ai")
        assert len(added) == 3 and not dropped, (added, dropped)
        expect("RATE-LIMIT", db.candidates_propose, u, rec["id"], "", [{"text": "넷", "quote": "넷"}], "ai")

        # 결정 300 — 후보 이력 + 항목 이력의 합. candidate_decide·item_decide 둘 다
        u = U["decisions"]
        rec = db.record_add(u, "결정 원천", "결정할 문장이 여기 있다")
        cid = db.candidates_propose(u, rec["id"], "", [{"text": "결정", "quote": "결정할 문장"}], "ai")[0][0]
        _bulk("INSERT INTO candidate_history (user_id,candidate_id,action,prev_revision,prev_state) "
              "SELECT %s, %s, 'confirm', 1000 + g, '{\"schema_version\":2,\"text\":\"x\",\"decision\":\"pending\"}'::jsonb "
              "FROM generate_series(1, 299) AS g", (u, cid))
        assert db.candidate_decide(u, cid, "confirm")["status"] == "confirmed"   # 300번째
        e = expect("RATE-LIMIT", db.candidate_decide, u, cid, "exclude")
        assert e.message == "오늘 결정은 300개까지예요.", e.message
        expect("RATE-LIMIT", db.item_decide, u, "i_none", 0, None, "confirm")   # 항목 결정도 같은 한도(조회 전에 거절)
        assert db.candidate_get(u, cid)["status"] == "confirmed"
        # edit_confirm 은 이력 2줄 — 299 에서 거절, 298 에서 통과(이력 300)
        def n_hist():
            with db.tx() as c:
                return c.execute(f"SELECT count(*) AS n FROM candidate_history WHERE user_id=%s AND at >= {db._KST_TODAY}", (u,)).fetchone()["n"]
        assert n_hist() == 300, n_hist()
        _bulk("DELETE FROM candidate_history WHERE id = (SELECT min(id) FROM candidate_history WHERE user_id=%s AND prev_revision > 1000)", (u,))
        assert n_hist() == 299
        e = expect("RATE-LIMIT", db.candidate_decide, u, cid, "edit_confirm", "결정 고침")
        assert e.message == "오늘 결정은 300개까지예요.", e.message
        assert n_hist() == 299 and db.candidate_get(u, cid)["text"] == "결정"
        _bulk("DELETE FROM candidate_history WHERE id = (SELECT min(id) FROM candidate_history WHERE user_id=%s AND prev_revision > 1000)", (u,))
        assert n_hist() == 298
        got = db.candidate_decide(u, cid, "edit_confirm", "결정 고침")
        assert got["status"] == "confirmed" and got["text"] == "결정 고침", got
        assert n_hist() == 300, n_hist()

        # 제안 100 — 오브젝트 정의 + 항목. 새 행을 만드는 경로만 센다(기존 오브젝트의 draft 갱신은 안 센다)
        u = U["proposals"]
        _bulk("INSERT INTO object_defs (user_id,id,key,label,semantic_kind) "
              "SELECT %s, 'od_lim' || g, 'lim' || g, '한도', 'collection' FROM generate_series(1, 99) AS g", (u,))
        d100 = {"key": "lim_last", "label": "100번째", "semantic_kind": "collection", "fields": []}
        p = db.definition_propose(u, None, 0, d100)   # 100번째 새 행
        e = expect("RATE-LIMIT", db.definition_propose, u, None, 0, {**d100, "key": "lim_over"})
        assert e.message == "오늘 제안은 100개까지예요.", e.message
        db.definition_propose(u, None, 0, {**d100, "label": "같은 key 다시"})   # 적용 전 같은 key 행의 제안 바꾸기 — 새 행 아님
        db.definition_apply(u, p["object_id"], db.definition_propose(u, p["object_id"], 0, d100)["proposal_id"], 0)
        db.definition_propose(u, p["object_id"], 1, {**d100, "label": "고친 정의"})   # 기존 오브젝트 draft 갱신 — 안 센다
        e = expect("RATE-LIMIT", db.item_propose, u, p["object_id"], 1, None, 0, None, "새 항목", "", {}, [])
        assert e.message == "오늘 제안은 100개까지예요.", e.message

        # 합산 소스: 정의 층 시험 사용자(def1)의 오늘 결정·제안 = 두 테이블 합 — 상한을 그 수로 바꿔 경계를 본다
        real_limits = dict(db.DAILY_LIMITS)
        d1 = db.user_by_email(DEF1)["id"]
        with db.tx() as c:
            cnt = lambda t, col: c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s AND {col} >= {db._KST_TODAY}", (d1,)).fetchone()["n"]
            n_dec = cnt("candidate_history", "at") + cnt("item_history", "at")
            n_prop = cnt("object_defs", "created_at") + cnt("items", "created_at")
            assert cnt("item_history", "at") > 0 and cnt("items", "created_at") > 0, "def1 에 항목 이력·항목이 있어야 합산을 본다"
        try:
            for kind, n in (("decisions", n_dec), ("proposals", n_prop)):
                db.DAILY_LIMITS[kind] = n
                with db.tx() as c:
                    db._daily_gate(c, d1, kind, 0)
                try:
                    with db.tx() as c:
                        db._daily_gate(c, d1, kind, 1)
                    raise AssertionError(f"{kind}: 두 테이블 합을 세지 않았다")
                except db.CrmError as e:
                    assert e.code == "RATE-LIMIT", e.code
        finally:
            db.DAILY_LIMITS.clear()
            db.DAILY_LIMITS.update(real_limits)
        # 사용자 행이 없으면 AUTH-INVALID
        expect("AUTH-INVALID", db.record_add, "u_no_such_user", "t", "b")
    finally:
        _set_role(U["records"], "user")
        site.dev, site.dev_user = True, TESTER
        os.environ["CRM_DEV_USER"] = TESTER
    print("OK — α2 일일 한도(기록 50·질문 50·후보 148+3/147+3·결정 300·edit_confirm 2줄 299 거절/298 통과·제안 100·관리자 같음·KST 날짜 경계·합산) 통과")


def _usage_row(day, uid, name, code):
    with db.tx() as c:
        r = c.execute("SELECT n FROM usage_daily WHERE day=%s AND user_id=%s AND name=%s AND code=%s", (day, uid, name, code)).fetchone()
    return r["n"] if r else 0


async def test_usage():
    """usage_daily — 요청 경로는 메모리만 · MCP 모든 도구·웹 /api/* 래퍼 · flush 합산·접기·실패 버림 · lifespan 종료 순서."""
    site = main.site
    today = db._dt.datetime.now(db.KST).date()
    tester = db.user_by_email(TESTER)["id"]
    _as_user(site, TESTER)
    snap = db.usage_snapshot
    # 요청 경로 풀 checkout 0회
    db.pool()
    before = db.pool().get_stats().get("requests_num", 0)
    for _ in range(50):
        db.usage_count(tester, "state_get", "OK")
    assert db.pool().get_stats().get("requests_num", 0) == before, "usage_count 가 DB 를 건드렸다"

    def delta(s0, key):
        return snap().get(key, 0) - s0.get(key, 0)

    async with Client(main.mcp) as c, httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
        s0 = snap()
        assert not (await c.call_tool("record_add", {"text": "집계 시험 기록"})).is_error
        assert (await c.call_tool("candidate_decide", {"candidate_id": "c_nope", "action": "confirm"})).is_error
        assert not (await c.call_tool("help_get", {"topic": "words"})).is_error
        assert not (await c.call_tool("state_get", {})).is_error
        rid = db.record_add(tester, "집계", "가 나 다 라")["id"]
        r = await c.call_tool("candidates_propose", {"record_id": rid, "question_id": "",
                                                     "candidates": [{"text": t, "quote": t} for t in ("가", "나", "다", "라")]})
        assert r.is_error   # 4개 — 도구 본문의 ToolError(CrmError 아님)
        assert delta(s0, (today, tester, "record_add", "OK")) == 1
        assert delta(s0, (today, tester, "candidate_decide", "ARG-INVALID")) == 1
        assert delta(s0, (today, tester, "state_get", "OK")) == 1
        assert delta(s0, (today, "", "help_get", "OK")) == 1, "help_get 은 uid 를 풀지 않는다 — 추가 조회 없이 ''"
        assert delta(s0, (today, tester, "candidates_propose", "EXC")) == 1, "쓰기 도구는 게이트(write_gate)에서 uid 를 이미 풀었다"
        # 웹: 경로 이름 · 응답 code · 401 은 '' · 200 본문의 code(항목 코드)는 읽지 않는다
        s0 = snap()
        assert (await h.get("/api/state")).status_code == 200
        assert (await h.get("/api/journal", params={"id": "r_none"})).status_code == 404
        r = await h.post("/api/candidate/decide", json={"candidate_id": "c_none", "action": "confirm"})   # Origin 없음
        assert r.status_code == 403
        site.dev, site.dev_user = False, ""
        assert (await h.get("/api/state")).status_code == 401
        site.dev, site.dev_user = True, TESTER
        assert delta(s0, (today, tester, "web:state", "OK")) == 1
        assert delta(s0, (today, tester, "web:journal", "RECORD-MISSING")) == 1
        assert delta(s0, (today, tester, "web:candidate_decide", "ORIGIN")) == 1
        assert delta(s0, (today, "", "web:state", "UNAUTHORIZED")) == 1
    names = {k[2] for k in snap()}
    codes = {k[3] for k in snap()}
    assert all(re.fullmatch(r"[a-z_]+|web:[a-z_]+", n) for n in names), names
    assert all(re.fullmatch(r"OK|EXC|[A-Z][A-Z-]*", cd) for cd in codes), codes

    # flush: 합산 upsert · 두 번이면 n 합산 · 로그인 전(uid 없음·'') 요청만 '' — 로그인 사용자는 동의 행과 무관하게 그 uid
    db.usage_flush()
    assert snap() == {}, "flush 는 스냅샷을 뗀다"
    t0, a0 = _usage_row(today, tester, "record_add", "OK"), _usage_row(today, "", "state_get", "OK")
    for _ in range(2):
        db.usage_count(tester, "record_add", "OK")
    db.usage_count(None, "state_get", "OK")
    db.usage_count("", "state_get", "OK")
    assert db.usage_flush() == 2, "None 과 '' 은 같은 키"
    assert _usage_row(today, tester, "record_add", "OK") == t0 + 2
    assert _usage_row(today, "", "state_get", "OK") == a0 + 2
    for _ in range(3):
        db.usage_count(tester, "record_add", "OK")
    db.usage_flush()
    assert _usage_row(today, tester, "record_add", "OK") == t0 + 5, "두 번째 flush 는 n 에 더한다"
    # 처리방침 버전이 없어도(동의 문서 없음) 로그인 사용자는 그 uid — 접기 없음
    real_v = dict(db.CONSENT_VERSIONS)
    try:
        db.set_consent_versions({})
        a1, t1 = _usage_row(today, "", "record_get", "OK"), _usage_row(today, tester, "record_get", "OK")
        db.usage_count(tester, "record_get", "OK")
        db.usage_flush()
        assert _usage_row(today, "", "record_get", "OK") == a1 and _usage_row(today, tester, "record_get", "OK") == t1 + 1
    finally:
        db.set_consent_versions(real_v)
    # 풀 checkout 실패 → 묶음 버림, 예외 없음
    real_conn = db._usage_conn

    def boom():
        raise RuntimeError("checkout 실패 주입")
    q0 = _usage_row(today, tester, "question_list", "OK")
    try:
        db._usage_conn = boom
        db.usage_count(tester, "question_list", "OK")
        assert db.usage_flush() == 0
        assert snap() == {}, "실패한 묶음은 버린다(다시 쌓지 않는다)"
    finally:
        db._usage_conn = real_conn
    assert _usage_row(today, tester, "question_list", "OK") == q0

    # lifespan: 주기 flush 가 돌고, 종료 때 태스크 취소 → flush 1회 → db.close() 순서
    order = []
    real_flush, real_close, real_every = db.usage_flush, db.close, main.USAGE_FLUSH_EVERY
    sm = main.mcp.session_manager

    @asynccontextmanager
    async def fake_run():   # 세션 매니저 run() 은 인스턴스당 한 번 — test_http 가 이미 썼다
        yield

    try:
        db.usage_flush = lambda: order.append("flush") or 0
        db.close = lambda: order.append("close")
        main.USAGE_FLUSH_EVERY = 0.01
        sm.run = fake_run
        async with main.lifespan(main.app):
            for _ in range(100):
                if order:
                    break
                await asyncio.sleep(0.01)
            assert order and set(order) == {"flush"}, ("5분 주기 태스크가 돌지 않았다", order)
            main.USAGE_FLUSH_EVERY = 3600
            await asyncio.sleep(0.05)   # 돌던 주기를 긴 수면으로 넘긴다
            n_periodic = len(order)
        assert order[n_periodic:] == ["flush", "close"], order
    finally:
        db.usage_flush, db.close, main.USAGE_FLUSH_EVERY = real_flush, real_close, real_every
        del sm.run
    print("OK — α2 usage_daily(메모리만·풀 checkout 0·MCP 전 도구·웹 래퍼·flush 합산/그 uid·로그인 전만 ''/실패 버림·종료 순서 flush→close) 통과")


def test_alpha2_delete():
    """user_delete 뒤 usage_daily·agreements 에 그 uid 행 0."""
    nc = db.user_by_email(NOCONSENT)["id"]
    db.usage_count(nc, "record_add", "OK")
    db.usage_flush()
    today = db._dt.datetime.now(db.KST).date()
    assert _usage_row(today, nc, "record_add", "OK") >= 1, "로그인 사용자는 동의 행이 없어도 자기 uid 로 집계된다"
    db.user_delete(nc)
    with db.tx() as c:
        left = {t: c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s", (nc,)).fetchone()["n"] for t in ("usage_daily", "agreements")}
    assert left == {"usage_daily": 0, "agreements": 0}, left
    print("OK — α2 계정 삭제(usage_daily·agreements 0) 통과")


def test_alpha2_delete_lock():
    """usage_flush 가 _flush_lock 을 잡은(INSERT 직전) 동안 user_delete 는 블록된다 — flush 가 올린 그 uid 행을 delete 가 지운다.
    user_delete 뒤 메모리 _usage 에 그 uid 키 0."""
    import threading
    from contextlib import contextmanager
    uid = db.user_by_email(LIM_EMAIL["records"])["id"]
    today = db._dt.datetime.now(db.KST).date()
    db.usage_flush()
    entered, go, errs = threading.Event(), threading.Event(), []
    real_conn = db._usage_conn

    @contextmanager
    def held_conn():   # flush 가 잠금을 잡고 INSERT 직전에서 멈춘다(풀 연결은 아직 안 잡는다)
        entered.set()
        assert go.wait(10), "go 신호가 오지 않았다"
        with real_conn() as c:
            yield c

    def run(fn, *a):
        try:
            fn(*a)
        except BaseException as e:   # noqa: BLE001 — 스레드 예외를 본 스레드로
            errs.append(e)

    db.usage_count(uid, "record_add", "OK")
    try:
        db._usage_conn = held_conn
        tf = threading.Thread(target=run, args=(db.usage_flush,))
        tf.start()
        assert entered.wait(5), "flush 가 INSERT 단계에 오지 않았다"
        assert db._flush_lock.locked()
        db.usage_count(uid, "state_get", "OK")   # 삭제 전 메모리에 남은 키 — delete 가 지워야 한다
        td = threading.Thread(target=run, args=(db.user_delete, uid))
        td.start()
        td.join(0.5)
        assert td.is_alive(), "flush 가 잠금을 잡은 동안 user_delete 가 블록되지 않았다"
        go.set()
        tf.join(10)
        td.join(10)
        assert not tf.is_alive() and not td.is_alive(), "flush·delete 가 끝나지 않았다"
    finally:
        go.set()
        db._usage_conn = real_conn
    assert not errs, errs
    assert not [k for k in db.usage_snapshot() if k[1] == uid], "user_delete 뒤 메모리 _usage 에 그 uid 키가 남았다"
    assert _usage_row(today, uid, "record_add", "OK") == 0, "flush 가 올린 삭제 uid 행이 남았다"
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM users WHERE id=%s", (uid,)).fetchone()["n"] == 0
    print("OK — α2 삭제 잠금(flush 가 잠금 잡은 동안 user_delete 블록·삭제 uid 행 0·메모리 키 0) 통과")


# ── 10) β2 — 관리자 역할 · 감사 · 웹 끊기 · 차단 · Setup (재인증 없음) ─────────

B2 = {k: f"b2{k}@example.com" for k in ("a", "b", "c", "m")}   # a·b = 관리자 후보, c = 사용자, m = MCP(차단 시험)
B2_XSS = "<b>esc</b>@example.com"


def _audit_rows(target=None, action=None):
    with db.tx() as c:
        return c.execute("SELECT action,actor_id,target_id,detail,at FROM admin_audit WHERE (%s::text IS NULL OR target_id=%s) "
                         "AND (%s::text IS NULL OR action=%s) ORDER BY id", (target, target, action, action)).fetchall()


def _urow(uid):
    with db.tx() as c:
        return c.execute("SELECT role,blocked_at,web_revoked_before,mcp_revoked_before FROM users WHERE id=%s", (uid,)).fetchone()


def _sql(sql, args=()):
    with db.tx() as c:
        c.execute(sql, args)


def _tok(sub, iat="now"):
    """auth.user_id_for 에 넣을 검증 끝난 토큰 모양. iat=None 이면 iat 클레임 없음."""
    from mcp.server.auth.provider import AccessToken
    now = int(time.time())
    claims = {"sub": sub, "iss": FAKE_ISSUER, "aud": RESOURCE, "exp": now + 600}
    if iat is not None:
        claims["iat"] = now if iat == "now" else iat
    return AccessToken(token="t", client_id="client_test", scopes=["openid", "email"], expires_at=now + 600,
                       resource=RESOURCE, subject=sub, claims=claims)


async def test_beta2():
    """β2: /admin 404(무세션·비관리자·DEV) · 재인증 없음(ra 없이 화면·POST, /admin/reauth→/admin, 콜백→/app, 옛 ra 무시) · 쿠키 v2(옛 쿠키·끊기·차단·DB 오류) · 부여·회수·LAST-ADMIN·동시 회수
    · 차단/해제(웹·MCP) · 웹 끊기는 MCP 무관 · /app 관리자 경로 창 · CSP·이스케이프 · CLI · 감사 파기 · lifespan 배선."""
    import datetime
    import threading
    from urllib.parse import parse_qs, urlparse
    import admin_cli
    _is_int = web._is_int
    site = main.site
    verifier = main._auth_kwargs["token_verifier"]
    fake_jwks(verifier)
    A = db.user_by_subject("b2a_sub", B2["a"])["id"]
    B = db.user_by_subject("b2b_sub", B2["b"])["id"]
    C = db.user_by_subject("b2c_sub", B2["c"])["id"]
    M = db.user_by_subject("b2m_sub", B2["m"])["id"]
    X = db.user_by_email(B2_XSS)["id"]
    with db.tx() as c:   # 시험 밖 관리자(이전 실행·bootstrap)는 잠깐 내린다 — LAST-ADMIN·동시 회수는 전역 관리자 수를 본다
        others = [r["id"] for r in c.execute("SELECT id FROM users WHERE role='admin' AND NOT (email = ANY(%s))",
                                             (list(TEST_EMAILS),)).fetchall()]
        c.execute("UPDATE users SET role='user' WHERE id = ANY(%s)", (others,))
    _set_role(A, "admin")
    real = (site.dev, site.dev_user, site.client_id, site.token_post, db.session_user)
    site.dev, site.dev_user, site.client_id = False, "", "client_test"
    now = lambda: int(time.time())   # noqa: E731
    ck = lambda uid, iat=None: site.sign({"uid": uid, "iat": now() if iat is None else iat}, 600)   # noqa: E731
    ck_ra = lambda uid, ra: site.sign({"uid": uid, "iat": now(), "ra": ra}, 600)   # 옛 쿠키 — ra 는 무시돼야 한다  # noqa: E731

    async def call(method, path, cookie=None, origin=True, data=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.request(method, path, headers={"origin": PUBLIC} if origin else {}, data=data)

    tok_next = {"sub": "b2a_sub", "extra": None}

    async def fake_token_post(data):
        return 200, {"access_token": make_token(sub=tok_next["sub"], extra=tok_next["extra"]), "token_type": "Bearer"}
    site.token_post = fake_token_post

    async def login(cookie, reauth, sub, extra=None):
        """쿠키 세션으로 /login(옛 링크 ?fresh=1&reauth=1 포함) → 콜백. (콜백 응답, 새 세션 쿠키 내용)."""
        tok_next.update(sub=sub, extra=extra)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            r = await h.get("/login", params={"fresh": "1", "reauth": "1"} if reauth else {})
            q = parse_qs(urlparse(r.headers["location"]).query)
            if reauth:
                assert q.get("prompt") == ["login"], q
            r = await h.get("/auth/callback", params={"code": "c", "state": q["state"][0]})
            return r, site.unsign(r.cookies.get("crm_session"))

    posts = (("/admin/role", {"uid": C, "role": "admin"}), ("/admin/signout", {"uid": C}), ("/admin/block", {"uid": C}),
             ("/admin/unblock", {"uid": C}))
    try:
        # ── 404: 무세션 · 비관리자 · DEV ──
        for cookie in (None, ck_ra(C, now()), ck(C)):
            for path in ("/admin", "/admin/setup", "/admin/reauth"):
                r = await call("GET", path, cookie)
                assert r.status_code == 404 and "<" not in r.text, (path, r.status_code, r.text[:80])
            for path, data in posts:
                r = await call("POST", path, cookie, data=data)
                assert r.status_code == 404, (path, r.status_code)
        site.dev, site.dev_user = True, B2["a"]   # DEV: 관리자 dev_user 여도, 관리자 쿠키가 있어도 404
        try:
            for cookie in (None, ck(A)):
                for path in ("/admin", "/admin/setup", "/admin/reauth"):
                    assert (await call("GET", path, cookie)).status_code == 404, ("DEV", path)
                for path, data in posts:
                    assert (await call("POST", path, cookie, data=data)).status_code == 404, ("DEV", path)
        finally:
            site.dev, site.dev_user = False, ""
        assert _urow(C)["role"] == "user" and not _audit_rows(C), "404 경로는 아무것도 안 바꾼다"

        # ── 재인증 없음 — 관리자 세션이면 화면·POST 바로 ──
        for path in ("/admin", "/admin/setup", "/admin/tickets", "/admin/schema", "/admin/reports"):
            r = await call("GET", path, ck(A))
            assert r.status_code == 200, (path, r.status_code, r.headers.get("location"))
            assert "재인증" not in r.text and "다시 로그인" not in r.text and "/admin/reauth" not in r.text, (path, "재인증 문구 0")
        r = await call("GET", "/admin/reauth", ck(A))
        assert r.status_code == 302 and r.headers["location"] == "/admin", r.headers.get("location")
        for ra in (now() - 13 * 3600, now() + 60, "x", True):   # 옛 쿠키의 ra — 값이 무엇이든 무시(무효로 만들지 않는다)
            r = await call("GET", "/admin/setup", ck_ra(A, ra))
            assert r.status_code == 200, ("옛 ra", ra, r.status_code)
            assert (await call("GET", "/api/state", ck_ra(A, ra))).status_code == 200, ("옛 ra /api", ra)
        r = await call("GET", "/admin/setup", ck(A))
        assert "<th>차단</th>" in r.text and "처리방침" not in r.text and "미동의" not in r.text, "사용자 표에 처리방침 동의 열 없음"
        # 콜백은 항상 /app, 쿠키 = {uid, iat, exp}. 옛 링크(reauth=1)·옛 ra 쿠키·auth_time 클레임도 같다
        for cookie, reauth, sub, extra in ((ck_ra(A, now()), True, "b2a_sub", None), (ck(A), True, "b2c_sub", None),
                                           (ck(A), True, "b2a_sub", {"auth_time": now() - 3600}), (ck_ra(A, now()), False, "b2a_sub", None),
                                           (None, True, "b2a_sub", None), (None, False, "b2a_sub", None)):
            r, s = await login(cookie, reauth, sub, extra)
            assert r.status_code == 302 and r.headers["location"] == "/app", (sub, reauth, r.headers.get("location"))
            assert set(s) == {"uid", "iat", "exp"} and s["uid"] == (C if sub == "b2c_sub" else A), s
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:   # flow 쿠키에 r·t0 없음
            h.cookies.set("crm_session", ck(A))
            r = await h.get("/login", params={"fresh": "1", "reauth": "1"})
            flow = site.unsign(r.cookies.get(web.FLOW_COOKIE))
            assert r.status_code == 302 and set(flow) == {"v", "s", "exp"}, flow
        assert not hasattr(web, "reauth_ok") and not hasattr(web, "REAUTH_WINDOWS") and not hasattr(web, "AUTH_TIME_SKEW")
        assert not hasattr(web.Web, "_reauth_fresh")
        code = "\n".join(Path(__file__).with_name(n).read_text(encoding="utf-8") for n in
                         ("web.py", "admin_db.py", "db.py", "auth.py", "main.py", "admin_cli.py", "app.html", "start.html"))
        for word in ("REAUTH", "reauth_ok", "auth_time", "_reauth_fresh", "state.ra", "재인증"):
            assert word not in code, f"정적: {word} 0"

        # ── 쿠키 v2 ──
        assert (await call("GET", "/api/state", site.sign({"uid": A}, 600))).status_code == 401, "옛 쿠키(iat 없음) 무효"
        assert (await call("GET", "/api/state", ck(A, iat=now() + 30))).status_code == 401, "iat 미래 무효"
        assert (await call("GET", "/api/state", site.sign({"uid": A, "iat": str(now())}, 600))).status_code == 401, "iat 정수 아님"
        assert (await call("GET", "/api/state", ck("u_no_such_user"))).status_code == 401, "사용자 없음"
        _sql("UPDATE users SET web_revoked_before = now() - interval '100 seconds' WHERE id=%s", (C,))
        assert (await call("GET", "/api/state", ck(C, iat=now() - 200))).status_code == 401, "끊은 시각 전 iat 무효"
        assert (await call("GET", "/api/state", ck(C, iat=now() - 50))).status_code == 200, "끊은 시각 뒤 iat 유효"
        _sql("UPDATE users SET blocked_at = now() WHERE id=%s", (C,))
        assert (await call("GET", "/api/state", ck(C))).status_code == 401, "차단 사용자 쿠키 무효"
        _sql("UPDATE users SET blocked_at = NULL, web_revoked_before = NULL WHERE id=%s", (C,))

        def boom(uid):
            raise RuntimeError("DB 오류 주입")
        db.session_user = boom
        try:
            assert (await call("GET", "/api/state", ck(C))).status_code == 401, "DB 오류 → 세션 없음"
            assert (await call("GET", "/admin/setup", ck(A, now()))).status_code == 404, "DB 오류 → /admin 도 세션 없음"
        finally:
            db.session_user = real[4]
        assert (await call("GET", "/api/state", ck(C))).status_code == 200

        # ── 권한: 부여·회수·감사 ──
        fresh = lambda: ck(A, now())   # noqa: E731
        for hd_origin in (False,):
            r = await call("POST", "/admin/role", fresh(), origin=hd_origin, data={"uid": C, "role": "admin"})
            assert r.status_code == 403 and "ORIGIN" in r.text, (r.status_code, r.text[:200])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", fresh())
            r = await h.post("/admin/role", headers={"origin": "https://evil.example"}, data={"uid": C, "role": "admin"})
            assert r.status_code == 403 and "ORIGIN" in r.text, r.status_code
            r = await h.post("/admin/role", headers={"origin": PUBLIC}, json={"uid": C, "role": "admin"})
            assert r.status_code == 400 and "ARG-INVALID" in r.text, "form 만"
            r = await h.post("/admin/role", headers={"origin": PUBLIC}, data={"uid": C, "role": "root"})
            assert r.status_code == 400, "role 은 admin·user 만"
        assert _urow(C)["role"] == "user" and not _audit_rows(C), "Origin 없음/다름·틀린 form → 변화 없음"
        c_cookie_old = ck(C, iat=now() - 5)
        r = await call("POST", "/admin/role", fresh(), data={"uid": C, "role": "admin"})
        assert r.status_code == 303 and r.headers["location"] == "/admin/setup", (r.status_code, r.text[:200])
        g = _audit_rows(C, "role_grant")
        assert _urow(C)["role"] == "admin" and len(g) == 1 and g[0]["actor_id"] == A and g[0]["detail"] == {"from": "user", "to": "admin"}, g
        assert (await call("POST", "/admin/role", fresh(), data={"uid": C, "role": "admin"})).status_code == 303
        assert len(_audit_rows(C)) == 1, "이미 admin 부여 → 무동작(감사 없음)"
        assert (await call("GET", "/api/state", c_cookie_old)).status_code == 200
        r = await call("POST", "/admin/role", fresh(), data={"uid": C, "role": "user"})
        rv = _audit_rows(C, "role_revoke")
        assert r.status_code == 303 and _urow(C)["role"] == "user" and len(rv) == 1 and rv[0]["detail"] == {"from": "admin", "to": "user"}, rv
        assert _urow(C)["web_revoked_before"] is not None and (await call("GET", "/api/state", c_cookie_old)).status_code == 401, "회수 대상 기존 쿠키 무효"
        # 마지막 관리자 회수 거절(자기 자신)
        r = await call("POST", "/admin/role", fresh(), data={"uid": A, "role": "user"})
        assert r.status_code == 409 and "LAST-ADMIN" in r.text and _urow(A)["role"] == "admin", (r.status_code, r.text[:200])
        e = expect("LAST-ADMIN", db.admin_act, A, "role", A, "user")
        assert e.code == "LAST-ADMIN" and not _audit_rows(A, "role_revoke")
        r = await call("POST", "/admin/role", fresh(), data={"uid": "u_nope", "role": "admin"})
        assert r.status_code == 404 and "NOT-FOUND" in r.text
        expect("FORBIDDEN", db.admin_act, C, "role", C, "admin")   # 실행자 재조회 — 비관리자는 트랜잭션 안에서도 거절
        # 동시 회수 2건(A→B, B→A) → 관리자 0명이 되지 않는다
        for i in range(5):
            _sql("UPDATE users SET role='admin' WHERE id = ANY(%s)", ([A, B],))
            bar, out = threading.Barrier(2), {}

            def go(actor, target):
                bar.wait()
                try:
                    out[actor] = db.admin_act(actor, "role", target, "user")
                except db.CrmError as e:
                    out[actor] = e.code
            ts = [threading.Thread(target=go, args=(A, B)), threading.Thread(target=go, args=(B, A))]
            [t.start() for t in ts]
            [t.join(10) for t in ts]
            with db.tx() as c:
                n = c.execute("SELECT count(*) AS n FROM users WHERE role='admin'").fetchone()["n"]
            assert n == 1 and sorted(map(str, out.values())) == ["FORBIDDEN", "{'changed': True}"], (i, n, out)
        _sql("UPDATE users SET role = CASE WHEN id=%s THEN 'admin' ELSE 'user' END WHERE id = ANY(%s)", (A, [A, B]))
        time.sleep(1.1)   # 동시 회수에서 A 가 회수됐으면 web_revoked_before 가 방금 — 같은 초에 만든 쿠키는 무효(닫는 쪽)

        # ── 차단·해제·웹 끊기 (웹 + MCP) ──
        m_cookie = ck(M, iat=now() - 5)
        old_tok = _tok("b2m_sub", now() - 5)
        assert auth.user_id_for(old_tok) == M and (await call("GET", "/api/state", m_cookie)).status_code == 200
        r = await call("POST", "/admin/block", fresh(), data={"uid": M})
        u = _urow(M)
        assert r.status_code == 303 and u["blocked_at"] and u["web_revoked_before"] and u["mcp_revoked_before"], (r.status_code, u)
        assert [a["action"] for a in _audit_rows(M)] == ["block"]
        assert (await call("GET", "/api/state", m_cookie)).status_code == 401, "차단 → 웹 쿠키 거절"
        for t in (old_tok, _tok("b2m_sub"), _tok("b2m_sub", None)):
            expect("AUTH-BLOCKED", auth.user_id_for, t)   # iat 과거·지금·없음 모두 차단
        r, s = await login(None, False, "b2m_sub")
        assert r.status_code == 502 and s is None, "차단 계정은 웹 로그인도 안 된다"
        time.sleep(1.1)   # 새 토큰 iat(정수 초)가 mcp_revoked_before 뒤가 되게
        r = await call("POST", "/admin/unblock", fresh(), data={"uid": M})
        u = _urow(M)
        assert r.status_code == 303 and u["blocked_at"] is None and u["mcp_revoked_before"] and u["web_revoked_before"], u
        e = expect("AUTH-REVOKED", auth.user_id_for, old_tok)
        assert "커넥터를 다시 연결" in e.action, e.action
        assert auth.user_id_for(_tok("b2m_sub")) == M, "해제 뒤 새 토큰(iat 이후) 통과"
        assert auth.user_id_for(_tok("b2m_sub", None)) == M, "iat 없는 토큰은 revoked 검사 건너뜀(차단 아님)"
        assert (await call("GET", "/api/state", m_cookie)).status_code == 401, "해제해도 차단 전 쿠키는 안 살아난다"
        assert (await call("POST", "/admin/unblock", fresh(), data={"uid": M})).status_code == 303
        assert [a["action"] for a in _audit_rows(M)] == ["block", "unblock"], "이미 해제면 무동작"
        m_cookie = ck(M)
        assert (await call("GET", "/api/state", m_cookie)).status_code == 200
        mrb = _urow(M)["mcp_revoked_before"]
        time.sleep(1.1)
        r = await call("POST", "/admin/signout", fresh(), data={"uid": M})
        assert r.status_code == 303 and (await call("GET", "/api/state", m_cookie)).status_code == 401, "웹 끊기 → 쿠키 거절"
        assert _urow(M)["mcp_revoked_before"] == mrb and auth.user_id_for(_tok("b2m_sub", now() - 1)) == M, "웹 끊기는 MCP 무관"
        assert [a["action"] for a in _audit_rows(M)] == ["block", "unblock", "web_signout"]
        assert all(a["actor_id"] == A and set(a["detail"]) <= {"from", "to"} for a in _audit_rows(M))
        # 마지막 관리자 차단은 허용(복구는 CLI)
        assert db.admin_act(A, "block", A) == {"changed": True} and _urow(A)["blocked_at"] is not None
        assert (await call("GET", "/admin/setup", ck(A, now()))).status_code == 404, "차단된 관리자 → 세션 없음"
        assert not db.is_admin(A)
        time.sleep(1.1)   # 복구 뒤 새 쿠키 iat(정수 초)가 자기 차단 때의 web_revoked_before 뒤가 되게

        # ── CLI ──
        assert admin_cli.main(["grant"]) == 2 and admin_cli.main([]) == 2 and admin_cli.main(["grant", "nobody-b2@example.com"]) == 1
        assert admin_cli.main(["grant", "  B2A@example.com "]) == 0   # 소문자 정규화
        u = _urow(A)
        rc = _audit_rows(A, "recover")
        assert u["role"] == "admin" and u["blocked_at"] is None and len(rc) == 1 and rc[0]["actor_id"] is None, (u, rc)
        assert rc[0]["detail"] == {"from": "admin", "to": "admin", "unblocked": True}, rc
        assert admin_cli.main(["list"]) == 0 and A in {r["id"] for r in db.admins()}

        # ── /app 에 관리자 데이터 없음(P5) — 옛 ra 쿠키여도 같은 모양, ⚙ 링크용 admin 만 ──
        for cookie in (ck(A), ck_ra(A, now() - 13 * 3600), ck_ra(A, now())):
            st = (await call("GET", "/api/state", cookie)).json()
            assert "admin_tickets" not in st and st["user"].get("admin") is True, list(st)
            r = await call("GET", "/api/feedback", cookie)
            assert r.status_code == 200 and set(r.json()) == {"tickets"}, list(r.json())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                h.cookies.set("crm_session", cookie)
                for path in ("/api/feedback/status", "/api/feedback/note"):
                    r = await h.post(path, json={"id": "tk_nope", "status": "ack", "admin_note": "x"}, headers={"origin": PUBLIC})
                    assert r.status_code == 404, (path, r.status_code)
        assert "admin" not in (await call("GET", "/api/state", ck(C, now()))).json()["user"], "비관리자는 admin 키 없음"

        # ── /admin/setup: CSP · 스크립트 0 · 이스케이프 · 감사 표 ──
        r = await call("GET", "/admin/setup", ck(A, now()))
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
        assert "script-src 'self'" in r.headers["content-security-policy"] and "unsafe-inline" not in r.headers["content-security-policy"]
        assert "<script" not in r.text.lower() and not re.search(r"\son[a-z]+\s*=", r.text, re.I), "스크립트·이벤트 속성 0"
        assert "&lt;b&gt;esc&lt;/b&gt;@example.com" in r.text and "<b>esc" not in r.text, "이메일 이스케이프"
        for word in (B2["m"], "관리자 부여", "관리자 회수", "웹 로그인 끊기", "차단", 'action="/admin/role"', 'action="/admin/block"',
                     'name="uid"', "감사 로그", "복구(CLI)", "웹 로그인 끊기"):
            assert word in r.text, word
        for word in ("재인증", "다시 로그인", "/admin/reauth", "REAUTH", "남음"):
            assert word not in r.text, ("Setup 에 재인증 문구 0", word)
        db.admin_act(A, "signout", X)
        db.user_delete(X)   # 지워진 계정 → 감사 표엔 id 앞 8자
        r = await call("GET", "/admin/setup", ck(A, now()))
        assert X[:8] in r.text and B2_XSS not in r.text, "삭제된 계정은 id 앞 8자"
        _sql("DELETE FROM admin_audit WHERE target_id=%s", (X,))

        # ── 감사 파기: 366일 전 삭제, 364일 유지, bootstrap·recover 는 유지 ──
        _sql("INSERT INTO admin_audit (at,action,actor_id,target_id) VALUES (now() - interval '366 days','role_grant',%s,%s),"
             "(now() - interval '364 days','role_revoke',%s,%s),(now() - interval '400 days','bootstrap',NULL,%s),"
             "(now() - interval '400 days','recover',NULL,%s)", (A, C, A, C, C, C))
        assert db.admin_audit_purge() >= 1
        left = {(a["action"], (a["at"] < datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=300))) for a in _audit_rows(C)}
        assert ("role_grant", True) not in left and {("role_revoke", True), ("bootstrap", True), ("recover", True)} <= left, left
    finally:
        site.dev, site.dev_user, site.client_id, site.token_post, db.session_user = real
        site.dev, site.dev_user = True, TESTER
        _sql("UPDATE users SET role='admin' WHERE id = ANY(%s)", (others,))
    print("OK — β2 /admin 404·재인증 없음(ra 없이 화면·POST·/admin/reauth→/admin·콜백→/app·옛 ra 무시·정적 0)·쿠키 v2·끊기·차단/해제(웹·MCP)·부여/회수·LAST-ADMIN·동시 회수·CLI·/app 창·CSP·이스케이프·감사 파기 통과")


async def test_beta2_bootstrap():
    """bootstrap: 감사 행 없음 + env uid → admin + bootstrap 행 · 두 번째 → env 바꿔도 변화 없음 · env 비어도 행 · recover 가 있으면 안 함
    · lifespan 이 env 를 읽어 bootstrap·감사 파기를 부른다. 공유 시험 DB 엔 이전 bootstrap 행이 남으므로 임시 스키마에서."""
    tag = secrets.token_hex(3)
    s1, s2, s3 = f"crm_b2_boot1_{tag}", f"crm_b2_boot2_{tag}", f"crm_b2_boot3_{tag}"
    try:
        for n in (s1, s2, s3):
            create_schema(n)
        with schema_conn(s1) as c:
            db.init_schema(c)
            c.execute("INSERT INTO users (id,email) VALUES ('u_b1','b1@x.test'),('u_b2','b2@x.test')")
            c.commit()
            assert db.admin_bootstrap(["u_b1", "u_nope", " "], conn=c) == 1
            roles = {r["id"]: r["role"] for r in c.execute("SELECT id,role FROM users").fetchall()}
            rows = c.execute("SELECT action,actor_id,target_id,detail FROM admin_audit").fetchall()
            assert roles == {"u_b1": "admin", "u_b2": "user"} and rows == [{"action": "bootstrap", "actor_id": None, "target_id": None,
                                                                            "detail": {"listed": 2, "promoted": 1}}], (roles, rows)
            assert db.admin_bootstrap(["u_b2"], conn=c) is None, "두 번째 시작 — env 바꿔도 변화 없음"
            assert c.execute("SELECT role FROM users WHERE id='u_b2'").fetchone()["role"] == "user"
            assert c.execute("SELECT count(*) AS n FROM admin_audit").fetchone()["n"] == 1
        with schema_conn(s2) as c:
            db.init_schema(c)
            c.commit()
            assert db.admin_bootstrap([], conn=c) == 0
            assert [r["action"] for r in c.execute("SELECT action FROM admin_audit").fetchall()] == ["bootstrap"], "env 비어도 행"
        with schema_conn(s3) as c:
            db.init_schema(c)
            c.execute("INSERT INTO users (id,email) VALUES ('u_b3','b3@x.test')")
            c.execute("INSERT INTO admin_audit (action,target_id) VALUES ('recover','u_b3')")
            c.commit()
            assert db.admin_bootstrap(["u_b3"], conn=c) is None, "recover 행이 있으면 초기화 끝"
    finally:
        for n in (s1, s2, s3):
            drop_schema(n)
    # lifespan 배선 — env 를 시작 때 읽어 넘긴다, 파기 2종도 부른다(실패해도 기동)
    calls = []
    real = (db.admin_bootstrap, db.admin_audit_purge, os.environ.get("CRM_ADMIN_USER_IDS"))

    def fake_boot(ids, conn=None):
        calls.append(("boot", list(ids)))
        raise RuntimeError("bootstrap 실패 주입")
    db.admin_bootstrap = fake_boot
    db.admin_audit_purge = lambda: calls.append(("purge",)) or 0
    os.environ["CRM_ADMIN_USER_IDS"] = " u_x , u_y ,"
    sm = main.mcp.session_manager

    @asynccontextmanager
    async def fake_run():   # 세션 매니저 run() 은 인스턴스당 한 번 — test_http 가 이미 썼다
        yield
    sm.run = fake_run
    try:
        async with main.lifespan(main.app):
            pass
    finally:
        del sm.run
        db.admin_bootstrap, db.admin_audit_purge = real[0], real[1]
        if real[2] is None:
            os.environ.pop("CRM_ADMIN_USER_IDS", None)
        else:
            os.environ["CRM_ADMIN_USER_IDS"] = real[2]
    assert calls == [("boot", ["u_x", "u_y"]), ("purge",)], calls
    print("OK — β2 bootstrap(한 번만·env 무시·빈 env 행·recover 존중·lifespan 배선·실패해도 기동) 통과")


# ── 11) 운영 경계 (admin_db) ───────────────────────────
# 운영 화면·운영 POST 의 DB 접근은 admin_db 한 모듈. 「본문」은 운영 경로에서 SELECT 하지 않는다.
# 표식 시험이 도는 관리자 응답 — 새 /admin 페이지·리포트는 여기에 더한다(P4). (method, path, form/json)
ADB = {"admin": "adb-admin@example.com", "user": "adb-user@example.com"}
ADMIN_GETS = ("/admin", "/admin/setup", "/admin/reauth", "/api/state", "/api/feedback", "/admin/schema", "/admin/reports", "/admin/tickets",
              "/admin/users", "/admin/find", "/admin/find?q=%EA%B8%B0%EC%A4%80", "/admin/schema/objects/std-principle",
              "/admin/schema/templates/author-1", "/admin/schema/templates/author-1/objects/tpl-homework")   # PR A 새 GET(Quick Find·Object Manager 상세)
# P4: 리포트는 프리셋 전부 + 데이터셋별 차원 하나씩, 스키마는 사용자 트리(?u=)까지 — 동적 URL 이라 admin_report_urls() 가 만든다
ADB_ALLOWED_DB = {"CrmError"}   # /admin·관리자 문의 핸들러 구간에서 허용하는 db.<이름> — 예외만, 조회 함수 없음(처리방침 동의 표시는 뺐다)
ADB_BODY_COLS = re.compile(r"\b(title|body|data|doc|draft_doc|history|provenance_history|prev_state|quote|value)\b", re.I)
ADB_BODY_TABLES = re.compile(r"\b(questions|records|candidates|tabs|items)\b", re.I)
# PR #20 Q5: 연결·무결성 조회만 본문 테이블을 이름으로 부른다 — 대신 SQL 의 한정 열(별칭.열)은 상태·식별 열뿐(ADB_STATE_COLS)
ADB_STATE_KEYS = ("userview.counts", "userview.links")
ADB_STATE_COLS = {"id", "user_id", "created", "kind", "state", "version", "std_key", "record_id", "object_id", "revision", "archived_at",
                  "deleted_at", "source_item_id", "source_object_id", "source_record_id", "field_key", "target_item_id", "target_record_id"}
ADB_TEXT_COLS = re.compile(r"(?<!::)\b(text|admin_note)\b", re.I)
ADB_NAME_COLS = re.compile(r"\b(label|options)\b", re.I)
ADB_TEXT_KEYS = ("tickets.detail",)   # text·admin_note 를 읽는 유일한 조회(P5 에서 /app 문의함 이행 키를 뺐다)


def admin_report_urls():
    """리포트 GET 전부 — 프리셋 + 데이터셋별 모든 차원(g1) + 데이터셋별 모든 필터 값. 표식 시험·운영 화면 시험이 같이 쓴다."""
    import admin_db
    urls = [href for _, _, href in web.preset_links()] + ["/admin/reports"]
    for ds in admin_db.REPORT_DATASETS:
        spec = admin_db.report_spec(ds)
        urls.append(web._report_href({"ds": ds}))
        urls += [web._report_href({"ds": ds, "g1": d}) for d in spec["dims"]]
        urls += [web._report_href({"ds": ds, "g1": next(iter(spec["dims"])), k: v}) for k, vals in spec["filters"].items() for v in vals
                 if next(iter(spec["dims"])) not in spec.get("user_dims", ())]
    return urls


def _web_admin_block():
    """web.py 의 /admin 구간(「# ── /admin (β2)」부터 routes 전까지) — /admin 핸들러 전부(Setup·문의 처리 POST·문의 화면)."""
    src = (HERE / "web.py").read_text(encoding="utf-8")
    i, j = src.index("    # ── /admin (β2)"), src.index("    def routes(self)")
    return src, src[i:j]


def test_admin_boundary_static():
    """정적: /admin 핸들러가 db 조회를 직접 안 부름 · admin_db 의 SQL 은 ALLOW 리터럴뿐 · SELECT * 0 · 본문 열 0 ·
    text·admin_note 는 tickets.detail 만 · label·options 는 이름 공유(consent=struct_names) 키만 · 옮긴 함수는 db 에 없다."""
    import ast
    import admin_db
    src, blk = _web_admin_block()
    used = set(re.findall(r"\bdb\.(\w+)", blk))
    assert used <= ADB_ALLOWED_DB, f"/admin 핸들러가 db 를 직접 부른다: {sorted(used - ADB_ALLOWED_DB)}"
    assert "admin_db." in blk
    for name in ("admin_users", "admin_audit_list", "tickets_admin_counts", "tickets_all", "admin_act", "ticket_set_status", "ticket_set_note"):
        assert f"db.{name}(" not in src and f"db.{name})" not in src and f"db.{name}," not in src, f"web.py 가 db.{name} 을 직접 부른다"
    for gone in ("tickets_legacy_inbox", "_admin_write", "_ticket_admin(", "ticket.read_all", "[\"admin_tickets\"]", "\"admin_tickets\":"):
        assert gone not in src, ("web.py 에 /app 관리자 문의함 흔적", gone)
    assert not hasattr(admin_db, "tickets_legacy_inbox") and "tickets.legacy_inbox" not in admin_db.ALLOW
    for name in ("admin_users", "admin_audit_list", "tickets_admin_counts", "tickets_all", "_TICKET_ADMIN_COLS", "_ticket_admin_row", "TICKET_ADMIN_LIST"):
        assert not hasattr(db, name), f"db.{name} 이 남았다 — 운영 조회는 admin_db 로"
    asrc = (HERE / "admin_db.py").read_text(encoding="utf-8")
    assert not re.search(r"SELECT\s+(\w+\.)?\*", asrc, re.I), "admin_db 에 SELECT *"
    tree = ast.parse(asrc)
    assert not any(isinstance(n, ast.JoinedStr) for n in ast.walk(tree)), "admin_db 에 f-string — SQL 보간 금지"
    assert not any(isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mod, ast.Add)) and isinstance(n.left, ast.Constant)
                   and isinstance(n.left.value, str) for n in ast.walk(tree)), "admin_db 에 문자열 % · + 조립"
    sqls = {k: v["sql"] for k, v in admin_db.ALLOW.items() if v["sql"]}
    lits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", n.value)]
    assert lits and set(lits) <= set(sqls.values()), "ALLOW 밖 SQL 리터럴이 있다"
    for k, v in admin_db.ALLOW.items():
        assert v["viewer"] == admin_db.VIEWER_ADMIN and v["consent"] in (None, "struct_names") and v["cols"], k
        sql = v["sql"]
        if not sql:
            continue
        assert not ADB_BODY_COLS.search(sql), (k, ADB_BODY_COLS.search(sql).group(0))
        if k in ADB_STATE_KEYS:
            cols = set(re.findall(r"\b[a-z]{1,3}\.([a-z_]+)\b", sql))
            assert cols <= ADB_STATE_COLS, (k, "상태·식별 열만", sorted(cols - ADB_STATE_COLS))
        else:
            assert not ADB_BODY_TABLES.search(sql), (k, ADB_BODY_TABLES.search(sql).group(0))
        if k not in ADB_TEXT_KEYS:
            assert not ADB_TEXT_COLS.search(sql), (k, "text·admin_note 는 문의 상세만")
        if v["consent"] != "struct_names":
            assert not ADB_NAME_COLS.search(sql), (k, "label·options 는 이름 공유 분기만")
    assert ADB_TEXT_COLS.search(sqls["tickets.detail"]), "상세는 원문을 읽는다"
    # 조회자·미구현 키 거절
    expect("FORBIDDEN", admin_db.users, "user")
    expect("FORBIDDEN", admin_db.ticket_history, None, "tk_x")
    assert not hasattr(admin_db, "ticket_counts") and "tickets.counts" not in admin_db.ALLOW, "/app 띠 숫자는 P5 에서 뺐다(개요는 overview.band)"
    for fn, args in ((admin_db.ticket_status, ("ack",)), (admin_db.ticket_note, ("x",)), (admin_db.ticket_reply, ("x",)),
                     (admin_db.ticket_reclassify, ("bug", ""))):
        expect("FORBIDDEN", fn, "user", "u_x", "tk_x", *args)
    expect("ARG-INVALID", admin_db._rows, "schema.names", "admin")
    expect("ARG-INVALID", admin_db._rows, "users; DROP TABLE users", "admin")
    print("OK — 운영 경계 정적(핸들러 db 조회 0 · ALLOW 리터럴만 · SELECT * 0 · 본문 열 0 · text/label 분기 · 조회자 거절) 통과")


async def test_admin_boundary():
    """표식: 다른 사용자의 금지 열(items 제목·본문·data, 기록, 질문, 탭, history, 비동의 label·options)에 표식을 넣고
    관리자 응답 전부에 표식이 없는지. 허용 응답(문의 상세)에는 원문·메모가 있는지. ALLOW 키별 행 키 = cols(응답 키 스냅샷)."""
    import admin_db
    from psycopg.types.json import Jsonb
    site = main.site
    mark = "MARKF" + secrets.token_hex(4)        # 금지 열 표식 — 어떤 관리자 응답에도 없어야 한다
    tmark, nmark = "MARKT" + secrets.token_hex(4), "MARKN" + secrets.token_hex(4)   # 문의 원문·메모 — 상세에만
    A = db.user_by_subject("adb_admin_sub", ADB["admin"])["id"]
    U = db.user_by_subject("adb_user_sub", ADB["user"])["id"]
    _set_role(A, "admin")
    with db.tx() as c:
        c.execute("INSERT INTO questions (id,user_id,text) VALUES ('q_adb',%s,%s)", (U, mark + "-q"))
        c.execute("INSERT INTO records (id,user_id,title,body) VALUES ('r_adb',%s,%s,%s)", (U, mark + "-rt", mark + "-rb"))
        c.execute("INSERT INTO tabs (user_id,doc) VALUES (%s,%s)", (U, Jsonb({"tabs": [{"name": mark + "-tab"}]})))
        c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision,history) VALUES (%s,'od_adb','adb',%s,'collection',1,%s)",
                  (U, mark + "-ol", Jsonb([{"label": mark + "-hist"}])))
        c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,options) VALUES (%s,'od_adb','f1',%s,'select',%s)",
                  (U, mark + "-fl", Jsonb([{"value": "v", "label": mark + "-opt"}])))
        c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,title,body,data,revision) "
                  "VALUES (%s,'i_adb','od_adb',1,%s,%s,%s,1)", (U, mark + "-it", mark + "-ib", Jsonb({"f1": mark + "-data"})))
        # PR #20 Q5: 참조 필드 + 연결 1개(자기 참조) — /admin/users 상세의 관계·연결 집계가 도는 자리
        c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,target_kind,target_object_id,position) "
                  "VALUES (%s,'od_adb','f2',%s,'reference','item','od_adb',1)", (U, mark + "-rl"))
        c.execute("INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_item_id) "
                  "VALUES (%s,'i_adb','od_adb','f2','i_adb')", (U,))
    t = {"id": "tk_adb" + secrets.token_hex(3)}   # SQL 로 — desk 시험이 전역 하루 한도를 채워 둔다
    with db.tx() as c:
        c.execute("INSERT INTO tickets (id,user_id,kind,text,via,admin_note) VALUES (%s,%s,'bug',%s,'web',%s)", (t["id"], U, tmark, nmark))
        # P4: 사용량 1행(개요·리포트 스냅샷) · 이름 공유 동의자(A)의 표식 없는 정의 — 허용 응답(동의자 현재 이름) 대조용
        c.execute("INSERT INTO usage_daily (day,user_id,name,code,n) VALUES ((now() AT TIME ZONE 'Asia/Seoul')::date,%s,'record_add','OK',1) "
                  "ON CONFLICT DO NOTHING", (U,))
        c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision) VALUES (%s,'od_adbn','adbn','이름공유오브젝트','collection',1)",
                  (A,))
        c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,options) VALUES (%s,'od_adbn','f1','이름공유필드','select',%s)",
                  (A, Jsonb([{"value": "v", "label": "이름공유선택지"}])))
    db.optional_consent_set(A, "struct_names", True)
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    cookie = site.session_value(A)
    seen = []

    async def call(method, path, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", cookie)
            r = await h.request(method, path, headers={"origin": PUBLIC}, **kw)
        seen.append((method, path, r.status_code))
        return r, r.text + json.dumps(dict(r.headers), ensure_ascii=False)
    try:
        for path in ADMIN_GETS + tuple(admin_report_urls()) + (f"/admin/schema?u={site._opaque(U)}", f"/admin/users/{site._opaque(U)}"):
            r, out = await call("GET", path)
            assert r.status_code in (200, 302), (path, r.status_code)
            assert mark not in out, f"{path} 응답에 금지 열 값"
        # 허용 응답 — 이름 공유 동의자(A)의 현재 이름은 사용자 상세에 보인다(비동의 U 의 이름은 위에서 0)
        r, out = await call("GET", f"/admin/users/{site._opaque(A)}")
        assert r.status_code == 200 and all(w in out for w in ("이름공유오브젝트", "이름공유필드", "이름공유선택지")) and mark not in out, out[-600:]
        r, out = await call("GET", "/admin/setup")
        assert r.status_code == 200 and ADB["user"] in out and tmark not in out and nmark not in out, "Setup 엔 이메일만, 문의 원문 없음"
        # 문의 화면(P4) — 리스트엔 원문·메모 없음, 레코드 페이지(허용 응답)엔 원문·메모가 있다. 둘 다 금지 열 값 없음
        for q in ("?sort=new", "?status=all&sort=new", "?status=all&kind=bug&sort=new&cols=via,closed"):   # 최근순 — 앞 절 문의가 많아도 상한 안
            r, out = await call("GET", "/admin/tickets" + q)
            assert r.status_code == 200 and ADB["user"] in out and mark not in out and tmark not in out and nmark not in out, q
        r, out = await call("GET", f"/admin/tickets/{t['id']}")
        assert r.status_code == 200 and tmark in out and nmark in out and mark not in out, "레코드 페이지 = 문의 상세 허용 응답"
        r, out = await call("GET", "/api/state")
        assert r.status_code == 200 and "admin_tickets" not in r.json() and tmark not in out and nmark not in out, "/app 에 관리자 데이터 없음"
        # 문의 처리 POST(303) — 사건이 남고 레코드 페이지(허용 응답)가 원문·메모를 보인다
        r, out = await call("POST", f"/admin/tickets/{t['id']}/status", data={"status": "ack"})
        assert r.status_code == 303 and mark not in out and tmark not in out, r.status_code
        r, out = await call("POST", f"/admin/tickets/{t['id']}/note", data={"admin_note": nmark + "!"})
        assert r.status_code == 303 and mark not in out, r.status_code
        r, out = await call("GET", f"/admin/tickets/{t['id']}")
        assert r.status_code == 200 and tmark in out and nmark + "!" in out and mark not in out
        d = admin_db.ticket_detail("admin", t["id"])
        # 대조 — 표식은 본인 화면에선 보인다(심은 값이 실제로 읽히는 자리에 있다는 증거)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", site.session_value(U))
            own = (await h.get("/api/state")).text
        for suffix in ("-q", "-rt", "-tab", "-ol", "-fl", "-opt", "-it", "-ib"):
            assert mark + suffix in own, f"대조 실패 — 본인 state 에 {suffix} 표식이 없다"
        assert d["text"] == tmark and d["admin_note"] == nmark + "!" and d["email"] == ADB["user"], d
        # 응답 키 스냅샷 — ALLOW 키별 행 키 = cols(/admin 은 HTML 이라 키는 함수 반환으로 본다)
        await call("POST", "/admin/signout", data={"uid": U})   # 감사 행 1개 이상
        snap = {"setup.users": admin_db.users("admin"), "audit.list": admin_db.audit("admin"),
                "tickets.detail": [admin_db.ticket_detail("admin", t["id"])],
                "tickets.history": admin_db.ticket_history("admin", t["id"]),
                "tickets.list": admin_db.tickets_list("admin", "all")[0]}
        today = admin_db.kst_today()   # P4 키 — 개요·리포트 원행·스키마 트리·이름 분기(동의자 A)
        names = admin_db.schema_names("admin", A)
        snap.update({"overview.band": [admin_db.overview_band("admin")], "overview.last_active": admin_db.last_active("admin"),
                     **{admin_db.REPORT_DATASETS[ds]: admin_db.report_rows("admin", ds, *(() if ds == "structure" else (today, today)))
                        for ds in admin_db.REPORT_DATASETS},
                     "schema.tree": admin_db.schema_tree("admin", U), "schema.object_names": names[0], "schema.field_names": names[1],
                     **admin_db.userview_rows("admin", U)})
        assert admin_db.schema_names("admin", U) == ([], []), "비동의자는 이름 분기 0행"
        assert set(snap) == {k for k, v in admin_db.ALLOW.items() if v["sql"]}, "ALLOW 키가 늘면 스냅샷도 늘린다"
        for k, rows in snap.items():
            assert rows, k
            for row in rows:
                assert tuple(row) == admin_db.ALLOW[k]["cols"], (k, tuple(row))
                assert mark not in json.dumps(row, default=str, ensure_ascii=False), k
    finally:
        site.dev, site.dev_user = real
    print(f"OK — 운영 경계 표식(관리자 응답 {len(seen)}개에 금지 열 값 0 · 문의 상세·레코드 페이지에만 원문·메모 · ALLOW 행 키 = cols) 통과")


# ── 12) 선택 동의 · 처리방침 v4 · 옛 사용량 삭제 ──────────
OC = {k: f"oc{k}@example.com" for k in ("1", "2", "3", "4")}   # 1 = 옛 버전(v1) 동의 행 · 3 = 왕복 · 4 = 실패 주입·동시


def _optional_rows(uid):
    with db.tx() as c:
        cur = c.execute("SELECT kind,version,state FROM optional_consents WHERE user_id=%s", (uid,)).fetchall()
        log = c.execute("SELECT kind,version,action FROM optional_consent_log WHERE user_id=%s ORDER BY id", (uid,)).fetchall()
    return cur, log


async def test_optional_consent():
    """옛 버전 동의 행만 있는 사용자도 쓰기·/app 통과 · 계정 메뉴 경로(/api/optional-consent)로 켜기 · 실패 주입 → 행 0
    · /api/optional-consent 401·403·400 · 켜기/끄기 왕복과 로그 · 같은 상태 무동작 · 미동의자도 끔 · 동시 두 요청 → 행 1개
    · 옛 버전 동의는 무효 · /privacy v4 처리 근거 표·출처·표준 편입·작성자 표시·계정 메뉴 문의(비로그인) · 사용량 삭제 표식 멱등."""
    import threading
    site = main.site
    U = {k: db.user_by_subject(f"oc{k}_sub", e)["id"] for k, e in OC.items()}
    with db.tx() as c:
        c.execute("DELETE FROM agreements WHERE user_id = ANY(%s)", (list(U.values()),))
    assert db.CONSENT_VERSIONS.get("privacy") == "v4" and db.OPTIONAL_VERSIONS == {"struct_names": "s1"}, (db.CONSENT_VERSIONS, db.OPTIONAL_VERSIONS)
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    origin = {"origin": PUBLIC}

    def client(uid=None):
        h = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC)
        if uid:
            h.cookies.set("crm_session", site.session_value(uid))
        return h
    try:
        # 옛 버전(v1) 동의 행만 있어도 — 처리방침 버전과 무관하게 쓰기·/app 통과(재동의 없음)
        with db.tx() as c:
            c.execute("INSERT INTO agreements (user_id,kind,version) VALUES (%s,'privacy','v1')", (U["1"],))
        db.write_gate("record_add", {"text": "v1 동의 행"}, U["1"])   # 통과
        async with client(U["1"]) as h:
            assert (await h.get("/app")).status_code == 200
            u = (await h.get("/api/state")).json()["user"]
            assert u["struct_names"] is False, "이름 공유는 기본 꺼짐"
            # 이름 공유는 계정 메뉴 경로로만 켠다
            r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": True}, headers=origin)
            assert r.status_code == 200 and r.json()["changed"] is True, r.text
        assert [(a["kind"], a["version"]) for a in _agreements(U["1"])] == [("privacy", "v1")], "agreements 는 새로 쓰지 않는다"
        assert _optional_rows(U["1"]) == ([{"kind": "struct_names", "version": "s1", "state": "granted"}],
                                          [{"kind": "struct_names", "version": "s1", "action": "grant"}]), _optional_rows(U["1"])
        assert db.optional_consent_ok(U["1"], "struct_names")
        assert _optional_rows(U["3"]) == ([], []) and not db.optional_consent_ok(U["3"], "struct_names")
        # 실패 주입 — 선택 저장이 터지면 행도 로그도 없다(한 트랜잭션)
        real_apply = db._optional_apply

        def boom(*a, **k):
            raise db.CrmError("INJECTED", "선택 저장 실패 주입")
        db._optional_apply = boom
        try:
            expect("INJECTED", db.optional_consent_set, U["4"], "struct_names", True)
            async with client(U["4"]) as h:
                r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": True}, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "INJECTED", r.text
        finally:
            db._optional_apply = real_apply
        assert not _agreements(U["4"]) and _optional_rows(U["4"]) == ([], []), "실패하면 아무것도 저장되지 않는다"
        # /api/optional-consent — 401 · 403 · 400
        async with client() as h:
            r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": True}, headers=origin)
            assert r.status_code == 401, r.status_code
        async with client(U["3"]) as h:
            r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": True})
            assert r.status_code == 403 and r.json()["code"] == "ORIGIN", r.text
            for bad in ({"kind": "struct_names", "on": "true"}, {"kind": "struct_names"}, {"kind": "names", "on": True}, []):
                r = await h.post("/api/optional-consent", json=bad, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad, r.text)
            # 켜기 → 같은 상태 → 끄기 → 같은 상태 : 로그 2행
            outs = []
            for on in (True, True, False, False):
                r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": on}, headers=origin)
                assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
                outs.append(r.json()["changed"])
            assert outs == [True, False, True, False], outs
        cur, log = _optional_rows(U["3"])
        assert cur == [{"kind": "struct_names", "version": "s1", "state": "withdrawn"}] and [x["action"] for x in log] == ["grant", "withdraw"], (cur, log)
        assert not db.optional_consent_ok(U["3"], "struct_names")
        # 미동의자도 끌 수 있다(게이트 밖) — 행 없음 + 끄기 = 무동작, 켜기·끄기도 된다
        assert not _agreements(U["4"])
        async with client(U["4"]) as h:
            r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": False}, headers=origin)
            assert r.status_code == 200 and r.json()["changed"] is False, r.text
        assert _optional_rows(U["4"]) == ([], [])
        # 동시 두 켜기 → 현재 행 1개 · grant 로그 1개
        outs, errs, gate = [], [], threading.Barrier(2)

        def on_():
            try:
                gate.wait()
                outs.append(db.optional_consent_set(U["4"], "struct_names", True)["changed"])
            except Exception as e:   # noqa: BLE001
                errs.append(e)
        th = [threading.Thread(target=on_) for _ in range(2)]
        [x.start() for x in th]
        [x.join() for x in th]
        assert not errs and sorted(outs) == [False, True], (outs, errs)
        cur, log = _optional_rows(U["4"])
        assert len(cur) == 1 and [x["action"] for x in log] == ["grant"], (cur, log)
        # 옛 버전 동의는 무효 — 켜면 현재 버전으로 다시 grant
        with db.tx() as c:
            c.execute("UPDATE optional_consents SET version='s0' WHERE user_id=%s", (U["4"],))
        assert not db.optional_consent_ok(U["4"], "struct_names")
        assert db.optional_consent_set(U["4"], "struct_names", True)["changed"] is True and db.optional_consent_ok(U["4"], "struct_names")
        expect("ARG-INVALID", db.optional_consent_set, U["4"], "names", True)
        expect("ARG-INVALID", db.optional_consent_set, U["4"], "struct_names", 1)
        # /privacy v4 — 로그인 없이 · 처리 근거 표(제15조①4호 계약 이행 · ⑥ 정당한 이익 · ① 선택 동의)
        async with client() as h:
            r = await h.get("/privacy")
        for word in ("v4 · 2026-10-20", "구조 이름 공유", "동의하지 않아도 모든 기능을 쓸 수 있습니다",
                     "v2 시행 전에 모은 이용 기록은 v2 시행 때 지웠습니다", "메시지·내용은 모으지 않습니다", 'id="privacy-1"',
                     "처리 근거", "제15조 제1항 제4호", "제15조 제1항 제6호", "제15조 제1항 제1호",
                     "로그인 전 요청의 이용 기록은 사용자와 연결하지 않습니다",
                     "출처(종류·이름·위치)", "표준 편입", "작성자 표시만 검사", "계정 메뉴 「문의 보내기」"):
            assert word in r.text, word
        basis = r.text[r.text.index("처리 근거"):r.text.index('id="privacy-2"')]
        assert "<table>" in basis and basis.count("<tr>") == 4, basis
        assert r.status_code == 200 and "지금은 모으지 않습니다" not in r.text and "v1 · 2026-10-18" not in r.text
        assert "v2 · 2026-10-19" not in r.text and "v3 · 2026-10-19" not in r.text and "처리방침에 동의하기 전" not in r.text
        assert "로그인한 뒤 「문의」에서" not in r.text, "문의 경로는 계정 메뉴 「문의 보내기」"
    finally:
        site.dev, site.dev_user = real
    # 사용량 삭제 표식 — 임시 스키마: 새 DB 는 표식만 · 두 번째 init 은 안 지움 · 표식 없는 옛 DB 는 1회 전부 삭제
    name = f"oc_{secrets.token_hex(3)}"
    create_schema(name)
    try:
        with schema_conn(name) as c:
            db.init_schema(c)
            c.execute("INSERT INTO usage_daily (day,user_id,name,code,n) VALUES ('2026-10-01','','x','OK',3), ('2026-10-19','u','y','OK',1)")
            c.commit()
            db.init_schema(c)
            assert c.execute("SELECT count(*) AS n FROM usage_daily").fetchone()["n"] == 2, "표식이 있으면 지우지 않는다"
            assert [r["key"] for r in c.execute("SELECT key FROM schema_marks").fetchall()] == [db.USAGE_RESET_MARK]
            c.execute("DELETE FROM schema_marks")   # 표식 없는 옛 DB
            c.commit()
            db.init_schema(c)
            assert c.execute("SELECT count(*) AS n FROM usage_daily").fetchone()["n"] == 0, "표식이 없으면 전부 지운다"
            c.execute("INSERT INTO usage_daily (day,user_id,name,code,n) VALUES ('2026-10-20','','x','OK',1)")
            c.commit()
            db.init_schema(c)
            assert c.execute("SELECT count(*) AS n FROM usage_daily").fetchone()["n"] == 1, "삭제는 1회만"
    finally:
        drop_schema(name)
    print("OK — 선택 동의(옛 버전 행도 통과·계정 메뉴로만 켬·실패 주입 행 0·왕복 로그 2·무동작·미동의자 끄기·동시 1행·옛 버전 무효)"
          "·/privacy v4·사용량 삭제 표식 멱등 통과")


# ── 13) 문의 완결 (P3) ──────────────────────────────────
TK = {"owner": "tk-owner@example.com", "other": "tk-other@example.com", "admin": "tk-admin@example.com"}


def _tk_insert(uid, kind="bug", text="문의 글", created_days_ago=0):
    tid = "tk_p3" + secrets.token_hex(4)
    with db.tx() as c:   # SQL 로 — desk 시험이 전역 하루 한도를 채워 둔다
        c.execute("INSERT INTO tickets (id,user_id,kind,text,via,created,updated) "
                  "VALUES (%s,%s,%s,%s,'web',now() - make_interval(days => %s),now() - make_interval(days => %s))",
                  (tid, uid, kind, text, created_days_ago, created_days_ago))
    return tid


def _tk_hist(tid):
    with db.tx() as c:
        return c.execute("SELECT visibility,kind,from_val,to_val,reason,actor_id FROM ticket_history WHERE ticket_id=%s ORDER BY id",
                         (tid,)).fetchall()


async def test_ticket_p3():
    """공개/내부 분리(웹·MCP) · 남의 문의 404 · 답변 한 트랜잭션(사건 실패 주입 → 답변 롤백) · 메모는 public_updated 그대로
    · 재분류 기산점 = created(11일 전 → 즉시 초과) · privacy 재진입 privacy_log 1행 · 해제 사유 필수·저장 · 종결 문의 재분류
    · 파기 cascade · 정기 파기 태스크 · /admin/tickets POST 사다리(404·403 Origin·400·303 — 옛 ra 쿠키도 303) · 회수된 관리자 거절."""
    import admin_db
    site = main.site
    O = db.user_by_subject("tk_owner_sub", TK["owner"])["id"]
    X = db.user_by_subject("tk_other_sub", TK["other"])["id"]
    A = db.user_by_subject("tk_admin_sub", TK["admin"])["id"]
    _set_role(A, "admin")
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    now = int(time.time())
    ck_admin, ck_stale = site.session_value(A), site.sign({"uid": A, "iat": now, "ra": now - 13 * 3600}, 600)   # ck_stale = 옛 ra 쿠키(무시)

    async def req(method, path, cookie=None, origin=True, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.request(method, path, headers={"origin": PUBLIC} if origin else {}, **kw)
    try:
        # ── 공개/내부 분리 ──
        t1 = _tk_insert(O, text="P3 문의 원문")
        assert db.ticket_set_status(A, t1, "ack")["changed"] is True
        assert db.ticket_set_status(A, t1, "ack")["changed"] is False, "같은 상태는 무동작"
        assert db.ticket_set_reply(A, t1, "공개 답변이에요")["changed"] is True
        pu = _ticket(t1)["public_updated"]
        assert db.ticket_set_note(A, t1, "운영자만 MARKNOTE")["changed"] is True
        assert db.ticket_reclassify(A, t1, "request")["changed"] is True
        row = _ticket(t1)
        assert row["public_updated"] == pu and row["admin_note"] == "운영자만 MARKNOTE", "내부 메모·재분류는 public_updated 를 안 바꾼다"
        assert [(h["visibility"], h["kind"], h["from_val"], h["to_val"]) for h in _tk_hist(t1)] == [
            ("public", "status", "open", "ack"), ("public", "reply", None, None), ("internal", "note", None, None),
            ("internal", "reclassify", "bug", "request")], _tk_hist(t1)
        assert all(h["actor_id"] == A for h in _tk_hist(t1))
        r = await req("GET", f"/api/feedback/{t1}", site.session_value(O))
        j = r.json()
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
        assert j["text"] == "P3 문의 원문" and j["reply"] == "공개 답변이에요" and j["reply_at"] and j["public_updated"], j
        assert [h["kind"] for h in j["history"]] == ["status", "reply"] and "admin_note" not in j and "hint" not in j, j
        assert "MARKNOTE" not in r.text and "internal" not in r.text and "reclassify" not in r.text, r.text
        mine = {t["id"]: t for t in (await req("GET", "/api/feedback", site.session_value(O))).json()["tickets"]}
        assert mine[t1]["has_reply"] is True and mine[t1]["public_updated"] and "reply" not in mine[t1], mine[t1]
        for cookie, tid in ((site.session_value(X), t1), (site.session_value(O), "tk_nope")):
            r = await req("GET", f"/api/feedback/{tid}", cookie)
            assert r.status_code == 404 and r.json()["code"] == "NOT-FOUND", r.text
        assert (await req("GET", f"/api/feedback/{t1}")).status_code == 401
        os.environ["CRM_DEV_USER"] = TK["owner"]
        try:
            async with Client(main.mcp) as c:
                lst = val(await c.call_tool("ticket_list_mine", {}))
                me = next(t for t in lst if t["id"] == t1)
                assert set(me) == {"id", "kind", "status", "created", "has_reply", "public_updated"} and me["has_reply"] is True, me
                got = val(await c.call_tool("ticket_get_mine", {"ticket_id": t1}))
                assert got["reply"] == "공개 답변이에요" and [h["kind"] for h in got["history"]] == ["status", "reply"], got
                assert "MARKNOTE" not in json.dumps(got, ensure_ascii=False) and "admin_note" not in got
            os.environ["CRM_DEV_USER"] = TK["other"]
            async with Client(main.mcp) as c:
                r = await c.call_tool("ticket_get_mine", {"ticket_id": t1})
                assert r.is_error and "[NOT-FOUND]" in r.content[0].text, r.content[0].text
                assert t1 not in {t["id"] for t in val(await c.call_tool("ticket_list_mine", {}))}
        finally:
            os.environ["CRM_DEV_USER"] = TESTER
        # ── 답변 트랜잭션 · 지우기 · 길이 ──
        real_ev = db._ticket_event

        def boom(*a, **k):
            raise db.CrmError("INJECTED", "사건 기록 실패 주입")
        db._ticket_event = boom
        try:
            expect("INJECTED", db.ticket_set_reply, A, t1, "바뀐 답변")
        finally:
            db._ticket_event = real_ev
        assert _ticket(t1)["reply"] == "공개 답변이에요" and len(_tk_hist(t1)) == 4, "사건이 실패하면 답변도 롤백"
        assert db.ticket_set_reply(A, t1, "  ")["changed"] is True
        row = _ticket(t1)
        assert row["reply"] is None and row["reply_at"] is None and _tk_hist(t1)[-1]["kind"] == "reply", row
        expect("PAYLOAD-LIMIT", db.ticket_set_reply, A, t1, "가" * 1366)   # 4098 바이트
        # ── 재분류: 기산점 = created(KST) · 즉시 초과 · 재진입 1행 · 사유 ──
        t2 = _tk_insert(O, kind="bug", created_days_ago=11)
        db.ticket_reclassify(A, t2, "privacy")
        with db.tx() as c:
            pl = c.execute("SELECT received,result FROM privacy_log WHERE ticket_id=%s", (t2,)).fetchall()
            want = c.execute("SELECT (created AT TIME ZONE 'Asia/Seoul')::date AS d FROM tickets WHERE id=%s", (t2,)).fetchone()["d"]
        assert len(pl) == 1 and pl[0]["received"] == want and pl[0]["result"] is None, (pl, want)
        d = admin_db.ticket_detail("admin", t2)
        assert d["privacy_received"] == want.isoformat() and admin_db.privacy_deadline(d["privacy_received"])[1] is True, d
        assert admin_db.privacy_deadline(_kst_today())[1] is False and admin_db.privacy_deadline(None) == (None, False)
        expect("ARG-INVALID", db.ticket_reclassify, A, t2, "bug")            # 사유 없음
        expect("ARG-INVALID", db.ticket_reclassify, A, t2, "bug", "nope")    # 모르는 사유
        db.ticket_reclassify(A, t2, "question", "duplicate")
        assert _tk_hist(t2)[-1]["reason"] == "duplicate" and _tk_hist(t2)[-1]["visibility"] == "internal"
        assert admin_db.ticket_detail("admin", t2)["privacy_received"] is None, "활성 개인정보 요청에서 빠진다(kind 로 거름)"
        expect("ARG-INVALID", db.ticket_reclassify, A, t2, "bug", "other")   # privacy 가 아닌 데서 사유
        db.ticket_reclassify(A, t2, "privacy")
        with db.tx() as c:
            assert c.execute("SELECT count(*) AS n FROM privacy_log WHERE ticket_id=%s", (t2,)).fetchone()["n"] == 1, "재진입 중복 없음"
        db.ticket_set_status(A, t2, "done")                                  # 종결 문의도 재분류
        assert db.ticket_reclassify(A, t2, "bug", "not_privacy")["changed"] is True
        with db.tx() as c:
            assert c.execute("SELECT result FROM privacy_log WHERE ticket_id=%s", (t2,)).fetchone()["result"] == "done", "result 는 그대로"
        expect("ARG-INVALID", db.ticket_reclassify, A, t2, "nope")
        # ── 실행자 재조회: 비관리자·차단 → FORBIDDEN(DB 단) ──
        for fn, args in ((db.ticket_set_status, ("doing",)), (db.ticket_set_reply, ("x",)), (db.ticket_set_note, ("x",)),
                         (db.ticket_reclassify, ("question",))):
            expect("FORBIDDEN", fn, O, t1, *args)
        expect("NOT-FOUND", db.ticket_set_status, A, "tk_nope", "ack")
        # ── /admin/tickets POST 사다리 ──
        ops = (("status", {"status": "doing"}), ("reply", {"reply": "가" * 1365}), ("note", {"admin_note": "웹 메모"}),
               ("reclassify", {"kind": "question", "reason": ""}))
        n0 = len(_tk_hist(t1))
        for op, form in ops:
            p = f"/admin/tickets/{t1}/{op}"
            for cookie in (None, site.session_value(O)):           # 무세션·비관리자 → 404
                assert (await req("POST", p, cookie, data=form)).status_code == 404, (op, "404")
            r = await req("POST", p, ck_admin, origin=False, data=form)
            assert r.status_code == 403 and "ORIGIN" in r.text, (op, r.status_code)
            r = await req("POST", p, ck_admin, json=form)
            assert r.status_code == 400 and "ARG-INVALID" in r.text, (op, r.status_code)
            assert len(_tk_hist(t1)) == n0, (op, "거절은 아무것도 안 바꾼다")
            r = await req("POST", p, ck_stale, data=form)   # 재인증 없음 — 옛 ra(13h 전) 쿠키도 그대로 처리
            assert r.status_code == 303 and r.headers["location"] == f"/admin/tickets/{t1}", (op, r.status_code, r.text[:200])
            n0 = len(_tk_hist(t1))
        row = _ticket(t1)
        assert row["status"] == "doing" and row["reply"] == "가" * 1365 and row["admin_note"] == "웹 메모" and row["kind"] == "question", row
        r = await req("POST", f"/admin/tickets/{t1}/reply", ck_admin, data={"reply": "가" * 1366})
        assert r.status_code == 400 and "PAYLOAD-LIMIT" in r.text and "가가가" not in r.text, r.status_code
        r = await req("POST", "/admin/tickets/tk_nope/status", ck_admin, data={"status": "ack"})
        assert r.status_code == 404 and "NOT-FOUND" in r.text, r.status_code
        r = await req("POST", f"/admin/tickets/{t1}/status", ck_admin, data={"status": "<script>x</script>"})
        assert r.status_code == 400 and "<script>x" not in r.text, r.text
        # 회수된 관리자(role=user) → 모든 POST 404 · 아무것도 안 바뀜
        _set_role(A, "user")
        try:
            for op, form in ops:
                assert (await req("POST", f"/admin/tickets/{t1}/{op}", ck_admin, data=form)).status_code == 404, op
            for path, body in (("/api/feedback/status", {"id": t1, "status": "ack"}), ("/api/feedback/note", {"id": t1, "admin_note": "y"})):
                assert (await req("POST", path, ck_admin, json=body)).status_code == 404, path   # 옛 /app 경로는 P5 에서 없앴다
        finally:
            _set_role(A, "admin")
        assert len(_tk_hist(t1)) == n0
        # ── 파기 cascade ──
        t3 = _tk_insert(O)
        db.ticket_set_status(A, t3, "done")
        db.ticket_set_reply(A, t3, "답")
        with db.tx() as c:
            c.execute("UPDATE tickets SET closed_at = now() - interval '181 days' WHERE id=%s", (t3,))
        assert len(_tk_hist(t3)) == 2
        assert db.tickets_purge() >= 1 and _ticket(t3) is None and _tk_hist(t3) == [], "문의 파기 → 답변·이력 함께"
    finally:
        site.dev, site.dev_user = real
    # ── 정기 파기: lifespan 이 태스크를 띄우고, 주기마다 purge_once → 파기 2종 ──
    calls = []
    real_p = (db.tickets_purge, db.admin_audit_purge, main.PURGE_EVERY)
    db.tickets_purge = lambda: calls.append("tickets") or 0
    db.admin_audit_purge = lambda: calls.append("audit") or 0
    main.PURGE_EVERY = 0.05
    sm = main.mcp.session_manager

    @asynccontextmanager
    async def fake_run():
        yield
    sm.run = fake_run
    try:
        async with main.lifespan(main.app):
            await asyncio.sleep(0.3)
    finally:
        del sm.run
        db.tickets_purge, db.admin_audit_purge, main.PURGE_EVERY = real_p
    assert calls[:2] == ["tickets", "audit"] and calls.count("tickets") >= 3 and calls.count("audit") >= 3, calls
    n = len(calls)
    await asyncio.sleep(0.15)
    assert len(calls) == n, "lifespan 이 끝나면 정기 태스크도 멈춘다"
    # 옛 tickets(P3 열 없음)에 init_schema → 열이 생기고, 두 번째는 그대로(멱등)
    name = f"tk_{secrets.token_hex(3)}"
    create_schema(name)
    try:
        with schema_conn(name) as c:
            db.init_schema(c)
            c.execute("ALTER TABLE tickets DROP COLUMN reply, DROP COLUMN reply_at, DROP COLUMN public_updated")
            c.commit()
            cols = lambda: [r["column_name"] for r in c.execute(   # noqa: E731
                "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='tickets' "
                "ORDER BY column_name").fetchall()]
            db.init_schema(c)
            first = cols()
            assert {"reply", "reply_at", "public_updated"} <= set(first), first
            db.init_schema(c)
            assert cols() == first
            c.execute("INSERT INTO users (id,email) VALUES ('u_t','t@x.test')")
            c.execute("INSERT INTO tickets (id,user_id,kind,text,via,reply) VALUES ('tk_t','u_t','bug','x','web',%s)", ("가" * 1366,))
            raise AssertionError("reply 4096 바이트 CHECK 가 있어야 한다")
    except psycopg.errors.CheckViolation:
        pass
    finally:
        drop_schema(name)
    print("OK — 문의 완결(공개/내부 분리·웹·MCP 소유권·답변 한 트랜잭션·메모 public_updated 불변·재분류 기산점·즉시 초과·재진입 1행"
          "·해제 사유·종결 재분류·실행자 재조회·/admin/tickets 사다리·회수 관리자 거절·파기 cascade·정기 파기) 통과")


# ── 13b) 문의 화면 (P4-4·5) ────────────────────────────
TKV = {"admin": "tkv-admin@example.com", "u": "<i>tkv</i>@example.com"}


async def test_ticket_pages():
    """/admin/tickets 리스트·/admin/tickets/<id> 레코드: 비관리자·무세션 404 · 옛 ra 쿠키도 200 · CSP·스크립트 0·이벤트 속성 0
    · escape(text·reply·memo·email 의 <script>) · 기본 필터(미종결)·기한 정렬(초과 최우선 → 기한 → 접수순) · 모르는 키 400 · 행 상한
    · 리스트에 원문·메모·화면·도구 없음 · 레코드에 원문·Path(현재 강조)·답변·메모·재분류·이력(공개/내부) · form action 이 P3 POST 를 가리킴."""
    import admin_db
    site = main.site
    A = db.user_by_subject("tkv_admin_sub", TKV["admin"])["id"]
    U = db.user_by_subject("tkv_user_sub", TKV["u"])["id"]
    _set_role(A, "admin")
    xss = "<script>alert(1)</script>"
    # 앞 절 문의(오늘 접수)가 많아 기본 목록이 상한에 걸릴 수 있다 — 이 절의 비개인정보 문의는 접수일을 앞당겨 앞쪽에 둔다
    t_new = _tk_insert(U, kind="bug", text="새 버그 " + xss, created_days_ago=29)
    t_old = _tk_insert(U, kind="question", text="옛 질문", created_days_ago=30)
    t_over = _tk_insert(U, kind="bug", text="초과될 개인정보", created_days_ago=12)
    db.ticket_reclassify(A, t_over, "privacy")                      # 받은 날 = 12일 전 → 기한 초과
    t_due = _tk_insert(U, kind="bug", text="기한 남은 개인정보", created_days_ago=2)
    db.ticket_reclassify(A, t_due, "privacy")                       # 받은 날 = 2일 전 → 8일 남음
    t_done = _tk_insert(U, kind="bug", text="끝난 문의")
    db.ticket_set_status(A, t_done, "done")
    db.ticket_set_reply(A, t_new, "답변 " + xss)
    db.ticket_set_note(A, t_new, "메모 " + xss)
    with db.tx() as c:
        c.execute("UPDATE tickets SET page='record', tool='record_add', ua_family='safari', hint='privacy?' WHERE id=%s", (t_new,))
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    now = int(time.time())
    ck_admin, ck_stale = site.session_value(A), site.sign({"uid": A, "iat": now, "ra": now - 13 * 3600}, 600)   # ck_stale = 옛 ra 쿠키(무시)

    async def get(path, cookie=ck_admin):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.get(path)
    pages = ("/admin/tickets", f"/admin/tickets/{t_new}")
    try:
        for path in pages:
            for cookie in (None, site.session_value(U)):
                r = await get(path, cookie)
                assert r.status_code == 404 and "<" not in r.text, (path, r.status_code)
            r = await get(path, ck_stale)
            assert r.status_code == 200, (path, r.status_code)
            r = await get(path)
            t = r.text
            csp = r.headers["content-security-policy"]
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store", (path, r.status_code)
            assert "script-src 'self'" in csp and "unsafe-inline" not in csp and r.headers["x-frame-options"] == "DENY", csp
            assert "<script" not in t and not re.search(r"\son[a-z]+\s*=", t), (path, "스크립트·이벤트 속성 0")
            assert "&lt;i&gt;tkv&lt;/i&gt;@example.com" in t and "<i>tkv" not in t, (path, "이메일 escape")
        site.dev, site.dev_user = True, TKV["admin"]   # DEV 는 404
        try:
            for path in pages:
                assert (await get(path)).status_code == 404, ("DEV", path)
        finally:
            site.dev, site.dev_user = False, ""
        # 리스트 — 기본 = 미종결, 기한 정렬(초과 → 남은 기한 → 나머지 접수순), 원문·메모·화면·도구 없음
        r = await get("/admin/tickets")
        t = r.text
        mine = [x for x in re.findall(r'href="/admin/tickets/(tk_[^"]+)"', t) if x in {t_new, t_old, t_over, t_due, t_done}]
        assert mine[:2] == [t_over, t_due] and mine.index(t_old) < mine.index(t_new) and t_done not in mine, mine
        assert "기한 초과 2일" in t and "8일 남음" in t and "개인정보?" in t, "기한·의심 표시"
        for word in ("새 버그", "메모 &lt;script", "답변 &lt;script", "record_add", "safari", "초과될 개인정보"):
            assert word not in t, ("리스트에 원문·메모·화면·도구가 없다", word)
        r = await get("/admin/tickets?status=closed&kind=bug&sort=updated&cols=via,closed")
        assert r.status_code == 200 and t_done in r.text and t_new not in r.text and "<th>종결일</th>" in r.text, r.status_code
        r = await get("/admin/tickets?status=all&kind=privacy&sort=new")
        ids = [x for x in re.findall(r'href="/admin/tickets/(tk_[^"]+)"', r.text) if x in {t_new, t_old, t_over, t_due, t_done}]
        assert ids == [t_due, t_over], ids
        for bad in ("?status=nope", "?kind=nope", "?sort=nope", "?cols=text", "?x=1", "?cols=via,admin_note"):
            r = await get("/admin/tickets" + bad)
            assert r.status_code == 400 and "ARG-INVALID" in r.text and "새 버그" not in r.text, bad
        real_max = admin_db.TICKET_LIST_MAX
        admin_db.TICKET_LIST_MAX = 2
        try:
            rows, more = admin_db.tickets_list("admin", "all")
            assert len(rows) == 2 and more is True
            assert "상위 2건만" in (await get("/admin/tickets?status=all")).text
        finally:
            admin_db.TICKET_LIST_MAX = real_max
        expect("ARG-INVALID", admin_db.tickets_list, "admin", "nope")
        expect("FORBIDDEN", admin_db.tickets_list, "user")
        # 레코드 — 원문 전문·메타·Path(현재 강조)·form 4종·이력 배지
        r = await get(f"/admin/tickets/{t_new}")
        t = r.text
        assert "새 버그 &lt;script&gt;" in t and "record_add" in t and "safari" in t and "record" in t and "개인정보?" in t, "원문·화면·도구·UA·의심"
        assert 'class="cur" disabled aria-current="step">확인<' not in t and 'aria-current="step">접수<' in t, "현재 단계 = 접수"
        for op in ("status", "reply", "note", "reclassify"):
            assert f'action="/admin/tickets/{t_new}/{op}"' in t, op
        assert ">답변 &lt;script&gt;alert(1)&lt;/script&gt;</textarea>" in t and ">메모 &lt;script&gt;alert(1)&lt;/script&gt;</textarea>" in t
        assert t.count('<span class="tag pub">공개</span>') == 1 and t.count('<span class="tag int">내부</span>') == 1, "답변=공개 · 메모=내부"
        r = await get(f"/admin/tickets/{t_over}")
        assert "기한 초과 2일" in r.text and "재분류" in r.text and "버그 → 개인정보 요청" in r.text, "재분류 이력 from→to"
        r = await get(f"/admin/tickets/{t_done}")
        assert 'aria-current="step">완료<' in r.text and "종결" in r.text
        r = await get("/admin/tickets/tk_nope")
        assert r.status_code == 404 and "NOT-FOUND" in r.text
        # POST → 303 → 레코드 페이지가 바뀐 값을 보인다
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", ck_admin)
            r = await h.post(f"/admin/tickets/{t_old}/status", data={"status": "doing"}, headers={"origin": PUBLIC})
            assert r.status_code == 303
            r = await h.get(r.headers["location"])
            assert r.status_code == 200 and 'aria-current="step">처리중<' in r.text and "접수 → 처리중" in r.text
    finally:
        site.dev, site.dev_user = real
    print("OK — 문의 화면(404·옛 ra 통과·DEV · CSP·스크립트 0·escape · 기본 미종결·기한 정렬·초과 표시 · 리스트에 원문·메모 없음 · 400 · 상한"
          " · 레코드 Path·form 4종·공개/내부 배지 · POST→303→반영) 통과")


# ── 14) 운영 화면 (P4: 개요 · 스키마 · 리포트) ───────────
P4 = {k: f"p4{k}@example.com" for k in ("a", "g", "n", "w", "o")}


async def test_admin_screens():
    """/admin 개요·/admin/schema·/admin/reports — 404(무세션·비관리자·DEV) · 옛 ra 쿠키도 200 · CSP·스크립트 0·no-store·공통 메뉴
    · 이름 공유: 동의자만 현재 이름(템플릿 이름 바꾼 것 포함), 비동의·철회·옛 버전은 0 · 출력 escape · 리포트 잘못된 키·조합 400
    · 행 상한 200 · 프리셋 전부 200 · 개요 = 프리셋 정의 · 표준 구조(템플릿 + 코어)."""
    import datetime
    import hashlib
    import admin_db
    import structure_svg
    import template_author
    from psycopg.types.json import Jsonb
    site = main.site
    U = {k: db.user_by_email(e)["id"] for k, e in P4.items()}
    A = U["a"]
    _set_role(A, "admin")
    mark = "P4MARK" + secrets.token_hex(4)   # 비동의·철회·옛 버전 사용자의 이름 — 어떤 운영 응답에도 없어야 한다
    xss = "<script>alert(1)</script>"
    today = admin_db.kst_today()
    for k in ("g", "n", "w", "o"):
        template_author.seed(U[k], log=lambda *a: None)
        lab = f"{xss}G-OBJ" if k == "g" else f"{mark}-{k}-obj"
        opt = "<b>G-OPT</b>" if k == "g" else f"{mark}-{k}-opt"
        with db.tx() as c:
            c.execute("UPDATE object_defs SET label=%s WHERE user_id=%s AND key='axes'", ("G-RENAMED" if k == "g" else f"{mark}-{k}-tpl", U[k]))
            c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision) VALUES (%s,'od_p4u','p4u',%s,'collection',1)",
                      (U[k], lab))
            c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,options,position) VALUES (%s,'od_p4u','pick',%s,'select',%s,0)",
                      (U[k], f"{mark}-{k}-fld" if k != "g" else "G-FLD", Jsonb([{"value": "v1", "label": opt}])))
            c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,target_kind,target_object_id,multiple,position) "
                      "VALUES (%s,'od_p4u','link',%s,'reference','item','od_axes',true,1)", (U[k], f"{mark}-{k}-ref" if k != "g" else "G-REF"))
    db.optional_consent_set(U["g"], "struct_names", True)
    db.optional_consent_set(U["w"], "struct_names", True)
    db.optional_consent_set(U["w"], "struct_names", False)
    db.optional_consent_set(U["o"], "struct_names", True)
    with db.tx() as c:
        c.execute("UPDATE optional_consents SET version='s0' WHERE user_id=%s", (U["o"],))
        c.execute("INSERT INTO usage_daily (day,user_id,name,code,n) VALUES (%s,%s,'record_add','RATE-LIMIT',3),(%s,%s,'state_get','OK',7),"
                  "(%s,'','p4:unlinked','OK',2) ON CONFLICT DO NOTHING", (today, U["g"], today, U["g"], today))
        c.execute("INSERT INTO usage_daily (day,user_id,name,code,n) SELECT %s,%s,'p4cap-'||g,'OK',1 FROM generate_series(1,205) g "
                  "ON CONFLICT DO NOTHING", (today, U["n"]))
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    now = int(time.time())
    good = site.session_value(A)
    pages = ("/admin", "/admin/schema", "/admin/reports", "/admin/users")

    async def get(path, cookie=good):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.get(path)

    def clean(r, path):
        """스크립트 0 · 이벤트 속성 0 · CSP · no-store · 금지 이름 0."""
        t = r.text
        assert "script-src 'self'" in r.headers["content-security-policy"] and "unsafe-inline" not in r.headers["content-security-policy"], path
        assert r.headers["cache-control"] == "no-store", path
        assert "<script" not in t.lower() and not re.search(r"\son[a-z]+\s*=", t, re.I), (path, "스크립트·이벤트 속성 0")
        assert mark not in t, (path, "비동의·철회·옛 버전 이름이 보인다")
        return t
    try:
        # ── 404 · 재인증 없음 ──
        for cookie in (None, site.session_value(U["g"])):
            for path in pages + (f"/admin/schema?u={site._opaque(U['g'])}", "/admin/reports?ds=usage", f"/admin/users/{site._opaque(U['g'])}"):
                r = await get(path, cookie)
                assert r.status_code == 404 and "<" not in r.text, (path, r.status_code)
        site.dev, site.dev_user = True, P4["a"]
        try:
            for path in pages:
                assert (await get(path)).status_code == 404, ("DEV", path)
        finally:
            site.dev, site.dev_user = False, ""
        for cookie in (site.session_value(A), site.sign({"uid": A, "iat": now, "ra": now - 13 * 3600}, 600)):
            for path in pages + ("/admin/reports?ds=usage&g1=day",):
                r = await get(path, cookie)
                assert r.status_code == 200, (path, r.status_code)
        # ── 공통 메뉴 · 개요 ──
        for path, cur in (("/admin", "/admin"), ("/admin/users", "/admin/users"), ("/admin/schema", "/admin/schema"), ("/admin/reports", "/admin/reports"),
                          ("/admin/setup", "/admin/setup"), ("/admin/tickets", "/admin/tickets")):
            r = await get(path)
            t = clean(r, path)
            assert r.status_code == 200 and all(f'href="{h}"' in t for h in ("/admin", "/admin/users", "/admin/schema", "/admin/reports",
                                                                            "/admin/tickets", "/admin/setup", "/app")), path
            assert f'<a href="{cur}" aria-current=page>' in t and len(re.findall(r"<a [^>]*aria-current", t)) == 1, (path, "현재 메뉴 하나")
        r = await get("/admin")
        t = clean(r, "/admin")
        assert all(e in t for e in P4.values()) and "미연결 사용량" in t and "RATE-LIMIT" in t and "record_add" in t, t[-1500:]
        assert "G-RENAMED" not in t and "G-OBJ" not in t, "개요엔 이름이 없다"
        bar = t[t.index('<div class="bar">'):]
        bar = bar[:bar.index("</div>")]
        assert "이름 공유 동의" in bar and "처리방침" not in bar and bar.count("동의") == 1, ("상태 띠: 처리방침 동의 항목 없음", bar)
        assert "consent" not in admin_db.ALLOW["report.users"]["dims"] and "consent" not in admin_db.ALLOW["report.users"]["filters"]
        assert "consented" not in admin_db.ALLOW["report.users"]["cols"] + admin_db.ALLOW["setup.users"]["cols"] + admin_db.ALLOW["overview.band"]["cols"]
        assert [k for k, _, _ in admin_db.REPORT_PRESETS] == ["errors14", "linked14", "defs", "types", "tickets"], "프리셋 「사용자 동의」 없음"
        assert "consent" not in admin_db.VALUE_LABELS
        assert all("agreements" not in (v["sql"] or "") for v in admin_db.ALLOW.values()), "운영 조회는 agreements 를 읽지 않는다"
        r = await get("/admin/reports?ds=users&g1=consent")
        assert r.status_code == 400, "사용자 동의 차원은 없다"
        for k, _, href in web.preset_links(today):   # 개요 표의 리포트 링크 = 프리셋
            if k in ("errors14", "defs"):
                assert f'href="{web._esc(href)}"' in t or f'href="{href}"' in t, (k, href)
        pq = admin_db.preset_params(web._preset("errors14"), today)
        ov = admin_db.report("admin", "usage", (pq["g1"], pq["g2"]), {"result": pq["result"]}, pq["from"], pq["to"], today)
        assert ("record_add", "RATE-LIMIT") in [r_["keys"] for r_ in ov["rows"]], "프리셋 「오류 14일」 = 개요 오류 표"
        assert not any(r_["keys"][1] == "OK" for r_ in ov["rows"])
        dist = admin_db.report("admin", "structure", ("objects_bucket", "fields_bucket"), {}, today=today)
        cnt = admin_db.structure_counts(admin_db.report_rows("admin", "structure"))
        assert cnt[U["g"]] == (7, sum(len(o["fields"]) for o in web.template_schema()["objects"]) + 2), cnt[U["g"]]
        assert sum(r_["values"]["users"] for r_ in dist["rows"]) == len(cnt), "구간 = 사용자 전원(0 구간 포함)"
        # ── Object Manager(PR A): 서비스 표준(코어 5 + std@1 3) · 별도 템플릿 구역 · 사용자 목록은 Users 로 옮겼다 ──
        r = await get("/admin/schema")
        t = clean(r, "/admin/schema")
        assert "이전 원작자 템플릿 · author@1" in t and 'href="/admin/schema/templates/author-1"' in t, "템플릿 구역"
        for oid in ("core-question", "core-record", "core-candidate", "core-ticket", "core-source", "std-axis", "std-decision", "std-principle"):
            assert f'href="/admin/schema/objects/{oid}"' in t, ("서비스 오브젝트", oid)
        for w in ("M-D", "Lookup", "근거 기록", "변경 이력", "붙는 곳 · 관심 축"):
            assert w in t, ("관계", w)
        # 문의(tickets)도 코어 표준 오브젝트 — 필드는 schema.sql 그대로, 이력은 부모 쪽 M-D 관계로만, 소유자·운영자 전용 열은 없다
        std = [x for x in web.core_schema(web.SCHEMA_SQL.read_text(encoding="utf-8")) if x["table"] == "tickets"]
        assert len(std) == 1 and std[0]["label"] == "문의", std
        names = {f["name"] for f in std[0]["fields"]}
        assert {"kind", "status", "via", "page", "text", "reply", "reply_at", "public_updated", "closed_at"} <= names, names
        assert not names & {"user_id", "admin_note", "hint"}, ("소유자·운영자 전용 열은 표준 구조에 없다", names)
        assert std[0]["links"] == [{"field": "ticket_history.ticket_id", "target": "tickets", "kind": "M-D", "required": True}], std[0]["links"]
        assert [x["table"] for x in web.core_schema(web.SCHEMA_SQL.read_text(encoding="utf-8"))] == ["questions", "records", "candidates", "tickets", "sources"]
        r = await get("/admin/schema/objects/core-ticket")
        d = clean(r, "/admin/schema/objects/core-ticket")
        assert "<h1>문의</h1>" in d and "문의 변경 이력(오브젝트 아님)" in d and "공개 답변" in d, "문의 오브젝트·이력 관계"
        assert "admin_note" not in d and "내부 메모" not in d and "개인정보 의심" not in d, "표준 구조 화면에도 운영자 전용 열이 없다"
        assert "G-RENAMED" not in t and "G-FLD" not in t and "admin_audit" not in t and "usage_daily" not in t, "목록엔 사용자 이름·DB 내부 테이블 없음"
        assert "/admin/users/" not in t, "사용자 목록 표는 Users 로"
        t = clean(await get("/admin/users"), "/admin/users")
        for k in ("g", "n", "w", "o"):
            assert f'href="/admin/users/{site._opaque(U[k])}"' in t, k
        # ── 옛 사용자 트리(/admin/schema?u=) → 새 상세로 302. 상세: 동의자만 현재 이름(PR #20 Q5 — 계약 출력) ──
        r = await get(f"/admin/schema?u={site._opaque(U['g'])}")
        assert r.status_code == 302 and r.headers["location"] == f"/admin/users/{site._opaque(U['g'])}", (r.status_code, r.headers)
        r = await get(f"/admin/users/{site._opaque(U['g'])}")
        t = clean(r, "/admin/users/g")
        assert r.status_code == 200 and "&lt;script&gt;alert(1)&lt;/script&gt;G-OBJ" in t and "&lt;b&gt;G-OPT&lt;/b&gt;" in t, t[-1500:]
        assert "G-RENAMED" in t and "G-FLD" in t and "G-REF" in t and "동의 — 현재 이름" in t, "템플릿 유래라도 바꾼 이름은 동의자만"
        assert "오브젝트 1" in t and "필드 a" in t and "N:N" in t, "자리표·관계"
        assert "p4u" not in t and "pick" not in t and "tpl:" not in t and "v1" not in t, "사용자가 지은 id·key·선택지 값·tpl 표시 이름은 보이지 않는다"
        for k in ("n", "w", "o"):
            r = await get(f"/admin/users/{site._opaque(U[k])}")
            t = clean(r, f"/admin/users/{k}")
            assert r.status_code == 200 and "오브젝트 1" in t and 'class="lbl"' not in t and "동의 없음" in t, (k, t[-800:])
            assert admin_db.schema_names("admin", U[k]) == ([], []), k
        # 철회하면 그 뒤 조회부터 이름이 빠진다
        db.optional_consent_set(U["g"], "struct_names", False)
        t = (await get(f"/admin/users/{site._opaque(U['g'])}")).text
        assert "G-RENAMED" not in t and "G-OBJ" not in t and "G-FLD" not in t, "철회 뒤 이름 0"
        db.optional_consent_set(U["g"], "struct_names", True)
        expect("FORBIDDEN", admin_db._rows, "schema.object_names", "admin", (U["g"], "s1"))   # 이름 키는 일반 조회 경로로 못 연다
        expect("FORBIDDEN", admin_db.schema_names, "user", U["g"])
        for path, code in ((f"/admin/schema?u={'0' * 12}", 404), ("/admin/schema?u=x&u=y", 400), ("/admin/schema?x=1", 400),
                           (f"/admin/schema?u={site._opaque(U['g'])}&zz=1", 400), (f"/admin/users/{'0' * 12}", 404), ("/admin/users?x=1", 400),
                           (f"/admin/users/{site._opaque(U['g'])}?zz=1", 400)):
            r = await get(path)
            assert r.status_code == code and "<script" not in r.text.lower(), (path, r.status_code)
        # ── 리포트 구조 출처 = contract.origin_of 순서(PR #20 R4): std_id 가 template_slot 보다 먼저 ──
        with db.tx() as c:   # 시드가 template_slot 을 칠한 행에 std_id 를 단다(옛 백필 + 편입) — 표준이어야 한다
            assert c.execute("UPDATE object_defs SET std_id='std:principles' WHERE user_id=%s AND key='principles' "
                             "AND template_slot IS NOT NULL", (U["g"],)).rowcount == 1
            assert c.execute("UPDATE field_defs SET std_id='std:principles.axes' WHERE user_id=%s AND object_id='od_principles' "
                             "AND key='axes' AND template_slot IS NOT NULL", (U["g"],)).rowcount == 1
        ref = {k: hashlib.md5(k.encode()).hexdigest() for k in ("od_principles", "od_axes", "od_p4u")}
        gf = [(r_, admin_db._facts("structure", r_, today)) for r_ in admin_db.report_rows("admin", "structure") if r_["user_id"] == U["g"]]
        og = {r_["object_ref"]: f_["origin"] for r_, f_ in gf}
        assert (og[ref["od_principles"]], og[ref["od_axes"]], og[ref["od_p4u"]]) == ("standard", "template", "user"), og
        fo = {(r_["object_ref"], r_["field_std"] or r_["field_slot"] or ""): f_["field_origin"] for r_, f_ in gf if f_["field_origin"]}
        assert fo[(ref["od_principles"], "std:principles.axes")] == "standard", fo
        assert {v for (o_, _), v in fo.items() if o_ == ref["od_principles"]} == {"standard", "template"}, "같은 오브젝트의 나머지 필드는 템플릿"
        assert {v for (o_, _), v in fo.items() if o_ == ref["od_p4u"]} == {"user"}, fo
        assert "standard" in admin_db.report_spec("structure")["filters"]["origin"]
        ot = structure_svg.ORIGIN_TEXT   # 리포트 출처 라벨 = 앱 용어(키 user ↔ 앱 custom) · 다른 차원의 user 라벨은 그대로
        assert admin_db.VALUE_LABELS["origin"] == admin_db.VALUE_LABELS["field_origin"] == {
            "standard": ot["standard"], "template": ot["template"], "user": ot["custom"]}, admin_db.VALUE_LABELS["origin"]
        assert admin_db.VALUE_LABELS["role"]["user"] == "사용자"
        st = admin_db.report("admin", "structure", ("origin",), {"origin": "standard"}, today=today)
        assert st["rows"] and st["rows"][0]["values"]["objects"] >= 1, st
        for path in ("/admin/reports?ds=structure&g1=origin", "/admin/reports?ds=structure&g1=field_origin&origin=standard"):
            r = await get(path)
            t = clean(r, path)
            assert r.status_code == 200 and "표준" in t, (path, r.status_code)
            assert "G-RENAMED" not in t and "G-OBJ" not in t and "G-FLD" not in t, (path, "리포트에 이름 없음")
        # ── 리포트: 프리셋 · 모든 차원·필터 200, 이름 0 ──
        urls = admin_report_urls()
        assert len([u for u in urls if "ds=" in u]) > 40, len(urls)
        for path in urls + [web._report_href({"ds": "structure", "g1": "objects_bucket", "g2": "fields_bucket"}),
                            "/admin/reports?ds=usage&g1=day&g2=&result=&from=&to="]:
            r = await get(path)
            t = clean(r, path)
            assert r.status_code == 200, (path, r.status_code, t[-300:])
            assert "G-RENAMED" not in t and "G-OBJ" not in t and "G-FLD" not in t, (path, "리포트에 이름 없음")
        r = await get(web._report_href({"ds": "usage", "g1": "name", "g2": "code", "result": "error",
                                        "from": today.isoformat(), "to": today.isoformat()}))
        assert "RATE-LIMIT" in r.text and "<th>산출 근거</th>" in r.text and "<th>출처</th>" not in r.text and "집계식" in r.text and "sum(n)" in r.text, "지표 정의 표"
        # 행 상한 — 이름 205개 → 상위 200
        out = admin_db.report("admin", "usage", ("name",), {}, today.isoformat(), today.isoformat(), today)
        assert out["capped"] and len(out["rows"]) == admin_db.REPORT_ROWS and out["total"] >= 205, (out["total"], len(out["rows"]))
        r = await get(web._report_href({"ds": "usage", "g1": "name", "from": today.isoformat(), "to": today.isoformat()}))
        assert r.status_code == 200 and "상위 200행만" in r.text and r.text.count('class="barf"') == admin_db.REPORT_ROWS, r.text.count('class="barf"')
        # ── 리포트: 모르는 키·조합 400(다른 리포트로 대체하지 않는다) ──
        far = (today - datetime.timedelta(days=180)).isoformat()
        bads = ["ds=nope", "ds=usage&g1=nope", "ds=usage&g1=kind", "ds=usage&kind=bug", "ds=usage&result=maybe", "ds=usage&zzz=1",
                "ds=usage&g2=day", "ds=usage&g1=day&g2=day", "ds=structure&from=2026-01-01", "ds=usage&from=2026-13-01",
                f"ds=usage&from={far}&to={today.isoformat()}", f"ds=usage&from={today.isoformat()}&to={far}",
                "ds=structure&g1=objects_bucket&g2=value_type", "ds=structure&g1=objects_bucket&archived=no", "ds=usage&ds=users",
                "g1=day", "ds=users&g1=signup_month&g2=signup_week&g3=role", "ds=usage&%3Cscript%3E=1", "ds=%3Cscript%3Ex"]
        for qs in bads:
            r = await get(f"/admin/reports?{qs}")
            assert r.status_code == 400 and "ARG-INVALID" in r.text and "<script" not in r.text.lower(), (qs, r.status_code, r.text[-300:])
            assert 'class="barf"' not in r.text, (qs, "400 은 결과 표를 그리지 않는다")
        r = await get("/admin/reports?ds=usage&%3Cscript%3E=1")
        assert "&lt;script&gt;" in r.text, "모르는 키 이름은 escape 해서 보인다"
        # 함수 단위 — 기간 경계
        assert admin_db.report_window("usage", None, None, today) == (today - datetime.timedelta(days=179), today)
        expect("ARG-INVALID", admin_db.report_window, "usage", far, today.isoformat(), today)
        expect("ARG-INVALID", admin_db.report_check, "tickets", ("kind", "status", "via"), {})
    finally:
        site.dev, site.dev_user = real
        with db.tx() as c:
            c.execute("DELETE FROM usage_daily WHERE user_id='' AND name='p4:unlinked'")
            c.execute("UPDATE object_defs SET std_id=NULL WHERE user_id=%s AND std_id='std:principles'", (U["g"],))
            c.execute("UPDATE field_defs SET std_id=NULL WHERE user_id=%s AND std_id='std:principles.axes'", (U["g"],))
    print("OK — 운영 화면(개요·스키마·리포트: 404·옛 ra 통과·CSP·메뉴·이름 공유 허용/금지·철회·escape·400·행 상한·프리셋·표준 구조) 통과")


async def run():
    db.init_schema()   # 서버 시작과 같은 멱등 마이그레이션 — 시험 DB 가 직전 코드의 스키마여도 새 열·admin_audit 를 깐다(cleanup 이 쓴다)
    cleanup()
    try:
        test_migration()
        test_core_pr1()
        await test_verifier()
        await test_tools()
        await test_http()
        await test_web()
        await test_start_assets()
        A = test_definitions()
        await test_definition_tools()
        await test_web_items(A)
        test_template_slot(A)
        await test_limits_sse()
        await test_limits()
        await test_feedback()
        await test_feedback_admin()
        test_feedback_units()
        test_consent_versions()
        await test_consent_gate()
        await test_daily_limits()
        await test_usage()
        test_alpha2_delete()
        test_alpha2_delete_lock()
        await test_beta2()
        await test_beta2_bootstrap()
        test_admin_boundary_static()
        await test_admin_boundary()
        await test_optional_consent()
        await test_ticket_p3()
        await test_ticket_pages()
        await test_admin_screens()
        await test_app_account_menu()
        await test_integration_p5()
        await test_r1_flags_off()
        test_r2_contract()
        await test_r3_std()
        await test_q2_std()
        await test_q3()
        await test_q5_admin()
        await test_q45_user()
        await test_prA_setup()
    finally:
        with db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchall()]
        cleanup()   # 후보·질문·정의·항목이 있는 사용자 삭제가 끝까지 되는지도 여기서 확인된다
        with db.tx() as c:
            left = c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (list(TEST_EMAILS),)).fetchone()["n"]
            rest = {t: c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = ANY(%s)", (ids,)).fetchone()["n"]
                    for t in ("object_defs", "field_defs", "items", "item_links", "item_history", "records", "candidates", "tickets",
                              "agreements", "usage_daily", "optional_consents", "optional_consent_log", "std_marks", "std_choices",
                              "sources", "user_roles")}
            rest["admin_audit"] = c.execute("SELECT count(*) AS n FROM admin_audit WHERE actor_id = ANY(%s) OR target_id = ANY(%s)",
                                            (ids, ids)).fetchone()["n"]
        db.close()
    assert left == 0, left
    assert not any(rest.values()), rest
    print("OK — 시험 사용자 삭제(cascade)")


# ── 15) /app 계정 메뉴(P5) ─────────────────────────────

AM = {"user": "am-user@example.com", "nocon": "am-nocon@example.com"}   # user = 일반 · nocon = 동의 행 없음


async def test_app_account_menu():
    """users.desk_hidden · POST /api/desk-visibility(401 → 403 → 400 → 저장, no-store, 게이트 밖) · /api/state user 두 키
    · MCP state_get 에는 없음 · app.html 정적. 시험 사용자는 이 절에서 만들고 끝에 지운다(TEST_EMAILS 밖)."""
    site = main.site
    emails = list(AM.values())

    def wipe():
        with db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (emails,)).fetchall()]
        for i in ids:
            db.user_delete(i)

    def hidden_of(uid):
        with db.tx() as c:
            return c.execute("SELECT desk_hidden FROM users WHERE id=%s", (uid,)).fetchone()["desk_hidden"]

    wipe()
    U = {k: db.user_by_subject(f"am-{k}_sub", e)["id"] for k, e in AM.items()}
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    origin = {"origin": PUBLIC}

    def client(uid=None):
        h = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC)
        if uid:
            h.cookies.set("crm_session", site.session_value(uid))
        return h
    try:
        # 열: schema.sql 최종형 = boolean NOT NULL DEFAULT false (옛 DB 마이그레이션 모양은 test_migration 의 users 비교가 본다)
        with db.tx() as c:
            col = c.execute("SELECT data_type,is_nullable,column_default FROM information_schema.columns "
                            "WHERE table_schema=current_schema() AND table_name='users' AND column_name='desk_hidden'").fetchone()
        assert col == {"data_type": "boolean", "is_nullable": "NO", "column_default": "false"}, col
        assert hidden_of(U["user"]) is False, "새 사용자는 보임"
        # 401 — 세션 없음(Origin 이 맞아도)
        async with client() as h:
            r = await h.post("/api/desk-visibility", json={"hidden": True}, headers=origin)
            assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED", r.text
        async with client(U["user"]) as h:
            u = (await h.get("/api/state")).json()["user"]
            assert u["email"] == AM["user"] and u["desk_hidden"] is False and u["struct_names"] is False, u
            # 403 — Origin 없음·다른 Origin (본문이 틀려도 403 이 먼저)
            for hd in ({}, {"origin": "https://evil.example"}):
                r = await h.post("/api/desk-visibility", json={"hidden": True}, headers=hd)
                assert r.status_code == 403 and r.json()["code"] == "ORIGIN", (hd, r.text)
            r = await h.post("/api/desk-visibility", json={"hidden": "x"})
            assert r.status_code == 403, "Origin 이 본문 검사보다 먼저"
            # 400 — JSON {hidden: bool} 만
            for bad in ({"hidden": "true"}, {"hidden": 1}, {"hidden": None}, {}, [True], "hidden"):
                r = await h.post("/api/desk-visibility", json=bad, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad, r.text)
            r = await h.post("/api/desk-visibility", content=b"hidden=1", headers={**origin, "content-type": "application/x-www-form-urlencoded"})
            assert r.status_code == 400, "JSON 이 아니면 400"
            assert hidden_of(U["user"]) is False, "거절된 요청은 저장하지 않는다"
            # 저장 왕복
            for want in (True, True, False):
                r = await h.post("/api/desk-visibility", json={"hidden": want}, headers=origin)
                assert r.status_code == 200 and r.json() == {"hidden": want}, r.text
                assert r.headers.get("cache-control") == "no-store", r.headers
                assert hidden_of(U["user"]) is want
                st = await h.get("/api/state")
                assert st.json()["user"]["desk_hidden"] is want and st.headers.get("cache-control") == "no-store"
            # struct_names = 유효 동의(optional_consent_ok) — 비동의 → 동의 → 철회 → 옛 버전
            for on in (True, False, True):
                r = await h.post("/api/optional-consent", json={"kind": "struct_names", "on": on}, headers=origin)
                assert r.status_code == 200, r.text
                u = (await h.get("/api/state")).json()["user"]
                assert u["struct_names"] is on and u["struct_names"] == db.optional_consent_ok(U["user"], "struct_names"), (on, u)
            with db.tx() as c:   # 옛 버전 동의는 유효하지 않다
                c.execute("UPDATE optional_consents SET version='s0' WHERE user_id=%s AND kind='struct_names'", (U["user"],))
            assert (await h.get("/api/state")).json()["user"]["struct_names"] is False
            # 두 값은 서로 독립
            await h.post("/api/desk-visibility", json={"hidden": True}, headers=origin)
            u = (await h.get("/api/state")).json()["user"]
            assert u["desk_hidden"] is True and u["struct_names"] is False, u
            assert set(u) == {"email", "struct_names", "desk_hidden"}, "비관리자 user 키는 셋"
        # 동의 행이 없는 사용자도 /app 200 · 바꾼다
        async with client(U["nocon"]) as h:
            assert not _agreements(U["nocon"])
            r = await h.get("/app")
            assert r.status_code == 200, r.status_code
            r = await h.post("/api/desk-visibility", json={"hidden": True}, headers=origin)
            assert r.status_code == 200 and hidden_of(U["nocon"]) is True, r.text
        # MCP state_get(db.state)에는 두 키가 없다
        for uid in U.values():
            st = db.state(uid)
            assert "desk_hidden" not in st and "struct_names" not in st and not ({"desk_hidden", "struct_names"} & set(st.get("user") or {})), st.keys()
        site.dev, site.dev_user = real
        async with Client(main.mcp) as mc:
            st = val(await mc.call_tool("state_get", {}))
        assert "desk_hidden" not in json.dumps(st) and "struct_names" not in json.dumps(st), "MCP state_get 응답에 두 키 없음"
        # app.html 정적: 인라인 스크립트 1개(CSP 해시 = 보낸 본문) · 인라인 이벤트 속성 0 · 메뉴 항목 · ⚙ 유지 · #who 말줄임
        html = (HERE / "app.html").read_text(encoding="utf-8")
        assert len(web.inline_script_hashes(html)) == 1 and html.count("<script>") == 1, "인라인 스크립트 1개"
        assert not re.search(r"\son[a-z]+\s*=", html, re.I), "인라인 이벤트 속성 0"
        menu = html[html.index('<details class="acct" id="acct">'):html.index("</details>")]
        for act in ("send", "mine", "privacy", "names", "logout"):
            assert f'data-acct="{act}"' in menu, act
        for w in ('<span id="who"></span>', 'id="logout"', 'id="acct-names"', "구조 이름 공유", "로그아웃"):
            assert w in menu, w
        for w in ('data-acct="desk"', 'id="acct-desk"', "문의 섹션"):   # Q1b: 숨기기 메뉴 없음(서버 API·열은 남는다)
            assert w not in menu, w
        assert 'id="setup-link"' not in menu and 'id="setup-link"' in html, "⚙ 는 메뉴 밖에 그대로"
        assert re.search(r"#who \{[^}]*text-overflow: ellipsis[^}]*\}", html) and re.search(r"#who \{[^}]*min-width: 0", html), "#who 말줄임"
        for w in ('fetch(url, {method: "POST"', '"/api/optional-consent", {kind: "struct_names"',
                  '[name="kind"][value="privacy"]', '$("desk-dlg").showModal()'):
            assert w in html, w
        for w in ("/api/desk-visibility", "desk_hidden", "deskPeek", 'id="desk"'):   # Q1b: 화면은 숨김 상태를 안 쓴다
            assert w not in html, w
        async with client(U["user"]) as h:
            r = await h.get("/app")
            assert r.status_code == 200 and f"'sha256-{web.inline_script_hashes(r.text)[0]}'" in r.headers["content-security-policy"], "CSP 해시 = 보낸 본문"
    finally:
        site.dev, site.dev_user = real
        wipe()
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (emails,)).fetchone()["n"] == 0
    print("OK — /app 계정 메뉴(desk_hidden·/api/desk-visibility·state 두 키·MCP 없음·app.html 정적) 통과")


# ── 16) 통합(P5-6) — 운영 콘솔 묶음 최종 ─────────────────
# /admin 의 GET 화면 전부(개요·Setup·옛 재인증 링크·문의·스키마·리포트).
INT = {"a": "int-admin@example.com", "b": "int-admin2@example.com", "o": "int-owner@example.com", "x": "int-other@example.com",
       "n": "int-names@example.com"}
INT_ADMIN_GETS = ("/admin", "/admin/setup", "/admin/reauth", "/admin/tickets", "/admin/schema", "/admin/reports", "/admin/users",
                  "/admin/find", "/admin/schema/objects/core-record", "/admin/schema/templates/author-1", "/admin/schema/templates/author-1/objects/tpl-axis")
INT_TICKET_OPS = (("status", {"status": "doing"}), ("reply", {"reply": "공개 답변"}), ("note", {"admin_note": "내부 메모 INTNOTE"}),
                  ("reclassify", {"kind": "request", "reason": ""}))


async def test_integration_p5():
    """권한 회수 뒤 /admin 전부 404·문의 POST 거절(세션 끊김·새 세션 둘 다) · 이름 공유: 동의자 이름은 /admin/users 상세에만 보이고
    리포트(프리셋·구조)엔 없음, 철회 뒤 다음 조회부터 상세에도 0 · 문의 소유권(웹·MCP) · 공개/내부 이력 · 「내 문의」 순서(최근 공개 갱신,
    없으면 접수 — 웹·MCP 같음) · 파기 cascade(privacy_log 는 남음) · 관리자 세션으로도 /app·/api/state·/api/feedback 에 관리자 데이터 0."""
    site = main.site
    A = db.user_by_subject("int_a_sub", INT["a"])["id"]
    B = db.user_by_subject("int_b_sub", INT["b"])["id"]
    O = db.user_by_subject("int_o_sub", INT["o"])["id"]
    X = db.user_by_subject("int_x_sub", INT["x"])["id"]
    N = db.user_by_subject("int_n_sub", INT["n"])["id"]
    _set_role(A, "admin")
    _set_role(B, "admin")
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    now = lambda: int(time.time())   # noqa: E731

    async def req(method, path, cookie=None, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.request(method, path, headers={"origin": PUBLIC}, **kw)
    t = _tk_insert(O, kind="privacy", text="통합 문의 INTTEXT")
    with db.tx() as c:
        c.execute("INSERT INTO privacy_log (id,kind,via,ticket_id,received) VALUES (%s,'other','form',%s,(now() AT TIME ZONE 'Asia/Seoul')::date)",
                  (db.new_id("pl"), t))
    try:
        # ── 공개/내부 이력 — 관리자가 웹으로 처리 4종 ──
        ck_a = site.session_value(A)
        for op, form in INT_TICKET_OPS:
            if op == "reclassify":
                form = {"kind": "request", "reason": "not_privacy"}
            r = await req("POST", f"/admin/tickets/{t}/{op}", ck_a, data=form)
            assert r.status_code == 303, (op, r.status_code, r.text[:200])
        rec = (await req("GET", f"/admin/tickets/{t}", ck_a)).text
        assert "INTTEXT" in rec and "INTNOTE" in rec and rec.count('class="tag pub"') == 2 and rec.count('class="tag int"') == 2, "레코드: 공개 2·내부 2"
        # ── 소유권·공개 이력(웹·MCP) ──
        r = await req("GET", f"/api/feedback/{t}", site.session_value(O))
        j = r.json()
        assert r.status_code == 200 and j["reply"] == "공개 답변" and [h["kind"] for h in j["history"]] == ["status", "reply"], j
        assert "INTNOTE" not in r.text and "admin_note" not in j and "reclassify" not in r.text and "not_privacy" not in r.text, r.text
        assert (await req("GET", f"/api/feedback/{t}", site.session_value(X))).status_code == 404, "남의 문의는 404"
        assert (await req("GET", f"/api/feedback/{t}", site.session_value(A))).status_code == 404, "관리자도 /api 로는 남의 문의 404"
        try:
            for email, ok in ((INT["o"], True), (INT["x"], False)):
                os.environ["CRM_DEV_USER"] = email
                async with Client(main.mcp) as c:
                    r = await c.call_tool("ticket_get_mine", {"ticket_id": t})
                    assert r.is_error is (not ok), (email, r.content[0].text[:200])
                    if ok:
                        got = json.loads(r.content[0].text)
                        assert "INTNOTE" not in r.content[0].text and [h["kind"] for h in got["history"]] == ["status", "reply"], got
                    assert (t in {x["id"] for x in val(await c.call_tool("ticket_list_mine", {}))}) is ok, email
        finally:
            os.environ["CRM_DEV_USER"] = TESTER
        # ── 관리자 세션으로도 /app 쪽엔 관리자 데이터 0 ──
        for cookie in (ck_a, site.session_value(A)):
            st = await req("GET", "/api/state", cookie)
            fb = await req("GET", "/api/feedback", cookie)
            app_ = await req("GET", "/app", cookie)
            assert st.status_code == fb.status_code == app_.status_code == 200, (st.status_code, fb.status_code, app_.status_code)
            assert "admin_tickets" not in st.json() and st.json()["user"]["admin"] is True and set(fb.json()) == {"tickets"}
            for out in (st.text, fb.text, app_.text):
                assert "INTTEXT" not in out and "INTNOTE" not in out and INT["o"] not in out, "남의 문의·메모·이메일 없음"
            for gone in ("admin-band", "admin_tickets", "/api/feedback/status", "/api/feedback/note", "문의함"):
                assert gone not in app_.text, gone
        # ── 이름 공유 — 동의자 이름은 /admin/users 상세에만, 리포트엔 언제나 0 · 철회하면 다음 조회부터 상세에도 0 ──
        from psycopg.types.json import Jsonb
        nm = "INTNAME" + secrets.token_hex(3)
        with db.tx() as c:
            c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision) VALUES (%s,'od_int','intobj',%s,'collection',1)",
                      (N, nm + "-obj"))
            c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,options) VALUES (%s,'od_int','f1',%s,'select',%s)",
                      (N, nm + "-fld", Jsonb([{"value": "v", "label": nm + "-opt"}])))
        tree = f"/admin/users/{site._opaque(N)}"
        reports = [href for _, _, href in web.preset_links()] + ["/admin/reports?ds=structure",
                                                                 "/admin/reports?ds=structure&g1=value_type&g2=origin"]

        async def names_seen():
            t_ = await req("GET", tree, ck_a)
            assert t_.status_code == 200, (tree, t_.status_code)
            for href in reports:
                r_ = await req("GET", href, ck_a)
                assert r_.status_code == 200 and nm not in r_.text, (href, r_.status_code, "리포트엔 이름이 없다")
            for path in ("/admin", "/admin/schema", "/admin/setup", "/admin/tickets?status=all", "/admin/users"):
                assert nm not in (await req("GET", path, ck_a)).text, (path, "상세 밖엔 이름이 없다")
            return [x for x in ("-obj", "-fld", "-opt") if nm + x in t_.text]
        assert await names_seen() == [], "비동의 — 트리에 이름 0"
        db.optional_consent_set(N, "struct_names", True)
        assert await names_seen() == ["-obj", "-fld", "-opt"], "동의 — 오브젝트·필드·선택지 현재 이름"
        r = await req("POST", "/api/optional-consent", site.session_value(N), json={"kind": "struct_names", "on": False})
        assert r.status_code == 200 and r.json()["changed"] is True, r.text   # 사용자가 /app 계정 메뉴로 끈다
        assert await names_seen() == [], "철회 뒤 다음 조회부터 이름 0"
        # ── 「내 문의」 순서 — 최근 공개 갱신순, 없으면 접수 시각(웹 표·MCP 같음) ──
        t_old = _tk_insert(O, text="오래된 문의, 오늘 답변", created_days_ago=5)
        t_new = _tk_insert(O, text="어제 문의, 갱신 없음", created_days_ago=1)
        t_mid = _tk_insert(O, text="사흘 전 문의, 갱신 없음", created_days_ago=3)
        assert (await req("POST", f"/admin/tickets/{t_old}/reply", ck_a, data={"reply": "오늘 답했어요"})).status_code == 303
        want = [t_old, t, t_new, t_mid]   # t = 맨 위 처리 4종으로 오늘 공개 갱신(답변보다 먼저)
        web_ids = [x["id"] for x in (await req("GET", "/api/feedback", site.session_value(O))).json()["tickets"]]
        assert [x for x in web_ids if x in want] == want, web_ids
        os.environ["CRM_DEV_USER"] = INT["o"]
        try:
            async with Client(main.mcp) as c:
                mcp_ids = [x["id"] for x in val(await c.call_tool("ticket_list_mine", {}))]
        finally:
            os.environ["CRM_DEV_USER"] = TESTER
        assert [x for x in mcp_ids if x in want] == want and mcp_ids == web_ids, (mcp_ids, web_ids)
        # ── 권한 회수: B 가 A 를 회수 → A 의 옛 세션은 끊기고, 새 세션(role=user)도 /admin 전부 404·처리 POST 거절 ──
        assert db.admin_act(B, "role", A, "user") == {"changed": True}
        n_hist = len(_tk_hist(t))
        for cookie in (ck_a, site.session_value(A)):
            for path in INT_ADMIN_GETS + (f"/admin/tickets/{t}",):
                r = await req("GET", path, cookie)
                assert r.status_code == 404, (path, r.status_code)
            for op, form in INT_TICKET_OPS:
                assert (await req("POST", f"/admin/tickets/{t}/{op}", cookie, data=form)).status_code == 404, op
            for path, form in (("/admin/role", {"uid": O, "role": "admin"}), ("/admin/signout", {"uid": O}), ("/admin/block", {"uid": O})):
                assert (await req("POST", path, cookie, data=form)).status_code == 404, path
        expect("FORBIDDEN", db.ticket_set_status, A, t, "ack")   # DB 단 실행자 재조회도 거절
        assert len(_tk_hist(t)) == n_hist and _urow(O)["role"] == "user", "회수된 관리자는 아무것도 못 바꾼다"
        # ── 파기 cascade — 답변·이력은 함께 지워지고 privacy_log(FK 없음)는 남는다 ──
        db.ticket_set_status(B, t, "done")
        with db.tx() as c:
            c.execute("UPDATE tickets SET closed_at = now() - interval '181 days' WHERE id=%s", (t,))
        assert db.tickets_purge() >= 1 and _ticket(t) is None and _tk_hist(t) == []
        with db.tx() as c:
            assert c.execute("SELECT count(*) AS n FROM privacy_log WHERE ticket_id=%s", (t,)).fetchone()["n"] == 1, "처리 기록은 남는다"
        assert (await req("GET", f"/api/feedback/{t}", site.session_value(O))).status_code == 404
    finally:
        site.dev, site.dev_user = real
        with db.tx() as c:
            c.execute("DELETE FROM privacy_log WHERE ticket_id=%s", (t,))   # FK·cascade 밖
    print("OK — 통합(회수 뒤 /admin 404·처리 거절 · 이름 공유 상세만·리포트 0·철회 뒤 0 · 소유권 웹·MCP · 공개/내부 이력"
          " · 내 문의 공개 갱신순 웹=MCP · 파기 cascade · /app 관리자 데이터 0) 통과")


# ── 17) R1 — 기능 플래그 둘 다 미설정(off) 기준선 · Q1 관리자 /app 메뉴 ─────────
# 플래그 코드는 3단계 몫이다. 이 절은 두 env 를 지운 상태(= off, 명세 §7)에서 로그인·/admin·문의 처리·Q1 이 되는지를 고정한다.
R1 = {"a": "r1-admin@example.com", "u": "r1-user@example.com"}
R1_FLAGS = ("CRM_FEATURE_STANDARD", "CRM_FEATURE_SOURCES")
R1_USER_ONLY = ('data-acct="send"', 'data-acct="mine"', 'data-acct="privacy"', 'data-acct="names"',
                'id="acct-names"', 'id="desk-dlg"', 'id="desk-form"', 'id="desk-list"', "구조 이름 공유")
R1_GONE = ('id="desk"', '<section class="wrap desk"', 'data-acct="desk"', 'id="acct-desk"', "문의 섹션", "deskPeek", "/api/desk-visibility")   # Q1b — 둘 다
R1_ADMIN_ONLY = ('id="acct-console"', '<a class="btn" href="/admin" id="acct-console">운영 콘솔</a>')


async def test_r1_flags_off():
    """두 플래그 미설정에서: 웹 로그인(/login → 콜백 → /app)·DEV 경로 · /admin 무세션·일반·DEV 404, 관리자 GET 화면 전부 바로(재인증 없음,
    /admin/reauth → /admin) · 문의 처리 POST(상태·답변, 일반 사용자 404) → 소유자 「내 문의」에 반영 · Q1: 관리자 /app 계정 메뉴 = 운영 콘솔·로그아웃,
    문의·이름 공유 메뉴·문의 대화창은 본문에 없음(서버가 안 보냄) · Q1b: 일반 사용자는 하단 문의 섹션 없이 「문의 보내기」·「내 문의」 메뉴가
    대화창(desk-dlg)을 연다, 숨기기 메뉴 없음 · 표식 0 · CSP 해시 = 보낸 본문 · API 모양은 그대로."""
    from urllib.parse import parse_qs, urlparse
    site = main.site
    fake_jwks(main._auth_kwargs["token_verifier"])
    saved = {k: os.environ.pop(k, None) for k in R1_FLAGS}
    A = db.user_by_subject("r1a_sub", R1["a"])["id"]
    U = db.user_by_subject("r1u_sub", R1["u"])["id"]
    _set_role(A, "admin")
    real = (site.dev, site.dev_user, site.client_id, site.token_post)
    site.dev, site.dev_user, site.client_id = False, "", "client_test"
    tok = {"sub": ""}

    async def fake_token_post(data):
        return 200, {"access_token": make_token(sub=tok["sub"]), "token_type": "Bearer"}
    site.token_post = fake_token_post

    async def login(sub):
        """/login → /auth/callback(가짜 토큰 교환) → 302 /app. 새 세션 쿠키 값."""
        tok["sub"] = sub
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            r = await h.get("/login")
            st = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
            r = await h.get("/auth/callback", params={"code": "c", "state": st})
            assert r.status_code == 302 and r.headers["location"] == "/app", (sub, r.status_code, r.text[:200])
            return r.cookies.get("crm_session")

    async def req(method, path, cookie=None, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.request(method, path, headers={"origin": PUBLIC}, **kw)

    def q1(r, admin):
        """Q1·Q1b — /app 본문. 관리자 = ⚙ + 운영 콘솔·로그아웃만, 일반 = 문의 메뉴 + 문의 대화창(하단 섹션 없음). 표식은 어느 쪽에도 남지 않는다."""
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
        hs = web.inline_script_hashes(r.text)
        assert len(hs) == 1 and f"'sha256-{hs[0]}'" in r.headers["content-security-policy"], "CSP 해시 = 보낸 본문"
        assert not re.search(r"<!--/?(user|admin)-->", r.text), "표식 0"
        for w in ('id="setup-link"', 'id="acct-menu"', 'id="logout"'):
            assert w in r.text, w
        for w in R1_USER_ONLY:
            assert (w in r.text) is not admin, (admin, w)
        for w in R1_ADMIN_ONLY:
            assert (w in r.text) is admin, (admin, w)
        for w in R1_GONE:
            assert w not in r.text, (admin, w)
        if not admin:   # Q1b: 문의 폼·목록은 대화창 안에만 있다
            i = r.text.index('<dialog id="desk-dlg"')
            dlg = r.text[i:r.text.index("</dialog>", i)]
            for w in ('id="desk-form"', 'id="desk-list"', 'id="desk-text"', 'id="desk-send"'):
                assert r.text.count(w) == 1 and w in dlg, w
            menu = r.text[r.text.index('id="acct-menu"'):r.text.index("</details>")]
            for act in ("send", "mine"):
                assert f'data-acct="{act}" aria-haspopup="dialog"' in menu, act
        if admin:
            menu = r.text[r.text.index('id="acct-menu"'):r.text.index("</details>")]
            assert re.findall(r"<(?:a|button)\b[^>]*>([^<]*)", menu) == ["운영 콘솔", "로그아웃"], menu
    try:
        assert not any(k in os.environ for k in R1_FLAGS), "두 플래그 미설정"
        # ── 로그인 — 웹(세션 쿠키) · DEV ──
        ck_a, ck_u = await login("r1a_sub"), await login("r1u_sub")
        assert site.unsign(ck_a)["uid"] == A and site.unsign(ck_u)["uid"] == U
        for ck, admin in ((ck_a, True), (ck_u, False)):
            q1(await req("GET", "/app", ck), admin)
            st = await req("GET", "/api/state", ck)
            assert st.status_code == 200 and (st.json()["user"].get("admin") is True) is admin, st.json()["user"]
            assert set(st.json()["user"]) == {"email", "struct_names", "desk_hidden"} | ({"admin"} if admin else set()), "API 모양 그대로"
            fb = await req("GET", "/api/feedback", ck)
            assert fb.status_code == 200 and set(fb.json()) == {"tickets"}, fb.text
        for email, admin in ((R1["u"], False), (R1["a"], True)):
            site.dev, site.dev_user = True, email
            q1(await req("GET", "/app"), admin)
            assert (await req("GET", "/api/state")).json()["user"]["email"] == email
            assert (await req("GET", "/admin")).status_code == 404, "DEV 는 관리자여도 /admin 404"
        site.dev, site.dev_user = False, ""
        # ── 관리자 권한 — 무세션·일반 404 · 관리자 바로(재인증 없음) ──
        for path in INT_ADMIN_GETS:
            for ck in (None, ck_u):
                assert (await req("GET", path, ck)).status_code == 404, (path, ck is None)
            r = await req("GET", path, ck_a)
            if path == "/admin/reauth":
                assert r.status_code == 302 and r.headers["location"] == "/admin", r.status_code
            else:
                assert r.status_code == 200 and "재인증" not in r.text, (path, r.status_code)
        # ── /admin 문의 처리 — 상태·답변(일반 사용자는 404, 바뀌지 않음) ──
        t = _tk_insert(U, text="R1 문의 R1TEXT")
        ops = (("status", {"status": "doing"}), ("reply", {"reply": "R1 답변"}))
        for op, form in ops:
            assert (await req("POST", f"/admin/tickets/{t}/{op}", ck_u, data=form)).status_code == 404, op
        assert _ticket(t)["status"] == "open" and _tk_hist(t) == [], "일반 사용자 POST 는 아무것도 안 바꾼다"
        for op, form in ops:
            r = await req("POST", f"/admin/tickets/{t}/{op}", ck_a, data=form)
            assert r.status_code == 303 and r.headers["location"] == f"/admin/tickets/{t}", (op, r.status_code, r.text[:200])
        rec = await req("GET", f"/admin/tickets/{t}", ck_a)
        assert rec.status_code == 200 and "R1TEXT" in rec.text, rec.status_code
        j = (await req("GET", f"/api/feedback/{t}", ck_u)).json()
        assert j["status"] == "doing" and j["reply"] == "R1 답변" and [h["kind"] for h in j["history"]] == ["status", "reply"], j
        assert not any(k in os.environ for k in R1_FLAGS), "시험 중에도 미설정"
    finally:
        site.dev, site.dev_user, site.client_id, site.token_post = real
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("OK — R1 플래그 off 기준선(로그인 웹·DEV · /admin 일반 404·관리자 바로·재인증 없음 · 문의 처리 · Q1 관리자 /app 메뉴 · Q1b 문의 대화창) 통과")


# ── 18) R2 — 응답 계약 · 플래그 해석 · 오류 코드 (DB 없음) ─────────
# 명세 §7·§8·§1·§6. 화면·DB 열은 다음 단계 — 여기선 contract 순수 함수와 동결 예제(server/contract/*.json)만 고정한다.
R2_FORBID_ADMIN = {"id", "key", "value", "name", "location", "object_id", "field_key", "target_object_id"}   # counts.sources(개수)는 허용 — 출처 목록은 최상위 키 검사가 막는다
R2_ADMIN_OBJ = {"ref", "std_name", "semantic_kind", "origin", "std_id", "locked", "fields"}
R2_ADMIN_FIELD = {"ref", "std_name", "origin", "std_id", "locked"}
R2_ADMIN_REL = {"from", "field", "to", "cardinality", "origin", "std_id", "locked"}


def _r2_keys(x):
    """응답 안 모든 dict 의 키 — std.objects 의 표준 key 는 값 쪽 제품 문자열이라 뺀다."""
    out = set()
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "objects" and isinstance(v, dict):
                continue
            out.add(k)
            out |= _r2_keys(v)
    elif isinstance(x, list):
        for v in x:
            out |= _r2_keys(v)
    return out


def _r2_user_strings(user):
    """사용자가 정한 문자열 — (label 류, key·id·선택지 값·출처 류). 표준 key·표준 필드 key 는 std_id·std 상태의 제품 문자열이라 key 쪽에서 뺀다."""
    std_keys = {k for sid in contract.STD_NAMES for k in sid[4:].split(".")}
    labels, keys = set(), set()
    for o in user["definitions"]:
        labels.add(o["label"])
        keys.add(o["id"])
        if o["key"] not in std_keys:
            keys.add(o["key"])
        for f in o["fields"]:
            labels.add(f["label"])
            labels |= {op["label"] for op in f["options"]}
            keys |= {op["value"] for op in f["options"]}
            if f["key"] not in std_keys:
                keys.add(f["key"])
    for s in user["sources"]:
        keys |= {s["id"], s["name"], s["location"]}
    return labels, keys


def test_r2_contract():
    """플래그: CRM_FEATURE_* 는 정확히 `on` 만 켬(호출마다 env) · render.yaml 두 키 sync:false
    · 예제: server/contract/*.json == contract.respond(EXAMPLES) 직렬화(6개, 그 밖 파일 없음)
    · 누출: 운영자 비동의 응답 문자열에 사용자 label·key·선택지(값·label)·출처 name·location·id 0, 동의 응답에도 key·선택지 값·출처·id 0
      · 운영자 응답의 dict 키 = 허용 목록 · 본인 응답엔 전부 있음
    · 파생: 편입된 기준에 사용자가 더한 필드·참조 필드 = custom·null·false · 관계 = 참조 필드 값 그대로 · N:1/N:N · template · core 노드
      · 미편입 같은 key 오브젝트엔 표준명·std_id 없음 · std 모양(표식 없음 = version·state null, objects 는 선택 그대로) · 표준명 = templates/std@1.json · 오류 코드 3종 문구"""
    # ── 플래그 해석 표 ──
    saved = {k: os.environ.pop(k, None) for k in R1_FLAGS}
    try:
        for name, env_key in (("STANDARD", "CRM_FEATURE_STANDARD"), ("SOURCES", "CRM_FEATURE_SOURCES")):
            other = [k for k in R1_FLAGS if k != env_key][0]
            for v, want in ((None, False), ("on", True), ("off", False), ("ON", False), ("On", False), ("1", False),
                            ("true", False), ("", False), (" on", False), ("on ", False)):
                os.environ.pop(env_key, None)
                if v is not None:
                    os.environ[env_key] = v
                assert contract.feature_on(name) is want, (name, v)
                assert (contract.standard_on() if name == "STANDARD" else contract.sources_on()) is want, (name, v)
                assert contract.feature_on("SOURCES" if name == "STANDARD" else "STANDARD") is False, "다른 플래그는 영향 없음"
                assert other not in os.environ
            os.environ.pop(env_key, None)
        expect_err = False
        try:
            contract.feature_on("standard")
        except ValueError:
            expect_err = True
        assert expect_err, "모르는 플래그 이름은 거절"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    ry = (HERE / "render.yaml").read_text(encoding="utf-8")
    for k in R1_FLAGS:
        assert re.search(rf"- key: {k}\n\s+sync: false\b", ry), k
    # ── 예제 동결 ──
    names = sorted(p.name for p in contract.EXAMPLES_DIR.glob("*.json"))
    assert names == sorted(f"{n}.{v}.json" for n in contract.EXAMPLES for v in contract.VIEWERS), names
    for n, user in contract.EXAMPLES.items():
        for v in contract.VIEWERS:
            got = contract.dump(contract.respond(user, v))
            assert got == contract.example_path(n, v).read_text(encoding="utf-8"), \
                f"동결 예제와 다름: {n}.{v} — 의도한 변경이면 cd server && .venv/bin/python contract.py"
    # ── 수신자별 노출 ──
    for n, user in contract.EXAMPLES.items():
        labels, keys = _r2_user_strings(user)
        assert labels and keys
        r_self, r_no, r_yes = (contract.respond(user, v) for v in contract.VIEWERS)
        s_self, s_no, s_yes = (json.dumps(r, ensure_ascii=False) for r in (r_self, r_no, r_yes))
        for w in labels | keys:
            assert w in s_self, (n, "본인 응답엔 이름이 다 있다", w)
            assert w not in s_no, (n, "비동의 누출", w)
        for w in keys:
            assert w not in s_yes, (n, "동의여도 key·선택지 값·출처 name·location·id 0", w)
        for w in labels:
            assert w in s_yes, (n, "동의 응답엔 현재 label·선택지 label", w)
        for r, consent in ((r_no, False), (r_yes, True)):
            ks = _r2_keys(r)
            assert not ks & R2_FORBID_ADMIN, (n, ks & R2_FORBID_ADMIN)
            assert ("label" in ks) is consent and ("options" in ks) is consent, (n, consent)
            extra = {"label"} if consent else set()
            for o in r["objects"]:
                assert set(o) == R2_ADMIN_OBJ | extra, set(o)
                for f in o["fields"]:
                    assert set(f) == R2_ADMIN_FIELD | (extra | {"options"} if consent else set()), set(f)
                    assert all(isinstance(x, str) for x in f.get("options", [])), "선택지는 label 문자열만"
            for rel in r["relations"]:
                assert set(rel) == R2_ADMIN_REL, set(rel)
            assert set(r) == {"contract", "viewer", "std", "counts", "objects", "relations", "core"}, set(r)
            assert r["std"] == r_self["std"] and r["counts"] == r_self["counts"], "표준 상태 코드·개수는 같다"
        assert "sources" in r_self and r_self["counts"]["sources"] == len(user["sources"]) >= 1
    # ── 파생 — full: 편입된 기준 + 사용자 필드·참조 필드 ──
    full = contract.respond(contract.EXAMPLES["full"], "self")
    byo = {o["id"]: o for o in full["objects"]}
    pr = byo["od_principles"]
    assert (pr["origin"], pr["std_id"], pr["locked"], pr["std_name"]) == ("standard", "std:principles", True, "기준")
    pf = {f["key"]: f for f in pr["fields"]}
    for k in ("ukey_register", "ukey_owner"):
        assert (pf[k]["origin"], pf[k]["std_id"], pf[k]["locked"], pf[k]["std_name"]) == ("custom", None, False, None), k
    assert (pf["axes"]["origin"], pf["axes"]["std_id"], pf["axes"]["locked"]) == ("standard", "std:principles.axes", True)
    rels = {(r["object_id"], r["field_key"]): r for r in full["relations"]}
    assert len(rels) == len(full["relations"]) == sum(f["value_type"] == "reference" for o in full["objects"] for f in o["fields"])
    for (oid, fk), r in rels.items():
        f = next(f for f in byo[oid]["fields"] if f["key"] == fk)
        assert (r["origin"], r["std_id"], r["locked"]) == (f["origin"], f["std_id"], f["locked"]), "관계 = 참조 필드 값 그대로"
        assert r["cardinality"] == ("N:N" if f["multiple"] else "N:1")
    assert (rels[("od_principles", "ukey_owner")]["cardinality"], rels[("od_principles", "ukey_owner")]["origin"],
            rels[("od_principles", "ukey_owner")]["locked"]) == ("N:1", "custom", False)
    assert rels[("od_principles", "evidence_events")]["cardinality"] == "N:N" and rels[("od_principles", "evidence_events")]["locked"]
    assert byo["od_craft"]["origin"] == "template" and rels[("od_craft", "related_events")]["origin"] == "template"
    assert {c["kind"]: c["origin"] for c in full["core"]} == {"record": "core", "candidate": "core"}
    adm = contract.respond(contract.EXAMPLES["full"], "admin_noconsent")
    arel = {(r["from"], r["field"]): r for r in adm["relations"]}
    assert arel[("오브젝트 3", "필드 e")]["to"] == "오브젝트 4" and arel[("오브젝트 3", "필드 e")]["origin"] == "custom"
    assert arel[("오브젝트 2", "필드 b")]["to"] == "candidate" and arel[("오브젝트 4", "필드 b")]["to"] == "record"
    assert full["std"] == {"version": "std@1", "state": "adopting",
                           "objects": {"axes": "incorporated", "decision_events": "incorporated", "principles": "incorporated"}}
    # ── 파생 — partial: 부분 편입 · 같은 key 미편입 오브젝트에 표준명 없음 ──
    part = contract.respond(contract.EXAMPLES["partial"], "self")
    pa = contract.respond(contract.EXAMPLES["partial"], "admin_noconsent")
    assert part["std"] == {"version": "std@1", "state": "adopting",
                           "objects": {"axes": "incorporated", "decision_events": "deferred", "principles": "none"}}
    same = next(o for o in part["objects"] if o["key"] == "decision_events")
    assert (same["origin"], same["std_id"], same["locked"], same["std_name"]) == ("custom", None, False, None)
    i = part["objects"].index(same)
    assert (pa["objects"][i]["std_name"], pa["objects"][i]["std_id"]) == (None, None), "같은 key 라는 이유로 표준명을 붙이지 않는다"
    assert all(f["std_name"] is None for f in pa["objects"][i]["fields"])
    assert "결정" not in json.dumps(pa, ensure_ascii=False), "미편입 오브젝트의 표준명 0"
    # ── std 모양 · origin 우선순위 · 자리표 ──
    none_all = {"version": None, "state": None, "objects": {k: "none" for k in contract.STD_KEYS}}
    assert contract.std_state(None, None) == none_all and contract.respond({}, "self")["std"] == none_all
    # 표식 없음 + 보류·거절 선택만 → objects 는 선택 그대로, version·state 는 null(§0)
    nomark = {"std_choices": {"decision_events": "deferred", "principles": "refused"}}
    want = {"version": None, "state": None, "objects": {"axes": "none", "decision_events": "deferred", "principles": "refused"}}
    assert contract.std_state(None, nomark["std_choices"]) == want
    for v in contract.VIEWERS:
        assert contract.respond(nomark, v)["std"] == want, v
    # 표준명 = templates/std@1.json label(std_id 는 key 로 만든 것, 파일엔 없음)
    sd = json.loads((HERE / "templates" / "std@1.json").read_text(encoding="utf-8"))
    assert contract.STD_VERSION == "std@1" and contract.STD_KEYS == ("axes", "decision_events", "principles")
    assert contract.STD_NAMES == {**{f"std:{o['key']}": o["label"] for o in sd["objects"]},
                                  **{f"std:{o['key']}.{f['key']}": f["label"] for o in sd["objects"] for f in o["fields"]}}
    assert "std:" not in json.dumps(sd), "식별자는 파일에 넣지 않는다"
    assert contract.std_state({"version": "std@1", "state": "seeded"}, {"axes": "bogus"})["objects"]["axes"] == "none"
    assert contract.origin_of({"std_id": "std:axes", "template_slot": "author@1:axes"}) == "standard"
    assert contract.origin_of({"template_slot": "author@1:axes"}) == "template" and contract.origin_of({}) == "custom"
    assert contract.std_name("std:principles.evidence_events") == "근거 결정" and contract.std_name("std@1:axes") is None
    assert [contract.field_ref(i) for i in (0, 25, 26, 27)] == ["필드 a", "필드 z", "필드 aa", "필드 ab"]
    # ── 오류 코드 ──
    e = db._std_locked("archive", "std:principles")
    assert isinstance(e, db.CrmError) and e.code == "STANDARD-LOCKED" and "std:principles" in e.message
    assert set(contract.LOCKED_CHANGES) >= {"archive", "key", "semantic_kind", "std_id", "field_remove", "field_shape", "option_remove"}
    for why in ("author_unknown", "external", "processed", "deleted"):
        e = db._cand_review(why)
        assert e.code == "CANDIDATE-REVIEW" and e.message and "{" not in e.message + e.action, why
        assert "verbatim" not in e.message + e.action and "self" not in e.message + e.action, "「self·verbatim 으로 바꾸면 풀린다」 금지"
        assert ("record_provenance_set" in e.action) is (why in ("author_unknown", "processed")), why
    assert len({contract.REVIEW_MSG[w] for w in contract.REVIEW_REASONS}) == 4
    e = db._scope_self()
    assert e.code == "SCOPE-SELF-ONLY" and "{" not in e.message + e.action
    rr = lambda a, t, d=None: contract.review_reason({"author_kind": a, "text_process": t, "deleted_at": d})
    assert rr("self", "verbatim") is None and rr("unknown", "verbatim") == "author_unknown" and rr("external", "verbatim") == "external"
    assert [rr("self", t) for t in ("structured", "rewritten", "generated", "unknown")] == ["processed"] * 4
    assert rr("external", "generated", "2026-10-19") == "deleted"
    print("OK — R2 계약(플래그 on 만 · render.yaml · 예제 6개 동결 · 비동의·동의 이름 누출 0 · origin·관계 파생 · std 모양 · 오류 코드 3종) 통과")


# ── 19) R3 — 표준 편입 미리보기(읽기 전용) · std_choices 기록(보류·거절) ─────────
R3 = {k: f"r3{k}@example.com" for k in ("none", "match", "conf", "edge", "orig")}
# none = 정의 없음(전부 누락) · match = 일치·확장·참조 대응 · conf = 충돌 사유 · edge = 초안만·보관 · orig = template·std_id 출처


def _r3_def(uid, key, label, kind, fields, oid=None, policy=None, object_id=None, rev=0):
    d = {"key": key, "label": label, "semantic_kind": kind, "fields": fields}
    if oid:
        d["id"] = oid
    if policy is not None:
        d["policy"] = policy
    p = db.definition_propose(uid, object_id, rev, d)
    return db.definition_apply(uid, p["object_id"], p["proposal_id"], rev)


def _r3_tables_digest():
    """현재 스키마 모든 테이블의 행 수·내용 해시 — 미리보기 전후가 같아야 한다(쓰기 0)."""
    with db.tx() as c:
        ts = [r["table_name"] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() "
                                                 "AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
        return {t: c.execute(f"SELECT count(*) AS n, md5(coalesce(string_agg(x::text, ',' ORDER BY x::text), '')) AS h FROM \"{t}\" x")
                .fetchone() for t in ts}


def _r3_by(pv):
    return {o["std_key"]: o for o in pv["objects"]}


async def test_r3_std():
    """미리보기 판정 표(누락·일치·확장·충돌 사유·참조 의존·초안·보관) · 쓰기 0(READ ONLY·전 테이블 해시) · 플래그 무관 읽기
    · 선택 기록(STANDARD off → FEATURE-OFF, on → 기록·덮어쓰기·def_id/def_revision) · 편입된 것 보류·거절 → STANDARD-LOCKED
    · 보류·거절이 정의·항목 쓰기를 안 막음 · 다른 사용자 접근 거절 · /api/std/* 401·403·400 · /api/state std 모양(MCP 엔 없음)
    · definitions() 의 template_slot·std_id → contract origin · init_schema 옛 DB 2회 멱등."""
    site = main.site
    saved = os.environ.pop("CRM_FEATURE_STANDARD", None)
    U = {k: db.user_by_email(e)["id"] for k, e in R3.items()}
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""

    async def req(method, path, uid=None, origin=True, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if uid:
                h.cookies.set("crm_session", site.session_value(uid))
            return await h.request(method, path, headers={"origin": PUBLIC} if origin else {}, **kw)

    def with_flag(on, fn, *a):
        prev = os.environ.pop("CRM_FEATURE_STANDARD", None)
        if on:
            os.environ["CRM_FEATURE_STANDARD"] = "on"
        try:
            return fn(*a)
        finally:
            os.environ.pop("CRM_FEATURE_STANDARD", None)
            if prev is not None:
                os.environ["CRM_FEATURE_STANDARD"] = prev

    def choices(uid):
        with db.tx() as c:
            return {r["std_key"]: r for r in c.execute("SELECT std_key,state,def_id,def_revision,confirmed_meaning,at FROM std_choices "
                                                       "WHERE user_id=%s", (uid,)).fetchall()}

    def sql(q, args=()):
        with db.tx() as c:
            c.execute(q, args)
    try:
        assert "CRM_FEATURE_STANDARD" not in os.environ
        # ── 재료 ──
        # match: 축 일치(id 가 템플릿과 다름) · 결정 확장(사용자 필드) · 기준 일치 — 참조는 그 사용자의 축·결정 오브젝트로 대응
        m_ax = _r3_def(U["match"], "axes", "주제 R3LBL01", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}],
                       oid="od_myaxes")
        m_ev = _r3_def(U["match"], "decision_events", "결정 R3LBL02", "event", [
            {"key": "happened_on", "label": "날", "value_type": "date"},
            {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "multiple": True},
            {"key": "project", "label": "프로젝트", "value_type": "text"}], oid="od_myevents")
        m_pr = _r3_def(U["match"], "principles", "원칙 R3LBL03", "principle", [
            {"key": "evidence_events", "label": "근거", "value_type": "reference", "target_kind": "item", "target_object_id": m_ev["id"],
             "multiple": True},
            {"key": "axes", "label": "축", "value_type": "reference", "target_kind": "item", "target_object_id": m_ax["id"],
             "multiple": True}])
        # conf: 축 종류·필드 타입 · 결정 필드 누락·multiple·required · 기준 대상 다름·대상 종류 다름·보관 필드
        c_ax = _r3_def(U["conf"], "axes", "축", "event", [{"key": "keywords", "label": "단어", "value_type": "text"}])
        _r3_def(U["conf"], "decision_events", "결정", "event", [
            {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "required": True}])
        c_bk = _r3_def(U["conf"], "books", "책", "collection", [])
        c_pr = _r3_def(U["conf"], "principles", "기준", "principle", [
            {"key": "evidence_events", "label": "근거", "value_type": "reference", "target_kind": "item", "target_object_id": c_bk["id"],
             "multiple": True},
            {"key": "axes", "label": "축", "value_type": "reference", "target_kind": "candidate", "multiple": True}])
        promo = {**db.DEFAULT_PROMOTION, "claim_field": "my_claims"}
        _r3_def(U["conf"], "principles", "기준", "principle", [   # evidence_claims 를 빼고 적용 → 보관 필드
            {"key": "evidence_events", "label": "근거", "value_type": "reference", "target_kind": "item", "target_object_id": c_bk["id"],
             "multiple": True},
            {"key": "axes", "label": "축", "value_type": "reference", "target_kind": "candidate", "multiple": True},
            {"key": "my_claims", "label": "내 근거", "value_type": "reference", "target_kind": "candidate", "multiple": True}],
            policy={"promotion": promo}, object_id=c_pr["id"], rev=1)
        # edge: 축 = 적용 전 초안만 · 결정 = 보관 오브젝트 · 기준 없음
        db.definition_propose(U["edge"], None, 0, {"key": "axes", "label": "초안 축", "semantic_kind": "collection", "fields": []})
        e_ev = _r3_def(U["edge"], "decision_events", "결정", "event", [
            {"key": "happened_on", "label": "날", "value_type": "date"},
            {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "multiple": True}])
        sql("UPDATE object_defs SET archived_at=now() WHERE user_id=%s AND id=%s", (U["edge"], e_ev["id"]))

        # ── 미리보기 판정 표 (플래그 미설정 = 읽기는 무관) ──
        before = _r3_tables_digest()
        pv = {k: db.std_preview(uid) for k, uid in U.items()}
        assert _r3_tables_digest() == before, "미리보기는 어떤 행도 쓰지 않는다"
        real_objs = db._std_objects

        def evil(c, uid, lock=False):   # 미리보기 트랜잭션 안에서 쓰기를 시도 → READ ONLY 가 막는다
            c.execute("INSERT INTO std_marks (user_id,version,state) VALUES (%s,'std@x','seeded')", (uid,))
            raise AssertionError("미리보기 트랜잭션에서 쓰기가 통과했다 — READ ONLY 가 아니다")
        db._std_objects = evil
        try:
            db.std_preview(U["none"])
            raise AssertionError("READ ONLY 트랜잭션이어야 한다")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass
        finally:
            db._std_objects = real_objs
        for k, p in pv.items():
            assert p["version"] == contract.STD_VERSION and [o["std_key"] for o in p["objects"]] == list(contract.STD_KEYS), k
            assert p["std"] == contract.std_state(None, None), k
            for o in p["objects"]:
                assert o["structure"] in db.STD_STRUCTURES and set(o["blocked"]) <= set(db.STD_BLOCKS), o
                assert all(x["code"] in db.STD_CONFLICTS for x in o["conflicts"]), o
                assert o["can_incorporate"] is (not o["blocked"]) and o["state"] == "none" and o["choice"] is None
                assert o["std_id"] == contract.std_id_of(o["std_key"]) and o["std_name"] == contract.std_name(o["std_id"])
                assert o["meaning_check"] is (o["object"] is not None), o
                assert o["lock"]["changes"] == ["archive", "key", "semantic_kind", "std_id", "field_remove", "field_shape"], o["lock"]
        lockf = {"axes": ["keywords"], "decision_events": ["happened_on", "claims"], "principles": ["evidence_claims", "evidence_events", "axes"]}
        assert {o["std_key"]: o["lock"]["fields"] for o in pv["none"]["objects"]} == lockf
        n = _r3_by(pv["none"])
        assert [n[k]["structure"] for k in contract.STD_KEYS] == ["missing"] * 3 and not any(o["conflicts"] for o in n.values())
        assert n["axes"]["can_incorporate"] and n["decision_events"]["can_incorporate"] and n["axes"]["needs"] == []
        assert n["principles"]["blocked"] == ["needs_target"], "참조 대상(결정·축)이 편입 전이면 기준은 편입 불가"
        assert n["principles"]["needs"] == [{"field": "evidence_events", "std_key": "decision_events", "state": "none", "ready": False},
                                            {"field": "axes", "std_key": "axes", "state": "none", "ready": False}]
        m = _r3_by(pv["match"])
        assert (m["axes"]["structure"], m["decision_events"]["structure"], m["principles"]["structure"]) == ("match", "extend", "match"), m
        assert m["axes"]["object"] == {"id": "od_myaxes", "key": "axes", "label": "주제 R3LBL01", "revision": 1, "archived": False}
        assert m["axes"]["blocked"] == [] and m["principles"]["blocked"] == ["needs_target"]
        cf = _r3_by(pv["conf"])
        got = {k: [(x["code"], x["field"]) for x in o["conflicts"]] for k, o in cf.items()}
        assert got == {"axes": [("kind", None), ("field_value_type", "keywords")],
                       "decision_events": [("field_missing", "happened_on"), ("field_multiple", "claims"), ("field_required", "claims")],
                       "principles": [("field_archived", "evidence_claims"), ("field_target", "evidence_events"),
                                      ("field_target_kind", "axes")]}, got
        assert all(o["structure"] == "conflict" and o["blocked"][0] == "conflict" and o["meaning_check"] for o in cf.values())
        assert cf["principles"]["blocked"] == ["conflict", "needs_target"]
        ed = _r3_by(pv["edge"])
        assert (ed["axes"]["conflicts"], ed["axes"]["object"]["revision"]) == ([{"code": "key_draft_only", "field": None}], 0)
        assert (ed["decision_events"]["conflicts"], ed["decision_events"]["object"]["archived"]) == ([{"code": "key_archived", "field": None}], True)
        assert ed["axes"]["meaning_check"] and ed["decision_events"]["meaning_check"] and ed["principles"]["structure"] == "missing"
        # 단위 — select 선택지(std@1 엔 없음: 잠금 영향 option_remove·field_options 는 표준 select 가 있을 때만) · 대상 대응
        sd = {"key": "s", "semantic_kind": "collection", "fields": [
            {"key": "kind", "value_type": "select", "required": False, "multiple": False, "target_kind": None, "target_object_id": None,
             "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
            {"key": "to", "value_type": "reference", "required": False, "multiple": False, "target_kind": "item",
             "target_object_id": "od_tpl", "options": []}]}
        row = {"revision": 2, "archived_at": None, "semantic_kind": "collection"}
        uf = lambda opts, to: [{**sd["fields"][0], "options": [{"value": v, "label": v} for v in opts], "archived_at": None},   # noqa: E731
                               {**sd["fields"][1], "target_object_id": to, "archived_at": None}]
        assert db._std_classify(sd, row, uf(["a", "b", "c"], "od_mine"), {"od_tpl": "od_mine"}) == ("match", [])
        assert db._std_classify(sd, row, uf(["a"], "od_mine"), {"od_tpl": "od_mine"}) == ("conflict", [{"code": "field_options", "field": "kind"}])
        assert db._std_classify(sd, row, uf(["a", "b"], "od_tpl"), {}) == ("conflict", [{"code": "field_target", "field": "to"}])
        assert "option_remove" in db._std_lock_effects(sd)["changes"]

        # ── 선택 기록: 플래그 off → FEATURE-OFF(쓰기 0), 모드 전환(STD_CHOICE_NEEDS_FLAG=False)이면 off 에서도 기록 ──
        e = expect("FEATURE-OFF", db.std_choose, U["match"], "principles", "deferred")
        assert e.message == contract.FEATURE_OFF_MSG["STANDARD"] and choices(U["match"]) == {}
        db.STD_CHOICE_NEEDS_FLAG = False
        try:
            assert db.std_choose(U["none"], "axes", "deferred")["state"] == "deferred"
        finally:
            db.STD_CHOICE_NEEDS_FLAG = True
        sql("DELETE FROM std_choices WHERE user_id=%s", (U["none"],))
        for bad in (("nope", "deferred"), ("axes", "incorporated"), ("axes", "seeded"), ("axes", "none")):   # incorporated = 확인 없음(5단계)
            with_flag(True, expect, "ARG-INVALID", db.std_choose, U["match"], *bad)
        # on → 기록(def_id·def_revision = 같은 key 기존 오브젝트) · 덮어쓰기 · 없는 오브젝트는 NULL
        r = with_flag(True, db.std_choose, U["match"], "principles", "deferred")
        assert (r["def_id"], r["def_revision"], r["std"]["objects"]["principles"]) == (m_pr["id"], 1, "deferred"), r
        assert (r["std"]["version"], r["std"]["state"]) == (None, None), "표식 없이 보류만 — version·state null(§0)"
        at1 = choices(U["match"])["principles"]["at"]
        _r3_def(U["match"], "principles", "원칙 R3LBL03", "principle", [   # 정의가 바뀐 뒤 다시 고르면 새 revision 을 기록
            {"key": "evidence_events", "label": "근거", "value_type": "reference", "target_kind": "item", "target_object_id": m_ev["id"],
             "multiple": True},
            {"key": "axes", "label": "축", "value_type": "reference", "target_kind": "item", "target_object_id": m_ax["id"],
             "multiple": True}, {"key": "memo", "label": "메모", "value_type": "text"}], object_id=m_pr["id"], rev=1)
        r = with_flag(True, db.std_choose, U["match"], "principles", "refused")
        ch = choices(U["match"])
        assert len(ch) == 1 and (ch["principles"]["state"], ch["principles"]["def_revision"]) == ("refused", 2), ch
        assert ch["principles"]["at"] >= at1 and ch["principles"]["confirmed_meaning"] is False
        r = with_flag(True, db.std_choose, U["none"], "decision_events", "refused")
        assert (r["def_id"], r["def_revision"]) == (None, None)
        r = with_flag(True, db.std_choose, U["edge"], "axes", "deferred")
        assert (r["def_id"], r["def_revision"]) == (ed["axes"]["object"]["id"], 0), "초안 행도 기록"
        p2 = _r3_by(db.std_preview(U["match"]))
        assert p2["principles"]["state"] == "refused" and p2["principles"]["choice"]["def_revision"] == 2
        assert p2["principles"]["structure"] == "extend", "사용자 필드 memo 를 더하면 확장"
        # 참조 의존: 대상이 편입(또는 seeded)이면 풀린다 — 편입 적용은 5단계라 행을 직접 넣는다(시험 재료)
        sql("INSERT INTO std_choices (user_id,std_key,state,def_id,def_revision) VALUES (%s,'axes','incorporated',%s,1),"
            "(%s,'decision_events','seeded',%s,1)", (U["match"], m_ax["id"], U["match"], m_ev["id"]))
        p3 = _r3_by(db.std_preview(U["match"]))
        assert p3["principles"]["blocked"] == [] and p3["principles"]["can_incorporate"], p3["principles"]
        assert [x["ready"] for x in p3["principles"]["needs"]] == [True, True]
        assert p3["axes"]["blocked"] == ["already"] and not p3["axes"]["can_incorporate"]
        # 이미 편입·시드된 것은 보류·거절로 되돌릴 수 없다(= std_id 제거) — 대응 오브젝트에 std_id 만 있어도 같다
        for k in ("axes", "decision_events"):
            e = with_flag(True, expect, "STANDARD-LOCKED", db.std_choose, U["match"], k, "refused")
            assert f"std:{k}" in e.message
        sql("UPDATE object_defs SET std_id='std:axes' WHERE user_id=%s AND id=%s", (U["conf"], c_ax["id"]))
        with_flag(True, expect, "STANDARD-LOCKED", db.std_choose, U["conf"], "axes", "deferred")
        sql("UPDATE object_defs SET std_id=NULL WHERE user_id=%s AND id=%s", (U["conf"], c_ax["id"]))

        # ── 보류·거절은 정의·항목 쓰기를 막지 않는다 ──
        with_flag(True, db.std_choose, U["conf"], "axes", "refused")
        with_flag(True, db.std_choose, U["conf"], "decision_events", "deferred")
        ax2 = _r3_def(U["conf"], "axes", "축 바꿈", "collection", [{"key": "keywords", "label": "단어", "value_type": "text"},
                                                                  {"key": "color", "label": "색", "value_type": "text"}],
                      object_id=c_ax["id"], rev=1)
        assert ax2["revision"] == 2 and ax2["semantic_kind"] == "collection"
        it = db.item_propose(U["conf"], c_ax["id"], 2, None, 0, "AX-1", "축 하나", "", {"keywords": "k", "color": "r"}, [])
        assert db.item_decide(U["conf"], it["item_id"], 0, it["proposal_id"], "apply")["revision"] == 1
        assert _r3_def(U["conf"], "notes", "노트", "collection", [])["revision"] == 1
        assert choices(U["conf"])["axes"]["def_revision"] == 1, "기록은 고를 때 본 revision 그대로(적용 재검증은 5단계)"

        # ── 웹: 본인만 · 401 · 403 · 400 · 플래그 ──
        assert (await req("GET", "/api/std/preview")).status_code == 401
        assert (await req("POST", "/api/std/choose", json={"std_key": "axes", "state": "deferred"})).status_code == 401
        w = await req("GET", "/api/std/preview", U["match"])
        assert w.status_code == 200 and w.headers.get("cache-control") == "no-store" and w.json() == db.std_preview(U["match"])
        wo = await req("GET", f"/api/std/preview?user_id={U['match']}&uid={U['match']}", U["none"])   # 다른 사용자를 고를 인자가 없다
        assert wo.json() == db.std_preview(U["none"]) and "R3LBL" not in wo.text and U["match"] not in wo.text and "od_my" not in wo.text
        assert (await req("POST", "/api/std/choose", U["none"], origin=False, json={"std_key": "axes", "state": "deferred"})).status_code == 403
        for body in ([], {"std_key": "axes"}, {"std_key": 1, "state": "deferred"}):
            b = await req("POST", "/api/std/choose", U["none"], json=body)
            assert b.status_code == 400 and b.json()["code"] == "ARG-INVALID", body
        off = await req("POST", "/api/std/choose", U["none"], json={"std_key": "axes", "state": "deferred"})
        assert off.status_code == 400 and off.json()["code"] == "FEATURE-OFF" and "axes" not in choices(U["none"])
        os.environ["CRM_FEATURE_STANDARD"] = "on"
        try:
            ok = await req("POST", "/api/std/choose", U["none"], json={"std_key": "axes", "state": "deferred", "user_id": U["match"]})
            assert ok.status_code == 200 and ok.json()["std"]["objects"]["axes"] == "deferred", ok.text
            lk = await req("POST", "/api/std/choose", U["match"], json={"std_key": "axes", "state": "refused"})
            assert lk.status_code == 400 and lk.json()["code"] == "STANDARD-LOCKED" and "R3LBL" not in lk.text
        finally:
            os.environ.pop("CRM_FEATURE_STANDARD", None)
        assert choices(U["match"])["axes"]["state"] == "incorporated", "본문 user_id 는 무시 — 남의 선택을 못 바꾼다"
        # /api/state std = contract.std_state 모양(계약 예제와 같은 키) · MCP state_get 엔 없음
        ex = json.loads(contract.example_path("full", "self").read_text(encoding="utf-8"))["std"]
        for k, uid in U.items():
            st = (await req("GET", "/api/state", uid)).json()
            assert set(st["std"]) == set(ex) and set(st["std"]["objects"]) == set(ex["objects"]), st["std"]
            assert st["std"] == db.std_status(uid) == db.std_preview(uid)["std"], k
        assert (await req("GET", "/api/state", U["match"])).json()["std"]["objects"] == \
            {"axes": "incorporated", "decision_events": "seeded", "principles": "refused"}
        sql("INSERT INTO std_marks (user_id,version,state) VALUES (%s,%s,'adopting')", (U["match"], contract.STD_VERSION))
        assert db.std_status(U["match"])["version"] == "std@1" and db.std_status(U["match"])["state"] == "adopting"
        assert "std" not in db.state(U["match"]), "MCP state_get 에는 넣지 않는다"

        # ── definitions() → template_slot·std_id → contract origin ──
        o_cr = _r3_def(U["orig"], "craft", "방식", "collection", [{"key": "project", "label": "프로젝트", "value_type": "text"},
                                                                {"key": "mine", "label": "내 것", "value_type": "text"}])
        db.template_slot_mark(U["orig"], o_cr["id"], "author@1:craft", {"project": "author@1:craft.project"})
        o_ax = _r3_def(U["orig"], "axes", "축", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"},
                                                             {"key": "extra", "label": "더함", "value_type": "text"}])
        sql("UPDATE object_defs SET std_id='std:axes', template_slot='author@1:axes' WHERE user_id=%s AND id=%s", (U["orig"], o_ax["id"]))
        sql("UPDATE field_defs SET std_id='std:axes.keywords' WHERE user_id=%s AND object_id=%s AND key='keywords'", (U["orig"], o_ax["id"]))
        defs = db.definitions(U["orig"])
        assert all({"template_slot", "std_id"} <= set(d) and all({"template_slot", "std_id"} <= set(f) for f in d["fields"]) for d in defs)
        resp = contract.respond({"definitions": defs}, "self")
        org = {o["key"]: (o["origin"], [(f["key"], f["origin"], f["locked"]) for f in o["fields"]]) for o in resp["objects"]}
        assert org == {"craft": ("template", [("project", "template", False), ("mine", "custom", False)]),
                       "axes": ("standard", [("keywords", "standard", True), ("extra", "custom", False)])}, org

        # ── init_schema: 옛 DB(std 열·테이블 없음) → 2회 멱등 · CHECK ──
        name = f"r3_{secrets.token_hex(3)}"
        create_schema(name)
        try:
            with schema_conn(name) as c:
                db.init_schema(c)
                c.execute("DROP TABLE std_marks, std_choices")
                c.execute("ALTER TABLE field_defs DROP COLUMN std_id")
                c.execute("ALTER TABLE object_defs DROP COLUMN std_id")
                c.commit()
                shape = lambda: c.execute(   # noqa: E731
                    "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name IN ('object_defs','field_defs','std_marks','std_choices') "
                    "ORDER BY table_name, column_name").fetchall()
                db.init_schema(c)
                c.commit()
                first = shape()
                cols = {(r["table_name"], r["column_name"]) for r in first}
                assert {("object_defs", "std_id"), ("field_defs", "std_id"), ("std_marks", "state"), ("std_choices", "confirmed_meaning")} <= cols
                db.init_schema(c)
                c.commit()
                assert shape() == first, "두 번째 init_schema 가 뭔가 바꿨다"
                c.execute("INSERT INTO users (id,email) VALUES ('u_r3','r3@x.test')")
                for q in ("INSERT INTO std_choices (user_id,std_key,state,def_id) VALUES ('u_r3','axes','refused','od_x')",
                          "INSERT INTO std_choices (user_id,std_key,state) VALUES ('u_r3','axes','none')",
                          "INSERT INTO std_marks (user_id,version,state) VALUES ('u_r3','std@1','done')"):
                    try:
                        with c.transaction():
                            c.execute(q)
                        raise AssertionError(f"CHECK 가 막아야 한다: {q}")
                    except psycopg.errors.CheckViolation:
                        pass
                c.rollback()
        finally:
            drop_schema(name)
    finally:
        site.dev, site.dev_user = real
        db.STD_CHOICE_NEEDS_FLAG = True
        os.environ.pop("CRM_FEATURE_STANDARD", None)
        if saved is not None:
            os.environ["CRM_FEATURE_STANDARD"] = saved
    print("OK — R3 표준 미리보기(누락·일치·확장·충돌 사유·참조 의존·초안·보관 · 쓰기 0 READ ONLY · 플래그 무관) · std_choices 보류·거절"
          "(FEATURE-OFF·덮어쓰기·편입 잠금·무관한 쓰기 안 막음) · 본인만 · /api/state std · template·std_id 출처 · init_schema 멱등 통과")


# ── 20) Q2 — 신규 시드 · 편입 적용 · 표준 잠금 ─────────
Q2 = {k: f"q2{k}@example.com" for k in ("off", "seed", "dev", "fail", "stop", "race", "old", "inc", "dep", "conf", "lock", "opt", "web")}
# off = 플래그 off 신규 · seed = 신규 시드 · dev = user_by_email 신규 · fail = 시드 실패 → 재시도 · stop = 정의 생긴 뒤 재시도 중단
# race = 동시 재시도 · old = 기존 사용자(표식 없음) · inc = 편입(누락·일치·확장·재검증) · dep = 참조 대상 상태 바뀜 · conf = 구조 충돌
# lock = 잠금 표(시드 사용자) · opt = 표준 select 선택지(명세를 시험에서만 바꿔 끼움) · web = POST /api/std/choose 편입


@contextmanager
def _q2_flag(on):
    prev = os.environ.pop("CRM_FEATURE_STANDARD", None)
    if on:
        os.environ["CRM_FEATURE_STANDARD"] = "on"
    try:
        yield
    finally:
        os.environ.pop("CRM_FEATURE_STANDARD", None)
        if prev is not None:
            os.environ["CRM_FEATURE_STANDARD"] = prev


def _q2_rows(uid):
    """그 사용자의 표준 관련 행 전부 — 쓰기 0 비교용."""
    with db.tx() as c:
        q = lambda sql: [dict(r) for r in c.execute(sql, (uid,)).fetchall()]   # noqa: E731
        return {"marks": q("SELECT version,state FROM std_marks WHERE user_id=%s ORDER BY version"),
                "choices": q("SELECT std_key,state,def_id,def_revision,confirmed_meaning,at FROM std_choices WHERE user_id=%s ORDER BY std_key"),
                "objects": q("SELECT id,key,label,semantic_kind,revision,std_id,template_slot,updated_at FROM object_defs WHERE user_id=%s ORDER BY id"),
                "fields": q("SELECT object_id,key,value_type,std_id,template_slot,archived_at FROM field_defs WHERE user_id=%s ORDER BY object_id,key")}


def _q2_sub(k):
    return f"q2{k}_sub"


async def test_q2_std():
    """template_std 출력 == 동결 파일 · 신규 시드(on)/안 함(off)·user_by_email 경로 · 시드 실패 주입 → 사용자 생성 성공·seed_pending·재시도
    성공 · 정의 생긴 뒤 재시도 중단 · 플래그 off 면 재시도 없음 · 동시 재시도 1회 · 기존 사용자(표식 없음) 로그인 쓰기 0
    · 편입(누락·일치·확장, 명세 id 가 쓰이면 새 id) · 재검증(revision·참조 대상 상태·이미 편입) · 참조 의존 · 구조 충돌 · 확인 없음 · 플래그 off
    · 잠금 표(거절·허용, propose·apply, 플래그 off 에서도) · 일일 한도·template_slot 백필 제외 · init_schema 2회 멱등."""
    import contextlib
    import io
    import threading
    import template_std
    saved = os.environ.pop("CRM_FEATURE_STANDARD", None)
    real_seed, real_doc = db._std_seed, dict(db._std_doc_cache)
    U = {}
    try:
        # ── 동결 파일 ──
        assert template_std.frozen_doc() == json.loads((HERE / "templates" / "std@1.json").read_text(encoding="utf-8"))
        assert template_std.STD_NAME == contract.STD_VERSION and "std_slot" not in (HERE / "template_std.py").read_text(encoding="utf-8")
        doc = db.std_doc()

        # ── 신규 시드: off 면 아무것도 · on 이면 정의 3·선택 3·표식 seeded ──
        U["off"] = db.user_by_subject(_q2_sub("off"), Q2["off"])["id"]
        assert _q2_rows(U["off"]) == {"marks": [], "choices": [], "objects": [], "fields": []}, "플래그 off = 시드 안 함"
        with _q2_flag(True):
            U["seed"] = db.user_by_subject(_q2_sub("seed"), Q2["seed"])["id"]
            U["dev"] = db.user_by_email(Q2["dev"])["id"]
            U["lock"] = db.user_by_subject(_q2_sub("lock"), Q2["lock"])["id"]
            again = _q2_rows(U["seed"])
            assert db.user_by_subject(_q2_sub("seed"), Q2["seed"])["id"] == U["seed"] and _q2_rows(U["seed"]) == again, "재로그인 = 쓰기 0"
        for k in ("seed", "dev", "lock"):
            r = _q2_rows(U[k])
            assert r["marks"] == [{"version": "std@1", "state": "seeded"}], (k, r["marks"])
            assert [(o["id"], o["key"], o["revision"], o["std_id"], o["template_slot"]) for o in sorted(r["objects"], key=lambda o: o["id"])] == [
                ("od_axes", "axes", 1, "std:axes", None), ("od_events", "decision_events", 1, "std:decision_events", None),
                ("od_principles", "principles", 1, "std:principles", None)], k
            assert {(f["object_id"], f["key"]): f["std_id"] for f in r["fields"]} == {
                (o["id"], f["key"]): f"std:{o['key']}.{f['key']}" for o in doc["objects"] for f in o["fields"]}, k
            assert [(ch["std_key"], ch["state"], ch["def_id"], ch["def_revision"], ch["confirmed_meaning"]) for ch in r["choices"]] == [
                ("axes", "seeded", "od_axes", 1, False), ("decision_events", "seeded", "od_events", 1, False),
                ("principles", "seeded", "od_principles", 1, False)], k
            assert db.std_status(U[k]) == {"version": "std@1", "state": "seeded", "objects": {k2: "seeded" for k2 in contract.STD_KEYS}}
        defs = db.definitions(U["seed"])
        pr = next(d for d in defs if d["key"] == "principles")
        assert [(f["key"], f["target_object_id"], f["position"]) for f in pr["fields"]] == [
            ("evidence_claims", None, 0), ("evidence_events", "od_events", 1), ("axes", "od_axes", 2)]
        assert pr["policy"] == doc["objects"][2]["policy"] and pr["label"] == "기준"
        hist = db.definition_history(U["seed"], "od_principles")
        assert len(hist) == 1 and hist[0]["revision"] == 1 and hist[0]["fields"][1]["target_object_id"] == "od_events"
        resp = contract.respond({"definitions": defs, "std_marks": {"version": "std@1", "state": "seeded"},
                                 "std_choices": {k: "seeded" for k in contract.STD_KEYS}}, "self")
        assert all(o["origin"] == "standard" and o["locked"] and all(f["origin"] == "standard" for f in o["fields"]) for o in resp["objects"])
        assert all(o["blocked"] == ["already"] for o in db.std_preview(U["seed"])["objects"])
        # 일일 한도: 시드 행은 안 센다(상한 0 이어도 통과) · 사용자가 만든 정의는 센다
        real_limits = dict(db.DAILY_LIMITS)
        try:
            db.DAILY_LIMITS["proposals"] = 0
            with db.tx() as c:
                db._daily_gate(c, U["seed"], "proposals", 0)
        finally:
            db.DAILY_LIMITS.clear()
            db.DAILY_LIMITS.update(real_limits)
        # template_slot 백필(author@1: id·key 가 같으면 칠함)이 시드 행(od_axes·axes …)을 칠하지 않는다 · init_schema 2회 멱등
        before = _q2_rows(U["seed"])
        with db.tx() as c:
            db._backfill_template_slots(c)
        db.init_schema()
        db.init_schema()
        assert _q2_rows(U["seed"]) == before, "백필·init_schema 가 시드 행을 바꿨다"

        # ── 시드 실패 주입 → 사용자 생성 성공 · seed_pending · 로그 · 재시도 성공 ──
        def boom(c, uid):
            c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision,std_id) VALUES (%s,'od_axes','axes','x','collection',1,'std:axes')", (uid,))
            raise RuntimeError("주입")
        out = io.StringIO()
        db._std_seed = boom
        try:
            with _q2_flag(True), contextlib.redirect_stdout(out):
                for k in ("fail", "stop", "race"):
                    U[k] = db.user_by_subject(_q2_sub(k), Q2[k])["id"]
                    assert db.user_by_subject(_q2_sub(k), Q2[k])["id"] == U[k], "재시도가 또 실패해도 로그인은 된다"
        finally:
            db._std_seed = real_seed
        assert out.getvalue().count("[std] create 표준 시드 실패 — seed_pending 으로 남김: RuntimeError") == 3, out.getvalue()
        assert out.getvalue().count("[std] retry 표준 시드 실패") == 3 and "주입" not in out.getvalue()
        for k in ("fail", "stop", "race"):
            assert _q2_rows(U[k]) == {"marks": [{"version": "std@1", "state": "seed_pending"}], "choices": [], "objects": [], "fields": []}, \
                "savepoint 롤백 — 시드 중간 행이 남지 않는다"
        db.user_by_subject(_q2_sub("fail"), Q2["fail"])   # 플래그 off — 재시도 안 함
        assert _q2_rows(U["fail"])["marks"][0]["state"] == "seed_pending"
        with _q2_flag(True):
            db.user_by_subject(_q2_sub("fail"), Q2["fail"])
        r = _q2_rows(U["fail"])
        assert r["marks"][0]["state"] == "seeded" and len(r["objects"]) == 3 and len(r["choices"]) == 3, r
        # 정의가 하나라도(초안 포함) 생겼으면 재시도를 멈춘다 — 표식은 seed_pending 그대로, §3 로
        db.definition_propose(U["stop"], None, 0, {"key": "notes", "label": "노트", "semantic_kind": "collection", "fields": []})
        with _q2_flag(True):
            db.user_by_subject(_q2_sub("stop"), Q2["stop"])
        r = _q2_rows(U["stop"])
        assert r["marks"][0]["state"] == "seed_pending" and [o["key"] for o in r["objects"]] == ["notes"] and r["choices"] == []
        assert all(o["structure"] == "missing" for o in db.std_preview(U["stop"])["objects"])
        with _q2_flag(True):   # 시드를 멈춘 계정의 첫 편입 → 표식을 adopting 으로 올린다(§12)
            db.std_choose(U["stop"], "axes", "incorporated", True, None)
        assert _q2_rows(U["stop"])["marks"] == [{"version": "std@1", "state": "adopting"}]
        assert db.std_status(U["stop"])["objects"]["axes"] == "incorporated"
        # 동시 재시도 두 요청 → 시드 1회(users 행 잠금 → 표식 재확인)
        calls = []

        def slow(c, uid):
            calls.append(uid)
            time.sleep(0.3)
            real_seed(c, uid)
        db._std_seed = slow
        errs = []

        def go():
            try:
                db.user_by_subject(_q2_sub("race"), Q2["race"])
            except Exception as e:   # noqa: BLE001
                errs.append(e)
        out = io.StringIO()
        try:
            with _q2_flag(True), contextlib.redirect_stdout(out):
                th = [threading.Thread(target=go) for _ in range(2)]
                for t in th:
                    t.start()
                for t in th:
                    t.join()
        finally:
            db._std_seed = real_seed
        r = _q2_rows(U["race"])
        assert not errs and calls == [U["race"]] and "[std]" not in out.getvalue(), (errs, calls, out.getvalue())
        assert r["marks"][0]["state"] == "seeded" and len(r["objects"]) == 3 and len(r["choices"]) == 3

        # ── 기존 사용자(표식 없음): 플래그 on 으로 로그인·DEV 조회해도 쓰기 0 (자동 시드·분류 없음) ──
        U["old"] = db.user_by_subject(_q2_sub("old"), Q2["old"])["id"]
        _r3_def(U["old"], "axes", "축", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}])
        before = _q2_rows(U["old"])
        with _q2_flag(True):
            db.user_by_subject(_q2_sub("old"), Q2["old"])
            db.user_by_email(Q2["old"])
        assert _q2_rows(U["old"]) == before and before["marks"] == [] and before["choices"] == []

        # ── 편입 적용 ──
        U["inc"] = db.user_by_email(Q2["inc"])["id"]
        ax = _r3_def(U["inc"], "axes", "주제", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}], oid="od_myax")
        ev = _r3_def(U["inc"], "decision_events", "결정", "event", [
            {"key": "happened_on", "label": "날", "value_type": "date"},
            {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "multiple": True},
            {"key": "project", "label": "프로젝트", "value_type": "text"}], oid="od_myev")
        _r3_def(U["inc"], "books", "책", "collection", [], oid="od_principles")   # 명세 id 를 다른 key 가 쓴다 → 누락 편입은 새 id
        # 편입 전에 만든 초안 — 편입 뒤 apply 가 최신 정의로 재검증해 STANDARD-LOCKED
        d_ax = db.definition_propose(U["inc"], ax["id"], 1, {"key": "axes", "label": "주제", "semantic_kind": "collection", "fields": []})
        d_ev = db.definition_propose(U["inc"], ev["id"], 1, {"key": "decision_events", "label": "결정", "semantic_kind": "event", "fields": [
            {"key": "happened_on", "label": "날", "value_type": "text"},
            {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "multiple": True}]})
        pv = _r3_by(db.std_preview(U["inc"]))
        assert (pv["axes"]["structure"], pv["decision_events"]["structure"], pv["principles"]["structure"]) == ("match", "extend", "missing")
        inc = lambda k, rev, meant=True: db.std_choose(U["inc"], k, "incorporated", meant, rev)   # noqa: E731
        expect("FEATURE-OFF", inc, "axes", 1)
        with _q2_flag(True):
            before = _q2_rows(U["inc"])
            for meant in (False, None, "true", 1):
                expect("ARG-INVALID", inc, "axes", 1, meant)
            expect("ARG-INVALID", inc, "axes", "1")
            e = expect("DEF-INVALID", inc, "principles", None)   # 참조 의존 — 축·결정 편입 전
            assert "decision_events" in e.message and "axes" in e.message, e.message
            expect("CONFLICT", inc, "axes", 0)                  # 미리보기와 다른 revision
            expect("CONFLICT", inc, "principles", 1)            # 누락인데 revision 을 들고 옴
            assert _q2_rows(U["inc"]) == before, "거절은 아무것도 안 쓴다"
            ax2 = _r3_def(U["inc"], "axes", "주제 바꿈", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}],
                          object_id=ax["id"], rev=1)   # 미리보기 뒤에 정의가 바뀜(rev 2) — draft 는 지워진다
            assert ax2["revision"] == 2
            e = expect("CONFLICT", inc, "axes", 1)
            assert e.action == "표준 미리보기를 다시 보고 고르세요." and "지금 2" in e.message
            d_ax = db.definition_propose(U["inc"], ax["id"], 2, {"key": "axes", "label": "주제", "semantic_kind": "collection", "fields": []})
            r = inc("axes", 2)
            assert (r["def_id"], r["def_revision"], r["std"]["objects"]["axes"], r["std"]["state"]) == ("od_myax", 2, "incorporated", "adopting")
            expect("CONFLICT", inc, "axes", 2)                  # 이미 편입
            r = inc("decision_events", 1)
            assert (r["def_id"], r["def_revision"]) == ("od_myev", 1)
            rows = _q2_rows(U["inc"])
            byo = {o["id"]: o for o in rows["objects"]}
            assert (byo["od_myax"]["std_id"], byo["od_myax"]["revision"], byo["od_myev"]["std_id"], byo["od_myev"]["revision"]) == \
                ("std:axes", 2, "std:decision_events", 1), "편입은 std_id 만 — revision 그대로"
            assert {(f["object_id"], f["key"]): f["std_id"] for f in rows["fields"] if f["object_id"] in ("od_myax", "od_myev")} == {
                ("od_myax", "keywords"): "std:axes.keywords", ("od_myev", "happened_on"): "std:decision_events.happened_on",
                ("od_myev", "claims"): "std:decision_events.claims", ("od_myev", "project"): None}, "사용자 필드엔 달지 않는다"
            # 편입 전 초안 apply → 최신 정의(std_id 있음)로 재검증해 거절
            e = expect("STANDARD-LOCKED", db.definition_apply, U["inc"], ax["id"], d_ax["proposal_id"], 2)
            assert e.message == contract.locked_message("field_remove", "std:axes.keywords"), e.message
            e = expect("STANDARD-LOCKED", db.definition_apply, U["inc"], ev["id"], d_ev["proposal_id"], 1)
            assert e.message == contract.locked_message("field_shape", "std:decision_events.happened_on"), e.message
            # 대상(축) label 변경은 허용 — revision 이 올라도 기준 편입을 막지 않는다(대상 revision 은 비교하지 않는다)
            _r3_def(U["inc"], "axes", "주제 또 바꿈", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}],
                    object_id=ax["id"], rev=2)
            r = inc("principles", None)
            assert r["def_id"] not in ("od_principles", None) and r["def_revision"] == 1, r
            np = next(d for d in db.definitions(U["inc"]) if d["key"] == "principles")
            assert np["std_id"] == "std:principles" and np["revision"] == 1 and np["template_slot"] is None
            assert {f["key"]: (f["target_object_id"], f["std_id"]) for f in np["fields"]} == {
                "evidence_claims": (None, "std:principles.evidence_claims"), "evidence_events": ("od_myev", "std:principles.evidence_events"),
                "axes": ("od_myax", "std:principles.axes")}
            ch = {c["std_key"]: c for c in _q2_rows(U["inc"])["choices"]}
            assert all(ch[k]["state"] == "incorporated" and ch[k]["confirmed_meaning"] is True for k in contract.STD_KEYS), ch
            assert _q2_rows(U["inc"])["marks"] == [{"version": "std@1", "state": "adopting"}]
            expect("STANDARD-LOCKED", db.std_choose, U["inc"], "principles", "refused")
        # 일일 한도: 편입된 정의(누락 편입으로 넣은 기준 포함)는 안 센다 — 오늘 센 제안 = 편입 안 한 books 하나
        real_limits = dict(db.DAILY_LIMITS)
        try:
            db.DAILY_LIMITS["proposals"] = 1
            with db.tx() as c:
                db._daily_gate(c, U["inc"], "proposals", 0)
            try:
                with db.tx() as c:
                    db._daily_gate(c, U["inc"], "proposals", 1)
                raise AssertionError("books 는 세야 한다")
            except db.CrmError as e:
                assert e.code == "RATE-LIMIT"
        finally:
            db.DAILY_LIMITS.clear()
            db.DAILY_LIMITS.update(real_limits)

        # 참조 대상 상태가 미리보기 뒤에 바뀌면(시험 재료: SQL 로 보류로 되돌림) 기준 편입 거절, 쓰기 0
        U["dep"] = db.user_by_email(Q2["dep"])["id"]
        with _q2_flag(True):
            dax = _r3_def(U["dep"], "axes", "축", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}])
            dev = _r3_def(U["dep"], "decision_events", "결정", "event", [
                {"key": "happened_on", "label": "날", "value_type": "date"},
                {"key": "claims", "label": "판단", "value_type": "reference", "target_kind": "candidate", "multiple": True}])
            db.std_choose(U["dep"], "axes", "incorporated", True, 1)
            db.std_choose(U["dep"], "decision_events", "incorporated", True, 1)
            assert _r3_by(db.std_preview(U["dep"]))["principles"]["can_incorporate"]
            with db.tx() as c:
                c.execute("UPDATE std_choices SET state='deferred' WHERE user_id=%s AND std_key='decision_events'", (U["dep"],))
            before = _q2_rows(U["dep"])
            e = expect("DEF-INVALID", db.std_choose, U["dep"], "principles", "incorporated", True, None)
            assert "decision_events" in e.message and _q2_rows(U["dep"]) == before
            assert dax["id"] and dev["id"]
        # 구조 충돌 · 초안만 → DEF-INVALID(사유 코드), 쓰기 0
        U["conf"] = db.user_by_email(Q2["conf"])["id"]
        _r3_def(U["conf"], "axes", "축", "event", [{"key": "keywords", "label": "단어", "value_type": "text_list"}])
        db.definition_propose(U["conf"], None, 0, {"key": "decision_events", "label": "초안", "semantic_kind": "event", "fields": []})
        with _q2_flag(True):
            before = _q2_rows(U["conf"])
            e = expect("DEF-INVALID", db.std_choose, U["conf"], "axes", "incorporated", True, 1)
            assert "kind" in e.message and "축" not in e.message, e.message
            e = expect("DEF-INVALID", db.std_choose, U["conf"], "decision_events", "incorporated", True, 0)
            assert "key_draft_only" in e.message
            assert _q2_rows(U["conf"]) == before

        # ── 잠금 표 (시드 사용자 · 플래그 off — 잠금은 플래그와 무관) ──
        L = U["lock"]
        assert "CRM_FEATURE_STANDARD" not in os.environ
        base = {d["key"]: d for d in db.definitions(L)}

        def nd_of(okey, **ch):
            d = base[okey]
            fields = [{k: f[k] for k in ("key", "label", "value_type", "required", "options", "target_kind", "target_object_id", "multiple")}
                      for f in d["fields"]]
            out = {"key": d["key"], "label": d["label"], "semantic_kind": d["semantic_kind"], "policy": d["policy"], "fields": fields}
            for k, v in ch.items():
                if k == "fields":
                    out["fields"] = v(fields)
                else:
                    out[k] = v
            return out

        def setf(key, attr, val):
            return lambda fs: [{**f, attr: val} if f["key"] == key else f for f in fs]
        rejected = [
            ("principles", {"key": "principles2"}, "key", "std:principles"),
            ("principles", {"semantic_kind": "collection"}, "semantic_kind", "std:principles"),
            ("axes", {"fields": lambda fs: []}, "field_remove", "std:axes.keywords"),
            ("axes", {"fields": setf("keywords", "value_type", "text")}, "field_shape", "std:axes.keywords"),
            ("decision_events", {"fields": setf("happened_on", "required", True)}, "field_shape", "std:decision_events.happened_on"),
            ("decision_events", {"fields": setf("claims", "multiple", False)}, "field_shape", "std:decision_events.claims"),
            ("principles", {"fields": setf("axes", "target_object_id", "od_events")}, "field_shape", "std:principles.axes"),
            ("principles", {"fields": setf("axes", "target_kind", "candidate")}, "field_shape", "std:principles.axes"),
        ]
        for key, ch, change, sid in rejected:
            o = base[key]
            e = expect("STANDARD-LOCKED", db.definition_propose, L, o["id"], o["revision"], nd_of(key, **ch))
            assert e.message == contract.locked_message(change, sid) and e.action == contract.LOCKED_ACT, (change, e.message)
        with _q2_flag(True):
            expect("STANDARD-LOCKED", db.definition_propose, L, base["axes"]["id"], 1, nd_of("axes", fields=lambda fs: []))
        # 허용: label(오브젝트·필드) · policy · 사용자 필드 추가 → 수정 → 삭제 · 항목 쓰기
        allowed = [
            ("principles", {"label": "내 원칙", "fields": setf("evidence_claims", "label", "근거 문장")}),
            ("principles", {"policy": {"promotion": {**base["principles"]["policy"]["promotion"], "confirmed_claims_min": 3}}}),
            ("axes", {"fields": lambda fs: fs + [{"key": "color", "label": "색", "value_type": "text"}]}),
            ("axes", {"fields": setf("color", "value_type", "number")}),
            ("axes", {"fields": lambda fs: [f for f in fs if f["key"] != "color"]}),
        ]
        for key, ch in allowed:
            o = next(d for d in db.definitions(L) if d["key"] == key)
            base[key] = o
            p = db.definition_propose(L, o["id"], o["revision"], nd_of(key, **ch))
            out = db.definition_apply(L, o["id"], p["proposal_id"], o["revision"])
            assert out["revision"] == o["revision"] + 1 and out["std_id"] == f"std:{key}", (key, ch)
        cur = {d["key"]: d for d in db.definitions(L)}
        assert cur["principles"]["label"] == "내 원칙" and cur["principles"]["policy"]["promotion"]["confirmed_claims_min"] == 3
        assert [f["key"] for f in cur["axes"]["fields"]] == ["keywords"] and all(f["std_id"] for f in cur["principles"]["fields"])
        with db.tx() as c:
            col = c.execute("SELECT std_id, archived_at FROM field_defs WHERE user_id=%s AND object_id='od_axes' AND key='color'", (L,)).fetchone()
        assert col["std_id"] is None and col["archived_at"] is not None, "사용자 필드는 지울 수 있다(보관)"
        it = db.item_propose(L, "od_axes", cur["axes"]["revision"], None, 0, "AX-1", "축", "", {"keywords": ["a"]}, [])
        assert db.item_decide(L, it["item_id"], 0, it["proposal_id"], "apply")["revision"] == 1
        # 표준 select 선택지(std@1 엔 없음) — 명세를 시험에서만 바꿔 끼운다: 명세 값 삭제 거절 · 사용자 값 삭제·label 변경·추가 허용
        U["opt"] = db.user_by_email(Q2["opt"])["id"]
        o = _r3_def(U["opt"], "axes", "축", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"},
                                                        {"key": "tone", "label": "톤", "value_type": "select", "options": ["a", "b", "c"]}])
        with db.tx() as c:
            c.execute("UPDATE object_defs SET std_id='std:axes' WHERE user_id=%s AND id=%s", (U["opt"], o["id"]))
            c.execute("UPDATE field_defs SET std_id='std:axes.'||key WHERE user_id=%s AND object_id=%s", (U["opt"], o["id"]))
        fake = json.loads(json.dumps(doc))
        fake["objects"][0]["fields"].append({"key": "tone", "label": "톤", "value_type": "select", "required": False, "multiple": False,
                                             "options": [{"value": "a", "label": "a"}, {"value": "b", "label": "b"}],
                                             "target_kind": None, "target_object_id": None})
        db._std_doc_cache[contract.STD_VERSION] = fake

        def opt_def(opts):
            return {"key": "axes", "label": "축", "semantic_kind": "collection", "fields": [
                {"key": "keywords", "label": "단어", "value_type": "text_list"}, {"key": "tone", "label": "톤", "value_type": "select", "options": opts}]}
        e = expect("STANDARD-LOCKED", db.definition_propose, U["opt"], o["id"], 1, opt_def(["a", "c"]))
        assert e.message == contract.locked_message("option_remove", "std:axes.tone")
        p = db.definition_propose(U["opt"], o["id"], 1, opt_def([{"value": "a", "label": "에이"}, "b", "d"]))
        assert db.definition_apply(U["opt"], o["id"], p["proposal_id"], 1)["revision"] == 2
        db._std_doc_cache.clear()
        db._std_doc_cache.update(real_doc)

        # ── 웹 POST /api/std/choose 편입 — 본문 검사 400 · FEATURE-OFF · 200 · 다시 보내면 CONFLICT · 본문 user_id 무시 ──
        site = main.site
        real_site = (site.dev, site.dev_user)
        site.dev, site.dev_user = False, ""
        U["web"] = db.user_by_email(Q2["web"])["id"]
        _r3_def(U["web"], "axes", "축 Q2WLBL", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}])

        async def post(body, uid=U["web"]):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                h.cookies.set("crm_session", site.session_value(uid))
                return await h.post("/api/std/choose", headers={"origin": PUBLIC}, json=body)
        try:
            ok_body = {"std_key": "axes", "state": "incorporated", "confirmed_meaning": True, "expected_revision": 1, "user_id": U["inc"]}
            for bad in ({**ok_body, "confirmed_meaning": "true"}, {**ok_body, "expected_revision": "1"},
                        {**ok_body, "expected_revision": True}):
                r = await post(bad)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad, r.text)
            r = await post({k: v for k, v in ok_body.items() if k != "confirmed_meaning"})
            assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
            r = await post(ok_body)
            assert r.status_code == 400 and r.json()["code"] == "FEATURE-OFF"
            with _q2_flag(True):
                inc_before = _q2_rows(U["inc"])
                r = await post(ok_body)
                assert r.status_code == 200 and r.json()["std"]["objects"]["axes"] == "incorporated", r.text
                r = await post(ok_body)
                assert r.status_code == 400 and r.json()["code"] == "CONFLICT" and "Q2WLBL" not in r.text, r.text
                assert _q2_rows(U["inc"]) == inc_before, "본문 user_id 는 무시 — 남의 정의를 못 건드린다"
            assert (await post(ok_body)).json()["code"] == "FEATURE-OFF"
        finally:
            site.dev, site.dev_user = real_site
    finally:
        db._std_seed = real_seed
        db._std_doc_cache.clear()
        db._std_doc_cache.update(real_doc)
        os.environ.pop("CRM_FEATURE_STANDARD", None)
        if saved is not None:
            os.environ["CRM_FEATURE_STANDARD"] = saved
    print("OK — Q2 표준(template_std 동결 · 시드 on/off·user_by_email · 실패 주입 → 로그인 성공·seed_pending·재시도·정의 생기면 중단·off 재시도 없음"
          " · 동시 1회 · 기존 사용자 쓰기 0 · 편입 누락·일치·확장·새 id · 재검증 revision·대상 상태·이미 편입 · 참조 의존·충돌·초안·확인 없음·off"
          " · 잠금 거절 8·허용 5·apply 재검증·플래그 off · select 선택지 · 한도·백필 제외 · init_schema 2회) 통과")


# ── 21) Q3 축소 — 출처 · 분류 역할 · 확정 재검사 · 수집 범위 ─────────
Q3 = {k: f"q3{k}@example.com" for k in ("src", "srcb", "role", "nor", "refu", "late", "rev", "mcp")}
# src·srcb = 출처(A·B) · role = 역할 이관(axes 있음) · nor = 축 없음 · refu = 표준 축 거절해도 역할 유지 · late = 이관 뒤에 축 생성
# rev = 확정 재검사·review·수집 범위 · mcp = MCP record_add 출처 인자


@contextmanager
def _q3_sources(on):
    prev = os.environ.pop("CRM_FEATURE_SOURCES", None)
    if on:
        os.environ["CRM_FEATURE_SOURCES"] = "on"
    try:
        yield
    finally:
        os.environ.pop("CRM_FEATURE_SOURCES", None)
        if prev is not None:
            os.environ["CRM_FEATURE_SOURCES"] = prev


def _q3_role(uid):
    with db.tx() as c:
        r = c.execute("SELECT object_id, at FROM user_roles WHERE user_id=%s AND role='classify'", (uid,)).fetchone()
    return dict(r) if r else None


async def test_q3():
    """출처: off 면 기록만(source_linked=false)·on 이면 찾거나 만들기·kind 기본·검사 · 다른 사용자 출처 연결은 FK 거절 · 옛 기록 미확인
    · 노출(/api/state 본인만, MCP state 엔 목록 없음) · MCP record_add 출처 인자 · 역할 이관(axes 있음·없음·초안·표준 거절·멱등)
    · 시드 = 표준 축 · 이관 뒤 만든 축(즉시 적용) · record_attach 역할 경유·대상 없음 CLASSIFY-OFF·항목→항목 axes 필드는 역할 무관
    · 확정 재검사 4사유·다른 action 허용·edit_confirm 롤백·확정 판단 불변·review 표시·정정 뒤 해제 · external 거절·정정 허용·PROVENANCE-LOCKED
    · init_schema 옛 DB 2회 멱등."""
    site = main.site
    saved = {k: os.environ.pop(k, None) for k in ("CRM_FEATURE_STANDARD", "CRM_FEATURE_SOURCES")}
    real_site = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    U = {k: db.user_by_email(e)["id"] for k, e in Q3.items() if k not in ("mcp",)}

    async def get_state(uid):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", site.session_value(uid))
            r = await h.get("/api/state")
            assert r.status_code == 200, r.text
            return r.json()

    def nrec(uid):
        with db.tx() as c:
            return c.execute("SELECT count(*) AS n FROM records WHERE user_id=%s", (uid,)).fetchone()["n"]
    try:
        # ── 출처 ──
        A, B = U["src"], U["srcb"]
        old = db.record_add(A, "옛 기록", "출처 없이 남긴 글이다.", None, "회의 메모")
        assert (old["source"], old["source_id"], old["source_linked"]) == ("회의 메모", None, None), old
        ref = {"kind": "file", "name": "업무일지 Q3SRC", "location": "drive/Q3LOC.md"}
        off = db.record_add(A, "off", "출처 기능 꺼짐.", None, "대화에서 입력", source_ref=ref)
        assert (off["source_id"], off["source_linked"]) == (None, False) and db.sources(A) == [], "off = 기록만, 출처 행 0"
        db.record_add(A, "off2", "검사도 안 한다.", source_ref={"kind": "bogus"})   # off 면 출처 인자를 쓰지 않는다
        with _q3_sources(True):
            r1 = db.record_add(A, "on1", "첫 글.", source_ref=ref)
            r2 = db.record_add(A, "on2", "둘째 글.", source_ref=dict(ref))
            r3 = db.record_add(A, "on3", "다른 위치.", source_ref={**ref, "location": "drive/other.md"})
            r4 = db.record_add(A, "on4", "종류 없음.", source_ref={"name": "메모장 Q3SRC"})
            assert r1["source_linked"] and r1["source_id"] == r2["source_id"] != r3["source_id"], (r1, r2, r3)
            for bad, code in (({**ref, "name": "  "}, "ARG-INVALID"), ({**ref, "kind": "pdf"}, "ARG-INVALID"),
                              ({**ref, "name": "가" * 400}, "PAYLOAD-LIMIT"), ("file", "ARG-INVALID")):
                n0 = nrec(A)
                expect(code, db.record_add, A, "x", "본문", source_ref=bad)
                assert nrec(A) == n0, "거절이면 기록도 안 남는다"
            rb = db.record_add(B, "b", "B 의 글.", source_ref=ref)
        srcs = db.sources(A)
        shape = set(contract.EXAMPLES["full"]["sources"][0])
        assert all(set(s) == shape for s in srcs) and [s["id"] for s in srcs] == [r1["source_id"], r3["source_id"], r4["source_id"]]
        assert next(s for s in srcs if s["id"] == r4["source_id"])["kind"] == "other" and srcs[0]["location"] == "drive/Q3LOC.md"
        assert rb["source_id"] not in [s["id"] for s in srcs], "같은 (kind,name,location) 이어도 사용자마다 따로"
        assert db.source_counts(A) == {"by_source": {r1["source_id"]: 2, r3["source_id"]: 1, r4["source_id"]: 1}, "unconfirmed": 3}
        try:   # 다른 사용자 출처를 가리킬 수 없다(복합 FK)
            with db.tx() as c:
                c.execute("UPDATE records SET source_id=%s WHERE user_id=%s AND id=%s", (r1["source_id"], B, rb["id"]))
            raise AssertionError("다른 사용자 출처에 연결됐다")
        except psycopg.errors.ForeignKeyViolation:
            pass
        assert db.sources(A) == srcs, "off 로 돌아가도 이미 있는 출처는 읽힌다"
        assert next(j for j in db.records(A) if j["id"] == old["id"])["source_id"] is None, "옛 기록 = 출처 미확인(NULL)"
        st = await get_state(A)
        assert st["sources"] == srcs and st["source_counts"] == db.source_counts(A)
        assert {j["id"]: j["source_id"] for j in st["journal"]}[r2["id"]] == r1["source_id"]
        stb = await get_state(B)
        assert [s["id"] for s in stb["sources"]] == [rb["source_id"]] and not any(s["id"] in json.dumps(stb) for s in srcs), "본인 출처만"
        assert "sources" not in db.state(A) and "source_counts" not in db.state(A), "MCP state_get 엔 목록 없음(본인 웹만)"
        # MCP record_add 출처 인자 — off 면 note, on 이면 연결
        prev_dev = os.environ.get("CRM_DEV_USER")
        os.environ["CRM_DEV_USER"] = Q3["mcp"]
        try:
            async with Client(main.mcp) as c:
                a = {"text": "MCP 로 남긴 글.", "source_name": "회의록 Q3MCP", "source_kind": "file", "source_location": "notes/m.md"}
                r = val(await c.call_tool("record_add", a))
                assert (r["source_id"], r["source_linked"]) == (None, False) and "꺼져" in r["note"], r
                assert set(val(await c.call_tool("record_add", {"text": "출처 인자 없음."}))) == {"id", "title", "created"}
                with _q3_sources(True):
                    r = val(await c.call_tool("record_add", a))
                    assert r["source_linked"] is True and r["source_id"] and "note" not in r, r
                    bad = await c.call_tool("record_add", {**a, "source_kind": "pdf"})
                    assert bad.is_error, bad
        finally:
            os.environ["CRM_DEV_USER"] = prev_dev
        U["mcp"] = db.user_by_email(Q3["mcp"])["id"]
        assert [s["name"] for s in db.sources(U["mcp"])] == ["회의록 Q3MCP"]

        # ── 분류 역할 ──
        R = U["role"]
        rax = _r3_def(R, "axes", "축", "collection", [{"key": "keywords", "label": "단어", "value_type": "text_list"}])
        rbk = _r3_def(R, "books", "책", "collection", [])
        _r3_def(U["refu"], "axes", "축", "event", [])   # 표준과 모양이 달라도·거절해도 역할은 key 로
        with _q2_flag(True):
            db.std_choose(U["refu"], "axes", "refused")
        db.definition_propose(U["nor"], None, 0, {"key": "axes", "label": "초안", "semantic_kind": "collection", "fields": []})   # 초안만
        db.init_schema()
        assert _q3_role(R)["object_id"] == rax["id"] and _q3_role(U["refu"]) is not None and _q3_role(U["nor"]) is None
        before = (_q3_role(R), _q3_role(U["refu"]))
        db.init_schema()
        assert (_q3_role(R), _q3_role(U["refu"])) == before, "이관 멱등 — at 도 그대로"
        with db.tx() as c:
            assert db._migrate_classify_roles(c) == 0
        assert db.classify_target(U["refu"]) == _q3_role(U["refu"])["object_id"]
        # record_attach: 역할 대상 항목만 · 대상 없으면 CLASSIFY-OFF(다른 칸은 됨) · 저장 키 axes 그대로
        it_ax = db.item_decide(R, (p := db.item_propose(R, rax["id"], 1, None, 0, "AX", "축 하나", "", {}, []))["item_id"], 0,
                               p["proposal_id"], "apply")
        it_bk = db.item_decide(R, (p := db.item_propose(R, rbk["id"], 1, None, 0, "BK", "책 하나", "", {}, []))["item_id"], 0,
                               p["proposal_id"], "apply")
        rr = db.record_add(R, "분류할 글", "분류할 글이다.")
        out = db.record_attach(R, rr["id"], axes=[it_ax["id"]])
        assert [(l["field_key"], l["target_id"]) for l in out["links"]] == [("axes", it_ax["id"])]
        e = expect("LINK-INVALID", db.record_attach, R, rr["id"], None, [it_bk["id"]])
        assert "분류 대상" in e.message
        nr = db.record_add(U["nor"], "축 없음", "분류할 데가 없다.")
        e = expect("CLASSIFY-OFF", db.record_attach, U["nor"], nr["id"], None, [])
        assert e.message == contract.CLASSIFY_OFF_MSG
        assert db.record_attach(U["nor"], nr["id"], use_at="쓸 자리")["notes"]["use_at"] == "쓸 자리", "분류만 꺼진다"
        # 이관 뒤에 축을 만든 사용자 — 다음 재시작을 기다리지 않고 같은 규칙으로 잡힌다
        lax = _r3_def(U["late"], "axes", "축", "collection", [])
        assert _q3_role(U["late"]) is None
        lit = db.item_decide(U["late"], (p := db.item_propose(U["late"], lax["id"], 1, None, 0, "L", "축", "", {}, []))["item_id"], 0,
                             p["proposal_id"], "apply")
        lr = db.record_add(U["late"], "글", "늦게 만든 축.")
        assert db.record_attach(U["late"], lr["id"], axes=[lit["id"]])["links"] and _q3_role(U["late"])["object_id"] == lax["id"]
        # 항목 → 항목의 axes 참조 필드는 역할을 안 본다 — 기준의 axes 필드가 책 항목을 가리켜도 된다
        rpr = _r3_def(R, "principles", "기준", "principle", [
            {"key": "axes", "label": "축", "value_type": "reference", "target_kind": "item", "target_object_id": rbk["id"], "multiple": True}])
        ip = db.item_propose(R, rpr["id"], 1, None, 0, "P", "기준", "", {}, [{"field_key": "axes", "target_kind": "item", "target_id": it_bk["id"]}])
        assert db.item_decide(R, ip["item_id"], 0, ip["proposal_id"], "apply")["revision"] == 1
        # 신규 시드 = 표준 축이 분류 대상
        assert _q3_role(db.user_by_email("q2seed@example.com")["id"])["object_id"] == "od_axes"

        # ── 확정 재검사 · review · 수집 범위 ──
        V = U["rev"]
        n0 = nrec(V)
        e = expect("SCOPE-SELF-ONLY", db.record_add, V, "남의 글", "남이 쓴 글이다.", None, "가져옴", "external", "verbatim")
        assert e.message == contract.SCOPE_MSG and nrec(V) == n0
        assert db.record_add(V, "미상", "누가 썼는지 모른다.", None, "가져옴", "unknown", "unknown")["author_kind"] == "unknown"
        cand = {}
        for why in ("ok", "author_unknown", "external", "processed", "deleted", "keep"):
            rec = db.record_add(V, why, f"{why} 기록의 원문 문장이다.")
            (cid,), _ = db.candidates_propose(V, rec["id"], "", [{"text": f"{why} 판단", "quote": f"{why} 기록의 원문 문장이다"}], "ai")
            cand[why] = (rec["id"], cid)
        assert db.candidate_decide(V, cand["keep"][1], "confirm")["status"] == "confirmed"
        fix = {"author_unknown": "author_kind='unknown'", "external": "author_kind='external'", "processed": "text_process='rewritten'",
               "deleted": "deleted_at=now()", "keep": "author_kind='unknown'"}
        with db.tx() as c:   # 시험 재료: 후보가 붙은 뒤 근거가 비적격이 된 옛 데이터(정정 API 는 PROVENANCE-LOCKED 로 막는다)
            for why, sql in fix.items():
                c.execute(f"UPDATE records SET {sql} WHERE user_id=%s AND id=%s", (V, cand[why][0]))
        for why in ("author_unknown", "external", "processed", "deleted"):
            rid, cid = cand[why]
            before = db.candidate_get(V, cid)
            assert before["review"] == why, (why, before)
            e = expect("CANDIDATE-REVIEW", db.candidate_decide, V, cid, "confirm")
            assert (e.message, e.action) == (contract.REVIEW_MSG[why], contract.REVIEW_ACT[why])
            expect("CANDIDATE-REVIEW", db.candidate_decide, V, cid, "edit_confirm", "고친 문장")
            assert db.candidate_get(V, cid) == before, "거절 = 아무것도 안 바뀜(edit_confirm 의 edit 도 롤백)"
            assert (before["record_id"], before["quote"]) == (rid, f"{why} 기록의 원문 문장이다"), "근거 표시 보존"
            assert db.candidate_decide(V, cid, "edit", "고친 판단")["text"] == "고친 판단"
            assert db.candidate_decide(V, cid, "exclude")["status"] == "excluded"
            assert db.candidate_decide(V, cid, "pending")["review"] == why
        kept = db.candidate_get(V, cand["keep"][1])
        assert (kept["status"], kept["review"]) == ("confirmed", None), "이미 확정한 판단은 그대로(재검토 표시 없음)"
        assert db.candidate_decide(V, cand["keep"][1], "confirm")["status"] == "confirmed", "같은 결정 반복은 no-op — 재검사 안 함"
        listed = {x["id"]: x["review"] for x in db.candidates(V)}
        assert cand["deleted"][1] not in listed, "삭제 기록의 후보는 목록에 없다"
        assert {why: listed[cand[why][1]] for why in ("ok", "author_unknown", "external", "processed")} == {
            "ok": None, "author_unknown": "author_unknown", "external": "external", "processed": "processed"}
        st = await get_state(V)
        assert {x["id"]: x["review"] for x in st["candidates"]} == listed and \
            {x["id"]: x["review"] for x in db.state(V)["candidates"]} == listed, "웹·MCP state 둘 다"
        # 해제 = 근거 기록이 적격으로 정정될 때뿐(메타데이터가 실제로 틀렸던 경우)
        db.record_provenance_set(V, cand["author_unknown"][0], "self", "verbatim", "작성자 확인함")
        assert db.candidate_get(V, cand["author_unknown"][1])["review"] is None
        assert db.candidate_decide(V, cand["author_unknown"][1], "confirm")["status"] == "confirmed"
        # 수집 범위: external 로의 정정은 된다(후보 없는 기록) · 후보 있는 적격 기록 → 비적격은 PROVENANCE-LOCKED 그대로
        lone = db.record_add(V, "혼자", "후보 없는 글이다.")
        assert db.record_provenance_set(V, lone["id"], "external", "verbatim", "남의 글이었음")["author_kind"] == "external"
        expect("PROVENANCE-LOCKED", db.record_provenance_set, V, cand["ok"][0], "external", "verbatim", "남의 글?")

        # ── init_schema: 옛 DB(출처·역할 없음) → 2회 멱등, 이관 ──
        name = f"q3_{secrets.token_hex(3)}"
        create_schema(name)
        try:
            with schema_conn(name) as c:
                db.init_schema(c)
                c.execute("ALTER TABLE records DROP CONSTRAINT fk_records_source")
                c.execute("ALTER TABLE records DROP COLUMN source_id")
                c.execute("DROP TABLE sources, user_roles")
                c.execute("INSERT INTO users (id,email) VALUES ('u_q3','q3@x.test')")
                c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision) VALUES ('u_q3','od_a','axes','축','collection',1)")
                c.execute("INSERT INTO records (id,user_id,title,body,source) VALUES ('r_q3','u_q3','t','b','옛 문자열')")
                c.commit()
                shape = lambda: c.execute(   # noqa: E731
                    "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name IN ('records','sources','user_roles') ORDER BY 1, 2").fetchall()
                db.init_schema(c)
                c.commit()
                first, roles = shape(), c.execute("SELECT * FROM user_roles").fetchall()
                assert {(r["table_name"], r["column_name"]) for r in first} >= {("records", "source_id"), ("sources", "location"),
                                                                                 ("user_roles", "object_id")}
                assert [(r["user_id"], r["object_id"]) for r in roles] == [("u_q3", "od_a")]
                assert c.execute("SELECT source, source_id FROM records WHERE id='r_q3'").fetchone() == {"source": "옛 문자열", "source_id": None}
                db.init_schema(c)
                c.commit()
                assert shape() == first and c.execute("SELECT * FROM user_roles").fetchall() == roles, "두 번째 init_schema 가 바꿨다"
                assert db._has_constraint(c, "records", "fk_records_source")
                c.rollback()
        finally:
            drop_schema(name)
    finally:
        site.dev, site.dev_user = real_site
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    print("OK — Q3 축소(출처 off 기록만·on 찾거나 만들기·검사·사용자별·FK·옛 기록 미확인·본인만·MCP 인자 · 역할 이관·초안 제외·거절 유지·멱등"
          "·시드 표준 축·늦은 축·record_attach 역할 경유·CLASSIFY-OFF·항목 필드 무관 · 확정 재검사 4사유·edit_confirm 롤백·다른 결정 허용"
          "·확정 불변·review 웹=MCP·정정 해제 · external 거절·정정 허용·PROVENANCE-LOCKED · init_schema 2회) 통과")


# ── 22a) Q5 운영자 화면 — /admin/users · /admin/users/<불투명 id> · structure_svg ─────────
Q5A = {k: f"q5a{k}@example.com" for k in ("a", "n", "c", "r", "x")}   # a 관리자 · n 비동의 · c 이름 공유 동의 · r 철회 · x 일반 사용자
TEST_EMAILS += tuple(Q5A.values())


def _q5a_fixture(uid, m, h):
    """표준 편입 2(축·결정) · 사용자 오브젝트(N:1·N:N·대상 미지정 참조·select) · 선택 템플릿·직접 추가 고립 노드 · 출처 1 ·
    연결: 정상 3 · 없는 대상(보관 항목) 1 · 대상 오브젝트 불일치 1 · 검사 불가(대상 미지정) 1 · M-D 위반(지워진 근거 기록의 후보) 1.
    사용자 문자열은 m(label·선택지 label·출처·본문), key·id·선택지 값은 h 를 품는다 — 누출 전문 검색용."""
    from psycopg.types.json import Jsonb
    o = [f"od_q5{h}_{i}" for i in range(1, 6)]   # 같은 created_at → id 순 = 오브젝트 1..5
    with db.tx() as c:
        for oid, key, lab, kind, std, slot in ((o[0], "axes", "축", "collection", "std:axes", None),
                                               (o[1], "decision_events", "결정", "event", "std:decision_events", None),
                                               (o[2], f"q5key{h}c", "사용자", "collection", None, None),
                                               (o[3], f"q5key{h}t", "템플릿", "collection", None, "author@1:craft"),
                                               (o[4], f"q5key{h}i", "고립", "collection", None, None)):
            c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision,std_id,template_slot) VALUES (%s,%s,%s,%s,%s,1,%s,%s)",
                      (uid, oid, key, f"{m}-L-{lab}", kind, std, slot))
        fd = "INSERT INTO field_defs (user_id,object_id,key,label,value_type,options,target_kind,target_object_id,multiple,position,std_id) " \
             "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        for row in ((o[0], "keywords", "kw", "text_list", [], None, None, False, 0, "std:axes.keywords"),
                    (o[1], "happened_on", "날짜", "date", [], None, None, False, 0, "std:decision_events.happened_on"),
                    (o[1], "claims", "판단", "reference", [], "candidate", None, True, 1, "std:decision_events.claims"),
                    (o[2], f"q5key{h}sel", "고르기", "select", [{"value": f"q5val{h}", "label": f"{m}-OPT"}], None, None, False, 0, None),
                    (o[2], f"q5key{h}one", "하나", "reference", [], "item", o[0], False, 1, None),
                    (o[2], f"q5key{h}many", "여럿", "reference", [], "item", o[1], True, 2, None),
                    (o[2], f"q5key{h}any", "아무", "reference", [], "item", None, True, 3, None)):
            c.execute(fd, (uid, row[0], row[1], f"{m}-F-{row[2]}", row[3], Jsonb(row[4]), *row[5:]))
        it = "INSERT INTO items (user_id,id,object_id,schema_revision,title,body,revision,archived_at) VALUES (%s,%s,%s,1,%s,%s,1,%s)"
        for iid, oid, gone in (("i1", o[0], False), ("i2", o[1], False), ("i3", o[2], False), ("i4", o[0], True), ("i5", o[2], False)):
            c.execute(it, (uid, f"i_q5{h}{iid}", oid, f"{m}-T", f"{m}-B", "2026-01-01" if gone else None))
        c.execute("INSERT INTO records (id,user_id,title,body,deleted_at) VALUES (%s,%s,%s,%s,now())", (f"r_q5{h}", uid, f"{m}-RT", "abcdef"))
        c.execute("INSERT INTO candidates (id,user_id,record_id,text,quote,quote_start,quote_end,origin) VALUES (%s,%s,%s,%s,'abc',0,3,'self')",
                  (f"c_q5{h}", uid, f"r_q5{h}", f"{m}-CT"))
        ln = "INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_item_id,target_candidate_id) VALUES (%s,%s,%s,%s,%s,%s)"
        for src, oid, fk, ti, tc in (("i2", o[1], "claims", None, f"c_q5{h}"),       # 정상(코어 후보)
                                     ("i3", o[2], f"q5key{h}one", "i1", None),         # 정상 N:1
                                     ("i3", o[2], f"q5key{h}many", "i2", None),        # 정상 N:N
                                     ("i3", o[2], f"q5key{h}many", "i4", None),        # 없는 대상(보관 항목)
                                     ("i5", o[2], f"q5key{h}many", "i1", None),        # 대상 오브젝트 불일치
                                     ("i5", o[2], f"q5key{h}any", "i1", None)):        # 검사 불가(대상 미지정)
            c.execute(ln, (uid, f"i_q5{h}{src}", oid, fk, f"i_q5{h}{ti}" if ti else None, tc))
        c.execute("INSERT INTO sources (user_id,id,kind,name,location) VALUES (%s,%s,'file',%s,%s)", (uid, f"s_q5{h}", f"{m}-SRCNAME", f"{m}-SRCLOC"))
        c.execute("INSERT INTO std_marks (user_id,version,state) VALUES (%s,'std@1','adopting')", (uid,))
        for k, stt in (("axes", "incorporated"), ("decision_events", "incorporated"), ("principles", "deferred")):
            c.execute("INSERT INTO std_choices (user_id,std_key,state) VALUES (%s,%s,%s)", (uid, k, stt))
        c.execute("INSERT INTO tickets (id,user_id,kind,text,via) VALUES (%s,%s,'bug',%s,'web')", (f"tk_q5{h}", uid, f"{m}-TK"))


async def test_q5_admin():
    """Q5 운영자 화면: structure_svg 결정성 · 비동의·철회 사용자 — 목록·상세 HTML·SVG 속성·aria·URL·302 Location·404 본문에 label·key·선택지 값·
    출처 name·location·id 0 · 동의 사용자 — 현재 label 보임·key·선택지 값 0 · 위조 불투명 id 404 · 일반 사용자 404 · 연결 있음 M ·
    무결성 오류 3종 ≠ 검사 불가 · cardinality · 표준 상태 코드 · 고립 노드 · 표준 정의 보호."""
    import admin_db
    import structure_svg
    site = main.site
    U = {k: db.user_by_email(e)["id"] for k, e in Q5A.items()}
    A = U["a"]
    _set_role(A, "admin")
    M = {k: f"Q5M{k.upper()}" + secrets.token_hex(4) for k in ("n", "c", "r")}
    H = {k: secrets.token_hex(4) for k in ("n", "c", "r")}
    for k in ("n", "c", "r"):
        _q5a_fixture(U[k], M[k], H[k])
    db.optional_consent_set(U["c"], "struct_names", True)
    db.optional_consent_set(U["r"], "struct_names", True)
    db.optional_consent_set(U["r"], "struct_names", False)
    secret = [M["n"], M["r"]] + [f"{p}{H[k]}" for k in ("n", "c", "r") for p in ("q5key", "q5val", "od_q5", "i_q5", "r_q5", "c_q5", "s_q5", "tk_q5")]
    secret += [M["c"] + x for x in ("-SRCNAME", "-SRCLOC", "-T", "-B", "-RT", "-CT", "-TK")] + list(U.values())
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""

    async def get(path, cookie=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", cookie or site.session_value(A))
            return await h.get(path)

    def leak_free(r, path):
        out = r.text + json.dumps(dict(r.headers), ensure_ascii=False)
        hit = [s for s in secret if s in out]
        assert not hit, (path, "누출", hit)
        if r.status_code == 200:
            assert "<script" not in r.text.lower() and not re.search(r"\son[a-z]+\s*=", r.text, re.I), (path, "스크립트·이벤트 속성 0")
        return r.text

    def svg_of(t):
        i = t.index("<svg")
        return t[i:t.index("</svg>", i) + 6]
    try:
        # ── structure_svg: 결정성 · 고정 예제 · 본인 응답도 그린다 ──
        for name in contract.EXAMPLES:
            for v in contract.VIEWERS:
                resp = contract.respond(contract.EXAMPLES[name], v)
                a, b = structure_svg.render(resp), structure_svg.render(json.loads(json.dumps(resp)))
                assert a == b and a.startswith("<svg") and a.endswith("</svg>"), (name, v)
                banned = {"self": None, "admin_noconsent": r"ULBL|PLBL|SRC|ukey|uopt|od_|src_", "admin_consent": r"SRC|ukey|uopt|od_|src_"}[v]
                assert banned is None or not re.search(banned, a), (name, v, "운영자 SVG 에 key·선택지 값·출처·id(비동의는 label 도) 0")
        # 대조 — 심은 이름·key·선택지 값·출처는 본인 응답에선 보인다(읽히는 자리에 있다는 증거)
        own = (await get("/api/state", site.session_value(U["n"]))).text
        for w in (f"{M['n']}-L-사용자", f"{M['n']}-OPT", f"q5key{H['n']}sel", f"q5val{H['n']}", f"{M['n']}-SRCNAME", f"{M['n']}-SRCLOC"):
            assert w in own, ("대조 실패 — 본인 state 에 없다", w)
        # 운영 화면은 밝게 고정(OS 미추종) — 다크 미디어 쿼리 0
        assert "prefers-color-scheme" not in web._ADMIN_CSS and "prefers-color-scheme" not in (await get("/admin/users")).text
        # ── 일반 사용자·무세션 404 ──
        for cookie in (site.session_value(U["x"]), site.session_value(U["n"])):
            for path in ("/admin/users", f"/admin/users/{site._opaque(U['n'])}"):
                r = await get(path, cookie)
                assert r.status_code == 404 and "<" not in r.text, (path, r.status_code)
        # ── 목록: n · N · M · 표준 상태 코드 ──
        r = await get("/admin/users")
        t = leak_free(r, "/admin/users")
        assert r.status_code == 200 and f'href="/admin/users/{site._opaque(U["n"])}"' in t
        row = next(x for x in t.split("<tr>") if Q5A["n"] in x)
        assert '<td class="num">5</td><td class="num">4</td><td class="num">3</td>' in row, row
        assert "std@1 adopting" in row and "axes incorporated" in row and "principles deferred" in row, row
        assert M["c"] not in t, "목록엔 동의자 이름도 없다"
        # ── 상세(비동의·철회) ──
        for k in ("n", "r"):
            path = f"/admin/users/{site._opaque(U[k])}"
            r = await get(path)
            t = leak_free(r, path)
            assert r.status_code == 200 and "동의 없음" in t and 'class="lbl"' not in t, (k, t[-600:])
            svg = svg_of(t)
            assert svg == svg_of((await get(path)).text), "같은 사용자 두 번 → 같은 SVG"
            assert svg.count('r="42"') == 6, "오브젝트 5(고립 2 포함) + 코어 후보 1 — 같은 크기"
            assert svg.count("stroke-dasharray") == 2 and svg.count(structure_svg.LOCK_TEXT) >= 2, "편입된 표준 2 = 표준 정의 보호"
            assert 'aria-label="오브젝트 1 · 축 · 표준 · 표준 정의 보호"' in svg and "오브젝트 3.필드 b — 축 · N:1 · 직접 추가" in svg, svg[:1500]
            assert "marker" not in svg and "arrow" not in svg, "화살표 없음"
            assert all(f'id="{structure_svg.node_anchor(i)}"' in t for i in range(5)) and f'id="{structure_svg.core_anchor("candidate")}"' in t
            assert t.count('<td>N:1</td>') == 1 and t.count('<td>N:N</td>') == 3, "카디널리티 표시"
            for code, n in (("REF-DANGLING", 1), ("REF-MISMATCH", 1), ("MD-ORPHAN", 1)):
                assert re.search(rf"<td>{code}</td><td>[^<]+</td><td class=\"num\"><span class=err>{n}</span></td>", t), (code, t[-2500:])
            assert re.search(r"<td>UNCHECKED</td><td>검사 불가[^<]*</td><td class=\"num\">1</td>", t), "검사 불가는 오류와 따로"
            assert "std@1" in t and "adopting" in t and "incorporated" in t and "deferred" in t, "표준 상태 코드"
            assert "표준 축" in t and "표준 결정" in t, "편입된 것만 표준명"
            assert "<th>출처</th>" not in t and "<th>카디널리티</th><th>구분</th>" in t, "관계 집계 열 머리 = 구분(정의 구분 뜻의 '출처' 0 — 출처는 기록 출처 개수만)"
        v = admin_db.userview("admin", U["n"])[U["n"]]
        inp = admin_db.contract_input(v)
        st = admin_db.link_stats(v, inp)
        assert st["linked"] == [1, 1, 1, 0], st
        assert sorted((e["code"], e["why"], e["n"]) for e in st["errors"]) == [("MD-ORPHAN", "candidate", 1), ("REF-DANGLING", "target", 1),
                                                                             ("REF-MISMATCH", "object", 1)], st
        assert [(e["rel"], e["why"], e["n"]) for e in st["unchecked"]] == [(3, "any_item", 1)], st
        resp = contract.respond(inp, "admin_noconsent")
        assert [r_["cardinality"] for r_ in resp["relations"]] == ["N:N", "N:1", "N:N", "N:N"] and resp["counts"]["sources"] == 1
        assert structure_svg.render(resp) == structure_svg.render(contract.respond(admin_db.contract_input(v), "admin_noconsent"))
        # ── 상세(동의): 현재 label·선택지 label 보임, key·선택지 값·출처 0 ──
        path = f"/admin/users/{site._opaque(U['c'])}"
        r = await get(path)
        t = leak_free(r, path)
        assert r.status_code == 200 and "동의 — 현재 이름" in t, t[-600:]
        assert f"{M['c']}-L-사용자" in t and f"{M['c']}-F-여럿" in t and f"{M['c']}-OPT" in t, "동의자 현재 label·선택지 label"
        assert f'aria-label="오브젝트 3 · {M["c"]}-L-사용자 · 직접 추가"' in svg_of(t), "SVG 에도 동의자 label"
        # 철회 → 다음 조회부터 이름 0
        db.optional_consent_set(U["c"], "struct_names", False)
        t = (await get(path)).text
        assert M["c"] not in t and "동의 없음" in t, "철회 뒤 이름 0"
        db.optional_consent_set(U["c"], "struct_names", True)
        # ── URL·오류: 위조 불투명 id · 내부 id · 쿼리 · 옛 트리 302 ──
        for bad in ("0" * 12, secrets.token_hex(6), U["n"], site._opaque(U["n"])[:-1] + ("0" if site._opaque(U["n"])[-1] != "0" else "1")):
            r = await get(f"/admin/users/{bad}")
            t = leak_free(r, bad)
            assert r.status_code == 404 and "NOT-FOUND" in t and bad not in t, (bad, r.status_code)
        r = await get(f"/admin/users/{site._opaque(U['n'])}?u=1")
        assert r.status_code == 400 and leak_free(r, "?u=1")
        r = await get(f"/admin/schema?u={site._opaque(U['n'])}")
        leak_free(r, "/admin/schema?u=n")
        assert r.status_code == 302 and r.headers["location"] == f"/admin/users/{site._opaque(U['n'])}", r.headers
        for path in ("/admin", "/admin/schema", "/admin/reports?ds=structure&g1=value_type"):
            leak_free(await get(path), path)
    finally:
        site.dev, site.dev_user = real
    print("OK — 22a Q5 운영자 화면(SVG 결정성·예제·본인 응답 · 일반 사용자 404 · 목록 n·N·M·표준 코드 · 비동의·철회 상세 이름·key·값·출처·id 0"
          "(HTML·SVG·aria·URL·302·404) · 동의 label 보임·key 0·철회 뒤 0 · 위조 id 404 · 무결성 3종 ≠ 검사 불가 · N:1·N:N · 고립·표준 정의 보호) 통과")


# ── 22b) Q4·Q5 사용자 화면 — 기록 출처·재검토 · 구조 정의 · 편입 미리보기·확인 대화창 ─────────
Q45 = {k: f"q45{k}@example.com" for k in ("a", "b")}   # a = 화면 주인 · b = 다른 사용자(이름 누출 표식 Q45B…)


async def test_q45_user():
    """app.html 정적(「구조 정의」 보기·편입 대화창 마크업·체크 게이트·출처 미확인·재검토 문구·인라인 스크립트 1개·on* 0·목록/그래프 전환)
    · /api/state structure == contract.respond(self) · structure_svg == structure_svg.render(structure)(결정적·script·on* 0·다른 사용자 이름 0)(기대값은 SQL 로 읽은 std_marks·std_choices + db.definitions + db.sources)
    · 편입 전후 locked·origin 변화 · MCP state(db.state)엔 structure 없음 · 다른 사용자 이름 0(/api/state·/api/std/preview)
    · 출처 있는 기록·없는 기록·재검토 후보가 화면 입력에 있다 · 관리자 /app 에도 편입 대화창(사용자·관리자 구간 밖).
    시험 사용자는 이 절에서 만들고 끝에 지운다(TEST_EMAILS 밖)."""
    from html.parser import HTMLParser
    import structure_svg
    site = main.site
    saved = {k: os.environ.pop(k, None) for k in ("CRM_FEATURE_STANDARD", "CRM_FEATURE_SOURCES")}
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    emails = list(Q45.values())
    tokens = structure_svg.tokens_css(dark_attr=True, dark_media=False)

    def svg_tags(svg):
        """SVG 마크업 → [(태그, 속성 이름들)]. 글자(escape 된 label) 안의 「 onload=」 는 속성이 아니라 세지 않는다."""
        out = []

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                out.append((tag, [k for k, _ in attrs]))
            handle_startendtag = handle_starttag
        P().feed(svg)
        return out

    def wipe():
        with db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (emails,)).fetchall()]
        for i in ids:
            db.user_delete(i)

    async def get(path, uid):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", site.session_value(uid))
            r = await h.get(path)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])
            return r

    async def choose(uid, body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", site.session_value(uid))
            return await h.post("/api/std/choose", json=body, headers={"origin": PUBLIC})

    def expected(uid):
        """web.py 와 다른 경로 — std_marks·std_choices 를 SQL 로 직접 읽는다."""
        with db.tx() as c:
            m = c.execute("SELECT version,state FROM std_marks WHERE user_id=%s AND version=%s", (uid, contract.STD_VERSION)).fetchone()
            ch = {r["std_key"]: r["state"] for r in c.execute("SELECT std_key,state FROM std_choices WHERE user_id=%s", (uid,)).fetchall()}
        return contract.respond({"definitions": db.definitions(uid), "std_marks": dict(m) if m else None, "std_choices": ch,
                                 "sources": db.sources(uid)}, "self")

    wipe()
    try:
        # ── app.html 정적 ──
        html = (HERE / "app.html").read_text(encoding="utf-8")
        assert len(web.inline_script_hashes(html)) == 1 and html.count("<script>") == 1, "인라인 스크립트 1개"
        assert not re.search(r"\son[a-z]+\s*=", html, re.I), "인라인 이벤트 속성 0"
        assert '"구조 정의"' in html and "/defs" in html and 'if (a === "defs") return renderDefs(tab);' in html, "구조 정의 보기·라우트"
        assert "S.structure" in html and "/api/std/preview" in html and "/api/std/choose" in html
        i = html.index('<dialog id="std-dlg"')
        dlg = html[i:html.index("</dialog>", i)]
        for w in ('id="std-dlg-ok"', 'type="checkbox"', 'id="std-dlg-go" disabled', 'id="std-dlg-lock"', 'id="std-dlg-oktext"',
                  'id="std-dlg-cancel"'):
            assert w in dlg, w
        for mark in ("<!--user-->", "<!--admin-->"):   # 관리자 /app 도 구조 탭을 쓴다 — 대화창은 두 구간 표식 밖
            for a in [m.start() for m in re.finditer(re.escape(mark), html)]:
                b = html.index(mark.replace("<!--", "<!--/"), a)
                assert not (a < i < b), ("편입 대화창이 구간 안", mark)
        for w in ('같은 뜻으로 쓴다', 'confirmed_meaning = true', 'expected_revision = o.object ? o.object.revision : null',
                  '$("std-dlg-go").disabled = !$("std-dlg-ok").checked', '"FEATURE-OFF":', '"DEF-INVALID":', '"CONFLICT":',
                  "표준 정의 보호", "출처 미확인", "source_counts", "확정 전 재검토", "S.sources"):
            assert w in html, w
        for code in contract.LOCKED_CHANGES:   # 잠금 영향 문구 — 서버 코드 전부
            assert re.search(rf"\b{code}: \"", html), code
        for code in contract.REVIEW_REASONS:
            assert re.search(rf"\b{code}: \"", html), code
        fix = html[html.index("const REVIEW_FIX = {"):html.index("};", html.index("const REVIEW_FIX = {"))]
        assert "author_unknown" in fix and "processed" in fix and "external" not in fix and "deleted" not in fix, "정정 안내는 두 사유만"
        assert "record_provenance_set" not in html and "verbatim 으로" not in html, "도구명·「바꾸면 풀린다」 안내 없음"
        # 목록/그래프 전환(기본 목록) · 그래프 = 서버 structure_svg 문자열 · 목록 id = structure_svg 앵커 · 해시를 바꾸지 않는 이동 · 토큰
        for w in ('let defsMode = "list";', 'data-dmode="${m}"', '"목록" : "그래프"', "S.structure_svg", 'id="dlist"', 'id="dgraph"',
                  'id="dmo-o${i + 1}"', 'id="dmo-r${i + 1}"', 'id="dmo-core-${esc(x.kind)}"', 'closest(".dmo a")', 'getAttribute("href")',
                  "e.preventDefault(); defsJump(", "scrollIntoView"):
            assert w in html, w
        assert (structure_svg.node_anchor(0), structure_svg.rel_anchor(0), structure_svg.core_anchor("record")) == (
            "dmo-o1", "dmo-r1", "dmo-core-record"), "app.html 의 id 꼴 = structure_svg 앵커"
        assert tokens in html and "prefers-color-scheme" not in tokens and "prefers-color-scheme" not in html, "토큰 = 테마 토글(data-theme)만"
        assert '[data-theme="dark"]' in tokens
        chip = html[html.index("const ORIGIN_CHIP = {"):html.index("};", html.index("const ORIGIN_CHIP = {"))]
        assert dict(re.findall(r'(\w+): \["[^"]*", "([^"]+)"\]', chip)) == structure_svg.ORIGIN_TEXT, "구분 용어 — 그래프 = 목록 칩"
        assert '<th scope="col">출처</th>' not in html and '<th scope="col">구분</th><th scope="col">보호</th>' in html, "필드 표 열 머리 = 구분(정의 구분 뜻의 '출처' 0)"
        sv = html[html.index("function structViews("):html.index("\n}", html.index("function structViews("))]   # 구조 탭 순서: 보기 전환 → 오브젝트 필(항목 보기만)
        assert sv.index('aria-label="구조 보기"') < sv.index("+ sub;") and 'subnav("");\n  const sub = `<nav class="pills sobjs" aria-label="구조 고르기">' in html \
            and '"구조 고르기");' not in html and 'structViews(tab, "defs", ' not in html and html.count('structViews(tab, "items", sub)') == 3, "보기 전환이 맨 위 · 오브젝트 필은 그 아래 항목 보기에서만"
        assert "min-width: 420px" in html and ".dmo { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px; overflow-x: auto; }" in html

        # ── 사용자 A(기존 사용자형 — 표식 없음) · B(다른 사용자) ──
        A = db.user_by_subject("q45a_sub", Q45["a"])["id"]
        B = db.user_by_subject("q45b_sub", Q45["b"])["id"]
        _r3_def(A, "axes", "내 관심축 Q45A01", "collection", [{"key": "keywords", "label": "단어 Q45A02", "value_type": "text_list"},
                                                                    {"key": "memo", "label": "메모 Q45A03", "value_type": "text"}])
        _r3_def(A, "principles", "내 원칙 Q45A04", "collection", [{"key": "axes", "label": "축", "value_type": "text"}])   # 충돌
        _r3_def(B, "axes", "남의 축 Q45B01", "collection", [{"key": "keywords", "label": "남의 단어 Q45B02", "value_type": "text_list"}])
        os.environ["CRM_FEATURE_SOURCES"] = "on"
        try:
            ra = db.record_add(A, "출처 있는 글", "출처가 있는 원문 문장이다.", source_ref={"kind": "file", "name": "업무일지 Q45A05",
                                                                                   "location": "drive/q45a.md"})
            db.record_add(B, "남의 글", "남의 원문.", source_ref={"kind": "url", "name": "남의 출처 Q45B03", "location": "https://q45b.example"})
        finally:
            os.environ.pop("CRM_FEATURE_SOURCES", None)
        rn = db.record_add(A, "출처 없는 글", "출처가 없는 원문 문장이다.")
        rv = db.record_add(A, "비적격 글", "재검토할 원문 문장이다.")
        (cid,), _ = db.candidates_propose(A, rv["id"], "", [{"text": "재검토 판단", "quote": "재검토할 원문 문장이다"}], "ai")
        with db.tx() as c:   # 후보가 붙은 뒤 근거가 비적격이 된 옛 데이터(정정 API 는 PROVENANCE-LOCKED)
            c.execute("UPDATE records SET author_kind='unknown' WHERE user_id=%s AND id=%s", (A, rv["id"]))

        st = (await get("/api/state", A)).json()
        assert st["structure"] == expected(A), "structure = contract.respond(self)"
        assert st["structure"]["viewer"] == "self" and st["structure"]["std"] == st["std"]
        by = {o["key"]: o for o in st["structure"]["objects"]}
        assert by["axes"]["origin"] == "custom" and by["axes"]["locked"] is False and by["axes"]["std_name"] is None, "편입 전"
        js = {j["id"]: j for j in st["journal"]}
        assert js[ra["id"]]["source_id"] and js[rn["id"]]["source_id"] is None, "출처 있는 기록·없는(출처 미확인) 기록"
        assert [x["name"] for x in st["sources"]] == ["업무일지 Q45A05"] and st["source_counts"] == {
            "by_source": {js[ra["id"]]["source_id"]: 1}, "unconfirmed": 2}
        assert next(c for c in st["candidates"] if c["id"] == cid)["review"] == "author_unknown"
        assert "structure" not in db.state(A), "MCP state_get 에는 structure 없음"
        pv = (await get("/api/std/preview", A)).json()
        for body in (json.dumps(st, ensure_ascii=False), json.dumps(pv, ensure_ascii=False)):
            assert "Q45B" not in body, "다른 사용자 이름 0"
        pb = _r3_by(pv)
        assert pb["axes"]["can_incorporate"] and pb["axes"]["structure"] == "extend"
        assert not pb["principles"]["can_incorporate"] and pb["principles"]["structure"] == "conflict"

        # 편입(화면과 같은 본문) → structure 가 locked·standard 로 바뀐다
        os.environ["CRM_FEATURE_STANDARD"] = "on"
        r = await choose(A, {"std_key": "axes", "state": "incorporated", "confirmed_meaning": True,
                             "expected_revision": pb["axes"]["object"]["revision"]})
        assert r.status_code == 200 and r.json()["state"] == "incorporated", r.text
        r = await choose(A, {"std_key": "principles", "state": "incorporated", "confirmed_meaning": True,
                             "expected_revision": pb["principles"]["object"]["revision"]})
        assert r.status_code == 400 and r.json()["code"] == "DEF-INVALID", r.text
        r = await choose(A, {"std_key": "decision_events", "state": "deferred"})
        assert r.status_code == 200, r.text
        st = (await get("/api/state", A)).json()
        assert st["structure"] == expected(A)
        by = {o["key"]: o for o in st["structure"]["objects"]}
        assert (by["axes"]["origin"], by["axes"]["locked"], by["axes"]["std_name"]) == ("standard", True, contract.std_name("std:axes")), by["axes"]
        fo = {f["key"]: f for f in by["axes"]["fields"]}
        assert fo["keywords"]["locked"] is True and (fo["memo"]["origin"], fo["memo"]["locked"]) == ("custom", False), "사용자 필드는 custom"
        assert st["structure"]["std"]["objects"] == {"axes": "incorporated", "decision_events": "deferred", "principles": "none"}
        assert "Q45B" not in json.dumps(st, ensure_ascii=False) + json.dumps((await get("/api/std/preview", A)).json(), ensure_ascii=False)
        os.environ.pop("CRM_FEATURE_STANDARD", None)

        # ── 구조 그래프 — N:1(편입된 axes)·N:N(코어 후보) 관계 · escape 가 필요한 label · 결정적 · script·on* 0 · 다른 사용자 이름 0 ──
        axes_id = by["axes"]["id"]
        _r3_def(A, "q45links", 'Q45 연결 "<script>alert(1)</script> onload=x &', "collection",
                [{"key": "axis", "label": "축 <b onclick=y>", "value_type": "reference", "target_kind": "item", "target_object_id": axes_id,
                  "multiple": False},
                 {"key": "claims", "label": "판단들", "value_type": "reference", "target_kind": "candidate", "multiple": True}])
        st = (await get("/api/state", A)).json()
        svg = st["structure_svg"]
        assert st["structure"] == expected(A), "structure = contract.respond(self)"
        assert svg == structure_svg.render(st["structure"]) == structure_svg.render(expected(A)), "structure_svg = render(structure) — 결정적"
        assert "structure_svg" not in db.state(A), "MCP state_get 에는 structure_svg 없음"
        objs, rels = st["structure"]["objects"], st["structure"]["relations"]
        assert sorted(r["cardinality"] for r in rels) == ["N:1", "N:N"] and [c["kind"] for c in st["structure"]["core"]] == ["candidate"]
        assert all(f'href="#{structure_svg.node_anchor(i)}"' in svg for i in range(len(objs)))
        assert all(f'href="#{structure_svg.rel_anchor(i)}"' in svg for i in range(len(rels)))
        assert f'href="#{structure_svg.core_anchor("candidate")}"' in svg
        tags = svg_tags(svg)
        assert tags and tags[0][0] == "svg" and not [t for t, _ in tags if t in ("script", "foreignobject", "iframe")], "script 0"
        assert not [(t, k) for t, ks in tags for k in ks if k.lower().startswith("on")], "on* 속성 0"
        assert "<script" not in svg.lower() and "&lt;script&gt;" in svg and "&lt;b onclick=y&gt;" in svg, "label 은 escape — 글자로만 남는다"
        assert "Q45B" not in svg and "Q45B" not in json.dumps(st, ensure_ascii=False), "다른 사용자 이름 0"

        # /app — 일반·관리자 모두 편입 대화창·구조 정의 보기 코드가 있다(CSP 해시 = 보낸 본문)
        for admin in (False, True):
            _set_role(A, "admin" if admin else "user")
            r = await get("/app", A)
            hs = web.inline_script_hashes(r.text)
            assert len(hs) == 1 and f"'sha256-{hs[0]}'" in r.headers["content-security-policy"]
            assert r.text.count('<dialog id="std-dlg"') == 1 and "renderDefs" in r.text, admin
            assert tokens in r.text and "defsJump" in r.text, ("그래프 전환·토큰", admin)
            assert ('id="desk-dlg"' in r.text) is not admin, "Q1b·Q1 분기 유지"
        _set_role(A, "user")
    finally:
        site.dev, site.dev_user = real
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        wipe()
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (emails,)).fetchone()["n"] == 0
    print("OK — Q4·Q5 사용자 화면(app.html 정적·structure = contract.respond(self)·편입 전후 locked·MCP 없음·다른 사용자 이름 0"
          "·구조 그래프 structure_svg = render(structure)·script·on* 0·토큰 data-theme"
          "·출처 미확인·재검토·관리자 /app 대화창) 통과")


# ── 23) PR A 운영 Setup — 왼쪽 메뉴 · Quick Find · Setup Home · Object Manager(서비스 표준 조회) ─────────
PRA = {k: f"pra{k}@example.com" for k in ("a", "u", "m")}   # a 관리자 · u 일반 사용자 · m 금지 값 표식 사용자
TEST_EMAILS += tuple(PRA.values())
PRA_SVG_SHA = "c005852b5f2b51a12557ce4f2205204f15d125e4382836cd9b4bada625b0ad2b"   # 사용자 DMO SVG(contract 예제 × 수신자, PR A 전 바이트)
PRA_STD_SHA = "c08007a2db55f219d7f5b16b5aa518e2b2081f95e1a3adfee52240d1380d00bf"   # templates/std@1.json 동결
PRA_OBJECTS = ("core-question", "core-record", "core-candidate", "core-ticket", "core-source", "std-axis", "std-decision", "std-principle")
PRA_TPL = ("tpl-axis", "tpl-decision", "tpl-principle", "tpl-homework", "tpl-publication", "tpl-method")


def _pra_visible(t, skip=()):
    """노출 검사 대상 — 보이는 글자(태그 제거) + href·title·aria-label·id 속성 값(skip = 운영 메뉴 경로처럼 원래 있는 값). <style> 은 뺀다."""
    import html
    t = re.sub(r"<style>.*?</style>", "", t, flags=re.S)
    attrs = " ".join(v for v in re.findall(r'\b(?:href|title|aria-label|id)="([^"]*)"', t) if v not in skip)
    return html.unescape(re.sub(r"<[^>]+>", " ", t)) + " " + html.unescape(attrs)


async def test_prA_setup():
    """PR A: 접근 통제(새 GET 전부 무세션·일반·DEV 404, 모르는 ID·판·조합 404) · 노출 0(본문·이름표·출처 이름·위치·key·선택지 값 표식)
    · 동의 경계 · DB 경계(서비스 정의·Quick Find 사용자 DB 0) · 옛 URL · Quick Find 경계값 · 스크립트 0 · 보안 헤더 · Object Manager 정확히 8
    · 결정성(반복·입력 순서·재시작) · 정의 일치 · std@1 동결·사용자 DMO 바이트 · 옛 _admin_page 호환 · user_delete cascade 회귀."""
    import hashlib
    import html as _html
    import subprocess
    import unicodedata
    from urllib.parse import quote, urlencode
    import admin_db
    import service_schema
    import structure_svg
    from psycopg.types.json import Jsonb
    site = main.site
    U = {k: db.user_by_subject(f"pra_{k}_sub", e)["id"] for k, e in PRA.items()}
    A, M = U["a"], U["m"]
    _set_role(A, "admin")
    mk = "PRAMK" + secrets.token_hex(4)   # 모든 금지 값에 들어간다 — 어떤 새 화면에도 없어야 한다
    with db.tx() as c:
        c.execute("INSERT INTO questions (id,user_id,text) VALUES (%s,%s,%s)", (f"q_{mk}", M, mk + "-Q"))
        c.execute("INSERT INTO sources (user_id,id,kind,name,location) VALUES (%s,%s,'file',%s,%s)", (M, f"s_{mk}", mk + "-SRCNAME", mk + "-SRCLOC"))
        c.execute("INSERT INTO records (id,user_id,title,body,source_id) VALUES (%s,%s,%s,%s,%s)", (f"r_{mk}", M, mk + "-RT", mk + "-RB", f"s_{mk}"))
        c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision) VALUES (%s,%s,%s,%s,'collection',1)",
                  (M, f"od_{mk}", f"key{mk}", mk + "-PEOPLE"))
        c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,options) VALUES (%s,%s,%s,%s,'select',%s)",
                  (M, f"od_{mk}", f"fkey{mk}", mk + "-FL", Jsonb([{"value": f"val{mk}", "label": mk + "-OPT"}])))
        c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,title,body,data,revision) VALUES (%s,%s,%s,1,%s,%s,%s,1)",
                  (M, f"i_{mk}", f"od_{mk}", mk + "-IT", mk + "-IB", Jsonb({f"fkey{mk}": f"val{mk}"})))
    tk = "tk_pra" + secrets.token_hex(3)   # 문의 id 는 목록 링크에 나온다(허용) — 표식을 넣지 않는다
    with db.tx() as c:
        c.execute("INSERT INTO tickets (id,user_id,kind,text,via) VALUES (%s,%s,'privacy',%s,'web')", (tk, M, mk + "-TK"))
    sd = web.service_def()
    new_gets = (["/admin/find", "/admin/find?q=" + quote("기준"), "/admin/find?q=" + mk, "/admin/schema", "/admin/schema/templates/author-1"]
                + [f"/admin/schema/objects/{o}" for o in PRA_OBJECTS] + [f"/admin/schema/templates/author-1/objects/{o}" for o in PRA_TPL])
    bad_ids = ("/admin/schema/objects/axes", "/admin/schema/objects/questions", "/admin/schema/objects/tpl-homework", "/admin/schema/objects/record",
               "/admin/schema/objects/core-%3Cscript%3E", "/admin/schema/templates/std@1", "/admin/schema/templates/author@1",
               "/admin/schema/templates/author", "/admin/schema/templates/author-1/objects/std-axis", "/admin/schema/templates/nope/objects/tpl-axis",
               "/admin/schema/templates/author-1/objects/open_questions", "/admin/schema/objects/" + mk)
    # 서비스 정의에서도 보이면 안 되는 것 — 운영자 전용 열·소유자 열·선택지 값·정의 파일 안 id·내부 파일·테이블 이름.
    # (코어 열·std@1·author@1 key 는 「API 이름」으로 보인다 — 서비스가 정한 제품 문자열. key 0 규칙은 사용자 구조에만)
    keys = {"user_id", "admin_note", "hint", "item_links", "schema.sql", "templates/"}
    for doc in (json.loads(web.STD_PATH.read_text(encoding="utf-8")), web.template_schema()):
        keys |= {op["value"] for o in doc["objects"] for f in o["fields"] for op in f["options"]}
        keys |= {o["id"] for o in doc["objects"]}
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    good = site.session_value(A)

    async def req(method, path, cookie=good, **kw):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            if cookie:
                h.cookies.set("crm_session", cookie)
            return await h.request(method, path, **kw)

    def clean(r, path, status=200):
        """상태 · CSP·no-store · 스크립트·이벤트 속성·javascript: 0 · 금지 값 표식 0."""
        out = r.text + json.dumps(dict(r.headers), ensure_ascii=False)
        assert r.status_code == status, (path, r.status_code, r.text[-300:])
        assert mk not in out, (path, "금지 값 노출")
        assert "script-src 'self'" in r.headers["content-security-policy"] and "unsafe-inline" not in r.headers["content-security-policy"], path
        assert r.headers["cache-control"] == "no-store", path
        t = r.text
        assert "<script" not in t.lower() and not re.search(r"\son[a-z]+\s*=", t, re.I) and "javascript:" not in t.lower(), (path, "스크립트 0")
        return t

    def no_keys(t, path):
        vis = _pra_visible(t, {h for _, h, _, _, _ in web.ADMIN_MENU})
        hit = sorted(k for k in keys if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", vis))
        assert not hit, (path, "운영자 전용 열·선택지 값·내부 이름 노출", hit)
    try:
        # ── 접근 통제: 무세션·일반 사용자·DEV 404(본문 없음) ──
        for cookie in (None, site.session_value(U["u"]), site.session_value(M)):
            for path in new_gets + list(bad_ids):
                r = await req("GET", path, cookie)
                assert r.status_code == 404 and "<" not in r.text and mk not in r.text, (path, r.status_code)
        site.dev, site.dev_user = True, PRA["a"]
        try:
            for path in new_gets:
                assert (await req("GET", path)).status_code == 404, ("DEV", path)
        finally:
            site.dev, site.dev_user = False, ""
        # ── 관리자: 새 GET 전부 200 · 노출 0 · key 0 · 편집 form 없음(Quick Find GET form 하나뿐) ──
        for path in new_gets:
            r = await req("GET", path)
            t = clean(r, path)
            no_keys(t, path)
            forms = re.findall(r"<form\b[^>]*>", t)
            assert forms == ['<form class="qf" method="get" action="/admin/find" role="search">'], (path, forms)
            assert "<button" not in t.replace('<button type="submit">찾기</button>', ""), (path, "편집·설치 버튼 없음")
            assert all(f'href="{h}"' in t for _, h, _, _, _ in web.ADMIN_MENU) and 'href="/app"' in t, (path, "왼쪽 메뉴")
            assert re.search(r'<input id="qf" type="search" name="q" maxlength="100"[^>]*>', t) and " value=" not in re.search(r"<input id=\"qf\"[^>]*>", t).group(0), \
                (path, "검색어를 입력창에 되비추지 않는다")
        # 모르는 ID·판·조합 → 404(관리자에게도) · 입력 되비춤 없음 · 헤더 유지 · 쿼리 키 400
        for path in bad_ids:
            r = await req("GET", path)
            assert r.status_code == 404, (path, r.status_code)
            if "<html" in r.text:
                clean(r, path, 404)
                assert "NOT-FOUND" in r.text and path.rsplit("/", 1)[1] not in r.text.replace("/admin/schema", ""), path
        for path in ("/admin/schema/objects/std-axis?x=1", "/admin/schema/templates/author-1?u=1", "/admin/schema/templates/author-1/objects/tpl-axis?x=",
                     "/admin/find?q=a&q=b", "/admin/find?x=1", "/admin/find?q=a&zz=1"):
            r = await req("GET", path)
            clean(r, path, 400)
            assert "ARG-INVALID" in r.text, path
        # ── 메뉴 · breadcrumb · 좁은 화면 ──
        labels = [m[2] for m in web.ADMIN_MENU]
        assert labels == ["Setup Home", "Object Manager", "Users", "Reports", "Support", "Administration"], labels
        for key, href, label, _, _ in web.ADMIN_MENU:
            t = clean(await req("GET", href), href)
            nav = t[t.index('<nav class="side"'):t.index("</nav>", t.index('<nav class="side"'))]
            assert re.findall(r">([^<]+)</a></li>", nav) == labels, (href, "메뉴 순서")
            assert re.findall(r"<a [^>]*aria-current", t) == [f'<a href="{href}" aria-current'], (href, "현재 메뉴 하나")
        t = clean(await req("GET", "/admin/schema/templates/author-1/objects/tpl-homework"), "tpl-homework")
        assert ('<nav class="crumbs" aria-label="위치"><a href="/admin">Setup Home</a> › <a href="/admin/schema">Object Manager</a> › '
                '<a href="/admin/schema/templates/author-1">이전 원작자 템플릿 · author@1</a> › <span aria-current="page">숙제</span></nav>') in t, t[:3000]
        assert '<a href="/admin/schema" aria-current=page>' in t, "상세는 상위 메뉴 활성"
        assert "@media (max-width:760px){.shell{grid-template-columns:1fr" in web._ADMIN_CSS and "prefers-color-scheme" not in web._ADMIN_CSS
        # 옛 _admin_page 호출(위치 인자 4개) 그대로 동작
        r = web._admin_page("옛 호출", "<p>본문</p>", 200, "users")
        t = r.body.decode()
        assert r.status_code == 200 and '<a href="/admin/users" aria-current=page>' in t and '<span aria-current="page">Users</span>' in t
        assert r.headers["cache-control"] == "no-store" and "script-src 'self'" in r.headers["content-security-policy"]
        # ── Setup Home · Support · Users · Administration 재배치 ──
        t = clean(await req("GET", "/admin"), "/admin")
        assert [x for x in re.findall(r"<h2>([^<]+)", t)] == ["개요", "확인할 오류", "문의"], re.findall(r"<h2>([^<]+)", t)
        assert "코어 + std@1" in t and "표준 정의 조회 전용" in t and "처리 상태별 건수는 이 화면에서 확인할 수 없어요" in t
        assert "배포" not in t, "배포 상태 표시 없음"
        t = clean(await req("GET", "/admin/tickets?status=all&kind=privacy&sort=new&cols=via,closed"), "/admin/tickets")
        assert f"운영자 확인 후 처리하며, {admin_db.PRIVACY_DUE_DAYS}일 안에 처리 결과를 안내합니다." in t and PRA["m"] in t
        assert "삭제" not in t, "삭제 실행 버튼·안내 없음"
        r = await req("GET", f"/admin/tickets/{tk}")
        t = r.text
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and mk + "-TK" in t and '<span aria-current="page">문의 상세</span>' in t, "문의 레코드 = 허용 응답(원문) · breadcrumb"
        t = clean(await req("GET", "/admin/users"), "/admin/users")
        assert '<th class="num">필드</th>' in t and f'href="/admin/users/{site._opaque(M)}"' in t, "옛 스키마 사용자 표 → Users"
        t = clean(await req("GET", "/admin/setup"), "/admin/setup")
        assert "<h1>Administration</h1>" in t and 'action="/admin/role"' in t and 'action="/admin/block"' in t
        # ── 옛 URL · POST 경로 ──
        r = await req("GET", "/admin/reauth")
        assert r.status_code == 302 and r.headers["location"] == "/admin"
        r = await req("GET", f"/admin/schema?u={site._opaque(M)}")
        assert r.status_code == 302 and r.headers["location"] == f"/admin/users/{site._opaque(M)}" and mk not in r.text
        for path, code in ((f"/admin/schema?u={'0' * 12}", 404), ("/admin/schema?u=x&u=y", 400), ("/admin/schema?x=1", 400)):
            assert (await req("GET", path)).status_code == code, path
        for path in ("/admin/reports?ds=usage&g1=day", "/admin/tickets?status=all&kind=bug&sort=new&cols=via,closed"):
            clean(await req("GET", path), path)
        for path, form in (("/admin/role", {"uid": U["u"], "role": "admin"}), ("/admin/block", {"uid": U["u"]}),
                           (f"/admin/tickets/{tk}/status", {"status": "ack"})):
            r = await req("POST", path, data=form, headers={"origin": "https://evil.example"})
            assert r.status_code == 403 and "ORIGIN" in r.text, (path, r.status_code)
            assert (await req("POST", path, site.session_value(U["u"]), data=form, headers={"origin": PUBLIC})).status_code == 404, path
        assert _urow(U["u"])["role"] == "user" and not _urow(U["u"])["blocked_at"]
        # ── Object Manager: 정확히 코어 5 + std@1 3, 템플릿·PR B·사용자 정의 혼입 없음 ──
        t = clean(await req("GET", "/admin/schema"), "/admin/schema")
        svg = t[t.index("<svg"):t.index("</svg>") + 6]
        assert [o["id"] for o in sd["objects"]] == list(PRA_OBJECTS) and [o["name"] for o in sd["objects"]] == ["질문", "기록", "후보", "문의", "출처", "축", "결정", "기준"]
        assert sorted(set(re.findall(r'href="/admin/schema/objects/([\w-]+)"', t))) == sorted(PRA_OBJECTS)
        assert svg.count('r="42"') == 8 and svg.count("stroke-dasharray") == 3, "노드 8 · 정의상 잠금 = std@1 3"
        assert re.findall(r'<a href="(/admin/schema/objects/[\w-]+)" aria-label', svg) == [f"/admin/schema/objects/{o}" for o in PRA_OBJECTS], "노드 링크 = 상세"
        assert "tpl-" not in t and all(w not in _pra_visible(t) for w in ("숙제", "발행", "접근 방식", "std@2", "사람", "요약", "반응")), "템플릿·PR B 혼입 없음"
        assert "이전 원작자 템플릿 · author@1" in t and 'href="/admin/schema/templates/author-1"' in t and "현재 표준" not in t.replace("현재 표준이 아니에요", "")
        assert "서비스 정의이며, 개별 계정에 설치된 구조와 다를 수 있습니다." in t and 'href="/admin/users"' in t
        assert f'href="/admin/users/' not in t, "Object Manager 엔 사용자 목록이 없다"
        # 관계 = 정의에서 확인된 것만(schema.sql FK · std@1 참조 필드). 그래프 선 8 · 그리지 않는 연결 2(분류 연결 · 문의 이력)
        names = service_schema.names_of(sd["objects"])
        drawn = [(names[r["from"]], r["name"], names[r["to"]]) for o in sd["objects"] for r in o["relations"] if r["graph"]]
        assert drawn == [("기록", "출처", "출처"), ("기록", "질문", "질문"), ("후보", "근거 기록", "기록"), ("후보", "질문", "질문"), ("결정", "판단", "후보"),
                         ("기준", "근거 판단", "후보"), ("기준", "근거 결정", "결정"), ("기준", "관심 축", "축")], drawn
        assert svg.count("<line ") == 2 * len(drawn), "관계마다 선(+투명 넓은 선) 하나"
        assert [(r["name"], r["to_text"] or r["from_text"]) for o in sd["objects"] for r in o["relations"] if not r["graph"]] == [
            ("붙는 곳 · 관심 축", "항목 — 대상 오브젝트 모름(정의 미확인) · 분류 대상은 사용자별 역할로 정함"), ("변경 이력", "문의 변경 이력(오브젝트 아님)")]
        # 코어 표시명 = 기존 정본(web.CORE_LABEL · app.html LABELS). 출처는 두 정본에 없다(보고)
        app = (HERE / "app.html").read_text(encoding="utf-8")
        assert [o["name"] for o in sd["objects"][:4]] == [web.CORE_LABEL[t_] for _, t_, _, _ in service_schema.CORE[:4]]
        assert all(f'{k}: "{v}"' in app for k, v in (("question", "질문"), ("record", "기록"), ("candidate", "후보"))), "app.html LABELS"
        # API 이름 = 코어 열·테이블 · std@1 key(Salesforce API Name 꼴) — 표시명과 함께
        assert [o["api"] for o in sd["objects"]] == ["questions", "records", "candidates", "tickets", "sources", "axes", "decision_events", "principles"]
        assert '<td class="api">records</td>' in t and '<td class="api">decision_events</td>' in t and '<th>API 이름</th>' in t
        assert t.count('<div class="tbl fit">') == 2 and '<div class="tbl">' not in t, "Object Manager 표는 칸 줄바꿈(가로 넘침 0)"
        for p_ in new_gets + [h for _, h, _, _, _ in web.ADMIN_MENU]:   # 모든 /admin 표는 가로 스크롤 래퍼(.tbl overflow-x:auto) 안 — 문서 넘침 0
            t_ = (await req("GET", p_)).text
            assert t_.count("<table") == len(re.findall(r'<div class="tbl(?: fit)?"><table>', t_)), (p_, "래퍼 밖 표")
        assert ".tbl{overflow-x:auto}" in web._ADMIN_CSS
        rk = {(names[r["from"]], r["name"]): (r["kind"], r["required"], r["cardinality"]) for o in sd["objects"] for r in o["relations"] if r["graph"]}
        assert rk[("후보", "근거 기록")] == ("M-D", True, "N:1") and rk[("후보", "질문")] == ("Lookup", False, "N:1") and rk[("기록", "출처")] == ("Lookup", False, "N:1")
        assert rk[("기준", "관심 축")] == (None, False, "N:N"), "std@1 관계 종류는 정의에 없다 → 모름"
        assert all(o["lock"]["state"] is None for o in sd["objects"][:5]) and all(o["lock"]["state"] is True for o in sd["objects"][5:])
        assert all(o["lock"]["state"] is False for o in sd["templates"]["author-1"]["objects"])
        # 필드 = 정의 전부(조용히 빼지 않음). 코어 = schema.sql 열(소유자·운영자 전용 열 제외) · std@1 = 정본 필드
        core = {t_["table"]: t_ for t_ in web.core_schema(web.SCHEMA_SQL.read_text(encoding="utf-8"))}
        std = {o["key"]: o for o in json.loads(web.STD_PATH.read_text(encoding="utf-8"))["objects"]}
        want = [len(core[tb]["fields"]) for _, tb, _, _ in service_schema.CORE] + [len(std[k]["fields"]) for _, k, _ in service_schema.STD]
        assert [len(o["fields"]) for o in sd["objects"]] == want, want
        assert sd["objects"][3]["fields"][-1]["name"] == "공개 갱신" and "내부 메모" not in json.dumps(sd, ensure_ascii=False)
        assert [f["choices"] for f in sd["objects"][3]["fields"] if f["choices"] is not None] == [4, 2, 5], "문의 열거 CHECK 개수(종류·경로·상태)"
        with pytest_raises(ValueError):
            service_schema.build([{**core["questions"], "fields": core["questions"]["fields"] + [{"name": "zz_new", "type": "text", "required": False,
                                                                                                  "choices": None}]}] + list(core.values())[1:],
                                 json.loads(web.STD_PATH.read_text(encoding="utf-8")), web.template_schema())
        # ── 정의 일치: 목록·그래프·상세·검색의 표시명·링크가 같다 ──
        idx = web.find_index()
        for o in sd["objects"]:
            href = f"/admin/schema/objects/{o['id']}"
            assert f'<a href="{href}">{o["name"]}</a>' in t, (o["id"], "목록")
            assert re.search(rf'<a href="{href}" aria-label="{re.escape(o["name"])} · ', svg), (o["id"], "그래프")
            d = clean(await req("GET", href), href)
            assert f"<h1>{o['name']}</h1>" in d and f'<span aria-current="page">{o["name"]}</span>' in d
            assert d.count('<tr id="f') == len(o["fields"]) and d.count('<tr id="r') == len(o["relations"]), o["id"]
            hits = [e for e in service_schema.find(o["name"], idx)[1] if e["kind"] == "object"]
            assert {"title": o["name"], "href": href} in [{"title": e["title"], "href": e["href"]} for e in hits], (o["id"], "검색")
        d = clean(await req("GET", "/admin/schema/objects/core-question"), "core-question")
        assert "들어오는 관계 2" in d and "모름(정의 미확인)" in d, "질문 = 기록·후보가 가리킨다 · 코어 잠금 모름"
        d = clean(await req("GET", "/admin/schema/objects/std-principle"), "std-principle")
        assert structure_svg.LOCK_TEXT in d and "조회 전용과 다른 뜻" in d and '<td class="api">evidence_claims</td>' in d
        d = clean(await req("GET", "/admin/schema/objects/core-candidate"), "core-candidate")
        assert '<td>근거 기록</td><td class="api">record_id</td>' in d, "필드 표시명 · API 이름"
        d = clean(await req("GET", "/admin/schema/templates/author-1/objects/tpl-principle"), "tpl-principle")
        assert '<td class="api">register</td>' in d and "customer" not in d and "고객 업무" not in d, "템플릿 API 이름 · 선택지 값·이름 0(개수만)"
        # ── 결정성: 반복 · 입력 순서 · 재시작 ──
        om = web.render_object_manager()
        assert om == web.render_object_manager()
        sh = lambda d_: {**d_, "objects": list(reversed(d_["objects"]))}   # noqa: E731
        again = service_schema.build(list(reversed(list(core.values()))), sh(json.loads(web.STD_PATH.read_text(encoding="utf-8"))), sh(web.template_schema()))
        assert again == sd, "입력 순서를 바꿔도 같은 자료"
        g1, h1, _ = service_schema.graph(again["objects"])
        g0, h0, _ = service_schema.graph(sd["objects"])
        assert structure_svg.render(g1, h1) == structure_svg.render(g0, h0) == svg
        code = "import hashlib, web; print(hashlib.sha256(web.render_object_manager().encode()).hexdigest())"
        outs = {subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip() for _ in range(2)}
        assert outs == {hashlib.sha256(om.encode()).hexdigest()}, outs
        # ── std@1 동결 · 사용자 DMO 바이트 그대로 · 계약 예제 그대로 ──
        assert hashlib.sha256(web.STD_PATH.read_bytes()).hexdigest() == PRA_STD_SHA, "std@1 정본이 바뀌었다"
        hh = hashlib.sha256()
        for name in contract.EXAMPLES:
            for v in contract.VIEWERS:
                resp = contract.respond(contract.EXAMPLES[name], v)
                assert contract.dump(resp) == contract.example_path(name, v).read_text(encoding="utf-8"), (name, v)
                hh.update(structure_svg.render(resp).encode())
        assert hh.hexdigest() == PRA_SVG_SHA, "사용자 DMO SVG 바이트가 바뀌었다"
        # ── Quick Find 경계값 ──
        f = service_schema.find
        st, hits, over = f("", idx)
        assert st == "empty" and [e["title"] for e in hits] == labels and not over
        assert f("   \t ", idx)[0] == "empty"
        st, hits, _ = f("기준", idx)
        order = {"menu": 0, "object": 1, "field": 2, "relation": 2}
        assert st == "ok" and hits and [order[e["kind"]] for e in hits] == sorted(order[e["kind"]] for e in hits), "운영 메뉴 → 오브젝트 → 필드·관계"
        assert hits[0]["href"] == "/admin/schema/objects/std-principle"
        assert f(unicodedata.normalize("NFD", "기준"), idx)[1] == hits, "NFC 정규화"
        assert f("  기준  ", idx)[1] == hits, "앞뒤 공백"
        assert [e["href"] for e in f("OBJECT manager", idx)[1]] == ["/admin/schema"] and f("setup HOME", idx)[1][0]["href"] == "/admin", "대소문자 무시"
        both = f("기준 근거", idx)[1]
        assert both and all("기준" in e["hay"] and "근거" in e["hay"] for e in both) and len(both) < len(hits), "AND"
        assert f("가" * 100, idx)[0] == "ok" and f(" " + "가" * 100 + " ", idx)[0] == "ok" and f("가" * 101, idx)[0] == "too_long", "100자 경계"
        for q in ("숙제", "발행", "접근 방식", "open_questions", "craft", "admin_note", "hint", "customer", mk, f"key{mk}", mk + "-PEOPLE", PRA["m"],
                  "SRCNAME", "없는말xyz", ".*", "%", "[a-z]+"):
            assert f(q, idx)[1] == [], (q, "템플릿 내부·운영자 전용 열·선택지 값·사용자 정의 key·label·사용자 자료·정규식은 찾지 않는다")
        # API 이름도 찾는다 — 서비스 정의(코어 + std@1)만
        assert f("principles", idx)[1][0]["href"] == "/admin/schema/objects/std-principle"
        assert [e["title"] for e in f("record_id", idx)[1]] == ["후보 · 근거 기록", "후보 · 근거 기록 → 기록"], f("record_id", idx)[1]
        assert [e["title"] for e in f("EVIDENCE_claims", idx)[1]] == ["기준 · 근거 판단", "기준 · 근거 판단 → 후보"]
        paren = f("(", idx)[1]
        assert paren and all("(" in e["hay"] for e in paren), "특수문자는 글자 그대로(정규식 아님)"
        many = [{"kind": "object", "title": f"t{i}", "desc": "", "href": "/admin/schema", "hay": "같은말"} for i in range(60)]
        st, hits, over = f("같은말", many)
        assert len(hits) == service_schema.FIND_LIMIT and over and hits[0]["title"] == "t0"
        assert not any(set(e) - {"kind", "title", "desc", "href", "hay"} for e in idx), "색인 = 허용 글자만"
        # HTTP — 되비춤 없음 · 400 · 넘침 문구 · 결과 없음
        for q in ("<b>xss</b>", '"><i>q', "zz되비춤zz", mk):   # 결과에 없는 검색어 — 화면 어디에도(본문·속성) 나오면 되비춤이다
            r = await req("GET", "/admin/find?" + urlencode({"q": q}))
            t = clean(r, q)
            assert q not in t and _html.escape(q) not in t and quote(q) not in t, (q, "검색어 되비춤")
        t = clean(await req("GET", "/admin/find?" + urlencode({"q": "기준"})), "기준")
        assert '<a href="/admin/schema/objects/std-principle">기준</a>' in t and "결과 " in t
        t = clean(await req("GET", "/admin/find?" + urlencode({"q": "가" * 101})), "101", 400)
        assert "100자까지" in t and "가" * 101 not in t
        clean(await req("GET", "/admin/find?" + urlencode({"q": "가" * 100})), "100")
        assert "찾은 항목이 없어요" in clean(await req("GET", "/admin/find?q=" + quote("없는말xyz")), "none")
        t = clean(await req("GET", "/admin/find"), "/admin/find")
        assert "운영 메뉴 6" in t
        saved = web._FIND_INDEX
        web._FIND_INDEX = many
        try:
            t = clean(await req("GET", "/admin/find?q=" + quote("같은말")), "many")
            assert t.count('<li><span class="tag">') == 50 and "검색어를 더 구체적으로 입력하세요." in t
        finally:
            web._FIND_INDEX = saved
        # ── DB 경계: 서비스 정의·Quick Find 는 admin_db 조회 0 · 순수 모듈 ──
        real_check = admin_db._check

        def boom(*a, **k):
            raise AssertionError("서비스 정의 화면이 운영 DB 조회를 불렀다")
        admin_db._check = boom
        try:
            for path in new_gets:
                clean(await req("GET", path), path)
        finally:
            admin_db._check = real_check
        ssrc = (HERE / "service_schema.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(db|admin_db|psycopg|httpx)\b", ssrc, re.M), "service_schema 는 순수 모듈"
        _, blk = _web_admin_block()
        assert set(re.findall(r"\bdb\.(\w+)", blk)) <= ADB_ALLOWED_DB
        wsrc = (HERE / "web.py").read_text(encoding="utf-8")
        assert 'Route("/admin/find", self.admin_find)' in wsrc and '"web:find' not in wsrc, "Quick Find 검색어를 사용 집계로 남기지 않는다"
        # ── 동의 경계: 비동의·동의 모두 기존 contract 결과 · 항상 금지 값(key·선택지 값·출처 이름·위치·본문)은 동의해도 0 ──
        path = f"/admin/users/{site._opaque(M)}"
        t = (await req("GET", path)).text
        assert mk not in t and "동의 없음" in t
        db.optional_consent_set(M, "struct_names", True)
        t = (await req("GET", path)).text
        assert mk + "-PEOPLE" in t and mk + "-FL" in t and mk + "-OPT" in t, "동의 = 현재 label·선택지 label"
        assert all(x not in t for x in (f"key{mk}", f"fkey{mk}", f"val{mk}", mk + "-SRCNAME", mk + "-SRCLOC", mk + "-RB", mk + "-RT", mk + "-IB", mk + "-Q")), \
            "항상 금지 값은 동의해도 0"
        for p_ in new_gets + ["/admin/users", "/admin/find?q=" + quote(mk + "-PEOPLE"), f"/admin/find?q=key{mk}"]:
            t = clean(await req("GET", p_), p_)   # 동의해도 서비스 정의 화면·Users 목록·Quick Find 엔 사용자 정의 key·label 0
            if p_.startswith("/admin/find?q=") and mk in p_:
                assert "찾은 항목이 없어요" in t, p_
        db.optional_consent_set(M, "struct_names", False)
        # ── user_delete cascade 회귀(삭제 CLI 시험 아님 — PR B) ──
        db.user_delete(M)
        with db.tx() as c:
            left = {tb: c.execute(f"SELECT count(*) AS n FROM {tb} WHERE user_id=%s", (M,)).fetchone()["n"]
                    for tb in ("questions", "sources", "records", "object_defs", "field_defs", "items", "tickets", "optional_consents")}
        assert not any(left.values()), left
        r = await req("GET", f"/admin/tickets/{tk}")
        assert r.status_code == 404 and mk not in r.text
    finally:
        site.dev, site.dev_user = real
    print("OK — 23 PR A 운영 Setup(새 GET 404 무세션·일반·DEV · 모르는 ID·판 404 · 사용자 자료·사용자 key·label 0 · 표시명+API 이름 · 편집 form 0 · 메뉴 6 순서·활성 1·breadcrumb · "
          "Setup Home 3구역 · Support 안내 · 옛 URL·POST · Object Manager 8·선 8·잠금 3 · 정의 일치 · 결정성(반복·순서·재시작) · std@1·DMO 바이트 · "
          "Quick Find 경계값 · DB 조회 0 · 동의 경계 · cascade) 통과")


class pytest_raises:
    """새 프레임워크 없이 예외 기대(with 블록)."""
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        assert et is not None and issubclass(et, self.exc), f"{self.exc.__name__} 를 기대했다"
        return True


# ── 임시 스키마 격리 (CRM_TEST_SCHEMA) ─────────────────

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
    name = (envnames.env("TEST_SCHEMA") or "").strip()
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
