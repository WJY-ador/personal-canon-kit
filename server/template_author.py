"""원작자 템플릿 시드 — 정의 데이터(테이블 아님)를 한 사용자에게 넣는다.

실행: .venv/bin/python template_author.py <email>            # 그 사용자에게 오브젝트 6개 적용
      .venv/bin/python template_author.py <email> --dry-run  # DB 에 붙지 않고 정규화된 정의 JSON 만 출력
경로: MCP 의 definition_propose → definition_apply 와 같은 함수(db.definition_propose·db.definition_apply)를 부른다.
멱등: 같은 key 의 오브젝트가 이미 적용돼 있으면 건너뛴다(고치지 않는다 — 고치기는 대화에서 제안→적용).
순서: 관계 대상이 먼저 적용돼야 해서 축 → 결정 → 기준 → 숙제 → 발행 → 접근 방식.
라벨: 아래 LABELS 한 곳에서 바꾼다(정의 데이터라 적용 뒤에도 definition_propose 로 label 만 바꿀 수 있다).
근거: notes/raw/2026-10-18_schema-round2-astra.md §3, notes/2026-10-18_2단계-정의층-구현계획.md §A·§F.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 오브젝트 화면 라벨 — 원작자가 고르는 중. 한 줄씩 바꾸면 오브젝트 라벨과 그 오브젝트를 가리키는 필드 라벨이 같이 바뀐다.
LABELS = {
    "principles": "기준",
    "decision_events": "결정",
    "open_questions": "숙제",
    "publications": "발행",
    "axes": "축",
    "craft": "접근 방식",
}


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


TEMPLATE = [
    {"id": "od_axes", "key": "axes", "label": LABELS["axes"], "semantic_kind": "collection", "policy": {},
     "fields": [_f("keywords", "검색어", "text_list")]},
    {"id": "od_events", "key": "decision_events", "label": LABELS["decision_events"], "semantic_kind": "event", "policy": {},
     "fields": [_f("happened_on", "결정일", "date"), _f("project", "프로젝트", "text"), _f("source_url", "원본 위치", "text"),
                _ref("claims", "판단", "candidate", multiple=True)]},
    {"id": "od_principles", "key": "principles", "label": LABELS["principles"], "semantic_kind": "principle",
     "policy": {"promotion": {"confirmed_claims_min": 2, "claim_field": "evidence_claims", "distinct_events_min": 2,
                              "event_field": "evidence_events", "event_claim_field": "claims"}},
     "fields": [_f("register", "레지스터", "select", required=True,
                   options=[{"value": "customer", "label": "고객 업무"}, {"value": "work", "label": "일하는 방식"}]),
                _f("maturity", "완성도", "select",
                   options=[{"value": "seed", "label": "🌱"}, {"value": "growing", "label": "🌿"}, {"value": "mature", "label": "🌳"}]),
                _ref("evidence_claims", "근거 판단", "candidate", multiple=True),
                _ref("evidence_events", f"근거 {LABELS['decision_events']}", "item", "od_events", multiple=True),
                _ref("axes", f"관심 {LABELS['axes']}", "item", "od_axes", multiple=True)]},
    {"id": "od_open_questions", "key": "open_questions", "label": LABELS["open_questions"], "semantic_kind": "collection",
     "policy": {"exactly_one_reference": ["subject_principle", "subject_event", "subject_claim"]},
     "fields": [_ref("subject_principle", f"대상 {LABELS['principles']}", "item", "od_principles"),
                _ref("subject_event", f"대상 {LABELS['decision_events']}", "item", "od_events"),
                _ref("subject_claim", "대상 판단", "candidate"),
                _f("registered_on", "등록일", "date", required=True),
                _f("state", "해결 상태", "select", required=True,
                   options=[{"value": "open", "label": "열림"}, {"value": "answered", "label": "답함"}]),
                _ref("answer", "답 기록", "record")]},
    {"id": "od_publications", "key": "publications", "label": LABELS["publications"], "semantic_kind": "collection", "policy": {},
     "fields": [_f("channel", "채널", "select", required=True,
                   options=[{"value": "linkedin", "label": "LinkedIn"}, {"value": "threads", "label": "Threads"}, {"value": "other", "label": "기타"}]),
                _f("published_on", "발행일", "date", required=True), _f("url", "발행 URL", "text"), _f("key_line", "핵심 한 줄", "text"),
                _ref("materials", "재료", "record", multiple=True), _ref("axes", f"관심 {LABELS['axes']}", "item", "od_axes", multiple=True)]},
    {"id": "od_craft", "key": "craft", "label": LABELS["craft"], "semantic_kind": "collection", "policy": {},
     "fields": [_f("project", "프로젝트", "text"), _f("approach", "요지", "text"),
                _ref("related_events", f"관련 {LABELS['decision_events']}", "item", "od_events", multiple=True)]},
]


def normalized():
    import db
    return [db.normalize_definition(d) for d in TEMPLATE]


def seed(uid, log=print):
    """멱등 시드. 반환: [(key, 'applied'|'skipped', object_id)]."""
    import db
    have = {d["key"]: d for d in db.definitions(uid, include_archived=True)}
    out = []
    for d in TEMPLATE:
        if d["key"] in have:
            out.append((d["key"], "skipped", have[d["key"]]["id"]))
            log(f"건너뜀 {d['key']} ({have[d['key']]['id']}) — 이미 있음")
            continue
        p = db.definition_propose(uid, None, 0, d)
        a = db.definition_apply(uid, p["object_id"], p["proposal_id"], p["base_revision"])
        out.append((d["key"], "applied", a["id"]))
        log(f"적용 {d['key']} → {a['id']} revision {a['revision']} · 필드 {len(a['fields'])}개")
    return out


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.removeprefix("export ").strip(), v.strip().strip('"').strip("'"))


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    dry = "--dry-run" in argv
    if len(args) != 1:
        raise SystemExit("사용법: template_author.py <email> [--dry-run]")
    if dry:   # DB 연결 전에 분기
        print(json.dumps(normalized(), ensure_ascii=False, indent=2))
        return
    _load_env(HERE / ".env")
    import db
    try:
        with db.tx() as c:   # 없는 이메일로 새 사용자를 만들지 않는다(오타 방지)
            r = c.execute("SELECT id FROM users WHERE email=%s", (args[0].strip().lower(),)).fetchone()
        if not r:
            raise SystemExit(f"그 이메일의 사용자가 없어요: {args[0]} — 먼저 로그인(또는 MCP 연결)해 계정을 만드세요.")
        seed(r["id"])
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
