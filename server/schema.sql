-- My CRM — Postgres 스키마 (2026-10-18 확정, 같은 날 Codex 토론 반영). 설계 근거: data-model.md
-- 행 격리: 모든 조회·쓰기는 user_id 로 묶는다 (OWD Private). 자식은 (user_id, 부모id) 복합 FK 로 다른 사용자 행을 참조 못 한다.

CREATE TABLE IF NOT EXISTS users (
  id            text PRIMARY KEY,                 -- u_...
  email         text NOT NULL UNIQUE,             -- 소문자 정규화. 매직링크·Google 같은 이메일이면 같은 행
  auth_subject  text UNIQUE,                      -- AuthKit sub (External ID)
  created       timestamptz NOT NULL DEFAULT now(),
  -- β2(2026-10-19): 관리자 = role. 옛 DB 는 db._init_schema 가 ADD COLUMN IF NOT EXISTS 로 더한다(같은 모양).
  role               text NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  blocked_at         timestamptz,                 -- 차단(웹·MCP 둘 다 거절). 해제 = NULL
  web_revoked_before timestamptz,                 -- 이 시각 전에 발급한 웹 세션 쿠키(iat) 무효
  mcp_revoked_before timestamptz,                 -- 이 시각 전에 발급한 MCP 토큰(iat) 무효
  desk_hidden        boolean NOT NULL DEFAULT false, -- P5: /app 문의 섹션 숨김(계정 메뉴). 옛 DB 는 db._init_schema 가 더한다
  -- PR B(2026-10-20): 「나」 = 이 행. 옛 DB 는 db._init_schema 가 같은 모양(열 → 이름 붙은 CHECK)으로 더한다.
  display_name       text,                        -- 표시 이름(필수는 쓰기 관문 NAME-REQUIRED 가 보장 — 옛 코드 INSERT 때문에 NOT NULL 아님)
  status             text NOT NULL DEFAULT 'active',   -- deleted = 삭제 CLI 실행됨(행은 남긴다). 차단(blocked_at)과 다른 말이다
  deleted_at         timestamptz,
  nav_accepted_at    timestamptz,                 -- 새 탐색 수락 시각. NULL = 기존 화면
  CONSTRAINT ck_users_display_name CHECK (display_name IS NULL OR (char_length(display_name) BETWEEN 1 AND 80 AND display_name = btrim(display_name))),
  CONSTRAINT ck_users_status CHECK (status IN ('active','deleted')),
  CONSTRAINT ck_users_deleted CHECK ((status = 'deleted') = (deleted_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS agreements (
  id          bigserial PRIMARY KEY,
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('terms','privacy')),
  version     text NOT NULL,
  accepted_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, kind, version)
);

CREATE TABLE IF NOT EXISTS questions (
  id       text PRIMARY KEY,                      -- q_...
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text     text NOT NULL,
  status   text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),  -- 삭제 없음, archive 만
  created  timestamptz NOT NULL DEFAULT now(),
  updated  timestamptz,
  UNIQUE (user_id, id)                            -- 복합 FK 대상
);

-- 출처(PR #20 §4, 2026-10-19). 사용자별. 같은 (kind, name, location) 은 한 행 — record_add 가 찾거나 만든다(SOURCES=on 일 때만).
-- 옛 records.source 문자열과 별개다(그 문자열은 보존·표시만, 자동 연결 없음 — §12). records 보다 먼저 만든다(records 의 FK 대상).
CREATE TABLE IF NOT EXISTS sources (
  user_id   text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id        text NOT NULL,                        -- s_...
  kind      text NOT NULL,
  name      text NOT NULL,
  location  text NOT NULL DEFAULT '',             -- 없으면 '' — UNIQUE 가 NULL 을 서로 다르게 보지 않게
  created   timestamptz NOT NULL DEFAULT now(),
  -- board 이전(2026-10-20): 연결 장치 활동의 일반 출처. NULL = 보통 출처. 값이 있으면 서버가 만든 일반 이름·kind other·위치 '' 뿐
  -- (게시물 제목·방 이름·메일 제목·작성자명을 넣지 않는다). 옛 DB 는 db._init_schema 가 열 → 이름 붙은 CHECK·부분 UNIQUE 를 더한다
  activity_provider text,
  PRIMARY KEY (user_id, id),
  UNIQUE (user_id, kind, name, location),
  CONSTRAINT ck_sources_kind CHECK (kind IN ('file','url','chat','other')),
  CONSTRAINT ck_sources_activity_provider CHECK (activity_provider IS NULL OR (activity_provider IN ('sns','web','mail','kakao')
                                                 AND kind = 'other' AND location = ''))
);

CREATE TABLE IF NOT EXISTS records (
  id          text PRIMARY KEY,                   -- r_...
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id text,
  title       text NOT NULL,
  body        text NOT NULL,                      -- 수신한 그대로. 공백 하나도 안 다듬는다
  source      text NOT NULL DEFAULT '대화에서 입력',
  created     timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,                        -- soft delete. 하드 삭제는 계정 삭제(users cascade) 때만
  notes       jsonb NOT NULL DEFAULT '{}',        -- 2단계: 「어디에 붙나」 중 관계가 아닌 메모 {use_at, unattached}
  -- PR①: 출처. 인용 적격 = self·verbatim·삭제 안 됨. 확인 못 한 출처는 unknown(추측 백필 금지). 본문은 불변, 출처 판정만 정정 가능
  author_kind        text  NOT NULL DEFAULT 'unknown',
  text_process       text  NOT NULL DEFAULT 'unknown',
  provenance_history jsonb NOT NULL DEFAULT '[]',  -- 생성·정정마다 {at, by, author_kind, text_process[, from, reason]}
  source_id          text,                        -- PR #20 출처 연결. NULL = 출처 미확인(옛 기록 전부). 옛 DB 는 init_schema 가 더한다
  -- board 이전(2026-10-20). 옛 DB 는 init_schema 가 열을 더한다 — 옛 행 received_at NULL·ingest_kind legacy, 새 INSERT 부터 now()·interactive.
  -- 일일 기록 한도는 COALESCE(received_at, created) 로 센다(이전한 기록의 created 가 과거여도 우회 못 한다)
  received_at        timestamptz DEFAULT now(),   -- 서버에 실제로 생긴 시각
  source_created_at  timestamptz,                 -- 확인된 원본 시각(시간대 포함)
  source_date        date,                        -- 날짜만 확인된 원본. 자정·KST 를 붙이지 않는다
  ingest_kind        text NOT NULL DEFAULT 'interactive',
  generation_kind    text NOT NULL DEFAULT 'unknown',
  generation_meta    jsonb NOT NULL DEFAULT '{}', -- 연결 장치가 신고한 도구·모델·입력 참조(검증된 사실 아님). 토큰·프롬프트·경로 없음
  import_batch_id    text,                        -- 처음 만든 이전 배치(FK 아님 — 대응은 import_entries)
  UNIQUE (user_id, id),
  CONSTRAINT fk_records_source FOREIGN KEY (user_id, source_id) REFERENCES sources(user_id, id),   -- 같은 사용자 출처만
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE SET NULL (question_id),  -- Lookup
  CONSTRAINT ck_records_author_kind CHECK (author_kind IN ('self','external','unknown')),
  CONSTRAINT ck_records_text_process CHECK (text_process IN ('verbatim','structured','rewritten','generated','unknown')),
  CONSTRAINT ck_records_provenance_history CHECK (jsonb_typeof(provenance_history) = 'array'),
  CONSTRAINT ck_records_ingest_kind CHECK (ingest_kind IN ('legacy','interactive','inbox','board_migration')),
  -- PR ②-b: conversation = 확인 1번을 거친 대화 정리(인용 적격은 confirm_events 실재까지 — db._eligible) · routine = 루틴 결과(비적격)
  CONSTRAINT ck_records_generation_kind CHECK (generation_kind IN ('unknown','human','ai','mixed','conversation','routine')),
  CONSTRAINT ck_records_generation_meta CHECK (jsonb_typeof(generation_meta) = 'object'),
  CONSTRAINT ck_records_source_when CHECK (source_created_at IS NULL OR source_date IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_records_user ON records (user_id, created DESC) WHERE deleted_at IS NULL;

-- PR B 코어 「사람」(2026-10-20). 사용자 소유 별칭 이름표 하나뿐 — 다른 개인정보 열을 만들지 않는다. 기록·요약/반응과 관계 없음.
CREATE TABLE IF NOT EXISTS people (
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id       text NOT NULL,                         -- p_...
  alias    text NOT NULL CHECK (char_length(alias) BETWEEN 1 AND 80 AND alias = btrim(alias)),
  revision integer NOT NULL DEFAULT 0,
  created  timestamptz NOT NULL DEFAULT now(),
  updated  timestamptz NOT NULL DEFAULT now(),
  -- board 이전(2026-10-20): 연결 장치가 보낸 사람 식별자. HMAC 키·원본 계정 대응표는 PC 에만. 값이 있으면 둘 다 있다.
  -- (user_id, hmac_namespace, identity_hmac) 부분 UNIQUE 는 init_schema 가 만든다(옛 DB 에선 이 파일이 열보다 먼저 돈다)
  hmac_namespace text,
  identity_hmac  bytea,
  PRIMARY KEY (user_id, id),                      -- 별칭 중복 허용
  CONSTRAINT ck_people_identity CHECK ((hmac_namespace IS NULL AND identity_hmac IS NULL)
                                       OR (hmac_namespace IS NOT NULL AND identity_hmac IS NOT NULL AND octet_length(identity_hmac) = 32))
);

-- PR B 코어 「출처 요약/반응」. 출처를 접한 뒤 내가 한 생각(자기 성찰). records 와 분리 — 인용 적격 경로(후보 근거·관계 대상)에 들어가지 않는다.
-- 검토 완료(reviewed) ≠ 판단 확정. 출처 FK 는 NO ACTION — 요약/반응이 달린 출처는 지울 수 없다.
CREATE TABLE IF NOT EXISTS source_reflections (
  user_id           text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                text NOT NULL,                -- sr_...
  source_id         text NOT NULL,
  kind              text NOT NULL CHECK (kind IN ('summary','reaction')),
  body              text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 8000),
  review            text NOT NULL DEFAULT 'pending' CHECK (review IN ('pending','reviewed')),
  revision          integer NOT NULL DEFAULT 0,
  reviewed_revision integer,
  reviewed_at       timestamptz,
  created           timestamptz NOT NULL DEFAULT now(),
  updated           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id),
  FOREIGN KEY (user_id, source_id) REFERENCES sources(user_id, id),
  CHECK ((review = 'reviewed') = (reviewed_at IS NOT NULL AND reviewed_revision IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_reflections_source ON source_reflections (user_id, source_id);

CREATE TABLE IF NOT EXISTS candidates (
  id             text PRIMARY KEY,                -- c_...
  user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  record_id      text NOT NULL,
  question_id    text,                            -- 2단계: NULL = 질문 없음(화면은 기록별 묶음). 복합 FK 는 NULL 이면 검사 안 함
  text           text NOT NULL,
  quote          text NOT NULL,                   -- record.body[quote_start:quote_end] 와 같다. 서버가 위치를 센다
  quote_start    integer NOT NULL,                -- 코드포인트 오프셋
  quote_end      integer NOT NULL,
  origin         text NOT NULL CHECK (origin IN ('ai','self')),
  decision       text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','confirmed','excluded')),
  edited_by_user boolean NOT NULL DEFAULT false,
  created        timestamptz NOT NULL DEFAULT now(),
  updated        timestamptz NOT NULL DEFAULT now(),
  -- PR①: 행 revision(변경마다 +1, 이력 한 줄 = +1) · 현재 문장 작성 주체(미상 NULL) · 현재 문장 승인
  revision         integer NOT NULL DEFAULT 1,
  wording_by       text,
  wording_approved boolean NOT NULL DEFAULT false,
  UNIQUE (user_id, id),
  UNIQUE (user_id, record_id, quote_start, quote_end),   -- 같은 근거 구간 재등록 방지
  FOREIGN KEY (user_id, record_id)   REFERENCES records(user_id, id)   ON DELETE CASCADE,   -- M-D: 근거 없는 판단 없음
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE RESTRICT,
  CONSTRAINT ck_candidates_revision CHECK (revision > 0),
  CONSTRAINT ck_candidates_wording_by CHECK (wording_by IN ('self','ai')),
  CONSTRAINT ck_candidates_wording_approval CHECK (NOT wording_approved OR wording_by IS NOT NULL),
  CONSTRAINT ck_candidates_quote_bounds CHECK (quote_start >= 0 AND quote_end > quote_start
                                               AND char_length(quote) = quote_end - quote_start)
);
CREATE INDEX IF NOT EXISTS ix_candidates_user_q ON candidates (user_id, question_id, decision);

CREATE TABLE IF NOT EXISTS candidate_history (
  id            bigserial PRIMARY KEY,
  user_id       text NOT NULL,                    -- PR①: 소유자. 복합 FK 로 다른 사용자 후보를 못 가리킨다
  candidate_id  text NOT NULL,
  action        text NOT NULL,
  prev_revision integer,                          -- 변경 전 후보 revision. 옛 이력(PR① 전)만 NULL
  prev_state    jsonb NOT NULL,                   -- 변경 전 스냅샷. v1(옛 이력) {schema_version,text,decision} ·
                                                  -- v2 {schema_version,text,decision,wording_by,wording_approved,edited_by_user,question_id}
  at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT candidate_history_action_check CHECK (action IN ('confirm','exclude','pending','edit','restore')),
  CONSTRAINT ck_candidate_history_prev_revision CHECK (prev_revision IS NULL OR prev_revision > 0),
  CONSTRAINT ck_candidate_history_prev_state CHECK (jsonb_typeof(prev_state) = 'object' AND prev_state ? 'schema_version'
                                                    AND prev_state ? 'text' AND prev_state ? 'decision'),
  CONSTRAINT uq_candidate_history_revision UNIQUE (user_id, candidate_id, prev_revision),   -- 이력 한 줄 = revision +1
  CONSTRAINT fk_candidate_history_tenant FOREIGN KEY (user_id, candidate_id)
    REFERENCES candidates(user_id, id) ON UPDATE RESTRICT ON DELETE CASCADE                  -- M-D
);
CREATE INDEX IF NOT EXISTS ix_hist_cand ON candidate_history (user_id, candidate_id, id);

CREATE TABLE IF NOT EXISTS profile (
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key      text NOT NULL CHECK (key IN ('purpose','refind','daily_views','first_material','separate','help_level')),
  value    jsonb NOT NULL,
  status   text NOT NULL CHECK (status IN ('confirmed','guessed','empty','off')),
  source   text NOT NULL DEFAULT '',
  updated  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS tabs (
  user_id   text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  doc       jsonb NOT NULL,                       -- 적용된 탭
  draft_doc jsonb,                                -- tabs_propose 결과. NULL 이면 제안 없음
  draft_id  text,                                 -- tabs_apply 가 이 ID 와 revision 이 맞을 때만 적용
  revision  integer NOT NULL DEFAULT 0,
  updated   timestamptz NOT NULL DEFAULT now()
);

-- 2단계 기존 테이블 변경(records.notes · candidates.question_id NULL 허용)과 PR① 코어 보수(출처·revision·이력 격리)는
-- 위 CREATE 에 반영했다(새 설치 = 최종형). 이미 있는 DB 에는 db.init_schema 가 상태를 보고 멱등 마이그레이션을 건다.

-- ── 2단계: 사용자 정의 층 (설계: data-model.md 「사용자 정의 층」) ─────────────────
-- 사용자는 오브젝트·필드·Lookup 까지만 정의한다. 증거 규칙(원문 불변·인용 1곳·후보≠판단)은 코어가 지킨다.
-- 보호 Lookup(대상 삭제 보호)은 NO ACTION — 계정 삭제 cascade 가 한 문장 안에서 끝나야 통과한다(RESTRICT 는 즉시 검사).

CREATE TABLE IF NOT EXISTS object_defs (
  user_id             text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                  text NOT NULL,                -- od_...
  key                 text NOT NULL,                -- 적용 뒤 불변
  label               text NOT NULL,                -- 화면 라벨(한글 한 단어)
  semantic_kind       text NOT NULL CHECK (semantic_kind IN ('collection','event','principle')),
  revision            integer NOT NULL DEFAULT 0 CHECK (revision >= 0),   -- 0 = 아직 적용 안 된 제안 행
  policy              jsonb NOT NULL DEFAULT '{}',  -- promotion · exactly_one_reference
  history             jsonb NOT NULL DEFAULT '[]',  -- 적용 revision 마다 전체 정의 스냅샷
  draft_doc           jsonb,
  draft_id            text,
  draft_base_revision integer,
  archived_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  template_slot       text,                         -- 템플릿 계보 `author@1:<key>`. 시드만 채운다(apply 는 안 건드림). 옛 DB 는 init_schema ADD COLUMN
  std_id              text,                         -- PR #20 표준 식별자 `std:<key>`. NULL = 표준 아님. 시드·편입만 채운다. 옛 DB 는 init_schema ADD COLUMN
  PRIMARY KEY (user_id, id),
  UNIQUE (user_id, key)
);

CREATE TABLE IF NOT EXISTS field_defs (
  user_id          text NOT NULL,
  object_id        text NOT NULL,
  key              text NOT NULL,
  label            text NOT NULL,
  value_type       text NOT NULL CHECK (value_type IN ('text','number','boolean','date','select','text_list','reference')),
  required         boolean NOT NULL DEFAULT false,
  options          jsonb NOT NULL DEFAULT '[]',     -- select: [{value,label}]
  target_kind      text CHECK (target_kind IN ('item','record','candidate','person','source','activity')),   -- 뒤 셋 = board 이전. 옛 DB 는 init_schema 가 넓힌다
  target_object_id text,
  multiple         boolean NOT NULL DEFAULT false,
  position         integer NOT NULL DEFAULT 0,
  archived_at      timestamptz,
  template_slot    text,                            -- 템플릿 계보 `author@1:<object_key>.<field_key>`. 시드만 채운다
  std_id           text,                            -- PR #20 표준 식별자 `std:<object_key>.<field_key>`. NULL = 표준 아님(사용자가 더한 필드 포함)
  PRIMARY KEY (user_id, object_id, key),
  FOREIGN KEY (user_id, object_id) REFERENCES object_defs(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, target_object_id) REFERENCES object_defs(user_id, id),          -- 보호 Lookup
  CHECK ((value_type = 'reference') = (target_kind IS NOT NULL)),
  CHECK (target_object_id IS NULL OR target_kind = 'item')
);

CREATE TABLE IF NOT EXISTS items (
  user_id             text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                  text NOT NULL,                -- i_...
  object_id           text NOT NULL,
  schema_revision     integer NOT NULL CHECK (schema_revision > 0),
  code                text,                         -- 적용 뒤 변경·재사용 금지(서버). archive 해도 UNIQUE 유지
  title               text NOT NULL DEFAULT '',
  body                text NOT NULL DEFAULT '',
  data                jsonb NOT NULL DEFAULT '{}',  -- 일반 필드만. 관계 ID 는 item_links
  decision            text CHECK (decision IN ('pending','confirmed','excluded')),   -- principle 만 NOT NULL(서버)
  revision            integer NOT NULL DEFAULT 0 CHECK (revision >= 0),             -- 0 = 아직 적용 안 된 제안 행
  draft_doc           jsonb,
  draft_id            text,
  draft_base_revision integer,
  archived_at         timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  -- board 이전(2026-10-20) — records 와 같은 뜻. 옛 DB 는 init_schema 가 더한다(옛 행 received_at NULL·ingest_kind legacy)
  received_at         timestamptz DEFAULT now(),
  source_created_at   timestamptz,
  source_date         date,
  ingest_kind         text NOT NULL DEFAULT 'interactive',
  generation_kind     text NOT NULL DEFAULT 'unknown',
  generation_meta     jsonb NOT NULL DEFAULT '{}',
  import_batch_id     text,
  -- PR ②-b 판 이력: 지금 판(item_versions.id). NULL = 판 이력 없음(확인 1번으로 확정한 적 없음). FK 없음(서버 검사) — 삭제 순서가 items 보다 판이 먼저.
  -- 옛 DB 는 db._init_schema 가 더한다
  current_version_id  text,
  PRIMARY KEY (user_id, id),
  UNIQUE (user_id, id, object_id),                  -- item_links 복합 FK 대상
  CONSTRAINT ck_items_ingest_kind CHECK (ingest_kind IN ('legacy','interactive','inbox','board_migration')),
  CONSTRAINT ck_items_generation_kind CHECK (generation_kind IN ('unknown','human','ai','mixed','conversation','routine')),
  CONSTRAINT ck_items_generation_meta CHECK (jsonb_typeof(generation_meta) = 'object'),
  CONSTRAINT ck_items_source_when CHECK (source_created_at IS NULL OR source_date IS NULL),
  UNIQUE (user_id, object_id, code),
  FOREIGN KEY (user_id, object_id) REFERENCES object_defs(user_id, id)                 -- 보호 Lookup
);
CREATE INDEX IF NOT EXISTS ix_items_user_obj ON items (user_id, object_id, updated_at DESC) WHERE archived_at IS NULL;

-- ── board 이전 · 연결 장치(2026-10-20, 구현계획 notes/2026-10-20_board-마이그-구현계획.md · r3 T1) ─────────────
-- 원문은 PC, 서버엔 구조화 데이터만. 활동 = 코어(사용자 정의 오브젝트 아님). 계정 삭제는 db.DELETE_TABLES 순서(관계 → 접수 대응표 → 업무 자료 → 입력원).
-- 활동·접수 대응표가 참조하는 테이블보다 먼저 만든다(item_links 가 activities 를 가리킨다).

-- 입력원 = 연결 장치(토큰) 또는 board 이전(병행 기간). 장치당 현재 토큰 하나 — 옛 토큰은 감사(admin_audit)에만.
CREATE TABLE IF NOT EXISTS import_streams (
  user_id               text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                    text NOT NULL,              -- is_<hex24>
  channel               text NOT NULL CHECK (channel IN ('device','board_migration')),
  label                 text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80 AND label = btrim(label)),
  token_id              text,                       -- it_<hex16>. 비밀값은 SHA-256 만
  secret_hash           bytea,
  token_expires_at      timestamptz,
  token_revoked_at      timestamptz,
  last_used_at          timestamptz,
  hmac_namespace        text,                       -- 키가 아닌 무작위 키 공간 ID(hn_<hex24>)
  policy                jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(policy) = 'object'),   -- 허용 행동·변환·축(로컬 경로 없음)
  policy_revision       integer NOT NULL DEFAULT 1 CHECK (policy_revision > 0),
  migration_started_at  timestamptz,
  migration_ends_at     timestamptz,
  migration_closed_at   timestamptz,
  final_manifest_sha256 bytea,
  created_at            timestamptz NOT NULL DEFAULT now(),
  revoked_at            timestamptz,                -- 장치 연결 해제(되살리지 않는다)
  PRIMARY KEY (user_id, id),
  UNIQUE (token_id),
  CHECK (secret_hash IS NULL OR octet_length(secret_hash) = 32),
  CHECK (final_manifest_sha256 IS NULL OR octet_length(final_manifest_sha256) = 32),
  CHECK ((channel = 'device' AND token_id IS NOT NULL AND secret_hash IS NOT NULL AND token_expires_at IS NOT NULL)
      OR (channel = 'board_migration' AND token_id IS NULL AND secret_hash IS NULL AND token_expires_at IS NULL)),
  CHECK ((migration_started_at IS NULL AND migration_ends_at IS NULL)
      OR (migration_started_at IS NOT NULL AND migration_ends_at > migration_started_at)),
  CHECK (channel = 'board_migration' OR (migration_started_at IS NULL AND migration_ends_at IS NULL
                                         AND migration_closed_at IS NULL AND final_manifest_sha256 IS NULL)),
  CHECK (migration_closed_at IS NULL OR migration_started_at IS NOT NULL)
);

-- 활동 = 불변 판. 새 개정은 새 행(옛 판을 가리키는 참조는 그대로). 집계는 import_entries.is_current_applied 가 가리키는 판만.
-- 제목·본문·메모·URL·실명 열이 없다 — 자동 경로의 핵심 방어는 자유 본문 필드 부재.
CREATE TABLE IF NOT EXISTS activities (
  user_id      text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id           text NOT NULL,                       -- a_...
  stream_id    text NOT NULL,
  source_id    text NOT NULL,                       -- 일반 출처(sources.activity_provider). 서버가 찾거나 만든다
  action       text NOT NULL CHECK (action IN ('like','repost','comment','read','save','send','reply')),
  state        text NOT NULL DEFAULT 'active' CHECK (state IN ('active','retracted')),
  occurred_on  date,                                -- 확인된 행동 날짜만(일자). 모르면 NULL — 수집일로 대체하지 않는다
  observed_on  date,                                -- 수집기가 처음 본 날(화면 「처음 수집한 날」). 기간 집계에 쓰지 않는다(구현계획 §2-11 D1)
  raw_id       text NOT NULL CHECK (raw_id ~ '^[0-9a-f]{48}$'),   -- PC 원문을 찾는 난수 ID. 실체·경로·URL 에서 유도하지 않는다
  received_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  PRIMARY KEY (user_id, id),
  FOREIGN KEY (user_id, stream_id) REFERENCES import_streams(user_id, id),
  FOREIGN KEY (user_id, source_id) REFERENCES sources(user_id, id)
);

CREATE TABLE IF NOT EXISTS item_links (
  user_id             text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                  bigserial,
  source_item_id      text,                         -- 출발은 item 또는 record 중 정확히 하나
  source_object_id    text,
  source_record_id    text,
  field_key           text NOT NULL,                -- item 출발: field_defs 키 · record 출발: attach_to | axes (서버 검증)
  target_item_id      text,
  target_record_id    text,
  target_candidate_id text,
  target_person_id    text,                         -- board 이전: reference 대상 person·source·activity(활동은 특정 판). 옛 DB 는 init_schema 가 더한다
  target_source_id    text,
  target_activity_id  text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id),
  CHECK ((source_item_id IS NOT NULL AND source_object_id IS NOT NULL AND source_record_id IS NULL)
      OR (source_item_id IS NULL AND source_object_id IS NULL AND source_record_id IS NOT NULL)),
  CHECK (source_record_id IS NULL OR field_key IN ('attach_to','axes')),
  -- 대상 열 정확히 하나. 옛 DB 의 이름 없는 3열 CHECK(item_links_check2)는 init_schema 가 정의로 찾아 지우고 이 이름으로 다시 만든다
  CONSTRAINT ck_item_links_target CHECK (num_nonnulls(target_item_id, target_record_id, target_candidate_id,
                                                      target_person_id, target_source_id, target_activity_id) = 1),
  FOREIGN KEY (user_id, source_item_id, source_object_id) REFERENCES items(user_id, id, object_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, source_object_id, field_key) REFERENCES field_defs(user_id, object_id, key),   -- 보호
  FOREIGN KEY (user_id, source_record_id) REFERENCES records(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, target_item_id) REFERENCES items(user_id, id),                -- 보호
  FOREIGN KEY (user_id, target_record_id) REFERENCES records(user_id, id),            -- 보호
  FOREIGN KEY (user_id, target_candidate_id) REFERENCES candidates(user_id, id),      -- 보호
  CONSTRAINT fk_item_links_person FOREIGN KEY (user_id, target_person_id) REFERENCES people(user_id, id),          -- 보호
  CONSTRAINT fk_item_links_source FOREIGN KEY (user_id, target_source_id) REFERENCES sources(user_id, id),         -- 보호
  CONSTRAINT fk_item_links_activity FOREIGN KEY (user_id, target_activity_id) REFERENCES activities(user_id, id)   -- 보호
);
CREATE INDEX IF NOT EXISTS ix_links_src_item ON item_links (user_id, source_item_id);
CREATE INDEX IF NOT EXISTS ix_links_target_item ON item_links (user_id, target_item_id);
CREATE INDEX IF NOT EXISTS ix_links_target_cand ON item_links (user_id, target_candidate_id);
CREATE INDEX IF NOT EXISTS ix_links_src_record ON item_links (user_id, source_record_id);

-- 활동·기록의 사람·축·기록 연결(board 이전). 출발 = 활동 또는 기록(기록 출발은 사람만 — 기록의 축·항목은 기존 item_links(record_attach)).
CREATE TABLE IF NOT EXISTS activity_links (
  user_id           text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                text NOT NULL,                  -- al_...
  activity_id       text,
  record_id         text,
  person_id         text,
  axis_item_id      text,
  linked_record_id  text,
  relation          text NOT NULL CHECK (relation IN ('author','counterparty','participant','axis','evidence','reaction')),
  created_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id),
  FOREIGN KEY (user_id, activity_id) REFERENCES activities(user_id, id),
  FOREIGN KEY (user_id, record_id) REFERENCES records(user_id, id),
  FOREIGN KEY (user_id, person_id) REFERENCES people(user_id, id),
  FOREIGN KEY (user_id, axis_item_id) REFERENCES items(user_id, id),
  FOREIGN KEY (user_id, linked_record_id) REFERENCES records(user_id, id),
  CHECK (num_nonnulls(activity_id, record_id) = 1),
  CHECK (num_nonnulls(person_id, axis_item_id, linked_record_id) = 1),
  CHECK ((relation IN ('author','counterparty','participant') AND person_id IS NOT NULL)
      OR (relation = 'axis' AND axis_item_id IS NOT NULL)
      OR (relation IN ('evidence','reaction') AND linked_record_id IS NOT NULL)),
  CHECK (record_id IS NULL OR person_id IS NOT NULL),
  CHECK (activity_id <> '' AND record_id <> '' AND person_id <> '' AND axis_item_id <> '' AND linked_record_id <> '')   -- 빈 문자열 ID 금지(NULL 은 통과)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_activity_links_identity ON activity_links
  (user_id, COALESCE(activity_id, ''), COALESCE(record_id, ''), relation, COALESCE(person_id, ''), COALESCE(axis_item_id, ''),
   COALESCE(linked_record_id, ''));
CREATE INDEX IF NOT EXISTS ix_activity_links_person ON activity_links (user_id, person_id) WHERE person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_activity_links_axis ON activity_links (user_id, axis_item_id) WHERE axis_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_activity_links_linked ON activity_links (user_id, linked_record_id) WHERE linked_record_id IS NOT NULL;

-- 원본 개정 대응표 · 수신 결과 · 검토 대기 통합. 본문(payload)은 pending 에서만 — 승인·제외·만료 때 지운다. 해시·식별자·결과는 계정 존속 기간.
CREATE TABLE IF NOT EXISTS import_entries (
  user_id              text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                   text NOT NULL,               -- ie_...
  stream_id            text NOT NULL,
  original_id          text NOT NULL,               -- PC 가 만든 불투명 ID
  source_revision      text NOT NULL,               -- 불투명 개정 ID
  previous_entry_id    text,                        -- 같은 원본·종류의 직전 접수 행. 최초 = NULL
  kind                 text NOT NULL CHECK (kind IN ('self_text','self_summary','activity',
                                                     'migration_object','migration_item','migration_record')),
  request_sha256       bytea NOT NULL CHECK (octet_length(request_sha256) = 32),
  projection_sha256    bytea NOT NULL CHECK (octet_length(projection_sha256) = 32),
  state                text NOT NULL CHECK (state IN ('pending','applied','noop','excluded','expired')),
  is_latest_received   boolean NOT NULL DEFAULT true,
  is_current_applied   boolean NOT NULL DEFAULT false,
  payload              jsonb,
  payload_bytes        integer NOT NULL DEFAULT 0 CHECK (payload_bytes >= 0),
  expires_at           timestamptz,
  received_at          timestamptz NOT NULL DEFAULT now(),
  resolved_at          timestamptz,
  resolved_by          text CHECK (resolved_by IS NULL OR resolved_by IN ('user','expiry','superseded','system','migration')),
  target_object_id     text,
  target_item_id       text,
  target_record_id     text,
  target_activity_id   text,
  target_item_revision integer CHECK (target_item_revision >= 0),
  batch_id             text,
  manifest_sha256      bytea,
  transform_version    text NOT NULL,
  execution            text CHECK (execution IS NULL OR execution IN ('attended','unattended')),
  PRIMARY KEY (user_id, id),
  UNIQUE (user_id, stream_id, original_id, source_revision),
  UNIQUE (user_id, stream_id, original_id, kind, id),
  FOREIGN KEY (user_id, stream_id) REFERENCES import_streams(user_id, id),
  FOREIGN KEY (user_id, stream_id, original_id, kind, previous_entry_id)
    REFERENCES import_entries (user_id, stream_id, original_id, kind, id),
  FOREIGN KEY (user_id, target_object_id) REFERENCES object_defs(user_id, id),
  FOREIGN KEY (user_id, target_item_id) REFERENCES items(user_id, id),
  FOREIGN KEY (user_id, target_record_id) REFERENCES records(user_id, id),
  FOREIGN KEY (user_id, target_activity_id) REFERENCES activities(user_id, id),
  CHECK (manifest_sha256 IS NULL OR octet_length(manifest_sha256) = 32),
  CHECK (num_nonnulls(target_object_id, target_item_id, target_record_id, target_activity_id) <= 1),
  CHECK ((state = 'pending' AND payload IS NOT NULL AND expires_at IS NOT NULL AND resolved_at IS NULL)
      OR (state <> 'pending' AND payload IS NULL AND payload_bytes = 0 AND resolved_at IS NOT NULL)),
  CHECK (state NOT IN ('applied','noop') OR num_nonnulls(target_object_id, target_item_id, target_record_id, target_activity_id) = 1),
  CHECK (NOT is_current_applied OR state IN ('applied','noop')),
  CHECK (target_item_revision IS NULL OR target_item_id IS NOT NULL),
  CHECK (kind <> 'activity' OR state <> 'pending')
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_import_latest ON import_entries (user_id, stream_id, original_id) WHERE is_latest_received;
CREATE UNIQUE INDEX IF NOT EXISTS ux_import_current ON import_entries (user_id, stream_id, original_id) WHERE is_current_applied;
CREATE INDEX IF NOT EXISTS ix_import_pending ON import_entries (user_id, received_at) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS ix_import_target_activity ON import_entries (user_id, target_activity_id) WHERE target_activity_id IS NOT NULL;

-- 원자적 접수·요청 한도. scope_key = 'user' 또는 장치(입력원) ID, bucket = 서버 고정 열거(imports.BUCKETS). 클라이언트 입력을 그대로 쓰지 않는다.
-- usage_daily 는 관측용이라 강제 한도에 쓰지 않는다.
CREATE TABLE IF NOT EXISTS import_counters (
  user_id       text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope_key     text NOT NULL,
  period        text NOT NULL CHECK (period IN ('minute','day')),
  period_start  timestamptz NOT NULL,
  bucket        text NOT NULL,
  n             bigint NOT NULL DEFAULT 0 CHECK (n >= 0),
  bytes         bigint NOT NULL DEFAULT 0 CHECK (bytes >= 0),
  PRIMARY KEY (user_id, scope_key, period, period_start, bucket)
);

CREATE TABLE IF NOT EXISTS item_history (
  id            bigserial PRIMARY KEY,
  user_id       text NOT NULL,
  item_id       text NOT NULL,
  action        text NOT NULL,                      -- apply · confirm · exclude · pending · archive · restore
  prev_revision integer NOT NULL,
  prev_state    jsonb NOT NULL,                     -- title·body·data·decision·code·schema_revision·links·archived
  event_meta    jsonb NOT NULL DEFAULT '{}',
  at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, item_id, prev_revision),         -- 이력 한 줄 = revision +1
  FOREIGN KEY (user_id, item_id) REFERENCES items(user_id, id) ON DELETE CASCADE
);

-- desk-0 문의 데스크(2026-10-19). 「본문 0」 원칙의 예외: 문의 글은 운영자가 직접 읽는다(폼·MCP 응답에 고지).
-- 종결(done·wontfix) 180일 뒤 파기(db.tickets_purge, 서버 시작 때 1회). 계정 삭제 = users cascade.
CREATE TABLE IF NOT EXISTS tickets (
  id          text PRIMARY KEY,                           -- tk_...
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('bug','request','question','privacy')),
  text        text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 4096),
  via         text NOT NULL CHECK (via IN ('web','mcp')),
  page        text NOT NULL DEFAULT 'other',               -- 열거: today/question/record/structure/map/review/start/help/privacy/other
  tool        text NOT NULL DEFAULT '',                    -- MCP 도구 이름 또는 ''
  ua_family   text NOT NULL DEFAULT '',                    -- 'chrome'/'safari'/'firefox'/'edge'/'other'/'' (버전·OS 없음)
  hint        text NOT NULL DEFAULT '' CHECK (hint IN ('','privacy?')),
  status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open','ack','doing','done','wontfix')),
  admin_note  text NOT NULL DEFAULT '',
  created     timestamptz NOT NULL DEFAULT now(),
  updated     timestamptz NOT NULL DEFAULT now(),
  closed_at   timestamptz,
  reply          text CHECK (octet_length(reply) <= 4096),   -- 운영자 공개 답변(P3). NULL = 답변 없음. 옛 DB 는 init_schema ADD COLUMN
  reply_at       timestamptz,                                -- 답변을 쓰거나 고친 때
  public_updated timestamptz,                                -- 사용자에게 보이는 변경(상태·답변)의 마지막 시각. 내부 메모는 안 바꾼다
  CHECK ((status IN ('done','wontfix')) = (closed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_tickets_user ON tickets(user_id, created DESC);
CREATE INDEX IF NOT EXISTS ix_tickets_open ON tickets(status, created) WHERE status NOT IN ('done','wontfix');

-- 개인정보 요청 처리 사실. CASCADE 밖 — 계정·문의가 지워져도 남는다. 사람 식별 정보 없음. 상태 전이·파기는 desk-1(/admin).
CREATE TABLE IF NOT EXISTS privacy_log (
  id               text PRIMARY KEY,                       -- pl_...
  kind             text NOT NULL CHECK (kind IN ('access','correct_delete','stop','withdraw','other')),
  via              text NOT NULL CHECK (via IN ('form','mail')),
  ticket_id        text,                                   -- FK 없음(티켓 삭제 뒤에도 남는다)
  received         date NOT NULL,                          -- KST 날짜. 기본값 없음 — 넣는 쪽이 정한다
  notified         date,
  result           text CHECK (result IN ('done','partial','refused')),
  identity_checked boolean NOT NULL DEFAULT false,
  created          timestamptz NOT NULL DEFAULT now()
);

-- α2 사용 집계(2026-10-19). 요청 경로는 메모리 카운터만, db.usage_flush 가 5분마다 합산 upsert(수면·재배포 때 ≤5분치 유실 허용).
-- name = MCP 도구 이름 또는 web:<경로 이름>, code = OK·EXC·오류 코드(열거형만, 자유 텍스트 없음). day = KST 날짜.
-- FK 없음(익명·미동의 '' 행이 있다). 계정 삭제는 db.user_delete 가 이 테이블을 따로 지운다.
CREATE TABLE IF NOT EXISTS usage_daily (
  day      date NOT NULL,
  user_id  text NOT NULL DEFAULT '',
  name     text NOT NULL,
  code     text NOT NULL,
  n        integer NOT NULL,
  PRIMARY KEY (day, user_id, name, code)
);

-- β2 관리자 감사(2026-10-19). FK 없음 — 계정이 지워져도 남는다. detail 은 열거 키만(이메일·자유 텍스트 없음).
-- 1년(db.AUDIT_KEEP_DAYS) 지난 행은 서버 시작 때 파기(db.admin_audit_purge). bootstrap·recover 행은 파기하지 않는다 — 초기화 완료 표시라 지우면 env 를 다시 읽는다.
CREATE TABLE IF NOT EXISTS admin_audit (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor_id   text,                                -- 실행자 user id. bootstrap·recover(CLI) 는 NULL
  action     text NOT NULL CHECK (action IN ('bootstrap','recover','role_grant','role_revoke','web_signout','block','unblock',
                                         'delete_notify','delete_run','delete_cancel',
                                         'device_connect','device_reissue','device_revoke','device_policy',
                                         'migration_apply','migration_extend','migration_close')),   -- 삭제 CLI(PR B) 3종 · board 이전 7종. 옛 DB 는 init_schema 가 넓힌다
  target_id  text,
  detail     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_admin_audit_at ON admin_audit (at DESC);

-- 선택 동의(2026-10-19, 운영 콘솔). 처리방침 게이트(agreements) 밖 — 동의하지 않아도 모든 기능을 쓴다.
-- 현재 상태 1행(PK user_id·kind) + 이력(로그). 변경은 db.optional_consent_set 한 곳(FOR UPDATE → 상태 → 로그, 한 트랜잭션).
-- 유효 동의 = state='granted' AND version = 현재 상수(db.OPTIONAL_VERSIONS). version 은 공유 범위 문서 버전(처리방침 머리글과 별개).
CREATE TABLE IF NOT EXISTS optional_consents (
  user_id    text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       text NOT NULL CHECK (kind IN ('struct_names')),
  version    text NOT NULL,
  state      text NOT NULL CHECK (state IN ('granted','withdrawn')),
  changed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kind)
);
CREATE TABLE IF NOT EXISTS optional_consent_log (
  id       bigserial PRIMARY KEY,
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind     text NOT NULL CHECK (kind IN ('struct_names')),
  version  text NOT NULL,
  action   text NOT NULL CHECK (action IN ('grant','withdraw')),
  at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_optional_consent_log_user ON optional_consent_log (user_id, at);

-- 1회성 마이그레이션 표식(2026-10-19). key 가 있으면 그 마이그레이션은 이미 했다 — init_schema 가 INSERT … ON CONFLICT DO NOTHING 으로 판정.
CREATE TABLE IF NOT EXISTS schema_marks (
  key  text PRIMARY KEY,
  at   timestamptz NOT NULL DEFAULT now()
);

-- 문의 이력(P3, 2026-10-19). public = 사용자에게 보이는 사건(상태·답변), internal = 운영자만(메모·재분류).
-- from_val·to_val 에는 열거값만(상태·kind). reply·note 사건은 값 없이 「바뀜」만. reason = privacy 에서 다른 kind 로 재분류한 사유.
-- 문의 파기 = cascade 로 함께 지운다.
CREATE TABLE IF NOT EXISTS ticket_history (
  id          bigserial PRIMARY KEY,
  ticket_id   text NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  at          timestamptz NOT NULL DEFAULT now(),
  actor_id    text,                                -- 실행한 관리자 user id(FK 없음 — 계정이 지워져도 남는다)
  visibility  text NOT NULL CHECK (visibility IN ('public','internal')),
  kind        text NOT NULL CHECK (kind IN ('status','reply','note','reclassify','deleted')),   -- deleted = 삭제 CLI 실행(PR B, public·값 없음)
  from_val    text CHECK (from_val IS NULL OR from_val IN ('open','ack','doing','done','wontfix','bug','request','question','privacy')),
  to_val      text CHECK (to_val IS NULL OR to_val IN ('open','ack','doing','done','wontfix','bug','request','question','privacy')),
  reason      text CHECK (reason IS NULL OR reason IN ('not_privacy','duplicate','other'))
);
CREATE INDEX IF NOT EXISTS ix_ticket_history_ticket ON ticket_history (ticket_id, at, id);

-- PR #20 표준(2026-10-19, 명세 §0·§3). std_id 를 참조하는 INDEX·CHECK 는 여기 두지 않는다 — 옛 DB 에선 이 파일이 ADD COLUMN 보다 먼저 돈다.
-- std_marks = 사용자별 설치 버전 표식. 신규 시드 시작 또는 첫 편입 때 생긴다(일부 편입이어도 있다). 없으면 std.version·state 가 null.
CREATE TABLE IF NOT EXISTS std_marks (
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version  text NOT NULL,                          -- 명세 파일 주소 `std@1`. 식별자(std_id)엔 버전을 넣지 않는다
  state    text NOT NULL CHECK (state IN ('seed_pending','seeded','adopting')),
  at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, version)
);
-- std_choices = 표준 오브젝트별 사용자 상태. 행 없음 = none(아직 고르지 않음). 다시 고르면 덮어쓴다(이력 없음).
-- def_id·def_revision = 고를 때 본 같은 key 기존 오브젝트(없으면 NULL) — 적용 때 재검증용 스냅샷이라 object_defs FK 를 걸지 않는다
-- (db.user_delete 가 object_defs 를 users 보다 먼저 지운다).
CREATE TABLE IF NOT EXISTS std_choices (
  user_id            text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  std_key            text NOT NULL,                -- 표준 오브젝트 key(std@1.json)
  state              text NOT NULL CHECK (state IN ('deferred','refused','incorporated','seeded')),
  def_id             text,
  def_revision       integer CHECK (def_revision IS NULL OR def_revision >= 0),
  confirmed_meaning  boolean NOT NULL DEFAULT false,   -- 기존 오브젝트 편입 때 「같은 뜻으로 쓴다」 명시 확인(5단계)
  at                 timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, std_key),
  CONSTRAINT ck_std_choices_def CHECK ((def_id IS NULL) = (def_revision IS NULL))
);

-- 분류 역할(PR #20 §5). 사용자별 역할 하나당 오브젝트 1개. 이번 역할은 classify 뿐 — 기록 → 항목 링크(field_key='axes')의 대상 오브젝트.
-- 이관: init_schema 가 key='axes' 인 적용·미보관 오브젝트를 대상으로 잡는다(매번, 없는 사용자만 — 멱등). 신규 시드는 표준 축.
-- 오브젝트가 지워지면(계정 삭제 때 user_delete 가 object_defs 를 먼저 지운다) 같이 지운다.
CREATE TABLE IF NOT EXISTS user_roles (
  user_id    text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('classify')),
  object_id  text NOT NULL,
  at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role),
  FOREIGN KEY (user_id, object_id) REFERENCES object_defs(user_id, id) ON DELETE CASCADE
);

-- PR B 삭제 통지 메타 + contact(2026-10-20). 계정당 한 행. 실행 때 이메일·auth_subject 를 users 에서 여기로 옮긴다.
-- users FK 없음(옛 코드 user_delete 뒤에도 남게) · tickets FK 없음(파기 순서는 db.contacts_purge 가 정한다).
-- 이 행이 가리키는 문의는 180일 파기(db.tickets_purge)에서 빠지고, purge_after(= deleted_at + 1년) 뒤 이 행과 함께 파기된다.
CREATE TABLE IF NOT EXISTS deletion_contacts (
  user_id      text PRIMARY KEY,
  ticket_id    text NOT NULL,                     -- 삭제 요청 문의(kind='privacy')
  requested_at timestamptz NOT NULL,              -- = 그 문의 created
  notified_at  timestamptz NOT NULL,              -- 공개 통지 시각
  delete_after timestamptz NOT NULL,              -- = notified_at + 7일
  email        text,
  auth_subject text,
  deleted_at   timestamptz,
  purge_after  timestamptz,
  CHECK ((deleted_at IS NULL) = (purge_after IS NULL)),
  CHECK (deleted_at IS NOT NULL OR (email IS NULL AND auth_subject IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_deletion_contacts_email ON deletion_contacts (email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_deletion_contacts_subject ON deletion_contacts (auth_subject) WHERE auth_subject IS NOT NULL;

-- PR ①-b 페이지 레이아웃(2026-10-22, 구현계획 §3). 사용자당 한 행 — 「나」 하위 탭 순서·요약 블록 순서. 기본값은 코드(db.DEFAULT_LAYOUT) 한 곳.
-- 행 없음 = 기본 + version 0. layout NULL = [기본으로 되돌리기] 뒤(기본 + 그 version) — 행을 지우지 않아 버전이 되감기지 않는다(옛 탭의 PUT 이 STALE).
-- 관리자 API 없음. 계정 삭제는 db.DELETE_TABLES.
CREATE TABLE IF NOT EXISTS page_layouts (
  user_id    text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  layout     jsonb CHECK (layout IS NULL OR jsonb_typeof(layout) = 'object'),
  version    integer NOT NULL CHECK (version > 0),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- PR ②-a 작업 전달(2026-10-22, 구현계획 §5). 세션(session_id) = 여러 실행을 묶는 불투명 ID, 작업 = PC 실행 1번.
-- 이 행엔 본문이 없다 — 입력·결과(미확정 내용 전체)는 임시 보관(tmp 스키마, db._init_tmp 가 만든다 — 운영 백업 -n public 밖, 24h 만료 삭제).
-- 종료 표식 = status·terminal_at·reason_code(코드만). CLI 세션 ID 는 PC 에만. 계정 삭제는 db.DELETE_TABLES(입력원 앞) + tmp 명시 삭제.
CREATE TABLE IF NOT EXISTS jobs (
  id                  uuid PRIMARY KEY,
  user_id             text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id          uuid NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('converse','summarize_reading','routine_ingest')),
  status              text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','leased','running','result_ready','confirmed','failed',
                                                                      'cancelled','expired','needs_check')),
  device_id           text,                       -- 임대한 장치(import_streams.id). 임대 전 NULL. FK 없음 — 옛 DB 재현 시험이 import_streams 를 지운다
  lease_generation    integer NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
  lease_expires_at    timestamptz,
  input_version       integer NOT NULL DEFAULT 1 CHECK (input_version > 0),
  definitions_version text,                       -- ②-b 구조 정의 스냅샷 sha256 hex(최초 발급 때 고정). ②-a 는 NULL. 옛 DB integer → init_schema 가 text 로
  edit_generation     integer NOT NULL DEFAULT 0 CHECK (edit_generation >= 0),   -- ②-b 이 작업을 접수할 때의 세션 편집 세대. 옛 DB 는 init_schema
  parent_result_version uuid,                     -- ②-b 재정리 작업이면 원 결과 버전. 옛 DB 는 init_schema
  attempts            integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),   -- 입력 조회 성공 수(PC 시도 기록)
  reason_code         text CHECK (reason_code IN ('output-not-json','output-shape','result-shape','timeout','cli-exit','login',
                                                  'input-too-large')),
  created_at          timestamptz NOT NULL DEFAULT now(),
  expires_at          timestamptz NOT NULL,       -- created_at + 24h(서버 시각). 연장 없음
  terminal_at         timestamptz,
  UNIQUE (user_id, id),
  CHECK ((status IN ('confirmed','failed','cancelled','expired','needs_check')) = (terminal_at IS NOT NULL)),
  CHECK (status <> 'failed' OR reason_code IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_jobs_queue ON jobs (user_id, created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_jobs_session ON jobs (user_id, session_id);

-- PR ②-b 쌓기 단계·확인 1번(2026-10-22, 구현계획 §3-1). 이 표들엔 본문이 없다 — 입력·결과·미리보기(정리 글·변경 목록)는 임시 보관
-- (tmp 스키마, db._init_tmp). 계정 삭제는 db.DELETE_TABLES + user_delete(items cascade).
-- 세션 = 대화 하나(여러 작업). 만료 = 최초 입력 + 24h 고정(후속 턴·재정리로 연장 없음). 확정은 세션당 1번(confirm_events UNIQUE).
CREATE TABLE IF NOT EXISTS stack_sessions (
  id                uuid PRIMARY KEY,                 -- = jobs.session_id(FK 없음, 서버 검사)
  user_id           text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  input_kind        text NOT NULL CHECK (input_kind IN ('converse','verbatim')),   -- 첫 입력 때 정한다. 재정리로 바뀌지 않는다
  latest_job_id     uuid,                             -- 이 세션에서 가장 늦게 접수한 작업
  latest_preview_id uuid,                             -- 그 작업의 최신 미리보기(tmp). 없으면 NULL
  edit_generation   integer NOT NULL DEFAULT 0 CHECK (edit_generation >= 0),
  expires_at        timestamptz NOT NULL,
  confirm_event_id  uuid,                             -- 확정되면 그 확인 이벤트
  created_at        timestamptz NOT NULL DEFAULT now(),
  terminal_at       timestamptz,                      -- 확정·만료 시각
  UNIQUE (user_id, id),
  CHECK (confirm_event_id IS NULL OR terminal_at IS NOT NULL)
);

-- 확인 이벤트 = 사용자가 미리보기 하나(버전·해시)를 보고 [확정] 1번. 대화 정리 기록의 인용 적격 근거(db._eligible). 계정 존속 기간 보관.
-- request_id = 화면이 만든 확정 요청 ID(멱등 키). response = 성공 응답의 식별자만(본문 없음) — 같은 요청 재전송에 그대로 돌려준다.
CREATE TABLE IF NOT EXISTS confirm_events (
  id               uuid PRIMARY KEY,
  user_id          text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id       uuid NOT NULL,
  job_id           uuid NOT NULL,
  preview_id       uuid NOT NULL,
  preview_version  integer NOT NULL CHECK (preview_version > 0),
  preview_sha      text NOT NULL CHECK (preview_sha ~ '^[0-9a-f]{64}$'),
  request_id       text NOT NULL CHECK (request_id ~ '^[A-Za-z0-9_-]{8,64}$'),
  final_text_sha   text NOT NULL CHECK (final_text_sha ~ '^[0-9a-f]{64}$'),
  response         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(response) = 'object'),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, id),
  UNIQUE (user_id, request_id),
  UNIQUE (user_id, session_id)                      -- 세션당 확정 1번
);

-- 판 이력 = 확인 1번으로 확정한 판단(principle) 항목의 불변 판. 확정 판은 고치지 않는다 — 새 판 + 이전 판 ID. 인용은 당시 판을 가리킨다.
CREATE TABLE IF NOT EXISTS item_versions (
  user_id          text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id               text NOT NULL,                    -- iv_...
  item_id          text NOT NULL,
  version_no       integer NOT NULL CHECK (version_no > 0),
  prev_version_id  text,
  item_revision    integer NOT NULL CHECK (item_revision > 0),   -- 이 판을 만든 뒤의 items.revision
  fields           jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'object'),   -- {title, body, data, decision, code}
  links            jsonb NOT NULL CHECK (jsonb_typeof(links) = 'array'),
  confirm_event_id uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id),
  UNIQUE (user_id, item_id, version_no),
  FOREIGN KEY (user_id, item_id) REFERENCES items(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, prev_version_id) REFERENCES item_versions(user_id, id),
  FOREIGN KEY (user_id, confirm_event_id) REFERENCES confirm_events(user_id, id),
  CHECK ((version_no = 1) = (prev_version_id IS NULL))
);

-- stage = 서버가 계산하는 관리값(직접 수정 API 없음). 판단의 확정 기준은 items.decision. 규칙 = stack_service.compute_stage(rule_version).
-- item_revision = 계산에 쓴 items.revision — 다르면(또는 규칙 버전이 다르면) 읽을 때 다시 계산한다(lazy, 백필 없음).
CREATE TABLE IF NOT EXISTS item_stage (
  user_id          text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id          text NOT NULL,
  stage            text NOT NULL CHECK (stage IN ('draft','organized','confirmed')),
  rule_version     integer NOT NULL CHECK (rule_version > 0),
  item_revision    integer NOT NULL CHECK (item_revision >= 0),
  item_version_id  text,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, item_id),
  FOREIGN KEY (user_id, item_id) REFERENCES items(user_id, id) ON DELETE CASCADE
);
