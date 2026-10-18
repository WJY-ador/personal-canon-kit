"""시작 화면 배경 루프 렌더 — shaders(MeshGradient)를 헤드리스 Chromium WebGPU 로 프레임 단위 렌더 → 끊김 없는 animated WebP.

실행(원작자 Mac, 수동): 시스템 python3 (playwright·Pillow 필요) — README 참고.
    cd tools/start-visuals/still && npm ci && python3 render.py            # 라이트·다크 둘 다 server/assets/ 에 씀
    python3 render.py --theme dark                                          # 한 벌만
    python3 render.py --out /tmp/x --grain 0.06 --q 40                     # 실험(상수 덮어쓰기)
CI 에서 돌리지 않는다(ubuntu WebGPU 미확인). CI 는 server/test_tools.py 가 존재·용량만 본다.

단정(하나라도 어기면 exit 1, 출력 파일을 쓰지 않는다):
  · entry.js 에 `disableTelemetry: true` 문자열 · 번들에 telemetry 수집 경로 없음
  · 127.0.0.1 밖으로 나간 요청 0건(route 로 전부 끊고 기록)
  · 셰이더 초기화 성공 · 캡처 프레임이 비지 않음
  · 루프 ≤ MAX_LOOP, 포스터 ≤ MAX_POSTER
루프: LOOP_S + FADE_S 초를 렌더하고, 앞 FADE_S 초를 뒤 FADE_S 초와 섞는다 → 마지막 프레임 다음이 첫 프레임과 이어진다.
"""
import argparse
import asyncio
import base64
import functools
import http.server
import io
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
ASSETS = REPO / "server" / "assets"

# ── 최종값(용량 게이트 순서: 해상도↓ → fps↓ → 길이↓). 바꾸면 README 표도 고친다. ──
# 테마별 해상도·품질. 다크는 어두운 그라디언트가 8×8 블록으로 깨져 보여(1280 q80, 실측) 640×360 q90 으로 —
# 배경은 CSS cover 로 늘어나며 브라우저가 부드럽게 보간한다. 1280 q90 은 859KB, 960 q90 은 543KB 로 상한 초과.
THEME_SIZE = {"light": (1280, 720, 80), "dark": (640, 360, 90)}
FPS = 12
LOOP_S = 8          # 루프 길이(초)
FADE_S = 2          # 이음매 크로스페이드(초)
GRAIN = 0.0         # FilmGrain strength(0 = 끔). 0.06(bias 0)은 830KB 로 상한 초과 — README 실측표
SPEED = 8.0         # MeshGradient speed(기본 1 은 8초에 거의 안 움직인다)
# img2webp lossy 품질은 THEME_SIZE 셋째 값. 낮을수록 "거의 같은" 인접 프레임을 합친다(q50: 96→15프레임 = 뚝뚝 끊김, q80: 60프레임)
# 정지 그림 품질 = 그 테마의 루프 품질(다크는 q70 이면 블록이 보인다)
MAX_LOOP = 400 * 1024
MAX_POSTER = 30 * 1024
TELEMETRY_PATH = "/api/" + "telemetry"   # 수집 주소의 경로. 번들엔 이미지·영상 기본값(샘플 URL)이 있어 호스트명만으로는 못 가린다

PAGE = """<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#fff}canvas{display:block}</style>
<canvas id="c"></canvas><script type="module" src="bundle.js"></script>"""


def die(msg):
    print("실패:", msg, flush=True)
    sys.exit(1)


def build(build_dir: Path) -> None:
    entry = (HERE / "entry.js").read_text(encoding="utf-8")
    if "disableTelemetry: true" not in entry:
        die("entry.js 에 disableTelemetry: true 가 없어요")
    esb = HERE / "node_modules" / ".bin" / "esbuild"
    if not esb.exists():
        die("node_modules 가 없어요 — tools/start-visuals/still 에서 npm ci")
    build_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(esb), str(HERE / "entry.js"), "--bundle", "--format=esm", f"--outfile={build_dir / 'bundle.js'}",
                    "--log-level=warning"], check=True, cwd=HERE)
    bundle = (build_dir / "bundle.js").read_text(encoding="utf-8")
    if TELEMETRY_PATH in bundle:
        die("번들에 telemetry 주소가 들어 있어요 — entry 가 shaders/core 만 import 하는지 확인")
    (build_dir / "index.html").write_text(PAGE, encoding="utf-8")
    print(f"번들 {len((build_dir / 'bundle.js').read_bytes()):,} B (렌더 전용, 레포에 안 올림)")


async def capture(build_dir: Path, theme: str, w: int, h: int, grain: float, speed: float, n: int, dt: float) -> list[Image.Image]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(build_dir))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}/"
    external: list[str] = []
    frames: list[Image.Image] = []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(channel="chromium", headless=True, args=["--enable-unsafe-webgpu", "--enable-features=Vulkan"])
            pg = await b.new_page(viewport={"width": w, "height": h})

            async def route(r):
                if r.request.url.startswith(base):
                    await r.continue_()
                else:
                    external.append(r.request.url)
                    await r.abort()
            await pg.route("**/*", route)
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            await pg.goto(base + "index.html")
            await pg.wait_for_function("window.__ready === true", timeout=20000)
            if not await pg.evaluate("!!navigator.gpu"):
                die("이 브라우저에 WebGPU 가 없어요")
            await pg.evaluate("([t,w,h,g,s]) => window.__bg.init(t,w,h,g,s)", [theme, w, h, grain, speed])
            for i in range(n):
                url = await pg.evaluate("(dt) => window.__bg.step(dt)", dt)
                img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
                if i == 0 and img.getextrema() in (((0, 0),) * 3, ((255, 255),) * 3):
                    die("캡처 프레임이 비었어요(단색) — WebGPU 캔버스 읽기 실패")
                frames.append(img)
            await b.close()
            if errors:
                die(f"페이지 오류: {errors}")
    finally:
        srv.shutdown()
    print(f"외부 요청 {len(external)}건", external)
    if external:
        die("127.0.0.1 밖 요청이 있었어요")
    return frames


def seamless(frames: list[Image.Image], loop_n: int, fade_n: int) -> list[Image.Image]:
    """frames 길이 = loop_n + fade_n. out[i] = mix(frames[loop_n+i], frames[i], i/fade_n) (i < fade_n), 나머지는 그대로.
    out[loop_n-1] 다음은 frames[loop_n] ≈ out[0] 이 이어지므로 이음매가 없다."""
    out = []
    for i in range(loop_n):
        if i < fade_n:
            out.append(Image.blend(frames[loop_n + i], frames[i], i / fade_n))
        else:
            out.append(frames[i])
    return out


# 테마 → (루프, 정지) 파일 이름. server/web.py ASSET_FILES 와 같아야 한다.
NAMES = {"light": ("start-bg.webp", "start-bg-still.webp"), "dark": ("start-bg-dark.webp", "start-bg-dark-still.webp")}


def encode(frames: list[Image.Image], fps: int, q: int, poster_q: int, tmp: Path) -> tuple[Path, Path]:
    for i, f in enumerate(frames):
        f.save(tmp / f"{i:04d}.png")
    delay = round(1000 / fps)
    loop = tmp / "start-bg.webp"
    # -kmax 1: 모든 프레임을 키프레임으로. 부분 사각형(lossy)을 겹쳐 쌓으면 그라디언트에 사각 이음매 찌꺼기가 남는다(실측, q95 에서도).
    cmd = ["img2webp", "-loop", "0", "-kmax", "1", "-lossy", "-q", str(q), "-m", "6", "-d", str(delay)]
    cmd += [str(tmp / f"{i:04d}.png") for i in range(len(frames))] + ["-o", str(loop)]
    subprocess.run(cmd, check=True, capture_output=True)
    poster = tmp / "start-bg-still.webp"
    subprocess.run(["cwebp", "-quiet", "-q", str(poster_q), "-m", "6", str(tmp / "0000.png"), "-o", str(poster)], check=True)
    return loop, poster


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ASSETS))
    ap.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    ap.add_argument("--width", type=int, help="기본: THEME_SIZE")
    ap.add_argument("--height", type=int, help="기본: THEME_SIZE")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--seconds", type=float, default=LOOP_S)
    ap.add_argument("--fade", type=float, default=FADE_S)
    ap.add_argument("--grain", type=float, default=GRAIN)
    ap.add_argument("--speed", type=float, default=SPEED)
    ap.add_argument("--q", type=int, help="기본: THEME_SIZE")
    ap.add_argument("--poster-q", type=int, help="기본: 루프 품질과 같게")
    ap.add_argument("--keep-frames", help="섞은 프레임 PNG 를 이 폴더에 남긴다(비교용)")
    a = ap.parse_args()
    if not shutil.which("img2webp") or not shutil.which("cwebp"):
        die("img2webp·cwebp 가 없어요 — brew install webp")
    for theme in (["light", "dark"] if a.theme == "both" else [a.theme]):
        one(a, theme)


def one(a, theme: str):
    loop_name, still_name = NAMES[theme]
    tw, th, tq = THEME_SIZE[theme]
    w, h, q = a.width or tw, a.height or th, a.q or tq
    print(f"── {theme}")
    loop_n, fade_n = round(a.seconds * a.fps), round(a.fade * a.fps)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        build(tmp / "build")
        raw = asyncio.run(capture(tmp / "build", theme, w, h, a.grain, a.speed, loop_n + fade_n, 1 / a.fps))
        frames = seamless(raw, loop_n, fade_n)
        (tmp / "f").mkdir()
        pq = a.poster_q or q
        loop, poster = encode(frames, a.fps, q, pq, tmp / "f")
        ls, ps = loop.stat().st_size, poster.stat().st_size
        print(f"루프 {w}x{h} {a.fps}fps {loop_n}프레임({a.seconds:g}초, 이음 {a.fade:g}초) grain={a.grain} speed={a.speed:g} q={q} → {ls:,} B")
        kept = subprocess.run(["webpinfo", str(loop)], capture_output=True, text=True).stdout.count("Chunk ANMF")
        print(f"  WebP 안 프레임 {kept}개(거의 같은 인접 프레임은 합쳐짐)")
        print(f"포스터 q={pq} → {ps:,} B")
        if a.keep_frames:
            kf = Path(a.keep_frames)
            kf.mkdir(parents=True, exist_ok=True)
            for p in (tmp / "f").glob("*.png"):
                shutil.copy(p, kf / p.name)
        if ls > MAX_LOOP:
            die(f"루프가 상한 {MAX_LOOP:,} B 를 넘어요 — 해상도↓ → fps↓ → 길이↓ 순으로 줄이세요")
        if ps > MAX_POSTER:
            die(f"포스터가 상한 {MAX_POSTER:,} B 를 넘어요")
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(loop, out / loop_name)
        shutil.copy(poster, out / still_name)
        print("썼어요:", out / loop_name, out / still_name)


if __name__ == "__main__":
    main()
