# 07. 점검·복구

깨진 상태를 숨기지 않고, 어디서 멈췄는지·다음 조치 하나·되돌릴 수 있는지를 보여 주는 것이 목적입니다.
Claude Code 에서 「canon-doctor 스킬로 점검해 줘」 라고 해도 같은 순서로 진행합니다.

## 1. 점검

```bash
python3 canon.py doctor
```

| 표시 | 뜻 |
|---|---|
| ✓ 정상 | 그대로 |
| ! 경고 | 진행 가능. 아래 → 조치를 보면 좋음 |
| ✗ 실패 | 그 조치부터 |
| - 미실행 | 이 점검으로는 확인 못 함 (예: 로그인은 첫 정리 때 확인) |

맨 아래 **다음 조치** 한 줄만 하고 다시 `doctor`.

## 2. 오류 코드

| 코드 | 무엇 | 분류 | 조치 |
|---|---|---|---|
| ENV-PY | Python 낮음/없음 | 사용자 조치 | 설치 가이드 1절 |
| ENV-CLI | claude 없음 | 사용자 조치 | 설치 가이드 2절 (화면만 쓰면 무시 가능) |
| CLI-LOGIN | 로그인 만료 | 사용자 조치 | `claude` 열고 `/login` |
| ENV-PORT / PORT-BUSY | 포트 사용 중 | 사용자 조치 | `serve --port 8766` |
| DATA-WRITE | 쓰기 권한 없음 | 사용자 조치 | 다른 데이터 폴더 |
| DATA-CORRUPT / FILE-CORRUPT | 설정 파일 손상 | 재설치·롤백 | `backups` → `restore <ID>` |
| DATA-SCHEMA | 형식·버전 불일치 | 재설치·롤백 | `restore` 또는 08 가이드 |
| DATA-BLOCK / BLOCK-UNKNOWN | 없는 블록 | 사용자 조치 | `blocks` 확인 후 `tabs propose` |
| DATA-LINK | 후보의 원문 없음 | 사용자 조치 | journal 파일 위치 확인 |
| RUN-LOCKED | 다른 작업 실행 중 | 사용자 조치 | 기다리기 (15분 후 자동 해제) |
| MODEL-FORMAT / MODEL-GROUNDING | 모델 답 형식·근거 검사 실패 | 반복되면 제품 결함 후보 | 한 번만 재실행, 반복 시 issue |
| MODEL-TIMEOUT | 응답 지연 | 사용자 조치 | 잠시 뒤 한 번 |

실패한 정리·설정·복구는 **이전 정상본을 그대로 둡니다.**

## 3. 백업과 복구

```bash
python3 canon.py backup --label 이름     # 지금 상태
python3 canon.py backups                 # 목록
python3 canon.py restore <백업ID>        # 되돌리기
```

- `restore` 는 지금 상태를 먼저 `before-restore` 로 백업합니다.
- 백업 뒤에 새로 쓴 기록은 지우지 않고 남깁니다.
- `tabs apply` 는 적용 직전에 자동 백업합니다.
- 백업이 하나도 없으면 복구를 약속할 수 없습니다. `doctor` 가 경고합니다.

## 4. 복구 리허설 (한 번은 해 보기)

```bash
python3 canon.py drill start     # 탭 설정을 일부러 깨뜨림
python3 canon.py doctor          # 실패를 읽음
python3 canon.py restore <ID>    # drill start 가 알려 준 ID
python3 canon.py drill finish    # 같은 탭이 돌아왔는지 검사
```

## 5. 같은 실패가 반복되면

무한 재시도하지 말고 멈춥니다.

```bash
python3 canon.py issue add "무슨 일이 몇 번" --kind "제품 결함 후보"
python3 canon.py diag
```

`diag` 는 지원용 진단 파일을 `<데이터>/.canon/diag-*.json` 에 만듭니다. 원문·질문·판단 문장·경로·토큰은 넣지 않습니다. **보내기 전에 직접 열어 확인하세요.**
지원 채널·응답 시간은 아직 정해지지 않았습니다.
