# 02. 설치 — macOS

터미널(응용 프로그램 › 유틸리티 › 터미널)에서 한 줄씩 실행합니다.

## 1. Python 3.9 이상

```bash
python3 --version
```

`Python 3.9` 이상이 나오면 다음으로. 없거나 낮으면 python.org 에서 macOS 설치 파일을 받거나, Homebrew 가 있으면 `brew install python`.

## 2. Claude Code (정리 기능용)

```bash
claude --version
```

없으면 Claude Code 공식 설치 안내를 따라 설치하고, `claude` 를 한 번 열어 `/login` 합니다.
구독은 별도입니다. 없어도 화면·기록·`--no-model` 정리는 됩니다.

## 3. 패키지 위치 정하기

받은 폴더를 원하는 곳에 둡니다. 예: `~/Applications/canon-kit`. 한글·공백이 들어간 경로도 됩니다.

```bash
cd ~/Applications/canon-kit
python3 canon.py doctor
```

`✗ 실패` 줄이 있으면 그 아래 `→` 조치를 먼저 합니다. `! 경고` 는 진행해도 됩니다.

## 4. 데이터 폴더 만들기

내 기록이 쌓일 곳입니다. 제품 폴더 **밖**이어야 합니다 — 업데이트 때 제품 폴더는 통째로 바뀝니다.

```bash
python3 canon.py init --data ~/canon-data
python3 canon.py backup --label first
```

`init` 은 이 위치를 제품 폴더의 `local.json` 에 기억합니다. 이미 있는 파일은 건드리지 않습니다.
Claude Code 전역 설정(`~/.claude`)은 바꾸지 않습니다.

## 5. 확인

```bash
python3 canon.py doctor
python3 canon.py serve --open
```

브라우저에 "시작" 탭이 뜨면 설치 끝. 끄려면 터미널에서 `Ctrl+C`. 다음은 [04-setup-conversation.md](04-setup-conversation.md).

## 막히면

| 증상 | 조치 |
|---|---|
| `command not found: python3` | 1절 다시 |
| `ENV-CLI 실패` | 2절. 화면만 쓸 거면 넘어가도 됨 |
| `ENV-PORT 경고` | `python3 canon.py serve --port 8766` |
| `DATA-WRITE 실패` | 데이터 폴더를 홈 아래 다른 곳으로 |

그래도 안 되면 [07-check-and-recover.md](07-check-and-recover.md).
