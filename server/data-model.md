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
| (2단계) object_defs·field_defs·items·item_links·item_history | 사용자 정의 층 — 아래 절 | users M-D |

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

## 사용자 정의 층 (2단계, 2026-10-18)
공통 코어(원문·인용·확정·이력·격리) + 제한된 정의 층. 사용자는 오브젝트·필드·Lookup 까지만 정의한다. 수식·롤업·자동화·사용자 M-D 없음.
증거 규칙(원문 불변·인용 1곳·후보≠판단)은 코어가 지키고 정의로 바꿀 수 없다. 근거: 레포 `notes/2026-10-18_내-구조-서버-스키마-매핑.md`.

| 오브젝트 | 용도 | 관계 |
|---|---|---|
| object_defs | 오브젝트 정의. `semantic_kind` collection/event/principle · `policy`(승격·대상 규칙) · `history`(적용 revision 마다 전체 정의 스냅샷) · draft_* | users M-D |
| field_defs | 필드. `value_type` text/number/boolean/date/select/text_list/reference · select `options`=[{value,label}] · reference 는 target_kind(item/record/candidate)·target_object_id·multiple. 빠진 필드는 `archived_at` | object_defs M-D · 대상 오브젝트 보호 Lookup |
| items | 항목. `code`(적용 뒤 변경·재사용 금지) · title · body · `data`(일반 필드만) · `decision`(principle 만) · revision · draft_* | object_defs 보호 Lookup |
| item_links | 관계. 출발은 item(필드 key) 또는 record(`attach_to`·`axes`) 중 하나 → 대상 item/record/candidate 중 하나. 값은 data 가 아니라 여기 | 출발 M-D · 대상 보호 Lookup |
| item_history | 항목 이력. `prev_state`(title·body·data·decision·code·schema_revision·links·archived) + `prev_revision` | items M-D |

기존 테이블 변경: `records.notes jsonb`(「어디에 붙나」 중 쓸 자리·아직 안 붙는 것) · `candidates.question_id` NULL 허용(질문 없는 후보 — 화면은 기록별 묶음). `init_schema` 가 상태를 보고 멱등 ALTER.

### 판단 로그
- 제안과 적용을 나눈다. MCP 는 `*_propose` 로 draft 만 쓰고, 적용(`definition_apply`·`item_decide apply`)과 기준 확정은 사용자가 말했을 때만. 웹은 결정만(항목 본문 편집 없음).
- 새 정의·새 항목 제안은 `revision=0` 행으로 둔다. 일반 조회에서 빠지고, 적용하면 1. 행당 제안 하나 — 새 제안이 덮고, 낡은 제안 ID 적용은 CONFLICT.
- 모든 변경은 행 잠금 + `expected_revision`. 틀리면 CONFLICT. 항목 이력 한 줄 = revision +1(`UNIQUE(user_id,item_id,prev_revision)`). 되돌리기(restore)도 새 변경이다.
- 결정 축은 principle 에만. 일반 항목은 apply·archive·restore 만. 제안 버리기는 `discard` + `proposal_id`(새 항목 제안이면 행 삭제, 아니면 draft 만 비움, 이력·revision 변화 없음). `exclude` 는 기준의 「남기지 않기」 전용.
- 정의 적용은 기존 항목을 깨면 거절한다 — 필수 추가·타입 변경·옵션 축소·multiple 해제·대상 변경·항목 있는 오브젝트의 종류 변경. key 는 적용 뒤 불변.
- 관계 검증은 서버: reference 필드만, 대상 종류·같은 사용자·살아 있는 대상·대상 오브젝트·단일성. 다른 사용자 대상은 복합 FK 전에 LINK-INVALID 로 거절.
- 계정 삭제는 정의 층 자식부터 지운다(`user_delete`). 보호 Lookup(NO ACTION) 때문에 users cascade 만으로는 `field_defs.target_object_id` FK 에 막힌다(2026-10-18 실측).

### 승격 정책
principle 정의의 `policy.promotion`. 기본 `{"confirmed_claims_min":2,"claim_field":"evidence_claims","distinct_events_min":0}` — 신규 기준 정의엔 `evidence_claims`(후보, 다중) 필드를 서버가 넣는다.
센다: 연결된 후보 중 확정이고 기록이 지워지지 않은 것. 결정(event) 조건(`distinct_events_min`·`event_field`·`event_claim_field`)은 연결된 결정 항목 중 그 판단이 이 기준의 확정 근거에도 있는 것.
`confirm` 은 미충족이어도 거절하지 않는다. 결과에 `promotion: {met, …}` 사실만 싣고 판단은 사용자 몫.

### 원작자 템플릿 (`template_author.py`)
정의 데이터일 뿐 테이블이 아니다. 기준 `od_principles`(principle, register·maturity·근거 판단·근거 결정·관심 축, 결정 2개 조건) · 결정 `od_events`(event) · 숙제 `od_open_questions`(대상 기준·결정·판단 중 정확히 하나) · 발행 `od_publications` · 축 `od_axes` · 접근 방식 `od_craft`. 라벨은 정의 데이터라 뒤에 바꿀 수 있다 — 시드 라벨은 `template_author.py` 맨 위 `LABELS`, 화면 코어 라벨(질문·기록·후보 등)은 `app.html` 의 `const LABELS` 한 곳. questions 자동 생성 없음, works·E 카드 제외.

## 제약 검증
Governor limit 없음. M-D 깊이 최대 2(users→candidates→candidate_history). cascade 끝은 users 삭제 = 그 사용자 데이터 전체 삭제 — 계정 삭제 요청 처리와 같다. 인덱스: `(user_id, question_id, decision)`, `(user_id, created)`.

## `db.py`(SQLite 임시판)와 차이 — Postgres 전환 때 할 일
끝남(2026-10-18, `db.py` psycopg 판). 남은 것(지인 검증 전): `confirm` 에 `expected_text`·revision 검사, 같은 본문 10분 중복 억제, `state_get` 요약+커서, 입력 상한 거절. 토론 raw 는 세션 스크래치패드 `arch/`.
