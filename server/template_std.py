"""표준 std@1 — 동결 명세 파일 templates/std@1.json 을 만드는·검증하는 도구. 명세: notes 「PR20 실행명세」 §0·§2.

무엇: 축(collection)·결정(event)·기준(principle) 세 오브젝트. 코어 테이블(기록·후보 등)은 표준 정의가 아니다(공통 기반).
      숙제·발행·접근 방식은 원작자 템플릿(template_author.py)이다 — 표준이 아니다.
식별: object_defs·field_defs.std_id = `std:<key>[.<field>]`(contract.std_id_of). 버전은 식별자에 넣지 않는다 — 설치 버전은 std_marks.version.
      표시명(label)으로 표준 여부를 판정하지 않는다. 파일엔 std_id 를 적지 않는다(key 로 만든다).
id: 템플릿 id(od_axes·od_events·od_principles)는 참조 대상을 적는 자리일 뿐이다 — 사용자에게 넣을 때 다른 key 가 이미 쓰면
    db 가 새 id 를 뽑는다(§12). 참조 필드의 target_object_id 는 그 사용자의 대응 오브젝트 id 로 바꿔 넣는다.
시드·편입: db 가 동결 파일(templates/std@1.json)을 읽는다. 이 모듈은 DB 에 붙지 않는다.
동결: TEMPLATE·LABELS 를 바꾸면 시험(20절 「template_std 출력 == 동결 파일」)이 깨진다 — 바꾸려면 STD_VERSION 을 올리고
      새 판 파일을 만든다(옛 판 파일은 지우지 않는다).
덤프: .venv/bin/python template_std.py           # 정규화된 동결 JSON 을 출력(새 판 파일 만들 때)
      .venv/bin/python template_std.py --check   # 동결 파일과 같은지(다르면 exit 1)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STD_KEY = "std"
STD_VERSION = 1
STD_NAME = f"{STD_KEY}@{STD_VERSION}"
FROZEN_PATH = HERE / "templates" / f"{STD_NAME}.json"

LABELS = {"axes": "축", "decision_events": "결정", "principles": "기준"}


def _ref(key, label, target_kind, target_object_id=None, multiple=False, required=False):
    f = {"key": key, "label": label, "value_type": "reference", "target_kind": target_kind, "multiple": multiple, "required": required}
    if target_object_id:
        f["target_object_id"] = target_object_id
    return f


def _f(key, label, value_type, required=False, options=None):
    f = {"key": key, "label": label, "value_type": value_type, "required": required}
    if options is not None:
        f["options"] = options
    return f


# 순서 = 참조 대상이 먼저(시드가 이 순서로 넣는다)
TEMPLATE = [
    {"id": "od_axes", "key": "axes", "label": LABELS["axes"], "semantic_kind": "collection", "policy": {},
     "fields": [_f("keywords", "검색어", "text_list")]},
    {"id": "od_events", "key": "decision_events", "label": LABELS["decision_events"], "semantic_kind": "event", "policy": {},
     "fields": [_f("happened_on", "결정일", "date"), _ref("claims", "판단", "candidate", multiple=True)]},
    {"id": "od_principles", "key": "principles", "label": LABELS["principles"], "semantic_kind": "principle",
     "policy": {"promotion": {"confirmed_claims_min": 2, "claim_field": "evidence_claims", "distinct_events_min": 0}},
     "fields": [_ref("evidence_claims", "근거 판단", "candidate", multiple=True),
                _ref("evidence_events", f"근거 {LABELS['decision_events']}", "item", "od_events", multiple=True),
                _ref("axes", f"관심 {LABELS['axes']}", "item", "od_axes", multiple=True)]},
]


def normalized():
    import db   # 정규화(normalize_definition)만 쓴다 — DB 연결은 열지 않는다
    return [db.normalize_definition(d) for d in TEMPLATE]


def frozen_doc():
    """동결 파일 내용. 새 판 파일: json.dumps(frozen_doc(), ensure_ascii=False, indent=2) + 줄바꿈."""
    return {"template_key": STD_KEY, "template_version": STD_VERSION, "objects": normalized()}


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        same = frozen_doc() == json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
        print("동결 파일과 같아요" if same else f"동결 파일({FROZEN_PATH.name})과 달라요 — 판을 올리세요")
        sys.exit(0 if same else 1)
    print(json.dumps(frozen_doc(), ensure_ascii=False, indent=2))
