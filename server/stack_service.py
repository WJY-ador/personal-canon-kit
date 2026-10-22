"""PR ②-b 쌓기 단계 서비스 (2026-10-22, 구현계획 §3) — 정의 스냅샷 · stack/1 결과 재검증 · 미리보기 · 항목 제외 · stage 규칙.

서버는 모델을 부르지 않는다. PC(agent/stack.py)가 보낸 값(정리 단위·변경 목록·막힘)을 결정적으로 다시 검사하고,
사용자가 확인할 미리보기를 임시 보관(tmp)에 만든다. 모델 출력으로 확정·인용·한도·stage 를 정하지 않는다 —
그런 키는 계약 거부다. 근거 유형(ref_kind) 문자열로 권한을 주지 않는다: source_id 는 서버가 실제 대상으로 해석하고(못 하면 unresolved),
explicit_id·inferred 는 소유·존재·정의를 검사한다. inferred 는 확인 카드에 「추정」 으로 보일 뿐 자동 적용 경로가 없다
(적용은 확정 트랜잭션 하나뿐 — 사용자 [확정] 1번).

이 모듈의 함수는 호출자 트랜잭션(c) 안에서 돈다 — 자체 커밋 없음. db 쪽 진입점은 db.job_definitions·job_result·stack_exclude.
"""
import hashlib
import json
import re

import contract
import db
import imports
from db import CrmError

CONTRACT = "stack/1"
RULE_VERSION = 1                                   # stage 규칙표 버전(compute_stage)
STAGES = ("draft", "organized", "confirmed")
KINDS = ("judgment", "event", "classify", "custom")   # principle → judgment · event → event · 분류 역할 → classify · 그 밖 → custom
# 내용 제한(메인 판정 2026-10-22) — PC 프롬프트에 그대로 싣는다. 서버의 결정적 검사는 길이·상한 + imports.LEAK_RULES(URL·이메일·로컬 경로)
CONTENT_RULES = ("타인의 실명·핸들·이메일·URL·로컬 경로·소속·정확한 날짜 조합을 쓰지 않는다",
                 "타인의 말은 업무 방식·주장·일반화한 문제/개선안으로만 옮긴다",
                 "일반화할 수 없는 타인 정보는 넣지 않는다(빈 칸으로 둔다)")
PROTECTED_FIELDS = ("code", "decision")            # 쌓기 단계가 쓰지 못하는 항목 열(코드 = 적용 뒤 불변, decision = 확인 1번만)
FORBIDDEN_FIELD_KEYS = frozenset(PROTECTED_FIELDS) | {"confirmed", "stage", "citation", "cite", "eligible", "quota", "limit",
                                                    "provenance", "generation_kind", "author_kind", "text_process"}
REF_KINDS = ("source_id", "explicit_id", "inferred", "unresolved")
IDENTITY_SCHEMES = ("code",)                       # 안정 원천 ID = items.code(오브젝트 안 유일, 적용 뒤 불변)
BLOCK_CODES = ("judgment_confirm", "missing_required", "unresolved_target", "ambiguous")
OPEN_BLOCKS = ("missing_required", "unresolved_target", "ambiguous")   # 남아 있으면 [확정] 잠금. judgment_confirm 은 확인 1번 자체가 푼다
MAX_UNITS, MAX_CHANGES, MAX_BLOCKED, MAX_TEXT, QUESTION_MAX = 40, 60, 60, 4000, 300
UNIT_JOIN = "\n\n"                                 # 최종 정리 글 = units[].text 를 순서대로 이 구분자로 합친 것
TITLE_MAX = 300
MODEL_META_MAX = 2048                              # model_meta JSON 바이트
RESTACK_ANSWERS_MAX, RESTACK_ANSWER_MAX = 20, 1000
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_BUILTIN = ({"key": "title", "type": "text", "required": True, "enum": None, "max": TITLE_MAX, "builtin": True},
            {"key": "body", "type": "text", "required": False, "enum": None, "max": db.JOB_TEXT_MAX_CP, "builtin": True})
_BLOCK_QUESTIONS = {"missing_required": "꼭 필요한 칸이 비어 있어요: {f}. 알려 주시면 채워 둘게요.",
                    "unresolved_target": "어느 항목을 말하는지 못 찾았어요. 항목 이름을 알려 주세요."}

ENVELOPE_KEYS = frozenset({"contract", "job_id", "input_version", "definitions_version", "result_version", "edit_generation",
                           "parent_result_version"})
RESULT_KEYS = frozenset({"lease_generation", "envelope", "units", "changes", "blocked", "model_meta"})
UNIT_KEYS = frozenset({"uid", "text", "change_ids"})
CHANGE_KEYS = frozenset({"cid", "op", "object_id", "target", "fields", "links", "depends_on"})
BLOCK_KEYS = frozenset({"cid", "code", "question"})


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha(obj) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def bad(path, rule):
    """계약 위반 — 경로·규칙 이름만(값은 넣지 않는다). imports._bad 와 같은 코드."""
    return CrmError("CONTRACT-INVALID", f"{path}: {rule}", "결과를 계약(stack/1)에 맞추세요.")


def defs_changed():
    return CrmError("JOB-DEFS", "구조 정의가 이 작업을 시작한 뒤 바뀌었어요.", "이 결과는 버리고 다시 정리하세요.")


# ── 정의 스냅샷 ─────────────────────────────────────────

def _index(c, uid):
    """적용·미보관 오브젝트 → {row, fields(db 모양), kind}. 스냅샷과 결과 검사가 같은 읽기를 쓴다."""
    rows = db._all(c, f"SELECT {db._DEF_COLS} FROM object_defs WHERE user_id=%s AND revision > 0 AND archived_at IS NULL ORDER BY id",
                   (uid,))
    cls = db._one(c, "SELECT object_id FROM user_roles WHERE user_id=%s AND role='classify'", (uid,))
    cls = cls["object_id"] if cls else None
    out = {}
    for r in rows:
        sk = r["semantic_kind"]
        kind = "judgment" if sk == "principle" else "event" if sk == "event" else "classify" if r["id"] == cls else "custom"
        out[r["id"]] = {"row": r, "fields": db._fields(c, uid, r["id"]), "kind": kind}
    return out


def _snap_object(oid, o):
    r = o["row"]
    fields = list(_BUILTIN)
    rels = []
    for f in o["fields"]:
        if f["value_type"] == "reference":
            rels.append({"key": f["key"], "target_kind": f["target_kind"], "target_object": f["target_object_id"],
                         "cardinality": "many" if f["multiple"] else "one", "required": bool(f["required"])})
        else:
            fields.append({"key": f["key"], "type": f["value_type"], "required": bool(f["required"]),
                           "enum": [x["value"] for x in f["options"]] if f["value_type"] == "select" else None, "max": None})
    return {"id": oid, "key": r["key"], "origin": contract.origin_of(r), "label": r["label"], "kind": o["kind"],
            "revision": r["revision"], "fields": fields, "relations": rels,
            "identity": [{"scheme": s, "field": "code"} for s in IDENTITY_SCHEMES], "protected_fields": list(PROTECTED_FIELDS),
            "stage_values": list(STAGES), "confirm_required": o["kind"] == "judgment"}


def snapshot(c, uid, index=None):
    """정의 스냅샷 — {definitions_version, rule_version, objects, content_rules}. definitions_version = 나머지의 정규화 JSON sha256 hex.
    사람·출처는 objects 에 없다(연결 대상으로만 — ref_kind explicit_id, target_kind person|source)."""
    index = index if index is not None else _index(c, uid)
    body = {"rule_version": RULE_VERSION, "objects": [_snap_object(oid, o) for oid, o in index.items()],
            "content_rules": list(CONTENT_RULES)}
    return {"definitions_version": sha(body), **body}


# ── 결과 재검증 ─────────────────────────────────────────

def _leak(path, v):
    for rule, rx in imports.LEAK_RULES:
        if rx.search(v):
            raise bad(path, f"{rule}_not_allowed")


def _str(v, path, max_cp, empty_ok=False):
    if not isinstance(v, str):
        raise bad(path, "not_string")
    if not empty_ok and not v.strip():
        raise bad(path, "empty")
    if len(v) > max_cp:
        raise bad(path, "too_long")
    _leak(path, v)
    return v


def _rid(v, path):
    if not isinstance(v, str) or not _ID_RE.match(v):
        raise bad(path, "id_shape")
    return v


def _list(v, path, max_n):
    if not isinstance(v, list):
        raise bad(path, "not_list")
    if len(v) > max_n:
        raise bad(path, "too_many")
    return v


def _keys(d, allowed, path, required=None):
    if not isinstance(d, dict):
        raise bad(path, "not_object")
    extra = set(d) - allowed
    if extra:
        raise bad(path, "forbidden_key")
    if set(required if required is not None else allowed) - set(d):
        raise bad(path, "missing_key")


def _item_live(c, uid, item_id):
    return db._one(c, "SELECT id,object_id,revision,title,data,decision FROM items WHERE user_id=%s AND id=%s AND revision > 0 "
                      "AND archived_at IS NULL", (uid, item_id))


def _by_code(c, uid, oid, code):
    return db._one(c, "SELECT id,object_id,revision,title,data,decision FROM items WHERE user_id=%s AND object_id=%s AND code=%s "
                      "AND revision > 0 AND archived_at IS NULL", (uid, oid, code))


def _check_fields(fields, o, path):
    """변경 fields → 검사한 dict. 보호·금지 키 거부, 정의 밖 키 거부, 타입·LEAK 검사. 관계는 links 로만."""
    if not isinstance(fields, dict):
        raise bad(path, "not_object")
    byk = {f["key"]: f for f in o["fields"]}
    out = {}
    for k, v in fields.items():
        p = f"{path}.{k}"
        if k in FORBIDDEN_FIELD_KEYS:
            raise bad(p, "protected_field")
        if k == "title":
            out[k] = _str(v, p, TITLE_MAX)
            continue
        if k == "body":
            out[k] = _str(v, p, db.JOB_TEXT_MAX_CP, empty_ok=True)
            continue
        f = byk.get(k)
        if f is None:
            raise bad(p, "unknown_field")
        if f["value_type"] == "reference":
            raise bad(p, "reference_in_fields")
        if v is None or v == "" or v == []:
            out[k] = v
            continue
        why = db._value_error(f, v)
        if why:
            raise bad(p, "value_type")
        if isinstance(v, str):
            _str(v, p, db.JOB_TEXT_MAX_CP)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    _str(x, f"{p}[{i}]", db.JOB_TEXT_MAX_CP)
        out[k] = v
    return out


def _target(c, uid, op, oid, t, path):
    """대상 참조 → (item_id|None, ref_kind, inferred, item_row|None). update 만 대상이 있다."""
    if op == "create":
        if t not in (None, {}, {"ref_kind": "none"}):
            raise bad(path, "create_has_target")
        return None, "none", False, None
    if not isinstance(t, dict) or t.get("ref_kind") not in REF_KINDS:
        raise bad(path, "ref_kind")
    rk = t["ref_kind"]
    if rk == "unresolved":
        _keys(t, {"ref_kind"}, path)
        return None, rk, False, None
    if rk == "source_id":
        _keys(t, {"ref_kind", "scheme", "value"}, path)
        if t["scheme"] not in IDENTITY_SCHEMES or not isinstance(t["value"], str) or not t["value"].strip():
            raise bad(path, "scheme")
        row = _by_code(c, uid, oid, t["value"])
        return (row["id"], rk, False, row) if row else (None, "unresolved", False, None)   # 해석 실패 = 대상 미해결로 강등
    _keys(t, {"ref_kind", "value"}, path)
    row = _item_live(c, uid, t["value"]) if isinstance(t["value"], str) else None
    if not row or row["object_id"] != oid:
        raise bad(path, "target_not_found")   # 허위·남의·다른 오브젝트·보관된 항목 ID
    return row["id"], rk, rk == "inferred", row


def _link(c, uid, o, ln, path):
    """관계 하나 → {relation, target_kind, target_id|None, ref_kind, inferred}. relation = 그 오브젝트의 reference 필드 key."""
    _keys(ln, {"relation", "target"}, path)
    f = next((x for x in o["fields"] if x["key"] == ln["relation"] and x["value_type"] == "reference"), None)
    if f is None:
        raise bad(f"{path}.relation", "relation_not_allowed")
    t = ln["target"]
    if not isinstance(t, dict) or "ref_kind" not in t:
        raise bad(f"{path}.target", "ref_kind_missing")   # 근거 유형 없는 연결(날짜·출처·유사 문장만의 연결 포함) = 계약 거부
    rk = t["ref_kind"]
    if rk not in REF_KINDS:
        raise bad(f"{path}.target", "ref_kind")
    kind = f["target_kind"]
    if "target_kind" in t and t["target_kind"] != kind:
        raise bad(f"{path}.target", "target_kind")
    base = {"relation": f["key"], "target_kind": kind, "ref_kind": rk, "inferred": rk == "inferred", "target_id": None}
    if rk == "unresolved":
        _keys(t, {"ref_kind", "target_kind"}, f"{path}.target", {"ref_kind"})
        return base
    if rk == "source_id":
        _keys(t, {"ref_kind", "target_kind", "scheme", "value"}, f"{path}.target", {"ref_kind", "scheme", "value"})
        if kind != "item" or t["scheme"] not in IDENTITY_SCHEMES or not isinstance(t["value"], str):
            raise bad(f"{path}.target", "scheme")
        row = _by_code(c, uid, f["target_object_id"], t["value"])
        if not row:
            return {**base, "ref_kind": "unresolved"}
        return {**base, "target_id": row["id"]}
    _keys(t, {"ref_kind", "target_kind", "value"}, f"{path}.target", {"ref_kind", "value"})
    tid = t["value"] if isinstance(t["value"], str) else ""
    got = db._resolve_target(c, uid, kind, tid)
    if not got or (kind == "item" and got["object_id"] != f["target_object_id"]):
        raise bad(f"{path}.target", "target_not_found")
    return {**base, "target_id": tid}


def _cycle(changes):
    deps = {ch["cid"]: ch["depends_on"] for ch in changes}
    state = {}

    def visit(n):
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(m) for m in deps[n]):
            return True
        state[n] = 2
        return False
    return any(visit(n) for n in deps)


def _required_missing(o, data, title, links):
    miss = []
    if not (title or "").strip():
        miss.append("title")
    for f in o["fields"]:
        if not f["required"]:
            continue
        if f["value_type"] == "reference":
            if not any(ln["relation"] == f["key"] and ln["target_id"] for ln in links):
                miss.append(f["key"])
        elif db._empty((data or {}).get(f["key"])):
            miss.append(f["key"])
    return miss


def validate_result(c, uid, job, body, index):
    """stack/1 결과 본문 재검증(봉투 대조는 호출자). 반환 {units, changes, blocked, target_versions, text, model_meta}.
    changes 정규화 꼴 = {cid, op, object_id, item_id, ref_kind, inferred, fields, links, depends_on}."""
    units = _list(body["units"], "units", MAX_UNITS)
    raw_changes = _list(body["changes"], "changes", MAX_CHANGES)
    raw_blocked = _list(body["blocked"], "blocked", MAX_BLOCKED)
    mm = body["model_meta"]
    if not isinstance(mm, dict) or len(canon(mm)) > MODEL_META_MAX:
        raise bad("model_meta", "shape")
    changes, cids = [], set()
    for i, ch in enumerate(raw_changes):
        p = f"changes[{i}]"
        _keys(ch, CHANGE_KEYS, p, CHANGE_KEYS - {"target"})
        cid = _rid(ch["cid"], f"{p}.cid")
        if cid in cids:
            raise bad(f"{p}.cid", "duplicate")
        cids.add(cid)
        if ch["op"] not in ("create", "update"):
            raise bad(f"{p}.op", "op")
        o = index.get(ch["object_id"]) if isinstance(ch["object_id"], str) else None
        if o is None:
            raise bad(f"{p}.object_id", "object_not_found")
        fields = _check_fields(ch["fields"], o, f"{p}.fields")
        item_id, rk, inferred, row = _target(c, uid, ch["op"], ch["object_id"], ch.get("target"), f"{p}.target")
        if ch["op"] == "create" and "title" not in fields:
            pass   # 필수 칸 미충족 → 아래 막힘(missing_required)
        links, seen = [], set()
        for j, ln in enumerate(_list(ch["links"], f"{p}.links", MAX_CHANGES)):
            got = _link(c, uid, o, ln, f"{p}.links[{j}]")
            k = (got["relation"], got["target_kind"], got["target_id"])
            if got["target_id"] and k in seen:
                continue
            seen.add(k)
            links.append(got)
        deps = _list(ch["depends_on"], f"{p}.depends_on", MAX_CHANGES)
        for d in deps:
            _rid(d, f"{p}.depends_on")
        changes.append({"cid": cid, "op": ch["op"], "object_id": ch["object_id"], "item_id": item_id, "ref_kind": rk,
                        "inferred": inferred, "fields": fields, "links": links, "depends_on": list(dict.fromkeys(deps)), "_row": row})
    for ch in changes:
        for d in ch["depends_on"]:
            if d not in cids or d == ch["cid"]:
                raise bad(f"changes[{ch['cid']}].depends_on", "unknown_cid")
    if _cycle(changes):
        raise bad("changes", "depends_cycle")
    seen_upd = set()
    for ch in changes:   # 같은 항목을 두 변경이 고치면 어느 쪽이 맞는지 모른다
        if ch["item_id"]:
            if ch["item_id"] in seen_upd:
                raise bad(f"changes[{ch['cid']}].target", "duplicate_target")
            seen_upd.add(ch["item_id"])
    # 정리 단위 — 모든 변경은 정확히 한 단위에 묶인다(제외할 때 그 문단이 같이 빠진다)
    uids, owner, out_units = set(), {}, []
    for i, u in enumerate(units):
        p = f"units[{i}]"
        _keys(u, UNIT_KEYS, p)
        uid_ = _rid(u["uid"], f"{p}.uid")
        if uid_ in uids:
            raise bad(f"{p}.uid", "duplicate")
        uids.add(uid_)
        text = _str(u["text"], f"{p}.text", MAX_TEXT)
        ids = _list(u["change_ids"], f"{p}.change_ids", MAX_CHANGES)
        for cid in ids:
            if cid not in cids:
                raise bad(f"{p}.change_ids", "unknown_cid")
            if cid in owner:
                raise bad(f"{p}.change_ids", "cid_in_two_units")
            owner[cid] = uid_
        out_units.append({"uid": uid_, "text": text, "change_ids": list(dict.fromkeys(ids))})
    if not out_units:
        raise bad("units", "empty")
    if set(owner) != cids:
        raise bad("changes", "cid_without_unit")
    text = UNIT_JOIN.join(u["text"] for u in out_units)
    if len(text) > MAX_TEXT:
        raise bad("units", "text_too_long")
    # 막힘 — 모델 신고 + 서버 재검사(대상 미해결·필수 칸)
    blocked, bkeys = [], set()
    for i, b in enumerate(raw_blocked):
        p = f"blocked[{i}]"
        _keys(b, BLOCK_KEYS, p)
        if b["cid"] is not None and b["cid"] not in cids:
            raise bad(f"{p}.cid", "unknown_cid")
        if b["code"] not in BLOCK_CODES:
            raise bad(f"{p}.code", "code")
        q = _str(b["question"], f"{p}.question", QUESTION_MAX)
        if (b["cid"], b["code"]) not in bkeys:
            bkeys.add((b["cid"], b["code"]))
            blocked.append({"cid": b["cid"], "code": b["code"], "question": q})

    def add_block(cid, code, q):
        if (cid, code) not in bkeys:
            bkeys.add((cid, code))
            blocked.append({"cid": cid, "code": code, "question": q})
    tv = {}
    for ch in changes:
        o = index[ch["object_id"]]
        row = ch.pop("_row")
        if ch["op"] == "update" and not ch["item_id"]:
            add_block(ch["cid"], "unresolved_target", _BLOCK_QUESTIONS["unresolved_target"])
        if any(ln["target_id"] is None for ln in ch["links"]):
            add_block(ch["cid"], "unresolved_target", _BLOCK_QUESTIONS["unresolved_target"])
        if row:
            tv[f"item:{row['id']}"] = row["revision"]
            data = {**(row["data"] or {}), **{k: v for k, v in ch["fields"].items() if k not in ("title", "body")}}
            title = ch["fields"].get("title", row["title"])
            cur_links = db._links_by_item(c, uid, [row["id"]]).get(row["id"], [])
            merged = [{"relation": x["field_key"], "target_id": x["target_id"]} for x in cur_links] + ch["links"]
        else:
            data = {k: v for k, v in ch["fields"].items() if k not in ("title", "body")}
            title = ch["fields"].get("title")
            merged = ch["links"]
        if ch["op"] == "create" or row:
            miss = _required_missing(o, data, title, merged)
            if miss:
                add_block(ch["cid"], "missing_required", _BLOCK_QUESTIONS["missing_required"].format(f=", ".join(miss)))
        for ln in ch["links"]:
            if ln["target_id"] and ln["target_kind"] in ("item", "person"):
                tbl = "items" if ln["target_kind"] == "item" else "people"
                r = db._one(c, f"SELECT revision FROM {tbl} WHERE user_id=%s AND id=%s", (uid, ln["target_id"]))
                tv[f"{ln['target_kind']}:{ln['target_id']}"] = r["revision"]
    return {"units": out_units, "changes": changes, "blocked": blocked, "target_versions": tv, "text": text, "model_meta": mm}


def check_envelope(env, job):
    """봉투 대조 — 서버가 아는 값(작업·입력·정의·편집 세대·원 결과)과 같아야 한다. 결과 버전은 PC 가 만든 UUID."""
    import uuid as _uuid
    _keys(env, ENVELOPE_KEYS, "envelope")
    if env["contract"] != CONTRACT:
        raise bad("envelope.contract", "contract")
    if env["job_id"] != str(job["id"]):
        raise bad("envelope.job_id", "mismatch")
    try:
        rv = _uuid.UUID(env["result_version"]) if isinstance(env["result_version"], str) else None
    except ValueError:
        rv = None
    if rv is None:
        raise bad("envelope.result_version", "uuid")
    if not db._is_int(env["input_version"]) or env["input_version"] != job["input_version"]:
        raise CrmError("JOB-VERSION", "입력 버전이 맞지 않아요.", "그 결과는 버리세요.")
    if not db._is_int(env["edit_generation"]) or env["edit_generation"] != job["edit_generation"]:
        raise bad("envelope.edit_generation", "mismatch")
    parent = str(job["parent_result_version"]) if job["parent_result_version"] else None
    if env["parent_result_version"] != parent:
        raise bad("envelope.parent_result_version", "mismatch")
    if job["definitions_version"] is None or env["definitions_version"] != job["definitions_version"]:
        raise defs_changed()
    return rv


# ── 미리보기 ───────────────────────────────────────────

def preview_sha(p):
    """미리보기 해시 — 사용자가 보는 것(단위·변경·막힘·제외)과 묶인 버전(결과·편집 세대·대상 버전) 전체."""
    return sha({k: p[k] for k in ("result_version", "preview_version", "edit_generation", "units", "changes", "blocked", "excluded",
                                  "target_versions")})


def blocked_open(blocked):
    return any(b["code"] in OPEN_BLOCKS for b in blocked)


def exclude_plan(units, changes, blocked, cids):
    """항목 끄기 — 고른 변경 + 그것에 의존하는 변경(연쇄) + 같은 정리 단위에 묶인 변경을 함께 뺀다(고정점까지).
    반환 (units, changes, blocked, removed 목록). 남는 정리 글이 없으면 기계적 제거로 확인할 게 없다 → PREVIEW-NEEDS-RESTACK."""
    live = {ch["cid"] for ch in changes}
    want = set(cids)
    if not want or not want <= live:
        raise CrmError("ARG-INVALID", "뺄 항목(cids)이 이 미리보기에 없어요.", "새로 고친 미리보기를 다시 보세요.")
    unit_of = {cid: u["uid"] for u in units for cid in u["change_ids"]}
    removed, gone_units = set(want), set()
    while True:
        before = (len(removed), len(gone_units))
        for ch in changes:
            if ch["cid"] not in removed and set(ch["depends_on"]) & removed:
                removed.add(ch["cid"])
        for cid in list(removed):
            if cid in unit_of:
                gone_units.add(unit_of[cid])
        for u in units:
            if u["uid"] in gone_units:
                removed |= set(u["change_ids"])
        if (len(removed), len(gone_units)) == before:
            break
    new_units = [u for u in units if u["uid"] not in gone_units]
    if not new_units:
        raise CrmError("PREVIEW-NEEDS-RESTACK", "이 항목을 빼면 정리 글이 남지 않아요.", "빼고 싶은 내용을 반영해 다시 정리하세요.")
    new_changes = [ch for ch in changes if ch["cid"] not in removed]
    new_blocked = [b for b in blocked if b["cid"] is None or b["cid"] not in removed]
    return new_units, new_changes, new_blocked, sorted(removed)


def display(c, uid, row, index=None):
    """웹 응답용 미리보기 — 변경은 표시 요약(서버가 만든 한 줄)만, 대상 해석 내부값은 싣지 않는다."""
    index = index if index is not None else _index(c, uid)
    unit_of = {}
    for u in row["units"]:
        for cid in u["change_ids"]:
            unit_of.setdefault(cid, []).append(u["uid"])
    out = []
    for ch in row["changes"]:
        o = index.get(ch["object_id"])
        label = o["row"]["label"] if o else ch["object_id"]
        if ch["op"] == "create":
            summary = f"새 {label}: {ch['fields'].get('title') or '(제목 없음)'}"
        else:
            cur = _item_live(c, uid, ch["item_id"]) if ch["item_id"] else None
            summary = f"{label} 고침: {cur['title'] if cur else '(대상 못 찾음)'}"
        out.append({"cid": ch["cid"], "op": ch["op"], "object_id": ch["object_id"], "object_label": label, "summary": summary,
                    "ref_kind": ch["ref_kind"], "inferred": bool(ch["inferred"] or any(ln["inferred"] for ln in ch["links"])),
                    "depends_on": ch["depends_on"], "unit_ids": unit_of.get(ch["cid"], [])})
    return {"id": str(row["id"]), "job_id": str(row["job_id"]), "session_id": str(row["session_id"]),
            "preview_version": row["preview_version"], "preview_sha": row["preview_sha"], "edit_generation": row["edit_generation"],
            "units": row["units"], "text": UNIT_JOIN.join(u["text"] for u in row["units"]), "changes": out, "blocked": row["blocked"],
            "blocked_open": blocked_open(row["blocked"]), "excluded": row["excluded"]}


# ── stage 규칙 (rule_version 1) ─────────────────────────

def compute_stage(kind, required_ok, decision):
    """판단 종류(judgment) + decision=confirmed → confirmed · 필수 칸 충족 → organized · 아니면 draft. 모델 출력은 입력이 아니다."""
    if kind == "judgment" and decision == "confirmed":
        return "confirmed"
    return "organized" if required_ok else "draft"
