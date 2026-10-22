"""웹 시험 — PR① 「나」 홈(기존 데이터 화면): 기록 이어 받기 읽기 API GET /api/records · 홈 화면 정적 검사 (2026-10-21).

실행: DATABASE_URL=postgresql://localhost/crm_test_ux python3 server/test_web_home.py
가드: server/.env 를 읽지 않는다(운영 DB 폴백 사고 방지) — DATABASE_URL 을 명시해야 하고, 호스트는 로컬, DB 이름은 crm_test 로 시작해야 한다.

A. /api/records — 401 · no-store · 기본(before 없음) = /api/state journal 과 같은 50편 · next_before 커서로 겹침·빠짐 없이 이어 받기 ·
   created 동률 블록(한 tx 의 now())이 페이지 경계에 걸쳐도 겹침·누락 0 · limit 끝값 · 틀린 before 400 · 남의 기록 0 · db.records 기본 호출 결과 불변.
B. app.html 정적 — 「나」 상세(PR①: 빈 상태 문구·옛 링크 #_me/<출처> 이동·쓰기는 저장·동작 버튼 두 곳) +
   「나」 v9(PR ①-b S2: 탭 나·오늘·Setup · 하위 탭 7 = 레이아웃 순서 · 서버 접점 meSync 한 곳(GET 만) · 축 색 코드 순 8 + 회색 ·
   렌즈 3 + 커스텀·출처 목록/상세 · 추정 연결 0).
"""
import asyncio
import datetime as dt
import os
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
URL = os.environ.get("DATABASE_URL", "")
if not URL:
    raise SystemExit("중단: DATABASE_URL 을 명시하세요(예: postgresql://localhost/crm_test_ux). server/.env 는 읽지 않아요.")
_u = urllib.parse.urlsplit(URL)
if _u.hostname not in ("localhost", "127.0.0.1", "::1", None) or not _u.path.lstrip("/").startswith("crm_test"):
    raise SystemExit("중단: 로컬 시험 DB(이름 crm_test*)만 허용해요.")
PUBLIC = "http://127.0.0.1:8800"
os.environ["CRM_DEV"] = "1"
os.environ["CRM_DEV_USER"] = "wh-main@example.com"
os.environ["AUTHKIT_ISSUER"] = "https://fake.authkit.test"
os.environ["CRM_PUBLIC_URL"] = PUBLIC
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402

MAIN, OTHER, TIE = "wh-main@example.com", "wh-other@example.com", "wh-tie@example.com"
EMAILS = (MAIN, OTHER, TIE)
N = 120   # 50 + 50 + 20


def cleanup():
    with db.tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users WHERE email = ANY(%s)", (list(EMAILS),)).fetchall()]
        c.execute("DELETE FROM admin_audit WHERE actor_id = ANY(%s) OR target_id = ANY(%s)", (ids, ids))
    for uid in ids:
        db.user_delete(uid)


def seed(uid, n, tag):
    """합성 기록 n 편 — created 를 1시간씩 과거로(하루 한도를 피해 직접 넣는다). 한 편은 지운 것."""
    now = dt.datetime.now(dt.timezone.utc)
    with db.tx() as c:
        for i in range(n):
            at = now - dt.timedelta(hours=i)
            c.execute("INSERT INTO records (id,user_id,title,body,created,author_kind,text_process,received_at) "
                      "VALUES (%s,%s,%s,%s,%s,'self','verbatim',%s)", (f"r_{tag}{i:03d}", uid, f"{tag} 기록 {i}", f"{tag} 본문 {i}", at, at))
        c.execute("UPDATE records SET deleted_at=now() WHERE user_id=%s AND id=%s", (uid, f"r_{tag}005"))


def seed_tie(uid):
    """동률 경계 — 앞 40편은 1분 간격(미래 쪽), 뒤 20편은 한 tx 의 기본 created(now() = tx 시작 시각, board 이전과 같은 꼴).
    limit 50 이면 1쪽 = 40 + 동률 10, 2쪽 = 동률 나머지 10. created 단독 커서였다면 2쪽이 0편(10편 누락)."""
    now = dt.datetime.now(dt.timezone.utc)
    with db.tx() as c:
        for i in range(40):
            at = now + dt.timedelta(minutes=40 - i)
            c.execute("INSERT INTO records (id,user_id,title,body,created,author_kind,text_process,received_at) "
                      "VALUES (%s,%s,%s,%s,%s,'self','verbatim',%s)", (f"r_s{i:03d}", uid, f"간격 {i}", f"간격 본문 {i}", at, at))
        for i in range(20):
            c.execute("INSERT INTO records (id,user_id,title,body,author_kind,text_process) "
                      "VALUES (%s,%s,%s,%s,'self','verbatim')", (f"r_t{i:03d}", uid, f"동률 {i}", f"동률 본문 {i}"))


async def test_records_api():
    site = main.site
    real = site.dev, site.dev_user
    uid = db.user_by_email(MAIN)["id"]
    db.display_name_set(uid, "홈시험")
    other = db.user_by_email(OTHER)["id"]
    tie = db.user_by_email(TIE)["id"]
    seed(uid, N, "m")
    seed(other, 3, "o")
    seed_tie(tie)
    live = N - 1   # 지운 1편 제외
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=PUBLIC) as h:
            site.dev, site.dev_user = False, ""
            assert (await h.get("/api/records")).status_code == 401
            site.dev, site.dev_user = True, MAIN
            r = await h.get("/api/records")
            assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.status_code
            p1 = r.json()
            assert set(p1) == {"records", "next_before"}, "응답 계약 {records, next_before}"
            st = (await h.get("/api/state")).json()
            assert p1["records"] == st["journal"] and len(p1["records"]) == 50, "기본 = /api/state journal 50편"
            assert p1["next_before"] == f"{p1['records'][-1]['created']}|{p1['records'][-1]['id']}", "커서 = 마지막 created|id"
            assert st["journal_next_before"] == p1["next_before"], "/api/state journal_next_before = 기본 /api/records next_before(같은 커서 함수)"
            assert db.records(uid) == st["journal"] == db.records(uid, 50, None, None), "db.records 기본 호출 결과 불변"
            assert db.records(uid, 5) == st["journal"][:5], "records(uid, 5) 호출자(db.py) 불변"

            async def walk(first):
                seen, page, pages = [x["id"] for x in first["records"]], first, 1
                while page["next_before"]:
                    r = await h.get("/api/records", params={"before": page["next_before"], "limit": 50})
                    assert r.status_code == 200, r.text
                    nxt = r.json()
                    last = page["records"][-1]
                    assert all((x["created"], x["id"]) < (last["created"], last["id"]) or x["created"] < last["created"]
                               for x in nxt["records"]), "커서보다 뒤 순서만"
                    seen += [x["id"] for x in nxt["records"]]
                    page, pages = nxt, pages + 1
                return seen, pages
            seen, pages = await walk(p1)
            # journal_next_before 로 이어 받아도 같은 결과(화면 첫 「더 보기」 경로)
            r2 = (await h.get("/api/records", params={"before": st["journal_next_before"]})).json()
            assert [x["id"] for x in r2["records"]] == seen[50:100], "journal_next_before 로 2쪽"
            assert pages == 3 and len(seen) == live == len(set(seen)), (pages, len(seen))
            assert "r_m005" not in seen and not any(x.startswith("r_o") for x in seen), "지운 기록·남의 기록 0"
            created = [x["created"] for x in p1["records"]]
            assert created == sorted(created, reverse=True), "최신순"
            # limit 범위 밖 = 끝값(_qint 관례) · 숫자 아님 = 기본
            assert len((await h.get("/api/records?limit=101")).json()["records"]) == 100
            assert len((await h.get("/api/records?limit=500")).json()["records"]) == 100
            assert len((await h.get("/api/records?limit=0")).json()["records"]) == 1
            assert len((await h.get("/api/records?limit=x")).json()["records"]) == 50
            # 틀린 before → 400 ARG-INVALID(시간대 없음 · 날짜 틀림 · id 빠짐 · 구분자 없음)
            for bad in ("abc", "2026-10-01T00:00:00|r_x", "2026-13-01T00:00:00+00:00|r_x", "2026-10-01T00:00:00+00:00|",
                        "2026-10-01T00:00:00+00:00", "|r_x"):
                r = await h.get("/api/records", params={"before": bad})
                assert r.status_code == 400 and r.json()["code"] == "ARG-INVALID", (bad, r.status_code)
            # 아주 이른 before = 빈 목록
            r = (await h.get("/api/records", params={"before": "2000-01-01T00:00:00+00:00|r_zzz"})).json()
            assert r == {"records": [], "next_before": None}
            # created 동률 블록이 1·2쪽 경계에 걸침 — 겹침·누락 0
            site.dev_user = TIE
            db.display_name_set(tie, "동률")
            t1 = (await h.get("/api/records")).json()
            tied = [x for x in t1["records"] if x["id"].startswith("r_t")]
            assert len(t1["records"]) == 50 and len(tied) == 10 and len({x["created"] for x in tied}) == 1, "1쪽 = 간격 40 + 동률 10"
            assert [x["id"] for x in tied] == [f"r_t{i:03d}" for i in range(19, 9, -1)], "동률은 id DESC"
            seen, pages = await walk(t1)
            assert pages == 2 and len(seen) == 60 == len(set(seen)), (pages, len(seen))
            assert db.records(tie, 50, dt.datetime.fromisoformat(tied[-1]["created"])) == [], "created 단독 커서였다면 동률 10편 누락(대조)"
            # 다른 사용자 세션 = 자기 기록 3편만
            site.dev_user = OTHER
            db.display_name_set(other, "남")
            r = (await h.get("/api/records")).json()
            assert [x["id"] for x in r["records"]] == ["r_o000", "r_o001", "r_o002"] and r["next_before"] is None
            assert (await h.get("/api/state")).json()["journal_next_before"] is None, "journal 50편 미만 = null"
            # journal 정확히 50편 = 커서 줌, 다음 호출 0편 + null(수용)
            with db.tx() as c:
                for i in range(3, 50):
                    c.execute("INSERT INTO records (id,user_id,title,body,created,author_kind,text_process,received_at) "
                              "VALUES (%s,%s,'t','b',now() - make_interval(days => %s),'self','verbatim',now())", (f"r_o{i:03d}", other, i))
            st50 = (await h.get("/api/state")).json()
            assert len(st50["journal"]) == 50 and st50["journal_next_before"], "정확히 50편 = 커서"
            r = (await h.get("/api/records", params={"before": st50["journal_next_before"]})).json()
            assert r == {"records": [], "next_before": None}, "50편 뒤 = 0편 + null"
    finally:
        site.dev, site.dev_user = real
    print("OK — /api/records(401·no-store·기본 = state journal·next_before 커서 이어 받기 겹침 0·동률 경계·limit 끝값·틀린 before 400·지운·남의 기록 0·state journal_next_before) 통과")


def test_static():
    html = (HERE / "app.html").read_text(encoding="utf-8")
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    assert html.count("<script>") == 1
    home = js[js.index("// ── 「나」 상세 (PR①)"):js.index("// ── /「나」 홈 (PR①)")]
    # 빈 상태 문구(r1 T2) · 상세 종류 이름 · 옛 링크
    for w in ("아직 표시할 데이터가 없어요. 데이터가 들어오면 여기서 볼 수 있어요.", "이 데이터를 열 수 없어요.",
              '"내가 쓴 글"', '"질문"', '"검토할 판단"', '"확정한 판단"', '"읽고 든 생각"',
              'location.replace("#_obj/source/"', "(max-width: 767px)"):
        assert w in home, w
    # S5: 홈 쓰기는 두 곳(저장 homeSave · 동작 버튼 homeDo)에서만 기존 엔드포인트로 — 그 밖(선택·입력·포커스 이탈·취소·전환)은 요청 0.
    # 목록 위치·종류·편집 중인 글은 메모리에만
    assert home.count("corePost(") == 2 and "method: \"POST\"" not in home and "localStorage" not in home
    for f in ("async function homeSave()", "async function homeDo(b)"):
        body = home[home.index(f):home.index("\n}\n", home.index(f))]
        assert body.count("corePost(") == 1, f
    for w in ('"/api/me/name"', "/api/reflections/${encodeURIComponent(x.id)}", '"/api/candidate/decide"', '"edit_confirm"', '"/api/item/decide"',
              "/review`", "확인했어요", "확인 전으로", "고쳐서 남기기", "남기지 않기", "저장", "취소"):
        assert w in home, w
    assert not re.search(r"corePost\([^)]*/api/(people|sources)", home), "출처·사람 쓰기 없음(홈) — 고치기는 기존 화면(#_obj/person·#_obj/source) 링크로"
    # 미저장 보호 — 대화창 문구 · hashchange 가드 · 새로 고침 경고
    for w in ("저장하지 않은 변경이 있어요", "계속 편집", "변경 버리기"):
        assert w in html, w
    assert "if (homeLeaveGuard()) return;" in js and 'addEventListener("beforeunload"' in js
    pr1 = home[:home.index("// ── 「나」 v9 (PR ①-b)")]   # PR① 상세 — 「최근」 금지(섞인 정렬). v9 요약 「최근 변경 5」는 PRD v9 B6 이 정한 이름
    assert "「최근" not in pr1 and "최근 " not in pr1, "「최근」 표현 금지"
    assert "@media (max-width: 767px)" in html, "모바일 분기점 = 768 미만(JS 와 같은 값)"
    # S4 연결 그래프 — 빈 상태 2종 · 상한(넓은 25·40 / 1열 12·18) · 생략 문구 · 기존 읽기 API 만 · 외부 라이브러리 0
    for w in ("연결된 데이터가 없어요.", "연결을 불러오지 못했어요.", "다시 시도", 'wide: [25, 40], narrow: [12, 18]', "개·연결 ",
              "/api/axes/${encodeURIComponent(id)}/context", "/api/activities/${encodeURIComponent(id)}", "목록으로 보기", "그래프로 보기"):
        assert w in home, w
    assert "<script src" not in html.replace('<script src="{{asset:theme.js}}"></script>', ""), "외부 스크립트 0(테마 자산만)"
    # 타입 그래프 기본 = 그래프(새 탐색) · 영속 없음(저장소에 쓰지 않는다)
    assert 'let defsMode = "graph", defsAt = "";' in js and 'defsMode = navNew() ? "graph" : "list"' in js
    assert not re.search(r"localStorage\.setItem\([^)]*defs", js)
    # PR ①-b S2 — 탭 3 · 하위 탭 7(기본 레이아웃 = 계약 §3 JSON) · 서버 접점 한 곳(GET 만, 실패는 폴백) · 축 색 · 렌즈 · 추정 연결 0
    v9 = home[home.index("// ── 「나」 v9 (PR ①-b)"):]
    assert "if (navNew()) return [NAVTAB._me, NAVTAB._today, NAVTAB._setup];" in js, "탭 나·오늘·Setup"
    for w in ('const ME_TABS = ["summary", "principle", "decision", "axis", "custom", "person", "source"];',
              'const ME_BLOCKS = ["heatmap", "kinds", "waiting", "recent"];', "const ME_LAYOUT_DEFAULT = {v: 1, tabs: ME_TABS.slice(), blocks: {summary: ME_BLOCKS.slice()}};",
              'getJSON("/api/me/layout"), getJSON("/api/me/stats")', "const meLayout = () => ME.layout || ME_LAYOUT_DEFAULT;",
              "const meStats = () => ME.stats;", "meLayout().tabs.map(", "통계를 불러오지 못했어요. 새로 고침해 주세요.", "항목·글",
              "const ME_AX_COLORS = 8;", 'i < ME_AX_COLORS ? `var(--ax${i + 1})` : "var(--soft)"', '"근거 역추적"', '"축 묶음"', '"사람 관계"',
              "/api/people/${encodeURIComponent(id)}/context", "/api/sources/${encodeURIComponent(sel.id)}/context",
              'location.replace(`#_me/${meTabOfItem(it)}/', "GRAPH_CAP.narrow : GRAPH_CAP.wide"):
        assert w in v9, w
    assert js.count("async function meSync()") == 1 and js.count("= meSync();") == 1, "서버 접점 = meSync 한 곳(정의 + load 호출)"
    assert js.count('getJSON("/api/me/layout")') == 2 and js.count('"/api/me/stats"') == 1, "읽기 = meSync(+ STALE 때 레이아웃 다시 받기) — 통계는 meSync 안에만"
    assert "corePost(" not in v9 and "fetch(" not in v9 and "PUT" not in v9, "S2 「나」 화면 쓰기 요청 0"
    assert not re.search(r"가정|hyp|추천 연결", v9), "추정 연결·가정 연결 표시 0(B21)"
    assert "meStatsFromS" not in js, "통계 규칙은 서버 한 곳(S2 판정 ③ — 폴백 제거)"
    # S2 스크린샷 판정: 옆 묶음 = 종류별 5 + 「더 N」 1(그 목록만 펼침) · 활동 = 행동 · 날짜(문맥 API 값) + 부제 관계·사람 별칭
    for w in ("const ME_SHOW = 5;", "data-me-open=", "meActs[a.id] = a", "a.action_label || actLabel(a.action)", "x.id !== selfPerson"):
        assert w in js, w
    assert "<details" not in v9, "접힘(details) 대신 「더 N」 버튼"
    # S2 판정 ①: 51편 이후 글 = 출처 상세 「더 보기」 1개가 PR① 읽기 API(/api/records 커서)로 이어 받는다(새 API 0)
    for w in ('getJSON("/api/records?before=" + encodeURIComponent(cur) + "&limit=50")', "data-me-more", "S.journal_next_before",
              "meRec.extra = []; meRec.next = undefined;"):
        assert w in js, w
    # S3 요약 — 차트 4(블록 순서 = layout.blocks.summary) · 서버 값 그대로 · 버튼 0 · 미래 칸 비움 · 색만으로 구분하지 않음(글자 범례)
    for w in ("function meHeat(st)", "function meKinds(st)", "function meWaiting(st)", "function meRecent()",
              "draw = {heatmap: meHeat, kinds: meKinds, waiting: meWaiting, recent: meRecent}", "(meLayout().blocks || {}).summary",
              "future: c.d > today", 'cells.filter(c => !c.future)', '"Asia/Seoul"', "(c.records || 0) + (c.activities || 0)",
              '"candidates_pending"', '"principles_pending"', '"inbox_pending"', "이 기간에 날짜가 있는 글·활동이 없어요.", "기다리는 결정이 없어요."):
        assert w in v9, w
    summ = v9[v9.index("// 요약 — 차트 4"):]
    assert "<button" not in summ, "요약 버튼 0"
    assert "created" not in summ[:summ.index("function meRecent()")], "히트맵·막대·띠에 생성일 대체 없음(서버 값만)"
    # S4 Setup — 페이지 6 · 레이아웃 쓰기는 layFetch 한 곳(PUT·DELETE /api/me/layout) · PUT version = 마지막 응답 version(복원 뒤 포함) ·
    # 한 번에 하나(보내는 중이면 마지막 배치만 줄 세움) · 실패 = 마지막 저장 배치로 복원 · STALE = 서버 배치 다시 받기 · 옛 묶음 주소 replace
    setup = js[js.index("// ── Setup (PR ①-b S4)"):js.index("// ── /Setup ──")]
    for w in ('["layout", "페이지 레이아웃"]', '["structure", "내 구조 설정"]', '["objects", "자료 종류"]', '["devices", "연결 장치"]', '["map", "지도"]',
              '["help", "문의 및 개인정보"]', 'fetch("/api/me/layout"', '{layout: op.layout, version: ME.version}', 'layFetch("DELETE")',
              "ME.version = Number(res.j.version) || 0;", "if (LAY.busy) LAY.queued = op; else laySend(op);", 'res.code === "LAYOUT-STALE"',
              "ME.layout = LAY.saved.layout; ME.version = LAY.saved.version;", "기본으로 되돌리기", "e.altKey",
              'if (R.tab === "_obj" && !R.a) return "#_setup/objects";', 'if (R.tab === "_settings") return "#_setup/structure";',
              'if (R.tab === "_devices") return "#_setup/devices";', 'if (R.tab === "map") return "#_setup/map";',
              'if (R.tab === "_inbox" && !R.a) return "#_today";', 'if (R.tab === "structure") {'):
        assert w in setup, w
    assert js.count('fetch("/api/me/layout"') == 1, "레이아웃 쓰기 = layFetch 한 곳"
    assert "corePost(" not in setup and "/api/" not in setup.replace("/api/me/layout", ""), "Setup 의 다른 쓰기·API 0"
    assert "if (navNew()) { const to = navReplace(R); if (to) { location.replace(to); return; } }" in js
    for i, (lt, dk) in enumerate(zip(("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"),
                                     ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767")), 1):
        assert f"--ax{i}: {lt};" in html and f"--ax{i}: {dk};" in html, ("축 색", i)
    print("OK — 홈 정적(빈 상태 문구·옛 링크·쓰기 두 곳·「최근」 0·분기점·연결 그래프 상한·읽기 API·타입 그래프 기본 · v9 탭 3·하위 탭 7·접점 한 곳·축 색·렌즈·추정 0 · 요약 차트 4·버튼 0·서버 값만 · Setup 페이지 6·레이아웃 쓰기 한 곳·version·옛 주소 replace) 통과")


async def run():
    db.init_schema()
    cleanup()
    try:
        test_static()
        await test_records_api()
    finally:
        cleanup()
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
