# START — 받은 뒤 여기부터

이 패키지는 내 기록을 내 질문·내 화면으로 다시 쓰게 하는 로컬 키트입니다. 버전은 `manifest.json` 의 `version` 에 있습니다.
첫 성공은 설치 완료가 아닙니다. **내가 고른 기록 1편이 내 질문에 연결돼 내 탭에 보이고, 다음 세션에도 다시 찾는 것**입니다.

## 0. 지금 어느 단계인가

| 나는 지금 | 열 문서 |
|---|---|
| 사기 전, 나한테 맞는지 보는 중 | [guides/01-before-you-buy.md](guides/01-before-you-buy.md) |
| 처음 설치한다 (Mac) | [guides/02-install-macos.md](guides/02-install-macos.md) |
| 처음 설치한다 (Windows) | [guides/03-install-windows.md](guides/03-install-windows.md) |
| 설치는 됐고 내 목적·질문·탭을 정한다 | [guides/04-setup-conversation.md](guides/04-setup-conversation.md) |
| 매일·매주 쓰는 법 | [guides/05-daily-use.md](guides/05-daily-use.md) |
| 자료 폴더를 연결·변경한다 | [guides/06-connect-sources.md](guides/06-connect-sources.md) |
| 뭔가 깨졌다 | [guides/07-check-and-recover.md](guides/07-check-and-recover.md) |
| 업데이트·컴퓨터 이동·제거 | [guides/08-update-move-remove.md](guides/08-update-move-remove.md) |

## 1. 가장 짧은 길 (10단계)

명령은 Mac 기준 `python3`, Windows 는 `py` 로 바꿔 쓰세요.

1. 환경 점검 — `python3 canon.py doctor`
2. 샘플로 먼저 보기(선택) — `python3 canon.py init --data ~/canon-sample --sample --no-remember` 후 `python3 canon.py --data ~/canon-sample serve --open`
3. 내 데이터 폴더 만들기 — `python3 canon.py init --data ~/canon-data` (제품 폴더 **밖**에 둡니다)
4. 백업 한 번 — `python3 canon.py backup --label first`
5. Claude Code 를 이 폴더에서 열고 한 문장: **「canon-setup 스킬로 설정 시작해 줘」**
   — 읽을 자료와 모델 전송 범위를 먼저 묻고, 내 글에서 알 수 있는 건 추정으로 보여 주고, 모르는 것만 묻습니다.
6. 탭 미리보기를 보고 승인 — `python3 canon.py tabs apply`
7. 기록 1편 — 「canon-record 스킬로 기록 1편 남길게」
8. 정리 — 「canon-digest 스킬로 방금 기록 정리해 줘」 → 후보를 원문과 대조해 확인·제외
9. 다시 열기 — 새 터미널에서 `python3 canon.py serve --open`, 같은 결과가 보이면 `python3 canon.py setup done reopen`
10. 복구 리허설 — `python3 canon.py drill start` 를 따라 한 번 깨뜨리고 되돌리기

언제든 `python3 canon.py next` 가 지금 할 일 하나를 알려 줍니다. 중단해도 `python3 canon.py status` 에서 이어집니다.

## 2. 꼭 알아 둘 것

- **별도 구독이 필요합니다.** 정리(digest)는 내 Claude Code 구독의 `claude -p` 로 돕니다. 이 패키지 가격에 포함돼 있지 않습니다. API 키는 받지 않습니다.
- **모델 없이도 됩니다.** `digest --no-model` 은 기록 끝 `- 판단:` 줄만 후보로 만듭니다.
- **화면은 읽기 전용입니다.** 새로고침·탭 전환은 모델을 부르지 않고 파일을 바꾸지 않습니다.
- **AI 제안은 내 판단이 아닙니다.** `candidate confirm` 을 해야 "내가 확인한 판단"으로 옮겨집니다.
- **내 자료는 내 데이터 폴더에만 있습니다.** 제품 폴더를 지워도 기록·정리본·설정은 .md/.json 으로 남습니다.
- **Windows 는 아직 미검증입니다.** 지원표는 `manifest.json` 의 `support` 를 보세요.

명령 전체: `python3 canon.py --help`
