# templates — 양식

`init` 이 데이터 폴더를 만들 때 여기 있는 JSON 을 복사한다. 직접 고칠 필요는 없다 — 쓰기는 `crm.py` 명령으로 한다.

| 파일 | 무엇 |
|---|---|
| profile.json | 프로필. 항목마다 상태 confirmed(확인)·guessed(추정)·empty(미입력)·off(꺼 둠) |
| questions.json | 계속 돌아오는 질문. 1개부터 |
| sources.json | 연결한 자료 위치와 모델 전송 허용 여부 |
| tabs.json | 내 탭. 표시 이름(label)과 내부 ID(id)를 따로 둔다. 카탈로그 밖 요구는 requests |
| candidates.json | 판단 후보. pending(대기)·confirmed(확정)·excluded(제외), 누가 뽑았나 self(내 메모)·ai(AI 제안) |
| setup-state.json | 첫 설정 진행 단계 — 중단해도 이어서 하기 위함 |
| journal.md | 기록 양식 예시 (`record add` 가 자동으로 같은 꼴을 만든다) |
| journal-tail.md | 기록 끝에 붙는 "이게 어디에 붙나" 네 줄 |
| issue-log.md | 사건 기록 양식 |

모든 설정 파일에 `schemaVersion` 이 있다. 제품이 바뀌어 형식이 달라지면 업데이트 가이드의 마이그레이션 절차를 따른다.
