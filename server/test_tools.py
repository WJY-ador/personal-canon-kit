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
   · 비DEV+CRM_CONTACT 없음·공백 → 기동 성공(선택 env) (app.html 정적 단언은 ③-0.2 에 test_web_home.py test_static_moved 로 옮겼다)
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
   · /app 응답 CSP 해시 일치
16) 통합(P5-6): 권한 회수 뒤 /admin 전부 404·문의 처리 거절 · 문의 소유권(웹·MCP) · 공개/내부 이력 · 파기 cascade
   · 관리자 세션으로도 /app·/api 에 관리자 데이터 0 · 이름 공유(사용자 상세에만·리포트 0·철회 뒤 0) · 「내 문의」 공개 갱신순(웹=MCP)
17) R1(PR #20 2단계): 기능 플래그 CRM_FEATURE_STANDARD·SOURCES 둘 다 미설정(off)에서 로그인(웹·DEV) · /admin 권한(일반 404·관리자 바로·재인증 없음)
   · /admin 문의 처리(상태·답변) · Q1 관리자 /app = ⚙ + 「운영 콘솔」·로그아웃만(문의·이름 공유 메뉴·문의 대화창은 보내지 않음)
   · Q1b 일반 사용자 /app = 하단 문의 섹션 없음 · 「문의 보내기」·「내 문의」는 대화창(desk-dlg) · 「문의 섹션 숨기기」 없음
18) R2(PR #20 3단계, DB 없이): 기능 플래그 제거(PR B — 코드·render.yaml 참조 0) · 수신자별 응답(본인·운영자 비동의·동의) 허용 목록
   · 이름 누출 0(비동의: label·key·선택지·출처 name·location·id / 동의: key·선택지 값·출처·id) · origin·관계 파생 · std 모양 · 오류 코드 문구 · 예제 동결 일치
19) R3(PR #20 4단계): 표준 편입 미리보기 판정(누락·일치·확장·충돌 사유·참조 의존·초안·보관) · 쓰기 0(READ ONLY·전 테이블 해시)·플래그 무관
   · std_choices 보류·거절(env 무관 기록(PR B) · 덮어쓰기 · 편입된 것 STANDARD-LOCKED · 무관한 쓰기 안 막음) · 본인만(/api/std/*)
   · /api/state std 모양(MCP 엔 없음) · definitions() template_slot·std_id → origin · init_schema 옛 DB 2회 멱등
20) Q2(PR #20 5단계): template_std == 동결 파일 · 신규 시드(on)/안 함(off) · 시드 실패 주입 → 로그인 성공·seed_pending·재시도·정의 생기면 중단
   · 동시 재시도 1회 · 기존 사용자(표식 없음) 쓰기 0 · 편입 적용(누락·일치·확장·새 id) · 재검증(revision·대상 상태·이미 편입)·참조 의존·충돌·확인 없음
   · 잠금 표(거절·허용, propose·apply, 플래그 off 에서도, select 선택지) · 일일 한도·template_slot 백필 제외 · init_schema 2회 멱등
21) Q3 축소(PR #20 6단계): 출처(SOURCES off 기록만·on 찾거나 만들기·사용자별·FK·옛 기록 미확인·본인만·MCP 인자) · 분류 역할(이관·멱등·시드·
   늦은 축·record_attach 역할 경유·CLASSIFY-OFF) · 확정 재검사 4사유·review(웹=MCP)·해제 · external 거절·정정 허용 · init_schema 2회 멱등
23) PR A 운영 Setup: 왼쪽 메뉴·Quick Find(GET /admin/find, 서비스 정의만)·Setup Home 3구역·Object Manager(코어 5 + std@1 3, 상세·템플릿 구역)
   — 새 GET 404 · 노출·key 0 · DB 조회 0 · 결정성 · 정의 일치 · std@1·사용자 DMO 바이트 · 옛 URL·POST · cascade 회귀

24) PR B: 스키마(옛 DB 2회 멱등 = 새 DB 모양·CHECK 확장) · 표시 이름 관문(MCP 쓰기 16 NAME-REQUIRED·허용 17·분류 누락 0·웹 403·예외 경로)
   · 새 탐색 수락 · 사람·출처·출처 요약/반응(남의 id = 없는 id·revision·검토 상태·인용 차단 4곳·한도 50·50) · 「나」 개수
   · 삭제 CLI(거절·통지·유예·미리보기≠실행·재조회 중단·③~⑦ 실패 주입·경쟁·결과·인증·파기) · 옛 코드 호환(CRM_TEST_OLD_REF, 없으면 SKIP) · 처리방침 v5
   — 기존 절은 시험 배선 두 개로 돈다: 새 시험 사용자 표시 이름 자동(_autoname, prb-* 제외) · 신규 표준 시드는 SEED_ON() 안에서만
실행: server/.venv/bin/python server/test_tools.py   (어느 폴더에서든)
시험 사용자 tester@example.com · other@example.com 은 시작·끝에 지운다(cascade).
운영 DB(옛 스키마)에 붙어 새 코드를 시험할 땐 CRM_TEST_ALLOW_REMOTE=1 + CRM_TEST_SCHEMA=<이름> — 둘 중 하나라도 없으면
로컬 아닌 호스트에선 시작 전에 멈춘다. 그 이름의 임시 스키마를 만들어
최종형을 init_schema 로 깔고 그 안에서만 돈 뒤 끝에 지운다(운영 테이블은 안 건드린다). CI 는 빈 Postgres 라 안 쓴다.
"""
import asyncio
import hashlib
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

import activity  # noqa: E402
import auth  # noqa: E402
import contract  # noqa: E402
import db  # noqa: E402
import envnames  # noqa: E402
import imports  # noqa: E402
import main  # noqa: E402
import stack_service  # noqa: E402
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
PRB_TOOLS = {"display_name_set", "me_get", "person_list", "person_get", "person_save", "person_delete", "source_list", "source_get",
             "source_create", "source_reflection_list", "source_reflection_get", "source_reflection_save", "source_reflection_review",
             "source_reflection_delete",   # PR B 계획 §5 — 기존 19개는 그대로
             "record_provenance_set", "definition_archive", "definition_restore"}   # R5 결함(PR B 범위 추가) 3
# board 이전(25절) MCP 읽기 7 — 쓰기 없음(승인·제외는 웹에서만)
BI_TOOLS = {"activity_list", "activity_get", "person_context_get", "source_context_get", "me_aggregate_get", "inbox_list", "inbox_get"}
# 휴지통 PR-1(30절) MCP 4 — 목록(읽기) + 이동·복원·영구 삭제 요청(쓰기, 전역 크기 관문 면제)
TRASH_TOOLS = {"trash_list", "trash_move", "trash_restore", "trash_purge"}
TRASH_WRITES = TRASH_TOOLS - {"trash_list"}
BI = {k: f"bi-{k}@example.com" for k in ("a", "b", "agg", "adm", "del", "mig")}   # 25절: a 장치·수신 · b 남 · agg 집계 · adm 삭제 실행 · del 삭제 대상 · mig 이전
# 처리방침 머리글(`vN · 날짜`) — PR B 부터 v5. 날짜는 문서가 정본이라 파일에서 읽는다(시험이 날짜를 따로 들지 않는다)
PRIVACY_VERSION = "v7"   # board 이전(2026-10-20) v6 → PR ②-b(2026-10-22) v7
PRIVACY_HEAD = re.search(r"`(v\d+ · [^`]+)`", (HERE.parent / "guides" / "11-privacy.md").read_text(encoding="utf-8")).group(1)
# 머리글 꼴 = v7 · 2026-10-<두 자리>. 날짜 자리값 DD 는 릴리스 때 채운다(릴리스 체크 「처리방침 날짜 채움」) — 그 전에도 시험은 통과
assert re.fullmatch(r"v7 · 2026-10-(\d\d|DD)", PRIVACY_HEAD), PRIVACY_HEAD
# PR B(24절) 시험 사용자 — prb-* 는 표시 이름을 자동으로 채우지 않는다(관문 시험). 그 밖 시험 사용자는 아래 _autoname 이 채운다
PRB_EMAILS = tuple(f"prb-{k}@example.com" for k in ("a", "b", "noname", "web", "mcp", "del", "dela", "delb", "adm", "race", "old", "nav"))
TEST_EMAILS += PRB_EMAILS
TEST_EMAILS += tuple(BI.values())
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


# ── 표시 이름 관문(PR B) 시험 배선 ──
# 기존 절(1~23)은 이름 있는 사용자를 전제로 쓰기를 시험한다 — 새로 만들어진 시험 사용자에게 표시 이름을 한 번 채운다.
# 서버 코드는 이름을 자동으로 채우지 않는다(이 래퍼는 시험 프로세스 안에서만). prb-* 사용자는 건너뛴다(관문 시험이 직접 다룬다).
AUTONAME = "시험 사용자"
_orig_user_by_email, _orig_user_by_subject = db.user_by_email, db.user_by_subject


def _autoname(row):
    if row and row.get("display_name") is None and not str(row.get("email", "")).startswith("prb-"):
        with db.tx() as c:
            c.execute("UPDATE users SET display_name=%s WHERE id=%s AND display_name IS NULL AND status='active'", (AUTONAME, row["id"]))
        row = {**row, "display_name": AUTONAME}
    return row


db.user_by_email = lambda email: _autoname(_orig_user_by_email(email))
db.user_by_subject = lambda sub, email, iat=None: _autoname(_orig_user_by_subject(sub, email, iat))

# ── 신규 계정 표준 시드 배선(시험 전용) ──
# PR B 에서 기능 플래그를 지워 서버는 신규 계정을 늘 시드한다(_std_on_create)·seed_pending 을 늘 재시도한다(_std_retry).
# 1~16절은 「정의 0개로 시작하는 계정」(PR #20 전 기존 계정과 같은 출발점)을 전제로 쓴 시험이라, 그 절들에선 시험 프로세스가 시드를 건너뛴다.
# 시드를 보는 절(19~24)은 SEED_ON() 안에서 서버 그대로(원래 함수)를 돈다. 서버 코드에는 스위치가 없다.
_SEED = {"on": False}
_orig_std_on_create, _orig_std_retry = db._std_on_create, db._std_retry
db._std_on_create = lambda c, uid: _orig_std_on_create(c, uid) if _SEED["on"] else None
db._std_retry = lambda c, uid: _orig_std_retry(c, uid) if _SEED["on"] else None


@contextmanager
def SEED_ON(on=True):
    prev, _SEED["on"] = _SEED["on"], on
    try:
        yield
    finally:
        _SEED["on"] = prev


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
                             "제28조의8", PRIVACY_HEAD, 'href="/help"', 'href="/login"', "--accent: #186A4B"):
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
        legacy = base | new | {"help_get", "feedback_send", "ticket_list_mine", "ticket_get_mine"}   # 기존 15개 + help_get + feedback_send(desk-0) + 내 문의 읽기 2(P3)
        assert len(legacy) == 19 and legacy <= tools and tools - legacy == PRB_TOOLS | BI_TOOLS | TRASH_TOOLS, tools   # PR B 코어 도구는 24절, board 이전 읽기 7은 25절, 휴지통 4는 30절이 본다
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
                                            "ticket_list_mine", "ticket_get_mine",
                                            "me_get", "person_list", "person_get", "source_list", "source_get",   # PR B 읽기
                                            "source_reflection_list", "source_reflection_get", "trash_list"} | BI_TOOLS, names
    assert len(names) == 19 + len(PRB_TOOLS) + len(BI_TOOLS) + len(TRASH_TOOLS) \
        and db.QUOTA_EXEMPT == {"feedback_send"} | TRASH_WRITES <= set(db.WRITE_LIMITS), (len(names), db.QUOTA_EXEMPT)

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
            for f in ("start.html",):   # app.html 몫은 test_web_home.py B1(③-0.2)
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
    files = [*(f for f in HERE.glob("*.html") if f.name != "app.html"), *HERE.glob("*.py"), *adir.glob("*")]   # app.html 은 test_web_home.py B2
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
    """ua 계열 판정 · CRM_CONTACT 선택(비DEV 에서 없어도 기동)."""
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
    # app.html 문의 구간 esc 정적 검사는 test_web_home.py test_static_moved(A1)로 옮겼다(③-0.2)
    print("OK — 문의 단위(ua 계열·CRM_CONTACT 선택) 통과")


# ── 9) α2 — 동의 게이트 · 일일 한도 · usage_daily ─────────────

def _as_user(site, email):
    """MCP(DEV 사용자)와 웹(DEV 폴백)을 같은 사용자로."""
    os.environ["CRM_DEV_USER"] = email
    site.dev, site.dev_user = True, email


def _agreements(uid):
    with db.tx() as c:
        return c.execute("SELECT kind,version FROM agreements WHERE user_id=%s ORDER BY kind", (uid,)).fetchall()


def test_consent_versions():
    """버전 파싱 — 운영에서 게이트가 소리 없이 열리지 않게. 레포 guides 기준 privacy v5(PR B)."""
    import tempfile
    assert db.CONSENT_VERSIONS and db.CONSENT_VERSIONS.get("privacy") == PRIVACY_VERSION, db.CONSENT_VERSIONS
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
    · POST /api/consent 404 · 사용량은 그 uid 로 flush(로그인 전 요청만 '') · 코드에 CONSENT-REQUIRED·/consent 링크 0(app.html 은 test_web_home.py B3)."""
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
            assert set(u) == {"email", "struct_names", "desk_hidden", "display_name", "nav"}, u
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
        if f.name in ("test_tools.py", "test_web_home.py", "app.html"):   # 시험 파일 = 단언 문자열 자신 · app.html 은 test_web_home.py B3
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
    assert db.DAILY_LIMITS == {"records": 50, "candidates": 150, "decisions": 300, "proposals": 100, "questions": 50,
                               "people": 50, "reflections": 50}   # PR B ⓔ — 사람·출처 요약/반응은 24절이 본다
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
        # 날짜 경계(_KST_TODAY): 하나를 어제로 옮기면 오늘 카운트에서 빠진다. board 이전부터 한도는 COALESCE(received_at, created) —
        # created 만 과거로 옮기면 그대로 센다(이전한 기록의 과거 created 로 한도 우회 금지, r3 T2)
        _bulk("UPDATE records SET created = created - interval '1 day' WHERE user_id=%s AND id=%s", (u, r50["id"]))
        expect("RATE-LIMIT", db.record_add, u, "created 만 과거", "받은 시각은 오늘")
        _bulk("UPDATE records SET received_at = received_at - interval '1 day' WHERE user_id=%s AND id=%s", (u, r50["id"]))
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
                         ("web.py", "admin_db.py", "db.py", "auth.py", "main.py", "admin_cli.py", "start.html"))   # app.html 은 test_web_home.py B4
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
                  "deleted_at", "source_item_id", "source_object_id", "source_record_id", "field_key", "target_item_id", "target_record_id",
                  "target_candidate_id", "target_person_id", "target_source_id", "target_activity_id",   # 뒤 넷 = board 이전 대상 종류 판정(ID 열)
                  "activity_provider",   # board 이전 — 출처 수에서 활동 출처를 빼는 종류 열(값을 내보내지 않는다)
                  "trashed_at", "purged_at"}   # 휴지통 PR-1 — 무결성 집계에서 휴지통·사람 표식을 가르는 상태 열(값을 내보내지 않는다)
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
    assert db.CONSENT_VERSIONS.get("privacy") == PRIVACY_VERSION and db.OPTIONAL_VERSIONS == {"struct_names": "s1"}, (db.CONSENT_VERSIONS, db.OPTIONAL_VERSIONS)
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
        for word in (PRIVACY_HEAD, "구조 이름 공유", "동의하지 않아도 모든 기능을 쓸 수 있습니다",
                     "v2 시행 전에 모은 이용 기록은 v2 시행 때 지웠습니다", "메시지·내용은 모으지 않습니다", 'id="privacy-1"',
                     "처리 근거", "제15조 제1항 제4호", "제15조 제1항 제6호", "제15조 제1항 제1호",
                     "로그인 전 요청과 연결 장치 요청의 이용 기록은 사용자와 연결하지 않습니다",
                     "출처(종류·이름·위치)", "표준 편입", "작성자 표시만 검사", "계정 메뉴 「문의 보내기」",
                     "표시 이름", "사람", "출처 요약/반응", "삭제 요청", "1년"):   # v5(PR B)
            assert word in r.text, word
        basis = r.text[r.text.index("처리 근거"):r.text.index('id="privacy-2"')]
        assert "<table>" in basis and basis.count("<tr>") == 7, basis   # v5: 삭제 뒤 contact 1년(정당한 이익) 행 · v6: 연결 장치 행 · v7: 웹 대화 행 추가
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
        core8 = web.core_schema(web.SCHEMA_SQL.read_text(encoding="utf-8"))
        assert [x["table"] for x in core8] == ["questions", "records", "candidates", "tickets", "sources",
                                               "users", "people", "source_reflections", "activities"], "PR B 코어 8 + board 이전 활동"
        assert {f["name"] for f in core8[5]["fields"]} == {"display_name", "status", "nav_accepted_at", "created"}, "「나」 = users 의 네 열만"
        assert {f["name"] for f in core8[6]["fields"]} == {"id", "alias", "revision", "created", "updated", "hmac_namespace",
                                                           "identity_hmac", "trashed_at", "purge_at", "last_trashed_at", "purged_at"}, \
            "사람 = 별칭 이름표 + 장치 식별자(HMAC, board 이전) + 휴지통 상태(PR-1, 개인정보 열 아님)"
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
        only = [x.strip() for x in (envnames.env("TEST_ONLY") or "").split(",") if x.strip()]
        if only:   # 개발 중 절 하나만(CRM_TEST_ONLY=test_trash_core 등). CI·커밋 전 전체 시험은 이 env 없이
            for name in only:
                r = globals()[name]()
                if asyncio.iscoroutine(r):
                    await r
            return
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
        test_prb_schema()
        await test_prb_gate()
        await test_prb_core()
        await test_prb_delete()
        test_prb_oldcode()
        await test_prb_r5()
        await test_prb_app()
        await test_prb_privacy()
        test_bi_schema()
        test_bi_contract()
        await test_bi_imports()
        await test_bi_aggregate()
        test_bi_migration()
        await test_bi_delete()
        await test_pr1b_layout_stats()   # PR ①-b — 맨 끝(시작 때 tester·other 를 새로 만든다)
        await test_pr2a_jobs()   # PR ②-a — 맨 끝(시작 때 tester·other 를 새로 만든다)
        await test_pr2b_stack()   # PR ②-b 28절 — 맨 끝(시작 때 tester·other 를 새로 만든다)
        await test_serve_devices_status()   # ③-0.2 29절 — 맨 끝(시작 때 tester·other 를 새로 만든다)
        test_trash_schema()   # 휴지통 PR-1 30절 — 맨 끝(시작 때 tester·other 를 새로 만든다)
        test_trash_off()
        test_trash_core()
        test_trash_purge()
        await test_trash_web_mcp()
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
    · MCP state_get 에는 없음 · /app 응답 CSP 해시. 시험 사용자는 이 절에서 만들고 끝에 지운다(TEST_EMAILS 밖)."""
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
            assert set(u) == {"email", "struct_names", "desk_hidden", "display_name", "nav"}, "비관리자 user 키는 다섯(PR B 표시 이름·nav)"
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
        # app.html 정적(메뉴 항목·⚙·#who 말줄임)은 test_web_home.py test_static_moved(A2)로 옮겼다(③-0.2) — 여기는 응답 CSP 해시만
        async with client(U["user"]) as h:
            r = await h.get("/app")
            assert r.status_code == 200 and f"'sha256-{web.inline_script_hashes(r.text)[0]}'" in r.headers["content-security-policy"], "CSP 해시 = 보낸 본문"
    finally:
        site.dev, site.dev_user = real
        wipe()
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM users WHERE email = ANY(%s)", (emails,)).fetchone()["n"] == 0
    print("OK — /app 계정 메뉴(desk_hidden·/api/desk-visibility·state 두 키·MCP 없음·CSP 해시) 통과")


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
        if admin:   # PR B 결정 3: 표시 이름 관문은 관리자도 — 이름·새 탐색·내 구조 설정 메뉴(hidden 이어도 마크업엔 있다)
            menu = r.text[r.text.index('id="acct-menu"'):r.text.index("</details>")]
            assert re.findall(r"<(?:a|button)\b[^>]*>([^<]*)", menu) == ["운영 콘솔", "표시 이름 바꾸기", "새 탐색으로 바꾸기", "내 구조 설정",
                                                                      "저장 승인 대기", "연결 장치", "로그아웃"], menu   # PR① 저장 승인 대기 = 관리자도(연결 장치와 같은 구간 밖)
            for w in ('id="acct-name"', 'id="acct-nav"', 'id="acct-settings"', '<dialog id="name-dlg"', '<dialog id="nav-dlg"'):
                assert r.text.count(w) == 1, w
            assert '<dialog id="desk-dlg"' not in r.text
    try:
        assert not any(k in os.environ for k in R1_FLAGS), "두 플래그 미설정"
        # ── 로그인 — 웹(세션 쿠키) · DEV ──
        ck_a, ck_u = await login("r1a_sub"), await login("r1u_sub")
        assert site.unsign(ck_a)["uid"] == A and site.unsign(ck_u)["uid"] == U
        for ck, admin in ((ck_a, True), (ck_u, False)):
            q1(await req("GET", "/app", ck), admin)
            st = await req("GET", "/api/state", ck)
            assert st.status_code == 200 and (st.json()["user"].get("admin") is True) is admin, st.json()["user"]
            assert set(st.json()["user"]) == {"email", "struct_names", "desk_hidden", "display_name", "nav"} | ({"admin"} if admin else set()), "API 모양 그대로"
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
    # PR B — 표시 이름·사람 별칭·요약/반응 본문·id 는 운영자에게 동의와 무관하게 0(key 쪽)
    if user.get("display_name"):
        keys.add(user["display_name"])
    for x in user.get("people") or []:
        keys |= {x["id"], x["alias"]}
    for x in user.get("reflections") or []:
        keys |= {x["id"], x["body"]}
    return labels, keys


def test_r2_contract():
    """플래그 제거(PR B): 코드·render.yaml 에 CRM_FEATURE_ 참조 0 · 플래그 함수·FEATURE-OFF 없음
    · 예제: server/contract/*.json == contract.respond(EXAMPLES) 직렬화(full·partial·named × 3, 그 밖 파일 없음)
    · PR B named 예제: 표시 이름·별칭·요약/반응 본문·id 는 본인 응답에만(운영자 동의·비동의 0)
    · 누출: 운영자 비동의 응답 문자열에 사용자 label·key·선택지(값·label)·출처 name·location·id 0, 동의 응답에도 key·선택지 값·출처·id 0
      · 운영자 응답의 dict 키 = 허용 목록 · 본인 응답엔 전부 있음
    · 파생: 편입된 기준에 사용자가 더한 필드·참조 필드 = custom·null·false · 관계 = 참조 필드 값 그대로 · N:1/N:N · template · core 노드
      · 미편입 같은 key 오브젝트엔 표준명·std_id 없음 · std 모양(표식 없음 = version·state null, objects 는 선택 그대로) · 표준명 = templates/std@1.json · 오류 코드 3종 문구"""
    # ── 기능 플래그 제거(PR B §9) — 코드·render.yaml 에 CRM_FEATURE_ 참조 0, 플래그 함수·FEATURE-OFF 경로 없음 ──
    for name in ("FEATURES", "feature_on", "standard_on", "sources_on", "FEATURE_OFF", "FEATURE_OFF_MSG", "FEATURE_OFF_ACT"):
        assert not hasattr(contract, name), name
    for name in ("STD_CHOICE_NEEDS_FLAG", "_feature_off"):
        assert not hasattr(db, name), name
    for f in sorted(HERE.glob("*.py")) + [HERE / "render.yaml"]:
        if f.name not in ("test_tools.py", "test_web_home.py"):   # 시험 파일 = 단언 문자열 자신(app.html FEATURE-OFF 0 은 test_web_home.py A5)
            assert not re.search(r"CRM_FEATURE_|FEATURE_(STANDARD|SOURCES)|FEATURE-OFF|feature_on|standard_on|sources_on",
                                 f.read_text(encoding="utf-8")), f.name
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
    · 선택 기록(env 무관 — PR B 플래그 제거, 기록·덮어쓰기·def_id/def_revision) · 편입된 것 보류·거절 → STANDARD-LOCKED
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

        # PR B: 플래그 제거 — env(미설정·off·잘못된 값·on)와 무관하게 기록된다(FEATURE-OFF 없음)
        for v in (None, "off", "ON", "on"):
            os.environ.pop("CRM_FEATURE_STANDARD", None)
            if v is not None:
                os.environ["CRM_FEATURE_STANDARD"] = v
            try:
                assert db.std_choose(U["none"], "axes", "deferred")["state"] == "deferred", v
            finally:
                os.environ.pop("CRM_FEATURE_STANDARD", None)
            sql("DELETE FROM std_choices WHERE user_id=%s", (U["none"],))
        assert choices(U["match"]) == {}
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
        assert "CRM_FEATURE_STANDARD" not in os.environ   # PR B: env 없이 바로 기록(FEATURE-OFF 없음)
        ok = await req("POST", "/api/std/choose", U["none"], json={"std_key": "axes", "state": "deferred", "user_id": U["match"]})
        assert ok.status_code == 200 and ok.json()["std"]["objects"]["axes"] == "deferred", ok.text
        lk = await req("POST", "/api/std/choose", U["match"], json={"std_key": "axes", "state": "refused"})
        assert lk.status_code == 400 and lk.json()["code"] == "STANDARD-LOCKED" and "R3LBL" not in lk.text
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
        os.environ.pop("CRM_FEATURE_STANDARD", None)
        if saved is not None:
            os.environ["CRM_FEATURE_STANDARD"] = saved
    print("OK — R3 표준 미리보기(누락·일치·확장·충돌 사유·참조 의존·초안·보관 · 쓰기 0 READ ONLY · 플래그 무관) · std_choices 보류·거절"
          "(env 무관 기록·덮어쓰기·편입 잠금·무관한 쓰기 안 막음) · 본인만 · /api/state std · template·std_id 출처 · init_schema 멱등 통과")


# ── 20) Q2 — 신규 시드 · 편입 적용 · 표준 잠금 ─────────
Q2 = {k: f"q2{k}@example.com" for k in ("off", "seed", "dev", "fail", "stop", "race", "old", "inc", "dep", "conf", "lock", "opt", "web")}
# off = 플래그 off 신규 · seed = 신규 시드 · dev = user_by_email 신규 · fail = 시드 실패 → 재시도 · stop = 정의 생긴 뒤 재시도 중단
# race = 동시 재시도 · old = 기존 사용자(표식 없음) · inc = 편입(누락·일치·확장·재검증) · dep = 참조 대상 상태 바뀜 · conf = 구조 충돌
# lock = 잠금 표(시드 사용자) · opt = 표준 select 선택지(명세를 시험에서만 바꿔 끼움) · web = POST /api/std/choose 편입


@contextmanager
def _q2_flag(on):
    """PR B: 서버엔 플래그가 없다 — 시험 시드 배선(SEED_ON)만 켜고 끈다. 옛 env 는 일부러 반대 값으로 둬 결과와 무관함을 보인다."""
    prev = os.environ.pop("CRM_FEATURE_STANDARD", None)
    os.environ["CRM_FEATURE_STANDARD"] = "off" if on else "on"
    try:
        with SEED_ON(on):
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
        with _q2_flag(True):   # PR B: env CRM_FEATURE_STANDARD=off 여도 신규 계정은 시드(플래그 제거)
            assert os.environ["CRM_FEATURE_STANDARD"] == "off"
            U["off"] = db.user_by_subject(_q2_sub("off"), Q2["off"])["id"]
            assert _q2_rows(U["off"])["marks"] == [{"version": "std@1", "state": "seeded"}], "env off 무관 — 시드"
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

        # ── 웹 POST /api/std/choose 편입 — 본문 검사 400 · 200 · 다시 보내면 CONFLICT · 본문 user_id 무시 ──
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
            with _q2_flag(True):
                inc_before = _q2_rows(U["inc"])
                r = await post(ok_body)
                assert r.status_code == 200 and r.json()["std"]["objects"]["axes"] == "incorporated", r.text
                r = await post(ok_body)
                assert r.status_code == 400 and r.json()["code"] == "CONFLICT" and "Q2WLBL" not in r.text, r.text
                assert _q2_rows(U["inc"]) == inc_before, "본문 user_id 는 무시 — 남의 정의를 못 건드린다"
            assert (await post(ok_body)).json()["code"] == "CONFLICT", "env 를 지워도 같은 결과(이미 편입)"
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
    """PR B: 출처 연결은 상시 — 옛 env 를 반대 값으로 둬도 결과가 같음을 보인다(on 인자는 옛 호출 모양 유지용)."""
    prev = os.environ.pop("CRM_FEATURE_SOURCES", None)
    os.environ["CRM_FEATURE_SOURCES"] = "off" if on else "on"
    try:
        yield
    finally:
        os.environ.pop("CRM_FEATURE_SOURCES", None)
        if prev is not None:
            os.environ["CRM_FEATURE_SOURCES"] = prev


def _bi_drop_new(c):
    """옛 DB 흉내 — board 이전(25절)이 더한 표 5개와 item_links 의 새 대상 FK 를 지운다(새 열·CHECK 는 남긴다 — 옛 표를 지우는 시험용)."""
    for fk in ("fk_item_links_person", "fk_item_links_source", "fk_item_links_activity"):
        c.execute(f"ALTER TABLE item_links DROP CONSTRAINT IF EXISTS {fk}")
    c.execute("DROP TABLE IF EXISTS import_entries, activity_links, activities, import_counters, import_streams")


def _q3_role(uid):
    with db.tx() as c:
        r = c.execute("SELECT object_id, at FROM user_roles WHERE user_id=%s AND role='classify'", (uid,)).fetchone()
    return dict(r) if r else None


async def test_q3():
    """출처: env 무관 찾거나 만들기(PR B 플래그 제거)·kind 기본·검사 · 다른 사용자 출처 연결은 FK 거절 · 옛 기록 미확인
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
        assert db.sources(A) == []
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
        assert db.source_counts(A) == {"by_source": {r1["source_id"]: 2, r3["source_id"]: 1, r4["source_id"]: 1}, "unconfirmed": 1}
        try:   # 다른 사용자 출처를 가리킬 수 없다(복합 FK)
            with db.tx() as c:
                c.execute("UPDATE records SET source_id=%s WHERE user_id=%s AND id=%s", (r1["source_id"], B, rb["id"]))
            raise AssertionError("다른 사용자 출처에 연결됐다")
        except psycopg.errors.ForeignKeyViolation:
            pass
        assert db.sources(A) == srcs, "env 와 무관하게 출처는 읽힌다"
        assert next(j for j in db.records(A) if j["id"] == old["id"])["source_id"] is None, "옛 기록 = 출처 미확인(NULL)"
        st = await get_state(A)
        assert st["sources"] == srcs and st["source_counts"] == db.source_counts(A)
        assert {j["id"]: j["source_id"] for j in st["journal"]}[r2["id"]] == r1["source_id"]
        stb = await get_state(B)
        assert [s["id"] for s in stb["sources"]] == [rb["source_id"]] and not any(s["id"] in json.dumps(stb) for s in srcs), "본인 출처만"
        assert "sources" not in db.state(A) and "source_counts" not in db.state(A), "MCP state_get 엔 목록 없음(본인 웹만)"
        # MCP record_add 출처 인자 — PR B: env 와 무관하게 늘 연결(note 없음)
        prev_dev = os.environ.get("CRM_DEV_USER")
        os.environ["CRM_DEV_USER"] = Q3["mcp"]
        try:
            async with Client(main.mcp) as c:
                a = {"text": "MCP 로 남긴 글.", "source_name": "회의록 Q3MCP", "source_kind": "file", "source_location": "notes/m.md"}
                r0 = val(await c.call_tool("record_add", a))
                assert r0["source_linked"] is True and r0["source_id"] and "note" not in r0, r0
                assert set(val(await c.call_tool("record_add", {"text": "출처 인자 없음."}))) == {"id", "title", "created"}
                with _q3_sources(True):
                    r = val(await c.call_tool("record_add", a))
                    assert r["source_linked"] is True and r["source_id"] == r0["source_id"] and "note" not in r, r
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
                _bi_drop_new(c)   # board 이전 표·FK 도 옛 DB 엔 없다(출처·사람을 가리킨다)
                c.execute("DROP TABLE source_reflections, sources, user_roles")   # PR B 요약/반응도 옛 DB 엔 없다
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
            assert 'aria-label="오브젝트 1 · 축 · 표준 · 표준 정의 보호"' in svg and "오브젝트 3.필드 b — 축 · N:1 · 커스텀" in svg, svg[:1500]
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
        assert f'aria-label="오브젝트 3 · {M["c"]}-L-사용자 · 커스텀"' in svg_of(t), "SVG 에도 동의자 label"
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
    """(app.html 정적은 test_web_home.py A3 로 옮겼다)
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
        # app.html 정적(구조 정의·편입 대화창·토큰·칩)은 test_web_home.py test_static_moved(A3)로 옮겼다(③-0.2)

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
    print("OK — Q4·Q5 사용자 화면(structure = contract.respond(self)·편입 전후 locked·MCP 없음·다른 사용자 이름 0"
          "·구조 그래프 structure_svg = render(structure)·script·on* 0·토큰 data-theme"
          "·출처 미확인·재검토·관리자 /app 대화창) 통과")


# ── 23) PR A 운영 Setup — 왼쪽 메뉴 · Quick Find · Setup Home · Object Manager(서비스 표준 조회) ─────────
PRA = {k: f"pra{k}@example.com" for k in ("a", "u", "m")}   # a 관리자 · u 일반 사용자 · m 금지 값 표식 사용자
TEST_EMAILS += tuple(PRA.values())
PRA_SVG_SHA = "d5b973693f88d324c0fc255191340b753cfbfaafa1dee68d952cdb4be2f18d1c"   # 사용자 DMO SVG(contract 예제 × 수신자) — PR ①-b(2026-10-22) 「내 글」 후 바이트(전: d33ed383… PR① · c005852b… PR A 전)
PRA_STD_SHA = "c08007a2db55f219d7f5b16b5aa518e2b2081f95e1a3adfee52240d1380d00bf"   # templates/std@1.json 동결
PRA_OBJECTS = ("core-question", "core-record", "core-candidate", "core-ticket", "core-source", "core-me", "core-person", "core-reflection",
               "core-activity", "std-axis", "std-decision", "std-principle")   # PR B: 코어 5 → 8(나·사람·출처 요약/반응) · board 이전 9(활동)
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
        # PR B 결정 11: 삭제 요청 최소 입력 안내 한 줄은 있다(실행은 CLI) — 삭제 실행 버튼·form·경로는 여전히 없다
        assert "요청 문구만 적어 주세요. 다른 정보는 넣지 마세요" in t and "admin_cli.py delete-notify" in t
        assert not re.search(r"<(form|button)[^>]*delete|/delete", t, re.I), "삭제 실행 버튼·form 없음"
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
        # ── Object Manager: 정확히 코어 8(PR B) + std@1 3, 템플릿·사용자 정의 혼입 없음 ──
        t = clean(await req("GET", "/admin/schema"), "/admin/schema")
        svg = t[t.index("<svg"):t.index("</svg>") + 6]
        assert [o["id"] for o in sd["objects"]] == list(PRA_OBJECTS) and [o["name"] for o in sd["objects"]] == ["질문", "기록", "후보", "문의", "출처", "나", "사람", "출처 요약/반응", "활동", "축", "결정", "기준"]
        assert sorted(set(re.findall(r'href="/admin/schema/objects/([\w-]+)"', t))) == sorted(PRA_OBJECTS)
        assert svg.count('r="42"') == 12 and svg.count("stroke-dasharray") == 3, "노드 12 · 정의상 잠금 = std@1 3"
        assert re.findall(r'<a href="(/admin/schema/objects/[\w-]+)" aria-label', svg) == [f"/admin/schema/objects/{o}" for o in PRA_OBJECTS], "노드 링크 = 상세"
        assert "tpl-" not in t and all(w not in _pra_visible(t) for w in ("숙제", "발행", "접근 방식", "std@2")), "템플릿 혼입 없음"
        me_t = clean(await req("GET", "/admin/schema/objects/core-me"), "/admin/schema/objects/core-me")
        assert all(f'<td class="api">{w}</td>' in me_t for w in ("display_name", "status", "nav_accepted_at", "created"))
        assert len(re.findall(r'<td class="api">', me_t)) == 4 and all(f'<td class="api">{w}</td>' not in me_t for w in (
            "email", "auth_subject", "role", "blocked_at", "deleted_at", "web_revoked_before", "mcp_revoked_before", "desk_hidden")), "「나」 = 네 열만"
        assert "이전 원작자 템플릿 · author@1" in t and 'href="/admin/schema/templates/author-1"' in t and "현재 표준" not in t.replace("현재 표준이 아니에요", "")
        assert "서비스 정의이며, 개별 계정에 설치된 구조와 다를 수 있습니다." in t and 'href="/admin/users"' in t
        assert f'href="/admin/users/' not in t, "Object Manager 엔 사용자 목록이 없다"
        # 관계 = 정의에서 확인된 것만(schema.sql FK · std@1 참조 필드). 그래프 선 8 · 그리지 않는 연결 2(분류 연결 · 문의 이력)
        names = service_schema.names_of(sd["objects"])
        drawn = [(names[r["from"]], r["name"], names[r["to"]]) for o in sd["objects"] for r in o["relations"] if r["graph"]]
        assert drawn == [("기록", "출처", "출처"), ("기록", "질문", "질문"), ("후보", "근거 기록", "기록"), ("후보", "질문", "질문"),
                         ("출처 요약/반응", "출처", "출처"), ("활동", "출처", "출처"), ("결정", "판단", "후보"),
                         ("기준", "근거 판단", "후보"), ("기준", "근거 결정", "결정"), ("기준", "관심 축", "축")], drawn
        assert svg.count("<line ") == 2 * len(drawn), "관계마다 선(+투명 넓은 선) 하나"
        assert [(r["name"], r["to_text"] or r["from_text"]) for o in sd["objects"] for r in o["relations"] if not r["graph"]] == [
            ("붙는 곳 · 관심 축", "항목 — 대상 오브젝트 모름(정의 미확인) · 분류 대상은 사용자별 역할로 정함"), ("변경 이력", "문의 변경 이력(오브젝트 아님)")]
        # 코어 표시명 = 기존 정본(web.CORE_LABEL · app.html LABELS — 화면 몫은 test_web_home.py A4). 출처는 두 정본에 없다(보고)
        assert [o["name"] for o in sd["objects"][:4]] == [web.CORE_LABEL[t_] for _, t_, _, _ in service_schema.CORE[:4]]
        # API 이름 = 코어 열·테이블 · std@1 key(Salesforce API Name 꼴) — 표시명과 함께
        assert [o["api"] for o in sd["objects"]] == ["questions", "records", "candidates", "tickets", "sources", "users", "people", "source_reflections",
                                                     "activities", "axes", "decision_events", "principles"]
        assert '<td class="api">records</td>' in t and '<td class="api">decision_events</td>' in t and '<th>API 이름</th>' in t
        assert t.count('<div class="tbl fit">') == 2 and '<div class="tbl">' not in t, "Object Manager 표는 칸 줄바꿈(가로 넘침 0)"
        for p_ in new_gets + [h for _, h, _, _, _ in web.ADMIN_MENU]:   # 모든 /admin 표는 가로 스크롤 래퍼(.tbl overflow-x:auto) 안 — 문서 넘침 0
            t_ = (await req("GET", p_)).text
            assert t_.count("<table") == len(re.findall(r'<div class="tbl(?: fit)?"><table>', t_)), (p_, "래퍼 밖 표")
        assert ".tbl{overflow-x:auto}" in web._ADMIN_CSS
        rk = {(names[r["from"]], r["name"]): (r["kind"], r["required"], r["cardinality"]) for o in sd["objects"] for r in o["relations"] if r["graph"]}
        assert rk[("후보", "근거 기록")] == ("M-D", True, "N:1") and rk[("후보", "질문")] == ("Lookup", False, "N:1") and rk[("기록", "출처")] == ("Lookup", False, "N:1")
        assert rk[("기준", "관심 축")] == (None, False, "N:N"), "std@1 관계 종류는 정의에 없다 → 모름"
        assert all(o["lock"]["state"] is None for o in sd["objects"][:9]) and all(o["lock"]["state"] is True for o in sd["objects"][9:])
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
        for name in ("full", "partial"):   # PR A 전부터 있던 예제 — PR B 의 named 는 24절·18절이 본다
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
          "Setup Home 3구역 · Support 안내 · 옛 URL·POST · Object Manager 11(코어 8)·선 9·잠금 3 · 정의 일치 · 결정성(반복·순서·재시작) · std@1·DMO 바이트 · "
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


# ── 24) PR B — 코어(나·사람·출처·출처 요약/반응) · 표시 이름 관문 · 새 탐색 수락 · 삭제 CLI · 플래그 제거 · 옛 코드 호환 ─────────
PRB = {k: f"prb-{k}@example.com" for k in ("a", "b", "noname", "web", "mcp", "del", "dela", "delb", "adm", "race", "old", "nav")}
# a·b = 사람·요약/반응 소유자 둘 · noname = 관문(MCP) · web = 관문(웹)·새 경로 · mcp = 이름 정한 뒤 MCP 쓰기 · del·dela·delb = 삭제 대상
# adm = 삭제 실행 관리자 · race = 삭제 경쟁 · old = 옛 코드 호환(임시 스키마 안) · nav = 새 탐색 수락
PRB_TABLES = ("people", "source_reflections", "deletion_contacts")
# MCP 도구별 「모양은 맞고 대상은 없는」 인자 — 관문은 도구 본문 전에 걸린다. 키 집합 = main 의 분류 상수(누락이면 시험 실패)
PRB_WRITE_ARGS = {
    "question_add": {"text": "질문"}, "record_add": {"text": "기록"},
    "candidates_propose": {"record_id": "r_x", "question_id": "", "candidates": [{"text": "a", "quote": "b"}]},
    "candidate_decide": {"candidate_id": "c_x", "action": "confirm"},
    "profile_set": {"key": "purpose", "value": "v", "status": "guessed"},
    "definition_propose": {"object_id": None, "expected_revision": 0, "definition_json": "{}"},
    "definition_apply": {"object_id": "od_x", "proposal_id": "p", "expected_revision": 0},
    "item_propose": {"object_id": "od_x", "schema_revision": 1, "item_id": None, "expected_revision": 0, "code": None, "title": "t",
                     "body": "b", "data_json": "{}", "links": []},
    "item_decide": {"item_id": "i_x", "expected_revision": 0, "proposal_id": None, "action": "confirm", "history_id": None},
    "record_attach": {"record_id": "r_x"},
    "person_save": {"alias": "별칭"}, "person_delete": {"person_id": "p_x", "expected_revision": 0},
    "source_create": {"kind": "url", "name": "n"},
    "source_reflection_save": {"source_id": "s_x", "kind": "summary", "body": "b"},
    "source_reflection_review": {"reflection_id": "sr_x", "expected_revision": 0, "reviewed": True},
    "source_reflection_delete": {"reflection_id": "sr_x", "expected_revision": 0},
    "record_provenance_set": {"record_id": "r_x", "author_kind": "external", "text_process": "verbatim", "reason": "남의 글"},
    "definition_archive": {"object_id": "od_x"}, "definition_restore": {"object_id": "od_x"},
    # 휴지통 PR-1(30절) — 이름 관문은 스위치·대상 검사보다 먼저
    "trash_move": {"kind": "record", "target_id": "r_x", "expected_revision": None},
    "trash_restore": {"kind": "record", "target_id": "r_x", "expected_revision": None},
    "trash_purge": {"kind": "record", "target_id": "r_x", "expected_revision": None},
}
PRB_ALLOWED_ARGS = {
    "state_get": {}, "question_list": {}, "candidate_list": {}, "record_get": {"record_id": "r_x"},
    "definitions_get": {"object_ids": None, "include_archived": False}, "help_get": {"topic": "words"},
    "feedback_send": {"kind": "question", "text": "이름 없이 보내는 문의"}, "ticket_list_mine": {}, "ticket_get_mine": {"ticket_id": "tk_x"},
    "me_get": {}, "person_list": {}, "person_get": {"person_id": "p_x"}, "source_list": {}, "source_get": {"source_id": "s_x"},
    "source_reflection_list": {}, "source_reflection_get": {"reflection_id": "sr_x"},
    # board 이전 읽기 7(25절) — 이름 없이도 된다
    "activity_list": {}, "activity_get": {"activity_id": "a_xxxxxx"}, "person_context_get": {"person_id": "p_x"},
    "source_context_get": {"source_id": "s_x"}, "me_aggregate_get": {}, "inbox_list": {}, "inbox_get": {"import_id": "ie_x"},
    "trash_list": {},   # 휴지통 PR-1 목록 — 이름 없이도(스위치 꺼짐이면 SWITCH-OFF)
    "display_name_set": {"display_name": "  이름 PRBNAME  "},   # 마지막에 부른다(관문 통과 뒤 쓰기 확인)
}


def _prb_err(res):
    """MCP 오류 결과의 코드([CODE]) 또는 None."""
    if not res.is_error:
        return None
    m = re.search(r"\[([A-Z][A-Z-]+)\]", res.content[0].text)
    return m.group(1) if m else "?"


def _prb_count(uid, t):
    with db.tx() as c:
        return c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s", (uid,)).fetchone()["n"]


def _prb_user_tables():
    """schema.sql 에서 user_id 열을 가진 테이블 — 삭제 목록 정적 대조용."""
    sql = (HERE / "schema.sql").read_text(encoding="utf-8")
    out = set()
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        if any(line.strip().startswith("user_id ") for line in m.group(2).splitlines()):
            out.add(m.group(1))
    return out


def _prb_shape(c, tables):
    """열·제약·인덱스 모양(스키마 이름은 지운다) — 옛 DB 마이그레이션 결과 = 새 DB 비교용."""
    sch = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    cols = c.execute("SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                     "WHERE table_schema=current_schema() AND table_name = ANY(%s) ORDER BY 1, 2", (list(tables),)).fetchall()
    cons = c.execute("SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
                     "WHERE connamespace = current_schema()::regnamespace AND conrelid::regclass::text = ANY(%s) ORDER BY 1, 2",
                     (list(tables),)).fetchall()
    idx = c.execute("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename = ANY(%s) "
                    "ORDER BY 1, 2", (list(tables),)).fetchall()
    return [dict(r) for r in cols], [dict(r) for r in cons], [{**dict(r), "indexdef": r["indexdef"].replace(sch + ".", "")} for r in idx]


def test_prb_schema():
    """init_schema: 옛 DB(PR B 열·테이블 없음, 옛 CHECK) → 2회 멱등 · 새 DB 와 같은 모양 · 기존 행·값 불변 · 옛 모양 users INSERT 성공
    · CHECK 확장 뒤 기존 값 통과·새 값 허용."""
    tables = ("users", "admin_audit", "ticket_history") + PRB_TABLES
    fresh, old = f"prbf_{secrets.token_hex(3)}", f"prbo_{secrets.token_hex(3)}"
    create_schema(fresh)
    create_schema(old)
    try:
        with schema_conn(fresh) as c:
            db.init_schema(c)
            c.commit()
            want = _prb_shape(c, tables)
        with schema_conn(old) as c:
            db.init_schema(c)
            c.execute("ALTER TABLE users DROP COLUMN display_name, DROP COLUMN status, DROP COLUMN deleted_at, DROP COLUMN nav_accepted_at")
            _bi_drop_new(c)   # board 이전 표·FK 는 사람을 가리킨다 — 옛 DB 엔 없다
            c.execute("DROP TABLE source_reflections, people, deletion_contacts")
            c.execute("ALTER TABLE admin_audit DROP CONSTRAINT admin_audit_action_check")
            c.execute("ALTER TABLE admin_audit ADD CONSTRAINT admin_audit_action_check CHECK (action IN "
                      "('bootstrap','recover','role_grant','role_revoke','web_signout','block','unblock'))")
            c.execute("ALTER TABLE ticket_history DROP CONSTRAINT ticket_history_kind_check")
            c.execute("ALTER TABLE ticket_history ADD CONSTRAINT ticket_history_kind_check CHECK (kind IN ('status','reply','note','reclassify'))")
            c.execute("INSERT INTO users (id,email,role,blocked_at) VALUES ('u_o1','o1@x.test','admin',NULL), ('u_o2','o2@x.test','user',now())")
            c.execute("INSERT INTO admin_audit (action,actor_id,target_id) VALUES ('block','u_o1','u_o2')")
            c.execute("INSERT INTO tickets (id,user_id,kind,text,via) VALUES ('tk_o','u_o2','privacy','글','web')")
            c.execute("INSERT INTO ticket_history (ticket_id,visibility,kind) VALUES ('tk_o','internal','note')")
            c.commit()
            data = lambda: (c.execute("SELECT id,email,role,blocked_at,created FROM users ORDER BY id").fetchall(),   # noqa: E731
                            c.execute("SELECT action,actor_id,target_id FROM admin_audit ORDER BY id").fetchall(),
                            c.execute("SELECT kind,visibility FROM ticket_history ORDER BY id").fetchall())
            before = data()
            db.init_schema(c)
            c.commit()
            first = _prb_shape(c, tables)
            db.init_schema(c)
            c.commit()
            assert _prb_shape(c, tables) == first == want, "옛 DB 2회 = 새 DB 모양(열·이름 붙은 CHECK·인덱스)"
            assert data() == before, "기존 행·값 불변"
            assert [tuple(r.values()) for r in c.execute("SELECT id,status,display_name,deleted_at,nav_accepted_at FROM users ORDER BY id")] == [
                ("u_o1", "active", None, None, None), ("u_o2", "active", None, None, None)], "옛 행 = 기본값(active·이름 없음·미수락)"
            c.execute("INSERT INTO users (id,email) VALUES ('u_o3','o3@x.test')")   # 옛 코드 모양 INSERT
            c.execute("INSERT INTO admin_audit (action) VALUES ('delete_run'), ('unblock')")
            c.execute("INSERT INTO ticket_history (ticket_id,visibility,kind) VALUES ('tk_o','public','deleted')")
            c.commit()
            for bad in ("UPDATE users SET status='gone' WHERE id='u_o3'", "UPDATE users SET status='deleted' WHERE id='u_o3'",
                        "UPDATE users SET display_name=' x' WHERE id='u_o3'", "UPDATE users SET display_name='' WHERE id='u_o3'",
                        "INSERT INTO admin_audit (action) VALUES ('purge')"):
                try:
                    c.execute(bad)
                    raise AssertionError(f"CHECK 가 막지 않았다: {bad}")
                except psycopg.errors.CheckViolation:
                    c.rollback()
    finally:
        drop_schema(fresh)
        drop_schema(old)
    print("OK — 24a PR B 스키마(옛 DB 2회 멱등 = 새 DB 모양 · 기존 값 불변 · 옛 INSERT 기본값 · CHECK 확장·이름 붙은 CHECK) 통과")


async def test_prb_gate():
    """표시 이름 관문 — 입력 규칙 · 「나」 = users.display_name 한 곳 · MCP 쓰기 16종 NAME-REQUIRED · 허용 17종 통과 · 분류 누락 0
    · 웹 쓰기 403 · 예외 경로(이름·문의·선택 동의·문의 숨김·로그아웃) · 새 경로 401·Origin 403·400."""
    site = main.site
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    prev_dev = os.environ.get("CRM_DEV_USER")
    try:
        # ── 분류 상수 = 등록된 도구 ──
        async with Client(main.mcp) as c:
            names = {t.name for t in (await c.list_tools()).tools}
        assert main.WRITE_TOOLS | main.ALLOWED_TOOLS == names and not main.WRITE_TOOLS & main.ALLOWED_TOOLS, names ^ (main.WRITE_TOOLS | main.ALLOWED_TOOLS)
        assert main.WRITE_TOOLS == set(db.WRITE_LIMITS) - db.NAME_EXEMPT and db.NAME_EXEMPT == {"feedback_send", "display_name_set"}
        assert set(PRB_WRITE_ARGS) == main.WRITE_TOOLS and set(PRB_ALLOWED_ARGS) == main.ALLOWED_TOOLS, "시험 인자 표 = 분류"
        assert names - PRB_TOOLS - BI_TOOLS - TRASH_TOOLS == {"state_get", "question_add", "question_list", "record_add", "record_get", "candidates_propose",
                                     "candidate_decide", "candidate_list", "profile_set", "definitions_get", "definition_propose",
                                     "definition_apply", "item_propose", "item_decide", "record_attach", "help_get", "feedback_send",
                                     "ticket_list_mine", "ticket_get_mine"}, "기존 19개 이름 그대로"
        # ── 입력 규칙 ──
        N = db.user_by_email(PRB["noname"])["id"]
        assert db.user_get(N) and db.me_home(N)["display_name"] is None and db.me_home(N)["nav"] == "new", "신규 = 이름 없음 · 새 탐색"
        for bad in ("", "   ", "가" * 81, "줄\n바꿈", "탭\t문자", "널\x00문자", "단\u2029락", 7, None):
            expect("ARG-INVALID", db.display_name_set, N, bad)
        assert db.me_home(N)["display_name"] is None, "거절은 저장하지 않는다"
        assert db.display_name_set(N, " " + "가" * 80 + " ")["display_name"] == "가" * 80, "앞뒤 공백 제거 뒤 80자"
        _sql("UPDATE users SET display_name=NULL WHERE id=%s", (N,))
        # ── MCP: 이름 없음 → 쓰기 16종 NAME-REQUIRED · 허용 17종 통과 ──
        os.environ["CRM_DEV_USER"] = PRB["noname"]
        async with Client(main.mcp) as c:
            for t, a in PRB_WRITE_ARGS.items():
                res = await c.call_tool(t, a)
                assert _prb_err(res) == "NAME-REQUIRED" and "대시보드에서 표시 이름을 정해 주세요" in res.content[0].text, (t, res.content[0].text)
            assert _prb_count(N, "questions") == _prb_count(N, "records") == _prb_count(N, "people") == 0, "거절 = 쓰기 0"
            for t, a in PRB_ALLOWED_ARGS.items():
                res = await c.call_tool(t, a)
                assert _prb_err(res) != "NAME-REQUIRED", (t, res.content[0].text if res.is_error else "")
            assert db.me_home(N)["display_name"] == "이름 PRBNAME", "display_name_set = 앞뒤 공백 제거"
            q = val(await c.call_tool("question_add", {"text": "이제 된다"}))
            assert q["id"].startswith("q_")
            me = val(await c.call_tool("me_get", {}))
            assert me == db.me_home(N) and me["display_name"] == "이름 PRBNAME" and set(me["counts"]) == {
                "claims", "principles", "decisions", "people", "sources", "reflections"}
            st = val(await c.call_tool("state_get", {}))
            assert set(st) == {"generated", "profile", "questions", "tabs", "candidates", "journal", "next", "definitions", "items",
                               "item_proposals", "record_links"}, "MCP state_get 모양 불변(이름·사람·요약/반응 없음)"
            assert "PRBNAME" not in json.dumps(st, ensure_ascii=False)
        # ── 웹: 이름 없음 → 쓰기 403 · 예외 경로 통과 ──
        W = db.user_by_email(PRB["web"])["id"]

        async def req(method, path, uid=W, origin=True, **kw):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                if uid:
                    h.cookies.set("crm_session", site.session_value(uid))
                return await h.request(method, path, headers={"origin": PUBLIC} if origin else {}, **kw)
        st = (await req("GET", "/api/state")).json()
        assert st["user"]["display_name"] is None and st["user"]["nav"] == "new" and st["people"] == [] and st["reflections"] == []
        assert st["me"] == {"counts": db.me_home(W)["counts"]} and "display_name" not in st["structure"], "구조 응답엔 PR B 키 없음"
        writes = [("/api/candidate/decide", {"candidate_id": "c_x", "action": "confirm"}),
                  ("/api/item/decide", {"item_id": "i_x", "expected_revision": 0, "action": "confirm"}),
                  ("/api/std/choose", {"std_key": "axes", "state": "deferred"}), ("/api/nav/accept", {}),
                  ("/api/people", {"alias": "a"}), ("/api/people/p_x", {"alias": "a", "expected_revision": 0}),
                  ("/api/people/p_x/delete", {"expected_revision": 0}), ("/api/sources", {"kind": "url", "name": "n"}),
                  ("/api/reflections", {"source_id": "s_x", "kind": "summary", "body": "b"}),
                  ("/api/reflections/sr_x", {"source_id": "s_x", "kind": "summary", "body": "b", "expected_revision": 0}),
                  ("/api/reflections/sr_x/review", {"reviewed": True, "expected_revision": 0}),
                  ("/api/reflections/sr_x/delete", {"expected_revision": 0})]
        for path, body in writes:
            r = await req("POST", path, json=body)
            assert r.status_code == 403 and r.json()["code"] == "NAME-REQUIRED", (path, r.status_code, r.text)
        assert db.me_home(W)["nav"] == "new" and _prb_count(W, "people") == 0
        for path, body in writes[3:]:   # PR B 새 경로 — 401 · Origin 403 · 본문 400
            assert (await req("POST", path, uid=None, json=body)).status_code == 401, path
            r = await req("POST", path, origin=False, json=body)
            assert r.status_code == 403 and r.json()["code"] == "ORIGIN", path
            if body:
                r = await req("POST", path, json=[])
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", path
        assert (await req("POST", "/api/people", json={"alias": 3})).status_code == 400
        assert (await req("POST", "/api/reflections/sr_x/review", json={"reviewed": 1, "expected_revision": 0})).status_code == 400
        assert (await req("POST", "/api/people/p_x", json={"alias": "a", "expected_revision": True})).status_code == 400
        for path, body in (("/api/feedback", {"kind": "question", "text": "이름 없이 문의"}),
                           ("/api/optional-consent", {"kind": "struct_names", "on": False}), ("/api/desk-visibility", {"hidden": False})):
            r = await req("POST", path, json=body)
            assert r.status_code == 200 or (path == "/api/feedback" and r.json()["code"] == "RATE-LIMIT"), (path, r.text)   # 전역 문의 한도는 앞 절이 채운다
        r = await req("POST", "/api/me/name", json={"display_name": "줄\n바꿈"})
        assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
        r = await req("POST", "/api/me/name", json={"display_name": " 웹 이름 PRBWEB "})
        assert r.status_code == 200 and r.json() == {"display_name": "웹 이름 PRBWEB"}, r.text
        st = (await req("GET", "/api/state")).json()
        assert st["user"]["display_name"] == "웹 이름 PRBWEB" == db.me_home(W)["display_name"], "「나」 = users.display_name 한 곳"
        r = await req("POST", "/api/people", json={"alias": "이제 됨"})
        assert r.status_code == 200 and r.json()["alias"] == "이제 됨"
        expect("ARG-INVALID", db.display_name_set, W, "")   # 빈 값으로 바꾸기 거절
        r = await req("POST", "/logout")
        assert r.status_code == 200
    finally:
        site.dev, site.dev_user = real
        os.environ["CRM_DEV_USER"] = prev_dev
    print("OK — 24b 표시 이름 관문(입력 규칙 · 「나」 한 곳 · MCP 쓰기 16 NAME-REQUIRED·허용 17 통과·분류 누락 0·기존 19 이름 · state_get 모양 불변"
          " · 웹 쓰기 403·예외 경로·새 경로 401·403·400) 통과")


async def test_prb_core():
    """새 탐색 수락 · 사람 · 출처 · 출처 요약/반응 · 인용 차단 · 「나」 홈 개수 · 일일 한도 · 웹 경로."""
    site = main.site
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    try:
        A, B = (db.user_by_email(PRB[k])["id"] for k in ("a", "b"))
        for u in (A, B):
            db.display_name_set(u, "이름")
        # ── 새 탐색 수락: 기존 계정(NULL) = old · 수락 멱등 · 표준 상태 불변 ──
        V = db.user_by_email(PRB["nav"])["id"]
        db.display_name_set(V, "탐색")
        _sql("UPDATE users SET nav_accepted_at=NULL WHERE id=%s", (V,))
        assert db.me_home(V)["nav"] == "old"
        std0 = db.std_status(V)
        a1 = db.nav_accept(V)
        a2 = db.nav_accept(V)
        assert a1 == a2 and a1["nav"] == "new" and db.me_home(V)["nav"] == "new", "멱등 — 처음 시각 그대로"
        assert db.std_status(V) == std0, "편입 상태를 읽지도 바꾸지도 않는다"
        # ── 사람 ──
        p = db.person_save(A, "  동료 PRBALIAS  ")
        assert set(p) == {"id", "alias", "revision", "created", "updated"} and p["alias"] == "동료 PRBALIAS" and p["revision"] == 0
        with db.tx() as c:
            assert {r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                                                        "AND table_name='people'").fetchall()} == {"user_id", "id", "alias", "revision", "created", "updated",
                                                                                                    "hmac_namespace", "identity_hmac",   # board 이전(실명·URL·소속 열 없음)
                                                                                                    "trashed_at", "purge_at", "last_trashed_at", "purged_at"}   # 휴지통 PR-1 상태 열
        e1 = expect("NOT-FOUND", db.person_get, B, p["id"])
        e2 = expect("NOT-FOUND", db.person_get, B, "p_none")
        assert (e1.message, e1.action) == (e2.message, e2.action), "남의 id = 없는 id"
        expect("NOT-FOUND", db.person_save, B, "x", p["id"], 0)
        expect("NOT-FOUND", db.person_delete, B, p["id"], 0)
        expect("ARG-INVALID", db.person_save, A, "x", "", 0)   # 생성엔 expected_revision 없음
        expect("ARG-INVALID", db.person_save, A, "x", p["id"], None)   # 수정엔 필수
        expect("ARG-INVALID", db.person_save, A, "줄\n바꿈")
        expect("CONFLICT", db.person_save, A, "새 별칭", p["id"], 5)
        assert db.person_save(A, "동료 PRBALIAS", p["id"], 0)["revision"] == 0, "같은 값 = 무동작"
        p2 = db.person_save(A, "선배", p["id"], 0)
        assert (p2["alias"], p2["revision"]) == ("선배", 1)
        expect("CONFLICT", db.person_delete, A, p["id"], 0)
        dup = db.person_save(A, "선배")
        assert dup["id"] != p["id"], "별칭 중복 허용"
        for i in range(3):
            db.person_save(A, f"사람{i}")
        pg1 = db.person_list(A, limit=2)
        pg2 = db.person_list(A, cursor=pg1["next_cursor"], limit=2)
        pg3 = db.person_list(A, cursor=pg2["next_cursor"], limit=2)
        ids = [x["id"] for x in pg1["people"] + pg2["people"] + pg3["people"]]
        assert len(ids) == len(set(ids)) == 5 and pg3["next_cursor"] == "" and ids == [x["id"] for x in db.people_all(A)]
        assert [x["alias"] for x in db.person_list(A, query="사람")["people"]] == ["사람2", "사람1", "사람0"]
        assert db.person_list(A, query="%")["people"] == [], "LIKE 특수문자는 글자 그대로"
        for bad in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"cursor": "!!"}):
            expect("ARG-INVALID", db.person_list, A, **bad)
        assert db.person_delete(A, dup["id"], 0)["deleted"] and len(db.people_all(A)) == 4
        assert db.person_list(B)["people"] == [], "본인 것만"
        # ── 출처 · 요약/반응 ──
        s = db.source_create(A, "url", " 읽은 글 PRBSRC ", "https://prb.example/a")
        assert set(s) == {"id", "kind", "name", "location"} and s["name"] == "읽은 글 PRBSRC"
        assert db.source_create(A, "url", "읽은 글 PRBSRC", "https://prb.example/a")["id"] == s["id"], "찾거나 만들기"
        rec = db.record_add(A, "t", "그 글을 읽고 쓴 내 기록.", source_ref={"kind": "url", "name": "읽은 글 PRBSRC", "location": "https://prb.example/a"})
        assert rec["source_id"] == s["id"], "record_add 와 같은 출처 규칙"
        sb = db.source_create(B, "url", "B 출처", "")
        expect("NOT-FOUND", db.source_get, B, s["id"])
        expect("NOT-FOUND", db.source_reflection_save, A, sb["id"], "summary", "남의 출처")
        expect("ARG-INVALID", db.source_reflection_save, A, s["id"], "insight", "유형")
        expect("ARG-INVALID", db.source_reflection_save, A, s["id"], "summary", "  ")
        expect("ARG-INVALID", db.source_reflection_save, A, s["id"], "summary", "가" * 8001)
        r = db.source_reflection_save(A, s["id"], "summary", "내 요약 PRBBODY")
        assert (r["review"], r["revision"], r["reviewed_revision"], r["reviewed_at"]) == ("pending", 0, None, None), r
        assert db.source_get(A, s["id"])["counts"] == {"records": 1, "summaries": 1, "reactions": 0}
        expect("NOT-FOUND", db.source_reflection_get, B, r["id"])
        expect("NOT-FOUND", db.source_reflection_review, B, r["id"], 0, True)
        expect("CONFLICT", db.source_reflection_review, A, r["id"], 3, True)
        rv = db.source_reflection_review(A, r["id"], 0, True)
        assert (rv["review"], rv["revision"], rv["reviewed_revision"]) == ("reviewed", 1, 1) and rv["reviewed_at"], rv
        assert db.source_reflection_review(A, r["id"], 1, True) == rv, "이미 완료 = 무동작"
        expect("CONFLICT", db.source_reflection_save, A, s["id"], "summary", "고침", r["id"], 0)   # stale revision
        ed = db.source_reflection_save(A, s["id"], "summary", "고친 요약", r["id"], 1)
        assert (ed["review"], ed["revision"], ed["reviewed_revision"], ed["reviewed_at"]) == ("pending", 2, None, None), "완료 뒤 수정 → 대기"
        same = db.source_reflection_save(A, s["id"], "summary", "고친 요약", r["id"], 2)
        assert same["revision"] == 2, "같은 값 = 무동작"
        rv = db.source_reflection_review(A, r["id"], 2, True)
        un = db.source_reflection_review(A, r["id"], rv["revision"], False)
        assert (un["review"], un["reviewed_revision"], un["reviewed_at"]) == ("pending", None, None), "완료 취소 허용"
        rr = db.source_reflection_save(A, s["id"], "reaction", "내 반응")
        lst = db.source_reflection_list(A, source_id=s["id"], kind="reaction")
        assert [x["id"] for x in lst["reflections"]] == [rr["id"]] and db.source_reflection_list(A, review="reviewed")["reflections"] == []
        expect("ARG-INVALID", db.source_reflection_list, A, kind="bogus")
        s2 = db.source_create(A, "other", "요약만 있는 출처")
        db.source_reflection_save(A, s2["id"], "reaction", "기록 없이 반응만")
        try:   # 연결된 출처 삭제 거절(FK NO ACTION) — 기록이 없고 요약/반응만 달린 출처
            _sql("DELETE FROM sources WHERE user_id=%s AND id=%s", (A, s2["id"]))
            raise AssertionError("요약/반응이 달린 출처가 지워졌다")
        except psycopg.errors.ForeignKeyViolation:
            pass
        # ── 인용 차단: sr_ id 는 후보 근거·record_attach·기준 근거·관계 대상 어디에도 못 들어간다 ──
        expect("RECORD-MISSING", db.candidates_propose, A, r["id"], "", [{"text": "t", "quote": "고친"}], "ai")
        expect("RECORD-MISSING", db.record_attach, A, r["id"], None, None, "x")
        expect("LINK-INVALID", db.record_attach, A, rec["id"], [{"field_key": "attach_to", "target_kind": "candidate", "target_id": r["id"]}])
        with SEED_ON():
            P = db.user_by_email(PRB["delb"])["id"]   # 표준 기준(principles) 이 있는 계정 — 근거 판단 필드 = candidate 참조
        db.display_name_set(P, "기준")
        sp = db.source_create(P, "url", "p 출처")
        rp = db.source_reflection_save(P, sp["id"], "summary", "기준 근거로 쓰면 안 되는 요약")
        od = next(d for d in db.definitions(P) if d["key"] == "principles")
        e = expect("LINK-INVALID", db.item_propose, P, od["id"], od["revision"], None, 0, None, "기준", "본문", {},
                   [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": rp["id"]}])
        assert rp["id"] in e.message
        # ── 「나」 홈 개수 ──
        h = db.me_home(A)["counts"]
        assert h == {"claims": 0, "principles": 0, "decisions": 0, "people": 4, "sources": 2, "reflections": 3}, h
        ev = next(d for d in db.definitions(P) if d["key"] == "decision_events")
        _sql("INSERT INTO items (user_id,id,object_id,schema_revision,revision,decision) VALUES (%s,'i_prb1',%s,1,1,NULL), "
             "(%s,'i_prb2',%s,1,1,'confirmed'), (%s,'i_prb3',%s,1,1,'pending')", (P, ev["id"], P, od["id"], P, od["id"]))
        pr = db.record_add(P, "t", "내가 쓴 원문이다.")
        cid = db.candidates_propose(P, pr["id"], "", [{"text": "판단", "quote": "원문이다"}], "self")[0][0]
        db.candidate_decide(P, cid, "confirm")
        assert db.me_home(P)["counts"] == {"claims": 1, "principles": 1, "decisions": 1, "people": 0, "sources": 1, "reflections": 1}
        # ── 일일 한도 50 (사람·요약/반응) — 생성만 센다 ──
        with db.tx() as c:
            c.execute("INSERT INTO people (user_id,id,alias) SELECT %s, 'p_bulk' || g, 'x' FROM generate_series(1, %s) g",
                      (B, db.DAILY_LIMITS["people"]))
            c.execute("INSERT INTO source_reflections (user_id,id,source_id,kind,body) SELECT %s, 'sr_bulk' || g, %s, 'summary', 'x' "
                      "FROM generate_series(1, %s) g", (B, sb["id"], db.DAILY_LIMITS["reflections"]))
        e = expect("RATE-LIMIT", db.person_save, B, "51번째")
        assert "사람은 50개" in e.message
        e = expect("RATE-LIMIT", db.source_reflection_save, B, sb["id"], "summary", "51번째")
        assert "출처 요약/반응은 50개" in e.message
        assert db.person_save(B, "고치기는 된다", "p_bulk1", 0)["revision"] == 1
        # ── 웹 경로 왕복 ──

        async def req(method, path, uid=A, **kw):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as hc:
                hc.cookies.set("crm_session", site.session_value(uid))
                return await hc.request(method, path, headers={"origin": PUBLIC}, **kw)
        r1 = (await req("POST", "/api/people", json={"alias": "웹 사람"})).json()
        r2 = await req("POST", f"/api/people/{r1['id']}", json={"alias": "웹 사람2", "expected_revision": 0})
        assert r2.status_code == 200 and r2.json()["revision"] == 1
        assert (await req("POST", f"/api/people/{r1['id']}", json={"alias": "x", "expected_revision": 0})).json()["code"] == "CONFLICT"
        nf = await req("POST", f"/api/people/{r1['id']}/delete", uid=B, json={"expected_revision": 1})
        assert nf.status_code == 404 and nf.json()["code"] == "NOT-FOUND", "남의 사람 = 404"
        assert (await req("POST", f"/api/people/{r1['id']}/delete", json={"expected_revision": 1})).json()["deleted"] is True
        ws = (await req("POST", "/api/sources", json={"kind": "file", "name": "웹 출처"})).json()
        wr = (await req("POST", "/api/reflections", json={"source_id": ws["id"], "kind": "reaction", "body": "웹 반응"})).json()
        assert wr["review"] == "pending"
        wv = (await req("POST", f"/api/reflections/{wr['id']}/review", json={"reviewed": True, "expected_revision": 0})).json()
        assert wv["review"] == "reviewed"
        we = (await req("POST", f"/api/reflections/{wr['id']}", json={"source_id": ws["id"], "kind": "reaction", "body": "웹 반응 고침",
                                                                    "expected_revision": wv["revision"]})).json()
        assert we["review"] == "pending"
        st = (await req("GET", "/api/state")).json()
        assert {x["id"] for x in st["people"]} == {x["id"] for x in db.people_all(A)} and {x["id"] for x in st["reflections"]} == {
            x["id"] for x in db.reflections_all(A)}
        sb_state = json.dumps((await req("GET", "/api/state", uid=B)).json(), ensure_ascii=False)
        assert st["me"]["counts"] == db.me_home(A)["counts"]
        assert not any(x["id"] in sb_state or x["alias"] in sb_state for x in db.people_all(A)), "남의 사람 0"
        assert not any(x["id"] in sb_state for x in db.reflections_all(A)), "남의 요약/반응 0"
        assert (await req("POST", f"/api/reflections/{wr['id']}/delete", json={"expected_revision": we["revision"]})).json()["deleted"]
        assert "people" not in db.state(A) and "reflections" not in db.state(A), "MCP state_get 엔 없음"
    finally:
        site.dev, site.dev_user = real
    print("OK — 24c 코어(새 탐색 수락 멱등·편입 불변 · 사람 별칭만·남의 id=없는 id·revision·중복·목록 cursor·query · 출처 찾거나 만들기"
          " · 요약/반응 상태·완료 뒤 수정 대기·stale·연결 출처 삭제 거절 · sr_ 인용 차단 4곳 · 「나」 개수 · 한도 50·50 · 웹 왕복) 통과")


async def test_prb_delete():
    """삭제 CLI — 거절 사다리 · 통지·유예·미리보기≠실행·재조회 불일치 중단·취소 · 원자성(③~⑦ 실패 주입) · 결과(정적 목록·자료 0·보존·contact·
    privacy_log·usage) · CLI 출력 · 감사 키 · 인증(옛 세션·옛 토큰·iat 없음 거절, 새 토큰 = 새 uid) · 파기(180일 제외·purge_after·유예) · 경쟁."""
    import contextlib
    import io
    import threading
    import admin_cli
    site = main.site
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    real_fault, real_userinfo = db._delete_fault, auth._userinfo_email
    gone = []   # 삭제된 uid — 끝에 시험 정리(이메일이 자리값이라 cleanup 이 못 찾는다)
    try:
        # ── 정적: 삭제 목록 = schema 의 user_id 테이블 − 예외 ──
        assert set(db.DELETE_TABLES) == _prb_user_tables() - set(db.DELETE_KEEP_TABLES) and len(db.DELETE_TABLES) == len(set(db.DELETE_TABLES))
        assert "privacy_log" not in _prb_user_tables() and "admin_audit" not in _prb_user_tables() and "users" not in _prb_user_tables()
        # ── 재료 ──
        ADM = db.user_by_email(PRB["adm"])["id"]
        _set_role(ADM, "admin")
        with SEED_ON():
            D = db.user_by_subject("prb_del_sub", PRB["del"], int(time.time()))["id"]
        db.display_name_set(D, "지울 사람 PRBDNAME")
        q = db.question_add(D, "질문 PRBTEXT")
        rc = db.record_add(D, "t", "원문 PRBTEXT 이다.", q["id"], source_ref={"kind": "url", "name": "출처 PRBSRCNAME"})
        cid = db.candidates_propose(D, rc["id"], q["id"], [{"text": "판단", "quote": "원문 PRBTEXT"}], "self")[0][0]
        db.candidate_decide(D, cid, "confirm")
        db.record_attach(D, rc["id"], [{"field_key": "attach_to", "target_kind": "candidate", "target_id": cid}], None, "자리")
        db.person_save(D, "별칭 PRBALIAS")
        db.source_reflection_save(D, rc["source_id"], "summary", "요약 PRBBODY")
        db.profile_set(D, "purpose", "목적", "confirmed")
        db.optional_consent_set(D, "struct_names", True)
        db.definition_propose(D, None, 0, {"key": "notes", "label": "노트", "semantic_kind": "collection", "fields": []})
        tk_bug = _tk_insert(D, "bug", "버그 PRBTEXT")   # SQL 로 — 앞 절이 전역 문의 한도를 채워 둔다
        tk = _tk_insert(D, "privacy", "계정 삭제 요청")
        _sql("INSERT INTO privacy_log (id,kind,via,ticket_id,received) VALUES (%s,'other','form',%s,(now() AT TIME ZONE 'Asia/Seoul')::date)",
             ("pl_prb" + secrets.token_hex(4), tk))   # ticket_add 와 같은 행(전역 문의 한도 때문에 SQL 로 넣었다)
        _sql("INSERT INTO usage_daily (day,user_id,name,code,n) VALUES ((now() AT TIME ZONE 'Asia/Seoul')::date, %s, 'record_add', 'OK', 3)", (D,))
        before = {t: _prb_count(D, t) for t in db.DELETE_TABLES}
        assert all(before[t] for t in ("item_links", "object_defs", "field_defs", "std_choices", "std_marks", "user_roles", "candidate_history",
                                        "candidates", "source_reflections", "records", "sources", "questions", "profile", "people",
                                        "optional_consent_log", "optional_consents", "usage_daily", "tickets")), before
        # 다른 사용자 · 관리자 소유 privacy 문의
        O = db.user_by_email(PRB["dela"])["id"]
        tk_adm = _tk_insert(ADM, "privacy", "관리자 삭제 요청")
        with db.tx() as c:
            plog = c.execute("SELECT * FROM privacy_log ORDER BY id").fetchall()
        nonadm = O

        def audits():
            with db.tx() as c:
                return c.execute("SELECT action,actor_id,target_id,detail FROM admin_audit WHERE action LIKE 'delete_%%' AND actor_id=%s ORDER BY id",
                                 (ADM,)).fetchall()
        # ── 거절 사다리 ──
        expect("FORBIDDEN", db.delete_preview, nonadm, tk)
        expect("FORBIDDEN", db.delete_notify, "", tk)
        expect("NOT-FOUND", db.delete_preview, ADM, "tk_none")
        expect("DELETE-NOT-PRIVACY", db.delete_notify, ADM, tk_bug)
        expect("DELETE-ADMIN-TARGET", db.delete_notify, ADM, tk_adm)
        pv = db.delete_preview(ADM, tk)
        assert pv["ready"] is False and pv["reasons"] == ["no_notice"] and pv["counts"] == {**before, "tickets": before["tickets"] - 1}
        expect("DELETE-NO-NOTICE", db.delete_run, ADM, tk, pv["value"])
        expect("DELETE-NO-NOTICE", db.delete_cancel, ADM, tk)
        # ── 통지 ──
        n = db.delete_notify(ADM, tk)
        assert n["within_10_days"] is True and (n["delete_after"] - n["notified_at"]).days == 7
        mine = db.ticket_get_mine(D, tk)
        assert mine["status"] == "doing" and mine["reply"] == db.DELETE_NOTICE and "삭제 결과는 이 문의 이력에 남습니다" in mine["reply"]
        assert [h["kind"] for h in mine["history"]] == ["status", "reply"]
        expect("DELETE-NOTIFIED", db.delete_notify, ADM, tk)
        pv = db.delete_preview(ADM, tk)
        assert pv["ready"] is False and pv["reasons"] == ["grace"] and pv["keep"] == {"tickets": 1, "ticket_history": 2, "deletion_contacts": 1}
        expect("DELETE-GRACE", db.delete_run, ADM, tk, pv["value"])
        # ── 취소 → 다시 통지 ──
        assert db.delete_cancel(ADM, tk)["cancelled"] and db.delete_preview(ADM, tk)["reasons"] == ["no_notice"]
        expect("DELETE-NO-NOTICE", db.delete_run, ADM, tk, pv["value"])
        db.delete_notify(ADM, tk)
        _sql("UPDATE deletion_contacts SET notified_at = now() - interval '8 days', delete_after = now() - interval '1 day' WHERE user_id=%s", (D,))
        pv = db.delete_preview(ADM, tk)
        assert pv["ready"] and pv["reasons"] == [] and pv == db.delete_preview(ADM, tk), "미리보기 = 읽기 전용·결정적"
        assert {t: _prb_count(D, t) for t in db.DELETE_TABLES} == before, "미리보기는 아무것도 안 지운다"
        expect("DELETE-STALE", db.delete_run, ADM, tk, "wrongvalue00")
        db.question_add(D, "미리보기 뒤에 생긴 질문")
        expect("DELETE-STALE", db.delete_run, ADM, tk, pv["value"])   # 재조회 불일치 → 중단
        # ── 원자성: ③~⑦ 각 단계 실패 주입 → 전부 원상태 + 실패 감사 1행 ──
        pv = db.delete_preview(ADM, tk)

        def snap():
            with db.tx() as c:
                return ({t: _prb_count(D, t) for t in db.DELETE_TABLES},
                        dict(c.execute("SELECT email,auth_subject,display_name,status,deleted_at,nav_accepted_at FROM users WHERE id=%s", (D,)).fetchone()),
                        dict(c.execute("SELECT * FROM deletion_contacts WHERE user_id=%s", (D,)).fetchone()),
                        dict(c.execute("SELECT status,closed_at,reply FROM tickets WHERE id=%s", (tk,)).fetchone()),
                        c.execute("SELECT count(*) AS n FROM ticket_history WHERE ticket_id=%s", (tk,)).fetchone()["n"])
        s0 = snap()
        for step in (3, 4, 5, 6, 7):
            def boom(k, step=step):
                if k == step:
                    raise RuntimeError(f"주입 {step}")
            db._delete_fault = boom
            n0 = len(audits())
            try:
                db.delete_run(ADM, tk, pv["value"])
                raise AssertionError("실패 주입이 통과했다")
            except RuntimeError:
                pass
            assert snap() == s0, f"단계 {step} 실패 → 원상태"
            a = audits()
            assert len(a) == n0 + 1 and a[-1]["action"] == "delete_run" and a[-1]["detail"] == {"ticket_id": tk, "result": "EXC"}, a[-1]
        db._delete_fault = real_fault
        # ── 경쟁 ①: 사용자 쓰기가 먼저 잠금 → 실행은 기다렸다가 재조회 불일치로 중단 ──
        locked, go, errs = threading.Event(), threading.Event(), []

        def writer():
            try:
                with db.tx() as c:
                    db._user_lock(c, D)
                    c.execute("INSERT INTO questions (id,user_id,text) VALUES (%s,%s,'경쟁 질문')", (db.new_id("q"), D))
                    locked.set()
                    go.wait(5)
            except Exception as e:
                errs.append(e)
        res = {}

        def runner():
            try:
                res["out"] = db.delete_run(ADM, tk, pv["value"])
            except Exception as e:
                res["err"] = e
        tw = threading.Thread(target=writer)
        tw.start()
        assert locked.wait(5)
        tr = threading.Thread(target=runner)
        tr.start()
        time.sleep(0.3)
        assert tr.is_alive(), "실행은 사용자 잠금을 기다린다"
        go.set()
        tw.join(5)
        tr.join(5)
        assert not errs and isinstance(res.get("err"), db.CrmError) and res["err"].code == "DELETE-STALE", res
        # ── 경쟁 ②: 실행이 먼저 잠금 → 기다리던 사용자 쓰기는 삭제 뒤 AUTH-INVALID(자료 생성 0) ──
        pv = db.delete_preview(ADM, tk)
        held, go2, wres = threading.Event(), threading.Event(), {}

        def hold(k):
            if k == 3:
                held.set()
                go2.wait(5)
        db._delete_fault = hold
        db.usage_count(D, "record_add", "OK")   # 이 프로세스 메모리 카운터 — 실행 뒤 버려진다

        def late_write():
            try:
                db.question_add(D, "삭제 중에 온 질문")
                wres["ok"] = True
            except db.CrmError as e:
                wres["code"] = e.code
        out = io.StringIO()
        # CLI 출력 함수(_delete)로 실행 — main 은 끝에 db.close() 라 다른 스레드가 풀을 쓰는 동안 부르지 않는다
        tr = threading.Thread(target=lambda: res.update(cli=admin_cli._delete("delete-run", [tk, pv["value"]], ADM) or 0))
        with contextlib.redirect_stdout(out):
            tr.start()
            assert held.wait(5)
            tq = threading.Thread(target=late_write)
            tq.start()
            time.sleep(0.3)
            assert tq.is_alive(), "사용자 쓰기는 실행의 잠금을 기다린다"
            go2.set()
            tr.join(10)
            tq.join(10)
        db._delete_fault = real_fault
        gone.append(D)
        assert res["cli"] == 0 and wres == {"code": "AUTH-INVALID"}, (res, wres)
        assert not [k for k in db.usage_snapshot() if k[1] == D], "메모리 카운터 버림"
        # ── 결과 ──
        assert {t: _prb_count(D, t) for t in db.DELETE_TABLES} == {**{t: 0 for t in db.DELETE_TABLES}, "tickets": 1}, "보존 문의만 남음"
        with db.tx() as c:
            u = c.execute("SELECT * FROM users WHERE id=%s", (D,)).fetchone()
            ct = c.execute("SELECT * FROM deletion_contacts WHERE user_id=%s", (D,)).fetchone()
            t = c.execute("SELECT * FROM tickets WHERE id=%s", (tk,)).fetchone()
            after = c.execute("SELECT * FROM privacy_log ORDER BY id").fetchall()
            mine = [r for r in after if r["ticket_id"] == tk]
            assert len(mine) == 1 and mine[0]["result"] == "done" and mine[0]["notified"] is not None, "그 요청 행에 처리 결과"
            assert [r for r in after if r["ticket_id"] != tk] == [r for r in plog if r["ticket_id"] != tk], "다른 privacy_log 행 불변"
            assert set(mine[0]) == {"id", "kind", "via", "ticket_id", "received", "notified", "result", "identity_checked", "created"}, "식별정보 열 없음"
            # 이메일 자리값: email 열엔 형식 CHECK 가 없고(UNIQUE 만) '@' 가 없어 검증된 이메일로 올 수 없다
            assert not c.execute("SELECT 1 FROM pg_constraint WHERE conrelid='users'::regclass AND contype='c' "
                                 "AND pg_get_constraintdef(oid) LIKE '%%email%%'").fetchone(), "email CHECK 없음"
        assert (u["email"], u["auth_subject"], u["display_name"], u["nav_accepted_at"], u["status"], u["desk_hidden"]) == (
            f"deleted:{D}", None, None, None, "deleted", False) and u["deleted_at"] and u["web_revoked_before"] and u["mcp_revoked_before"]
        assert (ct["email"], ct["auth_subject"], ct["ticket_id"]) == (PRB["del"], "prb_del_sub", tk) and ct["deleted_at"] == u["deleted_at"]
        assert "@" not in u["email"] and not db.user_by_email(PRB["del"])["id"] == D, "원래 이메일로 찾으면 삭제 행이 아니다(DEV 경로)"
        _sql("DELETE FROM users WHERE email=%s AND id<>%s", (PRB["del"], D))
        assert (ct["purge_after"] - ct["deleted_at"]).days == db.CONTACT_KEEP_DAYS
        assert t["status"] == "done" and t["closed_at"] and t["text"] == "계정 삭제 요청"
        hist = db.ticket_get_mine(D, tk)["history"]
        assert [h["kind"] for h in hist][-2:] == ["status", "deleted"] and hist[-2]["to_val"] == "done"
        printed = out.getvalue()
        assert "삭제했어요" in printed and "KST" in printed
        for w in (PRB["del"], "PRBDNAME", "PRBTEXT", "PRBALIAS", "PRBBODY", "PRBSRCNAME", "prb_del_sub", db.DELETE_NOTICE[:10]):
            assert w not in printed, w
        for a in audits():
            assert set(a["detail"]) == {"ticket_id", "result"} and a["target_id"] in (D, None), a
        assert [a["detail"]["result"] for a in audits() if a["action"] == "delete_run"][-1] == "ok"
        assert {a["action"] for a in audits()} == {"delete_notify", "delete_cancel", "delete_run"}
        expect("DELETE-ALREADY", db.delete_run, ADM, tk, pv["value"])
        expect("DELETE-ALREADY", db.delete_preview, ADM, tk)
        # 운영 화면: Users 목록·상세 「삭제됨」 코드 · 문의 이력 「삭제 완료」 · 감사 라벨 — 표시 이름·별칭·본문 0
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", site.session_value(ADM))
            ul = (await h.get("/admin/users")).text
            row = next(x for x in ul.split("<tr>") if f"deleted:{D}" in x)
            assert '<span class="tag deleted">삭제됨</span>' in row
            ud = (await h.get(f"/admin/users/{site._opaque(D)}")).text
            assert "· 삭제됨" in ud
            tkd = (await h.get(f"/admin/tickets/{tk}")).text
            assert "삭제 완료" in tkd
            setup = (await h.get("/admin/setup")).text
            assert "계정 삭제(CLI)" in setup and "삭제 통지(CLI)" in setup
            for page in (ul, ud, setup):
                for w in ("PRBDNAME", "PRBALIAS", "PRBBODY"):
                    assert w not in page, w
        # usage_flush 는 deleted uid 카운터를 버린다(서버 프로세스)
        db.usage_count(D, "state_get", "OK")
        db.usage_flush()
        assert _prb_count(D, "usage_daily") == 0
        # ── 인증 ──
        old_iat = int(u["deleted_at"].timestamp()) - 60
        assert site.session_value(D)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            h.cookies.set("crm_session", site.sign({"uid": D, "iat": old_iat}, 600))
            assert (await h.get("/api/state")).status_code == 401, "옛 웹 세션 무효"
            h.cookies.set("crm_session", site.session_value(D))
            assert (await h.get("/api/state")).status_code == 401, "삭제 뒤 새 쿠키도 status 로 무효"
        expect("AUTH-REVOKED", db.check_token_user, dict(u), int(time.time()))
        auth._userinfo_email = lambda tok: PRB["del"]
        for iat in (old_iat, None):
            try:
                auth.user_id_for(_tok("prb_del_sub", iat=iat))
                raise AssertionError("옛 토큰이 통과했다")
            except db.CrmError as e:
                assert e.code == "AUTH-REVOKED", (iat, e.code)
        expect("AUTH-REVOKED", db.user_by_subject, "prb_other_sub", PRB["del"], old_iat)   # 이메일로도 대조
        with SEED_ON(False):
            new = auth.user_id_for(_tok("prb_del_sub", iat=int(time.time()) + 1))
        assert new != D and db.me_home(new)["display_name"] is None and db.me_home(new)["counts"] == {
            "claims": 0, "principles": 0, "decisions": 0, "people": 0, "sources": 0, "reflections": 0}, "새 uid · 옛 자료 연결 0"
        with db.tx() as c:
            assert c.execute("SELECT user_id FROM deletion_contacts WHERE email=%s", (PRB["del"],)).fetchone()["user_id"] == D, "contact 는 옛 uid"
        try:
            auth.user_id_for(_tok("prb_del_sub", iat=old_iat))
            raise AssertionError("옛 토큰이 새 계정에 붙었다")
        except db.CrmError as e:
            assert e.code == "AUTH-REVOKED"
        # ── 파기: 일반 문의 180일 · 보존 문의 제외 · purge_after · 유예 중 행 ──
        _sql("UPDATE tickets SET closed_at = now() - interval '181 days' WHERE id=%s", (tk,))
        G = db.user_by_email(PRB["delb"])["id"]
        tk_g = _tk_insert(G, "privacy", "유예 중 요청")
        db.delete_notify(ADM, tk_g)
        _sql("UPDATE tickets SET status='done', closed_at = now() - interval '181 days' WHERE id=%s", (tk_g,))
        tk_old = _tk_insert(G, "bug", "오래된 문의")
        _sql("UPDATE tickets SET status='done', closed_at = now() - interval '181 days' WHERE id=%s", (tk_old,))
        db.tickets_purge()
        with db.tx() as c:
            alive = {r["id"] for r in c.execute("SELECT id FROM tickets WHERE id = ANY(%s)", ([tk, tk_g, tk_old],)).fetchall()}
        assert alive == {tk, tk_g}, "보존 문의·유예 중 문의는 180일 파기 제외"
        assert db.contacts_purge() == 0, "purge_after 전"
        _sql("UPDATE deletion_contacts SET purge_after = now() - interval '1 minute' WHERE user_id=%s", (D,))
        n_audit = len(audits())
        assert db.contacts_purge() == 1
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM tickets WHERE id=%s", (tk,)).fetchone() and \
                not c.execute("SELECT 1 FROM ticket_history WHERE ticket_id=%s", (tk,)).fetchone()
            assert not c.execute("SELECT 1 FROM deletion_contacts WHERE user_id=%s", (D,)).fetchone()
            assert c.execute("SELECT 1 FROM deletion_contacts WHERE user_id=%s AND deleted_at IS NULL", (G,)).fetchone(), "유예 중 행 파기 0"
        assert len(audits()) == n_audit, "감사는 contact 파기와 독립(1년 규칙)"
        # ── CLI 쓰는 법·오류 출력 ──
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert admin_cli.main(["delete-run", tk_g]) == 2 and admin_cli.main(["delete-preview", tk_g, "--actor"]) == 2
            assert admin_cli.main(["delete-preview", tk_g, "--actor", O]) == 1
        assert "[FORBIDDEN]" in err.getvalue() and PRB["delb"] not in err.getvalue()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert admin_cli.main(["delete-preview", tk_g, "--actor", ADM]) == 0
            assert admin_cli.main(["delete-cancel", tk_g, "--actor", ADM]) == 0
        assert "미리보기 값" in out.getvalue() and "실행 가능 = 아니오 (사유: grace)" in out.getvalue() and PRB["delb"] not in out.getvalue()
    finally:
        db._delete_fault = real_fault
        auth._userinfo_email = real_userinfo
        site.dev, site.dev_user = real
        with db.tx() as c:
            c.execute("DELETE FROM deletion_contacts WHERE user_id = ANY(%s) OR email = ANY(%s) "
                      "OR user_id IN (SELECT id FROM users WHERE email = ANY(%s))", (gone, list(PRB.values()), list(PRB.values())))
            c.execute("DELETE FROM admin_audit WHERE target_id = ANY(%s)", (gone,))
        for uid in gone:
            db.user_delete(uid)
    print("OK — 24d 삭제 CLI(거절 사다리 · 통지·유예·취소 · 미리보기 읽기 전용·≠실행·재조회 불일치 중단 · ③~⑦ 실패 주입 원상태+감사 · 경쟁 2"
          " · 결과(정적 목록·자료 0·보존 문의·contact 이동·users 자리값·privacy_log 불변·usage 버림) · CLI 출력 건수만 · 감사 키 2"
          " · 옛 세션·옛 토큰·iat 없음 거절·새 토큰 새 uid · 파기 180일 제외·purge_after·유예 0) 통과")


def test_prb_oldcode():
    """옛 코드(PR #21 = CRM_TEST_OLD_REF, 기본 feat/admin-setup)를 git archive 로 꺼내 새 스키마(임시 스키마)에서 init 2회·읽기·쓰기
    · 옛 user_delete 가 사람·요약/반응을 cascade 로 지우고 deletion_contacts 는 남는다 · 새 테이블·열·제약 모양 불변. 실데이터 아님."""
    import subprocess
    import tempfile
    ref = os.environ.get("CRM_TEST_OLD_REF", "5704546")   # PR #21 최종 커밋(feat/admin-setup) — 브랜치가 지워져도 돈다
    assert not subprocess.run(["git", "-C", str(HERE), "rev-parse", "--verify", "-q", ref + "^{commit}"], capture_output=True).returncode, \
        f"24e 옛 코드 호환: 커밋 {ref} 이 없어요(얕은 clone 이면 fetch-depth 0)"
    tables = ("users", "admin_audit", "ticket_history", "sources", "records") + PRB_TABLES
    name = f"prbold_{secrets.token_hex(3)}"
    create_schema(name)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            arc = subprocess.run(["git", "-C", str(HERE.parent), "archive", ref, "server"], capture_output=True, check=True).stdout
            subprocess.run(["tar", "-x", "-C", tmp], input=arc, check=True)
            with schema_conn(name) as c:
                db.init_schema(c)
                db.init_schema(c)
                c.execute("INSERT INTO users (id,email,display_name,nav_accepted_at) VALUES ('u_new','new@x.test','새 이름',now())")
                c.execute("INSERT INTO sources (user_id,id,kind,name) VALUES ('u_new','s_n','url','n')")
                c.execute("INSERT INTO people (user_id,id,alias) VALUES ('u_new','p_n','별칭')")
                c.execute("INSERT INTO source_reflections (user_id,id,source_id,kind,body) VALUES ('u_new','sr_n','s_n','summary','b')")
                c.execute("INSERT INTO deletion_contacts (user_id,ticket_id,requested_at,notified_at,delete_after) "
                          "VALUES ('u_new','tk_n',now(),now(),now())")
                # 삭제된 계정(새 코드가 남긴 모양) — 옛 코드는 원래 이메일·sub 로 이 행을 찾을 수 없어야 한다
                c.execute("INSERT INTO users (id,email,auth_subject,status,deleted_at) VALUES ('u_gone','deleted:u_gone',NULL,'deleted',now())")
                c.execute("INSERT INTO deletion_contacts (user_id,ticket_id,requested_at,notified_at,delete_after,email,auth_subject,deleted_at,"
                          "purge_after) VALUES ('u_gone','tk_g',now(),now(),now(),'gone@x.test','gone_sub',now(),now() + interval '1 year')")
                c.commit()
                shape0 = _prb_shape(c, tables)
            url = os.environ["DATABASE_URL"]
            url += ("&" if "?" in url else "?") + "options=" + urllib.parse.quote(f"-csearch_path={name}")
            script = ("import json, db\n"
                      "db.init_schema(); db.init_schema()\n"
                      "u = db.user_by_email('old-code@x.test')\n"
                      "q = db.question_add(u['id'], '옛 코드 질문')\n"
                      "r = db.record_add(u['id'], 't', '옛 코드 기록')\n"
                      "st = db.state(u['id'])\n"
                      "assert len(st['questions']) == 1 and len(st['journal']) == 1\n"
                      "db.user_delete('u_new')\n"
                      "g1 = db.user_by_email('gone@x.test')['id']\n"
                      "g2 = db.user_by_subject('gone_sub', 'gone@x.test')['id']\n"
                      "assert 'u_gone' not in (g1, g2) and g1 == g2, (g1, g2)\n"
                      "print(json.dumps({'uid': u['id']}))\n"
                      "db.close()\n")
            env = {**os.environ, "DATABASE_URL": url}
            for k in [k for k in env if k.startswith("CRM_FEATURE_")]:
                env.pop(k)
            p = subprocess.run([sys.executable, "-c", script], cwd=Path(tmp) / "server", env=env, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-2000:]
            uid = json.loads(p.stdout.strip().splitlines()[-1])["uid"]
            with schema_conn(name) as c:
                assert _prb_shape(c, tables) == shape0, "옛 init 이 새 테이블·열·제약을 바꾸지 않는다"
                u = c.execute("SELECT status,display_name,nav_accepted_at,deleted_at FROM users WHERE id=%s", (uid,)).fetchone()
                assert tuple(u.values()) == ("active", None, None, None), "옛 INSERT = 새 열 기본값"
                assert not c.execute("SELECT 1 FROM users WHERE id='u_new'").fetchone()
                assert not c.execute("SELECT 1 FROM people WHERE user_id='u_new'").fetchone()
                assert not c.execute("SELECT 1 FROM source_reflections WHERE user_id='u_new'").fetchone(), "옛 user_delete = cascade"
                assert c.execute("SELECT 1 FROM deletion_contacts WHERE user_id='u_new'").fetchone(), "deletion_contacts 는 남는다(FK 없음)"
            # 새 코드가 옛 코드가 만든 행을 읽고 쓴다
            with schema_conn(name) as c:
                c.execute("UPDATE users SET display_name='옛 사람' WHERE id=%s", (uid,))
                c.commit()
    finally:
        drop_schema(name)
    print(f"OK — 24e 옛 코드 호환({ref} · 새 스키마에서 옛 init 2회·읽기·쓰기·state · 옛 user_delete cascade 사람·요약/반응, contact 남음"
          " · 옛 코드가 원래 이메일·sub 로 삭제 행을 못 찾음 · 모양 불변) 통과")


async def test_prb_r5():
    """R5 결함(PR B 범위 추가): R-1 record_provenance_set MCP 도구(PROVENANCE-LOCKED 그대로·REVIEW_ACT 문구) · R-2 record_add 설명
    · R-3 표준 기준의 evidence_claims 빼기 → STANDARD-LOCKED(사용자 기준은 종전대로) · R-4 definitions_get(object_ids) 초안 draft=true
    · R-5 정의 JSON 모르는 키 ARG-INVALID(읽기 전용 왕복 키는 통과) · definition_archive/restore(표준 STANDARD-LOCKED·impact·멱등)
    · /api/state 에 기록별 축 연결(record_links)·축 항목 title·후보 review 가 있다."""
    prev_dev = os.environ.get("CRM_DEV_USER")
    with SEED_ON():
        U = db.user_by_email(PRB["mcp"])["id"]
    db.display_name_set(U, "R5")
    try:
        tools = {t.name: t for t in main.mcp._tool_manager.list_tools()}
        assert "record_provenance_set 으로 author_kind=external" in tools["record_add"].description
        assert {"record_id", "author_kind", "text_process", "reason"} <= set(tools["record_provenance_set"].parameters["properties"])
        for why in ("author_unknown", "processed"):
            assert "연결한 AI 대화에서 record_provenance_set 으로 정정" in contract.REVIEW_ACT[why]
        os.environ["CRM_DEV_USER"] = PRB["mcp"]
        async with Client(main.mcp) as c:
            # R-1
            r = val(await c.call_tool("record_add", {"text": "내가 쓴 줄 알았는데 남의 글이다."}))
            out = val(await c.call_tool("record_provenance_set", {"record_id": r["id"], "author_kind": "external",
                                                                    "text_process": "verbatim", "reason": "사용자: 남의 글"}))
            assert out["author_kind"] == "external" and out["provenance_history"][-1]["by"] == "tool"
            r2 = val(await c.call_tool("record_add", {"text": "내 원문 근거 문장."}))
            await c.call_tool("candidates_propose", {"record_id": r2["id"], "question_id": "", "candidates": [{"text": "t", "quote": "근거 문장"}]})
            res = await c.call_tool("record_provenance_set", {"record_id": r2["id"], "author_kind": "unknown", "text_process": "verbatim",
                                                             "reason": "모름"})
            assert _prb_err(res) == "PROVENANCE-LOCKED"
            # R-3 표준 기준(시드) evidence_claims 빼기
            pr = next(d for d in db.definitions(U) if d["key"] == "principles")
            keep = [{k: f[k] for k in ("key", "label", "value_type", "required", "options", "target_kind", "target_object_id", "multiple")}
                    for f in pr["fields"] if f["key"] != "evidence_claims"]
            res = await c.call_tool("definition_propose", {"object_id": pr["id"], "expected_revision": pr["revision"], "definition_json": json.dumps(
                {"key": "principles", "label": pr["label"], "semantic_kind": "principle", "policy": pr["policy"], "fields": keep})})
            assert _prb_err(res) == "STANDARD-LOCKED" and "std:principles.evidence_claims" in res.content[0].text, res.content[0].text
            # 사용자 기준(표준 아님)은 종전대로 — 빠진 evidence_claims 를 서버가 되살린다
            mine = db.definition_propose(U, None, 0, {"key": "my_rules", "label": "내 규칙", "semantic_kind": "principle", "fields": []})
            db.definition_apply(U, mine["object_id"], mine["proposal_id"], 0)
            again = db.definition_propose(U, mine["object_id"], 1, {"key": "my_rules", "label": "내 규칙", "semantic_kind": "principle", "fields": []})
            assert [f["key"] for f in again["definition"]["fields"]] == ["evidence_claims"]
            # R-4 초안
            dr = db.definition_propose(U, None, 0, {"key": "drafty", "label": "초안", "semantic_kind": "collection", "fields": []})
            got = val(await c.call_tool("definitions_get", {"object_ids": [dr["object_id"]], "include_archived": False}))
            assert len(got) == 1 and got[0]["draft"] is True and got[0]["revision"] == 0 and got[0]["proposal"]["proposal_id"] == dr["proposal_id"]
            assert dr["object_id"] not in [d["id"] for d in val(await c.call_tool("definitions_get", {"object_ids": None, "include_archived": False}))]
            assert all("draft" not in d for d in db.definitions(U)), "적용된 정의엔 키 추가 없음"
            # R-5 모르는 키
            for bad in ({"key": "x1", "label": "x", "semantic_kind": "collection", "fields": [], "archived": True},
                        {"key": "x1", "label": "x", "semantic_kind": "collection", "fields": [{"key": "a", "value_type": "text", "archived": True}]},
                        {"key": "x1", "label": "x", "semantic_kind": "collection", "fields": [], "archived_at": "2026-10-20"}):
                res = await c.call_tool("definition_propose", {"object_id": None, "expected_revision": 0, "definition_json": json.dumps(bad)})
                assert _prb_err(res) == "ARG-INVALID", (bad, res.content[0].text)
            echo = db.definitions(U, [mine["object_id"]])[0]   # definitions_get 응답을 그대로 되돌려 보내도 된다(읽기 전용 키 무시)
            echo.pop("history", None)
            ok = db.definition_propose(U, mine["object_id"], echo["revision"], {k: v for k, v in echo.items() if k != "id"})
            assert ok["proposal_id"]
            # 보관·복구
            ax = next(d for d in db.definitions(U) if d["key"] == "axes")
            res = await c.call_tool("definition_archive", {"object_id": ax["id"]})
            assert _prb_err(res) == "STANDARD-LOCKED"
            col = db.definition_propose(U, None, 0, {"key": "books", "label": "책", "semantic_kind": "collection", "fields": [
                {"key": "title", "label": "제목", "value_type": "text"}]})
            db.definition_apply(U, col["object_id"], col["proposal_id"], 0)
            ip = db.item_propose(U, col["object_id"], 1, None, 0, None, "책 하나", "", {"title": "t"}, [])
            db.item_decide(U, ip["item_id"], 0, ip["proposal_id"], "apply")
            a = val(await c.call_tool("definition_archive", {"object_id": col["object_id"]}))
            assert a["changed"] and a["impact"] == {"items": 1, "referencing_fields": 0} and a["archived_at"] and a["revision"] == 1
            assert col["object_id"] not in [d["id"] for d in db.definitions(U)]
            assert val(await c.call_tool("definition_archive", {"object_id": col["object_id"]}))["changed"] is False, "멱등"
            res = await c.call_tool("definition_archive", {"object_id": dr["object_id"]})
            assert _prb_err(res) == "ARG-INVALID", "초안은 보관 안 함"
            b = val(await c.call_tool("definition_restore", {"object_id": col["object_id"]}))
            assert b["changed"] and b["archived_at"] is None and col["object_id"] in [d["id"] for d in db.definitions(U)]
            assert len(db.items(U, col["object_id"])) == 1, "보관·복구가 항목을 지우지 않는다"
        # /api/state 에 화면이 쓸 키 — 기록별 축(record_links field_key axes · target_id = 축 항목 id, items 의 title) · 후보 review
        st = db.state(U)
        assert {"record_links", "items", "candidates"} <= set(st)
        assert all("review" in x for x in st["candidates"])
    finally:
        os.environ["CRM_DEV_USER"] = prev_dev
    print("OK — 24g R5 결함(record_provenance_set 도구·PROVENANCE-LOCKED·REVIEW_ACT · record_add 설명 · 표준 evidence_claims 빼기 STANDARD-LOCKED"
          " · 초안 draft · 모르는 키 ARG-INVALID·왕복 키 통과 · 보관/복구 표준 거절·impact·멱등·항목 보존 · /api/state 키) 통과")


async def test_prb_app():
    """/app 본문은 nav old/new 에서 같다(서버는 nav 로 분기하지 않는다) · 라우트 접두 `_` 근거. 24h 화면 정적은 test_web_home.py A5(③-0.2)."""
    # /app 본문 = nav old/new 같다(서버는 nav 로 분기하지 않는다 — 화면이 /api/state user.nav 로 고른다)
    site = main.site
    real = (site.dev, site.dev_user)
    site.dev, site.dev_user = False, ""
    try:
        V = db.user_by_email(PRB["nav"])["id"]
        bodies = []
        for accepted in (False, True):
            _sql("UPDATE users SET nav_accepted_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s", (accepted, V))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
                h.cookies.set("crm_session", site.session_value(V))
                r = await h.get("/app")
            assert r.status_code == 200 and db.me_home(V)["nav"] == ("new" if accepted else "old")
            bodies.append(r.text)
        assert bodies[0] == bodies[1], "/app 본문은 nav 와 무관"
    finally:
        site.dev, site.dev_user = real
    # 라우트 접두 `_`(#_me·#_obj·#_settings) 근거: 사용자 탭 id = 키트 규칙 ^t-\d{3,}$ 또는 기본 id(q·journal·map·…) — `_` 로 시작할 수 없다.
    # 서버엔 탭을 쓰는 경로가 없다(읽기 db.tabs 뿐). 탭 쓰기 경로를 더하면 `_` 접두 id 를 거절해야 한다.
    assert '"tab": re.compile(r"^t-\d{3,}$")' in (HERE.parent / "crmkit" / "schema.py").read_text(encoding="utf-8"), "키트 탭 id 규칙"
    code = "".join((HERE / f).read_text(encoding="utf-8") for f in ("db.py", "web.py", "main.py", "admin_db.py"))
    assert not re.search(r"(INSERT INTO|UPDATE)\s+tabs\b", code) and "/api/tabs" not in code and "def tabs_" not in code, "서버 탭 쓰기 경로 0"
    print("OK — 24h /app 본문 nav 무관 · 탭 쓰기 경로 0 통과")


async def test_prb_privacy():
    """처리방침 v6 — /privacy 머리글·표시 이름·사람·요약/반응·contact 1년 + board 이전(연결 장치·HMAC·검토 대기 30일·AI 요약)
    (문구 정본은 guides/11 — 문서 에이전트)."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
        r = await h.get("/privacy")
    assert r.status_code == 200 and PRIVACY_HEAD.startswith(PRIVACY_VERSION + " ")
    for w in (PRIVACY_HEAD, "표시 이름", "출처 요약/반응", "1년", "연결 장치", "HMAC", "승인 대기", "30일", "AI 요약"):
        assert w in r.text, w
    print("OK — 24f 처리방침 v6(머리글·표시 이름·출처 요약/반응·1년·연결 장치·HMAC·승인 대기 30일·AI 요약) 통과")


# ── 25) board 이전 — 활동·가져오기(스키마 · 계약 벡터 · 장치 토큰 · /api/imports · 승인 대기 · 집계 · MCP 읽기 · 삭제) ─────────
# 정본: notes/2026-10-20_board-마이그-구현계획.md · r3 T1~T7. 시험 자료는 전부 합성(실제 원문·이름·URL 없음). 스위치는 시험 안에서만 켠다.

BI_SWITCH_NAMES = ("IMPORTS_ENABLED", "ACTIVITY_IMPORTS_ENABLED", "UNATTENDED_SUMMARY_IMPORTS_ENABLED", "INBOX_APPROVALS_ENABLED",
                   "BOARD_MIGRATION_ENABLED")
BI_TABLES = ("import_streams", "activities", "activity_links", "import_entries", "import_counters")


@contextmanager
def _bi_switch(*on):
    """주어진 스위치만 켜고(나머지 끔) 끝나면 원래대로."""
    saved = {n: os.environ.pop("CRM_" + n, None) for n in BI_SWITCH_NAMES}
    for n in on:
        os.environ["CRM_" + n] = "1"
    try:
        yield
    finally:
        for n in BI_SWITCH_NAMES:
            os.environ.pop("CRM_" + n, None)
            if saved[n] is not None:
                os.environ["CRM_" + n] = saved[n]


@contextmanager
def _bi_fast():
    """요청 속도(분당) 한도를 시험 동안만 넓힌다 — 흐름 시험이 1분에 수십 번 보낸다. 분당 한도 자체는 25c 끝에서 원래 값으로 본다."""
    saved = dict(imports.LIMITS)
    imports.LIMITS[("device", "minute", "request")] = (100000, None)
    imports.LIMITS[("user", "minute", "request")] = (100000, None)
    try:
        yield
    finally:
        imports.LIMITS.clear()
        imports.LIMITS.update(saved)


def _bi_axes(uid, n=2):
    """분류 역할이 될 「축」 오브젝트(key axes) + 적용 항목 n 개. 반환 (오브젝트 id, [항목 id])."""
    p = db.definition_propose(uid, None, 0, {"key": "axes", "label": "축", "semantic_kind": "collection", "fields": []})
    db.definition_apply(uid, p["object_id"], p["proposal_id"], 0)
    out = []
    for i in range(n):
        ev = db.item_propose(uid, p["object_id"], 1, None, 0, f"AX-{i}", f"합성 축 {i}", "", {}, [])
        out.append(db.item_decide(uid, ev["item_id"], 0, ev["proposal_id"], "apply")["id"])
    with db.tx() as c:   # 역할 이관 규칙(key axes)을 이 사용자에게 바로 적용
        assert db._classify_target(c, uid) == p["object_id"]
    return p["object_id"], out


def _bi_o():
    return "o_" + secrets.token_hex(16)


def _bi_v():
    return "v_" + secrets.token_hex(16)


def _bi_act(dev, original=None, revision=None, prev=None, **kw):
    r = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": dev, "original_id": original or _bi_o(),
         "revision": revision or _bi_v(), "previous_revision": prev, "kind": "activity", "execution": "unattended",
         "transform_version": "board-likes/1", "policy_revision": 1, "action": "like", "state": "active", "occurred_on": None,
         "observed_on": None, "provider": "sns", "raw_id": secrets.token_hex(24)}
    r.update(kw)
    return r


def _bi_text(dev, kind="self_text", original=None, revision=None, prev=None, **kw):
    r = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": dev, "original_id": original or _bi_o(),
         "revision": revision or _bi_v(), "previous_revision": prev, "kind": kind, "execution": "attended",
         "transform_version": "self-text/1" if kind == "self_text" else "self-summary/1", "policy_revision": 1,
         "title": "합성 제목", "body": "합성 자기 글 BITEXT 본문"}
    if kind == "self_summary":
        r["generation"] = {"method": "ai_summary", "tool": "claude-cli", "model": "unknown", "input_scope": "self_only",
                           "inputs": [{"original_id": _bi_o(), "revision": _bi_v(), "sha256": "ab" * 32}]}
    r.update(kw)
    return r


async def _bi_post(token, body, key=None, raw=None):
    data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if key is None and isinstance(body, dict) and all(k in body for k in ("source_instance_id", "original_id", "revision")):
        key = imports.idempotency_key(body["source_instance_id"], body["original_id"], body["revision"])
    hd = {"content-type": "application/json"}
    if token is not None:
        hd["authorization"] = "Bearer " + token if not token.startswith("Bearer ") else token
    if key:
        hd["idempotency-key"] = key
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
        return await h.post("/api/imports", content=data, headers=hd)


def _bi_count(uid, t, where="", args=()):
    with db.tx() as c:
        return c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s {where}", (uid,) + tuple(args)).fetchone()["n"]


def _bi_today():
    with db.tx() as c:
        return c.execute("SELECT (now() AT TIME ZONE 'Asia/Seoul')::date AS d").fetchone()["d"]


def _bi_values(x):
    """요청 안의 문자열 값(키 제외) 전부."""
    if isinstance(x, dict):
        return [v for y in x.values() for v in _bi_values(y)]
    if isinstance(x, list):
        return [v for y in x for v in _bi_values(y)]
    return [x] if isinstance(x, str) else []


def test_bi_schema():
    """25a 스키마 — 옛 DB(board 이전 전 모양: 새 표·열 없음·item_links 이름 없는 3열 CHECK·좁은 CHECK) → init_schema 2회 = 새 DB 모양
    · 옛 행 received_at NULL·ingest_kind legacy · 새 INSERT 는 now()·interactive · 6열 대상 CHECK · 새 reference 종류 · 삭제 목록."""
    tables = ("records", "items", "people", "sources", "item_links", "field_defs", "admin_audit") + BI_TABLES
    fresh, old = f"bif_{secrets.token_hex(3)}", f"bio_{secrets.token_hex(3)}"
    create_schema(fresh)
    create_schema(old)
    try:
        with schema_conn(fresh) as c:
            db.init_schema(c)
            c.commit()
            want = _prb_shape(c, tables)
            names = {r["conname"] for r in c.execute("SELECT conname FROM pg_constraint WHERE conrelid='item_links'::regclass AND contype='c'")}
            assert "ck_item_links_target" in names and "item_links_check2" not in names, names
        with schema_conn(old) as c:
            db.init_schema(c)
            _bi_drop_new(c)
            c.execute("ALTER TABLE item_links DROP COLUMN target_person_id, DROP COLUMN target_source_id, DROP COLUMN target_activity_id")
            c.execute("ALTER TABLE item_links ADD CHECK (num_nonnulls(target_item_id, target_record_id, target_candidate_id) = 1)")
            for t in ("records", "items"):
                c.execute(f"ALTER TABLE {t} DROP COLUMN received_at, DROP COLUMN source_created_at, DROP COLUMN source_date, "
                          "DROP COLUMN ingest_kind, DROP COLUMN generation_kind, DROP COLUMN generation_meta, DROP COLUMN import_batch_id")
            c.execute("DROP INDEX IF EXISTS ux_people_identity")
            c.execute("DROP INDEX IF EXISTS ux_sources_activity_provider")
            c.execute("ALTER TABLE people DROP COLUMN hmac_namespace, DROP COLUMN identity_hmac")
            c.execute("ALTER TABLE sources DROP COLUMN activity_provider")
            c.execute("ALTER TABLE field_defs DROP CONSTRAINT field_defs_target_kind_check")
            c.execute("ALTER TABLE field_defs ADD CONSTRAINT field_defs_target_kind_check CHECK (target_kind IN ('item','record','candidate'))")
            c.execute("ALTER TABLE admin_audit DROP CONSTRAINT admin_audit_action_check")
            c.execute("ALTER TABLE admin_audit ADD CONSTRAINT admin_audit_action_check CHECK (action IN ('bootstrap','recover','role_grant',"
                      "'role_revoke','web_signout','block','unblock','delete_notify','delete_run','delete_cancel'))")
            c.execute("INSERT INTO users (id,email,display_name) VALUES ('u_bi','bi@x.test','옛')")
            c.execute("INSERT INTO records (id,user_id,title,body) VALUES ('r_bi','u_bi','t','옛 기록')")
            c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision) VALUES ('u_bi','od_bi','notes','노트','collection',1)")
            c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,target_kind) VALUES ('u_bi','od_bi','rel','관계','reference','record')")
            c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,title,revision) VALUES ('u_bi','i_bi','od_bi',1,'옛 항목',1)")
            c.execute("INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_record_id) VALUES ('u_bi','i_bi','od_bi','rel','r_bi')")
            c.execute("INSERT INTO people (user_id,id,alias) VALUES ('u_bi','p_bi','옛 별칭')")
            c.commit()
            db.init_schema(c)
            c.commit()
            first = _prb_shape(c, tables)
            db.init_schema(c)
            c.commit()
            assert _prb_shape(c, tables) == first == want, "옛 DB 2회 = 새 DB 모양(열·이름 붙은 CHECK·인덱스·새 표)"
            assert c.execute("SELECT received_at, ingest_kind, generation_kind FROM records WHERE id='r_bi'").fetchone() == {
                "received_at": None, "ingest_kind": "legacy", "generation_kind": "unknown"}, "옛 행 = NULL·legacy(추측 백필 없음)"
            assert c.execute("SELECT received_at, ingest_kind FROM items WHERE id='i_bi'").fetchone() == {"received_at": None, "ingest_kind": "legacy"}
            c.execute("INSERT INTO records (id,user_id,title,body) VALUES ('r_bi2','u_bi','t','새 기록')")
            r = c.execute("SELECT received_at, ingest_kind FROM records WHERE id='r_bi2'").fetchone()
            assert r["received_at"] is not None and r["ingest_kind"] == "interactive", r
            assert c.execute("SELECT hmac_namespace, identity_hmac FROM people WHERE id='p_bi'").fetchone() == {"hmac_namespace": None, "identity_hmac": None}
            assert c.execute("SELECT count(*) AS n FROM item_links WHERE user_id='u_bi'").fetchone()["n"] == 1, "옛 연결 보존"
            c.commit()
            for bad in ("INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key) VALUES ('u_bi','i_bi','od_bi','rel')",
                        "INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_record_id,target_person_id) "
                        "VALUES ('u_bi','i_bi','od_bi','rel','r_bi','p_bi')",
                        "INSERT INTO people (user_id,id,alias,hmac_namespace) VALUES ('u_bi','p_x','x','hn_x')",
                        "INSERT INTO sources (user_id,id,kind,name,activity_provider) VALUES ('u_bi','s_x','url','x','sns')",
                        "UPDATE records SET source_created_at=now(), source_date=current_date WHERE id='r_bi'",
                        "INSERT INTO admin_audit (action) VALUES ('purge')"):
                try:
                    c.execute(bad)
                    raise AssertionError(f"CHECK 가 막지 않았다: {bad}")
                except psycopg.errors.CheckViolation:
                    c.rollback()
            c.execute("INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_person_id) VALUES ('u_bi','i_bi','od_bi','rel','p_bi')")
            c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,target_kind) VALUES ('u_bi','od_bi','who','누구','reference','person')")
            c.execute("INSERT INTO admin_audit (action) VALUES ('device_connect'), ('migration_close')")
            c.rollback()
    finally:
        drop_schema(fresh)
        drop_schema(old)
    # 삭제 목록 = schema 의 user_id 표(24절 정적 대조와 같은 규칙) · 순서 = 관계 → 접수 대응표 → 업무 자료 → 입력원
    assert set(BI_TABLES) <= set(db.DELETE_TABLES)
    order = {t: i for i, t in enumerate(db.DELETE_TABLES)}
    assert order["item_links"] < order["activity_links"] < order["import_entries"] < order["activities"] < order["import_streams"] \
        < order["items"] < order["records"] < order["sources"] < order["people"], db.DELETE_TABLES
    print("OK — 25a board 이전 스키마(옛 DB 2회 = 새 DB · 옛 행 NULL·legacy · 새 행 now()·interactive · 6열 대상 CHECK · 새 reference 종류 · 감사 7종 · 삭제 순서) 통과")


def test_bi_contract():
    """25b 계약 — 공용 벡터(contract/imports/vectors/canon-inbox-1.json) = 서버 검증기 · 멱등 키 · JCS · 스키마 파일 모양 · 오류에 값 없음."""
    vec = json.loads((HERE / "contract" / "imports" / "vectors" / "canon-inbox-1.json").read_text(encoding="utf-8"))
    assert vec["contract"] == imports.CONTRACT and vec["schema_revision"] == imports.SCHEMA_REVISION
    assert len(vec["request_cases"]) >= 50 and len(vec["raw_cases"]) >= 5
    n_ok = 0
    for case in vec["request_cases"]:
        try:
            imports.validate(case["request"])
            got = "valid"
        except db.CrmError as e:
            assert e.code == "CONTRACT-INVALID", (case["name"], e.code)
            f, rule = e.message.split(": ", 1)
            got = {"field": f, "rule": rule}
            for v in _bi_values(case["request"]):
                if len(v) >= 8 and v not in ("canon-inbox/1",):
                    assert v not in e.message, (case["name"], "오류 문구에 요청 값이 들어갔다")
        assert got == case["expect"], (case["name"], got, case["expect"])
        n_ok += got == "valid"
    assert n_ok >= 6, n_ok
    for case in vec["raw_cases"]:
        e = expect("CONTRACT-INVALID", lambda r: imports.validate(imports.parse_request(r.encode("utf-8"))), case["raw"])
        assert dict(zip(("field", "rule"), e.message.split(": ", 1))) == case["expect"], case["name"]
    for k in vec["idempotency_keys"]:
        assert imports.idempotency_key(k["source_instance_id"], k["original_id"], k["revision"]) == k["key"]
    for j in vec["jcs_cases"]:
        assert imports.jcs(j["value"]).decode("utf-8") == j["jcs_utf8"] and imports.sha256_hex(imports.jcs(j["value"])) == j["sha256"]
    for name in ("canon-inbox-1.schema.json", "board-migration-1.schema.json"):
        sc = json.loads((HERE / "contract" / "imports" / name).read_text(encoding="utf-8"))
        assert sc["$schema"].startswith("https://json-schema.org/") and sc["type"] == "object"
    sc = json.loads((HERE / "contract" / "imports" / "canon-inbox-1.schema.json").read_text(encoding="utf-8"))
    act = next(b for b in sc["oneOf"] if b["properties"]["kind"] == {"const": "activity"})
    assert not {"title", "body", "url", "name", "memo", "email"} & set(act["properties"]), "활동에 자유 본문 필드 없음"
    assert set(sc["properties"]["transform_version"]["enum"]) == set(imports.TRANSFORMS)
    # 기존 동결 예제(server/contract/*.json 9개)는 그대로 — imports/ 하위 폴더는 18절 글롭에 안 걸린다
    assert len(list(contract.EXAMPLES_DIR.glob("*.json"))) == 9
    print("OK — 25b 계약(공용 벡터 = 서버 검증기 · 오류 문구에 값 0 · 멱등 키·JCS 벡터 · 스키마 파일 · 활동 자유 본문 필드 0) 통과")


async def test_bi_imports():
    """25c 장치 토큰 · /api/imports · 개정·멱등 · 한도 · 승인 대기 · 개인정보 경계 · 권한 경합."""
    A, B = db.user_by_email(BI["a"])["id"], db.user_by_email(BI["b"])["id"]
    ax_obj, (X1, X2) = _bi_axes(A)
    # ── 장치: 연결·목록·5개·재발급·폐기·policy ──
    c1 = imports.device_connect(A, "  합성 맥북  ")
    tok, dev = c1["token"], c1["device"]
    assert re.fullmatch(r"imp_is_[0-9a-f]{24}\.it_[0-9a-f]{16}\.[A-Za-z0-9_-]{43}", tok) and tok.startswith("imp_" + dev["id"] + ".")
    assert dev["label"] == "합성 맥북" and dev["active"] and dev["policy"] == imports.DEFAULT_POLICY and dev["hmac_namespace"].startswith("hn_")
    lst = imports.device_list(A)
    blob = json.dumps(lst, ensure_ascii=False)
    assert lst["active"] == 1 and lst["max"] == 5 and tok.split(".")[-1] not in blob and "secret" not in blob and "token_id" not in blob
    assert set(lst["switches"]) == {"imports", "activity", "unattended_summary", "inbox_approvals", "board_migration"}
    with db.tx() as c:
        h = c.execute("SELECT secret_hash FROM import_streams WHERE id=%s", (dev["id"],)).fetchone()["secret_hash"]
        assert bytes(h) == hashlib.sha256(tok.split(".")[-1].encode()).digest(), "서버엔 SHA-256 만"
        aud = c.execute("SELECT action, detail::text AS d FROM admin_audit WHERE actor_id=%s ORDER BY id", (A,)).fetchall()
    assert [a["action"] for a in aud] == ["device_connect"] and tok.split(".")[-1] not in aud[0]["d"]
    expect("ARG-INVALID", imports.device_connect, A, "")
    expect("ARG-INVALID", imports.device_connect, A, "가" * 81)
    extra = [imports.device_connect(A, f"합성 {i}") for i in range(4)]
    expect("DEVICE-LIMIT", imports.device_connect, A, "여섯째")
    for x in extra:
        imports.device_revoke(A, x["device"]["id"])
    assert imports.device_revoke(A, extra[0]["device"]["id"])["device"]["active"] is False, "해제 멱등"
    expect("DEVICE-REVOKED", imports.device_reissue, A, extra[0]["device"]["id"])
    expect("NOT-FOUND", imports.device_revoke, B, dev["id"])
    expect("NOT-FOUND", imports.device_reissue, B, dev["id"])
    pol = {"actions": ["like"], "transform_versions": ["board-likes/1", "self-text/1", "self-summary/1"], "axes": [X1, X2]}
    expect("CONFLICT", imports.device_policy_set, A, dev["id"], pol, 5)
    expect("ARG-INVALID", imports.device_policy_set, A, dev["id"], {**pol, "axes": ["i_notmine"]}, 1)
    expect("ARG-INVALID", imports.device_policy_set, A, dev["id"], {**pol, "transform_versions": ["feed/1"]}, 1)
    expect("ARG-INVALID", imports.device_policy_set, A, dev["id"], {**pol, "path": "/x"}, 1)
    assert imports.device_policy_set(A, dev["id"], pol, 1)["device"]["policy_revision"] == 2
    DEV = dev["id"]

    def act(**kw):
        return _bi_act(DEV, policy_revision=2, **kw)

    # ── 스위치 · 인증 ──
    with _bi_switch():
        r = await _bi_post(tok, act())
        assert r.status_code == 503 and r.json()["code"] == "IMPORTS-OFF", r.text
    with _bi_switch("IMPORTS_ENABLED"):
        r = await _bi_post(tok, act())
        assert r.status_code == 503 and r.json()["code"] == "ACTIVITY-IMPORTS-OFF", r.text
        for bad in (None, "", "Bearer x", "Bearer " + tok[:-1] + ("A" if tok[-1] != "A" else "B"), tok.replace("imp_is_", "imp_is_0")):
            r = await _bi_post(bad, act())
            assert r.status_code == 401 and r.json()["code"] == "AUTH-INVALID", (bad and bad[:12], r.text)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:   # 웹 쿠키로는 못 보낸다
            h.cookies.set("crm_session", main.site.session_value(A))
            r = await h.post("/api/imports", content=json.dumps(act()).encode(), headers={"origin": PUBLIC})
            assert r.status_code == 401, r.text
    on = ("IMPORTS_ENABLED", "ACTIVITY_IMPORTS_ENABLED", "INBOX_APPROVALS_ENABLED", "UNATTENDED_SUMMARY_IMPORTS_ENABLED")
    fast = _bi_fast()
    fast.__enter__()
    with _bi_switch(*on):
        # ── 활동: 신규 201 · 같은 요청 200 · 다른 본문 409 · 키 불일치 400 · 개정 충돌 409 · noop · 새 판 ──
        H1, H2 = secrets.token_hex(32), secrets.token_hex(32)
        o1, v1 = _bi_o(), _bi_v()
        b1 = act(original=o1, revision=v1, observed_on="2026-10-20", people=[{"hmac": H1, "role": "author"}], axes=[X1])
        r = await _bi_post(tok, b1)
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["state"] == "applied" and j["target_kind"] == "activity" and j["duplicate"] is False and j["expires_at"] is None
        a1 = j["target_id"]
        assert set(j) == {"import_id", "state", "target_kind", "target_id", "duplicate", "received_at", "expires_at"}
        r2 = await _bi_post(tok, b1)
        assert r2.status_code == 200 and r2.json()["duplicate"] is True and r2.json()["import_id"] == j["import_id"]
        r = await _bi_post(tok, {**b1, "raw_id": secrets.token_hex(24)}, key=imports.idempotency_key(DEV, o1, v1))
        assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY-CONFLICT"
        r = await _bi_post(tok, act(), key="ci1_" + "0" * 64)
        assert r.status_code == 400 and r.json()["code"] == "IDEMPOTENCY-KEY-MISMATCH"
        r = await _bi_post(tok, act(original=o1, prev=_bi_v()))
        assert r.status_code == 409 and r.json()["code"] == "SOURCE-REVISION-CONFLICT"
        r = await _bi_post(tok, act(prev=_bi_v()))
        assert r.status_code == 409 and r.json()["code"] == "SOURCE-REVISION-CONFLICT", "처음 보내는 원본은 previous null"
        v2 = _bi_v()
        r = await _bi_post(tok, {**b1, "revision": v2, "previous_revision": v1})   # 반영 내용 같음 → noop
        assert r.status_code == 201 and r.json()["state"] == "noop" and r.json()["target_id"] == a1, r.text
        v3 = _bi_v()
        r = await _bi_post(tok, {**b1, "revision": v3, "previous_revision": v2, "occurred_on": "2026-10-19", "axes": [X2]})
        assert r.status_code == 201 and r.json()["state"] == "applied" and r.json()["target_id"] != a1
        a1b = r.json()["target_id"]
        g_old, g_new = activity.activity_get(A, a1), activity.activity_get(A, a1b)
        assert g_old["current"] is False and g_new["current"] is True and g_old["current_id"] == a1b and len(g_new["versions"]) == 2
        assert [x["id"] for x in g_new["axes"]] == [X2] and [x["id"] for x in g_old["axes"]] == [X1], "옛 판 관계 그대로"
        r = await _bi_post(tok, {**b1, "revision": _bi_v(), "previous_revision": v3, "kind": "self_text",
                                 "transform_version": "self-text/1"})
        assert r.status_code == 400 and r.json()["code"] == "CONTRACT-INVALID", "종류가 바뀌면 계약부터 틀린다"
        with db.tx() as c:
            assert c.execute("SELECT count(*) AS n FROM import_entries WHERE user_id=%s AND original_id=%s", (A, o1)).fetchone()["n"] == 3
            assert c.execute("SELECT count(*) AS n FROM import_entries WHERE user_id=%s AND original_id=%s AND is_current_applied",
                             (A, o1)).fetchone()["n"] == 1
            assert c.execute("SELECT payload FROM import_entries WHERE id=%s", (j["import_id"],)).fetchone()["payload"] is None
        # 응답 유실 뒤 확인(GET) — 같은 장치만
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            g = await h.get(f"/api/imports/{j['import_id']}", headers={"authorization": "Bearer " + tok})
            assert g.status_code == 200 and g.json()["import_id"] == j["import_id"] and g.json()["duplicate"] is True
        # ── 사람: HMAC → 별칭 사람-NNNN · 같은 HMAC 재사용 · 다른 장치(키 공간) = 다른 사람 ──
        with db.tx() as c:
            ppl = c.execute("SELECT id, alias, hmac_namespace FROM people WHERE user_id=%s AND identity_hmac IS NOT NULL", (A,)).fetchall()
        assert len(ppl) == 1 and re.fullmatch(r"사람-\d{4}", ppl[0]["alias"]) and ppl[0]["hmac_namespace"] == dev["hmac_namespace"]
        P1 = ppl[0]["id"]
        r = await _bi_post(tok, act(people=[{"hmac": H1, "role": "author"}, {"hmac": H2, "role": "participant"}]))
        assert r.status_code == 201
        assert _bi_count(A, "people", "AND identity_hmac IS NOT NULL") == 2, "같은 HMAC = 같은 사람"
        d2 = imports.device_connect(A, "합성 둘째")
        r = await _bi_post(d2["token"], _bi_act(d2["device"]["id"], people=[{"hmac": H1, "role": "author"}]))
        assert r.status_code == 201 and _bi_count(A, "people", "AND identity_hmac IS NOT NULL") == 3, "다른 키 공간 = 다른 사람(병합 없음)"
        pc = activity.person_context(A, P1)
        assert pc["person"]["linked"] and pc["activity_count"] == 2 and "identity_hmac" not in json.dumps(pc) and H1 not in json.dumps(pc)
        # ── policy · 크기 · 출처 ──
        r = await _bi_post(tok, act(axes=["i_notallowed"]))
        assert r.status_code == 403 and r.json()["code"] == "POLICY-DENIED"
        r = await _bi_post(tok, _bi_act(DEV, policy_revision=1))
        assert r.status_code == 409 and r.json()["code"] == "POLICY-STALE"
        r = await _bi_post(tok, act(source_instance_id=d2["device"]["id"]))
        assert r.status_code == 403 and r.json()["code"] == "STREAM-MISMATCH"
        big = act()
        r = await _bi_post(tok, big, raw=json.dumps(big).encode() + b" " * 9000)
        assert r.status_code == 413 and r.json()["code"] == "PAYLOAD-LIMIT", "활동 8 KiB"
        with db.tx() as c:
            srcs = c.execute("SELECT kind, name, location, activity_provider FROM sources WHERE user_id=%s AND activity_provider IS NOT NULL",
                             (A,)).fetchall()
        assert [dict(s) for s in srcs] == [{"kind": "other", "name": "SNS 활동", "location": "", "activity_provider": "sns"}], "일반 출처 하나"
        # ── 개인정보 경계: 금지 필드·URL 은 거절되고 DB·응답·감사에 남지 않는다 ──
        mark = "BILEAK" + secrets.token_hex(6)
        for bad in (act(title=mark), act(url="https://example.com/" + mark), act(people=[{"hmac": H1, "role": "author", "name": mark}])):
            r = await _bi_post(tok, bad)
            assert r.status_code == 400 and mark not in r.text, r.text
        r = await _bi_post(tok, _bi_text(DEV, policy_revision=2, body=f"합성 https://example.com/{mark}"))
        assert r.status_code == 400 and r.json()["msg"] == "body: url_not_allowed" and mark not in r.text
        with db.tx() as c:
            for t, col in (("import_entries", "payload::text"), ("records", "body"), ("admin_audit", "detail::text"), ("people", "alias"),
                           ("sources", "name")):
                assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE {col} LIKE %s", (f"%{mark}%",)).fetchone()["n"] == 0, t
        # ── 자기 글: 대기 → 승인 / 제외 / 새 개정 superseded / 만료 ──
        ot, vt = _bi_o(), _bi_v()
        bt = _bi_text(DEV, original=ot, revision=vt, policy_revision=2, activity_ref=a1b, source_date="2026-10-18")
        r = await _bi_post(tok, bt)
        assert r.status_code == 201 and r.json()["state"] == "pending" and r.json()["target_id"] is None and r.json()["expires_at"]
        e1 = r.json()["import_id"]
        assert imports.inbox_count(A) == 1
        il = imports.inbox_list(A)
        assert [x["id"] for x in il["entries"]] == [e1] and "body" not in il["entries"][0] and il["entries"][0]["approvable"]
        ig = imports.inbox_get(A, e1)
        assert ig["body"] == bt["body"] and ig["approve_notice"] is None and ig["device"]["label"] == "합성 맥북"
        assert expect("NOT-FOUND", imports.inbox_get, B, e1).message == "그 승인 대기 글을 찾지 못했어요."
        expect("NOT-FOUND", imports.inbox_approve, B, e1)
        with _bi_switch("IMPORTS_ENABLED"):
            expect("APPROVALS-OFF", imports.inbox_approve, A, e1)
        ap = imports.inbox_approve(A, e1)
        rec = db.record_get(A, ap["record_id"])
        assert (rec["author_kind"], rec["text_process"], rec["body"]) == ("self", "verbatim", bt["body"])
        with db.tx() as c:
            rr = c.execute("SELECT ingest_kind, generation_kind, source_date, received_at FROM records WHERE id=%s", (ap["record_id"],)).fetchone()
            ent = c.execute("SELECT state, payload, payload_bytes, is_current_applied FROM import_entries WHERE id=%s", (e1,)).fetchone()
        assert rr["ingest_kind"] == "inbox" and rr["generation_kind"] == "unknown" and str(rr["source_date"]) == "2026-10-18"
        assert dict(ent) == {"state": "applied", "payload": None, "payload_bytes": 0, "is_current_applied": True}, "승인 = 본문 파기"
        assert activity.activity_get(A, a1b)["memos"][0]["record_id"] == ap["record_id"], "activity_ref → 메모 연결"
        expect("INBOX-RESOLVED", imports.inbox_approve, A, e1)
        r = await _bi_post(tok, bt)
        assert r.status_code == 200 and r.json()["state"] == "applied" and r.json()["target_id"] == ap["record_id"], "본문 파기 뒤 재전송 = 기존 결과"
        n_rec = _bi_count(A, "records")
        r = await _bi_post(tok, {**bt, "revision": _bi_v(), "previous_revision": vt})   # 승인한 판과 같은 내용 → noop
        assert r.status_code == 201 and r.json()["state"] == "noop" and _bi_count(A, "records") == n_rec
        o2 = _bi_o()
        rA = (await _bi_post(tok, _bi_text(DEV, original=o2, revision=(va := _bi_v()), policy_revision=2))).json()
        rB = (await _bi_post(tok, _bi_text(DEV, original=o2, revision=_bi_v(), prev=va, policy_revision=2, body="고친 합성 본문"))).json()
        assert rA["state"] == rB["state"] == "pending"
        old = imports.inbox_get(A, rA["import_id"])
        assert old["state"] == "excluded" and old["body"] is None, "새 개정이 오면 옛 대기는 superseded 로 닫힌다"
        expect("INBOX-RESOLVED", imports.inbox_approve, A, rA["import_id"])
        assert imports.inbox_exclude(A, rB["import_id"])["state"] == "excluded"
        rC = (await _bi_post(tok, _bi_text(DEV, policy_revision=2))).json()
        _sql("UPDATE import_entries SET expires_at = now() - interval '1 second' WHERE id=%s", (rC["import_id"],))
        expect("INBOX-EXPIRED", imports.inbox_approve, A, rC["import_id"])
        assert imports.expire_pending() >= 1 and imports.inbox_get(A, rC["import_id"])["state"] == "expired"
        # ── AI 요약: 무인 스위치 · 승인 = unknown/generated·ai · 출처 정정 차단 ──
        sm = _bi_text(DEV, kind="self_summary", policy_revision=2, execution="unattended")
        with _bi_switch("IMPORTS_ENABLED", "INBOX_APPROVALS_ENABLED"):
            r = await _bi_post(tok, sm)
            assert r.status_code == 503 and r.json()["code"] == "SUMMARY-IMPORTS-OFF"
            r = await _bi_post(tok, {**sm, "execution": "attended", "original_id": _bi_o()})   # 사람이 시작한 요약은 무인 스위치와 무관
            assert r.status_code == 201 and r.json()["state"] == "pending"
            imports.inbox_exclude(A, r.json()["import_id"])
        r = await _bi_post(tok, sm)
        assert r.status_code == 201, r.text
        es = r.json()["import_id"]
        assert imports.inbox_get(A, es)["approve_notice"] == imports.AI_APPROVE_NOTICE
        assert imports.inbox_get(A, es)["generation"] == {"method": "ai_summary", "tool": "claude-cli", "model": "unknown",
                                                          "input_scope": "self_only", "inputs": 1}
        sr = imports.inbox_approve(A, es)["record_id"]
        rs = db.record_get(A, sr)
        assert (rs["author_kind"], rs["text_process"]) == ("unknown", "generated")
        with db.tx() as c:
            g = c.execute("SELECT generation_kind, generation_meta FROM records WHERE id=%s", (sr,)).fetchone()
        assert g["generation_kind"] == "ai" and g["generation_meta"]["reported_by"] == "device" and g["generation_meta"]["tool"] == "claude-cli"
        expect("PROVENANCE-AI-LOCKED", db.record_provenance_set, A, sr, "self", "verbatim", "내가 쓴 것으로")
        # ── 경합(실제 동시 — ASGI 스레드 2개): 같은 요청 동시 2건 = 접수 1행(201 하나·200 하나) · 승인/제외 동시 = 하나만 ──
        rb = _bi_act(d2["device"]["id"])
        rs = await asyncio.gather(_bi_post(d2["token"], rb), _bi_post(d2["token"], rb))
        assert sorted(x.status_code for x in rs) == [200, 201] and rs[0].json()["import_id"] == rs[1].json()["import_id"], [x.text for x in rs]
        assert _bi_count(A, "import_entries", "AND original_id=%s", (rb["original_id"],)) == 1

        def _try(fn, *a):
            try:
                fn(*a)
                return "ok"
            except db.CrmError as e:
                return e.code
        pend = (await _bi_post(d2["token"], _bi_text(d2["device"]["id"]))).json()["import_id"]
        outs = await asyncio.gather(asyncio.to_thread(_try, imports.inbox_approve, A, pend), asyncio.to_thread(_try, imports.inbox_exclude, A, pend))
        assert sorted(outs) == ["INBOX-RESOLVED", "ok"], outs
        # 폐기 vs 접수 동시(스레드 2개·커넥션 2개) — 폐기가 먼저 커밋되면 접수 0, 접수가 먼저면 접수 1 · 둘 다 끝난 뒤엔 401
        for _ in range(6):
            dr = imports.device_connect(A, "합성 경합")
            br = _bi_act(dr["device"]["id"])
            r, _rv = await asyncio.gather(_bi_post(dr["token"], br), asyncio.to_thread(imports.device_revoke, A, dr["device"]["id"]))
            n = _bi_count(A, "import_entries", "AND stream_id=%s", (dr["device"]["id"],))
            if r.status_code == 201:
                assert n == 1 and _bi_count(A, "activities", "AND stream_id=%s", (dr["device"]["id"],)) == 1
            else:
                assert r.status_code == 401 and r.json()["code"] == "TOKEN-REVOKED" and n == 0, (r.status_code, r.text, n)
            r2 = await _bi_post(dr["token"], _bi_act(dr["device"]["id"]))
            assert r2.status_code == 401 and r2.json()["code"] == "TOKEN-REVOKED"
        # 만료됐지만 아직 파기 전인 대기는 가득 참 계산에서 빠진다 · 지난 카운터 파기
        _sql("INSERT INTO import_counters (user_id,scope_key,period,period_start,bucket,n) VALUES (%s,'user','minute',now() - interval '2 hours','request',1)", (A,))
        assert imports.counters_purge() >= 1
        assert _bi_count(A, "import_counters", "AND period='minute' AND period_start < now() - interval '1 hour'") == 0
        # ── 한도: 승인 대기 가득 · 하루 접수 · 요청 속도 · 사람 하루 50(트랜잭션 전체 취소) ──
        saved = (imports.PENDING_MAX, dict(imports.LIMITS))
        try:
            imports.PENDING_MAX = imports.inbox_count(A) + 1
            one = (await _bi_post(tok, _bi_text(DEV, policy_revision=2))).json()["import_id"]
            r = await _bi_post(tok, _bi_text(DEV, policy_revision=2))
            assert r.status_code == 429 and r.json()["code"] == "INBOX-FULL"
            _sql("UPDATE import_entries SET expires_at = now() - interval '1 second' WHERE id=%s", (one,))
            r = await _bi_post(tok, _bi_text(DEV, policy_revision=2))
            assert r.status_code == 201 and r.json()["state"] == "pending", r.text
            imports.PENDING_MAX = saved[0]
            with db.tx() as c:
                used = c.execute("SELECT COALESCE(sum(n),0) AS n FROM import_counters WHERE user_id=%s AND scope_key=%s AND period='day' "
                                 "AND bucket='activity'", (A, DEV)).fetchone()["n"]
            imports.LIMITS[("device", "day", "activity")] = (used, None)
            n_act = _bi_count(A, "activities")
            r = await _bi_post(tok, act())
            assert r.status_code == 429 and r.json()["code"] == "IMPORT-QUOTA" and _bi_count(A, "activities") == n_act
        finally:
            imports.PENDING_MAX, imports.LIMITS = saved[0], saved[1]
        _sql("INSERT INTO people (user_id,id,alias) SELECT %s, 'p_bilim' || g, '채움' FROM generate_series(1, 50) AS g", (A,))
        n_act, n_ppl = _bi_count(A, "activities"), _bi_count(A, "people")
        r = await _bi_post(tok, act(people=[{"hmac": secrets.token_hex(32), "role": "author"}]))
        assert r.status_code == 429 and r.json()["code"] == "PEOPLE-QUOTA" and r.json()["resume_at"].endswith("T00:00:00+09:00"), r.text
        assert (_bi_count(A, "activities"), _bi_count(A, "people")) == (n_act, n_ppl), "새 사람이 한도를 넘으면 활동도 안 생긴다"
        r = await _bi_post(tok, act(people=[{"hmac": H1, "role": "author"}]))
        assert r.status_code == 201, "이미 있는 사람만이면 통과"
        _sql("DELETE FROM people WHERE user_id=%s AND id LIKE 'p_bilim%%'", (A,))
        fast.__exit__(None, None, None)   # 여기부터 실제 분당 한도(장치 10)
        _sql("DELETE FROM import_counters WHERE user_id=%s AND period='minute'", (A,))
        codes = [(await _bi_post(d2["token"], {"x": 1})).status_code for _ in range(11)]
        assert codes[:10] == [400] * 10 and codes[10] == 429, codes   # 실패도 요청 속도에 센다
        _sql("DELETE FROM import_counters WHERE user_id=%s AND period='minute'", (A,))
        fast = _bi_fast()
        fast.__enter__()
        # ── 권한 경합: 만료 · 재발급 · 폐기 · 차단 · 삭제된 사용자 ──
        _sql("UPDATE import_streams SET token_expires_at = now() - interval '1 second' WHERE id=%s", (DEV,))
        r = await _bi_post(tok, act())
        assert r.status_code == 401 and r.json()["code"] == "TOKEN-EXPIRED"
        tok2 = imports.device_reissue(A, DEV)["token"]
        r = await _bi_post(tok, act())
        assert r.status_code == 401 and r.json()["code"] == "AUTH-INVALID", "재발급 = 옛 토큰 즉시 무효"
        assert (await _bi_post(tok2, act())).status_code == 201
        _sql("UPDATE users SET blocked_at = now() WHERE id=%s", (A,))
        r = await _bi_post(tok2, act())
        assert r.status_code == 403 and r.json()["code"] == "AUTH-BLOCKED"
        _sql("UPDATE users SET blocked_at = NULL WHERE id=%s", (A,))
        imports.device_revoke(A, DEV)
        r = await _bi_post(tok2, act())
        assert r.status_code == 401 and r.json()["code"] == "TOKEN-REVOKED"
        # 다른 사용자의 활동을 가리킬 수 없다
        cb = imports.device_connect(B, "합성 남")
        r = await _bi_post(cb["token"], _bi_text(cb["device"]["id"], activity_ref=a1b))
        assert r.status_code == 400 and r.json()["code"] == "REF-NOT-FOUND"
        # 장치 자기 확인·자기 폐기(장치 토큰만 — 웹 쿠키로는 401)
        hb = {"authorization": "Bearer " + cb["token"]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            g = await h.get("/api/imports/device", headers=hb)
            assert g.status_code == 200 and set(g.json()) == {"stream_id", "label", "policy", "policy_revision", "hmac_namespace",
                                                              "token_expires_at"} and g.json()["stream_id"] == cb["device"]["id"], g.text
            assert cb["token"].split(".")[-1] not in g.text
            h.cookies.set("crm_session", main.site.session_value(B))
            assert (await h.get("/api/imports/device")).status_code == 401, "웹 세션은 장치 경로에 안 걸린다"
            assert (await h.post("/api/imports/device/revoke", headers={"origin": PUBLIC})).status_code == 401
            rv = await h.post("/api/imports/device/revoke", headers=hb)
            assert rv.status_code == 200 and rv.json() == {"stream_id": cb["device"]["id"], "revoked": True}, rv.text
            g = await h.get("/api/imports/device", headers=hb)
            assert g.status_code == 401 and g.json()["code"] == "TOKEN-REVOKED"
        with db.tx() as c:
            aud = [x["action"] for x in c.execute("SELECT action FROM admin_audit WHERE actor_id=%s ORDER BY id", (A,)).fetchall()]
        assert aud.count("device_connect") == 12 and "device_reissue" in aud and "device_policy" in aud and aud.count("device_revoke") == 11
    fast.__exit__(None, None, None)
    print("OK — 25c 장치 토큰(SHA-256·5개·재발급·폐기·policy) · /api/imports(스위치·인증·멱등 200/409·키 400·개정 409·noop·새 판·GET)"
          " · 사람 HMAC(별칭·재사용·키 공간) · 경계(금지 필드·URL·값 0) · 승인 대기(승인·제외·superseded·만료·본문 파기·메모 연결)"
          " · AI 요약(무인 스위치·generated·정정 차단) · 한도(대기·하루·분당·사람 50 전체 취소) · 만료·재발급·차단·폐기 통과")


async def test_bi_aggregate():
    """25d 「나」 집계 — 손계산 대조(기간·다중 축·다중 사람·participant 제외·날짜 미확인·취소·noop·옛 판) · 근거 연결 · reference 새 대상
    · 사람 삭제 보호 · MCP 읽기 7(웹과 같은 함수·HMAC 없음)."""
    U = db.user_by_email(BI["agg"])["id"]
    _, (X1, X2) = _bi_axes(U)
    c1 = imports.device_connect(U, "합성 집계")
    tok, D = c1["token"], c1["device"]["id"]
    imports.device_policy_set(U, D, {"actions": ["like"], "transform_versions": ["board-likes/1"], "axes": [X1, X2]}, 1)
    T = _bi_today()
    day = lambda n: (T - __import__("datetime").timedelta(days=n)).isoformat()   # noqa: E731
    HP = {k: secrets.token_hex(32) for k in ("P1", "P2", "P3")}
    ids = {}
    with _bi_switch("IMPORTS_ENABLED", "ACTIVITY_IMPORTS_ENABLED"), _bi_fast():
        async def send(name, **kw):
            b = _bi_act(D, policy_revision=2, **kw)
            r = await _bi_post(tok, b)
            assert r.status_code == 201, r.text
            ids[name] = (b, r.json()["target_id"])
            return b
        await send("a1", occurred_on=day(0), people=[{"hmac": HP["P1"], "role": "author"}], axes=[X1])
        await send("a2", occurred_on=day(8), people=[{"hmac": HP["P1"], "role": "author"}, {"hmac": HP["P2"], "role": "participant"}],
                   axes=[X1, X2])
        await send("a3", occurred_on=day(40), people=[{"hmac": HP["P2"], "role": "author"}])
        await send("a4", occurred_on=None, observed_on=day(1))
        await send("a5", occurred_on=None)
        b6 = await send("a6", occurred_on=day(2))
        r = await _bi_post(tok, {**b6, "revision": _bi_v(), "previous_revision": b6["revision"], "state": "retracted"})
        assert r.json()["state"] == "applied"
        b7 = await send("a7", occurred_on=day(3))
        assert (await _bi_post(tok, {**b7, "revision": _bi_v(), "previous_revision": b7["revision"]})).json()["state"] == "noop"
        b8 = await send("a8", occurred_on=day(4), people=[{"hmac": HP["P3"], "role": "author"}])
        r = await _bi_post(tok, {**b8, "revision": _bi_v(), "previous_revision": b8["revision"], "axes": [X2]})
        ids["a8b"] = (None, r.json()["target_id"])
    with db.tx() as c:
        P = {k: c.execute("SELECT id FROM people WHERE user_id=%s AND identity_hmac=%s", (U, bytes.fromhex(h))).fetchone()["id"]
             for k, h in HP.items()}

    def top(agg):
        return sorted((x["person_id"], x["n"]) for x in agg["top_people"])

    m30 = activity.me_aggregate(U)
    assert m30["period"] == "30d" and m30["to"] == T.isoformat() and m30["from"] == day(29)
    assert m30["total"] == 4, m30   # a1 a2 a7 a8b (a3 기간 밖 · a4 a5 날짜 미확인 · a6 취소)
    assert m30["by_action"] == [{"action": "like", "label": "좋아요", "n": 4}]
    assert {x["item_id"]: x["n"] for x in m30["by_axis"]} == {X1: 2, X2: 2} and m30["axis_unclassified"] == 1 and m30["axis_sum_differs"]
    assert top(m30) == sorted([(P["P1"], 2), (P["P3"], 1)]), "participant 만인 P2 는 빠진다"
    assert m30["undated"] == {"n": 2, "observed": [{"observed_on": day(1), "n": 1}, {"observed_on": None, "n": 1}]}
    assert [x["n"] for x in m30["by_source"]] == [4] and m30["by_source"][0]["provider"] == "sns"
    assert m30["notice"] == activity.NOTICE and m30["grounds"] == {"principles": []}
    m7 = activity.me_aggregate(U, "7d")
    assert (m7["total"], {x["item_id"]: x["n"] for x in m7["by_axis"]}, m7["axis_unclassified"]) == (3, {X1: 1, X2: 1}, 1)
    assert top(m7) == sorted([(P["P1"], 1), (P["P3"], 1)])
    m90, mall = activity.me_aggregate(U, "90d"), activity.me_aggregate(U, "all")
    assert m90["total"] == mall["total"] == 5 and top(m90) == top(mall) == sorted([(P["P1"], 2), (P["P2"], 1), (P["P3"], 1)])
    assert mall["from"] is None
    expect("ARG-INVALID", activity.me_aggregate, U, "365d")
    # 목록·문맥
    al = activity.activity_list(U)
    assert len(al["activities"]) == 8 and ids["a8"][1] not in {a["id"] for a in al["activities"]}, "현재 판만(옛 판 a8 제외, 취소 판 보임)"
    occ = [a["occurred_on"] for a in al["activities"]]
    assert occ == sorted([d for d in occ if d], reverse=True) + [None] * occ.count(None), ("정렬 = 행동 날짜 최근 → 날짜 미확인", occ)
    for ctx in (activity.person_context(U, P["P1"])["activities"], activity.axis_context(U, X1)["activities"]):
        o2 = [a["occurred_on"] for a in ctx]
        assert o2 == sorted([d for d in o2 if d], reverse=True) + [None] * o2.count(None), o2
    assert {a["id"] for a in activity.activity_list(U, dated="no")["activities"]} == {ids["a4"][1], ids["a5"][1]}
    assert {a["id"] for a in activity.activity_list(U, axis_item_id=X2)["activities"]} == {ids["a2"][1], ids["a8b"][1]}
    assert {a["id"] for a in activity.activity_list(U, person_id=P["P2"])["activities"]} == {ids["a2"][1], ids["a3"][1]}
    pg = activity.activity_list(U, limit=3)
    pg2 = activity.activity_list(U, limit=3, cursor=pg["next_cursor"])
    assert len(pg["activities"]) == 3 and pg["next_cursor"] and not {a["id"] for a in pg["activities"]} & {a["id"] for a in pg2["activities"]}
    a1 = activity.activity_get(U, ids["a1"][1])
    assert a1["people"] == [{"id": P["P1"], "alias": a1["people"][0]["alias"], "role": "author"}] and a1["raw"]["local_only"]
    assert a1["raw_id"] == ids["a1"][0]["raw_id"] and "hmac" not in json.dumps(a1)
    sc = activity.source_context(U, a1["source"]["id"])
    assert sc["activity_count"] == 8 and sc["source"]["provider"] == "sns"
    ax = activity.axis_context(U, X1)
    assert ax["activity_count"] == 2
    expect("NOT-FOUND", activity.axis_context, U, ids["a1"][1])
    expect("NOT-FOUND", activity.activity_get, db.user_by_email(BI["b"])["id"], ids["a1"][1])
    # ── reference 새 대상(person·activity) + 근거 연결 2단계 + 사람 삭제 보호 ──
    rec = db.record_add(U, "합성 근거", "합성 근거 기록 본문")
    p = db.definition_propose(U, None, 0, {"key": "rules", "label": "기준", "semantic_kind": "principle", "fields": [
        {"key": "evid", "label": "근거 활동", "value_type": "reference", "target_kind": "activity", "multiple": True},
        {"key": "who", "label": "사람", "value_type": "reference", "target_kind": "person", "multiple": True},
        {"key": "recs", "label": "근거 기록", "value_type": "reference", "target_kind": "record", "multiple": True}]})
    od = db.definition_apply(U, p["object_id"], p["proposal_id"], 0)
    ip = db.item_propose(U, p["object_id"], od["revision"], None, 0, "R-1", "합성 기준", "", {}, [
        {"field_key": "evid", "target_kind": "activity", "target_id": ids["a8"][1]},   # 옛 판을 그대로 가리킨다
        {"field_key": "evid", "target_kind": "activity", "target_id": ids["a1"][1]},
        {"field_key": "who", "target_kind": "person", "target_id": P["P1"]},
        {"field_key": "recs", "target_kind": "record", "target_id": rec["id"]}])
    it = db.item_decide(U, ip["item_id"], 0, ip["proposal_id"], "apply")
    assert {(x["target_kind"], x["target_id"]) for x in it["links"]} >= {("activity", ids["a8"][1]), ("person", P["P1"])}
    bad = expect("LINK-INVALID", db.item_propose, U, p["object_id"], od["revision"], None, 0, "R-3", "x", "", {},
                 [{"field_key": "who", "target_kind": "person", "target_id": "p_notmine"}])
    assert "찾을 수 없어요" in bad.message
    db.item_decide(U, it["id"], it["revision"], None, "confirm")
    g = activity.me_aggregate(U)["grounds"]["principles"]
    assert g == [{"item_id": it["id"], "title": "합성 기준", "records": 1, "activities": 2}], g
    e = expect("PERSON-IN-USE", db.person_delete, U, P["P1"], 0)
    assert "활동" in e.message and "항목" in e.message
    pc = activity.person_context(U, P["P1"])
    assert pc["items"] and pc["items"][0]["id"] == it["id"] and pc["activity_count"] == 2
    lone = db.person_save(U, "연결 없음")
    assert db.person_delete(U, lone["id"], 0)["deleted"], "연결 없는 사람은 지운다"
    # ── 운영자 화면: 항목 → 사람·출처·활동 관계는 값도 개수도 없다(처리방침 v5 약속 유지) ──
    import admin_db
    v = admin_db.userview("admin", U)[U]
    assert _bi_count(U, "sources", "AND activity_provider IS NOT NULL") == 1 and v["sources_n"] == 0, "운영자 출처 수에 활동 출처 0"
    assert activity.person_context(U, P["P3"])["old_revision_links"] == 1 and activity.person_context(U, P["P1"])["old_revision_links"] == 0
    assert "옛 판 포함" in e.message, e.message
    assert v["links"] and all(ln["target_kind"] in ("item", "record", "candidate") for ln in v["links"]), "운영자 원행에 사람·출처·활동 연결 0"
    resp, st = web.uv_build(v)
    rels = contract.relations(admin_db.contract_input(v)["definitions"])
    priv = [i for i, r in enumerate(rels) if r["target_kind"] in admin_db.PRIVATE_TARGETS]
    assert len(priv) == 2 and all(st["linked"][i] is None for i in priv), (priv, st["linked"])
    assert not [e for e in st["errors"] + st["unchecked"] if e["rel"] in priv], "무결성 집계에서도 뺀다"
    page = web.render_userview({"email": "x@example.com", "created": db.now(), "role": "user", "blocked_at": None}, "op", False, resp, st, [])
    for i in priv:
        row_ = page[page.index(f'id="{web.structure_svg.rel_anchor(i)}"'):]
        row_ = row_[:row_.index("</tr>")]
        assert row_.endswith('<td class="num">—</td><td class="num">—</td><td class="num">—</td>'), row_[-200:]
    # ── MCP 읽기 7 — 웹과 같은 함수 · 쓰기 없음 · HMAC 없음 ──
    _as_user(main.site, BI["agg"])
    try:
        async with Client(main.mcp) as c:
            assert val(await c.call_tool("me_aggregate_get", {"period": "30d"})) == activity.me_aggregate(U)
            got = val(await c.call_tool("activity_list", {"dated": "no"}))
            assert {a["id"] for a in got["activities"]} == {ids["a4"][1], ids["a5"][1]}
            assert val(await c.call_tool("activity_get", {"activity_id": ids["a1"][1]})) == activity.activity_get(U, ids["a1"][1])
            assert val(await c.call_tool("person_context_get", {"person_id": P["P1"]}))["activity_count"] == 2
            assert val(await c.call_tool("source_context_get", {"source_id": a1["source"]["id"]}))["activity_count"] == 8
            assert val(await c.call_tool("inbox_list", {}))["entries"] == []
            res = await c.call_tool("inbox_get", {"import_id": "ie_none"})
            assert _prb_err(res) == "NOT-FOUND"
            for t in BI_TOOLS:
                assert t not in db.WRITE_LIMITS and t in main.ALLOWED_TOOLS
            blob = json.dumps(val(await c.call_tool("activity_list", {})), ensure_ascii=False)
            assert not any(h in blob for h in HP.values()) and "hmac" not in blob
    finally:
        os.environ["CRM_DEV_USER"] = "tester@example.com"
        main.site.dev, main.site.dev_user = True, "tester@example.com"
    print("OK — 25d 「나」 집계 손계산(30d 4·7d 3·90d/all 5·축 2/2+미분류 1·자주 반응 P1 2/P3 1·날짜 미확인 2+처음 수집 분포·취소·noop·옛 판)"
          " · 목록 거르기·쪽 · 문맥(사람·출처·축) · reference person·activity(특정 판) · 근거 2단계 · PERSON-IN-USE · MCP 읽기 7 통과")


def test_bi_migration():
    """25f board 이전 기본 연산(imports.migration_*) — 스위치 · 입력원 하나 · 항목 제안→적용·결정 · 기록 새 판(created 과거 보존·
    record_attach) · 같은 명세 재실행 업무 변경 0 · noop · 새 판 · 충돌(개정·멱등·대상 revision·보관) · 행 단위 원자성(한도·링크 오류)
    · 병행 기간 14일·7일 연장·종료 뒤 거절 · 감사."""
    M = db.user_by_email(BI["mig"])["id"]
    _, (X1,) = _bi_axes(M, 1)
    ax_obj = db.classify_target(M)
    p = db.definition_propose(M, None, 0, {"key": "rules", "label": "기준", "semantic_kind": "principle", "fields": [
        {"key": "axes", "label": "축", "value_type": "reference", "target_kind": "item", "target_object_id": ax_obj, "multiple": True}]})
    rules = db.definition_apply(M, p["object_id"], p["proposal_id"], 0)["id"]
    sha = "ab" * 32

    def row(o, v, prev=None, **kw):
        r = {"original_id": o, "revision": v, "previous_revision": prev}
        r.update(kw)
        return r

    def apply(r, batch="mb_bi1"):
        return imports.migration_apply_row(M, S, r, batch_id=batch, manifest_sha256=sha, transform_version="board-manifest/1")

    def counts():
        return {t: _bi_count(M, t) for t in ("records", "items", "item_history", "item_links", "import_entries")}

    with _bi_switch():
        expect("MIGRATION-OFF", imports.migration_apply_row, M, "is_x", {}, batch_id="b", manifest_sha256=sha, transform_version="t")
    assert imports.migration_stream_get(M) is None
    st = imports.migration_stream(M, "board 이전")
    S = st["id"]
    assert re.fullmatch(r"is_[0-9a-f]{24}", S) and st["migration_started_at"] is None and st["counts"] == {}
    assert imports.migration_stream(M, "다른 이름")["id"] == S and imports.migration_stream_get(M)["id"] == S, "사용자당 하나"
    expect("ARG-INVALID", imports.migration_extend, M, S, 7)   # 시작 전
    O = {k: _bi_o() for k in ("ax", "rule", "rec", "late")}
    V = {k: _bi_v() for k in ("ax", "rule", "rec", "rec2", "rule2", "rule3", "x")}
    with _bi_switch("BOARD_MIGRATION_ENABLED"):
        ra = row(O["ax"], V["ax"], target_kind="item", unit_kind="axis", object_id=ax_obj, code="AX-M", title="합성 이전 축")
        r1 = apply(ra)
        assert r1["state"] == "applied" and r1["target_kind"] == "item" and r1["duplicate"] is False
        st = imports.migration_stream_get(M)
        assert st["migration_started_at"] and st["migration_ends_at"]
        with db.tx() as c:
            w = c.execute("SELECT migration_ends_at - migration_started_at AS d FROM import_streams WHERE id=%s", (S,)).fetchone()["d"]
        assert w.days == 14, w
        rr = row(O["rule"], V["rule"], target_kind="item", unit_kind="principle_wd", object_id=rules, code="WD-1", title="합성 기준",
                 body="합성 본문", decision="confirmed", references=[{"field_key": "axes", "target": {"original_id": O["ax"], "revision": V["ax"]}}])
        r2 = apply(rr)
        it = db.item_get(M, r2["target_id"])
        assert it["decision"] == "confirmed" and [(x["target_kind"], x["target_id"]) for x in it["links"]] == [("item", r1["target_id"])]
        with db.tx() as c:
            ik = c.execute("SELECT ingest_kind, import_batch_id FROM items WHERE id=%s", (r2["target_id"],)).fetchone()
        assert dict(ik) == {"ingest_kind": "board_migration", "import_batch_id": "mb_bi1"}
        rc = row(O["rec"], V["rec"], target_kind="record", unit_kind="journal", title="합성 일지", body="합성 일지 본문이다.",
                 source_created_at="2026-09-01T09:30:00+09:00",
                 attach={"attach_to": [{"original_id": O["rule"], "revision": V["rule"]}], "axes": [{"original_id": O["ax"], "revision": V["ax"]}],
                         "use_at": ["합성 자리"]})
        r3 = apply(rc)
        rec = db.record_get(M, r3["target_id"])
        dt = __import__("datetime")
        assert (rec["author_kind"], rec["text_process"]) == ("self", "verbatim")
        assert dt.datetime.fromisoformat(rec["created"]) == dt.datetime(2026, 9, 1, 0, 30, tzinfo=dt.timezone.utc), "확인된 원본 시각 보존"
        with db.tx() as c:
            rk = c.execute("SELECT ingest_kind, received_at > now() - interval '1 hour' AS today, import_batch_id "
                           "FROM records WHERE id=%s", (r3["target_id"],)).fetchone()
        assert rk["ingest_kind"] == "board_migration" and rk["today"] and rk["import_batch_id"] == "mb_bi1"
        assert {(x["field_key"], x["target_id"]) for x in db.record_links(M, [r3["target_id"]])} == {("attach_to", r2["target_id"]),
                                                                                                      ("axes", r1["target_id"])}
        # ── 같은 명세 재실행 = duplicate · 업무 변경 0 ──
        before = counts()
        for r in (ra, rr, rc):
            assert apply(r)["duplicate"] is True
        assert counts() == before
        expect("IDEMPOTENCY-CONFLICT", apply, {**rc, "body": "다른 본문"})
        expect("SOURCE-REVISION-CONFLICT", apply, row(O["rec"], _bi_v(), prev=_bi_v(), target_kind="record", unit_kind="journal", body="x"))
        expect("SOURCE-REVISION-CONFLICT", apply, row(_bi_o(), _bi_v(), prev=_bi_v(), target_kind="record", unit_kind="journal", body="x"))
        expect("KIND-CHANGED", apply, row(O["rec"], _bi_v(), prev=V["rec"], target_kind="item", unit_kind="axis", object_id=ax_obj, title="x"))
        expect("REF-NOT-FOUND", apply, row(O["late"], _bi_v(), target_kind="item", unit_kind="principle_wd", object_id=rules, title="x",
                                           references=[{"field_key": "axes", "target": {"original_id": _bi_o(), "revision": _bi_v()}}]))
        expect("ARG-INVALID", apply, row(O["late"], _bi_v(), target_kind="item", unit_kind="principle_wd", object_id=rules, title="x",
                                         references=[{"field_key": "axes", "target": {"original_id": O["ax"]}}]))   # 정확한 판(revision) 필수
        expect("REF-NOT-FOUND", apply, row(O["late"], _bi_v(), target_kind="item", unit_kind="principle_wd", object_id=rules, title="x",
                                           references=[{"field_key": "axes", "target": {"original_id": O["ax"], "revision": _bi_v()}}]))
        assert counts() == before, "거절 = 쓰기 0"
        # ── noop · 기록 새 판 ──
        assert apply({**rc, "revision": V["rec2"], "previous_revision": V["rec"]})["state"] == "noop"
        r5 = apply({**rc, "revision": _bi_v(), "previous_revision": V["rec2"], "body": "고친 합성 일지 본문이다."})
        assert r5["state"] == "applied" and r5["target_id"] != r3["target_id"] and db.record_get(M, r3["target_id"])["body"] == rc["body"]
        # ── 항목 새 판 · 대상 충돌(서버에서 바뀜·보관) ──
        r6 = apply({**rr, "revision": V["rule2"], "previous_revision": V["rule"], "title": "고친 합성 기준"})
        assert r6["state"] == "applied" and r6["target_id"] == r2["target_id"] and db.item_get(M, r2["target_id"])["title"] == "고친 합성 기준"
        cur = db.item_get(M, r2["target_id"])
        db.item_decide(M, r2["target_id"], cur["revision"], None, "pending")   # 사용자가 서버에서 바꿈
        before = counts()
        expect("TARGET-REVISION-CONFLICT", apply, {**rr, "revision": V["rule3"], "previous_revision": V["rule2"], "title": "또 고침"})
        assert counts() == before, "충돌 = 전체 롤백"
        # ── 행 단위 원자성: 기록을 넣은 뒤 「어디에 붙나」 에서 실패해도 기록·대응표가 안 남는다 ──
        before = counts()
        expect("LINK-INVALID", apply, row(O["late"], _bi_v(), target_kind="record", unit_kind="journal", body="붙일 곳이 없는 합성 기록",
                                          attach={"attach_to": [{"item_id": "i_nothere"}]}))
        assert counts() == before, "기록 INSERT 뒤 실패 = 전체 롤백"
        expect("LINK-INVALID", apply, row(O["late"], _bi_v(), target_kind="item", unit_kind="principle_wd", object_id=rules, title="합성",
                                          references=[{"field_key": "axes", "target": {"item_id": "i_nothere"}}]))
        assert counts() == before
        _bulk("INSERT INTO records (id,user_id,title,body) SELECT 'r_bimig' || g, %s, 't', 'b' FROM generate_series(1, 50) AS g", (M,))
        e = expect("RATE-LIMIT", apply, row(O["late"], _bi_v(), target_kind="record", unit_kind="journal", body="한도 넘는 합성 기록"))
        assert e.resume_at == imports.next_kst_midnight() and "합성" not in e.message + e.action, "재개 시각만, 값 없음"
        assert _bi_count(M, "import_entries", "AND original_id=%s", (O["late"],)) == 0, "한도 = 미처리 행으로 남는다"
        _bulk("DELETE FROM records WHERE user_id=%s AND id LIKE 'r_bimig%%'", (M,))
        # ── 대응표 · 병행 기간 ──
        ents = imports.migration_entries(M, S)
        assert {e["original_id"] for e in ents} == {O["ax"], O["rule"], O["rec"]} and all("payload" not in e for e in ents)
        cur = {e["original_id"]: e for e in ents if e["is_current_applied"]}
        assert cur[O["rec"]]["target_id"] == r5["target_id"] and cur[O["rule"]]["target_item_revision"] == db.item_get(M, r2["target_id"])["revision"] - 1
        expect("ARG-INVALID", imports.migration_extend, M, S, 5)
        e1 = imports.migration_stream_get(M)["migration_ends_at"]
        e2 = imports.migration_extend(M, S, 7)["migration_ends_at"]
        assert (__import__("datetime").datetime.fromisoformat(e2) - __import__("datetime").datetime.fromisoformat(e1)).days == 7
        _sql("UPDATE import_streams SET migration_started_at = now() - interval '22 days', "
             "migration_ends_at = now() - interval '1 second' WHERE id=%s", (S,))
        expect("MIGRATION-WINDOW-CLOSED", apply, row(O["late"], _bi_v(), target_kind="record", unit_kind="journal", body="기간 뒤"))
        assert apply(ra)["duplicate"] is True, "같은 요청 재전송은 기간 뒤에도 결과만 돌려준다(쓰기 0)"
        imports.migration_extend(M, S, 7)
        cl = imports.migration_close(M, S, "cd" * 32)
        assert cl["migration_closed_at"] and cl["final_manifest_sha256"] == "cd" * 32
        expect("MIGRATION-WINDOW-CLOSED", apply, row(O["late"], _bi_v(), target_kind="record", unit_kind="journal", body="종료 뒤"))
        expect("MIGRATION-WINDOW-CLOSED", imports.migration_extend, M, S, 7)
        expect("MIGRATION-WINDOW-CLOSED", imports.migration_close, M, S, "cd" * 32)
    with db.tx() as c:
        aud = [(r["action"], r["detail"]) for r in c.execute("SELECT action, detail FROM admin_audit WHERE actor_id=%s AND action LIKE 'migration_%%' "
                                                              "ORDER BY id", (M,)).fetchall()]
    assert [a for a, _ in aud] == ["migration_apply", "migration_extend", "migration_extend", "migration_close"], aud
    assert all("본문" not in json.dumps(d, ensure_ascii=False) for _, d in aud)
    print("OK — 25f board 이전 기본 연산(스위치 · 입력원 하나 · 항목 제안→적용·결정·참조 해석 · 기록 새 판·created 과거 보존·record_attach"
          " · 재실행 업무 변경 0 · noop · 충돌 4종 · 행 원자성(대상 충돌·링크 오류·기록 한도) · 14일·7일 연장·종료 · 감사) 통과")


async def test_bi_delete():
    """25e 계정 삭제 — 장치·활동·관계·접수 대응표·한도·대기가 있는 계정을 삭제 CLI 로 지운다(FK 실패 0, 남는 행 0)
    · 삭제된 계정의 토큰은 거절 · user_delete(시험 정리 경로)도 같은 순서."""
    ADM = db.user_by_email(BI["adm"])["id"]
    _set_role(ADM, "admin")
    D = db.user_by_email(BI["del"])["id"]
    _, (X1,) = _bi_axes(D, 1)
    cd = imports.device_connect(D, "합성 삭제")
    tok, DEV = cd["token"], cd["device"]["id"]
    imports.device_policy_set(D, DEV, {"actions": ["like"], "transform_versions": ["board-likes/1", "self-text/1"], "axes": [X1]}, 1)

    async def fill():
        with _bi_switch("IMPORTS_ENABLED", "ACTIVITY_IMPORTS_ENABLED", "INBOX_APPROVALS_ENABLED"), _bi_fast():
            r = await _bi_post(tok, _bi_act(DEV, policy_revision=2, people=[{"hmac": secrets.token_hex(32), "role": "author"}], axes=[X1]))
            aid = r.json()["target_id"]
            e = (await _bi_post(tok, _bi_text(DEV, policy_revision=2, activity_ref=aid))).json()["import_id"]
            imports.inbox_approve(D, e)
            await _bi_post(tok, _bi_text(DEV, policy_revision=2))
            return aid
    aid = await fill()
    p = db.definition_propose(D, None, 0, {"key": "notes", "label": "노트", "semantic_kind": "collection", "fields": [
        {"key": "act", "label": "활동", "value_type": "reference", "target_kind": "activity"}]})
    od = db.definition_apply(D, p["object_id"], p["proposal_id"], 0)
    ip = db.item_propose(D, p["object_id"], od["revision"], None, 0, None, "합성", "", {}, [{"field_key": "act", "target_kind": "activity",
                                                                                         "target_id": aid}])
    db.item_decide(D, ip["item_id"], 0, ip["proposal_id"], "apply")
    before = {t: _bi_count(D, t) for t in BI_TABLES}
    assert all(before.values()), before
    tk = _tk_insert(D, "privacy", "합성 삭제 요청")
    db.delete_notify(ADM, tk)
    _sql("UPDATE deletion_contacts SET notified_at = now() - interval '8 days', delete_after = now() - interval '1 day' WHERE user_id=%s", (D,))
    pv = db.delete_preview(ADM, tk)
    assert pv["ready"] and all(pv["counts"][t] == before[t] for t in BI_TABLES), pv["counts"]
    out = db.delete_run(ADM, tk, pv["value"])
    assert out["deleted"] == pv["total"]
    assert {t: _bi_count(D, t) for t in db.DELETE_TABLES if t != "tickets"} == {t: 0 for t in db.DELETE_TABLES if t != "tickets"}
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM import_streams WHERE user_id=%s", (D,)).fetchone()["n"] == 0
    e = expect("AUTH-INVALID", imports.authenticate, "Bearer " + tok)
    assert e.message == "장치 토큰이 맞지 않아요."
    db.user_delete(D)   # 시험 정리(이메일이 자리값이라 cleanup 이 못 찾는다) — 남은 users 행·보존 문의
    _sql("DELETE FROM deletion_contacts WHERE user_id=%s", (D,))
    _sql("DELETE FROM tickets WHERE user_id=%s", (D,))
    # user_delete 경로(시험 정리·α2)도 새 표가 있는 계정을 끝까지 지운다
    E = db.user_by_email(BI["b"])["id"]
    ce = imports.device_connect(E, "합성 정리")
    with _bi_switch("IMPORTS_ENABLED", "ACTIVITY_IMPORTS_ENABLED"):
        r = await _bi_post(ce["token"], _bi_act(ce["device"]["id"], people=[{"hmac": secrets.token_hex(32), "role": "author"}]))
        assert r.status_code == 201
    db.user_delete(E)
    assert all(_bi_count(E, t) == 0 for t in BI_TABLES)
    print("OK — 25e 계정 삭제(장치·활동·관계·대응표·한도·대기·활동 reference 가 있는 계정 → 삭제 CLI 남는 행 0·FK 실패 0 · 토큰 거절"
          " · user_delete 경로) 통과")


# ── 26) PR ①-b: 페이지 레이아웃 · 「나」 통계 · 기록 정렬 키 (2026-10-22, 구현계획 §3) ──────────
# 사용자 = tester@·other@example.com 재사용(TEST_EMAILS 불변). run() 맨 끝에서 돌고 시작 때 두 계정을 새로 만든다.

def _ux_rec(c, uid, rid, created, source_date=None, source_created_at=None, deleted=False):
    c.execute("INSERT INTO records (id,user_id,title,body,created,author_kind,text_process,received_at,source_date,source_created_at,"
              "deleted_at) VALUES (%s,%s,%s,%s,%s,'self','verbatim',%s,%s,%s,%s)",
              (rid, uid, f"합성 {rid}", f"합성 본문 {rid}", created, created, source_date, source_created_at, created if deleted else None))


def _ux_link(c, uid, axis, record=None, item=None, obj=None, field="axes"):
    if record:
        c.execute("INSERT INTO item_links (user_id, source_record_id, field_key, target_item_id) VALUES (%s,%s,'axes',%s)",
                  (uid, record, axis))
    else:
        c.execute("INSERT INTO item_links (user_id, source_item_id, source_object_id, field_key, target_item_id) VALUES (%s,%s,%s,%s,%s)",
                  (uid, item, obj, field, axis))


def _ux_obj(uid, key, kind, fields=()):
    p = db.definition_propose(uid, None, 0, {"key": key, "label": key, "semantic_kind": kind, "fields": list(fields)})
    db.definition_apply(uid, p["object_id"], p["proposal_id"], 0)
    return p["object_id"]


def _ux_item(uid, oid, code, title):
    ev = db.item_propose(uid, oid, 1, None, 0, code, title, "", {}, [])
    return db.item_decide(uid, ev["item_id"], 0, ev["proposal_id"], "apply")["id"]


async def test_pr1b_layout_stats():
    """레이아웃: GET 기본+0 · PUT v0→1·STALE 409·연속·INVALID 400 9가지·PAYLOAD-LIMIT·401·Origin 403 · 사용자 격리 · DELETE 복원(버전 되감기 없음 → 옛 탭 STALE)
    · 행 없을 때 DELETE 쓰기 0 · 기본 상수 불변 · 계정 삭제. 통계: 71편(> journal 50) 전체 범위 · 축별 고유 합(두 축 글 각 1·중복 링크·지운 기록·보관 항목·자기 자신 제외)
    · 활동은 축 건수 밖 · 히트맵 91칸 월~일·source_date·source_created_at(KST)·날짜 없는 기록·창 밖 제외·활동 occurred_on · kinds·waiting · 남 0
    · 쓰기 0(분류 역할 이관 INSERT 없음) · 웹 = db. 정렬: COALESCE(source_date, kst(source_created_at), kst(created)) DESC → created → id
    · KST 고정(세션 UTC 여도) · journal 3열 · /api/records 커서 꼴 created|id 유지·이어 받기 겹침 0·기준 행을 지워도 이어짐."""
    import datetime as dt
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    O = db.user_by_email("other@example.com")["id"]
    site = main.site
    real = site.dev, site.dev_user
    origin = {"origin": PUBLIC}
    D0 = db.DEFAULT_LAYOUT
    snap = json.dumps(D0, sort_keys=True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # ── 레이아웃 ──
            site.dev, site.dev_user = False, ""
            assert (await h.get("/api/me/layout")).status_code == 401
            assert (await h.put("/api/me/layout", json={"layout": D0, "version": 0}, headers=origin)).status_code == 401
            assert (await h.delete("/api/me/layout", headers=origin)).status_code == 401
            assert (await h.get("/api/me/stats")).status_code == 401
            site.dev, site.dev_user = True, "tester@example.com"
            r = await h.get("/api/me/layout")
            assert r.status_code == 200 and r.headers.get("cache-control") == "no-store" and r.json() == {"layout": D0, "version": 0}
            r.json()["layout"]["tabs"].reverse()
            lay = {"v": 1, "tabs": ["person", "summary", "principle", "decision", "axis", "custom", "source"],
                   "blocks": {"summary": ["recent", "heatmap", "kinds", "waiting"]}}
            assert (await h.put("/api/me/layout", json={"layout": lay, "version": 0}, headers={"origin": "https://evil.example"})).status_code == 403
            assert (await h.delete("/api/me/layout", headers={"origin": "https://evil.example"})).status_code == 403
            r = await h.put("/api/me/layout", json={"layout": lay, "version": 0}, headers=origin)
            assert r.status_code == 200 and r.json() == {"layout": lay, "version": 1}, r.text
            assert (await h.get("/api/me/layout")).json() == {"layout": lay, "version": 1}
            r = await h.put("/api/me/layout", json={"layout": D0, "version": 0}, headers=origin)
            assert r.status_code == 409 and r.json()["code"] == "LAYOUT-STALE", r.text
            lay2 = {**lay, "blocks": {"summary": ["heatmap", "kinds"]}}   # 블록 일부만도 허용(알려진 이름·중복 없음)
            assert (await h.put("/api/me/layout", json={"layout": lay2, "version": 1}, headers=origin)).json()["version"] == 2
            r = await h.put("/api/me/layout", json={"layout": lay, "version": 1}, headers=origin)
            assert r.status_code == 409 and r.json()["code"] == "LAYOUT-STALE", "옛 버전(두 탭) = STALE"
            t7 = D0["tabs"]
            bad = [{**lay, "tabs": t7[:6]}, {**lay, "tabs": t7[:6] + [t7[0]]}, {**lay, "tabs": t7 + ["inbox"]},
                   {**lay, "tabs": t7[:6] + ["inbox"]}, {**lay, "blocks": {"summary": ["heatmap", "pie"]}},
                   {**lay, "blocks": {"summary": ["heatmap", "heatmap"]}}, {**lay, "blocks": {"today": ["heatmap"]}},
                   {**lay, "extra": 1}, {**lay, "v": 2}, {**lay, "blocks": []}, {"v": 1, "tabs": t7}]
            for b in bad:
                r = await h.put("/api/me/layout", json={"layout": b, "version": 2}, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "LAYOUT-INVALID", (b, r.text)
            for body in ({"layout": "x", "version": 2}, {"layout": lay, "version": "2"}, {"layout": lay}, {"layout": lay, "version": True}):
                r = await h.put("/api/me/layout", json=body, headers=origin)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (body, r.text)
            r = await h.put("/api/me/layout", json={"layout": {**lay, "pad": "가" * 2000}, "version": 2}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT", r.text
            r = await h.put("/api/me/layout", json={"layout": lay, "version": -1}, headers=origin)
            assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
            assert (await h.get("/api/me/layout")).json() == {"layout": lay2, "version": 2}, "거절은 저장하지 않는다"
            # 격리 — 남은 기본 + 0, 남의 저장이 내 행을 안 바꾼다
            site.dev_user = "other@example.com"
            assert (await h.get("/api/me/layout")).json() == {"layout": D0, "version": 0}
            r = await h.delete("/api/me/layout", headers=origin)
            assert r.status_code == 200 and r.json() == {"layout": D0, "version": 0}
            with db.tx() as c:
                assert c.execute("SELECT count(*) AS n FROM page_layouts WHERE user_id=%s", (O,)).fetchone()["n"] == 0, "행 없을 때 복원 = 쓰기 0"
            assert (await h.put("/api/me/layout", json={"layout": lay, "version": 0}, headers=origin)).json()["version"] == 1
            site.dev_user = "tester@example.com"
            assert (await h.get("/api/me/layout")).json() == {"layout": lay2, "version": 2}
            # 복원 — 기본 + version 3(행 유지). 복원 전 버전(2)으로 PUT 하면 STALE, 3 이면 저장
            r = await h.delete("/api/me/layout", headers=origin)
            assert r.status_code == 200 and r.json() == {"layout": D0, "version": 3}, r.text
            assert (await h.get("/api/me/layout")).json() == {"layout": D0, "version": 3}
            r = await h.put("/api/me/layout", json={"layout": lay, "version": 2}, headers=origin)
            assert r.status_code == 409 and r.json()["code"] == "LAYOUT-STALE", "복원 뒤 옛 탭 = STALE(버전 되감기 없음)"
            assert (await h.put("/api/me/layout", json={"layout": lay, "version": 3}, headers=origin)).json() == {"layout": lay, "version": 4}
            assert json.dumps(db.DEFAULT_LAYOUT, sort_keys=True) == snap, "기본 상수 불변(응답 사본을 바꿔도)"
            expect("LAYOUT-INVALID", db.layout_check, None)
            assert db.layout_check(D0) == D0

            # ── 통계·정렬 데이터 ──
            AXO, (X0, X1, X2) = _bi_axes(U, 3)
            p = db.definition_propose(U, AXO, 1, {"key": "axes", "label": "축", "semantic_kind": "collection", "fields": [
                {"key": "rel", "label": "관련 축", "value_type": "reference", "target_kind": "item", "target_object_id": AXO, "multiple": True}]})
            db.definition_apply(U, AXO, p["proposal_id"], 1)
            PO = _ux_obj(U, "principles", "principle", [{"key": "axes", "label": "관심 축", "value_type": "reference", "target_kind": "item",
                                                          "target_object_id": AXO, "multiple": True}])
            EO, CO = _ux_obj(U, "decision_events", "event"), _ux_obj(U, "notes_ux", "collection")
            PI1, PI2, PI3 = (_ux_item(U, PO, f"PR-{i}", f"합성 기준 {i}") for i in range(3))
            db.item_decide(U, PI2, 1, None, "confirm")
            db.item_decide(U, PI3, 1, None, "archive")
            _ux_item(U, EO, "EV-0", "합성 결정 0"), _ux_item(U, EO, "EV-1", "합성 결정 1"), _ux_item(U, CO, "NT-0", "합성 메모")
            with db.tx() as c:
                T = c.execute(f"SELECT {activity._TODAY} AS d").fetchone()["d"]
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            tag = secrets.token_hex(3)
            rid = [f"r_ux{tag}{i:03d}" for i in range(72)]
            utc = dt.timezone.utc
            with db.tx() as c:
                for i in range(72):
                    sd = sca = None
                    created = now - dt.timedelta(minutes=i)
                    if i < 20:
                        sd = T - dt.timedelta(days=i % 5)
                    elif i < 30:
                        sca = dt.datetime.combine(T - dt.timedelta(days=7), dt.time(20, 0), utc)   # KST = T-6 05:00
                    elif i < 35:
                        sd = T - dt.timedelta(days=200)   # 창 밖
                    elif i == 35:
                        created = dt.datetime.combine(T - dt.timedelta(days=1), dt.time(15, 30), utc)   # UTC 어제 · KST 오늘 00:30
                    if i == 71:
                        sd = T
                    _ux_rec(c, U, rid[i], created, sd, sca, deleted=(i == 71))
                for i in range(60):
                    _ux_link(c, U, X0, record=rid[i])
                for i in range(10):
                    _ux_link(c, U, X1, record=rid[i])   # 한 글 두 축 = 각 축 1
                _ux_link(c, U, X0, record=rid[0])   # 중복 링크 = 1
                _ux_link(c, U, X0, record=rid[71])   # 지운 기록 = 0
                for it in (PI1, PI1, PI2, PI3):   # PI1 중복 · PI3 보관 = 0
                    _ux_link(c, U, X0, item=it, obj=PO)
                _ux_link(c, U, X0, item=X0, obj=AXO, field="rel")   # 자기 자신 = 0
                _ux_link(c, U, X0, item=X1, obj=AXO, field="rel")   # 다른 축 항목 = 1
                b0 = "합성 본문 " + rid[0]
                for cid, rr, dec in ((f"c_ux{tag}a", rid[0], "pending"), (f"c_ux{tag}b", rid[71], "pending"), (f"c_ux{tag}c", rid[1], "confirmed")):
                    c.execute("INSERT INTO candidates (id,user_id,record_id,text,quote,quote_start,quote_end,origin,decision) "
                              "VALUES (%s,%s,%s,'합성 후보',%s,0,2,'ai',%s)", (cid, U, rr, b0[:2] if rr == rid[0] else ("합성 본문 " + rr)[:2], dec))
            # 활동 — 축 X0 에 붙어도 축 건수 밖. 히트맵은 occurred_on 만
            c1 = imports.device_connect(U, "합성 UX")
            tok, Dv = c1["token"], c1["device"]["id"]
            imports.device_policy_set(U, Dv, {"actions": ["like"], "transform_versions": ["board-likes/1"], "axes": [X0]}, 1)
            with _bi_switch("IMPORTS_ENABLED", "ACTIVITY_IMPORTS_ENABLED"), _bi_fast():
                for occ, axes in ((T, [X0]), (T, []), (T - dt.timedelta(days=1), []), (None, [X0])):
                    rr = await _bi_post(tok, _bi_act(Dv, policy_revision=2, occurred_on=occ.isoformat() if occ else None, axes=axes))
                    assert rr.status_code == 201, rr.text

            # ── 통계 ──
            s = db.me_stats(U)
            assert set(s) == {"axes", "heatmap", "kinds", "waiting"}
            assert [(x["id"], x["code"]) for x in s["axes"]] == [(X0, "AX-0"), (X1, "AX-1"), (X2, "AX-2")], s["axes"]
            ax = {x["id"]: (x["items"], x["records"], x["readings"], x["n"]) for x in s["axes"]}
            assert ax == {X0: (3, 60, 0, 63), X1: (0, 10, 0, 10), X2: (0, 0, 0, 0)}, ax
            assert all(set(x) == {"id", "code", "title", "items", "records", "readings", "n"} for x in s["axes"])
            hm = s["heatmap"]
            cells = hm["cells"]
            to, frm = dt.date.fromisoformat(hm["to"]), dt.date.fromisoformat(hm["from"])
            assert len(cells) == 91 and to.isoweekday() == 7 and frm.isoweekday() == 1 and (to - frm).days == 90 and frm <= T <= to
            assert [x["d"] for x in cells] == [(frm + dt.timedelta(days=i)).isoformat() for i in range(91)]
            got = {x["d"]: (x["records"], x["activities"]) for x in cells if x["records"] or x["activities"]}
            want = {(T - dt.timedelta(days=k)).isoformat(): (4, 0) for k in range(5)}
            want[(T - dt.timedelta(days=6)).isoformat()] = (10, 0)
            want[T.isoformat()] = (4, 2)
            want[(T - dt.timedelta(days=1)).isoformat()] = (4, 1)
            assert got == want, (got, want)
            assert all(x["records"] == 0 and x["activities"] == 0 for x in cells if dt.date.fromisoformat(x["d"]) > T), "미래 칸 0"
            with db.tx() as c:
                n_src = c.execute("SELECT count(*) AS n FROM sources WHERE user_id=%s", (U,)).fetchone()["n"]
                n_ppl = c.execute("SELECT count(*) AS n FROM people WHERE user_id=%s", (U,)).fetchone()["n"]
            assert s["kinds"] == {"principle": 2, "decision": 2, "axis": 3, "custom": 1, "records": 71, "readings": 0,
                                  "people": n_ppl, "sources": n_src}, s["kinds"]
            assert s["waiting"] == {"candidates_pending": 1, "principles_pending": 1, "inbox_pending": imports.inbox_count(U)}, s["waiting"]
            st = (await h.get("/api/state")).json()
            assert len(st["journal"]) == 50 and s["kinds"]["records"] == 71 and ax[X0][1] == 60, "통계 = 전체 범위(journal 50 과 무관)"
            r = await h.get("/api/me/stats")
            assert r.status_code == 200 and r.headers.get("cache-control") == "no-store" and r.json() == json.loads(json.dumps(s)), "웹 = db"
            # 남 0 · 쓰기 0(축 오브젝트는 있으나 분류 역할 행이 없는 사용자 — 이관 INSERT 를 하지 않는다)
            so = db.me_stats(O)
            assert so["axes"] == [] and not any(so["kinds"].values()) and not any(so["waiting"].values())
            assert not any(x["records"] or x["activities"] for x in so["heatmap"]["cells"])
            OAX = _ux_obj(O, "axes", "collection")
            OX = _ux_item(O, OAX, "AX-0", "남의 축")
            assert [x["id"] for x in db.me_stats(O)["axes"]] == [OX] and db.me_stats(O)["kinds"]["axis"] == 1
            with db.tx() as c:
                assert c.execute("SELECT count(*) AS n FROM user_roles WHERE user_id=%s", (O,)).fetchone()["n"] == 0, "통계는 역할 행을 안 쓴다"
            site.dev_user = "other@example.com"
            assert [x["id"] for x in (await h.get("/api/me/stats")).json()["axes"]] == [OX]
            site.dev_user = "tester@example.com"

            # ── 정렬 키 ──
            with db.tx() as c:
                rows = c.execute("SELECT id, created, source_date, source_created_at FROM records WHERE user_id=%s AND deleted_at IS NULL",
                                 (U,)).fetchall()
            kst = dt.timezone(dt.timedelta(hours=9))
            sk = lambda r: (r["source_date"] or (r["source_created_at"].astimezone(kst).date() if r["source_created_at"] else None)   # noqa: E731
                            or r["created"].astimezone(kst).date(), r["created"], r["id"])
            order = [r["id"] for r in sorted(rows, key=sk, reverse=True)]
            assert [x["id"] for x in st["journal"]] == order[:50], "journal = 정렬 키 순 50"
            assert all({"source_date", "ingest_kind", "generation_kind"} <= set(x) for x in st["journal"])
            j = {x["id"]: x for x in st["journal"]}
            assert j[rid[0]]["source_date"] == T.isoformat() and j[rid[0]]["ingest_kind"] == "interactive" and j[rid[0]]["generation_kind"] == "unknown"
            with db.tx() as c:   # KST 고정 — 세션 시간대가 UTC 여도 UTC 어제 15:30 = KST 오늘
                c.execute("SET LOCAL TimeZone = 'UTC'")
                assert c.execute(f"SELECT {db.RECORD_SORT} AS d FROM records WHERE id=%s", (rid[35],)).fetchone()["d"] == T
                assert c.execute(f"SELECT {db.RECORD_SORT} AS d FROM records WHERE id=%s", (rid[20],)).fetchone()["d"] == T - dt.timedelta(days=6)
            seen, nxt, pages = [], None, 0
            while True:
                q = {"limit": 20, **({"before": nxt} if nxt else {})}
                pg = (await h.get("/api/records", params=q)).json()
                seen += [x["id"] for x in pg["records"]]
                pages += 1
                if not pg["next_before"]:
                    break
                last = pg["records"][-1]
                assert pg["next_before"] == f"{last['created']}|{last['id']}", "커서 꼴 created|id 유지(PR① 계약)"
                nxt = pg["next_before"]
            assert seen == order and len(set(seen)) == 71 and pages == 4, "이어 받기 = 정렬 키 순 전체, 겹침·누락 0"
            assert db.records(U, 71) == db.records(U, 71, None, None) and [x["id"] for x in db.records(U, 71)] == order
            # 기준 행(1쪽 마지막)을 지워도 2쪽이 그대로 — 지운 행도 남아 정렬 날짜를 복원한다
            p1 = (await h.get("/api/records", params={"limit": 20})).json()
            with db.tx() as c:
                c.execute("UPDATE records SET deleted_at=now() WHERE user_id=%s AND id=%s", (U, p1["records"][-1]["id"]))
            p2 = (await h.get("/api/records", params={"limit": 20, "before": p1["next_before"]})).json()
            assert [x["id"] for x in p2["records"]] == order[20:40]
            # 없는 id 커서 = kst(created) 로 대체(PR① 시험 133 과 같은 꼴) · 남의 id 커서도 남의 행을 읽지 않는다
            assert (await h.get("/api/records", params={"before": "2000-01-01T00:00:00+00:00|r_zzz"})).json() == {"records": [], "next_before": None}
        # 계정 삭제가 레이아웃 행도 지운다
        db.user_delete(O)
        with db.tx() as c:
            assert c.execute("SELECT count(*) AS n FROM page_layouts WHERE user_id=%s", (O,)).fetchone()["n"] == 0
    finally:
        site.dev, site.dev_user = real
    print("OK — PR ①-b 레이아웃(GET·PUT·DELETE·STALE·INVALID·격리·복원) · 통계(축 고유 합·히트맵 91칸·kinds·waiting·쓰기 0) · 정렬 키(KST·커서 유지) 통과")

# ── 27) PR ②-a: 작업 전달(세션/작업·임대·롱폴·임시 보관 tmp) (2026-10-22, 구현계획 §5·G3) ──────────
# 사용자 = tester@·other@example.com 재사용(TEST_EMAILS 불변). run() 맨 끝(26절 뒤)에서 돌고 시작 때 두 계정을 새로 만든다.
JOB_SWITCH_NAMES = ("CONVERSE_ENABLED", "ROUTINE_ENABLED")


@contextmanager
def _job_switch(*on):
    """작업 스위치 중 주어진 것만 켜고(나머지 끔) 끝나면 원래대로. 시험 프로세스 env 만 — 운영 env 는 건드리지 않는다."""
    saved = {n: os.environ.pop("CRM_" + n, None) for n in JOB_SWITCH_NAMES}
    for n in on:
        os.environ["CRM_" + n] = "1"
    try:
        yield
    finally:
        for n in JOB_SWITCH_NAMES:
            os.environ.pop("CRM_" + n, None)
            if saved[n] is not None:
                os.environ["CRM_" + n] = saved[n]


@contextmanager
def _job_fast():
    """폴링 버킷을 시험 동안만 넓힌다(흐름 시험이 1분에 수십 번 확인한다). 한도 자체는 끝 절에서 원래 값으로 본다."""
    saved = dict(imports.POLL_LIMITS)
    for k in imports.POLL_LIMITS:
        imports.POLL_LIMITS[k] = 100000
    try:
        with _bi_fast():
            yield
    finally:
        imports.POLL_LIMITS.clear()
        imports.POLL_LIMITS.update(saved)


def _job_rows(uid):
    with db.tx() as c:
        inp, res = db._tmp_names(c)
        jobs = [r["j"] for r in c.execute("SELECT row_to_json(j)::text AS j FROM jobs j WHERE user_id=%s", (uid,)).fetchall()]
        ti = c.execute(f"SELECT count(*) AS n FROM {inp} WHERE user_id=%s", (uid,)).fetchone()["n"]
        tr = c.execute(f"SELECT count(*) AS n FROM {res} WHERE user_id=%s", (uid,)).fetchone()["n"]
    return jobs, ti, tr


def _job_status(jid):
    with db.tx() as c:
        return c.execute("SELECT status, attempts, reason_code FROM jobs WHERE id=%s", (jid,)).fetchone()


async def test_pr2a_jobs():
    """스위치 꺼짐(접수 503·할당 0·수신 503·확정 503·취소는 됨) · 접수(201·401·Origin 403·8000자·LIMIT-UNSET fail-closed·세션 404·일일 한도)
    · 일반 테이블 본문 0 · 격리(남 404·남 장치 410·남 장치 폴 204) · 원자 할당(둘째 장치 204) · 세대·장치 409 · input attempts +1·running
    · result JOB-VERSION·JOB-LEASE·모양 400·같은 버전 200 duplicate · fail 키·코드 400·failed·본문 삭제 · 같은 세션 직렬화
    · 임대 만료 409 → needs_check·본문 삭제 · 취소 멱등·410 · 24h 만료(할당 0·expired·본문 0·410) · 폴링 버킷 장치 4·사용자 12 → 429 Retry-After
    · 동시 롱폴 장치 1·웹 2 · 종료 표식 30일 파기 · 계정 삭제 jobs·tmp 0 · tmp 위치(public = tmp 스키마·FK · 임시 스키마 = 그 안, DROP 뒤 잔여 0)."""
    import uuid
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    O = db.user_by_email("other@example.com")["id"]
    site = main.site
    real = site.dev, site.dev_user
    origin = {"origin": PUBLIC}
    TXT = "합성 입력 JOBTEXT 본문 — 일반 테이블에 남으면 안 된다"
    res_body = lambda gen, iv=1, rv=None: {"lease_generation": gen, "input_version": iv, "result_version": rv or str(uuid.uuid4()),   # noqa: E731
                                           "summary_text": "합성 정리", "changes": [], "blocked": [], "text_map": [], "model_meta": {"tool": "fake"}}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = True, "tester@example.com"
            poll = lambda tk, w=0: h.get("/api/device/jobs", params={"wait": w}, headers=tk)   # noqa: E731
            dj = lambda jid, op, tk, **kw: (h.get(f"/api/device/jobs/{jid}/{op}", headers=tk, **kw) if op == "input"   # noqa: E731
                                            else h.post(f"/api/device/jobs/{jid}/{op}", headers=tk, **kw))
            new = lambda kind="converse", **kw: h.post("/api/jobs", json={"kind": kind, "text": TXT, **kw}, headers=origin)   # noqa: E731
            with _job_fast():
                # ── 연결 장치 0 = 접수 거절(409 NO-DEVICE, 작업 행·임시 입력 0) · 연결 해제 장치만 = 0 과 같다 · 스위치 꺼짐이 먼저 ──
                before = _job_rows(U)
                with _job_switch():
                    assert (await new()).json()["code"] == "SWITCH-OFF", "스위치 꺼짐이 장치 검사보다 먼저"
                with _job_switch("CONVERSE_ENABLED", "ROUTINE_ENABLED"):
                    for step in ("none", "revoked"):
                        for kind in ("converse", "routine_ingest"):
                            r = await new(kind)
                            assert r.status_code == 409 and r.json()["code"] == "NO-DEVICE", (step, kind, r.text)
                            assert r.json()["msg"] == "이 기능은 본인 PC에서 본인 Claude 구독으로 실행하며, PC 연결과 에이전트 실행이 필요합니다.", r.text
                        assert _job_rows(U) == before, ("NO-DEVICE = 작업 행·임시 입력 0 생성", step)
                        if step == "none":   # 등록만 된(오프라인 — 아직 수신 0) 장치 = 접수(대기). 그 뒤 연결 해제 = 다시 0
                            gone = imports.device_connect(U, "합성 해제 장치")
                            r = await new()
                            assert r.status_code == 201, ("오프라인 장치 = 접수", r.text)
                            await h.post(f"/api/jobs/{r.json()['id']}/cancel", headers=origin)
                            imports.device_revoke(U, gone["device"]["id"])
                            before = _job_rows(U)
                d1, d2, do = imports.device_connect(U, "합성 작업 1"), imports.device_connect(U, "합성 작업 2"), imports.device_connect(O, "남 작업")
                T1, T2, TO = ({"authorization": "Bearer " + d["token"]} for d in (d1, d2, do))
                D1 = d1["device"]["id"]
                # ── 스위치 꺼짐 ──
                with _job_switch():
                    r = await new()
                    assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF", r.text
                    assert (await h.post(f"/api/jobs/{uuid.uuid4()}/confirm", headers=origin)).json()["code"] == "SWITCH-OFF"
                with _job_switch("CONVERSE_ENABLED"):
                    j0 = (await new()).json()
                with _job_switch():
                    assert (await poll(T1)).status_code == 204, "꺼진 종류는 할당하지 않는다"
                    r = await h.post(f"/api/jobs/{j0['id']}/confirm", headers=origin)
                    assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF", "②-a 확정 = SWITCH-OFF(404 아님)"
                    assert (await h.post(f"/api/jobs/{j0['id']}/cancel", headers=origin)).json()["status"] == "cancelled", "꺼져도 취소"
                with _job_switch("CONVERSE_ENABLED", "ROUTINE_ENABLED"):
                    # ── 접수 ──
                    site.dev, site.dev_user = False, ""
                    assert (await new()).status_code == 401
                    assert (await h.get(f"/api/jobs/{j0['id']}")).status_code == 401
                    site.dev, site.dev_user = True, "tester@example.com"
                    assert (await h.post("/api/jobs", json={"kind": "converse", "text": TXT}, headers={"origin": "https://evil.example"})).status_code == 403
                    r = await new("routine_ingest")
                    assert r.status_code == 201, ("스위치 켜짐 = 접수(K3 가안 한도)", r.text)
                    await h.post(f"/api/jobs/{r.json()['id']}/cancel", headers=origin)
                    saved_lim = imports.JOB_REQUEST_LIMITS.pop("routine_ingest")   # ②-b K3: None = 무제한, 키 없음 = 설정 누락
                    try:
                        r = await new("routine_ingest")
                        assert r.status_code == 503 and r.json()["code"] == "LIMIT-UNSET", ("한도 설정 누락 = fail-closed", r.text)
                    finally:
                        imports.JOB_REQUEST_LIMITS["routine_ingest"] = saved_lim
                    r = await new("summarize_reading")
                    assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF", "읽기 자료 = ②-d 전 스위치 없음(늘 꺼짐)"
                    r = await h.post("/api/jobs", json={"kind": "converse", "text": "가" * 8001}, headers=origin)
                    assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT", r.text
                    for body in ({"kind": "nope", "text": TXT}, {"kind": "converse", "text": "  "}, {"kind": "converse"}):
                        assert (await h.post("/api/jobs", json=body, headers=origin)).json()["code"] == "ARG-INVALID", body
                    for sid in (str(uuid.uuid4()), "not-a-uuid"):
                        r = await new(session_id=sid)
                        assert r.status_code == 404 and r.json()["code"] == "NOT-FOUND", "모르는 세션"
                    r = await h.post("/api/jobs", json={"kind": "converse", "text": "가" * 8000}, headers=origin)
                    assert r.status_code == 201, "8,000 코드포인트 = 통과"
                    await h.post(f"/api/jobs/{r.json()['id']}/cancel", headers=origin)
                    r = await new()
                    assert r.status_code == 201 and set(r.json()) == {"id", "session_id"}, r.text
                    J1, S1 = r.json()["id"], r.json()["session_id"]
                    rows, ti, _tr = _job_rows(U)
                    assert ti >= 1 and not any("JOBTEXT" in x for x in rows), "jobs 행에 입력 본문 0"
                    # ── 격리 ──
                    site.dev_user = "other@example.com"
                    assert (await h.get(f"/api/jobs/{J1}")).status_code == 404
                    assert (await h.post(f"/api/jobs/{J1}/cancel", headers=origin)).status_code == 404
                    site.dev_user = "tester@example.com"
                    assert (await poll(TO)).status_code == 204, "남 장치는 내 작업을 못 받는다"
                    # ── 할당·임대 ──
                    r = await poll(T1)
                    assert r.status_code == 200, r.text
                    a = r.json()
                    assert set(a) == {"id", "kind", "session_ref", "input_ref", "input_version", "definitions_version", "lease",
                                      "edit_generation", "parent_result_version"}   # ②-b 키 2 추가
                    assert a["id"] == J1 and a["session_ref"] == S1 and a["input_version"] == 1 and a["lease"]["generation"] == 1
                    assert a["definitions_version"] is None and "T" in a["lease"]["expires_at"]
                    assert (await poll(T2)).status_code == 204, "원자 할당 — 둘째 장치는 없음"
                    assert (await dj(J1, "input", TO, params={"lease_generation": 1})).status_code == 410, "남 장치 = 없는 작업"
                    for tk, g in ((T1, 2), (T2, 1), (T1, "x")):
                        r = await dj(J1, "input", tk, params={"lease_generation": g})
                        assert r.status_code == 409 and r.json()["code"] == "JOB-LEASE", (g, r.text)
                    assert _job_status(J1)["status"] == "leased" and _job_status(J1)["attempts"] == 0
                    r = await dj(J1, "input", T1, params={"lease_generation": 1})
                    assert r.status_code == 200 and r.json() == {"text": TXT, "input_version": 1, "input_kind": "converse", "edit_generation": 0,
                                                                 "restack": None}, r.text   # ②-b 키 3 추가
                    assert "JOBTEXT" in r.content.decode("utf-8") and "\\u" not in r.content.decode("utf-8"), "ensure_ascii=False"
                    assert (await dj(J1, "input", T1, params={"lease_generation": 1})).status_code == 200
                    assert dict(_job_status(J1)) == {"status": "running", "attempts": 2, "reason_code": None}, "입력 조회마다 attempts +1"
                    # ── 결과 ──
                    r = await dj(J1, "result", T1, json=res_body(1, iv=2))
                    assert r.status_code == 409 and r.json()["code"] == "JOB-VERSION"
                    r = await dj(J1, "result", T1, json=res_body(2))
                    assert r.status_code == 409 and r.json()["code"] == "JOB-LEASE"
                    assert (await dj(J1, "result", T2, json=res_body(1))).json()["code"] == "JOB-LEASE", "다른 장치"
                    bad = res_body(1)
                    bad.pop("model_meta")
                    assert (await dj(J1, "result", T1, json=bad)).status_code == 400
                    assert (await dj(J1, "result", T1, json={**res_body(1), "result_version": "x"})).status_code == 400
                    assert (await dj(J1, "result", T1, content=b"{")).status_code == 400
                    rv = str(uuid.uuid4())
                    r = await dj(J1, "result", T1, json=res_body(1, rv=rv))
                    assert r.status_code == 200 and r.json() == {"status": "result_ready"}, r.text
                    r = await dj(J1, "result", T1, json=res_body(1, rv=rv))
                    assert r.status_code == 200 and r.json() == {"duplicate": True}, "같은 result_version 재전송 = 200 duplicate"
                    r = await dj(J1, "result", T1, json=res_body(1))
                    assert r.status_code == 409 and r.json()["code"] == "JOB-LEASE", "다른 버전 = 409"
                    assert (await dj(J1, "fail", T1, json={"lease_generation": 1, "reason_code": "timeout"})).status_code == 409
                    g = (await h.get(f"/api/jobs/{J1}")).json()
                    assert g["status"] == "result_ready" and g["result"]["result_version"] == rv and g["result"]["summary_text"] == "합성 정리"
                    assert set(g) == {"id", "session_id", "kind", "status", "input_version", "attempts", "reason_code", "created_at",
                                      "expires_at", "terminal_at", "result"}
                    # ── 같은 세션 직렬화 · 실패 ──
                    J2 = (await new()).json()
                    S2 = J2["session_id"]
                    J3 = (await new(session_id=S2)).json()
                    assert J3["session_id"] == S2
                    a2 = (await poll(T1)).json()
                    assert a2["id"] == J2["id"]
                    assert (await poll(T2)).status_code == 204, "같은 세션이 실행 중이면 다음 작업을 주지 않는다"
                    await dj(J2["id"], "input", T1, params={"lease_generation": 1})
                    for body in ({"lease_generation": 1, "reason_code": "timeout", "detail": "원문"}, {"lease_generation": 1, "reason_code": "boom"},
                                 {"lease_generation": True, "reason_code": "timeout"}, {"reason_code": "timeout"}):
                        r = await dj(J2["id"], "fail", T1, json=body)
                        assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", body
                    r = await dj(J2["id"], "fail", T1, json={"lease_generation": 1, "reason_code": "timeout"})
                    assert r.status_code == 200 and r.json() == {"status": "failed"}
                    assert dict(_job_status(J2["id"])) == {"status": "failed", "attempts": 1, "reason_code": "timeout"}
                    a3 = (await poll(T2)).json()
                    assert a3["id"] == J3["id"] and a3["session_ref"] == S2, "앞 작업이 끝나면 같은 세션 다음 작업"
                    # ── 임대 만료 → needs_check ──
                    with db.tx() as c:
                        c.execute("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id=%s", (J3["id"],))
                    r = await dj(J3["id"], "input", T2, params={"lease_generation": 1})
                    assert r.status_code == 409 and r.json()["code"] == "JOB-LEASE", "만료된 임대 = 정리 전이어도 409"
                    assert (await h.get(f"/api/jobs/{J3['id']}")).json()["status"] == "needs_check"
                    assert (await dj(J3["id"], "result", T2, json=res_body(1))).status_code == 409, "늦은 결과 거부"
                    # ── 취소 ──
                    J4 = (await new()).json()["id"]
                    assert (await h.post(f"/api/jobs/{J4}/cancel", headers=origin)).json()["status"] == "cancelled"
                    assert (await h.post(f"/api/jobs/{J4}/cancel", headers=origin)).json()["status"] == "cancelled", "멱등"
                    assert (await h.post(f"/api/jobs/{J4}/cancel")).status_code == 403
                    assert (await h.post(f"/api/jobs/{J4}/nope", headers=origin)).status_code == 404
                    assert (await poll(T1)).status_code == 204
                    assert (await dj(J4, "input", T1, params={"lease_generation": 1})).status_code == 410
                    # ── 24h 만료 ──
                    J5 = (await new()).json()["id"]
                    with db.tx() as c:
                        c.execute("UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id=%s", (J5,))
                    assert (await poll(T1)).status_code == 204, "만료된 작업은 할당하지 않는다"
                    assert (await h.get(f"/api/jobs/{J5}")).json()["status"] == "expired"
                    assert (await dj(J5, "input", T1, params={"lease_generation": 1})).status_code == 410
                    # 종료·만료 작업의 임시 본문 0(살아 있는 건 J1 result_ready 하나)
                    rows, ti, tr = _job_rows(U)
                    assert (ti, tr) == (1, 1), (ti, tr)
                    assert not any("JOBTEXT" in x or "합성 정리" in x for x in rows), "jobs 행에 입력·결과 본문 0"
                    # ── 수신 직전 스위치 ──
                    J6 = (await new()).json()["id"]
                    g6 = (await poll(T1)).json()["lease"]["generation"]
                    await dj(J6, "input", T1, params={"lease_generation": g6})
                with _job_switch():
                    r = await dj(J6, "result", T1, json=res_body(g6))
                    assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF", "꺼지면 결과 수신 안 함"
                    assert (await h.post(f"/api/jobs/{J6}/cancel", headers=origin)).json()["status"] == "cancelled"
            # ── 폴링 버킷(원래 한도) ──
            with db.tx() as c:
                c.execute("DELETE FROM import_counters WHERE user_id=%s AND bucket='poll'", (U,))
            for i in range(imports.POLL_LIMITS[("device", "minute")]):
                assert (await poll(T1)).status_code == 204, i
            r = await poll(T1)
            assert r.status_code == 429 and r.json()["code"] == "RATE-LIMIT" and int(r.headers["retry-after"]) >= 1, "장치 분당 4"
            with db.tx() as c:
                c.execute("DELETE FROM import_counters WHERE user_id=%s AND bucket='poll'", (U,))
            for i in range(imports.POLL_LIMITS[("user", "minute")]):
                assert (await h.get(f"/api/jobs/{J1}", params={"wait": 0})).status_code == 200, i
            r = await h.get(f"/api/jobs/{J1}", params={"wait": 0})
            assert r.status_code == 429 and r.json()["code"] == "RATE-LIMIT", "사용자 분당 12(웹 상태 확인 포함)"
            assert (await poll(T2)).status_code == 429, "장치 폴도 사용자 버킷을 함께 쓴다"
            # ── 동시 롱폴 ──
            with db.tx() as c:
                c.execute("DELETE FROM import_counters WHERE user_id=%s AND bucket='poll'", (U,))
            with _job_fast():
                rs = await asyncio.gather(poll(T1, 2), poll(T1, 2))
                assert sorted(x.status_code for x in rs) == [204, 429], [x.status_code for x in rs]
                with _job_switch("CONVERSE_ENABLED"):
                    JQ = (await new()).json()["id"]
                    assert imports.JOB_REQUEST_LIMITS["converse"] is None, "②-b K3: converse 하루 한도 없음"
                    with db.tx() as c:   # 하루 한도 없음 — 카운터가 옛 가안(60)을 한참 넘어도 접수(카운터는 관측용으로 계속 센다)
                        c.execute("UPDATE import_counters SET n=%s WHERE user_id=%s AND scope_key='user' AND period='day' AND bucket='job_converse'",
                                  (10_000, U))
                    r = await new()
                    assert r.status_code == 201, ("K3 하루 무제한", r.text)
                    await h.post(f"/api/jobs/{r.json()['id']}/cancel", headers=origin)
                with _job_switch():   # 꺼 두면 queued 로 남는다 → 웹 롱폴이 기다린다
                    rs = await asyncio.gather(*(h.get(f"/api/jobs/{JQ}", params={"wait": 2}) for _ in range(3)))
                    assert sorted(x.status_code for x in rs) == [200, 200, 429], [x.status_code for x in rs]
                    assert all(x.json()["status"] == "queued" for x in rs if x.status_code == 200)
            assert Web_polls_empty(), "롱폴이 끝나면 동시 수 0"
        # ── 종료 표식 보유 · 계정 삭제 ──
        with db.tx() as c:
            c.execute("UPDATE jobs SET terminal_at = now() - interval '31 days' WHERE id=%s", (J4,))
        assert db.jobs_purge() >= 1
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM jobs WHERE id=%s", (J4,)).fetchone(), "30일 지난 종료 표식 삭제"
        db.user_delete(U)
        rows, ti, tr = _job_rows(U)
        assert (rows, ti, tr) == ([], 0, 0), "계정 삭제 = jobs·임시 보관 0"
        assert "jobs" in db.DELETE_TABLES and db.DELETE_TABLES.index("jobs") < db.DELETE_TABLES.index("import_streams")
    finally:
        site.dev, site.dev_user = real
    # ── 임시 보관 위치 ──
    with db.tx() as c:
        if c.execute("SELECT current_schema() AS s").fetchone()["s"] == "public":
            assert db._tmp_names(c) == ("tmp.job_inputs", "tmp.job_results")
            fk = c.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype='f' AND conrelid IN ('tmp.job_inputs'::regclass, "
                           "'tmp.job_results'::regclass) AND confrelid='public.jobs'::regclass").fetchone()["n"]
            assert fk == 2, "tmp → public.jobs FK(ON DELETE CASCADE)"
    name = f"jb_{secrets.token_hex(3)}"
    create_schema(name)
    try:
        with schema_conn(name) as c:
            db.init_schema(c)
            db.init_schema(c)
            assert db._tmp_names(c) == (f'"{name}".tmp_job_inputs', f'"{name}".tmp_job_results')
            n = c.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema=%s AND table_name LIKE 'tmp_job_%%'",
                          (name,)).fetchone()["n"]
            assert n == 2
            c.commit()
    finally:
        drop_schema(name)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM pg_namespace WHERE nspname LIKE %s", (name + "%",)).fetchone(), "임시 스키마 잔여 0"
    print("OK — PR ②-a 작업 전달(스위치 4곳·접수·격리·원자 할당·임대 세대·attempts·result duplicate·fail 코드·세션 직렬화·needs_check·취소·24h 만료"
          "·본문 0·폴링 버킷 4/12·동시 1/2·표식 30일·계정 삭제·tmp 위치) 통과")


# ── 28) PR ②-b 쌓기 단계·확인 1번 (2026-10-22, 구현계획 §3·§4 B1~B3) ─────────────────────
# 사용자 = tester@·other@example.com 재사용(TEST_EMAILS 불변). run() 맨 끝(27절 뒤)에서 돌고 시작 때 두 계정을 새로 만든다.

def _pr2b_old_shape(c):
    """②-a 모양으로 되돌린다(②-b 열·표·CHECK 제거) — init_schema 가 옛 DB 를 최종형으로 올리는지 보는 재현."""
    for t in ("item_stage", "item_versions", "confirm_events", "tmp_stack_verbatim", "stack_sessions", "tmp_stack_previews"):
        c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    c.execute("ALTER TABLE jobs DROP COLUMN edit_generation, DROP COLUMN parent_result_version")
    c.execute("ALTER TABLE jobs ALTER COLUMN definitions_version TYPE integer USING definitions_version::integer")
    c.execute("ALTER TABLE items DROP COLUMN current_version_id")
    c.execute("ALTER TABLE tmp_job_inputs DROP COLUMN meta")
    c.execute("ALTER TABLE tmp_job_results DROP COLUMN units")
    for t in ("records", "items"):
        c.execute(f"ALTER TABLE {t} DROP CONSTRAINT ck_{t}_generation_kind")
        c.execute(f"ALTER TABLE {t} ADD CONSTRAINT ck_{t}_generation_kind CHECK (generation_kind IN ('unknown','human','ai','mixed'))")


def _pr2b_cols(c, t):
    return {r["column_name"]: r["data_type"] for r in c.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (t,)).fetchall()}


def _pr2b_sql(c, sql, args=()):
    """시험 전용 직접 쓰기(경계 조작). 반환 rowcount."""
    return c.execute(sql, args).rowcount


def _pr2b_err(fn, code):
    try:
        fn()
    except db.CrmError as e:
        assert e.code == code, (code, e.code, e.message)
        return e
    raise AssertionError(f"{code} 기대인데 성공")


def test_pr2b_stack_b1():
    """B1 — 스키마 4표·jobs 새 열·definitions_version text·items 판 포인터·generation_kind 2값 · 옛 DB(②-a 모양) → init_schema 최종형·멱등·기존 행 보존
    · 한도 K3(None = 무제한·키 없음 = LIMIT-UNSET·숫자 아님 = LIMIT-UNSET·모르는 kind 400) · 세션 행(첫 converse 가 만든다·input_kind 고정·
    만료 24h 고정·작업 만료 ≤ 세션 만료·끝난 세션 JOB-GONE·본문 열 0) · 세션 만료 sweep · 표식 파기(확정 세션은 남는다) · 계정 삭제 0."""
    import uuid
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    O = db.user_by_email("other@example.com")["id"]
    # ── 스키마(새 설치 = 이 DB) ──
    with db.tx() as c:
        for t in ("stack_sessions", "confirm_events", "item_versions", "item_stage"):
            assert db._has_table(c, t), t
        jc = _pr2b_cols(c, "jobs")
        assert jc["definitions_version"] == "text" and jc["edit_generation"] == "integer" and jc["parent_result_version"] == "uuid", jc
        assert "current_version_id" in _pr2b_cols(c, "items")
        body_cols = {"text", "body", "summary_text", "units", "changes", "title"}
        for t in ("stack_sessions", "confirm_events", "item_stage", "jobs"):
            assert not body_cols & set(_pr2b_cols(c, t)), ("본문 열 0", t)
        prv = db._tmp_previews_name(c)
        if c.execute("SELECT current_schema() AS s").fetchone()["s"] == "public":
            assert prv == "tmp.stack_previews"
            fk = c.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype='f' AND conrelid='tmp.stack_previews'::regclass "
                           "AND confrelid='public.jobs'::regclass").fetchone()["n"]
            assert fk == 1, "미리보기 → public.jobs FK(ON DELETE CASCADE)"
        rid = db.new_id("r")
        c.execute("INSERT INTO records (id,user_id,title,body,generation_kind) VALUES (%s,%s,'합성','합성 본문','conversation')", (rid, U))
        try:
            with c.transaction():
                _pr2b_sql(c, "UPDATE records SET generation_kind='bogus' WHERE id=%s", (rid,))
            raise AssertionError("generation_kind 열거 밖 허용")
        except psycopg.errors.CheckViolation:
            pass
        c.execute("DELETE FROM records WHERE id=%s", (rid,))
    # ── 옛 DB(②-a 모양) → init_schema ──
    name = f"s2b_{secrets.token_hex(3)}"
    create_schema(name)
    try:
        with schema_conn(name) as c:
            db.init_schema(c)
            c.commit()
            fresh = {t: _pr2b_cols(c, t) for t in ("jobs", "items", "records", "stack_sessions", "confirm_events", "item_versions",
                                                  "item_stage", "tmp_job_inputs", "tmp_job_results", "tmp_stack_previews", "tmp_stack_verbatim")}
            ck_sql = ("SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE connamespace = current_schema()::regnamespace "
                      "AND conname LIKE 'ck_%%generation_kind'")
            fresh_ck = {r["conname"]: r["d"] for r in c.execute(ck_sql).fetchall()}
            _pr2b_old_shape(c)
            c.execute("INSERT INTO users (id,email,display_name) VALUES ('u_old2b','old2b@example.com','합성')")
            c.execute("INSERT INTO records (id,user_id,title,body,generation_kind) VALUES ('r_old2b','u_old2b','옛','옛 본문','ai')")
            jid, sid = uuid.uuid4(), uuid.uuid4()
            c.execute("INSERT INTO jobs (id,user_id,session_id,kind,expires_at) VALUES (%s,'u_old2b',%s,'converse',now() + interval '1 hour')",
                      (jid, sid))
            c.execute("INSERT INTO tmp_job_inputs (job_id,user_id,text,bytes) VALUES (%s,'u_old2b','옛 입력',9)", (jid,))
            c.commit()
            assert _pr2b_cols(c, "jobs")["definitions_version"] == "integer" and not db._has_table(c, "stack_sessions"), "옛 모양 재현"
            db.init_schema(c)
            db.init_schema(c)   # 멱등
            c.commit()
            got = {t: _pr2b_cols(c, t) for t in fresh}
            assert got == fresh, {t: (set(got[t].items()) ^ set(fresh[t].items())) for t in fresh if got[t] != fresh[t]}
            got_ck = {r["conname"]: r["d"] for r in c.execute(ck_sql).fetchall()}
            assert got_ck == fresh_ck and all("'conversation'" in d and "'routine'" in d for d in got_ck.values()), got_ck
            r = c.execute("SELECT generation_kind FROM records WHERE id='r_old2b'").fetchone()
            j = c.execute("SELECT definitions_version, edit_generation, parent_result_version FROM jobs WHERE id=%s", (jid,)).fetchone()
            t = c.execute("SELECT text, meta FROM tmp_job_inputs WHERE job_id=%s", (jid,)).fetchone()
            assert r["generation_kind"] == "ai" and j == {"definitions_version": None, "edit_generation": 0, "parent_result_version": None}, (r, j)
            assert t == {"text": "옛 입력", "meta": {}}, "임시 입력 보존 + meta 기본값"
            c.commit()
    finally:
        drop_schema(name)
    # ── 한도 K3 ──
    imports.device_connect(U, "합성 b1")   # 접수 = 연결 장치 1 이상(NO-DEVICE). 남(O)은 세션 검사(NOT-FOUND)가 먼저
    TXT = "합성 입력 STACKTEXT — 일반 테이블에 남으면 안 된다"
    with _job_switch("CONVERSE_ENABLED"):
        assert all(v is None for v in imports.JOB_REQUEST_LIMITS.values()) and set(imports.JOB_REQUEST_LIMITS) == set(db.JOB_KINDS)
        for kind, ik in (("nope", None), ("converse", "nope"), ("routine_ingest", "converse")):
            _pr2b_err(lambda: db.job_create(U, kind, TXT, input_kind=ik), "ARG-INVALID")
        saved = imports.JOB_REQUEST_LIMITS.pop("converse")
        try:
            _pr2b_err(lambda: db.job_create(U, "converse", TXT), "LIMIT-UNSET")   # 키 없음 = 설정 누락
            imports.JOB_REQUEST_LIMITS["converse"] = "60"
            _pr2b_err(lambda: db.job_create(U, "converse", TXT), "LIMIT-UNSET")   # 숫자 아님 = 설정 이상
        finally:
            imports.JOB_REQUEST_LIMITS["converse"] = saved
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM jobs WHERE user_id=%s", (U,)).fetchone(), "거절은 행 0"
        # ── 세션 행 ──
        a = db.job_create(U, "converse", TXT, input_kind="verbatim")
        b = db.job_create(U, "converse", TXT + " 둘째", session_id=a["session_id"], input_kind="converse")   # 후속 input_kind 무시
        with db.tx() as c:
            s = c.execute("SELECT * FROM stack_sessions WHERE id=%s", (a["session_id"],)).fetchone()
            ja = c.execute("SELECT * FROM jobs WHERE id=%s", (a["id"],)).fetchone()
            jb = c.execute("SELECT * FROM jobs WHERE id=%s", (b["id"],)).fetchone()
        assert s["user_id"] == U and s["input_kind"] == "verbatim" and str(s["latest_job_id"]) == b["id"] and s["edit_generation"] == 0, s
        assert abs((s["expires_at"] - s["created_at"]).total_seconds() - 24 * 3600) < 5, "세션 만료 = 최초 입력 + 24h"
        assert jb["expires_at"] <= s["expires_at"] and ja["expires_at"] <= s["expires_at"], "작업 만료 ≤ 세션 만료"
        with db.tx() as c:   # 세션 만료를 당긴다 → 후속 작업 만료도 세션 만료에 묶인다
            _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() + interval '2 hours' WHERE id=%s", (a["session_id"],))
        cc = db.job_create(U, "converse", TXT + " 셋째", session_id=a["session_id"])
        with db.tx() as c:
            s2 = c.execute("SELECT expires_at FROM stack_sessions WHERE id=%s", (a["session_id"],)).fetchone()
            j3 = c.execute("SELECT expires_at FROM jobs WHERE id=%s", (cc["id"],)).fetchone()
        assert j3["expires_at"] == s2["expires_at"], "후속 턴이 세션을 연장하지 않는다(작업 만료 = 세션 만료)"
        for jid in (a["id"], b["id"], cc["id"]):
            db.job_cancel(U, jid)
        _pr2b_err(lambda: db.job_create(O, "converse", TXT, session_id=a["session_id"]), "NOT-FOUND")   # 남의 세션
        with db.tx() as c:   # 만료 → sweep 이 terminal_at → 접수 JOB-GONE
            _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() - interval '1 second' WHERE id=%s", (a["session_id"],))
            db._job_sweep(c)
            assert c.execute("SELECT terminal_at FROM stack_sessions WHERE id=%s", (a["session_id"],)).fetchone()["terminal_at"]
        _pr2b_err(lambda: db.job_create(U, "converse", TXT, session_id=a["session_id"]), "JOB-GONE")
        # ── 표식 파기: 끝난 지 30일 지난 미확정 세션만 지운다. 확정 세션(confirm_event_id)은 남는다 ──
        d = db.job_create(U, "converse", TXT)
        db.job_cancel(U, d["id"])
        ce = uuid.uuid4()
        with db.tx() as c:
            _pr2b_sql(c, "UPDATE stack_sessions SET terminal_at = now() - interval '31 days', expires_at = now() - interval '31 days' "
                         "WHERE user_id=%s", (U,))
            _pr2b_sql(c, "UPDATE stack_sessions SET confirm_event_id=%s WHERE id=%s", (ce, d["session_id"]))
        db.jobs_purge()
        with db.tx() as c:
            left = {str(r["id"]) for r in c.execute("SELECT id FROM stack_sessions WHERE user_id=%s", (U,)).fetchall()}
            jobs_text = c.execute("SELECT count(*) AS n FROM jobs j WHERE user_id=%s AND row_to_json(j)::text LIKE '%%STACKTEXT%%'",
                                  (U,)).fetchone()["n"]
            inp, res = db._tmp_names(c)
            tmp_left = c.execute(f"SELECT count(*) AS n FROM {inp} WHERE user_id=%s", (U,)).fetchone()["n"]
        assert left == {d["session_id"]}, ("미확정 표식 30일 파기 · 확정 세션 보존", left)
        assert jobs_text == 0 and tmp_left == 0, "일반 테이블 본문 0 · 종료 작업 임시 본문 0"
    # ── 계정 삭제 = 세션·확인 이벤트 0(DELETE_TABLES 정적 검사는 25절) ──
    db.user_delete(U)
    with db.tx() as c:
        for t in ("stack_sessions", "confirm_events", "item_versions", "item_stage"):
            assert c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s", (U,)).fetchone()["n"] == 0, t
    print("OK — 28 B1 ②-b 스키마(4표·jobs 새 열·definitions_version text·판 포인터·generation_kind 2값·본문 열 0·미리보기 FK) · 옛 DB 최종형·멱등·행 보존"
          " · 한도 K3(None 무제한·누락·이상 LIMIT-UNSET·모르는 kind 400) · 세션 행(input_kind 고정·24h 고정·작업 만료 ≤ 세션·남 404·만료 410)"
          " · 표식 파기(확정 세션 보존) · 계정 삭제 0 통과")


def _pr2b_axis(uid, code, title):
    """축 항목 하나(코드 있음) — 제안 → 적용."""
    rev = db.definitions(uid, ["od_axes"])[0]["revision"]
    p = db.item_propose(uid, "od_axes", rev, None, 0, code, title, "", {}, [])
    return db.item_decide(uid, p["item_id"], 0, p["proposal_id"], "apply")["id"]


def _pr2b_body(job, gen, defs_v, rv=None, units=None, changes=None, blocked=None, **env):
    import uuid
    e = {"contract": "stack/1", "job_id": job["id"], "input_version": job.get("input_version", 1), "definitions_version": defs_v,
         "result_version": rv or str(uuid.uuid4()), "edit_generation": job.get("edit_generation", 0),
         "parent_result_version": job.get("parent_result_version")}
    e.update(env)
    return {"lease_generation": gen, "envelope": e, "units": units if units is not None else [], "changes": changes if changes is not None else [],
            "blocked": blocked if blocked is not None else [], "model_meta": {"tool": "fake", "cli_attempts": 1}}


async def test_pr2b_stack_b2():
    """B2 — 정의 스냅샷(임대 유효 때만·최초 발급 고정·재발급 같은 값·정의 변경 409 JOB-DEFS) · stack/1 수신 재검증(봉투·허위 ID·금지 관계·근거 유형 없는
    연결·중복 cid·순환·보호 필드·금지 키·URL·단위 없는 변경) · source_id 해석(실패 = 미해결 강등) · 미리보기 v1(표시 요약·추정·막힘 잠금)
    · exclude(의존 연쇄·같은 단위·새 버전·옛 미리보기 409·남는 글 0 = NEEDS-RESTACK) · 세션 GET · 재정리(restack 모양·text 일치·세대·원 결과 단위 전달·
    대체 작업 취소) · 도착 역전(늦은 옛 결과 채택 안 함) · /api/state switches · 일반 테이블 본문 0 · 남 격리."""
    import uuid
    cleanup()
    with SEED_ON():   # 표준 시드(축·결정·기준) 있는 계정 — 서버 그대로
        U = db.user_by_email("tester@example.com")["id"]
        O = db.user_by_email("other@example.com")["id"]
    d1 = imports.device_connect(U, "합성 쌓기 1")
    T1 = {"authorization": "Bearer " + d1["token"]}
    TO = {"authorization": "Bearer " + imports.device_connect(O, "남 쌓기")["token"]}
    ax = _pr2b_axis(U, "AX1", "합성 축 하나")
    site = main.site
    real = site.dev, site.dev_user
    origin = {"origin": PUBLIC}
    TXT = "합성 입력 STACKB2 — 일반 테이블에 남으면 안 된다"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = True, "tester@example.com"
            poll = lambda tk=T1: h.get("/api/device/jobs", params={"wait": 0}, headers=tk)   # noqa: E731
            dget = lambda jid, op, gen, tk=T1: h.get(f"/api/device/jobs/{jid}/{op}", params={"lease_generation": gen}, headers=tk)   # noqa: E731
            dres = lambda jid, body, tk=T1: h.post(f"/api/device/jobs/{jid}/result", json=body, headers=tk)   # noqa: E731
            with _job_fast():
                with _job_switch():
                    st = (await h.get("/api/state")).json()
                    assert st["switches"] == {"converse": False, "trash": False}, st.get("switches")   # trash = 휴지통 PR-1(30절)
                with _job_switch("CONVERSE_ENABLED"):
                    st = (await h.get("/api/state")).json()
                    assert st["switches"] == {"converse": True, "trash": False} and st["stack_session"] is None, st.get("stack_session")
                    r = await h.post("/api/jobs", json={"kind": "converse", "text": TXT, "input_kind": "verbatim"}, headers=origin)
                    assert r.status_code == 201, r.text
                    J = r.json()
                    S = J["session_id"]
                    ss_ = (await h.get("/api/state")).json()["stack_session"]
                    assert ss_["id"] == S and set(ss_) == {"id", "expires_at"}, ss_
                    a = (await poll()).json()
                    assert a["id"] == J["id"] and a["edit_generation"] == 0 and a["parent_result_version"] is None and a["definitions_version"] is None
                    gen = a["lease"]["generation"]
                    # ── 정의 스냅샷 ──
                    r = await dget(J["id"], "definitions", gen + 1)
                    assert r.status_code == 409 and r.json()["code"] == "JOB-LEASE", "옛 세대"
                    r = await dget(J["id"], "definitions", gen, TO)
                    assert r.status_code == 410, "남 장치"
                    r = await dget(J["id"], "definitions", gen)
                    assert r.status_code == 200, r.text
                    D = r.json()
                    DV = D["definitions_version"]
                    assert re.fullmatch(r"[0-9a-f]{64}", DV) and D["rule_version"] == 1 and len(D["content_rules"]) == 3
                    kinds = {o["id"]: o["kind"] for o in D["objects"]}
                    assert kinds == {"od_axes": "classify", "od_events": "event", "od_principles": "judgment"}, kinds
                    pr = next(o for o in D["objects"] if o["id"] == "od_principles")
                    assert pr["confirm_required"] and pr["protected_fields"] == ["code", "decision"] and pr["identity"] == [{"scheme": "code", "field": "code"}]
                    assert {x["key"] for x in pr["relations"]} == {"evidence_claims", "evidence_events", "axes"}
                    nocand = lambda x: {k: v for k, v in x.items() if k not in ("candidates", "candidates_at")}   # noqa: E731
                    assert nocand((await dget(J["id"], "definitions", gen)).json()) == nocand(D), "재발급 = 같은 값(후보는 매 조회 최신)"
                    with db.tx() as c:
                        assert c.execute("SELECT definitions_version FROM jobs WHERE id=%s", (J["id"],)).fetchone()["definitions_version"] == DV
                    r = await dget(J["id"], "input", gen)
                    assert r.json() == {"text": TXT, "input_version": 1, "input_kind": "verbatim", "edit_generation": 0, "restack": None}, r.text
                    job = {"id": J["id"]}
                    U1 = [{"uid": "u1", "text": "새 기준 하나를 정리했어요.", "change_ids": ["c1", "c3"]},
                          {"uid": "u2", "text": "축 설명을 보탰어요.", "change_ids": ["c2"]},
                          {"uid": "u3", "text": "고칠 항목을 못 찾았어요.", "change_ids": ["c4"]}]
                    C1 = [{"cid": "c1", "op": "create", "object_id": "od_principles", "fields": {"title": "합성 기준 문장", "body": ""},
                           "links": [{"relation": "axes", "target": {"ref_kind": "inferred", "value": ax}}], "depends_on": []},
                          {"cid": "c2", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "source_id", "scheme": "code", "value": "AX1"},
                           "fields": {"keywords": ["합성"]}, "links": [], "depends_on": []},
                          {"cid": "c3", "op": "create", "object_id": "od_events", "fields": {"title": "합성 결정"}, "links": [],
                           "depends_on": ["c1"]},
                          {"cid": "c4", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "source_id", "scheme": "code", "value": "NOPE"},
                           "fields": {}, "links": [], "depends_on": []}]

                    def body(**kw):
                        b = _pr2b_body(job, gen, DV, units=json.loads(json.dumps(U1)), changes=json.loads(json.dumps(C1)))
                        for k, v in kw.items():
                            b[k] = v
                        return b

                    async def reject(b, code, rule=None, status=400):
                        r = await dres(J["id"], b)
                        assert r.status_code == status and r.json()["code"] == code, (code, rule, r.status_code, r.text)
                        if rule:
                            assert rule in r.json()["msg"], (rule, r.json()["msg"])
                    # ── 수신 재검증 거부 ──
                    b = body()
                    b["changes"][1]["target"] = {"ref_kind": "explicit_id", "value": "i_fake0000"}
                    await reject(b, "CONTRACT-INVALID", "target_not_found")   # 허위 ID
                    other_ax = _pr2b_axis(O, "AX9", "남 축")
                    b = body()
                    b["changes"][0]["links"][0]["target"] = {"ref_kind": "explicit_id", "value": other_ax}
                    await reject(b, "CONTRACT-INVALID", "target_not_found")   # 남의 항목(명시 ID)
                    b = body()
                    b["changes"][0]["links"] = [{"relation": "evidence_records", "target": {"ref_kind": "explicit_id", "value": ax}}]
                    await reject(b, "CONTRACT-INVALID", "relation_not_allowed")   # 정의에 없는 관계(record 간·임의 evidence)
                    b = body()
                    b["changes"][0]["links"] = [{"relation": "axes", "target": {"target_kind": "item", "value": ax}}]
                    await reject(b, "CONTRACT-INVALID", "ref_kind_missing")   # 근거 유형 없는 연결(같은 날짜·유사 문장만의 연결)
                    b = body()
                    b["changes"][0]["links"] = [{"relation": "axes", "target": {"ref_kind": "same_date", "value": ax}}]
                    await reject(b, "CONTRACT-INVALID", "ref_kind")
                    b = body()
                    b["changes"][2]["cid"] = "c1"
                    await reject(b, "CONTRACT-INVALID", "duplicate")
                    b = body()
                    b["changes"][0]["depends_on"] = ["c3"]
                    await reject(b, "CONTRACT-INVALID", "depends_cycle")
                    b = body()
                    b["changes"][0]["fields"]["decision"] = "confirmed"
                    await reject(b, "CONTRACT-INVALID", "protected_field")   # 모델이 확정을 고르지 못한다
                    b = body()
                    b["changes"][0]["stage"] = "confirmed"
                    await reject(b, "CONTRACT-INVALID", "forbidden_key")
                    b = body()
                    b["units"][0]["text"] = "자세한 건 https://example.com/x 참고"
                    await reject(b, "CONTRACT-INVALID", "url_not_allowed")
                    b = body()
                    b["changes"][1]["fields"]["keywords"] = ["메일 someone@example.com"]
                    await reject(b, "CONTRACT-INVALID", "email_not_allowed")
                    b = body()
                    b["units"][1]["change_ids"] = []
                    await reject(b, "CONTRACT-INVALID", "cid_without_unit")
                    b = body()
                    b["envelope"]["job_id"] = str(uuid.uuid4())
                    await reject(b, "CONTRACT-INVALID", "envelope.job_id")
                    b = body()
                    b["envelope"]["contract"] = "stack/2"
                    await reject(b, "CONTRACT-INVALID", "envelope.contract")
                    b = body()
                    b["envelope"]["edit_generation"] = 1
                    await reject(b, "JOB-VERSION", status=409)   # 옛 편집 세대 결과
                    b = body()
                    b["changes"][0]["fields"]["axes"] = [ax]
                    await reject(b, "CONTRACT-INVALID", "link_via_fields")   # 연결 칸을 fields 로 우회
                    b = body()
                    b["summary_text"] = "옛 키 섞기"
                    await reject(b, "CONTRACT-INVALID", "keys")
                    b = body()
                    b["envelope"]["definitions_version"] = "0" * 64
                    await reject(b, "JOB-DEFS", status=409)
                    with db.tx() as c:   # 결과 생성 뒤 정의 변경 → 수신·재발급 409
                        _pr2b_sql(c, "UPDATE object_defs SET label='합성 축 바뀜' WHERE user_id=%s AND id='od_axes'", (U,))
                    await reject(body(), "JOB-DEFS", status=409)
                    r = await dget(J["id"], "definitions", gen)
                    assert r.status_code == 409 and r.json()["code"] == "JOB-DEFS"
                    with db.tx() as c:
                        _pr2b_sql(c, "UPDATE object_defs SET label='축' WHERE user_id=%s AND id='od_axes'", (U,))
                    assert _job_status(J["id"])["status"] == "running", "거부는 상태를 바꾸지 않는다"
                    # ── 정상 수신 → 미리보기 v1 ──
                    RV = str(uuid.uuid4())
                    b = body()
                    b["envelope"]["result_version"] = RV
                    r = await dres(J["id"], b)
                    assert r.status_code == 200 and r.json() == {"status": "result_ready"}, r.text
                    assert (await dres(J["id"], b)).json() == {"duplicate": True}
                    g = (await h.get(f"/api/jobs/{J['id']}")).json()
                    P = g["preview"]
                    assert g["result"] == {"result_version": RV, "summary_text": "\n\n".join(u["text"] for u in U1)}
                    assert P["preview_version"] == 1 and P["latest"] and P["blocked_open"] and P["excluded"] == [] and P["text"] == g["result"]["summary_text"]
                    ch = {x["cid"]: x for x in P["changes"]}
                    assert ch["c1"]["inferred"] and ch["c1"]["summary"] == "새 기준: 합성 기준 문장" and ch["c1"]["unit_ids"] == ["u1"]
                    assert ch["c2"]["ref_kind"] == "source_id" and ch["c2"]["summary"] == "축 고침: 합성 축 하나" and not ch["c2"]["inferred"]
                    assert ch["c4"]["ref_kind"] == "unresolved", "해석 못 한 source_id = 대상 미해결로 강등"
                    assert {(x["cid"], x["code"]) for x in P["blocked"]} == {("c4", "unresolved_target")}, P["blocked"]
                    assert set(ch["c1"]) == {"cid", "op", "object_id", "object_label", "summary", "ref_kind", "inferred", "depends_on", "unit_ids"}
                    with db.tx() as c:
                        prv = db._tmp_previews_name(c)
                        tv = c.execute(f"SELECT target_versions FROM {prv} WHERE id=%s", (P["id"],)).fetchone()["target_versions"]
                        rev = c.execute("SELECT revision FROM items WHERE id=%s", (ax,)).fetchone()["revision"]
                    assert tv == {f"item:{ax}": rev}, ("대상 버전 = 결과 생성 때 읽은 items.revision", tv)
                    # ── exclude ──
                    ex = lambda pid, cids: h.post(f"/api/stack/previews/{pid}/exclude", json={"cids": cids}, headers=origin)   # noqa: E731
                    r = await ex(P["id"], ["zz"])
                    assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
                    site.dev_user = "other@example.com"
                    assert (await ex(P["id"], ["c4"])).status_code == 404, "남 미리보기"
                    assert (await h.get(f"/api/stack/sessions/{S}")).status_code == 404, "남 세션"
                    site.dev_user = "tester@example.com"
                    r = await ex(P["id"], ["c4"])
                    assert r.status_code == 200, r.text
                    P2 = r.json()["preview"]
                    assert r.json()["removed"] == ["c4"] and P2["preview_version"] == 2 and P2["latest"] and not P2["blocked_open"]
                    assert [u["uid"] for u in P2["units"]] == ["u1", "u2"] and P2["excluded"] == ["c4"] and P2["preview_sha"] != P["preview_sha"]
                    r = await ex(P["id"], ["c2"])
                    assert r.status_code == 409 and r.json()["code"] == "CONFIRM-STALE", "옛 미리보기"
                    r = await ex(P2["id"], ["c1"])   # c1 → 같은 단위 c3(의존도 c1) 함께 → u2 만 남는다
                    assert r.status_code == 200 and r.json()["removed"] == ["c1", "c3"], r.text
                    P3 = r.json()["preview"]
                    assert [u["uid"] for u in P3["units"]] == ["u2"] and P3["excluded"] == ["c4", "c1", "c3"]
                    r = await ex(P3["id"], ["c2"])
                    assert r.status_code == 409 and r.json()["code"] == "PREVIEW-NEEDS-RESTACK", "남는 글 0"
                    s = (await h.get(f"/api/stack/sessions/{S}")).json()
                    assert s["preview"]["id"] == P3["id"] and s["input_kind"] == "verbatim" and not s["confirmed"] and s["edit_generation"] == 0
                    assert s["latest_job"] == {"id": J["id"], "status": "result_ready"}
                    # ③-0.2 뺀 변경 이름(excluded_changes) — v1 표시 요약 그대로·excluded 순서·새로 고침(세션 조회) 뒤에도 같다
                    v1 = {ch["cid"]: ch["summary"] for ch in P["changes"]}
                    assert P["excluded_changes"] == [] and P2["excluded_changes"] == [{"cid": "c4", "summary": v1["c4"]}], P2["excluded_changes"]
                    assert P3["excluded_changes"] == [{"cid": x, "summary": v1[x]} for x in ("c4", "c1", "c3")], P3["excluded_changes"]
                    assert s["preview"]["excluded_changes"] == P3["excluded_changes"], "새로 고침 뒤 이름 유지"
                    assert all(set(e) == {"cid", "summary"} for e in P3["excluded_changes"]), "cid·표시 요약만"
                    # ── 재정리 ──
                    EDIT = "합성 편집본 STACKB2"
                    rq = lambda **kw: h.post("/api/jobs", json={"kind": "converse", "session_id": S, "text": EDIT, **kw}, headers=origin)   # noqa: E731
                    good = {"parent_result_version": RV, "edit_generation": 1, "edited_text": EDIT, "excluded_cids": ["c2"],
                            "answers": [{"cid": None, "text": "축은 합성 축 하나예요"}]}
                    r = await rq(restack={**good, "edited_text": "다른 글"})
                    assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", "text ≠ edited_text"
                    r = await rq(restack={**good, "extra": 1})
                    assert r.status_code == 400
                    r = await rq(restack={**good, "edit_generation": 2})
                    assert r.status_code == 409 and r.json()["code"] == "CONFIRM-STALE", "세대 건너뜀"
                    r = await rq(restack={**good, "parent_result_version": str(uuid.uuid4())})
                    assert r.status_code == 409 and r.json()["code"] == "CONFIRM-STALE", "보고 있지 않은 결과"
                    r = await rq(restack=good)
                    assert r.status_code == 201, r.text
                    R1 = r.json()
                    assert _job_status(J["id"])["status"] == "cancelled", "대체된 결과는 확정 대상이 아니다"
                    s = (await h.get(f"/api/stack/sessions/{S}")).json()
                    assert s["preview"] is None and s["edit_generation"] == 1 and s["latest_job"]["id"] == R1["id"]
                    a = (await poll()).json()
                    assert a["id"] == R1["id"] and a["edit_generation"] == 1 and a["parent_result_version"] == RV
                    g1 = a["lease"]["generation"]
                    inp = (await dget(R1["id"], "input", g1)).json()
                    assert inp["text"] == EDIT and inp["input_kind"] == "verbatim", "입력 종류는 첫 입력 그대로"
                    assert inp["restack"] == {"parent_result_version": RV, "units": P3["units"], "excluded_cids": ["c2"],
                                              "answers": good["answers"]}, inp["restack"]
                    DV1 = (await dget(R1["id"], "definitions", g1)).json()["definitions_version"]
                    # ── 도착 역전: 재정리 1 실행 중에 또 고침(재정리 2) → 재정리 1 결과는 채택하지 않는다 ──
                    r = await rq(restack={**good, "edit_generation": 2, "excluded_cids": []})
                    assert r.status_code == 201, r.text
                    R2 = r.json()
                    assert (await poll()).status_code == 204, "같은 세션 실행 중 — 다음 작업 안 준다"
                    one = [{"uid": "u1", "text": "다시 정리했어요.", "change_ids": ["c1"]}]
                    cone = [{"cid": "c1", "op": "create", "object_id": "od_principles", "fields": {"title": "고친 기준"}, "links": [],
                             "depends_on": []}]
                    r = await dres(R1["id"], _pr2b_body({"id": R1["id"], "edit_generation": 1, "parent_result_version": RV}, g1, DV1,
                                                        units=one, changes=cone))
                    assert r.status_code == 200, r.text
                    g = (await h.get(f"/api/jobs/{R1['id']}")).json()
                    assert g["preview"]["latest"] is False, "늦게 온 옛 재정리 결과 = 채택 안 함"
                    assert (await h.get(f"/api/stack/sessions/{S}")).json()["preview"] is None
                    a = (await poll()).json()
                    assert a["id"] == R2["id"] and a["edit_generation"] == 2
                    g2 = a["lease"]["generation"]
                    assert (await dget(R2["id"], "input", g2)).json()["restack"]["units"] == P3["units"], "앞 재정리의 원 결과 단위를 그대로"
                    DV2 = (await dget(R2["id"], "definitions", g2)).json()["definitions_version"]
                    r = await dres(R2["id"], _pr2b_body({"id": R2["id"], "edit_generation": 2, "parent_result_version": RV}, g2, DV2,
                                                        units=one, changes=cone))
                    assert r.status_code == 200, r.text
                    s = (await h.get(f"/api/stack/sessions/{S}")).json()
                    assert s["preview"]["job_id"] == R2["id"] and s["preview"]["latest"] and s["edit_generation"] == 2
                    # ── 끝난 세션: 만료 뒤 수신·exclude·접수 = 410 ──
                    with db.tx() as c:
                        _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() - interval '1 second' WHERE id=%s", (S,))
                    r = await ex(s["preview"]["id"], ["c1"])
                    assert r.status_code == 410, r.text
                    r = await rq()
                    assert r.status_code == 410 and r.json()["code"] == "JOB-GONE", r.text
            # ── 본문 0 ──
            with db.tx() as c:
                for t in ("jobs", "stack_sessions", "confirm_events"):
                    n = c.execute(f"SELECT count(*) AS n FROM {t} x WHERE user_id=%s AND row_to_json(x)::text LIKE '%%STACKB2%%'", (U,)).fetchone()["n"]
                    assert n == 0, ("일반 테이블 본문 0", t)
    finally:
        site.dev, site.dev_user = real
    print("OK — 28 B2 정의 스냅샷(임대·고정·재발급·JOB-DEFS) · stack/1 재검증(봉투·허위·남 ID·금지 관계·근거 유형 없음·중복·순환·보호·금지 키·URL·이메일·"
          "단위 없는 변경·옛 키) · source_id 해석·강등 · 미리보기(요약·추정·막힘 잠금·대상 버전) · exclude(단위·의존 연쇄·옛 409·NEEDS-RESTACK) · 세션 GET·격리"
          " · 재정리(모양·text 일치·세대·원 결과 단위·대체 취소) · 도착 역전 채택 안 함 · 만료 410 · switches · 본문 0 통과")


_PR2B_TABLES = ("records", "items", "item_versions", "item_stage", "item_links", "confirm_events", "item_history")


def _pr2b_counts(uid):
    with db.tx() as c:
        out = {t: c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s", (uid,)).fetchone()["n"] for t in _PR2B_TABLES}
        inp, res = db._tmp_names(c)
        for t in (inp, res, db._tmp_previews_name(c), db._tmp_verbatim_name(c)):
            out[t] = c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s", (uid,)).fetchone()["n"]
        out["sessions_open"] = c.execute("SELECT count(*) AS n FROM stack_sessions WHERE user_id=%s AND terminal_at IS NULL",
                                         (uid,)).fetchone()["n"]
        out["jobs"] = sorted((r["status"] for r in c.execute("SELECT status FROM jobs WHERE user_id=%s", (uid,)).fetchall()))
    return out


async def test_pr2b_stack_b3():
    """B3 — 확정 트랜잭션(r3 10단계): 스위치 503 · 본문 400 · 남 404 · 해시·버전 409 CONFLICT · 막힘 409 · 정의 변경 409 JOB-DEFS · 대상 버전·보관 409(부활 0)
    · 옛 미리보기·대체 작업 409 STALE · 원문 던지기 2건 예약(잔여 1 = 429 IMPORT-QUOTA·resume_at·쓰기 0) · 쓰기 경계 오류 주입(7·8·9·10) 전부 롤백
    · 동시 확정 1(다른 요청 ID = STALE) · 같은 요청 ID 재전송 = 같은 응답·쓰기 1회 · 같은 ID 다른 내용 = MISMATCH · 적용 결과(생성·고침·판단 확정·새 판·
    근거 attach_to·axes·stage)·원문 바이트 그대로·정리 글 = 미리보기 글 · 확정 뒤 세션 끝·임시 0·다른 작업 취소 · 판 이력 v2(prev v1, v1 불변)
    · 인용 규칙(확인 정리 적격·가짜 확인 이벤트·routine 비적격·출처 정정 잠금) · 기존 단건 경로 stage 갱신·lazy 재계산."""
    import uuid
    cleanup()
    with SEED_ON():
        U = db.user_by_email("tester@example.com")["id"]
        O = db.user_by_email("other@example.com")["id"]
    d1 = imports.device_connect(U, "합성 확정 1")
    T1 = {"authorization": "Bearer " + d1["token"]}
    ax = _pr2b_axis(U, "AX1", "합성 축 하나")
    site = main.site
    real = site.dev, site.dev_user
    origin = {"origin": PUBLIC}
    ORIG = "  원문 그대로 STACKB3\n\n  공백·줄바꿈도   그대로 \n"
    real_fault = stack_service._confirm_fault
    real_lim = db.DAILY_LIMITS["records"]
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = True, "tester@example.com"

            async def stack(units, changes, blocked=None, session_id=None, input_kind=None, text="합성 대화 STACKB3"):
                body = {"kind": "converse", "text": text}
                if session_id:
                    body["session_id"] = session_id
                if input_kind:
                    body["input_kind"] = input_kind
                r = await h.post("/api/jobs", json=body, headers=origin)
                assert r.status_code == 201, r.text
                J = r.json()
                a = (await h.get("/api/device/jobs", params={"wait": 0}, headers=T1)).json()
                assert a["id"] == J["id"], a
                g = a["lease"]["generation"]
                q = {"lease_generation": g}
                await h.get(f"/api/device/jobs/{J['id']}/input", params=q, headers=T1)
                dv = (await h.get(f"/api/device/jobs/{J['id']}/definitions", params=q, headers=T1)).json()["definitions_version"]
                b = _pr2b_body({"id": J["id"]}, g, dv, units=units, changes=changes, blocked=blocked or [])
                r = await h.post(f"/api/device/jobs/{J['id']}/result", json=b, headers=T1)
                assert r.status_code == 200, r.text
                P = (await h.get(f"/api/jobs/{J['id']}")).json()["preview"]
                return J["id"], J["session_id"], P

            def cbody(P, rq=None, **kw):
                return {"request_id": rq or ("rq-" + secrets.token_hex(8)), "preview_id": P["id"], "preview_version": P["preview_version"],
                        "preview_sha": P["preview_sha"], **kw}
            conf = lambda jid, b: h.post(f"/api/jobs/{jid}/confirm", json=b, headers=origin)   # noqa: E731
            with _job_fast(), _job_switch("CONVERSE_ENABLED"):
                UN = [{"uid": "u1", "text": "새 기준 하나를 남겨요.", "change_ids": ["c1"]},
                      {"uid": "u2", "text": "결정 하나를 적어요.", "change_ids": ["c2"]},
                      {"uid": "u3", "text": "축 설명을 보태요.", "change_ids": ["c3"]}]
                CH = [{"cid": "c1", "op": "create", "object_id": "od_principles", "target": {"ref_kind": "none"},
                       "fields": {"title": "합성 기준 A", "body": "기준 설명"},
                       "links": [{"relation": "axes", "target": {"ref_kind": "explicit_id", "target_kind": "item", "value": ax}}], "depends_on": []},
                      {"cid": "c2", "op": "create", "object_id": "od_events", "fields": {"title": "합성 결정 B", "happened_on": "2026-10-21"},
                       "links": [], "depends_on": ["c1"]},
                      {"cid": "c3", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "source_id", "scheme": "code", "value": "AX1"},
                       "fields": {"keywords": ["합성", "쌓기"]}, "links": [], "depends_on": []}]
                J, S, P = await stack(UN, CH, input_kind="verbatim", text=ORIG)
                assert not P["blocked_open"] and P["latest"]
                # ── 거부(쓰기 0) ──
                base = _pr2b_counts(U)
                with _job_switch():
                    r = await conf(J, cbody(P))
                    assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF"
                for bad in ({"request_id": "rq-short"}, {**cbody(P), "extra": 1}, cbody(P, rq="짧"), {**cbody(P), "preview_version": "1"}):
                    r = await conf(J, bad)
                    assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad, r.text)
                r = await conf(J, cbody(P, preview_sha="0" * 64))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT", r.text
                r = await conf(J, cbody(P, preview_version=2))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT"
                site.dev_user = "other@example.com"
                r = await conf(J, cbody(P))
                assert r.status_code == 404, "남 작업"
                site.dev_user = "tester@example.com"
                r = await conf(str(uuid.uuid4()), cbody(P))
                assert r.status_code == 404
                with db.tx() as c:
                    n_today = c.execute(f"SELECT count(*) AS n FROM records WHERE user_id=%s AND COALESCE(received_at, created) >= {db._KST_TODAY}",
                                        (U,)).fetchone()["n"]
                db.DAILY_LIMITS["records"] = n_today + 1   # 잔여 1 — 원문 던지기는 2건
                r = await conf(J, cbody(P))
                assert r.status_code == 429 and r.json()["code"] == "IMPORT-QUOTA" and r.json().get("resume_at"), r.text
                db.DAILY_LIMITS["records"] = real_lim
                assert _pr2b_counts(U) == base, "거부 = 쓰기 0(한도 차감 0)"
                # ── 쓰기 경계 오류 주입 → 전부 롤백 ──
                for step in (7, 8, 9, 10):
                    def boom(s, step=step):
                        if s == step:
                            raise RuntimeError(f"주입 {step}")
                    stack_service._confirm_fault = boom
                    try:
                        stack_service.confirm(U, J, cbody(P))
                        raise AssertionError("주입이 안 걸렸다")
                    except RuntimeError:
                        pass
                    finally:
                        stack_service._confirm_fault = real_fault
                    assert _pr2b_counts(U) == base, ("오류 주입 롤백", step)
                # ── 동시 확정: 다른 요청 ID 둘 → 하나만 ──
                b1, b2 = cbody(P), cbody(P)
                rs = await asyncio.gather(conf(J, b1), conf(J, b2))
                codes = sorted((x.status_code, x.json().get("code")) for x in rs)
                assert codes == [(200, None), (409, "CONFIRM-STALE")], codes
                win = b1 if rs[0].status_code == 200 else b2
                R = (rs[0] if rs[0].status_code == 200 else rs[1]).json()
                after = _pr2b_counts(U)
                again = await conf(J, win)
                assert again.status_code == 200 and again.json() == R, "같은 요청 ID 재전송 = 같은 응답"
                assert _pr2b_counts(U) == after, "재전송 쓰기 0"
                r = await conf(J, {**win, "preview_sha": "1" * 64})
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-REQUEST-MISMATCH"
                # ── 적용 결과 ──
                assert set(R) == {"confirm_event_id", "created", "updated", "versions"} and len(R["created"]["records"]) == 2
                assert len(R["created"]["items"]) == 2 and R["updated"]["items"] == [ax] and len(R["versions"]) == 1
                vrid, srid = R["created"]["records"]
                i_pr, i_ev = R["created"]["items"]
                with db.tx() as c:
                    vr = c.execute("SELECT * FROM records WHERE id=%s", (vrid,)).fetchone()
                    sr = c.execute("SELECT * FROM records WHERE id=%s", (srid,)).fetchone()
                    pr = c.execute("SELECT * FROM items WHERE id=%s", (i_pr,)).fetchone()
                    ev = c.execute("SELECT * FROM items WHERE id=%s", (i_ev,)).fetchone()
                    axr = c.execute("SELECT * FROM items WHERE id=%s", (ax,)).fetchone()
                    vers = c.execute("SELECT * FROM item_versions WHERE item_id=%s", (i_pr,)).fetchall()
                    links = {(x["field_key"], x["target_item_id"]) for x in c.execute(
                        "SELECT field_key,target_item_id FROM item_links WHERE source_record_id=%s", (srid,)).fetchall()}
                    pr_links = {(x["field_key"], x["target_item_id"]) for x in c.execute(
                        "SELECT field_key,target_item_id FROM item_links WHERE source_item_id=%s", (i_pr,)).fetchall()}
                    hist = [(x["action"], x["prev_revision"]) for x in c.execute(
                        "SELECT action,prev_revision FROM item_history WHERE item_id=%s ORDER BY id", (i_pr,)).fetchall()]
                    st = {x["item_id"]: x["stage"] for x in c.execute("SELECT item_id,stage FROM item_stage WHERE user_id=%s", (U,)).fetchall()}
                    ce = c.execute("SELECT * FROM confirm_events WHERE id=%s", (R["confirm_event_id"],)).fetchone()
                assert vr["body"] == ORIG and (vr["author_kind"], vr["text_process"], vr["generation_kind"]) == ("self", "verbatim", "human"), "원문 바이트 그대로"
                assert sr["body"] == P["text"] and (sr["author_kind"], sr["text_process"], sr["generation_kind"]) == ("self", "structured", "conversation")
                assert sr["generation_meta"]["confirm_event_id"] == R["confirm_event_id"] and sr["generation_meta"]["companion_record_id"] == vrid
                assert vr["generation_meta"]["companion_record_id"] == srid and sr["generation_meta"]["job_id"] == J
                assert ce["final_text_sha"] == hashlib.sha256(P["text"].encode()).hexdigest() and ce["request_id"] == win["request_id"]
                assert (pr["title"], pr["decision"], pr["generation_kind"], pr["revision"]) == ("합성 기준 A", "confirmed", "conversation", 2)
                assert hist == [("apply", 0), ("confirm", 1)], hist
                assert len(vers) == 1 and vers[0]["version_no"] == 1 and vers[0]["prev_version_id"] is None and pr["current_version_id"] == vers[0]["id"]
                assert vers[0]["fields"]["title"] == "합성 기준 A" and vers[0]["links"] == [{"field_key": "axes", "target_kind": "item", "target_id": ax}]
                assert R["versions"] == [{"item_id": i_pr, "version_no": 1}]
                assert pr_links == {("axes", ax)} and ev["data"] == {"happened_on": "2026-10-21"} and ev["decision"] is None
                assert axr["data"]["keywords"] == ["합성", "쌓기"] and axr["revision"] == 2
                assert links == {("attach_to", i_pr), ("attach_to", i_ev), ("axes", ax)}, links
                assert st == {i_pr: "confirmed", i_ev: "organized", ax: "organized"}, st
                # ── 확정 뒤: 세션 끝 · 임시 0 · 접수·제외 410 ──
                s = (await h.get(f"/api/stack/sessions/{S}")).json()
                assert s["confirmed"] and s["preview"] is None and s["latest_job"]["status"] == "confirmed"
                assert (await h.get("/api/state")).json()["stack_session"] is None
                cnt = _pr2b_counts(U)
                with db.tx() as c:
                    inp, res = db._tmp_names(c)
                    tmp_keys = (inp, res, db._tmp_previews_name(c), db._tmp_verbatim_name(c))
                assert all(cnt[t] == 0 for t in tmp_keys) and cnt["sessions_open"] == 0, cnt
                r = await h.post(f"/api/stack/previews/{P['id']}/exclude", json={"cids": ["c1"]}, headers=origin)
                assert r.status_code in (404, 410), r.text
                r = await h.post("/api/jobs", json={"kind": "converse", "text": "더", "session_id": S}, headers=origin)
                assert r.status_code == 410
                # ── 인용 규칙 ──
                added, dropped = db.candidates_propose(U, srid, "", [{"text": "합성 판단", "quote": "새 기준 하나를"}], "ai")
                assert len(added) == 1 and not dropped, ("확인 거친 대화 정리 = 인용 적격", dropped)
                added, _d = db.candidates_propose(U, vrid, "", [{"text": "원문 판단", "quote": "공백·줄바꿈도"}], "ai")
                assert len(added) == 1, "원문 던지기 원문 = 적격"
                with db.tx() as c:
                    fake, rout = db.new_id("r"), db.new_id("r")
                    c.execute("INSERT INTO records (id,user_id,title,body,author_kind,text_process,generation_kind,generation_meta) "
                              "VALUES (%s,%s,'가짜','가짜 정리 본문','self','structured','conversation',%s)",
                              (fake, U, db.Jsonb({"confirm_event_id": str(uuid.uuid4())})))
                    c.execute("INSERT INTO records (id,user_id,title,body,author_kind,text_process,generation_kind) "
                              "VALUES (%s,%s,'루틴','루틴 결과 본문','self','verbatim','routine')", (rout, U))
                for rid, q in ((fake, "가짜 정리"), (rout, "루틴 결과")):
                    added, dropped = db.candidates_propose(U, rid, "", [{"text": "x", "quote": q}], "ai")
                    assert not added and dropped[0]["why"] == db.INELIGIBLE_WHY, (rid, dropped)
                _pr2b_err(lambda: db.record_provenance_set(U, srid, "self", "verbatim", "합성 정정"), "PROVENANCE-AI-LOCKED")
                # ── 판 이력 v2: 다른 세션에서 같은 판단 고침 ──
                U2 = [{"uid": "u1", "text": "기준 A 를 고쳐요.", "change_ids": ["c1"]}]
                C2 = [{"cid": "c1", "op": "update", "object_id": "od_principles", "target": {"ref_kind": "explicit_id", "value": i_pr},
                       "fields": {"title": "합성 기준 A 고침"}, "links": [], "depends_on": []}]
                J2, S2, P2 = await stack(U2, C2)
                n_rec = _pr2b_counts(U)["records"]
                R2 = (await conf(J2, cbody(P2))).json()
                assert R2["versions"] == [{"item_id": i_pr, "version_no": 2}] and R2["updated"]["items"] == [i_pr], R2
                assert _pr2b_counts(U)["records"] == n_rec + 1, "대화(원문 던지기 아님) = 정리 글 1건"
                with db.tx() as c:
                    v = {x["version_no"]: x for x in c.execute("SELECT * FROM item_versions WHERE item_id=%s", (i_pr,)).fetchall()}
                    cur = c.execute("SELECT current_version_id, decision, revision FROM items WHERE id=%s", (i_pr,)).fetchone()
                    h2 = [x["action"] for x in c.execute("SELECT action FROM item_history WHERE item_id=%s ORDER BY id", (i_pr,)).fetchall()]
                assert v[2]["prev_version_id"] == v[1]["id"] and v[1]["fields"]["title"] == "합성 기준 A" and v[2]["fields"]["title"] == "합성 기준 A 고침"
                assert cur["current_version_id"] == v[2]["id"] and cur["decision"] == "confirmed" and h2 == ["apply", "confirm", "apply"], (cur, h2)
                # ── 충돌: 대상 버전 · 보관 · 정의 · 막힘 · 옛 미리보기 · 대체 작업 ──
                C3 = [{"cid": "c1", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "explicit_id", "value": ax},
                       "fields": {"keywords": ["충돌"]}, "links": [], "depends_on": []}]
                U3 = [{"uid": "u1", "text": "축을 고쳐요.", "change_ids": ["c1"]}]
                J3, S3, P3 = await stack(U3, C3)
                rev = db.definitions(U, ["od_axes"])[0]["revision"]
                p = db.item_propose(U, "od_axes", rev, ax, 2, None, "합성 축 하나", "", {"keywords": ["밖에서"]}, [])
                db.item_decide(U, ax, 2, p["proposal_id"], "apply")   # 결과 생성 뒤 대상이 바뀜
                base = _pr2b_counts(U)
                r = await conf(J3, cbody(P3))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT", r.text
                assert _pr2b_counts(U) == base, "충돌 = 쓰기 0"
                db.job_cancel(U, J3)
                J4, S4, P4 = await stack(U3, C3)
                db.item_decide(U, ax, 3, None, "archive")
                r = await conf(J4, cbody(P4))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT"
                with db.tx() as c:
                    assert c.execute("SELECT archived_at FROM items WHERE id=%s", (ax,)).fetchone()["archived_at"], "보관 대상 부활 0"
                h_ax = db.item_get(U, ax)["history"]
                db.item_decide(U, ax, 4, None, "restore", history_id=h_ax[-1]["id"])
                db.job_cancel(U, J4)
                J5, S5, P5 = await stack(U3, C3)
                with db.tx() as c:
                    _pr2b_sql(c, "UPDATE object_defs SET label='합성 축 바뀜' WHERE user_id=%s AND id='od_axes'", (U,))
                r = await conf(J5, cbody(P5))
                assert r.status_code == 409 and r.json()["code"] == "JOB-DEFS", r.text
                with db.tx() as c:
                    _pr2b_sql(c, "UPDATE object_defs SET label='축' WHERE user_id=%s AND id='od_axes'", (U,))
                db.job_cancel(U, J5)
                UB = [{"uid": "u1", "text": "못 찾은 항목.", "change_ids": ["c1"]}]
                CB = [{"cid": "c1", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "unresolved"}, "fields": {}, "links": [],
                       "depends_on": []}]
                J6, S6, P6 = await stack(UB, CB)
                r = await conf(J6, cbody(P6))
                assert P6["blocked_open"] and r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT", "막힘 = 확정 잠금"
                db.job_cancel(U, J6)
                other_ax = db.items(O, "od_axes")[0]["id"] if db.items(O, "od_axes") else _pr2b_axis(O, "AX8", "남 축")
                CI = [{"cid": "c1", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "inferred", "value": "i_fake0000"},
                       "fields": {}, "links": [], "depends_on": []},
                      {"cid": "c2", "op": "create", "object_id": "od_principles", "fields": {"title": "합성 추정 연결"},
                       "links": [{"relation": "axes", "target": {"ref_kind": "inferred", "value": other_ax}}], "depends_on": []}]
                UI = [{"uid": "u1", "text": "추정 대상.", "change_ids": ["c1"]}, {"uid": "u2", "text": "추정 연결.", "change_ids": ["c2"]}]
                J6b, _S6b, P6b = await stack(UI, CI)   # 모델 추정이 없는·남의 항목 = 거부 아님, 미해결 강등 + 막힘
                chi = {x["cid"]: x for x in P6b["changes"]}
                assert chi["c1"]["ref_kind"] == "unresolved" and P6b["blocked_open"], P6b
                assert {(b["cid"], b["code"]) for b in P6b["blocked"]} == {("c1", "unresolved_target"), ("c2", "unresolved_target")}, P6b["blocked"]
                r = await conf(J6b, cbody(P6b))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT"
                db.job_cancel(U, J6b)
                UX = [{"uid": "u1", "text": "결정 하나.", "change_ids": ["c1"]}, {"uid": "u2", "text": "결정 둘.", "change_ids": ["c2"]}]
                CX = [{"cid": "c1", "op": "create", "object_id": "od_events", "fields": {"title": "합성 결정 X1"}, "links": [], "depends_on": []},
                      {"cid": "c2", "op": "create", "object_id": "od_events", "fields": {"title": "합성 결정 X2"}, "links": [], "depends_on": []}]
                J7, S7, P7 = await stack(UX, CX)
                PX = (await h.post(f"/api/stack/previews/{P7['id']}/exclude", json={"cids": ["c2"]}, headers=origin)).json()["preview"]
                r = await conf(J7, cbody(P7))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-STALE", "옛 미리보기(제외 전)"
                rv7 = (await h.get(f"/api/jobs/{J7}")).json()["result"]["result_version"]
                r = await h.post("/api/jobs", json={"kind": "converse", "session_id": S7, "text": "편집",
                                                    "restack": {"parent_result_version": rv7, "edit_generation": 1, "edited_text": "편집"}},
                                 headers=origin)
                assert r.status_code == 201, r.text
                R8 = r.json()
                r = await conf(J7, cbody(PX))
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-STALE", "편집 뒤(재정리 대기) 옛 결과"
                db.job_cancel(U, R8["id"])
                # ── 기존 단건 경로 stage · lazy ──
                rev_pr = db.item_get(U, i_pr)["revision"]
                db.item_decide(U, i_pr, rev_pr, None, "exclude")
                with db.tx() as c:
                    assert c.execute("SELECT stage FROM item_stage WHERE item_id=%s", (i_pr,)).fetchone()["stage"] == "organized", "item_decide 뒤 재계산"
                    _pr2b_sql(c, "DELETE FROM item_stage WHERE item_id=%s", (i_ev,))
                assert db.item_get(U, i_ev)["stage"] == "organized"
                with db.tx() as c:
                    assert c.execute("SELECT stage FROM item_stage WHERE item_id=%s", (i_ev,)).fetchone(), "lazy 계산 저장"
                od = db.definitions(U, ["od_events"])[0]
                nd = {"key": od["key"], "label": od["label"], "semantic_kind": od["semantic_kind"], "policy": od["policy"],
                      "fields": [{k: f[k] for k in ("key", "label", "value_type", "required", "options", "target_kind", "target_object_id",
                                                    "multiple")} for f in od["fields"]]}
                pp = db.definition_propose(U, "od_events", od["revision"], nd)
                db.definition_apply(U, "od_events", pp["proposal_id"], od["revision"])
                with db.tx() as c:
                    assert not c.execute("SELECT 1 FROM item_stage WHERE item_id=%s", (i_ev,)).fetchone(), "정의 적용 = 그 오브젝트 stage 무효화"
                assert db.item_get(U, i_ev)["stage"] == "organized"
    finally:
        site.dev, site.dev_user = real
        stack_service._confirm_fault = real_fault
        db.DAILY_LIMITS["records"] = real_lim
    print("OK — 28 B3 확정 10단계(스위치·본문·남·해시·버전·막힘·정의·대상 버전·보관 부활 0·옛 미리보기·재정리 대기 STALE) · 원문 던지기 2건 예약 429·쓰기 0"
          " · 오류 주입 7·8·9·10 전부 롤백 · 동시 확정 1 · 같은 요청 ID 재전송 같은 응답·쓰기 1회 · MISMATCH · 생성·고침·판단 확정·새 판·근거 연결·stage"
          " · 원문 바이트 그대로 · 확정 뒤 세션 끝·임시 0·410 · 판 이력 v2 · 인용 규칙 · 기존 경로 stage·lazy 통과")


async def test_pr2b_stack_b6():
    """B6 서버 몫(실패표 r3 서버 행 보강) — 만료 직전 재정리(원래 만료 유지·만료 뒤 수신·확정 410) · conversation·routine 우회 저장 불가
    (inbox 생성 방법 고정·MCP record_add 인자 없음) · 스위치를 끈 뒤에도 확정 글·세션 조회·취소·만료 정리는 돈다(확정만 503)
    · 「나」 축 띠 = 항목 + 정리 글(축 연결 axes)."""
    import inspect
    import uuid
    cleanup()
    with SEED_ON():
        U = db.user_by_email("tester@example.com")["id"]
    T1 = {"authorization": "Bearer " + imports.device_connect(U, "합성 B6")["token"]}
    ax = _pr2b_axis(U, "AX1", "합성 축 하나")
    site = main.site
    real = site.dev, site.dev_user
    origin = {"origin": PUBLIC}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = True, "tester@example.com"

            async def lease():
                a = (await h.get("/api/device/jobs", params={"wait": 0}, headers=T1)).json()
                q = {"lease_generation": a["lease"]["generation"]}
                await h.get(f"/api/device/jobs/{a['id']}/input", params=q, headers=T1)
                dv = (await h.get(f"/api/device/jobs/{a['id']}/definitions", params=q, headers=T1)).json()["definitions_version"]
                return a, dv
            UN = [{"uid": "u1", "text": "축에 글 하나 남겨요 STACKB6.", "change_ids": ["c1"]}]
            CH = [{"cid": "c1", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "explicit_id", "value": ax},
                   "fields": {"keywords": ["B6"]}, "links": [], "depends_on": []}]
            with _job_fast(), _job_switch("CONVERSE_ENABLED"):
                before = next(a for a in db.me_stats(U)["axes"] if a["id"] == ax)
                r = await h.post("/api/jobs", json={"kind": "converse", "text": "합성 B6 대화"}, headers=origin)
                J = r.json()
                a, dv = await lease()
                await h.post(f"/api/device/jobs/{J['id']}/result", headers=T1,
                             json=_pr2b_body({"id": J["id"]}, a["lease"]["generation"], dv, units=UN, changes=CH))
                g = (await h.get(f"/api/jobs/{J['id']}")).json()
                P, RV = g["preview"], g["result"]["result_version"]
                # ── 만료 직전 재정리: 세션 만료를 2초 뒤로 당긴다 → 재정리 작업 만료 = 세션 만료(연장 없음) ──
                with db.tx() as c:
                    _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() + interval '2 seconds' WHERE id=%s", (J["session_id"],))
                    s_exp = c.execute("SELECT expires_at FROM stack_sessions WHERE id=%s", (J["session_id"],)).fetchone()["expires_at"]
                r = await h.post("/api/jobs", json={"kind": "converse", "session_id": J["session_id"], "text": "편집 B6",
                                                    "restack": {"parent_result_version": RV, "edit_generation": 1, "edited_text": "편집 B6"}},
                                 headers=origin)
                assert r.status_code == 201, r.text
                R = r.json()
                with db.tx() as c:
                    assert c.execute("SELECT expires_at FROM jobs WHERE id=%s", (R["id"],)).fetchone()["expires_at"] == s_exp, "재정리가 만료를 늘리지 않는다"
                    assert c.execute("SELECT expires_at FROM stack_sessions WHERE id=%s", (J["session_id"],)).fetchone()["expires_at"] == s_exp
                a2, dv2 = await lease()
                with db.tx() as c:   # 서버 시각 경계 — 만료를 지나게 한다
                    _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() - interval '1 second' WHERE id=%s", (J["session_id"],))
                    _pr2b_sql(c, "UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id=%s", (R["id"],))
                r = await h.post(f"/api/device/jobs/{R['id']}/result", headers=T1,
                                 json=_pr2b_body({"id": R["id"], "edit_generation": 1, "parent_result_version": RV}, a2["lease"]["generation"], dv2,
                                                 units=UN, changes=CH))
                assert r.status_code == 410 and r.json()["code"] == "JOB-GONE", ("만료 뒤 수신", r.text)
                r = await h.post(f"/api/jobs/{J['id']}/confirm", headers=origin,
                                 json={"request_id": "rq-b6-expired", "preview_id": P["id"], "preview_version": P["preview_version"],
                                       "preview_sha": P["preview_sha"]})
                assert r.status_code in (409, 410), ("만료 뒤 확정", r.text)
                assert r.json()["code"] in ("JOB-GONE", "CONFIRM-STALE"), r.text
                # ── 새 세션에서 확정 → 축 띠에 정리 글 1 ──
                r = await h.post("/api/jobs", json={"kind": "converse", "text": "합성 B6 대화 둘"}, headers=origin)
                J2 = r.json()
                a, dv = await lease()
                await h.post(f"/api/device/jobs/{J2['id']}/result", headers=T1,
                             json=_pr2b_body({"id": J2["id"]}, a["lease"]["generation"], dv, units=UN, changes=CH))
                P2 = (await h.get(f"/api/jobs/{J2['id']}")).json()["preview"]
                r = await h.post(f"/api/jobs/{J2['id']}/confirm", headers=origin,
                                 json={"request_id": "rq-b6-ok-0001", "preview_id": P2["id"], "preview_version": 1, "preview_sha": P2["preview_sha"]})
                assert r.status_code == 200, r.text
                srid = r.json()["created"]["records"][-1]
                after = next(a for a in db.me_stats(U)["axes"] if a["id"] == ax)
                assert after["records"] == before["records"] + 1, ("축 띠 = 항목 + 정리 글", before, after)
                # 다음 세션 하나(미확정) — 스위치를 끈 뒤 취소·만료 정리가 도는지
                r = await h.post("/api/jobs", json={"kind": "converse", "text": "합성 B6 대화 셋"}, headers=origin)
                J3 = r.json()
            # ── 스위치 끔: 확정만 503, 조회·취소·정리는 그대로 ──
            with _job_switch():
                assert (await h.get(f"/api/stack/sessions/{J2['session_id']}")).json()["confirmed"]
                assert db.record_get(U, srid)["body"] == "축에 글 하나 남겨요 STACKB6.", "확정 글 조회 유지"
                r = await h.post(f"/api/jobs/{J3['id']}/confirm", headers=origin, json={})
                assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF"
                r = await h.post(f"/api/jobs/{J3['id']}/cancel", headers=origin)
                assert r.json()["status"] == "cancelled", "꺼져도 취소"
                with db.tx() as c:
                    _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() - interval '1 second' WHERE id=%s", (J3["session_id"],))
                db.jobs_purge()
                with db.tx() as c:
                    assert c.execute("SELECT terminal_at FROM stack_sessions WHERE id=%s", (J3["session_id"],)).fetchone()["terminal_at"], "꺼져도 만료 정리"
                    inp, res = db._tmp_names(c)
                    left = sum(c.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id=%s", (U,)).fetchone()["n"]
                               for t in (inp, res, db._tmp_previews_name(c), db._tmp_verbatim_name(c)))
                assert left == 0, "끝난 작업·세션 임시 0"
    finally:
        site.dev, site.dev_user = real
    # ── 우회 저장 불가: conversation·routine 은 확정 트랜잭션만 쓴다 ──
    for method in ("conversation", "routine", "none"):
        g = {"method": method, "tool": "claude-code", "model": "m", "input_scope": "x", "inputs": []}
        _pr2b_err(lambda: imports._generation("self_summary", g), "CONTRACT-INVALID")
    params = set(inspect.signature(main.record_add).parameters) | set(inspect.signature(db.record_add).parameters)
    assert not params & {"generation_kind", "generation", "generation_meta"}, params
    with db.tx() as c:
        kinds = {r["generation_kind"] for r in c.execute("SELECT generation_kind FROM records WHERE user_id=%s", (U,)).fetchall()}
    assert kinds <= {"conversation", "human", "unknown"}, kinds
    print("OK — 28 B6 만료 직전 재정리(만료 고정·만료 뒤 수신 410·확정 거부) · 축 띠 = 항목 + 정리 글 · 스위치 끔(확정만 503·조회·취소·만료 정리 유지·임시 0)"
          " · conversation·routine 우회 저장 불가(inbox 방법 고정·MCP record_add 인자 없음) 통과")


async def test_pr2b_stack_r2():
    """r2 추가 실패표 보강 — 두 세션이 같은 대상 수정(뒤 확정이 앞 변경을 덮지 않음: 409 CONFLICT·쓰기 0) · 만료 정리가 늦어도 접근·확정 즉시 차단
    (sweep 전 만료 = 410·미리보기 재노출 0)."""
    cleanup()
    with SEED_ON():
        U = db.user_by_email("tester@example.com")["id"]
    T1 = {"authorization": "Bearer " + imports.device_connect(U, "합성 r2")["token"]}
    ax = _pr2b_axis(U, "AX1", "합성 축 하나")
    site = main.site
    real = site.dev, site.dev_user
    origin = {"origin": PUBLIC}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = True, "tester@example.com"

            async def stack(kw):
                J = (await h.post("/api/jobs", json={"kind": "converse", "text": "합성 r2 대화"}, headers=origin)).json()
                a = (await h.get("/api/device/jobs", params={"wait": 0}, headers=T1)).json()
                q = {"lease_generation": a["lease"]["generation"]}
                await h.get(f"/api/device/jobs/{a['id']}/input", params=q, headers=T1)
                dv = (await h.get(f"/api/device/jobs/{a['id']}/definitions", params=q, headers=T1)).json()["definitions_version"]
                ch = [{"cid": "c1", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "explicit_id", "value": ax},
                       "fields": {"keywords": [kw]}, "links": [], "depends_on": []}]
                un = [{"uid": "u1", "text": f"축에 {kw}.", "change_ids": ["c1"]}]
                r = await h.post(f"/api/device/jobs/{J['id']}/result", headers=T1,
                                 json=_pr2b_body({"id": J["id"]}, a["lease"]["generation"], dv, units=un, changes=ch))
                assert r.status_code == 200, r.text
                return J, (await h.get(f"/api/jobs/{J['id']}")).json()["preview"]

            def cb(P, rq):
                return {"request_id": rq, "preview_id": P["id"], "preview_version": P["preview_version"], "preview_sha": P["preview_sha"]}
            with _job_fast(), _job_switch("CONVERSE_ENABLED"):
                JA, PA = await stack("앞 세션")
                JB, PB = await stack("뒤 세션")   # 둘 다 같은 대상 revision 을 읽고 미리보기를 만들었다
                assert (await h.post(f"/api/jobs/{JA['id']}/confirm", json=cb(PA, "rq-r2-a-0001"), headers=origin)).status_code == 200
                base = _pr2b_counts(U)
                r = await h.post(f"/api/jobs/{JB['id']}/confirm", json=cb(PB, "rq-r2-b-0001"), headers=origin)
                assert r.status_code == 409 and r.json()["code"] == "CONFIRM-CONFLICT", r.text
                assert _pr2b_counts(U) == base and db.item_get(U, ax)["data"]["keywords"] == ["앞 세션"], "뒤 확정이 앞 변경을 덮지 않는다"
                db.job_cancel(U, JB["id"])
                # 만료 정리가 늦어도(sweep 전) 접근·확정 즉시 차단, 미리보기 재노출 0
                JC, PC = await stack("만료")
                with db.tx() as c:
                    _pr2b_sql(c, "UPDATE stack_sessions SET expires_at = now() - interval '1 second' WHERE id=%s", (JC["session_id"],))
                    _pr2b_sql(c, "UPDATE jobs SET expires_at = now() - interval '1 second' WHERE id=%s", (JC["id"],))
                    prv = db._tmp_previews_name(c)
                    assert c.execute(f"SELECT count(*) AS n FROM {prv} WHERE job_id=%s", (JC["id"],)).fetchone()["n"] == 1, "정리 전 — 행은 아직 있다"
                r = await h.post(f"/api/jobs/{JC['id']}/confirm", json=cb(PC, "rq-r2-c-0001"), headers=origin)
                assert r.status_code == 410 and r.json()["code"] == "JOB-GONE", r.text
                r = await h.post(f"/api/stack/previews/{PC['id']}/exclude", json={"cids": ["c1"]}, headers=origin)
                assert r.status_code in (404, 410), r.text
                g = (await h.get(f"/api/jobs/{JC['id']}")).json()
                s = (await h.get(f"/api/stack/sessions/{JC['session_id']}")).json()
                assert g["status"] == "expired" and "preview" not in g and "result" not in g and s["preview"] is None, (g, s)
                with db.tx() as c:
                    assert c.execute(f"SELECT count(*) AS n FROM {prv} WHERE job_id=%s", (JC["id"],)).fetchone()["n"] == 0, "읽기 경로 정리로 삭제"
    finally:
        site.dev, site.dev_user = real
    print("OK — 28 r2 보강(두 세션 같은 대상 = 뒤 확정 409·쓰기 0·앞 변경 보존 · 만료 정리 지연에도 확정 410·제외 차단·재노출 0) 통과")


async def test_pr2b_stack_candidates():
    """정의 스냅샷의 대상 후보(candidates) — 그 사용자 것만(남 0) · 적용·미보관 항목만(제안 revision 0·보관 제외) · 제목 80자 자름 · stage 포함
    · 오브젝트별·전체·사람·출처 상한 · definitions_version 해시 밖(항목이 늘어도 해시 같음, 후보는 최신) · candidates_at · 본문 0."""
    cleanup()
    with SEED_ON():
        U = db.user_by_email("tester@example.com")["id"]
        O = db.user_by_email("other@example.com")["id"]
    dev = imports.authenticate("Bearer " + imports.device_connect(U, "합성 후보")["token"])
    ax = _pr2b_axis(U, "AX1", "합성 축 " + "길" * 100)
    ax2 = _pr2b_axis(U, "AX2", "합성 축 둘")
    gone = _pr2b_axis(U, "AX3", "합성 보관 축")
    db.item_decide(U, gone, 1, None, "archive")
    rev = db.definitions(U, ["od_axes"])[0]["revision"]
    draft = db.item_propose(U, "od_axes", rev, None, 0, None, "합성 제안만", "본문 CANDBODY", {}, [])["item_id"]
    other = _pr2b_axis(O, "AX1", "남 축")
    p1 = db.person_save(U, "합성 사람")["id"]
    p_o = db.person_save(O, "남 사람")["id"]
    s1 = db.source_create(U, "other", "합성 출처")["id"]
    saved = (stack_service.CAND_PER_OBJECT, stack_service.CAND_ITEMS_MAX, stack_service.CAND_PEOPLE_MAX, stack_service.CAND_SOURCES_MAX)
    try:
        with _job_switch("CONVERSE_ENABLED"):
            j = db.job_create(U, "converse", "합성 후보 대화")
            a = db.job_assign(dev)
            g = a["lease"]["generation"]
            db.job_input(dev, j["id"], g)
            d1 = db.job_definitions(dev, j["id"], g)
            C = d1["candidates"]
            assert set(d1) == {"definitions_version", "rule_version", "objects", "content_rules", "candidates", "candidates_at"}
            assert set(C) == {"items", "people", "sources"} and d1["candidates_at"]
            ids = {x["id"] for x in C["items"]}
            assert {ax, ax2} <= ids and gone not in ids and draft not in ids and other not in ids, ids
            one = next(x for x in C["items"] if x["id"] == ax)
            assert set(one) == {"id", "object_id", "code", "title", "stage"} and one["code"] == "AX1" and one["object_id"] == "od_axes"
            assert len(one["title"]) == 80 and one["stage"] == "organized", one
            assert {x["id"] for x in C["people"]} == {p1} and C["people"][0]["alias"] == "합성 사람"
            assert {x["id"] for x in C["sources"]} == {s1} and C["sources"][0]["title"] == "합성 출처"
            assert p_o not in str(C) and "CANDBODY" not in str(d1), "남 데이터·본문 0"
            new = _pr2b_axis(U, "AX4", "합성 축 넷")   # 항목이 늘어도 해시는 그대로(후보만 최신)
            d2 = db.job_definitions(dev, j["id"], g)
            assert d2["definitions_version"] == d1["definitions_version"] and new in {x["id"] for x in d2["candidates"]["items"]}
            assert {k: v for k, v in d2.items() if k not in ("candidates", "candidates_at")} == \
                {k: v for k, v in d1.items() if k not in ("candidates", "candidates_at")}
            stack_service.CAND_PER_OBJECT, stack_service.CAND_ITEMS_MAX, stack_service.CAND_PEOPLE_MAX, stack_service.CAND_SOURCES_MAX = 2, 3, 0, 0
            C3 = db.job_definitions(dev, j["id"], g)["candidates"]
            per = {}
            for x in C3["items"]:
                per[x["object_id"]] = per.get(x["object_id"], 0) + 1
            assert len(C3["items"]) <= 3 and all(n <= 2 for n in per.values()) and per.get("od_axes") == 2, per
            assert [x["id"] for x in C3["items"] if x["object_id"] == "od_axes"] == [new, ax2], "오브젝트별 최근 갱신순"
            assert C3["people"] == [] and C3["sources"] == []
            stack_service.CAND_ITEMS_MAX = 1
            assert len(db.job_definitions(dev, j["id"], g)["candidates"]["items"]) == 1, "전체 상한"
            db.job_cancel(U, j["id"])
    finally:
        stack_service.CAND_PER_OBJECT, stack_service.CAND_ITEMS_MAX, stack_service.CAND_PEOPLE_MAX, stack_service.CAND_SOURCES_MAX = saved
    print("OK — 28 대상 후보(남 0·적용·미보관만·제목 80자·stage·상한 오브젝트별·전체·사람·출처·최근 갱신순·해시 밖·candidates_at·본문 0) 통과")


async def test_pr2b_stack():
    """28절 — ②-b 단계별 시험(B1 스키마·한도·세션 · B2 정의·수신·미리보기·제외·재정리 · B3 확정 트랜잭션·판 이력·stage·인용 · B6 경계·되돌림)."""
    test_pr2b_stack_b1()
    await test_pr2b_stack_b2()
    await test_pr2b_stack_b3()
    await test_pr2b_stack_b6()
    await test_pr2b_stack_r2()
    await test_pr2b_stack_candidates()


# ── 29) ③-0.2 장치 상태 — /api/state.devices_status (2026-10-22, 구현계획 §2-2) ─────────────────────
# 사용자 = tester@·other@example.com 재사용(TEST_EMAILS 불변). run() 맨 끝(28절 뒤)에서 돌고 시작 때 두 계정을 새로 만든다.

async def test_serve_devices_status():
    """29절 — 3값(미연결 none · 최근 연결 recent · 오프라인 offline, 기준 직전/직후) · 등록 ≠ 최근 연결 · 마지막 연결 = 서버 수신 시각(PC 시각 무시,
    여러 장치면 가장 최근) · 작업 요청도 연결 · 유효 임대(running·만료 전) = recent(시각 그대로, 만료 뒤 offline, 남 임대 영향 0) · 인증 실패(틀린 토큰·폐기·만료) 폴은 시각 0 · 쓰기 간격 60초(안 = 쓰기 0, 넘으면 갱신) · 남 장치 정보 0 · 기준 = max(180, 롱폴×3)(서버·PC 상수 묶음)
    · 필드 추가만(기존 키 유지 · MCP state_get·db.state 에 없음) · 출력(print·stderr·logging 핸들러)에 토큰 비밀값·시험 표식 0."""
    import contextlib
    import io
    import logging
    import uuid
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    O = db.user_by_email("other@example.com")["id"]
    T = db.DEVICE_RECENT_SECONDS
    # 기준값 = max(180, 정상 최대 폴 주기×3). 정상 최대 폴 주기 = 서버 롱폴 상한 = PC 롱폴 대기(시작 간격 하한은 그보다 짧다)
    assert db.DEVICE_POLL_MAX_SECONDS == imports.POLL_WAIT_MAX and T == max(180, 3 * imports.POLL_WAIT_MAX) == 180, T
    pc = (HERE.parent / "agent" / "jobs.py").read_text(encoding="utf-8")
    wait_sec = int(re.search(r"^WAIT_SEC = (\d+)", pc, re.M).group(1))
    min_iv = float(re.search(r"^MIN_POLL_INTERVAL = ([\d.]+)", pc, re.M).group(1))
    assert wait_sec == imports.POLL_WAIT_MAX and min_iv <= wait_sec, ("PC 롱폴·간격과 서버 기준이 어긋남", wait_sec, min_iv)

    def one(sql, args=()):
        with db.tx() as c:
            return c.execute(sql, args).fetchone()

    def seen(dev_id):
        return one("SELECT last_used_at FROM import_streams WHERE id=%s", (dev_id,))["last_used_at"]

    def set_seen(dev_id, secs_ago):
        _sql("UPDATE import_streams SET last_used_at = now() - make_interval(secs => %s) WHERE id=%s", (secs_ago, dev_id))

    def db_now():
        return one("SELECT now() AS t")["t"]

    site = main.site
    real = site.dev, site.dev_user
    out = io.StringIO()
    secrets_seen = []
    logged = []   # logging 핸들러 출력(print 밖) — root + 전파 안 하는 로거 전부에 캡처 핸들러

    class _Cap(logging.Handler):
        def emit(self, rec):
            try:
                logged.append(self.format(rec))
            except Exception:   # noqa: BLE001 — 형식 실패여도 원 메시지는 본다
                logged.append(str(rec.msg))
    cap = _Cap(level=logging.DEBUG)
    cap_on = [logging.getLogger()] + [lg for lg in logging.Logger.manager.loggerDict.values()
                                      if isinstance(lg, logging.Logger) and not lg.propagate]
    for lg in cap_on:
        lg.addHandler(cap)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            async def state(email="tester@example.com"):
                site.dev, site.dev_user = True, email
                r = await h.get("/api/state")
                assert r.status_code == 200, r.text
                return r.json()

            def poll(tk, **hdr):
                return h.get("/api/device/jobs", params={"wait": 0}, headers={"authorization": "Bearer " + tk, **hdr})

            with _job_fast(), _job_switch(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                # ── 미연결: 장치 0 ──
                st = await state()
                assert st["devices_status"] == {"state": "none", "last_seen_at": None, "threshold_seconds": T}, st["devices_status"]
                for k in ("journal", "definitions", "switches", "stack_session", "user", "me", "devices", "activity_ui"):   # 기존 키 유지(추가만)
                    assert k in st, k
                # ── 등록 ≠ 최근 연결: 연결만 하고 요청 없음 = offline(시각 없음) ──
                d1 = imports.device_connect(U, "장치 상태 1")
                D1, K1 = d1["device"]["id"], d1["token"]
                secrets_seen.append(K1.rsplit(".", 1)[1])
                assert (await state())["devices_status"] == {"state": "offline", "last_seen_at": None, "threshold_seconds": T}
                # ── 인증 실패 폴은 시각 0: 틀린 비밀값 · 형식 틀림 · 폐기 · 만료 ──
                bad = K1[:-4] + ("AAAA" if not K1.endswith("AAAA") else "BBBB")
                for tk in (bad, "imp_garbage", ""):
                    r = await poll(tk)
                    assert r.status_code == 401, (r.status_code, r.text)
                d2 = imports.device_connect(U, "장치 상태 2")
                D2, K2 = d2["device"]["id"], d2["token"]
                secrets_seen.append(K2.rsplit(".", 1)[1])
                imports.device_revoke(U, D2)
                assert (await poll(K2)).status_code == 401
                d3 = imports.device_connect(U, "장치 상태 3")
                D3, K3 = d3["device"]["id"], d3["token"]
                secrets_seen.append(K3.rsplit(".", 1)[1])
                _sql("UPDATE import_streams SET token_expires_at = now() - interval '1 second' WHERE id=%s", (D3,))
                assert (await poll(K3)).status_code == 401
                assert (seen(D1), seen(D2), seen(D3)) == (None, None, None), "인증 실패 = 시각 갱신 0"
                assert (await state())["devices_status"]["state"] == "offline"
                imports.device_revoke(U, D3)
                # ── 마지막 연결 = 서버 수신 시각(PC 가 보낸 시각 무시) ──
                t0 = db_now()
                r = await poll(K1, date="Sat, 01 Jan 2000 00:00:00 GMT", **{"x-client-time": "2000-01-01T00:00:00Z"})
                t1 = db_now()
                assert r.status_code == 204, r.text
                s1 = seen(D1)
                assert s1 is not None and t0 <= s1 <= t1, (t0, s1, t1)
                ds = (await state())["devices_status"]
                assert ds == {"state": "recent", "last_seen_at": s1.isoformat(), "threshold_seconds": T}, ds
                # ── 쓰기 줄이기: 간격(60초) 안 반복 요청 = 쓰기 0(행 xmin 불변) · 넘으면 갱신 ──
                G = db.DEVICE_SEEN_GAP_SECONDS
                assert G == 60 and 3 * G <= T, "간격 해상도는 기준보다 충분히 짧다"
                xmin = lambda: one("SELECT xmin::text AS x FROM import_streams WHERE id=%s", (D1,))["x"]   # noqa: E731
                x0 = xmin()
                for _ in range(3):
                    assert (await poll(K1)).status_code == 204
                assert seen(D1) == s1 and xmin() == x0, "간격 안 반복 폴 = 쓰기 0"
                set_seen(D1, G + 1)
                old, t0 = seen(D1), db_now()
                assert (await poll(K1)).status_code == 204
                assert seen(D1) > old and seen(D1) >= t0, "간격 넘으면 갱신"
                # ── 기준 경계: 직전 = recent · 직후 = offline(마지막 연결 시각은 그대로 보인다) ──
                set_seen(D1, T - 2)
                ds = (await state())["devices_status"]
                assert ds["state"] == "recent" and ds["last_seen_at"] == seen(D1).isoformat(), ds
                set_seen(D1, T + 2)
                ds = (await state())["devices_status"]
                assert ds["state"] == "offline" and ds["last_seen_at"] == seen(D1).isoformat(), ds
                # ── 작업 요청(input·result 등)도 인증된 연결 — 없는 작업 410 이어도 수신 시각은 남는다 ──
                t0 = db_now()
                r = await h.get(f"/api/device/jobs/{uuid.uuid4()}/input", params={"lease_generation": 1}, headers={"authorization": "Bearer " + K1})
                assert r.status_code == 410, r.text
                assert t0 <= seen(D1) and (await state())["devices_status"]["state"] == "recent"
                # ── 여러 장치 = 가장 최근 값 ──
                d4 = imports.device_connect(U, "장치 상태 4")
                D4 = d4["device"]["id"]
                secrets_seen.append(d4["token"].rsplit(".", 1)[1])
                set_seen(D1, T + 100)
                set_seen(D4, 5)
                ds = (await state())["devices_status"]
                assert ds["state"] == "recent" and ds["last_seen_at"] == seen(D4).isoformat(), ds
                set_seen(D4, T + 2)
                set_seen(D1, T + 50)
                ds = (await state())["devices_status"]
                assert ds["state"] == "offline" and ds["last_seen_at"] == seen(D4).isoformat(), ds
                # ── 유효 임대 = 최근 연결(A'): CLI 실행 중 폴 공백 > 기준이어도 running·임대 만료 전이면 recent, 시각은 꾸미지 않는다 ──
                origin = {"origin": PUBLIC}
                with _job_switch("CONVERSE_ENABLED"):
                    site.dev, site.dev_user = True, "other@example.com"   # 다른 사용자 임대 먼저 — U 에 영향 0
                    do = imports.device_connect(O, "남 장치 상태 DSOTHER")   # 접수 = 연결 장치 1 이상(NO-DEVICE) — 장치 먼저
                    jo = (await h.post("/api/jobs", json={"kind": "converse", "text": "합성 DSJOB 남"}, headers=origin)).json()["id"]
                    DO, KO = do["device"]["id"], do["token"]
                    secrets_seen.append(KO.rsplit(".", 1)[1])
                    a = (await poll(KO)).json()
                    assert a["id"] == jo and (await h.get(f"/api/device/jobs/{jo}/input", params={"lease_generation": a["lease"]["generation"]},
                                                          headers={"authorization": "Bearer " + KO})).status_code == 200
                    set_seen(DO, T + 50)
                    assert (await state("other@example.com"))["devices_status"]["state"] == "recent", "O 자신은 임대로 recent"
                    assert (await state())["devices_status"]["state"] == "offline", "남 사용자 임대 = U 영향 0"
                    site.dev, site.dev_user = True, "tester@example.com"
                    ju = (await h.post("/api/jobs", json={"kind": "converse", "text": "합성 DSJOB"}, headers=origin)).json()["id"]
                    a = (await poll(K1)).json()
                    assert a["id"] == ju, a
                    assert (await h.get(f"/api/device/jobs/{ju}/input", params={"lease_generation": a["lease"]["generation"]},
                                        headers={"authorization": "Bearer " + K1})).status_code == 200
                    assert _job_status(ju)["status"] == "running"
                    set_seen(D1, T + 60)   # 실행 중 폴 0 → 수신 시각은 기준 밖
                    ds = (await state())["devices_status"]
                    assert ds["state"] == "recent" and ds["last_seen_at"] == seen(D4).isoformat(), ("유효 임대 = recent, 시각은 마지막 수신 그대로", ds)
                    _sql("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id=%s", (ju,))
                    assert (await state())["devices_status"]["state"] == "offline", "임대 만료 뒤 = offline"
                    await h.post(f"/api/jobs/{ju}/cancel", headers=origin)
                # ── 남 장치 정보 0: O 가 방금 연결해도 U 는 그대로, U 응답에 O 장치 id·이름·시각 없음 ──
                assert (await poll(KO)).status_code == 204
                so = (await state("other@example.com"))["devices_status"]
                assert so["state"] == "recent" and so["last_seen_at"] == seen(DO).isoformat(), so
                su = await state()
                assert su["devices_status"]["state"] == "offline" and su["devices_status"]["last_seen_at"] == seen(D4).isoformat()
                blob = json.dumps(su["devices_status"], ensure_ascii=False)
                assert DO not in blob and "DSOTHER" not in blob and seen(DO).isoformat() not in blob, "남 장치 정보 0"
                assert set(su["devices_status"]) == {"state", "last_seen_at", "threshold_seconds"}, "장치 id·이름 키 없음"
                # ── 장치 전부 연결 해제 = 미연결(시각도 감춤) ──
                imports.device_revoke(U, D1)
                imports.device_revoke(U, D4)
                assert (await state())["devices_status"] == {"state": "none", "last_seen_at": None, "threshold_seconds": T}
                # ── MCP state_get · db.state 에는 없다(/app 전용) ──
                assert "devices_status" not in db.state(U)
                site.dev, site.dev_user = True, "tester@example.com"
                async with Client(main.mcp) as mc:
                    assert "devices_status" not in val(await mc.call_tool("state_get", {})), "MCP state_get 모양 불변"
    finally:
        site.dev, site.dev_user = real
        for lg in cap_on:
            lg.removeHandler(cap)
    log = out.getvalue()
    sys.stdout.write(log)
    assert logged, "logging 캡처가 실제로 붙었다(httpx 요청 로그)"
    for text, where in ((log, "print·stderr"), ("\n".join(logged), "logging")):
        assert secrets_seen and not any(s in text for s in secrets_seen), (where, "출력에 토큰 비밀값 0")
        assert "DSJOB" not in text and "DSOTHER" not in text, (where, "시험 표식(작업 본문·장치 이름) 0")
    print("OK — 29절 ③-0.2 장치 상태(none·recent·offline 경계 · 등록≠최근 · 서버 수신 시각 · 작업 요청 · 여러 장치 최댓값 · 유효 임대 · 인증 실패 0 · 쓰기 간격 60초"
          " · 남 장치 0 · 기준 max(180, 롱폴×3) · /app 전용 · 비밀값·표식 출력 0(print·logging)) 통과")


def Web_polls_empty():
    return not {k: v for k, v in main.site._polls.items() if v}

# ── 30) 휴지통 PR-1 서버 핵심 — 스키마 · 상태 전이 · 일반 이용 차단 · 물리 정리 · MCP·웹 · 스위치 꺼짐 회귀 (2026-10-23, 구현계획 §4 · r4) ─────────
# 사용자 = tester@·other@example.com 재사용(TEST_EMAILS 불변). run() 맨 끝에서 돌고 시작 때 두 계정을 새로 만든다.
# 활동 원천(activity.trash_op·trash_purge_due)·수신 차단은 trash-flow 의 test_trash_flow.py 가 본다 — 여기는 db 쪽 4종 + 공통 꼴.

@contextmanager
def _trash_switch(on=True):
    saved = os.environ.pop("CRM_TRASH_ENABLED", None)
    if on:
        os.environ["CRM_TRASH_ENABLED"] = "1"
    try:
        yield
    finally:
        os.environ.pop("CRM_TRASH_ENABLED", None)
        if saved is not None:
            os.environ["CRM_TRASH_ENABLED"] = saved


@contextmanager
def _trash_clock(at):
    saved = db.TRASH_CLOCK
    db.TRASH_CLOCK = at
    try:
        yield
    finally:
        db.TRASH_CLOCK = saved


def _trash_row(t, i):
    with db.tx() as c:
        return c.execute(f"SELECT * FROM {t} WHERE id=%s", (i,)).fetchone()


def _trash_age(t, i, days=1):
    """물리 정리 시험 — 옮긴 시각을 어제 이전으로(오늘 옮긴 행은 일일 한도 셈 보존 때문에 다음 날 정리)·기한도 지난 것으로."""
    _sql(f"UPDATE {t} SET trashed_at = trashed_at - make_interval(days => %s), last_trashed_at = last_trashed_at - make_interval(days => %s), "
         "purge_at = now() - interval '1 second' WHERE id=%s", (days, days, i))


def test_trash_schema():
    """30a — 5종 휴지통 열·CHECK·부분 인덱스 · 사람 별칭 NULL 허용 + 표식 CHECK · 접수 deleted 상태 · init_schema 2회 멱등 ·
    새 사용자 표 없음(DELETE_TABLES 정적 대조는 24절이 그대로 본다) · 옛 DB(열 없음)에서 _migrate_trash 가 같은 모양."""
    db.init_schema()
    db.init_schema()
    with db.tx() as c:
        for t in db.TRASH_TABLES:
            cols = {r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                                                         "AND table_name=%s", (t,)).fetchall()}
            assert {"trashed_at", "purge_at", "last_trashed_at"} <= cols, (t, cols)
            assert db._has_constraint(c, t, f"ck_{t}_trash"), t
            for ix in (f"ix_{t}_purge", f"ix_{t}_trash"):
                d = c.execute("SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND indexname=%s", (ix,)).fetchone()
                assert d and "trashed_at IS NOT NULL" in d["indexdef"].split(" WHERE ", 1)[1], (ix, d)
        assert c.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='people' "
                         "AND column_name='alias'").fetchone()["is_nullable"] == "YES"
        assert db._has_constraint(c, "people", "ck_people_purged")
        d = c.execute("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname='import_entries_state_check' "
                      "AND conrelid='import_entries'::regclass").fetchone()["d"]
        assert all(f"'{v}'" in d for v in db.IMPORT_STATES), d
        for ix in ("ix_links_target_record", "ix_activity_links_activity", "ix_activity_links_record", "ix_import_target_record",
                   "ix_import_target_item", "ix_import_deleted_target"):
            assert c.execute("SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname=%s", (ix,)).fetchone(), ix
    assert db.DELETE_TABLES and "trash" not in " ".join(db.DELETE_TABLES), "새 사용자 표 없음 — 표식은 기존 행·열"
    assert "TRASH_ENABLED" in envnames.SWITCHES and not db.trash_on(), "기본 꺼짐"
    # 옛 DB 재현 — 임시 스키마에 열 없는 5표 + 접수 표를 만들고 _migrate_trash 2회 = 같은 모양
    name = f"trash_old_{secrets.token_hex(3)}"
    with db.pool().connection() as c:
        try:
            with c.transaction():
                c.execute(f"CREATE SCHEMA {name}")
                c.execute(f"SET LOCAL search_path TO {name}")
                for t in ("records", "items", "source_reflections", "activities"):
                    c.execute(f"CREATE TABLE {t} (user_id text, id text, deleted_at timestamptz)")
                c.execute("CREATE TABLE people (user_id text, id text, alias text NOT NULL, identity_hmac bytea)")
                c.execute("CREATE TABLE item_links (user_id text, target_record_id text)")
                c.execute("CREATE TABLE activity_links (user_id text, activity_id text, record_id text)")
                c.execute("CREATE TABLE import_entries (user_id text, target_record_id text, target_item_id text, "
                          "state text NOT NULL CHECK (state IN ('pending','applied','noop','excluded','expired')))")
                c.execute(f"INSERT INTO people VALUES ('u','p','옛 사람', NULL)")
                db._migrate_trash(c)
                db._migrate_trash(c)
                assert db._has_col(c, "people", "purged_at") and db._has_constraint(c, "records", "ck_records_trash")
                assert db._has_col(c, "import_entries", "deleted_target_id")
                c.execute("INSERT INTO import_entries (state) VALUES ('deleted')")   # 넓힌 CHECK
                assert c.execute("SELECT alias FROM people").fetchone()["alias"] == "옛 사람", "기존 행 그대로"
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
    print("OK — 30a 휴지통 스키마(5종 열·CHECK·부분 인덱스 · 사람 표식 · 접수 deleted · init 2회 · 옛 DB 이전 2회 멱등 · 새 표 0)")


def _trash_seed(U):
    """U 의 재료 — 기준 오브젝트(evidence_claims 자동) · 기록 2(후보 하나씩) · 기준 P(후보 근거 2) · 사람 2(HMAC 하나) · 출처·생각.
    표식 문자열(TRASHMK*)로 일반 경로 응답에 휴지통 본문이 실리는지 본다."""
    rp = db.definition_propose(U, None, 0, {"key": "principles", "label": "기준", "semantic_kind": "principle", "fields": []})
    PR = db.definition_apply(U, rp["object_id"], rp["proposal_id"], 0)["id"]
    r1 = db.record_add(U, "TRASHMK-T1 휴지통 글", "TRASHMK-B1 첫 근거 문장이다. 나머지 글.")
    r2 = db.record_add(U, "보통 글", "둘째 근거 문장이다. 나머지.")
    (c1,), _ = db.candidates_propose(U, r1["id"], "", [{"text": "TRASHMK-C1 판단", "quote": "TRASHMK-B1 첫 근거 문장이다"}], "ai")
    (c2,), _ = db.candidates_propose(U, r2["id"], "", [{"text": "둘째 판단", "quote": "둘째 근거 문장이다"}], "ai")
    db.candidate_decide(U, c1, "confirm")
    db.candidate_decide(U, c2, "confirm")
    ip = db.item_propose(U, PR, 1, None, 0, "P-1", "TRASHMK-P 기준 제목", "기준 본문", {},
                         [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c1},
                          {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c2}])
    P = db.item_decide(U, ip["item_id"], 0, ip["proposal_id"], "apply")
    p_plain = db.person_save(U, "TRASHMK-사람 별칭")["id"]
    p_h = db.new_id("p")
    _sql("INSERT INTO people (user_id,id,alias,hmac_namespace,identity_hmac) VALUES (%s,%s,'HMAC 사람','hn_trash',%s)",
         (U, p_h, secrets.token_bytes(32)))
    _sql("INSERT INTO activity_links (user_id,id,record_id,person_id,relation) VALUES (%s,%s,%s,%s,'author')",
         (U, db.new_id("al"), r2["id"], p_plain))
    s = db.source_create(U, "url", "합성 출처", "https://example.com/trash")["id"]
    refl = db.source_reflection_save(U, s, "summary", "TRASHMK-R 읽고 든 생각 본문")
    return {"PR": PR, "r1": r1["id"], "r2": r2["id"], "c1": c1, "c2": c2, "P": P["id"], "p_plain": p_plain, "p_h": p_h, "s": s,
            "refl": refl["id"]}


def _trash_general(U):
    """일반 이용 경로 한 벌(db) — 응답 JSON 하나로 묶는다. 휴지통 본문·제목·인용·별칭 표식이 없어야 한다."""
    out = {"state": db.state(U), "home": db.me_home(U), "stats": db.me_stats(U), "people": db.people_all(U),
           "person_list": db.person_list(U), "refl": db.reflections_all(U), "refl_list": db.source_reflection_list(U),
           "sources": db.source_counts(U)}
    for it in out["state"]["items"]:
        out.setdefault("item_get", []).append(db.item_get(U, it["id"]))
    return json.dumps(out, ensure_ascii=False, default=str)


def test_trash_off():
    """30b — 스위치 꺼짐 회귀: 휴지통 진입 = SWITCH-OFF(db) · 기존 사람 삭제(연결 있으면 PERSON-IN-USE, 없으면 행 삭제) · 생각 즉시 삭제
    · 기준 남기지 않기·되돌리기 그대로(revision +1) · 일반 이용은 같다."""
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    with _trash_switch(False):
        S = _trash_seed(U)
        for fn, a in ((db.trash_move, ("record", S["r1"])), (db.trash_restore, ("record", S["r1"])), (db.trash_purge, ("record", S["r1"])),
                      (db.trash_list, ()), (db.trash_get, ("record", S["r1"]))):
            expect("SWITCH-OFF", fn, U, *a)
        assert _trash_row("records", S["r1"])["trashed_at"] is None, "꺼짐 = 데이터 불변"
        expect("PERSON-IN-USE", db.person_delete, U, S["p_plain"], 0)
        p3 = db.person_save(U, "지울 사람")["id"]
        assert db.person_delete(U, p3, 0) == {"id": p3, "deleted": True} and _trash_row("people", p3) is None, "옛 즉시 삭제"
        assert db.source_reflection_delete(U, S["refl"], 0) == {"id": S["refl"], "deleted": True} and _trash_row("source_reflections", S["refl"]) is None
        P = db.item_get(U, S["P"])
        x = db.item_decide(U, S["P"], P["revision"], None, "exclude")
        assert x["decision"] == "excluded" and x["revision"] == P["revision"] + 1
        y = db.item_decide(U, S["P"], x["revision"], None, "pending")
        assert y["decision"] == "pending" and y["revision"] == P["revision"] + 2
    print("OK — 30b 스위치 꺼짐 회귀(SWITCH-OFF 5 · 데이터 불변 · PERSON-IN-USE · 즉시 삭제 · 남기지 않기·되돌리기 그대로)")


def test_trash_core():
    """30c — 내 글·항목·사람·생각: 이동(소유자·revision·연결 있어도)·중복 무연장·일반 이용 차단 전 경로(목록·상세·직접 ID·관계·집계·state)
    ·직접 쓰기 IN-TRASH · 휴지통 목록·상세(본인만) · 복원(같은 ID·업무 상태·관계 다시 보임) · 관계 부활 방지 · 168h 경계(서버 시각 주입)
    · 재삭제 새 기한 · 수동 영구 삭제(즉시 복원 불가·요청≠제거) · 복원/영구 삭제 경합 하나만 · item_decide exclude·pending ACTION-RETIRED
    · 축 AXIS-IN-POLICY · 400MB 넘어도 이동 · last_trashed_at 남음 · 한도 셈 유지."""
    import concurrent.futures
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    O = db.user_by_email("other@example.com")["id"]
    with _trash_switch(True):
        S = _trash_seed(U)
        before = _trash_general(U)
        assert "TRASHMK-T1" in before and "TRASHMK-C1" in before and "TRASHMK-사람" in before and "TRASHMK-R" in before, "재료 확인"
        home0 = db.me_home(U)["counts"]
        # ── 남의 것 = 없는 것(이동·복원·영구 삭제·상세) ──
        for fn in (db.trash_move, db.trash_restore, db.trash_purge, db.trash_get):
            expect("NOT-FOUND", fn, O, "record", S["r1"])
        expect("ARG-INVALID", db.trash_move, U, "nope", "x")
        expect("ARG-INVALID", db.trash_move, U, "record", S["r1"], 3)   # record 는 revision 없음
        expect("ARG-INVALID", db.trash_move, U, "person", S["p_plain"])   # person 은 revision 필수
        expect("CONFLICT", db.trash_move, U, "person", S["p_plain"], 9)
        assert _trash_row("people", S["p_plain"])["trashed_at"] is None, "거절 = 불변"
        # ── 이동: 기록 · 사람(연결 있어도) · 생각 ──
        m = db.trash_move(U, "record", S["r1"])
        r = _trash_row("records", S["r1"])
        assert m["changed"] and m["restorable"] and r["purge_at"] - r["trashed_at"] == db._dt.timedelta(hours=168), (m, r)
        assert r["last_trashed_at"] == r["trashed_at"]
        m2 = db.trash_move(U, "record", S["r1"])
        assert not m2["changed"] and m2["purge_at"] == m["purge_at"], "중복 삭제 = 기존 결과(기한 연장 0)"
        rp = db.person_get(U, S["p_plain"])["revision"]
        pm = db.trash_move(U, "person", S["p_plain"], rp)
        assert pm["changed"] and _trash_row("people", S["p_plain"])["revision"] == rp, "revision 그대로"
        assert db.trash_move(U, "reflection", S["refl"], 0)["changed"]
        # ── 일반 이용 차단(전 경로) — 표식 문자열이 응답에 없다. 독립 대상(다른 글·기준 P·기록-사람 연결의 기록)은 그대로 ──
        after = _trash_general(U)
        for mk in ("TRASHMK-T1", "TRASHMK-B1", "TRASHMK-C1", "TRASHMK-사람", "TRASHMK-R"):
            assert mk not in after, mk
        assert "TRASHMK-P" in after and "둘째 판단" in after, "독립 대상 유지"
        expect("RECORD-MISSING", db.record_get, U, S["r1"])
        expect("ARG-INVALID", db.candidate_get, U, S["c1"])
        expect("NOT-FOUND", db.person_get, U, S["p_plain"])
        expect("NOT-FOUND", db.source_reflection_get, U, S["refl"])
        assert S["c1"] not in {x["id"] for x in db.candidates(U)}
        P = db.item_get(U, S["P"])
        assert [ln["target_id"] for ln in P["links"]] == [S["c2"]] and {t["target_id"] for t in P["targets"]} == {S["c2"]}, P["links"]
        assert P["promotion"]["linked_claims"] == 1 and P["promotion"]["confirmed_claims"] == 1, P["promotion"]
        h = db.me_home(U)["counts"]
        assert (h["claims"], h["people"], h["reflections"]) == (home0["claims"] - 1, home0["people"] - 1, home0["reflections"] - 1), (h, home0)
        k = db.me_stats(U)["kinds"]
        assert k["records"] == 1 and k["people"] == 1, k
        assert db.source_get(U, S["s"])["counts"]["summaries"] == 0
        with db.tx() as c:
            assert db._resolve_target(c, U, "record", S["r1"]) is None and db._resolve_target(c, U, "candidate", S["c1"]) is None
            assert db._resolve_target(c, U, "person", S["p_plain"]) is None
        # 직접 쓰기 = IN-TRASH(데이터 불변)
        expect("IN-TRASH", db.record_attach, U, S["r1"], use_at="x")
        expect("IN-TRASH", db.candidates_propose, U, S["r1"], "", [{"text": "t", "quote": "나머지 글"}], "ai")
        expect("IN-TRASH", db.record_provenance_set, U, S["r1"], "unknown", "unknown", "사유")
        crev = _trash_row("candidates", S["c1"])["revision"]
        expect("IN-TRASH", db.candidate_decide, U, S["c1"], "exclude")
        assert _trash_row("candidates", S["c1"])["revision"] == crev
        expect("IN-TRASH", db.person_save, U, "새 별칭", S["p_plain"], rp)
        expect("IN-TRASH", db.source_reflection_save, U, S["s"], "summary", "고침", S["refl"], 0)
        expect("IN-TRASH", db.source_reflection_review, U, S["refl"], 0, True)
        expect("LINK-INVALID", db.item_propose, U, S["PR"], 1, None, 0, "P-2", "새 기준", "", {},
               [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": S["c1"]}])
        # ── 휴지통 목록·상세(본인만, 본문은 상세만) ──
        L = db.trash_list(U)
        got = {(x["kind"], x["id"]) for x in L["items"]}
        assert got == {("record", S["r1"]), ("person", S["p_plain"]), ("reflection", S["refl"])} and L["hours"] == 168, got
        assert "TRASHMK-B1" not in json.dumps(L, ensure_ascii=False), "목록엔 본문 없음"
        assert db.trash_list(O)["items"] == [] and db.trash_list(U, "person")["items"][0]["title"] == "TRASHMK-사람 별칭"
        one = db.trash_list(U, limit=1)
        two = db.trash_list(U, cursor=one["next_cursor"], limit=5)
        assert len(one["items"]) == 1 and one["next_cursor"] and len(two["items"]) == 2 and not two["next_cursor"]
        g = db.trash_get(U, "record", S["r1"])
        assert "TRASHMK-B1" in g["target"]["body"] and g["purge_at"] == m["purge_at"]
        expect("NOT-FOUND", db.trash_get, U, "record", S["r2"])   # 휴지통 아님
        # ── 168h 경계(서버 시각 주입): 기한 미만만 복원 ──
        pa = _trash_row("records", S["r1"])["purge_at"]
        with _trash_clock(pa):
            expect("TRASH-EXPIRED", db.trash_restore, U, "record", S["r1"])
            expect("TRASH-EXPIRED", db.trash_get, U, "record", S["r1"])
            assert ("record", S["r1"]) not in {(x["kind"], x["id"]) for x in db.trash_list(U)["items"]}, "기한 = 목록에서 빠짐"
            assert not db.trash_move(U, "record", S["r1"])["restorable"]
        with _trash_clock(pa - db._dt.timedelta(milliseconds=1)):
            assert db.trash_restore(U, "record", S["r1"])["restored"]
        # ── 복원: 같은 ID·관계 다시 보임·결정 그대로 · last_trashed_at 남음(그 전 작업 무효 판정용) ──
        r = _trash_row("records", S["r1"])
        assert r["trashed_at"] is None and r["purge_at"] is None and r["last_trashed_at"] is not None
        assert db.record_get(U, S["r1"])["id"] == S["r1"] and db.candidate_get(U, S["c1"])["status"] == "confirmed"
        assert {ln["target_id"] for ln in db.item_get(U, S["P"])["links"]} == {S["c1"], S["c2"]}, "상대가 살아나면 관계 다시 보임"
        expect("NOT-IN-TRASH", db.trash_restore, U, "record", S["r1"])
        expect("NOT-IN-TRASH", db.trash_purge, U, "record", S["r1"])
        # ── 재삭제 = 새 168h ──
        m3 = db.trash_move(U, "record", S["r1"])
        assert m3["changed"] and m3["purge_at"] > m["purge_at"]
        # ── 관계 부활 방지: 휴지통 동안 P 의 관계를 바꾸면(c1 연결 제거) 복원해도 돌아오지 않는다 ──
        P = db.item_get(U, S["P"])
        ip = db.item_propose(U, S["PR"], 1, S["P"], P["revision"], None, P["title"], P["body"], {},
                             [{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": S["c2"]}])
        db.item_decide(U, S["P"], P["revision"], ip["proposal_id"], "apply")
        db.trash_restore(U, "record", S["r1"])
        assert [ln["target_id"] for ln in db.item_get(U, S["P"])["links"]] == [S["c2"]], "그사이 지운 관계는 되살리지 않는다"
        # ── 수동 영구 삭제: 즉시 복원 불가 · 요청 ≠ 제거(행 남음) · 반복 = 같은 결과 ──
        db.trash_move(U, "record", S["r1"])
        pr = db.trash_purge(U, "record", S["r1"])
        assert pr == {"kind": "record", "id": S["r1"], "purge_requested": True, "changed": True}
        assert not db.trash_purge(U, "record", S["r1"])["changed"]
        expect("TRASH-EXPIRED", db.trash_restore, U, "record", S["r1"])
        assert _trash_row("records", S["r1"]) is not None, "물리 제거는 정리 회차"
        expect("RECORD-MISSING", db.record_get, U, S["r1"])
        # ── 복원 / 영구 삭제 경합: 하나만 바꾼다 ──
        db.trash_restore(U, "person", S["p_plain"])
        for _ in range(3):
            db.trash_move(U, "person", S["p_plain"], rp)

            def go(fn):
                try:
                    return fn(U, "person", S["p_plain"])["changed"]
                except db.CrmError as e:
                    return e.code
            with concurrent.futures.ThreadPoolExecutor(2) as ex:
                res = list(ex.map(go, (db.trash_restore, db.trash_purge)))
            assert sorted(map(str, res)) in (["NOT-IN-TRASH", "True"], ["TRASH-EXPIRED", "True"]), res
            p = _trash_row("people", S["p_plain"])
            if p["trashed_at"] is not None:   # 영구 삭제가 이겼다 — 다음 반복을 위해 시험이 되돌린다
                _sql("UPDATE people SET trashed_at=NULL, purge_at=NULL WHERE id=%s", (S["p_plain"],))
        # ── 항목: revision 필수 · ACTION-RETIRED · 축 AXIS-IN-POLICY · 직접 쓰기 IN-TRASH ──
        P = db.item_get(U, S["P"])
        hist = len(P["history"])
        for act in ("exclude", "pending"):
            expect("ACTION-RETIRED", db.item_decide, U, S["P"], P["revision"], None, act)
        assert db.item_get(U, S["P"])["revision"] == P["revision"] and len(db.item_get(U, S["P"])["history"]) == hist, "데이터·revision 불변"
        AX, (a0, a1) = _bi_axes(U)
        d = imports.device_connect(U, "휴지통 시험 장치")["device"]
        imports.device_policy_set(U, d["id"], {"actions": ["like"], "transform_versions": ["board-likes/1"], "axes": [a0]}, d["policy_revision"])
        e = expect("AXIS-IN-POLICY", db.trash_move, U, "item", a0, 1)
        assert "휴지통 시험 장치" in e.message and _trash_row("items", a0)["trashed_at"] is None
        assert db.trash_move(U, "item", a1, 1)["changed"], "정책이 안 쓰는 축은 된다"
        imports.device_revoke(U, d["id"])
        assert db.trash_move(U, "item", a0, 1)["changed"], "연결 해제한 장치의 정책은 막지 않는다"
        rev = db.item_get(U, S["P"])["revision"]
        db.trash_move(U, "item", S["P"], rev)
        expect("ARG-INVALID", db.item_get, U, S["P"])
        expect("IN-TRASH", db.item_decide, U, S["P"], rev, None, "confirm")
        assert S["P"] not in {x["id"] for x in db.items(U)} and "TRASHMK-P" not in json.dumps(db.state(U), ensure_ascii=False)
        assert db.trash_list(U, "item")["items"][0]["object_label"] in ("기준", "축")
        ipz = db.item_propose(U, S["PR"], 1, None, 0, None, "제안만", "", {}, [])
        expect("ARG-INVALID", db.trash_move, U, "item", ipz["item_id"], 0)   # 적용 전 제안 = PR-2
        db.trash_restore(U, "item", S["P"])
        assert db.item_get(U, S["P"])["revision"] == rev
        # ── 400MB 를 넘어도 이동·복원·영구 삭제 요청은 된다(쓰기 관문 면제) — 일반 쓰기는 QUOTA-GLOBAL ──
        real = db._measure_db_size
        db._measure_db_size = lambda: db.DB_QUOTA_BYTES + 1
        db.quota_cache_reset()
        try:
            expect("QUOTA-GLOBAL", db.write_gate, "record_add", {"text": "x"}, U)
            for op in ("trash_move", "trash_restore", "trash_purge"):
                db.write_gate(op, {"kind": "record", "id": S["r2"]}, U, quota=op not in db.QUOTA_EXEMPT)
        finally:
            db._measure_db_size = real
            db.quota_cache_reset()
        # ── 한도 셈 유지: 휴지통 행도 오늘 기록 수에 든다 ──
        with db.tx() as c:
            n = c.execute(f"SELECT count(*) AS n FROM records WHERE user_id=%s AND COALESCE(received_at, created) >= {db._KST_TODAY}",
                          (U,)).fetchone()["n"]
        assert n == 2, n
    print("OK — 30c 휴지통 상태 전이·일반 이용 차단(4종·후보 종속·관계·집계·state·직접 쓰기 IN-TRASH)·목록/상세·168h 경계·복원·관계 부활 방지"
          "·재삭제·영구 삭제 요청·경합·ACTION-RETIRED·AXIS-IN-POLICY·400MB·한도")


def test_trash_purge():
    """30d — 물리 정리: 기한 + 하루 지난 것만(오늘 옮긴 것은 다음 날) · 참조 먼저(후보 관계·기록-사람 연결·접수 deleted) · 후보·이력 CASCADE
    · 항목 이력 함께 · 사람 HMAC = 표식(별칭 NULL·일반 사람 아님)·HMAC 없음 = 행 삭제 · 생각 삭제 · 독립 대상 유지
    · 실패 대상 건너뛰기·다음 회차 재시도 · 상한(수·시간) · 표식은 휴지통 목록에도 없음."""
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    with _trash_switch(True):
        S = _trash_seed(U)
        d = imports.device_connect(U, "정리 시험 장치")["device"]
        ie = db.new_id("ie")
        _sql("INSERT INTO import_entries (user_id,id,stream_id,original_id,source_revision,kind,request_sha256,projection_sha256,state,"
             "is_current_applied,resolved_at,resolved_by,target_record_id,transform_version) VALUES "
             "(%s,%s,%s,'o1','v1','self_text',%s,%s,'applied',true,now(),'user',%s,'self-text/1')",
             (U, ie, d["id"], secrets.token_bytes(32), secrets.token_bytes(32), S["r1"]))
        db.trash_move(U, "record", S["r1"])
        db.trash_move(U, "person", S["p_plain"], 0)
        db.trash_move(U, "person", S["p_h"], 0)
        db.trash_move(U, "reflection", S["refl"], 0)
        hrev = db.item_get(U, S["P"])["revision"]
        # 오늘 옮김 + 영구 삭제 요청 = 아직 물리 제거 안 함(일일 한도 셈 보존)
        db.trash_purge(U, "record", S["r1"])
        out = db.trash_purge_due()
        assert out["records"] == 0 and _trash_row("records", S["r1"]) is not None, out
        for t, i in (("records", S["r1"]), ("people", S["p_plain"]), ("people", S["p_h"]), ("source_reflections", S["refl"])):
            _trash_age(t, i)
        # 실패 주입 — 기록 하나가 실패해도 나머지는 정리되고, 다음 회차에 그 기록도 된다
        real = db._purge_fault

        def fault(t, i):
            if i == S["r1"]:
                raise RuntimeError("주입")
        db._purge_fault = fault
        try:
            out = db.trash_purge_due()
        finally:
            db._purge_fault = real
        assert out["failed"] == 1 and out["records"] == 0 and out["people"] == 2 and out["source_reflections"] == 1, out
        assert _trash_row("records", S["r1"]) is not None, "실패 = 완료 아님(행 남음)"
        out = db.trash_purge_due()
        assert out["records"] == 1 and out["failed"] == 0, out
        assert _trash_row("records", S["r1"]) is None and _trash_row("candidates", S["c1"]) is None
        with db.tx() as c:
            assert c.execute("SELECT count(*) AS n FROM candidate_history WHERE candidate_id=%s", (S["c1"],)).fetchone()["n"] == 0
            assert c.execute("SELECT count(*) AS n FROM item_links WHERE target_candidate_id=%s", (S["c1"],)).fetchone()["n"] == 0
            e = c.execute("SELECT state,target_record_id,is_current_applied,payload,deleted_target_id FROM import_entries WHERE id=%s",
                          (ie,)).fetchone()
            assert e == {"state": "deleted", "target_record_id": None, "is_current_applied": False, "payload": None,
                         "deleted_target_id": S["r1"]}, e
            assert c.execute("SELECT count(*) AS n FROM activity_links WHERE person_id=%s", (S["p_plain"],)).fetchone()["n"] == 0
        assert _trash_row("records", S["r2"]) is not None and db.item_get(U, S["P"])["revision"] == hrev, "독립 대상·기준 revision 그대로"
        assert [ln["target_id"] for ln in db.item_get(U, S["P"])["links"]] == [S["c2"]]
        assert _trash_row("people", S["p_plain"]) is None, "HMAC 없는 사람 = 행 삭제"
        mk = _trash_row("people", S["p_h"])
        assert mk["alias"] is None and mk["purged_at"] is not None and mk["identity_hmac"] is not None and mk["trashed_at"] is not None, mk
        assert S["p_h"] not in {p["id"] for p in db.people_all(U)} and db.trash_list(U, "person")["items"] == [], "표식 = 일반·휴지통 둘 다 아님"
        expect("NOT-FOUND", db.trash_restore, U, "person", S["p_h"])
        expect("NOT-FOUND", db.person_get, U, S["p_h"])
        assert _trash_row("source_reflections", S["refl"]) is None
        assert db.trash_purge_due() == {"records": 0, "items": 0, "people": 0, "source_reflections": 0, "failed": 0, "left": False}, "표식은 다시 안 집는다"
        # 항목 정리 — 이력·판 CASCADE, 들어오는 관계 먼저(기록 attach_to)
        db.record_attach(U, S["r2"], attach_to=[{"target_kind": "item", "target_id": S["P"]}])
        assert any(x["target_id"] == S["P"] for x in db.record_links(U, [S["r2"]]))
        db.trash_move(U, "item", S["P"], hrev)
        assert not any(x["target_id"] == S["P"] for x in db.record_links(U, [S["r2"]])), "휴지통 대상 관계 숨김"
        _trash_age("items", S["P"])
        assert db.trash_purge_due()["items"] == 1 and _trash_row("items", S["P"]) is None
        with db.tx() as c:
            assert c.execute("SELECT count(*) AS n FROM item_history WHERE item_id=%s", (S["P"],)).fetchone()["n"] == 0
            assert c.execute("SELECT count(*) AS n FROM item_links WHERE target_item_id=%s", (S["P"],)).fetchone()["n"] == 0
        # 상한 — 대상 수 0·시간 0 이면 아무것도 안 하고 「남김」
        r3 = db.record_add(U, "또 지울 글", "본문")["id"]
        db.trash_move(U, "record", r3)
        _trash_age("records", r3)
        assert db.trash_purge_due(limit=0)["left"] and db.trash_purge_due(seconds=0)["left"] and _trash_row("records", r3) is not None
        assert db.trash_purge_due()["records"] == 1
    print("OK — 30d 물리 정리(하루 미룸·참조 먼저·접수 deleted·CASCADE·사람 표식/삭제·생각·독립 대상 유지·실패 건너뛰기·재시도·상한·표식 재집계 0)")


async def test_trash_web_mcp():
    """30e — 웹 /api/trash*(401·Origin 403·꺼짐 503·모양 400·목록·상세·이동·복원·영구 삭제·남의 것 404) · /api/state switches.trash
    · 사람·생각 삭제 라우트 = 켜짐이면 휴지통(PERSON-IN-USE 없음) · MCP trash_*(꺼짐 SWITCH-OFF · 왕복) · item_decide exclude ACTION-RETIRED
    · state_get 에 휴지통 본문 없음 · lifespan 이 휴지통 정리를 기다리지 않는다(_purger 첫 회차)."""
    cleanup()
    U = db.user_by_email("tester@example.com")["id"]
    O = db.user_by_email("other@example.com")["id"]
    S = _trash_seed(U)
    site = main.site
    real = site.dev, site.dev_user
    prev = os.environ.get("CRM_DEV_USER")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            def who(email):
                site.dev, site.dev_user = True, email

            async def post(path, body, origin=True):
                return await h.post(path, json=body, headers={"origin": PUBLIC} if origin else {})
            who("tester@example.com")
            with _trash_switch(False):
                assert (await h.get("/api/state")).json()["switches"]["trash"] is False
                assert (await h.get("/api/trash")).status_code == 503
                r = await post("/api/trash/move", {"kind": "record", "id": S["r1"]})
                assert r.status_code == 503 and r.json()["code"] == "SWITCH-OFF"
                os.environ["CRM_DEV_USER"] = "tester@example.com"
                async with Client(main.mcp) as c:
                    assert _prb_err(await c.call_tool("trash_list", {})) == "SWITCH-OFF"
                    assert _prb_err(await c.call_tool("trash_move", {"kind": "record", "target_id": S["r1"], "expected_revision": None})) == "SWITCH-OFF"
            with _trash_switch(True):
                assert (await h.get("/api/state")).json()["switches"]["trash"] is True
                site.dev = False
                assert (await h.get("/api/trash")).status_code == 401
                who("tester@example.com")
                assert (await post("/api/trash/move", {"kind": "record", "id": S["r1"]}, origin=False)).status_code == 403
                assert (await post("/api/trash/move", {"kind": "record"})).status_code == 400
                assert (await post("/api/trash/nope", {"kind": "record", "id": S["r1"]})).status_code == 404
                r = await post("/api/trash/move", {"kind": "record", "id": S["r1"]})
                assert r.status_code == 200 and r.json()["changed"] and r.headers["cache-control"] == "no-store", r.text
                st = (await h.get("/api/state")).json()
                assert "TRASHMK-T1" not in json.dumps(st, ensure_ascii=False) and "TRASHMK-C1" not in json.dumps(st, ensure_ascii=False)
                L = (await h.get("/api/trash")).json()
                assert [x["id"] for x in L["items"]] == [S["r1"]], L
                g = await h.get(f"/api/trash/record/{S['r1']}")
                assert g.status_code == 200 and "TRASHMK-B1" in g.json()["target"]["body"]
                assert (await h.get(f"/api/journal?id={S['r1']}")).status_code == 404, "일반 상세 = 없음"
                # 사람 삭제 라우트 — 켜짐 = 휴지통(연결 있어도)
                r = await post(f"/api/people/{S['p_plain']}/delete", {"expected_revision": 0})
                assert r.status_code == 200 and r.json()["trashed"] and r.json()["deleted"], r.text
                r = await post(f"/api/reflections/{S['refl']}/delete", {"expected_revision": 0})
                assert r.status_code == 200 and r.json()["trashed"]
                assert _trash_row("source_reflections", S["refl"])["trashed_at"] is not None
                # 남의 것 = 404
                who("other@example.com")
                assert (await h.get(f"/api/trash/record/{S['r1']}")).status_code == 404
                assert (await post("/api/trash/restore", {"kind": "record", "id": S["r1"]})).status_code == 404
                who("tester@example.com")
                r = await post("/api/trash/restore", {"kind": "record", "id": S["r1"]})
                assert r.status_code == 200 and r.json()["restored"]
                r = await post("/api/trash/purge", {"kind": "person", "id": S["p_plain"]})
                assert r.status_code == 200 and r.json()["purge_requested"]
                r = await post("/api/trash/restore", {"kind": "person", "id": S["p_plain"]})
                assert r.status_code == 400 and r.json()["code"] == "TRASH-EXPIRED"
                rev = db.item_get(U, S["P"])["revision"]
                r = await post("/api/item/decide", {"item_id": S["P"], "expected_revision": rev, "action": "exclude"})
                assert r.status_code == 400 and r.json()["code"] == "ACTION-RETIRED"
                # MCP 왕복
                os.environ["CRM_DEV_USER"] = "tester@example.com"
                async with Client(main.mcp) as c:
                    m = val(await c.call_tool("trash_move", {"kind": "reflection", "target_id": S["refl"], "expected_revision": 0}))
                    assert not m["changed"], "이미 휴지통 = 같은 결과"
                    L = val(await c.call_tool("trash_list", {}))
                    assert {(x["kind"], x["id"]) for x in L["items"]} == {("reflection", S["refl"])}, L
                    assert val(await c.call_tool("trash_restore", {"kind": "reflection", "target_id": S["refl"], "expected_revision": 0}))["restored"]
                    assert val(await c.call_tool("trash_move", {"kind": "record", "target_id": S["r1"], "expected_revision": None}))["changed"]
                    st = val(await c.call_tool("state_get", {}))
                    assert "TRASHMK-T1" not in json.dumps(st, ensure_ascii=False)
                    assert _prb_err(await c.call_tool("record_get", {"record_id": S["r1"]})) == "RECORD-MISSING"
                    res = await c.call_tool("item_decide", {"item_id": S["P"], "expected_revision": rev, "proposal_id": None, "action": "pending",
                                                            "history_id": None})
                    assert _prb_err(res) == "ACTION-RETIRED" and "trash_move" in res.content[0].text
                    assert val(await c.call_tool("trash_purge", {"kind": "record", "target_id": S["r1"], "expected_revision": None}))["purge_requested"]
                assert "trash_move" in main.INSTRUCTIONS and "168시간" in main.INSTRUCTIONS
        # lifespan — 휴지통 정리를 기다리지 않는다(정리가 막혀도 서빙 시작)
        import threading
        gate = threading.Event()
        sm = main.mcp.session_manager

        @asynccontextmanager
        async def fake_run():   # 세션 매니저 run() 은 인스턴스당 한 번
            yield
        calls = []

        def slow(*a, **k):
            calls.append("db")
            gate.wait(5)
            return {"records": 0, "items": 0, "people": 0, "source_reflections": 0, "failed": 0, "left": False}
        real_due, real_act = db.trash_purge_due, activity.trash_purge_due
        db.trash_purge_due = slow
        activity.trash_purge_due = lambda *a, **k: {"activities": 0, "failed": 0, "left": False}
        sm.run = fake_run
        try:
            t0 = time.monotonic()
            async with main.lifespan(main.app):
                served = time.monotonic() - t0
                for _ in range(50):
                    if calls:
                        break
                    await asyncio.sleep(0.02)
                assert calls == ["db"], "서빙 시작 뒤 _purger 첫 회차에서 정리"
                gate.set()
            assert served < 3, served
        finally:
            gate.set()
            db.trash_purge_due, activity.trash_purge_due = real_due, real_act
            del sm.run
    finally:
        site.dev, site.dev_user = real
        if prev is None:
            os.environ.pop("CRM_DEV_USER", None)
        else:
            os.environ["CRM_DEV_USER"] = prev
    print("OK — 30e 웹 /api/trash*·state 스위치·사람/생각 삭제 라우트·MCP trash_*·ACTION-RETIRED·state_get 차단·lifespan 비대기")


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
