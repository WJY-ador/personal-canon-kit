-- personal-canon-kit — Postgres 스키마 (2026-10-18 확정, 같은 날 Codex 토론 반영). 설계 근거: data-model.md
-- 행 격리: 모든 조회·쓰기는 user_id 로 묶는다 (OWD Private). 자식은 (user_id, 부모id) 복합 FK 로 다른 사용자 행을 참조 못 한다.

CREATE TABLE IF NOT EXISTS users (
  id            text PRIMARY KEY,                 -- u_...
  email         text NOT NULL UNIQUE,             -- 소문자 정규화. 매직링크·Google 같은 이메일이면 같은 행
  auth_subject  text UNIQUE,                      -- AuthKit sub (External ID)
  created       timestamptz NOT NULL DEFAULT now()
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
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE SET NULL (question_id),  -- Lookup
  CONSTRAINT ck_records_author_kind CHECK (author_kind IN ('self','external','unknown')),
  CONSTRAINT ck_records_text_process CHECK (text_process IN ('verbatim','structured','rewritten','generated','unknown')),
  CONSTRAINT ck_records_provenance_history CHECK (jsonb_typeof(provenance_history) = 'array')
);
CREATE INDEX IF NOT EXISTS ix_records_user ON records (user_id, created DESC) WHERE deleted_at IS NULL;

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
  target_kind      text CHECK (target_kind IN ('item','record','candidate')),
  target_object_id text,
  multiple         boolean NOT NULL DEFAULT false,
  position         integer NOT NULL DEFAULT 0,
  archived_at      timestamptz,
  template_slot    text,                            -- 템플릿 계보 `author@1:<object_key>.<field_key>`. 시드만 채운다
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
  PRIMARY KEY (user_id, id),
  UNIQUE (user_id, id, object_id),                  -- item_links 복합 FK 대상
  UNIQUE (user_id, object_id, code),
  FOREIGN KEY (user_id, object_id) REFERENCES object_defs(user_id, id)                 -- 보호 Lookup
);
CREATE INDEX IF NOT EXISTS ix_items_user_obj ON items (user_id, object_id, updated_at DESC) WHERE archived_at IS NULL;

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
  created_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, id),
  CHECK ((source_item_id IS NOT NULL AND source_object_id IS NOT NULL AND source_record_id IS NULL)
      OR (source_item_id IS NULL AND source_object_id IS NULL AND source_record_id IS NOT NULL)),
  CHECK (source_record_id IS NULL OR field_key IN ('attach_to','axes')),
  CHECK (num_nonnulls(target_item_id, target_record_id, target_candidate_id) = 1),
  FOREIGN KEY (user_id, source_item_id, source_object_id) REFERENCES items(user_id, id, object_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, source_object_id, field_key) REFERENCES field_defs(user_id, object_id, key),   -- 보호
  FOREIGN KEY (user_id, source_record_id) REFERENCES records(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, target_item_id) REFERENCES items(user_id, id),                -- 보호
  FOREIGN KEY (user_id, target_record_id) REFERENCES records(user_id, id),            -- 보호
  FOREIGN KEY (user_id, target_candidate_id) REFERENCES candidates(user_id, id)       -- 보호
);
CREATE INDEX IF NOT EXISTS ix_links_src_item ON item_links (user_id, source_item_id);
CREATE INDEX IF NOT EXISTS ix_links_target_item ON item_links (user_id, target_item_id);
CREATE INDEX IF NOT EXISTS ix_links_target_cand ON item_links (user_id, target_candidate_id);
CREATE INDEX IF NOT EXISTS ix_links_src_record ON item_links (user_id, source_record_id);

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
