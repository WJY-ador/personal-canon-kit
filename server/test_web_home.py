"""웹 시험 — PR① 「나」 홈(기존 데이터 화면): 기록 이어 받기 읽기 API GET /api/records · 홈 화면 정적 검사 (2026-10-21).

실행: DATABASE_URL=postgresql://localhost/crm_test_ux python3 server/test_web_home.py
가드: server/.env 를 읽지 않는다(운영 DB 폴백 사고 방지) — DATABASE_URL 을 명시해야 하고, 호스트는 로컬, DB 이름은 crm_test 로 시작해야 한다.

A. /api/records — 401 · no-store · 기본(before 없음) = /api/state journal 과 같은 50편 · next_before 커서로 겹침·빠짐 없이 이어 받기 ·
   created 동률 블록(한 tx 의 now())이 페이지 경계에 걸쳐도 겹침·누락 0 · limit 끝값 · 틀린 before 400 · 남의 기록 0 · db.records 기본 호출 결과 불변.
B. app.html 정적 — 「나」 상세(PR①: 빈 상태 문구·옛 링크 #_me/<출처> 이동·쓰기는 저장·동작 버튼 두 곳) +
   「나」 v9(PR ①-b S2: 탭 나·오늘·Setup · 하위 탭 7 = 레이아웃 순서 · 서버 접점 meSync 한 곳(GET 만) · 축 색 코드 순 8 + 회색 ·
   렌즈 3 + 커스텀·출처 목록/상세 · 추정 연결 0).
C. app.html 정적 — 대화 서랍(PR ②-b B5: 스위치 · 버튼 3 · 기본 포함 · 자동 확정 0 · 쓰기 3곳 · 편집 잠금 → 이탈 재정리 · 최신 결과만).
D. 대화 서랍 실행(③-0.2, node — 대화 절 함수를 가짜 DOM 으로 돌린다): 장치 상태 없음/최근 연결/오프라인 · 스위치 꺼짐 = 현행 · 남 장치 0 ·
   뺀 변경 표시 = 확정 미리보기의 excluded.
E. test_tools.py 에서 옮겨 온 app.html 정적 단언(③-0.2 A1~A5·B1~B4 — 문의 esc·계정 메뉴·구조 정의·LABELS·24h 화면·라이트 기본·셰이더·동의·재인증).
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
            ds = st["devices_status"]   # ③-0.2 화면이 읽는 꼴 — 키 3개뿐(장치 id·이름 없음), 장치 없는 사용자 = none
            assert set(ds) == {"state", "last_seen_at", "threshold_seconds"} and ds["state"] == "none" and ds["last_seen_at"] is None, ds
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
              '"내 글"', '"질문"', '"검토할 판단"', '"확정한 판단"', '"읽고 든 생각"',
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
    # S6: 글 이름 하나 「내 글」(v9 P1) — 사용자 표시 라벨 자리별. 「내 글 AI 요약」·「내가 쓴 메모」(판단 출처 표시)는 그대로
    assert "내가 쓴 글" not in js and 'input_scope: {self_only: "내 글만"}' in js, "「내가 쓴 글」 0 — 생성 정보 입력 범위도 「내 글만」"
    assert "연결된 활동과 내 글을 정해진 계산 규칙으로 보여 줍니다." in js, "ME_NOTICE = 서버 activity.NOTICE 미러"
    for w in ('"기록": "내 글"', 'kind: "내 글"', '"내 글 AI 요약"', '"내가 쓴 메모"', 'r.act', '"today:" + day'):
        assert w in js, w
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
    # S5 오늘 — 대기(승인 대기) + 보관됨(ingest_kind=inbox 글 · 활동) · 들어온 날(KST) 머리글 · [삭제] 0 · 동작 = 기존 승인·제외(inboxOne) · 읽기만
    today = js[js.index("// ── 오늘 (PR ①-b S5)"):js.index("// ── /오늘 ──")]
    for w in ('getJSON("/api/inbox?state=pending")', 'getJSON("/api/activities?limit=50")', 'r.ingest_kind === "inbox"', 'st: "대기"', 'st: "보관됨"',
              '"Asia/Seoul"', "class=\"tday\"", "inboxOne", "#_inbox/all", "ME_EMPTY"):
        assert w in today, w
    code = "\n".join(l for l in today.splitlines() if not l.lstrip().startswith("//"))
    assert "삭제" not in code and "corePost(" not in today and "fetch(" not in today, "오늘: [삭제] 0 · 새 쓰기 0(승인·제외는 기존 data-core 경로)"
    assert 'if (R.tab === "_inbox" && !INBOX_STATE[R.a]) return "#_today/inbox/" + encodeURIComponent(R.a);' in js
    assert 'hash = navNew() ? "#_today" : "#_inbox";' in js
    for i, (lt, dk) in enumerate(zip(("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"),
                                     ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767")), 1):
        assert f"--ax{i}: {lt};" in html and f"--ax{i}: {dk};" in html, ("축 색", i)
    print("OK — 홈 정적(빈 상태 문구·옛 링크·쓰기 두 곳·「최근」 0·분기점·연결 그래프 상한·읽기 API·타입 그래프 기본 · v9 탭 3·하위 탭 7·접점 한 곳·축 색·렌즈·추정 0 · 요약 차트 4·버튼 0·서버 값만 · Setup 페이지 6·레이아웃 쓰기 한 곳·version·옛 주소 replace · 오늘 대기/보관됨·날짜 머리글·삭제 0) 통과")


def test_static_chat():
    """PR ②-b B5 대화 서랍 — 스위치 켜짐일 때만 · 버튼 3(보내기·확정·닫기) · 변경 기본 포함(기본 off 0) · [정리 반영] 0 · 자동 확정 0 ·
    쓰기 = 작업 접수·제외·확정 3곳(stackApi 한 곳) · 편집 = 즉시 잠금(요청 0) → 칸 이탈 때 재정리 1 · 최신 작업 결과만 채택 · 이어 받기 = 서버 세션(저장소 0)."""
    html = (HERE / "app.html").read_text(encoding="utf-8")
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    chat = js[js.index("// ── 대화 (PR ②-b B5)"):js.index("// ── /대화 ──")]
    aside = html[html.index('<aside class="chat" id="chat"'):html.index("</aside>", html.index('<aside class="chat" id="chat"'))]
    assert aside.count("<button") + chat.count("<button") == 3, "서랍 버튼 = 닫기·보내기·확정 3"
    for w in ('id="chat-close"', 'id="chat-send"', 'id="chat-in"', 'id="chat-verbatim"', "원문으로 저장", 'role="status"'):
        assert w in aside, w
    assert 'id="chat-confirm">확정</button>' in chat
    assert "정리 반영" not in html, "[정리 반영] 버튼 0(편집 = 칸 이탈 때 자동 재정리)"
    assert 'data-cid="${esc(c.cid)}" checked>' in chat and chat.count(" checked>") == 1 and "box.checked = true" in chat, "변경 = 기본 포함(기본 off 0, 실패하면 다시 켬) — 실행 확인은 test_chat_excluded"
    # 스위치 — 꺼지면 버튼·서랍 없음. 탭 줄 오른쪽(render 의 탭 줄 끝)
    assert 'if (!chatOn() || !navNew()) return "";' in chat and "const chatOn = () => !!(S && S.switches && S.switches.converse);" in chat
    assert '}).join("")}${chatNavHtml()}</div>`;' in js and js.count("chatSync();") == 1
    # 서버 접점 한 곳 — 쓰기 3(작업 접수·제외·확정) · 읽기 2(작업 롱폴·세션 복원). 그 밖 fetch·corePost 0
    for w in ('send: body => stackPost("/api/jobs", body)', "poll: jid => getJSON(`/api/jobs/${encodeURIComponent(jid)}?wait=30`)",
              "exclude: (pid, cids) => stackPost(`/api/stack/previews/${encodeURIComponent(pid)}/exclude`, {cids})",
              "confirm: (jid, body) => stackPost(`/api/jobs/${encodeURIComponent(jid)}/confirm`, body)",
              "session: sid => getJSON(`/api/stack/sessions/${encodeURIComponent(sid)}`)"):
        assert w in chat, w
    assert chat.count("fetch(") == 1 and chat.count("stackPost(") == 4 and "corePost(" not in chat, "쓰기 = stackPost 3곳"
    assert js.count("/api/jobs") == 3 and js.count("/api/stack/") == 2, "작업·쌓기 API 는 stackApi 밖에서 부르지 않는다"
    # 자동 확정 0 — 확정은 [확정] 클릭 한 곳에서만 · 요청 ID = 미리보기당 1(재시도 재사용) · 미리보기 판·해시 동봉
    assert chat.count("stackApi.confirm(") == 1 and chat.count("chatConfirm()") == 2 and 'if (e.target.closest("#chat-confirm")) chatConfirm();' in chat
    assert "if (CHAT.reqFor !== key) { CHAT.reqId = chatUuid(); CHAT.reqFor = key; }" in chat
    assert "{request_id: CHAT.reqId, preview_id: p.id, preview_version: p.preview_version, preview_sha: p.preview_sha}" in chat
    assert "!chatOpenBlock(CHAT.preview)" in chat and "p.blocked_open === true ||" in chat, "막힌 질문이 남으면 확정 잠금(서버 blocked_open 우선)"
    assert "c.inferred === true" in chat and "CHAT.gen = rs.edit_generation;" in chat, "「추정」 = 서버 inferred · 재정리 세대는 접수됐을 때만 올림"
    # 편집 — 입력 = 즉시 잠금(요청 0) · 칸 이탈(focusout, 정리 글 밖으로) = 재정리 1(원 결과·편집 세대·고친 글) · 늦은 결과 폐기
    inp = chat[chat.index('document.addEventListener("input"'):chat.index('document.addEventListener("focusout"')]
    assert "if (!CHAT.dirty) { CHAT.dirty = true;" in inp and "stackApi" not in inp and "chatRestack" not in inp, "입력 = 잠금만"
    assert 'if (to && to.closest && to.closest("#chat-units")) return;' in chat and "if (CHAT.dirty && !CHAT.busy) chatRestack();" in chat
    assert "{parent_result_version: CHAT.rv, edit_generation: CHAT.gen + 1, edited_text: text}" in chat and \
        '{kind: "converse", session_id: CHAT.sid, text, restack: rs}' in chat, "재정리 = 구조(원 결과·편집 세대·고친 글, text = edited_text)"
    assert "excluded_cids: off" in chat and "chatRestack({answers})" in chat and 'const CHAT_UNIT_JOIN = "\\n\\n";' in chat, \
        "뺀 변경·막힘 답은 재정리 구조로(자유 턴 아님) · 정리 글 결합 = PC 와 같은 구분"
    assert "if (CHAT.polling === jid) return;" in chat, "같은 작업 롱폴 겹침 0"
    assert "CHAT.ready = p.latest !== false;" in chat and "chatShow(j.preview, jid, j.result && j.result.result_version)" in chat, \
        "늦게 온 옛 결과(latest=false) 확정 불가 · 원 결과 버전 = 그린 작업의 result.result_version"
    assert "if (jid !== CHAT.want || CHAT.dirty) return false;" in chat and "if (CHAT.want !== jid || !res) return;" in chat, "최신 작업 결과만 · 고치는 중 도착 = 버림"
    assert 'res.code === "PREVIEW-NEEDS-RESTACK"' in chat and 'res.code === "CONFIRM-STALE"' in chat
    # 원문으로 저장 = 세션 첫 입력만 · 8,000자 카운터(코드포인트) · 기억 = 세션 ID 만(본문 0) · 확정 뒤 load()(상태 + meSync)
    assert 'else body.input_kind = $("chat-verbatim").checked ? "verbatim" : "converse";' in chat and '$("chat-vb").hidden = !!CHAT.sid;' in chat
    assert "const CHAT_MAX = 8000;" in chat and 'const chatCp = s => [...String(s || "")].length;' in chat
    assert "Storage" not in chat and "const sid = S.stack_session && S.stack_session.id;" in chat, "이어 받기 = 서버 세션(브라우저 저장소 0)"
    assert "저장했어요 · 변경 ${n}" in chat and "await load();   // 「나」 통계·목록 갱신(상태 + meSync)" in chat
    assert "@media (max-width: 767px)" in html[html.index("/* 대화 서랍 (PR ②-b B5)"):html.index("</style>")]
    print("OK — 대화 서랍 정적(스위치·버튼 3·기본 포함·[정리 반영] 0·자동 확정 0·접점 한 곳 쓰기 3·편집 잠금→이탈 재정리·최신만·원문 첫 입력만·8,000자·저장소 0) 통과")


def chat_run(scenario):
    """대화 서랍 함수 실행(③-0.2) — app.html <script> 의 대화 절(선언만, 이벤트 연결 전까지) + 날짜·esc 도우미를 node 로 돌린다.
    DOM 은 최소 가짜($ = 요소 메모, querySelectorAll = 빈 목록) — 서랍 HTML 문자열과 CHAT 상태를 돌려받아 단언한다(CI 에 node 있음)."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    assert node, "node 가 필요해요(CI 는 setup-node) — 대화 서랍 실행 시험"
    html = (HERE / "app.html").read_text(encoding="utf-8")
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    helpers = "\n".join(re.search(rf"^{p}.*$", js, re.M).group(0) for p in (
        r"const esc = ", r"const toDate = ", r"const pad2 = ", r"function mdhm\("))
    a = js.index("// ── 대화 (PR ②-b B5)")
    chat = js[a:js.index('document.addEventListener("click"', a)]
    stub = """let S = null; const navNew = () => true; const els = {};
const $ = id => els[id] || (els[id] = {id, textContent: "", innerHTML: "", hidden: false, value: "", disabled: false, checked: false,
  classList: {toggle() {}}, focus() {}, setAttribute() {}});
const Q = {};   // querySelectorAll 가짜 — 시나리오가 선택자별 요소를 넣는다(기본 빈 목록)
const document = {querySelectorAll: sel => Q[sel] || [], querySelector: () => null, body: {classList: {toggle() {}}}};
const getJSON = async () => null; const load = async () => {};"""
    src = f"{stub}\n{helpers}\n{chat}\nconst out = {{}};\n(async () => {{\n{scenario}\n}})().then(() => console.log(JSON.stringify(out)), e => {{ console.error(e); process.exit(1); }});"
    r = subprocess.run([node, "-"], input=src, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


NODEV = "이 기능은 본인 PC에서 본인 Claude 구독으로 실행하며, PC 연결과 에이전트 실행이 필요합니다."   # PRD 1-2 고정 문구(글자 그대로)


def test_chat_devices():
    """③-0.2 장치 상태 — /api/state.devices_status {state, last_seen_at, threshold_seconds} 를 서버 판정 그대로 쓴다.
    none = 「대화」 버튼 대신 PRD 안내 1줄(버튼·서랍 0, 이어 받기도 열지 않음) · recent = 「최근 연결」(「실행 가능」 0) · offline = 마지막 연결 +
    받기 대기 작업에 PC 연결 대기·취소·만료 · 필드 없음·모르는 값 = 현행 · 스위치 꺼짐 = 상태와 무관하게 현행(버튼·안내 0) · 남의 장치 정보 표시 0."""
    T = "2026-10-22T05:03:00+00:00"
    leak = '{devices: [{id: "dv-other", label: "남의장치라벨"}], user: {email: "other@example.com"}, label: "남의장치라벨", user_id: "u-other"}'
    o = chat_run(f"""
const T = "{T}";
const set = (sw, ds, sess = true) => {{ S = {{switches: {{converse: sw}}, stack_session: sess ? {{id: "s-old"}} : null}}; if (ds !== undefined) S.devices_status = ds; }};
const look = () => {{ CHAT.booted = false; CHAT.open = true; CHAT.sid = ""; chatSync();
  return {{nav: chatNavHtml(), dev: chatDevText(), q: chatQueuedLine(), open: CHAT.open, booted: CHAT.booted, hidden: $("chat").hidden, ctx: $("chat-ctx").textContent, form: $("chat-form").hidden, sid: CHAT.sid}}; }};
const L = {leak};
out.seen = mdhm(T);
set(true, Object.assign({{state: "none", last_seen_at: null, threshold_seconds: 180}}, L), false); out.none = look();
set(true, Object.assign({{state: "recent", last_seen_at: T, threshold_seconds: 180}}, L)); out.recent = look();
set(true, Object.assign({{state: "offline", last_seen_at: T, threshold_seconds: 180}}, L)); out.offline = look();
set(true, {{state: "offline", last_seen_at: null, threshold_seconds: 180}}); out.offline_noseen = look();
set(true); out.absent = look();
set(true, {{state: "busy", last_seen_at: T}}); out.unknown = look();
out.off = {{}};
for (const st of ["none", "recent", "offline"]) {{ set(false, {{state: st, last_seen_at: T, threshold_seconds: 180}}); out.off[st] = look(); }}
set(false); out.off.absent = look();
""")
    # 장치 없음 — 버튼 대신 안내 1줄(글자 그대로) · 버튼 0 · 서랍 닫힘(세션이 남아 있어도 이어 받지 않음)
    n = o["none"]
    assert n["nav"] == f'<span class="chatnote" role="note">{NODEV}</span>', n["nav"]
    assert "<a" not in n["nav"] and "<button" not in n["nav"] and "data-chat-toggle" not in n["nav"], "장치 없음 = 죽은 버튼 0"
    assert n["sid"] == "" and n["form"] is True, "이어 받을 세션 없음 = 열 세션 0 · 서랍이 이미 열려 있었어도 입력 칸 대신 안내(확정만 남은 세션은 test_chat_nodev_session)"
    # 최근 연결 — 버튼(현행 그대로) + 서랍 머리글 「최근 연결」 · 「실행 가능」 0
    r = o["recent"]
    assert r["nav"].count("data-chat-toggle") == 1 and r["nav"].endswith(">대화</a>"), r["nav"]
    assert r["dev"] == "최근 연결" and r["ctx"] == "최근 연결 · 임시 보관" and r["open"] is True and r["hidden"] is False, r   # 이어 받은 세션 + 장치 상태
    assert r["q"] == "PC 가 받기를 기다리고 있어요… PC 가 켜져 있어야 정리돼요.", "최근 연결 = 기존 대기 문구"
    # 오프라인 — 버튼 유지 · 머리글 = 오프라인 + 마지막 연결(서버 값 그대로를 화면 시각 꼴로) · 대기 작업 = PC 연결 대기·취소·만료
    f = o["offline"]
    assert f["nav"].count("data-chat-toggle") == 1 and f["dev"] == f"PC 오프라인 · 마지막 연결 {o['seen']}" and f["ctx"] == f["dev"] + " · 임시 보관", f
    assert f["q"] == f"PC 연결을 기다리고 있어요(마지막 연결 {o['seen']}). 연결되지 않으면 작업이 취소되거나 대화가 만료될 수 있어요.", f["q"]
    assert o["offline_noseen"]["dev"] == "PC 오프라인" and "마지막 연결" not in o["offline_noseen"]["q"], "마지막 연결 시각 없음 = 시각 없이"
    # 필드 없음(옛 서버)·모르는 값 = 현행(버튼·기존 대기 문구·머리글 비움)
    for k in ("absent", "unknown"):
        x = o[k]
        assert x["nav"].count("data-chat-toggle") == 1 and x["dev"] == "" and x["ctx"] == "임시 보관" and x["open"] is True, (k, x)
        assert x["q"] == "PC 가 받기를 기다리고 있어요… PC 가 켜져 있어야 정리돼요.", k
    # 스위치 꺼짐 = 상태와 무관하게 현행(버튼·안내·서랍 0)
    for k, x in o["off"].items():
        assert x["nav"] == "" and x["open"] is False and x["hidden"] is True, ("스위치 꺼짐", k, x)
    # 남의 장치 정보 표시 0 — devices_status 에 섞여 와도 화면 문자열에 나오지 않는다
    blob = repr(o)
    for w in ("남의장치라벨", "dv-other", "other@example.com", "u-other", "실행 가능"):
        assert w not in blob, w
    # 정적(실행으로 못 보는 범위) — 상태는 chatDev 한 곳에서만 읽고, 읽는 키는 state·last_seen_at 둘 · 고정 문구 한 곳
    html = (HERE / "app.html").read_text(encoding="utf-8")
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    code = re.sub(r"//[^\n]*", "", js)   # 주석을 뺀 코드에서 센다(설명 주석의 낱말은 세지 않는다)
    assert code.count("devices_status") == 1 and "const d = S && S.devices_status;" in code, "장치 상태 읽기 = chatDev 한 곳"
    assert set(re.findall(r"\bd\.(\w+)", code[code.index("const chatDev"):code.index("function chatQueuedLine")])) <= {"state", "last_seen_at"}, "읽는 키 2"
    assert js.count(NODEV) == 1 and "실행 가능" not in code, "고정 문구 한 곳 · 「실행 가능」 0"
    print("OK — 장치 상태(장치 없음 = 버튼 대신 안내 · 최근 연결 · 오프라인 = 마지막 연결·대기·취소·만료 · 필드 없음·모르는 값 = 현행 · 스위치 꺼짐 = 현행 · 남 장치 0) 통과")


def test_chat_nodev_session():
    """장치 없음 + 확정 전 세션(원작자 판정 2026-10-22) — 확정은 서버 작업이라 이어 받아 연다. 막는 것은 작업 접수(새 턴·재정리·막힘 답)뿐:
    입력 칸(보내기) 대신 안내 1줄 · 정리 글 읽기만 · 막힘 답 잠금 · 끄기·확정은 된다 · 확정 뒤 세션이 없으면 탭 줄은 다시 안내."""
    o = chat_run(f"""
S = {{switches: {{converse: true}}, stack_session: {{id: "s1"}}, devices_status: {{state: "none", last_seen_at: null, threshold_seconds: 180}}}};
let sends = 0, restore = 0, excl = 0, sent = null;
stackApi.send = async () => {{ sends++; return {{ok: true, j: {{id: "jx", session_id: "s1"}}}}; }};
stackApi.session = async () => {{ restore++; return null; }};
stackApi.exclude = async (pid, cids) => {{ excl++; return {{ok: true, j: {{preview: Object.assign({{}}, p, {{id: "p2", preview_version: 2, preview_sha: "h2", changes: [p.changes[0]], excluded: cids}})}}}}; }};
stackApi.confirm = async (jid, body) => {{ sent = body; return {{ok: true, j: {{}}}}; }};
const ta = {{readOnly: false, value: "하나"}}, bq = {{disabled: false, value: ""}};
Q["#chat-units textarea[data-uid]"] = [ta]; Q["#chat-card input[data-bq]"] = [bq];
const ch = (cid, t) => ({{cid, op: "create", summary: t, ref_kind: "new", inferred: false, depends_on: [], unit_ids: []}});
const p = {{id: "p1", preview_version: 1, preview_sha: "h1", latest: true, blocked: [], blocked_open: false, excluded: [],
  units: [{{uid: "u1", text: "하나", change_ids: ["c1"]}}, {{uid: "u2", text: "둘", change_ids: ["c2"]}}], changes: [ch("c1", "새 결정: 가"), ch("c2", "새 판단: 나")]}};
chatSync();
out.boot = {{open: CHAT.open, sid: CHAT.sid, restore, nav: chatNavHtml(), hidden: $("chat").hidden}};
CHAT.want = "j1"; chatShow(p, "j1", "r1");
out.draw = {{form: $("chat-form").hidden, note: $("chat-nodev").hidden, text: $("chat-nodev").textContent, ro: ta.readOnly, bq: bq.disabled, send: $("chat-send").disabled}};
$("chat-in").value = "새 말"; await chatSend();
await chatRestack(); await chatRestack({{answers: [{{cid: null, text: "답"}}]}});
out.sends = sends; out.line = CHAT.line; out.dirty = CHAT.dirty;
await chatExclude({{dataset: {{cid: "c2"}}, checked: false}}); out.excl = excl; out.card = $("chat-cardbox").innerHTML;
await chatConfirm(); out.sent = sent; out.after = {{line: CHAT.line, sid: CHAT.sid, nav: chatNavHtml()}};
""")
    b = o["boot"]
    assert b["open"] is True and b["sid"] == "s1" and b["restore"] == 1 and b["hidden"] is False, ("확정 전 세션은 장치 없어도 이어 받는다", b)
    assert b["nav"].count("data-chat-toggle") == 1, "세션이 있는 동안은 서랍을 다시 여는 「대화」(닫아도 돌아올 길)"
    d = o["draw"]
    assert d["form"] is True and d["note"] is False and d["text"] == NODEV, ("입력 칸 대신 안내 1줄", d)
    assert d["ro"] is True and d["bq"] is True and d["send"] is True, ("재정리를 부르는 칸 잠금", d)
    assert o["sends"] == 0 and o["line"] == NODEV and o["dirty"] is False, (o["sends"], o["line"], o["dirty"], "작업 접수 0(새 턴·재정리·막힘 답) — 안내 1줄")
    assert o["excl"] == 1 and "뺀 것 1" in o["card"], "끄기(서버 작업)는 된다"
    assert o["sent"] and o["sent"]["preview_id"] == "p2", "확정(서버 작업)은 된다 — 뺀 판을 가리킨다"
    a = o["after"]
    assert a["line"] == "저장했어요 · 변경 1" and a["sid"] == "" and a["nav"] == f'<span class="chatnote" role="note">{NODEV}</span>', \
        ("확정 뒤 세션 없음 = 탭 줄은 다시 안내", a)
    html = (HERE / "app.html").read_text(encoding="utf-8")
    assert ".chat .ccomp[hidden] { display: none; }" in html, "입력 칸 숨김이 display:flex 에 덮이지 않게"
    print("OK — 장치 없음 + 확정 전 세션(이어 받기·입력 칸 대신 안내·재정리 칸 잠금·작업 접수 0·끄기·확정 됨·확정 뒤 안내) 통과")


def test_chat_excluded():
    """③-0.2 뺀 변경 표시 = 확정이 가리키는 미리보기의 실제 excluded. 서버 exclude 는 뺀 변경(연쇄·같은 단위 포함)을 changes 에서 지우고
    excluded 에 ID 를 쌓는다 → 화면은 excluded 를 「뺀 것 N」으로 그대로 그린다(이름 = 같은 결과에서 본 변경, 못 봤으면 「앞서 뺀 변경」).
    확정 요청은 그 미리보기(id·판·해시)를 가리킨다 · 바뀌는 것 = 기본 포함 체크 · 뺀 것엔 체크 칸 0 · 결과가 바뀌면 본 이름을 잊는다."""
    o = chat_run("""
S = {switches: {converse: true}, devices_status: {state: "recent", last_seen_at: null, threshold_seconds: 180}};
const ch = (cid, s, x) => Object.assign({cid, op: "create", summary: s, ref_kind: "new", inferred: false, depends_on: [], unit_ids: []}, x || {});
const p1 = {id: "p1", preview_version: 1, preview_sha: "h1", latest: true, blocked: [], blocked_open: false, excluded: [],
  units: [{uid: "u1", text: "하나", change_ids: ["c1"]}, {uid: "u2", text: "둘", change_ids: ["c2", "c3"]}],
  changes: [ch("c1", "새 결정: 가"), ch("c2", "새 판단: 나"), ch("c3", "새 판단: 다", {depends_on: ["c2"]})]};
// 서버 stack_exclude 꼴 — c2 를 끄면 기대는 c3·같은 단위가 함께 빠진다: changes 에서 지우고 excluded 에 둘
const p2 = Object.assign({}, p1, {id: "p2", preview_version: 2, preview_sha: "h2", units: [p1.units[0]], changes: [p1.changes[0]], excluded: ["c2", "c3"]});
chatReset(); CHAT.want = "j1";
chatShow(p1, "j1", "r1"); out.card1 = $("chat-cardbox").innerHTML;
chatShow(p2, "j1", "r1"); out.card2 = $("chat-cardbox").innerHTML;
let sent = null; stackApi.confirm = async (jid, body) => { sent = {jid, body}; return {ok: true, j: {}}; };
await chatConfirm(); out.sent = sent; out.line = CHAT.line;
// 새로 고침 뒤(본 변경 없음) — 이름 없이 같은 수
chatReset(); CHAT.want = "j2"; chatShow(Object.assign({}, p2), "j2", "r1"); out.card3 = $("chat-cardbox").innerHTML;
// 새로 고침 뒤 + 서버 excluded_changes(첫 판 표시 요약) — 서버 이름을 먼저 쓴다
chatReset(); CHAT.want = "j5"; chatShow(Object.assign({}, p2, {excluded_changes: [{cid: "c2", summary: "새 판단: 나"}, {cid: "c3", summary: "새 판단: 다"}]}), "j5", "r1");
out.card5 = $("chat-cardbox").innerHTML;
// 다른 결과(재정리) — 같은 ID 라도 옛 결과의 이름을 붙이지 않는다
chatReset(); CHAT.want = "j1"; chatShow(p1, "j1", "r1");
CHAT.want = "j3"; chatShow(Object.assign({}, p2, {id: "p3"}), "j3", "r2"); out.card4 = $("chat-cardbox").innerHTML;
""")
    xs = lambda h: re.findall(r'<li class="out" data-xcid="([^"]+)"><span>([^<]*)</span><span class="cnote">뺐어요</span></li>', h)   # noqa: E731
    on = lambda h: re.findall(r'<input type="checkbox" data-cid="([^"]+)"( checked)?( disabled)?>', h)   # noqa: E731
    assert xs(o["card1"]) == [] and "뺀 것" not in o["card1"] and "바뀌는 것 3" in o["card1"], "뺀 것 없음 = 칸 0"
    assert on(o["card1"]) == [("c1", " checked", ""), ("c2", " checked", ""), ("c3", " checked", "")], "바뀌는 것 = 기본 포함(체크), 기본 off 0"
    assert xs(o["card2"]) == [("c2", "새 판단: 나"), ("c3", "새 판단: 다")], "뺀 것 = excluded 그대로(연쇄 포함) · 이름 = 빼기 전 본 변경"
    assert "뺀 것 2" in o["card2"] and "바뀌는 것 1" in o["card2"] and on(o["card2"]) == [("c1", " checked", "")], "뺀 것엔 체크 칸 0"
    assert o["card2"].count("<button") == 1, "버튼 = [확정] 하나"
    assert o["sent"] == {"jid": "j1", "body": {"request_id": o["sent"]["body"]["request_id"], "preview_id": "p2", "preview_version": 2, "preview_sha": "h2"}}, \
        "확정 = 뺀 것을 그린 그 미리보기(서버는 그 판의 excluded 로 저장)"
    assert o["line"] == "저장했어요 · 변경 1", "저장 문구의 변경 수 = 바뀌는 것(뺀 것 제외)"
    assert xs(o["card3"]) == [("c2", "앞서 뺀 변경"), ("c3", "앞서 뺀 변경")] and "뺀 것 2" in o["card3"], "새로 고침 뒤 = 이름 없이 같은 수"
    assert xs(o["card4"]) == [("c2", "앞서 뺀 변경"), ("c3", "앞서 뺀 변경")], "다른 결과의 같은 ID 에 옛 이름 0"
    assert xs(o["card5"]) == [("c2", "새 판단: 나"), ("c3", "새 판단: 다")], "새로 고침 뒤 = 서버 excluded_changes 이름(키 없으면 「앞서 뺀 변경」)"
    print("OK — 뺀 변경 표시(excluded 그대로·연쇄 포함·이름 = 본 변경·체크 칸 0·확정 = 그 미리보기·저장 수 = 포함만·새로 고침 뒤 수 유지·결과별 이름) 통과")


def test_static_moved():
    """test_tools.py 에서 옮겨 온 app.html 정적 단언(③-0.2 — 서버 에이전트 목록 A1~A5·B1~B4, 검증 범위 그대로).
    문자열 단언으로 둔 이유: 파일 성질(인라인 스크립트·CSP 해시·on* 0·특정 낱말 부재·마크업 구간·서버 정본과 같은 문구)이라 실행으로 보이지 않는다.
    서버 정본 값(db·web·contract·structure_svg)은 import 해 같은 기대값을 쓴다(하드코딩하지 않는다)."""
    import contract
    import structure_svg
    import web
    html = (HERE / "app.html").read_text(encoding="utf-8")
    # 공통(A1·A2·A3·A5) — 인라인 스크립트 1개(CSP 해시 대상) · 인라인 이벤트 속성 0(CSP)
    assert len(web.inline_script_hashes(html)) == 1 and html.count("<script>") == 1, "인라인 스크립트 1개"
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "app.html 인라인 이벤트 속성 0"

    # A1 문의 데스크 — 문의 구간의 모든 보간은 esc(…) · innerHTML 우변은 템플릿 하나(이어 붙이기 없음) · 다른 HTML 삽입 API 없음
    blk = html[html.index("// ── 문의 데스크 (desk-0) ──"):html.index("// ── /문의 데스크 ──")]
    assert blk.count("${") == blk.count("${esc(") >= 4, re.findall(r"\$\{(?!esc\()[^}]*", blk)
    for bad in ("insertAdjacentHTML", "outerHTML", "document.write", "DOMParser", "createContextualFragment"):
        assert bad not in blk, bad
    sites = list(re.finditer(r"\.innerHTML\s*=\s*", blk))
    assert sites, "행은 innerHTML 템플릿으로 만든다"
    for m in sites:
        rest = blk[m.end():]
        assert rest.startswith("`"), rest[:60]
        end = rest.index("`", 1)
        assert rest[end + 1:].lstrip().startswith(";"), rest[end:end + 40]
    i = html.index('<dialog id="desk-dlg"')   # Q1b: 문의 폼·목록은 계정 메뉴에서 여는 대화창
    sec = html[i:html.index("</dialog>", i)]
    assert "${" not in sec and db.NOTICE in sec and 'href="/privacy"' in sec, "정적 대화창 — 고지 문장·처리방침 링크"
    for w in ('value="bug"', 'value="request"', 'value="question"', 'value="privacy"', "개인정보 요청", "내 문의", "4096"):
        assert w in sec, w
    assert 'ticketStatus: {open: "접수", ack: "확인", doing: "처리중", done: "완료", wontfix: "보류"}' in html
    assert "const DESK_MAX = 4096;" in html and db.LIMIT_TICKET == 4096
    # 내 문의(P5): 상세는 본인 API 하나 · 글·답변은 esc · 이력은 textContent · 관리자 문의함·띠·옛 API 흔적 0
    for w in ('fetch("/api/feedback/" + encodeURIComponent(id)', "${esc(j.text)}", "${esc(j.reply || ", "li.textContent = ",
              "${esc(t.has_reply ? ", "${esc(md(t.public_updated)"):
        assert w in blk, w
    assert "${t." not in blk and "${j." not in blk
    for gone in ("admin-band", "deskBand", "deskAdmin", "/api/feedback/status", "/api/feedback/note", "admin_tickets", "admin_note",
                 "desk-admin", "DESK_NOTE_MAX", "j.admin"):
        assert gone not in html, ("app.html 에 관리자 문의함 흔적", gone)
    # ⚙ Setup 링크: 기본 hidden · /admin/setup · 상태가 admin 일 때만 hidden 해제
    gear = re.search(r'<a\b[^>]*\bid="setup-link"[^>]*>', html)
    assert gear and re.search(r"\shidden(\s|>)", gear.group(0)) and 'href="/admin/setup"' in gear.group(0), gear
    assert '$("setup-link").hidden = !(S.user && S.user.admin);' in html

    # A2 계정 메뉴 — 메뉴 항목 · ⚙ 메뉴 밖 · #who 말줄임 · 문의 숨기기 흔적 0 (/app 응답 CSP 해시는 test_tools 에 남는다)
    menu = html[html.index('<details class="acct" id="acct">'):html.index("</details>")]
    for act in ("send", "mine", "privacy", "names", "logout"):
        assert f'data-acct="{act}"' in menu, act
    for w in ('<span id="who"></span>', 'id="logout"', 'id="acct-names"', "구조 이름 공유", "로그아웃"):
        assert w in menu, w
    for w in ('data-acct="desk"', 'id="acct-desk"', "문의 섹션"):   # Q1b: 숨기기 메뉴 없음(서버 API·열은 남는다)
        assert w not in menu, w
    assert 'id="setup-link"' not in menu and 'id="setup-link"' in html, "⚙ 는 메뉴 밖에 그대로"
    assert re.search(r"#who \{[^}]*text-overflow: ellipsis[^}]*\}", html) and re.search(r"#who \{[^}]*min-width: 0", html), "#who 말줄임"
    for w in ('fetch(url, {method: "POST"', '"/api/optional-consent", {kind: "struct_names"',
              '[name="kind"][value="privacy"]', '$("desk-dlg").showModal()'):
        assert w in html, w
    for w in ("/api/desk-visibility", "desk_hidden", "deskPeek", 'id="desk"'):   # Q1b: 화면은 숨김 상태를 안 쓴다
        assert w not in html, w

    # A3 구조 정의 보기·편입 대화창(Q4·Q5)
    tokens = structure_svg.tokens_css(dark_attr=True, dark_media=False)
    assert '"구조 정의"' in html and "/defs" in html and 'if (a === "defs") return renderDefs(tab);' in html, "구조 정의 보기·라우트"
    assert "S.structure" in html and "/api/std/preview" in html and "/api/std/choose" in html
    i = html.index('<dialog id="std-dlg"')
    dlg = html[i:html.index("</dialog>", i)]
    for w in ('id="std-dlg-ok"', 'type="checkbox"', 'id="std-dlg-go" disabled', 'id="std-dlg-lock"', 'id="std-dlg-oktext"',
              'id="std-dlg-cancel"'):
        assert w in dlg, w

    def outside_marks(pos, what):   # 관리자 /app 도 쓰는 대화창 — <!--user-->/<!--admin--> 구간 표식 밖
        for mark in ("<!--user-->", "<!--admin-->"):
            for a in [m.start() for m in re.finditer(re.escape(mark), html)]:
                b = html.index(mark.replace("<!--", "<!--/"), a)
                assert not (a < pos < b), (what, "구간 안", mark)
    outside_marks(i, "std-dlg")
    for w in ('같은 뜻으로 쓴다', 'confirmed_meaning = true', 'expected_revision = o.object ? o.object.revision : null',
              '$("std-dlg-go").disabled = !$("std-dlg-ok").checked', '"DEF-INVALID":', '"CONFLICT":',
              "표준 정의 보호", "출처 미확인", "source_counts", "확정 전 재검토", "S.sources"):
        assert w in html, w
    for code in contract.LOCKED_CHANGES:   # 잠금 영향 문구 — 서버 코드 전부
        assert re.search(rf"\b{code}: \"", html), code
    for code in contract.REVIEW_REASONS:
        assert re.search(rf"\b{code}: \"", html), code
    fix = html[html.index("const REVIEW_FIX = {"):html.index("};", html.index("const REVIEW_FIX = {"))]
    assert "author_unknown" in fix and "processed" in fix and "external" not in fix and "deleted" not in fix, "정정 안내는 두 사유만"
    assert "record_provenance_set" not in html and "verbatim 으로" not in html, "도구명·「바꾸면 풀린다」 안내 없음"
    for w in ('let defsMode = "graph", defsAt = "";', 'data-dmode="${m}"', '"목록" : "그래프"', "S.structure_svg", 'id="dlist"', 'id="dgraph"',
              'id="dmo-o${i + 1}"', 'id="dmo-r${i + 1}"', 'id="dmo-core-${esc(x.kind)}"', 'closest(".dmo a")', 'getAttribute("href")',
              "e.preventDefault(); defsJump(", "scrollIntoView"):
        assert w in html, w
    assert (structure_svg.node_anchor(0), structure_svg.rel_anchor(0), structure_svg.core_anchor("record")) == (
        "dmo-o1", "dmo-r1", "dmo-core-record"), "app.html 의 id 꼴 = structure_svg 앵커"
    assert tokens in html and "prefers-color-scheme" not in tokens and "prefers-color-scheme" not in html, "토큰 = 테마 토글(data-theme)만"
    assert '[data-theme="dark"]' in tokens
    chip = html[html.index("const ORIGIN_CHIP = {"):html.index("};", html.index("const ORIGIN_CHIP = {"))]
    assert dict(re.findall(r'(\w+): \["[^"]*", "([^"]+)"\]', chip)) == structure_svg.ORIGIN_TEXT, "구분 용어 — 그래프 = 목록 칩"
    assert '<th scope="col">출처</th>' not in html and '<th scope="col">구분</th><th scope="col">보호</th>' in html, "필드 표 열 머리 = 구분(정의 구분 뜻의 '출처' 0)"
    sv = html[html.index("function structViews("):html.index("\n}", html.index("function structViews("))]   # 구조 탭 순서: 보기 전환 → 오브젝트 필(항목 보기만)
    assert sv.index('aria-label="구조 보기"') < sv.index("+ sub;") and 'subnav("");\n  const sub = `<nav class="pills sobjs" aria-label="구조 고르기">' in html \
        and '"구조 고르기");' not in html and 'structViews(tab, "defs", ' not in html and html.count('structViews(tab, "items", sub)') == 3, "보기 전환이 맨 위 · 오브젝트 필은 그 아래 항목 보기에서만"
    assert "min-width: 420px" in html and ".dmo { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px; overflow-x: auto; }" in html

    # A4 코어 표시명 — app.html LABELS(std@1 표시명 절의 화면 몫. web.CORE_LABEL 비교는 test_tools 에 남는다)
    assert all(f'{k}: "{v}"' in html for k, v in (("question", "질문"), ("record", "기록"), ("candidate", "후보"))), "app.html LABELS"

    # A5 24h 화면 — 플래그 0 · 이름 관문·nav·새 경로·탭 3 · 안내 문구 · 재검토 표시 · 대화창 둘(구간 밖) · 메뉴
    assert "FEATURE-OFF" not in html, "플래그 제거 — 화면에도 FEATURE-OFF 0"
    for w in ('if (needName()) return renderNameGate();', 'data-form="name"',
              "실명이 아니어도 돼요. 다른 사람에게 보이지 않고 운영자 화면에도 나오지 않아요.",
              '"/api/nav/accept"', '"/api/me/name"', '"/api/people', '"/api/reflections',
              'id: "_me"', 'id: "_obj"', 'id: "_settings"',
              "if (navNew()) return [NAVTAB._me, NAVTAB._today, NAVTAB._setup];",   # PR ①-b 탭 3: 나 · 오늘 · Setup(B1)
              'if (R.tab === "_settings") return "#_setup/structure";', "function axisChips",
              "출처를 접한 뒤 내가 한 생각만 적어요. 다른 사람의 말·경험·생각을 옮기는 곳이 아니에요.",
              "별칭도 그 사람을 알아볼 수 있으면 개인정보예요.", "삭제 완료"):
        assert w in html, w
    assert re.search(r"REFL_HELP\s*=\s*\"출처를 접한 뒤 내가 한 생각만 적어요", html) and re.search(r"PERSON_HELP\s*=\s*\"별칭도", html)
    assert html.count("${reviewNote(c)}") >= 4, "오늘·후보·기록·나 화면 재검토 표시"
    for dlg_id in ("name-dlg", "nav-dlg"):
        outside_marks(html.index(f'<dialog id="{dlg_id}"'), dlg_id)
    i = html.index('<dialog id="nav-dlg"')
    nav = html[i:html.index("</dialog>", i)]
    for w in ("「나」 홈", "「자료 종류」", "「내 구조 설정」", "데이터는 바뀌지 않아요", "되돌릴 수 없어요"):   # PR① 새 탐색 안내 = 탭 3(나·자료 종류·지도)
        assert w in nav, ("nav 대화창", w)
    menu = html[html.index('id="acct-menu"'):html.index("</details>")]
    for a in ("name", "nav", "settings"):
        assert f'data-acct="{a}"' in menu, a

    # B1 기본 밝은 화면 — OS 다크 설정·?theme 를 따르지 않는다. 어두운 화면은 토글(data-theme="dark")로만
    assert "prefers-color-scheme" not in html and "dataset.theme" not in html and html.count(':root[data-theme="dark"] {') >= 1, "app.html"
    assert '<html lang="ko">' in html and "data-theme" not in html.split("<head>", 1)[0], "app.html: 기본은 라이트"
    # B2 외부 셰이더 문자열 0(바이트) — 이 파일 자신에 걸리지 않게 조립한다
    raw = (HERE / "app.html").read_bytes()
    assert not [n for n in (b"shaders" + b".com", b"type" + b"gpu") if n in raw], "app.html 외부 셰이더 문자열 0"
    # B3 필수 동의 게이트 없음 — 화면에 CONSENT-REQUIRED·/api/consent·consent_grant·/consent 링크 0
    assert "CONSENT-REQUIRED" not in html and "/api/consent" not in html and "consent_grant" not in html, "app.html 동의 게이트 흔적 0"
    assert [ln for ln in html.splitlines() if "/consent" in ln] == [], "app.html /consent 줄 0"
    # B4 재인증 제거 — 화면에도 흔적 0
    for word in ("REAUTH", "reauth_ok", "auth_time", "_reauth_fresh", "state.ra", "재인증"):
        assert word not in html, f"정적: {word} 0"
    print("OK — test_tools 에서 옮긴 app.html 정적(A1 문의 esc·A2 계정 메뉴·A3 구조 정의·편입 대화창·A4 LABELS·A5 24h 화면·B1 라이트 기본·B2 셰이더 0·B3 동의 0·B4 재인증 0) 통과")


async def run():
    db.init_schema()
    cleanup()
    try:
        test_static()
        test_static_chat()
        test_chat_devices()
        test_chat_nodev_session()
        test_chat_excluded()
        test_static_moved()
        await test_records_api()
    finally:
        cleanup()
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
