# 데이터 모델 (2026-10-18 확정)

전제: 사용자 AI 클라이언트 → MCP → 이 서버 → Postgres. 서버 모델 호출 0. 사용자 10~100명, 1인당 월 기록 10~30편 — 작아서 파티션·아카이브 없음. DDL은 `schema.sql`. 지금 `db.py`는 SQLite 임시판이라 아래와 다르다(차이는 끝 절).

## 용어 대응 (Salesforce → Postgres)
| Salesforce | 여기 |
|---|---|
| Object · key prefix | 테이블 · ID 접두 `u_ q_ r_ c_` |
| Lookup | nullable FK, `ON DELETE SET NULL` |
| Master-Detail | `NOT NULL` FK, `ON DELETE CASCADE`, 소유자 부모 상속 |
| Picklist | `CHECK` |
| Field History | `candidate_history` |
| OWD Private | 모든 행 `user_id`, 모든 쿼리 `user_id` 필터 |
| External ID | `users.auth_subject` |

## 오브젝트 목록
| 오브젝트 | 용도 | 관계 |
|---|---|---|
| users | 계정. 이메일 하나 = 사용자 하나 | — |
| agreements | 약관·개인정보 동의, 버전별 | users M-D |
| questions | 계속 돌아오는 질문. 삭제 없음, `archived` 만 | users M-D |
| records | 기록 원문. soft delete | users M-D · questions Lookup |
| candidates | 판단 후보. 두 축 `origin`(ai/self) × `decision`(pending/confirmed/excluded) | users M-D(반정규화) · records M-D · questions RESTRICT |
| candidate_history | 결정 이력, 되돌리기 근거 | candidates M-D |
| profile | 프로필 항목 key-value, 상태 4값 | users M-D |
| tabs | 화면 탭. `doc`=적용본, `draft_doc`=제안 | users M-D |

## 판단 로그
- 질문은 삭제 금지. 확정 판단 밑 질문이 사라지면 판단 근거가 끊긴다 — `candidates.question_id` RESTRICT + `archived`.
- 기록→질문은 Lookup. 질문을 archive 해도 기록은 남는다.
- 기록 삭제는 soft delete(`deleted_at`). 후보는 기록 M-D — 기록 하드 삭제(계정 삭제) 때만 같이 사라진다. 근거 없는 판단을 남기는 안(Lookup SET NULL)은 제품 원칙 위반이라 버렸다.
- `candidates.user_id` 반정규화 유지. 행 격리 필터·인덱스에 join 없음. 자식은 `(user_id, 부모id)` 복합 FK — DB 가 다른 사용자의 기록·질문 참조를 막는다(Codex 토론: 앱 필터만으론 교차 참조를 못 막음).
- 근거 구간은 문자열이 아니라 **위치**(`quote_start`,`quote_end`)로 식별한다. 모델은 문자열만 보내고 서버가 원문에서 위치를 센다. 원문에 같은 구간이 2곳 이상이면 저장하지 않고 "앞뒤를 포함해 더 길게" 돌려준다. `UNIQUE (user_id, record_id, quote_start, quote_end)` — 같은 구간 재등록 방지. 같은 문장이 두 질문에 답하면 사용자가 질문 하나를 고른다(의도 확인 2026-10-18). Codex 토론: 문자열 키는 반복 문장을 하나로 합친다.
- 후보 결정 컬럼은 `decision`. 테이블마다 뜻 다른 `status` 를 없앤다. MCP 도구 인자명은 그대로 — ChatGPT 가 승인 시점 스키마를 고정하므로.
- 탭 제안→적용은 같은 행 `draft_doc`+`draft_id` 로. 적용은 제안 ID 와 기반 revision 이 맞을 때만. 동시에 제안 하나만 — 사용자 1명이 미리보기 하나 보는 흐름이라 충분. 별도 테이블은 안 만든다.
- `agreements` 지금부터. 지인 시험 사용자도 데이터 저장 동의를 받아야 하고, 나중에 붙이면 소급이 생긴다. 로컬 판의 읽기 경로 동의(`read_paths`)는 호스팅에서 뜻이 없어 버린다.
- `profile` 은 key-value 유지. 항목 추가에 마이그레이션 없음.
- `candidate_history` 에 `user_id` 없음, 부모 상속. RLS 쓰면 join 한 번.

## 제약 검증
Governor limit 없음. M-D 깊이 최대 2(users→candidates→candidate_history). cascade 끝은 users 삭제 = 그 사용자 데이터 전체 삭제 — 계정 삭제 요청 처리와 같다. 인덱스: `(user_id, question_id, decision)`, `(user_id, created)`.

## `db.py`(SQLite 임시판)와 차이 — Postgres 전환 때 할 일
끝남(2026-10-18, `db.py` psycopg 판). 남은 것(지인 검증 전): `confirm` 에 `expected_text`·revision 검사, 같은 본문 10분 중복 억제, `state_get` 요약+커서, 입력 상한 거절. 토론 raw 는 세션 스크래치패드 `arch/`.
