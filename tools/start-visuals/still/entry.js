// 시작 화면 배경 루프 — 렌더 전용 entry. 페이지(server/start.html)에는 이 코드가 들어가지 않는다. 결과 WebP 만 들어간다.
// 경로: shaders/core 의 createRendererFromJSON — 프레임 잠금 렌더(renderFrame({deltaSeconds})).
//   시간이 RAF·벽시계가 아니라 우리가 넘긴 delta 로만 흐른다 → 헤드리스에서도 프레임 간격이 일정하다.
//   이 경로는 telemetry 모듈을 import 하지 않는다(telemetry 는 shaders/js 의 createShader 에만 있다).
//   그래도 옵션은 명시한다 — render.py 가 이 문자열을 확인하고, 진짜 가드는 render.py 의 외부 요청 0건 단정이다.
// 색: 보드 토큰 안에서만. 라이트 = 종이 #F7F8F6 · 초록 --accent-soft 계열 · 연보라 --cand-line #D9D6EE,
//      다크 = 종이 #15181A · 초록 --accent-soft #1C2E26 · 연보라 --ai-bg #272437. Pro 프리셋 붙여넣기 금지.
import { createRendererFromJSON } from "shaders/core";

const OPTIONS = { disableTelemetry: true };

const stops = (paper, green, lav) => [
  { color: paper, position: 0 },
  { color: green, position: 0.3 },
  { color: paper, position: 0.52 },
  { color: lav, position: 0.78 },
  { color: paper, position: 1 },
];
export const STOPS = {
  light: stops("#F7F8F6", "#DDEEE4", "#D9D6EE"),
  dark: stops("#15181A", "#1C2E26", "#272437"),
};

function preset(theme, grain, speed) {
  const components = [
    { type: "MeshGradient", id: "mesh", props: { stops: STOPS[theme], count: 5, smoothness: 2.4, swirl: 0.35, drift: 0.5, speed, seed: 7 } },
  ];
  if (grain > 0) components.push({ type: "FilmGrain", id: "grain", props: { strength: grain, bias: 0, animated: true } });
  return { components };
}

// render.py 가 부른다: init → step(dt) 반복 → 매 step 뒤 캔버스를 읽는다.
window.__bg = {
  async init(theme, width, height, grain, speed) {
    if (!STOPS[theme]) throw new Error("모르는 테마: " + theme);
    const canvas = document.getElementById("c");
    canvas.width = width; canvas.height = height;
    canvas.style.width = width + "px"; canvas.style.height = height + "px";
    const r = createRendererFromJSON(preset(theme, grain, speed), OPTIONS);
    await r.initialize(canvas);
    const why = r.getFailureReason && r.getFailureReason();
    if (why) throw new Error("shader 실패: " + why);
    this.r = r;
    await r.renderFrame({ deltaSeconds: 0, waitForGpu: true });
    return true;
  },
  async step(dt) {
    await this.r.renderFrame({ deltaSeconds: dt, waitForGpu: true });
    return document.getElementById("c").toDataURL("image/png");
  },
};
window.__ready = true;
