# server — 원격 MCP + 웹 (호스팅 판)

사용자는 자기 ChatGPT·Claude 구독으로 이 서버의 MCP 도구를 부른다. 모델 호출은 서버에 없다. 서버는 저장·근거 검사·화면만 맡는다.

## 실행 (개발)

```bash
cd server
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
CANON_DEV_USER=you@example.com .venv/bin/python main.py        # http://127.0.0.1:8800
.venv/bin/python test_tools.py                                   # 도구 흐름 시험
```

Claude Code 에서 붙이기: `claude mcp add --transport http canon http://127.0.0.1:8800/mcp`
또는 `--mcp-config` JSON `{"mcpServers":{"canon":{"type":"http","url":"http://127.0.0.1:8800/mcp"}}}`.

## 지금 판(2단계) 경계
- 인증 없음. `CANON_DEV_USER` 이메일 하나로 고정. 다음 판에서 AuthKit 토큰 검증 → 이메일로 사용자 결정.
- 저장은 SQLite(`server/data/`). 배포 때 Postgres.
- 도구: state_get · question_add · question_list · record_add · record_get · candidates_propose · candidate_decide · candidate_list · profile_set. 이름·인자는 고정(ChatGPT 는 승인된 스키마를 고정하므로 바꾸면 재승인).
- 근거 인용이 원문에 글자 그대로 없으면 버림. 같은 기록·같은 근거 구간은 중복 저장 안 함. 결정은 이력(candidate_history)에 이전 상태·문장을 남겨 되돌릴 수 있음.
