"""활동 · 관계 · 「나」 집계 · 사람/출처/축 문맥 (board 이전, 2026-10-20). 읽기 전용 모듈 — 쓰기는 imports.py 가 한다
(예외: 끝 절 「휴지통 PR-1」 의 활동 원천 전이·물리 정리 — db.trash_* 와 main 정리 회차가 부른다).

정본: r3 T3(집계 규칙·화면 배치·MCP 읽기). 웹(#_me·#_obj/activity·사람·출처·축 상세)과 MCP 읽기 도구가 같은 함수를 쓴다.
사람 HMAC·원 URL·PC 경로는 내보내지 않는다. 사람은 id·별칭만, 원문은 raw_id(불투명)만.

집계 대상 = 현재 판(import_entries.is_current_applied) · state=active · deleted_at IS NULL · 휴지통 아님 · occurred_on 이 기간 안.
휴지통(PR-1): 휴지통 원천의 판은 목록·상세·집계·문맥 전부에서 빠진다. 휴지통 사람·축·기록은 연결만 빠지고 활동은 남는다.
기간 = KST 오늘 포함 최근 N일(7d·30d·90d) 또는 all. occurred_on NULL = 「날짜 미확인」(기간 집계 밖, 따로 센다 — observed_on 분포 곁들임).
성향 점수·친밀도·영향력·역량 점수는 만들지 않는다. 주제가 없는 자료에서 키워드를 뽑지 않는다.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json

import db
from db import CrmError, _all, _one, tx


def _live(alias, table):
    """휴지통 PR-1 — 일반 이용 대상 SQL 조각. trashed_at NULL = 일반(휴지통·만료·영구 삭제 요청·사람 표식은 모두 trashed_at 이 있다).
    table 은 읽는 사람을 위한 표시(5종 같은 열 — db.TRASH_TABLES)."""
    assert table in db.TRASH_TABLES, table
    return f"{alias}.trashed_at IS NULL"


PERIODS = {"7d": 7, "30d": 30, "90d": 90, "all": None}
DEFAULT_PERIOD = "30d"
REACT_ACTIONS = ("like", "repost", "comment", "reply")      # 「자주 반응한 사람」 대상 행동
REACT_ROLES = ("author", "counterparty")                    # participant 만 있는 사람은 올리지 않는다
TOP_PEOPLE = 10
GROUNDS_MAX = 20                                            # 근거 연결을 보여 줄 확정 기준 수(최근 고친 순)
CONTEXT_MAX = 50                                            # 상세 화면 활동 목록 상한
ACTION_LABELS = {"like": "좋아요", "repost": "리포스트", "comment": "댓글", "read": "읽음", "save": "보관", "send": "보냄", "reply": "답장"}
PROVIDER_LABELS = {"sns": "SNS", "web": "웹", "mail": "메일", "kakao": "카카오톡"}
ROLE_LABELS = {"author": "작성자", "counterparty": "상대", "participant": "참여자"}
NOTICE = ("연결된 활동과 내 글을 정해진 계산 규칙으로 보여 줍니다. 활동량을 성격이나 능력으로 해석하지 않습니다. "
          "AI가 작성한 설명은 저장 승인 대기에 표시합니다.")

# 현재 판 활동 — 원본마다 현재 접수 행 하나(ux_import_current)가 가리키는 판. noop 개정이 같은 판을 가리켜도 한 번
# 휴지통 PR-1: 휴지통 원천의 판(모든 판이 같은 삭제 사건)은 빠진다. 영구 삭제분은 정리가 is_current_applied 를 내려 빠진다
_CUR = ("SELECT a.* FROM activities a WHERE a.user_id=%(uid)s AND a.deleted_at IS NULL AND " + _live("a", "activities") + " AND EXISTS ("
        "SELECT 1 FROM import_entries e WHERE e.user_id=a.user_id AND e.target_activity_id=a.id AND e.is_current_applied "
        "AND e.kind='activity')")
_TODAY = "(now() AT TIME ZONE 'Asia/Seoul')::date"
ORDER = "occurred_on DESC NULLS LAST, received_at DESC, id DESC"   # 목록·문맥 공통 정렬(행동 날짜 최근 → 날짜 미확인 → 받은 순)

# 활동 dict(목록) = {id, action, action_label, state, occurred_on, observed_on, received_at, source: {id, name, provider},
#                   people: [{id, alias, role}], axes: [{id, title}], memo_count, raw_id}


def _j(v):
    return v.isoformat() if isinstance(v, (_dt.datetime, _dt.date)) else v


def _nf(what):
    return CrmError("NOT-FOUND", f"{what}을 찾지 못했어요.", "목록을 다시 보고 id 를 확인하세요.")


def _period(period):
    if period not in PERIODS:
        raise CrmError("ARG-INVALID", f"period 는 {', '.join(PERIODS)} 중 하나예요.")
    return PERIODS[period]


def _in_period(days):
    """occurred_on 조건 SQL — KST 오늘 포함 최근 days 일, None = 날짜 있는 전부."""
    if days is None:
        return "occurred_on IS NOT NULL"
    return f"occurred_on BETWEEN {_TODAY} - {int(days) - 1} AND {_TODAY}"


def _decorate(c, uid, rows):
    """활동 행 → 활동 dict 목록(출처·사람·축·메모 수를 한 번에 붙인다)."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    srcs = {r["id"]: r for r in _all(c, "SELECT id,name,activity_provider FROM sources WHERE user_id=%s AND id = ANY(%s)",
                                       (uid, list({r["source_id"] for r in rows})))}
    people, axes, memos = {}, {}, {}
    # 휴지통 끝점(사람·축·메모 기록)은 JOIN 에서 떨어뜨려 그 연결만 뺀다 — 활동은 독립 결과라 남는다(r4 §2·§7). 쿼리 수 그대로
    for r in _all(c, "SELECT l.activity_id,l.relation,l.person_id,p.alias,l.axis_item_id,i.title,l.linked_record_id "
                     "FROM activity_links l LEFT JOIN people p ON p.user_id=l.user_id AND p.id=l.person_id AND " + _live("p", "people") + " "
                     "LEFT JOIN items i ON i.user_id=l.user_id AND i.id=l.axis_item_id AND " + _live("i", "items") + " "
                     "LEFT JOIN records rec ON rec.user_id=l.user_id AND rec.id=l.linked_record_id AND " + _live("rec", "records") + " "
                     "WHERE l.user_id=%s AND l.activity_id = ANY(%s) AND (l.person_id IS NULL OR p.id IS NOT NULL) "
                     "AND (l.axis_item_id IS NULL OR i.id IS NOT NULL) "
                     "AND (l.linked_record_id IS NULL OR (rec.id IS NOT NULL AND rec.deleted_at IS NULL)) "
                     "ORDER BY l.created_at, l.id", (uid, ids)):
        if r["person_id"]:
            people.setdefault(r["activity_id"], []).append({"id": r["person_id"], "alias": r["alias"], "role": r["relation"]})
        elif r["axis_item_id"]:
            axes.setdefault(r["activity_id"], []).append({"id": r["axis_item_id"], "title": r["title"]})
        elif r["linked_record_id"]:
            memos[r["activity_id"]] = memos.get(r["activity_id"], 0) + 1
    out = []
    for r in rows:
        s = srcs.get(r["source_id"]) or {}
        out.append({"id": r["id"], "action": r["action"], "action_label": ACTION_LABELS.get(r["action"], r["action"]),
                    "state": r["state"], "occurred_on": _j(r["occurred_on"]), "observed_on": _j(r["observed_on"]),
                    "received_at": _j(r["received_at"]),
                    "source": {"id": r["source_id"], "name": s.get("name"), "provider": s.get("activity_provider")},
                    "people": people.get(r["id"], []), "axes": axes.get(r["id"], []), "memo_count": memos.get(r["id"], 0),
                    "raw_id": r["raw_id"]})
    return out


def me_aggregate(uid: str, period: str = DEFAULT_PERIOD) -> dict:
    """「나」 집계(r3 T3 표). 반환
    {"period": "30d", "from": "YYYY-MM-DD"|None, "to": "YYYY-MM-DD"(KST 오늘),
     "total": n,                                  # 서로 다른 활동 ID
     "by_action": [{"action", "label", "n"}],     # 건수 내림차순 → action 순
     "by_axis": [{"item_id", "title", "n"}], "axis_unclassified": n, "axis_sum_differs": bool,
     "top_people": [{"person_id", "alias", "n"}],  # REACT_ACTIONS × REACT_ROLES, 최대 TOP_PEOPLE, 건수 ↓ → id ↑
     "by_source": [{"source_id", "name", "provider", "n"}],
     "undated": {"n": n, "observed": [{"observed_on": "YYYY-MM-DD"|None, "n"}]},   # 기간 무관, 현재·active 전체 중 occurred_on NULL
     "grounds": {"principles": [{"item_id", "title", "records": n, "activities": n}]},  # 확정 기준 → 근거 연결(최대 2단계)
     "notice": 「나」 홈 설명 문장}
    period 가 PERIODS 밖이면 ARG-INVALID."""
    days = _period(period)
    a = {"uid": uid}
    base = f"WITH cur AS ({_CUR} AND a.state='active'), per AS (SELECT * FROM cur WHERE {_in_period(days)}) "
    with tx() as c:
        today = _one(c, f"SELECT {_TODAY} AS d")["d"]
        total = _one(c, base + "SELECT count(DISTINCT id) AS n FROM per", a)["n"]
        by_action = [{"action": r["action"], "label": ACTION_LABELS.get(r["action"], r["action"]), "n": r["n"]} for r in _all(
            c, base + "SELECT action, count(DISTINCT id) AS n FROM per GROUP BY action ORDER BY n DESC, action", a)]
        by_axis = [{"item_id": r["item_id"], "title": r["title"], "n": r["n"]} for r in _all(
            c, base + "SELECT l.axis_item_id AS item_id, i.title, count(DISTINCT per.id) AS n FROM per "
                      "JOIN activity_links l ON l.user_id=per.user_id AND l.activity_id=per.id AND l.relation='axis' "
                      "JOIN items i ON i.user_id=l.user_id AND i.id=l.axis_item_id AND " + _live("i", "items") + " "
                      "GROUP BY l.axis_item_id, i.title ORDER BY n DESC, l.axis_item_id", a)]
        # 휴지통 축만 달린 활동 = 미분류(축별 합 + 미분류 = 전체 셈이 유지된다)
        unclassified = _one(c, base + "SELECT count(DISTINCT id) AS n FROM per WHERE NOT EXISTS (SELECT 1 FROM activity_links l "
                                      "JOIN items i ON i.user_id=l.user_id AND i.id=l.axis_item_id AND " + _live("i", "items") + " "
                                      "WHERE l.user_id=per.user_id AND l.activity_id=per.id AND l.relation='axis')", a)["n"]
        top = [{"person_id": r["person_id"], "alias": r["alias"], "n": r["n"]} for r in _all(
            c, base + "SELECT l.person_id, p.alias, count(DISTINCT per.id) AS n FROM per "
                      "JOIN activity_links l ON l.user_id=per.user_id AND l.activity_id=per.id AND l.relation = ANY(%(roles)s) "
                      "JOIN people p ON p.user_id=l.user_id AND p.id=l.person_id AND " + _live("p", "people") + " "
                      "WHERE per.action = ANY(%(acts)s) "
                      "GROUP BY l.person_id, p.alias ORDER BY n DESC, l.person_id LIMIT %(top)s",
            {**a, "roles": list(REACT_ROLES), "acts": list(REACT_ACTIONS), "top": TOP_PEOPLE})]
        by_source = [{"source_id": r["source_id"], "name": r["name"], "provider": r["provider"], "n": r["n"]} for r in _all(
            c, base + "SELECT per.source_id, s.name, s.activity_provider AS provider, count(DISTINCT per.id) AS n FROM per "
                      "JOIN sources s ON s.user_id=per.user_id AND s.id=per.source_id GROUP BY per.source_id, s.name, s.activity_provider "
                      "ORDER BY n DESC, per.source_id", a)]
        undated = _one(c, base + "SELECT count(DISTINCT id) AS n FROM cur WHERE occurred_on IS NULL", a)["n"]
        observed = [{"observed_on": _j(r["observed_on"]), "n": r["n"]} for r in _all(
            c, base + "SELECT observed_on, count(DISTINCT id) AS n FROM cur WHERE occurred_on IS NULL GROUP BY observed_on "
                      "ORDER BY observed_on DESC NULLS LAST", a)]
        grounds = _grounds(c, uid)
    return {"period": period, "from": None if days is None else _j(today - _dt.timedelta(days=days - 1)), "to": _j(today),
            "total": total, "by_action": by_action, "by_axis": by_axis, "axis_unclassified": unclassified,
            "axis_sum_differs": sum(x["n"] for x in by_axis) + unclassified != total, "top_people": top, "by_source": by_source,
            "undated": {"n": undated, "observed": observed}, "grounds": {"principles": grounds}, "notice": NOTICE}


def _grounds(c, uid):
    """확정 기준(principle·confirmed·적용·미보관, 최근 고친 순 GROUNDS_MAX) → 연결된 기록·활동 수. 명시한 관계만 최대 2단계:
    1단계 = 기준의 reference(기록·후보의 근거 기록·활동·항목) · 2단계 = 1단계 항목의 reference(기록·후보의 근거 기록·활동).
    기록은 지우지 않은 것, 활동은 가리킨 판(지우지 않은 것). 순환해도 2단계에서 멈춘다."""
    ps = _all(c, "SELECT i.id, i.title FROM items i JOIN object_defs o ON o.user_id=i.user_id AND o.id=i.object_id "
                 "WHERE i.user_id=%s AND o.semantic_kind='principle' AND i.decision='confirmed' AND i.revision > 0 "
                 "AND i.archived_at IS NULL AND " + _live("i", "items") + " ORDER BY i.updated_at DESC, i.id LIMIT %s", (uid, GROUNDS_MAX))
    if not ps:
        return []
    ids = [p["id"] for p in ps]
    # 휴지통 항목은 2단계로 건너가지 않는다(대상 항목 JOIN). 기록·활동 휴지통은 아래 live_r·live_a 가 거른다(후보 → 부모 기록)
    hop = ("SELECT l.source_item_id AS src, ti.id AS target_item_id, COALESCE(l.target_record_id, cd.record_id) AS rec, l.target_activity_id AS act "
           "FROM item_links l LEFT JOIN candidates cd ON cd.user_id=l.user_id AND cd.id=l.target_candidate_id "
           "LEFT JOIN items ti ON ti.user_id=l.user_id AND ti.id=l.target_item_id AND " + _live("ti", "items") + " "
           "WHERE l.user_id=%s AND l.source_item_id = ANY(%s)")
    first = _all(c, hop, (uid, ids))
    mids = {r["target_item_id"] for r in first if r["target_item_id"]}
    second = _all(c, hop, (uid, list(mids))) if mids else []
    via = {}
    for r in second:
        via.setdefault(r["src"], []).append(r)
    recs, acts = {p: set() for p in ids}, {p: set() for p in ids}
    for r in first:
        for x in [r] + via.get(r["target_item_id"], []):
            if x["rec"]:
                recs[r["src"]].add(x["rec"])
            if x["act"]:
                acts[r["src"]].add(x["act"])
    all_r = {x for s in recs.values() for x in s}
    all_a = {x for s in acts.values() for x in s}
    live_r = {r["id"] for r in _all(c, "SELECT id FROM records r WHERE user_id=%s AND id = ANY(%s) AND deleted_at IS NULL AND "
                                       + _live("r", "records"),
                                    (uid, list(all_r)))} if all_r else set()
    live_a = {r["id"] for r in _all(c, "SELECT id FROM activities a WHERE user_id=%s AND id = ANY(%s) AND deleted_at IS NULL AND "
                                       + _live("a", "activities"),
                                    (uid, list(all_a)))} if all_a else set()
    return [{"item_id": p["id"], "title": p["title"], "records": len(recs[p["id"]] & live_r), "activities": len(acts[p["id"]] & live_a)}
            for p in ps]


def _cursor(cursor):
    """불투명 cursor → 건너뛸 개수. 정렬에 NULL 날짜가 섞여 위치 기반이다(개인 규모 — 쪽 사이에 새 활동이 오면 겹칠 수 있다)."""
    if not cursor:
        return 0
    try:
        n = json.loads(base64.urlsafe_b64decode(str(cursor).encode()).decode())
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError
        return n
    except Exception:
        raise CrmError("ARG-INVALID", "cursor 가 맞지 않아요.", "처음부터(cursor 빈 값) 다시 보세요.")


def _limit(limit):
    if limit is None:
        return db.PAGE_DEFAULT
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= db.PAGE_MAX:
        raise CrmError("ARG-INVALID", f"limit 은 1~{db.PAGE_MAX} 정수예요.")
    return limit


def activity_list(uid: str, action: str = "", person_id: str = "", source_id: str = "", axis_item_id: str = "",
                  dated: str = "", cursor: str = "", limit: int = 50) -> dict:
    """현재 판 활동 목록 — 정렬 ORDER(행동 날짜 최근 → 날짜 미확인 → 받은 시각 최근 → id). 거르기: 행동 · 사람 · 출처 · 축 ·
    dated(""=전부, "yes", "no"). 철회(state=retracted) 판도 목록엔 보인다(화면 「철회됨」) — 집계에선 빠진다(메인 판정 2026-10-20).
    반환 {"activities": [활동 dict…], "next_cursor"}."""
    import imports   # 순환 import 피함(imports 가 db 를 먼저 쓴다)
    if action and action not in imports.ACTIONS:
        raise CrmError("ARG-INVALID", f"action 은 {', '.join(imports.ACTIONS)} 중 하나예요.")
    if dated not in ("", "yes", "no"):
        raise CrmError("ARG-INVALID", "dated 는 빈 값·yes·no 중 하나예요.")
    limit, after = _limit(limit), _cursor(cursor)
    sql, a = f"SELECT * FROM ({_CUR}) cur WHERE true", {"uid": uid}
    if action:
        sql += " AND action=%(action)s"
        a["action"] = action
    if source_id:
        sql += " AND source_id=%(source_id)s"
        a["source_id"] = str(source_id)
    if dated:
        sql += " AND occurred_on IS NOT NULL" if dated == "yes" else " AND occurred_on IS NULL"
    # 휴지통 사람·축으로 거르면 빈 목록(그 id 로 활동 수를 셀 수 없게 — 끝점 JOIN 하나)
    for col, val, tbl in (("person_id", person_id, "people"), ("axis_item_id", axis_item_id, "items")):
        if val:
            sql += (f" AND EXISTS (SELECT 1 FROM activity_links l JOIN {tbl} t ON t.user_id=l.user_id AND t.id=l.{col} "
                    f"AND {_live('t', tbl)} WHERE l.user_id=cur.user_id AND l.activity_id=cur.id AND l.{col}=%({col})s)")
            a[col] = str(val)
    with tx() as c:
        rows = _all(c, sql + f" ORDER BY {ORDER} LIMIT %(lim)s OFFSET %(off)s", {**a, "lim": limit + 1, "off": after})
        nxt = ""
        if len(rows) > limit:
            rows = rows[:limit]
            nxt = base64.urlsafe_b64encode(json.dumps(after + limit).encode()).decode()
        return {"activities": _decorate(c, uid, rows), "next_cursor": nxt}


def activity_get(uid: str, activity_id: str) -> dict:
    """활동 하나(특정 판 — 옛 판 ID 도 열린다) + {"current": bool, "current_id", "versions": [{id, received_at, state, current}],
    "memos": [{record_id, title, created}](승인된 메모·반응 기록), "raw": {"raw_id", "local_only": true}}. 없으면 NOT-FOUND."""
    with tx() as c:
        # 휴지통 원천의 판(옛 판 직접 ID 포함) = 없는 활동(일반 경로는 휴지통 상태를 드러내지 않는다 — 휴지통 화면만)
        r = _one(c, "SELECT * FROM activities a WHERE user_id=%s AND id=%s AND deleted_at IS NULL AND " + _live("a", "activities"),
                 (uid, str(activity_id or "")))
        if not r:
            raise _nf("그 활동")
        d = _decorate(c, uid, [r])[0]
        e = _one(c, "SELECT stream_id, original_id FROM import_entries WHERE user_id=%s AND target_activity_id=%s AND kind='activity' "
                    "LIMIT 1", (uid, r["id"]))
        versions, cur_id = [], None
        if e:
            for v in _all(c, "SELECT a.id, a.received_at, a.state, bool_or(x.is_current_applied) AS current FROM import_entries x "
                             "JOIN activities a ON a.user_id=x.user_id AND a.id=x.target_activity_id "
                             "WHERE x.user_id=%s AND x.stream_id=%s AND x.original_id=%s AND x.kind='activity' "
                             "GROUP BY a.id, a.received_at, a.state ORDER BY a.received_at, a.id", (uid, e["stream_id"], e["original_id"])):
                versions.append({"id": v["id"], "received_at": _j(v["received_at"]), "state": v["state"], "current": bool(v["current"])})
                if v["current"]:
                    cur_id = v["id"]
        memos = [{"record_id": m["id"], "title": m["title"], "created": _j(m["created"])} for m in _all(
            c, "SELECT rec.id, rec.title, rec.created FROM activity_links l JOIN records rec ON rec.user_id=l.user_id "
               "AND rec.id=l.linked_record_id WHERE l.user_id=%s AND l.activity_id=%s AND rec.deleted_at IS NULL AND " + _live("rec", "records") + " "
               "ORDER BY rec.created, rec.id", (uid, r["id"]))]
    d.update({"current": cur_id == r["id"], "current_id": cur_id, "versions": versions, "memos": memos,
              "raw": {"raw_id": r["raw_id"], "local_only": True}})
    return d


def _current_ids_where(extra):
    return f"SELECT * FROM ({_CUR}) cur WHERE {extra} ORDER BY {ORDER} LIMIT {CONTEXT_MAX}"


def person_context(uid: str, person_id: str) -> dict:
    """사람 상세 역방향 — {"person": {id, alias, revision, linked: bool}, "activities": [활동 dict(최근 50)], "activity_count": n(현재 판),
    "old_revision_links": n(옛 판 활동 연결 — 사람 삭제를 막는다),
    "records": [{id, title, created}], "items": [{id, object_id, title, field_key}]}. 남의 id = 없는 id = NOT-FOUND."""
    with tx() as c:
        p = _one(c, "SELECT id, alias, revision, identity_hmac IS NOT NULL AS linked FROM people p WHERE user_id=%s AND id=%s AND "
                    + _live("p", "people"), (uid, str(person_id or "")))   # 휴지통·표식 사람 = 없는 사람
        if not p:
            raise _nf("그 사람")
        a = {"uid": uid, "pid": p["id"]}
        has = ("EXISTS (SELECT 1 FROM activity_links l WHERE l.user_id=cur.user_id AND l.activity_id=cur.id AND l.person_id=%(pid)s)")
        rows = _all(c, _current_ids_where(has), a)
        n = _one(c, f"SELECT count(*) AS n FROM ({_CUR}) cur WHERE {has}", a)["n"]
        # 옛 판(현재 판이 아닌 활동)에 걸린 연결 — 집계·목록엔 안 나오지만 사람 삭제를 막는다(FK). 화면이 그 사실을 보이게
        old = _one(c, f"SELECT count(DISTINCT l.activity_id) AS n FROM activity_links l JOIN activities a ON a.user_id=l.user_id "
                      f"AND a.id=l.activity_id AND {_live('a', 'activities')} WHERE l.user_id=%(uid)s AND l.person_id=%(pid)s "
                      f"AND a.id NOT IN (SELECT id FROM ({_CUR}) cur)", a)["n"]   # 휴지통 원천의 판은 세지 않는다
        recs = [{"id": r["id"], "title": r["title"], "created": _j(r["created"])} for r in _all(
            c, "SELECT DISTINCT rec.id, rec.title, rec.created FROM activity_links l JOIN records rec ON rec.user_id=l.user_id "
               "AND rec.id=l.record_id WHERE l.user_id=%s AND l.person_id=%s AND rec.deleted_at IS NULL AND " + _live("rec", "records") + " "
               "ORDER BY rec.created DESC, rec.id",
            (uid, p["id"]))]
        items = [dict(r) for r in _all(
            c, "SELECT i.id, i.object_id, i.title, l.field_key FROM item_links l JOIN items i ON i.user_id=l.user_id "
               "AND i.id=l.source_item_id WHERE l.user_id=%s AND l.target_person_id=%s AND " + _live("i", "items") + " "
               "ORDER BY i.updated_at DESC, i.id",
            (uid, p["id"]))]
        acts = _decorate(c, uid, rows)
    return {"person": {"id": p["id"], "alias": p["alias"], "revision": p["revision"], "linked": p["linked"]},
            "activities": acts, "activity_count": n, "old_revision_links": old, "records": recs, "items": items}


def source_context(uid: str, source_id: str) -> dict:
    """출처 상세 — {"source": {id, kind, name, location, provider}, "activity_count": n, "by_action": [{action, label, n}],
    "activities": [활동 dict(최근 50)]}."""
    with tx() as c:
        s = _one(c, "SELECT id, kind, name, location, activity_provider AS provider FROM sources WHERE user_id=%s AND id=%s",
                 (uid, str(source_id or "")))
        if not s:
            raise _nf("그 출처")
        a = {"uid": uid, "sid": s["id"]}
        rows = _all(c, _current_ids_where("source_id=%(sid)s"), a)
        by = [{"action": r["action"], "label": ACTION_LABELS.get(r["action"], r["action"]), "n": r["n"]} for r in _all(
            c, f"SELECT action, count(*) AS n FROM ({_CUR}) cur WHERE source_id=%(sid)s GROUP BY action ORDER BY n DESC, action", a)]
        acts = _decorate(c, uid, rows)
    return {"source": dict(s), "activity_count": sum(x["n"] for x in by), "by_action": by, "activities": acts}


def axis_context(uid: str, item_id: str) -> dict:
    """축 상세 — 분류 역할 오브젝트의 항목만(아니면 NOT-FOUND). {"axis": {id, title}, "activity_count": n, "activities": [최근 50],
    "records": [{id, title}](record_attach axes), "items": [{id, object_id, title, field_key}](이 축을 가리키는 판단·기준 등)}."""
    with tx() as c:
        oid = db._classify_target(c, uid)
        ax = oid and _one(c, "SELECT id, title FROM items i WHERE user_id=%s AND id=%s AND object_id=%s AND revision > 0 AND "
                             + _live("i", "items"),
                          (uid, str(item_id or ""), oid))
        if not ax:
            raise _nf("그 축")
        a = {"uid": uid, "ax": ax["id"]}
        has = ("EXISTS (SELECT 1 FROM activity_links l WHERE l.user_id=cur.user_id AND l.activity_id=cur.id AND l.axis_item_id=%(ax)s)")
        rows = _all(c, _current_ids_where(has), a)
        n = _one(c, f"SELECT count(*) AS n FROM ({_CUR}) cur WHERE {has}", a)["n"]
        recs = [{"id": r["id"], "title": r["title"]} for r in _all(
            c, "SELECT rec.id, rec.title FROM item_links l JOIN records rec ON rec.user_id=l.user_id AND rec.id=l.source_record_id "
               "WHERE l.user_id=%s AND l.field_key='axes' AND l.target_item_id=%s AND rec.deleted_at IS NULL AND " + _live("rec", "records") + " "
               "ORDER BY rec.created DESC, rec.id",
            (uid, ax["id"]))]
        items = [dict(r) for r in _all(
            c, "SELECT i.id, i.object_id, i.title, l.field_key FROM item_links l JOIN items i ON i.user_id=l.user_id "
               "AND i.id=l.source_item_id WHERE l.user_id=%s AND l.target_item_id=%s AND i.archived_at IS NULL AND " + _live("i", "items") + " "
               "ORDER BY i.updated_at DESC, i.id", (uid, ax["id"]))]
        acts = _decorate(c, uid, rows)
    return {"axis": dict(ax), "activity_count": n, "activities": acts, "records": recs, "items": items}


def state_part(uid: str) -> dict:
    """/api/state 에 실을 키 한 벌(웹 에이전트가 st 에 그대로 합친다):
    {"activity": me_aggregate(uid, "30d"), "inbox": {"pending": imports.inbox_count(uid)},
     "devices": {"active": n, "max": imports.MAX_DEVICES}}"""
    import imports
    with tx() as c:
        n = _one(c, "SELECT count(*) AS n FROM import_streams WHERE user_id=%s AND channel='device' AND revoked_at IS NULL", (uid,))["n"]
    return {"activity": me_aggregate(uid, DEFAULT_PERIOD), "inbox": {"pending": imports.inbox_count(uid)},
            "devices": {"active": n, "max": imports.MAX_DEVICES}}


# ── 휴지통 PR-1: 활동 원천 전이·물리 정리 (r4 §3·§8) ─────────────────
# db.trash_move·trash_restore·trash_purge(kind="activity") 가 사용자 잠금을 잡은 트랜잭션 안에서 trash_op 를 부른다. main 의 정리 회차는
# db.trash_purge_due 보다 먼저 trash_purge_due(여기)를 부른다. 이 절만 쓰기다(원천 단위라 판 목록·대응표를 아는 이 모듈에 둔다).
# 삭제 단위 = 원천(사용자·입력원·original_id)의 모든 판 — 같은 trashed_at·purge_at(삭제 사건 하나, 판별 기한 없음). 옛 판 ID 로 불러도 원천 전체.
# 수신 차단은 imports._source_deleted 가 이 열과 대응표(state=deleted)로 판정한다.

def _source_rows(c, uid, activity_id):
    """활동 ID(어느 판이든) → 같은 원천 모든 판(잠금, now 포함). 없거나 남의 것·지운 판(deleted_at) = NOT-FOUND."""
    e = _one(c, "SELECT stream_id, original_id FROM import_entries WHERE user_id=%s AND target_activity_id=%s AND kind='activity' LIMIT 1",
             (uid, activity_id))
    if not e:
        raise _nf("그 활동")
    rows = _all(c, f"SELECT id, trashed_at, purge_at, deleted_at, {db._TNOW} AS now FROM activities WHERE user_id=%(uid)s AND id IN "
                   "(SELECT target_activity_id FROM import_entries WHERE user_id=%(uid)s AND stream_id=%(s)s AND original_id=%(o)s "
                   "AND target_activity_id IS NOT NULL) ORDER BY received_at, id FOR UPDATE",
                {"now": db.TRASH_CLOCK, "uid": uid, "s": e["stream_id"], "o": e["original_id"]})
    if not any(r["id"] == activity_id and r["deleted_at"] is None for r in rows):
        raise _nf("그 활동")
    return rows


def trash_op(c, uid, op, activity_id):
    """op ∈ move·restore·purge — db._trash_do 와 같은 반환 꼴(kind="activity", id = 부른 판 ID). 호출자가 users 행을 잠갔다.
    move: 이미 휴지통이면 그 사건 그대로(기한 연장 0, changed False) · restore: 기한 전만(TRASH-EXPIRED·NOT-IN-TRASH)
    · purge: 기한 전이면 purge_at=지금, 이미 복원 불가면 같은 결과. 차단 중 받은 판을 복원이 다시 모으지 않는다(PC 는 같은 해시를 다시 안 보낸다)."""
    rows = _source_rows(c, uid, activity_id)
    ids = [r["id"] for r in rows]
    trashed = [r for r in rows if r["trashed_at"] is not None]
    restorable = bool(trashed) and all(db._trash_restorable(r) for r in trashed)
    if op == "move":
        if trashed:   # 중복 삭제 = 기존 결과(원천의 판은 늘 함께 옮겨진다)
            r = trashed[0]
            return {"kind": "activity", "id": activity_id, "trashed_at": _j(r["trashed_at"]), "purge_at": _j(r["purge_at"]),
                    "changed": False, "restorable": restorable}
        db._trash_rows(c, uid, "activities", ids, "move")
        n = _one(c, "SELECT trashed_at, purge_at FROM activities WHERE user_id=%s AND id=%s", (uid, activity_id))
        return {"kind": "activity", "id": activity_id, "trashed_at": _j(n["trashed_at"]), "purge_at": _j(n["purge_at"]),
                "changed": True, "restorable": True}
    if not trashed:
        raise db._not_in_trash()
    if not restorable:
        if op == "purge":   # 이미 복원 불가(기한 지남·요청함) — 같은 결과
            return {"kind": "activity", "id": activity_id, "purge_requested": True, "changed": False}
        raise db._trash_expired()
    db._trash_rows(c, uid, "activities", ids, op)
    if op == "restore":
        return {"kind": "activity", "id": activity_id, "restored": True, "changed": True}
    return {"kind": "activity", "id": activity_id, "purge_requested": True, "changed": True}


def _purge_source(c, uid, stream_id, original_id):
    """원천 하나 물리 제거 — 모든 판이 정리 대상(db._TRASH_DUE)일 때만(아니면 False, 손대지 않음). NO ACTION 참조 순서:
    activity_links(활동 출발 — 사람·축·메모) → item_links.target_activity_id → import_entries(state=deleted · target NULL · 현재 판 해제 —
    원천 차단 표식으로 계정 존속 기간 남는다) → activities. 사람·항목·기록 같은 상대는 지우지 않는다."""
    vers = _all(c, f"SELECT id, ({db._TRASH_DUE}) AS due FROM activities WHERE user_id=%(uid)s AND id IN "
                   "(SELECT target_activity_id FROM import_entries WHERE user_id=%(uid)s AND stream_id=%(s)s AND original_id=%(o)s "
                   "AND target_activity_id IS NOT NULL) FOR UPDATE",   # 판 일부만 지우지 않게 전부 잠근다(대기는 statement_timeout 안)
                {"now": db.TRASH_CLOCK, "uid": uid, "s": stream_id, "o": original_id})
    if not vers or not all(v["due"] for v in vers):   # 그사이 복원·다른 회차가 잡음·판 일부만 대상 — 다음 회차
        return False
    ids = [v["id"] for v in vers]
    c.execute("DELETE FROM activity_links WHERE user_id=%s AND activity_id = ANY(%s)", (uid, ids))
    c.execute("DELETE FROM item_links WHERE user_id=%s AND target_activity_id = ANY(%s)", (uid, ids))
    # deleted_target_id = 지운 판 ID 표식(본문 아님) — 그 활동을 가리키는 늦은 내 글(activity_ref)을 「남의·없는 ID」 와 구별한다
    c.execute("UPDATE import_entries SET state='deleted', deleted_target_id=target_activity_id, target_activity_id=NULL, "
              "is_current_applied=false WHERE user_id=%s AND target_activity_id = ANY(%s)", (uid, ids))
    c.execute("DELETE FROM activities WHERE user_id=%s AND id = ANY(%s)", (uid, ids))
    return True


def trash_purge_due(seconds=None, limit=None) -> dict:
    """활동 원천 물리 정리 한 회차 — {"activities": 지운 원천 수, "failed": 실패 수, "left": 상한으로 남겼나}. 예외를 밖으로 내지 않는다.
    대상 = 판이 db._TRASH_DUE(기한 지남 · 오늘(KST) 옮긴 것 제외)인 원천. 원천 하나 = 트랜잭션 하나 + statement_timeout.
    실패·건너뛴 원천은 이 회차에서 다시 집지 않고 다음 회차에 다시(완료로 기록하지 않는다). 상한 = db.TRASH_PURGE_SECONDS·TRASH_PURGE_LIMIT."""
    import time
    deadline = time.monotonic() + (db.TRASH_PURGE_SECONDS if seconds is None else seconds)
    left = db.TRASH_PURGE_LIMIT if limit is None else limit
    done, failed, seen, after, stop = 0, 0, set(), None, False
    while not stop:
        if left <= 0 or time.monotonic() >= deadline:
            stop = True
            break
        # 휴지통 PR-2(PR-1 후속): 판 행을 (purge_at, user_id, id) 커서로 앞으로만 TRASH_BATCH 행씩 읽고(ix_activities_purge 범위 스캔 + LIMIT)
        # 그 판들의 원천 키만 붙인다 — 전 사용자 만료 판을 모아 GROUP BY 한 뒤 LIMIT 하던 탐색을 바꿨다(한 번에 읽는 양 = 배치 상한).
        # 판이 배치 경계에 걸린 원천은 seen 으로 한 번만 처리한다. 실패·건너뛴 원천은 커서가 지나가 이 회차에 다시 집지 않는다(다음 회차).
        a = {"now": db.TRASH_CLOCK, "n": min(db.TRASH_BATCH, left)}
        cur = ""
        if after:
            cur = " AND purge_at >= %(ap)s AND (purge_at, user_id, id) > (%(ap)s, %(au)s, %(ai)s)"
            a["ap"], a["au"], a["ai"] = after
        try:   # 판 열(trashed_at·purge_at)은 activities 에만 있어 db._TRASH_DUE 를 그대로 쓴다
            with tx() as c:
                c.execute(f"SET LOCAL statement_timeout = '{db.TRASH_STMT_TIMEOUT}'")
                vers = _all(c, f"SELECT v.user_id, v.id, v.purge_at, e.stream_id, e.original_id FROM (SELECT user_id, id, purge_at "
                               f"FROM activities WHERE {db._TRASH_DUE}{cur} ORDER BY purge_at, user_id, id LIMIT %(n)s) v "
                               "LEFT JOIN LATERAL (SELECT stream_id, original_id FROM import_entries WHERE user_id=v.user_id "
                               "AND target_activity_id=v.id LIMIT 1) e ON true ORDER BY v.purge_at, v.user_id, v.id", a)
        except Exception as e:
            print(f"[trash] 활동 정리 대상 조회 실패 — 다음 회차: {type(e).__name__}", flush=True)
            break
        if not vers:
            break
        after = (vers[-1]["purge_at"], vers[-1]["user_id"], vers[-1]["id"])
        keys = []
        for v in vers:
            k = (v["user_id"], v["stream_id"], v["original_id"])
            if v["stream_id"] is not None and k not in seen and k not in keys:
                keys.append(k)
        for k in keys:
            if left <= 0 or time.monotonic() >= deadline:
                stop = True
                break
            left -= 1
            seen.add(k)
            try:
                with tx() as c:
                    c.execute(f"SET LOCAL statement_timeout = '{db.TRASH_STMT_TIMEOUT}'")
                    db._purge_fault("activities", k[2])
                    if _purge_source(c, *k):
                        done += 1
            except Exception as e:
                failed += 1
                print(f"[trash] 활동 원천 하나 정리 실패 — 다음 회차에 다시: {type(e).__name__}", flush=True)
    return {"activities": done, "failed": failed, "left": stop}
