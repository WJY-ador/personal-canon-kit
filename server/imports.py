"""연결 장치 · 수신(/api/imports) · 승인 대기 · 개정 · 한도 · board 이전 기본 연산 (board 이전, 2026-10-20).

정본: notes/2026-10-20_board-마이그-구현계획.md(고정본) > notes/raw/2026-10-20_board-migr-astra-r3.md T1~T7. 계약 = contract/imports/.
원문은 PC, 서버엔 구조화 데이터만. 이 모듈은 모델을 부르지 않고, 본문·토큰·URL 을 로그·감사·오류 문구에 넣지 않는다.

호출하는 곳
  · main.py  — POST /api/imports · GET /api/imports/{import_id}(장치 토큰) · MCP 읽기 inbox_list·inbox_get · 주기 파기 expire_pending
  · web.py   — 「연결 장치」(#_devices) device_* · 「승인 대기」(#_inbox) inbox_* (세션·Origin·write_gate 는 web 이 먼저)
  · migrate_cli.py — migration_* (DATABASE_URL 명시, 웹 실행 없음)

오류 = db.CrmError(code, message, action). HTTP 상태는 http_status(code) 한 곳에서 정한다.
잠금 순서 = users 행(db._user_lock) → 입력원 행 → 접수 행. 수신·승인·제외·폐기·계정 삭제가 같은 순서를 쓴다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac as _hmac
import json
import re
import secrets

from psycopg.types.json import Jsonb

import db
from db import CrmError, _all, _one, tx
from envnames import switch_on

# 공개 상수 ─────────────────────────────────────────────
CONTRACT = "canon-inbox/1"
SCHEMA_REVISION = 2
KINDS = ("self_text", "activity", "self_summary")
ACTIONS = ("like", "repost", "comment", "read", "save", "send", "reply")
PROVIDERS = ("sns", "web", "mail", "kakao")
PERSON_ROLES = ("author", "counterparty", "participant")
EXECUTIONS = ("attended", "unattended")
ACTIVITY_STATES = ("active", "retracted")
# 등록된 변환 버전 → 허용 종류·행동·제공자. 장치 policy.transform_versions 는 이 키에서만 고른다(자유 문자열 없음)
TRANSFORMS = {
    "board-likes/1": {"kind": "activity", "actions": ("like",), "providers": ("sns",)},
    "self-text/1": {"kind": "self_text"},
    "self-summary/1": {"kind": "self_summary"},
}
DEFAULT_POLICY = {"actions": ["like"], "transform_versions": ["board-likes/1", "self-text/1", "self-summary/1"], "axes": []}
SUMMARY_TOOL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")    # 신고 도구 코드(예 claude-cli) — 짧은 코드만
SUMMARY_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")   # 신고 모델 코드 또는 unknown

TOKEN_DAYS = 90              # 장치 토큰 만료
PENDING_DAYS = 30            # 승인 대기 보관
MAX_DEVICES = 5              # 활성 장치(연결 해제 안 된 device 입력원)
MAX_PEOPLE_PER_ACTIVITY = 10
MAX_AXES_PER_ACTIVITY = 10
MAX_SUMMARY_INPUTS = 20
REQ_MAX_BYTES = 64 * 1024    # 요청 본문
ACTIVITY_MAX_BYTES = 8 * 1024
BODY_MAX_CP, BODY_MAX_BYTES = 8000, 32 * 1024
TITLE_MAX_CP = 200
# 한도 (scope, period, bucket) → (건수, 바이트 또는 None). 같은 요청 재전송은 0, 실패·중복도 요청 속도엔 센다
LIMITS = {("device", "minute", "request"): (10, None), ("user", "minute", "request"): (30, None),
          ("device", "day", "self"): (10, 128 * 1024), ("user", "day", "self"): (20, 256 * 1024),
          ("device", "day", "activity"): (500, 1024 * 1024), ("user", "day", "activity"): (1000, 2 * 1024 * 1024)}
PENDING_MAX, PENDING_MAX_BYTES = 100, 1024 * 1024
BUCKETS = ("request", "self", "activity")
# PR ②-a 작업 전달(2026-10-22, 구현계획 §5 · r2 R1) — 폴링은 별도 버킷(업무 요청·쓰기 버킷과 분리). 장치 /result·/fail 은 위 request 버킷.
POLL_LIMITS = {("device", "minute"): 4, ("user", "minute"): 12}   # GET /api/device/jobs(장치·사용자) · GET /api/jobs/<id>(사용자)
POLL_CONCURRENT = {"device": 1, "web": 2}                         # 동시 롱폴(장치당 · 사용자당 웹) — 서버 프로세스 메모리
POLL_WAIT_MAX = 30                                                # 롱폴 최대 초
# 사용자 하루 접수 한도(작업 종류별). K3 원작자 결정(2026-10-22): 일일 한도 없음 → None = 무제한(카운터 job_<kind> 는 관측용으로 센다).
# None 의 뜻은 여기 등록된 작업 종류의 일일 요청·시도 한도에만 — 키가 빠진 종류 = 설정 누락 LIMIT-UNSET(fail-closed), 모르는 kind = ARG-INVALID.
# 분당 버킷·폴링·진행 중 상한·임시 용량·기록 보관 한도(db.DAILY_LIMITS)에는 이 뜻을 옮기지 않는다.
JOB_REQUEST_LIMITS = {"summarize_reading": None, "converse": None, "routine_ingest": None}
# PC 시도(입력 조회 성공 = CLI 시작 직전) 기록은 jobs.attempts. 실제 CLI 재시도는 PC 가 결과 model_meta 에 따로 싣는다
JOBS_OPEN_MAX = 20                                                # 사용자당 살아 있는 작업(queued·leased·running·result_ready) — 가안(원작자 결정 남음)
JOB_BUCKETS = ("poll",) + tuple("job_" + k for k in JOB_REQUEST_LIMITS)   # import_counters.bucket 추가 열거
ALIAS_PREFIX = "사람-"        # 장치가 만든 사람의 기본 별칭(사람-0001). 실명에서 만들지 않는다
SOURCE_NAMES = {"sns": "SNS 활동", "web": "웹 활동", "mail": "메일 활동", "kakao": "카카오톡 활동"}   # 활동 일반 출처 이름(서버가 정한다)
RECORD_SOURCE_TEXT = "연결 장치에서 가져옴"

AI_APPROVE_NOTICE = "AI 요약을 보관합니다. 승인해도 직접 작성한 원문이나 인용 가능한 기록으로 바뀌지 않습니다."

_ID_RES = {
    "source_instance_id": re.compile(r"^is_[0-9a-f]{24}$"),
    "original_id": re.compile(r"^o_[0-9a-f]{32}$"),
    "revision": re.compile(r"^v_[0-9a-f]{32}$"),
}
_RAW_RE = re.compile(r"^[0-9a-f]{48}$")
_HMAC_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ACT_ID_RE = re.compile(r"^a_[A-Za-z0-9_-]{6,40}$")
_ITEM_ID_RE = re.compile(r"^i_[A-Za-z0-9_-]{4,40}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")
_TOKEN_RE = re.compile(r"^Bearer imp_(is_[0-9a-f]{24})\.(it_[0-9a-f]{16})\.([A-Za-z0-9_-]{43})$")
_KEY_RE = re.compile(r"^ci1_[0-9a-f]{64}$")
# 문자열 필드에서 막는 꼴 — URL·이메일·명백한 로컬 경로. 실명 탐지는 하지 않는다(활동은 자유 본문 필드 자체가 없다)
# 에이전트 agent/contract.py _BLOCK_PATTERNS 와 같은 규칙·순서(검토 반영 2026-10-20) — 공용 벡터가 둘을 같이 묶는다
LEAK_RULES = (("url", re.compile(r"(?i)\b(?:https?|ftp|file|wss?)://")),
              ("url", re.compile(r"(?i)\bwww\.[a-z0-9-]+\.[a-z]{2,}")),
              ("url", re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|kr|co|me|ai|app|dev|so)/\S")),
              ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
              ("path", re.compile(r"(?:^|[\s(\"'])(?:~/|/Users/|/home/|/private/|/var/|/Volumes/)")),
              ("path", re.compile(r"(?:^|[\s(\"'])[A-Za-z]:\\")),
              ("path", re.compile(r"\\\\[A-Za-z0-9._-]+\\")))

COMMON = ("contract", "schema_revision", "source_instance_id", "original_id", "revision", "previous_revision", "kind", "execution",
          "transform_version", "policy_revision")
# 종류별 (필수, 선택) 추가 키
KIND_KEYS = {
    "self_text": (("body",), ("title", "source_created_at", "source_date", "activity_ref", "generation")),
    "activity": (("action", "state", "occurred_on", "observed_on", "provider", "raw_id"), ("people", "axes")),   # observed_on = 필수 키·null 허용
    "self_summary": (("body", "generation"), ("title", "source_created_at", "source_date")),
}

_HTTP = {"AUTH-INVALID": 401, "TOKEN-EXPIRED": 401, "TOKEN-REVOKED": 401, "AUTH-BLOCKED": 403,
         "IMPORTS-OFF": 503, "ACTIVITY-IMPORTS-OFF": 503, "SUMMARY-IMPORTS-OFF": 503, "APPROVALS-OFF": 503, "MIGRATION-OFF": 503,
         "PAYLOAD-LIMIT": 413, "CONTRACT-INVALID": 400, "IDEMPOTENCY-KEY-MISMATCH": 400, "ARG-INVALID": 400, "REF-NOT-FOUND": 400,
         "POLICY-DENIED": 403, "STREAM-MISMATCH": 403, "POLICY-STALE": 409, "IDEMPOTENCY-CONFLICT": 409,
         "SOURCE-REVISION-CONFLICT": 409, "KIND-CHANGED": 409, "CONFLICT": 409, "DEVICE-LIMIT": 409, "DEVICE-REVOKED": 409,
         "INBOX-RESOLVED": 409, "INBOX-NOT-LATEST": 409, "INBOX-EXPIRED": 409,
         "RATE-LIMIT": 429, "PEOPLE-QUOTA": 429, "IMPORT-QUOTA": 429, "TARGET-REVISION-CONFLICT": 409, "MIGRATION-WINDOW-CLOSED": 409, "QUOTA-GLOBAL": 507, "INBOX-FULL": 429, "NOT-FOUND": 404, "NAME-REQUIRED": 403}


def http_status(code: str) -> int:
    """CrmError code → HTTP 상태. 모르는 code = 400."""
    return _HTTP.get(code, 400)


def next_kst_midnight() -> str:
    """다음 KST 0시(ISO, +09:00) — 일일 한도에 막혔을 때 재개 가능 시각."""
    kst = _dt.timezone(_dt.timedelta(hours=9))
    d = _dt.datetime.now(kst).date() + _dt.timedelta(days=1)
    return _dt.datetime(d.year, d.month, d.day, tzinfo=kst).isoformat()


def _with_resume(e):
    """일일 한도 오류에 resume_at(다음 KST 0시) 속성을 단다 — 메시지·action 엔 값을 넣지 않는다."""
    e.resume_at = next_kst_midnight()
    return e


def _bad(field: str, rule: str):
    """계약 위반 — 필드 경로·규칙 이름만(값은 넣지 않는다)."""
    return CrmError("CONTRACT-INVALID", f"{field}: {rule}", "요청을 계약(canon-inbox/1 schema_revision 2)에 맞추세요.")


def _off(code, what):
    return CrmError(code, f"{what}이 지금 꺼져 있어요.", "운영자가 켤 때까지 기다리세요. 받은 자료는 장치에 남아 있어요.")


# ── 계약 · 정규화 ───────────────────────────────────────

def jcs(value) -> bytes:
    """JCS(RFC 8785) 정규 직렬화 — 이 계약 범위(정수·문자열·bool·null·객체·배열, 실수 없음, 키는 ASCII)에서
    json.dumps(sort_keys, separators=(',', ':'), ensure_ascii=False) 와 같다. 실수·고아 서러게이트는 계약 검증에서 먼저 거절한다."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def idempotency_key(source_instance_id: str, original_id: str, revision: str) -> str:
    """ci1_ + SHA256(JCS(["canon-inbox/1", 2, source_instance_id, original_id, revision])) 소문자 hex."""
    return "ci1_" + sha256_hex(jcs([CONTRACT, SCHEMA_REVISION, source_instance_id, original_id, revision]))


def _no_dup(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _bad("$", "duplicate_key")
        d[k] = v
    return d


def _no_float(_s):
    raise _bad("$", "float_not_allowed")


def _no_const(_s):
    raise _bad("$", "nan_not_allowed")


def parse_request(raw: bytes) -> dict:
    """요청 본문 → dict. UTF-8 · JSON 객체 하나 · 중복 키 금지 · 실수 금지. 실패 = CrmError CONTRACT-INVALID(field, rule 만 — 값 없음)."""
    if not isinstance(raw, (bytes, bytearray)):
        raise _bad("$", "bytes_required")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise _bad("$", "utf8")
    if text.startswith("﻿"):
        raise _bad("$", "bom")
    try:
        v = json.loads(text, object_pairs_hook=_no_dup, parse_float=_no_float, parse_constant=_no_const)
    except CrmError:
        raise
    except (ValueError, RecursionError):
        raise _bad("$", "json")
    if not isinstance(v, dict):
        raise _bad("$", "object_required")
    return v


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _text(v, path, max_cp, max_bytes=None, single_line=False):
    if not isinstance(v, str):
        raise _bad(path, "string_required")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in v) or "\x00" in v:
        raise _bad(path, "invalid_char")
    if not v.strip():
        raise _bad(path, "empty")
    if len(v) > max_cp:
        raise _bad(path, "too_long")
    if max_bytes is not None and len(v.encode("utf-8")) > max_bytes:
        raise _bad(path, "too_long_bytes")
    if single_line and any(ch in v for ch in "\r\n  "):
        raise _bad(path, "single_line")
    for rule, rx in LEAK_RULES:
        if rx.search(v):
            raise _bad(path, f"{rule}_not_allowed")


def _date(v, path, nullable=True):
    if v is None and nullable:
        return None
    if not isinstance(v, str) or not _DATE_RE.match(v):
        raise _bad(path, "date_required")
    try:
        d = _dt.date.fromisoformat(v)
    except ValueError:
        raise _bad(path, "date_invalid")
    if not (1900 <= d.year <= 2100):
        raise _bad(path, "date_range")
    return d


def _ts(v, path):
    if v is None:
        return None
    if not isinstance(v, str) or not _TS_RE.match(v):
        raise _bad(path, "timestamp_with_offset_required")
    try:
        t = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise _bad(path, "timestamp_invalid")
    if not (1900 <= t.year <= 2100):
        raise _bad(path, "timestamp_range")
    return t


def _exact_keys(obj, path, required, optional=()):
    if not isinstance(obj, dict):
        raise _bad(path, "object_required")
    for k in obj:
        if k not in required and k not in optional:
            raise _bad(f"{path}.{k}" if path != "$" else k, "unknown_field")
    for k in required:
        if k not in obj:
            raise _bad(f"{path}.{k}" if path != "$" else k, "required")


def _generation(kind, g):
    if kind == "self_text":
        _exact_keys(g, "generation", ("method",))
        if g["method"] != "none":
            raise _bad("generation.method", "enum")
        return
    _exact_keys(g, "generation", ("method", "tool", "model", "input_scope", "inputs"))
    if g["method"] != "ai_summary":
        raise _bad("generation.method", "enum")
    if not isinstance(g["tool"], str) or not SUMMARY_TOOL_RE.match(g["tool"]):
        raise _bad("generation.tool", "code")
    if not isinstance(g["model"], str) or not SUMMARY_MODEL_RE.match(g["model"]):
        raise _bad("generation.model", "code")
    if g["input_scope"] != "self_only":
        raise _bad("generation.input_scope", "enum")
    ins = g["inputs"]
    if not isinstance(ins, list) or not 1 <= len(ins) <= MAX_SUMMARY_INPUTS:
        raise _bad("generation.inputs", "list_1_20")
    for i, x in enumerate(ins):
        p = f"generation.inputs[{i}]"
        _exact_keys(x, p, ("original_id", "revision", "sha256"))
        if not isinstance(x["original_id"], str) or not _ID_RES["original_id"].match(x["original_id"]):
            raise _bad(f"{p}.original_id", "format")
        if not isinstance(x["revision"], str) or not _ID_RES["revision"].match(x["revision"]):
            raise _bad(f"{p}.revision", "format")
        if not isinstance(x["sha256"], str) or not _SHA_RE.match(x["sha256"]):
            raise _bad(f"{p}.sha256", "format")


def validate(req: dict) -> dict:
    """canon-inbox/1 schema_revision 2 검증(서버·에이전트 공용 벡터 contract/imports/vectors/*.json 와 같은 판정).
    통과하면 req 그대로 반환. 실패 = CrmError("CONTRACT-INVALID", "<필드 경로>: <규칙>") — 값은 넣지 않는다."""
    if not isinstance(req, dict):
        raise _bad("$", "object_required")
    kind = req.get("kind")
    if kind not in KINDS:
        raise _bad("kind", "enum")
    req_k, opt_k = KIND_KEYS[kind]
    _exact_keys(req, "$", COMMON + req_k, opt_k)
    if req["contract"] != CONTRACT:
        raise _bad("contract", "const")
    if not _is_int(req["schema_revision"]) or req["schema_revision"] != SCHEMA_REVISION:
        raise _bad("schema_revision", "const")
    for k, rx in _ID_RES.items():
        if not isinstance(req[k], str) or not rx.match(req[k]):
            raise _bad(k, "format")
    pr = req["previous_revision"]
    if pr is not None and (not isinstance(pr, str) or not _ID_RES["revision"].match(pr)):
        raise _bad("previous_revision", "format")
    if pr == req["revision"]:
        raise _bad("previous_revision", "same_as_revision")
    if req["execution"] not in EXECUTIONS:
        raise _bad("execution", "enum")
    tv = req["transform_version"]
    if not isinstance(tv, str) or tv not in TRANSFORMS:
        raise _bad("transform_version", "unregistered")
    if TRANSFORMS[tv]["kind"] != kind:
        raise _bad("transform_version", "kind_mismatch")
    if not _is_int(req["policy_revision"]) or req["policy_revision"] < 1:
        raise _bad("policy_revision", "positive_int")
    if kind == "activity":
        t = TRANSFORMS[tv]
        if req["action"] not in ACTIONS:
            raise _bad("action", "enum")
        if req["action"] not in t["actions"]:
            raise _bad("action", "not_in_transform")
        if req["state"] not in ACTIVITY_STATES:
            raise _bad("state", "enum")
        _date(req["occurred_on"], "occurred_on")
        _date(req.get("observed_on"), "observed_on")
        if req["provider"] not in PROVIDERS:
            raise _bad("provider", "enum")
        if req["provider"] not in t["providers"]:
            raise _bad("provider", "not_in_transform")
        if not isinstance(req["raw_id"], str) or not _RAW_RE.match(req["raw_id"]):
            raise _bad("raw_id", "format")
        people = req.get("people", [])
        if not isinstance(people, list) or len(people) > MAX_PEOPLE_PER_ACTIVITY:
            raise _bad("people", "list_max_10")
        seen = set()
        for i, x in enumerate(people):
            _exact_keys(x, f"people[{i}]", ("hmac", "role"))
            if not isinstance(x["hmac"], str) or not _HMAC_RE.match(x["hmac"]):
                raise _bad(f"people[{i}].hmac", "format")
            if x["role"] not in PERSON_ROLES:
                raise _bad(f"people[{i}].role", "enum")
            if (x["hmac"], x["role"]) in seen:
                raise _bad(f"people[{i}]", "duplicate")
            seen.add((x["hmac"], x["role"]))
        axes = req.get("axes", [])
        if not isinstance(axes, list) or len(axes) > MAX_AXES_PER_ACTIVITY:
            raise _bad("axes", "list_max_10")
        for i, a in enumerate(axes):
            if not isinstance(a, str) or not _ITEM_ID_RE.match(a):
                raise _bad(f"axes[{i}]", "format")
        if len(set(axes)) != len(axes):
            raise _bad("axes", "duplicate")
        return req
    _text(req["body"], "body", BODY_MAX_CP, BODY_MAX_BYTES)
    if "title" in req:
        _text(req["title"], "title", TITLE_MAX_CP, single_line=True)
    sca, sd = req.get("source_created_at"), req.get("source_date")
    _ts(sca, "source_created_at")
    _date(sd, "source_date")
    if sca is not None and sd is not None:
        raise _bad("source_date", "exclusive_with_source_created_at")
    if kind == "self_text":
        ref = req.get("activity_ref")
        if ref is not None and (not isinstance(ref, str) or not _ACT_ID_RE.match(ref)):
            raise _bad("activity_ref", "format")
        if "generation" in req:
            _generation(kind, req["generation"])
    else:
        _generation(kind, req["generation"])
    return req


def projection(req: dict) -> dict:
    """실제 반영 내용 — 같은 원본의 새 개정이 이것과 같으면 noop. 개정·멱등 필드는 빼고 반영할 값만."""
    if req["kind"] == "activity":
        return {"action": req["action"], "state": req["state"], "occurred_on": req["occurred_on"],
                "observed_on": req.get("observed_on"), "provider": req["provider"], "raw_id": req["raw_id"],
                "people": sorted([x["hmac"], x["role"]] for x in req.get("people", [])),
                "axes": sorted(req.get("axes", []))}
    out = {"kind": req["kind"], "title": req.get("title"), "body": req["body"], "source_created_at": req.get("source_created_at"),
           "source_date": req.get("source_date")}
    if req["kind"] == "self_text":
        out["activity_ref"] = req.get("activity_ref")
    else:
        out["generation"] = req["generation"]
    return out


def _pending_payload(req: dict) -> dict:
    p = {"title": req.get("title"), "body": req["body"], "source_created_at": req.get("source_created_at"),
         "source_date": req.get("source_date"), "execution": req["execution"], "transform_version": req["transform_version"]}
    if req["kind"] == "self_text":
        p["activity_ref"] = req.get("activity_ref")
    else:
        p["generation"] = req["generation"]
    return p


# ── 공통 헬퍼 ───────────────────────────────────────────

def _j(v):
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).hex()
    return v


def _row(d):
    return {k: _j(v) for k, v in d.items()} if d else d


def _nf(what="그 항목"):
    return CrmError("NOT-FOUND", f"{what}을 찾지 못했어요.", "목록을 다시 보고 id 를 확인하세요.")


def _d(v):
    return _dt.date.fromisoformat(v) if v else None


_DEV_COLS = "id,label,created_at,token_expires_at,last_used_at,revoked_at,token_revoked_at,policy,policy_revision,hmac_namespace"


def _dev_out(r):
    d = _row(r)
    d["active"] = r["revoked_at"] is None
    return d


def _new_token(stream_id):
    tid = "it_" + secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    return tid, hashlib.sha256(secret.encode("ascii")).digest(), f"imp_{stream_id}.{tid}.{secret}"


def switches() -> dict:
    """스위치 5개 현재 값(웹 표시용)."""
    return {"imports": switch_on("IMPORTS_ENABLED"), "activity": switch_on("ACTIVITY_IMPORTS_ENABLED"),
            "unattended_summary": switch_on("UNATTENDED_SUMMARY_IMPORTS_ENABLED"), "inbox_approvals": switch_on("INBOX_APPROVALS_ENABLED"),
            "board_migration": switch_on("BOARD_MIGRATION_ENABLED")}


def _axes_split(c, uid, ids):
    """ids → (분류 항목 아님 첫 id 또는 None, 휴지통 축 id 집합). 분류 역할 오브젝트의 적용·미보관 항목이면 분류 항목이고,
    그중 휴지통(trashed_at)인 것은 따로 돌려준다 — 「없는 id」 와 「삭제한 축」 을 구별한다(휴지통 PR-1, r4 §7)."""
    if not ids:
        return None, set()
    oid = db._classify_target(c, uid)
    if not oid:
        return ids[0], set()
    rows = {r["id"]: r["live"] for r in _all(c, "SELECT id, trashed_at IS NULL AS live FROM items WHERE user_id=%s AND object_id=%s "
                                               "AND id = ANY(%s) AND revision > 0 AND archived_at IS NULL", (uid, oid, list(ids)))}
    bad = next((i for i in ids if i not in rows), None)
    return bad, {i for i, live in rows.items() if not live}


def _norm_policy(c, uid, policy):
    if not isinstance(policy, dict) or set(policy) != {"actions", "transform_versions", "axes"}:
        raise CrmError("ARG-INVALID", "허용 규칙 키는 actions·transform_versions·axes 셋이에요.")
    acts, tvs, axes = policy["actions"], policy["transform_versions"], policy["axes"]
    if not isinstance(acts, list) or any(a not in ACTIONS for a in acts) or len(set(acts)) != len(acts):
        raise CrmError("ARG-INVALID", f"actions 는 {', '.join(ACTIONS)} 중에서 골라요.")
    if not isinstance(tvs, list) or any(t not in TRANSFORMS for t in tvs) or len(set(tvs)) != len(tvs):
        raise CrmError("ARG-INVALID", "transform_versions 는 등록된 변환 버전만 골라요.")
    if (not isinstance(axes, list) or len(axes) > MAX_AXES_PER_ACTIVITY
            or any(not isinstance(a, str) or not _ITEM_ID_RE.match(a) for a in axes) or len(set(axes)) != len(axes)):
        raise CrmError("ARG-INVALID", "axes 는 분류 항목 id 목록(최대 10)이에요.")
    bad, trashed = _axes_split(c, uid, axes)
    if bad:
        raise CrmError("ARG-INVALID", "axes 에 분류(축) 항목이 아닌 id 가 있어요.", "definitions_get 으로 분류 오브젝트의 항목을 확인하세요.")
    if trashed:   # 휴지통 축을 정책에 새로 넣지 않는다 — 축 이동(core)과 같은 사용자 잠금 안이라 경합하지 않는다
        raise CrmError("ARG-INVALID", "axes 에 휴지통에 있는 축이 있어요.", "축을 복원하거나 정책에서 빼세요.")
    return {"actions": list(acts), "transform_versions": list(tvs), "axes": list(axes)}


# ── 연결 장치 (web #_devices) ─────────────────────────────
# 장치 dict = {id, label, created_at, token_expires_at, last_used_at, revoked_at, token_revoked_at, policy, policy_revision,
#             hmac_namespace, active(bool)}. 시각은 ISO 문자열. 토큰 원문·해시는 넣지 않는다.

def device_list(uid: str) -> dict:
    """{"devices": [장치…(최근 순)], "active": n, "max": MAX_DEVICES, "switches": {"imports", "activity",
    "unattended_summary", "inbox_approvals", "board_migration"}(bool)}."""
    with tx() as c:
        rows = _all(c, f"SELECT {_DEV_COLS} FROM import_streams WHERE user_id=%s AND channel='device' ORDER BY created_at DESC, id DESC",
                    (uid,))
    devs = [_dev_out(r) for r in rows]
    return {"devices": devs, "active": sum(1 for d in devs if d["active"]), "max": MAX_DEVICES, "switches": switches()}


def _dev_lock(c, uid, device_id):
    r = _one(c, f"SELECT {_DEV_COLS} FROM import_streams WHERE user_id=%s AND id=%s AND channel='device' FOR NO KEY UPDATE",
             (uid, str(device_id or "")))
    if not r:
        raise _nf("그 장치")
    return r


def _dev_get(c, uid, device_id):
    return _dev_out(_one(c, f"SELECT {_DEV_COLS} FROM import_streams WHERE user_id=%s AND id=%s", (uid, device_id)))


def device_connect(uid: str, label: str) -> dict:
    """장치 연결 — 입력원(device) + 토큰 1개. 활성 5개 넘으면 DEVICE-LIMIT. policy = DEFAULT_POLICY.
    반환 {"device": 장치, "token": "imp_<stream>.<token_id>.<secret>"} — 토큰은 이 응답에서 한 번만 보인다. 감사 device_connect."""
    label = db._label(label, "장치 이름")
    with tx() as c:
        db._user_lock(c, uid)
        n = _one(c, "SELECT count(*) AS n FROM import_streams WHERE user_id=%s AND channel='device' AND revoked_at IS NULL", (uid,))["n"]
        if n >= MAX_DEVICES:
            raise CrmError("DEVICE-LIMIT", f"연결 장치는 {MAX_DEVICES}개까지예요.", "쓰지 않는 장치를 먼저 연결 해제하세요.")
        sid = "is_" + secrets.token_hex(12)
        tid, h, token = _new_token(sid)
        c.execute("INSERT INTO import_streams (user_id,id,channel,label,token_id,secret_hash,token_expires_at,hmac_namespace,policy) "
                  "VALUES (%s,%s,'device',%s,%s,%s,now() + make_interval(days => %s),%s,%s)",
                  (uid, sid, label, tid, h, TOKEN_DAYS, "hn_" + secrets.token_hex(12), Jsonb(DEFAULT_POLICY)))
        db._audit(c, "device_connect", uid, uid, {"device_id": sid, "result": "ok"})
        return {"device": _dev_get(c, uid, sid), "token": token}


def device_reissue(uid: str, device_id: str) -> dict:
    """토큰 재발급 — 옛 토큰 즉시 무효, 만료 90일 새로. 연결 해제된 장치는 DEVICE-REVOKED. 반환 {"device", "token"}. 감사 device_reissue."""
    with tx() as c:
        db._user_lock(c, uid)
        r = _dev_lock(c, uid, device_id)
        if r["revoked_at"] is not None:
            raise CrmError("DEVICE-REVOKED", "연결 해제한 장치예요.", "새 장치로 다시 연결하세요.")
        tid, h, token = _new_token(r["id"])
        c.execute("UPDATE import_streams SET token_id=%s, secret_hash=%s, token_expires_at = now() + make_interval(days => %s), "
                  "token_revoked_at=NULL WHERE user_id=%s AND id=%s", (tid, h, TOKEN_DAYS, uid, r["id"]))
        db._audit(c, "device_reissue", uid, uid, {"device_id": r["id"], "result": "ok"})
        return {"device": _dev_get(c, uid, r["id"]), "token": token}


def device_revoke(uid: str, device_id: str) -> dict:
    """연결 해제 — 토큰 폐기 + revoked_at. 되살리지 않는다(새로 연결). 이미 해제면 그대로 반환(멱등). 이미 받은 활동·기록은 남는다.
    반환 {"device": 장치}. 감사 device_revoke."""
    with tx() as c:
        db._user_lock(c, uid)
        r = _dev_lock(c, uid, device_id)
        if r["revoked_at"] is None:
            c.execute("UPDATE import_streams SET revoked_at=now(), token_revoked_at=COALESCE(token_revoked_at, now()) "
                      "WHERE user_id=%s AND id=%s", (uid, r["id"]))
            db._audit(c, "device_revoke", uid, uid, {"device_id": r["id"], "result": "ok"})
        return {"device": _dev_get(c, uid, r["id"])}


def device_policy_set(uid: str, device_id: str, policy: dict, expected_revision: int) -> dict:
    """허용 규칙 교체 — {"actions": [ACTIONS 부분집합], "transform_versions": [TRANSFORMS 키], "axes": [분류 역할 오브젝트의 항목 id ≤10]}.
    로컬 경로·자유 문자열 없음. policy_revision +1(다르면 CONFLICT). 반환 {"device"}. 감사 device_policy."""
    if not _is_int(expected_revision):
        raise CrmError("ARG-INVALID", "expected_revision 은 정수예요.")
    with tx() as c:
        db._user_lock(c, uid)
        r = _dev_lock(c, uid, device_id)
        if r["revoked_at"] is not None:
            raise CrmError("DEVICE-REVOKED", "연결 해제한 장치예요.")
        if r["policy_revision"] != expected_revision:
            raise db._conflict(f"지금 revision {r['policy_revision']}")
        pol = _norm_policy(c, uid, policy)
        if pol != r["policy"]:
            c.execute("UPDATE import_streams SET policy=%s, policy_revision=policy_revision+1 WHERE user_id=%s AND id=%s",
                      (Jsonb(pol), uid, r["id"]))
            db._audit(c, "device_policy", uid, uid, {"device_id": r["id"], "result": "ok"})
        return {"device": _dev_get(c, uid, r["id"])}


# ── 수신 (main.py /api/imports) ───────────────────────────

def authenticate(authorization: str) -> dict:
    """Bearer 장치 토큰 → 입력원 {user_id, id, label, policy, policy_revision, hmac_namespace}.
    실패 = AUTH-INVALID·TOKEN-EXPIRED·TOKEN-REVOKED(401)·AUTH-BLOCKED(403). 비밀값은 SHA-256 으로만 비교(상수 시간)."""
    m = _TOKEN_RE.match(authorization or "")
    if not m:
        raise CrmError("AUTH-INVALID", "장치 토큰이 없거나 형식이 틀렸어요.", "장치를 다시 연결하세요.")
    sid, tid, secret = m.groups()
    with tx() as c:
        r = _one(c, "SELECT s.user_id,s.id,s.label,s.secret_hash,s.token_revoked_at,s.revoked_at,s.policy,s.policy_revision,"
                    "s.hmac_namespace,s.channel,u.status,u.blocked_at,(s.token_expires_at <= now()) AS expired "
                    "FROM import_streams s JOIN users u ON u.id=s.user_id WHERE s.token_id=%s", (tid,))
    h = hashlib.sha256(secret.encode("ascii")).digest()
    if not r or r["id"] != sid or r["channel"] != "device" or not _hmac.compare_digest(bytes(r["secret_hash"]), h):
        raise CrmError("AUTH-INVALID", "장치 토큰이 맞지 않아요.", "장치를 다시 연결하세요.")
    if r["revoked_at"] is not None or r["token_revoked_at"] is not None:
        raise CrmError("TOKEN-REVOKED", "이 장치 토큰은 폐기됐어요.", "웹의 「연결 장치」에서 다시 연결하세요.")
    if r["expired"]:
        raise CrmError("TOKEN-EXPIRED", "장치 토큰이 만료됐어요.", "웹의 「연결 장치」에서 토큰을 재발급하세요.")
    if r["status"] != "active":
        raise CrmError("AUTH-INVALID", "사용자를 찾을 수 없어요.")
    if r["blocked_at"] is not None:
        raise CrmError("AUTH-BLOCKED", "이 계정은 지금 쓸 수 없어요.")
    return {k: r[k] for k in ("user_id", "id", "label", "policy", "policy_revision", "hmac_namespace")}


_PERIOD_START = {"minute": "date_trunc('minute', now())", "day": db._KST_TODAY}


def _bump(c, uid, scope_key, period, bucket, n=1, nbytes=0):
    """카운터 +n·+bytes 뒤 {n, bytes}. 같은 트랜잭션 안에서 원자적(upsert)."""
    return _one(c, "INSERT INTO import_counters (user_id,scope_key,period,period_start,bucket,n,bytes) "
                   f"VALUES (%s,%s,%s,{_PERIOD_START[period]},%s,%s,%s) "
                   "ON CONFLICT (user_id,scope_key,period,period_start,bucket) "
                   "DO UPDATE SET n = import_counters.n + EXCLUDED.n, bytes = import_counters.bytes + EXCLUDED.bytes RETURNING n, bytes",
                (uid, scope_key, period, bucket, n, nbytes))


def _rate(dev):
    """요청 속도 — 별도 트랜잭션(뒤에서 실패해도 남는다). 장치 분당 10 · 사용자 분당 30."""
    with tx() as c:
        for scope, key in (("device", dev["id"]), ("user", "user")):
            lim = LIMITS[(scope, "minute", "request")][0]
            if _bump(c, dev["user_id"], key, "minute", "request")["n"] > lim:
                raise CrmError("RATE-LIMIT", f"요청이 너무 잦아요(분당 {lim}회).", "1분 뒤 다시 보내세요.")


def _quota(c, dev, bucket, nbytes):
    """하루 접수 예산(KST) — 장치·사용자. 넘으면 IMPORT-QUOTA(트랜잭션 전체 취소 — 카운터도 되돌아간다)."""
    for scope, key in (("device", dev["id"]), ("user", "user")):
        lim_n, lim_b = LIMITS[(scope, "day", bucket)]
        r = _bump(c, dev["user_id"], key, "day", bucket, 1, nbytes)
        if r["n"] > lim_n or (lim_b is not None and r["bytes"] > lim_b):
            raise CrmError("IMPORT-QUOTA", "오늘 받을 수 있는 양을 넘었어요.", "내일(KST 0시 이후) 다시 보내세요.")


_ENTRY_COLS = ("id,stream_id,original_id,source_revision,previous_entry_id,kind,request_sha256,projection_sha256,state,"
               "is_latest_received,is_current_applied,payload,payload_bytes,expires_at,received_at,resolved_at,resolved_by,"
               "target_object_id,target_item_id,target_record_id,target_activity_id,target_item_revision,transform_version,execution")


def _target(e):
    for kind, col in (("activity", "target_activity_id"), ("record", "target_record_id"), ("item", "target_item_id"),
                      ("object", "target_object_id")):
        if e.get(col):
            return kind, e[col]
    return None, None


def _resp(e, duplicate):
    tk, tid = _target(e)
    return {"import_id": e["id"], "state": e["state"], "target_kind": tk, "target_id": tid, "duplicate": duplicate,
            "received_at": _j(e["received_at"]), "expires_at": _j(e["expires_at"])}


# ── 휴지통 PR-1: 원천 차단 (r4 §3·D11) ─────────────────────
# 원천 = (사용자, 입력원, original_id) 의 모든 판. 그 결과(활동 판 또는 승인한 기록)가 휴지통이면 "trash", 영구 삭제 뒤(접수 행이
# state=deleted 로 남고 대상이 없음)면 "purged". 삭제된 원천의 재전송은 같은 해시 noop·같은 개정 재전송보다 먼저 막는다 —
# 응답 = 200 + state "deleted" + source_deleted, 대상 ID 없음(활성 ID 로 오인 금지). 활동·관계·한도는 만들지 않는다.
# 새 개정이면 대응표에 state=deleted 접수 행 1줄만 남긴다(최신 판 교체 — 복원 뒤 PC 의 다음 개정이 판 체인으로 이어진다).
# PC 는 내용(해시)이 같으면 다시 보내지 않으므로, 복원해도 차단 중 보낸 변경분은 돌아오지 않는다(r4 §3).
DELETED_STATE = "deleted"
SOURCE_DELETED = ("trash", "purged")


def _source_deleted(c, uid, stream_id, original_id):
    """원천 삭제 상태 — None | "trash" | "purged". 쿼리 1번(대응표 UNIQUE 앞부분 인덱스).
    trash = 현재 판(is_current_applied)의 결과가 휴지통 — 활동은 모든 판이 함께 옮겨지고, 승인한 기록은 승인마다 새 기록이라
    옛 판의 기록만 휴지통이면 원천은 살아 있다(현재 결과까지 막지 않는다). purged = 대상 없는 state=deleted 행만 남음(현재 판도 없다)."""
    r = _one(c, "SELECT bool_or(e.is_current_applied AND (a.trashed_at IS NOT NULL OR rec.trashed_at IS NOT NULL "
                "OR it.trashed_at IS NOT NULL)) AS trash, "
                "bool_or(e.state='deleted') AS gone, "
                "bool_or(num_nonnulls(e.target_activity_id, e.target_record_id, e.target_item_id, e.target_object_id) > 0) AS has_target "
                "FROM import_entries e LEFT JOIN activities a ON a.user_id=e.user_id AND a.id=e.target_activity_id "
                "LEFT JOIN records rec ON rec.user_id=e.user_id AND rec.id=e.target_record_id "
                "LEFT JOIN items it ON it.user_id=e.user_id AND it.id=e.target_item_id "
                "WHERE e.user_id=%s AND e.stream_id=%s AND e.original_id=%s", (uid, stream_id, original_id))
    if r and r["trash"]:
        return "trash"
    if r and r["gone"] and not r["has_target"]:
        return "purged"
    return None


def _target_trashed(c, uid, e):
    """접수 행 e 의 결과(기록·항목·활동)가 휴지통인가 — 같은 개정 재전송 때만(쿼리 1번)."""
    tk, tid = _target(e)
    if tk not in ("record", "item", "activity"):
        return False
    t = {"record": "records", "item": "items", "activity": "activities"}[tk]
    return bool(_one(c, f"SELECT 1 FROM {t} WHERE user_id=%s AND id=%s AND trashed_at IS NOT NULL", (uid, tid)))


def _deleted_resp(e, gone, duplicate):
    """삭제된 원천 응답 — 대상 종류·ID 는 항상 null. e = 이 전송의 접수 행(없으면 None)."""
    return {"import_id": e["id"] if e else None, "state": DELETED_STATE, "target_kind": None, "target_id": None, "duplicate": duplicate,
            "received_at": _j(e["received_at"]) if e else None, "expires_at": None, "source_deleted": gone}


def _receive_deleted(c, dev, req, same, gone, req_sha, proj_sha):
    """삭제된 원천에 온 전송 — 같은 개정 재전송이면 그 행으로(duplicate), 판 체인이 맞는 새 개정이면 state=deleted 접수 행 1줄.
    체인이 어긋나면 행 없이 응답만(import_id null). 활동·사람·관계·한도 0."""
    uid = dev["user_id"]
    if same:
        return _deleted_resp(same, gone, True)
    latest = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                     "AND is_latest_received FOR UPDATE", (uid, dev["id"], req["original_id"]))
    e = None
    if latest is not None and req["previous_revision"] == latest["source_revision"] and latest["kind"] == req["kind"]:
        c.execute("UPDATE import_entries SET is_latest_received=false WHERE user_id=%s AND id=%s", (uid, latest["id"]))
        e = _insert_entry(c, dev, req, latest, req_sha, proj_sha, DELETED_STATE, False)
    return _deleted_resp(e, gone, False)


def _source_for(c, uid, provider):
    """활동 일반 출처 — (사용자, 제공자) 하나. 이름은 서버가 정한 일반 이름(사용자가 같은 이름 출처를 이미 쓰면 뒤에 번호)."""
    r = _one(c, "SELECT id FROM sources WHERE user_id=%s AND activity_provider=%s", (uid, provider))
    if r:
        return r["id"]
    base = SOURCE_NAMES[provider]
    for i in range(1, 50):
        name = base if i == 1 else f"{base} {i}"
        r = _one(c, "INSERT INTO sources (user_id,id,kind,name,location,activity_provider) VALUES (%s,%s,'other',%s,'',%s) "
                    "ON CONFLICT DO NOTHING RETURNING id", (uid, db.new_id("s"), name, provider))
        if r:
            return r["id"]
    raise CrmError("DB-STATE", "활동 출처를 만들지 못했어요.")


def _people_for(c, uid, ns, people):
    """[{hmac, role}] → [(person_id, role)]. 없는 사람은 별칭 「사람-NNNN」 으로 만든다(사람 일일 한도 — 넘으면 RATE-LIMIT, 전체 취소).
    HMAC 이 다르면 다른 사람으로 둔다(동일인 병합 없음). HMAC 없는 기존 사람과 맞추지 않는다.
    휴지통 PR-1(r4 §7): 휴지통 사람·영구 삭제 표식(trashed_at 있음)도 같은 HMAC 으로 찾는다 — 「없음」 으로 보지 않으니 새 사람(UNIQUE 위반·
    「사람-NNNN」 재생성)을 만들지 않는다. 그 사람과의 연결만 빼고 돌려준다(활동은 독립 결과라 저장된다)."""
    if not people:
        return []
    want = {x["hmac"] for x in people}
    rows = _all(c, "SELECT id, identity_hmac, trashed_at IS NULL AS live FROM people WHERE user_id=%s AND hmac_namespace=%s "
                   "AND identity_hmac = ANY(%s)", (uid, ns, [bytes.fromhex(h) for h in want]))
    have = {bytes(r["identity_hmac"]).hex(): r["id"] for r in rows}
    gone = {bytes(r["identity_hmac"]).hex() for r in rows if not r["live"]}
    new = sorted(want - set(have))
    if new:
        try:
            db._daily_gate(c, uid, "people", adding=len(new))
        except CrmError as e:   # 장치 경로에선 분당 한도(RATE-LIMIT)와 구분 — 사람 하루 한도(PEOPLE-QUOTA) + 재개 시각
            if e.code != "RATE-LIMIT":
                raise
            raise _with_resume(CrmError("PEOPLE-QUOTA", e.message, e.action)) from None
        n = _one(c, "SELECT count(*) AS n FROM people WHERE user_id=%s AND identity_hmac IS NOT NULL", (uid,))["n"]
        for i, h in enumerate(new, 1):
            pid = db.new_id("p")
            c.execute("INSERT INTO people (user_id,id,alias,hmac_namespace,identity_hmac) VALUES (%s,%s,%s,%s,%s)",
                      (uid, pid, f"{ALIAS_PREFIX}{n + i:04d}", ns, bytes.fromhex(h)))
            have[h] = pid
    return [(have[x["hmac"]], x["role"]) for x in people if x["hmac"] not in gone]


_INSERT_ENTRY = ("INSERT INTO import_entries (user_id,id,stream_id,original_id,source_revision,previous_entry_id,kind,request_sha256,"
                 "projection_sha256,state,is_latest_received,is_current_applied,payload,payload_bytes,expires_at,resolved_at,"
                 "resolved_by,target_record_id,target_activity_id,transform_version,execution) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s,{expires},{resolved},%s,%s,%s,%s,%s) "
                 f"RETURNING {_ENTRY_COLS}")


def _insert_entry(c, dev, req, latest, req_sha, proj_sha, state, current, payload=None, pbytes=0, record_id=None, activity_id=None):
    pending = state == "pending"
    sql = _INSERT_ENTRY.format(expires=f"now() + make_interval(days => {PENDING_DAYS})" if pending else "NULL",
                               resolved="NULL" if pending else "now()")
    return _one(c, sql, (dev["user_id"], db.new_id("ie"), dev["id"], req["original_id"], req["revision"],
                         latest["id"] if latest else None, req["kind"], req_sha, proj_sha, state, current,
                         Jsonb(payload) if payload is not None else None, pbytes, None if pending else "system",
                         record_id, activity_id, req["transform_version"], req["execution"]))


def _apply_activity(c, dev, req, latest, current, req_sha, proj_sha, nbytes):
    uid = dev["user_id"]
    _quota(c, dev, "activity", nbytes)
    if latest:
        c.execute("UPDATE import_entries SET is_latest_received=false WHERE user_id=%s AND id=%s", (uid, latest["id"]))
    if current:
        c.execute("UPDATE import_entries SET is_current_applied=false WHERE user_id=%s AND id=%s", (uid, current["id"]))
    if current and bytes(current["projection_sha256"]) == proj_sha:
        return _insert_entry(c, dev, req, latest, req_sha, proj_sha, "noop", True, activity_id=current["target_activity_id"])
    bad, gone_axes = _axes_split(c, uid, req.get("axes", []))
    if bad:
        raise CrmError("POLICY-DENIED", "axes 에 분류(축) 항목이 아닌 id 가 있어요.")
    src = _source_for(c, uid, req["provider"])
    links = _people_for(c, uid, dev["hmac_namespace"], req.get("people", []))
    aid = db.new_id("a")
    c.execute("INSERT INTO activities (user_id,id,stream_id,source_id,action,state,occurred_on,observed_on,raw_id) "
              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (uid, aid, dev["id"], src, req["action"], req["state"], _d(req["occurred_on"]), _d(req.get("observed_on")), req["raw_id"]))
    for pid, role in links:
        c.execute("INSERT INTO activity_links (user_id,id,activity_id,person_id,relation) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                  (uid, db.new_id("al"), aid, pid, role))
    for ax in [a for a in req.get("axes", []) if a not in gone_axes]:   # 휴지통 축 = 그 연결만 뺀다(활동을 영구 격리시키지 않는다)
        c.execute("INSERT INTO activity_links (user_id,id,activity_id,axis_item_id,relation) VALUES (%s,%s,%s,%s,'axis') "
                  "ON CONFLICT DO NOTHING", (uid, db.new_id("al"), aid, ax))
    return _insert_entry(c, dev, req, latest, req_sha, proj_sha, "applied", True, activity_id=aid)


def _receive_self(c, dev, req, latest, current, req_sha, proj_sha):
    uid = dev["user_id"]
    payload = _pending_payload(req)
    if req["kind"] == "self_text" and req.get("activity_ref"):
        # 휴지통 PR-1 — 삭제한 활동을 가리켜도 내 글(독립 결과)은 받는다(의존 단위: ref 연결만 뺀다, PC 격리 0).
        # 휴지통 활동 = ref 그대로(승인 전에 복원될 수 있다 — 연결은 승인 때 살아 있는 활동에만, inbox_approve)
        # · 영구 삭제한 활동(대응표 deleted_target_id 표식) = ref 를 빼고 받는다 · 남의·없는 ID = 지금처럼 REF-NOT-FOUND
        r = _one(c, "SELECT EXISTS (SELECT 1 FROM activities WHERE user_id=%s AND id=%s AND deleted_at IS NULL) AS kept, "
                    "EXISTS (SELECT 1 FROM import_entries WHERE user_id=%s AND deleted_target_id=%s) AS purged",
                 (uid, req["activity_ref"], uid, req["activity_ref"]))
        if not r["kept"] and not r["purged"]:
            raise CrmError("REF-NOT-FOUND", "activity_ref 가 가리키는 활동이 없어요.")
        if not r["kept"]:
            payload["activity_ref"] = None
    pbytes = len(jcs(payload))
    _quota(c, dev, "self", pbytes)
    if latest:
        c.execute("UPDATE import_entries SET is_latest_received=false WHERE user_id=%s AND id=%s", (uid, latest["id"]))
    # 같은 원본의 옛 대기 개정은 승인할 수 없다 — 제외(superseded)로 닫고 본문을 지운다. 이미 승인한 판은 그대로
    c.execute("UPDATE import_entries SET state='excluded', payload=NULL, payload_bytes=0, resolved_at=now(), resolved_by='superseded' "
              "WHERE user_id=%s AND stream_id=%s AND original_id=%s AND state='pending'", (uid, dev["id"], req["original_id"]))
    if current and bytes(current["projection_sha256"]) == proj_sha:   # 승인한 판과 반영 내용이 같다 — 대기 없이 noop
        c.execute("UPDATE import_entries SET is_current_applied=false WHERE user_id=%s AND id=%s", (uid, current["id"]))
        return _insert_entry(c, dev, req, latest, req_sha, proj_sha, "noop", True, record_id=current["target_record_id"])
    p = _one(c, "SELECT count(*) AS n, COALESCE(sum(payload_bytes), 0) AS b FROM import_entries WHERE user_id=%s AND state='pending' "
                "AND expires_at > now()", (uid,))   # 만료됐지만 아직 파기 전(expire_pending 은 하루 1회)인 행은 세지 않는다
    if p["n"] + 1 > PENDING_MAX or p["b"] + pbytes > PENDING_MAX_BYTES:
        raise CrmError("INBOX-FULL", "승인 대기가 가득 찼어요.", "웹 「승인 대기」에서 승인하거나 제외한 뒤 다시 보내세요.")
    return _insert_entry(c, dev, req, latest, req_sha, proj_sha, "pending", False, payload=payload, pbytes=pbytes)


def submit(authorization: str, idempotency_key_header: str, raw: bytes) -> tuple[int, dict]:
    """POST /api/imports 한 건. 순서: 스위치(IMPORTS) → 토큰 → 요청 속도(별도 트랜잭션, 실패도 센다) → 크기·파싱·계약 →
    Idempotency-Key 일치 → 장치 policy → 종류 스위치 → (사용자 잠금 한 트랜잭션) 멱등·개정·한도·적용.
    반환 (HTTP 상태, 본문). 신규 201 · 같은 요청 200:
      {"import_id", "state": applied|noop|pending, "target_kind": "activity"|"record"|None, "target_id", "duplicate": bool,
       "received_at", "expires_at"}
    삭제된 원천(휴지통·영구 삭제)이면 200 + _deleted_resp 꼴({..., "state": "deleted", "target_kind": None, "target_id": None,
      "source_deleted": "trash"|"purged"}) — 새 4xx 없음.
    실패는 CrmError — main.py 가 http_status(code) 와 {"code", "msg"} 로 바꾼다(본문·값을 되돌려 주지 않는다)."""
    if not switch_on("IMPORTS_ENABLED"):
        raise _off("IMPORTS-OFF", "가져오기")
    dev = authenticate(authorization)
    _rate(dev)
    db.check_global_quota(dev["user_id"])   # ③ 전역 DB 크기(웹·MCP 쓰기와 같은 관문 — 관리자만 예외)
    if len(raw) > REQ_MAX_BYTES:
        raise CrmError("PAYLOAD-LIMIT", f"요청 본문은 {REQ_MAX_BYTES} 바이트까지예요.")
    req = validate(parse_request(raw))
    kind = req["kind"]
    if kind == "activity" and len(raw) > ACTIVITY_MAX_BYTES:
        raise CrmError("PAYLOAD-LIMIT", f"활동 요청은 {ACTIVITY_MAX_BYTES} 바이트까지예요.")
    if req["source_instance_id"] != dev["id"]:
        raise CrmError("STREAM-MISMATCH", "source_instance_id 가 이 장치 토큰과 달라요.")
    if not _KEY_RE.match(idempotency_key_header or "") or \
            idempotency_key_header != idempotency_key(req["source_instance_id"], req["original_id"], req["revision"]):
        raise CrmError("IDEMPOTENCY-KEY-MISMATCH", "Idempotency-Key 가 요청과 맞지 않아요.", "계약의 키 계산식으로 다시 만드세요.")
    pol = dev["policy"] or {}
    if req["policy_revision"] != dev["policy_revision"]:
        raise CrmError("POLICY-STALE", "장치 허용 규칙이 바뀌었어요.", "장치에서 허용 규칙을 다시 받아 오세요.")
    if req["transform_version"] not in pol.get("transform_versions", []):
        raise CrmError("POLICY-DENIED", "이 장치에 허용하지 않은 변환 버전이에요.")
    if kind == "activity":
        if req["action"] not in pol.get("actions", []):
            raise CrmError("POLICY-DENIED", "이 장치에 허용하지 않은 행동이에요.")
        if any(a not in pol.get("axes", []) for a in req.get("axes", [])):
            raise CrmError("POLICY-DENIED", "이 장치에 허용하지 않은 축이에요.")
        if not switch_on("ACTIVITY_IMPORTS_ENABLED"):
            raise _off("ACTIVITY-IMPORTS-OFF", "활동 가져오기")
    if kind == "self_summary" and req["execution"] == "unattended" and not switch_on("UNATTENDED_SUMMARY_IMPORTS_ENABLED"):
        raise _off("SUMMARY-IMPORTS-OFF", "무인 요약 가져오기")
    uid = dev["user_id"]
    req_sha = hashlib.sha256(jcs(req)).digest()
    proj_sha = hashlib.sha256(jcs(projection(req))).digest()
    with tx() as c:
        db._user_lock(c, uid)   # 개정 경합·한도·승인·폐기·계정 삭제와 같은 잠금 순서(users → 입력원 → 접수 행)
        s = _one(c, "SELECT revoked_at, token_revoked_at, token_expires_at <= now() AS expired, policy_revision FROM import_streams "
                    "WHERE user_id=%s AND id=%s FOR NO KEY UPDATE", (uid, dev["id"]))
        if not s or s["revoked_at"] is not None or s["token_revoked_at"] is not None:
            raise CrmError("TOKEN-REVOKED", "이 장치 토큰은 폐기됐어요.")
        if s["expired"]:
            raise CrmError("TOKEN-EXPIRED", "장치 토큰이 만료됐어요.")
        if s["policy_revision"] != req["policy_revision"]:
            raise CrmError("POLICY-STALE", "장치 허용 규칙이 바뀌었어요.")
        same = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                       "AND source_revision=%s", (uid, dev["id"], req["original_id"], req["revision"]))
        gone = _source_deleted(c, uid, dev["id"], req["original_id"])   # 휴지통 PR-1 — 같은 개정·noop 판정보다 먼저
        if not gone and same and _target_trashed(c, uid, same):   # 옛 판 재전송인데 그 판의 결과만 휴지통 — 대상 ID 를 주지 않는다
            gone = "trash"
        if gone:
            out = _receive_deleted(c, dev, req, same, gone, req_sha, proj_sha)
            c.execute("UPDATE import_streams SET last_used_at=now() WHERE user_id=%s AND id=%s", (uid, dev["id"]))
            return 200, out
        if same:
            if bytes(same["request_sha256"]) != req_sha:
                raise CrmError("IDEMPOTENCY-CONFLICT", "같은 개정 ID 로 다른 내용을 보냈어요.", "새 개정 ID 로 보내세요.")
            c.execute("UPDATE import_streams SET last_used_at=now() WHERE user_id=%s AND id=%s", (uid, dev["id"]))
            return 200, _resp(same, True)
        latest = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                         "AND is_latest_received FOR UPDATE", (uid, dev["id"], req["original_id"]))
        if latest is None and req["previous_revision"] is not None:
            raise CrmError("SOURCE-REVISION-CONFLICT", "처음 보내는 원본은 previous_revision 이 null 이어야 해요.")
        if latest is not None:
            if req["previous_revision"] != latest["source_revision"]:
                raise CrmError("SOURCE-REVISION-CONFLICT", "previous_revision 이 서버의 최신 개정과 달라요.", "최신 개정 뒤로 다시 보내세요.")
            if latest["kind"] != kind:
                raise CrmError("KIND-CHANGED", "같은 원본의 종류는 바꿀 수 없어요.")
        current = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                          "AND is_current_applied FOR UPDATE", (uid, dev["id"], req["original_id"]))
        if kind == "activity":
            e = _apply_activity(c, dev, req, latest, current, req_sha, proj_sha, len(raw))
        else:
            e = _receive_self(c, dev, req, latest, current, req_sha, proj_sha)
        c.execute("UPDATE import_streams SET last_used_at=now() WHERE user_id=%s AND id=%s", (uid, dev["id"]))
        return 201, _resp(e, False)


def device_self(authorization: str) -> dict:
    """GET /api/imports/device — 장치 토큰으로 자기 입력원 확인. {stream_id, label, policy, policy_revision, hmac_namespace,
    token_expires_at}. 다른 장치·원문·사용자 정보 없음."""
    dev = authenticate(authorization)
    _rate(dev)
    with tx() as c:
        r = _one(c, "SELECT token_expires_at FROM import_streams WHERE user_id=%s AND id=%s", (dev["user_id"], dev["id"]))
    return {"stream_id": dev["id"], "label": dev["label"], "policy": dev["policy"], "policy_revision": dev["policy_revision"],
            "hmac_namespace": dev["hmac_namespace"], "token_expires_at": _j(r["token_expires_at"])}


def device_self_revoke(authorization: str) -> dict:
    """POST /api/imports/device/revoke — 장치 토큰으로 자기 토큰만 폐기(연결 해제, 감사 device_revoke). 반환 {stream_id, revoked: true}."""
    dev = authenticate(authorization)
    device_revoke(dev["user_id"], dev["id"])
    return {"stream_id": dev["id"], "revoked": True}


def entry_status(authorization: str, import_id: str) -> dict:
    """GET /api/imports/{import_id} — 같은 장치가 낸 접수 행의 현재 상태(submit 응답 모양, duplicate=true). 다른 장치·사용자 = NOT-FOUND."""
    dev = authenticate(authorization)
    _rate(dev)
    with tx() as c:
        e = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND id=%s",
                 (dev["user_id"], dev["id"], str(import_id or "")))
        gone = e and _source_deleted(c, dev["user_id"], dev["id"], e["original_id"])
    if not e:
        raise _nf("그 접수")
    if gone or e["state"] == DELETED_STATE:   # 삭제된 원천의 접수 행은 대상 ID 를 돌려주지 않는다
        return _deleted_resp(e, gone, True)   # source_deleted null = 차단됐던 전송인데 원천은 그 뒤 복원됐다
    return _resp(e, True)


# ── 승인 대기 (web #_inbox · MCP inbox_list/inbox_get) ─────────────
# 대기 행 dict = {id, kind: self_text|self_summary, state, title, received_at, expires_at, device: {id, label}, execution,
#               source_created_at, source_date, generation(self_summary 만: method·tool·model·input_scope·inputs 수),
#               activity_ref, is_latest(bool), approvable(bool), payload_bytes, resolved_at, resolved_by, target_record_id}.
#               목록엔 body 가 없다(inbox_get 에만). 처리된 행은 본문이 지워져 title 도 None 이다.

INBOX_STATES = ("pending", "applied", "noop", "excluded", "expired", "all")
_INBOX_SQL = ("SELECT e.id,e.kind,e.state,e.payload,e.payload_bytes,e.expires_at,e.received_at AS created,e.received_at,e.resolved_at,"
              "e.resolved_by,e.is_latest_received,e.target_record_id,e.execution,(e.expires_at > now()) AS alive,s.id AS device_id,"
              "s.label AS device_label FROM import_entries e JOIN import_streams s ON s.user_id=e.user_id AND s.id=e.stream_id "
              "WHERE e.user_id=%s AND e.kind IN ('self_text','self_summary') AND e.state <> 'deleted'")   # 휴지통 PR-1: 원천 차단 행은 목록·상세에 없다


def _inbox_out(r, body=False):
    p = r["payload"] or {}
    g = p.get("generation")
    gen = None
    if r["kind"] == "self_summary" and g:
        gen = {k: g[k] for k in ("method", "tool", "model", "input_scope")}
        gen["inputs"] = len(g.get("inputs", []))
    d = {"id": r["id"], "kind": r["kind"], "state": r["state"], "title": p.get("title"), "received_at": _j(r["received_at"]),
         "expires_at": _j(r["expires_at"]), "device": {"id": r["device_id"], "label": r["device_label"]}, "execution": r["execution"],
         "source_created_at": p.get("source_created_at"), "source_date": p.get("source_date"), "generation": gen,
         "activity_ref": p.get("activity_ref"), "is_latest": r["is_latest_received"],
         "approvable": r["state"] == "pending" and r["is_latest_received"] and bool(r["alive"]), "payload_bytes": r["payload_bytes"],
         "resolved_at": _j(r["resolved_at"]), "resolved_by": r["resolved_by"], "target_record_id": r["target_record_id"]}
    if body:
        d["body"] = p.get("body") if r["state"] == "pending" else None
        d["approve_notice"] = AI_APPROVE_NOTICE if r["kind"] == "self_summary" else None
    return d


def inbox_count(uid: str) -> int:
    """승인할 수 있는(최신·pending·만료 전) 대기 수 — 「나」 홈 [승인 대기 N]."""
    with tx() as c:
        return _one(c, "SELECT count(*) AS n FROM import_entries WHERE user_id=%s AND state='pending' AND is_latest_received "
                       "AND expires_at > now()", (uid,))["n"]


def inbox_list(uid: str, state: str = "pending", cursor: str = "", limit: int = 50) -> dict:
    """{"entries": [대기 행…(받은 순 최근 먼저)], "next_cursor", "counts": {"pending": n}}. state ∈ INBOX_STATES."""
    if state not in INBOX_STATES:
        raise CrmError("ARG-INVALID", f"state 는 {', '.join(INBOX_STATES)} 중 하나예요.")
    after, limit = db._page_args(cursor, limit)
    sql, args = _INBOX_SQL, [uid]
    if state != "all":
        sql += " AND e.state=%s"
        args.append(state)
    if after:
        sql += " AND (e.received_at, e.id) < (%s, %s)"
        args += list(after)
    with tx() as c:
        rows, nxt = db._page(_all(c, sql + " ORDER BY e.received_at DESC, e.id DESC LIMIT %s", args + [limit + 1]), limit)
    return {"entries": [_inbox_out(r) for r in rows], "next_cursor": nxt, "counts": {"pending": inbox_count(uid)}}


def inbox_get(uid: str, import_id: str) -> dict:
    """대기 행 + "body"(pending 일 때만, 그 밖엔 None) + "approve_notice"(self_summary 면 AI_APPROVE_NOTICE)."""
    with tx() as c:
        r = _one(c, _INBOX_SQL + " AND e.id=%s", (uid, str(import_id or "")))
    if not r:
        raise _nf("그 승인 대기 글")
    return _inbox_out(r, body=True)


def _inbox_lock(c, uid, import_id):
    r = _one(c, f"SELECT {_ENTRY_COLS},(expires_at > now()) AS alive FROM import_entries WHERE user_id=%s AND id=%s "
                "AND kind IN ('self_text','self_summary') FOR UPDATE", (uid, str(import_id or "")))
    if not r:
        raise _nf("그 승인 대기 글")
    if r["state"] != "pending":
        raise CrmError("INBOX-RESOLVED", "이미 처리한 승인 대기예요.", "목록을 새로 고치세요.")
    return r


def inbox_approve(uid: str, import_id: str) -> dict:
    """승인(웹만) — 스위치 INBOX_APPROVALS 필요. 최신 대기 개정만. 기록 한도(기록 50) 사용. 한 트랜잭션:
    self_text → records(self/verbatim, ingest_kind=inbox) · self_summary → records(unknown/generated, generation_kind=ai, generation_meta)
    · activity_ref 있으면 activity_links(활동 → 기록, relation=reaction) · 접수 행 applied·현재 판 이동·payload 삭제.
    반환 {"import_id", "state": "applied", "record_id"}. 실패하면 전체 롤백."""
    if not switch_on("INBOX_APPROVALS_ENABLED"):
        raise _off("APPROVALS-OFF", "승인 대기 글의 승인")
    with tx() as c:
        db._daily_gate(c, uid, "records")   # users 잠금 + 기록 한도
        e = _inbox_lock(c, uid, import_id)
        if not e["is_latest_received"]:
            raise CrmError("INBOX-NOT-LATEST", "더 새 개정이 있어요. 최신 대기만 승인할 수 있어요.")
        if not e["alive"]:
            raise CrmError("INBOX-EXPIRED", "보관 기간이 지난 승인 대기예요.")
        p = e["payload"]
        body = p["body"]
        title = (p.get("title") or "").strip() or body.strip().splitlines()[0][:40]
        if e["kind"] == "self_text":
            ak, tp, gk, gm = "self", "verbatim", "unknown", {}
        else:
            g = p["generation"]
            ak, tp, gk = "unknown", "generated", "ai"
            gm = {"method": g["method"], "tool": g["tool"], "model": g["model"], "input_scope": g["input_scope"], "inputs": g["inputs"],
                  "transform_version": p["transform_version"], "execution": p["execution"], "reported_by": "device"}
        rid = db.new_id("r")
        hist = [{"at": db.now().isoformat(), "by": "inbox", "author_kind": ak, "text_process": tp}]
        sca = p.get("source_created_at")
        c.execute("INSERT INTO records (id,user_id,title,body,source,author_kind,text_process,provenance_history,ingest_kind,"
                  "generation_kind,generation_meta,source_created_at,source_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'inbox',%s,%s,%s,%s)",
                  (rid, uid, title, body, RECORD_SOURCE_TEXT, ak, tp, Jsonb(hist), gk, Jsonb(gm),
                   _dt.datetime.fromisoformat(sca.replace("Z", "+00:00")) if sca else None, _d(p.get("source_date"))))
        ref = p.get("activity_ref")
        if ref and _one(c, "SELECT 1 FROM activities WHERE user_id=%s AND id=%s AND deleted_at IS NULL AND trashed_at IS NULL", (uid, ref)):
            c.execute("INSERT INTO activity_links (user_id,id,activity_id,linked_record_id,relation) VALUES (%s,%s,%s,%s,'reaction') "
                      "ON CONFLICT DO NOTHING", (uid, db.new_id("al"), ref, rid))
        c.execute("UPDATE import_entries SET is_current_applied=false WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                  "AND is_current_applied", (uid, e["stream_id"], e["original_id"]))
        c.execute("UPDATE import_entries SET state='applied', payload=NULL, payload_bytes=0, resolved_at=now(), resolved_by='user', "
                  "target_record_id=%s, is_current_applied=true WHERE user_id=%s AND id=%s", (rid, uid, e["id"]))
    return {"import_id": e["id"], "state": "applied", "record_id": rid}


def inbox_exclude(uid: str, import_id: str) -> dict:
    """제외(웹만, 스위치 무관) — pending → excluded, payload 삭제. 반환 {"import_id", "state": "excluded"}."""
    with tx() as c:
        db._user_lock(c, uid)
        e = _inbox_lock(c, uid, import_id)
        c.execute("UPDATE import_entries SET state='excluded', payload=NULL, payload_bytes=0, resolved_at=now(), resolved_by='user' "
                  "WHERE user_id=%s AND id=%s", (uid, e["id"]))
    return {"import_id": e["id"], "state": "excluded"}


def counters_purge() -> int:
    """지난 한도 카운터 파기 — 분 버킷은 1시간, 하루 버킷은 35일 지난 행. 서버 시작·매일(main.purge_once). 반환 건수."""
    with tx() as c:
        return c.execute("DELETE FROM import_counters WHERE (period='minute' AND period_start < now() - interval '1 hour') "
                         "OR (period='day' AND period_start < now() - interval '35 days')").rowcount


def expire_pending() -> int:
    """만료 지난 대기 → expired(payload 삭제). 서버 시작·매일(main.purge_once). 반환 건수."""
    with tx() as c:
        return c.execute("UPDATE import_entries SET state='expired', payload=NULL, payload_bytes=0, resolved_at=now(), "
                         "resolved_by='expiry' WHERE state='pending' AND expires_at <= now()").rowcount


# ── board 이전 기본 연산 (migrate_cli.py) ────────────────────
# 행 단위 업무 쓰기·접수 대응표·관계·한도·감사를 한 트랜잭션에(db.tx_scope — 안에서 부르는 기존 db 함수는 savepoint 로 합쳐진다).
# 스위치 BOARD_MIGRATION 필요. 병행 기간은 첫 업무 적용 트랜잭션에서 시작(14일), 원작자 결정으로 7일 단위 연장, 종료 뒤엔 다시 열지 않는다.
# 명세에서 빠지거나 원본에서 지워졌다고 서버에서 지우지 않는다. 숨김·보관된 대상을 몰래 되살리지 않는다(TARGET-REVISION-CONFLICT).

MIGRATION_DAYS = 14
MIGRATION_EXTEND_STEP = 7
_DECISION_ACTIONS = {"confirmed": "confirm", "excluded": "exclude", "pending": "pending"}
_MIG_COLS = "id,label,migration_started_at,migration_ends_at,migration_closed_at,final_manifest_sha256,created_at"


def _mig_out(c, uid, r):
    d = _row(r)
    d["counts"] = {x["state"]: x["n"] for x in _all(c, "SELECT state, count(*) AS n FROM import_entries WHERE user_id=%s AND stream_id=%s "
                                                     "GROUP BY state", (uid, r["id"]))}
    return d


def migration_stream_get(uid: str):
    """이 사용자의 board_migration 입력원(읽기 전용) — 없으면 None. 모양은 migration_stream 과 같다."""
    with tx() as c:
        r = _one(c, f"SELECT {_MIG_COLS} FROM import_streams WHERE user_id=%s AND channel='board_migration'", (uid,))
        return _mig_out(c, uid, r) if r else None


def migration_stream(uid: str, label: str = "board 이전") -> dict:
    """이 사용자의 board_migration 입력원 — 없으면 만든다(사용자당 하나). 반환 {id, label, migration_started_at, migration_ends_at,
    migration_closed_at, final_manifest_sha256(hex|None), created_at, counts: {state: n}}."""
    label = db._label(label, "입력원 이름")
    with tx() as c:
        db._user_lock(c, uid)
        r = _one(c, f"SELECT {_MIG_COLS} FROM import_streams WHERE user_id=%s AND channel='board_migration'", (uid,))
        if not r:
            c.execute("INSERT INTO import_streams (user_id,id,channel,label) VALUES (%s,%s,'board_migration',%s)",
                      (uid, "is_" + secrets.token_hex(12), label))
            r = _one(c, f"SELECT {_MIG_COLS} FROM import_streams WHERE user_id=%s AND channel='board_migration'", (uid,))
        return _mig_out(c, uid, r)


def migration_entries(uid: str, stream_id: str) -> list:
    """이전 입력원의 접수 대응표(받은 순) — [{id, original_id, source_revision, previous_entry_id, kind, state, is_latest_received,
    is_current_applied, target_kind, target_id, target_item_revision, batch_id, received_at}]. 본문·해시는 없다."""
    with tx() as c:
        rows = _all(c, "SELECT e.id,e.original_id,e.source_revision,e.previous_entry_id,e.kind,e.state,e.is_latest_received,"
                       "e.is_current_applied,e.target_item_id,e.target_record_id,e.target_object_id,e.target_activity_id,"
                       "e.target_item_revision,e.batch_id,e.received_at FROM import_entries e JOIN import_streams s "
                       "ON s.user_id=e.user_id AND s.id=e.stream_id AND s.channel='board_migration' "
                       "WHERE e.user_id=%s AND e.stream_id=%s ORDER BY e.received_at, e.id", (uid, str(stream_id or "")))
    out = []
    for r in rows:
        tk, tid = _target(r)
        out.append({"id": r["id"], "original_id": r["original_id"], "source_revision": r["source_revision"],
                    "previous_entry_id": r["previous_entry_id"], "kind": r["kind"], "state": r["state"],
                    "is_latest_received": r["is_latest_received"], "is_current_applied": r["is_current_applied"],
                    "target_kind": tk, "target_id": tid, "target_item_revision": r["target_item_revision"], "batch_id": r["batch_id"],
                    "received_at": _j(r["received_at"])})
    return out


def _mig_lock(c, uid, stream_id):
    r = _one(c, f"SELECT {_MIG_COLS},(migration_ends_at IS NOT NULL AND migration_ends_at <= now()) AS ended FROM import_streams "
                "WHERE user_id=%s AND id=%s AND channel='board_migration' FOR NO KEY UPDATE", (uid, str(stream_id or "")))
    if not r:
        raise _nf("그 이전 입력원")
    return r


def _mig_target(c, uid, stream_id, ref):
    """명세 참조 대상 {original_id+revision|record_id|item_id|candidate_id} → (target_kind, target_id).
    original_id 는 같은 입력원 대응표에서 지정 revision 으로 적용된 판(정확한 판 — r3). 그 판이 없으면 REF-NOT-FOUND."""
    if not isinstance(ref, dict) or not ref:
        raise CrmError("ARG-INVALID", "참조 대상은 original_id(+revision)·record_id·item_id·candidate_id 중 하나예요.")
    if "original_id" in ref:
        if set(ref) != {"original_id", "revision"}:
            raise CrmError("ARG-INVALID", "original_id 참조는 revision 이 필요해요.")
        e = _one(c, "SELECT target_item_id, target_record_id FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                    "AND source_revision=%s AND state IN ('applied','noop')", (uid, stream_id, str(ref["original_id"]), str(ref["revision"])))
        if not e or not (e["target_item_id"] or e["target_record_id"]):
            raise CrmError("REF-NOT-FOUND", "참조하는 원본이 아직 이전되지 않았어요.", "선행 행을 먼저 이전하세요(명세 순서).")
        return ("item", e["target_item_id"]) if e["target_item_id"] else ("record", e["target_record_id"])
    if len(ref) != 1:
        raise CrmError("ARG-INVALID", "참조 대상은 하나만 가리켜요.")
    (k, v), = ref.items()
    kinds = {"record_id": "record", "item_id": "item", "candidate_id": "candidate"}
    if k not in kinds:
        raise CrmError("ARG-INVALID", "참조 대상 키가 틀렸어요.")
    return kinds[k], str(v)


def _mig_projection(row):
    keep = ("target_kind", "unit_kind", "object_id", "code", "title", "body", "fields", "decision", "source_created_at", "source_date",
            "references", "attach")
    return {k: row.get(k) for k in keep}


def _entry_flags_off(c, uid, latest, current):
    """새 접수 행을 넣기 전 — 같은 원본의 최신·현재 표시를 내린다(부분 UNIQUE 가 하나만 허용)."""
    if latest:
        c.execute("UPDATE import_entries SET is_latest_received=false WHERE user_id=%s AND id=%s", (uid, latest["id"]))
    if current:
        c.execute("UPDATE import_entries SET is_current_applied=false WHERE user_id=%s AND id=%s", (uid, current["id"]))


def migration_apply_row(uid: str, stream_id: str, row: dict, *, batch_id: str, manifest_sha256: str, transform_version: str) -> dict:
    """정제 명세 한 행(board-migration/1 — CLI 가 object_ref 를 object_id 로 해석하고 previous_revision 을 대응표로 정한 것) 적용.
    결과 {"import_id", "state": applied|noop, "target_kind": item|record, "target_id", "duplicate": bool}. 한 트랜잭션 — 실패하면
    업무 쓰기·대응표·관계·한도·감사 전부 롤백. 오류 = MIGRATION-OFF · MIGRATION-WINDOW-CLOSED · SOURCE-REVISION-CONFLICT ·
    IDEMPOTENCY-CONFLICT · KIND-CHANGED · TARGET-REVISION-CONFLICT · REF-NOT-FOUND · RATE-LIMIT(기록 50·제안 100·결정 300) · ARG-INVALID·LINK-INVALID."""
    if not switch_on("BOARD_MIGRATION_ENABLED"):
        raise _off("MIGRATION-OFF", "board 이전")
    if not isinstance(row, dict):
        raise CrmError("ARG-INVALID", "행은 객체예요.")
    for k in ("original_id", "revision"):
        if not isinstance(row.get(k), str) or not _ID_RES[k].match(row[k]):
            raise CrmError("ARG-INVALID", f"{k} 형식이 틀렸어요.")
    pr = row.get("previous_revision")
    if pr is not None and (not isinstance(pr, str) or not _ID_RES["revision"].match(pr)):
        raise CrmError("ARG-INVALID", "previous_revision 형식이 틀렸어요.")
    tk = row.get("target_kind")
    if tk not in ("item", "record"):
        raise CrmError("ARG-INVALID", "target_kind 는 item·record 중 하나예요.")
    if not isinstance(manifest_sha256, str) or not _SHA_RE.match(manifest_sha256):
        raise CrmError("ARG-INVALID", "manifest_sha256 은 64자리 소문자 hex 예요.")
    if not isinstance(batch_id, str) or not 1 <= len(batch_id) <= 80 or not isinstance(transform_version, str) or not transform_version:
        raise CrmError("ARG-INVALID", "batch_id·transform_version 이 비었어요.")
    if row.get("source_created_at") is not None and row.get("source_date") is not None:
        raise CrmError("ARG-INVALID", "source_created_at 과 source_date 는 함께 쓰지 않아요.")
    try:
        sca = _ts(row.get("source_created_at"), "source_created_at")
        sdate = _date(row.get("source_date"), "source_date")
    except CrmError as e:
        raise CrmError("ARG-INVALID", e.message)
    if row.get("decision") is not None and row["decision"] not in _DECISION_ACTIONS:
        raise CrmError("ARG-INVALID", "decision 은 pending·confirmed·excluded 중 하나예요.")
    try:
        return _mig_apply(uid, stream_id, row, pr, tk, sca, sdate, batch_id, manifest_sha256, transform_version)
    except CrmError as e:
        if e.code == "RATE-LIMIT":   # 일일 한도 — 미처리 행으로 남기고 다음 KST 구간에 재개(e.resume_at)
            raise _with_resume(e)
        raise


def _mig_apply(uid, stream_id, row, pr, tk, sca, sdate, batch_id, manifest_sha256, transform_version):
    kind = "migration_item" if tk == "item" else "migration_record"
    req_sha = hashlib.sha256(jcs(row)).digest()
    proj_sha = hashlib.sha256(jcs(_mig_projection(row))).digest()
    with db.tx_scope() as c:
        db._user_lock(c, uid)
        st = _mig_lock(c, uid, stream_id)
        same = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                       "AND source_revision=%s", (uid, st["id"], row["original_id"], row["revision"]))
        if same:
            if bytes(same["request_sha256"]) != req_sha:
                raise CrmError("IDEMPOTENCY-CONFLICT", "같은 개정 ID 로 다른 내용을 보냈어요.", "새 개정 ID 로 명세를 다시 만드세요.")
            out = _resp(same, True)
            return {k: out[k] for k in ("import_id", "state", "target_kind", "target_id", "duplicate")}
        if st["migration_closed_at"] is not None or st["ended"]:
            raise CrmError("MIGRATION-WINDOW-CLOSED", "병행 기간이 끝났거나 종료된 이전 입력원이에요.",
                           "원작자가 결정했으면 extend 로 연장하세요. 종료된 입력원은 다시 열지 않아요.")
        latest = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                         "AND is_latest_received FOR UPDATE", (uid, st["id"], row["original_id"]))
        if (latest is None) != (pr is None) or (latest is not None and latest["source_revision"] != pr):
            raise CrmError("SOURCE-REVISION-CONFLICT", "previous_revision 이 서버 대응표의 최신 개정과 달라요.", "대응표를 다시 읽고 resume 하세요.")
        if latest is not None and latest["kind"] != kind:
            raise CrmError("KIND-CHANGED", "같은 원본의 대상 종류는 바꿀 수 없어요.")
        current = _one(c, f"SELECT {_ENTRY_COLS} FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
                          "AND is_current_applied FOR UPDATE", (uid, st["id"], row["original_id"]))
        state, item_rev = "applied", None
        if current and bytes(current["projection_sha256"]) == proj_sha:
            state = "noop"
            target = current["target_item_id"] or current["target_record_id"]
            item_rev = current["target_item_revision"]
        elif tk == "record":
            target = _mig_record(c, uid, st["id"], row, sca, sdate, batch_id)
        else:
            target, item_rev = _mig_item(c, uid, st["id"], row, current, sca, sdate, batch_id)
        _entry_flags_off(c, uid, latest, current)
        if state == "applied" and st["migration_started_at"] is None:   # 병행 기간 = 첫 업무 적용부터 14일
            c.execute("UPDATE import_streams SET migration_started_at=now(), migration_ends_at = now() + make_interval(days => %s) "
                      "WHERE user_id=%s AND id=%s", (MIGRATION_DAYS, uid, st["id"]))
        if not _one(c, "SELECT 1 FROM import_entries WHERE user_id=%s AND stream_id=%s AND batch_id=%s LIMIT 1", (uid, st["id"], batch_id)):
            db._audit(c, "migration_apply", uid, uid, {"stream_id": st["id"], "batch": batch_id[:24], "manifest": manifest_sha256[:12],
                                                      "result": "started"})
        e = _one(c, "INSERT INTO import_entries (user_id,id,stream_id,original_id,source_revision,previous_entry_id,kind,request_sha256,"
                    "projection_sha256,state,is_latest_received,is_current_applied,resolved_at,resolved_by,target_item_id,target_record_id,"
                    "target_item_revision,batch_id,manifest_sha256,transform_version) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,true,now(),'migration',%s,%s,%s,%s,%s,%s) "
                    f"RETURNING {_ENTRY_COLS}",
                    (uid, db.new_id("ie"), st["id"], row["original_id"], row["revision"], latest["id"] if latest else None, kind, req_sha,
                     proj_sha, state, target if tk == "item" else None, target if tk == "record" else None, item_rev, batch_id,
                     bytes.fromhex(manifest_sha256), transform_version))
    out = _resp(e, False)
    return {k: out[k] for k in ("import_id", "state", "target_kind", "target_id", "duplicate")}


def _mig_record(c, uid, stream_id, row, sca, sdate, batch_id):
    """기록 새 판 — self/verbatim(원작자 결정 1·8: 검토표를 마친 「나와 AI 가 함께 만든 문장」) · ingest_kind board_migration.
    확인된 원본 시각이 있으면 created 에 보존(한도는 received_at 으로 센다). 「어디에 붙나」 는 record_attach 그대로."""
    body = row.get("body")
    if not isinstance(body, str) or not body.strip():
        raise CrmError("ARG-INVALID", "기록 본문이 비었어요.")
    title = (row.get("title") or "").strip() or body.strip().splitlines()[0][:40]
    db.check_len("body", body, db.LIMIT_BODY)
    db.check_len("title", title, db.LIMIT_TEXT)
    db._daily_gate(c, uid, "records")
    rid = db.new_id("r")
    hist = [{"at": db.now().isoformat(), "by": "board_migration", "author_kind": "self", "text_process": "verbatim"}]
    c.execute("INSERT INTO records (id,user_id,title,body,source,author_kind,text_process,provenance_history,ingest_kind,import_batch_id,"
              "source_created_at,source_date,created) "
              "VALUES (%s,%s,%s,%s,%s,'self','verbatim',%s,'board_migration',%s,%s,%s,COALESCE(%s, now()))",
              (rid, uid, title, body, "board 이전", Jsonb(hist), batch_id, sca, sdate, sca))
    at = row.get("attach") or {}
    if at:
        attach_to = None
        if "attach_to" in at:
            attach_to = []
            for t in at.get("attach_to") or []:
                k, tid = _mig_target(c, uid, stream_id, t)
                attach_to.append({"field_key": "attach_to", "target_kind": k, "target_id": tid})
        axes = [_mig_target(c, uid, stream_id, t)[1] for t in at.get("axes") or []] if "axes" in at else None
        db.record_attach(uid, rid, attach_to, axes, at.get("use_at"), at.get("unattached"))
    return rid


def _mig_item(c, uid, stream_id, row, current, sca, sdate, batch_id):
    """항목 — 새 원본이면 제안 → 적용, 이미 적용한 원본이면 같은 항목에 제안 → 적용(revision+1). 결정(decision)은 기준에만.
    서버 항목 revision 이 마지막 적용(또는 expected_target_revision)과 다르거나 보관됐으면 TARGET-REVISION-CONFLICT. 반환 (item_id, revision)."""
    oid = row.get("object_id")
    if not isinstance(oid, str) or not oid:
        raise CrmError("ARG-INVALID", "항목 행은 object_id(해석한 대상 오브젝트)가 필요해요.")
    od = _one(c, "SELECT revision, semantic_kind FROM object_defs WHERE user_id=%s AND id=%s AND revision > 0 AND archived_at IS NULL",
              (uid, oid))
    if not od:
        raise CrmError("ARG-INVALID", "대상 오브젝트가 없거나 보관됐어요.", "validate-only 로 대상 해석을 다시 보세요.")
    links = []
    for ref in row.get("references") or []:
        if not isinstance(ref, dict) or set(ref) != {"field_key", "target"}:
            raise CrmError("ARG-INVALID", "references 는 {field_key, target} 목록이에요.")
        k, t = _mig_target(c, uid, stream_id, ref["target"])
        links.append({"field_key": ref["field_key"], "target_kind": k, "target_id": t})
    item_id, base = None, 0
    if current:
        item_id = current["target_item_id"]
        it = _one(c, "SELECT revision, archived_at, trashed_at, object_id FROM items WHERE user_id=%s AND id=%s FOR UPDATE", (uid, item_id))
        exp = row.get("expected_target_revision")
        if (not it or it["archived_at"] is not None or it["trashed_at"] is not None or it["object_id"] != oid or it["revision"] != current["target_item_revision"]
                or (exp is not None and exp != it["revision"])):
            raise CrmError("TARGET-REVISION-CONFLICT", "서버 항목이 마지막 이전 뒤에 바뀌었거나 보관됐어요.", "서버 쪽 변경을 확인한 뒤 명세를 다시 만드세요.")
        base = it["revision"]
    p = db.item_propose(uid, oid, od["revision"], item_id, base, row.get("code"), row.get("title") or "", row.get("body") or "",
                        row.get("fields") or {}, links)
    item = db.item_decide(uid, p["item_id"], base, p["proposal_id"], "apply")
    dec = row.get("decision")
    if dec is not None and od["semantic_kind"] == "principle" and item.get("decision") != dec:
        item = db.item_decide(uid, p["item_id"], item["revision"], None, _DECISION_ACTIONS[dec])
    if not current:   # 최초 배치·원본 날짜는 처음 만들 때만(재이전이 덮지 않는다)
        c.execute("UPDATE items SET ingest_kind='board_migration', import_batch_id=%s, source_created_at=%s, source_date=%s "
                  "WHERE user_id=%s AND id=%s", (batch_id, sca, sdate, uid, p["item_id"]))
    return p["item_id"], item["revision"]


def migration_extend(uid: str, stream_id: str, days: int = MIGRATION_EXTEND_STEP) -> dict:
    """병행 기간 7일 단위 연장(원작자 결정 · 감사 migration_extend). 시작 전·종료된 입력원은 거절. 반환 입력원 dict."""
    if not _is_int(days) or days <= 0 or days % MIGRATION_EXTEND_STEP or days > 4 * MIGRATION_EXTEND_STEP:
        raise CrmError("ARG-INVALID", f"연장은 {MIGRATION_EXTEND_STEP}일 단위(최대 {4 * MIGRATION_EXTEND_STEP}일)예요.")
    with tx() as c:
        db._user_lock(c, uid)
        st = _mig_lock(c, uid, stream_id)
        if st["migration_closed_at"] is not None:
            raise CrmError("MIGRATION-WINDOW-CLOSED", "종료된 이전 입력원은 다시 열지 않아요.")
        if st["migration_started_at"] is None:
            raise CrmError("ARG-INVALID", "아직 병행 기간이 시작되지 않았어요(첫 적용 전).")
        c.execute("UPDATE import_streams SET migration_ends_at = GREATEST(migration_ends_at, now()) + make_interval(days => %s) "
                  "WHERE user_id=%s AND id=%s", (days, uid, st["id"]))
        db._audit(c, "migration_extend", uid, uid, {"stream_id": st["id"], "days": days, "result": "ok"})
        return _mig_out(c, uid, _one(c, f"SELECT {_MIG_COLS} FROM import_streams WHERE user_id=%s AND id=%s", (uid, st["id"])))


def migration_close(uid: str, stream_id: str, final_manifest_sha256: str) -> dict:
    """병행 종료 — migration_closed_at·최종 명세 해시 기록(감사 migration_close). 이후 적용 거절, 다시 열지 않는다. 반환 입력원 dict."""
    if not isinstance(final_manifest_sha256, str) or not _SHA_RE.match(final_manifest_sha256):
        raise CrmError("ARG-INVALID", "final_manifest_sha256 은 64자리 소문자 hex 예요.")
    with tx() as c:
        db._user_lock(c, uid)
        st = _mig_lock(c, uid, stream_id)
        if st["migration_closed_at"] is not None:
            raise CrmError("MIGRATION-WINDOW-CLOSED", "이미 종료된 이전 입력원이에요.")
        if st["migration_started_at"] is None:
            raise CrmError("ARG-INVALID", "아직 병행 기간이 시작되지 않았어요(첫 적용 전).")
        c.execute("UPDATE import_streams SET migration_closed_at=now(), final_manifest_sha256=%s WHERE user_id=%s AND id=%s",
                  (bytes.fromhex(final_manifest_sha256), uid, st["id"]))
        db._audit(c, "migration_close", uid, uid, {"stream_id": st["id"], "manifest": final_manifest_sha256[:12], "result": "ok"})
        return _mig_out(c, uid, _one(c, f"SELECT {_MIG_COLS} FROM import_streams WHERE user_id=%s AND id=%s", (uid, st["id"])))
