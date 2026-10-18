"""저장소 — Postgres (psycopg 3, 동기, 작은 풀). 스키마는 schema.sql, 설계 근거는 data-model.md.

데이터 흐름: 기록(원문 그대로) → 후보(pending) → 사용자 결정(confirm/exclude/edit/pending) → 이력 보존.
사용자 구분은 users.id. 모든 조회·쓰기는 user_id 로 묶이고, 자식 테이블은 복합 FK 로 다른 사용자 행을 참조 못 한다.
연결: 환경변수 DATABASE_URL (Supabase 는 Session pooler, 5432).
"""
import json
import os
import re
import secrets
import threading
import time
import datetime as _dt
from contextlib import contextmanager
from pathlib import Path

from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

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


class CanonError(Exception):
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
}

# 관리자 내부 user id — ③ 전역 DB 크기 거절에서만 예외. main 이 시작 때 env CANON_ADMIN_USER_IDS 로 한 번 채운다.
ADMIN_USER_IDS: frozenset = frozenset()


def _nbytes(v: str) -> int:
    return len(v.encode("utf-8"))


def check_len(name: str, v: str, lim: int):
    """② 문자열 하나의 길이. 넘으면 PAYLOAD-LIMIT(필드명·상한). check_lengths 와 MCP 도구가 아닌 db 쓰기 함수가 같이 쓴다."""
    if _nbytes(v) > lim:
        raise CanonError("PAYLOAD-LIMIT", f"{name} 가 너무 길어요 (상한 {lim} 바이트, 지금 {_nbytes(v)} 바이트).",
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
    if (uid() if callable(uid) else uid) in ADMIN_USER_IDS:
        return
    raise CanonError("QUOTA-GLOBAL", "서비스 저장 공간이 한도에 가까워 지금은 새로 쓸 수 없어요. 읽기는 그대로 돼요.",
                     "운영자에게 알려 주세요.")


def write_gate(op: str, args: dict, uid):
    """쓰기 진입 한 곳 — ② 길이 → ③ 전역 크기. MCP guard 와 웹 쓰기 API 가 같은 이 함수를 부른다."""
    check_lengths(op, args)
    check_global_quota(uid)


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
            raise CanonError("DB-CONFIG", "DATABASE_URL 이 없어요.", "서버 환경변수에 Postgres 연결 문자열을 넣으세요.")
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
      4. template_slot — object_defs·field_defs 에 ADD COLUMN IF NOT EXISTS → 백필(_backfill_template_slots). 열이 먼저다."""
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
        for t in ("object_defs", "field_defs"):   # 열이 있으면 ALTER 를 안 부른다(IF NOT EXISTS 여도 테이블을 잠근다)
            if not _has_col(c, t, "template_slot"):
                c.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS template_slot text")
        _backfill_template_slots(c)   # 매번 돈다 — 시드가 적용 뒤 표시 전에 끊긴 경우도 여기서 채운다


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
        n_obj += c.execute("UPDATE object_defs SET template_slot=%s WHERE template_slot IS NULL AND id=%s AND key=%s",
                           (slot, d["id"], d["key"])).rowcount
        # 필드는 오브젝트 슬롯이 이 판으로 잡힌 곳 아래에서만(이번에 잡힌 것 + 시드 표시 중 필드 표시가 빠진 것)
        n_field += c.execute("UPDATE field_defs AS f SET template_slot = %s || '.' || f.key FROM object_defs AS o "
                             "WHERE o.user_id = f.user_id AND o.id = f.object_id AND o.id = %s AND o.template_slot = %s "
                             "AND f.template_slot IS NULL AND f.key = ANY(%s)",
                             (slot, d["id"], slot, [f["key"] for f in d["fields"]])).rowcount
    return n_obj, n_field


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
            raise CanonError("DB-MIGRATE", f"PR① 마이그레이션 중단: 인용 범위가 맞지 않는 후보 {bad}개 — 전체 롤백했어요.",
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
            raise CanonError("DB-MIGRATE", f"PR① 마이그레이션 중단: candidate_history 백필 실패 {left}행(부모 없음 또는 스냅샷 없음) — 전체 롤백했어요.")
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


@contextmanager
def tx():
    with pool().connection() as c:
        with c.transaction():
            yield c


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
            return _iso(r)
        uid = new_id("u")
        c.execute("INSERT INTO users (id,email) VALUES (%s,%s)", (uid, email))
        return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (uid,)))


def user_by_subject(sub, email):
    """인증된 sub 로 먼저 찾는다. 없으면 검증된 이메일로 찾아 sub 를 붙이고, 그것도 없으면 새로 만든다.
    같은 이메일 = 한 사용자(Google·매직링크 어느 쪽이든)."""
    sub = (sub or "").strip()
    email = (email or "").strip().lower()
    if not sub:
        raise CanonError("AUTH-INVALID", "토큰에 사용자 식별자(sub)가 없어요.")
    with tx() as c:
        r = _one(c, "SELECT * FROM users WHERE auth_subject=%s", (sub,))
        if r:
            return _iso(r)
        if not email:
            raise CanonError("AUTH-INVALID", "처음 연결할 땐 검증된 이메일이 필요해요.")
        r = _one(c, "SELECT * FROM users WHERE email=%s", (email,))
        if r:
            if r["auth_subject"] and r["auth_subject"] != sub:
                raise CanonError("AUTH-CONFLICT", "이 이메일은 다른 로그인 계정에 이미 연결돼 있어요.")
            c.execute("UPDATE users SET auth_subject=%s WHERE id=%s", (sub, r["id"]))
            return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (r["id"],)))
        uid = new_id("u")
        c.execute("INSERT INTO users (id,email,auth_subject) VALUES (%s,%s,%s)", (uid, email, sub))
        return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (uid,)))


def user_get(uid):
    with tx() as c:
        r = _one(c, "SELECT id,email,created FROM users WHERE id=%s", (uid,))
    if not r:
        raise CanonError("AUTH-INVALID", "사용자를 찾을 수 없어요. 다시 로그인하세요.")
    return _iso(r)


def user_delete(uid):
    """계정 삭제 = 그 사용자 데이터 전체 삭제(cascade)."""
    with tx() as c:
        # 정의 층은 보호 Lookup(NO ACTION)이 있어 자식부터 지운다. 나머지는 users cascade.
        for t in ("item_links", "item_history", "items", "field_defs", "object_defs"):
            c.execute(f"DELETE FROM {t} WHERE user_id=%s", (uid,))
        c.execute("DELETE FROM users WHERE id=%s", (uid,))


# ── 질문 ───────────────────────────────────────────────

def question_add(uid, text):
    text = (text or "").strip()
    if not text:
        raise CanonError("ARG-INVALID", "질문 문장이 비었어요.")
    qid = new_id("q")
    with tx() as c:
        c.execute("INSERT INTO questions (id,user_id,text) VALUES (%s,%s,%s)", (qid, uid, text))
        return _iso(_one(c, "SELECT id,text,status,created FROM questions WHERE id=%s", (qid,)))


def questions(uid):
    with tx() as c:
        return [_iso(r) for r in _all(c, "SELECT id,text,status,created FROM questions WHERE user_id=%s ORDER BY created", (uid,))]


def question_get(uid, qid):
    with tx() as c:
        r = _one(c, "SELECT id,text,status,created FROM questions WHERE user_id=%s AND id=%s", (uid, qid))
    if not r:
        raise CanonError("ARG-INVALID", f"질문을 찾을 수 없어요: {qid}", "question_list 로 질문 ID 를 확인하세요.")
    return _iso(r)


# ── 기록 ───────────────────────────────────────────────
# 출처(PR①): author_kind(self·external·unknown) × text_process(verbatim·structured·rewritten·generated·unknown).
# 인용 적격 = self·verbatim·삭제 안 됨 — 후보(판단 근거)는 적격 기록에서만 만든다. 본문은 불변, 출처 판정만 정정한다.

AUTHOR_KINDS = ("self", "external", "unknown")
TEXT_PROCESSES = ("verbatim", "structured", "rewritten", "generated", "unknown")
_ELIGIBLE_SQL = "r.author_kind='self' AND r.text_process='verbatim' AND r.deleted_at IS NULL"
INELIGIBLE_WHY = "이 기록은 본인 원문이 아니라 근거로 쓸 수 없어요"
_REC_COLS = "id,title,body,source,question_id,author_kind,text_process,created"


def _eligible(r):
    return r["author_kind"] == "self" and r["text_process"] == "verbatim" and r.get("deleted_at") is None


def _check_provenance(author_kind, text_process):
    if author_kind not in AUTHOR_KINDS:
        raise CanonError("ARG-INVALID", f"author_kind 는 {', '.join(AUTHOR_KINDS)} 중 하나예요.")
    if text_process not in TEXT_PROCESSES:
        raise CanonError("ARG-INVALID", f"text_process 는 {', '.join(TEXT_PROCESSES)} 중 하나예요.")


def record_add(uid, title, body, question_id=None, source="대화에서 입력", author_kind="self", text_process="verbatim"):
    """body 는 받은 그대로 저장한다. 공백도 안 다듬는다.
    출처 기본값 self·verbatim = MCP record_add(사용자가 대화에서 직접 준 말). 확인 못 한 출처는 호출자가 unknown 으로 넘긴다."""
    title = (title or "").strip()
    body = body or ""
    if not body.strip():
        raise CanonError("ARG-INVALID", "기록 내용이 비었어요.")
    _check_provenance(author_kind, text_process)
    if not title:
        title = body.strip().splitlines()[0][:40]
    if question_id:
        question_get(uid, question_id)
    rid = new_id("r")
    hist = [{"at": now().isoformat(), "by": "tool", "author_kind": author_kind, "text_process": text_process}]
    with tx() as c:
        c.execute("INSERT INTO records (id,user_id,title,body,source,question_id,author_kind,text_process,provenance_history) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (rid, uid, title, body, source, question_id or None, author_kind, text_process, Jsonb(hist)))
        return _iso(_one(c, f"SELECT {_REC_COLS} FROM records WHERE id=%s", (rid,)))


def record_get(uid, rid):
    with tx() as c:
        r = _one(c, f"SELECT {_REC_COLS} FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL", (uid, rid))
    if not r:
        raise CanonError("RECORD-MISSING", f"기록을 찾을 수 없어요: {rid}")
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
        raise CanonError("ARG-INVALID", "출처를 고치는 사유가 비었어요.")
    check_len("reason", reason, LIMIT_TEXT)   # α1 ② — provenance_history 에 그대로 쌓인다
    with tx() as c:
        r = _one(c, "SELECT id,author_kind,text_process,deleted_at FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL "
                    "FOR UPDATE", (uid, rid))
        if not r:
            raise CanonError("RECORD-MISSING", f"기록을 찾을 수 없어요: {rid}")
        if (r["author_kind"], r["text_process"]) == (author_kind, text_process):
            return _record_in(c, uid, rid)
        if _eligible(r) and not (author_kind == "self" and text_process == "verbatim"):
            n = _one(c, "SELECT count(*) AS n FROM candidates WHERE user_id=%s AND record_id=%s", (uid, rid))["n"]
            if n:
                raise CanonError("PROVENANCE-LOCKED", f"이 기록을 근거로 한 후보가 {n}개 있어 본인 원문이 아니라고 바꿀 수 없어요.",
                                 "과거 오판정 정정은 별도 복구 작업으로 하세요.")
        line = {"at": now().isoformat(), "by": by, "from": {"author_kind": r["author_kind"], "text_process": r["text_process"]},
                "author_kind": author_kind, "text_process": text_process, "reason": reason}
        c.execute("UPDATE records SET author_kind=%s, text_process=%s, provenance_history = provenance_history || %s "
                  "WHERE user_id=%s AND id=%s", (author_kind, text_process, Jsonb([line]), uid, rid))
        return _record_in(c, uid, rid)


def records(uid, limit=50):
    out = []
    with tx() as c:
        rows = _all(c, "SELECT id,title,body,source,question_id,author_kind,text_process,notes,created FROM records "
                       "WHERE user_id=%s AND deleted_at IS NULL ORDER BY created DESC LIMIT %s", (uid, limit))
    for r in rows:
        d = _iso(r)
        d["excerpt"] = _excerpt(d.pop("body"))
        out.append(d)
    return out


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
        raise CanonError("ARG-INVALID", "origin 은 ai 또는 self 예요.")
    question_id = (question_id or "").strip() or None   # "" = 질문 없음 → NULL (화면은 기록별 묶음)
    wording_by = "ai" if origin == "ai" else None
    added, dropped = [], []
    with tx() as c:
        rec = _one(c, "SELECT id,body,author_kind,text_process,deleted_at FROM records "
                      "WHERE user_id=%s AND id=%s AND deleted_at IS NULL FOR UPDATE", (uid, record_id))
        if not rec:
            raise CanonError("RECORD-MISSING", f"기록을 찾을 수 없어요: {record_id}")
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


def candidate_get(uid, cid):
    with tx() as c:
        r = _one(c, f"SELECT {_CAND_COLS} FROM candidates WHERE user_id=%s AND id=%s", (uid, cid))
    if not r:
        raise CanonError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
    return _iso(r)


def candidates(uid, question_id=None, status=None):
    """status 인자는 도구 계약 이름. 내부 컬럼은 decision. 지워진 기록의 후보는 빠진다."""
    sql = (f"SELECT {_CAND_COLS} FROM candidates c WHERE c.user_id=%s "
           "AND EXISTS (SELECT 1 FROM records r WHERE r.user_id=c.user_id AND r.id=c.record_id AND r.deleted_at IS NULL)")
    args = [uid]
    if question_id:
        sql += " AND c.question_id=%s"; args.append(question_id)
    if status:
        sql += " AND c.decision=%s"; args.append(status)
    sql += " ORDER BY c.created, c.quote_start, c.id"
    with tx() as c:
        return [_iso(r) for r in _all(c, sql, args)]


def _cand_snapshot(r):
    """candidate_history.prev_state v2 — 변경 전 현재값(문장·결정·문장 주체·승인·편집 여부·질문 연결)."""
    return {"schema_version": 2, "text": r["text"], "decision": r["decision"], "wording_by": r["wording_by"],
            "wording_approved": r["wording_approved"], "edited_by_user": r["edited_by_user"], "question_id": r["question_id"]}


def _cand_lock(c, uid, cid):
    r = _one(c, "SELECT * FROM candidates WHERE user_id=%s AND id=%s FOR UPDATE", (uid, cid))
    if not r:
        raise CanonError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
    return r


def _cand_bump(c, uid, r, action, sets, args):
    """변경 한 번 = 변경 전 스냅샷 이력 한 줄(prev_revision = 지금 revision) + 현재값 변경 + revision+1. 바뀐 행을 돌려준다."""
    c.execute("INSERT INTO candidate_history (user_id,candidate_id,action,prev_revision,prev_state) VALUES (%s,%s,%s,%s,%s)",
              (uid, r["id"], action, r["revision"], Jsonb(_cand_snapshot(r))))
    c.execute(f"UPDATE candidates SET {sets}, revision=revision+1, updated=now() WHERE user_id=%s AND id=%s",
              (*args, uid, r["id"]))
    return _cand_lock(c, uid, r["id"])


def candidate_decide(uid, cid, action, text=None, expected_revision=None, wording_by=None):
    """confirm / exclude / pending(확정 취소) / edit / edit_confirm(웹 전용). 바꾸기 전 상태를 이력에 남기고 revision+1.
    같은 결정 반복은 no-op(revision 그대로). 확정된 문장을 고치면 pending 으로 돌아간다(새 문장 확정은 별도 결정).
    edit_confirm 은 edit → confirm 을 한 트랜잭션으로 한다(이력 두 줄, revision +2). 같은 문장 + 이미 confirmed 면 no-op.
    expected_revision: 주면(웹) 지금 revision 과 다를 때 CONFLICT. None 이면(MCP 도구) 검사 안 함.
    문장 수정은 wording_approved=false, wording_by=인자(웹 편집 = self, MCP edit = 모름 NULL). 승인(wording_approved=true)은 이번 범위 밖."""
    if action not in ("confirm", "exclude", "pending", "edit", "edit_confirm"):
        raise CanonError("ARG-INVALID", "action 은 confirm·exclude·pending·edit 중 하나예요.")
    if wording_by not in (None, "self", "ai"):
        raise CanonError("ARG-INVALID", "wording_by 는 self 또는 ai 예요.")
    with tx() as c:
        r = _cand_lock(c, uid, cid)
        if expected_revision is not None and r["revision"] != expected_revision:
            raise _conflict(f"지금 revision {r['revision']}")
        if action in ("edit", "edit_confirm"):
            new = (text or "").strip()
            if not new:
                raise CanonError("ARG-INVALID", "고친 문장이 비었어요.")
            if new != r["text"]:
                r = _cand_bump(c, uid, r, "edit",
                               "text=%s, edited_by_user=true, decision='pending', wording_by=%s, wording_approved=false",
                               (new, wording_by))
            if action == "edit_confirm" and r["decision"] != "confirmed":
                r = _cand_bump(c, uid, r, "confirm", "decision='confirmed'", ())
        else:
            target = CAND_ACTIONS[action]
            if target != r["decision"]:
                _cand_bump(c, uid, r, action, "decision=%s", (target,))
    return candidate_get(uid, cid)


def candidate_restore(uid, cid, history_id, expected_revision=None):
    """이력 한 줄의 변경 전 상태로 되돌린다. 되돌리기도 새 변경 — 지금 상태를 새 이력(action=restore)으로 남기고 revision+1.
    v2 스냅샷: 문장·결정·문장 주체·편집 여부·질문 연결을 되살린다. v1(PR① 전 부분 이력): 문장·결정만, wording_by=NULL.
    승인(wording_approved)은 어느 쪽이든 false — 승인은 그 revision 에 대한 것이라 새 revision 으로 넘기지 않는다.
    MCP 도구엔 안 붙인다(웹 API 는 다음 PR)."""
    with tx() as c:
        r = _cand_lock(c, uid, cid)
        if expected_revision is not None and r["revision"] != expected_revision:
            raise _conflict(f"지금 revision {r['revision']}")
        try:
            hid = int(history_id)
        except (TypeError, ValueError):
            raise CanonError("ARG-INVALID", "history_id 는 숫자예요.")
        h = _one(c, "SELECT id,prev_revision,prev_state FROM candidate_history WHERE user_id=%s AND candidate_id=%s AND id=%s",
                 (uid, cid, hid))
        if not h:
            raise CanonError("ARG-INVALID", f"이 후보의 이력을 찾을 수 없어요: {history_id}")
        st = h["prev_state"]
        if st.get("decision") not in ("pending", "confirmed", "excluded") or not st.get("text"):
            raise CanonError("ARG-INVALID", "이 이력은 되돌릴 수 있는 모양이 아니에요.")
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
        raise CanonError("ARG-INVALID", f"프로필에 없는 항목: {key}", "가능한 항목: " + ", ".join(PROFILE_KEYS))
    if status not in STATUSES:
        raise CanonError("ARG-INVALID", f"상태는 {', '.join(STATUSES)} 중 하나예요.")
    if key == "daily_views":
        value = [v.strip() for v in (value.split("|") if isinstance(value, str) else value) if str(v).strip()]
        if len(value) > 3:
            raise CanonError("ARG-INVALID", "매일 볼 것은 최대 3개예요.")
    with tx() as c:
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
TARGET_KINDS = ("item", "record", "candidate")
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
    return CanonError("CONFLICT", CONFLICT_MSG + (f" ({what})" if what else ""), CONFLICT_ACT)


def _bad_def(msg, action=""):
    return CanonError("DEF-INVALID", msg, action)


def _bad_link(msg, action=""):
    return CanonError("LINK-INVALID", msg, action)


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


def normalize_definition(d):
    """definition_json → 저장 형태. 형식만 본다(대상 오브젝트·기존 항목은 _check_definition)."""
    if not isinstance(d, dict):
        raise _bad_def("정의는 JSON 객체여야 해요.")
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
                raise _bad_def(f"reference 필드 {fk} 의 target_kind 는 item·record·candidate 중 하나예요.")
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

_DEF_COLS = "id,key,label,semantic_kind,revision,policy,draft_doc,draft_id,draft_base_revision,archived_at,created_at,updated_at"
_FIELD_COLS = "key,label,value_type,required,options,target_kind,target_object_id,multiple,position,archived_at"


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
    """적용된(revision>0) 정의 + 필드. 적용 전 새 오브젝트 제안은 definition_propose 결과의 proposal_id 로만 다룬다."""
    sql = f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND revision > 0"
    args = [uid]
    if not include_archived:
        sql += " AND archived_at IS NULL"
    if object_ids:
        sql += " AND id = ANY(%s)"
        args.append(list(object_ids))
    with tx() as c:
        return [_def_out(c, uid, r, include_archived) for r in _all(c, sql + " ORDER BY created_at, id", args)]


def _check_definition(c, uid, oid, row, nd):
    """대상 오브젝트·기존 항목과 맞는지. 깨지면 DEF-INVALID."""
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
    with tx() as c:
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
                c.execute("INSERT INTO object_defs (user_id,id,key,label,semantic_kind) VALUES (%s,%s,%s,%s,%s)",
                          (uid, oid, nd["key"], nd["label"], nd["semantic_kind"]))
                row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, oid))
        else:
            oid = object_id
            row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, oid))
            if not row:
                raise CanonError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {oid}", "definitions_get 으로 ID 를 확인하세요.")
            if row["revision"] != expected_revision:
                raise _conflict(f"지금 revision {row['revision']}")
            if row["archived_at"]:
                raise _bad_def("보관된 오브젝트는 고칠 수 없어요.")
        nd.pop("id", None)
        _check_definition(c, uid, oid, row, nd)
        pid = new_id("dp")
        c.execute("UPDATE object_defs SET draft_doc=%s, draft_id=%s, draft_base_revision=%s, updated_at=now() WHERE user_id=%s AND id=%s",
                  (Jsonb(nd), pid, row["revision"], uid, oid))
    return {"object_id": oid, "proposal_id": pid, "base_revision": row["revision"], "definition": nd,
            "note": "제안만 저장했어요. 사용자가 적용하라고 말할 때만 definition_apply 를 부르세요."}


def definition_apply(uid, object_id, proposal_id, expected_revision):
    """제안 ID·기반 revision 이 맞을 때만 적용. revision+1, history 에 전체 정의 스냅샷, field_defs 동기화(빠진 필드는 archived_at)."""
    with tx() as c:
        row = _one(c, f"SELECT {_DEF_COLS} FROM object_defs WHERE user_id=%s AND id=%s FOR UPDATE", (uid, object_id))
        if not row:
            raise CanonError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}")
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
            raise CanonError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}")
        for fk, fs in field_slots.items():
            c.execute("UPDATE field_defs SET template_slot=%s WHERE user_id=%s AND object_id=%s AND key=%s", (fs, uid, object_id, fk))


def definition_history(uid, object_id):
    with tx() as c:
        r = _one(c, "SELECT history FROM object_defs WHERE user_id=%s AND id=%s", (uid, object_id))
    if not r:
        raise CanonError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {object_id}")
    return r["history"]


# ── 관계 ──

def _link_out(r):
    if r["target_item_id"]:
        return {"field_key": r["field_key"], "target_kind": "item", "target_id": r["target_item_id"]}
    if r["target_record_id"]:
        return {"field_key": r["field_key"], "target_kind": "record", "target_id": r["target_record_id"]}
    return {"field_key": r["field_key"], "target_kind": "candidate", "target_id": r["target_candidate_id"]}


def _links_by_item(c, uid, item_ids):
    out = {}
    if not item_ids:
        return out
    rows = _all(c, "SELECT l.source_item_id,l.field_key,l.target_item_id,l.target_record_id,l.target_candidate_id,t.object_id AS t_obj "
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
    col = {"item": "target_item_id", "record": "target_record_id", "candidate": "target_candidate_id"}[link["target_kind"]]
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


def record_attach(uid, record_id, attach_to=None, axes=None, use_at=None, unattached=None):
    """「이게 어디에 붙나」 네 줄. 항목 → links(attach_to: 기준·결정 항목 또는 후보) · 관심 축 → links(axes: 모음 항목)
    · 쓸 자리·아직 안 붙는 것 → records.notes. None 인 인자는 그대로 두고, 값이 오면(빈 목록·빈 문자열 포함) 바꾼다. 원문은 안 바뀐다."""
    with tx() as c:
        rec = _one(c, "SELECT id,notes FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL FOR UPDATE", (uid, record_id))
        if not rec:
            raise CanonError("RECORD-MISSING", f"기록을 찾을 수 없어요: {record_id}")
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
            ids = []
            for tid in axes:
                tid = str(tid or "")
                t = _resolve_target(c, uid, "item", tid)
                if not t:
                    raise _bad_link(f"관심 축 항목을 찾을 수 없어요: {tid}")
                if t["semantic_kind"] != "collection":
                    raise _bad_link("관심 축은 모음(collection) 종류 항목만이에요.")
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
        raise CanonError("ARG-INVALID", f"오브젝트를 찾을 수 없어요: {oid}", "definitions_get 으로 ID 를 확인하세요.")
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
        raise CanonError("ARG-INVALID", "data 는 JSON 객체예요.")
    title = (title or "").strip()
    if not title:
        raise CanonError("ARG-INVALID", "제목(title)이 비었어요.")
    body = body or ""
    code = (code or "").strip() or None
    with tx() as c:
        od = _object_row(c, uid, object_id)
        if schema_revision != od["revision"]:
            raise _conflict(f"정의 revision 이 지금 {od['revision']}")
        fields = _fields(c, uid, object_id)
        nlinks = _norm_links(c, uid, fields, links)
        dp, lp = _item_problems(fields, od["policy"], data, nlinks, strict=True)
        if dp:
            raise CanonError("ARG-INVALID", "항목 값이 정의와 맞지 않아요 — " + " · ".join(dp), "definitions_get 으로 필드를 확인하세요.")
        if lp:
            raise _bad_link("관계가 정의와 맞지 않아요 — " + " · ".join(lp))
        if item_id:
            row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s FOR UPDATE", (uid, item_id))
            if not row:
                raise CanonError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
            if row["object_id"] != object_id:
                raise CanonError("ARG-INVALID", "항목의 오브젝트가 달라요.")
            if row["revision"] != expected_revision:
                raise _conflict(f"지금 revision {row['revision']}")
            if row["archived_at"]:
                raise CanonError("ARG-INVALID", "보관된 항목이에요. 먼저 되돌리세요(restore).")
            if row["code"] is not None:
                if code is not None and code != row["code"]:
                    raise CanonError("ARG-INVALID", f"코드는 적용 뒤 바꿀 수 없어요(지금 {row['code']}).")
                code = row["code"]
        else:
            if expected_revision != 0:
                raise _conflict("새 항목은 expected_revision 0")
            item_id = new_id("i")
            c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,decision) VALUES (%s,%s,%s,%s,%s)",
                      (uid, item_id, object_id, od["revision"], "pending" if od["semantic_kind"] == "principle" else None))
        if code and _code_taken(c, uid, object_id, code, item_id):
            raise CanonError("ARG-INVALID", f"코드 {code} 는 이미 쓴 코드예요. 코드는 다시 쓰지 않아요(보관 포함).")
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
            raise CanonError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
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
            raise CanonError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
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
        raise CanonError("ARG-INVALID", f"action 은 {', '.join(ITEM_ACTIONS)} 중 하나예요.")
    promotion = None
    with tx() as c:
        row = _one(c, f"SELECT {_ITEM_COLS} FROM items WHERE user_id=%s AND id=%s FOR UPDATE", (uid, item_id))
        if not row:
            raise CanonError("ARG-INVALID", f"항목을 찾을 수 없어요: {item_id}")
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
            except CanonError as e:
                raise _bad_link(e.message + " — 연결 대상이 그사이 보관·삭제됐을 수 있어요.", e.action)
            dp, lp = _item_problems(fields, od["policy"], data, nl, strict=False)
            if dp:
                raise CanonError("ARG-INVALID", "지금 정의와 맞지 않아요 — " + " · ".join(dp), "새로 제안하세요(item_propose).")
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
                raise CanonError("ARG-INVALID", f"코드 {code} 는 이미 쓴 코드예요.")
            try:   # 위 검사와 이 UPDATE 사이에 다른 곳이 같은 코드를 쓰면 UNIQUE 가 막는다 — savepoint 로 잡아 CONFLICT 로
                with c.transaction():
                    bump("apply", "code=%s, title=%s, body=%s, data=%s, schema_revision=%s, draft_doc=NULL, draft_id=NULL, draft_base_revision=NULL",
                         (code, dr["title"], dr["body"], Jsonb(dr["data"]), od["revision"]),
                         {"proposal_id": proposal_id, "created": row["revision"] == 0})
            except pg_errors.UniqueViolation:
                raise CanonError("CONFLICT", f"같은 코드({code})가 방금 다른 곳에서 쓰였어요. 다른 코드로 다시 제안하세요.",
                                 "state_get 으로 지금 코드를 확인하고 item_propose 로 다시 제안하세요.")
            _replace_item_links(c, uid, item_id, row["object_id"], nl)
        elif action in ITEM_DECISIONS:
            if kind != "principle":
                raise CanonError("ARG-INVALID", "내 판단으로 남기기·남기지 않기·되돌리기는 기준(principle) 항목만 해요.",
                                 "다른 항목은 apply·archive·restore 만 있어요.")
            if row["revision"] == 0:
                raise CanonError("ARG-INVALID", "아직 적용하지 않은 제안이에요. 먼저 적용(apply)하세요.")
            target = ITEM_DECISIONS[action]
            if target != row["decision"]:
                bump(action, "decision=%s", (target,))
            if action == "confirm":
                promotion = _promotion(c, uid, od["policy"], cur_links)
        elif action == "archive":
            if row["revision"] == 0:
                raise CanonError("ARG-INVALID", "아직 적용하지 않은 제안이에요. 버리려면 discard 에 proposal_id 를 주세요.")
            if row["archived_at"] is None:
                bump("archive", "archived_at=now()", ())
        else:   # restore
            if history_id is None:
                raise CanonError("ARG-INVALID", "restore 는 history_id 가 필요해요.")
            h = _one(c, "SELECT id,prev_revision,prev_state FROM item_history WHERE user_id=%s AND item_id=%s AND id=%s",
                     (uid, item_id, int(history_id)))
            if not h:
                raise CanonError("ARG-INVALID", f"이 항목의 이력을 찾을 수 없어요: {history_id}")
            if h["prev_revision"] == 0:
                raise CanonError("ARG-INVALID", "처음 적용하기 전 상태로는 되돌릴 수 없어요. 보관(archive)하세요.")
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
