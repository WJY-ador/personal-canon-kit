# server — 원격 MCP + 웹 (호스팅 판)

사용자는 자기 ChatGPT·Claude 구독으로 이 서버의 MCP 도구를 부른다. 모델 호출은 서버에 없다. 서버는 저장·근거 검사·화면만 맡는다.

## 실행 (개발)

```bash
cd server
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
# .env 에 DATABASE_URL (Supabase Session pooler, 5432) — gitignored
CRM_DEV=1 CRM_DEV_USER=you@example.com .venv/bin/python main.py   # http://127.0.0.1:8800
DATABASE_URL=postgresql://localhost/crm_test .venv/bin/python test_tools.py   # 로컬 Postgres 에서 인증·도구·HTTP 시험. 시험 사용자는 끝에 삭제. 로컬 아닌 호스트면 시작 전에 멈춘다
CRM_TEST_ALLOW_REMOTE=1 CRM_TEST_SCHEMA=crm_test .venv/bin/python test_tools.py   # 원격(server/.env) DB 가 아직 옛 스키마일 때만: 임시 스키마에 최종형을 깔고 그 안에서만 시험, 끝에 삭제
```

원작자 템플릿(기준·결정·숙제·발행·축·접근 방식 정의, 라벨은 파일 맨 위 `LABELS`) 넣기 — 대상 사용자가 먼저 로그인해 계정이 있어야 한다:
```bash
.venv/bin/python template_author.py <email> --dry-run   # DB 안 붙고 정의 JSON 만
.venv/bin/python template_author.py <email>             # definition_propose→apply 와 같은 경로, 같은 key 있으면 건너뜀
```

Claude Code 에서 붙이기: `claude mcp add --transport http my-crm http://127.0.0.1:8800/mcp`
또는 `--mcp-config` JSON `{"mcpServers":{"my-crm":{"type":"http","url":"http://127.0.0.1:8800/mcp"}}}`.

웹 화면은 `http://127.0.0.1:8800/app`. 개발(`CRM_DEV=1`)은 로그인 없이 `CRM_DEV_USER` 로 보인다. 실제 로그인을 로컬에서 보려면
`AUTHKIT_WEB_CLIENT_ID` 를 넣고 `/login` 으로 — 로컬 콜백 `http://127.0.0.1:8800/auth/callback` 도 등록돼 있다.

## 배포 (Render Free, singapore)

배포 경로(2026-10-18): `main` 은 보호 브랜치 — 직접 push 불가. 브랜치 → PR → GitHub Actions `test`(`.github/workflows/ci.yml`: py_compile·node --check·운영 DB 가드·init_schema·test_tools, postgres:17 컨테이너) 초록 → merge → Render Auto-Deploy(After CI Checks Pass). 롤백은 Render Dashboard 직전 배포 복귀 → 문제 커밋 revert PR. 시작 시 ALTER 는 이전 코드와 호환돼야 한다.
예외: PR①(출처·revision·이력 격리) 마이그레이션은 `candidate_history.prev_decision`·`prev_text` 를 지운다 — 옛 인스턴스가 같이 돌면 결정 저장이 깨지고, 직전 배포 복귀만으로는 되돌아가지 않는다(raw §4 「구버전 writer와 혼용하지 않는다」).

Blueprint 는 `server/render.yaml`(대시보드 Blueprint Path 에 지정). rootDir 없음 — 레포 루트에서 빌드 `pip install -r server/requirements.txt`, 시작 `uvicorn --app-dir server main:app --host 0.0.0.0 --port $PORT`(도움말 정본 `guides/` 가 같이 올라가야 한다. rootDir=`server` 면 안 올라간다). 파이썬은 루트 `.python-version`(3.13), 헬스체크 `/health`(DB 핑 포함). MCP 는 stateless(json_response) — 무료 인스턴스가 잠들어도 다음 호출이 산다.

환경변수:
- `DATABASE_URL` — Supabase Session pooler 연결 문자열 (비밀, 대시보드에서 입력)
- `AUTHKIT_ISSUER` — `https://wealthy-technology-63-staging.authkit.app`
- `CRM_PUBLIC_URL` — 배포된 https 주소. MCP resource 는 이 주소 + `/mcp`, Host 허용 목록도 여기서 만든다
- `PORT` — Render 가 넣는다 (로컬은 `CRM_PORT`, 기본 8800)
- `AUTHKIT_WEB_CLIENT_ID` — 웹 로그인용 공개 클라이언트. AuthKit DCR(`POST {issuer}/oauth2/register`)로 2026-10-18 등록한
  `client_01M593TEKZ4P1CAJMEKXFE6JA8` (2026-10-19 새 주소로 다시 등록, 이름 「My CRM web」 · redirect: `https://personal-mycrm.onrender.com/auth/callback`, `http://127.0.0.1:8800/auth/callback`,
  `token_endpoint_auth_method=none`). 환경 client ID 는 `/oauth2/authorize` 에서 `application_not_found` — Connect 흐름은 DCR·CIMD 클라이언트만 받는다(실측).
- `CRM_SESSION_SECRET` — 세션·PKCE 쿠키 서명 키(비밀, 32바이트 이상 임의 문자열). render.yaml 은 `generateValue: true` — 서비스를 처음 만들 때 Render 가 만든다(값이 바뀌면 전원 재로그인).
  둘 중 하나라도 없으면 웹 로그인만 503 이고 MCP 는 정상 — 자동 배포로 서버가 죽지 않게.
- `CRM_CONTACT` — (선택) 처리방침 보조 메일. 있으면 처리방침에 메일 한 줄로 보이고, 없으면 보이지 않는다. 없어도 서버는 뜬다. 문의·개인정보 요청 경로는 대시보드 계정 메뉴 「문의 보내기」 하나다.
- `CRM_ADMIN_USER_IDS` — (선택, **첫 배포 1회만**) 쉼표 구분 내부 user id(`u_…`). β2 부터 관리자 판정은 DB `users.role` 이다. 이 env 는 감사 기록에 `bootstrap`·`recover` 행이 없을 때 한 번만 읽혀 그 계정을 관리자로 옮긴다(아래 「관리자」 운영 절). 그 뒤엔 바꿔도 효과 없음 — 관리자 부여·회수는 `/admin/setup`, 잠기면 `admin_cli.py grant <email>`.

운영엔 `CRM_DEV`·`CRM_DEV_USER` 를 넣지 않는다 — 넣으면 시작을 거부한다.
**PR B 배포 뒤 1회**: 운영 확인(신규 계정 시드·기존 사용자 이름 입력·관리자 노출 0) 뒤 Render 대시보드에서 `CRM_FEATURE_STANDARD`·`CRM_FEATURE_SOURCES` 두 env 를 **Save only** 로 삭제한다. PR B 코드는 둘을 읽지 않는다(표준 시드·출처 연결 상시 켬, render.yaml 선언도 없음). 배포 전에 먼저 지우지 않는다 — 옛 코드로 돌아가면 off 로 돈다.
확인: `curl https://<주소>/health` → `{"ok":true,"db":true}` · 토큰 없이 `POST /mcp` → 401 + `WWW-Authenticate` · `GET /.well-known/oauth-protected-resource/mcp` → 200.
웹 로그인 확인: `GET /` 시작 화면(세션 있으면 `/app`) → [로그인] = `/login` → AuthKit → `/auth/callback` → `/app` 에 이메일 표시. `GET /app` 세션 없음 → 302 `/`.
로그아웃: `POST /logout` 이 쿠키를 지우고 `redirect: /?out=1` 을 준다. AuthKit 세션은 끝낼 엔드포인트가 없다(end_session 없음, `user_management/sessions/logout` 은 Connect 토큰의 `sid=app_consent_…` 에 안 맞아 멈춤 — 2026-10-18 실측). 대신 `/?out=1` 의 [로그인] 은 `/login?fresh=1` → authorize 에 `prompt=login` 을 붙여 로그인 폼을 강제한다(실측: 세션 있어도 폼 뜸).
WorkOS 대시보드 등록은 DCR 클라이언트라 Redirect URI 가 필요 없다. Connect > Configuration 의 MCP resource indicator = `<주소>/mcp` 는 그대로.

## 운영 — 처리방침·일일 한도·사용 집계

- **처리방침 버전** = `guides/11-privacy.md` 머리글 `` `vN · 날짜` ``(첫 20줄). 서버 시작 때 읽어 `/privacy` 에 쓴다. 운영(비DEV)은 처리방침 문서가 없거나 못 읽거나 버전 머리글이 없거나 둘 이상이면 뜨지 않는다.
- **필수 동의 없음**(2026-10-19 v3 부터, 현행 v5 — 시행일은 릴리스 때 머리글에 채운다): 서비스 제공 처리는 계약 이행(개인정보 보호법 제15조①4호), 구조 분석(개수·종류)은 정당한 이익(①6호)으로 보고 처리방침 「처리 근거」 표로 공개한다. 동의 화면·재동의·쓰기 차단이 없다. 시작 화면에 「시작하면 개인정보 처리방침이 적용돼요」 한 줄. 처리방침을 바꾸면 버전·날짜를 올려 공개하고 지인에게 공지만 한다. `GET /consent` 는 옛 링크용 302 `/app`. 옛 `agreements` 행은 이력으로 남는다(새로 쓰지 않음).
- **v5 요약**(PR B): 표시 이름 필수(실명 아님·다른 이용자와 운영자 화면에 안 보임, 처리 근거 계약 이행) · 사람 = 별칭 이름표만 · 출처 요약/반응 = 내 생각만(검토 완료 ≠ 판단 확정, 인용 근거 아님) · 삭제 절차(「개인정보 요청」 문의에 요청 문구만 → 접수 10일 안 통지 → 7일 유예 → 삭제, 유예 중 취소) · contact 1년 보관 · 재가입 = 새 계정(연결 없음). 필수 동의 게이트는 여전히 없다.
- **선택 동의**는 「구조 이름 공유」(①1호) 하나 — /app 계정 메뉴에서만 켜고 끈다, 기본 꺼짐.
- **일일 한도**(KST 0시 기준, 관리자도 같음): 기록 50 · 후보 150 — 제출 수 기준(검증 전, 한 호출 최대 3) · 결정 300 · 제안 100 — 하루 새로 만들어 **남아 있는** 새 정의·새 항목. discard 로 지우면 다시 셀 수 있다(목적은 저장 증가 억제) · 질문 50 · 사람 50 · 출처 요약/반응 50(PR B, 생성만 센다). 넘으면 `RATE-LIMIT`. 값은 `db.py` 「일일 한도」 `DAILY_LIMITS`.
- **usage_daily**: 도구·`/api/*` 호출 수를 (KST 날짜, 사용자, 이름, 코드)별로 메모리에 세고 5분마다 DB 에 합산한다. 요청 경로에 DB 비용이 없다. 대신 **수면·재배포 때 마지막 통상 약 5분치, DB 장애로 flush 가 연속 실패하면 그만큼 더 유실**된다(정상 종료면 종료 직전 1회 flush). 로그인 전 요청만 `user_id=''` 로 합쳐진다.
- `CRM_SESSION_SECRET` 교체 = 전원 로그아웃.
- **처리방침을 바꿔 배포한 뒤**: `/privacy` 버전 확인 · 지인에게 「처리방침이 바뀌었다」 공지(동의 절차 없음).

## 운영 — 관리자·감사·웹 끊기·차단 (β2)

- **관리자 = DB 역할**(`users.role='admin'`). env 로 판정하지 않는다. 화면은 `/admin/setup`(스크립트 없는 서버 렌더 + form) — 사용자 표(이메일·가입일·역할·차단·웹 끊은 시각)와 행마다 [관리자 부여|회수] [웹 로그인 끊기] [차단|해제], 감사 로그 최근 100건. 비관리자·로그인 안 함·DEV 는 404.
- **첫 지정은 배포 때 한 번**: 서버 시작 때 감사(`admin_audit`)에 `bootstrap`·`recover` 행이 하나도 없으면 env `CRM_ADMIN_USER_IDS` 의 id 중 있는 계정을 관리자로 하고 `bootstrap` 행을 남긴다(0명이어도 남긴다). 그 뒤로 env 는 읽지 않는다 — 바꿔도 효과 없음. 대상 계정이 먼저 로그인해 있어야 승격된다.
- **잠기면(관리자 0명·자기 차단)** 원작자 Mac 에서: `DATABASE_URL=… .venv/bin/python admin_cli.py grant <email>` → 관리자 + 차단 해제 + 감사 `recover`. `admin_cli.py list` 는 관리자 목록. `.env` 는 읽지 않는다(어느 DB 인지 명령 줄에서 보이게).
- **재인증 없음**(2026-10-19): 관리자 세션(`role=admin`·차단 아님)이면 /admin 열람·문의 처리·부여·회수·끊기·차단·해제를 바로 한다. POST 는 Origin 이 공개 주소와 같아야 한다. 옛 `/admin/reauth` 는 302 `/admin`. 대가: 세션 쿠키(HttpOnly·Secure·7일)를 도난당하면 관리 동작까지 막을 장치가 없다 — 의심되면 `/admin/setup` 에서 그 계정 [웹 로그인 끊기] 또는 `CRM_SESSION_SECRET` 교체(전원 로그아웃).
- **웹 로그인 끊기** = 그 계정의 지금까지 웹 세션 쿠키 무효(다음 로그인부터 다시 된다). MCP 커넥터에는 영향 없음. 관리자 회수도 대상의 웹 세션을 끊는다. 마지막 관리자는 회수할 수 없다(`LAST-ADMIN`).
- **차단** = 웹 세션·웹 로그인·MCP 호출 모두 거절(`AUTH-BLOCKED`). 마지막 관리자 차단(자기 자신 포함)도 허용 — 복구는 CLI. **해제** 뒤 차단 전 웹 쿠키·MCP 토큰은 살아나지 않는다 — 사용자는 웹에 다시 로그인하고 MCP 커넥터를 한 번 다시 연결한다(옛 토큰은 `AUTH-REVOKED`). 토큰에 `iat` 가 없으면 MCP 끊기 검사는 건너뛰고 로그 1줄(차단 검사는 그대로).
- **감사** `admin_audit`: 첫 지정·복구·부여·회수·웹 끊기·차단·해제 · 삭제 통지·실행·취소(PR B, detail = `ticket_id`·`result` 만). 실행자·대상 id 와 열거 키만(이메일·자유 텍스트 없음), 계정이 지워져도 남는다(화면엔 id 앞 8자). 1년 지난 행은 서버 시작 때 파기 — 단 `bootstrap`·`recover` 행은 남긴다(지우면 다음 시작 때 env 로 다시 승격한다).
- 세션 쿠키 = `{uid, iat, exp}` 서명 값(β2 에서 iat 도입, 그 전 쿠키는 무효였다). 재인증 제거 때 옛 쿠키에 남은 `ra` 는 무시할 뿐 무효로 만들지 않는다 — 재로그인 필요 없음.

## 운영 — 운영 콘솔 (/admin)

- 화면(전부 서버 렌더 + form, 스크립트 0, no-store, 비관리자·로그인 안 함·DEV 는 404): 왼쪽 세로 메뉴(PR A — Salesforce Setup 꼴, 정본 `web.py` `ADMIN_MENU`) = Quick Find + Setup Home `/admin` · Object Manager `/admin/schema` · Users `/admin/users` · Reports `/admin/reports` · Support `/admin/tickets` · Administration `/admin/setup`. **Setup Home** 3구역 = 개요(상태 띠·사용자 표·미연결 사용량·정의 분포) · 확인할 오류(오류 14일) · 문의(미종결·개인정보 기한 임박·기한 초과). **Users** `/admin/users`(사용자 목록: 오브젝트 n·관계 N·그중 연결 있음 M·표준 상태 코드 · 삭제한 계정은 「삭제됨」 — 상태 코드만, 표시 이름·사람·요약/반응은 값도 개수도 0) · `/admin/users/<불투명 id>`(요약 → DMO 그래프 → 관계 집계 → 무결성 REF-DANGLING·REF-MISMATCH·MD-ORPHAN과 「검사 불가」 → 표준 상태 → 구조 트리 → 문의 요약. 구조 글자는 전부 `contract.respond`. PR A 에서 계약 변화 없음) · **Reports** `/admin/reports`(데이터셋 사용자·사용량·구조·문의, 묶기 ≤2·필터·기간 ≤180일, 행 ≤200, 정의 = GET 쿼리스트링) · **Support** `/admin/tickets`(리스트 — 개인정보 요청 안내 「요청 문구만 적어 주세요. 다른 정보는 넣지 마세요」와 삭제 CLI 순서 한 줄) · `/admin/tickets/<id>`(레코드: 원문·Path·공개 답변·내부 메모·재분류·이력) · **Administration** `/admin/setup`(관리자 부여·회수·웹 끊기·차단·감사 — 위 절). 관리자 `/app` = ⚙(/admin/setup) + 계정 메뉴 「운영 콘솔」·로그아웃(문의·이름 공유 메뉴·문의 대화창은 보내지 않는다). 운영 화면은 밝게 고정(OS 다크 미추종).
- **Object Manager**(`/admin/schema`, 조회 전용 — 편집·설치·편입 버튼 없음): 서비스 정의만 읽는다(`templates/std@1.json`·`author@1.json` + 코드의 코어 정의). 사용자 DB 는 읽지 않는다. 첫 화면 = 코어 8(질문·기록·후보·문의·출처 + PR B 나·사람·출처 요약/반응) + std@1 3(축·결정·기준) DMO 그래프 → 오브젝트 표(구분·판·필드·관계·정의상 잠금·서비스 설명·API 이름) → 관계 표 → 「선택 템플릿」 구역(「이전 원작자 템플릿 · author@1」). 상세 `/admin/schema/objects/{id}` · `/admin/schema/templates/{tid}[/objects/{id}]` — 공개 ID 허용목록(`core-record`·`std-principle`·`tpl-…` 꼴), 모르는 ID 는 404. 필드 표 = 표시명·API 이름·자료형·필수·읽기 전용·선택지(개수만)·정의상 잠금. 정의에 없는 값은 「모름(정의 미확인)」. 코어 열 표시명 표는 `service_schema.py` `CORE_FIELDS` 한 곳. 「나」(`users`)는 `display_name`·`status`·`nav_accepted_at`·`created` 열 정의만 보인다(`web.py` `CORE_ONLY`) — 열 이름·자료형이고 값은 읽지 않는다. API 이름 = 서비스가 정한 제품 문자열(코어 테이블·열, std/author key) — 사용자 데이터 아님. 숨기는 것: 선택지 값·운영자 전용 열(`admin_note`·`hint`)·정의 파일 id·내부 경로·사용자 정의 key·label(이름 공유 동의여도). 기록 → `item_links` 는 선 없이 「항목 — 대상 오브젝트 모름 · 분류 대상은 사용자별 역할로 정함」.
- **Quick Find** `GET /admin/find?q=`: 서버 검색. 색인 = 운영 메뉴 + 서비스 표준(코어 + std@1) 정의의 표시명·API 이름·설명·자료형(프로세스당 한 번 메모리에 만든다). 사용자 DB·선택 템플릿은 찾지 않는다. 결과 ≤50, 검색어 100자 상한(넘으면 400 `ARG-INVALID`). 검색어는 화면·속성에 되비추지 않고 저장하지 않는다. 여러 낱말은 모두 들어간 항목만.
- **옛 URL 유지**: `/admin/schema?u=` 는 302 `/admin/users/<불투명 id>`, `/admin/reauth` 는 302 `/admin`.
- **운영 경계** `admin_db.py`: 운영 화면의 DB 읽기는 허용표 `ALLOW` 의 키로만 한다(고정 SQL·허용 열·조회자·동의 조건). 기록 본문·질문 글·탭 이름·정의 이력은 읽지 않는다. 문의 원문·내부 메모는 레코드 페이지에서만. 정적 시험과 표식 시험이 지킨다.
- **이름 공유(선택 동의 `struct_names`)**: 동의한 사용자의 오브젝트·필드·선택지 **현재 이름**(오브젝트·필드 label, 선택지 label)만 `/admin/users` 상세에 보인다(리포트엔 없음). key·선택지 값·출처 이름·위치는 동의여도 0. 비동의자는 자리표 「오브젝트 n」·「필드 a」와 편입된 표준의 표준명만(URL 은 불투명 id). 철회하면 다음 조회부터 빠진다. 저장은 `optional_consents`(현재 상태 1행) + `optional_consent_log`. 동의 버전은 처리방침과 별개인 `db.STRUCT_NAMES_VERSION` — 이름 공유 범위를 바꿀 때만 올리고, 올리면 옛 버전 동의는 무효.
- **문의 처리**: 상태 전이·답변·메모·재분류는 변경과 `ticket_history`(공개/내부) 사건을 한 트랜잭션으로. 사용자에겐 공개 사건과 답변만(웹 「내 문의」 상세·MCP `ticket_list_mine`·`ticket_get_mine`). privacy 로 재분류하면 기한 기산점 = 최초 접수일(이미 지났으면 즉시 「기한 초과」), privacy 에서 빼면 사유 필수·`privacy_log` 는 남김. 종결 180일 뒤 파기는 서버 시작 때 + 실행 중 매일(문의 이력도 cascade).
- **처리방침 이력**(`guides/11-privacy.md`): v2 = 구조 분석 목적·이름 공유 선택 동의·이용 기록 사실대로(v2 를 처음 띄우는 `init_schema` 가 그 전 `usage_daily` 를 **한 번** 지운다, `schema_marks` 표식). v3 = 필수 동의 게이트 제거. v4 = 기록 출처·표준 편입 선택·운영자 출처 개수만·작성자 표시만 검사. v5 = 위 「처리방침」 절 v5 요약.

## 운영 — 표준·출처·코어 오브젝트 (PR #20 · PR B)

- 정본 명세는 레포 밖 `notes/2026-10-19_PR20-실행명세.md`·`notes/2026-10-20_PR-B-구조-구현계획.md`(gitignore). 응답 계약 = `contract.py`(순수 모듈) + 고정 예제 `contract/*.json`(재생성 `.venv/bin/python contract.py`).
- **기능 플래그 없음**(PR B): PR #20 의 `CRM_FEATURE_STANDARD`·`CRM_FEATURE_SOURCES` 분기를 코드에서 지웠다 — 표준 시드·출처 연결은 상시 켬. 시험이 `server/*.py`·`render.yaml` 에 `CRM_FEATURE_` 참조 0 을 본다. 대시보드 값 삭제는 「배포」 절.
- **표준(std@1)** = 축{keywords}·결정{happened_on, claims}·기준{evidence_claims, evidence_events, axes}, 동결 `templates/std@1.json`(재생성·검사 `template_std.py [--check]`). 식별자 `std:<key>[.<field>]`(버전 무관), 설치 버전은 `std_marks.version`.
- **신규 계정 시드**: 사용자 INSERT 트랜잭션 안(savepoint). 실패해도 로그인 성공 + `seed_pending`, 재시도는 소유 정의 0개일 때만. **기존 사용자는 자동 시드·편입 없음** — /app 「구조 정의」(새 탐색: 「내 구조 설정」)에서 미리보기를 보고 편입·보류·거절(`std_choices`). 참조 의존: 축을 편입하지 않으면 기준은 편입할 수 없다.
- **표준 잠금**(`STANDARD-LOCKED`, 항상): 편입된 정의의 key·종류·표준 필드 제거·모양·명세 선택지 삭제, 표준 오브젝트 보관. label·policy·사용자 필드·선택지 추가는 허용.
- **오브젝트 보관·복구**(R5 결함, PR B): `definition_archive`·`definition_restore` 가 `archived_at` 만 바꾼다(revision·이력·항목 그대로). 결과 `impact` = 살아 있는 항목 수·이 오브젝트를 가리키는 다른 필드 수. 표준 = `STANDARD-LOCKED`, 적용 전 초안(revision 0) = `ARG-INVALID`. 웹 `/api/state` 는 보관 오브젝트·초안을 싣지 않는다 — 보기는 MCP `definitions_get`(`include_archived`, 초안은 `object_ids` 로 콕 집을 때만 `draft=true`).
- **출처**: `sources` + `records.source_id`(같은 사용자만). `record_add` 의 `source_name`·`source_kind`(file·url·chat·other)·`source_location` 으로 찾거나 만들어 연결(상시). 기록 없이 만들기 = `source_create`(같은 `(user_id, kind, name, location)` 유일 규칙). 옛 기록은 연결하지 않는다(「출처 미확인」, 옛 `source` 문자열 보존). 연결된 기록·요약/반응이 있는 출처를 지우는 기능은 없다(FK NO ACTION).
- **기록 출처 판정 정정**(R5 결함, PR B): `record_provenance_set`(author_kind·text_process·사유) — 본문 불변, `provenance_history` 에 사유와 한 줄. 후보가 있는 기록을 적격 → 비적격으로 바꾸면 `PROVENANCE-LOCKED`(반대 방향은 된다).
- **코어 오브젝트**(PR B) = 모든 사용자에게 같은 공통 테이블(origin=core, std_id 없음): 질문·기록·후보·문의·출처 + **나**(`users` 행 하나, 이름 = `users.display_name`)·**사람**(`people`, 별칭 `alias` 하나뿐)·**출처 요약/반응**(`source_reflections`, kind `summary`·`reaction`, review `pending`·`reviewed`). 모든 조회·쓰기는 user_id 로 묶고 남의 id = 없는 id = `NOT-FOUND`. 수정·삭제는 `expected_revision` 필수(다르면 `CONFLICT`), 바뀔 때마다 revision +1. 요약/반응 검토 완료 뒤 본문·유형·출처를 바꾸면 `pending` 으로 돌아간다. 요약/반응은 `records` 밖이라 인용 적격 경로(후보 근거·관계 대상·`record_attach`)에 들어가지 않는다 — `sr_` id 는 거절.
- **새 탐색 수락**: `users.nav_accepted_at`(`POST /api/nav/accept`, 멱등). 표준 편입 상태를 읽지도 바꾸지도 않는다. 신규 계정은 생성 트랜잭션에서 채운다. 되돌리는 화면은 없다 — 운영 복구 = 그 열을 NULL 로.
- **표시 이름 관문**(`NAME-REQUIRED`, 웹·MCP 공통 · `db.write_gate` 한 곳): 판정 = `users.display_name IS NULL`. 문구 「대시보드에서 표시 이름을 정해 주세요.」(`contract.NAME_REQUIRED_MSG`). MCP 는 `main.WRITE_TOOLS`(19개)가 관문, `ALLOWED_TOOLS`(17개 — 읽기·`help_get`·`feedback_send`·`ticket_*`·`display_name_set`)는 통과 — 분류 안 된 도구가 있으면 시험 24 가 실패한다. 웹은 `/api` 쓰기 경로 전부 관문(웹 응답 403), 예외 = `/api/me/name`·`/api/feedback`·`/api/optional-consent`·`/api/desk-visibility`·로그아웃. `/api/nav/accept` 는 관문을 받는다. 입력 = 앞뒤 공백 제거 뒤 1~80자 한 줄, 빈 값으로 바꾸기 거절, 이메일 등으로 자동 채우지 않는다. 기존 사용자는 다음 웹 로그인 때 이름 입력 화면(건너뛰기 없음).
- **분류 역할** `user_roles(classify)`: 기록 → 분류 대상 링크(field_key `axes`)는 역할 대상 오브젝트의 항목만. init 이관 = key `axes` 적용 오브젝트, 시드 = 표준 축, `record_attach` 지연 배정. 대상 없으면 `CLASSIFY-OFF`.
- **오류 코드**: PR #20 `STANDARD-LOCKED` · `CANDIDATE-REVIEW` · `SCOPE-SELF-ONLY` · `CLASSIFY-OFF`(`FEATURE-OFF` 는 PR B 에서 없앴다) · R5 `PROVENANCE-LOCKED` · PR B `NAME-REQUIRED` · `NOT-FOUND`(코어 남의·없는 id) · 삭제 CLI 코드(아래 절).
- **되돌림 주의(PR #20)**: 옛 코드(PR #20 전)는 새 스키마에서 읽기·쓰기 된다(R4 실측). 다만 옛 init 의 author@1 백필이 표준 시드 행(같은 id)에 `template_slot` 을 칠한다 — 화면·리포트 구분은 std_id 우선이라 표준 그대로. PR #20 전 코드로 되돌렸다면 시드·누락 편입 표준 행의 `template_slot` 을 비운다(기존 템플릿 오브젝트를 편입한 행은 그대로). PR B 의 되돌림 경계는 아래 「계정 삭제(CLI)」 절.

## 운영 — 계정 삭제(CLI)

원작자 Mac 에서만 돌린다. `.env` 는 읽지 않는다 — `DATABASE_URL` 을 명령 줄에 적는다. `--actor` = 실행하는 관리자 user id(DB 접속권이 곧 권한이라 추가 검증 없음).
```bash
DATABASE_URL=… .venv/bin/python admin_cli.py delete-notify  <ticket_id> --actor <uid>
DATABASE_URL=… .venv/bin/python admin_cli.py delete-preview <ticket_id> --actor <uid>
DATABASE_URL=… .venv/bin/python admin_cli.py delete-run     <ticket_id> <미리보기 값> --actor <uid>
DATABASE_URL=… .venv/bin/python admin_cli.py delete-cancel  <ticket_id> --actor <uid>
```
- **대상** = 그 「개인정보 요청」 문의(kind=privacy)의 소유자. 이메일로 대상을 고르지 않는다. 문의는 로그인 계정으로만 생기므로 소유자 = 요청한 본인이다.
- **절차**: 사용자가 「개인정보 요청」으로 접수 → 접수일부터 **10일 안** `delete-notify`(고정 통지문을 문의 공개 답변에, 상태 doing, `deletion_contacts` 행 — 출력에 「접수 뒤 10일 안 통지 = 예/아니오」) → **7일 유예** → `delete-preview`(읽기 전용: 지울 테이블별 건수·보존 건수·실행 가능 여부와 사유 `no_notice`·`other_ticket`·`grace`·미리보기 값) → `delete-run <ticket_id> <미리보기 값>`(한 트랜잭션). 유예 중 사용자가 같은 문의에 「취소」를 남기면 `delete-cancel`(통지 행 삭제) — 사용자 답변은 /admin 문의 화면에서 운영자가 쓴다.
- 고정 통지문(`db.DELETE_NOTICE`): 「7일 뒤 자료를 모두 지우고 계정을 닫습니다. 이메일과 이 삭제 요청 문의·처리 이력은 처리 증빙과 분쟁 대응을 위해 삭제 실행일부터 1년 보관한 뒤 파기합니다. 삭제 결과는 이 문의 이력에 남습니다. 그 전에 취소하려면 이 문의에 「취소」라고 남겨 주세요.」
- **run 이 하는 일**: 소유자 행·contact 잠금 → 재조회(공통 거절·통지 있음·유예 끝·미리보기 값 일치) → `db.DELETE_TABLES` 전부 삭제(보존 문의 1건 제외) → 이메일·`auth_subject` 를 contact 로 옮김(`deleted_at`, `purge_after` = +1년) → `users` 를 자리값으로(email `deleted:<uid>`, auth_subject·표시 이름·새 탐색 수락 NULL, `status='deleted'`, 웹·MCP 끊기) → 보존 문의 done + 공개 이력 「삭제 완료」 → 감사 `delete_run`. 실패하면 전체 롤백 뒤 감사에 `result` = 오류 코드 한 줄.
- **출력**: 건수·시각(KST)·코드만. 이메일·표시 이름·본문·별칭·출처 값·답변은 찍지 않는다. 오류 = `[코드] 문구`, 종료 1.
- **거절 코드**: `FORBIDDEN`(실행자가 활성 관리자 아님) · `NOT-FOUND`(문의 없음) · `DELETE-NOT-PRIVACY` · `DELETE-ADMIN-TARGET`(대상이 관리자 — 먼저 `/admin/setup` 에서 권한 회수) · `DELETE-ALREADY` · `DELETE-NOTIFIED`(이미 통지) · `DELETE-NO-NOTICE` · `DELETE-GRACE`(유예 중) · `DELETE-STALE`(미리보기 뒤 자료가 바뀜 — 다시 미리보기).
- **남는 것**: `users` 자리값 행 · `deletion_contacts` 행(이메일·subject, 삭제 실행일부터 1년) · 그 삭제 요청 문의 1건과 `ticket_history` · `admin_audit`(1년 규칙) · `privacy_log`(식별정보 없음 — run 이 그 요청 행에 처리일·`result=done` 을 채운다). 문의 180일 파기(`tickets_purge`)는 보존 문의를 빼고, `contacts_purge` 가 `purge_after` 지난 contact 와 그 문의를 함께 지운다(서버 시작 때 + 매일, 유예 중 행은 대상 아님).
- **인증**: 삭제된 계정의 옛 웹 세션은 무효, 옛 MCP 토큰(`iat < deleted_at`)은 `AUTH-REVOKED`. `iat` 없는 토큰 + contact 일치도 거절(보수). 그 뒤 같은 이메일·같은 sub 로 새로 로그인하면 새 uid 계정(옛 자료·contact 와 연결 없음).
- **삭제됨 ≠ 차단**: 삭제됨 = `users.status='deleted'`, 차단 = `blocked_at`. 섞어 쓰지 않는다.
- **되돌림 경계**:

| 시점 | 되돌릴 수 있는 곳 |
|---|---|
| 배포 직후, 새 데이터 없음 | PR #21 코드(R4 2단계 통과 조건). 새 테이블·열은 남긴다 |
| 새 데이터(이름·사람·요약/반응·수락) 생긴 뒤, 삭제 실행 전 | R4 4단계를 통과한 PR #21 코드. 새 데이터는 옛 화면에 안 보이고, 다시 전진 배포하면 보인다. env 를 지웠다면 옛 코드는 off/off 로 돈다(지원 조합) |
| 삭제를 한 번이라도 실행한 뒤 | **삭제 계약을 아는 버전(PR B 이후)만**. PR #21 이하 복귀 금지 — 옛 코드는 status·contact·iat 거절을 모르고 180일 파기가 보존 문의를 지운다 |
| 백업 복원 | 삭제 이전 백업을 운영에 복원하면 지운 자료가 되살아난다 — 복원 뒤 삭제 CLI 를 다시 실행하는 절차를 운영 문서에 둔다(백업 보존 기간은 **모름**) |

- 실측(2026-10-20, 운영 복원본 사본): 삭제 뒤 옛 코드는 기동·렌더는 정상이지만 **조용히 깨진다** — 원래 이메일·sub 로 새 계정을 거절 없이 만들고, 180일 파기가 보존 문의·이력을 지워 contact 가 고아가 되며, 옛 세션 검사는 status 를 모른다. 기동 오류가 없어 운영자가 알아채기 어렵다.
- **백업 복원 뒤**: 복원 시점 이후에 실행한 삭제는 CLI 로 다시 해야 한다. 복원본에 통지 행(`deletion_contacts`, `deleted_at` NULL)이 있으면 유예 계산 그대로 `delete-preview` → `delete-run`. 없으면 `delete-notify` 부터 **다시 통지한다** — 사용자가 통지를 두 번 보는 것과 7일 유예가 다시 걸리는 것은 수용(2026-10-20 메인 결정).

## 웹 (`web.py` · `app.html`)
- 로그인은 MCP 와 같은 AuthKit. `/login` 이 PKCE(S256)·`resource=<주소>/mcp` 로 authorize 에 보내고, 콜백에서 받은 액세스 토큰을
  **MCP 와 같은 `AuthKitVerifier`·`user_id_for`** 로 검증·연결한다 → 웹 사용자 = MCP 사용자(같은 sub).
- 서버 메모리에 상태 없음. PKCE verifier·state 는 10분 서명 쿠키, 세션은 7일 서명 쿠키(HMAC-SHA256, HttpOnly, SameSite=Lax, https 면 Secure).
- 쓰기 API 는 `POST /api/candidate/decide`(confirm·exclude·pending·edit·edit_confirm, `expected_revision` 선택 — 틀리면 CONFLICT, 웹 편집 문장은 wording_by=self)와 `POST /api/item/decide`(항목: apply·discard·confirm·exclude·pending·archive·restore, `expected_revision` 필수, 제안 제외는 discard+proposal_id). 표준 편입 `GET /api/std/preview`(미리보기, 읽기 전용) · `POST /api/std/choose`(`{std_key, state: deferred|refused|incorporated, confirmed_meaning, expected_revision}`). 모두 세션 + `Origin` 이 공개 주소와 같을 때만. 「고쳐서 남기기」는 edit→confirm 두 번(이력 두 줄). 읽기 `GET /api/item?id=` 는 항목·관계 대상·들어오는 관계·이력·승격 상태(서랍).
  PR B 코어 쓰기(세션 + Origin + 페이로드 상한 + `write_gate`): `POST /api/me/name` · `/api/nav/accept` · `/api/people`·`/api/people/{id}`·`/api/people/{id}/delete` · `/api/sources` · `/api/reflections`·`/api/reflections/{id}`·`/api/reflections/{id}/review`·`/api/reflections/{id}/delete`. 남의·없는 id = 404 `NOT-FOUND`, `NAME-REQUIRED` = 403.
  기록·질문·후보 추가는 웹에 없다 — 연결한 AI 대화에서 한다.
- `app.html` 은 로컬 보드(`~/Personal/scripts/checkpoint_board.html`·360·지도) 구조를 옮긴 보드형 화면(2026-10-18): 마스트(날짜·상태 스펙트럼·질문 레전드) + 기본 탭 오늘/질문/기록/구조/지도(사용자 정의 tabs 있으면 그 탭 + 구조·지도 고정) + 대조 2열(≥1000px). 토큰은 보드 값. 가안·스펙은 레포 `notes/`(gitignore).
- 새 탐색(PR B, `user.nav=new`) = 이름 입력 화면(표시 이름 없을 때, 건너뛰기 없음) → 탭 줄 「나」(`#_me`, 출처별 탭 `#_me/<출처 id>`) + 사용자 탭(없으면 질문·기록) + 「오브젝트」(`#_obj` · `#_obj/person[/<id>]` · `#_obj/source[/<id>]` · `#_obj/reflection[/<id>|/new[/<출처 id>]]`) + 지도. 새 화면 id 는 `_` 로 시작해 사용자 탭 id 와 겹치지 않는다. 「오늘」은 탭 줄에서 빠지고 「나」 홈 [오늘 할 일] 버튼(`#today`, 미결정 후보 수)로 연다 — `#today`·`#home`·`#structure…` 옛 링크는 그대로 열린다. 「구조 정의」 → 계정 메뉴 「내 구조 설정」(`#_settings/structure`), 옛 `#structure/defs` 는 그리로 바꾼다. 미수락 계정은 아래 화면 그대로.
- 구조 탭 = 「항목」/「구조 정의」 두 보기(PR #20). 구조 정의(`#structure/defs`) = 오브젝트·필드 목록(표시명·표준명·구분 코어/표준/선택 템플릿/직접 추가·「표준 정의 보호」·참조 대상·cardinality)과 그래프 전환(서버 `structure_svg` 결정적 SVG) + 표준 편입 미리보기·선택(확인 대화창). 기록 탭은 기록마다 출처(없으면 「출처 미확인」)·출처 필터·「확정 전 재검토」. 문의는 하단 섹션 없이 계정 메뉴 대화창.
- 2단계(항목 보기): 360(서브 필: 기준 오브젝트 · 후보 · 그 밖의 오브젝트, 카드 그리드 + 오른쪽 서랍 `#panel`). 서랍은 본문·필드·관계 대상·들어오는 관계·이력·승격 사실, 제안이 있으면 diff + [적용][제외(discard)], 기준이면 [내 판단으로 남기기][남기지 않기][되돌리기]. 라우트 `#structure/<오브젝트ID>[/<항목ID>]` · `#structure/cand/<필터>`. 오늘 탭은 질문 없는 미결정 후보를 기록별로 묶고, 지도 「쌓이는 재료」에 확정 기준을 함께 둔다.
- 옛 설명: `app.html` 은 `web/index.html` 의 이식본. 필드명(`record_id`·`quote`·`question_id`), 문구(Claude Code·파일 전제 제거), 기본 탭 「시작」+「기록」, 시각은 브라우저 시간대.
  `web/index.html` 은 로컬 키트가 아직 쓰므로 그대로 둔다.
- 도움말 `GET /help`(로그인 불필요, DB 안 건드림): 레포의 `guides/09-hosted-start.md`·`10-words.md` 를 서버 시작 때 읽어(`CRM_GUIDES_DIR` 가 있으면 그 폴더, 없으면 `server/../guides`) 정규식으로 HTML 로 바꾼다(제목·문단·표·굵게·목록·인라인 코드·링크, `10-words.md` 링크는 `#words`). 모든 글자를 먼저 escape(raw HTML 통과 없음), 링크는 http(s)·#앵커·상대경로만. 좁은 화면의 표는 가로 스크롤. 토큰·글꼴은 `start.html` 의 `:root` 블록을 그대로 쓴다. 세션 쿠키가 있으면 [노트로], 없으면 [로그인]. 문서가 없으면 시작 로그에 경고 + 503.
  문서 정본은 guides 이고 서버는 읽기만 한다. `app.html` 푸터·`start.html` 하단에 [도움말].
- 보안 헤더(웹 HTML 응답만 — MCP·JSON·/health 제외): `nosniff`·`X-Frame-Options: DENY`·`Referrer-Policy: same-origin`·CSP `frame-ancestors 'none'; base-uri 'none'; object-src 'none'; script-src 'self'`. `/app` 은 보낼 본문의 `src` 없는 인라인 `<script>` 1개를 요청마다 sha256 해 `'sha256-…'` 를 더한다(1개가 아니면 해시 없이 `'self'` 만 + 로그 — 화면 스크립트가 막힌다). `'unsafe-inline'`·`'unsafe-eval'` 은 넣지 않는다.
- 개인정보 처리방침 `GET /privacy`(로그인 불필요, DB 안 건드림): 정본 `guides/11-privacy.md` 를 /help 와 같은 렌더러·보안 헤더로. 권리 행사 경로는 대시보드 계정 메뉴 「문의 보내기」. 문안의 `{{CONTACT_LINE}}` 은 env `CRM_CONTACT`(선택 — 보조 메일, 레포에 적지 않는다)가 있으면 메일 한 줄로, 없으면 빈 문자열로 바꾼다. help_get 에는 안 들어간다. 문서가 없으면 503. 링크는 `start.html` 하단·`app.html` 푸터·/help 하단.

## 경계
- 사용자 결정: AuthKit JWT(RS256, iss·exp·aud 검증) 의 `sub` 우선. 처음 연결 땐 userinfo 의 검증된 이메일로 같은 이메일 = 한 사용자. `email` 클레임은 토큰에 없다.
- 저장은 Postgres (`schema.sql`, 설계 `data-model.md`). 행 격리는 user_id + 복합 FK.
- 도구: state_get · question_add · question_list · record_add · record_get · candidates_propose · candidate_decide · candidate_list · profile_set.
  2단계(정의 층) 5+1: definitions_get · definition_propose · definition_apply · item_propose · item_decide + record_attach(「어디에 붙나」: 기준·결정·후보 연결, 관심 축, 쓸 자리·아직 안 붙는 것). 동적 내용은 JSON 문자열(`definition_json`·`data_json`).
  도움말 help_get(`topic`: start·words·all, 기본 words) — `/help` 와 같은 guides 원문을 `{topic, text}` 로. INSTRUCTIONS 끝 줄: 해당 topic 하나만 읽고, 같은 대화에서 다시 부르지 않고, 문서에 없으면 "문서에서 확인되지 않아요" + 가장 가까운 절, 제품 밖 질문엔 적용 안 함. 
  문의 feedback_send(kind·text·tool) — 사용자가 글을 확인한 뒤 호출, 응답에 보낸 글 그대로·고지(desk-0). 웹은 `GET·POST /api/feedback`(계정 메뉴 「문의 보내기」·「내 문의」 대화창, 관리자도 본인 것만) · `GET /api/feedback/<id>`(본인 문의 상세: 글·공개 답변·공개 이력). 내 문의 읽기 ticket_list_mine · ticket_get_mine(공개 이력만, 최근 공개 갱신순). 운영자 처리는 /admin 아래(위 「운영 콘솔」).
  R5 결함 3(PR B): record_provenance_set · definition_archive · definition_restore.
  PR B 코어 14: display_name_set · me_get · person_list · person_get · person_save · person_delete · source_list · source_get · source_create · source_reflection_list · source_reflection_get · source_reflection_save · source_reflection_review · source_reflection_delete. 목록 limit 기본 50·최대 100, cursor 불투명. `state_get` 응답 모양은 그대로다.
  도구는 모두 36개(19 + 3 + 14). 기존 19개의 이름·인자는 그대로다(설명 문구만 일부 갱신 · `definitions_get` 은 초안에 `draft=true` 키 추가) — 쓰기 도구에 `NAME-REQUIRED` 가 늘었다.
  **커넥터 갱신**: 도구가 늘면 ChatGPT 커넥터는 스키마를 다시 승인해야 하고, Claude 커넥터는 다시 연결하거나 새 대화를 열어야 새 도구가 보인다. PR B 배포 뒤 운영 확인 항목.
  웹 `/api/state` 는 같은 `db.state` 에 /app 전용 키를 얹는다 — `user`(email·이름 공유·desk_hidden, PR B `display_name`·`nav`) · `std`·`sources`·`source_counts`·`structure`·`structure_svg`(PR #20) · `me`·`people`·`reflections`(PR B). MCP `state_get` 에는 넣지 않는다(2단계 키: definitions · items · item_proposals · record_links, journal 행에 notes). 이름·인자는 고정(ChatGPT 는 승인된 스키마를 고정하므로 바꾸면 재승인).
  `candidates_propose` 의 `question_id=""` = 질문 없음(NULL). 화면은 기록별 묶음, 대조 경로 `#today/review/r:<기록ID>/<후보ID>`.
- 근거 인용은 **인용 적격 기록**(author_kind=self·text_process=verbatim·삭제 안 됨)의 원문에 글자 그대로 **한 곳**만 있어야 저장(0곳·2곳 이상·비적격 기록 거절). 기존 기록은 작성 판정 unknown(비적격). 확정(confirm·edit_confirm)도 근거 기록을 다시 검사한다 — 비적격이면 `CANDIDATE-REVIEW`(사유 author_unknown·external·processed·deleted), 미확정 후보에 `review` 사유. `record_add` 의 author_kind=external 은 `SCOPE-SELF-ONLY`(서버는 작성자 표시만 검사, 내용은 확인하지 않는다). 기록 1편당 후보 3개. 후보는 변경마다 revision+1, 이력에 변경 전 스냅샷(`prev_state`·`prev_revision`). 결정 이력(candidate_history)으로 되돌릴 수 있고, 확정 문장을 고치면 pending 으로 돌아간다.
