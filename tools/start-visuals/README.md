# 시작 화면 그림 만들기 (제작자 전용)

호스팅 대시보드의 시작(로그인) 화면 `/` 에 쓰는 그림 8장을 만든다(배경 루프·정지 × 라이트·다크, 데모·포스터 × 라이트·다크). 결과는 `server/assets/` 에 들어가고 서버가 `/assets/{name}` 으로 준다.
키트 사용자 배포물이 아니다(`manifest.json` 밖, `server/` 와 같은 취급). 원작자 Mac 에서 손으로 돌린다. CI 에서 다시 만들지 않는다 — CI 는 `server/test_tools.py` 가 존재·용량만 본다.

| 파일 | 무엇 | 상한 |
|---|---|---|
| `start-bg.webp` | 배경 루프(animated WebP, 1280×720 · 12fps · 8초, 끊김 없이 반복) | 400KB |
| `start-bg-still.webp` | 배경 정지 그림 — reduced-motion·「움직임 멈추기」 | 30KB |
| `start-bg-dark.webp` | 어두운 화면 배경 루프(640×360 · 12fps · 8초 — CSS cover 로 늘린다) | 400KB |
| `start-bg-dark-still.webp` | 어두운 화면 배경 정지 그림 | 30KB |
| `start-demo.webp` | 샘플 데이터로 찍은 실제 화면 데모(animated WebP, 1280w · 약 17초) | 800KB |
| `start-demo-poster.webp` | 데모 정지 그림(장면 ② 원문과 대조) | 60KB |
| `start-demo-dark.webp` | 어두운 화면 대시보드로 찍은 같은 데모 | 800KB |
| `start-demo-dark-poster.webp` | 어두운 화면 데모 정지 그림 | 60KB |
| `start-demo.json` | 테마별 촬영 때 `app.html` sha256·시각 `{light: {…}, dark: {…}}`. 서빙하지 않는다 | — |

페이지는 스크립트 0 이다. 셰이더를 브라우저에서 돌리지 않는다(런타임 번들 2.5MB·WebGPU 폴백 없음·telemetry). 미리 렌더한 그림만 받는다.

## 준비

- macOS, 시스템 `python3` 에 `playwright`(Chromium 포함)·`Pillow`. `brew install ffmpeg webp`(img2webp·cwebp·webpinfo).
- Node 22 · `cd tools/start-visuals/still && npm ci`(`node_modules` 는 커밋하지 않는다).
- 데모만: 로컬 postgres(`brew services start postgresql@17`, DB 하나 — 예 `createdb crm_test`) + server 의 파이썬(venv, `server/requirements.txt`).
  로컬 postgres 가 없으면 Playwright route 로 `/api/*` 를 모킹하는 대안이 있다(구현하지 않음).

## 배경 — `still/`

```
cd tools/start-visuals/still
python3 render.py                 # 라이트·다크 둘 다 → server/assets/start-bg{,-dark}{,-still}.webp
python3 render.py --theme dark    # 한 벌만
```

- `entry.js` 를 임시 폴더에 번들 → 로컬 http(127.0.0.1) → 헤드리스 Chromium(WebGPU) → `shaders/core` 의 `createRendererFromJSON` 으로 프레임 잠금 렌더(`renderFrame({deltaSeconds: 1/12})`).
  RAF·벽시계를 안 써서 프레임 간격이 일정하다.
- 끊김 없는 루프: 10초를 렌더하고 앞 2초를 뒤 2초와 섞는다(`render.py` `seamless`).
- 단정(어기면 exit 1, 파일 안 씀): `entry.js` 의 `disableTelemetry: true` · 번들에 telemetry 수집 경로 없음 · 127.0.0.1 밖 요청 0건 · 첫 프레임이 단색 아님 · 용량 상한.
  `shaders/core` 경로는 telemetry 모듈을 아예 읽지 않는다(그건 `shaders/js` 의 `createShader` 에만 있다). 실제 가드는 외부 요청 0건이다.
- 색은 보드 토큰(종이·초록·연보라) 안에서만 `entry.js` `STOPS` 로. npm `shaders` 안 컴포넌트만 쓴다 — 에디터 Pro 프리셋 붙여넣기 금지.
- 실험: `python3 render.py --out /tmp/x --speed 6 --q 85 --keep-frames /tmp/x/f` (상수 덮어쓰기, 레포 안 건드림).

실측(2026-10-18, 라이트 1280×720 · 12fps · 96프레임):

| 설정 | 결과 |
|---|---|
| speed 1(기본) | 8초 동안 거의 안 움직임 → speed 8 |
| q50, 부분 사각형 허용 | 3KB — 거의 같은 인접 프레임을 합쳐 96→15프레임(뚝뚝 끊김) |
| q95, 부분 사각형 허용 | 158KB · 95프레임 — 그러나 lossy 사각형이 겹쳐 쌓여 **사각 이음매 찌꺼기**가 보임 |
| **q80, `-kmax 1`(전부 키프레임)** | **236KB · 60프레임 — 라이트 채택** |
| 다크 1280×720 q80 `-kmax 1` | 316KB — 어두운 그라디언트가 8×8 블록으로 깨져 보임 |
| 다크 1280 q90 / 960×540 q90 | 859KB / 543KB — 상한 초과 |
| **다크 640×360 q90 `-kmax 1`** | **293KB · 96프레임 — 다크 채택**(cover 로 늘리며 보간돼 블록이 덜 보임. 정지 그림도 q90) |
| FilmGrain 0.06(bias 0, 움직이는 그레인) q95 | 830KB — 상한 초과. 화면에선 거의 안 보임(표준편차 3.21→3.24) → 그레인 끔 |

## 데모 — `demo/`

```
cd tools/start-visuals/demo
DATABASE_URL=postgresql://localhost/crm_test \
  python3 shoot.py --work /tmp/start-demo   # light/·dark/ 둘 다. 서버 파이썬 기본 = server/.venv/bin/python
#   다른 파이썬이면 --server-python <경로>(상대경로는 지금 폴더 기준으로 풀린다)
./encode.sh /tmp/start-demo/light light   # start-demo.webp · start-demo-poster.webp · start-demo.json[light]
./encode.sh /tmp/start-demo/dark dark     # start-demo-dark.webp · start-demo-dark-poster.webp · start-demo.json[dark]
```

- `seed.py`(server 파이썬): `DATABASE_URL` 호스트가 localhost·127.0.0.1 이 아니면 exit 1. 스키마 `CRM_TEST_SCHEMA`(기본 `start_demo`)를 DROP→CREATE 하고
  연결 문자열에 `options=-csearch_path=<스키마>` 를 얹어 그 안에서만 쓴다. 값은 `sample-data/` 와 같은 문장(상수). 촬영 사용자 `demo@local`.
  필수 동의 게이트는 없다 — 촬영 사용자에게 동의 행을 넣지 않아도 `/app` 이 열린다.
- `shoot.py`(시스템 python3): DEV 서버를 `--host 127.0.0.1 --port 8932` 로 띄운다(인증 없는 DEV — 0.0.0.0 금지). 브라우저 시계와 「불러온 시각」을 2026-10-15 23:05 KST 로 고정.
  장면 ① 기록 원문 ② 원문과 대조(후보·형광펜) ②' 「내 판단으로 남기기」 클릭 링(촬영 때만 `add_style_tag`) ③ 남긴 뒤(실제 POST) ④ 구조 탭 내 판단. 127.0.0.1 밖 요청 0건 단정.
- `encode.sh`: 장면 PNG(2x) → 1280w · 12fps · 홀드 + 0.33초 페이드(클릭 전후는 컷) → img2webp(`-kmax 1`, lossy q70). 포스터는 장면 ② 한 장.
- 다크 촬영: `localStorage["crm-theme"]="dark"` 를 미리 넣어 theme.js 가 어두운 화면으로 연 대시보드를 같은 시나리오로 찍는다(시드부터 다시).
  실측: 라이트 431KB · 다크 421KB, 포스터 31.9KB · 31.0KB.
  실측: 16.8초 · 202프레임 → WebP 14프레임 · 431KB, 포스터 32KB. 부분 사각형을 허용하면 349KB 지만 오른쪽·아래에 찌꺼기 줄이 남았다.

## 언제 다시 만드나

- `server/app.html` 을 바꾼 PR 은 데모를 다시 찍을지 본문에 적는다. `test_tools.py` 는 `start-demo.json` 의 테마별 `app_sha256` 이 지금 `app.html` 과 다르면 **경고만** 낸다.
- 그림을 바꾸면 파일 sha 가 바뀌어 `?v=` 가 바뀐다 — 브라우저 캐시(1년 immutable)는 새 주소로 자동으로 넘어간다.
- 나중에 CSP `default-src` 를 넣으면 `img-src 'self'` 가 필요하다(지금은 같은 출처라 영향 없음).

## 대역폭(첫 방문)

데스크톱·모바일 같음, 고른 테마 것만 받는다: HTML + theme.js 2KB + 배경 루프(라이트 236KB · 다크 293KB) + 데모(라이트 431KB · 다크 421KB)
≈ 라이트 0.67MB · 다크 0.72MB. reduced-motion 이면 정지 그림 2장 ≈ 35KB.
「움직임 멈추기」를 누르면 정지 그림 2장을 그때 받는다. 데모 그림 4장(테마 × 움직임·정지)은 모두 `loading="lazy"` 라
`display:none` 인 3장은 받지 않는다(Playwright 요청 수로 확인).

## 테마 토글(`server/assets/theme.js`)

손으로 쓰는 파일이다(렌더 결과 아님). 시작·도움말·대시보드 `<head>` 가 `/assets/theme.js?v=…` 로 읽는 유일한 외부(self) 스크립트.
기본은 밝은 화면이고 OS 다크 설정은 따르지 않는다. ☀/☾ 버튼(`[data-theme-toggle]`)이 `<html data-theme="dark">` 를 붙이고 떼며,
선택은 `localStorage["crm-theme"]` 에 기억한다(막혀 있으면 이번 화면에만). 데모 그림은 테마별 촬영본을 CSS 로 바꿔 보인다(다크에서 데모 창만 하얗게 뜨지 않게).

## 시작 화면 구성(시안 A 「히어로」, `server/start.html`)

배경 무대 = 루프 WebP(채도·밝기 필터) + 색 덩어리 3개(@keyframes 표류) + SVG 노이즈(data URI) + 비네팅. 3단계 칩 · h1 · 글로우 로그인 버튼 ·
첫 화면 아래에 걸친 데모. h1·lead 문구는 start.html 의 `<!-- 문구 -->` 주석 아래 한 곳만 바꾼다(`<em>` 이 강조색).
「움직임 멈추기」 체크는 CSS `:has()` 로 배경 루프·색 덩어리·데모를 같이 멈춘다(`:has` 없는 옛 브라우저에선 멈추지 않는다 — reduced-motion 은 그와 무관하게 정지).
