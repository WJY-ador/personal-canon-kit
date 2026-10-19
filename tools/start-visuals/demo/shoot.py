"""데모 촬영 — 로컬 DEV 서버(127.0.0.1 만) + 샘플 시드로 실제 app.html 장면 4개를 찍는다. 합성은 encode.sh.

실행(원작자 Mac, 수동): 시스템 python3 (playwright 필요). 서버·시드는 server 의 파이썬(venv)으로 띄운다.
    DATABASE_URL=postgresql://localhost/crm_test python3 shoot.py --work /tmp/demo   # 서버 파이썬 기본 = server/.venv/bin/python(--server-python 은 상대경로도 된다)
    ./encode.sh /tmp/demo/light light && ./encode.sh /tmp/demo/dark dark
테마: 라이트·다크 둘 다 같은 시나리오로 찍는다(--theme 로 한 벌만). 다크는 localStorage["crm-theme"]="dark" 를 미리 넣어
     theme.js 가 <html data-theme="dark"> 를 붙인 상태 — 시작 화면이 어두울 때 데모 창만 하얗게 뜨지 않게.
     테마마다 시드부터 다시 한다(「남기기」가 실제 POST 라 상태가 바뀐다).
가드: DATABASE_URL 호스트 localhost·127.0.0.1 만(seed.py 와 같은 규칙). 서버는 --host 127.0.0.1(인증 없는 DEV 라 0.0.0.0 금지).
단정: 127.0.0.1:PORT 밖 요청 0건(route 로 끊고 기록) · 「남기기」 POST 200.
장면: ① 기록 탭 원문 ② 원문과 대조(후보 카드·인용 형광펜) ②' 「내 판단으로 남기기」 클릭 링 ③ 남긴 뒤 ④ 구조 탭 내 판단.
      클릭 링은 촬영 때만 add_style_tag 로 얹는다 — app.html 은 바꾸지 않는다.
화면 시계는 2026-10-15 23:05 KST 로 고정(시드 날짜와 맞춘다) — 브라우저 Date 와 /api/state 의 generated 둘 다.
"""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SERVER = REPO / "server"
ASSETS = SERVER / "assets"
PORT = 8932
BASE = f"http://127.0.0.1:{PORT}"
VIEW = {"width": 1280, "height": 720}
DSF = 2
CLOCK = "2026-10-15T23:05:00+09:00"
RING_CSS = """.demo-ring { outline: 3px solid #186A4B !important; outline-offset: 3px; box-shadow: 0 0 0 9px rgba(24,106,75,.18) !important; }"""


def die(msg):
    print("실패:", msg, flush=True)
    sys.exit(1)


def guard() -> str:
    base = os.environ.get("DATABASE_URL", "")
    if urlsplit(base).hostname not in ("localhost", "127.0.0.1"):
        die("DATABASE_URL 호스트가 localhost·127.0.0.1 이 아니에요")
    return base


def start_server(py: str, base_url: str) -> subprocess.Popen:
    sys.path.insert(0, str(HERE))
    from seed import guarded_url   # 같은 가드·같은 search_path 규칙
    _, _, url = guarded_url()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUTHKIT_", "CRM_SESSION"))}
    env.update(DATABASE_URL=url, CRM_DEV="1", CRM_DEV_USER="demo@local", CRM_PUBLIC_URL=BASE, PYTHONUNBUFFERED="1")
    subprocess.run([py, str(HERE / "seed.py")], check=True, env={**os.environ, "DATABASE_URL": base_url})
    proc = subprocess.Popen([py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
                            cwd=SERVER, env=env)
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=1) as r:
                if r.status == 200:
                    return proc
        except OSError:
            time.sleep(0.5)
    proc.terminate()
    die("DEV 서버가 뜨지 않았어요")


async def shoot(work: Path, theme: str):
    external: list[str] = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport=VIEW, device_scale_factor=DSF, locale="ko-KR", timezone_id="Asia/Seoul")
        await ctx.add_init_script(f"try {{ localStorage.setItem('crm-theme', '{theme}'); }} catch (e) {{}}")
        pg = await ctx.new_page()
        await pg.clock.set_fixed_time(CLOCK)

        async def route(r):
            if r.request.url.startswith(BASE + "/api/state"):   # 「불러온 시각」(서버 generated)도 화면 시계에 맞춘다
                resp = await r.fetch()
                body = await resp.json()
                body["generated"] = CLOCK
                await r.fulfill(response=resp, json=body)
            elif r.request.url.startswith(BASE + "/"):
                await r.continue_()
            else:
                external.append(r.request.url)
                await r.abort()
        await pg.route("**/*", route)
        st = await (await pg.request.get(BASE + "/api/state")).json()
        pend = [c for c in st["candidates"] if c["status"] == "pending"]
        if len(pend) != 2:
            die(f"시드가 이상해요 — 대기 후보 {len(pend)}개")
        qid, cid, rid = pend[0]["question_id"], pend[0]["id"], pend[0]["record_id"]

        async def go(hash_, name):
            await pg.goto(f"{BASE}/app{hash_}")
            await pg.wait_for_load_state("networkidle")
            await pg.wait_for_timeout(500)
            await pg.screenshot(path=str(work / f"{name}.png"))
            print("장면", name, hash_)

        await go(f"#journal/{rid}", "s1")                       # ① 기록 원문
        await go(f"#today/review/{qid}/{cid}", "s2")            # ② 후보 + 인용 형광펜 대조
        await pg.add_style_tag(content=RING_CSS)
        btn = pg.locator('button[data-act="confirm"]').first
        await btn.evaluate("b => b.classList.add('demo-ring')")
        await pg.wait_for_timeout(200)
        await pg.screenshot(path=str(work / "s2b.png"))
        print("장면 s2b 클릭 링")
        async with pg.expect_response(lambda r: r.url.endswith("/api/candidate/decide")) as resp:
            await btn.click()
        if (await resp.value).status != 200:
            die("「남기기」 POST 가 200 이 아니에요")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(600)
        await pg.screenshot(path=str(work / "s3.png"))
        print("장면 s3 남긴 뒤", pg.url)
        await go("#structure/cand/confirmed", "s4")             # ④ 구조 — 내 판단
        await b.close()
    print(f"외부 요청 {len(external)}건", external)
    if external:
        die("127.0.0.1 밖 요청이 있었어요")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-python", default=os.environ.get("CRM_SERVER_PYTHON", str(SERVER / ".venv" / "bin" / "python")))
    ap.add_argument("--work", required=True, help="장면 PNG 를 둘 폴더(레포 밖). 테마별 하위 폴더 light/·dark/ 에 쓴다")
    ap.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    a = ap.parse_args()
    # 서버는 cwd=server/ 에서 뜬다 — 상대경로는 지금 폴더 기준 절대경로로 바꾼다(심볼릭 링크는 풀지 않는다: venv python 이 링크다)
    a.server_python = os.path.abspath(a.server_python)
    base = guard()
    for theme in (["light", "dark"] if a.theme == "both" else [a.theme]):
        work = Path(a.work) / theme
        work.mkdir(parents=True, exist_ok=True)
        print(f"── {theme}")
        proc = start_server(a.server_python, base)
        try:
            asyncio.run(shoot(work, theme))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        meta = {"app_sha256": hashlib.sha256((SERVER / "app.html").read_bytes()).hexdigest(),
                "shot_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
        (work / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("meta.json →", work / "meta.json", f"(encode.sh {theme} 가 server/assets/start-demo.json 의 {theme} 칸에 넣는다)")


if __name__ == "__main__":
    main()
