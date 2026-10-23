# My CRM (가칭) · v0.1.0

내 기록을 내 질문·내 화면으로 다시 쓰게 하는 로컬 키트. Windows·macOS 목표, Python 표준 라이브러리만 씁니다.
**받았으면 [START.md](START.md) 부터.**

> 상태: 2026-10-18 구조 전환 중 — 로컬 설치형에서 **호스팅 + MCP**(ChatGPT·Claude 구독으로 연결, 데이터는 서버)로 옮깁니다. 아래는 전환 전 로컬 판 설명입니다. 결정 요지는 [DECISIONS-PENDING.md](DECISIONS-PENDING.md).
> 가격·라이선스·지원 정책은 미정입니다 — [DECISIONS-PENDING.md](DECISIONS-PENDING.md).

## 무엇을 하나

```
내가 고른 기록 1편 ──record──▶ 일지(원문 보존)
                    ──digest──▶ 판단 후보 (대기 · AI 제안/내 메모 구분, 근거 인용 검사)
                    ──confirm─▶ 질문별 한 장 (내가 확인한 판단만 "내 판단")
                    ──serve───▶ 내 말로 이름 붙인 탭에서 다시 보기 (읽기 전용)
```

깨지면 `doctor` → 다음 조치 1개 → `restore` → 같은 화면 재조회.

대시보드는 왼쪽에 질문(기록형 탭이면 기록) 목록, 오른쪽에 고른 질문의 내 판단을 두는 2단 화면입니다. 아직 안 정한 문장은 「원문과 대조하기」에서 근거 원문과 나란히 한 장씩 봅니다. 이 버전의 대조 화면은 보기만 하고, 남기기·빼기는 `candidate` 명령으로 합니다.

## 구조 — 제품과 내 데이터는 따로

| 제품 폴더 (업데이트 때 바뀜) | 데이터 폴더 (내 것, 제품 밖) |
|---|---|
| `crm.py` 명령 | `profile.json` 확인/추정/미입력/꺼 둠 |
| `crmkit/` 코어 | `questions.json`·`sources.json`·`tabs.json` |
| `web/` 대시보드 화면 | `journal/*.md` 원문 기록 |
| `templates/` 양식 | `candidates.json` 판단 후보 |
| `guides/` 가이드 8개 | `sheets/*.md` 질문별 한 장 |
| `.claude/skills/` 스킬 4개 | `issue-log.md` |
| `sample-data/` 가짜 샘플 | `.crm/` 백업·실행 상태 (정본 아님) |
| `agent/` 로컬 에이전트(호스팅 판 연결 장치, 선택) | 에이전트 홈 — 원문·대응표·전송 대기(제품 밖, [agent/README.md](agent/README.md)) |
| `manifest.json` 파일 목록·지원표 | |

## 경계 (코드로 지키는 것)

- 쓰기는 `crm.py` 명령만. 대시보드는 GET 만 받고, `127.0.0.1` 에만 붙고, 파일을 쓰지 않고, 모델을 부르지 않습니다.
- 모든 쓰기: 잠금(`O_EXCL` 파일) → 임시 파일 → 스키마 검사 → `os.replace`. 검사 실패 시 기존 파일 유지.
- 모델 호출은 `digest` 한 곳, 구독 `claude -p` 로만. API 키 입력 칸 없음. 보내는 것은 기록 1편 + 질문 문장.
- AI 후보의 근거 인용은 원문에 글자 그대로 있어야 저장. 같은 후보 중복 생성 안 함.
- 파일 읽기는 `consent --read` 로 동의한 경로 안만. 모델 전송 동의는 따로.
- 표시 이름(탭 label)과 내부 ID(`t-001`) 분리 — 이름을 바꿔도 링크 유지.
- `restore` 는 지금 상태를 먼저 백업하고, 백업 이후 새 기록은 지우지 않습니다.

## 시험

```bash
python3 tests/test_flow.py                   # 흐름 시험 (모델은 가짜 CLI 로)
python3 crm.py check                        # 패키지 검사: manifest·원작자 값·문서 속 명령
python3 -m unittest discover -s agent/tests  # 로컬 에이전트 시험 (Keychain·launchd 는 모의 실행기)
```

## 로컬 에이전트 (호스팅 판, 선택)

`agent/` 는 호스팅 판 계정에 내 PC 를 「연결 장치」로 붙이는 프로그램입니다. 수집기 산출물(첫 입력원은 좋아요 `likes.jsonl`)을 활동으로 바꿔 보내고, 다른 사람의 원문은 PC 에 둡니다. macOS 는 1시간 예약(launchd)과 Keychain 을 씁니다. 표준 라이브러리만 씁니다. 쓰는 법은 [agent/README.md](agent/README.md).

웹 「대화」 정리는 서버 스위치가 켜져 있고, PC 에서 `MYCRM_JOBS_ALLOW_REAL_CLI=1 python3 -m agent serve --jobs` 로 켭니다 — 자기 PC·자기 구독 CLI 로 돕니다. 쓰는 법은 [guides/09-hosted-start.md](guides/09-hosted-start.md) 2-1절.

루틴(예약 실행)의 `claude -p` 요약·정리는 사람 없이 돕니다. 조건: ① 루틴 승인을 받은 범위만 실행 ② 판단 확정은 사람이 확인 1번으로만 — 무인 확정 없음 ③ 무인으로 쓴 값은 「루틴이 썼음」 출처 표시 · [삭제]로 되돌릴 수 있음 · 판단 근거로 인용 불가 ④ 내용 제한(타인 식별 정보 제거)은 생성 단계부터 ⑤ PC 실행 시도 10회/일 ⑥ 스위치 기본 꺼짐. 2026-10-22 결정.
(출시 전 확인: 구독 CLI 무인 실행 약관)

## 이 버전에 없는 것

로컬 판의 자동 수집·계정 연결(로컬 에이전트는 호스팅 판 전용), 로컬 판 예약 실행·알림, 대시보드 안 채팅·쓰기, 결제, 클라우드·팀 기능.
