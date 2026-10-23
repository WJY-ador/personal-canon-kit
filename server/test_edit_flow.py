"""글 수정 흐름 시험 — 내 글·항목 제목·본문 웹 저장 = 확정 · 판 고정 · 확인 증거 · 변경 번호 · 재시도 · 영구 삭제 (2026-10-23).

실행: DATABASE_URL=postgresql://localhost/crm_test_edit python3 server/test_edit_flow.py
가드: server/.env 를 읽지 않는다(운영 DB 폴백 사고 방지) — DATABASE_URL 을 명시해야 하고, 호스트는 로컬, DB 이름은 crm_test 로 시작해야 한다.
정본: notes 글-수정-구현계획 §1 · astra r2 「공통 계약」·「최종 가안 A′」·「놓치기 쉬운 조건」 5. 데이터 상태(행·열)까지 본다.
"""
import asyncio
import datetime as dt
import hashlib
import os
import secrets
import sys
import urllib.parse
import uuid
from pathlib import Path

HERE = Path(__file__).parent
URL = os.environ.get("DATABASE_URL", "")
if not URL:
    raise SystemExit("중단: DATABASE_URL 을 명시하세요(예: postgresql://localhost/crm_test_edit). server/.env 는 읽지 않아요.")
_u = urllib.parse.urlsplit(URL)
if _u.hostname not in ("localhost", "127.0.0.1", "::1", None) or not _u.path.lstrip("/").startswith("crm_test"):
    raise SystemExit("중단: 로컬 시험 DB(이름 crm_test*)만 허용해요.")
PUBLIC = "http://127.0.0.1:8800"
os.environ["CRM_DEV"] = "1"
os.environ["CRM_DEV_USER"] = "ed-main@example.com"
os.environ["AUTHKIT_ISSUER"] = "https://fake.authkit.test"
os.environ["CRM_PUBLIC_URL"] = PUBLIC
os.environ["CRM_TRASH_ENABLED"] = "1"
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402
import psycopg  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402
import stack_service  # noqa: E402

MAIN, OTHER, QC1, QC3 = "ed-main@example.com", "ed-other@example.com", "ed-qc1@example.com", "ed-qc3@example.com"
EMAILS = (MAIN, OTHER, QC1, QC3)
# 일반 조회 쿼리 수 — 글 수정 전 코드(feat/trash2 4d0902e)에서 잰 값. 수정한 글이 있어도·몇 편이어도 같아야 한다(조회 수 증가 0)
BASELINE = {"state": 20, "candidates": 2, "items": 3, "item_get": 7, "record_get": 1, "records": 1}


def ok(msg):
    print("OK —", msg, flush=True)


def cleanup():
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(EMAILS),)).fetchall()]
        c.execute("DELETE FROM admin_audit WHERE actor_id = ANY(%s) OR target_id = ANY(%s)", (ids, ids))
    for uid in ids:
        db.user_delete(uid)


def rows(sql, args=()):
    with db.tx() as c:
        return c.execute(sql, args).fetchall()


def one(sql, args=()):
    with db.tx() as c:
        return c.execute(sql, args).fetchone()


def run_sql(sql, args=()):
    with db.tx() as c:
        c.execute(sql, args)


def expect(code, fn, *a, **k):
    try:
        fn(*a, **k)
    except db.CrmError as e:
        assert e.code == code, (code, e.code, e.message)
        return e
    raise AssertionError(f"{code} 이어야 해요")


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def rq():
    return "rq_" + secrets.token_hex(8)


def user(email, name="수정시험"):
    uid = db.user_by_email(email)["id"]
    db.display_name_set(uid, name)
    return uid


def decisions_today(uid):
    n = 0
    for table, col, *extra in db._DAILY_SOURCES["decisions"]:
        cond = "".join(f" AND {x}" for x in extra)
        n += one(f"SELECT count(*) AS n FROM {table} WHERE user_id=%s AND {col} >= {db._KST_TODAY}{cond}", (uid,))["n"]
    return n


def confirms(uid, kind, tid):
    return rows("SELECT version_no, body_sha, kind, request_id FROM content_confirms WHERE user_id=%s AND target_kind=%s AND target_id=%s "
                "ORDER BY id", (uid, kind, tid))


def principle(uid, title, body="", links=None):
    p = db.item_propose(uid, "od_principles", 1, None, 0, None, title, body, {}, links or [])
    return db.item_decide(uid, p["item_id"], p["base_revision"], p["proposal_id"], "apply")


def axis(uid, title, body=""):
    p = db.item_propose(uid, "od_axes", 1, None, 0, None, title, body, {}, [])
    return db.item_decide(uid, p["item_id"], p["base_revision"], p["proposal_id"], "apply")


def rec_edit(uid, rid, title, body, rid_req=None, restored=None):
    g = db.record_get(uid, rid)
    return db.record_edit(uid, rid, title, body, g["content_version"], g["change_no"], rid_req or rq(), restored)


def item_edit(uid, iid, title, body, req=None):
    g = db.item_get(uid, iid)
    return db.item_edit(uid, iid, title, body, g["revision"], g["change_no"], req or rq())


def conversation_record(uid, body, matching=True):
    """확정한 대화 정리 글(옛 데이터 꼴) — 확인 이벤트 + conversation 기록. matching=False 면 당시 해시가 지금 본문과 다르다."""
    ceid = uuid.uuid4()
    rid = db.new_id("r")
    with db.tx() as c:
        c.execute("INSERT INTO confirm_events (id,user_id,session_id,job_id,preview_id,preview_version,preview_sha,request_id,final_text_sha) "
                  "VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s)", (ceid, uid, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "0" * 64, rq(),
                                                        sha(body if matching else body + " (당시)")))
        c.execute("INSERT INTO records (id,user_id,title,body,source,author_kind,text_process,generation_kind,generation_meta) "
                  "VALUES (%s,%s,%s,%s,'대화 정리','self','structured','conversation',%s)",
                  (rid, uid, body[:20], body, db.Jsonb({"confirm_event_id": str(ceid)})))
    return rid


# ── 1. 내 글 저장 기본 · 판 고정 · 과거 판 열기 ─────────────────────────────

def test_record_edit(uid):
    body1 = "첫 판 원문입니다. 오늘 고객 회의에서 일정부터 정했다."
    r = db.record_add(uid, "회의 메모", body1)
    rid = r["id"]
    added, _ = db.candidates_propose(uid, rid, "", [{"text": "일정을 먼저 정한다", "quote": "일정부터 정했다"}], "ai")
    c1 = added[0]
    g = db.record_get(uid, rid)
    assert (g["content_version"], g["change_no"], g["eligible"], g["confirmed"]) == (1, 0, True, False), g
    assert one("SELECT record_version FROM candidates WHERE id=%s", (c1,))["record_version"] == 1
    # 판·변경 번호 어긋남 = CONFLICT + latest(최신 글)
    e = expect("CONFLICT", db.record_edit, uid, rid, "회의 메모", "새 본문", 2, 0, rq())
    assert e.latest == {"title": "회의 메모", "body": body1, "content_version": 1, "change_no": 0}, e.latest
    expect("CONFLICT", db.record_edit, uid, rid, "회의 메모", "새 본문", 1, 1, rq())
    expect("NO-CHANGE", db.record_edit, uid, rid, "  회의 메모 ", body1, 1, 0, rq())   # 제목 앞뒤 공백은 다듬는다 — 같은 글
    expect("ARG-INVALID", db.record_edit, uid, rid, "", "x", 1, 0, rq())
    expect("ARG-INVALID", db.record_edit, uid, rid, "t", "  ", 1, 0, rq())
    expect("ARG-INVALID", db.record_edit, uid, rid, "t", "x", 1, 0, "short")
    expect("PAYLOAD-LIMIT", db.record_edit, uid, rid, "t", "가" * (db.LIMIT_BODY // 3 + 1), 1, 0, rq())
    assert rows("SELECT 1 FROM record_versions WHERE user_id=%s AND record_id=%s", (uid, rid)) == [], "거절 = 판 0"
    # 저장
    body2 = "둘째 판. 일정보다 범위를 먼저 정했어야 했다."
    out = rec_edit(uid, rid, "회의 메모(고침)", body2)
    assert out["saved"] == {"version": 2, "change_no": 1, "confirmed": True, "replay": False}, out
    v1 = one("SELECT * FROM record_versions WHERE user_id=%s AND record_id=%s AND version_no=1", (uid, rid))
    assert (v1["title"], v1["body"], v1["body_sha"], v1["author_kind"], v1["text_process"]) == ("회의 메모", body1, sha(body1), "self", "verbatim")
    assert v1["became_current_at"] == one("SELECT created FROM records WHERE id=%s", (rid,))["created"], "판 1 = 최초 수신 시각부터"
    cur = one("SELECT title,body,body_sha,content_version,change_no,content_at,source,author_kind,text_process,generation_kind "
              "FROM records WHERE id=%s", (rid,))
    assert (cur["body"], cur["body_sha"], cur["content_version"], cur["change_no"]) == (body2, sha(body2), 2, 1) and cur["content_at"]
    assert (cur["author_kind"], cur["text_process"], cur["generation_kind"]) == ("self", "verbatim", "unknown"), "출처·생성 경로 유지"
    assert [(x["version_no"], x["body_sha"], x["kind"]) for x in confirms(uid, "record", rid)] == [(2, sha(body2), "direct_edit")]
    g = db.record_get(uid, rid)
    assert (g["content_version"], g["change_no"], g["eligible"], g["confirmed"], g["body"]) == (2, 1, True, True, body2), g
    assert next(x for x in db.records(uid) if x["id"] == rid)["content_version"] == 2
    # 기존 후보 = 판 1 고정 · 과거 판 근거로 확정 가능(판 1 = 최초 원문)
    cand = db.candidate_get(uid, c1)
    assert (cand["record_version"], cand["quote"], cand["review"]) == (1, "일정부터 정했다", None), cand
    assert body1[cand["quote_start"]:cand["quote_end"]] == cand["quote"], "오프셋은 그 판 본문에만"
    db.candidate_decide(uid, c1, "confirm")
    # 새 후보 = 지금 판 · 옛 본문에만 있는 인용은 버린다
    added, dropped = db.candidates_propose(uid, rid, "", [{"text": "범위를 먼저", "quote": "범위를 먼저 정했어야"},
                                                          {"text": "옛 인용", "quote": "고객 회의에서"}], "ai")
    assert len(added) == 1 and dropped[0]["why"] == "근거 인용이 원문에 글자 그대로 없음", (added, dropped)
    c2 = added[0]
    assert db.candidate_get(uid, c2)["record_version"] == 2
    db.candidate_decide(uid, c2, "confirm")
    # 과거 판 열기 — 하나의 판 조회 계약
    p1 = db.record_version_get(uid, rid, 1)
    assert (p1["is_current"], p1["latest_version"], p1["title"], p1["body"], p1["confirmed"], p1["eligible"]) == \
        (False, 2, "회의 메모", body1, False, True), p1
    assert p1["provenance"] == {"author_kind": "self", "text_process": "verbatim", "generation_kind": "unknown", "source": "대화에서 입력",
                                "source_id": None}, p1
    p2 = db.record_version_get(uid, rid, 2)
    assert (p2["is_current"], p2["body"], p2["confirmed"], p2["eligible"]) == (True, body2, True, True), p2
    expect("NOT-FOUND", db.record_version_get, uid, rid, 3)
    expect("ARG-INVALID", db.record_version_get, uid, rid, 0)
    # 내용 이력 복원 = 같은 저장(새 판 + 확인). 지금 판 이상 = ARG-INVALID
    expect("ARG-INVALID", rec_edit, uid, rid, "회의 메모", body1, None, 2)
    out = rec_edit(uid, rid, "회의 메모", body1, None, 1)
    assert out["saved"]["version"] == 3 and confirms(uid, "record", rid)[-1]["body_sha"] == sha(body1)
    assert one("SELECT meta FROM content_confirms WHERE user_id=%s AND target_id=%s ORDER BY id DESC LIMIT 1", (uid, rid))["meta"] == \
        {"restored_from_version": 1}
    # 승격 집계 = 후보가 가리키는 판 기준(판 1·판 2 후보 둘 다 적격)
    it = principle(uid, "범위 먼저", links=[{"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c1}, {"field_key": "evidence_claims", "target_kind": "candidate", "target_id": c2}])
    assert db.item_get(uid, it["id"])["promotion"]["confirmed_claims"] == 2
    # 남의 글 = NOT-FOUND
    other = user(OTHER, "다른이")
    expect("NOT-FOUND", db.record_edit, other, rid, "t", "x", 3, 2, rq())
    expect("NOT-FOUND", db.record_version_get, other, rid, 1)
    ok("내 글 저장(이전 판 보존·새 판·증거·출처 유지·충돌 latest·NO-CHANGE·길이)·후보 판 고정·과거 판 열기·내용 복원·승격 집계")
    return rid, c1, c2


# ── 2. 놓치기 쉬운 조건 1 — 과거 증거를 소급하지 않는다 · 확정 검사 = SQL 적격 같은 계약 ──────────

def test_no_backfill_forgery(uid):
    good = conversation_record(uid, "대화 정리 하나. 납기는 고객과 먼저 합의한다.")
    bad = conversation_record(uid, "대화 정리 둘. 회의록은 당일 보낸다.", matching=False)
    run_sql("DELETE FROM content_confirms WHERE user_id=%s AND target_id = ANY(%s)", (uid, [good, bad]))   # 옛 DB = 증거 표 없음
    db.init_schema()   # _migrate_edit — 판 1 증거 이관(확인 당시 해시가 지금 본문과 같을 때만 해시)
    cg, cb = confirms(uid, "record", good), confirms(uid, "record", bad)
    assert [(x["version_no"], x["kind"], x["body_sha"]) for x in cg] == [(1, "stack_confirm", sha("대화 정리 하나. 납기는 고객과 먼저 합의한다."))], cg
    assert [(x["version_no"], x["kind"], x["body_sha"]) for x in cb] == [(1, "stack_confirm", None)], "불일치 = 모름(NULL) — 새 해시로 덮지 않는다"
    db.init_schema()
    assert len(confirms(uid, "record", good)) == 1 and len(confirms(uid, "record", bad)) == 1, "이관 멱등"
    assert db.record_get(uid, good)["eligible"] and not db.record_get(uid, bad)["eligible"]
    # 확정 검사(_confirm_check)와 목록 review 가 같은 계약 — 대화 정리(structured)도 증거로 적격이면 확정된다(옛 review_reason 은 processed 로 거절)
    a, _ = db.candidates_propose(uid, good, "", [{"text": "납기 먼저", "quote": "납기는 고객과 먼저 합의한다"}], "ai")
    assert db.candidate_get(uid, a[0])["review"] is None
    assert db.candidate_decide(uid, a[0], "confirm")["status"] == "confirmed"
    _, d = db.candidates_propose(uid, bad, "", [{"text": "당일", "quote": "당일 보낸다"}], "ai")
    assert d and d[0]["why"] == db.INELIGIBLE_WHY, d
    # 그 기록을 사용자가 고쳐 저장하면 새 판만 적격 — 판 1 은 여전히 모름
    rec_edit(uid, bad, "대화 정리 둘", "대화 정리 둘. 회의록은 다음 날 아침에 보낸다.")
    assert db.record_get(uid, bad)["eligible"] and not db.record_version_get(uid, bad, 1)["eligible"]
    ok("조건 1 — 기존 확인 이벤트 이관: 해시 일치만 기록·불일치 NULL(소급 없음)·멱등 · 확정 검사 = SQL 적격(대화 정리 확정 가능)")


def test_item_backfill(uid):
    """확인 1번으로 생긴 항목 판(옛 데이터) — 확정 뒤 안 바뀐 항목만 판 증거(해시 = 지금 본문), 바뀐 항목은 0(소급 없음)·멱등."""
    a, b = principle(uid, "확정 기준 A", "A 본문"), principle(uid, "확정 기준 B", "B 본문")
    with db.tx() as c:
        for it in (a, b):
            ceid = uuid.uuid4()
            c.execute("INSERT INTO confirm_events (id,user_id,session_id,job_id,preview_id,preview_version,preview_sha,request_id,final_text_sha) "
                      "VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s)", (ceid, uid, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "0" * 64, rq(), "0" * 64))
            stack_service._new_version(c, uid, it["id"], ceid)
    g = db.item_get(uid, b["id"])
    p = db.item_propose(uid, "od_principles", 1, b["id"], g["revision"], None, "확정 기준 B", "확정 뒤 MCP 로 바뀜", {}, [])
    db.item_decide(uid, b["id"], p["base_revision"], p["proposal_id"], "apply")
    assert confirms(uid, "item", a["id"]) == [] and confirms(uid, "item", b["id"]) == []
    db.init_schema()
    ca = confirms(uid, "item", a["id"])
    assert [(x["version_no"], x["body_sha"], x["kind"]) for x in ca] == [(1, sha("A 본문"), "stack_confirm")], ca
    assert confirms(uid, "item", b["id"]) == [], "확정 뒤 바뀐 항목 = 백필 0"
    assert db.item_get(uid, a["id"])["confirmed_latest"] is True and db.item_get(uid, b["id"])["confirmed_latest"] is False
    db.init_schema()
    assert len(confirms(uid, "item", a["id"])) == 1, "멱등"
    ok("확인 1번 항목 판 백필 — 확정 뒤 안 바뀐 항목만 판 증거(confirmed_latest)·바뀐 항목 0·멱등")


# ── 3. 놓치기 쉬운 조건 2 — 판과 해시의 역할 · 적격 = 판+해시+최신 ─────────────────────────────

def test_version_hash_latest(uid):
    rid = db.record_add(uid, "판 해시", "같은 본문으로 돌아오는 글입니다. 첫 문장.")["id"]
    b1 = "같은 본문으로 돌아오는 글입니다. 첫 문장."
    rec_edit(uid, rid, "판 해시(제목만)", b1)   # 제목만 → 새 판(본문 해시 같음)에 새 증거
    cs = confirms(uid, "record", rid)
    assert [(x["version_no"], x["body_sha"]) for x in cs] == [(2, sha(b1))], cs
    a, _ = db.candidates_propose(uid, rid, "", [{"text": "첫 문장", "quote": "돌아오는 글입니다"}], "ai")
    assert db.candidate_get(uid, a[0])["record_version"] == 2
    b3 = "고친 본문입니다."
    rec_edit(uid, rid, "판 해시", b3)
    rec_edit(uid, rid, "판 해시", b1)   # 판 4 = 판 1 과 같은 본문 — 해시만으로는 구별 못 한다, 판 4 증거로 적격
    assert db.record_get(uid, rid)["content_version"] == 4 and db.record_get(uid, rid)["eligible"]
    # 최신 증거만 본다 — 증거 없이 현재 행이 바뀐 상태(판 +1)는 비적격(옛 판 증거가 있어도)
    run_sql("UPDATE records SET content_version=5 WHERE user_id=%s AND id=%s", (uid, rid))
    g = db.record_get(uid, rid)
    assert (g["eligible"], g["confirmed"]) == (False, False), g
    # 해시 불일치(본문이 증거 없이 바뀜)도 비적격 — 판 번호가 같아도
    run_sql("UPDATE records SET content_version=4, body=body || '!' WHERE user_id=%s AND id=%s", (uid, rid))
    g = db.record_get(uid, rid)
    assert (g["eligible"], g["confirmed"]) == (False, False), g
    # 판 4 를 가리키는 후보는 목록 review·확정 검사 모두 같은 판정(processed 재사용)
    run_sql("INSERT INTO candidates (id,user_id,record_id,text,quote,quote_start,quote_end,origin,record_version) "
            "VALUES (%s,%s,%s,'x','같은',0,2,'ai',4)", ("c_edhash", uid, rid))
    assert db.candidate_get(uid, "c_edhash")["review"] == "processed"
    e = expect("CANDIDATE-REVIEW", db.candidate_decide, uid, "c_edhash", "confirm")
    assert "원문 그대로가 아니라" in e.message
    # 판 2 후보는 판 2 증거(그 판 이하 최신)로 여전히 적격
    assert db.candidate_get(uid, a[0])["review"] is None
    db.candidate_decide(uid, a[0], "confirm")
    run_sql("UPDATE records SET body=%s WHERE user_id=%s AND id=%s", (b1, uid, rid))
    ok("조건 2 — 제목만 고쳐도 새 판·같은 본문 되돌림 = 새 판 증거 · 최신 증거만(판 어긋남·해시 어긋남 비적격) · 과거 판 후보 = 그 판 증거")


# ── 4. 놓치기 쉬운 조건 3 — 같은 값 저장 ≠ 첫 확인 · 한도 ─────────────────────────────────────

def test_item_first_confirm(uid):
    it = principle(uid, "기준 하나", "본문 하나")
    iid = it["id"]
    assert it["decision"] == "pending" and it["content_version"] is None and it["confirmed_latest"] is False, it
    rev0, d0 = it["revision"], decisions_today(uid)
    expect("NO-CHANGE", db.item_edit, uid, iid, "기준 하나", "본문 하나", rev0, 0, rq())
    assert db.item_get(uid, iid)["revision"] == rev0 and decisions_today(uid) == d0, "같은 값 = 요청 0(이력·revision·한도 불변)"
    out = item_edit(uid, iid, "기준 하나", "본문 하나 — 고침")
    assert out["saved"] == {"revision": rev0 + 1, "version": 2, "change_no": 1, "confirmed": True, "replay": False}, out
    g = db.item_get(uid, iid)
    assert (g["decision"], g["stage"], g["content_version"], g["confirmed_latest"], g["change_no"]) == ("confirmed", "confirmed", 2, True, 1), g
    assert decisions_today(uid) == d0 + 1, "미확정 기준 첫 확정 저장 = decisions 1"
    h = rows("SELECT action, prev_revision FROM item_history WHERE user_id=%s AND item_id=%s ORDER BY id", (uid, iid))
    assert h[-1]["action"] == "edit_confirm" and len(h) == rev0 + 1, h   # 이력 한 줄 = revision +1
    vs = rows("SELECT version_no, item_revision, fields, confirm_event_id FROM item_versions WHERE user_id=%s AND item_id=%s ORDER BY version_no",
              (uid, iid))
    assert [(v["version_no"], v["item_revision"], v["fields"]["body"], v["fields"]["decision"]) for v in vs] == \
        [(1, rev0, "본문 하나", "pending"), (2, rev0 + 1, "본문 하나 — 고침", "confirmed")], vs   # 이전 판 보존(증거 없음) + 새 판
    assert [(x["version_no"], x["body_sha"]) for x in confirms(uid, "item", iid)] == [(2, sha("본문 하나 — 고침"))]
    item_edit(uid, iid, "기준 하나(재수정)", "본문 하나 — 고침")
    assert decisions_today(uid) == d0 + 1, "확정한 기준 재수정 = 다시 안 셈"
    assert rows("SELECT action FROM item_history WHERE user_id=%s AND item_id=%s ORDER BY id DESC LIMIT 1", (uid, iid))[0]["action"] == "edit"
    # 과거 판 열기(항목)
    p1, p3 = db.item_version_get(uid, iid, 1), db.item_version_get(uid, iid, 3)
    assert (p1["is_current"], p1["confirmed"], p1["body"], p1["latest_version"]) == (False, False, "본문 하나", 3), p1
    assert (p3["is_current"], p3["confirmed"], p3["title"]) == (True, True, "기준 하나(재수정)"), p3
    expect("NOT-FOUND", db.item_version_get, uid, iid, 4)
    # 다른 경로(MCP 제안 적용)가 현재 행을 바꾸면 지금 판 없음·확인 표지 없음 → 다음 저장이 그 상태를 판으로 보존
    p = db.item_propose(uid, "od_principles", 1, iid, db.item_get(uid, iid)["revision"], None, "기준 하나(MCP)", "MCP 본문", {}, [])
    expect("PROPOSAL-PENDING", item_edit, uid, iid, "x", "y")
    db.item_decide(uid, iid, p["base_revision"], p["proposal_id"], "apply")
    g = db.item_get(uid, iid)
    assert (g["content_version"], g["confirmed_latest"], g["decision"]) == (None, False, "confirmed"), g
    item_edit(uid, iid, "기준 하나", "웹 본문")
    vs = rows("SELECT version_no, fields FROM item_versions WHERE user_id=%s AND item_id=%s ORDER BY version_no", (uid, iid))
    assert [(v["version_no"], v["fields"]["body"]) for v in vs][-2:] == [(4, "MCP 본문"), (5, "웹 본문")], vs
    # 비기준 항목 — decision 없음·한도 안 셈·확인한 최신 판 표지
    ax = axis(uid, "축 하나")
    d1 = decisions_today(uid)
    item_edit(uid, ax["id"], "축 하나(고침)", "")
    g = db.item_get(uid, ax["id"])
    assert (g["decision"], g["content_version"], g["confirmed_latest"]) == (None, 2, True) and decisions_today(uid) == d1, g
    st = {i["id"]: i for i in db.state(uid)["items"]}
    assert (st[ax["id"]]["confirmed_latest"], st[ax["id"]]["content_version"], st[ax["id"]]["change_no"]) == (True, 2, 1)
    # 보관 항목 = ARG-INVALID
    g = db.item_get(uid, ax["id"])
    db.item_decide(uid, ax["id"], g["revision"], None, "archive")
    expect("ARG-INVALID", item_edit, uid, ax["id"], "t", "b")
    # 일일 결정 한도 — 첫 확정 저장은 한도에 걸린다(재수정은 안 걸린다)
    it2 = principle(uid, "기준 둘")
    real = dict(db.DAILY_LIMITS)
    try:
        db.DAILY_LIMITS["decisions"] = decisions_today(uid)
        expect("RATE-LIMIT", item_edit, uid, it2["id"], "기준 둘", "본문")
        assert db.item_get(uid, it2["id"])["decision"] == "pending"
        item_edit(uid, iid, "기준 하나", "웹 본문 다시")   # 확정한 기준 재수정은 한도 밖
    finally:
        db.DAILY_LIMITS.clear()
        db.DAILY_LIMITS.update(real)
    ok("조건 3 — 같은 값 NO-CHANGE·첫 확정 저장 decisions 1·재수정 0·비기준 0 · 이전 판 보존·과거 판·PROPOSAL-PENDING·보관 거절·한도")
    return iid


# ── 5. 놓치기 쉬운 조건 4 — 재시도·원자성 ────────────────────────────────────────────────

def test_retry_atomic(uid):
    rid = db.record_add(uid, "재시도", "재시도 본문 첫 판")["id"]
    req = rq()
    a = db.record_edit(uid, rid, "재시도", "재시도 본문 둘째 판", 1, 0, req)
    b = db.record_edit(uid, rid, "재시도", "재시도 본문 둘째 판", 1, 0, req)   # 응답 유실 뒤 재전송
    assert a["saved"] == {**b["saved"], "replay": False} and b["saved"]["replay"] is True, (a, b)
    assert len(confirms(uid, "record", rid)) == 1 and one("SELECT count(*) AS n FROM record_versions WHERE record_id=%s", (rid,))["n"] == 1
    assert db.record_get(uid, rid)["content_version"] == 2, "판 중복 0"
    expect("EDIT-REQUEST-MISMATCH", db.record_edit, uid, rid, "재시도", "다른 본문", 1, 0, req)
    expect("CONFLICT", db.record_edit, uid, rid, "재시도", "다른 본문", 1, 0, rq())   # 같은 기대 판의 다른 요청 = 충돌
    # 항목 재시도 — 판·증거·이력·한도 중복 0
    it = principle(uid, "재시도 기준")
    g, d0, req = db.item_get(uid, it["id"]), decisions_today(uid), rq()
    x = db.item_edit(uid, it["id"], "재시도 기준", "본문", g["revision"], 0, req)
    y = db.item_edit(uid, it["id"], "재시도 기준", "본문", g["revision"], 0, req)
    assert y["saved"]["replay"] and x["saved"]["version"] == y["saved"]["version"] and decisions_today(uid) == d0 + 1
    assert len(confirms(uid, "item", it["id"])) == 1 and db.item_get(uid, it["id"])["revision"] == g["revision"] + 1
    # 원자성 — 저장 중간 실패면 판·이력·증거·확정·한도 전부 롤백, 같은 요청 재시도는 한 번만 반영
    it3 = principle(uid, "실패 주입")
    g, d0, req = db.item_get(uid, it3["id"]), decisions_today(uid), rq()
    real = stack_service.stage_refresh

    def boom(*a, **k):
        raise RuntimeError("주입")
    stack_service.stage_refresh = boom
    try:
        try:
            db.item_edit(uid, it3["id"], "실패 주입", "본문", g["revision"], 0, req)
            raise AssertionError("실패해야 해요")
        except RuntimeError:
            pass
    finally:
        stack_service.stage_refresh = real
    g2 = db.item_get(uid, it3["id"])
    assert (g2["revision"], g2["decision"], g2["change_no"], decisions_today(uid)) == (g["revision"], "pending", 0, d0)
    assert rows("SELECT 1 FROM item_versions WHERE item_id=%s", (it3["id"],)) == [] and confirms(uid, "item", it3["id"]) == []
    z = db.item_edit(uid, it3["id"], "실패 주입", "본문", g["revision"], 0, req)
    assert z["saved"]["replay"] is False and db.item_edit(uid, it3["id"], "실패 주입", "본문", g["revision"], 0, req)["saved"]["replay"]
    # 분당 저장 한도(별도 트랜잭션 — 실패해도 셈)
    real_lim = db.EDIT_PER_MINUTE
    try:
        db.EDIT_PER_MINUTE = 0
        expect("RATE-LIMIT", db.record_edit, uid, rid, "재시도", "한도", 2, 1, rq())
    finally:
        db.EDIT_PER_MINUTE = real_lim
    ok("조건 4 — 같은 요청 재전송 = 처음 결과(판·증거·이력·한도 중복 0)·다른 내용 MISMATCH·다른 요청 CONFLICT · 중간 실패 전부 롤백 · 분당 한도")


# ── 6. 놓치기 쉬운 조건 5 — 휴지통·변경 번호·영구 삭제·지연 요청 ─────────────────────────────

def test_trash_purge(uid, iid):
    rid = db.record_add(uid, "삭제 흐름", "삭제 흐름 본문")["id"]
    req = rq()
    db.record_edit(uid, rid, "삭제 흐름", "삭제 흐름 본문 2", 1, 0, req)
    db.trash_move(uid, "record", rid)
    assert one("SELECT change_no FROM records WHERE id=%s", (rid,))["change_no"] == 2, "이동 = 변경 번호 +1"
    expect("IN-TRASH", db.record_edit, uid, rid, "삭제 흐름", "삭제 흐름 본문 3", 2, 1, rq())
    expect("IN-TRASH", db.record_edit, uid, rid, "삭제 흐름", "삭제 흐름 본문 2", 1, 0, req)   # 재반환도 휴지통이면 막는다
    expect("NOT-FOUND", db.record_version_get, uid, rid, 1)
    nv, nc = one("SELECT count(*) AS n FROM record_versions WHERE record_id=%s", (rid,))["n"], len(confirms(uid, "record", rid))
    db.trash_restore(uid, "record", rid)
    assert one("SELECT change_no FROM records WHERE id=%s", (rid,))["change_no"] == 3, "복원 = 변경 번호 +1"
    assert (one("SELECT count(*) AS n FROM record_versions WHERE record_id=%s", (rid,))["n"], len(confirms(uid, "record", rid))) == (nv, nc), \
        "휴지통 복원 = 내용 확인 아님(새 판·증거 0)"
    e = expect("CONFLICT", db.record_edit, uid, rid, "삭제 흐름", "삭제 전 편집", 2, 1, rq())   # 삭제 전 화면의 오래된 편집
    assert e.latest["change_no"] == 3 and e.latest["body"] == "삭제 흐름 본문 2"
    assert db.record_edit(uid, rid, "삭제 흐름", "삭제 흐름 본문 2", 1, 0, req)["saved"]["replay"], "복원 뒤 같은 요청 = 처음 결과"
    # 항목도 같다
    g = db.item_get(uid, iid)
    db.trash_move(uid, "item", iid, g["revision"])
    iv, ic = one("SELECT count(*) AS n FROM item_versions WHERE item_id=%s", (iid,))["n"], len(confirms(uid, "item", iid))
    db.trash_restore(uid, "item", iid)
    assert (one("SELECT count(*) AS n FROM item_versions WHERE item_id=%s", (iid,))["n"], len(confirms(uid, "item", iid))) == (iv, ic), \
        "휴지통 복원 = 새 판·증거·확정 0"
    g2 = db.item_get(uid, iid)
    assert (g2["revision"], g2["change_no"]) == (g["revision"], g["change_no"] + 2), "revision 그대로·변경 번호 +2"
    expect("CONFLICT", db.item_edit, uid, iid, "x", "y", g["revision"], g["change_no"], rq())
    ireq = rq()
    item_edit(uid, iid, "영구 삭제될 기준", "영구 삭제될 본문", ireq)
    # 영구 삭제 — 원문·모든 판·이력·증거·재시도 결과까지. 늦게 온 재전송이 되살리지 않는다
    db.trash_move(uid, "record", rid)
    db.trash_move(uid, "item", iid, db.item_get(uid, iid)["revision"])
    db.TRASH_CLOCK = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=db.TRASH_HOURS + 48)
    try:
        run_sql("UPDATE records SET trashed_at=trashed_at - interval '2 days' WHERE id=%s", (rid,))
        run_sql("UPDATE items SET trashed_at=trashed_at - interval '2 days' WHERE id=%s", (iid,))
        db.trash_purge_due()
    finally:
        db.TRASH_CLOCK = None
    assert one("SELECT count(*) AS n FROM records WHERE id=%s", (rid,))["n"] == 0
    for t, col in (("record_versions", "record_id"), ("item_versions", "item_id"), ("item_history", "item_id")):
        assert one(f"SELECT count(*) AS n FROM {t} WHERE {col} = ANY(%s)", ([rid, iid],))["n"] == 0, t
    assert one("SELECT count(*) AS n FROM content_confirms WHERE target_id = ANY(%s)", ([rid, iid],))["n"] == 0
    expect("NOT-FOUND", db.record_edit, uid, rid, "삭제 흐름", "삭제 흐름 본문 2", 1, 0, req)
    expect("NOT-FOUND", db.item_edit, uid, iid, "영구 삭제될 기준", "영구 삭제될 본문", 0, 0, ireq)
    ok("조건 5 — 휴지통 이동·복원 = 변경 번호(옛 편집 충돌·latest)·휴지통 중 IN-TRASH(재반환 포함) · 영구 삭제 = 판·이력·증거 0·늦은 재전송 NOT-FOUND")


# ── 7. 웹 라우트 — 상태 코드·응답 꼴·latest ───────────────────────────────────────────────

async def test_web(uid):
    site = main.site
    real = site.dev, site.dev_user
    rid = db.record_add(uid, "웹 글", "웹 글 본문")["id"]
    iid = principle(uid, "웹 기준", "웹 기준 본문")["id"]
    H = {"origin": PUBLIC}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = False, ""
            assert (await h.post("/api/record/edit", json={}, headers=H)).status_code == 401
            assert (await h.get("/api/record/version", params={"id": rid, "v": 1})).status_code == 401
            site.dev, site.dev_user = True, MAIN
            j = (await h.get("/api/journal", params={"id": rid})).json()
            assert (j["content_version"], j["change_no"], j["eligible"], j["confirmed"]) == (1, 0, True, False), j
            body = {"record_id": rid, "title": "웹 글", "body": "웹 글 본문 고침", "expected_version": 1, "expected_change": 0,
                    "request_id": rq()}
            assert (await h.post("/api/record/edit", json=body)).status_code == 403, "Origin"
            assert (await h.post("/api/record/edit", json={**body, "expected_version": "1"}, headers=H)).status_code == 400
            assert (await h.post("/api/record/edit", json={**body, "expected_change": True}, headers=H)).status_code == 400
            r = await h.post("/api/record/edit", json=body, headers=H)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
            out = r.json()
            assert (out["body"], out["content_version"], out["change_no"], out["confirmed"], out["saved"]) == \
                ("웹 글 본문 고침", 2, 1, True, {"version": 2, "change_no": 1, "confirmed": True, "replay": False}), out
            assert set(out) == {"id", "title", "body", "meta", "content_version", "change_no", "confirmed", "eligible", "saved"}, set(out)
            r = await h.post("/api/record/edit", json=body, headers=H)
            assert r.status_code == 200 and r.json()["saved"]["replay"] is True
            r = await h.post("/api/record/edit", json={**body, "body": "충돌", "request_id": rq()}, headers=H)
            assert r.status_code == 409 and r.json()["code"] == "CONFLICT" and r.json()["latest"] == \
                {"title": "웹 글", "body": "웹 글 본문 고침", "content_version": 2, "change_no": 1}, r.json()
            r = await h.post("/api/record/edit", json={**body, "body": "다름"}, headers=H)
            assert (r.status_code, r.json()["code"]) == (409, "EDIT-REQUEST-MISMATCH")
            r = await h.post("/api/record/edit", json={**body, "expected_version": 2, "expected_change": 1, "request_id": rq(),
                                                       "body": "웹 글 본문 고침"}, headers=H)
            assert (r.status_code, r.json()["code"]) == (400, "NO-CHANGE")
            r = await h.post("/api/record/edit", json={**body, "record_id": "r_nope", "request_id": rq()}, headers=H)
            assert (r.status_code, r.json()["code"]) == (404, "NOT-FOUND")
            v = await h.get("/api/record/version", params={"id": rid, "v": 1})
            assert v.status_code == 200 and v.json()["body"] == "웹 글 본문" and v.json()["is_current"] is False
            assert (await h.get("/api/record/version", params={"id": rid, "v": 9})).status_code == 404
            assert (await h.get("/api/record/version", params={"id": rid, "v": "x"})).status_code == 400
            # 항목
            g = (await h.get("/api/item", params={"id": iid})).json()
            assert (g["change_no"], g["content_version"], g["confirmed_latest"]) == (0, None, False), g
            ib = {"item_id": iid, "title": "웹 기준", "body": "웹 기준 본문 고침", "expected_revision": g["revision"], "expected_change": 0,
                  "request_id": rq()}
            r = await h.post("/api/item/edit", json=ib, headers=H)
            assert r.status_code == 200, r.text
            o = r.json()
            assert (o["decision"], o["stage"], o["content_version"], o["confirmed_latest"], o["saved"]["version"]) == \
                ("confirmed", "confirmed", 2, True, 2), o
            r = await h.post("/api/item/edit", json={**ib, "request_id": rq()}, headers=H)
            lt = r.json()
            assert r.status_code == 409 and lt["latest"] == {"title": "웹 기준", "body": "웹 기준 본문 고침", "revision": g["revision"] + 1,
                                                             "change_no": 1, "content_version": 2}, lt
            db.item_propose(uid, "od_principles", 1, iid, g["revision"] + 1, None, "제안", "제안 본문", {}, [])
            r = await h.post("/api/item/edit", json={**ib, "expected_revision": g["revision"] + 1, "expected_change": 1, "request_id": rq(),
                                                     "body": "또"}, headers=H)
            assert (r.status_code, r.json()["code"]) == (409, "PROPOSAL-PENDING"), r.json()
            v = await h.get("/api/item/version", params={"id": iid, "v": 1})
            assert v.status_code == 200 and v.json()["body"] == "웹 기준 본문" and v.json()["is_current"] is False
            db.trash_move(uid, "record", rid)
            r = await h.post("/api/record/edit", json={**body, "expected_version": 2, "expected_change": 2, "request_id": rq(), "body": "휴지통"},
                             headers=H)
            assert (r.status_code, r.json()["code"]) == (409, "IN-TRASH")
            real_lim = db.EDIT_PER_MINUTE
            try:
                db.EDIT_PER_MINUTE = 0
                r = await h.post("/api/item/edit", json={**ib, "request_id": rq()}, headers=H)
                assert (r.status_code, r.json()["code"]) == (429, "RATE-LIMIT")
            finally:
                db.EDIT_PER_MINUTE = real_lim
            st = (await h.get("/api/state")).json()
            assert all("record_version" in c for c in st["candidates"]) and all("content_version" in x for x in st["journal"])
            assert all({"change_no", "content_version", "confirmed_latest"} <= set(i) for i in st["items"])
    finally:
        site.dev, site.dev_user = real
    ok("웹 라우트 — 401·403 Origin·400 모양·200 상세+saved·재시도 replay·409 CONFLICT latest·MISMATCH·PROPOSAL-PENDING·IN-TRASH·400 NO-CHANGE·404·429·판 조회")


# ── 8. 일반 조회 쿼리 수 불변 · EXPLAIN ─────────────────────────────────────────────────

def test_query_count():
    n = {"n": 0}
    orig = psycopg.Connection.execute

    def ex(self, *a, **k):
        n["n"] += 1
        return orig(self, *a, **k)

    def cnt(fn, *a):
        n["n"] = 0
        fn(*a)
        return n["n"]
    got = {}
    psycopg.Connection.execute = ex
    try:
        for em, k in ((QC1, 1), (QC3, 3)):
            uid = user(em, "qc")
            rids = []
            for i in range(k):
                rid = db.record_add(uid, f"제목{i}", f"본문 하나 {i} 둘 셋 넷 다섯 {i}")["id"]
                rids.append(rid)
                db.candidates_propose(uid, rid, "", [{"text": "판단", "quote": f"하나 {i} 둘"}], "ai")
                rec_edit(uid, rid, f"제목{i}", f"본문 하나 {i} 둘 셋 넷 다섯 {i} 고침")   # 수정한 글 — 후보는 판 1
                db.candidates_propose(uid, rid, "", [{"text": "판단2", "quote": f"다섯 {i} 고침"}], "ai")
            it = principle(uid, "기준1", "본문")
            item_edit(uid, it["id"], "기준1", "본문 고침")
            got[k] = {"state": cnt(db.state, uid), "candidates": cnt(db.candidates, uid), "items": cnt(db.items, uid),
                      "item_get": cnt(db.item_get, uid, it["id"]), "record_get": cnt(db.record_get, uid, rids[0]),
                      "records": cnt(db.records, uid)}
    finally:
        psycopg.Connection.execute = orig
    assert got[1] == got[3] == BASELINE, got
    with db.tx() as c:   # 후보 적격 서브쿼리가 증거 인덱스를 쓸 수 있다(순차 탐색을 끈 계획에 ix_content_confirms_target)
        c.execute("SET LOCAL enable_seqscan = off")
        uid = db.user_by_email(QC3)["id"]
        plan = "\n".join(r["QUERY PLAN"] for r in c.execute(
            f"EXPLAIN SELECT c.id, ({db._elig_sql('c.record_version')}) AS e FROM candidates c JOIN records r ON r.user_id=c.user_id "
            "AND r.id=c.record_id WHERE c.user_id=%s", (uid,)).fetchall())
    assert "ix_content_confirms_target" in plan, plan
    ok(f"일반 조회 쿼리 수 = 수정 전과 같음(글 1편·3편 수정 후 {BASELINE}) · 증거 인덱스 사용 계획")


# ── 9. 「적용 영역」 1회 이관 · MCP 계약 · 계정 삭제 ───────────────────────────────────────

def test_register_label_and_misc(uid):
    other = db.user_by_email(OTHER)["id"]
    p = db.definition_propose(uid, None, 0, {"key": "my_rules", "label": "규칙", "semantic_kind": "principle", "fields": [
        {"key": "register", "label": "레지스터", "value_type": "select", "required": True,
         "options": [{"value": "customer", "label": "고객 업무"}, {"value": "work", "label": "일하는 방식"}]}]})
    oid = db.definition_apply(uid, p["object_id"], p["proposal_id"], 0)["id"]
    q = db.definition_propose(other, None, 0, {"key": "rules2", "label": "규칙", "semantic_kind": "principle", "fields": [
        {"key": "register", "label": "레지스터", "value_type": "text"}]})
    oid2 = db.definition_apply(other, q["object_id"], q["proposal_id"], 0)["id"]
    run_sql("UPDATE object_defs SET template_slot='author@1:principles' WHERE user_id=%s AND id=%s", (uid, oid))
    run_sql("UPDATE field_defs SET template_slot=%s WHERE user_id=%s AND object_id=%s AND key='register'", (db.REGISTER_SLOT, uid, oid))
    rev = {d["id"]: d for d in db.definitions(uid)}[oid]["revision"]
    d = db.definition_propose(uid, oid, rev, {"key": "my_rules", "label": "규칙(제안)", "semantic_kind": "principle", "fields": [
        {"key": "register", "label": "레지스터", "value_type": "select", "required": True,
         "options": [{"value": "customer", "label": "고객 업무"}, {"value": "work", "label": "일하는 방식"}]}]})
    run_sql("DELETE FROM schema_marks WHERE key=%s", (db.REGISTER_LABEL_MARK,))
    db.init_schema()
    f = one("SELECT label, options FROM field_defs WHERE user_id=%s AND object_id=%s AND key='register'", (uid, oid))
    assert f["label"] == "적용 영역" and [o["value"] for o in f["options"]] == ["customer", "work"], f
    row = one("SELECT revision, draft_doc FROM object_defs WHERE user_id=%s AND id=%s", (uid, oid))
    assert row["revision"] == rev and row["draft_doc"]["fields"][0]["label"] == "적용 영역", "revision 그대로·제안 문서 라벨도"
    assert one("SELECT label FROM field_defs WHERE user_id=%s AND object_id=%s AND key='register'", (other, oid2))["label"] == "레지스터", \
        "템플릿 계보 밖 정의는 그대로"
    db.definition_apply(uid, oid, d["proposal_id"], rev)
    assert one("SELECT label FROM field_defs WHERE user_id=%s AND object_id=%s AND key='register'", (uid, oid))["label"] == "적용 영역"
    run_sql("UPDATE field_defs SET label='레지스터' WHERE user_id=%s AND object_id=%s AND key='register'", (uid, oid))
    db.init_schema()   # 표식이 있으면 다시 하지 않는다
    assert one("SELECT label FROM field_defs WHERE user_id=%s AND object_id=%s AND key='register'", (uid, oid))["label"] == "레지스터"
    # MCP — 직접 수정 도구 없음 · INSTRUCTIONS 한 줄
    names = {t.name for t in asyncio.run(main.mcp.list_tools())}
    assert not any("edit" in t for t in names) and "record_edit" not in db.WRITE_LIMITS and "item_edit" not in db.WRITE_LIMITS, names
    assert "받은 원문은 최초 판으로 보존하고, 사용자가 저장한 수정본은 최신 확정 판으로 보관한다" in main.INSTRUCTIONS
    # 계정 삭제 — 새 표도 DELETE_TABLES 로
    assert {"record_versions", "content_confirms"} <= set(db.DELETE_TABLES)
    tmp = user("ed-tmp@example.com")
    r = db.record_add(tmp, "t", "본문")["id"]
    rec_edit(tmp, r, "t", "본문 고침")
    db.user_delete(tmp)
    assert one("SELECT count(*) AS n FROM record_versions WHERE user_id=%s", (tmp,))["n"] == 0
    assert one("SELECT count(*) AS n FROM content_confirms WHERE user_id=%s", (tmp,))["n"] == 0
    ok("「적용 영역」 1회 이관(템플릿 계보만·값·revision 그대로·제안 문서 라벨·표식 멱등) · MCP 수정 도구 없음·INSTRUCTIONS · 계정 삭제 새 표 0")


def main_():
    assert db.EDIT_PER_MINUTE == 30, "저장 분당 한도 = 30(원작자 승인)"
    db.EDIT_PER_MINUTE = 100000   # 분당 한도는 시험마다 0 으로 따로 본다(한 프로세스 안에서만)
    db.init_schema()
    cleanup()
    try:
        uid = user(MAIN)
        test_record_edit(uid)
        test_no_backfill_forgery(uid)
        test_item_backfill(uid)
        test_version_hash_latest(uid)
        iid = test_item_first_confirm(uid)
        test_retry_atomic(uid)
        test_trash_purge(uid, iid)
        asyncio.run(test_web(uid))
        test_query_count()
        test_register_label_and_misc(uid)
        print("OK — 글 수정 흐름 시험 전부 통과")
    finally:
        db.TRASH_CLOCK = None
        cleanup()
        run_sql("DELETE FROM users WHERE email='ed-tmp@example.com'")
        db.close()


if __name__ == "__main__":
    main_()
