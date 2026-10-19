# server — 원격 MCP + 웹 (호스팅 판)

사용자는 자기 ChatGPT·Claude 구독으로 이 서버의 MCP 도구를 부른다. 모델 호출은 서버에 없다. 서버는 저장·근거 검사·화면만 맡는다.

## 실행 (개발)

```bash
cd server
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# .env 에 DATABASE_URL (Supabase Session pooler, 5432) — gitignored
CANON_DEV=1 CANON_DEV_USER=you@example.com .venv/bin/python main.py   # http://127.0.0.1:8800
.venv/bin/python test_tools.py   # Supabase 에 붙어 인증·도구·HTTP 시험. 시험 사용자는 끝에 삭제
CANON_TEST_SCHEMA=canon_test .venv/bin/python test_tools.py   # 운영 DB 가 아직 옛 스키마일 때: 임시 스키마에 최종형을 깔고 그 안에서만 시험, 끝에 삭제
```

원작자 템플릿(기준·결정·숙제·발행·축·접근 방식 정의, 라벨은 파일 맨 위 `LABELS`) 넣기 — 대상 사용자가 먼저 로그인해 계정이 있어야 한다:
```bash
.venv/bin/python template_author.py <email> --dry-run   # DB 안 붙고 정의 JSON 만
.venv/bin/python template_author.py <email>             # definition_propose→apply 와 같은 경로, 같은 key 있으면 건너뜀
```

Claude Code 에서 붙이기: `claude mcp add --transport http canon http://127.0.0.1:8800/mcp`
또는 `--mcp-config` JSON `{"mcpServers":{"canon":{"type":"http","url":"http://127.0.0.1:8800/mcp"}}}`.

웹 화면은 `http://127.0.0.1:8800/app`. 개발(`CANON_DEV=1`)은 로그인 없이 `CANON_DEV_USER` 로 보인다. 실제 로그인을 로컬에서 보려면
`AUTHKIT_WEB_CLIENT_ID` 를 넣고 `/login` 으로 — 로컬 콜백 `http://127.0.0.1:8800/auth/callback` 도 등록돼 있다.

## 배포 (Render Free, singapore)

배포 경로(2026-10-18): `main` 은 보호 브랜치 — 직접 push 불가. 브랜치 → PR → GitHub Actions `test`(`.github/workflows/ci.yml`: py_compile·node --check·운영 DB 가드·init_schema·test_tools, postgres:17 컨테이너) 초록 → merge → Render Auto-Deploy(After CI Checks Pass). 롤백은 Render Dashboard 직전 배포 복귀 → 문제 커밋 revert PR. 시작 시 ALTER 는 이전 코드와 호환돼야 한다.
예외: PR①(출처·revision·이력 격리) 마이그레이션은 `candidate_history.prev_decision`·`prev_text` 를 지운다 — 옛 인스턴스가 같이 돌면 결정 저장이 깨지고, 직전 배포 복귀만으로는 되돌아가지 않는다(raw §4 「구버전 writer와 혼용하지 않는다」).

Blueprint 는 `server/render.yaml`(대시보드 Blueprint Path 에 지정). rootDir 없음 — 레포 루트에서 빌드 `pip install -r server/requirements.txt`, 시작 `uvicorn --app-dir server main:app --host 0.0.0.0 --port $PORT`(도움말 정본 `guides/` 가 같이 올라가야 한다. rootDir=`server` 면 안 올라간다). 파이썬은 루트 `.python-version`(3.13), 헬스체크 `/health`(DB 핑 포함). MCP 는 stateless(json_response) — 무료 인스턴스가 잠들어도 다음 호출이 산다.

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
- `CANON_CONTACT` — (선택) 처리방침 보조 메일. 있으면 처리방침에 메일 한 줄로 보이고, 없으면 보이지 않는다. 없어도 서버는 뜬다. 문의·개인정보 요청 경로는 대시보드 「문의」 하나다.
- `CANON_ADMIN_USER_IDS` — (선택, **첫 배포 1회만**) 쉼표 구분 내부 user id(`u_…`). β2 부터 관리자 판정은 DB `users.role` 이다. 이 env 는 감사 기록에 `bootstrap`·`recover` 행이 없을 때 한 번만 읽혀 그 계정을 관리자로 옮긴다(아래 「관리자」 운영 절). 그 뒤엔 바꿔도 효과 없음 — 관리자 부여·회수는 `/admin/setup`, 잠기면 `admin_cli.py grant <email>`.

운영엔 `CANON_DEV`·`CANON_DEV_USER` 를 넣지 않는다 — 넣으면 시작을 거부한다.
확인: `curl https://<주소>/health` → `{"ok":true,"db":true}` · 토큰 없이 `POST /mcp` → 401 + `WWW-Authenticate` · `GET /.well-known/oauth-protected-resource/mcp` → 200.
웹 로그인 확인: `GET /` 시작 화면(세션 있으면 `/app`) → [로그인] = `/login` → AuthKit → `/auth/callback` → `/app` 에 이메일 표시. `GET /app` 세션 없음 → 302 `/`.
로그아웃: `POST /logout` 이 쿠키를 지우고 `redirect: /?out=1` 을 준다. AuthKit 세션은 끝낼 엔드포인트가 없다(end_session 없음, `user_management/sessions/logout` 은 Connect 토큰의 `sid=app_consent_…` 에 안 맞아 멈춤 — 2026-10-18 실측). 대신 `/?out=1` 의 [로그인] 은 `/login?fresh=1` → authorize 에 `prompt=login` 을 붙여 로그인 폼을 강제한다(실측: 세션 있어도 폼 뜸).
WorkOS 대시보드 등록은 DCR 클라이언트라 Redirect URI 가 필요 없다. Connect > Configuration 의 MCP resource indicator = `<주소>/mcp` 는 그대로.

## 운영 — 동의·일일 한도·사용 집계 (α2, 초안)

- **동의 버전** = `guides/11-privacy.md` 머리글 `` `vN · 날짜` ``(첫 20줄). 서버 시작 때 읽는다. **머리글의 N 을 올리면 전원 재동의** — 다음 쓰기에서 `CONSENT-REQUIRED`, 브라우저 `/consent` 에서 다시 동의. 이용약관을 두려면 `guides/12-terms.md` 를 같은 머리글 형식으로 두면 동의 항목에 자동으로 들어간다. 운영(비DEV)은 처리방침 문서가 없거나 못 읽거나 버전 머리글이 없거나 둘 이상이면 뜨지 않는다(약관은 선택).
- **게이트**: 동의 전 사용자는 MCP 쓰기 도구·웹 쓰기 API 가 거절된다(MCP `[CONSENT-REQUIRED]` + `/consent` 주소, 웹 403). 읽기·도움말·문의(`feedback_send`·「문의」)는 동의 전에도 된다. 관리자 면제 없음. `GET /app` 은 미동의면 `/consent` 로 보낸다.
- **일일 한도**(KST 0시 기준, 관리자도 같음): 기록 50 · 후보 150 — 제출 수 기준(검증 전, 한 호출 최대 3) · 결정 300 · 제안 100 — 하루 새로 만들어 **남아 있는** 새 정의·새 항목. discard 로 지우면 다시 셀 수 있다(목적은 저장 증가 억제) · 질문 50. 넘으면 `RATE-LIMIT`. 값은 `db.py` 「일일 한도」 `DAILY_LIMITS`.
- **usage_daily**: 도구·`/api/*` 호출 수를 (KST 날짜, 사용자, 이름, 코드)별로 메모리에 세고 5분마다 DB 에 합산한다. 요청 경로에 DB 비용이 없다. 대신 **수면·재배포 때 마지막 통상 약 5분치, DB 장애로 flush 가 연속 실패하면 그만큼 더 유실**된다(정상 종료면 종료 직전 1회 flush). 동의하지 않은 사용자·익명은 `user_id=''` 로 합쳐진다. 사용자별 집계는 flush 시점(5분 주기) 의 동의 상태로 접는다 — 동의 직전 5분 안 호출은 사용자별로 남고, 버전을 올리면 재동의 전까지 익명으로 접힌다.
- `CANON_SESSION_SECRET` 교체 = 전원 로그아웃.
- **배포 직후**: 원작자가 `/consent` 를 한 번 지난다 · 지인에게 「다음 쓰기부터 동의 화면 1회」 공지 · 운영 실측 = 동의 전 MCP 쓰기 1건 거절 → `/consent` 동의 → 같은 쓰기 1건 통과.

## 운영 — 관리자·재인증·감사·웹 끊기·차단 (β2, 초안)

- **관리자 = DB 역할**(`users.role='admin'`). env 로 판정하지 않는다. 화면은 `/admin/setup`(스크립트 없는 서버 렌더 + form) — 사용자 표(이메일·가입일·역할·처리방침 동의·차단·웹 끊은 시각)와 행마다 [관리자 부여|회수] [웹 로그인 끊기] [차단|해제], 감사 로그 최근 100건. 비관리자·로그인 안 함·DEV 는 404.
- **첫 지정은 배포 때 한 번**: 서버 시작 때 감사(`admin_audit`)에 `bootstrap`·`recover` 행이 하나도 없으면 env `CANON_ADMIN_USER_IDS` 의 id 중 있는 계정을 관리자로 하고 `bootstrap` 행을 남긴다(0명이어도 남긴다). 그 뒤로 env 는 읽지 않는다 — 바꿔도 효과 없음. 대상 계정이 먼저 로그인해 있어야 승격된다.
- **잠기면(관리자 0명·자기 차단)** 원작자 Mac 에서: `DATABASE_URL=… .venv/bin/python admin_cli.py grant <email>` → 관리자 + 차단 해제 + 감사 `recover`. `admin_cli.py list` 는 관리자 목록. `.env` 는 읽지 않는다(어느 DB 인지 명령 줄에서 보이게).
- **재인증**: `/admin` 은 최근에 다시 로그인한 세션에서만 열린다. [다시 로그인] = `/admin/reauth` → 로그인 폼 강제(`prompt=login`) → 같은 계정으로 돌아오면 세션에 재인증 시각이 붙는다. 위험 동작(부여·회수·끊기·차단·해제) 15분, 화면·감사 로그와 `/app` 문의함(전체 조회·상태·메모) 12시간. 창이 지나면 `/app` 문의함은 일반 사용자 화면처럼 보이고 상태·메모 변경은 `REAUTH-REQUIRED`. **한계**: Google 로그인은 세션이 살아 있으면 폼 없이 자동 통과할 수 있다 — 토큰에 `auth_time` 이 있으면 재인증 시작 60초 전 이후의 로그인만 인정하고, 없으면 같은 계정으로 돌아온 것만 본다. 운영 첫 로그인 때 로그 `[web] 로그인 토큰 claims 키` 에 `auth_time`·`iat` 가 있는지 확인한다(값은 남기지 않는다).
- **웹 로그인 끊기** = 그 계정의 지금까지 웹 세션 쿠키 무효(다음 로그인부터 다시 된다). MCP 커넥터에는 영향 없음. 관리자 회수도 대상의 웹 세션을 끊는다. 마지막 관리자는 회수할 수 없다(`LAST-ADMIN`).
- **차단** = 웹 세션·웹 로그인·MCP 호출 모두 거절(`AUTH-BLOCKED`). 마지막 관리자 차단(자기 자신 포함)도 허용 — 복구는 CLI. **해제** 뒤 차단 전 웹 쿠키·MCP 토큰은 살아나지 않는다 — 사용자는 웹에 다시 로그인하고 MCP 커넥터를 한 번 다시 연결한다(옛 토큰은 `AUTH-REVOKED`). 토큰에 `iat` 가 없으면 MCP 끊기 검사는 건너뛰고 로그 1줄(차단 검사는 그대로).
- **감사** `admin_audit`: 첫 지정·복구·부여·회수·웹 끊기·차단·해제. 실행자·대상 id 와 열거 키만(이메일·자유 텍스트 없음), 계정이 지워져도 남는다(화면엔 id 앞 8자). 1년 지난 행은 서버 시작 때 파기 — 단 `bootstrap`·`recover` 행은 남긴다(지우면 다음 시작 때 env 로 다시 승격한다).
- **배포 직후 전원 1회 재로그인** — 세션 쿠키 형식이 바뀌어(v2: 발급 시각·재인증 시각) 옛 쿠키는 무효다. 원작자는 재로그인 → `/admin/reauth` → `/admin/setup` 이 열리는지 확인.

## 웹 (`web.py` · `app.html`)
- 로그인은 MCP 와 같은 AuthKit. `/login` 이 PKCE(S256)·`resource=<주소>/mcp` 로 authorize 에 보내고, 콜백에서 받은 액세스 토큰을
  **MCP 와 같은 `AuthKitVerifier`·`user_id_for`** 로 검증·연결한다 → 웹 사용자 = MCP 사용자(같은 sub).
- 서버 메모리에 상태 없음. PKCE verifier·state 는 10분 서명 쿠키, 세션은 7일 서명 쿠키(HMAC-SHA256, HttpOnly, SameSite=Lax, https 면 Secure).
- 쓰기 API 는 `POST /api/candidate/decide`(confirm·exclude·pending·edit·edit_confirm, `expected_revision` 선택 — 틀리면 CONFLICT, 웹 편집 문장은 wording_by=self)와 `POST /api/item/decide`(항목: apply·discard·confirm·exclude·pending·archive·restore, `expected_revision` 필수, 제안 제외는 discard+proposal_id). 둘 다 세션 + `Origin` 이 공개 주소와 같을 때만. 「고쳐서 남기기」는 edit→confirm 두 번(이력 두 줄). 읽기 `GET /api/item?id=` 는 항목·관계 대상·들어오는 관계·이력·승격 상태(서랍).
  기록·질문·후보 추가는 웹에 없다 — 연결한 AI 대화에서 한다.
- `app.html` 은 로컬 보드(`~/Personal/scripts/checkpoint_board.html`·360·지도) 구조를 옮긴 보드형 화면(2026-10-18): 마스트(날짜·상태 스펙트럼·질문 레전드) + 기본 탭 오늘/질문/기록/구조/지도(사용자 정의 tabs 있으면 그 탭 + 구조·지도 고정) + 대조 2열(≥1000px). 토큰은 보드 값. 가안·스펙은 레포 `notes/`(gitignore).
- 2단계: 구조 탭 = 360(서브 필: 기준 오브젝트 · 후보 · 그 밖의 오브젝트, 카드 그리드 + 오른쪽 서랍 `#panel`). 서랍은 본문·필드·관계 대상·들어오는 관계·이력·승격 사실, 제안이 있으면 diff + [적용][제외(discard)], 기준이면 [내 판단으로 남기기][남기지 않기][되돌리기]. 라우트 `#structure/<오브젝트ID>[/<항목ID>]` · `#structure/cand/<필터>`. 오늘 탭은 질문 없는 미결정 후보를 기록별로 묶고, 지도 「쌓이는 재료」에 확정 기준을 함께 둔다.
- 옛 설명: `app.html` 은 `web/index.html` 의 이식본. 필드명(`record_id`·`quote`·`question_id`), 문구(Claude Code·파일 전제 제거), 기본 탭 「시작」+「기록」, 시각은 브라우저 시간대.
  `web/index.html` 은 로컬 키트가 아직 쓰므로 그대로 둔다.
- 도움말 `GET /help`(로그인 불필요, DB 안 건드림): 레포의 `guides/09-hosted-start.md`·`10-words.md` 를 서버 시작 때 읽어(`CANON_GUIDES_DIR` 가 있으면 그 폴더, 없으면 `server/../guides`) 정규식으로 HTML 로 바꾼다(제목·문단·표·굵게·목록·인라인 코드·링크, `10-words.md` 링크는 `#words`). 모든 글자를 먼저 escape(raw HTML 통과 없음), 링크는 http(s)·#앵커·상대경로만. 좁은 화면의 표는 가로 스크롤. 토큰·글꼴은 `start.html` 의 `:root` 블록을 그대로 쓴다. 세션 쿠키가 있으면 [노트로], 없으면 [로그인]. 문서가 없으면 시작 로그에 경고 + 503.
  문서 정본은 guides 이고 서버는 읽기만 한다. `app.html` 푸터·`start.html` 하단에 [도움말].
- 보안 헤더(웹 HTML 응답만 — MCP·JSON·/health 제외): `nosniff`·`X-Frame-Options: DENY`·`Referrer-Policy: same-origin`·CSP `frame-ancestors 'none'; base-uri 'none'; object-src 'none'; script-src 'self'`. `/app` 은 보낼 본문의 `src` 없는 인라인 `<script>` 1개를 요청마다 sha256 해 `'sha256-…'` 를 더한다(1개가 아니면 해시 없이 `'self'` 만 + 로그 — 화면 스크립트가 막힌다). `'unsafe-inline'`·`'unsafe-eval'` 은 넣지 않는다.
- 개인정보 처리방침 `GET /privacy`(로그인 불필요, DB 안 건드림): 정본 `guides/11-privacy.md` 를 /help 와 같은 렌더러·보안 헤더로. 권리 행사 경로는 대시보드 「문의」. 문안의 `{{CONTACT_LINE}}` 은 env `CANON_CONTACT`(선택 — 보조 메일, 레포에 적지 않는다)가 있으면 메일 한 줄로, 없으면 빈 문자열로 바꾼다. help_get 에는 안 들어간다. 문서가 없으면 503. 링크는 `start.html` 하단·`app.html` 푸터·/help 하단.

## 경계
- 사용자 결정: AuthKit JWT(RS256, iss·exp·aud 검증) 의 `sub` 우선. 처음 연결 땐 userinfo 의 검증된 이메일로 같은 이메일 = 한 사용자. `email` 클레임은 토큰에 없다.
- 저장은 Postgres (`schema.sql`, 설계 `data-model.md`). 행 격리는 user_id + 복합 FK.
- 도구: state_get · question_add · question_list · record_add · record_get · candidates_propose · candidate_decide · candidate_list · profile_set.
  2단계(정의 층) 5+1: definitions_get · definition_propose · definition_apply · item_propose · item_decide + record_attach(「어디에 붙나」: 기준·결정·후보 연결, 관심 축, 쓸 자리·아직 안 붙는 것). 동적 내용은 JSON 문자열(`definition_json`·`data_json`).
  도움말 help_get(`topic`: start·words·all, 기본 words) — `/help` 와 같은 guides 원문을 `{topic, text}` 로. INSTRUCTIONS 끝 줄: 해당 topic 하나만 읽고, 같은 대화에서 다시 부르지 않고, 문서에 없으면 "문서에서 확인되지 않아요" + 가장 가까운 절, 제품 밖 질문엔 적용 안 함. 
  문의 feedback_send(kind·text·tool) — 사용자가 글을 확인한 뒤 호출, 응답에 보낸 글 그대로·고지(desk-0). 웹은 `GET·POST /api/feedback`(「문의」 폼·「내 문의」). 관리자(`users.role=admin`, 재인증 12시간 안 — 아니면 비관리자 응답과 같은 모양)면 `GET /api/feedback` 이 전체 문의 최근 100건(이메일·메모 포함)과 `admin_tickets`(open·privacy_open)를, `GET /api/state` 도 `admin_tickets` 를 준다(비관리자 응답엔 키 없음, MCP state_get 에도 없음). `POST /api/feedback/status {id, status}` · `POST /api/feedback/note {id, admin_note ≤2KB}` 는 관리자만(세션·Origin, 아니면 403) — 종결이면 `closed_at`, 다시 열면 NULL, `updated` 갱신(감사 테이블은 β). 도구는 모두 17개.
  이미 붙여 둔 Claude 커넥터는 새 도구(help_get 등)를 보려면 커넥터를 다시 연결하거나 새 대화를 연다.
  웹 `/api/state` 는 같은 `db.state` 에 `user.email` 만 얹는다(2단계 키: definitions · items · item_proposals · record_links, journal 행에 notes). 이름·인자는 고정(ChatGPT 는 승인된 스키마를 고정하므로 바꾸면 재승인).
  `candidates_propose` 의 `question_id=""` = 질문 없음(NULL). 화면은 기록별 묶음, 대조 경로 `#today/review/r:<기록ID>/<후보ID>`.
- 근거 인용은 **인용 적격 기록**(author_kind=self·text_process=verbatim·삭제 안 됨)의 원문에 글자 그대로 **한 곳**만 있어야 저장(0곳·2곳 이상·비적격 기록 거절). 기존 기록은 출처 unknown. 기록 1편당 후보 3개. 후보는 변경마다 revision+1, 이력에 변경 전 스냅샷(`prev_state`·`prev_revision`). 결정 이력(candidate_history)으로 되돌릴 수 있고, 확정 문장을 고치면 pending 으로 돌아간다.
