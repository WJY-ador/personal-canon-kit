# agent — 로컬 에이전트 (호스팅 판 「연결 장치」)

내 PC 를 호스팅 판 계정에 「연결 장치」로 붙이는 프로그램입니다. 수집기 산출물을 활동으로 바꿔 서버에 보내고, 다른 사람의 원문은 PC 에 둡니다. 서버엔 행동·날짜·별칭·HMAC 식별자·관계 같은 구조화 정보와 내가 승인할 글만 갑니다.

- Python ≥3.9, 표준 라이브러리만. 키트 루트(이 `agent/` 폴더의 상위)에서 `python3 -m agent …` 로 돌립니다.
- macOS: 자격 증명은 Keychain(`security` CLI), 예약은 launchd. Windows: 자격 증명 저장소가 아직 없어 `connect`·`run-once` 를 쓸 수 없습니다(아래 「Windows」).
- 무엇을 보내고 무엇을 PC 에 두는지는 [처리방침](../guides/11-privacy.md) 2절 「연결 장치와 PC 원문」. 화면 용어는 [말 뜻](../guides/10-words.md) 「연결 장치와 활동」.

## 1. 명령

```bash
python3 -m agent [--home 경로] <명령>
```

| 명령 | 하는 일 |
|---|---|
| `connect --server https://<주소>` | 토큰만 받습니다(비표시 입력). 입력원 ID·장치 이름·허용 규칙·정책 판·키 공간·만료는 서버 `GET /api/imports/device` 에서 받아 확인합니다. 확인되면 토큰을 Keychain 에 넣고, 그 키 공간의 HMAC 키가 없으면 새로 만들어 둡니다. 서버 확인이 실패하면 아무것도 저장하지 않습니다. `--label`·`--hmac-namespace`·`--policy-revision`·`--axis` 는 서버 값을 덮어쓸 때만 씁니다 |
| `connect … --token-stdin` | 토큰을 표준 입력 한 줄로 받습니다(화면 표시 없음) |
| `validate [파일 …]` | 설정과 전송 대기 파일(`pending/`)·지정 파일을 계약(`canon-inbox/1`)으로 검사합니다. 쓰지 않습니다 |
| `run-once` | 수집 → 변환 → 계약 검증 → 전송 대기 저장 → (켰으면) 요약 → 업로드를 한 번 합니다. 같은 홈에서 겹쳐 돌면 뒤엣것은 건너뜁니다 |
| `run-once --no-upload` | 전송 대기 저장까지만 |
| `run-once --resume <범위>` | 멈춘 범위(`auth`·`config`·`account`·`policy`·`forbidden`·`summary`)를 내가 조치한 뒤 풉니다 |
| `run-once --scheduled` | 예약이 부르는 꼴(0~5분 지연, 무인 실행으로 신고). 손으로 쓰지 않습니다 |
| `status` | 연결·토큰·키 유무, 켜진 어댑터, 무인 요약, 예약, 건수(이벤트·대기·접수·멈춤·충돌·거절·격리·사람), 오늘 요약 시도, 멈춤 범위 |
| `schedule install` · `schedule remove` | launchd 예약 등록(1시간마다)·해제. 둘 다 여러 번 해도 같습니다. 해제는 원문·대응표를 지우지 않습니다 |
| `serve [--port N]` | 원문 보기(127.0.0.1 전용, 기본 포트 8790). Ctrl+C 로 끕니다 |
| `disconnect` | 순서대로 ① 서버 토큰 폐기(`POST /api/imports/device/revoke`) ② 로컬 업로드 토큰 제거. 서버가 장애·네트워크 오류면 로컬 토큰을 그대로 두고 종료 코드 1로 멈춥니다(다시 시도). 토큰이 이미 폐기·만료됐으면 서버는 「이미 무효」로 보고 로컬만 지웁니다. 결과는 서버·로컬·HMAC 키를 따로 보여 줍니다. HMAC 키는 지우지 않습니다 |
| `disconnect --local-only` | 서버를 건너뛰고 로컬 토큰만 지웁니다 — 웹 「연결 장치」에서 이미 해제했을 때 |
| `disconnect --forget-hmac-key` | 그 키 공간의 HMAC 키도 지웁니다. **지우면 같은 사람 연결이 끊깁니다** — 다시 연결해도 전에 본 사람을 같은 사람으로 잇지 못하고, 서버의 HMAC 으로 되살릴 수 없습니다. 백업 없이 쓰지 마세요 |

종료 코드: 0 성공 · 1 오류 · 2 쓰는 법 · 3 멈춤(사용자 조치 필요 — 출력 마지막 줄의 안내 뒤 `--resume`).
출력에는 토큰·HMAC 키·원문·실명·URL 을 쓰지 않습니다.

## 2. 처음 연결하기 (macOS)

1. 웹에서 새 탐색을 수락한 계정으로 로그인 → 계정 메뉴 「연결 장치」 → 장치 이름을 적고 연결. 토큰은 **그 화면에서 한 번만** 보입니다. 대화·문서·메신저에 붙여 넣지 마세요.
2. 키트 루트에서:
   ```bash
   python3 -m agent connect --server https://<주소>     # 「업로드 토큰(화면에 안 보임):」 에 붙여 넣기
   ```
   출력에 입력원 ID·정책 판·허용 행동·토큰 만료·설정 파일 위치가 나옵니다.
3. 설정 파일(`<홈>/config.json`)의 `adapters.likes` 를 채웁니다 — 아래 3절.
4. 보내기 전에 확인:
   ```bash
   python3 -m agent run-once --no-upload
   python3 -m agent validate
   python3 -m agent status
   ```
5. 보내기 → 예약 → 원문 보기:
   ```bash
   python3 -m agent run-once
   python3 -m agent schedule install
   python3 -m agent serve
   ```
   서버 스위치가 꺼져 있으면(화면 「준비 중」) 업로드는 대기 파일로 남고 다음 실행에 다시 보냅니다.

## 3. 설정 (`config.json`)

설정엔 토큰·HMAC 키를 넣지 않습니다(Keychain 항목 이름만). 입력 경로는 전부 이 파일에서 옵니다. `connect` 가 서버 주소·입력원 ID·허용 규칙을 채웁니다. 내가 채우는 것은 어댑터와 요약입니다.

`likes` 예시(`config.example.json` 과 같음):
```json
{
  "config_version": 1,
  "adapters": {
    "likes": {
      "enabled": true,
      "input_path": "/absolute/path/to/likes.jsonl",
      "provider_code": "threads",
      "include_kinds": ["*"],
      "include_null_kind": true,
      "include_seed": true,
      "axes": []
    }
  }
}
```
- `input_path` — 수집기 `likes.jsonl` 의 절대 경로. `provider_code` — HMAC 제공자 코드(소문자, 예: 플랫폼 이름).
- `include_kinds`·`include_null_kind`·`include_seed` — 보낼 행. 코드 기본값은 **아무 행도 안 보냄**(`[]`·`false`·`false`)이라, 보내려면 이렇게 직접 적어야 합니다. `["*"]` = kind 가 있는 행 전부. kind 는 내가 붙인 분류 태그라 좋아요 여부와 무관하고, 축 매핑에도 쓰지 않습니다.
- `axes` — 모든 활동에 붙일 축 항목 ID. 웹 「연결 장치」 허용 규칙의 축 안에서만 됩니다.
- 변환 규칙: 행 하나 = `like` 활동 하나. 행동 날짜를 주는 필드가 없어 `occurred_on` 은 언제나 비움(「날짜 미확인」) — 게시 시각·수집 시각으로 대신하지 않습니다. `first_seen` 은 「처음 수집한 날」(`observed_on`)로만 갑니다. 작성자 핸들은 HMAC 으로만 가고, 본문·게시 시각·원 URL·주제 등 나머지는 PC 원문 저장소에만 둡니다. 파일에서 행이 사라져도 취소를 만들지 않습니다. 판별할 수 없는 중복은 격리하고 보내지 않습니다.
- 다른 어댑터(`feed`·`reactions`·`mail`·`kakao`)는 의미 규칙만 있는 골격이라 꺼져 있습니다. LinkedIn 이직 입력원(`linkedin_moves`)은 설정에 적으면 설정 오류로 막습니다.
- 그 밖: `viewer_port`(원문 보기 포트, 기본 8790 — 웹 활동 상세의 「로컬 연결 포트」와 같게), `upload`(`max_per_run` 100 · `min_interval_sec` 6.5 · `timeout_sec` 30).

## 4. 무인 요약 (기본 꺼짐)

`summary.enabled` 는 `false` 가 기본입니다. 구독 CLI 의 이용 조건·권한 제한·CLI 자체 보관 동작을 확인하기 전에는 켜지 않습니다. 요약은 실행 방식대로 신고합니다 — 내가 직접 돌린 `run-once`(`--scheduled` 없이)는 사람이 돌린 것(`attended`)이라 서버의 가져오기 스위치만으로 접수되고, 예약 실행(`--scheduled`, launchd)은 무인(`unattended`)이라 서버 스위치 `CRM_UNATTENDED_SUMMARY_IMPORTS_ENABLED` 도 켜져 있어야 접수됩니다(그 스위치는 따로 꺼져 있습니다).

켜는 조건과 동작:
- `input_dir` — 사람이 지정하고 확인한 **내 기록 전용** 폴더의 절대 경로. 다른 사람의 말·고객사 정보가 섞인 폴더를 모델에게 골라내게 하지 않습니다. 숨김 파일·심볼릭 링크는 읽지 않고, `file_glob_suffix`(기본 `.md`) 파일만 봅니다.
- `cli_path` — 이미 로그인해 쓰는 구독 CLI 의 절대 경로. `tool_code`(기본 `claude-cli`)·`model_code`(기본 `unknown`)는 서버에 신고하는 값입니다.
- 모델에는 업로드 토큰·HMAC 키·신원 매핑을 넘기지 않습니다(최소 환경변수, 빈 작업 폴더, 입력은 표준 입력). 도구·MCP 를 끄고 1턴만 돌립니다.
- 성공 = 종료 코드 0 **그리고** 계약 검증 통과. 실패 출력은 전송 대기에 넣지 않습니다. 5분 제한(`timeout_sec` 300) · 동시 1개 · PC 당 KST 하루 10회 시도(`daily_attempts`).
- 로그인·구독·권한 오류면 `summary` 범위가 멈춥니다. 조치한 뒤 `run-once --resume summary`.
- 요약은 서버 「승인 대기」로 가고, 내가 웹에서 승인해야 기록이 됩니다. 승인해도 AI 생성 표시가 남습니다.
- 지금 에이전트는 내 글(`self-text/1`)을 만드는 어댑터가 없습니다. 승인 대기로 가는 것은 AI 요약뿐입니다.

## 5. 원문 보기 (`serve`)

웹 활동 상세의 [원문 열기 · 이 PC] 는 `http://127.0.0.1:<포트>/raw/<원문 ID>` 로 갑니다.
- `127.0.0.1` 에만 붙습니다. 첫 화면은 **원문 없는 확인 화면**이고, 그 화면의 [원문 열기] 를 눌러야 원문을 보여 줍니다(같은 출처 요청 · 정확한 Host·Origin · 세션 · 60초 일회용 토큰).
- 요청에서 경로·URL·파일명을 받지 않습니다. 원문 위치는 PC 대응표로만 찾고, 저장소 밖으로 벗어나는 경로는 거절합니다. 다른 사람의 HTML 은 실행하지 않고 글자로 보여 줍니다. 외부 이미지·글꼴·스크립트가 없고, 캐시·리퍼러·프레임 삽입을 막습니다. 접근 로그를 남기지 않습니다.
- 다른 PC 에서는 같은 ID 라도 대응표가 없어 열리지 않습니다. 이 방어는 웹 페이지의 무단 접근을 줄이는 것이고, 같은 OS 계정에서 도는 악성 프로그램까지 막는다고 보장하지 않습니다.
- 휴대폰·태블릿에서는 원문을 열 수 없습니다.

## 6. 멈췄을 때

| 보이는 것 | 할 일 |
|---|---|
| 「서버가 토큰을 거절했어요…」(`auth`) | 웹 「연결 장치」에서 토큰 재발급 → `connect` 다시 → `run-once --resume auth` |
| 「입력원 ID 가 토큰과 달라요…」(`config`) | `connect` 다시 → `run-once --resume config` |
| 「웹에서 표시 이름을 먼저 정한 뒤…」(`account`) | 웹에서 표시 이름 정하기 → `run-once --resume account` |
| 허용 규칙 변경 | 웹에서 허용 규칙을 바꾸면 다음 업로드가 「장치 허용 규칙이 바뀌었어요」(`POLICY-STALE`)를 받습니다. 에이전트가 같은 토큰으로 새 규칙을 다시 받아 설정을 고치고, 아직 접수되지 않은 대기 요청을 새 판으로 다시 만들어 그 실행에서 한 번 다시 보냅니다 — 토큰을 다시 넣지 않습니다. 출력의 `policy_refresh` 에 결과(`ok`·다시 만든 수·새 규칙이 막아 뺀 수)가 나옵니다 |
| 「…자동으로 다시 받지 못했어요…」(`policy`) | 장치 정보를 받지 못했거나(서버·네트워크), 입력원·키 공간이 바뀌었거나, 설정의 `likes.axes` 가 새 허용 축 밖입니다. 다음 실행이 다시 시도합니다. 계속되면 `connect` 를 다시 한 뒤 `run-once --resume policy` |
| 「서버가 요청을 막았어요…」(`forbidden`) | 웹 「연결 장치」를 확인한 뒤 `run-once --resume forbidden` |
| `status` 의 「멈춤 N」(`SOURCE-REVISION-CONFLICT`) | 서버의 최신 판과 PC 판이 갈라진 것입니다. Phase 1 에는 자동 해소가 없습니다 — 해당 원본은 보내지 않고 남겨 두고, 운영자에게 알려 수동으로 정리합니다. 다른 원본은 계속 갑니다. 정리 전에 홈을 백업해 둡니다 |
| 「충돌」·「거절」·「격리」 건수 | 같은 요청 키에 다른 본문, 계약·허용 규칙 불일치, 판별할 수 없는 중복입니다. 보내지 않고 남겨 둡니다(원문·식별자 없이 요약 값만) |
| 업로드가 다음 날로 미뤄짐 | 일일 한도(활동 하루 500건·사람 새로 50명 등)에 닿았습니다. 서버가 알려 준 시각(대개 다음 KST 0시) 뒤 다시 보냅니다. 서버 스위치가 꺼져 있거나 네트워크 오류면 대기 파일을 두고 최대 1시간 간격으로 다시 시도합니다 |

## 7. 파일과 Keychain

- **홈** = `--home` > 환경변수 `MYCRM_AGENT_HOME`(에이전트 전용 이름 — 서버의 `CRM_*` 환경변수와 다른 접두입니다) > OS 기본. macOS `~/Library/Application Support/my-crm-agent` · Windows `%APPDATA%\my-crm-agent` · 그 밖 `~/.local/share/my-crm-agent`. 제품 폴더(레포) 밖입니다.
- 홈 안: `config.json`·`state.sqlite`(현재 사용자만 읽기·쓰기) · `raw/`(다른 사람 원문·원 URL — **PC 에만**) · `pending/`(검증된 서버 요청만) · `locks/` · `logs/`(예약 실행 출력) · `summary-work/`. 폴더는 현재 사용자만 열 수 있게 만듭니다.
- `state.sqlite` = 수집 이벤트 ↔ 불투명 원본 ID·개정·원문 ID·사람 HMAC 대응표와 전송 상태.
- Keychain: 서비스 `my-crm-agent`, 항목 `upload-token:<입력원 ID>`(업로드 토큰) · `hmac-key:<키 공간 ID>`(HMAC 키, 256비트). 평문 파일로 대신 저장하지 않습니다.
- **HMAC** = `HMAC-SHA256(PC 키, JCS(["person/1", 제공자 코드, 계정 식별자]))`. 표시 이름만으로 사람을 정하지 않고, 제공자가 다르면 같은 사람으로 합치지 않습니다. 키를 잃으면 서버의 HMAC 으로 원래 사람을 되살릴 수 없습니다. 여러 PC 에서 같은 사람을 유지하려면 같은 키와 대응표를 내가 안전하게 옮겨야 합니다.
- **예약**: `~/Library/LaunchAgents/local.mycrm.agent.plist`(현재 사용자 LaunchAgent 하나, 1시간마다). plist·명령줄·환경변수에 토큰이 없습니다. 실행 폴더는 등록할 때의 키트 위치와 Python 으로 고정되므로, 키트를 옮기거나 Python 을 바꾸면 `schedule install` 을 다시 합니다. PC 가 잠들거나 꺼져 있으면 돌지 않습니다.

## 8. 백업과 지우기

- **백업 권고**: 홈 전체(`raw/`·`state.sqlite`·`config.json`)와 Keychain 의 HMAC 키를, 내가 관리하는 **암호화 백업 한 곳**에 함께 둡니다. 원문과 대응표는 자동으로 지우지 않습니다. HMAC 키 없이 대응표만 있으면 같은 사람 연결을 이어 갈 수 없습니다.
- 서버 계정을 삭제해도 PC 의 원문·대응표·Keychain 항목은 그대로입니다. 연결 해제(`disconnect`)·예약 해제(`schedule remove`)·PC 원문 삭제는 서로 다른 작업입니다. PC 쪽 자료를 지우려면 예약을 해제하고 연결을 끊은 뒤 홈 폴더를 지우고, Keychain 의 `my-crm-agent` 항목을 지웁니다.

## 9. Windows

자격 증명 저장소(Windows 자격 증명 관리자) 어댑터가 아직 없어 `connect` 가 토큰을 저장하지 못하고 멈춥니다(평문으로 대신 저장하지 않습니다). 그래서 `run-once` 도 돌지 않습니다. 쓸 수 있는 것은 `validate`(계약 검증)와 `serve`(원문 보기)입니다. 예약 도우미는 Phase 2 입니다.

## 10. 시험

```bash
python3 -m unittest discover -s agent/tests   # 키트 루트에서. Keychain·launchctl·구독 CLI 는 모의 실행기
```
서버 공용 시험 벡터(`server/contract/imports/vectors/`)와 이전 명세 스키마가 없으면 그 시험은 건너뜁니다.

## board 이전 명세 생성기 (`board_manifest.py`, 원작자 PC 전용)

board 정본 마크다운에서 AI 검토표와 이전 명세(`board-migration/1`)를 만듭니다. 입력 경로는 전부 로컬 설정 파일(JSON)로 받고, 서버엔 검토표를 올리지 않습니다. 명세는 서버 `migrate_cli.py` 가 받습니다(쓰는 법은 [server/README.md](../server/README.md) 「board 이전 CLI」).
```bash
python3 agent/board_manifest.py sheet    --config CFG --state-dir DIR --out SHEET.md
python3 agent/board_manifest.py build    --config CFG --state-dir DIR --sheet SHEET.md --out MANIFEST.json \
                                         --target-user-id U --source-instance-id S --reviewed-at 2026-10-20T21:00:00+09:00
python3 agent/board_manifest.py validate MANIFEST.json [--config CFG --state-dir DIR --sheet SHEET.md]
```
`--source-instance-id` 는 서버 `migrate_cli.py init` 이 출력한 ID 입니다. 고객사 원문(D 본문·E·W)을 두는 폴더는 설정의 `forbidden_roots`(설정 파일 기준 상대 경로·`~/`·절대 경로) 에 직접 적습니다 — 코드에 박아 둔 경로는 없고 기본값은 비어 있습니다. 그 아래 파일은 심볼릭 링크로 가리켜도 읽지 않습니다. 예시 설정은 `agent/tests/fixtures/board/config.example.json`. 검토표의 차단 항목(`⚠️AI` 줄·`(AI)` 절·URL·경로 꼴 줄·D 색인 제목·craft 이슈 키)을 전부 골라야 명세가 나옵니다. 참조는 정확한 판(`{original_id, revision}`)으로 적습니다.
