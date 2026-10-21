"""웹 시험 — board 이전 화면(S3): 연결 장치 · 승인 대기 · 「나」 집계 · 활동/사람/출처/축 상세 (2026-10-20).

실행: DATABASE_URL=postgresql://localhost/crm_test_bi_web python3 server/test_web_import.py
가드: server/.env 를 읽지 않는다(운영 DB 폴백 사고 방지) — DATABASE_URL 을 명시해야 하고, 호스트는 로컬, DB 이름은 crm_test 로 시작해야 한다.

A. 웹 층(서버 함수는 가짜로 바꿔 끼움 — 서버 코어 구현과 무관): 401 · Origin 403 · JSON 모양 400 · 표시 이름 관문 403 · NOT-FOUND 404 ·
   no-store · 인자 전달(기간·상태·커서·limit 상한) · /api/state 활동 부분 실패 격리 · 감사 라벨 7종 · /app 정적(인라인 스크립트 1개 ·
   「연결 장치」 메뉴가 사용자 전용 구간 밖 · 원문은 127.0.0.1 링크만, fetch 없음 · 토큰을 저장하지 않음).
B. 실제 서버 함수(activity.me_aggregate)로 합성 활동 집계 손계산 대조 — 서버 코어가 아직 스텁(NotImplementedError)이면 건너뛴다고 출력한다.
"""
import asyncio
import datetime as dt
import hashlib
import os
import re
import secrets
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
URL = os.environ.get("DATABASE_URL", "")
if not URL:
    raise SystemExit("중단: DATABASE_URL 을 명시하세요(예: postgresql://localhost/crm_test_bi_web). server/.env 는 읽지 않아요.")
_u = urllib.parse.urlsplit(URL)
if _u.hostname not in ("localhost", "127.0.0.1", "::1", None) or not _u.path.lstrip("/").startswith("crm_test"):
    raise SystemExit("중단: 로컬 시험 DB(이름 crm_test*)만 허용해요.")
PUBLIC = "http://127.0.0.1:8800"
os.environ["CRM_DEV"] = "1"
os.environ["CRM_DEV_USER"] = "wi-main@example.com"
os.environ["AUTHKIT_ISSUER"] = "https://fake.authkit.test"
os.environ["CRM_PUBLIC_URL"] = PUBLIC
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

import activity  # noqa: E402
import db  # noqa: E402
import imports  # noqa: E402
import main  # noqa: E402
import web  # noqa: E402

MAIN, NONAME, INBOX = "wi-main@example.com", "wi-noname@example.com", "wi-inbox@example.com"
EMAILS = (MAIN, NONAME, "wi-agg@example.com", INBOX)
H = {"origin": PUBLIC}


def cleanup():
    """test_tools.cleanup 과 같은 길 — 감사 행 · db.user_delete(계정 삭제 순서 DELETE_TABLES)."""
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(EMAILS),)).fetchall()]
        c.execute("DELETE FROM admin_audit WHERE actor_id = ANY(%s) OR target_id = ANY(%s)", (ids, ids))
    for uid in ids:
        db.user_delete(uid)


class Fake:
    """imports·activity 함수 바꿔 끼우기 — 호출 인자를 남기고 정해 둔 값을 돌려준다. 끝나면 되돌린다."""
    def __init__(self):
        self.calls, self.saved = [], []

    def put(self, mod, name, ret=None, exc=None):
        self.saved.append((mod, name, getattr(mod, name)))

        def f(*a, **k):
            self.calls.append((name, a, k))
            if exc is not None:
                raise exc
            return ret(*a) if callable(ret) else ret
        setattr(mod, name, f)

    def last(self, name):
        return next(c for c in reversed(self.calls) if c[0] == name)

    def restore(self):
        for mod, name, fn in reversed(self.saved):
            setattr(mod, name, fn)


async def test_web_layer():
    site = main.site
    real = site.dev, site.dev_user
    uid = db.user_by_email(MAIN)["id"]
    db.display_name_set(uid, "웹시험")
    nn = db.user_by_email(NONAME)["id"]
    fk = Fake()
    dev = {"id": "is_x", "label": "집 노트북", "created_at": "2026-10-20T01:00:00+00:00", "token_expires_at": "2027-01-18T01:00:00+00:00",
           "last_used_at": None, "revoked_at": None, "token_revoked_at": None, "policy": imports.DEFAULT_POLICY, "policy_revision": 1,
           "hmac_namespace": "hn_x", "active": True}
    fk.put(imports, "device_list", {"devices": [dev], "active": 1, "max": 5, "switches": {"imports": False, "activity": False,
                                                                                         "unattended_summary": False, "inbox_approvals": False}})
    fk.put(imports, "device_connect", lambda u, label: {"device": {**dev, "label": label}, "token": "imp_is_x.it_y.SECRET"})
    fk.put(imports, "device_reissue", {"device": dev, "token": "imp_is_x.it_z.SECRET2"})
    fk.put(imports, "device_revoke", {"device": {**dev, "active": False}})
    fk.put(imports, "device_policy_set", {"device": dev})
    fk.put(imports, "inbox_list", {"entries": [], "next_cursor": None, "counts": {"pending": 0}})
    fk.put(imports, "inbox_get", exc=db.CrmError("NOT-FOUND", "없어요."))
    fk.put(imports, "inbox_approve", exc=db.CrmError("SWITCH-OFF", "꺼져 있어요."))
    fk.put(imports, "inbox_exclude", {"import_id": "ie_1", "state": "excluded"})
    fk.put(activity, "me_aggregate", lambda u, p: {"period": p, "total": 0})
    fk.put(activity, "activity_list", {"activities": [], "next_cursor": None})
    fk.put(activity, "activity_get", exc=db.CrmError("NOT-FOUND", "없어요."))
    fk.put(activity, "person_context", {"person": {}, "activities": [], "activity_count": 0, "records": [], "items": []})
    fk.put(activity, "source_context", {"source": {}, "activity_count": 0, "by_action": [], "activities": []})
    fk.put(activity, "axis_context", {"axis": {}, "activity_count": 0, "activities": [], "records": [], "items": []})
    fk.put(activity, "state_part", exc=NotImplementedError())
    gets = ["/api/devices", "/api/inbox", "/api/inbox/ie_1", "/api/me/aggregate", "/api/activities", "/api/activities/a_1",
            "/api/people/p_1/context", "/api/sources/s_1/context", "/api/axes/i_1/context"]
    posts = {"/api/devices": {"label": "집"}, "/api/devices/is_x/reissue": {}, "/api/devices/is_x/revoke": {},
             "/api/devices/is_x/policy": {"policy": {"actions": ["like"]}, "expected_revision": 1},
             "/api/inbox/ie_1/approve": {}, "/api/inbox/ie_1/exclude": {}}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            # 1) 세션 없음 → 401(읽기·쓰기 모두). 서버 함수는 불리지 않는다
            site.dev, site.dev_user = False, ""
            for p in gets:
                assert (await h.get(p)).status_code == 401, p
            for p, b in posts.items():
                assert (await h.post(p, json=b, headers=H)).status_code == 401, p
            assert not fk.calls, fk.calls
            site.dev, site.dev_user = True, MAIN
            # 2) Origin 없음·다른 사이트 → 403 ORIGIN
            for p, b in posts.items():
                for hd in ({}, {"origin": "https://evil.example"}):
                    r = await h.post(p, json=b, headers=hd)
                    assert r.status_code == 403 and r.json()["code"] == "ORIGIN", (p, hd, r.status_code)
            assert not fk.calls, fk.calls
            # 3) JSON 모양 → 400 ARG-INVALID(서버 함수 전)
            for p, b in [("/api/devices", {}), ("/api/devices", {"label": 3}), ("/api/devices/is_x/policy", {"policy": [], "expected_revision": 1}),
                         ("/api/devices/is_x/policy", {"policy": {}, "expected_revision": "1"}), ("/api/devices/is_x/policy", {"policy": {}, "expected_revision": True})]:
                r = await h.post(p, json=b, headers=H)
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (p, b, r.status_code)
            r = await h.post("/api/devices", content=b"not json", headers={**H, "content-type": "application/json"})
            assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID"
            assert not fk.calls, fk.calls
            # 4) 쓰기 성공 — 대상 사용자 = 세션 uid, 토큰 응답은 no-store
            r = await h.post("/api/devices", json={"label": "집 노트북"}, headers=H)
            assert r.status_code == 200 and r.json()["token"].startswith("imp_") and r.headers["cache-control"] == "no-store", r.text
            assert fk.last("device_connect")[1] == (uid, "집 노트북")
            r = await h.post("/api/devices/is_x/reissue", json={}, headers=H)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and fk.last("device_reissue")[1] == (uid, "is_x")
            assert (await h.post("/api/devices/is_x/revoke", json={}, headers=H)).status_code == 200 and fk.last("device_revoke")[1] == (uid, "is_x")
            pol = {"actions": ["like"], "transform_versions": ["board-likes/1"], "axes": []}
            n0 = len(fk.calls)   # policy JSON 상한 4 KiB — write_gate·서버 함수 전에 400
            r = await h.post("/api/devices/is_x/policy", json={"policy": {"actions": ["x" * 5000]}, "expected_revision": 1}, headers=H)
            assert r.status_code == 400 and r.json()["code"] == "PAYLOAD-LIMIT" and len(fk.calls) == n0, r.text
            big = {"actions": ["like"], "transform_versions": [], "axes": ["i_" + "a" * 20] * 150}   # 4 KiB 근처 경계 아래는 통과(서버 검증은 가짜)
            assert len(__import__("json").dumps(big, ensure_ascii=False, separators=(",", ":")).encode()) <= web.POLICY_MAX_BYTES
            assert (await h.post("/api/devices/is_x/policy", json={"policy": big, "expected_revision": 1}, headers=H)).status_code == 200
            assert (await h.post("/api/devices/is_x/policy", json={"policy": pol, "expected_revision": 4}, headers=H)).status_code == 200
            assert fk.last("device_policy_set")[1] == (uid, "is_x", pol, 4)
            assert (await h.post("/api/inbox/ie_1/exclude", json={}, headers=H)).json()["state"] == "excluded"
            assert fk.last("inbox_exclude")[1] == (uid, "ie_1")
            # 5) 서버 오류 코드 → NOT-FOUND 404 · 그 밖 400(문구 그대로, 본문 없음)
            r = await h.post("/api/inbox/ie_1/approve", json={}, headers=H)
            assert r.status_code == 400 and r.json()["code"] == "SWITCH-OFF", r.text
            for p in ("/api/inbox/ie_1", "/api/activities/a_1"):
                r = await h.get(p)
                assert r.status_code == 404 and r.json()["code"] == "NOT-FOUND", (p, r.status_code)
            # 6) 표시 이름 관문 — 이름 없는 사용자의 쓰기는 403 NAME-REQUIRED(서버 함수 전). 읽기는 된다
            n0 = len(fk.calls)
            site.dev_user = NONAME
            for p, b in posts.items():
                r = await h.post(p, json=b, headers=H)
                assert r.status_code == 403 and r.json()["code"] == "NAME-REQUIRED", (p, r.status_code)
            assert len(fk.calls) == n0, "관문에서 막혀 서버 함수가 불리지 않는다"
            assert (await h.get("/api/devices")).status_code == 200
            site.dev_user = MAIN
            # 7) 읽기 인자 전달 · no-store · 선택지(서버 상수)
            r = await h.get("/api/devices")
            j = r.json()
            assert r.headers["cache-control"] == "no-store" and fk.last("device_list")[1] == (uid,)
            assert [x["key"] for x in j["choices"]["actions"]] == list(imports.ACTIONS)
            assert [x["key"] for x in j["choices"]["transforms"]] == list(imports.TRANSFORMS)
            assert all(x["key"] in web.TRANSFORM_LABELS for x in j["choices"]["transforms"]), "변환 이름 표시가 빠졌다"
            assert j["choices"]["axis_object_id"] == db.classify_target(uid) and j["choices"]["token_days"] == imports.TOKEN_DAYS
            await h.get("/api/inbox?state=applied&cursor=c1&limit=999")
            assert fk.last("inbox_list")[1] == (uid, "applied", "c1", 100), "limit 상한 100"
            await h.get("/api/inbox?limit=x")
            assert fk.last("inbox_list")[1] == (uid, "pending", "", 50), "기본값"
            await h.get("/api/me/aggregate?period=7d")
            assert fk.last("me_aggregate")[1] == (uid, "7d")
            await h.get("/api/me/aggregate")
            assert fk.last("me_aggregate")[1] == (uid, activity.DEFAULT_PERIOD)
            await h.get("/api/activities?action=like&person=p_1&source=s_1&axis=i_1&dated=no&cursor=c2&limit=0")
            assert fk.last("activity_list")[1] == (uid, "like", "p_1", "s_1", "i_1", "no", "c2", 1), fk.last("activity_list")
            for p, name, arg in (("/api/people/p_1/context", "person_context", "p_1"), ("/api/sources/s_1/context", "source_context", "s_1"),
                                 ("/api/axes/i_1/context", "axis_context", "i_1")):
                r = await h.get(p)
                assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and fk.last(name)[1] == (uid, arg), p
            # 8) /api/state — 활동 부분이 실패해도 나머지는 200, 그 칸만 read.activity. 실패는 사용 집계(web:state_activity)에 코드만
            db.usage_flush()
            def urow(code):
                with db.tx() as c:
                    r_ = c.execute("SELECT COALESCE(sum(n),0) AS n FROM usage_daily WHERE user_id=%s AND name='web:state_activity' AND code=%s",
                                   (uid, code)).fetchone()
                return r_["n"]
            e0, c0 = urow("EXC"), urow("RATE-LIMIT")
            r = await h.get("/api/state")
            s = r.json()
            assert r.status_code == 200 and s["read"]["activity"]["ok"] is False and s["activity"] is None and s["inbox"] is None, s.get("read")
            assert s["read"]["activity"]["error"] == {"code": "EXC", "msg": ""} and "journal" in s, "예외는 EXC — 자유 텍스트 없음"
            fk.put(activity, "state_part", exc=db.CrmError("RATE-LIMIT", "자유 문구 <b>x</b>"))
            s = (await h.get("/api/state")).json()
            assert s["read"]["activity"]["error"] == {"code": "RATE-LIMIT", "msg": ""}, s["read"]
            fk.put(activity, "state_part", exc=db.CrmError("bad code 문구", "x"))
            assert (await h.get("/api/state")).json()["read"]["activity"]["error"]["code"] == "EXC", "열거형이 아닌 코드는 EXC"
            db.usage_flush()
            assert urow("EXC") == e0 + 2 and urow("RATE-LIMIT") == c0 + 1, (urow("EXC"), urow("RATE-LIMIT"))
            assert s["activity_ui"]["actions"] == activity.ACTION_LABELS and s["activity_ui"]["pending_days"] == imports.PENDING_DAYS
            fk.put(activity, "state_part", {"activity": {"total": 3}, "inbox": {"pending": 2}, "devices": {"active": 1, "max": 5}})
            s = (await h.get("/api/state")).json()
            assert s["activity"] == {"total": 3} and s["inbox"] == {"pending": 2} and s["devices"]["max"] == 5 and "activity" not in s.get("read", {})
            # 9) /app — 인라인 스크립트 1개 · 관리자 응답에도 「연결 장치」 메뉴가 남는다
            r = await h.get("/app")
            assert r.status_code == 200 and len(web.inline_script_hashes(r.text)) == 1 and 'id="acct-devices"' in r.text
            with db.tx() as c:
                c.execute("UPDATE users SET role='admin' WHERE id=%s", (uid,))
            try:
                r = await h.get("/app")
                assert 'id="acct-devices"' in r.text and 'data-acct="send"' not in r.text, "관리자: 사용자 전용 구간은 빠지고 연결 장치는 남는다"
            finally:
                with db.tx() as c:
                    c.execute("UPDATE users SET role='user' WHERE id=%s", (uid,))
    finally:
        fk.restore()
        site.dev, site.dev_user = real
    print("OK — 웹 층(401·Origin 403·모양 400·이름 관문 403·404·no-store·인자·limit 상한·선택지·/api/state 부분 실패 격리·/app 메뉴) 통과")


def test_static():
    html = (HERE / "app.html").read_text(encoding="utf-8")
    assert html.count("<script>") == 1 and len(web.inline_script_hashes(html)) == 1, "인라인 스크립트 1개"
    # 「연결 장치」 메뉴는 <!--user--> 구간 밖(관리자도 연결한다)
    for m in re.finditer(r"<!--user-->(.*?)<!--/user-->", html, re.S):
        assert "acct-devices" not in m.group(1)
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    # 원문: 127.0.0.1 은 링크 한 곳뿐이고 fetch 로 가져오지 않는다(구현계획 §3 — 브라우저→localhost fetch 제외)
    assert js.count("127.0.0.1") == 1 and re.search(r'href="http://127\.0\.0\.1:\$\{port\}/raw/\$\{id\}"', js), "원문 링크"
    assert 'rel="noopener noreferrer"' in js and "원문을 보관한 PC에서 열 수 있습니다" in js
    for call in re.findall(r"fetch\(([^,)]*)", js):
        assert "127.0.0.1" not in call and "RAW" not in call, call
    # 토큰: 저장소·주소·흘림 문구에 넣지 않는다. localStorage 에는 원문 포트만 쓴다
    assert re.findall(r"localStorage\.setItem\(([^,]+),", js) == ["RAW_PORT_KEY"], re.findall(r"localStorage\.setItem\(([^,]+),", js)
    assert "sessionStorage" not in js and not re.search(r"coreFlash\s*=\s*\{[^}]*token", js) and not re.search(r"location\.hash[^;\n]*token", js)
    assert "devToken = null; if (S)" in js, "화면을 떠나면 토큰을 지운다(hashchange)"
    # 「나」 홈 설명 문장(r3 T3) · AI 요약 승인 문구는 서버 문장(inbox_get.approve_notice) 그대로
    assert "연결된 활동과 내가 쓴 글을 정해진 계산 규칙으로 보여 줍니다. 활동량을 성격이나 능력으로 해석하지 않습니다. AI가 작성한 설명은 저장 승인 대기에 표시합니다." in js
    assert "e.approve_notice" in js and "처음 수집한 날" in js and "날짜 미확인" in js
    # AI 생성 정보 — 계약 열거값은 사람 말로(원값 노출 금지), 모르는 값은 「기타」 + 원값
    assert 'method: {ai_summary: "AI 요약"' in js and 'input_scope: {self_only: "내가 쓴 글만"}' in js and "기타 <span" in js
    # 감사 라벨 — schema.sql admin_audit CHECK 의 모든 action 에 표시 이름
    sql = (HERE / "schema.sql").read_text(encoding="utf-8")
    blk = re.search(r"CREATE TABLE IF NOT EXISTS admin_audit \((.*?)\n\);", sql, re.S).group(1)
    acts = set(re.findall(r"'([a-z_]+)'", re.search(r"action\s+text NOT NULL CHECK \(action IN \((.*?)\)\)", blk, re.S).group(1)))
    assert acts and acts <= set(web.ADMIN_LABELS), sorted(acts - set(web.ADMIN_LABELS))
    # 코어 정의: 숨긴 열(활동의 입력원 stream_id)은 열·FK 관계 둘 다 빠진다 — CORE_OBJECTS 에 활동을 넣은 꼴로 확인
    saved = web.CORE_OBJECTS
    try:
        if not any(t == "activities" for t, _ in web.CORE_OBJECTS):
            web.CORE_OBJECTS = saved + (("activities", "활동"),)
        a = next(x for x in web.core_schema(web.SCHEMA_SQL.read_text(encoding="utf-8")) if x["table"] == "activities")
        assert "stream_id" not in {f["name"] for f in a["fields"]} and {"occurred_on", "observed_on", "raw_id"} <= {f["name"] for f in a["fields"]}
        assert [ln["target"] for ln in a["links"]] == ["sources"], a["links"]
    finally:
        web.CORE_OBJECTS = saved
    print("OK — 정적(인라인 1개·메뉴 위치·원문 링크만·토큰 미저장·설명 문장·감사 라벨·활동 코어 정의 숨긴 열) 통과")


# ── B. 합성 활동 집계 손계산 대조(실제 activity.me_aggregate) ──────────────
def _kst_today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()


def seed_aggregate(uid):
    """합성 자료 — 오늘 T(KST). 기대값은 test_aggregate 의 표. 반환 {이름: id}."""
    T = _kst_today()
    ids = {}
    with db.tx() as c:
        ax_obj = db.classify_target(uid)
        assert ax_obj, "신규 계정 표준 시드에 분류(축) 오브젝트가 있다"
        c.execute("INSERT INTO import_streams (user_id,id,channel,label,token_id,secret_hash,token_expires_at) "
                  "VALUES (%s,'is_agg','device','합성 장치',%s,%s,now()+interval '90 days')", (uid, "it_agg_" + uid[-8:], hashlib.sha256(b"x").digest()))
        for k, prov in (("S1", "sns"), ("S2", "web")):
            ids[k] = f"s_{k.lower()}_{uid[-6:]}"
            c.execute("INSERT INTO sources (user_id,id,kind,name,location,activity_provider) VALUES (%s,%s,'other',%s,'',%s)",
                      (uid, ids[k], f"합성 {prov}", prov))
        for k in ("P1", "P2", "P3"):
            ids[k] = f"p_{k.lower()}"
            c.execute("INSERT INTO people (user_id,id,alias) VALUES (%s,%s,%s)", (uid, ids[k], f"사람-{k}"))
        for k in ("X1", "X2"):
            ids[k] = f"i_{k.lower()}"
            c.execute("INSERT INTO items (user_id,id,object_id,schema_revision,title,revision) VALUES (%s,%s,%s,1,%s,1)", (uid, ids[k], ax_obj, f"축 {k}"))
        # (이름, 행동, 행동 날짜 차(None=미확인), 처음 수집 차, 출처, 사람[(id,역할)], 축, 상태, 지움, 현재 판)
        rows = [("A1", "like", 0, 0, "S1", [("P1", "author")], ["X1"], "active", False, True),
                ("A2", "like", 3, 3, "S1", [("P1", "author"), ("P2", "participant")], ["X1", "X2"], "active", False, True),
                ("A3", "comment", 10, 10, "S2", [("P2", "counterparty")], [], "active", False, True),
                ("A4", "read", 40, 40, "S1", [("P1", "author")], [], "active", False, True),
                ("A5", "like", None, 1, "S1", [("P3", "author")], [], "active", False, True),
                ("A6", "like", None, 1, "S1", [], [], "active", False, True),
                ("A7", "repost", 2, 2, "S1", [("P3", "author")], [], "retracted", False, True),
                ("A8", "like", 1, 1, "S1", [("P3", "author")], [], "active", True, True),
                ("A9old", "like", 5, 5, "S1", [("P3", "author")], [], "active", False, False),
                ("A9", "like", 5, 5, "S1", [("P2", "author")], [], "active", False, True),
                ("A10", "save", 100, 100, "S1", [], [], "active", False, True),
                ("A11", "like", 29, 29, "S1", [], [], "active", False, True),
                ("A12", "like", 30, 30, "S1", [], [], "active", False, True),
                ("A13", "read", None, None, "S1", [], [], "active", False, True)]
        n = 0
        for name, act, occ, obs, src, ppl, axes, state, deleted, cur in rows:
            aid = ids[name] = f"a_{name.lower()}"
            c.execute("INSERT INTO activities (user_id,id,stream_id,source_id,action,state,occurred_on,observed_on,raw_id,deleted_at) "
                      "VALUES (%s,%s,'is_agg',%s,%s,%s,%s,%s,%s,%s)",
                      (uid, aid, ids[src], act, state, None if occ is None else T - dt.timedelta(days=occ),
                       None if obs is None else T - dt.timedelta(days=obs), secrets.token_hex(24), dt.datetime.now(dt.timezone.utc) if deleted else None))
            for pk, role in ppl:
                n += 1
                c.execute("INSERT INTO activity_links (user_id,id,activity_id,person_id,relation) VALUES (%s,%s,%s,%s,%s)", (uid, f"al_{n}", aid, ids[pk], role))
            for xk in axes:
                n += 1
                c.execute("INSERT INTO activity_links (user_id,id,activity_id,axis_item_id,relation) VALUES (%s,%s,%s,%s,'axis')", (uid, f"al_{n}", aid, ids[xk]))
        # 접수 대응표 — 원본 하나에 판 하나(A9 만 두 판: 옛 판 A9old 는 현재가 아니다)
        prev = None
        for name, *_r in rows:
            cur = _r[-1]
            orig = "o_a9" if name in ("A9old", "A9") else f"o_{name.lower()}"
            eid = f"ie_{name.lower()}"
            c.execute("INSERT INTO import_entries (user_id,id,stream_id,original_id,source_revision,previous_entry_id,kind,request_sha256,projection_sha256,"
                      "state,is_latest_received,is_current_applied,resolved_at,resolved_by,target_activity_id,transform_version) "
                      "VALUES (%s,%s,'is_agg',%s,%s,%s,'activity',%s,%s,'applied',%s,%s,now(),'system',%s,'board-likes/1')",
                      (uid, eid, orig, f"r_{name.lower()}", prev if name == "A9" else None, hashlib.sha256(eid.encode()).digest(),
                       hashlib.sha256(eid.encode()).digest(), cur, cur, ids[name]))
            if name == "A9old":
                prev = eid
    return ids, T


def test_aggregate():
    uid = db.user_by_email("wi-agg@example.com")["id"]
    db.display_name_set(uid, "집계시험")
    try:
        activity.me_aggregate(uid, "30d")
    except NotImplementedError:
        print("건너뜀 — activity.me_aggregate 가 아직 스텁(서버 코어 구현 뒤 손계산 대조를 돌린다)")
        return False
    except db.CrmError:
        pass
    ids, T = seed_aggregate(uid)
    iso = lambda d: (T - dt.timedelta(days=d)).isoformat()   # noqa: E731
    # 손계산 — 대상 = 현재 판 · active · 지우지 않음 · occurred_on 이 기간 안(30d = T-29..T).
    # 30d: A1 A2 A3 A9 A11 = 5 (A4 40일 · A12 30일 · A10 100일 밖, A5 A6 A13 미확인, A7 철회, A8 지움, A9old 옛 판)
    g = activity.me_aggregate(uid, "30d")
    assert g["period"] == "30d" and g["to"] == T.isoformat() and g["from"] == iso(29), (g["from"], g["to"])
    assert g["total"] == 5, g["total"]
    assert [(x["action"], x["n"]) for x in g["by_action"]] == [("like", 4), ("comment", 1)], g["by_action"]
    assert {x["item_id"]: x["n"] for x in g["by_axis"]} == {ids["X1"]: 2, ids["X2"]: 1}, g["by_axis"]
    assert g["axis_unclassified"] == 3 and g["axis_sum_differs"] is True, (g["axis_unclassified"], g["axis_sum_differs"])   # A3 A9 A11 / 2+1+3 ≠ 5
    # 자주 반응한 사람: like·repost·comment·reply × author·counterparty. P1 = A1 A2 · P2 = A3(상대) A9(글쓴이) — A2 의 P2 는 참여자라 빼고 · P3 = 0(A5 미확인 · A7 철회 · A8 지움 · A9old 옛 판)
    assert [(x["person_id"], x["n"]) for x in g["top_people"]] == sorted([(ids["P1"], 2), (ids["P2"], 2)], key=lambda t: (-t[1], t[0])), g["top_people"]
    assert {x["source_id"]: x["n"] for x in g["by_source"]} == {ids["S1"]: 4, ids["S2"]: 1}, g["by_source"]
    # 날짜 미확인 — 기간과 무관: A5 A6(처음 수집 T-1) · A13(처음 수집 없음) = 3
    assert g["undated"]["n"] == 3, g["undated"]
    assert {x["observed_on"]: x["n"] for x in g["undated"]["observed"]} == {iso(1): 2, None: 1}, g["undated"]["observed"]
    assert g["notice"] == "연결된 활동과 내가 쓴 글을 정해진 계산 규칙으로 보여 줍니다. 활동량을 성격이나 능력으로 해석하지 않습니다. AI가 작성한 설명은 저장 승인 대기에 표시합니다."
    g7 = activity.me_aggregate(uid, "7d")   # A1 A2 A9 = 3
    assert g7["total"] == 3 and g7["from"] == iso(6) and [(x["action"], x["n"]) for x in g7["by_action"]] == [("like", 3)], g7
    g90 = activity.me_aggregate(uid, "90d")   # + A3 A4 A11 A12 = 7
    assert g90["total"] == 7 and dict((x["action"], x["n"]) for x in g90["by_action"]) == {"like": 5, "comment": 1, "read": 1}, g90["by_action"]
    ga = activity.me_aggregate(uid, "all")   # + A10 = 8
    assert ga["total"] == 8 and ga["from"] is None and ga["undated"]["n"] == 3, ga
    try:
        activity.me_aggregate(uid, "1y")
        raise AssertionError("기간 밖은 ARG-INVALID")
    except db.CrmError as e:
        assert e.code == "ARG-INVALID"
    print("OK — 「나」 집계 손계산(30d 5 · 7d 3 · 90d 7 · 전체 8 · 행동별 · 축별·미분류·합계 다름 · 사람(참여자·옛 판·철회·지움 제외) · 출처 · 날짜 미확인 3·처음 수집 분포 · 문장) 통과")
    return True


def add_pending(uid, eid, kind, payload):
    """승인 대기 행 하나(합성 — 장치 접수와 같은 모양의 payload). 입력원은 seed_aggregate 의 is_agg."""
    from psycopg.types.json import Jsonb
    with db.tx() as c:
        c.execute("INSERT INTO import_entries (user_id,id,stream_id,original_id,source_revision,kind,request_sha256,projection_sha256,state,"
                  "payload,payload_bytes,expires_at,transform_version,execution) VALUES (%s,%s,'is_agg',%s,'r1',%s,%s,%s,'pending',%s,%s,"
                  "now()+interval '30 days',%s,%s)", (uid, eid, "o_" + eid, kind, hashlib.sha256(eid.encode()).digest(),
                                                       hashlib.sha256(eid.encode()).digest(), Jsonb(payload), len(str(payload).encode()),
                                                       payload["transform_version"], payload["execution"]))


async def test_approve():
    """승인 성공 경로 — 실제 imports.inbox_approve, 스위치(이 프로세스 env 만) 켬. 내 글 = self/verbatim + 활동 반응 연결 ·
    AI 요약 = unknown/generated·ai + 생성 정보 · 이중 클릭(동시 두 번) = 기록 1건 · 스위치 꺼짐 = APPROVALS-OFF 로 대기 그대로."""
    site = main.site
    real = site.dev, site.dev_user
    uid = db.user_by_email(INBOX)["id"]
    db.display_name_set(uid, "승인시험")
    ids, _T = seed_aggregate(uid)
    add_pending(uid, "ie_t1", "self_text", {"title": "메모", "body": "내가 쓴 메모 한 줄.", "source_created_at": None, "source_date": None,
                                            "execution": "attended", "transform_version": "self-text/1", "activity_ref": ids["A1"]})
    add_pending(uid, "ie_s1", "self_summary", {"title": "요약", "body": "AI 가 만든 요약.", "source_created_at": None, "source_date": None,
                                               "execution": "unattended", "transform_version": "self-summary/1",
                                               "generation": {"method": "ai_summary", "tool": "t", "model": "m", "input_scope": "self_only",
                                                              "inputs": ["a", "b"]}})
    saved = {k: os.environ.get(k) for k in ("CRM_INBOX_APPROVALS_ENABLED", "CRM_IMPORTS_ENABLED")}
    try:
        site.dev, site.dev_user = True, INBOX
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            os.environ.pop("CRM_INBOX_APPROVALS_ENABLED", None)
            r = await h.post("/api/inbox/ie_t1/approve", json={}, headers=H)
            assert r.status_code == 400 and r.json()["code"] == "APPROVALS-OFF", r.text
            assert (await h.get("/api/inbox/ie_t1")).json()["state"] == "pending", "꺼짐이면 대기 그대로"
            os.environ["CRM_INBOX_APPROVALS_ENABLED"] = os.environ["CRM_IMPORTS_ENABLED"] = "1"
            g = (await h.get("/api/inbox/ie_s1")).json()
            assert g["approve_notice"] == imports.AI_APPROVE_NOTICE and g["body"] and g["approvable"] and g["generation"]["inputs"] == 2
            assert (await h.get("/api/inbox/ie_t1")).json()["approve_notice"] is None
            # 내 글 승인
            r = await h.post("/api/inbox/ie_t1/approve", json={}, headers=H)
            assert r.status_code == 200 and r.json()["state"] == "applied", r.text
            rid = r.json()["record_id"]
            with db.tx() as c:
                rec = c.execute("SELECT author_kind,text_process,ingest_kind,generation_kind,body FROM records WHERE user_id=%s AND id=%s",
                                (uid, rid)).fetchone()
            assert (rec["author_kind"], rec["text_process"], rec["ingest_kind"], rec["generation_kind"]) == ("self", "verbatim", "inbox", "unknown"), rec
            assert rec["body"] == "내가 쓴 메모 한 줄."
            a = (await h.get(f"/api/activities/{ids['A1']}")).json()
            assert [m["record_id"] for m in a["memos"]] == [rid], ("활동에 승인한 메모가 붙는다", a["memos"])
            e = (await h.get("/api/inbox/ie_t1")).json()
            assert e["state"] == "applied" and e["body"] is None and e["target_record_id"] == rid and not e["approvable"], "대기 본문 삭제"
            # AI 요약 — 이중 클릭(동시 두 번): 기록 1건, 한쪽은 거절
            rs = await asyncio.gather(h.post("/api/inbox/ie_s1/approve", json={}, headers=H), h.post("/api/inbox/ie_s1/approve", json={}, headers=H))
            codes = sorted(x.status_code for x in rs)
            assert codes[0] == 200 and codes[1] in (400, 404), [(x.status_code, x.text) for x in rs]
            with db.tx() as c:
                rows = c.execute("SELECT author_kind,text_process,generation_kind,generation_meta FROM records WHERE user_id=%s AND ingest_kind='inbox' "
                                 "AND generation_kind='ai'", (uid,)).fetchall()
            assert len(rows) == 1 and (rows[0]["author_kind"], rows[0]["text_process"]) == ("unknown", "generated"), rows
            assert rows[0]["generation_meta"]["model"] == "m" and rows[0]["generation_meta"]["reported_by"] == "device"
            # 처리된 것은 다시 승인·제외 못 함 · 남은 대기 0
            assert (await h.post("/api/inbox/ie_s1/exclude", json={}, headers=H)).status_code in (400, 404)
            assert (await h.get("/api/state")).json()["inbox"] == {"pending": 0}
            assert {x["id"] for x in (await h.get("/api/inbox?state=applied")).json()["entries"]} == {"ie_t1", "ie_s1"}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        site.dev, site.dev_user = real
    print("OK — 승인(실제 함수: 꺼짐 APPROVALS-OFF · 내 글 self/verbatim·활동 메모 연결 · AI 요약 unknown/generated·ai·승인 문구 · 이중 클릭 기록 1건 · 대기 본문 삭제) 통과")


async def run():
    db.init_schema()
    cleanup()
    try:
        test_static()
        await test_web_layer()
        test_aggregate()
        await test_approve()
    finally:
        cleanup()
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
