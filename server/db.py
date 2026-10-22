"""저장소 — Postgres (psycopg 3, 동기, 작은 풀). 스키마는 schema.sql, 설계 근거는 data-model.md.

데이터 흐름: 기록(원문 그대로) → 후보(pending) → 사용자 결정(confirm/exclude/edit/pending) → 이력 보존.
사용자 구분은 users.id. 모든 조회·쓰기는 user_id 로 묶이고, 자식 테이블은 복합 FK 로 다른 사용자 행을 참조 못 한다.
연결: 환경변수 DATABASE_URL (Supabase 는 Session pooler, 5432).
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import datetime as _dt
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

import contract

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
TEMPLATES_DIR = Path(__file__).parent / "templates"   # 동결 템플릿 정의(template_author.py 가 만든다)
BACKFILL_TEMPLATE = "author@1"                         # template_slot 백필 기준 판 — 열이 생기기 전에 시드된 판은 이것뿐

PROFILE_KEYS = {
    "purpose": "이 노트로 하려는 일",
    "refind": "최근 다시 찾았던 자료와 그 이유",
    "daily_views": "매일·매주 열어 보고 싶은 것 (최대 3개)",
    "first_material": "처음 연결할 자기 기록 1종",
    "separate": "따로 빼 둘 민감·회사·타인 자료",
    "help_level": "직접 할 범위와 도움받고 싶은 범위",
}
STATUSES = ("confirmed", "guessed", "empty", "off")
CAND_ACTIONS = {"confirm": "confirmed", "exclude": "excluded", "pending": "pending"}
MAX_CANDIDATES_PER_RECORD = 3


class CrmError(Exception):
    def __init__(self, code, message, action=""):
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


# ── 전역 상한 (α1) ─────────────────────────────────────
# 공개 가입에서 계정 하나가 DB(Supabase Free 500MB, 넘으면 read-only)를 채워 전원의 쓰기를 멈추지 못하게 한다.
# ① 요청 본문은 main.BodyLimit(ASGI) ② 문자열 인자 길이·③ 전역 DB 크기는 쓰기 진입(MCP guard·웹 쓰기 API)에서 이 함수들로.
KB, MB = 1024, 1024 * 1024
MAX_BODY_BYTES = 256 * KB          # ① /mcp·/api/* 요청 본문
LIMIT_BODY = 64 * KB               # ② 기록 본문·항목 본문
LIMIT_TEXT = 1 * KB                # ② 제목·질문·후보 문장, 표에 없는 문자열(ID·출처·코드·메모)
LIMIT_PROFILE_VALUE = 4 * KB       # ② 프로필 값
LIMIT_JSON = 32 * KB               # ② 정의·항목 JSON(직렬화 문자열 길이)
LIMIT_TICKET = 4096                # ② 문의 글(desk-0). 바이트 — 폼 카운터도 UTF-8 바이트로 센다
DB_QUOTA_BYTES = 400 * MB          # ③ 400 × 1024² 바이트 넘으면 비관리자 쓰기 거절
DB_SIZE_TTL = 60                   # ③ 크기 측정 캐시(초)
DB_SIZE_TIMEOUT = 1.0              # ③ 측정용 풀 checkout 대기(초)

# 쓰기 도구(MCP 도구 이름 = 웹 쓰기 API 가 부르는 db 함수 이름) → 필드별 상한(UTF-8 바이트). 표에 없는 문자열은 LIMIT_TEXT.
# 목록 인자의 항목 필드는 "목록[].필드". 이 표의 키가 쓰기 진입 집합이다 — 표에 없는 도구는 읽기로 보고 검사하지 않는다.
# MCP 도구·웹 API 가 아닌 db 쓰기 함수(record_provenance_set 의 reason 등)는 표에 넣지 않고 함수 안에서 check_len 을 부른다
# — 표의 키는 MCP 도구 이름과 맞물려 고정 시험(test_limits 도구 분류)이 본다.
WRITE_LIMITS = {
    "question_add": {"text": LIMIT_TEXT},
    "record_add": {"text": LIMIT_BODY, "title": LIMIT_TEXT},
    "candidates_propose": {"candidates[].text": LIMIT_TEXT, "candidates[].quote": LIMIT_BODY},
    "candidate_decide": {"text": LIMIT_TEXT},
    "profile_set": {"value": LIMIT_PROFILE_VALUE},
    "definition_propose": {"definition_json": LIMIT_JSON},
    "definition_apply": {},
    "item_propose": {"title": LIMIT_TEXT, "body": LIMIT_BODY, "data_json": LIMIT_JSON},
    "item_decide": {},
    "record_attach": {},
    "feedback_send": {"text": LIMIT_TICKET, "tool": LIMIT_TEXT},
    # PR B 코어(나·사람·출처·출처 요약/반응). 별칭·표시 이름은 80자 CHECK 가 따로 있다 — 여기는 바이트 상한
    "display_name_set": {"display_name": LIMIT_TEXT},
    "person_save": {"alias": LIMIT_TEXT},
    "person_delete": {},
    "source_create": {"name": LIMIT_TEXT, "location": LIMIT_TEXT},
    "source_reflection_save": {"body": LIMIT_BODY},
    "source_reflection_review": {},
    "source_reflection_delete": {},
    # R5 결함(PR B 범위 추가) — 출처 판정 정정 · 사용자 오브젝트 보관/복구
    "record_provenance_set": {"reason": LIMIT_TEXT},
    "definition_archive": {},
    "definition_restore": {},
}
# ③ 전역 DB 크기 거절(QUOTA-GLOBAL) 면제 — 저장 공간이 찼다는 문의도 받아야 한다. 길이 검사(②)는 그대로.
# 대신 문의 한도(사용자 하루 10건·전역 100건, ticket_add)가 건다.
QUOTA_EXEMPT = frozenset({"feedback_send"})
# 표시 이름 관문(NAME-REQUIRED, PR B 결정 3) 면제 쓰기 — 문의와 표시 이름 설정 자체. 그 밖의 쓰기 진입(write_gate)은 전부 이름이 있어야 한다.
# 웹 전용 진입(std_choose·nav_accept·사람·요약/반응 경로)도 write_gate 를 지나므로 같은 관문을 받는다. 읽기는 write_gate 를 안 지난다.
NAME_EXEMPT = frozenset({"feedback_send", "display_name_set"})

# 관리자 = users.role='admin'(β2). ③ 전역 DB 크기 거절의 예외 판정도 is_admin(DB 조회)이다. env 는 첫 지정(admin_bootstrap)에만 쓴다.

# ── 처리방침 버전 ──────────────────────────────────────
# 필수 동의 게이트는 없다(처리 근거는 처리방침 「처리 근거」 표 — 계약 이행·정당한 이익). 동의는 선택 동의(아래)만.
# 문서·버전: web.Web.load_consent 가 시작 때 guides 머리글(`vN · 날짜`)에서 채운다 — {"privacy": "v3"(, "terms": …)}. /privacy·화면 표시용.
# agreements 테이블·옛 행은 이력으로 남긴다(새로 쓰지 않는다).
CONSENT_VERSIONS: dict = {}

# ── 선택 동의 (운영 콘솔) ───────────────────────────────
# 필수 아님 — 동의하지 않아도 모든 기능을 쓴다. 지금은 「구조 이름 공유」 하나: 운영자 조회에 현재 이름(오브젝트·필드·선택지)을 보인다.
# 버전 = 공유 범위 문서 버전(처리방침 머리글과 별개). 범위가 바뀌면 상수를 올린다 → 옛 버전 동의는 유효하지 않다.
STRUCT_NAMES_VERSION = "s1"
OPTIONAL_VERSIONS = {"struct_names": STRUCT_NAMES_VERSION}

# ── 일일 한도 (α2) ─────────────────────────────────────
# 계정 하나가 하루(KST 0시 기준, _KST_TODAY)에 쓸 수 있는 양. 각 쓰기 함수의 기존 트랜잭션 안 맨 앞에서 users 행을 잠그고 센다.
# 관리자도 같다(QUOTA-GLOBAL 만 관리자 예외).
DAILY_LIMITS = {"records": 50, "candidates": 150, "decisions": 300, "proposals": 100, "questions": 50,
                "people": 50, "reflections": 50}   # PR B 메인 확정 ⓔ — 사람·출처 요약/반응은 기록과 같은 급(생성만 센다)
_DAILY_NAMES = {"records": "기록은", "candidates": "후보는", "decisions": "결정은", "proposals": "제안은", "questions": "질문은",
                "people": "사람은", "reflections": "출처 요약/반응은"}   # 조사까지
# 종류 → 세는 (테이블, 시각 열[, 추가 조건]) 목록. 합계를 센다.
_DAILY_SOURCES = {
    "records": (("records", "COALESCE(received_at, created)"),),   # board 이전: 이전한 기록의 created 가 과거여도 받은 시각으로 센다
    "candidates": (("candidates", "created"),),
    "decisions": (("candidate_history", "at"), ("item_history", "at")),
    "proposals": (("object_defs", "created_at", "std_id IS NULL"), ("items", "COALESCE(received_at, created_at)")),   # 표준 정의(시드·편입)는 사용자 제안이 아니다
    "questions": (("questions", "created"),),
    "people": (("people", "created"),),
    "reflections": (("source_reflections", "created"),),
}


def _nbytes(v: str) -> int:
    return len(v.encode("utf-8"))


def check_len(name: str, v: str, lim: int):
    """② 문자열 하나의 길이. 넘으면 PAYLOAD-LIMIT(필드명·상한). check_lengths 와 MCP 도구가 아닌 db 쓰기 함수가 같이 쓴다."""
    if _nbytes(v) > lim:
        raise CrmError("PAYLOAD-LIMIT", f"{name} 가 너무 길어요 (상한 {lim} 바이트, 지금 {_nbytes(v)} 바이트).",
                         "줄이거나 나눠서 다시 보내세요.")


def check_lengths(op: str, args: dict):
    """② 쓰기 인자 길이. 넘으면 PAYLOAD-LIMIT(필드명·상한). 문자열·문자열 목록·모델/딕트 목록의 문자열 필드를 본다."""
    table = WRITE_LIMITS.get(op)
    if table is None:
        return

    def one(name, v):
        if isinstance(v, str):
            check_len(name, v, table.get(name, LIMIT_TEXT))

    for name, v in args.items():
        if isinstance(v, (list, tuple)):
            for el in v:
                if hasattr(el, "model_dump"):
                    el = el.model_dump()
                if isinstance(el, dict):
                    for k, sv in el.items():
                        one(f"{name}[].{k}", sv)
                else:
                    one(f"{name}[]", el)
        else:
            one(name, v)


_dbsize_lock = threading.Lock()
_dbsize = {"at": None, "bytes": None}   # bytes=None: 측정 실패(허용)


def _measure_db_size() -> int:
    """지금 DB 크기(바이트). 풀 checkout 은 짧게 기다린다. 시험은 이 함수를 바꿔 끼운다(운영 env 로는 못 바꾼다)."""
    with pool().connection(timeout=DB_SIZE_TIMEOUT) as c:
        return int(c.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"])


def quota_cache_reset():
    with _dbsize_lock:
        _dbsize["at"] = _dbsize["bytes"] = None


def db_size_cached() -> int | None:
    """DB_SIZE_TTL 초 캐시. 만료 때만 측정한다. 측정 실패는 None 으로 같은 시간 캐시(로그 1줄)."""
    with _dbsize_lock:
        t = time.monotonic()
        if _dbsize["at"] is None or t - _dbsize["at"] >= DB_SIZE_TTL:
            try:
                _dbsize["bytes"] = _measure_db_size()
            except Exception as e:
                _dbsize["bytes"] = None
                print(f"[quota] DB 크기 측정 실패 — 쓰기 허용: {type(e).__name__}", flush=True)
            _dbsize["at"] = t
        return _dbsize["bytes"]


def check_global_quota(uid):
    """③ 전역 DB 크기. 넘었으면 관리자만 쓴다. uid 는 문자열 또는 넘었을 때만 부를 함수(평소 uid 조회를 아낀다)."""
    n = db_size_cached()
    if n is None or n <= DB_QUOTA_BYTES:
        return
    if is_admin(uid() if callable(uid) else uid):   # 넘었을 때만 role 을 조회한다
        return
    raise CrmError("QUOTA-GLOBAL", "서비스 저장 공간이 한도에 가까워 지금은 새로 쓸 수 없어요. 읽기는 그대로 돼요.",
                     "운영자에게 알려 주세요.")


def set_consent_versions(versions: dict):
    """처리방침(·약관) 버전을 바꾼다(web.Web.load_consent 가 시작 때). /privacy·화면 표시용 — 쓰기 게이트는 없다."""
    global CONSENT_VERSIONS
    CONSENT_VERSIONS = dict(versions)


def _optional_apply(c, uid, kind, on):
    """선택 동의 변경 한 건 — 호출하는 쪽의 트랜잭션 안에서. 현재 행 FOR UPDATE(없고 켜기면 INSERT … ON CONFLICT 뒤 다시 잠금)
    → 상태 변경 → 로그 1행. 이미 그 상태면 무동작(로그 없음): 켜기 = granted·현재 버전, 끄기 = 행 없음·withdrawn.
    반환 True = 바뀜."""
    ver = OPTIONAL_VERSIONS[kind]
    sel = "SELECT state,version FROM optional_consents WHERE user_id=%s AND kind=%s FOR UPDATE"
    r = _one(c, sel, (uid, kind))
    if r is None:
        if not on:
            return False
        if _one(c, "INSERT INTO optional_consents (user_id,kind,version,state) VALUES (%s,%s,%s,'granted') "
                    "ON CONFLICT (user_id,kind) DO NOTHING RETURNING 1 AS x", (uid, kind, ver)) is None:
            r = _one(c, sel, (uid, kind))   # 동시 요청이 먼저 넣었다 — 그 행을 잠그고 같은 규칙으로
        else:
            c.execute("INSERT INTO optional_consent_log (user_id,kind,version,action) VALUES (%s,%s,%s,'grant')", (uid, kind, ver))
            return True
    if on and r["state"] == "granted" and r["version"] == ver:
        return False
    if not on and r["state"] == "withdrawn":
        return False
    c.execute("UPDATE optional_consents SET state=%s, version=%s, changed_at=now() WHERE user_id=%s AND kind=%s",
              ("granted" if on else "withdrawn", ver, uid, kind))
    c.execute("INSERT INTO optional_consent_log (user_id,kind,version,action) VALUES (%s,%s,%s,%s)",
              (uid, kind, ver, "grant" if on else "withdraw"))
    return True


def optional_consent_set(uid, kind, on):
    """선택 동의 켜기/끄기 — 한 트랜잭션. 반환 {kind, on(유효 동의), changed}."""
    if kind not in OPTIONAL_VERSIONS or not isinstance(on, bool):
        raise CrmError("ARG-INVALID", f"선택 동의는 {', '.join(OPTIONAL_VERSIONS)} 중 하나, on 은 true·false 예요.")
    with tx() as c:
        _user_lock(c, uid)   # PR B — 삭제 CLI 와 직렬화(삭제 뒤 자료 생성 0). 잠금 순서 = users 행 → optional_consents
        changed = _optional_apply(c, uid, kind, on)
    return {"kind": kind, "on": on, "changed": changed}


def optional_consent_ok(uid, kind) -> bool:
    """유효 동의 = state='granted' AND version = 현재 상수."""
    with tx() as c:
        return _one(c, "SELECT 1 AS x FROM optional_consents WHERE user_id=%s AND kind=%s AND state='granted' AND version=%s",
                    (uid, kind, OPTIONAL_VERSIONS.get(kind))) is not None


def name_gate(uid):
    """표시 이름 관문 — users.display_name 이 비면 NAME-REQUIRED. PK 조회 1건. 판정 = display_name IS NULL 하나."""
    with tx() as c:
        r = _one(c, "SELECT display_name FROM users WHERE id=%s", (uid,))
    if r is not None and r["display_name"] is None:
        raise CrmError(contract.NAME_REQUIRED, contract.NAME_REQUIRED_MSG, contract.NAME_REQUIRED_ACT)


def write_gate(op: str, args: dict, uid, quota: bool = True):
    """쓰기 진입 한 곳 — ② 길이 → uid 풀기 → 표시 이름 관문(NAME_EXEMPT 밖) → ③ 전역 크기. MCP guard 와 웹 쓰기 API 가 같은 이 함수를 부른다.
    uid 는 문자열 또는 함수 — 여기서 푼다(MCP 사용 집계가 도구 본문이 실패해도 그 uid 로 센다. 옛 동의 검사가 하던 조회 1회).
    quota=False 는 QUOTA_EXEMPT(문의) 만 — 호출하는 쪽이 넘긴다."""
    check_lengths(op, args)
    uid = uid() if callable(uid) else uid
    if op not in NAME_EXEMPT:
        name_gate(uid)
    if quota:
        check_global_quota(uid)


# ── 일일 한도 헬퍼 (α2) ─────────────────────────────────

def _user_lock(c, uid):
    """사용자 행 잠금 — 같은 사용자의 한도 검사·쓰기를 직렬화한다. 잠금 순서 = users 행 → 기존 자식 잠금.
    FOR NO KEY UPDATE: 게이트끼리는 서로 막지만(직렬화), 자식 INSERT 의 FK 검사(FOR KEY SHARE)와는 충돌하지 않는다.
    FOR UPDATE 면 record_attach(records 잠금 → item_links INSERT 가 users KEY SHARE 대기)와 candidates_propose(users 잠금 → records 잠금 대기)가 교착한다.
    PR B: status='active' 만 — 삭제 CLI(같은 잠금 뒤 status=deleted)가 커밋된 뒤 기다리던 쓰기는 다시 읽어 AUTH-INVALID(삭제 뒤 자료 생성 0)."""
    if not _one(c, "SELECT id FROM users WHERE id=%s AND status='active' FOR NO KEY UPDATE", (uid,)):
        raise CrmError("AUTH-INVALID", "사용자를 찾을 수 없어요. 다시 로그인하세요.")


def _daily_gate(c, uid, kind, adding=1):
    """일일 한도 — users 행을 잠근 뒤 KST 오늘 0시 이후 행을 센다. n + adding > 상한이면 RATE-LIMIT.
    같은 트랜잭션에서 다시 잠가도 된다(이미 잡은 잠금)."""
    _user_lock(c, uid)
    n = 0
    for table, col, *extra in _DAILY_SOURCES[kind]:
        cond = "".join(f" AND {x}" for x in extra)
        n += _one(c, f"SELECT count(*) AS n FROM {table} WHERE user_id=%s AND {col} >= {_KST_TODAY}{cond}", (uid,))["n"]
    lim = DAILY_LIMITS[kind]
    if n + adding > lim:
        raise CrmError("RATE-LIMIT", f"오늘 {_DAILY_NAMES[kind]} {lim}개까지예요.", "내일(KST 0시 이후) 다시 보내 주세요.")


# ── 사용 집계 usage_daily (α2) ──────────────────────────
# 요청 경로는 메모리 카운터만(DB 0). usage_flush 가 5분마다(main.lifespan) 묶어서 upsert. 수면·재배포 때 통상 약 5분치 유실 허용(flush 연속 실패면 더).
# name·code 는 열거형만 — 도구 이름·web:경로 이름 / 오류 코드·OK·EXC. 자유 텍스트·원본 오류 문자열 금지.
KST = ZoneInfo("Asia/Seoul")
USAGE_FLUSH_TIMEOUT = 1.0          # flush 용 풀 checkout 대기(초)
_usage_lock = threading.Lock()
_usage: dict = {}                  # (day_kst, uid or '', name, code) → n
# flush INSERT 와 user_delete tx 를 서로 막는다. flush INSERT 가 시작된 뒤 delete 가 커밋되면 삭제한 uid 행이 남는 창을 닫는다(1인스턴스).
# 잠금 순서: _flush_lock → _usage_lock(user_delete 끝의 키 제거). usage_flush 는 _usage_lock 을 놓은 뒤 _flush_lock 을 잡는다.
_flush_lock = threading.Lock()


def usage_count(uid, name, code):
    """메모리 카운터 +1. DB 호출 없음."""
    key = (_dt.datetime.now(KST).date(), uid or "", name, code)
    with _usage_lock:
        _usage[key] = _usage.get(key, 0) + 1


def usage_snapshot() -> dict:
    with _usage_lock:
        return dict(_usage)


def _usage_conn():
    """flush 용 연결. 시험은 이 함수를 바꿔 끼운다(checkout 실패 주입)."""
    return pool().connection(timeout=USAGE_FLUSH_TIMEOUT)


def usage_flush() -> int:
    """스냅샷을 떼고(lock 안에서 dict 교체) usage_daily 에 합산 upsert. 반환 = 올린 키 수.
    실패하면 그 묶음을 버리고 로그에 건수만 — 예외를 밖으로 내지 않는다.
    로그인 사용자는 항상 그 uid 로 올린다. '' 는 로그인 전(uid 없는) 요청만."""
    global _usage
    with _usage_lock:
        batch, _usage = _usage, {}
    if not batch:
        return 0
    try:
        with _flush_lock, _usage_conn() as c:
            with c.transaction():
                # PR B: 삭제된 계정(status=deleted)의 카운터는 버린다. FOR SHARE = 삭제 CLI 의 사용자 행 잠금과 직렬화(커밋 뒤 상태로 판정)
                ids = sorted({k[1] for k in batch if k[1]})
                gone = {r["id"] for r in c.execute("SELECT id,status FROM users WHERE id = ANY(%s) ORDER BY id FOR SHARE", (ids,)).fetchall()
                        if r["status"] == "deleted"} if ids else set()
                keys = [k for k in batch if k[1] not in gone]
                if not keys:
                    return 0
                days, uids, names, codes = ([k[i] for k in keys] for i in range(4))
                ns = [batch[k] for k in keys]
                c.execute(
                    "INSERT INTO usage_daily (day,user_id,name,code,n) "
                    "SELECT d, u, nm, cd, sum(n)::integer "
                    "FROM unnest(%s::date[], %s::text[], %s::text[], %s::text[], %s::integer[]) AS t(d, u, nm, cd, n) "
                    "GROUP BY 1, 2, 3, 4 "
                    "ON CONFLICT (day,user_id,name,code) DO UPDATE SET n = usage_daily.n + EXCLUDED.n",
                    (days, uids, names, codes, ns))
    except Exception:
        print(f"[usage] flush 실패 — {len(batch)}건 버림", flush=True)
        return 0
    return len(keys)


def now():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().replace(microsecond=0)


def new_id(prefix):
    return f"{prefix}_{secrets.token_urlsafe(8)}"


# ── 연결 ───────────────────────────────────────────────

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise CrmError("DB-CONFIG", "DATABASE_URL 이 없어요.", "서버 환경변수에 Postgres 연결 문자열을 넣으세요.")
        _pool = ConnectionPool(url, min_size=1, max_size=4, kwargs={"row_factory": dict_row}, open=True)
    return _pool


def close():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema(conn=None):
    """스키마를 최종형으로 맞춘다. 몇 번 돌려도 된다. 서버 시작(main.lifespan) 때 운영에도 자동으로 돈다.
    conn 을 주면 그 연결에서(시험: 임시 스키마), 없으면 풀에서. 전체가 한 트랜잭션 — 중간 실패는 전부 롤백.

    순서(바꾸지 않는다):
      1. PR① 마이그레이션(_migrate_pr1) — 옛 테이블이 있을 때만. schema.sql 보다 **먼저** 돈다:
         새 schema.sql 의 ix_hist_cand(user_id, …) 가 옛 candidate_history(user_id 없음)에서 컬럼 오류를 낼 수 있어서.
      2. schema.sql — CREATE … IF NOT EXISTS. 새 DB 는 여기서 바로 최종형, 옛 DB 는 no-op.
      3. 2단계 ALTER — records.notes 추가 · candidates.question_id NULL 허용(기존 행은 안 건드림).
      4. template_slot — object_defs·field_defs 에 ADD COLUMN IF NOT EXISTS → 백필(_backfill_template_slots). 열이 먼저다.
         std_id(PR #20) — 같은 두 테이블에 ADD COLUMN IF NOT EXISTS 만(값은 시드·편입이 채운다, 백필 없음). std_marks·std_choices 는 2 에서.
         records.source_id + fk_records_source(PR #20 출처) — 열·제약이 없을 때만. 옛 기록은 NULL(출처 미확인), 자동 연결 없음.
         분류 역할 이관(_migrate_classify_roles) — 매번, 역할 없는 사용자만.
      5. 1회성 — schema_marks 표식이 없을 때만(INSERT … ON CONFLICT DO NOTHING RETURNING): 처리방침 v2 전환 usage_daily 전체 삭제."""
    if conn is None:
        with pool().connection() as c:
            _init_schema(c)
    else:
        _init_schema(conn)


def _init_schema(c):
    with c.transaction():
        _migrate_pr1(c)
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        col = lambda t, n: c.execute(
            "SELECT is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s AND column_name=%s",
            (t, n)).fetchone()
        if col("records", "notes") is None:
            c.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS notes jsonb NOT NULL DEFAULT '{}'")
        q = col("candidates", "question_id")
        if q is not None and q["is_nullable"] == "NO":
            c.execute("ALTER TABLE candidates ALTER COLUMN question_id DROP NOT NULL")
        for n, ddl in _USERS_B2_COLS:   # β2 — 옛 users 에 역할·차단·끊기 열. schema.sql 최종형과 같은 모양(CHECK 이름 users_role_check)
            if not _has_col(c, "users", n):
                c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {n} {ddl}")
        for n, ddl in _TICKETS_P3_COLS:   # P3 — 옛 tickets 에 답변·공개 갱신 열. schema.sql 최종형과 같은 모양
            if not _has_col(c, "tickets", n):
                c.execute(f"ALTER TABLE tickets ADD COLUMN IF NOT EXISTS {n} {ddl}")
        for n, ddl in _USERS_P5_COLS:   # P5 — 옛 users 에 문의 섹션 숨김 열. schema.sql 최종형과 같은 모양
            if not _has_col(c, "users", n):
                c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {n} {ddl}")
        for n, ddl in _USERS_PRB_COLS:   # PR B — 옛 users 에 표시 이름·상태·삭제 시각·새 탐색 수락. 열 먼저, CHECK 는 이름으로 뒤에
            if not _has_col(c, "users", n):
                c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {n} {ddl}")
        for name, ddl in _USERS_PRB_CHECKS:
            if not _has_constraint(c, "users", name):
                c.execute(f"ALTER TABLE users ADD CONSTRAINT {name} {ddl}")
        for t, name, col, vals in _WIDEN_CHECKS:   # PR B — 열거 CHECK 허용값 확장(기존 값은 그대로). 이미 넓으면 손대지 않는다
            _widen_check(c, t, name, col, vals)
        for t in ("object_defs", "field_defs"):   # 열이 있으면 ALTER 를 안 부른다(IF NOT EXISTS 여도 테이블을 잠근다)
            if not _has_col(c, t, "template_slot"):
                c.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS template_slot text")
        for t in ("object_defs", "field_defs"):   # PR #20 표준 식별자 — 열만. 옛 행은 NULL(표준 아님) 그대로
            if not _has_col(c, t, "std_id"):
                c.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS std_id text")
        if not _has_col(c, "records", "source_id"):   # PR #20 출처 — sources 테이블은 schema.sql 이 먼저 만든다
            c.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS source_id text")
        if not _has_constraint(c, "records", "fk_records_source"):
            c.execute("ALTER TABLE records ADD CONSTRAINT fk_records_source FOREIGN KEY (user_id, source_id) REFERENCES sources(user_id, id)")
        _migrate_board_import(c)   # board 이전(2026-10-20) — 열·이름 붙은 CHECK·부분 UNIQUE·item_links 대상 확장
        _migrate_classify_roles(c)
        _backfill_template_slots(c)   # 매번 돈다 — 시드가 적용 뒤 표시 전에 끊긴 경우도 여기서 채운다
        # 처리방침 v2 전환 — v2 시행 전에 모은 이용 기록(usage_daily)을 1회 전부 지운다. 표식이 있으면 건너뛴다.
        if _one(c, "INSERT INTO schema_marks (key) VALUES (%s) ON CONFLICT (key) DO NOTHING RETURNING key", (USAGE_RESET_MARK,)):
            c.execute("DELETE FROM usage_daily")


_TICKETS_P3_COLS = (("reply", "text CHECK (octet_length(reply) <= 4096)"), ("reply_at", "timestamptz"),
                    ("public_updated", "timestamptz"))
USAGE_RESET_MARK = "usage_daily_reset_privacy_v2"   # schema_marks 키 — 옛 사용량 1회 삭제
_USERS_P5_COLS = (("desk_hidden", "boolean NOT NULL DEFAULT false"),)


_USERS_B2_COLS = (("role", "text NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin'))"), ("blocked_at", "timestamptz"),
                  ("web_revoked_before", "timestamptz"), ("mcp_revoked_before", "timestamptz"))
# PR B — schema.sql users 최종형과 같은 모양. 옛 코드 INSERT(새 열 없이)는 기본값(이름 NULL·active·미수락)으로 성공한다
_USERS_PRB_COLS = (("display_name", "text"), ("status", "text NOT NULL DEFAULT 'active'"), ("deleted_at", "timestamptz"),
                   ("nav_accepted_at", "timestamptz"))
_USERS_PRB_CHECKS = (
    ("ck_users_display_name", "CHECK (display_name IS NULL OR (char_length(display_name) BETWEEN 1 AND 80 AND display_name = btrim(display_name)))"),
    ("ck_users_status", "CHECK (status IN ('active','deleted'))"),
    ("ck_users_deleted", "CHECK ((status = 'deleted') = (deleted_at IS NOT NULL))"))
# 열거 CHECK 확장 — (테이블, 제약 이름(인라인 CHECK 의 자동 이름), 열, 최종 허용값). schema.sql 인라인 값과 같다
_WIDEN_CHECKS = (
    ("admin_audit", "admin_audit_action_check", "action",
     ("bootstrap", "recover", "role_grant", "role_revoke", "web_signout", "block", "unblock", "delete_notify", "delete_run", "delete_cancel",
      "device_connect", "device_reissue", "device_revoke", "device_policy", "migration_apply", "migration_extend", "migration_close")),
    ("ticket_history", "ticket_history_kind_check", "kind", ("status", "reply", "note", "reclassify", "deleted")),
    ("field_defs", "field_defs_target_kind_check", "target_kind", ("item", "record", "candidate", "person", "source", "activity")))

# board 이전(2026-10-20) — 옛 DB 에 더할 열(schema.sql 최종형과 같은 모양). received_at·ingest_kind 는 옛 행 값이 다르다(아래 함수)
_IMPORT_PROV_COLS = (("source_created_at", "timestamptz"), ("source_date", "date"),
                     ("generation_kind", "text NOT NULL DEFAULT 'unknown'"), ("generation_meta", "jsonb NOT NULL DEFAULT '{}'"),
                     ("import_batch_id", "text"))
_IMPORT_PROV_CHECKS = (
    ("ck_{t}_ingest_kind", "CHECK (ingest_kind IN ('legacy','interactive','inbox','board_migration'))"),
    ("ck_{t}_generation_kind", "CHECK (generation_kind IN ('unknown','human','ai','mixed'))"),
    ("ck_{t}_generation_meta", "CHECK (jsonb_typeof(generation_meta) = 'object')"),
    ("ck_{t}_source_when", "CHECK (source_created_at IS NULL OR source_date IS NULL)"))
_ITEM_LINK_TARGETS = (("target_person_id", "fk_item_links_person", "people"), ("target_source_id", "fk_item_links_source", "sources"),
                      ("target_activity_id", "fk_item_links_activity", "activities"))
ITEM_LINK_TARGET_CHECK = ("CHECK (num_nonnulls(target_item_id, target_record_id, target_candidate_id, "
                          "target_person_id, target_source_id, target_activity_id) = 1)")


def _migrate_board_import(c):
    """board 이전 스키마 — 옛 DB 를 schema.sql 최종형과 같은 모양으로. 매번 돌아도 된다(있으면 건드리지 않는다). 새 표 5개는 schema.sql 이 만든다.
    · records·items: received_at(옛 행 NULL → 기본값 now() 는 열을 만든 뒤 건다) · ingest_kind(옛 행 legacy → 기본값 interactive) · 나머지 열 + 이름 붙은 CHECK
    · people: hmac_namespace·identity_hmac + ck_people_identity · sources: activity_provider + ck_sources_activity_provider
    · item_links: target_person/source/activity 열 + 이름 붙은 FK → 대상 CHECK 를 ck_item_links_target 하나로(옛 이름 없는 3열 CHECK 는 정의로 찾아 지운다)
    · 부분 UNIQUE·인덱스(새 열을 쓰므로 schema.sql 이 아니라 여기서)"""
    for t in ("records", "items"):
        if not _has_col(c, t, "received_at"):
            c.execute(f"ALTER TABLE {t} ADD COLUMN received_at timestamptz")
            c.execute(f"ALTER TABLE {t} ALTER COLUMN received_at SET DEFAULT now()")
        if not _has_col(c, t, "ingest_kind"):
            c.execute(f"ALTER TABLE {t} ADD COLUMN ingest_kind text NOT NULL DEFAULT 'legacy'")
            c.execute(f"ALTER TABLE {t} ALTER COLUMN ingest_kind SET DEFAULT 'interactive'")
        for n, ddl in _IMPORT_PROV_COLS:
            if not _has_col(c, t, n):
                c.execute(f"ALTER TABLE {t} ADD COLUMN {n} {ddl}")
        for name, ddl in _IMPORT_PROV_CHECKS:
            if not _has_constraint(c, t, name.format(t=t)):
                c.execute(f"ALTER TABLE {t} ADD CONSTRAINT {name.format(t=t)} {ddl}")
    for n, ddl in (("hmac_namespace", "text"), ("identity_hmac", "bytea")):
        if not _has_col(c, "people", n):
            c.execute(f"ALTER TABLE people ADD COLUMN {n} {ddl}")
    if not _has_constraint(c, "people", "ck_people_identity"):
        c.execute("ALTER TABLE people ADD CONSTRAINT ck_people_identity CHECK ((hmac_namespace IS NULL AND identity_hmac IS NULL) "
                  "OR (hmac_namespace IS NOT NULL AND identity_hmac IS NOT NULL AND octet_length(identity_hmac) = 32))")
    if not _has_col(c, "sources", "activity_provider"):
        c.execute("ALTER TABLE sources ADD COLUMN activity_provider text")
    if not _has_constraint(c, "sources", "ck_sources_activity_provider"):
        c.execute("ALTER TABLE sources ADD CONSTRAINT ck_sources_activity_provider CHECK (activity_provider IS NULL OR "
                  "(activity_provider IN ('sns','web','mail','kakao') AND kind = 'other' AND location = ''))")
    for col, fk, table in _ITEM_LINK_TARGETS:
        if not _has_col(c, "item_links", col):
            c.execute(f"ALTER TABLE item_links ADD COLUMN {col} text")
        if not _has_constraint(c, "item_links", fk):
            c.execute(f"ALTER TABLE item_links ADD CONSTRAINT {fk} FOREIGN KEY (user_id, {col}) REFERENCES {table}(user_id, id)")
    if not _has_constraint(c, "item_links", "ck_item_links_target"):
        # 옛 이름 없는 대상 CHECK(3열 num_nonnulls) 는 이름을 가정하지 않고 정의로 찾는다(PR① 선례). 같은 트랜잭션 — 의미는 추가만
        for r in c.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'item_links'::regclass AND contype = 'c' "
                           "AND pg_get_constraintdef(oid) LIKE '%%num_nonnulls(target_item_id%%'").fetchall():
            c.execute(f'ALTER TABLE item_links DROP CONSTRAINT "{r["conname"]}"')
        c.execute(f"ALTER TABLE item_links ADD CONSTRAINT ck_item_links_target {ITEM_LINK_TARGET_CHECK}")
    idx = [("ux_people_identity", "CREATE UNIQUE INDEX ux_people_identity ON people (user_id, hmac_namespace, identity_hmac) "
                                  "WHERE identity_hmac IS NOT NULL"),
           ("ux_sources_activity_provider", "CREATE UNIQUE INDEX ux_sources_activity_provider ON sources (user_id, activity_provider) "
                                            "WHERE activity_provider IS NOT NULL")]
    idx += [(f"ix_links_{col[7:-3]}", f"CREATE INDEX ix_links_{col[7:-3]} ON item_links (user_id, {col}) WHERE {col} IS NOT NULL")
            for col in ("target_person_id", "target_source_id", "target_activity_id")]
    for name, ddl in idx:   # 있으면 부르지 않는다(IF NOT EXISTS 여도 테이블을 잠근다)
        if not c.execute("SELECT 1 FROM pg_class WHERE relname = %s AND relnamespace = current_schema()::regnamespace",
                         (name,)).fetchone():
            c.execute(ddl)


def _widen_check(c, t, name, col, vals):
    """열거 CHECK 를 vals 로 넓힌다 — 지금 정의에 없는 값이 있을 때만 DROP → 같은 이름으로 ADD(멱등). 옛 값은 vals 에 다 있다."""
    r = _one(c, "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s", (t, name))
    if r and all(f"'{v}'" in r["d"] for v in vals):
        return
    if r:
        c.execute(f"ALTER TABLE {t} DROP CONSTRAINT {name}")
    c.execute(f"ALTER TABLE {t} ADD CONSTRAINT {name} CHECK ({col} IN ({','.join(repr(v) for v in vals)}))")


def template_slot_of(template, object_key, field_key=None):
    """템플릿 슬롯 문자열 — 오브젝트 `author@1:principles` · 필드 `author@1:principles.register`."""
    return f"{template}:{object_key}" + (f".{field_key}" if field_key else "")


def template_frozen(template):
    """동결 템플릿 파일 templates/<key>@<version>.json — {template_key, template_version, objects:[정규화 정의]}."""
    return json.loads((TEMPLATES_DIR / f"{template}.json").read_text(encoding="utf-8"))


def _backfill_template_slots(c):
    """열이 생기기 전에 시드된 정의에 슬롯을 단다. 멱등(template_slot IS NULL 만), 전 사용자 대상.
    오브젝트: id·key 가 동결 템플릿의 같은 쌍과 둘 다 같을 때. 필드: 그 슬롯이 잡힌 오브젝트 아래 key 가 템플릿 필드 key 인 것.
    key 는 비교만 하고 값은 남기지 않는다. 한계: 사용자가 시드 전에 id·key 를 둘 다 템플릿과 똑같이 만들었으면 템플릿으로 잡힌다.
    반환 (오브젝트 수, 필드 수)."""
    tpl = template_frozen(BACKFILL_TEMPLATE)
    name = f"{tpl['template_key']}@{tpl['template_version']}"
    n_obj = n_field = 0
    for d in tpl["objects"]:
        slot = template_slot_of(name, d["key"])
        # 표준 정의(std_id 있음, template_slot NULL — 시드·누락 편입)는 템플릿 계보가 아니다 — id·key 가 같아도 칠하지 않는다
        n_obj += c.execute("UPDATE object_defs SET template_slot=%s WHERE template_slot IS NULL AND std_id IS NULL AND id=%s AND key=%s",
                           (slot, d["id"], d["key"])).rowcount
        # 필드는 오브젝트 슬롯이 이 판으로 잡힌 곳 아래에서만(이번에 잡힌 것 + 시드 표시 중 필드 표시가 빠진 것)
        n_field += c.execute("UPDATE field_defs AS f SET template_slot = %s || '.' || f.key FROM object_defs AS o "
                             "WHERE o.user_id = f.user_id AND o.id = f.object_id AND o.id = %s AND o.template_slot = %s "
                             "AND f.template_slot IS NULL AND f.key = ANY(%s)",
                             (slot, d["id"], slot, [f["key"] for f in d["fields"]])).rowcount
    return n_obj, n_field


def _has_constraint(c, t, name):
    return c.execute("SELECT 1 FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s", (t, name)).fetchone() is not None


def _migrate_classify_roles(c):
    """분류 역할 이관(§5) — key='axes' 인 적용·미보관 오브젝트를 classify 대상으로. 표준 편입 여부와 무관.
    역할이 이미 있는 사용자는 건드리지 않는다(ON CONFLICT DO NOTHING) — 결정적·멱등. 반환 새로 잡은 사용자 수."""
    return c.execute("INSERT INTO user_roles (user_id, role, object_id) SELECT user_id, 'classify', id FROM object_defs "
                     "WHERE key='axes' AND revision > 0 AND archived_at IS NULL ON CONFLICT (user_id, role) DO NOTHING").rowcount


def _has_table(c, t):
    return c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s",
                     (t,)).fetchone() is not None


def _has_col(c, t, n):
    return c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s "
                     "AND column_name=%s", (t, n)).fetchone() is not None


def _migrate_pr1(c):
    """PR① 코어 보수(출처·revision·이력 격리) — 옛 DB 를 schema.sql 최종형과 같은 모양으로. 근거: raw schema-round2 §4.
    부분마다 「테이블 있음 AND 새 컬럼 없음」일 때만 돈다 → 새 DB(테이블 없음)·이미 끝난 DB 는 아무것도 안 한다(멱등).
    호출자(_init_schema)의 트랜잭션 안에서 돈다 — 어느 단계든 실패하면 전체 롤백, 서버 시작 실패.
    옛 writer(prev_decision/prev_text 에 쓰는 이전 코드)와 같이 돌면 안 된다 — 그 컬럼을 지운다.

    실행 순서:
      0. lock_timeout 10s · LOCK records, candidates, candidate_history ACCESS EXCLUSIVE
      1. records: author_kind·text_process(DEFAULT 'unknown' — 기존 행 추측 백필 금지)·provenance_history + CHECK
      2. candidates: 인용 범위 위반 행을 먼저 센다(1 이상이면 중단) → revision(1)·wording_by(NULL)·wording_approved(false) + CHECK
      3. candidate_history: user_id·prev_revision·prev_state 추가 → 부모에서 user_id 백필 → prev_state = v1 부분 스냅샷
         {"schema_version":1,"text":prev_text,"decision":prev_decision} → NULL 남으면 중단 → NOT NULL·CHECK·UNIQUE·복합 FK
         → 옛 단일 FK(candidate_id→candidates.id) 제거 → action CHECK 에 restore → prev_decision·prev_text 삭제
         → ix_hist_cand 를 (user_id, candidate_id, id) 로 다시 만든다
    검증 SQL(적용 뒤, 운영에서 손으로 볼 때):
      SELECT count(*) FROM records WHERE author_kind<>'unknown' OR text_process<>'unknown';           -- 0 (기존 행)
      SELECT count(*) FROM candidates WHERE revision<>1 OR wording_by IS NOT NULL OR wording_approved; -- 0 (기존 행)
      SELECT count(*) FROM candidate_history h LEFT JOIN candidates c ON c.user_id=h.user_id AND c.id=h.candidate_id
        WHERE c.id IS NULL OR h.prev_state->>'schema_version'<>'1' OR h.prev_revision IS NOT NULL;     -- 0 (기존 행)
      SELECT column_name FROM information_schema.columns WHERE table_name='candidate_history';        -- prev_text 없음"""
    if not _has_table(c, "candidate_history"):
        return False                       # 새 DB — schema.sql 이 최종형을 만든다
    needs = lambda: (not _has_col(c, "records", "author_kind"), not _has_col(c, "candidates", "revision"),
                     not _has_col(c, "candidate_history", "prev_state"))
    if not any(needs()):
        return False                       # 이미 최종형 — 잠금 없이 끝
    c.execute("SET LOCAL lock_timeout = '10s'")
    c.execute("LOCK TABLE records, candidates, candidate_history IN ACCESS EXCLUSIVE MODE")
    need_rec, need_cand, need_hist = needs()   # 잠근 뒤 다시 본다(동시에 시작한 다른 인스턴스가 먼저 끝냈을 수 있다)
    if need_rec:
        c.execute("""
            ALTER TABLE records
              ADD COLUMN author_kind text NOT NULL DEFAULT 'unknown',
              ADD COLUMN text_process text NOT NULL DEFAULT 'unknown',
              ADD COLUMN provenance_history jsonb NOT NULL DEFAULT '[]'::jsonb,
              ADD CONSTRAINT ck_records_author_kind CHECK (author_kind IN ('self','external','unknown')),
              ADD CONSTRAINT ck_records_text_process
                CHECK (text_process IN ('verbatim','structured','rewritten','generated','unknown')),
              ADD CONSTRAINT ck_records_provenance_history CHECK (jsonb_typeof(provenance_history) = 'array')""")
    if need_cand:
        bad = c.execute("SELECT count(*) AS n FROM candidates WHERE NOT (quote_start >= 0 AND quote_end > quote_start "
                        "AND char_length(quote) = quote_end - quote_start)").fetchone()["n"]
        if bad:
            raise CrmError("DB-MIGRATE", f"PR① 마이그레이션 중단: 인용 범위가 맞지 않는 후보 {bad}개 — 전체 롤백했어요.",
                             "그 후보의 quote·quote_start·quote_end 를 먼저 확인하세요.")
        c.execute("""
            ALTER TABLE candidates
              ADD COLUMN revision integer NOT NULL DEFAULT 1,
              ADD COLUMN wording_by text,
              ADD COLUMN wording_approved boolean NOT NULL DEFAULT false,
              ADD CONSTRAINT ck_candidates_revision CHECK (revision > 0),
              ADD CONSTRAINT ck_candidates_wording_by CHECK (wording_by IN ('self','ai')),
              ADD CONSTRAINT ck_candidates_wording_approval CHECK (NOT wording_approved OR wording_by IS NOT NULL),
              ADD CONSTRAINT ck_candidates_quote_bounds CHECK (quote_start >= 0 AND quote_end > quote_start
                                                               AND char_length(quote) = quote_end - quote_start)""")
    if need_hist:
        c.execute("ALTER TABLE candidate_history ADD COLUMN user_id text, ADD COLUMN prev_revision integer, ADD COLUMN prev_state jsonb")
        c.execute("UPDATE candidate_history AS h SET user_id = c.user_id FROM candidates AS c WHERE c.id = h.candidate_id")
        c.execute("UPDATE candidate_history SET prev_state = jsonb_build_object('schema_version', 1, 'text', prev_text, "
                  "'decision', prev_decision)")
        left = c.execute("SELECT count(*) AS n FROM candidate_history WHERE user_id IS NULL OR prev_state IS NULL "
                         "OR prev_state->>'text' IS NULL OR prev_state->>'decision' IS NULL").fetchone()["n"]
        if left:
            raise CrmError("DB-MIGRATE", f"PR① 마이그레이션 중단: candidate_history 백필 실패 {left}행(부모 없음 또는 스냅샷 없음) — 전체 롤백했어요.")
        c.execute("""
            ALTER TABLE candidate_history
              ALTER COLUMN user_id SET NOT NULL,
              ALTER COLUMN prev_state SET NOT NULL,
              ADD CONSTRAINT ck_candidate_history_prev_revision CHECK (prev_revision IS NULL OR prev_revision > 0),
              ADD CONSTRAINT ck_candidate_history_prev_state CHECK (jsonb_typeof(prev_state) = 'object'
                AND prev_state ? 'schema_version' AND prev_state ? 'text' AND prev_state ? 'decision'),
              ADD CONSTRAINT uq_candidate_history_revision UNIQUE (user_id, candidate_id, prev_revision),
              ADD CONSTRAINT fk_candidate_history_tenant FOREIGN KEY (user_id, candidate_id)
                REFERENCES candidates(user_id, id) ON UPDATE RESTRICT ON DELETE CASCADE""")
        # 옛 단일 FK·action CHECK 는 이름을 가정하지 않고 카탈로그에서 찾는다
        for r in c.execute("""
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'candidate_history'::regclass AND contype = 'f'
                  AND confrelid = 'candidates'::regclass AND array_length(conkey, 1) = 1""").fetchall():
            c.execute(f'ALTER TABLE candidate_history DROP CONSTRAINT "{r["conname"]}"')
        for r in c.execute("""
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'candidate_history'::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%%action%%'""").fetchall():
            c.execute(f'ALTER TABLE candidate_history DROP CONSTRAINT "{r["conname"]}"')
        c.execute("ALTER TABLE candidate_history ADD CONSTRAINT candidate_history_action_check "
                  "CHECK (action IN ('confirm','exclude','pending','edit','restore'))")
        c.execute("ALTER TABLE candidate_history DROP COLUMN prev_decision, DROP COLUMN prev_text")
        c.execute("DROP INDEX IF EXISTS ix_hist_cand")
        c.execute("CREATE INDEX ix_hist_cand ON candidate_history (user_id, candidate_id, id)")
    return True


def ping():
    with pool().connection() as c:
        return c.execute("SELECT 1 AS ok").fetchone()["ok"] == 1


_scope_conn: "contextvars.ContextVar" = None   # 아래 tx_scope 가 쓰는 연결(설정 전엔 None — import 순서 때문에 지연 생성)


def _scope():
    global _scope_conn
    if _scope_conn is None:
        import contextvars
        _scope_conn = contextvars.ContextVar("crm_tx_scope", default=None)
    return _scope_conn


@contextmanager
def tx():
    """트랜잭션 하나(풀 연결). tx_scope 안이면 그 연결의 savepoint 로 합쳐진다 — 바깥이 롤백하면 같이 사라진다."""
    sc = _scope().get()
    if sc is not None:
        with sc.transaction():
            yield sc
        return
    with pool().connection() as c:
        with c.transaction():
            yield c


@contextmanager
def tx_scope():
    """여러 db 함수를 한 트랜잭션으로 묶는다(board 이전 행 단위 원자성 — imports.migration_apply_row 만 쓴다).
    안에서 부르는 tx() 는 모두 이 연결의 savepoint 다. 중첩 tx_scope 는 거절. 바깥 코드의 기존 동작(tx 마다 새 연결)은 그대로."""
    var = _scope()
    if var.get() is not None:
        raise RuntimeError("tx_scope 는 중첩하지 않는다")
    with pool().connection() as c:
        with c.transaction():
            token = var.set(c)
            try:
                yield c
            finally:
                var.reset(token)


def _one(c, sql, args=()):
    return c.execute(sql, args).fetchone()


def _all(c, sql, args=()):
    return c.execute(sql, args).fetchall()


def _iso(d):
    """timestamptz → ISO 문자열. JSON 으로 나갈 행은 전부 이걸 거친다."""
    return {k: (v.isoformat() if isinstance(v, _dt.datetime) else v) for k, v in d.items()}


# ── 사용자 ─────────────────────────────────────────────

def user_by_email(email):
    """이메일로 찾고 없으면 만든다. 개발·시험용. 운영은 user_by_subject."""
    email = email.strip().lower()
    with tx() as c:
        r = _one(c, "SELECT * FROM users WHERE email=%s", (email,))
        if r:
            _std_retry(c, r["id"])   # PR #20 — seed_pending 계정만(정의 0개)
            return _iso(r)
        uid = new_id("u")
        c.execute("INSERT INTO users (id,email,nav_accepted_at) VALUES (%s,%s,now())", (uid, email))   # PR B ⓐ 신규 = 새 탐색
        _std_on_create(c, uid)   # PR #20 신규 계정 시드 — 같은 트랜잭션, 실패해도 사용자 생성은 산다
        return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (uid,)))


_REVOKED = ("AUTH-REVOKED", "이 연결은 끊겼어요.", "커넥터를 다시 연결해 주세요.")


def _iat_num(iat):
    return iat if isinstance(iat, (int, float)) and not isinstance(iat, bool) else None


def _contact_check(c, sub, email, iat, strict):
    """삭제 계정 contact(deletion_contacts, deleted_at 있음)와 sub 또는 이메일이 맞고 토큰 iat < deleted_at 이면 AUTH-REVOKED.
    strict(새 계정을 만들기 직전) = iat 가 없어도 거절(보수 — 실제 토큰에 iat 가 늘 있는지 모름). 아니면(기존 행 조회) iat 없으면 넘어간다."""
    rows = _all(c, "SELECT deleted_at FROM deletion_contacts WHERE deleted_at IS NOT NULL "
                   "AND ((auth_subject IS NOT NULL AND auth_subject=%s) OR (email IS NOT NULL AND email=%s))", (sub, email))
    iat = _iat_num(iat)
    for r in rows:
        if (iat is None and strict) or (iat is not None and iat < r["deleted_at"].timestamp()):
            raise CrmError(*_REVOKED)


def user_by_subject(sub, email, iat=None):
    """인증된 sub 로 먼저 찾는다. 없으면 검증된 이메일로 찾아 sub 를 붙이고, 그것도 없으면 새로 만든다.
    같은 이메일 = 한 사용자(Google·매직링크 어느 쪽이든).
    PR #20(§2): 새 사용자는 INSERT 직후 같은 트랜잭션에서 표준 시드(_std_on_create). 기존 사용자 갈래는 seed_pending
    계정만 재시도(_std_retry). 웹 로그인 콜백·MCP 호출이 둘 다 auth.user_id_for → 여기를 지난다. 시드가 실패해도 사용자는 돌려준다.
    PR B: iat = 토큰 발급 시각(auth 가 넘긴다, 옛 호출은 없음). 삭제된 계정은 auth_subject 가 비고 이메일이 자리값이라 여기서 다시
    찾히지 않는다 — 새 계정을 만들기 직전 contact 와 대조해 옛 토큰(iat < deleted_at·iat 없음)은 AUTH-REVOKED, 새 토큰이면 새 uid
    (옛 행·contact 와 연결 없음). 기존 행을 찾은 갈래도 iat 가 contact 의 deleted_at 보다 앞이면 거절한다. 신규 = 새 탐색 수락 상태."""
    sub = (sub or "").strip()
    email = (email or "").strip().lower()
    if not sub:
        raise CrmError("AUTH-INVALID", "토큰에 사용자 식별자(sub)가 없어요.")
    with tx() as c:
        r = _one(c, "SELECT * FROM users WHERE auth_subject=%s", (sub,))
        if r:
            if r["status"] == "deleted":
                raise CrmError(*_REVOKED)
            _contact_check(c, sub, email, iat, False)
            _std_retry(c, r["id"])
            return _iso(r)
        if not email:
            raise CrmError("AUTH-INVALID", "처음 연결할 땐 검증된 이메일이 필요해요.")
        r = _one(c, "SELECT * FROM users WHERE email=%s", (email,))
        if r:
            if r["status"] == "deleted":
                raise CrmError(*_REVOKED)
            if r["auth_subject"] and r["auth_subject"] != sub:
                raise CrmError("AUTH-CONFLICT", "이 이메일은 다른 로그인 계정에 이미 연결돼 있어요.")
            _contact_check(c, sub, email, iat, False)
            c.execute("UPDATE users SET auth_subject=%s WHERE id=%s", (sub, r["id"]))
            _std_retry(c, r["id"])
            return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (r["id"],)))
        _contact_check(c, sub, email, iat, True)
        uid = new_id("u")
        c.execute("INSERT INTO users (id,email,auth_subject,nav_accepted_at) VALUES (%s,%s,%s,now())", (uid, email, sub))
        _std_on_create(c, uid)
        return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (uid,)))


def user_get(uid):
    with tx() as c:
        r = _one(c, "SELECT id,email,created FROM users WHERE id=%s", (uid,))
    if not r:
        raise CrmError("AUTH-INVALID", "사용자를 찾을 수 없어요. 다시 로그인하세요.")
    return _iso(r)


def desk_hidden_set(uid, hidden):
    """/app 문의 섹션 숨김 저장(계정 메뉴). 반환 {hidden}. 삭제된 계정 행은 바꾸지 않는다."""
    if not isinstance(hidden, bool):
        raise CrmError("ARG-INVALID", "hidden 은 true·false 예요.")
    with tx() as c:
        c.execute("UPDATE users SET desk_hidden=%s WHERE id=%s AND status='active'", (hidden, uid))
    return {"hidden": hidden}


def app_user_flags(uid) -> dict:
    """/api/state 의 user 에 붙는 /app 전용 값 — 이름 공유 유효 동의(optional_consent_ok)·문의 섹션 숨김. MCP state_get 에는 넣지 않는다."""
    with tx() as c:
        r = _one(c, "SELECT desk_hidden FROM users WHERE id=%s", (uid,))
    return {"struct_names": optional_consent_ok(uid, "struct_names"), "desk_hidden": bool(r and r["desk_hidden"])}


def user_delete(uid):
    """계정 삭제 = 그 사용자 데이터 전체 삭제(cascade).
    tx 전체를 _flush_lock 안에서 — flush INSERT 가 시작된 뒤 delete 가 커밋되면 삭제한 uid 행이 남는 창을 닫는다(1인스턴스).
    끝에 메모리 _usage 의 그 uid 키도 지운다(다음 flush 가 다시 올리지 않게)."""
    with _flush_lock:
        with tx() as c:
            # 정의 층은 보호 Lookup(NO ACTION)이 있어 자식부터 지운다. 나머지는 users cascade.
            for t in ("item_links", "activity_links", "import_entries", "activities", "item_history", "items", "field_defs", "object_defs"):
                c.execute(f"DELETE FROM {t} WHERE user_id=%s", (uid,))
            c.execute("DELETE FROM usage_daily WHERE user_id=%s", (uid,))   # FK 없음(익명 '' 행이 있다) — cascade 밖
            c.execute("DELETE FROM users WHERE id=%s", (uid,))
        with _usage_lock:
            for k in [k for k in _usage if k[1] == uid]:
                del _usage[k]


# ── 질문 ───────────────────────────────────────────────

def question_add(uid, text):
    text = (text or "").strip()
    if not text:
        raise CrmError("ARG-INVALID", "질문 문장이 비었어요.")
    qid = new_id("q")
    with tx() as c:
        _daily_gate(c, uid, "questions")
        c.execute("INSERT INTO questions (id,user_id,text) VALUES (%s,%s,%s)", (qid, uid, text))
        return _iso(_one(c, "SELECT id,text,status,created FROM questions WHERE id=%s", (qid,)))


def questions(uid):
    with tx() as c:
        return [_iso(r) for r in _all(c, "SELECT id,text,status,created FROM questions WHERE user_id=%s ORDER BY created", (uid,))]


def question_get(uid, qid):
    with tx() as c:
        r = _one(c, "SELECT id,text,status,created FROM questions WHERE user_id=%s AND id=%s", (uid, qid))
    if not r:
        raise CrmError("ARG-INVALID", f"질문을 찾을 수 없어요: {qid}", "question_list 로 질문 ID 를 확인하세요.")
    return _iso(r)


# ── 기록 ───────────────────────────────────────────────
# 출처(PR①): author_kind(self·external·unknown) × text_process(verbatim·structured·rewritten·generated·unknown).
# 인용 적격 = self·verbatim·삭제 안 됨 — 후보(판단 근거)는 적격 기록에서만 만든다. 본문은 불변, 출처 판정만 정정한다.

AUTHOR_KINDS = ("self", "external", "unknown")
TEXT_PROCESSES = ("verbatim", "structured", "rewritten", "generated", "unknown")
_ELIGIBLE_SQL = "r.author_kind='self' AND r.text_process='verbatim' AND r.deleted_at IS NULL"
INELIGIBLE_WHY = "이 기록은 본인 원문이 아니라 근거로 쓸 수 없어요"
_REC_COLS = "id,title,body,source,source_id,question_id,author_kind,text_process,created"   # source = 옛 문자열(보존), source_id = 출처 연결


def _eligible(r):
    return r["author_kind"] == "self" and r["text_process"] == "verbatim" and r.get("deleted_at") is None


def _check_provenance(author_kind, text_process):
    if author_kind not in AUTHOR_KINDS:
        raise CrmError("ARG-INVALID", f"author_kind 는 {', '.join(AUTHOR_KINDS)} 중 하나예요.")
    if text_process not in TEXT_PROCESSES:
        raise CrmError("ARG-INVALID", f"text_process 는 {', '.join(TEXT_PROCESSES)} 중 하나예요.")


SOURCE_KINDS = ("file", "url", "chat", "other")   # 출처 종류(§4). 계약 예제는 file·url


def _source_norm(ref):
    """출처 인자 {kind?, name, location?} → (kind, name, location). kind 없으면 other, location 없으면 ''."""
    if not isinstance(ref, dict):
        raise CrmError("ARG-INVALID", "출처는 {kind, name, location} 이에요.")
    kind = (ref.get("kind") or "other").strip()
    name = (ref.get("name") or "").strip()
    loc = (ref.get("location") or "").strip()
    if kind not in SOURCE_KINDS:
        raise CrmError("ARG-INVALID", f"출처 kind 는 {', '.join(SOURCE_KINDS)} 중 하나예요.")
    if not name:
        raise CrmError("ARG-INVALID", "출처 이름(name)이 비었어요.")
    check_len("source_name", name, LIMIT_TEXT)
    check_len("source_location", loc, LIMIT_TEXT)
    return kind, name, loc


def _source_find_or_create(c, uid, kind, name, loc):
    """같은 사용자의 같은 (kind, name, location) 출처 id — 없으면 만든다. 호출자가 users 행을 잠근 뒤(_daily_gate)."""
    q = "SELECT id FROM sources WHERE user_id=%s AND kind=%s AND name=%s AND location=%s"
    r = _one(c, q, (uid, kind, name, loc))
    if r:
        return r["id"]
    c.execute("INSERT INTO sources (user_id,id,kind,name,location) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (user_id,kind,name,location) DO NOTHING",
              (uid, new_id("s"), kind, name, loc))
    return _one(c, q, (uid, kind, name, loc))["id"]


def record_add(uid, title, body, question_id=None, source="대화에서 입력", author_kind="self", text_process="verbatim", source_ref=None):
    """body 는 받은 그대로 저장한다. 공백도 안 다듬는다.
    출처 기본값 self·verbatim = MCP record_add(사용자가 대화에서 직접 준 말). 확인 못 한 출처는 호출자가 unknown 으로 넘긴다.
    수집 범위(§6): author_kind='external'(남이 쓴 글)은 SCOPE-SELF-ONLY. 메타데이터 검사일 뿐 — 실제 작성자는 검증하지 않는다.
    source = 옛 출처 문자열(그대로 저장). source_ref = 출처 연결 {kind, name, location}(§4): 같은 사용자의 같은 출처를
    찾거나 만들어 source_id 로 잇는다(PR B 부터 플래그 없이 상시 — source_linked=true). 인자가 없으면 source_linked=None."""
    title = (title or "").strip()
    body = body or ""
    if not body.strip():
        raise CrmError("ARG-INVALID", "기록 내용이 비었어요.")
    _check_provenance(author_kind, text_process)
    if author_kind == "external":
        raise _scope_self()
    ref = None
    if source_ref is not None:
        ref = _source_norm(source_ref)
    if not title:
        title = body.strip().splitlines()[0][:40]
    if question_id:
        question_get(uid, question_id)
    rid = new_id("r")
    hist = [{"at": now().isoformat(), "by": "tool", "author_kind": author_kind, "text_process": text_process}]
    with tx() as c:
        _daily_gate(c, uid, "records")   # users 행 잠금 — 출처 찾거나 만들기도 이 사용자 안에서 직렬화
        sid = _source_find_or_create(c, uid, *ref) if ref else None
        c.execute("INSERT INTO records (id,user_id,title,body,source,source_id,question_id,author_kind,text_process,provenance_history) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (rid, uid, title, body, source, sid, question_id or None, author_kind, text_process, Jsonb(hist)))
        out = _iso(_one(c, f"SELECT {_REC_COLS} FROM records WHERE id=%s", (rid,)))
    out["source_linked"] = None if source_ref is None else sid is not None
    return out


def record_get(uid, rid):
    with tx() as c:
        r = _one(c, f"SELECT {_REC_COLS} FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL", (uid, rid))
    if not r:
        raise CrmError("RECORD-MISSING", f"기록을 찾을 수 없어요: {rid}")
    return _iso(r)


def _record_in(c, uid, rid):
    """같은 트랜잭션 안에서 읽기(출처 이력 포함)."""
    return _iso(_one(c, f"SELECT {_REC_COLS},provenance_history FROM records WHERE user_id=%s AND id=%s", (uid, rid)))


def record_provenance_set(uid, rid, author_kind, text_process, reason, by="user"):
    """출처 판정 정정. 본문은 안 바뀐다. 같은 값이면 no-op.
    후보가 있는 기록을 적격(self·verbatim) → 비적격으로 바꾸면 거절(PROVENANCE-LOCKED) — 이미 그 원문을 근거로 한 판단이 있다.
    정정마다 provenance_history 에 {at, by, from, author_kind, text_process, reason} 한 줄. 후보 생성과 같은 행 잠금(FOR UPDATE)."""
    _check_provenance(author_kind, text_process)
    reason = (reason or "").strip()
    if not reason:
        raise CrmError("ARG-INVALID", "출처를 고치는 사유가 비었어요.")
    check_len("reason", reason, LIMIT_TEXT)   # α1 ② — provenance_history 에 그대로 쌓인다
    with tx() as c:
        r = _one(c, "SELECT id,author_kind,text_process,deleted_at,generation_kind FROM records WHERE user_id=%s AND id=%s "
                    "AND deleted_at IS NULL FOR UPDATE", (uid, rid))
        if not r:
            raise CrmError("RECORD-MISSING", f"기록을 찾을 수 없어요: {rid}")
        if (r["author_kind"], r["text_process"]) == (author_kind, text_process):
            return _record_in(c, uid, rid)
        if r["generation_kind"] in ("ai", "mixed"):   # board 이전 r3 T2 — 출처 정정으로 AI 생성 이력을 지우지 않는다
            raise CrmError("PROVENANCE-AI-LOCKED", "AI 요약으로 보관한 기록은 출처 판정을 바꿀 수 없어요.",
                           "직접 쓴 글은 새 기록으로 남기세요.")
        if _eligible(r) and not (author_kind == "self" and text_process == "verbatim"):
            n = _one(c, "SELECT count(*) AS n FROM candidates WHERE user_id=%s AND record_id=%s", (uid, rid))["n"]
            if n:
                raise CrmError("PROVENANCE-LOCKED", f"이 기록을 근거로 한 후보가 {n}개 있어 본인 원문이 아니라고 바꿀 수 없어요.",
                                 "과거 오판정 정정은 별도 복구 작업으로 하세요.")
        line = {"at": now().isoformat(), "by": by, "from": {"author_kind": r["author_kind"], "text_process": r["text_process"]},
                "author_kind": author_kind, "text_process": text_process, "reason": reason}
        c.execute("UPDATE records SET author_kind=%s, text_process=%s, provenance_history = provenance_history || %s "
                  "WHERE user_id=%s AND id=%s", (author_kind, text_process, Jsonb([line]), uid, rid))
        return _record_in(c, uid, rid)


# PR ①-b 정렬 키(2026-10-22 S0 판정 D1·D2) — 원본 날짜 우선, 없으면 받은 날. 날짜는 전부 KST.
# source_date(날짜만 확인된 원본) → source_created_at(원본 시각, source_date 와 배타) → created. 히트맵은 created 대체를 쓰지 않는다(_RECORD_DAY).
_KST_DATE = "(({}) AT TIME ZONE 'Asia/Seoul')::date"
_RECORD_DAY = f"COALESCE(source_date, {_KST_DATE.format('source_created_at')})"          # 원본 날짜(없으면 NULL) — 히트맵
RECORD_SORT = f"COALESCE(source_date, {_KST_DATE.format('source_created_at')}, {_KST_DATE.format('created')})"   # 목록 정렬 날짜


def records(uid, limit=50, before=None, before_id=None):
    """정렬 = RECORD_SORT DESC, created DESC, id DESC(동률은 id 로 고정). before(datetime)+before_id = 그 행보다 뒤 순서인 기록만
    (PR① 홈 「더 보기」 keyset 커서 — 동률 블록이 페이지 경계에 걸쳐도 겹침·누락 없음). 커서 꼴은 PR① 그대로 created|id 다:
    정렬 날짜는 before_id 행(soft delete 라 지운 행도 남는다)에서 서버가 복원하고, 그 행이 없으면 kst(before) 로 본다.
    before_id 없이 before 만 = (정렬 날짜, created) < (kst(before), before)."""
    out = []
    key = f"({RECORD_SORT}, created, id)"
    if before is None:
        cond, args = "", ()
    elif before_id is None:
        cond, args = f" AND ({RECORD_SORT}, created) < ((%s::timestamptz AT TIME ZONE 'Asia/Seoul')::date, %s)", (before, before)
    else:
        cond = (f" AND {key} < (COALESCE((SELECT {RECORD_SORT} FROM records WHERE user_id=%s AND id=%s), "
                "(%s::timestamptz AT TIME ZONE 'Asia/Seoul')::date), %s, %s)")
        args = (uid, before_id, before, before, before_id)
    with tx() as c:
        rows = _all(c, "SELECT id,title,body,source,source_id,question_id,author_kind,text_process,notes,created,"
                       "source_date,ingest_kind,generation_kind FROM records "
                       "WHERE user_id=%s AND deleted_at IS NULL" + cond + f" ORDER BY {RECORD_SORT} DESC, created DESC, id DESC LIMIT %s",
                       (uid, *args, limit))
    for r in rows:
        d = _iso(r)
        d["excerpt"] = _excerpt(d.pop("body"))
        d["source_date"] = r["source_date"].isoformat() if r["source_date"] else None   # PR ①-b D3 — 날짜만(YYYY-MM-DD)
        out.append(d)
    return out


def sources(uid):
    """출처 목록 — 계약 모양 {id, kind, name, location}, 만든 순. 플래그와 무관하게 읽힌다."""
    with tx() as c:
        return [dict(r) for r in _all(c, "SELECT id,kind,name,location FROM sources WHERE user_id=%s ORDER BY created, id", (uid,))]


def source_counts(uid):
    """출처별 기록 수(삭제 안 된 기록) — {by_source: {source_id: n}, unconfirmed: 출처 미확인(source_id NULL) 수}."""
    with tx() as c:
        rows = _all(c, "SELECT source_id, count(*) AS n FROM records WHERE user_id=%s AND deleted_at IS NULL GROUP BY source_id", (uid,))
    return {"by_source": {r["source_id"]: r["n"] for r in rows if r["source_id"]},
            "unconfirmed": next((r["n"] for r in rows if r["source_id"] is None), 0)}


def _excerpt(body, n=60):
    for line in str(body or "").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or t.startswith("-"):
            continue
        return t[:n] + ("…" if len(t) > n else "")
    return ""


# ── 후보 ───────────────────────────────────────────────

def _occurrences(body, quote):
    """겹치는 출현까지 센다. 코드포인트 오프셋."""
    out, i = [], body.find(quote)
    while i != -1:
        out.append(i)
        i = body.find(quote, i + 1)
    return out


def candidates_propose(uid, record_id, question_id, items, origin):
    """items: [{"text","quote"}]. quote 는 원문에 글자 그대로 **한 곳**에만 있어야 저장한다.
    0곳 → 버림, 2곳 이상 → 더 길게 인용하라고 버림. 같은 구간 재제출은 중복. 기록당 누적 3개까지.
    PR①: 부모 기록을 FOR UPDATE 로 잠그고 같은 트랜잭션에서 삭제·출처 적격·인용 일치·유일 출현을 본다(출처 정정과 경합 방지).
    비적격(본인 원문 아님) 기록이면 전부 버린다. 새 후보 문장의 작성 주체 wording_by: origin=ai → 'ai', self → NULL(AI 가 다듬었는지 모름)."""
    if origin not in ("ai", "self"):
        raise CrmError("ARG-INVALID", "origin 은 ai 또는 self 예요.")
    question_id = (question_id or "").strip() or None   # "" = 질문 없음 → NULL (화면은 기록별 묶음)
    wording_by = "ai" if origin == "ai" else None
    added, dropped = [], []
    with tx() as c:
        _daily_gate(c, uid, "candidates", len(items or []))   # 제출한 수로 센다(검사 전) — 한 호출 최대 3
        rec = _one(c, "SELECT id,body,author_kind,text_process,deleted_at FROM records "
                      "WHERE user_id=%s AND id=%s AND deleted_at IS NULL FOR UPDATE", (uid, record_id))
        if not rec:
            raise CrmError("RECORD-MISSING", f"기록을 찾을 수 없어요: {record_id}")
        if question_id:
            question_get(uid, question_id)   # 기록 확인 뒤(남의 기록이면 RECORD-MISSING 이 먼저)
        if not _eligible(rec):
            return [], [{"text": (it.get("text") or "").strip(), "why": INELIGIBLE_WHY} for it in items or []]
        body = rec["body"]
        have = _one(c, "SELECT count(*) AS n FROM candidates WHERE user_id=%s AND record_id=%s", (uid, record_id))["n"]
        for it in items or []:
            text = (it.get("text") or "").strip()
            quote = it.get("quote") or ""
            if not text or not quote.strip():
                dropped.append({"text": text, "why": "판단 문장이나 근거 인용이 비었음"})
                continue
            pos = _occurrences(body, quote)
            if not pos:
                dropped.append({"text": text, "why": "근거 인용이 원문에 글자 그대로 없음"})
                continue
            if len(pos) > 1:
                dropped.append({"text": text, "why": f"원문에 같은 구간이 {len(pos)}곳 — 앞뒤를 포함해 더 길게 인용하세요"})
                continue
            start, end = pos[0], pos[0] + len(quote)
            dup = _one(c, "SELECT id FROM candidates WHERE user_id=%s AND record_id=%s AND quote_start=%s AND quote_end=%s",
                       (uid, record_id, start, end))
            if dup:
                dropped.append({"text": text, "why": "이미 있는 후보", "id": dup["id"]})
                continue
            if have >= MAX_CANDIDATES_PER_RECORD:
                dropped.append({"text": text, "why": f"기록 1편당 후보는 {MAX_CANDIDATES_PER_RECORD}개까지예요"})
                continue
            cid = new_id("c")
            c.execute("INSERT INTO candidates (id,user_id,question_id,record_id,text,quote,quote_start,quote_end,origin,wording_by) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (cid, uid, question_id, record_id, text, quote, start, end, origin, wording_by))
            have += 1
            added.append(cid)
    return added, dropped


_CAND_COLS = ("id,question_id,record_id,text,quote,quote_start,quote_end,origin,decision AS status,edited_by_user,"
              "revision,wording_by,wording_approved,created,updated")


def _with_review(c, uid, rows):
    """후보마다 review = 근거 기록의 재검토 사유 코드(contract.review_reason) — 미확정(pending·excluded)이고 근거가 비적격일 때만,
    그 밖은 None(§6). 후보·quote·기록 연결은 그대로 둔다 — 바뀌는 건 확정 가능 여부뿐."""
    ids = list({r["record_id"] for r in rows})
    recs = {x["id"]: x for x in _all(c, "SELECT id,author_kind,text_process,deleted_at FROM records WHERE user_id=%s AND id = ANY(%s)",
                                       (uid, ids))} if ids else {}
    out = []
    for r in rows:
        d = _iso(r)
        rec = recs.get(r["record_id"])
        d["review"] = None if d["status"] == "confirmed" or rec is None else contract.review_reason(rec)
        out.append(d)
    return out


def candidate_get(uid, cid):
    with tx() as c:
        r = _one(c, f"SELECT {_CAND_COLS} FROM candidates WHERE user_id=%s AND id=%s", (uid, cid))
        if not r:
            raise CrmError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
        return _with_review(c, uid, [r])[0]


def candidates(uid, question_id=None, status=None):
    """status 인자는 도구 계약 이름. 내부 컬럼은 decision. 지워진 기록의 후보는 빠진다(그래서 review=deleted 는 목록에 안 나온다).
    후보마다 review(재검토 사유 코드 또는 None)."""
    sql = (f"SELECT {_CAND_COLS} FROM candidates c WHERE c.user_id=%s "
           "AND EXISTS (SELECT 1 FROM records r WHERE r.user_id=c.user_id AND r.id=c.record_id AND r.deleted_at IS NULL)")
    args = [uid]
    if question_id:
        sql += " AND c.question_id=%s"; args.append(question_id)
    if status:
        sql += " AND c.decision=%s"; args.append(status)
    sql += " ORDER BY c.created, c.quote_start, c.id"
    with tx() as c:
        return _with_review(c, uid, _all(c, sql, args))


def _cand_snapshot(r):
    """candidate_history.prev_state v2 — 변경 전 현재값(문장·결정·문장 주체·승인·편집 여부·질문 연결)."""
    return {"schema_version": 2, "text": r["text"], "decision": r["decision"], "wording_by": r["wording_by"],
            "wording_approved": r["wording_approved"], "edited_by_user": r["edited_by_user"], "question_id": r["question_id"]}


def _cand_lock(c, uid, cid):
    r = _one(c, "SELECT * FROM candidates WHERE user_id=%s AND id=%s FOR UPDATE", (uid, cid))
    if not r:
        raise CrmError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
    return r


def _cand_bump(c, uid, r, action, sets, args):
    """변경 한 번 = 변경 전 스냅샷 이력 한 줄(prev_revision = 지금 revision) + 현재값 변경 + revision+1. 바뀐 행을 돌려준다."""
    c.execute("INSERT INTO candidate_history (user_id,candidate_id,action,prev_revision,prev_state) VALUES (%s,%s,%s,%s,%s)",
              (uid, r["id"], action, r["revision"], Jsonb(_cand_snapshot(r))))
    c.execute(f"UPDATE candidates SET {sets}, revision=revision+1, updated=now() WHERE user_id=%s AND id=%s",
              (*args, uid, r["id"]))
    return _cand_lock(c, uid, r["id"])


def _confirm_check(c, uid, r):
    """확정 시 재검사(§6) — 근거 기록을 _eligible 전체 조건으로 다시 본다(FOR SHARE: 출처 정정 FOR UPDATE 와 직렬화).
    비적격이면 CANDIDATE-REVIEW(사유별 문구). 결정이 confirmed 로 바뀔 때만 부른다 — 이미 확정된 판단은 건드리지 않는다."""
    rec = _one(c, "SELECT author_kind,text_process,deleted_at FROM records WHERE user_id=%s AND id=%s FOR SHARE", (uid, r["record_id"]))
    why = contract.review_reason(rec) if rec else "deleted"
    if why:
        raise _cand_review(why)


def candidate_decide(uid, cid, action, text=None, expected_revision=None, wording_by=None):
    """confirm / exclude / pending(확정 취소) / edit / edit_confirm(웹 전용). 바꾸기 전 상태를 이력에 남기고 revision+1.
    같은 결정 반복은 no-op(revision 그대로). 확정된 문장을 고치면 pending 으로 돌아간다(새 문장 확정은 별도 결정).
    edit_confirm 은 edit → confirm 을 한 트랜잭션으로 한다(이력 두 줄, revision +2). 같은 문장 + 이미 confirmed 면 no-op.
    expected_revision: 주면(웹) 지금 revision 과 다를 때 CONFLICT. None 이면(MCP 도구) 검사 안 함.
    문장 수정은 wording_approved=false, wording_by=인자(웹 편집 = self, MCP edit = 모름 NULL). 승인(wording_approved=true)은 이번 범위 밖.
    확정으로 바뀔 때(confirm·edit_confirm)만 근거 기록 재검사(_confirm_check) — 비적격이면 CANDIDATE-REVIEW, edit_confirm 은 edit 까지 롤백.
    exclude·pending·edit 은 근거와 무관하게 된다."""
    if action not in ("confirm", "exclude", "pending", "edit", "edit_confirm"):
        raise CrmError("ARG-INVALID", "action 은 confirm·exclude·pending·edit 중 하나예요.")
    if wording_by not in (None, "self", "ai"):
        raise CrmError("ARG-INVALID", "wording_by 는 self 또는 ai 예요.")
    with tx() as c:
        _daily_gate(c, uid, "decisions", 2 if action == "edit_confirm" else 1)   # edit_confirm 은 이력 2줄
        r = _cand_lock(c, uid, cid)
        if expected_revision is not None and r["revision"] != expected_revision:
            raise _conflict(f"지금 revision {r['revision']}")
        if action in ("edit", "edit_confirm"):
            new = (text or "").strip()
            if not new:
                raise CrmError("ARG-INVALID", "고친 문장이 비었어요.")
            if new != r["text"]:
                r = _cand_bump(c, uid, r, "edit",
                               "text=%s, edited_by_user=true, decision='pending', wording_by=%s, wording_approved=false",
                               (new, wording_by))
            if action == "edit_confirm" and r["decision"] != "confirmed":
                _confirm_check(c, uid, r)
                r = _cand_bump(c, uid, r, "confirm", "decision='confirmed'", ())
        else:
            target = CAND_ACTIONS[action]
            if target != r["decision"]:
                if target == "confirmed":
                    _confirm_check(c, uid, r)
                _cand_bump(c, uid, r, action, "decision=%s", (target,))
    return candidate_get(uid, cid)


def candidate_restore(uid, cid, history_id, expected_revision=None):
    """이력 한 줄의 변경 전 상태로 되돌린다. 되돌리기도 새 변경 — 지금 상태를 새 이력(action=restore)으로 남기고 revision+1.
    v2 스냅샷: 문장·결정·문장 주체·편집 여부·질문 연결을 되살린다. v1(PR① 전 부분 이력): 문장·결정만, wording_by=NULL.
    승인(wording_approved)은 어느 쪽이든 false — 승인은 그 revision 에 대한 것이라 새 revision 으로 넘기지 않는다.
    MCP 도구엔 안 붙인다(웹 API 는 다음 PR)."""
    with tx() as c:
        _user_lock(c, uid)   # PR B — 삭제 CLI 와 직렬화(삭제 뒤 자료 생성 0). 잠금 순서 = users 행 → candidates
        r = _cand_lock(c, uid, cid)
        if expected_revision is not None and r["revision"] != expected_revision:
            raise _conflict(f"지금 revision {r['revision']}")
        try:
            hid = int(history_id)
        except (TypeError, ValueError):
            raise CrmError("ARG-INVALID", "history_id 는 숫자예요.")
        h = _one(c, "SELECT id,prev_revision,prev_state FROM candidate_history WHERE user_id=%s AND candidate_id=%s AND id=%s",
                 (uid, cid, hid))
        if not h:
            raise CrmError("ARG-INVALID", f"이 후보의 이력을 찾을 수 없어요: {history_id}")
        st = h["prev_state"]
        if st.get("decision") not in ("pending", "confirmed", "excluded") or not st.get("text"):
            raise CrmError("ARG-INVALID", "이 이력은 되돌릴 수 있는 모양이 아니에요.")
        if st.get("schema_version") == 2:
            _cand_bump(c, uid, r, "restore",
                       "text=%s, decision=%s, wording_by=%s, wording_approved=false, edited_by_user=%s, question_id=%s",
                       (st["text"], st["decision"], st.get("wording_by"), bool(st.get("edited_by_user")), st.get("question_id")))
        else:
            _cand_bump(c, uid, r, "restore", "text=%s, decision=%s, wording_by=NULL, wording_approved=false",
                       (st["text"], st["decision"]))
    return candidate_get(uid, cid)


def candidate_history(uid, cid):
    """이력(오래된 순). 기존 키 prev_text·prev_status 는 prev_state 에서 꺼내 호환, PR① 키 id·prev_revision·prev_state 추가."""
    candidate_get(uid, cid)
    with tx() as c:
        rows = _all(c, "SELECT id,action,prev_state->>'decision' AS prev_status,prev_state->>'text' AS prev_text,"
                       "prev_revision,prev_state,at FROM candidate_history WHERE user_id=%s AND candidate_id=%s ORDER BY id",
                    (uid, cid))
    return [_iso(r) for r in rows]


# ── 프로필 ──────────────────────────────────────────────

def profile(uid):
    with tx() as c:
        rows = {r["key"]: r for r in _all(c, "SELECT key,value,status,source,updated FROM profile WHERE user_id=%s", (uid,))}
    out = {}
    for k, label in PROFILE_KEYS.items():
        r = rows.get(k)
        out[k] = {"label": label, "value": r["value"] if r else ("" if k != "daily_views" else []),
                  "status": r["status"] if r else "empty", "source": r["source"] if r else "",
                  "updated": r["updated"].isoformat() if r else ""}
    return out


def profile_set(uid, key, value, status, source=""):
    if key not in PROFILE_KEYS:
        raise CrmError("ARG-INVALID", f"프로필에 없는 항목: {key}", "가능한 항목: " + ", ".join(PROFILE_KEYS))
    if status not in STATUSES:
        raise CrmError("ARG-INVALID", f"상태는 {', '.join(STATUSES)} 중 하나예요.")
    if key == "daily_views":
        value = [v.strip() for v in (value.split("|") if isinstance(value, str) else value) if str(v).strip()]
        if len(value) > 3:
            raise CrmError("ARG-INVALID", "매일 볼 것은 최대 3개예요.")
    with tx() as c:
        _user_lock(c, uid)   # PR B — 삭제 CLI 와 직렬화(삭제 뒤 자료 생성 0). 잠금 순서 = users 행 → profile
        c.execute("INSERT INTO profile (user_id,key,value,status,source,updated) VALUES (%s,%s,%s,%s,%s,now()) "
                  "ON CONFLICT (user_id,key) DO UPDATE SET value=excluded.value,status=excluded.status,source=excluded.source,updated=now()",
                  (uid, key, json.dumps(value, ensure_ascii=False), status, source or ""))
    return profile(uid)[key]


# ── 탭 ─────────────────────────────────────────────────

def tabs(uid):
    with tx() as c:
        r = _one(c, "SELECT doc,revision FROM tabs WHERE user_id=%s", (uid,))
    return r["doc"] if r else {"tabs": [], "requests": [], "revision": 0}


# ── 사용자 정의 층: 정의(object_defs·field_defs) · 항목(items) · 관계(item_links) · 이력(item_history) ──
# 사용자는 오브젝트·필드·Lookup 까지만 정의한다. 제안(draft)은 MCP 가, 적용·확정은 사용자가 말했을 때만.
# 모든 변경은 행 잠금(FOR UPDATE) + expected_revision 검사. 항목 이력 한 줄 = revision +1.

VALUE_TYPES = ("text", "number", "boolean", "date", "select", "text_list", "reference")
SEMANTIC_KINDS = ("collection", "event", "principle")
TARGET_KINDS = ("item", "record", "candidate", "person", "source", "activity")   # 뒤 셋 = board 이전(사람·출처·활동 판)
ITEM_ACTIONS = ("apply", "confirm", "exclude", "pending", "archive", "restore", "discard")
ITEM_DECISIONS = {"confirm": "confirmed", "exclude": "excluded", "pending": "pending"}
RECORD_LINK_KEYS = ("attach_to", "axes")
DEFAULT_PROMOTION = {"confirmed_claims_min": 2, "claim_field": "evidence_claims", "distinct_events_min": 0,
                     "event_field": None, "event_claim_field": None}
DEFAULT_CLAIM_FIELD = {"key": "evidence_claims", "label": "근거 판단", "value_type": "reference", "required": False,
                       "options": [], "target_kind": "candidate", "target_object_id": None, "multiple": True}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_OBJ_ID_RE = re.compile(r"^od_[a-z0-9_]{1,40}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CONFLICT_MSG = "다른 곳에서 먼저 바뀌었어요."
CONFLICT_ACT = "definitions_get·state_get 으로 지금 revision 을 다시 보고, 사용자에게 확인한 뒤 다시 하세요."


def _conflict(what=""):
    return CrmError("CONFLICT", CONFLICT_MSG + (f" ({what})" if what else ""), CONFLICT_ACT)


def _bad_def(msg, action=""):
    return CrmError("DEF-INVALID", msg, action)


def _bad_link(msg, action=""):
    return CrmError("LINK-INVALID", msg, action)


# PR #20 신규 코드 — 문구는 contract 가 정본(고정 문구 + 표준 식별자·사유 코드만, 다른 사용자 정보 자리 없음). STANDARD-LOCKED 는
# _std_lock_check·std_choose(5단계), 나머지 둘은 6단계
def _std_locked(change, std_id):
    return CrmError(contract.STANDARD_LOCKED, contract.locked_message(change, std_id), contract.LOCKED_ACT)


def _cand_review(reason):
    return CrmError(contract.CANDIDATE_REVIEW, contract.REVIEW_MSG[reason], contract.REVIEW_ACT[reason])


def _scope_self():
    return CrmError(contract.SCOPE_SELF_ONLY, contract.SCOPE_MSG, contract.SCOPE_ACT)


# ── 정의 정규화·검증 (DB 없이 — 시드 --dry-run 도 같은 함수) ──

def _norm_options(raw, fkey):
    if not isinstance(raw, list) or not raw:
        raise _bad_def(f"select 필드 {fkey} 에 options 가 비었어요.",
                       "options 에 고를 값을 넣으세요. 예: [\"customer\", \"work\"] 또는 [{\"value\":\"customer\",\"label\":\"고객 업무\"}]")
    out, seen = [], set()
    for o in raw:
        if isinstance(o, str):
            o = {"value": o, "label": o}
        if not isinstance(o, dict) or not isinstance(o.get("value"), str) or not o["value"].strip():
            raise _bad_def(f"select 필드 {fkey} 의 option 형식이 틀렸어요.")
        v = o["value"].strip()
        if v in seen:
            raise _bad_def(f"select 필드 {fkey} 에 같은 값 {v} 가 두 번 있어요.")
        seen.add(v)
        out.append({"value": v, "label": str(o.get("label") or v).strip()})
    return out


# 정의 JSON 이 받는 키. 읽기 전용 키(definitions_get 응답을 그대로 되돌려 보낸 것)는 받되 무시한다 — archived_at 은 비어 있을 때만.
# 그 밖의 키(예 "archived")는 조용히 무시하지 않고 ARG-INVALID(R5 결함 R-5). 보관은 definition_archive.
DEF_KEYS = frozenset({"id", "key", "label", "semantic_kind", "policy", "fields"})
DEF_READONLY = frozenset({"revision", "created_at", "updated_at", "template_slot", "std_id", "proposal", "draft", "archived_at"})
FIELD_KEYS = frozenset({"key", "label", "value_type", "required", "options", "target_kind", "target_object_id", "multiple"})
FIELD_READONLY = frozenset({"position", "template_slot", "std_id", "archived_at"})


def _unknown_keys(d, allowed, readonly, where):
    bad = sorted(str(k) for k in d if k not in allowed and k not in readonly)
    if bad:
        raise CrmError("ARG-INVALID", f"{where}에 모르는 키가 있어요: {', '.join(bad)}",
                       "보관·복구는 definition_archive·definition_restore 로 해요. 받는 키: " + ", ".join(sorted(allowed)))
    if d.get("archived_at") is not None:
        raise CrmError("ARG-INVALID", f"{where}의 archived_at 은 정의 JSON 으로 바꿀 수 없어요.",
                       "보관·복구는 definition_archive·definition_restore 로 해요.")


def normalize_definition(d):
    """definition_json → 저장 형태. 형식만 본다(대상 오브젝트·기존 항목은 _check_definition)."""
    if not isinstance(d, dict):
        raise _bad_def("정의는 JSON 객체여야 해요.")
    _unknown_keys(d, DEF_KEYS, DEF_READONLY, "정의")
    key, label, kind = d.get("key"), str(d.get("label") or "").strip(), d.get("semantic_kind")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise _bad_def("key 는 영문 소문자로 시작하는 40자 이내 [a-z0-9_] 예요.")
    if not label:
        raise _bad_def("label(화면 이름)이 비었어요.")
    if kind not in SEMANTIC_KINDS:
        raise _bad_def(f"semantic_kind 는 {', '.join(SEMANTIC_KINDS)} 중 하나예요.")
    policy = d.get("policy") or {}
    if not isinstance(policy, dict):
        raise _bad_def("policy 는 JSON 객체예요.")
    raw_fields = d.get("fields") or []
    if not isinstance(raw_fields, list):
        raise _bad_def("fields 는 목록이에요.")
    fields, seen = [], set()
    for f in raw_fields:
        if not isinstance(f, dict):
            raise _bad_def("필드 정의는 JSON 객체예요.")
        _unknown_keys(f, FIELD_KEYS, FIELD_READONLY, f"필드 {f.get('key')!r} ")
        fk, vt = f.get("key"), f.get("value_type")
        if not isinstance(fk, str) or not _KEY_RE.match(fk):
            raise _bad_def(f"필드 key 형식이 틀렸어요: {fk!r}")
        if fk in seen:
            raise _bad_def(f"필드 key {fk} 가 두 번 있어요.")
        seen.add(fk)
        if vt not in VALUE_TYPES:
            raise _bad_def(f"필드 {fk} 의 value_type 은 {', '.join(VALUE_TYPES)} 중 하나예요.")
        nf = {"key": fk, "label": str(f.get("label") or fk).strip(), "value_type": vt, "required": bool(f.get("required")),
              "options": [], "target_kind": None, "target_object_id": None, "multiple": False}
        if vt == "select":
            nf["options"] = _norm_options(f.get("options"), fk)
        if vt == "reference":
            tk = f.get("target_kind")
            if tk not in TARGET_KINDS:
                raise _bad_def(f"reference 필드 {fk} 의 target_kind 는 {'·'.join(TARGET_KINDS)} 중 하나예요.")
            nf["target_kind"] = tk
            nf["multiple"] = bool(f.get("multiple"))
            if tk == "item":
                to = f.get("target_object_id")
                if not isinstance(to, str) or not to:
                    raise _bad_def(f"reference 필드 {fk} 는 target_object_id(대상 오브젝트 ID)가 필요해요.")
                nf["target_object_id"] = to
        fields.append(nf)
    policy = dict(policy)
    if kind == "principle":
        promo = {**DEFAULT_PROMOTION, **(policy.get("promotion") or {})}
        if promo["claim_field"] == DEFAULT_CLAIM_FIELD["key"] and DEFAULT_CLAIM_FIELD["key"] not in seen:
            fields.append(dict(DEFAULT_CLAIM_FIELD))   # 신규 기준 정의엔 근거 판단 필드를 서버가 넣는다(raw R8)
            seen.add(DEFAULT_CLAIM_FIELD["key"])
        policy["promotion"] = promo
    byk = {f["key"]: f for f in fields}
    if "promotion" in policy:
        p = policy["promotion"]
        if not isinstance(p, dict):
            raise _bad_def("policy.promotion 은 JSON 객체예요.")
        for n in ("confirmed_claims_min", "distinct_events_min"):
            if not isinstance(p.get(n), int) or isinstance(p.get(n), bool) or p[n] < 0:
                raise _bad_def(f"policy.promotion.{n} 는 0 이상 정수예요.")
        cf = byk.get(p.get("claim_field"))
        if not cf or cf["value_type"] != "reference" or cf["target_kind"] != "candidate":
            raise _bad_def("policy.promotion.claim_field 는 후보(candidate)를 가리키는 reference 필드여야 해요.")
        if p["distinct_events_min"] > 0:
            ef = byk.get(p.get("event_field"))
            if not ef or ef["value_type"] != "reference" or ef["target_kind"] != "item":
                raise _bad_def("결정(event) 조건을 쓰려면 policy.promotion.event_field 가 항목(item)을 가리키는 reference 필드여야 해요.")
    if "exactly_one_reference" in policy:
        keys = policy["exactly_one_reference"]
        if not isinstance(keys, list) or not keys or any((byk.get(k) or {}).get("value_type") != "reference" for k in keys):
            raise _bad_def("policy.exactly_one_reference 는 reference 필드 key 목록이에요.")
    out = {"key": key, "label": label, "semantic_kind": kind, "policy": policy, "fields": fields}
    if d.get("id") is not None:
        out["id"] = d["id"]
    return out


def _value_error(f, v):
    """일반 필드 값 하나가 타입에 맞으면 None, 아니면 이유."""
    vt = f["value_type"]
    if vt == "text":
        return None if isinstance(v, str) else "글자여야 해요"
    if vt == "number":
        return None if isinstance(v, (int, float)) and not isinstance(v, bool) else "숫자여야 해요"
    if vt == "boolean":
        return None if isinstance(v, bool) else "true/false 여야 해요"
    if vt == "date":
        if not isinstance(v, str) or not _DATE_RE.match(v):
            return "YYYY-MM-DD 날짜여야 해요"
        try:
            _dt.date.fromisoformat(v)
        except ValueError:
            return "없는 날짜예요"
        return None
    if vt == "select":
        vals = [o["value"] for o in f["options"]]
        return None if v in vals else f"{', '.join(vals)} 중 하나여야 해요"
    if vt == "text_list":
        return None if isinstance(v, list) and all(isinstance(x, str) for x in v) else "글자 목록이어야 해요"
    return "관계 값은 data 가 아니라 links 로 보내요"


def _empty(v):
    return v is None or v == "" or v == []


def _item_problems(fields, policy, data, links, strict):
    """항목 하나가 정의에 맞는지 → (data 문제, links 문제). strict=False 면 정의에 없는 data 키는 넘어간다(필드 제거 뒤 남은 값)."""
    dprob, lprob = [], []
    byk = {f["key"]: f for f in fields}
    for k, v in (data or {}).items():
        f = byk.get(k)
        if f is None:
            if strict:
                dprob.append(f"정의에 없는 필드 {k}")
            continue
        if f["value_type"] == "reference":
            dprob.append(f"{k}: 관계 값은 data 가 아니라 links 로 보내요")
            continue
        if _empty(v):
            continue
        why = _value_error(f, v)
        if why:
            dprob.append(f"{f['label']}({k}): {why}")
    per = {}
    for ln in links:
        per.setdefault(ln["field_key"], []).append(ln)
    for f in fields:
        if f["value_type"] == "reference":
            got = per.get(f["key"], [])
            if f["required"] and not got:
                lprob.append(f"{f['label']}({f['key']}) 연결이 필요해요")
            if not f["multiple"] and len(got) > 1:
                lprob.append(f"{f['label']}({f['key']}) 는 하나만 연결할 수 있어요")
            for ln in got:
                if ln["target_kind"] != f["target_kind"]:
                    lprob.append(f"{f['label']}({f['key']}) 는 {f['target_kind']} 만 가리켜요")
                elif f["target_kind"] == "item" and ln.get("target_object_id") and ln["target_object_id"] != f["target_object_id"]:
                    lprob.append(f"{f['label']}({f['key']}) 의 대상 오브젝트가 달라요")
        elif f["required"] and _empty((data or {}).get(f["key"])):
            dprob.append(f"{f['label']}({f['key']}) 값이 필요해요")
    for k in per:
        if k not in byk:
            lprob.append(f"정의에 없는 관계 필드 {k}")
        elif byk[k]["value_type"] != "reference":   # reference 를 일반 타입으로 바꾸면 남은 연결이 고아가 된다
            lprob.append(f"{byk[k]['label']}({k}) 에 연결이 남아 있어 관계가 아닌 필드로 바꿀 수 없어요")
    one = policy.get("exactly_one_reference")
    if one:
        n = sum(len(per.get(k, [])) for k in one)
        if n != 1:
            lprob.append(f"{', '.join(one)} 중 정확히 하나만 연결해요 (지금 {n}개)")
    return dprob, lprob


# ── 정의 읽기·쓰기 ──

_DEF_COLS = ("id,key,label,semantic_kind,revision,policy,draft_doc,draft_id,draft_base_revision,archived_at,created_at,updated_at,"
             "template_slot,std_id")   # template_slot·std_id = 출처 판정(contract.origin_of) 입력
_FIELD_COLS = "key,label,value_type,required,options,target_kind,target_object_id,multiple,position,archived_at,template_slot,std_id"


def _fields(c, uid, oid, include_archived=False):
    sql = f"SELECT {_FIELD_COLS} FROM field_defs WHERE user_id=%s AND object_id=%s"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    return [_iso(r) for r in _all(c, sql + " ORDER BY position, key", (uid, oid))]


def _def_out(c, uid, row, include_archived=False):
    d = _iso(row)
    d["fields"] = _fields(c, uid, row["id"], include_archived)
    d["proposal"] = ({"proposal_id": row["draft_id"], "base_revision": row["draft_base_revision"], "definition": row["draft_doc"]}
                     if row["draft_id"] else None)
    for k in ("draft_id", "draft_base_revision", "draft_doc"):
        d.pop(k, None)
    return d


def definitions(uid, object_ids=None, include_archived=False):
    """적용된(revision>0) 정의 + 필드. object_ids 로 콕 집어 물으면 적용 전 새 오브젝트 제안(revision 0)도 돌려주고 draft=true 를 단다
    (R5 결함 R-4 — 응답 모양은 키 추가만. 전체 목록(object_ids 없음)엔 초안을 넣지 않는다)."""
    sql = f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s" + ("" if object_ids else " AND revision > 0")
    args = [uid]
    if not include_archived:
        sql += " AND archived_at IS NULL"
    if object_ids:
        sql += " AND id = ANY(%s)"
        args.append(list(object_ids))
    with tx() as c:
        out = []
        for r in _all(c, sql + " ORDER BY created_at, id", args):
            d = _def_out(c, uid, r, include_archived)
            if r["revision"] == 0:
                d["draft"] = True
            out.append(d)
        return out


def _def_archive_set(uid, object_id, archive):
    """사용자 오브젝트 보관(archive=True)·복구 — archived_at 만 바꾼다(revision·이력·항목 그대로). 표준(std_id)은 STANDARD-LOCKED,
    적용 전 초안은 ARG-INVALID. 보관해도 항목은 지우지 않는다 — 결과 impact = 살아 있는 항목 수 · 이 오브젝트를 가리키는 다른 필드 수."""
    with tx() as c:
        _user_lock(c, uid)
        row = _one(c, "SELECT id,revision,archived_at,std_id FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, str(object_id or "")))
        if not row:
            raise CrmError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}", "definitions_get 으로 ID 를 확인하세요.")
        if row["std_id"]:
            raise _std_locked("archive", row["std_id"])
        if row["revision"] == 0:
            raise CrmError("ARG-INVALID", "적용 전 제안은 보관할 수 없어요.", "새 제안으로 덮거나 그대로 두세요.")
        changed = (row["archived_at"] is None) is archive
        if changed:
            c.execute("UPDATE object_defs SET archived_at = CASE WHEN %s THEN now() ELSE NULL END, updated_at=now() WHERE user_id=%s AND id=%s",
                      (archive, uid, row["id"]))
        items_n = _one(c, "SELECT count(*) AS n FROM items WHERE user_id=%s AND object_id=%s AND archived_at IS NULL", (uid, row["id"]))["n"]
        refs_n = _one(c, "SELECT count(*) AS n FROM field_defs WHERE user_id=%s AND target_object_id=%s AND object_id <> %s AND archived_at IS NULL",
                      (uid, row["id"], row["id"]))["n"]
        out = _def_out(c, uid, _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s", (uid, row["id"])), True)
    out["changed"], out["impact"] = changed, {"items": items_n, "referencing_fields": refs_n}
    return out


def definition_archive(uid, object_id):
    return _def_archive_set(uid, object_id, True)


def definition_restore(uid, object_id):
    return _def_archive_set(uid, object_id, False)


def _check_definition(c, uid, oid, row, nd, given=None):
    """표준 잠금(STANDARD-LOCKED, 편입된 것만·플래그 무관) → 대상 오브젝트·기존 항목과 맞는지(DEF-INVALID).
    propose·apply 둘 다 부른다 — apply 는 FOR UPDATE 로 다시 읽은 최신 행으로 재검증한다.
    given = 제안 JSON 에 사용자가 실제로 보낸 필드 key(propose 만). 정규화가 되살린 필드(근거 판단 evidence_claims)는 빼고 본다."""
    _std_lock_check(c, uid, oid, row, nd, given)
    for f in nd["fields"]:
        to = f["target_object_id"]
        if to and to != oid:
            t = _one(c, "SELECT revision,archived_at FROM object_defs WHERE user_id=%s AND id=%s", (uid, to))
            if not t or t["revision"] == 0 or t["archived_at"]:
                raise _bad_def(f"필드 {f['key']} 의 대상 오브젝트 {to} 가 없어요(적용 전·보관 포함).", "definitions_get 으로 오브젝트 ID 를 확인하세요.")
    if row is None or row["revision"] == 0:
        return
    if nd["key"] != row["key"]:
        raise _bad_def("적용된 오브젝트의 key 는 바꿀 수 없어요. 화면 이름은 label 로 바꿔요.")
    has_items = _one(c, "SELECT 1 AS x FROM items WHERE user_id=%s AND object_id=%s LIMIT 1", (uid, oid))
    if nd["semantic_kind"] != row["semantic_kind"] and has_items:
        raise _bad_def("항목이 있는 오브젝트의 종류(semantic_kind)는 바꿀 수 없어요.")
    rows = _all(c, "SELECT id,code,title,data FROM items WHERE user_id=%s AND object_id=%s AND revision > 0 AND archived_at IS NULL",
                (uid, oid))
    if not rows:
        return
    links = _links_by_item(c, uid, [i["id"] for i in rows])
    broken = []
    for it in rows:
        dp, lp = _item_problems(nd["fields"], nd["policy"], it["data"], links.get(it["id"], []), strict=False)
        if dp or lp:
            broken.append(f"{it['code'] or it['title'] or it['id']}: {(dp + lp)[0]}")
    if broken:
        raise _bad_def(f"이 정의로 바꾸면 기존 항목 {len(broken)}개가 깨져요 — " + " · ".join(broken[:3]),
                       "필수로 바꾸기 전에 항목을 먼저 채우거나, 필수를 빼고 다시 제안하세요.")


def definition_propose(uid, object_id, expected_revision, definition):
    """정의 제안을 draft 로 저장한다(행당 하나, 새 제안이 이전 제안을 덮는다). 적용은 definition_apply."""
    nd = normalize_definition(definition)
    given = {f.get("key") for f in definition.get("fields") or [] if isinstance(f, dict)}   # 정규화 전 — 되살린 필드와 구별
    with tx() as c:
        _user_lock(c, uid)   # 잠금 순서 = users 행 → object_defs. 한도는 새 행을 만드는 경로에서만 센다
        if not object_id:
            if expected_revision != 0:
                raise _conflict("새 오브젝트는 expected_revision 0")
            row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND key=%s FOR UPDATE", (uid, nd["key"]))
            if row and row["revision"] > 0:
                raise _bad_def(f"key {nd['key']} 오브젝트가 이미 있어요({row['id']}).", "고치려면 object_id 를 넣어 제안하세요.")
            if row:   # 아직 적용 안 된 같은 key 제안 행 — 그 행의 제안을 바꾼다
                oid = row["id"]
            else:
                oid = nd.get("id") or f"od_{secrets.token_hex(5)}"
                if not isinstance(oid, str) or not _OBJ_ID_RE.match(oid):
                    raise _bad_def("id 는 od_ 로 시작하는 [a-z0-9_] 예요.")
                if _one(c, "SELECT 1 AS x FROM object_defs WHERE user_id=%s AND id=%s", (uid, oid)):
                    raise _bad_def(f"오브젝트 ID {oid} 가 이미 있어요.")
                _daily_gate(c, uid, "proposals")
                c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind) VALUES (%s,%s,%s,%s,%s)",
                          (uid, oid, nd["key"], nd["label"], nd["semantic_kind"]))
                row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, oid))
        else:
            oid = object_id
            row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, oid))
            if not row:
                raise CrmError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {oid}", "definitions_get 으로 ID 를 확인하세요.")
            if row["revision"] != expected_revision:
                raise _conflict(f"지금 revision {row['revision']}")
            if row["archived_at"]:
                raise _bad_def("보관된 오브젝트는 고칠 수 없어요.")
        nd.pop("id", None)
        _check_definition(c, uid, oid, row, nd, given)
        pid = new_id("dp")
        c.execute("UPDATE object_defs SET draft_doc=%s, draft_id=%s, draft_base_revision=%s, updated_at=now() WHERE user_id=%s AND id=%s",
                  (Jsonb(nd), pid, row["revision"], uid, oid))
    return {"object_id": oid, "proposal_id": pid, "base_revision": row["revision"], "definition": nd,
            "note": "제안만 저장했어요. 사용자가 적용하라고 말할 때만 definition_apply 를 부르세요."}


def definition_apply(uid, object_id, proposal_id, expected_revision):
    """제안 ID·기반 revision 이 맞을 때만 적용. revision+1, history 에 전체 정의 스냅샷, field_defs 동기화(빠진 필드는 archived_at)."""
    with tx() as c:
        _user_lock(c, uid)   # PR B — 삭제 CLI 와 직렬화(삭제 뒤 자료 생성 0). 잠금 순서 = users 행 → object_defs
        row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, object_id))
        if not row:
            raise CrmError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}")
        if row["revision"] != expected_revision:
            raise _conflict(f"지금 revision {row['revision']}")
        if not row["draft_id"] or row["draft_id"] != proposal_id or row["draft_base_revision"] != row["revision"]:
            raise _conflict("제안이 없거나 바뀌었어요")
        nd = row["draft_doc"]
        _check_definition(c, uid, object_id, row, nd)
        rev = row["revision"] + 1
        keep = []
        for pos, f in enumerate(nd["fields"]):
            keep.append(f["key"])
            c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,required,options,target_kind,target_object_id,multiple,position,archived_at) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL) ON CONFLICT (user_id,object_id,key) DO UPDATE SET "
                      "label=excluded.label,value_type=excluded.value_type,required=excluded.required,options=excluded.options,"
                      "target_kind=excluded.target_kind,target_object_id=excluded.target_object_id,multiple=excluded.multiple,"
                      "position=excluded.position,archived_at=NULL",
                      (uid, object_id, f["key"], f["label"], f["value_type"], f["required"], Jsonb(f["options"]), f["target_kind"],
                       f["target_object_id"], f["multiple"], pos))
        c.execute("UPDATE field_defs SET archived_at=now() WHERE user_id=%s AND object_id=%s AND archived_at IS NULL AND NOT (key = ANY(%s))",
                  (uid, object_id, keep))
        snap = {**nd, "revision": rev, "applied_at": now().isoformat()}
        c.execute("UPDATE object_defs SET key=%s,label=%s,semantic_kind=%s,policy=%s,revision=%s,history=history || %s,"
                  "draft_doc=NULL,draft_id=NULL,draft_base_revision=NULL,updated_at=now() WHERE user_id=%s AND id=%s",
                  (nd["key"], nd["label"], nd["semantic_kind"], Jsonb(nd["policy"]), rev, Jsonb([snap]), uid, object_id))
        out = _def_out(c, uid, _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s", (uid, object_id)))
    return out


def template_slot_mark(uid, object_id, slot, field_slots):
    """템플릿 시드 전용 — template_author.seed 가 definition_apply 직후 한 번 부른다. MCP·웹 경로는 부르지 않는다.
    definition_apply 는 template_slot 을 건드리지 않으므로 뒤의 사용자 적용이 슬롯을 지우지 못한다."""
    with tx() as c:
        if c.execute("UPDATE object_defs SET template_slot=%s WHERE user_id=%s AND id=%s", (slot, uid, object_id)).rowcount != 1:
            raise CrmError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}")
        for fk, fs in field_slots.items():
            c.execute("UPDATE field_defs SET template_slot=%s WHERE user_id=%s AND object_id=%s AND key=%s", (fs, uid, object_id, fk))


def definition_history(uid, object_id):
    with tx() as c:
        r = _one(c, "SELECT history FROM object_defs WHERE user_id=%s AND id=%s", (uid, object_id))
    if not r:
        raise CrmError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}")
    return r["history"]


# ── PR #20 표준(std@1) — 상태 읽기 · 편입 미리보기(읽기 전용) · 선택 기록(보류·거절) ──
# 명세 §0·§1·§3. 시드·편입 적용·잠금 검사는 5단계. 자동 분류·편입 없음 — 미리보기는 행을 쓰지 않는다.

STD_CHOICE_STATES = ("deferred", "refused")   # std_choose 의 기록만 하는 값. incorporated 는 편입 적용 갈래, seeded 는 시드만
STD_READY = ("seeded", "incorporated")   # 참조 대상이 이 상태여야 그 참조를 가진 표준 오브젝트를 편입할 수 있다(§3 참조 의존)
STD_STRUCTURES = ("match", "extend", "missing", "conflict")   # 일치 · 확장(표준 + 사용자 필드) · 누락(같은 key 없음) · 충돌
# 충돌 사유 — field=None 이면 오브젝트 단위, 아니면 표준 필드 key 하나
STD_CONFLICTS = ("key_draft_only", "key_archived", "kind", "field_missing", "field_archived", "field_value_type",
                 "field_target_kind", "field_target", "field_multiple", "field_required", "field_options")
STD_BLOCKS = ("already", "conflict", "needs_target")   # 편입 불가 사유: 이미 seeded·incorporated · 구조 충돌 · 참조 대상 미편입


_std_doc_cache: dict = {}


def std_doc():
    """설치 버전 명세 templates/std@1.json — 정규화 정의 + 템플릿 id(od_axes …). 참조 대상은 템플릿 id 로 적혀 있다.
    template_std.py 가 만든 동결 파일이라 프로세스 안에서 한 번만 읽는다. 호출자는 고치지 않는다."""
    if contract.STD_VERSION not in _std_doc_cache:
        _std_doc_cache[contract.STD_VERSION] = template_frozen(contract.STD_VERSION)
    return _std_doc_cache[contract.STD_VERSION]


_STD_SHAPE = ("value_type", "target_kind", "target_object_id", "multiple", "required")   # 표준 필드에서 잠기는 모양(§1)


def _std_lock_check(c, uid, oid, row, nd, given=None):
    """편입된 정의(object_defs.std_id 있음)의 잠금(§1) — 깨면 STANDARD-LOCKED(첫 위반 하나). 플래그와 무관하게 항상 검사한다.
    잠금: key · semantic_kind · 표준 필드(field_defs.std_id 있음)의 존재(빼면 보관 = 삭제)·value_type·target_kind·target_object_id
          (= 그 사용자의 대응 오브젝트)·multiple·required · 명세에 있는 select 선택지 값 삭제.
    허용: label(오브젝트·필드·선택지) · policy · 사용자 필드(std_id 없음) 추가·수정·삭제 · 선택지 추가.
    std_id 는 정의 JSON 에 없고(normalize_definition 이 버린다) apply 의 UPSERT 가 그 열을 안 건드린다 — 이 경로로 떼거나 바꿀 수 없다."""
    if row is None or not row.get("std_id"):
        return
    sid = row["std_id"]
    if nd["key"] != row["key"]:
        raise _std_locked("key", sid)
    if nd["semantic_kind"] != row["semantic_kind"]:
        raise _std_locked("semantic_kind", sid)
    spec = {contract.std_id_of(o["key"], f["key"]): f for o in std_doc()["objects"] for f in o["fields"]}
    byk = {f["key"]: f for f in nd["fields"]}
    for cur in _all(c, "SELECT key,value_type,target_kind,target_object_id,multiple,required,std_id FROM field_defs "
                       "WHERE user_id=%s AND object_id=%s AND std_id IS NOT NULL AND archived_at IS NULL ORDER BY position, key",
                    (uid, oid)):
        nf = byk.get(cur["key"])
        if nf is None or (given is not None and cur["key"] not in given):   # R-3: 정규화가 되살린 표준 필드도 「뺀 것」이다
            raise _std_locked("field_remove", cur["std_id"])
        if any(nf[a] != cur[a] for a in _STD_SHAPE):
            raise _std_locked("field_shape", cur["std_id"])
        sf = spec.get(cur["std_id"])
        if sf and sf["value_type"] == "select" and not {o["value"] for o in sf["options"]} <= {o["value"] for o in nf["options"]}:
            raise _std_locked("option_remove", cur["std_id"])


def _std_rows(c, uid):
    marks = _one(c, "SELECT version,state FROM std_marks WHERE user_id=%s AND version=%s", (uid, contract.STD_VERSION))
    choices = {r["std_key"]: _iso(r) for r in _all(
        c, "SELECT std_key,state,def_id,def_revision,confirmed_meaning,at FROM std_choices WHERE user_id=%s", (uid,))}
    return marks, choices


def _std_state(c, uid):
    marks, choices = _std_rows(c, uid)
    return contract.std_state(marks, {k: r["state"] for k, r in choices.items()})


def std_status(uid):
    """사용자 `std` = {version, state, objects}(contract.std_state 모양). 표식 없으면 version·state null, objects 는 std_choices 대로."""
    with tx() as c:
        return _std_state(c, uid)


def _std_objects(c, uid, lock=False):
    """표준 key → 그 사용자의 대응 오브젝트 행 또는 None. 규칙(미리보기·편입 적용이 같이 쓴다): object_defs.std_id = std:<key> 가 먼저,
    없으면 같은 key 행 — 적용 전 초안(revision 0)·보관 행도 돌려준다(판정은 호출자). UNIQUE(user_id, key) 라 key 갈래는 많아야 하나.
    lock=True 면 그 행들을 FOR UPDATE(편입 적용 — definition_apply 와 직렬화)."""
    sids = [contract.std_id_of(k) for k in contract.STD_KEYS]
    rows = _all(c, "SELECT id,key,label,semantic_kind,revision,archived_at,std_id FROM object_defs "
                   "WHERE user_id=%s AND (key = ANY(%s) OR std_id = ANY(%s)) ORDER BY created_at, id" + (" FOR UPDATE" if lock else ""),
                (uid, list(contract.STD_KEYS), sids))
    out = {}
    for k, sid in zip(contract.STD_KEYS, sids):
        out[k] = next((r for r in rows if r["std_id"] == sid), None) or next((r for r in rows if r["key"] == k), None)
    return out


def _std_classify(sd, row, fields, target_ids):
    """표준 오브젝트 sd ↔ 대응 행 row → (구조 판정, 충돌 [{code, field}]). 사용자 label·값은 담지 않는다(코드·표준 필드 key 만).
    fields = row 의 field_defs 전부(보관 포함). target_ids = 표준 템플릿 id → 그 사용자 대응 오브젝트 id(적용·미보관만, 없으면 키 없음)
    — 표준 참조 필드는 이 id 를 가리켜야 같다. label·policy·사용자 필드 순서는 보지 않는다(잠금 밖)."""
    if row is None:
        return "missing", []
    if row["revision"] == 0:
        return "conflict", [{"code": "key_draft_only", "field": None}]
    if row["archived_at"] is not None:
        return "conflict", [{"code": "key_archived", "field": None}]
    probs = []
    if row["semantic_kind"] != sd["semantic_kind"]:
        probs.append(("kind", None))
    byk = {f["key"]: f for f in fields}
    for sf in sd["fields"]:
        k, uf = sf["key"], byk.get(sf["key"])
        if uf is None:
            probs.append(("field_missing", k))
            continue
        if uf["archived_at"] is not None:
            probs.append(("field_archived", k))
            continue
        if uf["value_type"] != sf["value_type"]:
            probs.append(("field_value_type", k))
            continue
        if uf["target_kind"] != sf["target_kind"]:
            probs.append(("field_target_kind", k))
        elif sf["target_kind"] == "item" and uf["target_object_id"] != target_ids.get(sf["target_object_id"]):
            probs.append(("field_target", k))
        if bool(uf["multiple"]) != bool(sf["multiple"]):
            probs.append(("field_multiple", k))
        if bool(uf["required"]) != bool(sf["required"]):
            probs.append(("field_required", k))
        if sf["value_type"] == "select" and not {o["value"] for o in sf["options"]} <= {o["value"] for o in uf["options"] or []}:
            probs.append(("field_options", k))
    if probs:
        return "conflict", [{"code": code, "field": fk} for code, fk in probs]
    std_keys = {sf["key"] for sf in sd["fields"]}
    extra = any(f["archived_at"] is None and f["key"] not in std_keys for f in fields)
    return ("extend" if extra else "match"), []


def _std_lock_effects(sd):
    """편입하면 막히는 변경(§1) — contract.LOCKED_CHANGES 코드 목록 + 잠기는 표준 필드 key. option_remove 는 표준 select 필드가 있을 때만."""
    has_select = any(f["value_type"] == "select" for f in sd["fields"])
    return {"changes": [ch for ch in contract.LOCKED_CHANGES if ch != "option_remove" or has_select],
            "fields": [f["key"] for f in sd["fields"]]}


def _std_assess(c, uid, lock=False):
    """미리보기 판정 — std_preview(읽기 전용)와 편입 적용(재검증, lock=True)이 같은 함수를 쓴다.
    반환 (std, objects, rows, target_ids). objects 는 std_preview 의 objects 와 같은 모양."""
    doc = std_doc()
    tid_key = {o["id"]: o["key"] for o in doc["objects"]}
    marks, choices = _std_rows(c, uid)
    rows = _std_objects(c, uid, lock)
    target_ids = {o["id"]: rows[o["key"]]["id"] for o in doc["objects"]
                  if rows[o["key"]] and rows[o["key"]]["revision"] > 0 and rows[o["key"]]["archived_at"] is None}
    fields = {k: _all(c, "SELECT key,value_type,required,options,target_kind,target_object_id,multiple,archived_at "
                         "FROM field_defs WHERE user_id=%s AND object_id=%s", (uid, r["id"]))
              for k, r in rows.items() if r}
    std = contract.std_state(marks, {k: r["state"] for k, r in choices.items()})
    out = []
    for sd in doc["objects"]:
        k, row = sd["key"], rows[sd["key"]]
        state = std["objects"][k]
        structure, conflicts = _std_classify(sd, row, fields.get(k, []), target_ids)
        needs = []
        for f in sd["fields"]:
            tk = tid_key.get(f["target_object_id"]) if f["target_kind"] == "item" else None
            if tk and tk != k:
                needs.append({"field": f["key"], "std_key": tk, "state": std["objects"][tk], "ready": std["objects"][tk] in STD_READY})
        blocked = ([] if state not in STD_READY else ["already"]) + (["conflict"] if structure == "conflict" else []) \
            + (["needs_target"] if any(not n["ready"] for n in needs) else [])
        ch = choices.get(k)
        out.append({
            "std_key": k, "std_id": contract.std_id_of(k), "std_name": contract.std_name(contract.std_id_of(k)), "state": state,
            "choice": {kk: ch[kk] for kk in ("def_id", "def_revision", "confirmed_meaning", "at")} if ch else None,
            "structure": structure, "conflicts": conflicts,
            "object": {"id": row["id"], "key": row["key"], "label": row["label"], "revision": row["revision"],
                       "archived": row["archived_at"] is not None} if row else None,
            "meaning_check": row is not None, "lock": _std_lock_effects(sd), "needs": needs,
            "can_incorporate": not blocked, "blocked": blocked})
    return std, out, rows, target_ids


def std_preview(uid):
    """편입 미리보기(§3) — 본인 것만, 읽기 전용(READ ONLY 트랜잭션 — 어떤 행도 쓰지 않는다), 플래그와 무관.
    표준 오브젝트마다(std@1.json 순): 현재 상태(std_choices, 없으면 none) · 구조 판정 하나 + 충돌 사유 · 대응 오브젝트(본인 label 포함)
    · meaning_check(같은 key 기존 행이 있으면 항상 true — 초안·보관 포함) · 잠금 영향 · 필요한 대상(표준 참조 필드의 대상 표준 key 와
    그 상태, ready = seeded·incorporated) · 편입 가능 여부와 불가 사유(STD_BLOCKS). 대상을 자동 편입하지 않는다.
    편입 요청은 여기서 본 object.revision(누락이면 null)을 expected_revision 으로 들고 온다."""
    with tx() as c:
        c.execute("SET TRANSACTION READ ONLY")
        std, out, _, _ = _std_assess(c, uid)
    return {"version": contract.STD_VERSION, "std": std, "objects": out}


# ── 표준 정의 넣기 — 신규 시드(§2)와 누락 편입(§3)이 같이 쓴다 ──

def _std_free_id(c, uid, oid):
    """명세 id(od_axes …)를 이 사용자가 이미 쓰면 새 id(§12 — 식별은 std_id, id 엔 의미를 두지 않는다)."""
    while _one(c, "SELECT 1 AS x FROM object_defs WHERE user_id=%s AND id=%s", (uid, oid)):
        oid = f"od_{secrets.token_hex(5)}"
    return oid


def _std_insert_def(c, uid, sd, oid, target_ids):
    """표준 오브젝트 하나를 적용 상태(revision 1)로 넣는다 — definition_apply 와 같은 모양(history 스냅샷·field position).
    오브젝트·모든 표준 필드에 std_id. 참조 대상 = target_ids(명세 템플릿 id → 이 사용자 대응 오브젝트 id). template_slot 은 비운다.
    일일 한도를 세지 않는다(_DAILY_SOURCES 가 std_id 있는 정의를 뺀다)."""
    fields = []
    for f in sd["fields"]:
        to = f["target_object_id"]
        if to and to not in target_ids:
            raise _bad_def(f"표준 필드 {f['key']} 의 대상이 아직 없어요.", "참조 대상 표준을 먼저 편입하세요.")
        fields.append({**f, "target_object_id": target_ids[to] if to else None})
    nd = {"key": sd["key"], "label": sd["label"], "semantic_kind": sd["semantic_kind"], "policy": sd["policy"], "fields": fields}
    snap = {**nd, "revision": 1, "applied_at": now().isoformat()}
    sid = contract.std_id_of(sd["key"])
    c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind,revision,policy,history,std_id) VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s)",
              (uid, oid, nd["key"], nd["label"], nd["semantic_kind"], Jsonb(nd["policy"]), Jsonb([snap]), sid))
    for pos, f in enumerate(fields):
        c.execute("INSERT INTO field_defs (user_id,object_id,key,label,value_type,required,options,target_kind,target_object_id,multiple,"
                  "position,std_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (uid, oid, f["key"], f["label"], f["value_type"], f["required"], Jsonb(f["options"]), f["target_kind"],
                   f["target_object_id"], f["multiple"], pos, contract.std_id_of(sd["key"], f["key"])))


def _std_choice_set(c, uid, std_key, state, def_id, def_revision, confirmed):
    c.execute("INSERT INTO std_choices (user_id,std_key,state,def_id,def_revision,confirmed_meaning) VALUES (%s,%s,%s,%s,%s,%s) "
              "ON CONFLICT (user_id,std_key) DO UPDATE SET state=excluded.state,def_id=excluded.def_id,"
              "def_revision=excluded.def_revision,confirmed_meaning=excluded.confirmed_meaning,at=now()",
              (uid, std_key, state, def_id, def_revision, confirmed))


# ── 신규 계정 시드 (§2) — 사용자 생성 트랜잭션 안. 기존 사용자(표식 없음)는 자동 시드·분류하지 않는다 ──

def _std_seed(c, uid):
    """정의 3개(명세 순 — 참조 대상이 먼저) + std_choices 3행(seeded) + 분류 역할(표준 축) + 표식 state='seeded'. 호출자가 savepoint 로 감싼다."""
    tids = {}
    for sd in std_doc()["objects"]:
        oid = _std_free_id(c, uid, sd["id"])
        _std_insert_def(c, uid, sd, oid, tids)
        tids[sd["id"]] = oid
        _std_choice_set(c, uid, sd["key"], "seeded", oid, 1, False)
        if sd["key"] == "axes":   # 분류 역할(§5) — 신규 시드는 표준 축이 classify 대상
            c.execute("INSERT INTO user_roles (user_id,role,object_id) VALUES (%s,'classify',%s) ON CONFLICT (user_id,role) DO NOTHING",
                      (uid, oid))
    c.execute("UPDATE std_marks SET state='seeded', at=now() WHERE user_id=%s AND version=%s", (uid, contract.STD_VERSION))


def _std_seed_try(c, uid, where):
    """_std_seed 를 savepoint 안에서 — 실패하면 그 savepoint 만 롤백하고 로그. 바깥 트랜잭션(사용자 생성·로그인)은 산다.
    표식은 seed_pending 으로 남는다(자동 재시도 대상). 반환 성공 여부."""
    try:
        with c.transaction():
            _std_seed(c, uid)
        return True
    except Exception as e:
        print(f"[std] {where} 표준 시드 실패 — seed_pending 으로 남김: {type(e).__name__}", flush=True)
        return False


def _std_on_create(c, uid):
    """user_by_subject·user_by_email 의 사용자 INSERT 직후, 같은 트랜잭션(PR B 부터 플래그 없이 상시):
    std_marks(seed_pending) → savepoint 안에서 시드. 새 사용자 행은 아직 커밋 전이라 다른 트랜잭션이 볼 수 없다(동시 실행 없음)."""
    c.execute("INSERT INTO std_marks (user_id,version,state) VALUES (%s,%s,'seed_pending') ON CONFLICT (user_id,version) DO NOTHING",
              (uid, contract.STD_VERSION))
    _std_seed_try(c, uid, "create")


def _std_retry(c, uid):
    """기존 사용자 갈래(조회·이메일 연결)에서 — 표식이 seed_pending 일 때만 자동 재시도.
    users 행 잠금(동시 요청 직렬화) → 표식 재확인 → 그 사용자 소유 정의가 출처와 무관하게(초안 포함) 0개일 때만 시드.
    정의가 하나라도 있으면 시드하지 않는다(표식은 seed_pending 그대로 — §3 편입으로). 표식 없는 사용자는 아무것도 안 한다."""
    m = _one(c, "SELECT state FROM std_marks WHERE user_id=%s AND version=%s", (uid, contract.STD_VERSION))
    if not m or m["state"] != "seed_pending":
        return
    _user_lock(c, uid)
    m = _one(c, "SELECT state FROM std_marks WHERE user_id=%s AND version=%s FOR UPDATE", (uid, contract.STD_VERSION))
    if not m or m["state"] != "seed_pending":
        return
    if _one(c, "SELECT 1 AS x FROM object_defs WHERE user_id=%s LIMIT 1", (uid,)):
        return
    _std_seed_try(c, uid, "retry")


# ── 선택 기록 · 편입 적용 (§3) ──

def _std_stale(why):
    return CrmError("CONFLICT", f"{CONFLICT_MSG} ({why})", "표준 미리보기를 다시 보고 고르세요.")


def std_choose(uid, std_key, state, confirmed_meaning=False, expected_revision=None):
    """표준 오브젝트 하나의 선택(§3). 기능 플래그 없음(PR B — 상시 켬).
    deferred·refused: 기록만, 다시 고르면 덮어쓴다(이력 없음), confirmed_meaning=false. def_id·def_revision = 지금 대응 오브젝트
      (있으면 — 초안·보관 포함). 이 기록은 정의·항목 쓰기를 막지 않는다 — 다른 쓰기 경로는 std_choices 를 읽지 않는다.
    incorporated: 편입 적용(_std_incorporate). confirmed_meaning=True 필수 — 기존 오브젝트면 「같은 뜻 + 잠금 영향 확인」,
      누락이면 「넣을 정의 확인」. expected_revision = 미리보기에서 본 대응 오브젝트 revision(누락이면 None).
    이미 seeded·incorporated 이거나 대응 오브젝트에 std_id 가 있으면 보류·거절은 STANDARD-LOCKED(편입 되돌리기 = std_id 제거, §1)."""
    if std_key not in contract.STD_KEYS:
        raise CrmError("ARG-INVALID", f"std_key 는 {', '.join(contract.STD_KEYS)} 중 하나예요.")
    if state not in STD_CHOICE_STATES + ("incorporated",):
        raise CrmError("ARG-INVALID", "state 는 deferred(보류)·refused(거절)·incorporated(편입) 중 하나예요.")
    if state == "incorporated":
        if confirmed_meaning is not True:
            raise CrmError("ARG-INVALID", "편입하려면 확인(confirmed_meaning=true)이 필요해요.",
                           "이 오브젝트를 표준과 같은 뜻으로 쓰는지, 잠금 영향을 확인한 뒤 다시 보내세요.")
        if expected_revision is not None and (not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
            raise CrmError("ARG-INVALID", "expected_revision 은 정수 또는 null 이에요.")
    with tx() as c:
        _user_lock(c, uid)   # 잠금 순서 = users 행 → object_defs → std_choices
        if state == "incorporated":
            return _std_incorporate(c, uid, std_key, expected_revision)
        row = _std_objects(c, uid)[std_key]
        cur = _one(c, "SELECT state FROM std_choices WHERE user_id=%s AND std_key=%s FOR UPDATE", (uid, std_key))
        if (cur and cur["state"] in STD_READY) or (row and row["std_id"]):
            raise _std_locked("std_id", contract.std_id_of(std_key))
        did, drev = (row["id"], row["revision"]) if row else (None, None)
        _std_choice_set(c, uid, std_key, state, did, drev, False)
        std = _std_state(c, uid)
    return {"std_key": std_key, "state": state, "def_id": did, "def_revision": drev, "std": std}


def _std_incorporate(c, uid, std_key, expected_revision):
    """편입 적용 — 호출자 트랜잭션(users 행 잠금 뒤). 표준 관련 object_defs 를 FOR UPDATE 로 잡고 미리보기와 같은 판정(_std_assess)을
    다시 돌린다. 재검증: 대응 오브젝트 revision 이 expected_revision 과 다르면 CONFLICT · 이미 seeded·incorporated 면 CONFLICT ·
    구조 충돌이면 DEF-INVALID · 참조 대상이 편입(또는 seeded) 전이면 DEF-INVALID — 하나라도 걸리면 아무것도 쓰지 않는다.
    통과: 누락 = 표준 정의를 새로 넣는다(명세 id 를 이미 쓰면 새 id) · 일치·확장 = 오브젝트와 표준 필드에 std_id(revision 은 그대로 —
    정의 내용이 바뀌지 않는다). 그리고 std_choices=incorporated(confirmed_meaning=true) · 표식이 없거나 seed_pending 이면 adopting."""
    _, objs, rows, target_ids = _std_assess(c, uid, lock=True)
    o = next(x for x in objs if x["std_key"] == std_key)
    row = rows[std_key]
    cur_rev = row["revision"] if row else None
    if cur_rev != expected_revision:
        raise _std_stale(f"{std_key} 정의 revision 이 지금 {cur_rev}")
    if "already" in o["blocked"]:
        raise _std_stale(f"{std_key} 는 이미 {o['state']}")
    if "conflict" in o["blocked"]:
        raise _bad_def(f"표준 {std_key} 와 구조가 달라 편입할 수 없어요 — " + " · ".join(
            x["code"] + (f":{x['field']}" if x["field"] else "") for x in o["conflicts"]),
            "대화로 정의를 표준 모양에 맞춘 뒤 미리보기를 다시 보세요.")
    if "needs_target" in o["blocked"]:
        raise _bad_def(f"표준 {std_key} 는 참조 대상 표준을 먼저 편입해야 해요 — " + " · ".join(
            n["std_key"] for n in o["needs"] if not n["ready"]), "참조 대상 표준을 먼저 편입한 뒤 다시 하세요.")
    sd = next(x for x in std_doc()["objects"] if x["key"] == std_key)
    if row is None:
        oid, rev = _std_free_id(c, uid, sd["id"]), 1
        _std_insert_def(c, uid, sd, oid, target_ids)
    else:
        oid, rev = row["id"], row["revision"]
        c.execute("UPDATE object_defs SET std_id=%s, updated_at=now() WHERE user_id=%s AND id=%s", (contract.std_id_of(std_key), uid, oid))
        c.execute("UPDATE field_defs SET std_id = %s || '.' || key WHERE user_id=%s AND object_id=%s AND archived_at IS NULL "
                  "AND key = ANY(%s)", (contract.std_id_of(std_key), uid, oid, [f["key"] for f in sd["fields"]]))
    _std_choice_set(c, uid, std_key, "incorporated", oid, rev, True)
    # 표식 없음 → adopting 생성 · seed_pending(시드를 멈춘 계정) → adopting 으로 올림(§12) · seeded·adopting 은 그대로
    c.execute("INSERT INTO std_marks (user_id,version,state) VALUES (%s,%s,'adopting') ON CONFLICT (user_id,version) "
              "DO UPDATE SET state='adopting', at=now() WHERE std_marks.state='seed_pending'", (uid, contract.STD_VERSION))
    return {"std_key": std_key, "state": "incorporated", "def_id": oid, "def_revision": rev, "std": _std_state(c, uid)}


# ── 관계 ──

# reference 대상 종류 → item_links 대상 열(정확히 하나만 채운다 — ck_item_links_target)
LINK_TARGET_COLS = {"item": "target_item_id", "record": "target_record_id", "candidate": "target_candidate_id",
                    "person": "target_person_id", "source": "target_source_id", "activity": "target_activity_id"}
_LINK_TARGET_SQL = ",".join(LINK_TARGET_COLS.values())


def _link_out(r):
    for kind, col in LINK_TARGET_COLS.items():
        if r.get(col):
            return {"field_key": r["field_key"], "target_kind": kind, "target_id": r[col]}
    raise CrmError("DB-STATE", "관계 대상이 비었어요.")


def _links_by_item(c, uid, item_ids):
    out = {}
    if not item_ids:
        return out
    rows = _all(c, "SELECT l.source_item_id,l.field_key,l.target_item_id,l.target_record_id,l.target_candidate_id,"
                   "l.target_person_id,l.target_source_id,l.target_activity_id,t.object_id AS t_obj "
                   "FROM item_links l LEFT JOIN items t ON t.user_id=l.user_id AND t.id=l.target_item_id "
                   "WHERE l.user_id=%s AND l.source_item_id = ANY(%s) ORDER BY l.id", (uid, list(item_ids)))
    for r in rows:
        d = _link_out(r)
        if r["t_obj"]:
            d["target_object_id"] = r["t_obj"]
        out.setdefault(r["source_item_id"], []).append(d)
    return out


def _resolve_target(c, uid, kind, tid):
    """대상이 이 사용자 것이고 살아 있으면 {kind,id,object_id?,semantic_kind?}, 아니면 None."""
    if kind == "item":
        r = _one(c, "SELECT i.id,i.object_id,o.semantic_kind FROM items i JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id "
                    "WHERE i.user_id=%s AND i.id=%s AND i.revision > 0 AND i.archived_at IS NULL", (uid, tid))
        return r and {"kind": kind, "id": r["id"], "object_id": r["object_id"], "semantic_kind": r["semantic_kind"]}
    if kind == "record":
        r = _one(c, "SELECT id FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL", (uid, tid))
        return r and {"kind": kind, "id": r["id"]}
    if kind == "candidate":
        r = _one(c, "SELECT c.id FROM candidates c JOIN records r ON r.id=c.record_id AND r.deleted_at IS NULL "
                    "WHERE c.user_id=%s AND c.id=%s", (uid, tid))
        return r and {"kind": kind, "id": r["id"]}
    if kind == "person":
        r = _one(c, "SELECT id FROM people WHERE user_id=%s AND id=%s", (uid, tid))
        return r and {"kind": kind, "id": r["id"]}
    if kind == "source":
        r = _one(c, "SELECT id FROM sources WHERE user_id=%s AND id=%s", (uid, tid))
        return r and {"kind": kind, "id": r["id"]}
    if kind == "activity":   # 특정 판을 가리킨다 — 현재 판으로 바꾸지 않는다. 지운 판(deleted_at)은 새로 가리킬 수 없다
        r = _one(c, "SELECT id FROM activities WHERE user_id=%s AND id=%s AND deleted_at IS NULL", (uid, tid))
        return r and {"kind": kind, "id": r["id"]}
    return None


def _norm_links(c, uid, fields, links):
    """links 인자 → [{field_key,target_kind,target_id,target_object_id?}]. 필드·대상 종류·소유·대상 오브젝트를 본다."""
    if links is None:
        links = []
    if not isinstance(links, list):
        raise _bad_link("links 는 목록이에요.")
    byk = {f["key"]: f for f in fields}
    out, seen = [], set()
    for ln in links:
        if not isinstance(ln, dict):
            raise _bad_link("link 는 {field_key, target_kind, target_id} 예요.")
        fk, kind, tid = ln.get("field_key"), ln.get("target_kind"), str(ln.get("target_id") or "")
        f = byk.get(fk)
        if not f or f["value_type"] != "reference":
            raise _bad_link(f"{fk} 는 이 오브젝트의 관계(reference) 필드가 아니에요.", "definitions_get 으로 필드를 확인하세요.")
        if kind != f["target_kind"]:
            raise _bad_link(f"{f['label']}({fk}) 는 {f['target_kind']} 만 가리켜요.")
        t = _resolve_target(c, uid, kind, tid)
        if not t:
            raise _bad_link(f"연결할 대상을 찾을 수 없어요: {kind} {tid}")
        if kind == "item" and t["object_id"] != f["target_object_id"]:
            raise _bad_link(f"{f['label']}({fk}) 는 오브젝트 {f['target_object_id']} 의 항목만 가리켜요.")
        if (fk, kind, tid) in seen:
            continue
        seen.add((fk, kind, tid))
        d = {"field_key": fk, "target_kind": kind, "target_id": tid}
        if kind == "item":
            d["target_object_id"] = t["object_id"]
        out.append(d)
    return out


def _insert_link(c, uid, link, source_item=None, source_object=None, source_record=None):
    col = LINK_TARGET_COLS[link["target_kind"]]
    c.execute(f"INSERT INTO item_links (user_id,source_item_id,source_object_id,source_record_id,field_key,{col}) "
              "VALUES (%s,%s,%s,%s,%s,%s)", (uid, source_item, source_object, source_record, link["field_key"], link["target_id"]))


def _replace_item_links(c, uid, item_id, object_id, links):
    c.execute("DELETE FROM item_links WHERE user_id=%s AND source_item_id=%s", (uid, item_id))
    for ln in links:
        _insert_link(c, uid, ln, source_item=item_id, source_object=object_id)


def record_links(uid, record_ids=None):
    sql = ("SELECT source_record_id AS record_id,field_key,target_item_id,target_record_id,target_candidate_id FROM item_links "
           "WHERE user_id=%s AND source_record_id IS NOT NULL")
    args = [uid]
    if record_ids:
        sql += " AND source_record_id = ANY(%s)"
        args.append(list(record_ids))
    with tx() as c:
        return [{"record_id": r["record_id"], **_link_out(r)} for r in _all(c, sql + " ORDER BY id", args)]


def _classify_target(c, uid):
    """분류 역할(§5) 대상 오브젝트 id 또는 None. 역할이 없으면 이관 규칙(_migrate_classify_roles: key='axes' 인 적용·미보관 오브젝트)을
    이 사용자에게 지금 적용해 본다 — 배포 사이에 축을 만든 사용자도 다음 재시작을 기다리지 않는다. 같은 규칙이라 결과가 같다."""
    r = _one(c, "SELECT object_id FROM user_roles WHERE user_id=%s AND role='classify'", (uid,))
    if r:
        return r["object_id"]
    r = _one(c, "INSERT INTO user_roles (user_id, role, object_id) SELECT user_id, 'classify', id FROM object_defs "
                "WHERE user_id=%s AND key='axes' AND revision > 0 AND archived_at IS NULL ON CONFLICT (user_id, role) DO NOTHING "
                "RETURNING object_id", (uid,))
    return r["object_id"] if r else None


def classify_target(uid):
    """분류 대상 오브젝트 id 또는 None(읽기 — 이관 규칙을 쓰지 않는다)."""
    with tx() as c:
        r = _one(c, "SELECT object_id FROM user_roles WHERE user_id=%s AND role='classify'", (uid,))
    return r["object_id"] if r else None


def record_attach(uid, record_id, attach_to=None, axes=None, use_at=None, unattached=None):
    """「이게 어디에 붙나」 네 줄. 항목 → links(attach_to: 기준·결정 항목 또는 후보) · 분류 → links(field_key='axes': 분류 대상 오브젝트 항목,
    대상은 역할 매핑 — 없으면 CLASSIFY-OFF, 나머지 칸은 그대로 쓸 수 있다. 저장 키 axes 는 이관 없이 그대로)
    · 쓸 자리·아직 안 붙는 것 → records.notes. None 인 인자는 그대로 두고, 값이 오면(빈 목록·빈 문자열 포함) 바꾼다. 원문은 안 바뀐다."""
    with tx() as c:
        _user_lock(c, uid)   # PR B — 삭제 CLI 와 직렬화(삭제 뒤 자료 생성 0). 잠금 순서 = users 행 → records
        rec = _one(c, "SELECT id,notes FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL FOR UPDATE", (uid, record_id))
        if not rec:
            raise CrmError("RECORD-MISSING", f"기록을 찾을 수 없어요: {record_id}")
        if attach_to is not None:
            links, seen = [], set()
            for ln in attach_to:
                if not isinstance(ln, dict):
                    raise _bad_link("attach_to 는 {field_key, target_kind, target_id} 목록이에요.")
                if (ln.get("field_key") or "attach_to") != "attach_to":
                    raise _bad_link("attach_to 의 field_key 는 attach_to 예요.")
                kind, tid = ln.get("target_kind"), str(ln.get("target_id") or "")
                if kind not in ("item", "candidate"):
                    raise _bad_link("attach_to 는 항목(item) 또는 후보(candidate)만 가리켜요.")
                t = _resolve_target(c, uid, kind, tid)
                if not t:
                    raise _bad_link(f"붙일 대상을 찾을 수 없어요: {kind} {tid}")
                if kind == "item" and t["semantic_kind"] not in ("principle", "event"):
                    raise _bad_link("attach_to 항목은 기준(principle)·결정(event) 종류만이에요.")
                if (kind, tid) not in seen:
                    seen.add((kind, tid))
                    links.append({"field_key": "attach_to", "target_kind": kind, "target_id": tid})
            c.execute("DELETE FROM item_links WHERE user_id=%s AND source_record_id=%s AND field_key='attach_to'", (uid, record_id))
            for ln in links:
                _insert_link(c, uid, ln, source_record=record_id)
        if axes is not None:
            target = _classify_target(c, uid)
            if target is None:
                raise CrmError(contract.CLASSIFY_OFF, contract.CLASSIFY_OFF_MSG, contract.CLASSIFY_OFF_ACT)
            ids = []
            for tid in axes:
                tid = str(tid or "")
                t = _resolve_target(c, uid, "item", tid)
                if not t:
                    raise _bad_link(f"분류 항목을 찾을 수 없어요: {tid}")
                if t["object_id"] != target:
                    raise _bad_link("분류 대상 오브젝트의 항목만 연결할 수 있어요.", "state_get 의 분류 대상 오브젝트 항목 ID 를 쓰세요.")
                if tid not in ids:
                    ids.append(tid)
            c.execute("DELETE FROM item_links WHERE user_id=%s AND source_record_id=%s AND field_key='axes'", (uid, record_id))
            for tid in ids:
                _insert_link(c, uid, {"field_key": "axes", "target_kind": "item", "target_id": tid}, source_record=record_id)
        notes = dict(rec["notes"] or {})
        if use_at is not None:
            notes["use_at"] = use_at
        if unattached is not None:
            notes["unattached"] = unattached
        if use_at is not None or unattached is not None:
            c.execute("UPDATE records SET notes=%s WHERE user_id=%s AND id=%s", (Jsonb(notes), uid, record_id))
    return {"record_id": record_id, "notes": notes, "links": record_links(uid, [record_id])}


# ── 항목 ──

_ITEM_COLS = ("id,object_id,schema_revision,code,title,body,data,decision,revision,draft_doc,draft_id,draft_base_revision,"
              "archived_at,created_at,updated_at")


def _object_row(c, uid, oid, applied=True):
    r = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s", (uid, oid))
    if not r or (applied and (r["revision"] == 0 or r["archived_at"])):
        raise CrmError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {oid}", "definitions_get 으로 ID 를 확인하세요.")
    return r


def _item_state(row, links):
    """item_history.prev_state — 변경 전 현재값 전체."""
    return {"title": row["title"], "body": row["body"], "data": row["data"], "decision": row["decision"], "code": row["code"],
            "schema_revision": row["schema_revision"],
            "links": [{k: ln[k] for k in ("field_key", "target_kind", "target_id")} for ln in links],
            "archived": row["archived_at"] is not None}


def _code_taken(c, uid, oid, code, item_id):
    return _one(c, "SELECT id FROM items WHERE user_id=%s AND object_id=%s AND code=%s AND id<>%s", (uid, oid, code, item_id or ""))


def item_propose(uid, object_id, schema_revision, item_id, expected_revision, code, title, body, data, links):
    """항목 제안 → draft. 신규는 revision 0 행을 만든다(일반 조회에서 빠짐). 적용은 item_decide(apply)."""
    if not isinstance(data, dict):
        raise CrmError("ARG-INVALID", "data 는 JSON 객체예요.")
    title = (title or "").strip()
    if not title:
        raise CrmError("ARG-INVALID", "제목(title)이 비었어요.")
    body = body or ""
    code = (code or "").strip() or None
    with tx() as c:
        _user_lock(c, uid)   # 잠금 순서 = users 행 → items. 한도는 새 행을 만드는 경로에서만 센다
        od = _object_row(c, uid, object_id)
        if schema_revision != od["revision"]:
            raise _conflict(f"정의 revision 이 지금 {od['revision']}")
        fields = _fields(c, uid, object_id)
        nlinks = _norm_links(c, uid, fields, links)
        dp, lp = _item_problems(fields, od["policy"], data, nlinks, strict=True)
        if dp:
            raise CrmError("ARG-INVALID", "항목 값이 정의와 맞지 않아요 — " + " · ".join(dp), "definitions_get 으로 필드를 확인하세요.")
        if lp:
            raise _bad_link("관계가 정의와 맞지 않아요 — " + " · ".join(lp))
        if item_id:
            row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s FOR UPDATE", (uid, item_id))
            if not row:
                raise CrmError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
            if row["object_id"] != object_id:
                raise CrmError("ARG-INVALID", "항목의 오브젝트가 달라요.")
            if row["revision"] != expected_revision:
                raise _conflict(f"지금 revision {row['revision']}")
            if row["archived_at"]:
                raise CrmError("ARG-INVALID", "보관된 항목이에요. 먼저 되돌리세요(restore).")
            if row["code"] is not None:
                if code is not None and code != row["code"]:
                    raise CrmError("ARG-INVALID", f"코드는 적용 뒤 바꿀 수 없어요(지금 {row['code']}).")
                code = row["code"]
        else:
            if expected_revision != 0:
                raise _conflict("새 항목은 expected_revision 0")
            _daily_gate(c, uid, "proposals")
            item_id = new_id("i")
            c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,decision) VALUES (%s,%s,%s,%s,%s)",
                      (uid, item_id, object_id, od["revision"], "pending" if od["semantic_kind"] == "principle" else None))
        if code and _code_taken(c, uid, object_id, code, item_id):
            raise CrmError("ARG-INVALID", f"코드 {code} 는 이미 쓴 코드예요. 코드는 다시 쓰지 않아요(보관 포함).")
        pid = new_id("ip")
        draft = {"schema_revision": od["revision"], "code": code, "title": title, "body": body, "data": data,
                 "links": [{k: ln[k] for k in ("field_key", "target_kind", "target_id")} for ln in nlinks],
                 "proposed_at": now().isoformat()}
        c.execute("UPDATE items SET draft_doc=%s, draft_id=%s, draft_base_revision=revision WHERE user_id=%s AND id=%s",
                  (Jsonb(draft), pid, uid, item_id))
    return {"item_id": item_id, "proposal_id": pid, "base_revision": expected_revision,
            "note": "제안만 저장했어요. 사용자가 적용하라고 말할 때만 item_decide(action=apply) 를 부르세요. 웹 화면에서도 적용할 수 있어요."}


def _promotion(c, uid, policy, links):
    """승격 조건 충족 여부(사실만). 판단은 사용자. 확정 + 인용 적격 기록(본인 원문·삭제 안 됨)의 후보만 센다.
    PR①: 출처 미확인(unknown 등) 기록의 후보는 목록엔 보이지만 근거 수에서 빠진다(raw §4 「현재 근거 집계」)."""
    p = {**DEFAULT_PROMOTION, **((policy or {}).get("promotion") or {})}
    claim_ids = [ln["target_id"] for ln in links if ln["field_key"] == p["claim_field"] and ln["target_kind"] == "candidate"]
    ok_ids = set()
    if claim_ids:
        ok_ids = {r["id"] for r in _all(c, "SELECT c.id FROM candidates c JOIN records r ON r.user_id=c.user_id AND r.id=c.record_id "
                                           f"AND {_ELIGIBLE_SQL} "
                                           "WHERE c.user_id=%s AND c.id = ANY(%s) AND c.decision='confirmed'", (uid, claim_ids))}
    events = 0
    if p["distinct_events_min"] > 0 and p.get("event_field"):
        ev_ids = [ln["target_id"] for ln in links if ln["field_key"] == p["event_field"] and ln["target_kind"] == "item"]
        if ev_ids:
            live = [r["id"] for r in _all(c, "SELECT id FROM items WHERE user_id=%s AND id = ANY(%s) AND revision > 0 AND archived_at IS NULL",
                                          (uid, ev_ids))]
            if p.get("event_claim_field"):   # 결정(event)의 판단 중 하나 이상이 이 기준의 확정 근거에도 있어야 센다
                ev_links = _links_by_item(c, uid, live)
                live = [e for e in live if any(ln["field_key"] == p["event_claim_field"] and ln["target_id"] in ok_ids
                                               for ln in ev_links.get(e, []))]
            events = len(set(live))
    met = len(ok_ids) >= p["confirmed_claims_min"] and events >= p["distinct_events_min"]
    return {"met": met, "confirmed_claims": len(ok_ids), "confirmed_claims_min": p["confirmed_claims_min"],
            "linked_claims": len(set(claim_ids)), "distinct_events": events, "distinct_events_min": p["distinct_events_min"]}


def promotion_status(uid, item_id):
    with tx() as c:
        row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s", (uid, item_id))
        if not row:
            raise CrmError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
        od = _object_row(c, uid, row["object_id"], applied=False)
        return _promotion(c, uid, od["policy"], _links_by_item(c, uid, [item_id]).get(item_id, []))


def _item_out(c, uid, row, links, od):
    d = _iso(row)
    d["links"] = links
    d["semantic_kind"] = od["semantic_kind"]
    d["proposal"] = ({"proposal_id": row["draft_id"], "base_revision": row["draft_base_revision"], **(row["draft_doc"] or {})}
                     if row["draft_id"] else None)
    for k in ("draft_id", "draft_base_revision", "draft_doc"):
        d.pop(k, None)
    if od["semantic_kind"] == "principle" and row["revision"] > 0:
        d["promotion"] = _promotion(c, uid, od["policy"], links)
    return d


def _items_query(c, uid, where, args):
    rows = _all(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND {where} ORDER BY updated_at DESC, id", [uid, *args])
    links = _links_by_item(c, uid, [r["id"] for r in rows])
    ods = {r["id"]: r for r in _all(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s", (uid,))}
    return [_item_out(c, uid, r, links.get(r["id"], []), ods[r["object_id"]]) for r in rows]


def items(uid, object_id=None):
    """적용된(revision>0)·보관 안 된 항목. 관계·제안·(기준이면) 승격 상태 포함."""
    with tx() as c:
        if object_id:
            return _items_query(c, uid, "revision > 0 AND archived_at IS NULL AND object_id=%s", [object_id])
        return _items_query(c, uid, "revision > 0 AND archived_at IS NULL", [])


def item_proposals(uid):
    """아직 적용 안 된 새 항목 제안(revision 0). 웹에서 적용·제외할 수 있게 상태에 싣는다."""
    with tx() as c:
        return _items_query(c, uid, "revision = 0 AND draft_id IS NOT NULL", [])


def item_get(uid, item_id):
    """항목 하나 + 관계 대상 요약 + 들어오는 관계 + 이력 + 승격 상태. 웹 서랍용."""
    with tx() as c:
        row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s", (uid, item_id))
        if not row:
            raise CrmError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
        od = _object_row(c, uid, row["object_id"], applied=False)
        out = _item_out(c, uid, row, _links_by_item(c, uid, [item_id]).get(item_id, []), od)
        out["object"] = {"id": od["id"], "key": od["key"], "label": od["label"], "semantic_kind": od["semantic_kind"], "revision": od["revision"]}
        out["fields"] = _fields(c, uid, od["id"])
        tg = {}
        for ln in out["links"] + ((out["proposal"] or {}).get("links") or []):
            k = (ln["target_kind"], ln["target_id"])
            if k in tg:
                continue
            if ln["target_kind"] == "candidate":
                r = _one(c, f"SELECT {_CAND_COLS} FROM candidates WHERE user_id=%s AND id=%s", (uid, ln["target_id"]))
            elif ln["target_kind"] == "record":
                r = _one(c, "SELECT id,title,created,deleted_at FROM records WHERE user_id=%s AND id=%s", (uid, ln["target_id"]))
            else:
                r = _one(c, "SELECT i.id,i.code,i.title,i.object_id,i.decision,i.archived_at,o.label AS object_label FROM items i "
                            "JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id WHERE i.user_id=%s AND i.id=%s",
                         (uid, ln["target_id"]))
            tg[k] = _iso(r) if r else None
        out["targets"] = [{"target_kind": k[0], "target_id": k[1], "target": v} for k, v in tg.items()]
        inc = _all(c, "SELECT l.field_key,l.source_item_id,l.source_record_id,i.code,i.title,i.object_id,o.label AS object_label,"
                      "r.title AS record_title FROM item_links l "
                      "LEFT JOIN items i ON i.user_id=l.user_id AND i.id=l.source_item_id "
                      "LEFT JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id "
                      "LEFT JOIN records r ON r.user_id=l.user_id AND r.id=l.source_record_id "
                      "WHERE l.user_id=%s AND l.target_item_id=%s AND (i.id IS NULL OR (i.revision > 0 AND i.archived_at IS NULL)) "
                      "AND (r.id IS NULL OR r.deleted_at IS NULL) ORDER BY l.id", (uid, item_id))
        out["incoming"] = [_iso(r) for r in inc]
        out["history"] = [_iso(r) for r in _all(c, "SELECT id,action,prev_revision,prev_state,event_meta,at FROM item_history "
                                                   "WHERE user_id=%s AND item_id=%s ORDER BY id", (uid, item_id))]
    return out


def item_decide(uid, item_id, expected_revision, proposal_id, action, history_id=None):
    """apply(제안 적용) · confirm/exclude/pending(기준만, 사용자 판단) · archive · restore(history_id 의 스냅샷을 새 변경으로).
    discard 는 제안(draft)만 버린다 — 새 항목 제안(revision 0)이면 행 삭제, 아니면 draft_* 만 비움. 이력·revision 변화 없음.
    모든 변경은 이력 한 줄 + revision+1. 같은 결정 반복은 no-op. confirm 은 승격 조건을 거절 사유로 쓰지 않고 결과에 사실로 싣는다."""
    if action not in ITEM_ACTIONS:
        raise CrmError("ARG-INVALID", f"action 은 {', '.join(ITEM_ACTIONS)} 중 하나예요.")
    promotion = None
    with tx() as c:
        _daily_gate(c, uid, "decisions")
        row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s FOR UPDATE", (uid, item_id))
        if not row:
            raise CrmError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
        if row["revision"] != expected_revision:
            raise _conflict(f"지금 revision {row['revision']}")
        od = _object_row(c, uid, row["object_id"], applied=False)
        kind = od["semantic_kind"]
        cur_links = _links_by_item(c, uid, [item_id]).get(item_id, [])
        snap = _item_state(row, cur_links)

        def bump(act, sets, args, meta=None):
            c.execute("INSERT INTO item_history (user_id,item_id,action,prev_revision,prev_state,event_meta) VALUES (%s,%s,%s,%s,%s,%s)",
                      (uid, item_id, act, row["revision"], Jsonb(snap), Jsonb(meta or {})))
            c.execute("UPDATE items SET " + sets + (", " if sets else "") + "revision=revision+1, updated_at=now() WHERE user_id=%s AND id=%s",
                      (*args, uid, item_id))

        def revalidate(data, links):
            if od["revision"] == 0 or od["archived_at"]:
                raise _bad_def("이 항목의 오브젝트 정의가 보관됐거나 적용 전이에요.")
            fields = _fields(c, uid, od["id"])
            try:
                nl = _norm_links(c, uid, fields, links)
            except CrmError as e:
                raise _bad_link(e.message + " — 연결 대상이 그사이 보관·삭제됐을 수 있어요.", e.action)
            dp, lp = _item_problems(fields, od["policy"], data, nl, strict=False)
            if dp:
                raise CrmError("ARG-INVALID", "지금 정의와 맞지 않아요 — " + " · ".join(dp), "새로 제안하세요(item_propose).")
            if lp:
                raise _bad_link("지금 정의와 맞지 않아요 — " + " · ".join(lp), "새로 제안하세요(item_propose).")
            return nl

        if action == "discard":
            if not proposal_id or row["draft_id"] != proposal_id:
                raise _conflict("제안이 없거나 바뀌었어요")
            if row["revision"] == 0:
                c.execute("DELETE FROM items WHERE user_id=%s AND id=%s", (uid, item_id))
                return {"id": item_id, "discarded": True, "deleted": True}
            c.execute("UPDATE items SET draft_doc=NULL, draft_id=NULL, draft_base_revision=NULL WHERE user_id=%s AND id=%s", (uid, item_id))
        elif action == "apply":
            if not row["draft_id"] or row["draft_id"] != proposal_id or row["draft_base_revision"] != row["revision"]:
                raise _conflict("제안이 없거나 바뀌었어요")
            dr = row["draft_doc"]
            nl = revalidate(dr["data"], dr["links"])
            code = row["code"] if row["code"] is not None else dr.get("code")
            if code and _code_taken(c, uid, row["object_id"], code, item_id):
                raise CrmError("ARG-INVALID", f"코드 {code} 는 이미 쓴 코드예요.")
            try:   # 위 검사와 이 UPDATE 사이에 다른 곳이 같은 코드를 쓰면 UNIQUE 가 막는다 — savepoint 로 잡아 CONFLICT 로
                with c.transaction():
                    bump("apply", "code=%s, title=%s, body=%s, data=%s, schema_revision=%s, draft_doc=NULL, draft_id=NULL, draft_base_revision=NULL",
                         (code, dr["title"], dr["body"], Jsonb(dr["data"]), od["revision"]),
                         {"proposal_id": proposal_id, "created": row["revision"] == 0})
            except pg_errors.UniqueViolation:
                raise CrmError("CONFLICT", f"같은 코드({code})가 방금 다른 곳에서 쓰였어요. 다른 코드로 다시 제안하세요.",
                                 "state_get 으로 지금 코드를 확인하고 item_propose 로 다시 제안하세요.")
            _replace_item_links(c, uid, item_id, row["object_id"], nl)
        elif action in ITEM_DECISIONS:
            if kind != "principle":
                raise CrmError("ARG-INVALID", "내 판단으로 남기기·남기지 않기·되돌리기는 기준(principle) 항목만 해요.",
                                 "다른 항목은 apply·archive·restore 만 있어요.")
            if row["revision"] == 0:
                raise CrmError("ARG-INVALID", "아직 적용하지 않은 제안이에요. 먼저 적용(apply)하세요.")
            target = ITEM_DECISIONS[action]
            if target != row["decision"]:
                bump(action, "decision=%s", (target,))
            if action == "confirm":
                promotion = _promotion(c, uid, od["policy"], cur_links)
        elif action == "archive":
            if row["revision"] == 0:
                raise CrmError("ARG-INVALID", "아직 적용하지 않은 제안이에요. 버리려면 discard 에 proposal_id 를 주세요.")
            if row["archived_at"] is None:
                bump("archive", "archived_at=now()", ())
        else:   # restore
            if history_id is None:
                raise CrmError("ARG-INVALID", "restore 는 history_id 가 필요해요.")
            h = _one(c, "SELECT id,prev_revision,prev_state FROM item_history WHERE user_id=%s AND item_id=%s AND id=%s",
                     (uid, item_id, int(history_id)))
            if not h:
                raise CrmError("ARG-INVALID", f"이 항목의 이력을 찾을 수 없어요: {history_id}")
            if h["prev_revision"] == 0:
                raise CrmError("ARG-INVALID", "처음 적용하기 전 상태로는 되돌릴 수 없어요. 보관(archive)하세요.")
            st = h["prev_state"]
            nl = revalidate(st.get("data") or {}, st.get("links") or [])
            dec = st.get("decision") if kind == "principle" else None
            if kind == "principle" and dec not in ("pending", "confirmed", "excluded"):
                dec = "pending"
            arch = "COALESCE(archived_at, now())" if st.get("archived") else "NULL"
            bump("restore", "title=%s, body=%s, data=%s, decision=%s, schema_revision=%s, archived_at=" + arch,
                 (st.get("title") or "", st.get("body") or "", Jsonb(st.get("data") or {}), dec, od["revision"]),
                 {"history_id": h["id"], "restored_revision": h["prev_revision"]})
            _replace_item_links(c, uid, item_id, row["object_id"], nl)
        row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s", (uid, item_id))
        out = _item_out(c, uid, row, _links_by_item(c, uid, [item_id]).get(item_id, []), od)
    if promotion is not None:
        out["promotion"] = promotion
    return out


# ── 문의 데스크 (desk-0) ────────────────────────────────
# 사용자가 운영자에게 보내는 글. 「본문 0」 원칙의 예외 — 운영자가 직접 읽는다(NOTICE 로 고지).
# 사용자는 「내 문의」에서 상태·공개 답변·공개 이력을 본다(ticket_get_mine). 운영자 메모(admin_note)·hint·내부 이력은 사용자 응답에 없다.
# 관리자(users.role='admin', β2)는 /admin/tickets 에서 문의를 처리한다(admin_db 경유). 문의 변경은 감사(admin_audit)가 아니라
# ticket_history 사건으로 남는다.

TICKET_KINDS = ("bug", "request", "question", "privacy")
TICKET_STATUSES = ("open", "ack", "doing", "done", "wontfix")
TICKET_PAGES = ("today", "question", "record", "structure", "map", "review", "start", "help", "privacy", "other")
TICKET_DAILY_USER = 10             # 사용자 하루(KST) 10건
TICKET_DAILY_ALL = 100             # 전역 하루(KST) 100건
TICKET_KEEP_DAYS = 180             # 종결 뒤 보관 일수
LIMIT_ADMIN_NOTE = 2 * KB          # 운영자 메모(바이트)
NOTICE = "이 글은 서버에 저장되고 운영자가 직접 읽어요. 기록 원문이나 다른 사람 정보는 넣지 마세요."
# 오분류된 개인정보 요청을 SQL 한 줄로 거르는 표식. kind 가 privacy 가 아닐 때만 단다.
_PRIVACY_HINT = re.compile(r"삭제|탈퇴|지워|열람|내 ?정보")
_KST_TODAY = "(date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul')"   # KST 오늘 0시(timestamptz)


def ticket_add(uid, kind, text, via, page="other", tool="", ua_family=""):
    """문의 1건. 한도는 같은 트랜잭션의 COUNT — 잠금 없음, 동시 요청이면 1~2건 초과를 허용한다(명시).
    kind=privacy 면 같은 트랜잭션에서 privacy_log(kind=other, via=form, received=KST 오늘) 1행 — 10일 시계는 여기서 시작한다.
    길이(PAYLOAD-LIMIT)는 호출 전에 write_gate 가 본다."""
    if kind not in TICKET_KINDS:
        raise CrmError("ARG-INVALID", f"문의 종류는 {', '.join(TICKET_KINDS)} 중 하나예요.")
    if via not in ("web", "mcp"):
        raise CrmError("ARG-INVALID", "via 는 web·mcp 중 하나예요.")
    text = text if isinstance(text, str) else ""
    if not text.strip():
        raise CrmError("ARG-INVALID", "문의 글이 비었어요.")
    page = page if page in TICKET_PAGES else "other"
    hint = "privacy?" if kind != "privacy" and _PRIVACY_HINT.search(text) else ""
    tid = new_id("tk")
    with tx() as c:
        _user_lock(c, uid)   # PR B — 삭제 CLI 와 직렬화(삭제 뒤 자료 생성 0). 잠금 순서 = users 행 → tickets(한도 COUNT 는 여전히 전역 잠금 없음)
        n_me = _one(c, f"SELECT count(*) AS n FROM tickets WHERE user_id=%s AND created >= {_KST_TODAY}", (uid,))["n"]
        if n_me >= TICKET_DAILY_USER:
            raise CrmError("RATE-LIMIT", f"오늘 문의는 {TICKET_DAILY_USER}건까지예요.", "내일 다시 보내 주세요.")
        n_all = _one(c, f"SELECT count(*) AS n FROM tickets WHERE created >= {_KST_TODAY}")["n"]
        if n_all >= TICKET_DAILY_ALL:
            raise CrmError("RATE-LIMIT", "오늘은 문의가 많아 더 받을 수 없어요.", "내일 다시 보내 주세요.")
        c.execute("INSERT INTO tickets (id,user_id,kind,text,via,page,tool,ua_family,hint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (tid, uid, kind, text, via, page, tool or "", ua_family or "", hint))
        if kind == "privacy":
            c.execute("INSERT INTO privacy_log (id,kind,via,ticket_id,received) "
                      "VALUES (%s,'other','form',%s,(now() AT TIME ZONE 'Asia/Seoul')::date)", (new_id("pl"), tid))
        return _iso(_one(c, "SELECT id,kind,status,created FROM tickets WHERE id=%s", (tid,)))


def tickets_mine(uid, limit=50):
    """「내 문의」 — 본인 글·상태·답변 유무·공개 갱신 시각. admin_note·hint·답변 본문은 넣지 않는다(본문은 ticket_get_mine).
    정렬 = 최근 공개 갱신순(공개 갱신이 없으면 접수 시각) — 웹 표와 MCP ticket_list_mine 이 같은 순서. 50건."""
    with tx() as c:
        return [_iso(r) for r in _all(c, "SELECT id,kind,text,status,via,created,updated,(reply IS NOT NULL) AS has_reply,reply_at,"
                                         "public_updated FROM tickets WHERE user_id=%s "
                                         "ORDER BY COALESCE(public_updated, created) DESC, created DESC, id LIMIT %s", (uid, limit))]


def ticket_get_mine(uid, tid):
    """본인 문의 하나 — 글·답변·공개 이력만. 다른 사람 문의·없는 id 는 NOT-FOUND(같은 모양). internal 사건·admin_note·hint 는 없다."""
    with tx() as c:
        t = _one(c, "SELECT id,kind,text,status,via,created,updated,reply,reply_at,public_updated FROM tickets WHERE id=%s AND user_id=%s",
                 (str(tid), uid))
        if not t:
            raise CrmError(*_TICKET_NF)
        hist = _all(c, "SELECT at,kind,from_val,to_val FROM ticket_history WHERE ticket_id=%s AND visibility='public' ORDER BY at, id",
                    (t["id"],))
    return {**_iso(t), "history": [_iso(h) for h in hist]}


def is_admin(uid) -> bool:
    """관리자 = users.role='admin' 이고 차단 아님(β2). PK 조회 1건."""
    if not uid:
        return False
    with tx() as c:
        return _one(c, "SELECT 1 AS x FROM users WHERE id=%s AND role='admin' AND blocked_at IS NULL", (uid,)) is not None


TICKET_RECLASS_REASONS = ("not_privacy", "duplicate", "other")   # privacy 에서 다른 kind 로 바꿀 때의 사유
PRIVACY_DAYS = 10                  # 개인정보 요청 처리 기한(받은 날부터, KST 날짜)
_TICKET_NF = ("NOT-FOUND", "그 문의를 찾지 못했어요.", "새로 고침한 뒤 다시 해 보세요.")


def _ticket_lock(c, actor_id, tid):
    """문의 처리 공통 — 실행자 재조회(관리자·차단 아님, 아니면 FORBIDDEN) → 문의 행 FOR UPDATE(없으면 NOT-FOUND)."""
    a = _one(c, "SELECT role,blocked_at FROM users WHERE id=%s", (str(actor_id or ""),))
    if not a or a["role"] != "admin" or a["blocked_at"] is not None:
        raise CrmError("FORBIDDEN", "관리자만 할 수 있어요.")
    t = _one(c, "SELECT id,kind,status,reply,created FROM tickets WHERE id=%s FOR UPDATE", (str(tid),))
    if not t:
        raise CrmError(*_TICKET_NF)
    return t


def _ticket_event(c, tid, actor_id, visibility, kind, from_val=None, to_val=None, reason=None):
    c.execute("INSERT INTO ticket_history (ticket_id,actor_id,visibility,kind,from_val,to_val,reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
              (tid, actor_id, visibility, kind, from_val, to_val, reason))


def ticket_set_status(actor_id, tid, status):
    """관리자 상태 변경 + 공개 사건(status, from→to) — 한 트랜잭션. 같은 상태면 무동작(사건 없음).
    종결(done·wontfix)이면 closed_at = 처음 종결한 때(종결끼리 옮겨도 유지), 다시 열면 NULL(파기 대상에서 빠진다). updated·public_updated 갱신.
    kind=privacy 면 같은 트랜잭션에서 privacy_log(ticket_id) — 종결: notified=KST 오늘(없을 때만)·result done→done/wontfix→refused,
    다시 열면 result 만 NULL(notified 유지). 티켓이 파기돼도 처리 사실은 privacy_log 에 남는다.
    반환 {id, changed} — 바뀐 상세는 admin_db.ticket_detail 이 커밋 뒤 읽는다(운영 경계)."""
    if status not in TICKET_STATUSES:
        raise CrmError("ARG-INVALID", f"상태는 {', '.join(TICKET_STATUSES)} 중 하나예요.")
    with tx() as c:
        t = _ticket_lock(c, actor_id, tid)
        if t["status"] == status:
            return {"id": t["id"], "changed": False}
        c.execute("UPDATE tickets SET status=%s, updated=now(), public_updated=now(), closed_at = CASE WHEN %s IN ('done','wontfix') "
                  "THEN COALESCE(closed_at, now()) ELSE NULL END WHERE id=%s", (status, status, t["id"]))
        if t["kind"] == "privacy":
            if status in ("done", "wontfix"):
                c.execute("UPDATE privacy_log SET notified = COALESCE(notified, (now() AT TIME ZONE 'Asia/Seoul')::date), result=%s "
                          "WHERE ticket_id=%s", ("done" if status == "done" else "refused", t["id"]))
            else:
                c.execute("UPDATE privacy_log SET result=NULL WHERE ticket_id=%s", (t["id"],))
        _ticket_event(c, t["id"], actor_id, "public", "status", t["status"], status)
        return {"id": t["id"], "changed": True}


def ticket_set_reply(actor_id, tid, reply):
    """공개 답변 쓰기·고치기·지우기(빈 문자열 = 지움) + 공개 사건(reply, 값 없음) — 한 트랜잭션. reply·reply_at·public_updated·updated.
    4KB(바이트) 상한. 같은 값이면 무동작(사건 없음)."""
    if not isinstance(reply, str):
        raise CrmError("ARG-INVALID", "답변은 문자열이에요.")
    check_len("reply", reply, LIMIT_TICKET)
    new = reply if reply.strip() else None
    with tx() as c:
        t = _ticket_lock(c, actor_id, tid)
        if t["reply"] == new:
            return {"id": t["id"], "changed": False}
        c.execute("UPDATE tickets SET reply=%s, reply_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END, "
                  "public_updated=now(), updated=now() WHERE id=%s", (new, new, t["id"]))
        _ticket_event(c, t["id"], actor_id, "public", "reply")
        return {"id": t["id"], "changed": True}


def ticket_set_note(actor_id, tid, note):
    """관리자 메모(사용자에게 안 보인다) + 내부 사건(note, 값 없음) — 한 트랜잭션. 2KB(바이트) 상한. updated 갱신, public_updated 는 그대로.
    같은 값이면 무동작. 반환 {id, changed} — 상세는 admin_db.ticket_detail."""
    if not isinstance(note, str):
        raise CrmError("ARG-INVALID", "메모는 문자열이에요.")
    check_len("admin_note", note, LIMIT_ADMIN_NOTE)
    with tx() as c:
        t = _ticket_lock(c, actor_id, tid)
        n = c.execute("UPDATE tickets SET admin_note=%s, updated=now() WHERE id=%s AND admin_note IS DISTINCT FROM %s",
                      (note, t["id"], note)).rowcount
        if n:
            _ticket_event(c, t["id"], actor_id, "internal", "note")
        return {"id": t["id"], "changed": bool(n)}


def ticket_reclassify(actor_id, tid, kind, reason=None):
    """문의 종류(kind) 바꾸기 + 내부 사건(reclassify, from→to, reason) — 한 트랜잭션. 종결된 문의도 된다. 같은 kind 면 무동작.
    → privacy: 이 문의의 privacy_log 행을 재사용(있으면 그대로), 없으면 만든다(received = tickets.created 의 KST 날짜 — 10일 시계 기산점).
    privacy →: 사유(TICKET_RECLASS_REASONS) 필수. privacy_log 는 남기고 result 는 건드리지 않는다(활성 요청 목록은 kind 로 거른다).
    그 밖의 바꿈엔 사유를 받지 않는다(빈 값만)."""
    if kind not in TICKET_KINDS:
        raise CrmError("ARG-INVALID", f"문의 종류는 {', '.join(TICKET_KINDS)} 중 하나예요.")
    reason = reason or None
    with tx() as c:
        t = _ticket_lock(c, actor_id, tid)
        if t["kind"] == kind:
            return {"id": t["id"], "changed": False}
        if t["kind"] == "privacy":
            if reason not in TICKET_RECLASS_REASONS:
                raise CrmError("ARG-INVALID", f"개인정보 요청에서 빼는 사유는 {', '.join(TICKET_RECLASS_REASONS)} 중 하나예요.")
        elif reason is not None:
            raise CrmError("ARG-INVALID", "사유는 개인정보 요청에서 뺄 때만 받아요.")
        c.execute("UPDATE tickets SET kind=%s, updated=now() WHERE id=%s", (kind, t["id"]))
        if kind == "privacy" and not _one(c, "SELECT 1 AS x FROM privacy_log WHERE ticket_id=%s", (t["id"],)):
            c.execute("INSERT INTO privacy_log (id,kind,via,ticket_id,received) "
                      "VALUES (%s,'other','form',%s,(%s::timestamptz AT TIME ZONE 'Asia/Seoul')::date)", (new_id("pl"), t["id"], t["created"]))
        _ticket_event(c, t["id"], actor_id, "internal", "reclassify", t["kind"], kind, reason)
        return {"id": t["id"], "changed": True}


def tickets_purge():
    """종결(closed_at 있음) 180일 지난 문의를 지운다. 서버 시작 때 1회(main.lifespan) — 배치 잡 없음.
    다시 연 문의(closed_at NULL)는 남는다. privacy_log 는 FK 가 없어 그대로. 지운 건수를 돌려준다.
    PR B: 삭제 통지·contact 가 가리키는 문의(deletion_contacts.ticket_id)는 빼고 — 그 문의는 contacts_purge 가 purge_after 뒤에 지운다."""
    with tx() as c:
        c.execute("SET LOCAL statement_timeout = '5s'")
        return c.execute("DELETE FROM tickets AS t WHERE t.closed_at < now() - make_interval(days => %s) "
                         "AND NOT EXISTS (SELECT 1 FROM deletion_contacts d WHERE d.ticket_id = t.id)", (TICKET_KEEP_DAYS,)).rowcount


# ── 관리자 · 세션 끊기 · 차단 (β2) ──────────────────────
# 진실 = users.role. 첫 지정은 서버 시작 때 한 번(admin_bootstrap, env CRM_ADMIN_USER_IDS), 잠기면 admin_cli.py grant(admin_recover).
# 관리자 변경은 전부 한 트랜잭션: pg_advisory_xact_lock(ADMIN_LOCK) → 실행자 재조회 → 대상 FOR UPDATE → 규칙 → 변경 → 감사 INSERT.
# 감사(admin_audit)는 FK 없음 · detail 은 열거 키만 · 1년 뒤 파기(bootstrap·recover 행은 남긴다 — 지우면 다음 시작 때 env 를 다시 읽는다).

ADMIN_LOCK = 0x63616E6F6E0B32       # 관리자 변경·bootstrap·복구를 직렬화하는 advisory lock 키(상수)
AUDIT_KEEP_DAYS = 365
AUDIT_LIST = 100                    # /admin/setup 감사 로그 최근 건수
ADMIN_ACTIONS = ("bootstrap", "recover", "role_grant", "role_revoke", "web_signout", "block", "unblock",
                 "delete_notify", "delete_run", "delete_cancel",   # 삭제 CLI 3종(PR B) — detail 은 ticket_id·result 만
                 "device_connect", "device_reissue", "device_revoke", "device_policy",   # board 이전: 장치 토큰(actor = 사용자 본인)
                 "migration_apply", "migration_extend", "migration_close")              # board 이전 CLI — detail 은 열거 키·건수·해시 앞자리만
_iat_missing_logged = False


def _audit(c, action, actor_id, target_id, detail=None):
    c.execute("INSERT INTO admin_audit (action,actor_id,target_id,detail) VALUES (%s,%s,%s,%s)",
              (action, actor_id, target_id, Jsonb(detail or {})))


def session_user(uid):
    """웹 세션 검사용 users 행(PK 1건) — {id, role, blocked_at, web_revoked_before, status}, datetime 그대로. 없으면 None."""
    with tx() as c:
        return _one(c, "SELECT id,role,blocked_at,web_revoked_before,status FROM users WHERE id=%s", (uid,))


def check_token_user(row, iat):
    """MCP·웹 로그인 토큰의 사용자 검사(auth.user_id_for 한 곳). row 는 user_by_subject 결과(_iso 문자열).
    차단이면 AUTH-BLOCKED. mcp_revoked_before 가 있고 토큰 iat 가 그보다 앞이면 AUTH-REVOKED.
    토큰에 iat 가 없으면 revoked 검사는 건너뛰고 로그 1줄(프로세스 최초 1회) — 차단은 그대로 동작."""
    global _iat_missing_logged
    if row.get("status") == "deleted":   # PR B 방어 — 삭제된 계정 행은 조회 갈래에서 이미 거른다
        raise CrmError(*_REVOKED)
    if row.get("blocked_at"):
        raise CrmError("AUTH-BLOCKED", "이 계정은 지금 쓸 수 없어요.", "운영자에게 문의해 주세요.")
    rb = row.get("mcp_revoked_before")
    if not rb:
        return
    if iat is None:
        if not _iat_missing_logged:
            _iat_missing_logged = True
            print("[auth] 토큰에 iat 가 없어 mcp_revoked_before 검사를 건너뛰었어요(차단 검사는 그대로)", flush=True)
        return
    rb = rb if isinstance(rb, _dt.datetime) else _dt.datetime.fromisoformat(str(rb))
    if not isinstance(iat, (int, float)) or isinstance(iat, bool) or iat < rb.timestamp():
        raise CrmError("AUTH-REVOKED", "이 연결은 끊겼어요.", "커넥터를 다시 연결해 주세요.")


def admin_bootstrap(ids, conn=None):
    """첫 관리자 지정 — 감사에 bootstrap·recover 행이 하나도 없을 때만. ids 중 users 에 있는 것을 admin 으로 + bootstrap 행 1개
    (승격 0명이어도 남긴다 = 초기화 완료 표시). 이미 초기화됐으면 None, 아니면 승격 수. 동시 실행은 ADMIN_LOCK 으로 직렬화.
    conn 을 주면 그 연결에서(시험: 임시 스키마)."""
    ids = sorted({str(x).strip() for x in (ids or ()) if str(x).strip()})

    def run(c):
        with c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(%s)", (ADMIN_LOCK,))
            if _one(c, "SELECT 1 AS x FROM admin_audit WHERE action IN ('bootstrap','recover') LIMIT 1"):
                return None
            n = c.execute("UPDATE users SET role='admin' WHERE id = ANY(%s) AND role <> 'admin'", (ids,)).rowcount
            _audit(c, "bootstrap", None, None, {"listed": len(ids), "promoted": n})
            return n
    if conn is not None:
        return run(conn)
    with pool().connection() as c:
        return run(c)


def admin_recover(email):
    """복구(admin_cli.py grant) — 이메일(소문자) 사용자를 admin 으로, 차단 해제, 감사 recover. 웹 서버 없이 부른다."""
    email = (email or "").strip().lower()
    with tx() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (ADMIN_LOCK,))
        t = _one(c, "SELECT id,role,blocked_at FROM users WHERE email=%s FOR UPDATE", (email,))
        if not t:
            raise CrmError("NOT-FOUND", "그 이메일의 계정이 없어요.", "그 사람이 먼저 한 번 로그인해야 계정이 생겨요.")
        c.execute("UPDATE users SET role='admin', blocked_at=NULL WHERE id=%s", (t["id"],))
        _audit(c, "recover", None, t["id"], {"from": t["role"], "to": "admin", "unblocked": t["blocked_at"] is not None})
        return {"id": t["id"], "email": email}


def admins():
    """role=admin 목록(차단 여부 포함). admin_cli.py list."""
    with tx() as c:
        return [_iso(r) for r in _all(c, "SELECT id,email,created,blocked_at FROM users WHERE role='admin' ORDER BY created")]


def admin_act(actor_id, op, target_id, role=None):
    """/admin POST 한 건 — 한 트랜잭션. op: role(role=admin|user)·signout·block·unblock.
    규칙: 부여 = 이미 admin 이면 무동작 · 회수 = role=admin 이 1명뿐이면 LAST-ADMIN, 회수하면 대상 웹 세션도 끊는다 ·
    끊기 = web_revoked_before · 차단 = blocked_at·web·mcp revoked_before 모두 now()(마지막 관리자도 허용 — 복구는 CLI) ·
    해제 = blocked_at 만 NULL(revoked_before 는 그대로 — 차단 전 쿠키·토큰은 살아나지 않는다).
    회수·해제는 대상이 이미 그 상태면 무동작(감사 없음). 반환 {"changed": bool}."""
    if op not in ("role", "signout", "block", "unblock") or (op == "role" and role not in ("admin", "user")):
        raise CrmError("ARG-INVALID", "동작이 틀렸어요.")
    with tx() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (ADMIN_LOCK,))
        a = _one(c, "SELECT role,blocked_at FROM users WHERE id=%s", (actor_id,))
        if not a or a["role"] != "admin" or a["blocked_at"] is not None:
            raise CrmError("FORBIDDEN", "관리자만 할 수 있어요.")
        t = _one(c, "SELECT id,role,blocked_at FROM users WHERE id=%s FOR UPDATE", (str(target_id),))
        if not t:
            raise CrmError("NOT-FOUND", "그 계정을 찾지 못했어요.", "새로 고침한 뒤 다시 해 보세요.")
        tid = t["id"]
        if op == "role" and role == "admin":
            if t["role"] == "admin":
                return {"changed": False}
            c.execute("UPDATE users SET role='admin' WHERE id=%s", (tid,))
            _audit(c, "role_grant", actor_id, tid, {"from": t["role"], "to": "admin"})
        elif op == "role":
            if t["role"] != "admin":
                return {"changed": False}
            if _one(c, "SELECT count(*) AS n FROM users WHERE role='admin'")["n"] <= 1:
                raise CrmError("LAST-ADMIN", "마지막 관리자는 회수할 수 없어요.", "다른 사람에게 먼저 관리자를 부여하세요.")
            c.execute("UPDATE users SET role='user', web_revoked_before=now() WHERE id=%s", (tid,))
            _audit(c, "role_revoke", actor_id, tid, {"from": "admin", "to": "user"})
        elif op == "signout":
            c.execute("UPDATE users SET web_revoked_before=now() WHERE id=%s", (tid,))
            _audit(c, "web_signout", actor_id, tid)
        elif op == "block":
            c.execute("UPDATE users SET blocked_at=now(), web_revoked_before=now(), mcp_revoked_before=now() WHERE id=%s", (tid,))
            _audit(c, "block", actor_id, tid)
        else:
            if t["blocked_at"] is None:
                return {"changed": False}
            c.execute("UPDATE users SET blocked_at=NULL WHERE id=%s", (tid,))
            _audit(c, "unblock", actor_id, tid)
        return {"changed": True}


def admin_audit_purge():
    """1년(AUDIT_KEEP_DAYS) 지난 감사 행을 지운다. 서버 시작 때 1회(main.lifespan, tickets_purge 와 같은 자리).
    bootstrap·recover 행은 남긴다 — 초기화 완료 표시라 지우면 다음 시작 때 env 로 다시 승격한다. 지운 건수."""
    with tx() as c:
        c.execute("SET LOCAL statement_timeout = '5s'")
        return c.execute("DELETE FROM admin_audit WHERE at < now() - make_interval(days => %s) "
                         "AND action NOT IN ('bootstrap','recover')", (AUDIT_KEEP_DAYS,)).rowcount


# ── 계정 삭제 CLI (PR B 결정 4·5·8·10) — admin_cli.py delete-notify·preview·run·cancel 이 부른다 ──
# 대상 = 「개인정보 요청」 문의(kind=privacy)의 소유자. 이메일로 대상을 고르지 않는다. 실행자는 활성 관리자, 대상은 관리자가 아니어야 한다.
# 통지(notify) → 유예 7일 → 미리보기(preview, 읽기 전용) → 실행(run, 한 트랜잭션: 잠금·재조회·자료 삭제·contact 이동·계정 닫기·이력·감사).
# 남기는 것: users 행(자리값으로 갱신) · deletion_contacts 행 · 그 삭제 요청 문의 1건과 ticket_history · admin_audit · privacy_log
# (실행이 그 요청의 privacy_log 행에 처리일·result=done 을 채운다 — 다른 행은 건드리지 않는다).
# users.email 자리값 `deleted:<uid>` — email 열엔 형식 CHECK 가 없고(UNIQUE 만) uid 가 유일해 겹치지 않는다. '@' 가 없어 인증 서버가
# 검증된 이메일로 내줄 수 없는 값이라 로그인 경로(user_by_subject 의 이메일 조회)로는 이 행을 찾을 수 없다.
# 감사 detail 은 ticket_id·result 만. 반환값에 이메일·표시 이름·본문·별칭·출처 값·답변이 없다(건수·시각·코드만).

DELETE_GRACE_DAYS = 7
CONTACT_KEEP_DAYS = 365
DELETE_NOTICE = ("7일 뒤 자료를 모두 지우고 계정을 닫습니다. 이메일과 이 삭제 요청 문의·처리 이력은 처리 증빙과 분쟁 대응을 위해 "
                 "삭제 실행일부터 1년 보관한 뒤 파기합니다. 삭제 결과는 이 문의 이력에 남습니다. "
                 "그 전에 취소하려면 이 문의에 「취소」라고 남겨 주세요.")
# 지울 테이블 = schema.sql 에서 user_id 열을 가진 테이블 − deletion_contacts. 순서 = 보호 Lookup(NO ACTION·RESTRICT) 자식부터.
# tickets 는 보존 문의 1건을 빼고 지운다(ticket_history 는 cascade). 시험이 「이 목록 = schema 의 user_id 테이블 − 예외」를 정적으로 본다.
# board 이전(2026-10-20): 관계(activity_links) → 접수 대응표(import_entries) → 업무 자료(activities) → 한도·입력원(import_counters·import_streams).
DELETE_TABLES = ("page_layouts", "item_links", "activity_links", "import_entries", "activities", "import_counters", "import_streams",
                 "item_history", "items", "user_roles", "field_defs", "object_defs", "std_choices", "std_marks",
                 "candidate_history", "candidates", "source_reflections", "records", "sources", "questions", "profile", "tabs",
                 "people", "agreements", "optional_consent_log", "optional_consents", "usage_daily", "tickets")
DELETE_KEEP_TABLES = ("deletion_contacts",)


def _delete_fault(step):
    """시험 전용 실패 주입 자리 — 실행 단계(③~⑦) 사이마다 부른다. 운영에선 아무것도 안 한다."""


def _del_err(code, msg, act=""):
    return CrmError(code, msg, act)


def _del_actor(c, actor_id):
    a = _one(c, "SELECT role,blocked_at,status FROM users WHERE id=%s", (str(actor_id or ""),))
    if not a or a["role"] != "admin" or a["blocked_at"] is not None or a["status"] != "active":
        raise CrmError("FORBIDDEN", "활성 관리자만 할 수 있어요.", "--actor 에 관리자 user id 를 넣으세요.")


def _del_ticket(c, tid, lock=False):
    """문의 → (문의 행, 소유자 행). 공통 거절: 없음 · kind≠privacy · 소유자가 관리자 · 소유자 삭제됨. lock 이면 소유자 → 문의 순으로 잠근다."""
    t = _one(c, "SELECT id,user_id,kind,status,created FROM tickets WHERE id=%s", (str(tid or ""),))
    if not t:
        raise CrmError(*_TICKET_NF)
    # lock: 소유자 users 행 → 문의 행. 사용자 쓰기(_user_lock)와 같은 잠금(FOR NO KEY UPDATE)·순서 — 상태 필터 없이 잠그고 아래에서 거절한다
    u = _one(c, "SELECT id,role,status,email,auth_subject FROM users WHERE id=%s" + (" FOR NO KEY UPDATE" if lock else ""), (t["user_id"],))
    if lock:
        t = _one(c, "SELECT id,user_id,kind,status,created FROM tickets WHERE id=%s FOR UPDATE", (t["id"],))
    if t["kind"] != "privacy":
        raise _del_err("DELETE-NOT-PRIVACY", "개인정보 요청 문의가 아니에요.", "삭제 요청은 kind=privacy 문의로만 처리해요.")
    if u["role"] == "admin":
        raise _del_err("DELETE-ADMIN-TARGET", "관리자 계정은 지울 수 없어요.", "먼저 관리자 권한을 회수하세요.")
    if u["status"] == "deleted":
        raise _del_err("DELETE-ALREADY", "이미 삭제된 계정이에요.")
    return t, u


def _del_counts(c, uid, keep_tid):
    out = {}
    for t in DELETE_TABLES:
        extra = " AND id <> %s" if t == "tickets" else ""
        out[t] = _one(c, f"SELECT count(*) AS n FROM {t} WHERE user_id=%s{extra}", (uid, keep_tid) if extra else (uid,))["n"]
    return out


def _del_value(uid, t, contact, counts):
    """미리보기 값 — 대상 자료 건수 · contact 행 · 문의 상태로 만든 짧은 해시. 실행이 잠근 뒤 다시 만들어 비교한다."""
    raw = json.dumps({"uid": uid, "ticket": [t["id"], t["status"]], "counts": counts,
                      "contact": [contact["ticket_id"], contact["notified_at"].isoformat(), contact["delete_after"].isoformat(),
                                  contact["deleted_at"].isoformat() if contact["deleted_at"] else None] if contact else None},
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _contact_row(c, uid, lock=False):
    return _one(c, "SELECT user_id,ticket_id,notified_at,delete_after,deleted_at FROM deletion_contacts WHERE user_id=%s"
                + (" FOR UPDATE" if lock else ""), (uid,))


def delete_notify(actor_id, tid):
    """사전 통지 — 한 트랜잭션: 고정 통지문을 문의 공개 답변에(상태 → doing) + 공개 이력 + deletion_contacts 행 + 감사 delete_notify.
    이미 통지 행이 있으면 거절. 반환 {ticket_id, notified_at, delete_after, within_10_days}."""
    with tx() as c:
        _del_actor(c, actor_id)
        t, u = _del_ticket(c, tid, lock=True)
        if _contact_row(c, u["id"], lock=True):
            raise _del_err("DELETE-NOTIFIED", "이미 통지했어요.", "delete-preview 로 상태를 보세요. 취소는 delete-cancel.")
        c.execute("UPDATE tickets SET reply=%s, reply_at=now(), public_updated=now(), updated=now(), status='doing', closed_at=NULL "
                  "WHERE id=%s", (DELETE_NOTICE, t["id"]))
        if t["status"] != "doing":
            _ticket_event(c, t["id"], actor_id, "public", "status", t["status"], "doing")
        _ticket_event(c, t["id"], actor_id, "public", "reply")
        r = _one(c, "INSERT INTO deletion_contacts (user_id,ticket_id,requested_at,notified_at,delete_after) "
                    "VALUES (%s,%s,%s,now(),now() + make_interval(days => %s)) RETURNING notified_at,delete_after, "
                    "(now() AT TIME ZONE 'Asia/Seoul')::date <= (%s::timestamptz AT TIME ZONE 'Asia/Seoul')::date + %s AS ok10",
                 (u["id"], t["id"], t["created"], DELETE_GRACE_DAYS, t["created"], PRIVACY_DAYS))
        _audit(c, "delete_notify", actor_id, u["id"], {"ticket_id": t["id"], "result": "ok"})
    return {"ticket_id": t["id"], "notified_at": r["notified_at"], "delete_after": r["delete_after"], "within_10_days": r["ok10"]}


def delete_preview(actor_id, tid):
    """미리보기 — 읽기 전용(READ ONLY). 지울 테이블별 건수 · 보존(문의 1 · 이력 n · contact) · 실행 가능 여부와 사유 코드 · 미리보기 값."""
    with tx() as c:
        c.execute("SET TRANSACTION READ ONLY")
        _del_actor(c, actor_id)
        t, u = _del_ticket(c, tid)
        contact = _contact_row(c, u["id"])
        counts = _del_counts(c, u["id"], t["id"])
        hist = _one(c, "SELECT count(*) AS n FROM ticket_history WHERE ticket_id=%s", (t["id"],))["n"]
        reasons = []
        if not contact:
            reasons.append("no_notice")
        elif contact["ticket_id"] != t["id"]:
            reasons.append("other_ticket")
        elif _one(c, "SELECT now() < %s AS early", (contact["delete_after"],))["early"]:
            reasons.append("grace")
        value = _del_value(u["id"], t, contact, counts)
    return {"ticket_id": t["id"], "counts": counts, "total": sum(counts.values()),
            "keep": {"tickets": 1, "ticket_history": hist, "deletion_contacts": 1 if contact else 0},
            "notified_at": contact["notified_at"] if contact else None, "delete_after": contact["delete_after"] if contact else None,
            "ready": not reasons, "reasons": reasons, "value": value}


def delete_run(actor_id, tid, value):
    """실행 — 한 트랜잭션. ① 소유자 행 잠금(사용자 쓰기와 같은 순서) + contact FOR UPDATE ② 재조회(공통 거절 · 통지 있음 · 유예 끝남 ·
    미리보기 값 일치) ③ 자료 삭제(DELETE_TABLES, 보존 문의 제외) ④ contact 로 이메일·auth_subject 이동, deleted_at·purge_after
    ⑤ users 자리값·상태 deleted·끊기 ⑥ 보존 문의 done + 공개 이력(status·deleted) ⑦ 감사 delete_run ok.
    실패하면 전체 롤백 뒤 별도 트랜잭션으로 감사 delete_run(result = 오류 코드) — 실행자가 활성 관리자일 때만. 반환 {ticket_id, deleted, deleted_at}."""
    state = {"actor_ok": False, "target": None}
    try:
        with tx() as c:
            _del_actor(c, actor_id)
            state["actor_ok"] = True
            t, u = _del_ticket(c, tid, lock=True)
            uid = state["target"] = u["id"]
            contact = _contact_row(c, uid, lock=True)
            if not contact or contact["ticket_id"] != t["id"]:
                raise _del_err("DELETE-NO-NOTICE", "이 문의로 통지한 기록이 없어요.", "delete-notify 로 먼저 통지하세요.")
            if contact["deleted_at"] is not None:
                raise _del_err("DELETE-ALREADY", "이미 삭제된 계정이에요.")
            if _one(c, "SELECT now() < %s AS early", (contact["delete_after"],))["early"]:
                raise _del_err("DELETE-GRACE", "유예 기간(통지 뒤 7일)이 끝나지 않았어요.", "delete_after 뒤에 다시 하세요.")
            counts = _del_counts(c, uid, t["id"])
            if str(value or "") != _del_value(uid, t, contact, counts):
                raise _del_err("DELETE-STALE", "미리보기 뒤에 자료가 바뀌었어요.", "delete-preview 를 다시 보고 새 값으로 실행하세요.")
            _delete_fault(3)
            for tb in DELETE_TABLES:   # ③
                if tb == "tickets":
                    c.execute("DELETE FROM tickets WHERE user_id=%s AND id <> %s", (uid, t["id"]))
                else:
                    c.execute(f"DELETE FROM {tb} WHERE user_id=%s", (uid,))
            _delete_fault(4)
            at = _one(c, "UPDATE deletion_contacts SET email=%s, auth_subject=%s, deleted_at=now(), "   # ④
                         "purge_after = now() + make_interval(days => %s) WHERE user_id=%s RETURNING deleted_at",
                      (u["email"], u["auth_subject"], CONTACT_KEEP_DAYS, uid))["deleted_at"]
            _delete_fault(5)
            c.execute("UPDATE users SET email=%s, auth_subject=NULL, display_name=NULL, nav_accepted_at=NULL, status='deleted', "   # ⑤
                      "deleted_at=now(), web_revoked_before=now(), mcp_revoked_before=now(), desk_hidden=false WHERE id=%s",
                      (f"deleted:{uid}", uid))
            _delete_fault(6)
            c.execute("UPDATE tickets SET status='done', closed_at=COALESCE(closed_at, now()), updated=now(), public_updated=now() "   # ⑥
                      "WHERE id=%s", (t["id"],))
            if t["status"] != "done":
                _ticket_event(c, t["id"], actor_id, "public", "status", t["status"], "done")
            _ticket_event(c, t["id"], actor_id, "public", "deleted")
            # 그 삭제 요청의 처리 사실(privacy_log, 식별정보 없음) — 종결(ticket_set_status done)과 같은 값: 처리일·result done
            c.execute("UPDATE privacy_log SET notified = COALESCE(notified, (now() AT TIME ZONE 'Asia/Seoul')::date), result='done' "
                      "WHERE ticket_id=%s", (t["id"],))
            _delete_fault(7)
            _audit(c, "delete_run", actor_id, uid, {"ticket_id": t["id"], "result": "ok"})   # ⑦
    except Exception as e:
        if state["actor_ok"]:
            code = e.code if isinstance(e, CrmError) else "EXC"
            with tx() as c:
                _audit(c, "delete_run", actor_id, state["target"], {"ticket_id": str(tid or ""), "result": code})
        raise
    with _usage_lock:   # 이 프로세스의 메모리 카운터(서버 프로세스는 usage_flush 가 deleted uid 를 버린다)
        for k in [k for k in _usage if k[1] == uid]:
            del _usage[k]
    return {"ticket_id": t["id"], "deleted": sum(counts.values()), "deleted_at": at}


def delete_cancel(actor_id, tid):
    """취소 — 유예 중(deleted_at NULL) contact 행 삭제 + 감사 delete_cancel. 사용자에게 알리는 답변은 운영자가 /admin 문의 화면에서 쓴다."""
    with tx() as c:
        _del_actor(c, actor_id)
        t, u = _del_ticket(c, tid, lock=True)
        contact = _contact_row(c, u["id"], lock=True)
        if not contact or contact["ticket_id"] != t["id"] or contact["deleted_at"] is not None:
            raise _del_err("DELETE-NO-NOTICE", "취소할 통지가 없어요.")
        c.execute("DELETE FROM deletion_contacts WHERE user_id=%s", (u["id"],))
        _audit(c, "delete_cancel", actor_id, u["id"], {"ticket_id": t["id"], "result": "ok"})
    return {"ticket_id": t["id"], "cancelled": True}


def contacts_purge():
    """purge_after 지난 contact — 그 문의(ticket_history cascade) 삭제 → contact 행 삭제, 한 트랜잭션. 유예 중 행(deleted_at NULL)은 대상 아님.
    main.purge_once 가 부른다. 지운 contact 수."""
    with tx() as c:
        c.execute("SET LOCAL statement_timeout = '5s'")
        rows = _all(c, "SELECT user_id,ticket_id FROM deletion_contacts WHERE purge_after IS NOT NULL AND purge_after < now() FOR UPDATE")
        if not rows:
            return 0
        c.execute("DELETE FROM tickets WHERE id = ANY(%s)", ([r["ticket_id"] for r in rows],))
        return c.execute("DELETE FROM deletion_contacts WHERE user_id = ANY(%s)", ([r["user_id"] for r in rows],)).rowcount


# ── PR B 코어: 나(users 행) · 사람 · 출처 · 출처 요약/반응 ─────────────
# 설계: notes PR-B 구현계획 §3~§5. 모든 조회·쓰기는 user_id 로 묶고, 남의 id 와 없는 id 는 같은 NOT-FOUND 다.
# 수정·삭제는 expected_revision 필수(다르면 CONFLICT), 생성은 받지 않는다. 바뀔 때마다 revision +1(같은 값이면 무동작).
# 출처 요약/반응은 records 와 다른 테이블이라 인용 적격 경로(후보 근거·관계 대상·record_attach)에 들어가지 않는다 — 그 경로는 records 만 찾는다.

LABEL_MAX = 80                     # 표시 이름·별칭 글자 수(schema CHECK 와 같다)
REFLECTION_MAX = 8000              # 요약/반응 본문 글자 수(schema CHECK 와 같다)
REFLECTION_KINDS = ("summary", "reaction")
REFLECTION_REVIEWS = ("pending", "reviewed")
PAGE_DEFAULT, PAGE_MAX = 50, 100
_PERSON_COLS = "id,alias,revision,created,updated"
_REFL_COLS = "id,source_id,kind,body,review,revision,reviewed_revision,reviewed_at,created,updated"


def _label(v, what):
    """표시 이름·별칭 — 앞뒤 공백 제거 뒤 1~80자, 줄바꿈·제어문자 거절. 자동으로 채우지 않는다."""
    if not isinstance(v, str):
        raise CrmError("ARG-INVALID", f"{what}은 문자열이에요.")
    v = v.strip()
    if not v:
        raise CrmError("ARG-INVALID", f"{what}이 비었어요.")
    if len(v) > LABEL_MAX:
        raise CrmError("ARG-INVALID", f"{what}은 {LABEL_MAX}자까지예요.")
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in v):
        raise CrmError("ARG-INVALID", f"{what}에 줄바꿈·제어문자는 넣을 수 없어요.")
    return v


def _rev_arg(v, required=True):
    if v is None and not required:
        return None
    if not isinstance(v, int) or isinstance(v, bool):
        raise CrmError("ARG-INVALID", "expected_revision 은 정수예요.")
    return v


def _nf(what):
    return CrmError("NOT-FOUND", f"{what}을 찾지 못했어요.", "목록을 다시 보고 id 를 확인하세요.")


def _page_args(cursor, limit):
    """목록 limit(기본 50·최대 100) · cursor(불투명 문자열 — (created, id) 를 담는다, 빈 값 = 처음)."""
    if limit is None:
        limit = PAGE_DEFAULT
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= PAGE_MAX:
        raise CrmError("ARG-INVALID", f"limit 은 1~{PAGE_MAX} 정수예요.")
    if not cursor:
        return None, limit
    try:
        at, cid = json.loads(base64.urlsafe_b64decode(str(cursor).encode()).decode())
        return (_dt.datetime.fromisoformat(at), str(cid)), limit
    except Exception:
        raise CrmError("ARG-INVALID", "cursor 가 맞지 않아요.", "처음부터(cursor 빈 값) 다시 보세요.")


def _page(rows, limit):
    """limit+1 개를 읽어 다음 cursor 를 만든다. 반환 (행, next_cursor 또는 "")."""
    if len(rows) <= limit:
        return rows, ""
    rows = rows[:limit]
    last = rows[-1]
    return rows, base64.urlsafe_b64encode(json.dumps([last["created"].isoformat(), last["id"]]).encode()).decode()


def _like(q):
    return "%" + str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ── 나 ──

def display_name_set(uid, display_name):
    """표시 이름 저장(「나」 = users.display_name 한 곳). 빈 값으로 바꾸기 거절. 관문 면제(NAME_EXEMPT). 반환 {display_name}."""
    name = _label(display_name, "표시 이름")
    with tx() as c:
        _user_lock(c, uid)
        c.execute("UPDATE users SET display_name=%s WHERE id=%s", (name, uid))
    return {"display_name": name}


def nav_accept(uid):
    """새 탐색 수락(계정별 1회) — nav_accepted_at = now(), 이미 있으면 그대로(멱등). 표준 편입 상태는 읽지도 바꾸지도 않는다."""
    with tx() as c:
        _user_lock(c, uid)
        r = _one(c, "UPDATE users SET nav_accepted_at = COALESCE(nav_accepted_at, now()) WHERE id=%s RETURNING nav_accepted_at", (uid,))
    return {"nav": "new", "accepted_at": r["nav_accepted_at"].isoformat()}


def me_home(uid):
    """「나」 홈 데이터 — 표시 이름 · 새 탐색 수락 여부 · 개수. 기존 조회로 센다(따로 저장하지 않는다).
    판단 = 확정 후보(지워지지 않은 기록) · 기준 = principle 오브젝트의 적용·미보관·확정 항목 · 결정 = event 오브젝트의 적용·미보관 항목."""
    with tx() as c:
        u = _one(c, "SELECT display_name,nav_accepted_at FROM users WHERE id=%s", (uid,))
        if not u:
            raise CrmError("AUTH-INVALID", "사용자를 찾을 수 없어요. 다시 로그인하세요.")
        n = lambda sql: _one(c, sql, (uid,))["n"]
        counts = {
            "claims": n("SELECT count(*) AS n FROM candidates c JOIN records r ON r.user_id=c.user_id AND r.id=c.record_id "
                        "AND r.deleted_at IS NULL WHERE c.user_id=%s AND c.decision='confirmed'"),
            "principles": n("SELECT count(*) AS n FROM items i JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id "
                            "WHERE i.user_id=%s AND o.semantic_kind='principle' AND i.revision > 0 AND i.archived_at IS NULL "
                            "AND i.decision='confirmed'"),
            "decisions": n("SELECT count(*) AS n FROM items i JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id "
                           "WHERE i.user_id=%s AND o.semantic_kind='event' AND i.revision > 0 AND i.archived_at IS NULL"),
            "people": n("SELECT count(*) AS n FROM people WHERE user_id=%s"),
            "sources": n("SELECT count(*) AS n FROM sources WHERE user_id=%s"),
            "reflections": n("SELECT count(*) AS n FROM source_reflections WHERE user_id=%s"),
        }
    return {"display_name": u["display_name"], "nav": "new" if u["nav_accepted_at"] else "old", "counts": counts}



# ── PR ①-b 페이지 레이아웃 · 「나」 통계 (2026-10-22, 구현계획 §3) ──
# 레이아웃 = 사용자당 page_layouts 한 행. 기본값은 여기 한 곳. 탭 7 은 순서만 바뀐다(빼기·더하기 없음). 블록은 알려진 이름만·중복 없음.
DEFAULT_LAYOUT = {"v": 1, "tabs": ["summary", "principle", "decision", "axis", "custom", "person", "source"],
                  "blocks": {"summary": ["heatmap", "kinds", "waiting", "recent"]}}
LAYOUT_BLOCKS = {"summary": ("heatmap", "kinds", "waiting", "recent")}   # 블록 자리 → 알려진 블록 이름
LAYOUT_MAX_BYTES = 4096   # 웹 PUT 본문 layout 의 JSON 상한(web 이 json_limits 로 먼저 본다)


def _layout_invalid(why):
    return CrmError("LAYOUT-INVALID", f"레이아웃 모양이 맞지 않아요: {why}", "화면을 새로 고친 뒤 다시 옮겨 보세요.")


def layout_check(layout):
    """허용 모양만 통과 — {"v": 1, "tabs": 기본 탭 7 의 순열, "blocks": {자리: 알려진 블록 이름 목록(중복 없음)}}. 다른 키 금지.
    통과하면 정규화한 사본(같은 모양)을 돌려준다. 아니면 LAYOUT-INVALID."""
    if not isinstance(layout, dict) or set(layout) != {"v", "tabs", "blocks"}:
        raise _layout_invalid("키는 v·tabs·blocks 셋이에요")
    if layout["v"] != 1 or isinstance(layout["v"], bool):
        raise _layout_invalid("v 는 1 이에요")
    tabs = layout["tabs"]
    want = DEFAULT_LAYOUT["tabs"]
    if not isinstance(tabs, list) or not all(isinstance(t, str) for t in tabs) or len(tabs) != len(want) or set(tabs) != set(want):
        raise _layout_invalid(f"tabs 는 {', '.join(want)} 일곱 개를 한 번씩")
    blocks = layout["blocks"]
    if not isinstance(blocks, dict) or not set(blocks) <= set(LAYOUT_BLOCKS):
        raise _layout_invalid(f"blocks 자리는 {', '.join(LAYOUT_BLOCKS)} 만")
    for slot, names in blocks.items():
        known = LAYOUT_BLOCKS[slot]
        if not isinstance(names, list) or not all(isinstance(n, str) and n in known for n in names) or len(set(names)) != len(names):
            raise _layout_invalid(f"blocks.{slot} 는 {', '.join(known)} 중에서 중복 없이")
    return {"v": 1, "tabs": list(tabs), "blocks": {k: list(v) for k, v in blocks.items()}}


def _default_layout():
    return json.loads(json.dumps(DEFAULT_LAYOUT))   # 깊은 사본 — 상수를 호출자가 못 바꾼다


def layout_get(uid):
    """{layout, version} — 행 없음 = 기본 + 0 · layout NULL(복원 뒤) = 기본 + 그 version."""
    with tx() as c:
        r = _one(c, "SELECT layout, version FROM page_layouts WHERE user_id=%s", (uid,))
    if not r:
        return {"layout": _default_layout(), "version": 0}
    return {"layout": r["layout"] if r["layout"] is not None else _default_layout(), "version": r["version"]}


def _layout_stale(cur):
    return CrmError("LAYOUT-STALE", f"다른 화면에서 레이아웃이 먼저 바뀌었어요 (지금 버전 {cur}).", "화면을 새로 고친 뒤 다시 옮겨 보세요.")


def layout_set(uid, layout, version):
    """PUT — version = 화면이 마지막으로 받은 값. 맞으면 저장하고 +1, 다르면 LAYOUT-STALE. 반환 {layout, version}."""
    lay = layout_check(layout)
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise CrmError("ARG-INVALID", "version 은 0 이상의 정수예요.")
    with tx() as c:
        _user_lock(c, uid)
        if version == 0:
            r = _one(c, "INSERT INTO page_layouts (user_id, layout, version) VALUES (%s, %s, 1) ON CONFLICT (user_id) DO NOTHING "
                        "RETURNING version", (uid, Jsonb(lay)))
        else:
            r = _one(c, "UPDATE page_layouts SET layout=%s, version=version+1, updated_at=now() WHERE user_id=%s AND version=%s "
                        "RETURNING version", (Jsonb(lay), uid, version))
        if not r:
            cur = _one(c, "SELECT version FROM page_layouts WHERE user_id=%s", (uid,))
            raise _layout_stale(cur["version"] if cur else 0)
    return {"layout": lay, "version": r["version"]}


def layout_reset(uid):
    """DELETE — 기본으로 되돌리기. 행이 있으면 layout NULL + version +1(행을 지우지 않는다 — 버전이 되감기면 옛 탭의 PUT 이 통과한다).
    행이 없으면 아무것도 안 한다(기본 + 0). 반환 {layout(기본), version}."""
    with tx() as c:
        _user_lock(c, uid)
        r = _one(c, "UPDATE page_layouts SET layout=NULL, version=version+1, updated_at=now() WHERE user_id=%s RETURNING version", (uid,))
    return {"layout": _default_layout(), "version": r["version"] if r else 0}


HEATMAP_WEEKS = 13


def _axis_object(c, uid):
    """분류 역할 오브젝트 id 또는 None — 읽기만(_classify_target 의 이관 INSERT 를 하지 않고 같은 규칙을 읽기로 적용)."""
    r = _one(c, "SELECT object_id FROM user_roles WHERE user_id=%s AND role='classify'", (uid,))
    if r:
        return r["object_id"]
    r = _one(c, "SELECT id FROM object_defs WHERE user_id=%s AND key='axes' AND revision > 0 AND archived_at IS NULL", (uid,))
    return r["id"] if r else None


def me_stats(uid):
    """「나」 통계 — 전체 범위(페이징 무관), 읽기만. 반환
    {"axes": [{id, code, title, items, records, readings, n}],   # 축 = 분류 역할 오브젝트의 적용·미보관 항목, 코드 순(→ 만든 순 → id)
     "heatmap": {"from", "to", "cells": [{d, records, activities}] × 91},   # 월요일 시작 13주, to = 이번 주 일요일(KST), 미래 칸 0
     "kinds": {principle, decision, axis, custom, records, readings, people, sources},
     "waiting": {candidates_pending, principles_pending, inbox_pending}}
    축 건수 = 그 축에 **직접** 연결된 고유 항목(자기 자신 제외·적용·미보관) + 고유 기록(record_attach axes, 지운 기록 제외) + 읽기 자료(②-d 전 0).
    한 글이 두 축에 붙으면 각 축에서 1. 활동은 축 건수에 넣지 않는다.
    히트맵 날짜 = 기록 COALESCE(source_date, kst(source_created_at)) · 활동 occurred_on(현재 판·active). 둘 다 없는 기록은 빠진다(created 대체 없음).
    kinds: principle·decision = semantic_kind principle·event 의 적용·미보관 항목 전체 · axis = 분류 역할 오브젝트 항목 ·
    custom = 그 밖의 적용·미보관 항목 · records = 지우지 않은 기록 · readings = 0(②-d 전) · people·sources = 행 수."""
    import activity, imports   # 둘 다 db 를 가져다 쓴다 — 모듈 맨 위에서 가져오면 순환
    with tx() as c:
        ax_obj = _axis_object(c, uid)
        live = ("SELECT i.id, i.object_id, o.semantic_kind FROM items i JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id "
                "WHERE i.user_id=%(uid)s AND i.revision > 0 AND i.archived_at IS NULL")
        a = {"uid": uid, "ax": ax_obj}
        axes = [] if ax_obj is None else [dict(r) for r in _all(c, f"""
            WITH live AS ({live})
            SELECT x.id, x.code, x.title,
              (SELECT count(DISTINCT l.source_item_id) FROM item_links l JOIN live s ON s.id=l.source_item_id
                WHERE l.user_id=x.user_id AND l.target_item_id=x.id AND l.source_item_id <> x.id) AS items,
              (SELECT count(DISTINCT l.source_record_id) FROM item_links l JOIN records r ON r.user_id=l.user_id AND r.id=l.source_record_id
                WHERE l.user_id=x.user_id AND l.target_item_id=x.id AND l.field_key='axes' AND r.deleted_at IS NULL) AS records
            FROM items x WHERE x.user_id=%(uid)s AND x.object_id=%(ax)s AND x.revision > 0 AND x.archived_at IS NULL
            ORDER BY x.code NULLS LAST, x.created_at, x.id""", a)]
        for x in axes:
            x["readings"] = 0
            x["n"] = x["items"] + x["records"] + x["readings"]
        to = _one(c, f"SELECT ({activity._TODAY} - extract(isodow FROM {activity._TODAY})::int + 7) AS d")["d"]   # 이번 주 일요일
        frm = to - _dt.timedelta(days=HEATMAP_WEEKS * 7 - 1)
        b = {"uid": uid, "frm": frm, "to": to}
        rec = {r["d"]: r["n"] for r in _all(c, f"SELECT {_RECORD_DAY} AS d, count(*) AS n FROM records WHERE user_id=%(uid)s "
                                               f"AND deleted_at IS NULL AND {_RECORD_DAY} BETWEEN %(frm)s AND %(to)s GROUP BY 1", b)}
        act = {r["d"]: r["n"] for r in _all(c, f"SELECT occurred_on AS d, count(DISTINCT id) AS n FROM ({activity._CUR} AND a.state='active') cur "
                                               "WHERE occurred_on BETWEEN %(frm)s AND %(to)s GROUP BY 1", b)}
        cells = []
        for i in range(HEATMAP_WEEKS * 7):
            d = frm + _dt.timedelta(days=i)
            cells.append({"d": d.isoformat(), "records": rec.get(d, 0), "activities": act.get(d, 0)})
        k = _one(c, f"""WITH live AS ({live}) SELECT
            (SELECT count(*) FROM live WHERE semantic_kind='principle' AND object_id IS DISTINCT FROM %(ax)s) AS principle,
            (SELECT count(*) FROM live WHERE semantic_kind='event' AND object_id IS DISTINCT FROM %(ax)s) AS decision,
            (SELECT count(*) FROM live WHERE object_id = %(ax)s) AS axis,
            (SELECT count(*) FROM live WHERE semantic_kind NOT IN ('principle','event') AND object_id IS DISTINCT FROM %(ax)s) AS custom,
            (SELECT count(*) FROM records WHERE user_id=%(uid)s AND deleted_at IS NULL) AS records,
            (SELECT count(*) FROM people WHERE user_id=%(uid)s) AS people,
            (SELECT count(*) FROM sources WHERE user_id=%(uid)s) AS sources,
            (SELECT count(*) FROM candidates cd JOIN records r ON r.user_id=cd.user_id AND r.id=cd.record_id AND r.deleted_at IS NULL
              WHERE cd.user_id=%(uid)s AND cd.decision='pending') AS candidates_pending,
            (SELECT count(*) FROM items i JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id WHERE i.user_id=%(uid)s
              AND o.semantic_kind='principle' AND i.revision > 0 AND i.archived_at IS NULL AND i.decision='pending') AS principles_pending""", a)
    kinds = {"principle": k["principle"], "decision": k["decision"], "axis": k["axis"], "custom": k["custom"],
             "records": k["records"], "readings": 0, "people": k["people"], "sources": k["sources"]}
    waiting = {"candidates_pending": k["candidates_pending"], "principles_pending": k["principles_pending"],
               "inbox_pending": imports.inbox_count(uid)}
    return {"axes": axes, "heatmap": {"from": frm.isoformat(), "to": to.isoformat(), "cells": cells}, "kinds": kinds, "waiting": waiting}

# ── 사람 (별칭 이름표만) ──

def person_list(uid, query="", cursor="", limit=PAGE_DEFAULT):
    """사람 목록(최근 순). query = 별칭 부분 일치. 반환 {people, next_cursor}."""
    after, limit = _page_args(cursor, limit)
    sql, args = f"SELECT {_PERSON_COLS} FROM people WHERE user_id=%s", [uid]
    if query:
        sql += " AND alias ILIKE %s"; args.append(_like(query))
    if after:
        sql += " AND (created, id) < (%s, %s)"; args += list(after)
    with tx() as c:
        rows, nxt = _page(_all(c, sql + " ORDER BY created DESC, id DESC LIMIT %s", args + [limit + 1]), limit)
    return {"people": [_iso(r) for r in rows], "next_cursor": nxt}


def people_all(uid):
    """/api/state 용 — 본인 사람 전부(최근 순)."""
    with tx() as c:
        return [_iso(r) for r in _all(c, f"SELECT {_PERSON_COLS} FROM people WHERE user_id=%s ORDER BY created DESC, id DESC", (uid,))]


def person_get(uid, pid):
    with tx() as c:
        r = _one(c, f"SELECT {_PERSON_COLS} FROM people WHERE user_id=%s AND id=%s", (uid, str(pid or "")))
    if not r:
        raise _nf("그 사람")
    return _iso(r)


def person_save(uid, alias, person_id="", expected_revision=None):
    """person_id 없으면 생성(일일 한도 people, revision 0), 있으면 별칭 수정(expected_revision 필수, revision +1). 별칭 외 필드는 없다."""
    alias = _label(alias, "별칭")
    with tx() as c:
        if not person_id:
            if expected_revision is not None:
                raise CrmError("ARG-INVALID", "새로 만들 땐 expected_revision 을 보내지 않아요.")
            _daily_gate(c, uid, "people")
            pid = new_id("p")
            c.execute("INSERT INTO people (user_id,id,alias) VALUES (%s,%s,%s)", (uid, pid, alias))
        else:
            rev = _rev_arg(expected_revision)
            _user_lock(c, uid)
            pid = str(person_id)
            r = _one(c, "SELECT revision,alias FROM people WHERE user_id=%s AND id=%s FOR UPDATE", (uid, pid))
            if not r:
                raise _nf("그 사람")
            if r["revision"] != rev:
                raise _conflict(f"지금 revision {r['revision']}")
            if r["alias"] != alias:
                c.execute("UPDATE people SET alias=%s, revision=revision+1, updated=now() WHERE user_id=%s AND id=%s", (alias, uid, pid))
        return _iso(_one(c, f"SELECT {_PERSON_COLS} FROM people WHERE user_id=%s AND id=%s", (uid, pid)))


def person_delete(uid, person_id, expected_revision):
    rev = _rev_arg(expected_revision)
    with tx() as c:
        _user_lock(c, uid)
        r = _one(c, "SELECT revision FROM people WHERE user_id=%s AND id=%s FOR UPDATE", (uid, str(person_id or "")))
        if not r:
            raise _nf("그 사람")
        if r["revision"] != rev:
            raise _conflict(f"지금 revision {r['revision']}")
        used = person_usage(c, uid, str(person_id))
        if any(used.values()):   # board 이전 r3 T7 — 연결을 몰래 끊거나 활동을 연쇄 삭제하지 않는다
            where = " · ".join(f"{_PERSON_USE_NAMES[k]} {n}" for k, n in used.items() if n)
            raise CrmError("PERSON-IN-USE", f"이 사람은 연결돼 있어 지울 수 없어요({where} — 활동은 옛 판 포함).",
                           "연결된 항목·활동(옛 판 포함)을 먼저 확인하세요. 별칭은 바꿀 수 있어요.")
        c.execute("DELETE FROM people WHERE user_id=%s AND id=%s", (uid, str(person_id)))
    return {"id": str(person_id), "deleted": True}


_PERSON_USE_NAMES = {"activities": "활동", "records": "기록", "items": "항목"}


def person_usage(c, uid, pid) -> dict:
    """사람을 가리키는 연결 수 — 활동(activity_links.activity_id 출발) · 기록(activity_links.record_id 출발) · 항목(item_links reference)."""
    a = _one(c, "SELECT count(DISTINCT activity_id) AS a, count(DISTINCT record_id) AS r FROM activity_links "
                "WHERE user_id=%s AND person_id=%s", (uid, pid))
    i = _one(c, "SELECT count(DISTINCT source_item_id) AS n FROM item_links WHERE user_id=%s AND target_person_id=%s", (uid, pid))
    return {"activities": a["a"], "records": a["r"], "items": i["n"]}


# ── 출처 ──

_SRC_COLS = "id,kind,name,location"


def source_list(uid, query="", cursor="", limit=PAGE_DEFAULT):
    """출처 목록(최근 순, 계약 모양 {id,kind,name,location}). query = 이름·위치 부분 일치. 반환 {sources, next_cursor}."""
    after, limit = _page_args(cursor, limit)
    sql, args = f"SELECT {_SRC_COLS},created FROM sources WHERE user_id=%s", [uid]
    if query:
        sql += " AND (name ILIKE %s OR location ILIKE %s)"; args += [_like(query)] * 2
    if after:
        sql += " AND (created, id) < (%s, %s)"; args += list(after)
    with tx() as c:
        rows, nxt = _page(_all(c, sql + " ORDER BY created DESC, id DESC LIMIT %s", args + [limit + 1]), limit)
    return {"sources": [{k: r[k] for k in ("id", "kind", "name", "location")} for r in rows], "next_cursor": nxt}


def source_get(uid, sid):
    """출처 하나 + 개수(지워지지 않은 기록 · 요약 · 반응)."""
    with tx() as c:
        r = _one(c, f"SELECT {_SRC_COLS} FROM sources WHERE user_id=%s AND id=%s", (uid, str(sid or "")))
        if not r:
            raise _nf("그 출처")
        rec = _one(c, "SELECT count(*) AS n FROM records WHERE user_id=%s AND source_id=%s AND deleted_at IS NULL", (uid, r["id"]))["n"]
        by = {x["kind"]: x["n"] for x in _all(c, "SELECT kind, count(*) AS n FROM source_reflections WHERE user_id=%s AND source_id=%s "
                                                 "GROUP BY kind", (uid, r["id"]))}
    return {**dict(r), "counts": {"records": rec, "summaries": by.get("summary", 0), "reactions": by.get("reaction", 0)}}


def source_create(uid, kind, name, location=""):
    """출처 찾거나 만들기 — record_add 와 같은 규칙((kind, name, location) 유일). 기록 없이 만들 수 있다. 반환 {id,kind,name,location}."""
    k, n, loc = _source_norm({"kind": kind, "name": name, "location": location})
    with tx() as c:
        _user_lock(c, uid)
        sid = _source_find_or_create(c, uid, k, n, loc)
        return dict(_one(c, f"SELECT {_SRC_COLS} FROM sources WHERE user_id=%s AND id=%s", (uid, sid)))


# ── 출처 요약/반응 (자기 성찰 · 검토 대기/완료 · 인용 근거 아님) ──

def _refl_args(kind, body):
    if kind not in REFLECTION_KINDS:
        raise CrmError("ARG-INVALID", "kind 는 summary(내 요약)·reaction(내 반응) 중 하나예요.")
    if not isinstance(body, str) or not body.strip():
        raise CrmError("ARG-INVALID", "본문이 비었어요.")
    if len(body) > REFLECTION_MAX:
        raise CrmError("ARG-INVALID", f"본문은 {REFLECTION_MAX}자까지예요.")
    return body


def _refl_lock(c, uid, rid):
    r = _one(c, f"SELECT {_REFL_COLS} FROM source_reflections WHERE user_id=%s AND id=%s FOR UPDATE", (uid, str(rid or "")))
    if not r:
        raise _nf("그 요약/반응")
    return r


def _refl_get(c, uid, rid):
    return _iso(_one(c, f"SELECT {_REFL_COLS} FROM source_reflections WHERE user_id=%s AND id=%s", (uid, rid)))


def source_reflection_list(uid, source_id="", kind="", review="", cursor="", limit=PAGE_DEFAULT):
    """요약/반응 목록(최근 순). 거르기: 출처·유형·검토 상태. 반환 {reflections, next_cursor}."""
    if kind and kind not in REFLECTION_KINDS:
        raise CrmError("ARG-INVALID", "kind 는 summary·reaction 중 하나예요.")
    if review and review not in REFLECTION_REVIEWS:
        raise CrmError("ARG-INVALID", "review 는 pending·reviewed 중 하나예요.")
    after, limit = _page_args(cursor, limit)
    sql, args = f"SELECT {_REFL_COLS} FROM source_reflections WHERE user_id=%s", [uid]
    for col, v in (("source_id", source_id), ("kind", kind), ("review", review)):
        if v:
            sql += f" AND {col}=%s"; args.append(v)
    if after:
        sql += " AND (created, id) < (%s, %s)"; args += list(after)
    with tx() as c:
        rows, nxt = _page(_all(c, sql + " ORDER BY created DESC, id DESC LIMIT %s", args + [limit + 1]), limit)
    return {"reflections": [_iso(r) for r in rows], "next_cursor": nxt}


def reflections_all(uid):
    """/api/state 용 — 본인 요약/반응 전부(최근 순)."""
    with tx() as c:
        return [_iso(r) for r in _all(c, f"SELECT {_REFL_COLS} FROM source_reflections WHERE user_id=%s ORDER BY created DESC, id DESC",
                                      (uid,))]


def source_reflection_get(uid, rid):
    with tx() as c:
        r = _one(c, f"SELECT {_REFL_COLS} FROM source_reflections WHERE user_id=%s AND id=%s", (uid, str(rid or "")))
    if not r:
        raise _nf("그 요약/반응")
    return _iso(r)


def source_reflection_save(uid, source_id, kind, body, reflection_id="", expected_revision=None):
    """reflection_id 없으면 생성(검토 대기, 일일 한도 reflections), 있으면 수정(expected_revision 필수). 출처는 본인 것만(남의 것 = 없는 것).
    검토 완료 뒤 본문·유형·출처가 바뀌면 검토 대기로 돌리고 검토 값을 비운다. 같은 값이면 무동작. 범위 확인 인자는 없다(결정 7)."""
    body = _refl_args(kind, body)
    sid = str(source_id or "")
    with tx() as c:
        if not reflection_id:
            if expected_revision is not None:
                raise CrmError("ARG-INVALID", "새로 만들 땐 expected_revision 을 보내지 않아요.")
            _daily_gate(c, uid, "reflections")
        else:
            rev = _rev_arg(expected_revision)
            _user_lock(c, uid)
        if not _one(c, "SELECT 1 AS x FROM sources WHERE user_id=%s AND id=%s", (uid, sid)):
            raise _nf("그 출처")
        if not reflection_id:
            rid = new_id("sr")
            c.execute("INSERT INTO source_reflections (user_id,id,source_id,kind,body) VALUES (%s,%s,%s,%s,%s)", (uid, rid, sid, kind, body))
            return _refl_get(c, uid, rid)
        r = _refl_lock(c, uid, reflection_id)
        if r["revision"] != rev:
            raise _conflict(f"지금 revision {r['revision']}")
        if (r["source_id"], r["kind"], r["body"]) != (sid, kind, body):
            c.execute("UPDATE source_reflections SET source_id=%s, kind=%s, body=%s, review='pending', reviewed_revision=NULL, "
                      "reviewed_at=NULL, revision=revision+1, updated=now() WHERE user_id=%s AND id=%s", (sid, kind, body, uid, r["id"]))
        return _refl_get(c, uid, r["id"])


def source_reflection_review(uid, reflection_id, expected_revision, reviewed):
    """검토 완료(reviewed=true) / 검토 대기로(false). expected_revision 이 지금 revision 일 때만. 바뀌면 revision +1 —
    완료면 reviewed_revision = 바뀐 revision(본 내용의 판)·reviewed_at = now(). 이미 그 상태면 무동작. 검토 완료 ≠ 판단 확정."""
    if not isinstance(reviewed, bool):
        raise CrmError("ARG-INVALID", "reviewed 는 true·false 예요.")
    rev = _rev_arg(expected_revision)
    with tx() as c:
        _user_lock(c, uid)
        r = _refl_lock(c, uid, reflection_id)
        if r["revision"] != rev:
            raise _conflict(f"지금 revision {r['revision']}")
        if reviewed and r["review"] != "reviewed":
            c.execute("UPDATE source_reflections SET review='reviewed', revision=revision+1, reviewed_revision=revision+1, reviewed_at=now(), "
                      "updated=now() WHERE user_id=%s AND id=%s", (uid, r["id"]))
        elif not reviewed and r["review"] != "pending":
            c.execute("UPDATE source_reflections SET review='pending', revision=revision+1, reviewed_revision=NULL, reviewed_at=NULL, "
                      "updated=now() WHERE user_id=%s AND id=%s", (uid, r["id"]))
        return _refl_get(c, uid, r["id"])


def source_reflection_delete(uid, reflection_id, expected_revision):
    rev = _rev_arg(expected_revision)
    with tx() as c:
        _user_lock(c, uid)
        r = _refl_lock(c, uid, reflection_id)
        if r["revision"] != rev:
            raise _conflict(f"지금 revision {r['revision']}")
        c.execute("DELETE FROM source_reflections WHERE user_id=%s AND id=%s", (uid, r["id"]))
    return {"id": r["id"], "deleted": True}


# ── 상태 한 벌 (화면과 도구가 같은 것을 본다) ─────────────

def next_action(uid):
    qs, recs, cands = questions(uid), records(uid, 5), candidates(uid)
    if not qs and not recs:
        return {"kind": "record", "text": "질문이 아직 없어도 돼요. 최근 경험 메모 1편부터 남겨 보세요."}
    if not recs:
        return {"kind": "record", "text": "기록 1편을 남겨 보세요."}
    used = {c["record_id"] for c in cands}
    fresh = [r for r in recs if r["id"] not in used]
    if qs and fresh:
        return {"kind": "digest", "target": fresh[0]["id"], "text": f"「{fresh[0]['title']}」에서 판단 후보를 뽑아 보세요."}
    pend = [c for c in cands if c["status"] == "pending"]
    if pend:
        return {"kind": "review", "target": pend[0]["id"], "text": f"아직 안 정한 문장 {len(pend)}개를 원문과 대조해 보세요."}
    if not qs:
        return {"kind": "question", "text": "기록을 다시 볼 때 궁금한 점을 질문 하나로 남겨 보세요."}
    return {"kind": "free", "text": "기록 1편 추가 또는 질문 수정 중 하나를 고르세요."}


def state(uid):
    return {
        "generated": now().isoformat(),
        "profile": profile(uid),
        "questions": questions(uid),
        "tabs": tabs(uid).get("tabs", []),
        "candidates": candidates(uid),
        "journal": records(uid),
        "next": next_action(uid),
        # 2단계 — 사용자 정의 층
        "definitions": definitions(uid),
        "items": items(uid),
        "item_proposals": item_proposals(uid),
        "record_links": record_links(uid),
    }
