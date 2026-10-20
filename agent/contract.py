"""canon-inbox/1 (schema_revision 2) 클라이언트 쪽 계약 — 상수·검증기·요청 생성기·멱등 키.

정본 = r3 T2. 서버 쪽 검증기·공용 테스트 벡터(server/contract/imports/vectors/)가 생기면 이 파일의
상수를 그 벡터에 맞춘다. 서버와 맞춰야 할 선택은 전부 이 파일 위쪽 상수에 모았다.

검증 실패 메시지에는 값이 아니라 필드 이름과 규칙 코드만 넣는다(본문이 로그로 새지 않게).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re

from . import jcs

# ---- 계약 식별 ---------------------------------------------------------------
CONTRACT = "canon-inbox/1"
SCHEMA_REVISION = 2
KINDS = ("self_text", "activity", "self_summary")
EXECUTIONS = ("attended", "unattended")

# ---- ID 형식 (서버와 맞출 것) ---------------------------------------------------
# 서버 생성 ID: server/db.py new_id = f"{prefix}_{token_urlsafe(8)}" 꼴. 접두는 서버가 정한다.
SERVER_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,7}_[A-Za-z0-9_-]{6,64}$")
SOURCE_INSTANCE_RE = re.compile(r"^is_[0-9a-f]{24}$")           # import_streams.id (서버 스키마와 같음)
AXIS_ID_RE = re.compile(r"^i_[A-Za-z0-9_-]{4,40}$")              # 축 = 항목 ID
ACTIVITY_ID_RE = re.compile(r"^a_[A-Za-z0-9_-]{6,40}$")          # activity_ref = 활동 판 ID
# PC 생성 ID: 128비트 난수 hex. URL·계정명·파일명에서 유도하지 않는다.
ORIGINAL_ID_RE = re.compile(r"^o_[0-9a-f]{32}$")
REVISION_RE = re.compile(r"^v_[0-9a-f]{32}$")
RAW_ID_RE = re.compile(r"^[0-9a-f]{48}$")          # 192비트 난수
HMAC_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# source_created_at: 오프셋 있는 RFC 3339(시간대 모르면 보내지 않는다 — KST 임의 변환 금지)
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
# 업로드 토큰: imp_<stream-id>.<token-id>.<secret>
TOKEN_RE = re.compile(r"^imp_(is_[0-9a-f]{24})\.([A-Za-z0-9_-]{6,64})\.([A-Za-z0-9_-]{32,128})$")
IDEMPOTENCY_RE = re.compile(r"^ci1_[0-9a-f]{64}$")

# ---- enum -------------------------------------------------------------------
ACTIONS = ("like", "repost", "comment", "read", "save", "send", "reply")
ACTIVITY_STATES = ("active", "retracted")
PROVIDERS = ("sns", "web", "mail", "kakao")
PERSON_ROLES = ("author", "counterparty", "participant")

# 등록된 변환 버전 → 허용 kind. 골격 어댑터(feed·reactions·mail·kakao)의 변환은 등록하지 않는다
# — 실수로 켜도 계약 검증에서 막힌다.
TRANSFORMS = {
    "board-likes/1": "activity",
    "self-text/1": "self_text",
    "self-summary/1": "self_summary",
}
# 변환 버전별 허용 행동·제공자(activity 만)
TRANSFORM_ACTIVITY = {
    "board-likes/1": {"actions": ("like",), "providers": ("sns",)},
}
SUMMARY_TOOLS = ("claude-cli", "unknown")
SUMMARY_MODELS = ("unknown",)

# ---- 필드 -------------------------------------------------------------------
COMMON_FIELDS = ("contract", "schema_revision", "source_instance_id", "original_id", "revision",
                 "previous_revision", "kind", "execution", "transform_version", "policy_revision")
# 종류별: 필수(키가 반드시 있음) / 선택(없어도 됨). 값 null 허용 여부는 검사 함수가 정한다.
KIND_FIELDS = {
    # 생성기는 activity 의 선택 필드도 늘 채워 보낸다. 검증기는 서버 스키마와 같은 필수 집합을 쓴다.
    "activity": {
        "required": ("action", "state", "occurred_on", "observed_on", "provider", "raw_id"),
        "optional": ("people", "axes"),
    },
    "self_text": {
        "required": ("body",),
        "optional": ("title", "source_created_at", "source_date", "activity_ref", "generation"),
    },
    "self_summary": {
        "required": ("body", "generation"),
        "optional": ("title", "source_created_at", "source_date"),
    },
}

# ---- 한도 -------------------------------------------------------------------
MAX_REQUEST_BYTES = 64 * 1024
MAX_ACTIVITY_BYTES = 8 * 1024
MAX_BODY_CODEPOINTS = 8000
MAX_BODY_BYTES = 32 * 1024
MAX_TITLE_CODEPOINTS = 200
MAX_PEOPLE = 10
MAX_AXES = 10
MAX_SUMMARY_INPUTS = 20
MAX_TRANSFORM_LEN = 40

# ---- 문자열 차단 패턴(URL·이메일·명백한 경로) ------------------------------------
_BLOCK_PATTERNS = (
    ("url", re.compile(r"(?i)\b(?:https?|ftp|file|wss?)://")),
    ("url", re.compile(r"(?i)\bwww\.[a-z0-9-]+\.[a-z]{2,}")),
    ("url", re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|kr|co|me|ai|app|dev|so)/\S")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("path", re.compile(r"(?:^|[\s(\"'])(?:~/|/Users/|/home/|/private/|/var/|/Volumes/)")),
    ("path", re.compile(r"(?:^|[\s(\"'])[A-Za-z]:\\")),
    ("path", re.compile(r"\\\\[A-Za-z0-9._-]+\\")),
)


class ContractError(ValueError):
    """code 는 고정 규칙 코드, field 는 필드 경로. 값은 담지 않는다."""

    def __init__(self, code: str, field: str = ""):
        self.code = code
        self.field = field
        super().__init__(f"{code}:{field}" if field else code)


# ---- 파싱 ---------------------------------------------------------------------
def _no_dup_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ContractError("DUPLICATE-KEY", k)
        d[k] = v
    return d


def _no_constant(name):
    raise ContractError("NON-JSON-NUMBER", name)


def parse_request(raw: bytes) -> dict:
    if not isinstance(raw, (bytes, bytearray)):
        raise ContractError("NOT-BYTES")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ContractError("REQUEST-TOO-LARGE")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise ContractError("NOT-UTF8") from None
    if text.startswith("﻿"):
        raise ContractError("BOM")
    try:
        obj = json.loads(text, object_pairs_hook=_no_dup_pairs, parse_constant=_no_constant,
                         parse_float=lambda s: _no_constant("float"))
    except ContractError:
        raise
    except ValueError:
        raise ContractError("NOT-JSON") from None
    if not isinstance(obj, dict):
        raise ContractError("NOT-OBJECT")
    return obj


# ---- 작은 검사들 ----------------------------------------------------------------
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _scan_strings(v, path: str) -> None:
    if isinstance(v, str):
        for code, pat in _BLOCK_PATTERNS:
            if pat.search(v):
                raise ContractError("BLOCKED-" + code.upper(), path)
    elif isinstance(v, dict):
        for k, x in v.items():
            _scan_strings(x, f"{path}.{k}" if path else k)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _scan_strings(x, f"{path}[{i}]")


def _valid_date(s) -> bool:
    if not isinstance(s, str) or not DATE_RE.match(s):
        return False
    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _valid_datetime(s) -> bool:
    if not isinstance(s, str) or not DATETIME_RE.match(s):
        return False
    try:
        _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _check_text(v, field: str, max_cp: int, max_bytes: int, allow_null: bool) -> None:
    if v is None:
        if allow_null:
            return
        raise ContractError("REQUIRED", field)
    if not isinstance(v, str):
        raise ContractError("TYPE", field)
    if not v.strip():
        raise ContractError("EMPTY", field)
    if len(v) > max_cp:
        raise ContractError("TOO-LONG", field)
    if len(v.encode("utf-8")) > max_bytes:
        raise ContractError("TOO-LARGE", field)
    if "\x00" in v:
        raise ContractError("NUL", field)


# ---- 검증기 ---------------------------------------------------------------------
def validate(obj: dict, *, policy: dict | None = None) -> dict:
    """계약 검증. policy 가 있으면 장치 정책(입력원·변환·축·정책 판)까지 본다.

    policy = {"source_instance_id": str, "policy_revision": int, "allowed_axes": [item_id...],
              "transforms": [code...] (선택), "actions": [action...] (선택)} — 서버 GET /api/imports/device 의 policy
    """
    if not isinstance(obj, dict):
        raise ContractError("NOT-OBJECT")
    kind = obj.get("kind")
    if kind not in KINDS:
        raise ContractError("ENUM", "kind")
    spec = KIND_FIELDS[kind]
    allowed = set(COMMON_FIELDS) | set(spec["required"]) | set(spec["optional"])
    for k in obj:
        if k not in allowed:
            raise ContractError("UNKNOWN-FIELD", k)
    for k in COMMON_FIELDS + spec["required"]:
        if k not in obj:
            raise ContractError("MISSING", k)

    if obj["contract"] != CONTRACT:
        raise ContractError("CONTRACT", "contract")
    if not _is_int(obj["schema_revision"]) or obj["schema_revision"] != SCHEMA_REVISION:
        raise ContractError("SCHEMA-REVISION", "schema_revision")
    if not isinstance(obj["source_instance_id"], str) or not SOURCE_INSTANCE_RE.match(obj["source_instance_id"]):
        raise ContractError("FORMAT", "source_instance_id")
    if not isinstance(obj["original_id"], str) or not ORIGINAL_ID_RE.match(obj["original_id"]):
        raise ContractError("FORMAT", "original_id")
    if not isinstance(obj["revision"], str) or not REVISION_RE.match(obj["revision"]):
        raise ContractError("FORMAT", "revision")
    prev = obj["previous_revision"]
    if prev is not None and (not isinstance(prev, str) or not REVISION_RE.match(prev)):
        raise ContractError("FORMAT", "previous_revision")
    if prev == obj["revision"]:
        raise ContractError("SELF-PARENT", "previous_revision")
    if obj["execution"] not in EXECUTIONS:
        raise ContractError("ENUM", "execution")
    tv = obj["transform_version"]
    if not isinstance(tv, str) or len(tv) > MAX_TRANSFORM_LEN or tv not in TRANSFORMS:
        raise ContractError("UNREGISTERED-TRANSFORM", "transform_version")
    if TRANSFORMS[tv] != kind:
        raise ContractError("TRANSFORM-KIND", "transform_version")
    if not _is_int(obj["policy_revision"]) or obj["policy_revision"] < 1:
        raise ContractError("FORMAT", "policy_revision")

    if kind == "activity":
        _validate_activity(obj)
    elif kind == "self_text":
        _validate_self_text(obj)
    else:
        _validate_self_summary(obj)

    _scan_strings(obj, "")

    size = len(jcs.canonical_bytes(obj))
    if size > MAX_REQUEST_BYTES:
        raise ContractError("REQUEST-TOO-LARGE")
    if kind == "activity" and size > MAX_ACTIVITY_BYTES:
        raise ContractError("ACTIVITY-TOO-LARGE")

    if policy is not None:
        _validate_policy(obj, policy)
    return obj


def _validate_activity(o: dict) -> None:
    if o["action"] not in ACTIONS:
        raise ContractError("ENUM", "action")
    if o["state"] not in ACTIVITY_STATES:
        raise ContractError("ENUM", "state")
    for f in ("occurred_on", "observed_on"):
        if o.get(f) is not None and not _valid_date(o[f]):
            raise ContractError("DATE", f)
    if o["provider"] not in PROVIDERS:
        raise ContractError("ENUM", "provider")
    rule = TRANSFORM_ACTIVITY.get(o["transform_version"])
    if rule is not None:
        if o["action"] not in rule["actions"]:
            raise ContractError("NOT-IN-TRANSFORM", "action")
        if o["provider"] not in rule["providers"]:
            raise ContractError("NOT-IN-TRANSFORM", "provider")
    if not isinstance(o["raw_id"], str) or not RAW_ID_RE.match(o["raw_id"]):
        raise ContractError("FORMAT", "raw_id")
    people = o.get("people", [])
    if not isinstance(people, list):
        raise ContractError("TYPE", "people")
    if len(people) > MAX_PEOPLE:
        raise ContractError("TOO-MANY", "people")
    seen = set()
    for i, p in enumerate(people):
        f = f"people[{i}]"
        if not isinstance(p, dict):
            raise ContractError("SHAPE", f)
        for k in p:
            if k not in ("hmac", "role"):
                raise ContractError("UNKNOWN-FIELD", f"{f}.{k}")
        for k in ("hmac", "role"):
            if k not in p:
                raise ContractError("MISSING", f"{f}.{k}")
        if not isinstance(p["hmac"], str) or not HMAC_RE.match(p["hmac"]):
            raise ContractError("FORMAT", f + ".hmac")
        if p["role"] not in PERSON_ROLES:
            raise ContractError("ENUM", f + ".role")
        key = (p["hmac"], p["role"])
        if key in seen:
            raise ContractError("DUPLICATE", f)
        seen.add(key)
    axes = o.get("axes", [])
    if not isinstance(axes, list):
        raise ContractError("TYPE", "axes")
    if len(axes) > MAX_AXES:
        raise ContractError("TOO-MANY", "axes")
    for i, a in enumerate(axes):
        if not isinstance(a, str) or not AXIS_ID_RE.match(a):
            raise ContractError("FORMAT", f"axes[{i}]")
    if len(set(axes)) != len(axes):
        raise ContractError("DUPLICATE", "axes")


def _validate_dates_pair(o: dict) -> None:
    sca, sd = o.get("source_created_at"), o.get("source_date")
    if sca is not None and sd is not None:
        raise ContractError("BOTH-DATES", "source_date")
    if sca is not None and not _valid_datetime(sca):
        raise ContractError("DATETIME", "source_created_at")
    if sd is not None and not _valid_date(sd):
        raise ContractError("DATE", "source_date")


def _validate_self_common(o: dict) -> None:
    _check_text(o["body"], "body", MAX_BODY_CODEPOINTS, MAX_BODY_BYTES, allow_null=False)
    if "title" in o:
        # 제목은 있으면 비지 않은 한 줄 문자열(null 금지 — 없으면 키를 뺀다)
        _check_text(o["title"], "title", MAX_TITLE_CODEPOINTS, MAX_TITLE_CODEPOINTS * 4, allow_null=False)
        if "\n" in o["title"] or "\r" in o["title"]:
            raise ContractError("SINGLE-LINE", "title")
    _validate_dates_pair(o)


def _validate_self_text(o: dict) -> None:
    _validate_self_common(o)
    if "generation" in o:
        g = o["generation"]
        if not isinstance(g, dict):
            raise ContractError("GENERATION", "generation")
        for k in g:
            if k != "method":
                raise ContractError("UNKNOWN-FIELD", "generation." + k)
        if g.get("method") != "none":
            raise ContractError("GENERATION", "generation.method")
    ref = o.get("activity_ref")
    if ref is not None and (not isinstance(ref, str) or not ACTIVITY_ID_RE.match(ref)):
        raise ContractError("FORMAT", "activity_ref")


def _validate_self_summary(o: dict) -> None:
    _validate_self_common(o)
    g = o["generation"]
    keys = {"method", "tool", "model", "input_scope", "inputs"}
    if not isinstance(g, dict) or set(g.keys()) != keys:
        raise ContractError("GENERATION", "generation")
    if g["method"] != "ai_summary":
        raise ContractError("GENERATION", "generation.method")
    if g["tool"] not in SUMMARY_TOOLS:
        raise ContractError("GENERATION", "generation.tool")
    if g["model"] not in SUMMARY_MODELS:
        raise ContractError("GENERATION", "generation.model")
    if g["input_scope"] != "self_only":
        raise ContractError("GENERATION", "generation.input_scope")
    inputs = g["inputs"]
    if not isinstance(inputs, list) or not inputs or len(inputs) > MAX_SUMMARY_INPUTS:
        raise ContractError("GENERATION", "generation.inputs")
    for i, ref in enumerate(inputs):
        f = f"generation.inputs[{i}]"
        if not isinstance(ref, dict) or set(ref.keys()) != {"original_id", "revision", "sha256"}:
            raise ContractError("SHAPE", f)
        if not isinstance(ref["original_id"], str) or not ORIGINAL_ID_RE.match(ref["original_id"]):
            raise ContractError("FORMAT", f + ".original_id")
        if not isinstance(ref["revision"], str) or not REVISION_RE.match(ref["revision"]):
            raise ContractError("FORMAT", f + ".revision")
        if not isinstance(ref["sha256"], str) or not SHA256_RE.match(ref["sha256"]):
            raise ContractError("FORMAT", f + ".sha256")


def _validate_policy(o: dict, policy: dict) -> None:
    if o["source_instance_id"] != policy.get("source_instance_id"):
        raise ContractError("POLICY-SOURCE", "source_instance_id")
    if o["policy_revision"] != policy.get("policy_revision"):
        raise ContractError("POLICY-REVISION", "policy_revision")
    transforms = policy.get("transforms")
    if transforms is not None and o["transform_version"] not in transforms:
        raise ContractError("POLICY-TRANSFORM", "transform_version")
    if o["kind"] == "activity":
        actions = policy.get("actions")
        if actions is not None and o["action"] not in actions:
            raise ContractError("POLICY-ACTION", "action")
        allowed_axes = set(policy.get("allowed_axes") or ())
        for i, a in enumerate(o["axes"]):
            if a not in allowed_axes:
                raise ContractError("POLICY-AXIS", f"axes[{i}]")


def validate_bytes(raw: bytes, *, policy: dict | None = None) -> dict:
    obj = parse_request(raw)
    validate(obj, policy=policy)
    return obj


# ---- 멱등 키·요청 생성 -------------------------------------------------------------
def idempotency_key(source_instance_id: str, original_id: str, revision: str) -> str:
    """ci1_ + SHA256(JCS(["canon-inbox/1", 2, source_instance_id, original_id, revision]))"""
    raw = jcs.canonical_bytes([CONTRACT, SCHEMA_REVISION, source_instance_id, original_id, revision])
    return "ci1_" + hashlib.sha256(raw).hexdigest()


def request_sha256(obj: dict) -> str:
    return jcs.sha256_hex(obj)


def build_request(*, kind: str, source_instance_id: str, original_id: str, revision: str,
                  previous_revision: str | None, execution: str, transform_version: str,
                  policy_revision: int, fields: dict) -> dict:
    obj = {
        "contract": CONTRACT,
        "schema_revision": SCHEMA_REVISION,
        "source_instance_id": source_instance_id,
        "original_id": original_id,
        "revision": revision,
        "previous_revision": previous_revision,
        "kind": kind,
        "execution": execution,
        "transform_version": transform_version,
        "policy_revision": policy_revision,
    }
    for k in fields:
        if k in obj:
            raise ContractError("FIELD-CLASH", k)
    obj.update(fields)
    return obj


def encode_request(obj: dict, *, policy: dict | None = None) -> bytes:
    """검증한 뒤 JCS 바이트로. 전송 대기 파일·전송 본문은 이 바이트 그대로 쓴다."""
    validate(obj, policy=policy)
    raw = jcs.canonical_bytes(obj)
    validate_bytes(raw, policy=policy)
    return raw


def parse_token(token: str):
    """업로드 토큰을 (stream_id, token_id) 로. secret 은 돌려주지 않는다."""
    if not isinstance(token, str):
        raise ContractError("TOKEN-FORMAT")
    m = TOKEN_RE.match(token)
    if not m:
        raise ContractError("TOKEN-FORMAT")
    return m.group(1), m.group(2)
