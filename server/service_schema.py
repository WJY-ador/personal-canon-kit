"""PR A — 운영 Object Manager 의 서비스 정의 표시 자료 · Quick Find 색인. 명세: notes 「2026-10-20_PR-A-Setup-구현계획」 + r2 §1~§4.

순수 모듈이다 — db·admin_db·네트워크를 부르지 않는다(사용자 DB 조회 0). 입력 = 코어 정의(web.core_schema — schema.sql 파싱 결과)
+ 표준 std@1·템플릿 author@1 정의(dict). 출력 = 공개 ID·서비스 표시명·자료형 설명만 담은 표시 자료.
표시명 + API 이름(코어 열·std@1·author@1 key — 서비스가 정한 제품 문자열, Salesforce Field Label / API Name 꼴). 선택지 값·파일 경로·운영자 전용 열은
출력에 없다. 열 → 표시명은 CORE_FIELDS 한 곳(표시명이 없는 열·모르는 자료형은 ValueError — 시험이 잡는다). 「key 0」 규칙은 사용자 구조(contract)에만 적용된다.

- 순서는 입력 순서와 무관하게 허용목록(CORE·STD·TEMPLATES) 순서. 같은 정의 → 같은 자료 → 같은 SVG.
- 정의에서 확인할 수 없는 값은 None — 화면은 「모름(정의 미확인)」으로 보인다(없음·선택·잠금 해제로 추정하지 않는다).
- 그래프 입력(graph_resp)은 contract 운영자 응답 모양 — structure_svg.render 를 그대로 쓴다(코어도 objects 에 넣는다).
"""
import unicodedata

import contract
import structure_svg

UNKNOWN = "모름(정의 미확인)"

# 코어 8 — (공개 ID, schema.sql 테이블 = API 이름, 표시명, 서비스 설명). 공개 ID 는 structure_svg.CORE_TEXT 키(record·candidate)와 겹치지 않게 접두를 단다.
# 표시명 = 기존 정본(web.CORE_LABEL · app.html LABELS)과 같은 말(시험이 대조). 출처는 두 정본에 없어 app.html 출처 화면 용어(「출처」)를 쓴다
CORE = (("core-question", "questions", "질문", "사용자가 세운 질문이에요. 기록과 후보가 이 질문에 붙어요."),
        ("core-record", "records", "기록", "사용자가 남긴 기록이에요. 받은 원문을 그대로 저장해요."),
        ("core-candidate", "candidates", "후보", "기록 원문의 한 구간을 근거로 뽑은 판단 후보예요."),
        ("core-ticket", "tickets", "문의", "사용자가 운영자에게 보낸 문의예요."),
        ("core-source", "sources", "출처", "기록을 가져온 곳이에요."),
        # PR B 코어 — 「나」 는 users 행 중 표시 이름·상태·새 탐색 수락·만든 때 열만(web.CORE_ONLY)
        ("core-me", "users", "나", "계정 하나에 하나뿐인 「나」예요. 표시 이름은 운영자 화면에 값이 보이지 않아요."),
        ("core-person", "people", "사람", "사용자가 관계를 구분하려고 적은 별칭 이름표예요."),
        ("core-reflection", "source_reflections", "출처 요약/반응", "출처를 접한 뒤 사용자가 쓴 자기 생각이에요. 판단 근거로 인용하지 않아요."))
# 코어 열 → 서비스 표시명. 소유자 열(user_id)·운영자 전용 열(문의 admin_note·hint)은 core_schema 가 이미 뺐다
CORE_FIELDS = {
    "questions": {"id": "ID", "text": "질문 글", "status": "상태", "created": "만든 때", "updated": "고친 때"},
    "records": {"id": "ID", "question_id": "질문", "title": "제목", "body": "본문", "source": "기록 당시 표기", "created": "만든 때",
                "deleted_at": "지운 때", "notes": "메모", "author_kind": "작성자", "text_process": "가공 방식",
                "provenance_history": "작성 판정 이력", "source_id": "출처"},
    "candidates": {"id": "ID", "record_id": "근거 기록", "question_id": "질문", "text": "판단 문장", "quote": "인용",
                   "quote_start": "인용 시작 위치", "quote_end": "인용 끝 위치", "origin": "누가 뽑았나", "decision": "결정 상태",
                   "edited_by_user": "사용자 수정 여부", "created": "만든 때", "updated": "고친 때", "revision": "변경 번호",
                   "wording_by": "문장 작성 주체", "wording_approved": "문장 승인"},
    "tickets": {"id": "ID", "kind": "종류", "text": "문의 글", "via": "경로", "page": "화면", "tool": "도구", "ua_family": "브라우저",
                "status": "상태", "created": "접수", "updated": "갱신", "closed_at": "종결", "reply": "공개 답변", "reply_at": "답변 시각",
                "public_updated": "공개 갱신"},
    "sources": {"id": "ID", "kind": "종류", "name": "이름", "location": "위치", "created": "만든 때"},
    "users": {"display_name": "표시 이름", "status": "상태", "nav_accepted_at": "새 탐색 수락", "created": "만든 때"},
    "people": {"id": "ID", "alias": "별칭", "revision": "변경 번호", "created": "만든 때", "updated": "고친 때"},
    "source_reflections": {"id": "ID", "source_id": "출처", "kind": "유형", "body": "본문", "review": "검토 상태", "revision": "변경 번호",
                           "reviewed_revision": "검토한 변경 번호", "reviewed_at": "검토 시각", "created": "만든 때", "updated": "고친 때"},
}
SQL_TYPES = {"text": "글", "timestamptz": "시각", "integer": "정수", "boolean": "예/아니오", "jsonb": "구조 자료"}
VALUE_TYPES = {"text": "글", "number": "숫자", "boolean": "예/아니오", "date": "날짜", "select": "선택", "text_list": "글 목록",
               "reference": "참조"}
# 코어 안에서 오브젝트가 아닌 연결 — 표시명·대상 설명. 그래프 선은 긋지 않는다(대상이 8개 안에 없다)
CORE_CHILD = {"ticket_history": "변경 이력"}
RECORD_LINKS = "붙는 곳 · 관심 축"
ITEM_TARGET = f"항목 — 대상 오브젝트 {UNKNOWN} · 분류 대상은 사용자별 역할로 정함"

# 표준 std@1 — (공개 ID, std@1 key, 서비스 설명)
STD = (("std-axis", "axes", "관심 주제를 모아 두는 표준 오브젝트예요."),
       ("std-decision", "decision_events", "결정한 일과 근거 판단을 남기는 표준 오브젝트예요."),
       ("std-principle", "principles", "확정한 판단을 종합해 사용자가 정한 기준이에요."))
STD_VERSION = "std@1"
STD_LOCK = {"state": True, "target": "편입한 계정의 이 표준 오브젝트·표준 필드",
            "reason": f"{structure_svg.LOCK_TEXT} — {contract.LOCKED_ACT}"}
CORE_LOCK = {"state": None, "target": None, "reason": None}
TPL_LOCK = {"state": False, "target": None, "reason": "표준 식별자가 없는 선택 템플릿 정의예요."}

# 선택 템플릿 — 공개 ID → (판 이름, 표시 이름, [(공개 ID, key)]). 서비스 표준(코어 + std@1)과 섞지 않는다
TEMPLATES = {"author-1": ("author@1", "이전 원작자 템플릿 · author@1",
                          (("tpl-axis", "axes"), ("tpl-decision", "decision_events"), ("tpl-principle", "principles"),
                           ("tpl-homework", "open_questions"), ("tpl-publication", "publications"), ("tpl-method", "craft")))}

ORIGIN_VERSION = {"core": None, "standard": STD_VERSION}


def _field(name, api, type_text, required, choices, lock_state):
    return {"name": name, "api": api, "type": type_text, "required": required, "readonly": None, "choices": choices, "locked": lock_state}


def _core_objects(core: list) -> list:
    by_table = {t["table"]: t for t in core}
    ids = {table: oid for oid, table, _, _ in CORE}
    out = []
    for oid, table, name, desc in CORE:
        t = by_table.get(table)
        if t is None:
            raise ValueError(f"코어 정의에 {name} 이 없어요")
        labels = CORE_FIELDS[table]
        fields = []
        for f in t["fields"]:
            if f["name"] not in labels or f["type"] not in SQL_TYPES:
                raise ValueError(f"코어 {name} 의 열 하나를 표시할 수 없어요")   # 열 이름은 오류 문구에도 넣지 않는다
            fields.append(_field(labels[f["name"]], f["name"], SQL_TYPES[f["type"]], bool(f["required"]), f.get("choices"), None))
        rels = []
        for ln in t["links"]:
            if ln["target"] == "items":   # 기록 → item_links(붙는 곳·관심 축) — 대상 오브젝트는 정의에 없다
                rels.append({"name": RECORD_LINKS, "api": ln["field"], "from": oid, "from_text": None, "to": None, "to_text": ITEM_TARGET,
                             "cardinality": None, "required": None, "kind": "분류 연결", "locked": None, "graph": False})
            elif "." in ln["field"]:   # 자식(이력) → 이 오브젝트
                child = ln["field"].split(".", 1)[0]
                if child not in CORE_CHILD:
                    raise ValueError(f"코어 {name} 의 자식 관계 하나를 표시할 수 없어요")
                rels.append({"name": CORE_CHILD[child], "api": ln["field"], "from": None, "from_text": f"{name} {CORE_CHILD[child]}(오브젝트 아님)",
                             "to": oid, "to_text": None, "cardinality": "N:1", "required": ln.get("required"), "kind": ln["kind"],
                             "locked": None, "graph": False})
            else:
                if ln["field"] not in labels or ln["target"] not in ids:
                    raise ValueError(f"코어 {name} 의 관계 하나를 표시할 수 없어요")
                rels.append({"name": labels[ln["field"]], "api": ln["field"], "from": oid, "from_text": None, "to": ids[ln["target"]], "to_text": None,
                             "cardinality": "N:1", "required": ln.get("required"), "kind": ln["kind"], "locked": None, "graph": True})
        out.append({"id": oid, "name": name, "api": table, "desc": desc, "origin": "core", "version": None, "lock": CORE_LOCK,
                    "fields": fields, "relations": rels})
    return out


def _def_objects(doc: dict, plan, origin: str, version: str, lock: dict, descs: dict) -> list:
    """std@1·author@1 정의 → 표시 자료. plan = [(공개 ID, key)] 허용목록 순서. 문서 안 object id → 공개 ID 로 바꾼다."""
    by_key = {o["key"]: o for o in doc["objects"]}
    pub = {}
    for oid, key in plan:
        if key not in by_key:
            raise ValueError(f"{version} 에 오브젝트 하나가 없어요")
        pub[by_key[key]["id"]] = oid
    core_t = {"candidate": "core-candidate", "record": "core-record"}
    out = []
    for oid, key in plan:
        o = by_key[key]
        fields, rels = [], []
        for f in o["fields"]:
            vt = f["value_type"]
            if vt not in VALUE_TYPES:
                raise ValueError(f"{version} 의 자료형 하나를 표시할 수 없어요")
            fields.append(_field(f["label"], f["key"], VALUE_TYPES[vt], bool(f["required"]), len(f["options"] or []) if vt == "select" else None,
                                 lock["state"]))
            if vt != "reference":
                continue
            tk = f["target_kind"]
            to = pub.get(f["target_object_id"]) if tk == "item" else core_t.get(tk)
            if to is None:
                raise ValueError(f"{version} 의 관계 대상 하나를 표시할 수 없어요")
            rels.append({"name": f["label"], "api": f["key"], "from": oid, "from_text": None, "to": to, "to_text": None,
                         "cardinality": contract.cardinality(f), "required": bool(f["required"]), "kind": None, "locked": lock["state"],
                         "graph": True})
        out.append({"id": oid, "name": o["label"], "api": o["key"], "desc": descs.get(oid), "origin": origin, "version": version, "lock": lock,
                    "fields": fields, "relations": rels})
    return out


def build(core: list, std_doc: dict, author_doc: dict) -> dict:
    """{objects: 코어 5 + std@1 3, templates: {공개 ID: {id, version, title, objects}}}. 같은 입력 → 같은 출력(입력 순서 무관)."""
    objs = _core_objects(core) + _def_objects(std_doc, [(oid, key) for oid, key, _ in STD], "standard", STD_VERSION, STD_LOCK,
                                              {oid: d for oid, _, d in STD})
    tpls = {}
    for tid, (version, title, plan) in TEMPLATES.items():
        tpls[tid] = {"id": tid, "version": version, "title": title,
                     "objects": _def_objects(author_doc, plan, "template", version, TPL_LOCK, {})}
    return {"objects": objs, "templates": tpls}


def names_of(objs: list) -> dict:
    """{공개 ID: 표시명} — 서비스 오브젝트 + 템플릿 오브젝트 대상 링크 글자."""
    return {o["id"]: o["name"] for o in objs}


def graph(objs: list) -> tuple[dict, dict, list]:
    """structure_svg 입력 — (응답 모양, 노드 앵커 → 상세 링크, 그래프에 그린 관계 [(오브젝트, 관계)]). 노드 이름 = 서비스 표시명."""
    names = names_of(objs)
    resp = {"objects": [], "relations": [], "core": []}
    hrefs, drawn = {}, []
    for i, o in enumerate(objs):
        resp["objects"].append({"ref": o["name"], "label": o["name"], "origin": o["origin"], "locked": bool(o["lock"]["state"]),
                                "fields": [{"ref": f["name"], "label": f["name"]} for f in o["fields"]]})
        hrefs[structure_svg.node_anchor(i)] = object_href(o["id"])
    for o in objs:
        for r in o["relations"]:
            if r["graph"] and r["to"] in names:
                resp["relations"].append({"from": o["name"], "field": r["name"], "to": names[r["to"]], "cardinality": r["cardinality"],
                                          "origin": o["origin"], "locked": bool(r["locked"])})
                drawn.append((o, r))
    return resp, hrefs, drawn


def object_href(oid: str, tid: str | None = None) -> str:
    return f"/admin/schema/templates/{tid}/objects/{oid}" if tid else f"/admin/schema/objects/{oid}"


# ── Quick Find (r2 §2) — 메모리 색인. 허용한 글자만 명시적으로 복사한다(정의 전체 직렬화 금지) ──
FIND_MAX = 100
FIND_LIMIT = 50
FIND_KINDS = {"menu": "운영 메뉴", "object": "오브젝트", "field": "필드", "relation": "관계"}


def norm(s: str) -> str:
    """앞뒤 공백 제거 → NFC → 영문 대소문자 무시."""
    return unicodedata.normalize("NFC", s.strip()).lower()


def index(menu, objs: list) -> list:
    """[{kind, title, desc, href, hay}] — 운영 메뉴 → 오브젝트 → 필드·관계 정의, 같은 종류 안은 정의 순서. 찾는 글자 = 표시명·API 이름·설명·자료형.
    menu = [(href, 표시명, 설명, 별칭들)] — 웹 ADMIN_MENU. objs = 서비스 표준(코어 + std@1)만 — 템플릿 내부는 넣지 않는다."""
    names = names_of(objs)
    out = [{"kind": "menu", "title": label, "desc": desc, "href": href, "hay": norm(" ".join((label, desc) + tuple(aliases)))}
           for href, label, desc, aliases in menu]
    for o in objs:
        tag = "코어" if o["origin"] == "core" else f"표준 {o['version']}"
        out.append({"kind": "object", "title": o["name"], "desc": f"{tag} · API 이름 {o['api']} · {o['desc']}", "href": object_href(o["id"]),
                    "hay": norm(f"{o['name']} {o['api']} {o['desc']} {tag}")})
    for o in objs:
        for j, f in enumerate(o["fields"]):
            out.append({"kind": "field", "title": f"{o['name']} · {f['name']}", "desc": f"API 이름 {f['api']} · 자료형 {f['type']}",
                        "href": f"{object_href(o['id'])}#f{j + 1}", "hay": norm(f"{o['name']} {f['name']} {f['api']} {f['type']}")})
        for j, r in enumerate(o["relations"]):
            to = names.get(r["to"]) or r["to_text"] or ""
            frm = names.get(r["from"]) or r["from_text"] or ""
            out.append({"kind": "relation", "title": f"{frm} · {r['name']} → {to}", "desc": f"관계 · API 이름 {r['api']}",
                        "href": f"{object_href(o['id'])}#r{j + 1}", "hay": norm(f"{frm} {r['name']} {r['api']} {to}")})
    return out


def find(q: str, idx: list) -> tuple[str, list, bool]:
    """(상태, 결과, 넘침). 상태 = ok · empty(빈 검색 → 운영 메뉴) · too_long. 검색어는 공백으로 나눈 AND 부분 문자열(정규식 아님)."""
    s = norm(q)
    if len(s) > FIND_MAX:
        return "too_long", [], False
    if not s:
        return "empty", [e for e in idx if e["kind"] == "menu"], False
    terms = s.split()
    hits = [e for e in idx if all(t in e["hay"] for t in terms)]
    return "ok", hits[:FIND_LIMIT], len(hits) > FIND_LIMIT
