---
name: crm-digest
description: My CRM 정리 — 기록 1편에서 판단 후보를 뽑아 질문별 한 장을 갱신하고, 사용자가 원문과 대조해 확인·제외하게 돕는다. "정리해 줘", "후보 뽑아 줘", "한 장 만들어 줘", "후보 확인하자" 때 사용.
---

# crm-digest — 기록 → 판단 후보 → 질문별 한 장

## 계약

| 항목 | 내용 |
|---|---|
| 전제 | 기록 1편과 질문 1개가 있음. 모델 모드는 `consent --model-send yes` 가 있어야 함 |
| 입력 | 기록 ID, 질문 ID, 모드(모델 / `--no-model`) |
| 모델 전송 | 그 기록 1편 본문 + 질문 문장만. 명령이 실행 전에 글자 수를 출력한다 — 사용자에게 보여 준다 |
| 쓰기 | `candidates.json` 에 **대기(pending)** 후보 추가, `sheets/<질문>.md` 재생성. 원문은 건드리지 않는다 |
| 검사 | 근거 인용이 원문에 글자 그대로 없으면 버림. 같은 후보 중복 생성 안 함. 한 장 검사 실패 시 이전 판 유지 |
| 완료 | 사용자가 후보마다 확인·제외·수정·보류를 정하고, 한 장을 열어 본다 |

## 순서

1. `python3 crm.py journal` 로 기록 목록을 보여 주고 대상 기록 ID 를 사용자와 정한다.
2. 모드를 묻는다: 모델로 정리 / 내 메모(`- 판단:` 줄)만.
3. 실행:
   ```bash
   python3 crm.py digest --record <기록ID> --question <질문ID>             # 모델
   python3 crm.py digest --record <기록ID> --question <질문ID> --no-model  # 내 메모만
   ```
4. `python3 crm.py candidates --question <질문ID>` 출력을 보여 주고, 후보마다 **원문 인용과 나란히** 묻는다:
   "이건 당신 판단인가요? 확인 / 제외 / 고쳐서 확인 / 보류"
   - 확인 → `candidate confirm <ID>`
   - 제외 → `candidate exclude <ID>`
   - 고쳐서 → `candidate edit <ID> "<사용자 문장>"` 후 `candidate confirm <ID>`
   - 보류 → 아무것도 안 함
5. `sheets/<질문ID>.md` 를 열어 보여 준다. "내가 확인한 판단"에는 사용자가 확인한 것만 있어야 한다.

## 원칙

- AI 후보는 자동으로 사용자 판단이 되지 않는다. 확인을 대신 누르지 않는다.
- "AI 제안"과 "내 메모"를 섞어 말하지 않는다.
- 후보 문장을 사용자 대신 더 그럴듯하게 고쳐 쓰지 않는다. 고칠 때는 사용자 표현으로.

## 실패

| 오류 | 할 일 |
|---|---|
| CONSENT-MISSING | 동의할지 사용자에게 묻거나 `--no-model` 로 |
| CLI-LOGIN | `claude` 열고 `/login` 안내 |
| MODEL-FORMAT / MODEL-GROUNDING | **한 번만** 재실행. 또 실패하면 멈추고 `issue add … --kind "제품 결함 후보"` |
| MODEL-TIMEOUT | 잠시 뒤 한 번. 반복 시 멈춤 |
| RUN-LOCKED | 다른 작업이 끝날 때까지 기다림 |

실패해도 이전 후보·한 장은 그대로다. 같은 명령을 다시 돌려도 중복이 생기지 않는다.
