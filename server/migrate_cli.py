"""board 정본 이전 CLI (board-migration/1) — 원작자 Mac 에서만, DATABASE_URL 을 명시해 돌린다. 웹 실행 경로 없음.

    DATABASE_URL=… .venv/bin/python migrate_cli.py init --user <uid>                           # 이전 입력원만 만들고 ID 출력(명세 source_instance_id)
    DATABASE_URL=… .venv/bin/python migrate_cli.py validate-only <명세.json> --user <uid>      # 읽기만 — 스키마·해시·대상·순서·진행
    DATABASE_URL=… .venv/bin/python migrate_cli.py run    <명세.json> --user <uid> [--limit N]   # 적용(소량은 --limit)
    DATABASE_URL=… .venv/bin/python migrate_cli.py resume <명세.json> --user <uid> [--limit N]   # 중단 뒤 — 완료 행 건너뜀
    DATABASE_URL=… .venv/bin/python migrate_cli.py status [<명세.json>] --user <uid>            # 병행 기간·상태별 건수(·명세 진행)
    DATABASE_URL=… .venv/bin/python migrate_cli.py extend --user <uid>                         # 병행 기간 7일 연장(원작자 결정 뒤만)
    DATABASE_URL=… .venv/bin/python migrate_cli.py close  <명세.json> --user <uid>              # 병행 종료 — 최종 명세 해시 기록

정본: notes/2026-10-20_board-마이그-구현계획.md §2-9·§2-11 > notes/raw/2026-10-20_board-migr-astra-r3.md T5·T7.
권한 = DB 접속권. .env·자동 탐색·기본 DB 주소를 쓰지 않는다(admin_cli.py 선례). 실행 조건 = 대상 계정(--user 가 명세 target_user_id 와 같음)
· 명세 해시(자기 해시 필드를 뺀 JCS 의 SHA-256) · 병행 기간 · 스위치 CRM_BOARD_MIGRATION_ENABLED.
행 단위 업무 쓰기·대응표·관계·한도·감사는 서버(imports.migration_*)가 한 트랜잭션에서 한다. 이 CLI 는 명세 검증·대상 해석·순서·진행 관리만.
출력은 건수·행 번호·불투명 ID(original_id)·오류 코드·시각(KST)만 — 제목·본문·필드 값·item code 를 찍지 않는다.
종료 코드: 0 성공 · 1 오류(거절·충돌) · 2 쓰는 법 · 3 일부만(일일 한도 — 다음 KST 구간에 resume).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CONTRACT = "board-migration/1"
SCHEMA_PATH = HERE / "contract" / "imports" / "board-migration-1.schema.json"
MANIFEST_MAX_BYTES = 16 * 1024 * 1024
TARGET_KINDS = ("item", "record")            # 정본 스키마 rows[].target_kind
DONE_STATES = ("applied", "noop")
PARTIAL_CODES = ("RATE-LIMIT",)                 # 일일 한도 — 미처리 행을 남기고 다음 KST 구간에 재개
EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_PARTIAL = 0, 1, 2, 3

USAGE = ("쓰는 법: migrate_cli.py validate-only|run|resume|close <명세.json> --user <uid> [--limit N]\n"
         "        migrate_cli.py status [<명세.json>] --user <uid>\n"
         "        migrate_cli.py init|extend --user <uid>\n"
         "DATABASE_URL 을 명령 앞에 명시해야 해요(.env 는 읽지 않아요).")


class Reject(Exception):
    """명세 거절 — problems = [(위치, 규칙)]. 값은 담지 않는다."""

    def __init__(self, problems):
        super().__init__(f"{len(problems)}건")
        self.problems = list(problems)


# ── 적재 · 해시 ────────────────────────────────────────────

def _no_float(s):
    raise Reject([("$", "float-forbidden")])


def _no_const(s):
    raise Reject([("$", "nonfinite-forbidden")])


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise Reject([("$", "duplicate-key")])
        out[k] = v
    return out


def load_manifest(path: str) -> tuple[dict, bytes]:
    """명세 파일 → (dict, 원래 바이트). UTF-8 · JSON 객체 하나 · 중복 키·실수·NaN 금지 · 크기 상한."""
    p = Path(path)
    if not p.is_file():
        raise Reject([("$", "file-missing")])
    raw = p.read_bytes()
    if len(raw) > MANIFEST_MAX_BYTES:
        raise Reject([("$", "too-large")])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Reject([("$", "utf8-invalid")]) from None
    try:
        doc = json.loads(text, object_pairs_hook=_pairs, parse_float=_no_float, parse_constant=_no_const)
    except json.JSONDecodeError:
        raise Reject([("$", "json-invalid")]) from None
    if not isinstance(doc, dict):
        raise Reject([("$", "object-required")])
    return doc, raw


def _check_jcs_value(v, path="$"):
    """JCS 범위 밖 값(실수·고아 서러게이트·비문자열 키) 거절 — 서버 imports.jcs 와 같은 범위."""
    if isinstance(v, bool) or v is None or isinstance(v, int):
        return
    if isinstance(v, float):
        raise Reject([(path, "float-forbidden")])
    if isinstance(v, str):
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            raise Reject([(path, "surrogate-forbidden")]) from None
        return
    if isinstance(v, list):
        for i, x in enumerate(v):
            _check_jcs_value(x, f"{path}[{i}]")
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise Reject([(path, "key-type")])
            _check_jcs_value(x, f"{path}.{k}")
        return
    raise Reject([(path, "type-forbidden")])


def jcs(obj) -> bytes:
    """JCS(RFC 8785) — 이 계약 범위(정수·문자열·bool·null·객체·배열, 실수 없음)에서
    json.dumps(sort_keys, separators=(',', ':'), ensure_ascii=False) 와 같다. 서버 imports.jcs 가 구현돼 있으면 그것을 쓴다(공용 벡터 일치)."""
    _check_jcs_value(obj)
    try:
        import imports
        return imports.jcs(obj)
    except (ImportError, NotImplementedError):
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_hash(doc: dict) -> str:
    """자기 해시 필드(manifest_sha256)를 뺀 JCS 의 SHA-256 소문자 hex."""
    body = {k: v for k, v in doc.items() if k != "manifest_sha256"}
    return hashlib.sha256(jcs(body)).hexdigest()


# ── 구조 검증 — 정본 스키마 파일(contract/imports/board-migration-1.schema.json)을 직접 읽는다 ─────────

def _is_id(v) -> bool:
    return isinstance(v, str) and 0 < len(v) <= 200 and v == v.strip()


def _is_date(v) -> bool:
    if not isinstance(v, str) or len(v) != 10:
        return False
    try:
        _dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _is_ts(v) -> bool:
    """시간대가 붙은 ISO 시각만(자정·KST 를 붙여 추측하지 않는다)."""
    if not isinstance(v, str) or "T" not in v:
        return False
    try:
        t = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return False
    return t.tzinfo is not None


_SCHEMA = None


def load_schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        if not SCHEMA_PATH.is_file():
            raise Reject([("$", "schema-file-missing")])
        _SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(v, t) -> bool:
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t]) and not (t != "boolean" and isinstance(v, bool) and _TYPES[t] is not bool)


def schema_errors(s: dict, v, at: str, root: dict, out: list) -> None:
    """JSON Schema 2020-12 의 이 계약이 쓰는 부분집합 — type·const·enum·pattern(검색, ASCII)·min/maxLength·minimum·
    required·properties·additionalProperties(false)·items·minItems·min/maxProperties·$ref(#/$defs)·allOf·if/then·not·anyOf·oneOf.
    모르는 키워드가 나오면 schema-keyword 로 거절한다(검증이 몰래 느슨해지지 않게). 오류엔 경로·규칙만(값 없음)."""
    import re
    known = {"$schema", "$id", "title", "$comment", "$defs", "type", "const", "enum", "pattern", "minLength", "maxLength", "minimum",
             "required", "properties", "additionalProperties", "items", "minItems", "minProperties", "maxProperties", "$ref",
             "allOf", "if", "then", "not", "anyOf", "oneOf"}
    unknown = set(s) - known
    if unknown:
        out.append((at, "schema-keyword:" + ",".join(sorted(unknown))))
        return
    if "$ref" in s:
        ref = s["$ref"]
        if not ref.startswith("#/$defs/"):
            out.append((at, "schema-ref"))
            return
        schema_errors(root["$defs"][ref[len("#/$defs/"):]], v, at, root, out)
    if "type" in s:
        ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_type_ok(v, t) for t in ts):
            out.append((at, "type"))
            return
    if "const" in s and v != s["const"]:
        out.append((at, "const"))
    if "enum" in s and not any(v == e and type(v) is type(e) for e in s["enum"]):
        out.append((at, "enum"))
    if isinstance(v, str):
        if "pattern" in s and not re.search(s["pattern"], v, re.ASCII):
            out.append((at, "pattern"))
        if "minLength" in s and len(v) < s["minLength"]:
            out.append((at, "minLength"))
        if "maxLength" in s and len(v) > s["maxLength"]:
            out.append((at, "maxLength"))
    if isinstance(v, int) and not isinstance(v, bool) and "minimum" in s and v < s["minimum"]:
        out.append((at, "minimum"))
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                out.append((f"{at}.{k}", "required"))
        props = s.get("properties", {})
        for k, x in v.items():
            if k in props:
                schema_errors(props[k], x, f"{at}.{k}", root, out)
            elif s.get("additionalProperties") is False:
                out.append((f"{at}.{k}", "additional"))
        if "minProperties" in s and len(v) < s["minProperties"]:
            out.append((at, "minProperties"))
        if "maxProperties" in s and len(v) > s["maxProperties"]:
            out.append((at, "maxProperties"))
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            out.append((at, "minItems"))
        if "items" in s:
            for i, x in enumerate(v):
                schema_errors(s["items"], x, f"{at}[{i}]", root, out)
    for sub in s.get("allOf", []):
        schema_errors(sub, v, at, root, out)
    if "if" in s:
        probe = []
        schema_errors(s["if"], v, at, root, probe)
        if not probe and "then" in s:
            schema_errors(s["then"], v, at, root, out)
    if "not" in s:
        probe = []
        schema_errors(s["not"], v, at, root, probe)
        if not probe:
            out.append((at, "not"))
    if "anyOf" in s:
        if not any(not _errs(sub, v, at, root) for sub in s["anyOf"]):
            out.append((at, "anyOf"))
    if "oneOf" in s:
        if sum(1 for sub in s["oneOf"] if not _errs(sub, v, at, root)) != 1:
            out.append((at, "oneOf"))


def _errs(s, v, at, root):
    o = []
    schema_errors(s, v, at, root, o)
    return o


def validate_structure(doc: dict) -> None:
    """명세 모양 — 서버 공용 검증기(imports.validate_migration)가 있으면 그것, 없으면 정본 스키마 파일로. 그 뒤 스키마로 못 쓰는 규칙:
    original_id 중복 · previous_revision 은 null(CLI 가 채움) · 실제 날짜·시각 · source_created_at 과 source_date 동시. 실패 = Reject(모든 위반)."""
    import imports
    fn = getattr(imports, "validate_migration", None)
    if fn is not None:
        try:
            fn(doc)
        except NotImplementedError:
            fn = None
        except Exception as e:   # db.CrmError(CONTRACT-INVALID) — 메시지엔 경로·규칙 이름만(값 없음)
            if getattr(e, "code", None):
                raise Reject([("$", f"{e.code}: {getattr(e, 'message', '')}")]) from None
            raise
    if fn is None:
        schema = load_schema()
        probs = _errs(schema, doc, "$", schema)
        if probs:
            raise Reject(probs)
    probs = []
    seen = set()
    for i, r in enumerate(doc["rows"]):
        at = f"$.rows[{i}]"
        if r["original_id"] in seen:
            probs.append((f"{at}.original_id", "duplicate"))   # 한 명세 = 원본마다 최신 개정 한 행
        seen.add(r["original_id"])
        if r.get("previous_revision") is not None:
            probs.append((f"{at}.previous_revision", "must-be-null"))   # 부모 개정은 CLI 가 서버 대응표로 정한다(생성기는 비워 둔다)
        if r.get("source_created_at") is not None and r.get("source_date") is not None:
            probs.append((at, "source_created_at-xor-source_date"))
        if r.get("source_created_at") is not None and not _is_ts(r["source_created_at"]):
            probs.append((f"{at}.source_created_at", "timestamp"))
        if r.get("source_date") is not None and not _is_date(r["source_date"]):
            probs.append((f"{at}.source_date", "date"))
    if not _is_ts(doc["reviewed_at"]):
        probs.append(("$.reviewed_at", "timestamp"))
    if probs:
        raise Reject(probs)


# ── 대상 해석 ──────────────────────────────────────────────

def resolve_object(defs: list[dict], ref: dict) -> tuple[str | None, str | None]:
    """정의 목록에서 대상 오브젝트 하나를 찾는다 — std_id → 없으면 template_slot → 없으면 key 순(계층마다 정확히 하나).
    반환 (object_id, None) 또는 (None, 규칙). 이름(label)으로 찾거나 만들지 않는다."""
    tiers = (("std_id", ref.get("std_id")), ("template_slot", ref.get("template_slot")), ("key", ref.get("key")))
    if not any(v for _, v in tiers):
        return None, "object-ref-empty"
    for col, want in tiers:
        if not want:
            continue
        hits = [d for d in defs if d.get(col) == want]
        if len(hits) == 1:
            return hits[0]["id"], None
        if len(hits) > 1:
            return None, f"object-ambiguous:{col}"
    return None, "object-not-found"


def _field_problems(defn: dict, fields: dict | None, at: str) -> list:
    """일반 필드는 오브젝트 해석 뒤 field key 로 — 없는 필드·보관된 필드·관계 필드(references 로 보낼 것) 거절."""
    if not fields:
        return []
    by_key = {f["key"]: f for f in defn.get("fields", [])}
    out = []
    for k in fields:
        f = by_key.get(k)
        if f is None or f.get("archived_at"):
            out.append((f"{at}.fields.{k}", "field-not-found"))
        elif f["value_type"] == "reference":
            out.append((f"{at}.fields.{k}", "reference-in-fields"))
    return out


def _target(t) -> tuple[str, str]:
    """target 원소 → (형식, 값). 형식 목록·개수는 스키마가 보장한다. original_id 대상엔 정확한 판 revision 이 붙을 수 있다
    (붙으면 _rev_problem 이 명세 안 그 행의 revision 과 대조)."""
    if "original_id" in t:
        return "original_id", t["original_id"]
    (form, v), = t.items()
    return form, v


def _rev_problem(t, src) -> str | None:
    """original_id 참조의 revision 은 정확한 판(r3). 명세 안 그 행의 지금 revision 이거나, 이미 적용된 옛 판일 수 있다(옛 판 참조 유지).
    옛 판이 서버에 있는지는 서버가 같은 트랜잭션에서 판정한다(없으면 REF-NOT-FOUND) — CLI 는 거절하지 않는다."""
    return None


def ref_problems(db, uid: str, defn: dict, row: dict, at: str, rows_by_oid: dict, obj_of_row) -> list:
    """references 검사(읽기만): field_key = 그 오브젝트의 관계 필드 · 대상 종류 일치 · original_id = 명세 안 행(종류·대상 오브젝트 일치)
    · record_id = 살아 있는 기존 기록 판 · item_id = 필드 대상 오브젝트의 기존 항목 · candidate_id = 기존 후보."""
    out = []
    by_key = {f["key"]: f for f in defn.get("fields", [])}
    for j, x in enumerate(row.get("references") or []):
        a = f"{at}.references[{j}]"
        f = by_key.get(x["field_key"])
        if f is None or f.get("archived_at"):
            out.append((a, "field-not-found"))
            continue
        if f["value_type"] != "reference":
            out.append((a, "not-reference-field"))
            continue
        form, v = _target(x["target"])
        tk, tobj = f.get("target_kind"), f.get("target_object_id")
        if form == "original_id":
            src = rows_by_oid.get(v)
            if src is None:
                out.append((a, "original-not-in-manifest"))   # 명세는 전량 수출 — 밖의 원본은 가리키지 않는다
            elif src["target_kind"] != tk:
                out.append((a, "target-kind-mismatch"))
            elif tk == "item" and tobj and obj_of_row(src) != tobj:
                out.append((a, "target-object-mismatch"))
            elif _rev_problem(x["target"], src):
                out.append((a, "revision-mismatch"))
        elif form == "record_id":
            if tk != "record":
                out.append((a, "target-kind-mismatch"))
            else:
                try:
                    db.record_get(uid, v)   # 정확한 판 ID — 숨김·삭제된 판은 RECORD-MISSING
                except db.CrmError:
                    out.append((a, "record-not-found"))
        elif form == "item_id":
            if tk != "item":
                out.append((a, "target-kind-mismatch"))
                continue
            try:
                it = db.item_get(uid, v)
            except db.CrmError:
                out.append((a, "item-not-found"))
                continue
            if it.get("archived_at") or not it.get("revision"):
                out.append((a, "item-not-found"))
            elif tobj and it.get("object_id") != tobj:
                out.append((a, "target-object-mismatch"))
        else:   # candidate_id
            if tk != "candidate":
                out.append((a, "target-kind-mismatch"))
                continue
            try:
                db.candidate_get(uid, v)
            except db.CrmError:
                out.append((a, "candidate-not-found"))
    return out


ATTACH_TARGETS = {"attach_to": ("original_id", "item_id", "candidate_id"), "axes": ("original_id", "item_id")}


def attach_problems(db, uid: str, row: dict, at: str, rows_by_oid: dict) -> list:
    """기록 행 attach(record_attach): attach_to = 명세 안 item 행·기존 항목·기존 후보 · axes = 명세 안 item 행·기존 항목.
    항목의 종류(기준·결정·분류 역할)는 서버 apply_row 의 record_attach 검증이 판정한다. use_at·unattached 는 메모(스키마가 본다)."""
    att = row.get("attach")
    if not att:
        return []
    out = []
    for k, forms in ATTACH_TARGETS.items():
        for j, t in enumerate(att.get(k) or []):
            a = f"{at}.attach.{k}[{j}]"
            form, v = _target(t)
            if form not in forms:
                out.append((a, "target-form"))
            elif form == "original_id":
                src = rows_by_oid.get(v)
                if src is None:
                    out.append((a, "original-not-in-manifest"))
                elif src["target_kind"] != "item":
                    out.append((a, "target-kind-mismatch"))
                elif _rev_problem(t, src):
                    out.append((a, "revision-mismatch"))
            elif form == "item_id":
                try:
                    it = db.item_get(uid, v)
                    if it.get("archived_at") or not it.get("revision"):
                        out.append((a, "item-not-found"))
                except db.CrmError:
                    out.append((a, "item-not-found"))
            else:
                try:
                    db.candidate_get(uid, v)
                except db.CrmError:
                    out.append((a, "candidate-not-found"))
    return out


# ── 참조 · 의존성 순서 ──────────────────────────────────────

def row_deps(r: dict) -> set[str]:
    """이 행이 기대는 명세 안 다른 행의 original_id — references · 기록 연결(attach)."""
    deps = set()
    for x in (r.get("references") or []):
        t = x.get("target") if isinstance(x, dict) else None
        if isinstance(t, dict) and t.get("original_id"):
            deps.add(t["original_id"])
    att = r.get("attach") if isinstance(r.get("attach"), dict) else {}
    for k in ATTACH_TARGETS:
        for x in (att.get(k) or []):
            if isinstance(x, dict) and x.get("original_id"):
                deps.add(x["original_id"])
    return deps


def order_rows(rows: list[dict]) -> list[int]:
    """의존성 순서 — (오브젝트·필드는 해석 단계에서 이미 있어야 한다) 축·선행 항목 → 기록에 의존 안 하는 항목
    → 기록(+record_attach) → 기록을 참조하는 항목(발행 등). 1차 키 = 단계, 같은 단계 안은 명세 순서. 명세 안 참조는 위상 정렬로 보장.
    순환·자기 참조 = Reject. 명세 밖 original_id 는 여기서 무시(해석 단계가 거절)."""
    import heapq
    by_id = {r["original_id"]: i for i, r in enumerate(rows)}
    deps, probs = {}, []
    for i, r in enumerate(rows):
        d = row_deps(r)
        if r["original_id"] in d:
            probs.append((f"$.rows[{i}]", "self-reference"))
        deps[i] = {by_id[x] for x in d if x in by_id and by_id[x] != i}
    if probs:
        raise Reject(probs)
    memo = {}

    def needs_record(i, stack=()):
        if i in memo:
            return memo[i]
        if i in stack:
            return False   # 순환은 아래 위상 정렬이 잡는다
        v = any(rows[j]["target_kind"] == "record" or needs_record(j, stack + (i,)) for j in deps[i])
        memo[i] = v
        return v

    def phase(i):
        if rows[i]["target_kind"] == "record":
            return 2
        return 3 if needs_record(i) else 1

    indeg = {i: len(deps[i]) for i in deps}
    users = {i: [] for i in deps}
    for i, ds in deps.items():
        for j in ds:
            users[j].append(i)
    heap = [(phase(i), i) for i in deps if indeg[i] == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        _, i = heapq.heappop(heap)
        out.append(i)
        for u in users[i]:
            indeg[u] -= 1
            if indeg[u] == 0:
                heapq.heappush(heap, (phase(u), u))
    if len(out) != len(rows):
        raise Reject([(f"$.rows[{i}]", "dependency-cycle") for i in sorted(set(deps) - set(out))])
    return out


# ── 시각 ──────────────────────────────────────────────────

def _kst():
    import db
    return db.KST


def _fmt(v) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, str):
        v = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v.astimezone(_kst()).strftime("%Y-%m-%d %H:%M KST")


def next_kst_window(now: _dt.datetime | None = None) -> _dt.datetime:
    """다음 KST 하루 구간 시작(KST 자정)."""
    k = _kst()
    n = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(k)
    return _dt.datetime.combine(n.date() + _dt.timedelta(days=1), _dt.time(0, 0), tzinfo=k)


# ── 출력 ──────────────────────────────────────────────────

def _say(*a):
    print(*a, flush=True)


def _err(*a):
    print(*a, file=sys.stderr, flush=True)


def _print_reject(e: Reject, limit: int = 50):
    for at, rule in e.problems[:limit]:
        _err(f"  거절\t{at}\t{rule}")
    if len(e.problems) > limit:
        _err(f"  … 외 {len(e.problems) - limit}건")
    _err(f"[MANIFEST-INVALID] 명세 거절 {len(e.problems)}건 — 로컬 검토표·정제 명세를 고친 뒤 다시 validate-only")


# ── 명세 준비(모든 서브커맨드 공통) ─────────────────────────

def prepare(path: str, uid: str) -> dict:
    """적재 → 구조(정본 스키마) → 해시 → 대상 계정 → 대상 해석 → 참조·연결 → 순서. 읽기만. 반환 계획 dict. 실패 = Reject 또는 db.CrmError."""
    import db
    doc, _raw = load_manifest(path)
    validate_structure(doc)
    sha = manifest_hash(doc)
    if sha != doc["manifest_sha256"]:
        raise Reject([("$.manifest_sha256", "hash-mismatch")])
    if doc["target_user_id"] != uid:
        raise Reject([("$.target_user_id", "user-mismatch")])   # --user 로 대상 계정을 한 번 더 확인
    db.user_get(uid)   # 없으면 AUTH-INVALID
    rows = doc["rows"]
    order = order_rows(rows)
    defs = db.definitions(uid, include_archived=True)
    by_oid = {d["id"]: d for d in defs}
    resolved, probs = {}, []
    for i, r in enumerate(rows):
        if r["target_kind"] != "item":
            continue
        at = f"$.rows[{i}]"
        oid, why = resolve_object(defs, r["object_ref"])
        if why:
            probs.append((f"{at}.object_ref", why))
            continue
        if by_oid[oid].get("archived_at"):
            probs.append((f"{at}.object_ref", "object-archived"))
            continue
        resolved[i] = oid
        probs += _field_problems(by_oid[oid], r.get("fields"), at)
    if probs:
        raise Reject(probs)
    manifest_ids = {r["original_id"]: r for r in rows}
    idx = {r["original_id"]: i for i, r in enumerate(rows)}

    def obj_of_row(src):
        return resolved.get(idx[src["original_id"]])

    for i, r in enumerate(rows):
        at = f"$.rows[{i}]"
        if i in resolved:
            probs += ref_problems(db, uid, by_oid[resolved[i]], r, at, manifest_ids, obj_of_row)
        elif r.get("references"):
            probs.append((f"{at}.references", "record-uses-attach"))   # 기록의 연결은 attach(record_attach)
        if r["target_kind"] == "item" and r.get("attach"):
            probs.append((f"{at}.attach", "record-only"))
        probs += attach_problems(db, uid, r, at, manifest_ids)
    if probs:
        raise Reject(probs)
    batch_id = "mb_" + sha[:24]   # 명세 해시로 정한다 — 같은 명세의 run·resume 은 같은 배치
    return {"doc": doc, "rows": rows, "order": order, "object_ids": resolved, "sha": sha, "batch_id": batch_id}


def server_row(plan: dict, i: int, previous_revision: str | None) -> dict:
    """명세 행 → imports.migration_apply_row 입력. 대상 오브젝트 ID(object_id)만 해석해 넣고 object_ref 는 뺀다.
    previous_revision 은 명세가 아니라 서버 대응표에서 정한 값(parent_revision). references·attach 는 원형 그대로 —
    서버가 같은 트랜잭션에서 대응표로 해석한다."""
    r = dict(plan["rows"][i])
    r.pop("object_ref", None)
    r["object_id"] = plan["object_ids"].get(i)
    r["previous_revision"] = previous_revision
    return r


def parent_revision(ents: list[dict], original_id: str, revision: str) -> str | None:
    """apply_row 에 넘길 previous_revision — 그 원본의 최신 접수 판(최초면 None).
    같은 revision 을 다시 보내는 경우(중단 뒤 재전송·재실행)엔 그 접수 행이 처음 받은 부모를 그대로 — 요청이 같아야 duplicate 가 된다."""
    mine = [e for e in ents if e["original_id"] == original_id]
    if not mine:
        return None
    latest = [e for e in mine if e.get("is_latest_received")] or mine[-1:]
    latest = latest[0]
    if latest["source_revision"] != revision:
        return latest["source_revision"]
    pid = latest.get("previous_entry_id")
    if not pid:
        return None
    par = [e for e in mine if e.get("id") == pid]
    return par[0]["source_revision"] if par else None


# ── 대응표 · 진행 ──────────────────────────────────────────

def entries(uid: str, stream_id: str) -> list[dict] | None:
    """서버 대응표(행 단위). 서버 함수가 아직 없으면 None — 진행 계산 생략."""
    import imports
    fn = getattr(imports, "migration_entries", None)
    if fn is None:
        return None
    try:
        return fn(uid, stream_id)
    except NotImplementedError:
        return None


def progress(plan: dict, ents: list[dict] | None) -> dict:
    """명세 행마다 완료(같은 개정이 현재 적용) · 미처리 · 개정 다름(새 판 대상) 을 센다."""
    if ents is None:
        return {"known": False}
    cur = {e["original_id"]: e for e in ents if e.get("is_current_applied") and e.get("state") in DONE_STATES}
    done, todo, changed = [], [], []
    for i in plan["order"]:
        r = plan["rows"][i]
        e = cur.get(r["original_id"])
        if e and e.get("source_revision") == r["revision"]:
            done.append(i)
        elif e:
            changed.append(i)
            todo.append(i)
        else:
            todo.append(i)
    return {"known": True, "done": done, "todo": todo, "changed": changed}


# ── 서브커맨드 ─────────────────────────────────────────────

def _counts_line(c: dict) -> str:
    return " · ".join(f"{k} {c.get(k, 0)}" for k in ("applied", "noop", "duplicate", "skipped"))


def cmd_validate(path: str, uid: str) -> int:
    plan = prepare(path, uid)
    kinds = {}
    for r in plan["rows"]:
        kinds[r["target_kind"]] = kinds.get(r["target_kind"], 0) + 1
    _say(f"명세 통과: 행 {len(plan['rows'])} (" + " · ".join(f"{k} {kinds.get(k, 0)}" for k in TARGET_KINDS) + ")")
    _say(f"명세 해시 일치 {plan['sha'][:12]}… · 배치 {plan['batch_id']} · 대상 오브젝트 해석 {len(plan['object_ids'])}행")
    st = _stream_read(uid)
    if st is not None:
        _print_window(st)
        if st["id"] != plan["doc"]["source_instance_id"]:
            _err(f"[SOURCE-INSTANCE-MISMATCH] 명세 source_instance_id 가 이전 입력원({st['id']})과 달라요.")
            return EXIT_ERR
        pr = progress(plan, entries(uid, st["id"]))
        if pr["known"]:
            _say(f"진행: 완료 {len(pr['done'])} · 남음 {len(pr['todo'])} (그중 새 개정 {len(pr['changed'])})")
        else:
            _say("진행: 서버 대응표 조회 함수 없음 — 계산 생략")
    else:
        _say("입력원: 아직 없음(첫 run 에서 만든다)")
    _say(f"스위치 BOARD_MIGRATION = {'켜짐' if _switch() else '꺼짐'}")
    return EXIT_OK


def _switch() -> bool:
    import envnames
    return envnames.switch_on("BOARD_MIGRATION_ENABLED")


def _stream_read(uid: str) -> dict | None:
    """읽기 전용 입력원 조회. migration_stream 은 없으면 만들므로 validate-only·status 에선 migration_stream_get 이 있을 때만 쓴다."""
    import imports
    fn = getattr(imports, "migration_stream_get", None)
    if fn is None:
        return None
    try:
        return fn(uid)
    except NotImplementedError:
        return None


def _print_window(st: dict):
    _say(f"이전 입력원: {st['id']}")
    _say(f"병행 기간: 시작 {_fmt(st.get('migration_started_at'))} · 종료 예정 {_fmt(st.get('migration_ends_at'))}"
         f" · 종료 {_fmt(st.get('migration_closed_at'))}")
    counts = st.get("counts") or {}
    if counts:
        _say("대응표: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def _window_block(st: dict) -> str | None:
    if st.get("migration_closed_at"):
        return "[MIGRATION-WINDOW-CLOSED] 병행이 종료된 입력원이에요 — 기존 창을 다시 열지 않아요(별도 이전 범위·기간 필요)."
    ends = st.get("migration_ends_at")
    if ends:
        e = _dt.datetime.fromisoformat(ends.replace("Z", "+00:00")) if isinstance(ends, str) else ends
        if e <= _dt.datetime.now(_dt.timezone.utc):
            return "[MIGRATION-WINDOW-CLOSED] 병행 기간이 끝났어요 — 원작자가 결정했으면 extend 로 7일 연장한 뒤 resume."
    return None


def cmd_apply(path: str, uid: str, limit: int | None, resume: bool) -> int:
    import db
    import imports
    if not _switch():
        _err("[SWITCH-OFF] CRM_BOARD_MIGRATION_ENABLED=1 이 아니에요 — 이전 적용은 스위치를 켠 뒤에만.")
        return EXIT_ERR
    plan = prepare(path, uid)
    st = _stream_read(uid)   # 입력원 생성은 init 만
    if st is None:
        _err("[MIGRATION-NOT-STARTED] 이전 입력원이 없어요 — init 먼저(그 ID 로 명세 source_instance_id 를 만든다).")
        return EXIT_ERR
    if st["id"] != plan["doc"]["source_instance_id"]:
        _err(f"[SOURCE-INSTANCE-MISMATCH] 명세 source_instance_id 가 이 계정의 이전 입력원({st['id']})과 달라요 — "
             "그 ID 로 명세를 다시 만든 뒤 validate-only 부터.")
        return EXIT_ERR
    block = _window_block(st)
    if block:
        _err(block)
        return EXIT_ERR
    ents = entries(uid, st["id"])
    if ents is None:
        _err("[SERVER-PENDING] 서버 대응표 조회 함수(imports.migration_entries)가 없어 previous_revision 을 정할 수 없어요.")
        return EXIT_ERR
    pr = progress(plan, ents)
    if resume:
        queue, skipped = pr["todo"], len(pr["done"])
    else:
        queue, skipped = plan["order"], 0
    c = {"applied": 0, "noop": 0, "duplicate": 0, "skipped": skipped}
    sent = 0       # 서버가 받은 행(이미 받은 같은 요청 = duplicate 포함)
    fresh = 0      # 이번에 새로 접수된 행(applied·noop) — --limit 는 이것을 센다
    for i in queue:
        r = plan["rows"][i]
        if limit is not None and fresh >= limit:
            break
        row = server_row(plan, i, parent_revision(ents, r["original_id"], r["revision"]))
        try:
            res = imports.migration_apply_row(uid, st["id"], row, batch_id=plan["batch_id"], manifest_sha256=plan["sha"],
                                              transform_version=plan["doc"]["transform_version"])
        except db.CrmError as e:
            _report_counts(c)
            left = len(queue) - sent
            if e.code in PARTIAL_CODES:
                when = _resume_at(e) or next_kst_window()
                _err(f"[{e.code}] 일일 한도에 닿았어요 — 행 {i}({r['original_id']})부터 {left}행 남음(일부만 적용).")
                _err(f"다음 KST 구간 {_fmt(when)} 이후에 resume 하세요.")
                return EXIT_PARTIAL
            _err(f"[{e.code}] 행 {i}({r['original_id']}) 에서 멈췄어요 — 남은 {left}행은 보내지 않았어요. {e.message}"
                 + (f" {e.action}" if e.action else ""))
            return EXIT_ERR
        sent += 1
        if res.get("duplicate") or res.get("state") == "duplicate":
            c["duplicate"] += 1
        else:
            c[res["state"]] = c.get(res["state"], 0) + 1
            fresh += 1
    _report_counts(c)
    remaining = len(queue) - sent
    if remaining > 0:
        _say(f"--limit {limit} 로 멈춤 — 남은 행 {remaining}. 대조 뒤 resume 하세요.")
    elif c["applied"] == 0:
        _say("업무 변경 0 — 같은 명세 재실행 확인(noop·duplicate 는 업무 쓰기 없음).")
    return EXIT_OK


def _resume_at(e):
    """RATE-LIMIT 의 재개 시각 — 서버가 CrmError 에 resume_at(ISO 또는 datetime)을 실으면 그것, 아니면 None."""
    v = getattr(e, "resume_at", None)
    if v is None and isinstance(getattr(e, "detail", None), dict):
        v = e.detail.get("resume_at")
    if isinstance(v, str):
        try:
            return _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return v


def _report_counts(c: dict):
    _say(f"결과: {_counts_line(c)} · 업무 변경 {c.get('applied', 0)}")


def cmd_status(path: str | None, uid: str) -> int:
    import db
    db.user_get(uid)
    import imports
    if getattr(imports, "migration_stream_get", None) is None:
        _err("[SERVER-PENDING] 서버 입력원 읽기 함수(imports.migration_stream_get)가 아직 없어요.")
        return EXIT_ERR
    st = _stream_read(uid)
    if st is None:
        _say("입력원: 없음(첫 run 에서 만든다)")
        return EXIT_OK
    _print_window(st)
    if path:
        plan = prepare(path, uid)
        pr = progress(plan, entries(uid, st["id"]))
        if pr["known"]:
            _say(f"명세 진행: 완료 {len(pr['done'])} · 남음 {len(pr['todo'])} (그중 새 개정 {len(pr['changed'])})")
    return EXIT_OK


def cmd_init(uid: str) -> int:
    """이전 입력원만 만든다(이미 있으면 그대로) — 그 ID 를 PC 명세 생성기의 source_instance_id 로 쓴다. 업무 자료 쓰기 없음."""
    import db
    import imports
    db.user_get(uid)
    before = _stream_read(uid)
    st = before or imports.migration_stream(uid, "board 이전")
    _say(f"이전 입력원: {st['id']} ({'이미 있음' if before else '새로 만듦'}) — 명세 source_instance_id 로 쓰세요.")
    return EXIT_OK


def cmd_extend(uid: str) -> int:
    import imports
    if getattr(imports, "migration_stream_get", None) is None:
        _err("[SERVER-PENDING] 서버 입력원 읽기 함수(imports.migration_stream_get)가 아직 없어요.")
        return EXIT_ERR
    st = _stream_read(uid)
    if st is None:
        _err("[MIGRATION-NOT-STARTED] 이전 입력원이 없어요 — 연장할 병행 기간이 없어요.")
        return EXIT_ERR
    r = imports.migration_extend(uid, st["id"], 7)
    _say(f"병행 기간을 7일 연장했어요 — 종료 예정 {_fmt(r.get('migration_ends_at'))} (감사 migration_extend)")
    return EXIT_OK


def cmd_close(path: str, uid: str) -> int:
    """종료 — ① 최종 명세의 모든 행이 같은 개정으로 현재 적용(미처리 0) ② 같은 명세 전 행 재전송 = 전부 duplicate(업무 변경 0)
    일 때만 최종 해시를 기록한다. 재전송은 이미 받은 (original_id, revision) 뿐이라 업무 쓰기가 생기지 않는다(다른 내용이면 서버가 충돌로 거절).
    서버는 병행 기간이 끝난 뒤에도 같은 요청엔 duplicate 결과만 돌려주므로 기간 사전 검사를 하지 않는다."""
    import db
    import imports
    if not _switch():
        _err("[SWITCH-OFF] CRM_BOARD_MIGRATION_ENABLED=1 이 아니에요 — 종료 확인(재전송)은 스위치를 켠 뒤에만.")
        return EXIT_ERR
    plan = prepare(path, uid)
    st = _stream_read(uid)
    if st is None:
        _err("[MIGRATION-NOT-STARTED] 이전 입력원이 없어요.")
        return EXIT_ERR
    if st["id"] != plan["doc"]["source_instance_id"]:
        _err(f"[SOURCE-INSTANCE-MISMATCH] 명세 source_instance_id 가 이전 입력원({st['id']})과 달라요.")
        return EXIT_ERR
    if st.get("migration_closed_at"):
        _err("[MIGRATION-WINDOW-CLOSED] 이미 종료된 입력원이에요.")
        return EXIT_ERR
    ents = entries(uid, st["id"])
    pr = progress(plan, ents)
    if not pr["known"]:
        _err("[SERVER-PENDING] 서버 대응표 조회 함수가 없어 미처리 0 을 확인할 수 없어요.")
        return EXIT_ERR
    if pr["todo"]:
        _err(f"[MIGRATION-INCOMPLETE] 미처리 {len(pr['todo'])}행(새 개정 {len(pr['changed'])}) — resume 으로 끝낸 뒤 close.")
        return EXIT_ERR
    for i in plan["order"]:
        r = plan["rows"][i]
        row = server_row(plan, i, parent_revision(ents, r["original_id"], r["revision"]))
        try:
            res = imports.migration_apply_row(uid, st["id"], row, batch_id=plan["batch_id"], manifest_sha256=plan["sha"],
                                              transform_version=plan["doc"]["transform_version"])
        except db.CrmError as e:
            _err(f"[{e.code}] 종료 확인 재전송이 행 {i}({r['original_id']}) 에서 거절됐어요 — 종료하지 않았어요. {e.message}")
            return EXIT_ERR
        if not res.get("duplicate"):
            _err(f"[MIGRATION-NOT-IDLE] 행 {i}({r['original_id']}) 재전송이 업무 변경({res.get('state')})으로 처리됐어요 — 종료하지 않았어요.")
            return EXIT_ERR
    _say(f"종료 확인: 같은 명세 {len(plan['order'])}행 재전송 = 전부 duplicate · 업무 변경 0")
    r = imports.migration_close(uid, st["id"], plan["sha"])
    _say(f"병행 종료: {_fmt(r.get('migration_closed_at'))} · 최종 명세 해시 {plan['sha'][:12]}… (감사 migration_close)")
    return EXIT_OK


# ── 진입 ──────────────────────────────────────────────────

def _parse(argv: list[str]):
    if not argv:
        return None
    cmd, rest = argv[0], argv[1:]
    opts = {"user": None, "limit": None}
    pos = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--user", "--limit"):
            if i + 1 >= len(rest):
                return None
            opts[a[2:]] = rest[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            return None
        pos.append(a)
        i += 1
    if not opts["user"]:
        return None
    if opts["limit"] is not None:
        if not opts["limit"].isdigit() or int(opts["limit"]) < 1 or cmd not in ("run", "resume"):
            return None
        opts["limit"] = int(opts["limit"])
    need = {"validate-only": (1, 1), "run": (1, 1), "resume": (1, 1), "close": (1, 1), "status": (0, 1), "extend": (0, 0),
            "init": (0, 0)}
    if cmd not in need or not (need[cmd][0] <= len(pos) <= need[cmd][1]):
        return None
    return cmd, pos, opts


def main(argv: list[str]) -> int:
    parsed = _parse(argv)
    if parsed is None:
        _err(USAGE)
        return EXIT_USAGE
    if not os.environ.get("DATABASE_URL", "").strip():
        _err("[DB-CONFIG] DATABASE_URL 을 명령 앞에 명시하세요(.env·기본 주소는 쓰지 않아요).")
        return EXIT_USAGE
    cmd, pos, opts = parsed
    import db
    try:
        if cmd == "validate-only":
            return cmd_validate(pos[0], opts["user"])
        if cmd in ("run", "resume"):
            return cmd_apply(pos[0], opts["user"], opts["limit"], resume=(cmd == "resume"))
        if cmd == "status":
            return cmd_status(pos[0] if pos else None, opts["user"])
        if cmd == "extend":
            return cmd_extend(opts["user"])
        if cmd == "init":
            return cmd_init(opts["user"])
        return cmd_close(pos[0], opts["user"])
    except Reject as e:
        _print_reject(e)
        return EXIT_ERR
    except db.CrmError as e:
        _err(f"[{e.code}] {e.message}" + (f" {e.action}" if e.action else ""))
        return EXIT_ERR
    except NotImplementedError:
        _err("[SERVER-PENDING] 서버 이전 함수가 아직 구현 전이에요(imports.migration_*).")
        return EXIT_ERR
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
