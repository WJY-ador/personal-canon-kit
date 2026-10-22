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
- **필수 동의 없음**(2026-10-19 v3 부터, 현행 v6 — 시행일은 릴리스 때 머리글에 채운다): 서비스 제공 처리는 계약 이행(개인정보 보호법 제15조①4호), 구조 분석(개수·종류)은 정당한 이익(①6호)으로 보고 처리방침 「처리 근거」 표로 공개한다. 동의 화면·재동의·쓰기 차단이 없다. 시작 화면에 「시작하면 개인정보 처리방침이 적용돼요」 한 줄. 처리방침을 바꾸면 버전·날짜를 올려 공개하고 지인에게 공지만 한다. `GET /consent` 는 옛 링크용 302 `/app`. 옛 `agreements` 행은 이력으로 남는다(새로 쓰지 않음).
- **v5 요약**(PR B): 표시 이름 필수(실명 아님·다른 이용자와 운영자 화면에 안 보임, 처리 근거 계약 이행) · 사람 = 별칭 이름표만 · 출처 요약/반응 = 내 생각만(검토 완료 ≠ 판단 확정, 인용 근거 아님) · 삭제 절차(「개인정보 요청」 문의에 요청 문구만 → 접수 10일 안 통지 → 7일 유예 → 삭제, 유예 중 취소) · contact 1년 보관 · 재가입 = 새 계정(연결 없음). 필수 동의 게이트는 여전히 없다.
- **v6 요약**(board 이전 PR): 연결 장치(토큰 90일·검증값만) · 활동(원문은 PC, 서버엔 행동·날짜·처음 수집한 날·별칭·HMAC 식별자·관계, 처리 근거 계약 이행) · 승인 대기(30일, 승인 전엔 기록 아님) · PC 에서 원문 열기 · 무인 요약(기본 꺼짐, 구독 CLI 전송은 위탁 표 밖) · AI 생성 표시 유지 · 운영자 화면엔 사람·출처·활동 대상 관계도 값·개수 0(v5 약속 유지) · 삭제 범위에 새 표. 「board 이전 예외」 문구는 넣지 않는다.
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
- **감사** `admin_audit`: 첫 지정·복구·부여·회수·웹 끊기·차단·해제 · 삭제 통지·실행·취소(PR B, detail = `ticket_id`·`result` 만) · 장치 연결·재발급·해제·허용 규칙 · 이전 적용·연장·종료(board 이전 PR — 아래 절). 실행자·대상 id 와 열거 키만(이메일·자유 텍스트 없음), 계정이 지워져도 남는다(화면엔 id 앞 8자). 1년 지난 행은 서버 시작 때 파기 — 단 `bootstrap`·`recover` 행은 남긴다(지우면 다음 시작 때 env 로 다시 승격한다).
- 세션 쿠키 = `{uid, iat, exp}` 서명 값(β2 에서 iat 도입, 그 전 쿠키는 무효였다). 재인증 제거 때 옛 쿠키에 남은 `ra` 는 무시할 뿐 무효로 만들지 않는다 — 재로그인 필요 없음.

## 운영 — 운영 콘솔 (/admin)

- 화면(전부 서버 렌더 + form, 스크립트 0, no-store, 비관리자·로그인 안 함·DEV 는 404): 왼쪽 세로 메뉴(PR A — Salesforce Setup 꼴, 정본 `web.py` `ADMIN_MENU`) = Quick Find + Setup Home `/admin` · Object Manager `/admin/schema` · Users `/admin/users` · Reports `/admin/reports` · Support `/admin/tickets` · Administration `/admin/setup`. **Setup Home** 3구역 = 개요(상태 띠·사용자 표·미연결 사용량·정의 분포) · 확인할 오류(오류 14일) · 문의(미종결·개인정보 기한 임박·기한 초과). **Users** `/admin/users`(사용자 목록: 오브젝트 n·관계 N·그중 연결 있음 M·표준 상태 코드 · 삭제한 계정은 「삭제됨」 — 상태 코드만, 표시 이름·사람·요약/반응·활동은 값도 개수도 0) · `/admin/users/<불투명 id>`(요약 → DMO 그래프 → 관계 집계(사람·출처·활동 대상 관계는 읽지도 세지도 않는다 — 그 칸 「—」, board 이전 PR) → 무결성 REF-DANGLING·REF-MISMATCH·MD-ORPHAN과 「검사 불가」 → 표준 상태 → 구조 트리 → 문의 요약. 구조 글자는 전부 `contract.respond`. PR A 에서 계약 변화 없음) · **Reports** `/admin/reports`(데이터셋 사용자·사용량·구조·문의, 묶기 ≤2·필터·기간 ≤180일, 행 ≤200, 정의 = GET 쿼리스트링) · **Support** `/admin/tickets`(리스트 — 개인정보 요청 안내 「요청 문구만 적어 주세요. 다른 정보는 넣지 마세요」와 삭제 CLI 순서 한 줄) · `/admin/tickets/<id>`(레코드: 원문·Path·공개 답변·내부 메모·재분류·이력) · **Administration** `/admin/setup`(관리자 부여·회수·웹 끊기·차단·감사 — 위 절). 관리자 `/app` = ⚙(/admin/setup) + 계정 메뉴 「운영 콘솔」·로그아웃(문의·이름 공유 메뉴·문의 대화창은 보내지 않는다). 운영 화면은 밝게 고정(OS 다크 미추종).
- **Object Manager**(`/admin/schema`, 조회 전용 — 편집·설치·편입 버튼 없음): 서비스 정의만 읽는다(`templates/std@1.json`·`author@1.json` + 코드의 코어 정의). 사용자 DB 는 읽지 않는다. 첫 화면 = 코어 9(질문·기록·후보·문의·출처 + PR B 나·사람·출처 요약/반응 + board 이전 활동 — 입력원 열 `stream_id` 와 그 FK 는 `CORE_HIDDEN` 으로 뺀다) + std@1 3(축·결정·기준) DMO 그래프 → 오브젝트 표(구분·판·필드·관계·정의상 잠금·서비스 설명·API 이름) → 관계 표 → 「선택 템플릿」 구역(「이전 원작자 템플릿 · author@1」). 상세 `/admin/schema/objects/{id}` · `/admin/schema/templates/{tid}[/objects/{id}]` — 공개 ID 허용목록(`core-record`·`std-principle`·`tpl-…` 꼴), 모르는 ID 는 404. 필드 표 = 표시명·API 이름·자료형·필수·읽기 전용·선택지(개수만)·정의상 잠금. 정의에 없는 값은 「모름(정의 미확인)」. 코어 열 표시명 표는 `service_schema.py` `CORE_FIELDS` 한 곳. 「나」(`users`)는 `display_name`·`status`·`nav_accepted_at`·`created` 열 정의만 보인다(`web.py` `CORE_ONLY`) — 열 이름·자료형이고 값은 읽지 않는다. API 이름 = 서비스가 정한 제품 문자열(코어 테이블·열, std/author key) — 사용자 데이터 아님. 숨기는 것: 선택지 값·운영자 전용 열(`admin_note`·`hint`)·정의 파일 id·내부 경로·사용자 정의 key·label(이름 공유 동의여도). 기록 → `item_links` 는 선 없이 「항목 — 대상 오브젝트 모름 · 분류 대상은 사용자별 역할로 정함」.
- **Quick Find** `GET /admin/find?q=`: 서버 검색. 색인 = 운영 메뉴 + 서비스 표준(코어 + std@1) 정의의 표시명·API 이름·설명·자료형(프로세스당 한 번 메모리에 만든다). 사용자 DB·선택 템플릿은 찾지 않는다. 결과 ≤50, 검색어 100자 상한(넘으면 400 `ARG-INVALID`). 검색어는 화면·속성에 되비추지 않고 저장하지 않는다. 여러 낱말은 모두 들어간 항목만.
- **옛 URL 유지**: `/admin/schema?u=` 는 302 `/admin/users/<불투명 id>`, `/admin/reauth` 는 302 `/admin`.
- **운영 경계** `admin_db.py`: 운영 화면의 DB 읽기는 허용표 `ALLOW` 의 키로만 한다(고정 SQL·허용 열·조회자·동의 조건). 기록 본문·질문 글·탭 이름·정의 이력은 읽지 않는다. 문의 원문·내부 메모는 레코드 페이지에서만. 정적 시험과 표식 시험이 지킨다.
- **이름 공유(선택 동의 `struct_names`)**: 동의한 사용자의 오브젝트·필드·선택지 **현재 이름**(오브젝트·필드 label, 선택지 label)만 `/admin/users` 상세에 보인다(리포트엔 없음). key·선택지 값·출처 이름·위치는 동의여도 0. 비동의자는 자리표 「오브젝트 n」·「필드 a」와 편입된 표준의 표준명만(URL 은 불투명 id). 철회하면 다음 조회부터 빠진다. 저장은 `optional_consents`(현재 상태 1행) + `optional_consent_log`. 동의 버전은 처리방침과 별개인 `db.STRUCT_NAMES_VERSION` — 이름 공유 범위를 바꿀 때만 올리고, 올리면 옛 버전 동의는 무효.
- **문의 처리**: 상태 전이·답변·메모·재분류는 변경과 `ticket_history`(공개/내부) 사건을 한 트랜잭션으로. 사용자에겐 공개 사건과 답변만(웹 「내 문의」 상세·MCP `ticket_list_mine`·`ticket_get_mine`). privacy 로 재분류하면 기한 기산점 = 최초 접수일(이미 지났으면 즉시 「기한 초과」), privacy 에서 빼면 사유 필수·`privacy_log` 는 남김. 종결 180일 뒤 파기는 서버 시작 때 + 실행 중 매일(문의 이력도 cascade).
- **처리방침 이력**(`guides/11-privacy.md`): v2 = 구조 분석 목적·이름 공유 선택 동의·이용 기록 사실대로(v2 를 처음 띄우는 `init_schema` 가 그 전 `usage_daily` 를 **한 번** 지운다, `schema_marks` 표식). v3 = 필수 동의 게이트 제거. v4 = 기록 출처·표준 편입 선택·운영자 출처 개수만·작성자 표시만 검사. v5 = 위 「처리방침」 절 v5 요약. v6 = 같은 절 v6 요약.

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
- **표시 이름 관문**(`NAME-REQUIRED`, 웹·MCP 공통 · `db.write_gate` 한 곳): 판정 = `users.display_name IS NULL`. 문구 「대시보드에서 표시 이름을 정해 주세요.」(`contract.NAME_REQUIRED_MSG`). MCP 는 `main.WRITE_TOOLS`(19개)가 관문, `ALLOWED_TOOLS`(24개 — 읽기·`help_get`·`feedback_send`·`ticket_*`·`display_name_set` + board 이전 읽기 7)는 통과 — 분류 안 된 도구가 있으면 시험 24 가 실패한다. 웹은 `/api` 쓰기 경로 전부 관문(웹 응답 403), 예외 = `/api/me/name`·`/api/feedback`·`/api/optional-consent`·`/api/desk-visibility`·로그아웃. `/api/nav/accept` 는 관문을 받는다. 입력 = 앞뒤 공백 제거 뒤 1~80자 한 줄, 빈 값으로 바꾸기 거절, 이메일 등으로 자동 채우지 않는다. 기존 사용자는 다음 웹 로그인 때 이름 입력 화면(건너뛰기 없음).
- **분류 역할** `user_roles(classify)`: 기록 → 분류 대상 링크(field_key `axes`)는 역할 대상 오브젝트의 항목만. init 이관 = key `axes` 적용 오브젝트, 시드 = 표준 축, `record_attach` 지연 배정. 대상 없으면 `CLASSIFY-OFF`.
- **오류 코드**: PR #20 `STANDARD-LOCKED` · `CANDIDATE-REVIEW` · `SCOPE-SELF-ONLY` · `CLASSIFY-OFF`(`FEATURE-OFF` 는 PR B 에서 없앴다) · R5 `PROVENANCE-LOCKED` · PR B `NAME-REQUIRED` · `NOT-FOUND`(코어 남의·없는 id) · 삭제 CLI 코드(아래 절) · board 이전 PR 장치·승인 대기·이전 코드와 `PERSON-IN-USE`·`PROVENANCE-AI-LOCKED`(아래 「연결 장치」 절, 상태 표 `imports.http_status`).
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

## 운영 — 연결 장치·가져오기·board 이전 (board 이전 PR)

- 정본 명세는 레포 밖 `notes/2026-10-20_board-마이그-구현계획.md`(고정본)·`notes/raw/2026-10-20_board-migr-astra-r3.md`(gitignore). 계약 = `contract/imports/`(`canon-inbox-1.schema.json` 장치 수신 · `board-migration-1.schema.json` 이전 명세 · `vectors/` 서버·에이전트 공용 시험 벡터). 기존 `contract/*.json` 은 그대로.
- **경계**: 원문은 PC, 서버엔 구조화 데이터만. `imports.py` = 수신·장치 토큰·승인 대기·개정·한도·이전 기본 연산, `activity.py` = 활동·관계·「나」 집계. 서버는 모델을 부르지 않고 본문·토큰·URL 을 로그·감사·오류 문구에 넣지 않는다. 문자열 필드에 URL·이메일·로컬 경로 꼴이 있으면 `CONTRACT-INVALID`. 실명 탐지는 하지 않는다.
- **스위치 5개**(`envnames.SWITCHES`): 코드 기본값 꺼짐 — env 가 없거나 값이 정확히 `1` 이 아니면 꺼짐. 쓰기 직전마다 읽는다. **배포 때 미리 넣지 않는다** — 켤 때만 Render env 에 하나씩 더한다. 상시 확정되면 그 스위치를 코드에서 지우는 PR 을 따로 둔다(옛 `CRM_FEATURE_*` 선례).

| 순서 | env | 꺼져 있으면 | 켜는 때 |
|---|---|---|---|
| 1 | `CRM_IMPORTS_ENABLED` | `POST /api/imports` 전체 503 `IMPORTS-OFF`(화면 「준비 중」). 장치 연결·재발급·해제·허용 규칙, `GET /api/imports/device`·`/revoke`·접수 확인은 스위치 무관 | 원작자가 웹에서 장치를 연결한 뒤 |
| 2 | `CRM_ACTIVITY_IMPORTS_ENABLED` | 활동 즉시 적재 503 `ACTIVITY-IMPORTS-OFF`. 내 글과 사람이 직접 돌린(attended — 에이전트 `run-once`, `--scheduled` 없이) AI 요약 접수는 1번만 본다 | 장치 `likes` 어댑터를 켤 때 |
| 3 | `CRM_INBOX_APPROVALS_ENABLED` | 승인 대기 글의 승인 `APPROVALS-OFF`. 제외·만료는 스위치 무관 | 내 글 접수를 받기 시작할 때 |
| 4 | `CRM_BOARD_MIGRATION_ENABLED` | `migrate_cli.py run·resume·close` 가 `[SWITCH-OFF]`, 서버 `migration_apply_row` 는 `MIGRATION-OFF`. `init`·`validate-only`·`status`·`extend` 는 무관 | 이전 소량 실행부터 병행 종료까지 |
| 5 | `CRM_UNATTENDED_SUMMARY_IMPORTS_ENABLED` | 무인(`execution=unattended`) AI 요약 접수 503 `SUMMARY-IMPORTS-OFF` | **약관 확인(출시 차단 항목) 뒤 마지막.** 예약 실행(launchd)의 요약만 여기에 걸린다. 장치 설정 `summary.enabled` 도 따로 꺼져 있다 |

- **장치 토큰**: 꼴 `imp_<is_…>.<it_…>.<비밀 43자>`. 웹 계정 메뉴 「연결 장치」(`#_devices`, 새 탐색 전용)의 연결·재발급 응답에 **한 번만**(no-store) 싣고, 서버엔 SHA-256 검증값(`import_streams.secret_hash`)만 둔다. 만료 90일(`TOKEN_DAYS`). 활성 장치 최대 5(`DEVICE-LIMIT`). 재발급 = 옛 토큰 즉시 무효. 연결 해제 = `revoked_at`(되살리지 않음 — `DEVICE-REVOKED`, 새로 연결). 받은 활동·기록은 남는다. 허용 규칙(policy) = 행동 부분집합 · 변환 `board-likes/1`(활동 바로 저장)·`self-text/1`(내 글, 승인 대기)·`self-summary/1`(AI 요약, 승인 대기) · 축 ≤10(분류 역할 오브젝트의 항목). 바꾸면 `policy_revision` +1 → 장치의 다음 업로드가 `POLICY-STALE` 를 받으면 에이전트가 같은 토큰으로 `GET /api/imports/device` 를 불러 새 규칙을 받고, 아직 접수되지 않은 대기 요청을 새 판으로 다시 만들어 그 실행에서 한 번 다시 보낸다(토큰 재입력 없음). 새 규칙이 막는 요청은 보내지 않고 격리한다. 입력원·키 공간이 바뀌었거나 장치 정보를 못 받으면 멈추고 connect 를 다시 해야 한다. 토큰 거절 = `AUTH-INVALID`·`TOKEN-EXPIRED`·`TOKEN-REVOKED` 401 · `AUTH-BLOCKED` 403(계정 차단). 웹 쿠키·MCP 토큰은 장치 경로에서 받지 않는다.
- **장치 경로**(`main.py`, Bearer 장치 토큰만): `POST /api/imports`(본문 `canon-inbox/1` — 종류 `self_text`·`activity`·`self_summary`, 헤더 `Idempotency-Key: ci1_<64 hex>`) · `GET /api/imports/{import_id}`(같은 장치의 접수 결과 — 응답 유실 뒤 확인) · `GET /api/imports/device`(정책·키 공간·만료) · `POST /api/imports/device/revoke`(자기 토큰 폐기). 오류 본문 = `{code, msg[, resume_at]}`, 상태 = `imports.http_status` 한 표. 요청 값을 응답에 되돌려 주지 않는다. 이용 집계 이름 `api:imports`·`api:import_device`·`api:import_status`(사용자 id 없이).
- **개정·멱등**: 같은 원본·같은 개정·같은 내용 = duplicate(업무 쓰기 0) · 같은 개정·다른 내용 = `IDEMPOTENCY-CONFLICT` · `previous_revision` 이 서버 최신 접수와 다름 = `SOURCE-REVISION-CONFLICT` · 원본 종류 변경 = `KIND-CHANGED`(전부 409). 새 개정·같은 반영 내용 = `noop`. 같은 원본의 옛 대기 개정은 새 개정이 오면 제외(`resolved_by=superseded`)로 닫고 본문을 지운다. 집계는 현재 판(`is_current_applied`)만.
- **한도**(`imports.LIMITS`, KST 0시 기준, 같은 요청 재전송은 세지 않음): 요청 — 장치 분당 10·사용자 분당 30(`RATE-LIMIT`) · 내 글/요약 — 장치 하루 10건·128KB, 사용자 20건·256KB · 활동 — 장치 하루 500건·1MB, 사용자 1000건·2MB(`IMPORT-QUOTA` 429 + `resume_at`) · 승인 대기 동시 100건·1MB(`INBOX-FULL`, 만료·파기된 대기는 세지 않음) · 새 사람 = 기존 사람 한도 하루 50 — 넘으면 그 활동 트랜잭션 전체 취소 `PEOPLE-QUOTA` 429 + `resume_at`(다음 KST 0시). 요청 본문 64KB · 활동 8KB · 본문 8000자/32KB. 전역 DB 크기 관문 `QUOTA-GLOBAL` 507(웹·MCP 쓰기와 같은 관문). 승인은 기록 한도(기록 50)를 쓴다.
- **승인 대기**(`#_inbox`, 「나」 홈 [승인 대기 N]): 승인·제외는 **웹에서만**(MCP 는 `inbox_list`·`inbox_get` 읽기만). 승인 = 최신 대기 개정만(`INBOX-NOT-LATEST`), 보관 기간 안만(`INBOX-EXPIRED`), 이미 처리 = `INBOX-RESOLVED`. 한 트랜잭션: 내 글 → `records`(self·verbatim, `ingest_kind=inbox`) · AI 요약 → `records`(unknown·generated, `generation_kind=ai`, 신고된 도구·모델은 `generation_meta`) — 인용 비적격이고 출처 판정을 바꾸면 `PROVENANCE-AI-LOCKED`. 기록 출처 글자 「연결 장치에서 가져옴」. 대기 본문은 30일(`PENDING_DAYS`), 승인·제외·만료 때 지운다.
- **사람 HMAC**: 장치가 보낸 `HMAC-SHA256(PC 키, JCS(["person/1", 제공자 코드, 계정 식별자]))` 를 키 공간(`hmac_namespace`, 장치마다 무작위 `hn_…`)과 함께 `people.identity_hmac` 에 둔다. 처음 보는 사람은 별칭 「사람-NNNN」(실명에서 만들지 않는다). 키·실명·대응표는 서버에 오지 않는다. 화면·MCP 엔 별칭과 사람 ID 만.
- **삭제 보호**: 활동·기록·항목에 연결된 사람 삭제 = `PERSON-IN-USE`(연결 위치 개수를 문구에, 연쇄 삭제·연결 끊기 없음). 출처는 지우는 기능 자체가 없다(FK NO ACTION). `item_links` 대상에 사람·출처·활동이 더해졌다(이름 붙은 CHECK `ck_item_links_target` — 옛 DB 는 `init_schema` 가 정의로 찾아 DROP → ADD).
- **MCP 읽기 7**(`ALLOWED_TOOLS`, 표시 이름 관문 밖): `activity_list` · `activity_get` · `person_context_get` · `source_context_get` · `me_aggregate_get`(7d·30d·90d·all — 웹 `/api/me/aggregate` 와 같은 서버 함수) · `inbox_list` · `inbox_get`. 원문·원 URL·PC 경로·실명은 응답에 없다(활동은 불투명 원문 ID 만).
- **주기 파기**(`purge_once`, 서버 시작 + 매일): 기간 지난 승인 대기 → expired(본문 삭제) · 한도 카운터(분 1시간·하루 35일 지난 행).
- **감사**(`admin_audit`, detail 에 토큰·본문 없음): `device_connect`·`device_reissue`·`device_revoke`·`device_policy` · `migration_apply`(배치당 1행)·`migration_extend`·`migration_close`.
- **계정 삭제**: `db.DELETE_TABLES` 가 관계(`item_links`·`activity_links`) → 접수 대응표(`import_entries`) → 활동 → 한도 카운터 → 입력원(`import_streams`) 순서로 먼저 지운다(삭제 CLI 절차는 위 절 그대로). PC 의 원문·대응표는 서버 삭제와 별개다.

### board 이전 CLI (`migrate_cli.py`)

원작자 Mac 에서만. `.env` 는 읽지 않는다 — `DATABASE_URL` 을 명령 줄에 적는다(`admin_cli.py` 선례, DB 접속권 = 실행 권한). 대상 = `--user <uid>`(명세 `target_user_id` 와 같아야 한다). 이전 CLI 는 오브젝트를 만들지 않는다 — 직접 추가 오브젝트(AC·지원 문서)는 원작자가 대화로 먼저 정의·적용하고, 명세는 `object_ref`(std_id → template_slot → key 순 해석, 못 찾거나 둘 이상이면 validate-only 에서 막음)로 가리킨다.
```bash
DATABASE_URL=… .venv/bin/python migrate_cli.py init          --user <uid>             # 이전 입력원만 만들고 ID 출력 → PC 명세 생성기의 --source-instance-id
DATABASE_URL=… .venv/bin/python migrate_cli.py validate-only <명세.json> --user <uid>  # 읽기만 — 스키마·해시·대상 해석·순서·진행·스위치 상태
DATABASE_URL=… .venv/bin/python migrate_cli.py run    <명세.json> --user <uid> --limit 3   # 소량 — --limit 는 새로 접수된 행만 센다
DATABASE_URL=… .venv/bin/python migrate_cli.py resume <명세.json> --user <uid>       # 대조 뒤 나머지 — 서버 대응표로 완료 행을 건너뛴다
DATABASE_URL=… .venv/bin/python migrate_cli.py status [<명세.json>] --user <uid>     # 병행 기간·상태별 건수(·명세 진행)
DATABASE_URL=… .venv/bin/python migrate_cli.py extend --user <uid>                   # 병행 7일 연장(원작자가 결정했을 때만, 감사 migration_extend)
DATABASE_URL=… .venv/bin/python migrate_cli.py close  <명세.json> --user <uid>       # 병행 종료 — 미처리 0 + 전 행 재전송 = 전부 duplicate 일 때만 최종 해시 기록
```
- 명세는 PC 의 `agent/board_manifest.py`(AI 검토표 `sheet` → 검토 완료 → `build`)가 만든다. 서버엔 검토표를 올리지 않는다. 명세 해시 = 자기 해시 필드를 뺀 정규화의 SHA-256. 참조는 정확한 판(`{original_id, revision}`) — 참조 대상이 새 판이면 생성기가 그 판을 넣어 참조하는 행도 새 판이 된다.
- 행마다 업무 쓰기·대응표·관계·한도·감사를 한 트랜잭션으로. 같은 개정·같은 내용 = duplicate, 새 개정 = 기록은 새 판·항목은 제안 → 적용. 마지막 이전 뒤 서버 항목이 바뀌었거나 보관됐으면 `TARGET-REVISION-CONFLICT`(몰래 되살리지 않는다). 명세에서 빠졌다고 서버에서 지우지 않는다.
- 병행 기간: 첫 업무 적용 트랜잭션에서 시작, 14일. 끝나면 `MIGRATION-WINDOW-CLOSED` — 원작자가 결정했을 때만 `extend`(CLI 는 한 번에 7일, 서버는 호출당 7의 배수·최대 28일만 받는다. 누적 상한은 없다). 종료한 입력원은 다시 열지 않는다.
- 종료 코드: 0 성공 · 1 거절·충돌(행 번호·불투명 ID·코드만 출력, 제목·본문·필드 값 없음) · 2 쓰는 법 · 3 일부만(일일 한도 `RATE-LIMIT` — 기록 50·제안 100·결정 300, 다음 KST 0시 뒤 `resume`).
- `SOURCE-REVISION-CONFLICT` 로 멈추면 `resume`(서버 대응표를 다시 읽어 `previous_revision` 을 정한다).

### 로컬 에이전트 (`agent/`) 요지

키트 루트에서 `python3 -m agent …`(표준 라이브러리만, Python ≥3.9). 웹 「연결 장치」에서 토큰 받기 → `connect --server https://<주소>`(토큰 비표시 입력 → Keychain) → 홈의 `config.json` 에 `likes` 설정 → `run-once --no-upload` · `validate` → `run-once` → `schedule install`(launchd 1시간) → `serve`(원문 보기 127.0.0.1:8790). 무인 요약은 꺼짐이 기본. 자세한 것은 [`agent/README.md`](../agent/README.md).
- **장치 쪽 `SOURCE-REVISION-CONFLICT` 멈춤**: `status` 에 멈춤 N(SOURCE-REVISION-CONFLICT)이 보이면 서버의 최신 판과 PC 판이 갈라진 것이다. Phase 1 에는 자동 해소가 없다 — 해당 원본은 보내지 않고 남겨 두고 운영자에게 알려 수동으로 정리한다(그 원본만 `halted`, 다른 원본은 계속 간다. 정리할 때는 에이전트 홈을 백업한 뒤 서버 `import_entries` 최신 개정과 PC `state.sqlite` 의 `revisions` 를 대조한다. 서버 행을 지우거나 고치지 않는다).
- **연결 해제**: `disconnect` = 서버 토큰 폐기 → 로컬 토큰 제거. 서버 장애면 로컬을 남기고 종료 1, 웹에서 이미 폐기했으면 `--local-only`. HMAC 키는 `--forget-hmac-key` 일 때만 지운다 — 지우면 같은 사람 연결이 끊긴다.

### 되돌림 경계 (board 이전 PR)

| 상태 | 처리 |
|---|---|
| 스위치 전부 false 로 배포, 자료 생성 전 | 기능 끄기·코드 복귀. PR #22 코드로 돌아갈 수 있다(구현계획 §7 — 이 PR 의 옛 코드 호환 실측은 따로 확인). 새 표·열은 남긴다 |
| 승인 대기만 있음 | 접수 중단(1번 스위치 끔)·토큰 폐기·제외·만료 |
| 활동·기록·항목이 생긴 뒤 | 추가 적용 중단(스위치 끔)·현재 판 노출 조정·지원되는 정정. **이 PR 이후 버전으로만** — PR #22 이하로 돌아가지 않는다 |
| 후보·발행이 참조한 뒤 | 옛 판·참조 유지, 새 판으로 정정 |
| 병행 종료 뒤 | 기존 창을 몰래 다시 열지 않는다. 별도 이전 범위와 기간이 필요하다 |

운영 DB 전체 복원·배치 전체 삭제·옛 기록 출처 일괄 변경을 정상 되돌림 수단으로 쓰지 않는다. 자료가 생긴 뒤 옛 코드로 돌아갈 때는 신규 데이터와 옛 코드의 호환을 먼저 확인한다.

## 운영 — 작업 전달 (PR ②-a)

서버 = 작업 요청·할당·상태·값 수신. 실행은 PC(`agent/jobs.py`, 구독 `claude -p`). 서버 모델 호출 0. 세션(`session_id`) = 여러 실행을 묶는 불투명 ID, 작업 = PC 실행 1번. CLI 세션 ID 는 PC 에만.

- **저장**: `jobs`(본문 열 없음 — 상태·임대 세대·만료·`attempts`·`reason_code` 코드만). 입력·결과(미확정 내용 전체) = **임시 보관** — public 이면 별도 스키마 `tmp`(`tmp.job_inputs`·`tmp.job_results`, FK → `jobs` ON DELETE CASCADE), `db._init_tmp` 가 서버 시작 때 만든다. 시험 임시 스키마(`CRM_TEST_SCHEMA`)에선 같은 스키마 안 `tmp_job_*`(DROP SCHEMA 가 함께 지운다). 운영 백업은 `pg_dump -n public` 이라 `tmp` 가 빠진다 — 복원 시험(2026-10-22, `crm_test_jobs`): 복원 DB 에 `tmp` 0·입력 문자열 0, `jobs` 행만 남고 그 작업은 `JOB-GONE`.
- **상태**: queued →(장치 폴) leased →(입력 조회) running →(결과) result_ready →(②-b 확정) confirmed · running →(실패) failed · 임대 5분 만료 → needs_check(재실행 없음) · 접수 24시간(서버 시각, 연장 없음) → expired · 웹 취소 → cancelled. 끝난 작업·만료 작업의 임시 본문은 다음 장치 폴·웹 접수·상태 확인(전역 정리) 또는 `purge_once`(매일) 중 빠른 쪽에서 지운다. 만료 판정(읽기·수신 거절)은 그 자리에서 서버 시각으로. needs_check·expired **상태 전이**는 같은 정리 시점에 일어난다 — 임대가 지난 결과는 곧바로 409 `JOB-LEASE` 로 거절되지만 그 직후 DB 는 아직 running 일 수 있다(결함 아님, 저장 경로 없음). 종료 표식(본문 없는 `jobs` 행)은 30일(`db.JOB_MARKER_DAYS`) 뒤 삭제.
- **장치 경로**(Bearer 장치 토큰만 — 웹 쿠키·MCP 토큰 아님, 장치 권한에 확정 없음): `GET /api/device/jobs?wait=0..30` → 200 `{id,kind,session_ref,input_ref,input_version,definitions_version,lease:{generation,expires_at}}` · 204 · 429. 같은 세션에 leased·running 이 있으면 그 세션 다음 작업은 주지 않는다(직렬화). `GET /api/device/jobs/<id>/input?lease_generation=N` → `{text, input_version}`(성공마다 leased→running·`attempts` +1) · `POST …/result` `{lease_generation,input_version,result_version,summary_text,changes,blocked,text_map,model_meta}`(같은 `result_version` 재전송 = 200 `{duplicate:true}`, 본문 64KiB) · `POST …/fail` `{lease_generation, reason_code}`(코드 7종: output-not-json·output-shape·result-shape·timeout·cli-exit·login·input-too-large, 원문 없음). 409 `JOB-LEASE`(세대·장치·임대 만료)·`JOB-VERSION` · 410 `JOB-GONE`(없음·남 사용자·만료·취소) · 503 `SWITCH-OFF`.
- **웹 경로**(세션 + Origin): `POST /api/jobs` `{kind, text, session_id?}` → 201 `{id, session_id}`(text 8,000 코드포인트·32KiB 각각, `PAYLOAD-LIMIT`) · `GET /api/jobs/<id>?wait=0..30`(본인만, result_ready 면 `result`) · `POST /api/jobs/<id>/cancel`(멱등, 스위치 무관) · `POST /api/jobs/<id>/confirm` = 확인 1번(②-b, 아래 절).
- **스위치**(`envnames.SWITCHES`, 기본 꺼짐 — 운영 env 는 원작자 확인 뒤): `CRM_CONVERSE_ENABLED`(converse) · `CRM_ROUTINE_ENABLED`(routine_ingest). 접수·할당·결과 수신·확정 4곳에서 본다. 꺼져도 만료·정리·취소는 돈다. `summarize_reading` 은 ②-d 전 스위치가 없어 늘 꺼짐.
- **한도**(`imports.py`, LIMITS 옆): 접수 하루(KST) `JOB_REQUEST_LIMITS` — **K3 원작자 결정(2026-10-22): 작업 종류별 일일 한도 없음 = `None`**(카운터 `job_<kind>` 는 관측용으로 센다). `None` 의 뜻은 등록된 작업 종류의 일일 요청 한도에만 — 키가 빠진 종류·숫자 아닌 값 = 설정 누락 503 `LIMIT-UNSET`(fail-closed), 모르는 kind = 400. 숫자를 넣으면 넘을 때 429 `JOB-QUOTA` + `resume_at` · 살아 있는 작업 사용자당 20(`JOBS_OPEN_MAX`, **가안** — 원작자 결정 남음, 429 `JOB-LIMIT`). 폴링은 별도 버킷 `POLL_LIMITS` — 장치 분당 4·사용자 분당 12(장치 폴 + 웹 상태 확인 합산), 동시 롱폴 장치 1·사용자 웹 2(`POLL_CONCURRENT`, 서버 프로세스 메모리), 429 에 `Retry-After`. `/input`·`/result`·`/fail` 은 기존 장치 요청 버킷(장치 분당 10·사용자 30). PC 시도 수는 `jobs.attempts` 에 남고 시도 한도는 PC 가 센다.
- **계정 삭제**: `db.DELETE_TABLES` 에 `jobs`(입력원 앞) + 임시 보관 명시 삭제(`user_delete`·삭제 CLI ③ 앞). 임시 보관은 schema.sql 밖이라 정적 목록 대조에 들어가지 않는다.
- **로컬 시험 서버**(J3): `DATABASE_URL=postgresql://localhost/crm_test_jobs CRM_DEV=1 CRM_DEV_USER=<합성@example.com> CRM_CONVERSE_ENABLED=1 PORT=8807 .venv/bin/python main.py` — 스위치는 그 프로세스 env 에만. 장치 토큰 = `imports.device_connect(uid, 라벨)["token"]`(시험 DB). 웹 쓰기 Origin = `http://127.0.0.1:8800`(DEV 허용 목록).

## 운영 — 쌓기 단계·확인 1번 (PR ②-b)

대화 1세션 = PC 가 구조에 맞춰 정리(stack/1) → 서버가 결정적으로 다시 검사해 미리보기(임시) → 웹 확인 카드 → [확정] 1번 = 한 트랜잭션. 서버 모델 호출 0. 모델 출력으로 확정·인용·한도·stage 를 정하지 않는다(그런 키는 계약 거부). 코드 = `stack_service.py`(스냅샷·재검증·미리보기·제외·stage·확정) + `db.py`(작업·세션 경로).

- **세션**(`stack_sessions`, 본문 0): 첫 converse 접수가 만든다. 입력 종류(`converse`·`verbatim` = 원문 던지기)는 첫 입력 때 고정. 만료 = 최초 입력 + 24h 고정(후속 턴·재정리로 연장 없음, 작업 만료 ≤ 세션 만료). 확정은 세션당 1번(`confirm_events` UNIQUE). 새 작업(후속 턴·재정리)은 세션 최신 미리보기를 무효로 하고 대체된 결과(재정리면 대기 작업도)를 취소한다. 실행 중 작업은 그대로(같은 세션 직렬화 — `_ASSIGN`). 세션 최신 작업·편집 세대가 맞는 결과만 최신 미리보기로 채택(늦게 온 옛 결과는 저장만). `/api/state` 에 `switches.converse` · `stack_session`(확정 전·만료 전 최신 1개 `{id, expires_at}` | null).
- **정의 스냅샷** `GET /api/device/jobs/<id>/definitions?lease_generation=N`(leased·running): `{definitions_version(sha256 hex), rule_version, objects:[{id,key,origin,label,kind(judgment|event|classify|custom),revision,fields,relations,identity:[code],protected_fields:[code,decision],stage_values,confirm_required}], content_rules(3)}`. 연결 칸은 `relations` 에만. 사람·출처는 연결 대상으로만. 최초 발급 때 `jobs.definitions_version` 에 해시 고정 — 다시 부르면 같을 때 같은 값, 다르면 409 `JOB-DEFS`(수신·확정도 같은 검사). 같은 응답에 **대상 후보** `candidates:{items:[{id,object_id,code,title(80자),stage}], people:[{id,alias}], sources:[{id,title}]}` + `candidates_at` — 그 사용자 것만, 적용·미보관 항목 오브젝트별 최근 갱신순 ≤200·전체 ≤500, 사람·출처 각 ≤200, 본문 없음. 해시 밖(매 조회 최신). 수신 검증은 후보가 아니라 실데이터로.
- **결과 수신**(`/result` 에 `envelope` 가 있으면 stack/1, 없으면 ②-a 꼴): 키 정확 집합 `{lease_generation, envelope{contract,job_id,input_version,definitions_version,result_version,edit_generation,parent_result_version}, units[{uid,text,change_ids}], changes[{cid,op,object_id,target,fields,links,depends_on}], blocked[{cid,code,question}], model_meta}`. 근거 유형(`ref_kind`) 5: `source_id`(scheme code — 서버가 `items.code` 로 해석, 못 하면 unresolved 강등) · `explicit_id`(소유·존재·오브젝트 — 아니면 400) · `inferred`(같은 검사, 없거나 남의 대상이면 unresolved 강등, 확인 카드에 「추정」) · `unresolved`(막힘) · 그 밖(날짜·출처·유사 문장만의 연결, 근거 유형 없음) = 400 `CONTRACT-INVALID "<경로>: <규칙>"`. 정의 밖 관계·연결 칸을 fields 로(`link_via_fields`)·보호·금지 키·URL·이메일·로컬 경로(`imports.LEAK_RULES`)·cid↔unit 정확히 1·중복·순환 = 400. 상한 units 40·changes 60·글 4,000자(`"\n\n"` 결합)·질문 500자·본문 64KiB. 봉투 세대·원 결과 불일치 409 `JOB-VERSION`. 서버가 막힘을 다시 계산(대상 미해결·필수 칸) — 남으면 확정 잠금. 대상 버전 = 결과 생성 때 읽은 `items.revision`(+ 사람 `revision`)을 미리보기에 묶는다(클라이언트 제출값 아님).
- **임시 보관**(운영 백업 `pg_dump -n public` 밖): `tmp.job_inputs`(+ `meta` = 재정리 구조화 입력: 원 결과 단위·제외·답) · `tmp.job_results`(+ `units`) · `tmp.stack_previews`(제외 반영마다 새 행 — 버전·해시) · `tmp.stack_verbatim`(원문 던지기 원문, 세션 단위 — 작업이 대체돼도 확정까지 남는다). 시험 스키마에선 같은 스키마 안 `tmp_*`. 끝난 작업·세션의 임시 내용은 전역 정리·`purge_once` 에서 지운다. 복원 시험(2026-10-22, `crm_test_stack` → 빈 DB): 원본 tmp 4표 각 1행 → 복원 DB `tmp` 표 0·세션·작업 행에 본문 0, `confirm_events`·`item_versions`·`item_stage`·`stack_sessions`·`records`·`jobs` 행 수 보존, 미확정 세션 확정 = `CONFIRM-STALE`.
- **웹**: `POST /api/jobs` `{…, input_kind?, restack?{parent_result_version, edit_generation(세션 +1), edited_text(= text), excluded_cids?, answers?[{cid|null,text}]}}`(어긋나면 409 `CONFIRM-STALE`) · `GET /api/jobs/<id>` 의 `preview`(있을 때만: units·text·changes 표시 요약·blocked·`blocked_open`·excluded·`latest`) · `POST /api/stack/previews/<id>/exclude {cids}`(의존 연쇄·같은 단위 함께 → 새 버전, 남는 글 0 = 409 `PREVIEW-NEEDS-RESTACK`, 최신 아님 409 STALE) · `GET /api/stack/sessions/<id>`(새로 고침 복원).
- **뺀 변경 이름**(③-0.2): 미리보기 표시 응답(`db._preview_display` — 결과·제외·세션 조회 공통)에 `excluded_changes:[{cid, summary}]`(`excluded` 순서). summary = 같은 작업 미리보기 v1 행의 표시 요약을 다시 계산(새 저장 없음, 본인 행만). 재정리 결과는 PC 가 `restack.excluded_cids` 를 반영해 다시 쌓으므로 v1 부터 `excluded = []`·`excluded_changes = []`.
- **확정** `POST /api/jobs/<id>/confirm {request_id, preview_id, preview_version, preview_sha}`(스위치 → 본문 → 쓰기 관문 → `stack_service.confirm`, 한 트랜잭션 10단계 — 어디서 실패해도 한도·확인 이벤트까지 롤백): ① 같은 요청 ID = 저장한 성공 응답 그대로(다른 내용 409 `CONFIRM-REQUEST-MISMATCH`) ② 세션·작업 잠금(최신·이미 확정 409 STALE·만료 410) ③ 미리보기 해시 재계산·버전(409 `CONFIRM-CONFLICT`)·편집 세대·정의 해시(`JOB-DEFS`)·막힘(CONFLICT) ④ 대상·관계 대상 기준 버전·보관(CONFLICT, 부활 0) ⑤ 정의 재검사 ⑥ 기록 하루 한도 — 정리 글 1(원문 던지기 2) 원자 확인, 넘으면 429 `IMPORT-QUOTA` + `resume_at` · 작업 종류별 한도·제안·결정 한도는 세지 않는다 ⑦ 확인 이벤트 + 생성·고침(이력 `apply`) · 판단(principle)은 `confirm` + 새 판(`item_versions`, 이전 판 ID·판 번호, `items.current_version_id` 교체 — 확정 판은 고치지 않는다) ⑧ 원문 record(self·verbatim·`human`) + 정리 글 record(self·structured·`conversation`, `generation_meta` = job·session·result·confirm_event·companion) · 근거 = 정리 글 → 기준·결정 `attach_to`·축 `axes`(축 띠 = 항목 + 글) · stage ⑨ 세션·작업 확정, 같은 세션 다른 작업 취소, 임시 삭제 ⑩ 커밋. 200 `{confirm_event_id, created:{records,items}, updated:{items}, versions}`.
- **인용 적격**(`db._ELIGIBLE_SQL`): self·삭제 안 됨 + (verbatim 이고 generation_kind 가 ai·routine 아님 · 또는 `conversation` 이고 `generation_meta.confirm_event_id` 가 실재하는 확인 이벤트). 루틴·임시·reading 비적격, unknown·mixed 기존 그대로. `conversation`·`routine` 기록은 출처 정정 잠금(`PROVENANCE-AI-LOCKED`). 두 값은 확정 트랜잭션만 쓴다(inbox 생성 방법 고정·MCP `record_add` 에 인자 없음).
- **stage**(`item_stage`, 서버 계산 관리값 — 직접 수정 API 없음, 판단 확정 기준은 `items.decision`): 규칙 `rule_version=1` = 판단 종류 + decision confirmed → confirmed · 필수 칸(연결 포함) 충족 → organized · 아니면 draft. 확정 트랜잭션·`item_decide` 모든 동작 뒤 다시 계산, `definition_apply` 는 그 오브젝트 stage 를 지운다. 읽기(`item_get` 의 `stage`)에서 행이 없거나 낡으면(items.revision·규칙 버전) 지금 계산(lazy, 백필 없음).
- **되돌림**: 스위치(`CRM_CONVERSE_ENABLED`)를 끄면 접수·할당·수신·확정만 멈춘다 — 확정 글·세션 조회·취소·만료 정리는 그대로. 확정 데이터를 지워서 되돌리지 않는다. 새 generation_kind 를 모르는 옛 코드로 바로 되돌리지 않는다.
- **시험**: `test_tools.py` 28절(B1 스키마·한도·세션 · B2 스냅샷·재검증·미리보기·제외·재정리·도착 역전 · B3 확정 10단계·오류 주입 롤백·동시 1·멱등·2건 예약·판 이력·인용·stage · B6 만료 경계·되돌림·우회 저장). 모의 실행만 — 실 `claude -p` 실측은 약관 확인·원작자 확인 뒤 별도.

## 운영 — 장치 상태 (③-0.2)

`/api/state` 의 `devices_status` = `{state: none|recent|offline, last_seen_at: ISO|null, threshold_seconds}` — 본인 장치만(장치 id·이름 없음), /app 전용(MCP `state_get`·`db.state` 에 없음). 활동 부분(`devices` 등)과 독립이라 그쪽이 실패해도 늘 있다. 등록 ≠ 최근 연결.

- **3값**: `none` = 연결 해제(`revoked_at`) 안 된 장치 0(시각도 null) · `recent` = 그 장치들 중 가장 최근 수신 시각이 기준 안(또는 유효 임대, 아래) · `offline` = 장치는 있으나 기준 밖, 또는 아직 한 번도 수신 없음. 토큰 만료 장치는 해제 전까지 `offline`(폴이 401 이라 시각이 안 움직인다 — 사용자를 「연결 장치」로 보내는 신호). 「최근 연결」은 CLI 로그인·구독 잔여량을 보장하지 않는다(화면 문구도 「실행 가능」 아님).
- **마지막 연결 시각** = 서버 수신 시각 `import_streams.last_used_at`(`now()`, 새 열 없음). 쓰는 곳 = 인증 통과한 장치 작업 요청(`web._device`: 롱폴 `GET /api/device/jobs` · `…/input`·`/definitions`·`/result`·`/fail` — 그 뒤 429·409·410 이어도 남는다, 요청당 별도 짧은 트랜잭션 1. 직전 값이 `DEVICE_SEEN_GAP_SECONDS`(60초) 안이면 쓰지 않는다 — 폴 30초마다 운영 DB 쓰기 0, 기준 180초에 60초 해상도) · 가져오기 수신(`imports`, 기존). PC 가 보낸 시각·작업 성공 시각은 쓰지 않는다. 인증 실패(틀린 토큰·폐기·만료·차단)는 쓰지 않는다. 같은 열이 연결 장치 화면의 「마지막 사용」·처리방침(`guides/11-privacy.md` 연결 장치 행)의 「마지막 사용 시각」이라 그 값도 이제 폴로 움직인다(최대 60초 늦음). 용어 정리는 활성화 게이트에서.
- **유효 임대 = 최근 연결**: 서버엔 임대 갱신 경로가 없고(`JOB_LEASE_SECONDS` 300 은 할당 때 1번 고정) PC 는 CLI 실행 중(최대 300초) 폴하지 않는다. 그래서 연결 해제 안 된 장치가 만료 전 임대(`running`, `lease_expires_at > now()`)를 가지면 수신 시각이 기준 밖이어도 `recent` — 긴 작업 중 화면이 「오프라인」으로 바뀌어 취소를 부르지 않게. `last_seen_at` 은 그대로 마지막 수신 시각(임대로 꾸미지 않는다). 임대가 지나면 `offline`.
- **기준** `db.DEVICE_RECENT_SECONDS` = max(180, `DEVICE_POLL_MAX_SECONDS`×3) = 180초. 정상 최대 폴 주기 = 롱폴 상한 `imports.POLL_WAIT_MAX`(30) = PC `agent/jobs.py` `WAIT_SEC`(시작 간격 하한 `MIN_POLL_INTERVAL` 15 는 그보다 짧다). 29절이 세 값을 묶는다.
- **시험**: `test_tools.py` 29절(3값·기준 ±2초 경계·쓰기 간격 60초(안 = 행 xmin 불변)·등록≠최근·서버 시각·작업 요청·여러 장치 최댓값·유효 임대(만료 뒤 offline·남 임대 영향 0)·인증 실패 0·남 장치 0·기준 상수 묶음·/app 전용·출력에 토큰 비밀값 0).
- **되돌림**: 필드는 추가만 — 남겨도 옛 화면이 동작한다. 장치 요청의 시각 기록을 빼려면 `web._device` 의 `db.device_seen` 한 줄.

## 웹 (`web.py` · `app.html`)
- 로그인은 MCP 와 같은 AuthKit. `/login` 이 PKCE(S256)·`resource=<주소>/mcp` 로 authorize 에 보내고, 콜백에서 받은 액세스 토큰을
  **MCP 와 같은 `AuthKitVerifier`·`user_id_for`** 로 검증·연결한다 → 웹 사용자 = MCP 사용자(같은 sub).
- 서버 메모리에 상태 없음. PKCE verifier·state 는 10분 서명 쿠키, 세션은 7일 서명 쿠키(HMAC-SHA256, HttpOnly, SameSite=Lax, https 면 Secure).
- 쓰기 API 는 `POST /api/candidate/decide`(confirm·exclude·pending·edit·edit_confirm, `expected_revision` 선택 — 틀리면 CONFLICT, 웹 편집 문장은 wording_by=self)와 `POST /api/item/decide`(항목: apply·discard·confirm·exclude·pending·archive·restore, `expected_revision` 필수, 제안 제외는 discard+proposal_id). 표준 편입 `GET /api/std/preview`(미리보기, 읽기 전용) · `POST /api/std/choose`(`{std_key, state: deferred|refused|incorporated, confirmed_meaning, expected_revision}`). 모두 세션 + `Origin` 이 공개 주소와 같을 때만. 「고쳐서 남기기」는 edit→confirm 두 번(이력 두 줄). 읽기 `GET /api/item?id=` 는 항목·관계 대상·들어오는 관계·이력·승격 상태(서랍).
  PR B 코어 쓰기(세션 + Origin + 페이로드 상한 + `write_gate`): `POST /api/me/name` · `/api/nav/accept` · `/api/people`·`/api/people/{id}`·`/api/people/{id}/delete` · `/api/sources` · `/api/reflections`·`/api/reflections/{id}`·`/api/reflections/{id}/review`·`/api/reflections/{id}/delete`. 남의·없는 id = 404 `NOT-FOUND`, `NAME-REQUIRED` = 403.
  board 이전 PR(세션 + Origin, 쓰기는 `write_gate`): `GET·POST /api/devices` · `POST /api/devices/{id}/reissue`·`/revoke`·`/policy`(policy JSON 4KB 상한) · `GET /api/inbox`·`/api/inbox/{id}` · `POST /api/inbox/{id}/approve`·`/exclude` · 읽기 `GET /api/me/aggregate?period=` · `/api/activities`·`/api/activities/{id}` · `/api/people/{id}/context` · `/api/sources/{id}/context` · `/api/axes/{item_id}/context`. 장치 토큰 원문은 연결·재발급 응답에서만(no-store).
  PR ①-b(2026-10-22): 페이지 레이아웃 `GET /api/me/layout` → `{layout, version}`(행 없으면 기본 + 0) · `PUT /api/me/layout` `{layout, version}`(세션 + Origin + `write_gate`, layout JSON 4KB) → version +1, 버전 불일치 409 `LAYOUT-STALE`, 모양 밖(탭 7 순열·알려진 블록·중복 없음 외) 400 `LAYOUT-INVALID` · `DELETE /api/me/layout` = 기본으로 되돌리기(행을 지우지 않고 layout NULL + version +1 — 옛 탭의 PUT 은 STALE). 저장 = `page_layouts`(사용자당 1행, 계정 삭제 대상), 기본값 = `db.DEFAULT_LAYOUT` 한 곳, 관리자 API 없음. 읽기 `GET /api/me/stats`(`db.me_stats`, 전체 범위) = 축 띠(축별 직접 연결 고유 항목 + 기록 + 읽기 자료, 활동 제외) · 히트맵(월요일 시작 13주 91칸, 기록 `COALESCE(source_date, kst(source_created_at))` · 활동 `occurred_on`, 날짜 없는 기록 제외) · 종류별 · 기다리는 결정. 기록 목록(`/api/state` journal · `/api/records`) 정렬 = `db.RECORD_SORT`(`COALESCE(source_date, kst(source_created_at), kst(created))`) DESC → created → id, 커서 꼴 `created|id` 는 그대로(정렬 날짜는 서버가 id 행에서 복원). journal 행에 `source_date`·`ingest_kind`·`generation_kind`.
  기록·질문·후보 추가는 웹에 없다 — 연결한 AI 대화에서 한다.
- `app.html` 은 원작자가 쓰던 로컬 보드 화면(체크포인트 보드·360·지도) 구조를 옮긴 보드형 화면(2026-10-18): 마스트(날짜·상태 스펙트럼·질문 레전드) + 기본 탭 오늘/질문/기록/구조/지도(사용자 정의 tabs 있으면 그 탭 + 구조·지도 고정) + 대조 2열(≥1000px). 토큰은 보드 값. 가안·스펙은 레포 `notes/`(gitignore).
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
  board 이전 읽기 7: activity_list · activity_get · person_context_get · source_context_get · me_aggregate_get · inbox_list · inbox_get(위 「연결 장치」 절).
  도구는 모두 43개(19 + 3 + 14 + 7). 기존 19개의 이름·인자는 그대로다(설명 문구만 일부 갱신 · `definitions_get` 은 초안에 `draft=true` 키 추가) — 쓰기 도구에 `NAME-REQUIRED` 가 늘었다.
  **커넥터 갱신**: 도구가 늘면 ChatGPT 커넥터는 스키마를 다시 승인해야 하고, Claude 커넥터는 다시 연결하거나 새 대화를 열어야 새 도구가 보인다. PR B 배포 뒤 운영 확인 항목.
  웹 `/api/state` 는 같은 `db.state` 에 /app 전용 키를 얹는다 — `user`(email·이름 공유·desk_hidden, PR B `display_name`·`nav`) · `std`·`sources`·`source_counts`·`structure`·`structure_svg`(PR #20) · `me`·`people`·`reflections`(PR B) · `activity`(「나」 30일 집계)·`inbox`(`pending` 수)·`devices`(활성 수·최대)·`activity_ui`(행동·제공자 표시 이름, 대기 보관 일수)(board 이전 PR — 이 부분만 실패하면 `read.activity` 에 코드만 싣고 나머지는 그대로) · `devices_status`(③-0.2, 위 「장치 상태」 절). MCP `state_get` 에는 넣지 않는다(2단계 키: definitions · items · item_proposals · record_links, journal 행에 notes). 이름·인자는 고정(ChatGPT 는 승인된 스키마를 고정하므로 바꾸면 재승인).
  `candidates_propose` 의 `question_id=""` = 질문 없음(NULL). 화면은 기록별 묶음, 대조 경로 `#today/review/r:<기록ID>/<후보ID>`.
- 근거 인용은 **인용 적격 기록**(author_kind=self·text_process=verbatim·삭제 안 됨)의 원문에 글자 그대로 **한 곳**만 있어야 저장(0곳·2곳 이상·비적격 기록 거절). 기존 기록은 작성 판정 unknown(비적격). 확정(confirm·edit_confirm)도 근거 기록을 다시 검사한다 — 비적격이면 `CANDIDATE-REVIEW`(사유 author_unknown·external·processed·deleted), 미확정 후보에 `review` 사유. `record_add` 의 author_kind=external 은 `SCOPE-SELF-ONLY`(서버는 작성자 표시만 검사, 내용은 확인하지 않는다). 기록 1편당 후보 3개. 후보는 변경마다 revision+1, 이력에 변경 전 스냅샷(`prev_state`·`prev_revision`). 결정 이력(candidate_history)으로 되돌릴 수 있고, 확정 문장을 고치면 pending 으로 돌아간다.
