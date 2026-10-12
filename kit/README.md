# kit — 대시보드 서버 시험 사본

원작자가 쓰던 로컬 대시보드 서버에서 개인 기능을 잘라낸 사본이다. 목적은 하나다 — 원작자 데이터 없이
`/api/state` 가 뜨는지 확인하는 것. 탭을 고르는 구조(블록·탭 설정)는 아직 없다.

## 실행

```bash
CANON_ROOT=~/my-canon CANON_PORT=8797 python3 kit/checkpoint_board.py --no-open
```

`CANON_ROOT` 는 데이터 루트다. 없으면 repo 의 `data/` 를 쓴다. 처음이면 `kit/sample-data/` 를 복사해서
시작한다. 로그·캐시는 `<CANON_ROOT>/.runtime/` 에 쌓인다. python 3.9 에서 돈다(stdlib 만).

| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `CANON_ROOT` | `<repo>/data` | 데이터 루트 |
| `CANON_PORT` | 8787 | 서버 포트 |
| `CANON_OWNER` | 나 | 워커 프롬프트에 들어가는 이름 |
| `CANON_SLACK_CHANNEL` | (없음) | 비었으면 Slack 알림 끔 |

모델 호출은 전부 `claude -p`(구독)다. `~/.local/bin/claude` 가 없으면 PATH 에서 찾는다.

## 잘라낸 것

카톡·메일·예약·이직 기능 — 서버 함수·라우트·경로 상수, 화면의 하위 탭·렌더·배지, 실행 워커의 카톡 분기,
파이프라인 상태의 해당 잡 코드. 원작자 프로젝트 폴더를 읽던 계보 분기와 하드코딩된 관심 축도 뺐다(축은
예시 하나). 지도(`/map`)와 360 화면(`/360`)은 템플릿을 싣지 않아 404 로 안내한다. 수집기는 없다.

## 시험 결과 (2026-10-12, sample-data, 포트 8797)

| 요청 | 상태 |
|---|---|
| GET `/` | 200 |
| GET `/api/state` | 200 |
| GET `/api/structure` | 200 |
| GET `/api/models` | 200 |
| POST `/api/logs` | 200 |
| GET `/360` · `/map` | 404 (kit 미포함 안내) |
| POST `/api/resv` · `mail_seen` · `move_seen` · `kakao_review` · `kakao_resummarize` | 404 (잘라냄) |

headless chromium 으로 탭 7개·하위 탭 3개를 눌러 본 결과 페이지 오류·콘솔 오류 0건.
