# server — 원격 MCP + 웹 (호스팅 판)

사용자는 자기 ChatGPT·Claude 구독으로 이 서버의 MCP 도구를 부른다. 모델 호출은 서버에 없다. 서버는 저장·근거 검사·화면만 맡는다.

## 실행 (개발)

```bash
cd server
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# .env 에 DATABASE_URL (Supabase Session pooler, 5432) — gitignored
CANON_DEV=1 CANON_DEV_USER=you@example.com .venv/bin/python main.py   # http://127.0.0.1:8800
.venv/bin/python test_tools.py   # Supabase 에 붙어 인증·도구·HTTP 시험. 시험 사용자는 끝에 삭제
```

Claude Code 에서 붙이기: `claude mcp add --transport http canon http://127.0.0.1:8800/mcp`
또는 `--mcp-config` JSON `{"mcpServers":{"canon":{"type":"http","url":"http://127.0.0.1:8800/mcp"}}}`.

## 배포 (Render Free, singapore)

Blueprint 는 `server/render.yaml`(대시보드 Blueprint Path 에 지정). rootDir=`server`, 시작 `uvicorn main:app --host 0.0.0.0 --port $PORT`, 헬스체크 `/health`(DB 핑 포함). MCP 는 stateless(json_response) — 무료 인스턴스가 잠들어도 다음 호출이 산다.

환경변수:
- `DATABASE_URL` — Supabase Session pooler 연결 문자열 (비밀, 대시보드에서 입력)
- `AUTHKIT_ISSUER` — `https://wealthy-technology-63-staging.authkit.app`
- `CANON_PUBLIC_URL` — 배포된 https 주소. MCP resource 는 이 주소 + `/mcp`, Host 허용 목록도 여기서 만든다
- `PORT` — Render 가 넣는다 (로컬은 `CANON_PORT`, 기본 8800)

운영엔 `CANON_DEV`·`CANON_DEV_USER` 를 넣지 않는다 — 넣으면 시작을 거부한다.
확인: `curl https://<주소>/health` → `{"ok":true,"db":true}` · 토큰 없이 `POST /mcp` → 401 + `WWW-Authenticate` · `GET /.well-known/oauth-protected-resource/mcp` → 200.
배포 뒤 WorkOS 에 등록할 것: Applications > Redirects 의 Redirect URI, Connect > Configuration 의 MCP resource indicator = `<주소>/mcp`.

## 경계
- 사용자 결정: AuthKit JWT(RS256, iss·exp·aud 검증) 의 `sub` 우선. 처음 연결 땐 userinfo 의 검증된 이메일로 같은 이메일 = 한 사용자. `email` 클레임은 토큰에 없다.
- 저장은 Postgres (`schema.sql`, 설계 `data-model.md`). 행 격리는 user_id + 복합 FK.
- 도구: state_get · question_add · question_list · record_add · record_get · candidates_propose · candidate_decide · candidate_list · profile_set. 이름·인자는 고정(ChatGPT 는 승인된 스키마를 고정하므로 바꾸면 재승인).
- 근거 인용은 원문에 글자 그대로 **한 곳**만 있어야 저장(0곳·2곳 이상 거절). 기록 1편당 후보 3개. 결정 이력(candidate_history)으로 되돌릴 수 있고, 확정 문장을 고치면 pending 으로 돌아간다.
