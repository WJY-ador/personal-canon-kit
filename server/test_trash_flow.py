"""휴지통 PR-1 흐름 시험 — 활동 원천 휴지통·수신 차단·사람 수신 표식·축 정책·쌓기 후보·운영자 집계·물리 정리 (2026-10-23).

실행: DATABASE_URL=postgresql://localhost/crm_test_trash_flow python3 server/test_trash_flow.py
가드: server/.env 를 읽지 않는다(운영 DB 폴백 사고 방지) — DATABASE_URL 을 명시해야 하고, 호스트는 로컬, DB 이름은 crm_test 로 시작해야 한다.
정본: notes 계획 §4 · astra r4 §3(활동)·§7(사람·후보·축)·§8(정리)·§12 PR-1 행. 데이터 상태(행·열)까지 본다.
"""
import hashlib
import json
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
URL = os.environ.get("DATABASE_URL", "")
if not URL:
    raise SystemExit("중단: DATABASE_URL 을 명시하세요(예: postgresql://localhost/crm_test_trash_flow). server/.env 는 읽지 않아요.")
_u = urllib.parse.urlsplit(URL)
if _u.hostname not in ("localhost", "127.0.0.1", "::1", None) or not _u.path.lstrip("/").startswith("crm_test"):
    raise SystemExit("중단: 로컬 시험 DB(이름 crm_test*)만 허용해요.")
os.environ["CRM_DEV"] = "1"
os.environ["CRM_DEV_USER"] = "tf-main@example.com"
os.environ["AUTHKIT_ISSUER"] = "https://fake.authkit.test"
os.environ["CRM_PUBLIC_URL"] = "http://127.0.0.1:8800"
os.environ.pop("MYCRM_JOBS_ALLOW_REAL_CLI", None)
sys.path.insert(0, str(HERE))

import activity  # noqa: E402
import admin_db  # noqa: E402
import db  # noqa: E402
import imports  # noqa: E402
import stack_service  # noqa: E402

MAIN, OTHER = "tf-main@example.com", "tf-other@example.com"
EMAILS = (MAIN, OTHER)
SWITCHES = ("CRM_IMPORTS_ENABLED", "CRM_ACTIVITY_IMPORTS_ENABLED")


def cleanup():
    """test_tools.cleanup 과 같은 길 — 감사 행 · db.user_delete(계정 삭제 순서 DELETE_TABLES)."""
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(EMAILS),)).fetchall()]
        c.execute("DELETE FROM admin_audit WHERE actor_id = ANY(%s) OR target_id = ANY(%s)", (ids, ids))
    for uid in ids:
        db.user_delete(uid)


def _o():
    return "o_" + secrets.token_hex(16)


def _v():
    return "v_" + secrets.token_hex(16)


def act_req(dev, original=None, revision=None, prev=None, policy_revision=1, **kw):
    r = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": dev, "original_id": original or _o(),
         "revision": revision or _v(), "previous_revision": prev, "kind": "activity", "execution": "unattended",
         "transform_version": "board-likes/1", "policy_revision": policy_revision, "action": "like", "state": "active",
         "occurred_on": None, "observed_on": None, "provider": "sns", "raw_id": secrets.token_hex(24)}
    r.update(kw)
    return r


def text_req(dev, original=None, revision=None, prev=None, policy_revision=1, **kw):
    r = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": dev, "original_id": original or _o(),
         "revision": revision or _v(), "previous_revision": prev, "kind": "self_text", "execution": "attended",
         "transform_version": "self-text/1", "policy_revision": policy_revision, "title": "합성 제목", "body": "합성 자기 글 본문"}
    r.update(kw)
    return r


def send(token, body):
    """imports.submit 직접 호출 — (HTTP 상태, 본문). CrmError 는 (http_status, {"code"}) 로 바꾼다."""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    key = imports.idempotency_key(body["source_instance_id"], body["original_id"], body["revision"])
    try:
        return imports.submit("Bearer " + token, key, raw)
    except db.CrmError as e:
        return imports.http_status(e.code), {"code": e.code}


def hmac_hex():
    return secrets.token_hex(32)


def count(uid, table, where="", args=()):
    with db.tx() as c:
        return c.execute(f"SELECT count(*) AS n FROM {table} WHERE user_id=%s {where}", (uid,) + tuple(args)).fetchone()["n"]


def rows(sql, args=()):
    with db.tx() as c:
        return c.execute(sql, args).fetchall()


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


def setup_user(email, name):
    uid = db.user_by_email(email)["id"]
    db.display_name_set(uid, name)
    ax_obj = db.classify_target(uid)
    assert ax_obj, "신규 계정 표준 시드에 분류(축) 오브젝트가 있다"
    ax = {}
    with db.tx() as c:
        for k in ("X1", "X2", "X3"):
            ax[k] = db.new_id("i")
            c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,title,revision) VALUES (%s,%s,%s,1,%s,1)",
                      (uid, ax[k], ax_obj, f"축 {k}"))
    d = imports.device_connect(uid, "흐름 시험 장치")
    dev = d["device"]
    imports.device_policy_set(uid, dev["id"], {"actions": ["like"], "transform_versions": list(imports.TRANSFORMS),
                                               "axes": [ax["X1"], ax["X2"]]}, dev["policy_revision"])
    pr = imports.device_list(uid)["devices"][0]["policy_revision"]
    return uid, dev["id"], d["token"], ax, pr


def act_ids(uid, stream, original):
    return [r["target_activity_id"] for r in rows(
        "SELECT target_activity_id FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s "
        "AND target_activity_id IS NOT NULL ORDER BY received_at, id", (uid, stream, original))]


def latest_rev(uid, dev, o):
    return rows("SELECT source_revision FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s AND is_latest_received",
                (uid, dev, o))[0]["source_revision"]


def counter(uid):
    return rows("SELECT COALESCE(sum(n),0) AS n FROM import_counters WHERE user_id=%s AND bucket='activity'", (uid,))[0]["n"]


def shift_trash(uid, table, ids, days):
    """합성 — 휴지통 이동 시각을 days 일 앞으로(기한·「오늘 옮긴 것」 규칙만 바꾼다)."""
    run_sql(f"UPDATE {table} SET trashed_at = trashed_at - make_interval(days => %s), last_trashed_at = last_trashed_at - "
            f"make_interval(days => %s), purge_at = purge_at - make_interval(days => %s) WHERE user_id=%s AND id = ANY(%s)",
            (days, days, days, uid, list(ids)))


# ── 1. 활동 원천: 여러 판 · 읽기 경로 · 같은 해시/다른 해시/새 revision/옛 판 조회 · 복원 뒤 판 체인 ─────────────
def test_activity_source(ctx):
    uid, dev, tok, ax, pr = ctx
    h1 = hmac_hex()
    ppl = [{"hmac": h1, "role": "author"}]
    o1, v1, v2 = _o(), _v(), _v()
    st, b = send(tok, act_req(dev, o1, v1, policy_revision=pr, people=ppl, axes=[ax["X1"]]))
    assert st == 201 and b["state"] == "applied", (st, b)
    old_id = b["target_id"]
    st, b = send(tok, act_req(dev, o1, v2, prev=v1, policy_revision=pr, people=ppl, axes=[ax["X1"]]))
    assert st == 201 and b["state"] == "applied" and b["target_id"] != old_id, (st, b)
    cur_id = b["target_id"]
    st, b = send(tok, act_req(dev, _o(), policy_revision=pr, people=ppl, axes=[ax["X1"]]))
    other_id = b["target_id"]
    pid = rows("SELECT id FROM people WHERE user_id=%s AND identity_hmac=%s", (uid, bytes.fromhex(h1)))[0]["id"]
    rid = db.record_add(uid, "메모", "활동에 단 메모")["id"]   # 기록 출발 연결(attach_to → 옛 판) — item_links.target_activity_id 참조
    run_sql("INSERT INTO item_links (user_id,source_record_id,field_key,target_activity_id) VALUES (%s,%s,'attach_to',%s)", (uid, rid, old_id))
    assert {a["id"] for a in activity.activity_list(uid)["activities"]} == {cur_id, other_id}
    assert activity.activity_get(uid, old_id)["current_id"] == cur_id, "옛 판도 열린다(이동 전)"
    undated = activity.me_aggregate(uid, "all")["undated"]["n"]
    assert activity.person_context(uid, pid)["old_revision_links"] == 1, "옛 판 v1 의 사람 연결(이동 전)"
    # 옛 판 ID 로 옮겨도 원천 전체(모든 판 같은 사건)
    m = db.trash_move(uid, "activity", old_id)
    assert m["changed"] and m["restorable"], m
    tr = rows("SELECT trashed_at, purge_at FROM activities WHERE user_id=%s AND id = ANY(%s)", (uid, [old_id, cur_id]))
    assert len(tr) == 2 and len({(r["trashed_at"], r["purge_at"]) for r in tr}) == 1 and tr[0]["trashed_at"] is not None, tr
    m2 = db.trash_move(uid, "activity", cur_id)
    assert not m2["changed"] and m2["purge_at"] == m["purge_at"], ("중복 = 기존 사건·연장 0", m, m2)
    # 일반 이용 차단 — 목록·상세(현재·옛 판 직접 ID)·집계·사람/출처/축 문맥
    assert {a["id"] for a in activity.activity_list(uid)["activities"]} == {other_id}
    for x in (old_id, cur_id):
        expect("NOT-FOUND", activity.activity_get, uid, x)
    assert activity.me_aggregate(uid, "all")["undated"]["n"] == undated - 1
    pc = activity.person_context(uid, pid)
    assert pc["old_revision_links"] == 0, ("휴지통 원천의 옛 판 연결은 세지 않는다", pc["old_revision_links"])
    assert pc["activity_count"] == 1 and [a["id"] for a in pc["activities"]] == [other_id], pc
    sc = activity.source_context(uid, pc["activities"][0]["source"]["id"])
    assert cur_id not in {a["id"] for a in sc["activities"]}
    assert activity.axis_context(uid, ax["X1"])["activity_count"] == 1
    # 수신 차단 — 같은 개정 재전송 · 같은 해시 새 개정 · 다른 해시 새 개정 · 체인 어긋남: 200 deleted · 대상 없음 · 활동/관계/한도 0
    n_act, n_link, cnt = count(uid, "activities"), count(uid, "activity_links"), counter(uid)
    same = act_req(dev, o1, v2, prev=v1, policy_revision=pr, people=ppl, axes=[ax["X1"]])
    st, b = send(tok, same)
    assert st == 200 and b["state"] == "deleted" and b["target_id"] is None and b["target_kind"] is None and b["duplicate"] is True, b
    assert b["source_deleted"] == "trash", b
    v3 = _v()
    st, b3 = send(tok, dict(same, revision=v3, previous_revision=v2))   # raw_id·사람·축 그대로 = 같은 projection 해시
    assert st == 200 and b3["state"] == "deleted" and b3["target_id"] is None and b3["import_id"] and not b3["duplicate"], b3
    v4 = _v()
    st, b4 = send(tok, act_req(dev, o1, v4, prev=v3, policy_revision=pr, state="retracted"))
    assert st == 200 and b4["state"] == "deleted" and b4["target_id"] is None, b4
    st, bx = send(tok, act_req(dev, o1, _v(), prev=v1, policy_revision=pr))
    assert st == 200 and bx["state"] == "deleted" and bx["import_id"] is None, ("체인 어긋남 = 행 없이 응답만", bx)
    assert count(uid, "activities") == n_act and count(uid, "activity_links") == n_link and counter(uid) == cnt, "활동·관계·한도 0"
    st, bm = send(tok, act_req(dev, policy_revision=pr, people=ppl))   # 혼합 전송 — 삭제 응답 뒤 다른 원천은 그대로 받는다
    assert st == 201 and bm["state"] == "applied" and bm["target_id"], bm
    n_act, n_link, other_ids = n_act + 1, n_link + 1, {other_id, bm["target_id"]}
    ent = rows("SELECT state, target_activity_id, is_current_applied, is_latest_received FROM import_entries WHERE user_id=%s AND id=%s",
               (uid, b4["import_id"]))[0]
    assert ent["state"] == "deleted" and ent["target_activity_id"] is None and not ent["is_current_applied"] and ent["is_latest_received"], ent
    assert [r["target_activity_id"] for r in rows("SELECT target_activity_id FROM import_entries WHERE user_id=%s AND stream_id=%s "
                                                  "AND original_id=%s AND is_current_applied", (uid, dev, o1))] == [cur_id], "현재 판 그대로"
    s = imports.entry_status("Bearer " + tok, b4["import_id"])
    assert s["state"] == "deleted" and s["target_id"] is None and s["source_deleted"] == "trash", s
    first = rows("SELECT id FROM import_entries WHERE user_id=%s AND stream_id=%s AND original_id=%s AND source_revision=%s",
                 (uid, dev, o1, v1))[0]["id"]
    s = imports.entry_status("Bearer " + tok, first)
    assert s["state"] == "deleted" and s["target_id"] is None, ("적용됐던 판의 상태 조회도 대상 없음", s)
    # 복원 = 같은 ID·판. 차단 중 받은 판은 활동이 없다. 다음 개정(previous = 차단된 판 v4)은 판 체인으로 이어진다
    assert db.trash_restore(uid, "activity", cur_id)["restored"]
    assert {a["id"] for a in activity.activity_list(uid)["activities"]} == {cur_id} | other_ids
    assert activity.activity_get(uid, old_id)["current_id"] == cur_id
    assert act_ids(uid, dev, o1) == [old_id, cur_id]
    st, b5 = send(tok, act_req(dev, o1, _v(), prev=v4, policy_revision=pr, people=ppl))
    assert st == 201 and b5["state"] == "applied", ("복원 뒤 다음 개정 정상", st, b5)
    expect("NOT-IN-TRASH", db.trash_restore, uid, "activity", cur_id)
    # 수동 영구 삭제 = 즉시 복원 불가(물리 제거는 정리 회차) · 정리 전 재전송은 아직 trash
    db.trash_move(uid, "activity", cur_id)
    p = db.trash_purge(uid, "activity", old_id)
    assert p["purge_requested"] and p["changed"], p
    expect("TRASH-EXPIRED", db.trash_restore, uid, "activity", cur_id)
    assert db.trash_purge(uid, "activity", cur_id)["changed"] is False
    st, b = send(tok, act_req(dev, o1, _v(), prev=latest_rev(uid, dev, o1), policy_revision=pr))
    assert st == 200 and b["state"] == "deleted" and b["source_deleted"] == "trash", b
    print("OK — 활동 원천(옛 판 ID 로 원천 전체·같은 사건·중복 무연장 · 목록/상세(옛 판 직접 ID)/집계/사람·출처·축 문맥 차단 · "
          "같은 개정·같은 해시·다른 해시·체인 어긋남 = 200 deleted·대상 없음·활동/관계/한도 0 · 장치 상태 조회 대상 없음 · "
          "복원 같은 ID·차단 판 미복원·다음 개정 체인 · 수동 영구 삭제 즉시 복원 불가)")
    return {"o1": o1, "ids": act_ids(uid, dev, o1), "rid": rid, "pid": pid}


# ── 2. 물리 정리: 참조 표·CHECK·표식 · 오늘 옮긴 것 미룸 · 실패 건너뛰기 · 정리 뒤 재전송 ─────────────
def test_purge(ctx, st1):
    uid, dev, tok, ax, pr = ctx
    fks = {r["t"] for r in rows("SELECT conrelid::regclass::text AS t FROM pg_constraint WHERE contype='f' AND confrelid='activities'::regclass")}
    assert fks == {"activity_links", "item_links", "import_entries"}, ("활동을 가리키는 참조 표가 늘면 정리 순서를 고친다", fks)
    o1, ids = st1["o1"], st1["ids"]
    assert len(ids) == 3
    out = activity.trash_purge_due()
    assert out["activities"] == 0 and count(uid, "activities", "AND id = ANY(%s)", (ids,)) == 3, ("오늘 옮긴 것은 다음 날", out)
    shift_trash(uid, "activities", ids, 2)
    o9 = _o()
    st, b9 = send(tok, act_req(dev, o9, policy_revision=pr))
    db.trash_move(uid, "activity", b9["target_id"])
    shift_trash(uid, "activities", [b9["target_id"]], 9)
    real = db._purge_fault

    def boom(t, k):
        if k == o1:
            raise RuntimeError("주입")
    db._purge_fault = boom
    try:
        out = activity.trash_purge_due()
    finally:
        db._purge_fault = real
    assert out["activities"] == 1 and out["failed"] == 1, out
    assert count(uid, "activities", "AND id = ANY(%s)", (ids,)) == 3, "실패한 원천은 한 트랜잭션 — 그대로"
    assert count(uid, "activities", "AND id=%s", (b9["target_id"],)) == 0, "다른 원천은 같은 회차에 진행"
    out = activity.trash_purge_due()
    assert out == {"activities": 1, "failed": 0, "left": False}, out
    assert count(uid, "activities", "AND id = ANY(%s)", (ids,)) == 0
    assert count(uid, "activity_links", "AND activity_id = ANY(%s)", (ids,)) == 0
    assert count(uid, "item_links", "AND target_activity_id = ANY(%s)", (ids,)) == 0
    assert count(uid, "records", "AND id=%s", (st1["rid"],)) == 1, "연결된 기록(상대)은 남는다"
    assert count(uid, "people", "AND id=%s AND alias IS NOT NULL", (st1["pid"],)) == 1, "사람(상대)은 남는다"
    es = rows("SELECT state, target_activity_id, is_current_applied, payload FROM import_entries WHERE user_id=%s AND stream_id=%s "
              "AND original_id=%s", (uid, dev, o1))
    assert es and all(e["state"] == "deleted" and e["target_activity_id"] is None and not e["is_current_applied"] and e["payload"] is None
                      for e in es), es
    n = count(uid, "activities")
    st, b = send(tok, act_req(dev, o1, _v(), prev=latest_rev(uid, dev, o1), policy_revision=pr))
    assert st == 200 and b["state"] == "deleted" and b["source_deleted"] == "purged" and b["target_id"] is None, b
    st, b = send(tok, act_req(dev, o9, _v(), prev=latest_rev(uid, dev, o9), policy_revision=pr))
    assert st == 200 and b["source_deleted"] == "purged", b
    assert count(uid, "activities") == n, "정리 뒤 재전송 = 새 활동 0"
    expect("NOT-FOUND", activity.activity_get, uid, ids[0])
    expect("NOT-FOUND", db.trash_restore, uid, "activity", ids[1])
    # 영구 삭제한 활동을 가리키는 늦은 내 글 = ref 만 빼고 정상 수신(PC 격리 0) · 남의·없는 ID 는 그대로 REF-NOT-FOUND
    st, bt = send(tok, text_req(dev, policy_revision=pr, activity_ref=ids[0]))
    assert st == 201 and bt["state"] == "pending", (st, bt)
    assert imports.inbox_get(uid, bt["import_id"])["activity_ref"] is None, "지운 활동 ref 는 빠진다"
    st, bt = send(tok, text_req(dev, policy_revision=pr, activity_ref="a_nothere0001"))
    assert st == 400 and bt["code"] == "REF-NOT-FOUND", (st, bt)
    print("OK — 물리 정리(참조 표 3종 · 오늘 옮긴 것 미룸 · 실패 원천 건너뛰고 다른 원천 진행 · 다음 회차 재시도 · 판 전부·관계·기록 출발 연결 제거 · "
          "대응표 state=deleted·target NULL·현재 판 해제(CHECK 통과) · 상대 기록·사람 유지 · 정리 뒤 재전송 purged·새 활동 0)")


# ── 3. 사람 수신: 휴지통·표식 사람 HMAC — 재생성 0 · UNIQUE 위반 0 · 활동은 저장(연결만 뺌) · 경합 ─────────────
def test_people(ctx):
    import threading
    uid, dev, tok, ax, pr = ctx
    h = hmac_hex()
    send(tok, act_req(dev, policy_revision=pr, people=[{"hmac": h, "role": "author"}]))
    pid = rows("SELECT id FROM people WHERE user_id=%s AND identity_hmac=%s", (uid, bytes.fromhex(h)))[0]["id"]
    db.trash_move(uid, "person", pid, db.person_get(uid, pid)["revision"])
    np = count(uid, "people")
    st, b = send(tok, act_req(dev, policy_revision=pr, people=[{"hmac": h, "role": "author"}, {"hmac": hmac_hex(), "role": "counterparty"}]))
    assert st == 201 and b["state"] == "applied", (st, b)
    assert count(uid, "people") == np + 1, "휴지통 사람은 새로 만들지 않는다(다른 HMAC 하나만 새로)"
    assert count(uid, "activity_links", "AND activity_id=%s AND person_id=%s", (b["target_id"], pid)) == 0, "휴지통 사람과 새 관계 0"
    assert [p["role"] for p in activity.activity_get(uid, b["target_id"])["people"]] == ["counterparty"]
    expect("NOT-FOUND", activity.person_context, uid, pid)
    assert activity.activity_list(uid, person_id=pid)["activities"] == [], "휴지통 사람으로 거르면 빈 목록"
    # 정리 → 표식(별칭 없음·HMAC 유지) → 같은 HMAC 재전송도 재생성 0
    shift_trash(uid, "people", [pid], 9)
    out = db.trash_purge_due()
    assert out["people"] >= 1, out
    mk = rows("SELECT alias, purged_at, identity_hmac IS NOT NULL AS h FROM people WHERE user_id=%s AND id=%s", (uid, pid))[0]
    assert mk["alias"] is None and mk["purged_at"] is not None and mk["h"], mk
    np = count(uid, "people")
    st, b = send(tok, act_req(dev, policy_revision=pr, people=[{"hmac": h, "role": "author"}]))
    assert st == 201 and count(uid, "people") == np and activity.activity_get(uid, b["target_id"])["people"] == [], "표식 = 재생성 0"
    assert pid not in {p["person_id"] for p in activity.me_aggregate(uid, "all")["top_people"]}
    assert pid not in {p["id"] for p in stack_cands(uid)["people"]}
    # 경합 — 사람 이동과 같은 HMAC 수신을 동시에(둘 다 사용자 잠금): 오류 0 · 같은 HMAC 사람 하나
    h2 = hmac_hex()
    send(tok, act_req(dev, policy_revision=pr, people=[{"hmac": h2, "role": "author"}]))
    p2 = rows("SELECT id, revision FROM people WHERE user_id=%s AND identity_hmac=%s", (uid, bytes.fromhex(h2)))[0]
    errs, res = [], []

    def recv():
        for _ in range(4):
            res.append(send(tok, act_req(dev, policy_revision=pr, people=[{"hmac": h2, "role": "author"}]))[0])

    def mv():
        try:
            db.trash_move(uid, "person", p2["id"], p2["revision"])
        except Exception as e:   # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=recv), threading.Thread(target=mv)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and res == [201] * 4, (errs, res)
    assert count(uid, "people", "AND identity_hmac=%s", (bytes.fromhex(h2),)) == 1, "같은 HMAC 사람 하나"
    print("OK — 사람 수신(휴지통 HMAC = 새 사람 0·연결 0·활동 저장 · 문맥 NOT-FOUND·거르기 빈 목록 · 표식(별칭 NULL·HMAC 유지) 재전송 재생성 0 · "
          "집계·쌓기 후보 제외 · 이동↔수신 동시 = 오류 0·사람 1)")


def stack_cands(uid):
    with db.tx() as c:
        return stack_service.candidates(c, uid, stack_service._index(c, uid))


# ── 4. 축 정책: 휴지통 축 선택 불가 · 정책이 쓰는 축 삭제 차단 · 영구 격리 0 · 경합 ─────────────
def test_axes(ctx):
    import threading
    uid, dev, tok, ax, pr = ctx
    e = expect("AXIS-IN-POLICY", db.trash_move, uid, "item", ax["X2"], 1)
    assert "흐름 시험 장치" in e.message
    db.trash_move(uid, "item", ax["X3"], 1)
    pol = imports.device_list(uid)["devices"][0]
    expect("ARG-INVALID", imports.device_policy_set, uid, dev, dict(pol["policy"], axes=[ax["X1"], ax["X3"]]), pol["policy_revision"])
    # 정책에서 빼고 삭제 → 옛 정책 판으로 보낸 PC = POLICY-STALE(409 멈춤 — 영구 격리 아님)
    imports.device_policy_set(uid, dev, dict(pol["policy"], axes=[ax["X1"]]), pol["policy_revision"])
    db.trash_move(uid, "item", ax["X2"], 1)
    st, b = send(tok, act_req(dev, policy_revision=pr, axes=[ax["X2"]]))
    assert st == 409 and b["code"] == "POLICY-STALE", (st, b)
    pr2 = imports.device_list(uid)["devices"][0]["policy_revision"]
    # 방어 층 — 정책에 남은 축이 (합성으로) 휴지통이어도 활동은 저장, 그 축 연결만 뺀다(POLICY-DENIED 영구 격리 0)
    run_sql("UPDATE items SET trashed_at=now(), purge_at=now() + interval '168 hours', last_trashed_at=now() WHERE user_id=%s AND id=%s",
            (uid, ax["X1"]))
    st, b = send(tok, act_req(dev, policy_revision=pr2, axes=[ax["X1"]]))
    assert st == 201 and b["state"] == "applied", (st, b)
    assert count(uid, "activity_links", "AND activity_id=%s AND relation='axis'", (b["target_id"],)) == 0
    g = activity.me_aggregate(uid, "all")
    assert ax["X1"] not in {x["item_id"] for x in g["by_axis"]}, g["by_axis"]
    assert sum(x["n"] for x in g["by_axis"]) + g["axis_unclassified"] >= g["total"], "휴지통 축만 달린 활동은 미분류로 센다"
    expect("NOT-FOUND", activity.axis_context, uid, ax["X1"])
    assert activity.activity_list(uid, axis_item_id=ax["X1"])["activities"] == [], "휴지통 축으로 거르면 빈 목록"
    assert all(x["id"] != ax["X1"] for a in activity.activity_list(uid)["activities"] for x in a["axes"]), "장식에 휴지통 축 이름 없음"
    run_sql("UPDATE items SET trashed_at=NULL, purge_at=NULL WHERE user_id=%s AND id=%s", (uid, ax["X1"]))
    # 경합 — 정책 저장(축 추가)과 그 축 삭제를 동시에: 「정책에 있는 휴지통 축」 이 생기지 않는다
    for _ in range(3):
        nx = db.new_id("i")
        run_sql("INSERT INTO items (user_id,id,object_id,schema_revision,title,revision) VALUES (%s,%s,%s,1,'경합 축',1)",
                (uid, nx, db.classify_target(uid)))
        p = imports.device_list(uid)["devices"][0]

        def save():
            try:
                imports.device_policy_set(uid, dev, dict(p["policy"], axes=p["policy"]["axes"] + [nx]), p["policy_revision"])
            except db.CrmError:
                pass

        def drop():
            try:
                db.trash_move(uid, "item", nx, 1)
            except db.CrmError:
                pass
        ts = [threading.Thread(target=save), threading.Thread(target=drop)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not rows("SELECT 1 FROM import_streams s JOIN items i ON i.user_id=s.user_id AND s.policy->'axes' ? i.id "
                        "WHERE s.user_id=%s AND s.revoked_at IS NULL AND i.trashed_at IS NOT NULL", (uid,)), "정책이 휴지통 축을 가리킴"
        p = imports.device_list(uid)["devices"][0]
        if nx in p["policy"]["axes"]:
            imports.device_policy_set(uid, dev, dict(p["policy"], axes=[a for a in p["policy"]["axes"] if a != nx]), p["policy_revision"])
    print("OK — 축 정책(정책이 쓰는 축 삭제 AXIS-IN-POLICY·장치 이름 · 휴지통 축 정책 저장 ARG-INVALID · 빼고 삭제 뒤 옛 판 = POLICY-STALE · "
          "휴지통 축 수신 = 201·축 연결만 뺌(영구 격리 0) · 집계·문맥·거르기·장식 · 저장↔삭제 동시 3회 = 정책에 휴지통 축 0)")


# ── 5. 승인한 내 글의 원천 · 활동 반응 연결 ─────────────
def test_record_source(ctx):
    uid, dev, tok, ax, _pr = ctx
    pr = imports.device_list(uid)["devices"][0]["policy_revision"]   # 축 시험이 정책을 바꿨다
    os.environ["CRM_INBOX_APPROVALS_ENABLED"] = "1"
    st, b = send(tok, act_req(dev, policy_revision=pr))
    aid = b["target_id"]
    o, v = _o(), _v()
    st, b = send(tok, text_req(dev, o, v, policy_revision=pr, activity_ref=aid))
    assert st == 201 and b["state"] == "pending", (st, b)
    db.trash_move(uid, "activity", aid)
    a = imports.inbox_approve(uid, b["import_id"])
    assert count(uid, "activity_links", "AND linked_record_id=%s", (a["record_id"],)) == 0, "휴지통 활동엔 반응 연결을 달지 않는다"
    db.trash_restore(uid, "activity", aid)
    r1 = a["record_id"]
    v2 = _v()   # 같은 원천 두 번째 승인 = 새 기록 R2(현재 판). 옛 판 기록 R1 만 휴지통이면 원천은 살아 있다
    st, b = send(tok, text_req(dev, o, v2, prev=v, policy_revision=pr, body="두 번째 본문"))
    r2 = imports.inbox_approve(uid, b["import_id"])["record_id"]
    db.trash_move(uid, "record", r1)
    v3 = _v()
    st, b = send(tok, text_req(dev, o, v3, prev=v2, policy_revision=pr, body="세 번째 본문"))
    assert st == 201 and b["state"] == "pending", ("옛 판 기록만 휴지통 = 현재 결과는 막지 않는다", st, b)
    st, b = send(tok, text_req(dev, o, v, policy_revision=pr, activity_ref=aid))
    assert st == 200 and b["duplicate"] and b["state"] == "deleted" and b["target_id"] is None, ("휴지통 기록을 가리키는 옛 판 재전송 = 대상 없음", b)
    db.trash_move(uid, "record", r2)
    st, b2 = send(tok, text_req(dev, o, _v(), prev=v3, policy_revision=pr, body="고친 본문"))
    assert st == 200 and b2["state"] == "deleted" and b2["target_id"] is None and b2["source_deleted"] == "trash", b2
    assert count(uid, "import_entries", "AND stream_id=%s AND original_id=%s AND state='pending' AND source_revision<>%s", (dev, o, v3)) == 0, \
        "새 승인 대기 0"
    st, b3 = send(tok, text_req(dev, o, v2, prev=v, policy_revision=pr, body="두 번째 본문"))
    assert st == 200 and b3["duplicate"] and b3["target_id"] is None, ("같은 개정 재전송도 기록 ID 를 주지 않는다", b3)
    # 원천 차단 행(state=deleted)은 승인 대기 목록(전부)·상세에 나오지 않는다
    assert b2["import_id"] and all(e["state"] != "deleted" and e["id"] != b2["import_id"] for e in imports.inbox_list(uid, "all", limit=100)["entries"])
    expect("NOT-FOUND", imports.inbox_get, uid, b2["import_id"])
    os.environ.pop("CRM_INBOX_APPROVALS_ENABLED", None)
    print("OK — 승인한 내 글 원천(휴지통 활동 반응 연결 0 · 옛 판 기록만 휴지통 = 새 개정 201 대기 · 옛 판 재전송 대상 없음 · "
          "현재 기록 휴지통 → 새 개정·같은 개정 = 200 deleted·대상 없음·새 승인 대기 0)")


def principle_with_links(uid, axis_id, cand_id):
    """합성 기준 항목(확정·적용) — axes → 축 항목, evidence_claims → 후보. 반환 item id."""
    pid = db.new_id("i")
    run_sql("INSERT INTO items (user_id,id,object_id,schema_revision,title,decision,revision) VALUES (%s,%s,'od_principles',1,'합성 기준',"
            "'confirmed',1)", (uid, pid))
    run_sql("INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_item_id) VALUES (%s,%s,'od_principles','axes',%s)",
            (uid, pid, axis_id))
    run_sql("INSERT INTO item_links (user_id,source_item_id,source_object_id,field_key,target_candidate_id) "
            "VALUES (%s,%s,'od_principles','evidence_claims',%s)", (uid, pid, cand_id))
    return pid


def record_with_candidate(uid, body="휴지통 시험 원문 한 줄. 후보가 될 문장."):
    rid = db.record_add(uid, "후보 원문", body)["id"]
    q = "후보가 될 문장"
    s = body.index(q)
    cid = db.new_id("c")
    run_sql("INSERT INTO candidates (id,user_id,record_id,text,quote,quote_start,quote_end,origin) VALUES (%s,%s,%s,'후보',%s,%s,%s,'ai')",
            (cid, uid, rid, q, s, s + len(q)))
    return rid, cid


# ── 6. 쌓기: 후보 스냅샷 · 대상/연결 검증에서 휴지통 = 「없음」 이 아닌 삭제 대상(막힘) ─────────────
def test_stack(ctx):
    uid, dev, tok, ax, pr = ctx
    y = db.new_id("i")
    run_sql("INSERT INTO items (user_id,id,object_id,schema_revision,code,title,revision) VALUES (%s,%s,'od_axes',1,'ycode','휴지통 축 Y',1)",
            (uid, y))
    rid, cid = record_with_candidate(uid)
    assert y in {i["id"] for i in stack_cands(uid)["items"]}
    db.trash_move(uid, "item", y, 1)
    db.trash_move(uid, "record", rid)
    assert y not in {i["id"] for i in stack_cands(uid)["items"]}, "후보 스냅샷에서 휴지통 항목 제외"
    gone_q = stack_service._BLOCK_QUESTIONS["deleted_target"]
    body = {"units": [{"uid": "u1", "text": "정리 글", "change_ids": ["c1", "c2", "c3", "c4", "c5"]}],
            "changes": [
                {"cid": "c1", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "explicit_id", "value": y},
                 "fields": {"title": "고침"}, "links": [], "depends_on": []},
                {"cid": "c2", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "source_id", "scheme": "code", "value": "ycode"},
                 "fields": {"title": "고침"}, "links": [], "depends_on": []},
                {"cid": "c3", "op": "create", "object_id": "od_principles", "fields": {"title": "새 기준"},
                 "links": [{"relation": "axes", "target": {"ref_kind": "explicit_id", "value": y}}], "depends_on": []},
                {"cid": "c4", "op": "create", "object_id": "od_principles", "fields": {"title": "새 기준 2"},
                 "links": [{"relation": "evidence_claims", "target": {"ref_kind": "explicit_id", "value": cid}}], "depends_on": []},
                {"cid": "c5", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "inferred", "value": y},
                 "fields": {}, "links": [], "depends_on": []}],
            "blocked": [], "model_meta": {}}
    with db.tx() as c:
        out = stack_service.validate_result(c, uid, None, body, stack_service._index(c, uid))
    by = {b["cid"]: b for b in out["blocked"]}
    for k in ("c1", "c2", "c3", "c4", "c5"):
        assert k in by and by[k]["code"] == "unresolved_target" and by[k]["question"] == gone_q, (k, out["blocked"])
    assert all(ch["item_id"] is None for ch in out["changes"]), "휴지통 항목을 고칠 대상으로 잡지 않는다"
    assert all(ln["target_id"] is None for ch in out["changes"] for ln in ch["links"]), "휴지통 대상과 새 관계 0"
    assert stack_service.blocked_open(out["blocked"]), "[확정] 잠김"
    assert count(uid, "items", "AND object_id='od_axes' AND code='ycode'") == 1, "같은 code 새 항목 0"
    # 없는 대상은 여전히 「없음」 문구(구별)
    body2 = {**body, "units": [{"uid": "u1", "text": "정리 글", "change_ids": ["c9"]}],
             "changes": [{"cid": "c9", "op": "update", "object_id": "od_axes", "target": {"ref_kind": "inferred", "value": "i_nothere1"},
                          "fields": {}, "links": [], "depends_on": []}]}
    with db.tx() as c:
        out2 = stack_service.validate_result(c, uid, None, body2, stack_service._index(c, uid))
    assert [b["question"] for b in out2["blocked"]] == [stack_service._BLOCK_QUESTIONS["unresolved_target"]], out2["blocked"]
    print("OK — 쌓기(후보 스냅샷 휴지통 항목 제외 · explicit_id·source_id(code)·inferred 대상 · 항목·후보(부모 기록) 연결 = 거부 아닌 막힘·"
          "삭제 문구 · 대상·관계 ID 없음 · [확정] 잠김 · 같은 code 새 항목 0 · 없는 대상은 「없음」 문구)")


# ── 7. 운영자 화면 · 근거 집계 · 장식 · 후보 정리 ─────────────
def test_admin_and_grounds(ctx):
    uid, dev, tok, ax, pr = ctx
    z = db.new_id("i")
    run_sql("INSERT INTO items (user_id,id,object_id,schema_revision,title,revision) VALUES (%s,%s,'od_axes',1,'축 Z',1)", (uid, z))
    rid, cid = record_with_candidate(uid, "근거 원문 두 번째. 후보가 될 문장.")
    pid = principle_with_links(uid, z, cid)

    def stats():
        v = admin_db.userview(admin_db.VIEWER_ADMIN, uid)[uid]
        return v, admin_db.link_stats(v, admin_db.contract_input(v))

    def grounds():
        return {g["item_id"]: g for g in activity.me_aggregate(uid, "all")["grounds"]["principles"]}[pid]
    v, s = stats()
    assert not s["errors"] and grounds()["records"] == 1, (s, grounds())
    db.trash_move(uid, "item", z, 1)
    db.trash_move(uid, "record", rid)
    v, s = stats()
    assert not s["errors"], ("휴지통 대상은 무결성 오류가 아니다", s["errors"])
    assert v["md_broken"] == 0, "휴지통 기록의 후보 = M-D 위반 아님"
    assert sum(u["n"] for u in s["unchecked"] if u["why"] == "trashed") == 2, s["unchecked"]
    assert grounds()["records"] == 0, "근거 집계에서 휴지통 기록(후보 경유) 제외"
    db.trash_move(uid, "item", pid, 1)
    v, s = stats()
    assert not any(ln for ln in v["links"]), ("휴지통 출발 항목은 세지 않는다", v["links"])
    assert pid not in {g["item_id"] for g in activity.me_aggregate(uid, "all")["grounds"]["principles"]}
    # 후보 정리 — 부모 기록 정리 때 후보를 가리키는 연결을 먼저 지우고 후보 본문 제거(quote 를 비우지 않는다)
    shift_trash(uid, "records", [rid], 9)
    out = db.trash_purge_due()
    assert out["failed"] == 0 and out["records"] >= 1, out
    assert count(uid, "candidates", "AND id=%s", (cid,)) == 0 and count(uid, "item_links", "AND target_candidate_id=%s", (cid,)) == 0
    assert count(uid, "items", "AND id=%s", (pid,)) == 1, "연결한 기준(상대)은 남는다"
    print("OK — 운영자 화면(휴지통 대상 = REF-DANGLING·MD-ORPHAN 아님·「검사 불가 trashed」 · 휴지통 출발 항목 미집계) · 근거 집계 제외 · "
          "후보 정리(참조 먼저·본문 제거·기준 유지)")


def main():
    for k in SWITCHES + ("CRM_TRASH_ENABLED",):
        os.environ[k] = "1"
    for k in (("device", "minute", "request"), ("user", "minute", "request")):   # 분당 요청 한도는 이 시험 밖(한 프로세스 안에서만)
        imports.LIMITS[k] = (100000, None)
    db.init_schema()
    cleanup()
    try:
        ctx = setup_user(MAIN, "흐름시험")
        st1 = test_activity_source(ctx)
        test_purge(ctx, st1)
        test_people(ctx)
        test_axes(ctx)
        test_record_source(ctx)
        test_stack(ctx)
        test_admin_and_grounds(ctx)
        o = db.user_by_email(OTHER)["id"]   # 남의 id = 없는 id
        expect("NOT-FOUND", db.trash_move, o, "activity", st1["ids"][0])
        print("OK — 휴지통 흐름 시험 전부 통과")
    finally:
        db.TRASH_CLOCK = None
        cleanup()
        db.close()


if __name__ == "__main__":
    main()
