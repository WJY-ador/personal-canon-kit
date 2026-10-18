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

웹 화면은 `http://127.0.0.1:8800/app`. 개발(`CANON_DEV=1`)은 로그인 없이 `CANON_DEV_USER` 로 보인다. 실제 로그인을 로컬에서 보려면
`AUTHKIT_WEB_CLIENT_ID` 를 넣고 `/login` 으로 — 로컬 콜백 `http://127.0.0.1:8800/auth/callback` 도 등록돼 있다.

## 배포 (Render Free, singapore)

Blueprint 는 `server/render.yaml`(대시보드 Blueprint Path 에 지정). rootDir=`server`, 시작 `uvicorn main:app --host 0.0.0.0 --port $PORT`, 헬스체크 `/health`(DB 핑 포함). MCP 는 stateless(json_response) — 무료 인스턴스가 잠들어도 다음 호출이 산다.

환경변수:
- `DATABASE_URL` — Supabase Session pooler 연결 문자열 (비밀, 대시보드에서 입력)
- `AUTHKIT_ISSUER` — `https://wealthy-technology-63-staging.authkit.app`
- `CANON_PUBLIC_URL` — 배포된 https 주소. MCP resource 는 이 주소 + `/mcp`, Host 허용 목록도 여기서 만든다
- `PORT` — Render 가 넣는다 (로컬은 `CANON_PORT`, 기본 8800)
- `AUTHKIT_WEB_CLIENT_ID` — 웹 로그인용 공개 클라이언트. AuthKit DCR(`POST {issuer}/oauth2/register`)로 2026-10-18 등록한
  `client_01M56STKY61NSCEJCN6141YVEQ` (redirect: `https://personal-canon-kit.onrender.com/auth/callback`, `http://127.0.0.1:8800/auth/callback`,
  `token_endpoint_auth_method=none`). 환경 client ID 는 `/oauth2/authorize` 에서 `application_not_found` — Connect 흐름은 DCR·CIMD 클라이언트만 받는다(실측).
- `CANON_SESSION_SECRET` — 세션·PKCE 쿠키 서명 키(비밀, 32바이트 이상 임의 문자열). render.yaml 은 `generateValue: true` — Blueprint 동기화 때 Render 가 만든다.
  둘 중 하나라도 없으면 웹 로그인만 503 이고 MCP 는 정상 — 자동 배포로 서버가 죽지 않게.

운영엔 `CANON_DEV`·`CANON_DEV_USER` 를 넣지 않는다 — 넣으면 시작을 거부한다.
확인: `curl https://<주소>/health` → `{"ok":true,"db":true}` · 토큰 없이 `POST /mcp` → 401 + `WWW-Authenticate` · `GET /.well-known/oauth-protected-resource/mcp` → 200.
웹 로그인 확인: `GET /` 시작 화면(세션 있으면 `/app`) → [로그인] = `/login` → AuthKit → `/auth/callback` → `/app` 에 이메일 표시. `GET /app` 세션 없음 → 302 `/`.
로그아웃: `POST /logout` 이 쿠키를 지우고 `redirect` 를 준다 — 세션에 AuthKit `sid` 가 있으면 WorkOS `user_management/sessions/logout?session_id&return_to=/?out=1`(AuthKit 세션까지 종료), 없으면 `/?out=1`. 운영 확인 필요: 토큰에 `sid` 가 오는지, WorkOS 가 `return_to` 를 지키는지.
WorkOS 대시보드 등록은 DCR 클라이언트라 Redirect URI 가 필요 없다. Connect > Configuration 의 MCP resource indicator = `<주소>/mcp` 는 그대로.

## 웹 (`web.py` · `app.html`)
- 로그인은 MCP 와 같은 AuthKit. `/login` 이 PKCE(S256)·`resource=<주소>/mcp` 로 authorize 에 보내고, 콜백에서 받은 액세스 토큰을
  **MCP 와 같은 `AuthKitVerifier`·`user_id_for`** 로 검증·연결한다 → 웹 사용자 = MCP 사용자(같은 sub).
- 서버 메모리에 상태 없음. PKCE verifier·state 는 10분 서명 쿠키, 세션은 30일 서명 쿠키(HMAC-SHA256, HttpOnly, SameSite=Lax, https 면 Secure).
- 쓰기 API 는 `POST /api/candidate/decide` 하나(confirm·exclude·pending·edit). 세션 + `Origin` 이 공개 주소와 같을 때만. 「고쳐서 남기기」는 edit→confirm 두 번(이력 두 줄).
  기록·질문·후보 추가는 웹에 없다 — 연결한 AI 대화에서 한다.
- `app.html` 은 `web/index.html` 의 이식본. 필드명(`record_id`·`quote`·`question_id`), 문구(Claude Code·파일 전제 제거), 기본 탭 「시작」+「기록」, 시각은 브라우저 시간대.
  `web/index.html` 은 로컬 키트가 아직 쓰므로 그대로 둔다.

## 경계
- 사용자 결정: AuthKit JWT(RS256, iss·exp·aud 검증) 의 `sub` 우선. 처음 연결 땐 userinfo 의 검증된 이메일로 같은 이메일 = 한 사용자. `email` 클레임은 토큰에 없다.
- 저장은 Postgres (`schema.sql`, 설계 `data-model.md`). 행 격리는 user_id + 복합 FK.
- 도구: state_get · question_add · question_list · record_add · record_get · candidates_propose · candidate_decide · candidate_list · profile_set.
  웹 `/api/state` 는 같은 `db.state` 에 `user.email` 만 얹는다. 이름·인자는 고정(ChatGPT 는 승인된 스키마를 고정하므로 바꾸면 재승인).
- 근거 인용은 원문에 글자 그대로 **한 곳**만 있어야 저장(0곳·2곳 이상 거절). 기록 1편당 후보 3개. 결정 이력(candidate_history)으로 되돌릴 수 있고, 확정 문장을 고치면 pending 으로 돌아간다.
