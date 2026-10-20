"""PR #20 R2 — 수신자별 응답 계약 · 신규 오류 코드 문구. 명세: notes 「PR20 실행명세」 §0·§1·§6·§8 · 「PR-B 구조 구현계획」 §4·§7.
기능 플래그 두 개(표준·출처)는 PR B 에서 코드에서 뺐다 — 표준 시드·출처 연결은 상시 켬.

순수 모듈이다 — db·네트워크를 부르지 않는다(시험·예제 재생성이 DB 없이 돈다). db.py 가 오류 문구를 여기서 가져간다.

공통 데이터(입력, 그대로 내보내지 않는다) = user dict:
  definitions  db.definitions() 모양(적용·보관 제외, (created_at, id) 순) + 오브젝트·필드에 선택 키 std_id·template_slot
               (DB 열은 5·6단계 몫 — 키가 없으면 None 으로 본다)
  std_marks    {version, state} 또는 None(표식 없음 — version·state 가 null)
  std_choices  {std_key: 표준 상태} — 표식 유무와 무관하게 그대로, 없는 key 는 none
  sources      [{id, kind, name, location}] — 없으면 []
  display_name 표시 이름 또는 None (PR B — 「나」. 아래 셋 중 하나라도 키가 있으면 본인 응답에 셋 다 싣는다)
  people       [{id, alias}] — 없으면 [] (PR B 코어 「사람」)
  reflections  [{id, source_id, kind, review, body}] — 없으면 [] (PR B 코어 「출처 요약/반응」)
응답 = respond(user, viewer), viewer ∈ VIEWERS. 본인 = 모든 이름 + 메타 · 운영자 비동의 = §8 허용 목록만 · 운영자 동의 = 비동의 + 현재 label·선택지 label.
key·선택지 값·출처 name·location·오브젝트 id 는 운영자에게 동의와 무관하게 0.
표시 이름·사람·요약/반응(값·개수 모두)은 본인 응답에만 있다 — 운영자 응답은 동의와 무관하게 0(PR B §7).

고정 예제(server/contract/*.json)는 EXAMPLES 를 이 함수로 만든 것이다. 손으로 고치지 않는다.
재생성: cd server && .venv/bin/python contract.py      # EXAMPLES 를 바꾼 뒤에만. test_tools 18) 이 「함수 출력 == 동결 파일」을 본다
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES_DIR = HERE / "contract"

# ── 표준 std@1 (§0) ──
# 운영자 표준명·std 상태 key 의 출처 = 명세 파일 templates/std@1.json(key·label). 식별자 std:<key> 는 파일에 없고 key 로 만든다.
STD_STATES = ("seeded", "incorporated", "deferred", "refused", "none")
STD_MARK_STATES = ("seed_pending", "seeded", "adopting")


def std_id_of(object_key, field_key=None):
    """표준 식별자 `std:<object_key>[.<field_key>]` — 버전을 넣지 않는다."""
    return f"std:{object_key}" + (f".{field_key}" if field_key else "")


def _std_load():
    doc = json.loads((HERE / "templates" / "std@1.json").read_text(encoding="utf-8"))
    names = {}
    for o in doc["objects"]:
        names[std_id_of(o["key"])] = o["label"]
        for f in o["fields"]:
            names[std_id_of(o["key"], f["key"])] = f["label"]
    return f"{doc['template_key']}@{doc['template_version']}", tuple(o["key"] for o in doc["objects"]), names


STD_VERSION, STD_KEYS, STD_NAMES = _std_load()   # 설치 버전 문자열 · 표준 오브젝트 key(파일 순) · {std_id: 명세 label}


def std_name(std_id):
    """명세 표준명(std@1 label). 명세에 없는 식별자·None 은 None. key 가 같다는 이유로 부르지 않는다 — std_id 로만."""
    return STD_NAMES.get(std_id) if isinstance(std_id, str) else None


def std_state(marks, choices):
    """사용자 `std` = {version, state, objects}. objects 는 std_choices 그대로(없는 key·모르는 값 = none) — 표식이 없어도 같다.
    version·state 는 std_marks 값, 표식이 없으면 null(§0)."""
    choices = choices or {}
    return {"version": marks["version"] if marks else None, "state": marks["state"] if marks else None,
            "objects": {k: choices.get(k) if choices.get(k) in STD_STATES else "none" for k in STD_KEYS}}


# ── 출처 판정 · 관계 파생 (§0·§8) ──
ORIGINS = ("core", "standard", "template", "custom")
CORE_TARGETS = ("record", "candidate")   # 참조 필드가 가리키는 코어(공통 테이블) — origin=core 고정 노드


def origin_of(d):
    """오브젝트·필드 하나의 출처. std_id → standard, 없고 template_slot → template, 둘 다 없음 → custom. 단위마다 따로 본다."""
    if d.get("std_id"):
        return "standard"
    if d.get("template_slot"):
        return "template"
    return "custom"


def _meta(d):
    sid = d.get("std_id") or None
    return {"origin": origin_of(d), "std_id": sid, "locked": sid is not None}


def cardinality(field):
    return "N:N" if field.get("multiple") else "N:1"


def relations(definitions):
    """관계 = 참조 필드 하나. origin·std_id·locked 는 그 참조 필드 값 그대로."""
    out = []
    for o in definitions:
        for f in o["fields"]:
            if f["value_type"] != "reference":
                continue
            tk = f["target_kind"]
            out.append({"object_id": o["id"], "field_key": f["key"], "target_kind": tk,
                        "target_object_id": f.get("target_object_id") if tk == "item" else None,
                        "cardinality": cardinality(f), **_meta(f)})
    return out


def common(user):
    """공통 데이터(내부 계약). 응답으로 그대로 내보내지 않는다 — respond 를 거친다."""
    defs = user.get("definitions") or []
    objects = []
    for o in defs:
        fields = [{"key": f["key"], "label": f["label"], "value_type": f["value_type"], "required": bool(f.get("required")),
                   "multiple": bool(f.get("multiple")), "target_kind": f.get("target_kind"),
                   "target_object_id": f.get("target_object_id"),
                   "options": [{"value": op["value"], "label": op["label"]} for op in f.get("options") or []],
                   **_meta(f)} for f in o["fields"]]
        objects.append({"id": o["id"], "key": o["key"], "label": o["label"], "semantic_kind": o["semantic_kind"],
                        **_meta(o), "fields": fields})
    rels = relations(defs)
    return {"std": std_state(user.get("std_marks"), user.get("std_choices")),
            "objects": objects, "relations": rels,
            "core": [{"kind": k, "origin": "core"} for k in CORE_TARGETS if any(r["target_kind"] == k for r in rels)],
            "sources": [{"id": s["id"], "kind": s["kind"], "name": s["name"], "location": s["location"]}
                        for s in user.get("sources") or []],
            "me": None if not any(k in user for k in ME_KEYS) else {
                "display_name": user.get("display_name"),
                "people": [{"id": p["id"], "alias": p["alias"]} for p in user.get("people") or []],
                "reflections": [{"id": r["id"], "source_id": r["source_id"], "kind": r["kind"], "review": r["review"], "body": r["body"]}
                                for r in user.get("reflections") or []]}}


ME_KEYS = ("display_name", "people", "reflections")   # PR B 「나」 데이터 — 입력에 하나라도 있으면 본인 응답에 셋 다 싣는다(운영자 응답엔 없음)


# ── 수신자별 응답 (§8) ──
VIEWERS = ("self", "admin_noconsent", "admin_consent")
CONTRACT_VERSION = 1


def _letters(i):
    """0 → a, 25 → z, 26 → aa."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s


def object_ref(i):
    return f"오브젝트 {i + 1}"


def field_ref(i):
    return f"필드 {_letters(i)}"


def _counts(cd):
    return {"objects": len(cd["objects"]), "fields": sum(len(o["fields"]) for o in cd["objects"]),
            "relations": len(cd["relations"]), "sources": len(cd["sources"])}


def _self(cd):
    objs = [{**o, "std_name": std_name(o["std_id"]),
             "fields": [{**f, "std_name": std_name(f["std_id"])} for f in o["fields"]]} for o in cd["objects"]]
    return {"contract": CONTRACT_VERSION, "viewer": "self", "std": cd["std"], "counts": _counts(cd), "objects": objs,
            "relations": cd["relations"], "core": cd["core"], "sources": cd["sources"], **(cd["me"] or {})}   # PR B 키 — 본인만, 입력에 있을 때만


def _admin(cd, consent):
    """허용 목록 = 자리표 · 편입된 것만 표준명 · semantic_kind · origin · std_id · locked · cardinality · 개수 · 표준 상태 코드.
    동의면 현재 label·선택지 label 을 더한다. 사용자 id·key·선택지 값·출처 name·location 은 어느 쪽에도 없다.
    표시 이름·사람·요약/반응(PR B)은 값도 개수도 넣지 않는다 — _counts 는 정의·관계·출처만 센다."""
    oref = {o["id"]: object_ref(i) for i, o in enumerate(cd["objects"])}
    fref = {}
    objs = []
    for o in cd["objects"]:
        fields = []
        for j, f in enumerate(o["fields"]):
            fref[(o["id"], f["key"])] = field_ref(j)
            fo = {"ref": field_ref(j), "std_name": std_name(f["std_id"]), "origin": f["origin"],
                  "std_id": f["std_id"] if std_name(f["std_id"]) else None, "locked": f["locked"]}
            if consent:
                fo["label"] = f["label"]
                fo["options"] = [op["label"] for op in f["options"]]
            fields.append(fo)
        oo = {"ref": oref[o["id"]], "std_name": std_name(o["std_id"]), "semantic_kind": o["semantic_kind"], "origin": o["origin"],
              "std_id": o["std_id"] if std_name(o["std_id"]) else None, "locked": o["locked"]}
        if consent:
            oo["label"] = o["label"]
        oo["fields"] = fields
        objs.append(oo)
    rels = []
    for r in cd["relations"]:
        to = oref.get(r["target_object_id"]) if r["target_kind"] == "item" else r["target_kind"]
        rels.append({"from": oref[r["object_id"]], "field": fref[(r["object_id"], r["field_key"])], "to": to,
                     "cardinality": r["cardinality"], "origin": r["origin"],
                     "std_id": r["std_id"] if std_name(r["std_id"]) else None, "locked": r["locked"]})
    return {"contract": CONTRACT_VERSION, "viewer": "admin_consent" if consent else "admin_noconsent", "std": cd["std"],
            "counts": _counts(cd), "objects": objs, "relations": rels, "core": cd["core"]}


def respond(user, viewer):
    if viewer not in VIEWERS:
        raise ValueError(f"모르는 수신자: {viewer}")
    cd = common(user)
    return _self(cd) if viewer == "self" else _admin(cd, viewer == "admin_consent")


# ── 신규 오류 코드 (§1·§6) — db.py 가 CrmError 로 감싼다. 문구에 다른 사용자 정보가 들어갈 자리가 없다 ──
STANDARD_LOCKED = "STANDARD-LOCKED"
CANDIDATE_REVIEW = "CANDIDATE-REVIEW"
SCOPE_SELF_ONLY = "SCOPE-SELF-ONLY"

# 잠금 거절 — 바꾼 항목 종류(고정 문구) + 표준 식별자. 사용자 label 은 문구에 넣지 않는다
LOCKED_CHANGES = {
    "archive": "표준 오브젝트는 삭제·보관할 수 없어요",
    "key": "표준 오브젝트의 key 는 바꿀 수 없어요",
    "semantic_kind": "표준 오브젝트의 종류(semantic_kind)는 바꿀 수 없어요",
    "std_id": "표준 식별자는 떼거나 바꿀 수 없어요",
    "field_remove": "표준 필드는 뺄 수 없어요",
    "field_shape": "표준 필드의 key·value_type·대상·multiple·required 는 바꿀 수 없어요",
    "option_remove": "표준 선택지는 빼거나 값을 바꿀 수 없어요",
}
LOCKED_ACT = "표준 정의는 label 만 바꿀 수 있어요. 필드·선택지는 더할 수 있어요."


def locked_message(change, std_id):
    return f"{LOCKED_CHANGES[change]} ({std_id})."


# 확정 재검사 거절 — 사유 코드별 고정 문구. 정정 안내는 메타데이터가 실제로 틀렸을 수 있는 사유에만(deleted·external 은 없음)
REVIEW_REASONS = ("deleted", "external", "author_unknown", "processed")   # review_reason 이 보는 순서
REVIEW_MSG = {
    "author_unknown": "근거 기록의 작성자를 확인할 수 없어 이 후보를 확정할 수 없어요.",
    "external": "근거 기록이 남이 쓴 글이라 이 후보를 확정할 수 없어요.",
    "processed": "근거 기록이 원문 그대로가 아니라 이 후보를 확정할 수 없어요.",
    "deleted": "근거 기록이 삭제돼 이 후보를 확정할 수 없어요.",
}
REVIEW_ACT = {
    "author_unknown": "작성자 정보가 실제로 틀렸을 때만 연결한 AI 대화에서 record_provenance_set 으로 정정하세요. 제외·보류는 할 수 있어요.",
    "external": "제외·보류는 할 수 있어요.",
    "processed": "가공 방식 정보가 실제로 틀렸을 때만 연결한 AI 대화에서 record_provenance_set 으로 정정하세요. 제외·보류는 할 수 있어요.",
    "deleted": "제외·보류는 할 수 있어요.",
}


def review_reason(record):
    """근거 기록 → 재검토 사유 코드, 적격(_eligible 전체 조건)이면 None."""
    if record.get("deleted_at") is not None:
        return "deleted"
    if record["author_kind"] == "external":
        return "external"
    if record["author_kind"] != "self":
        return "author_unknown"
    if record["text_process"] != "verbatim":
        return "processed"
    return None


SCOPE_MSG = "남이 쓴 글(author_kind=external)은 기록으로 넣을 수 없어요."
SCOPE_ACT = "직접 쓴 내 기록만 넣어요. 남의 말·AI 요약은 넣지 않아요."

# 표시 이름 관문(PR B 결정 3) — 이름 없는 계정의 쓰기 거절. 읽기·help_get·feedback_send·ticket_*·표시 이름 설정은 통과(db.NAME_EXEMPT·main 도구 분류)
NAME_REQUIRED = "NAME-REQUIRED"
NAME_REQUIRED_MSG = "대시보드에서 표시 이름을 정해 주세요."
NAME_REQUIRED_ACT = "대시보드(/app)에 로그인하면 이름 입력 화면이 나와요. 이름을 정한 뒤 다시 하세요."

# 분류 대상 없음(6단계 추가 — §8 신규 3종 밖). 분류(기록 → 항목 axes 링크)만 꺼지고 나머지는 그대로(§5)
CLASSIFY_OFF = "CLASSIFY-OFF"
CLASSIFY_OFF_MSG = "분류 대상 오브젝트가 없어 기록을 분류할 수 없어요."
CLASSIFY_OFF_ACT = "관심 축(key axes) 오브젝트가 생기면 분류할 수 있어요. 다른 칸(붙는 곳·쓸 자리·메모)은 지금도 남길 수 있어요."


# ── 고정 예제 입력 — 가짜 사용자 둘. 사용자가 정한 문자열은 전부 표식(ULBL·PLBL·SRC 등)을 달아 누출 시험이 찾게 한다 ──

def _fd(key, label, value_type, *, std=None, slot=None, target_kind=None, target=None, multiple=False, options=()):
    return {"key": key, "label": label, "value_type": value_type, "required": False,
            "options": [{"value": v, "label": lab} for v, lab in options], "target_kind": target_kind,
            "target_object_id": target, "multiple": multiple, "std_id": std, "template_slot": slot}


def _od(oid, key, label, kind, fields, *, std=None, slot=None):
    return {"id": oid, "key": key, "label": label, "semantic_kind": kind, "policy": {}, "fields": fields,
            "std_id": std, "template_slot": slot}


# full — 축·결정·기준 전부 편입(state=adopting). 편입된 기준에 사용자가 더한 필드·참조 필드(custom·null·false) · template 출처 · N:1·N:N · 출처 1
_FULL = {
    "definitions": [
        _od("od_axes", "axes", "내 관심축 ULBL01", "collection", std=std_id_of("axes"), fields=[
            _fd("keywords", "키워드 ULBL02", "text_list", std=std_id_of("axes", "keywords"))]),
        _od("od_events", "decision_events", "결정 기록 ULBL03", "event", std=std_id_of("decision_events"), fields=[
            _fd("happened_on", "날짜 ULBL04", "date", std=std_id_of("decision_events", "happened_on")),
            _fd("claims", "판단들 ULBL05", "reference", std=std_id_of("decision_events", "claims"), target_kind="candidate",
                multiple=True),
            _fd("ukey_project", "프로젝트명 ULBL06", "text")]),
        _od("od_principles", "principles", "내 원칙 ULBL07", "principle", std=std_id_of("principles"), fields=[
            _fd("evidence_claims", "근거 문장 ULBL08", "reference", std=std_id_of("principles", "evidence_claims"),
                target_kind="candidate", multiple=True),
            _fd("evidence_events", "근거 사건 ULBL09", "reference", std=std_id_of("principles", "evidence_events"),
                target_kind="item", target="od_events", multiple=True),
            _fd("axes", "축 연결 ULBL10", "reference", std=std_id_of("principles", "axes"), target_kind="item", target="od_axes",
                multiple=True),
            _fd("ukey_register", "영역 ULBL11", "select", options=(("uopt_customer", "고객 업무 ULBL12"),
                                                                   ("uopt_work", "일하는 방식 ULBL13"))),
            _fd("ukey_owner", "담당자 ULBL14", "reference", target_kind="item", target="od_upeople")]),
        _od("od_upeople", "ukey_people", "사람들 ULBL15", "collection", fields=[
            _fd("ukey_name", "이름 ULBL16", "text"),
            _fd("ukey_first_rec", "첫 기록 ULBL17", "reference", target_kind="record")]),
        _od("od_craft", "craft", "접근 방식 ULBL18", "collection", slot="author@1:craft", fields=[
            _fd("project", "프로젝트 ULBL19", "text", slot="author@1:craft.project"),
            _fd("approach", "방식 ULBL20", "text", slot="author@1:craft.approach"),
            _fd("related_events", "관련 결정 ULBL21", "reference", slot="author@1:craft.related_events", target_kind="item",
                target="od_events", multiple=True)]),
    ],
    "std_marks": {"version": STD_VERSION, "state": "adopting"},
    "std_choices": {"axes": "incorporated", "decision_events": "incorporated", "principles": "incorporated"},
    "sources": [{"id": "src_full1", "kind": "file", "name": "업무일지 SRCNAME01", "location": "fake-drive/SRCLOC01.md"}],
}

# partial — 부분 편입(state=adopting): 축 편입 · 같은 key 결정은 미편입·보류(표준명 안 붙음) · 기준 고르지 않음(none) · 출처 1
_PARTIAL = {
    "definitions": [
        _od("od_axes", "axes", "주제 PLBL01", "collection", std=std_id_of("axes"), fields=[
            _fd("keywords", "검색 단어 PLBL02", "text_list", std=std_id_of("axes", "keywords")),
            _fd("ukey_color", "색 PLBL03", "select", options=(("uopt_red", "빨강 PLBL04"),))]),
        _od("od_mydecisions", "decision_events", "결정 PLBL05", "event", fields=[
            _fd("happened_on", "언제 PLBL06", "date"),
            _fd("claims", "판단 PLBL07", "reference", target_kind="candidate", multiple=True)]),
        _od("od_ubooks", "ukey_books", "읽은 책 PLBL08", "collection", fields=[
            _fd("ukey_topics", "주제들 PLBL09", "reference", target_kind="item", target="od_axes", multiple=True),
            _fd("ukey_decision", "관련 결정 PLBL10", "reference", target_kind="item", target="od_mydecisions")]),
    ],
    "std_marks": {"version": STD_VERSION, "state": "adopting"},
    "std_choices": {"axes": "incorporated", "decision_events": "deferred"},
    "sources": [{"id": "src_part1", "kind": "url", "name": "블로그 SRCNAME02", "location": "https://SRCLOC02.example/feed"}],
}

# named(PR B) — 표시 이름·사람·요약/반응이 채워진 본인. 운영자 응답(동의·비동의)엔 DNAME·PALIAS·SRBODY 표식이 0 이어야 한다
_NAMED = {
    "definitions": [
        _od("od_axes", "axes", "관심 NLBL01", "collection", std=std_id_of("axes"), fields=[
            _fd("keywords", "단어 NLBL02", "text_list", std=std_id_of("axes", "keywords"))]),
    ],
    "std_marks": {"version": STD_VERSION, "state": "seeded"},
    "std_choices": {"axes": "seeded", "decision_events": "seeded", "principles": "seeded"},
    "sources": [{"id": "src_named1", "kind": "url", "name": "읽은 글 SRCNAME03", "location": "https://SRCLOC03.example/post"}],
    "display_name": "별칭 DNAME01",
    "people": [{"id": "p_named1", "alias": "동료 PALIAS01"}, {"id": "p_named2", "alias": "선배 PALIAS02"}],
    "reflections": [{"id": "sr_named1", "source_id": "src_named1", "kind": "summary", "review": "pending", "body": "내 요약 SRBODY01"},
                    {"id": "sr_named2", "source_id": "src_named1", "kind": "reaction", "review": "reviewed", "body": "내 반응 SRBODY02"}],
}

EXAMPLES = {"full": _FULL, "partial": _PARTIAL, "named": _NAMED}


def example_path(name, viewer):
    return EXAMPLES_DIR / f"{name}.{viewer}.json"


def dump(resp):
    return json.dumps(resp, ensure_ascii=False, indent=2) + "\n"


def write_examples():
    EXAMPLES_DIR.mkdir(exist_ok=True)
    out = []
    for name, user in EXAMPLES.items():
        for v in VIEWERS:
            p = example_path(name, v)
            p.write_text(dump(respond(user, v)), encoding="utf-8")
            out.append(p)
    return out


if __name__ == "__main__":
    for p in write_examples():
        print(p.relative_to(HERE))
    sys.exit(0)
