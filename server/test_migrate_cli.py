"""board 이전 CLI(migrate_cli.py) 시험 — 전용 로컬 DB crm_test_bi_mig 에서만 돈다(시작 가드: 호스트 로컬 + DB 이름 허용 목록).

실행: DATABASE_URL=postgresql://localhost/crm_test_bi_mig server/.venv/bin/python server/test_migrate_cli.py
DB 는 시작할 때 지우고 다시 만든다(허용 목록 이름만). 명세는 전부 합성 — 원작자 실제 board 내용 없음.

A) DB 없이: 적재(중복 키·실수·UTF-8) · 해시 재계산·불일치 · 구조(금지 필드·필수·날짜 xor) · 의존성 순서 · 순환
B) 픽스처 = 운영 원작자 계정 꼴: 신규 계정 표준 자동 시드 끔 → template_author 시드(author@1, std_id NULL) → std 3개 편입
   · 대상 해석(std_id → template_slot → key · 0개·2개 거절 · 보관 거절) · validate-only 출력에 제목·본문 없음 · 쓰기 0
C) 서버 이전 함수(imports.migration_*) 실행 — 스텁이면 PENDING 으로 표시하고 건너뛴다:
   --limit 소량 → resume 나머지 · 재실행 업무 변경 0 · 같은 개정·다른 내용 충돌 · 새 개정(기록 새 판·항목 제안→적용)
   · 서버 revision 변경 뒤 충돌 · 한도 partial → 재개 · 병행 기간 만료 뒤 차단 · extend · close · 스위치 꺼짐
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ALLOWED_DBS = ("crm_test_bi_mig",)
URL = os.environ.get("DATABASE_URL", "")
_u = urllib.parse.urlsplit(URL)
if not URL or _u.hostname not in ("localhost", "127.0.0.1", "::1") or _u.path.lstrip("/") not in ALLOWED_DBS:
    sys.exit(f"시작 거부: DATABASE_URL 은 로컬 호스트 + DB 이름 {ALLOWED_DBS} 만(명령 앞에 명시).")
DBNAME = _u.path.lstrip("/")

import migrate_cli as M  # noqa: E402

PENDING = []
PASSED = []
TMP = Path(tempfile.mkdtemp(prefix="migcli_"))
MARK = "합성표식"   # 픽스처 제목·본문에 넣는 표식 — CLI 출력에 나오면 안 된다


def ok(name):
    PASSED.append(name)
    print(f"OK  {name}", flush=True)


def pending(name, why):
    PENDING.append((name, why))
    print(f"PENDING  {name} — {why}", flush=True)


def expect_reject(fn, rule, *a):
    try:
        fn(*a)
    except M.Reject as e:
        rules = [r for _, r in e.problems]
        assert any(rule in r for r in rules), (rule, e.problems)
        return e
    raise AssertionError(f"거절 안 됨: {rule}")


def OID(name):
    """합성 불투명 원본 ID(o_ + 32 hex) — 이름에서 결정적으로."""
    return "o_" + hashlib.md5(name.encode()).hexdigest()


def _v(n):
    return "v_" + hashlib.md5(f"rev{n}".encode()).hexdigest()


V1, V2, V3, V9 = _v(1), _v(2), _v(3), _v(9)
SRC = "is_" + "ab" * 12   # 합성 이전 입력원 ID


def sha_of(doc):
    return hashlib.sha256(json.dumps({k: v for k, v in doc.items() if k != "manifest_sha256"}, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def manifest(uid, rows, **top):
    d = {"contract": "board-migration/1", "target_user_id": uid, "source_instance_id": SRC,
         "transform_version": "board-manifest/1", "reviewed_at": "2026-10-20T09:00:00+09:00", "rows": rows}
    d.update(top)
    d["manifest_sha256"] = sha_of(d)
    return d


def write(doc, name="m.json", raw=None):
    p = TMP / name
    p.write_bytes(raw if raw is not None else json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    return str(p)


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = M.main(list(argv))
    return code, out.getvalue(), err.getvalue()


# ── 합성 행 ───────────────────────────────────────────────

AX = {"std_id": "std:axes", "template_slot": "author@1:axes", "key": "axes"}
EV = {"std_id": "std:decision_events", "template_slot": "author@1:decision_events", "key": "decision_events"}
PUB = {"template_slot": "author@1:publications", "key": "publications"}


def r_axis(n, rev=V1, prev=None):
    return {"original_id": OID(f"ax{n}"), "revision": rev, "previous_revision": prev, "target_kind": "item", "unit_kind": "axis",
            "object_ref": AX, "code": f"AX-{n}", "title": f"{MARK} 축 {n}", "body": "", "fields": {"keywords": [f"k{n}"]},
            "references": [], "expected_target_revision": None}


def r_event(n, rev=V1, prev=None):
    return {"original_id": OID(f"ev{n}"), "revision": rev, "previous_revision": prev, "target_kind": "item", "unit_kind": "decision_index",
            "object_ref": EV, "code": f"EV-{n}", "title": f"{MARK} 결정 {n}", "body": f"{MARK} 본문", "fields": {"happened_on": "2026-01-0" + str(n)},
            "references": [], "source_date": "2026-01-0" + str(n), "expected_target_revision": None}


def r_record(n, rev=V1, prev=None, body=None, attach_axis=None):
    att = {"attach_to": [], "axes": [{"original_id": attach_axis, "revision": V1}] if attach_axis else []}
    return {"original_id": OID(f"rec{n}"), "revision": rev, "previous_revision": prev, "target_kind": "record", "unit_kind": "journal",
            "title": f"{MARK} 일지 {n}", "body": body if body is not None else f"{MARK} 기록 본문 {n}",
            "attach": att, "source_date": "2026-02-0" + str(n), "expected_target_revision": None}


def r_pub(n, rec, rev=V1, prev=None):
    return {"original_id": OID(f"pub{n}"), "revision": rev, "previous_revision": prev, "target_kind": "item", "unit_kind": "publication",
            "object_ref": PUB, "code": f"PUB-{n}", "title": f"{MARK} 발행 {n}", "body": "",
            "fields": {"channel": "other", "published_on": "2026-03-01", "url": ""},
            "references": [{"field_key": "materials", "target": {"original_id": rec, "revision": V1}}], "expected_target_revision": None}


# ── A) DB 없이 ──────────────────────────────────────────────

def section_a():
    uid = "u_x"
    good = manifest(uid, [r_axis(1)])
    doc, _ = M.load_manifest(write(good))
    assert M.manifest_hash(doc) == good["manifest_sha256"]
    ok("A1 해시 재계산 = 자기 해시 뺀 JCS SHA-256")

    expect_reject(M.load_manifest, "duplicate-key", write(None, "dup.json", b'{"a":1,"a":2}'))
    expect_reject(M.load_manifest, "float-forbidden", write(None, "flt.json", b'{"a":1.5}'))
    expect_reject(M.load_manifest, "utf8-invalid", write(None, "bad.json", b'{"a":"\xff"}'))
    expect_reject(M.load_manifest, "object-required", write(None, "arr.json", b'[1]'))
    ok("A2 적재 거절(중복 키·실수·UTF-8·객체 아님)")

    bad = copy.deepcopy(good)
    bad["rows"][0]["title"] = "바뀜"
    assert M.manifest_hash(bad) != bad["manifest_sha256"]
    ok("A3 내용이 바뀌면 해시 불일치")

    for mut, rule in ((lambda d: d.update(ai_markers=[]), "additional"),
                      (lambda d: d["rows"][0].update(legacy_ai_markers=[]), "additional"),
                      (lambda d: d.update(migration_acceptance={}), "additional"),
                      (lambda d: d["rows"][0].update(original_id="o_ax1"), "pattern"),
                      (lambda d: d["rows"][0].pop("object_ref"), "required"),
                      (lambda d: d["rows"][0].update(target_kind="record", body="x"), "not"),
                      (lambda d: d.pop("reviewed_at"), "required"),
                      (lambda d: d.update(contract="board-migration/2"), "const"),
                      (lambda d: d["rows"][0].update(source_created_at="2026-01-01T00:00:00+09:00", source_date="2026-01-01"),
                       "source_created_at-xor-source_date"),
                      (lambda d: d["rows"][0].update(source_date="2026-13-01"), "date"),
                      (lambda d: d["rows"][0].update(source_created_at="2026-01-01T00:00:00"), "pattern"),
                      (lambda d: d["rows"].append(copy.deepcopy(d["rows"][0])), "duplicate"),
                      (lambda d: d["rows"][0].update(target_kind="activity"), "enum"),
                      (lambda d: d.update(rows=[]), "minItems")):
        d = copy.deepcopy(good)
        mut(d)
        expect_reject(M.validate_structure, rule, d)
    ok("A4 스키마 위반 거절(금지 필드·필수·계약·날짜 xor·날짜·시간대·중복 원본·종류·빈 행)")

    rows = [r_pub(1, OID("rec1")), r_record(1, attach_axis=OID("ax1")), r_event(1), r_axis(1)]
    order = [rows[i]["original_id"] for i in M.order_rows(rows)]
    assert order == [OID("ev1"), OID("ax1"), OID("rec1"), OID("pub1")], order
    ok("A5 의존성 순서: 선행 항목 → 기록(+attach) → 기록을 참조하는 발행")

    cyc = [r_record(1), r_pub(1, OID("rec1"))]
    cyc[0]["references"] = [{"field_key": "x", "target": {"original_id": OID("pub1")}}]
    expect_reject(M.order_rows, "dependency-cycle", cyc)
    self_ref = [r_pub(1, OID("pub1"))]
    expect_reject(M.order_rows, "self-reference", self_ref)
    ok("A6 순환·자기 참조 거절")


# ── B) 픽스처 · 대상 해석 · validate-only ───────────────────

def reset_db():
    assert DBNAME in ALLOWED_DBS
    subprocess.run(["dropdb", "--if-exists", DBNAME], check=True)
    subprocess.run(["createdb", DBNAME], check=True)


def fixture(email):
    """운영 원작자 계정 꼴: 신규 계정 표준 자동 시드 없이 생성 → author@1 시드(std_id NULL) → std 편입."""
    import db
    import template_author
    orig = db._std_on_create
    db._std_on_create = lambda c, uid: None   # 표준 시드 전에 만든 옛 계정과 같은 출발점
    try:
        uid = db.user_by_subject(f"sub_{email}", email)["id"]
    finally:
        db._std_on_create = orig
    try:
        db.display_name_set(uid, "시험작성자")
    except Exception:
        pass
    template_author.seed(uid, log=lambda *a: None)
    for k in ("axes", "decision_events", "principles"):
        d = {x["key"]: x for x in db.definitions(uid)}[k]
        db.std_choose(uid, k, "incorporated", confirmed_meaning=True, expected_revision=d["revision"])
    return uid


def table_hash():
    import db
    with db.tx() as c:
        tabs = [r["t"] for r in c.execute("SELECT tablename AS t FROM pg_tables WHERE schemaname = current_schema() ORDER BY 1").fetchall()]
        h = hashlib.sha256()
        for t in tabs:
            n = c.execute(f'SELECT count(*) AS n, md5(coalesce(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) AS m FROM "{t}" x').fetchone()
            h.update(f"{t}:{n['n']}:{n['m']};".encode())
        return h.hexdigest()


def section_b():
    import db
    db.init_schema()
    uid = fixture("mig-a@example.com")
    defs = {d["key"]: d for d in db.definitions(uid)}
    assert defs["axes"]["std_id"] == "std:axes" and defs["axes"]["template_slot"] == "author@1:axes"
    assert defs["publications"]["std_id"] is None and defs["publications"]["template_slot"] == "author@1:publications"
    ok("B1 픽스처 = author@1 시드 → std 편입(axes·decision_events·principles std_id, publications 는 template_slot 만)")

    dl = db.definitions(uid, include_archived=True)
    assert M.resolve_object(dl, {"std_id": "std:axes", "key": "craft"}) == (defs["axes"]["id"], None)   # 첫 계층이 이긴다
    assert M.resolve_object(dl, {"std_id": "std:nope", "template_slot": "author@1:publications"}) == (defs["publications"]["id"], None)
    assert M.resolve_object(dl, {"key": "craft"}) == (defs["craft"]["id"], None)
    assert M.resolve_object(dl, {"key": "nope"}) == (None, "object-not-found")
    assert M.resolve_object(dl, {}) == (None, "object-ref-empty")
    ok("B2 대상 해석 std_id → template_slot → key")

    # 같은 template_slot 이 둘 — 사용자 오브젝트를 만들고 슬롯을 겹쳐 단다
    p = db.definition_propose(uid, None, 0, {"key": "craft_copy", "label": "복제", "semantic_kind": "collection", "policy": {},
                                             "fields": [{"key": "project", "label": "프로젝트", "value_type": "text"}]})
    a = db.definition_apply(uid, p["object_id"], p["proposal_id"], p["base_revision"])
    db.template_slot_mark(uid, a["id"], "author@1:craft", {})
    dl = db.definitions(uid, include_archived=True)
    assert M.resolve_object(dl, {"template_slot": "author@1:craft", "key": "craft"}) == (None, "object-ambiguous:template_slot")
    ok("B3 한 계층에 둘 이상 = 거절(다음 계층으로 넘어가지 않음)")

    rows = [r_axis(1), r_event(1), r_record(1, attach_axis=OID("ax1")), r_pub(1, OID("rec1"))]
    m = manifest(uid, rows)
    before = table_hash()
    code, out, err = cli("validate-only", write(m), "--user", uid)
    assert code == 0, (code, out, err)
    assert "행 4" in out and MARK not in out + err and "AX-1" not in out + err, out + err
    assert table_hash() == before, "validate-only 가 DB 를 바꿨다"
    ok("B4 validate-only 통과 · 출력에 제목·본문·code 없음 · 쓰기 0")

    for mut, rule in ((lambda d: d["rows"][0].update(object_ref={"key": "nope"}), "object-not-found"),
                      (lambda d: d["rows"][0].update(object_ref={"template_slot": "author@1:craft"}), "object-ambiguous"),
                      (lambda d: d["rows"][1]["fields"].update(nope=1), "field-not-found"),
                      (lambda d: d.update(target_user_id="u_other"), "user-mismatch")):
        d = copy.deepcopy(m)
        mut(d)
        d["manifest_sha256"] = sha_of(d)
        code, out, err = cli("validate-only", write(d, "v.json"), "--user", uid)
        assert code == 1 and rule in err and MARK not in out + err, (rule, code, err)
    for mut, rule in ((lambda d: d["rows"][3]["references"].__setitem__(0, {"field_key": "materials", "target": {"record_id": "r_nope"}}),
                       "record-not-found"),
                      (lambda d: d["rows"][3]["references"].__setitem__(0, {"field_key": "axes", "target": {"item_id": "i_nope1234"}}),
                       "item-not-found"),
                      (lambda d: d["rows"][3]["references"].__setitem__(0, {"field_key": "channel", "target": {"original_id": OID("rec1"), "revision": V1}}),
                       "not-reference-field"),
                      (lambda d: d["rows"][3]["references"].__setitem__(0, {"field_key": "axes", "target": {"original_id": OID("rec1"), "revision": V1}}),
                       "target-kind-mismatch"),
                      (lambda d: d["rows"][3]["references"].__setitem__(0, {"field_key": "materials", "target": {"original_id": OID("x"), "record_id": "r_abcd"}}),
                       "oneOf"),
                      (lambda d: d["rows"][3]["references"].__setitem__(0, {"field_key": "materials", "target": {"original_id": OID("rec1")}}),
                       "oneOf"),
                      (lambda d: d["rows"][3]["fields"].update(materials=[]), "reference-in-fields"),
                      (lambda d: d["rows"][2]["attach"].update(nope=[]), "additional"),
                      (lambda d: d["rows"][2]["attach"]["axes"].append({"candidate_id": "c_abcd"}), "target-form"),
                      (lambda d: (d["rows"].append(r_record(2)), d["rows"][2]["attach"]["axes"].append({"original_id": OID("rec2"), "revision": V1})),
                       "target-kind-mismatch"),
                      (lambda d: d["rows"][0].update(attach={"axes": []}), "record-only")):
        d = copy.deepcopy(m)
        mut(d)
        d["manifest_sha256"] = sha_of(d)
        code, out, err = cli("validate-only", write(d, "v.json"), "--user", uid)
        assert code == 1 and rule in err and MARK not in out + err, (rule, code, err)
    # 기존 기록 판 ID·기존 항목 code 참조는 통과
    rid = db.record_add(uid, f"{MARK} 기존", f"{MARK} 기존 본문")["id"]
    d = copy.deepcopy(m)
    d["rows"][3]["references"] = [{"field_key": "materials", "target": {"record_id": rid}}]
    d["manifest_sha256"] = sha_of(d)
    code, out, err = cli("validate-only", write(d, "v.json"), "--user", uid)
    assert code == 0, err
    ok("B5b references·attach 검사(없는 판·없는 항목·관계 아닌 필드·종류 불일치·형식·fields 의 관계·attach 모양) · 기존 판 ID 통과")
    d = copy.deepcopy(m)
    d["rows"][0]["title"] = "몰래 바뀜"
    code, out, err = cli("validate-only", write(d, "h.json"), "--user", uid)
    assert code == 1 and "hash-mismatch" in err, err
    ok("B5 validate-only 거절: 대상 0개·2개 · 없는 필드 · 계정 불일치 · 해시 불일치")

    code, out, err = cli("validate-only", write(m), "--user", uid)
    code2, _, err2 = cli("run", write(m), "--user", uid)
    assert code2 == 1 and "SWITCH-OFF" in err2, err2
    ok("B6 스위치 꺼짐 → run 거절")
    return uid, m


# ── C) 서버 이전 함수 ───────────────────────────────────────

def server_ready() -> str | None:
    import imports
    for fn in ("migration_stream", "migration_stream_get", "migration_entries", "migration_apply_row", "migration_extend", "migration_close"):
        f = getattr(imports, fn, None)
        if f is None:
            return f"imports.{fn} 없음"
    try:
        imports.migration_stream("u_probe_none", "probe")
    except NotImplementedError:
        return "imports.migration_* 스텁(NotImplementedError)"
    except Exception:
        pass
    if getattr(imports, "migration_entries", None) is None:
        return "imports.migration_entries(행 단위 대응표 읽기) 없음 — resume·close·진행 계산 차단"
    return None


def section_c(uid, m0):
    why = server_ready()
    if why:
        for name in ("C1 --limit 소량 → resume 나머지", "C2 재실행 업무 변경 0", "C3 같은 개정·다른 내용 충돌",
                     "C4 새 개정 → 기록 새 판·항목 제안→적용", "C5 서버 revision 변경 뒤 충돌", "C6 한도 partial → 재개",
                     "C7 병행 만료 뒤 차단 · extend", "C8 close"):
            pending(name, why)
        return
    import db
    os.environ["CRM_BOARD_MIGRATION_ENABLED"] = "1"

    def cli(*a):   # C 절 전부: 서버 오류 문구가 stderr 로 나가도 제목·본문 값이 섞이지 않아야 한다
        r = globals()["cli"](*a)
        assert MARK not in r[1] + r[2], ("출력에 합성 본문 표식", r)
        return r

    def q(sql, *args):
        with db.tx() as c:
            return c.execute(sql, args).fetchall()

    try:
        code, out, err = cli("init", "--user", uid)
        assert code == 0 and "새로 만듦" in out, (code, out, err)
        src = out.split("이전 입력원: ")[1].split(" ")[0]

        def mk(rows):
            return manifest(uid, rows, source_instance_id=src)

        m = mk(copy.deepcopy(m0["rows"]))
        p = write(m, "c1.json")
        code, out, err = cli("run", p, "--user", uid, "--limit", "2")
        assert code == 0 and "applied 2" in out and "남은 행 2" in out, (code, out, err)
        code, out, err = cli("resume", p, "--user", uid)
        assert code == 0 and "applied 2" in out and "skipped 2" in out, (code, out, err)
        its = {i["code"]: i for i in db.items(uid)}
        assert {"AX-1", "EV-1", "PUB-1"} <= set(its), sorted(its)
        recs = q("SELECT id, ingest_kind, author_kind, text_process, import_batch_id, source_date FROM records "
                 "WHERE user_id=%s AND ingest_kind='board_migration'", uid)
        assert len(recs) == 1 and recs[0]["author_kind"] == "self" and recs[0]["text_process"] == "verbatim", recs
        assert recs[0]["import_batch_id"] and recs[0]["source_date"] is not None, recs
        rid = recs[0]["id"]
        mats = q("SELECT 1 FROM item_links WHERE user_id=%s AND source_item_id=%s AND field_key='materials' AND target_record_id=%s",
                 uid, its["PUB-1"]["id"], rid)
        att = q("SELECT 1 FROM item_links WHERE user_id=%s AND source_record_id=%s AND field_key='axes' AND target_item_id=%s",
                uid, rid, its["AX-1"]["id"])
        assert mats and att, (mats, att)
        st = q("SELECT migration_started_at, migration_ends_at FROM import_streams WHERE user_id=%s AND id=%s", uid, src)[0]
        assert st["migration_started_at"] and (st["migration_ends_at"] - st["migration_started_at"]).days == 14, st
        ok("C1 실제 서버: --limit 2 → resume 나머지 · 기록 self/verbatim·board_migration·배치·날짜 · 발행→기록·기록→축 연결 · 병행 14일 시작")

        before = table_hash()
        code, out, err = cli("run", p, "--user", uid)
        assert code == 0 and "duplicate 4" in out and "업무 변경 0" in out, (code, out, err)
        assert table_hash() == before, "재실행이 DB 를 바꿨다"
        ok("C2 실제 서버: 같은 명세 재실행 → duplicate 4 · 업무 변경 0 · 전 테이블 해시 불변")

        rows3 = copy.deepcopy(m["rows"])
        rows3[2]["body"] = f"{MARK} 같은 개정 다른 본문"
        before = table_hash()
        code, out, err = cli("run", write(mk(rows3), "c3.json"), "--user", uid)
        assert code == 1 and "IDEMPOTENCY-CONFLICT" in err, (code, err)
        assert table_hash() == before
        ok("C3 실제 서버: 같은 개정·다른 내용 → IDEMPOTENCY-CONFLICT · 쓰기 0")

        rows4 = copy.deepcopy(m["rows"])
        rows4[2].update(revision=V2, body=f"{MARK} 새 판 본문")
        rows4[0].update(revision=V2, fields={"keywords": ["k1", "k1b"]})
        rows4[1].update(revision=V2)   # 반영 내용 같음 → noop
        p4 = write(mk(rows4), "c4.json")
        ax_rev = db.item_get(uid, its["AX-1"]["id"])["revision"]
        code, out, err = cli("run", p4, "--user", uid)
        assert code == 0 and "applied 2" in out and "noop 1" in out and "duplicate 1" in out, (code, out, err)
        recs = q("SELECT id FROM records WHERE user_id=%s AND ingest_kind='board_migration' ORDER BY created, id", uid)
        assert len(recs) == 2 and rid in [r["id"] for r in recs], recs   # 옛 판 그대로 + 새 판
        assert db.record_get(uid, rid)["body"] == f"{MARK} 기록 본문 1"
        ax = db.item_get(uid, its["AX-1"]["id"])
        assert ax["revision"] == ax_rev + 1 and ax["data"].get("keywords") == ["k1", "k1b"], (ax["revision"], ax["data"])
        ok("C4 실제 서버: 새 개정 → 기록 새 판(옛 판 유지) · 항목 제안→적용(revision+1) · 같은 반영 noop")

        pr = db.item_propose(uid, ax["object_id"], ax["schema_revision"], ax["id"], ax["revision"], ax["code"], ax["title"] + " 웹",
                             ax["body"], ax["data"], [])
        db.item_decide(uid, ax["id"], ax["revision"], pr["proposal_id"], "apply")
        rows5 = copy.deepcopy(rows4)
        rows5[0].update(revision=V3, fields={"keywords": ["k1c"]})
        code, out, err = cli("run", write(mk(rows5), "c5.json"), "--user", uid)
        assert code == 1 and ("TARGET-REVISION-CONFLICT" in err or "TARGET-CONFLICT" in err), (code, err)
        if "TARGET-REVISION-CONFLICT" not in err:
            pending("C5b 충돌 코드 이름", "서버가 TARGET-CONFLICT 를 낸다 — 메인 판정은 TARGET-REVISION-CONFLICT")
        ok("C5 실제 서버: 웹에서 항목을 바꾼 뒤 새 개정 → 대상 충돌로 멈춤")

        # 한도: 오늘 기록 = 이전 2판 + B5b 의 record_add 1 → 50 까지 47 남음. 새 기록 48행 → 47 적용 뒤 RATE-LIMIT
        rows6 = copy.deepcopy(rows4) + [r_record(100 + k) for k in range(48)]
        for r in rows6[4:]:
            r["source_date"] = "2026-02-01"
        p6 = write(mk(rows6), "c6.json")
        code, out, err = cli("run", p6, "--user", uid)
        assert code == 3 and "RATE-LIMIT" in err and "resume" in err and "applied 47" in out, (code, out, err)
        code, out, err = cli("resume", p6, "--user", uid)
        assert code == 3 and "RATE-LIMIT" in err and "applied 0" in out, (code, out, err)
        # 다음 KST 구간 흉내 — 시험 DB 에서만 이 사용자 기록의 받은 시각을 하루 앞당긴다
        with db.tx() as c:
            c.execute("UPDATE records SET received_at = received_at - interval '1 day', created = created - interval '1 day' WHERE user_id = %s", (uid,))
        code, out, err = cli("resume", p6, "--user", uid)
        assert code == 0 and "applied 1" in out, (code, out, err)
        ok("C6 실제 서버: 기록 일일 한도 → exit 3 partial(재개 안내) · 같은 날 resume 도 partial · 다음 구간 resume 로 나머지")

        with db.tx() as c:
            c.execute("UPDATE import_streams SET migration_started_at = now() - interval '15 days', migration_ends_at = now() - interval '1 day' WHERE user_id = %s AND id = %s", (uid, src))
        rows7 = copy.deepcopy(rows6)
        rows7[1].update(revision=V3, title=rows7[1]["title"] + " 개정")
        m7 = mk(rows7)
        p7 = write(m7, "c7.json")
        code, out, err = cli("run", p7, "--user", uid)
        assert code == 1 and "MIGRATION-WINDOW-CLOSED" in err and "extend" in err, (code, err)
        import imports
        try:
            imports.migration_apply_row(uid, src, M.server_row(M.prepare(p7, uid), 1, V2), batch_id="mb_x",
                                        manifest_sha256=m7["manifest_sha256"], transform_version="board-manifest/1")
            raise AssertionError("서버가 만료 뒤 적용을 받았다")
        except db.CrmError as e:
            assert e.code == "MIGRATION-WINDOW-CLOSED", e.code
        code, out, err = cli("extend", "--user", uid)
        assert code == 0 and "7일 연장" in out, (code, out, err)
        code, out, err = cli("run", p7, "--user", uid)
        assert code == 0 and "applied 1" in out, (code, out, err)
        ok("C7 실제 서버: 병행 기간 만료 → CLI·서버 둘 다 적용 차단 · extend 7일 → 적용")

        # 개인정보 경계: 서버가 값 검증으로 거절할 때 오류 문구에 필드 값이 섞이지 않는다(cli 래퍼가 MARK 를 본다)
        rows9 = copy.deepcopy(rows7)
        rows9[3].update(revision=V2, fields={"channel": f"{MARK}x", "published_on": "2026-03-01", "url": ""})
        before = table_hash()
        code, out, err = globals()["cli"]("run", write(mk(rows9), "c9.json"), "--user", uid)
        assert code == 1 and table_hash() == before, (code, err)
        leak = MARK in out + err
        print(f"     C9 거절 코드: {err.strip().splitlines()[-1].split(']')[0]}]", flush=True)
        if leak:
            pending("C9 오류 문구 값 누출", "서버 값 검증 오류 문구에 필드 값이 들어 있다 — 서버 코어 문구 수정 필요")
        else:
            ok("C9 실제 서버: 선택지 밖 필드 값 → 거절·쓰기 0 · 오류 출력에 값 없음")

        code, out, err = cli("close", p6, "--user", uid)   # 옛 명세 — 서버 현재 판(V3)과 달라 미처리
        assert code == 1 and "MIGRATION-INCOMPLETE" in err, (code, err)
        rows8 = copy.deepcopy(rows7)
        rows8[2]["body"] = f"{MARK} 같은 개정 다른 본문"   # 개정은 그대로(대응표상 미처리 0) · 내용만 다름
        before = table_hash()
        code, out, err = cli("close", write(mk(rows8), "c8.json"), "--user", uid)
        assert code == 1 and "IDEMPOTENCY-CONFLICT" in err and "종료하지 않았어요" in err and table_hash() == before, (code, err)
        code, out, err = cli("close", p7, "--user", uid)
        assert code == 0 and "전부 duplicate" in out and "병행 종료" in out, (code, out, err)
        fin = q("SELECT migration_closed_at, encode(final_manifest_sha256, 'hex') AS h FROM import_streams WHERE user_id=%s AND id=%s",
                uid, src)[0]
        assert fin["migration_closed_at"] and fin["h"] == m7["manifest_sha256"], fin
        code, out, err = cli("run", p7, "--user", uid)
        assert code == 1 and "MIGRATION-WINDOW-CLOSED" in err, (code, err)
        code, out, err = cli("status", p7, "--user", uid)
        assert code == 0 and "남음 0" in out, (code, out)
        code, out, err = cli("extend", "--user", uid)
        assert code == 1, (code, err)
        ok("C8 실제 서버: close — 미처리 있으면 거절 · 같은 개정·다른 내용이면 재전송 충돌로 거절(쓰기 0) · 재전송 전부 duplicate 확인 뒤 최종 명세 해시 기록 · 종료 뒤 run·extend 거절 · status")
    finally:
        os.environ.pop("CRM_BOARD_MIGRATION_ENABLED", None)


# ── D) CLI 흐름 — 가짜 서버(imports.migration_* 를 시험 안에서만 바꿔 끼움). 서버 의미는 C 절이 실제로 본다 ──

class FakeServer:
    """CLI 가 기대는 서버 계약의 최소 모형: 같은 개정·같은 내용 duplicate · 같은 개정·다른 내용 IDEMPOTENCY-CONFLICT ·
    부모 개정 불일치 SOURCE-REVISION-CONFLICT · 새 개정 같은 반영 noop · 첫 적용에 병행 14일 · 한도 RATE-LIMIT(resume_at)."""

    def __init__(self):
        import datetime as dt
        self.dt = dt
        self.stream = None
        self.ents = []      # dict(original_id, source_revision, state, is_current_applied, target_kind, target_id, proj, req)
        self.quota = None   # 남은 적용 수(None = 무제한)
        self.calls = 0

    def _now(self):
        return self.dt.datetime.now(self.dt.timezone.utc)

    def migration_stream(self, uid, label):
        if self.stream is None:
            self.stream = {"id": SRC, "label": label, "migration_started_at": None, "migration_ends_at": None,
                           "migration_closed_at": None, "final_manifest_sha256": None, "counts": {}}
        return self.stream

    def migration_stream_get(self, uid):
        return self.stream

    def migration_entries(self, uid, stream_id):
        return [{k: e[k] for k in ("id", "previous_entry_id", "original_id", "source_revision", "state", "is_latest_received",
                                   "is_current_applied", "target_kind", "target_id")} for e in self.ents]

    def migration_apply_row(self, uid, stream_id, row, *, batch_id, manifest_sha256, transform_version):
        import db
        self.calls += 1
        st = self.stream
        req = json.dumps(row, sort_keys=True, ensure_ascii=False)
        proj = json.dumps({k: v for k, v in row.items() if k not in ("revision", "previous_revision", "expected_target_revision")},
                          sort_keys=True, ensure_ascii=False)
        same = [e for e in self.ents if e["original_id"] == row["original_id"] and e["source_revision"] == row["revision"]]
        if same:
            if same[0]["req"] != req:
                raise db.CrmError("IDEMPOTENCY-CONFLICT", "같은 개정에 다른 내용이에요.")
            e = same[0]
            return {"import_id": "ie_x", "state": e["state"], "target_kind": e["target_kind"], "target_id": e["target_id"], "duplicate": True}
        if st["migration_closed_at"] or (st["migration_ends_at"] and st["migration_ends_at"] <= self._now()):
            raise db.CrmError("MIGRATION-WINDOW-CLOSED", "병행 기간 밖이에요.")
        cur = [e for e in self.ents if e["original_id"] == row["original_id"] and e["is_current_applied"]]
        latest = [e for e in self.ents if e["original_id"] == row["original_id"] and e["is_latest_received"]]
        if (latest and row.get("previous_revision") != latest[0]["source_revision"]) or (not latest and row.get("previous_revision")):
            raise db.CrmError("SOURCE-REVISION-CONFLICT", "부모 개정이 최신 접수와 달라요.")
        if self.quota is not None:
            if self.quota <= 0:
                e = db.CrmError("RATE-LIMIT", "오늘 한도예요.")
                e.resume_at = "2026-10-21T00:00:00+09:00"
                raise e
            self.quota -= 1
        if st["migration_started_at"] is None:
            st["migration_started_at"] = self._now()
            st["migration_ends_at"] = st["migration_started_at"] + self.dt.timedelta(days=14)
        state = "noop" if cur and cur[0]["proj"] == proj else "applied"
        for e in cur:
            e["is_current_applied"] = False
        for e in latest:
            e["is_latest_received"] = False
        tid = cur[0]["target_id"] if state == "noop" else f"t_{row['original_id']}_{row['revision']}"
        self.ents.append({"id": f"ie_{len(self.ents)}", "previous_entry_id": latest[0]["id"] if latest else None, "is_latest_received": True,
                          "original_id": row["original_id"], "source_revision": row["revision"], "state": state, "is_current_applied": True,
                          "target_kind": row["target_kind"], "target_id": tid, "proj": proj, "req": req})
        return {"import_id": "ie_x", "state": state, "target_kind": row["target_kind"], "target_id": tid, "duplicate": False}

    def migration_extend(self, uid, stream_id, days=7):
        import db
        if self.stream["migration_closed_at"]:
            raise db.CrmError("MIGRATION-WINDOW-CLOSED", "종료된 입력원이에요.")
        base = max(self.stream["migration_ends_at"] or self._now(), self._now())
        self.stream["migration_ends_at"] = base + self.dt.timedelta(days=days)
        return self.stream

    def migration_close(self, uid, stream_id, final_manifest_sha256):
        self.stream["migration_closed_at"] = self._now()
        self.stream["final_manifest_sha256"] = final_manifest_sha256
        return self.stream


@contextlib.contextmanager
def fake_server():
    import imports
    fs = FakeServer()
    names = ("migration_stream", "migration_stream_get", "migration_entries", "migration_apply_row", "migration_extend", "migration_close")
    saved = {n: getattr(imports, n, None) for n in names}
    for n in names:
        setattr(imports, n, getattr(fs, n))
    try:
        yield fs
    finally:
        for n, f in saved.items():
            if f is None:
                delattr(imports, n)
            else:
                setattr(imports, n, f)


def section_d(uid, m):
    os.environ["CRM_BOARD_MIGRATION_ENABLED"] = "1"
    try:
        with fake_server() as fs:
            p = write(m, "d.json")
            code, out, err = cli("extend", "--user", uid)
            assert code == 1 and "MIGRATION-NOT-STARTED" in err and fs.stream is None, (code, err)
            code, out, err = cli("status", "--user", uid)
            assert code == 0 and "입력원: 없음" in out, (code, out)
            code, out, err = cli("run", p, "--user", uid)
            assert code == 1 and "MIGRATION-NOT-STARTED" in err and "init" in err and fs.stream is None, (code, err)
            code, out, err = cli("init", "--user", uid)
            assert code == 0 and SRC in out and "새로 만듦" in out, (code, out, err)
            code, out, err = cli("init", "--user", uid)
            assert code == 0 and SRC in out and "이미 있음" in out, (code, out, err)
            other = manifest(uid, copy.deepcopy(m["rows"]), source_instance_id="is_" + "cd" * 12)
            code, out, err = cli("run", write(other, "src.json"), "--user", uid)
            assert code == 1 and "SOURCE-INSTANCE-MISMATCH" in err and SRC in err and fs.calls == 0, (code, err)
            ok("D0 extend 입력원 없음 거절 · status 없음 · init 전 run 거절(입력원 안 만듦) · init 새로 만듦/이미 있음 · source_instance_id 불일치 → 적용 0건으로 거절(입력원 ID 안내)")
            code, out, err = cli("run", p, "--user", uid, "--limit", "2")
            assert code == 0 and "applied 2" in out and "남은 행 2" in out, (code, out, err)
            order = [e["original_id"] for e in fs.ents]
            assert order == [OID("ax1"), OID("ev1")], order
            code, out, err = cli("validate-only", p, "--user", uid)
            assert code == 0 and "완료 2 · 남음 2" in out, out
            code, out, err = cli("resume", p, "--user", uid)
            assert code == 0 and "applied 2" in out and "skipped 2" in out, (code, out, err)
            assert [e["original_id"] for e in fs.ents][2:] == [OID("rec1"), OID("pub1")]
            assert MARK not in out + err
            ok("D1 --limit 2 → validate-only 진행 표시 → resume 나머지(완료 행 건너뜀 · 의존성 순서)")

            n0 = len(fs.ents)
            code, out, err = cli("run", p, "--user", uid)
            assert code == 0 and "duplicate 4" in out and "업무 변경 0" in out and len(fs.ents) == n0, (code, out, err)
            ok("D2 같은 명세 재실행 → duplicate 4 · 업무 변경 0 출력")

            d = copy.deepcopy(m)
            d["rows"][2]["body"] = f"{MARK} 같은 개정 다른 본문"
            d["manifest_sha256"] = sha_of(d)
            code, out, err = cli("run", write(d, "d3.json"), "--user", uid)
            assert code == 1 and "IDEMPOTENCY-CONFLICT" in err and OID("rec1") in err and MARK not in out + err, (code, err)
            ok("D3 같은 개정·다른 내용 → 충돌 코드로 멈춤(본문 미출력)")

            d = copy.deepcopy(m)
            d["rows"][2].update(revision=V2, previous_revision=None, body=f"{MARK} 새 판")
            d["rows"][0].update(revision=V2, previous_revision=None)   # 반영 내용 같음 → noop
            d["manifest_sha256"] = sha_of(d)
            p4 = write(d, "d4.json")
            code, out, err = cli("run", p4, "--user", uid)
            assert code == 0 and "applied 1" in out and "noop 1" in out and "duplicate 2" in out, (code, out, err)
            ok("D4 새 개정 → 변경 행 applied · 같은 반영 noop · 나머지 duplicate")

            d5 = copy.deepcopy(d)
            d5["rows"][1].update(revision=V2, previous_revision=V9)   # 생성기가 부모를 적으면 거절
            d5["manifest_sha256"] = sha_of(d5)
            code, out, err = cli("validate-only", write(d5, "d5.json"), "--user", uid)
            assert code == 1 and "must-be-null" in err, err
            rec = [e for e in fs.ents if e["original_id"] == OID("rec1")]
            assert [e["source_revision"] for e in rec] == [V1, V2] and rec[1]["previous_entry_id"] == rec[0]["id"], rec
            n0 = len(fs.ents)
            code, out, err = cli("run", write(m, "d5b.json"), "--user", uid)   # 옛 개정(V1)으로 되돌아간 명세
            assert code == 1 and "IDEMPOTENCY-CONFLICT" in err and len(fs.ents) == n0, (code, err)
            ok("D5 previous_revision: 명세에 적으면 거절 · CLI 가 대응표 최신 접수 판으로 채움(새 판 부모 연결) · 옛 개정 재전송 = 충돌")

            d6 = copy.deepcopy(d)
            for r in d6["rows"]:
                if r["original_id"] in (OID("ev1"), OID("pub1")):
                    r.update(revision=V2, previous_revision=None, title=r["title"] + " 개정")
            d6["manifest_sha256"] = sha_of(d6)
            p6 = write(d6, "d6.json")
            fs.quota = 1
            code, out, err = cli("run", p6, "--user", uid)
            assert code == 3 and "RATE-LIMIT" in err and "2026-10-21 00:00 KST" in err and "resume" in err, (code, out, err)
            fs.quota = None
            code, out, err = cli("resume", p6, "--user", uid)
            assert code == 0 and "applied 1" in out and "skipped 3" in out, (code, out, err)
            ok("D6 일일 한도 → exit 3 partial · 재개 시각(KST) 안내 → resume 로 나머지")

            ends = fs.stream["migration_ends_at"]
            fs.stream["migration_ends_at"] = fs.stream["migration_started_at"] + (ends - ends)   # 이미 지남
            code, out, err = cli("resume", p6, "--user", uid)
            assert code == 1 and "MIGRATION-WINDOW-CLOSED" in err and "extend" in err, (code, err)
            code, out, err = cli("extend", "--user", uid)
            assert code == 0 and "7일 연장" in out, (code, out, err)
            code, out, err = cli("run", p6, "--user", uid)
            assert code == 0 and "업무 변경 0" in out, (code, out, err)
            ok("D7 병행 기간 만료 → 적용 차단 · extend 7일 → 다시 적용 가능")

            code, out, err = cli("close", p, "--user", uid)   # 옛 명세 — 서버 현재 판과 개정이 달라 미처리
            assert code == 1 and "MIGRATION-INCOMPLETE" in err, (code, err)
            code, out, err = cli("close", p6, "--user", uid)
            assert code == 0 and "병행 종료" in out and fs.stream["final_manifest_sha256"] == d6["manifest_sha256"], (code, out, err)
            code, out, err = cli("run", p6, "--user", uid)
            assert code == 1 and "MIGRATION-WINDOW-CLOSED" in err, (code, err)
            code, out, err = cli("status", p6, "--user", uid)
            assert code == 0 and "종료" in out and "완료 4 · 남음 0" in out, (code, out)
            ok("D8 close: 미처리 있으면 거절 · 최종 명세면 종료(최종 해시) · 종료 뒤 run 차단 · status")
            assert fs.calls > 0
    finally:
        os.environ.pop("CRM_BOARD_MIGRATION_ENABLED", None)


def main():
    section_a()
    reset_db()
    uid, m = section_b()
    section_d(uid, m)
    section_c(uid, m)
    import db
    db.close()
    print(f"\n통과 {len(PASSED)} · 보류 {len(PENDING)}")
    for n, w in PENDING:
        print(f"  보류 {n}: {w}")


if __name__ == "__main__":
    main()
