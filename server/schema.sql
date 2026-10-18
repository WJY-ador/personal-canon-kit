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
  UNIQUE (user_id, id),
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE SET NULL (question_id)  -- Lookup
);
CREATE INDEX IF NOT EXISTS ix_records_user ON records (user_id, created DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS candidates (
  id             text PRIMARY KEY,                -- c_...
  user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  record_id      text NOT NULL,
  question_id    text NOT NULL,
  text           text NOT NULL,
  quote          text NOT NULL,                   -- record.body[quote_start:quote_end] 와 같다. 서버가 위치를 센다
  quote_start    integer NOT NULL,                -- 코드포인트 오프셋
  quote_end      integer NOT NULL,
  origin         text NOT NULL CHECK (origin IN ('ai','self')),
  decision       text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','confirmed','excluded')),
  edited_by_user boolean NOT NULL DEFAULT false,
  created        timestamptz NOT NULL DEFAULT now(),
  updated        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, id),
  UNIQUE (user_id, record_id, quote_start, quote_end),   -- 같은 근거 구간 재등록 방지
  FOREIGN KEY (user_id, record_id)   REFERENCES records(user_id, id)   ON DELETE CASCADE,   -- M-D: 근거 없는 판단 없음
  FOREIGN KEY (user_id, question_id) REFERENCES questions(user_id, id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_candidates_user_q ON candidates (user_id, question_id, decision);

CREATE TABLE IF NOT EXISTS candidate_history (
  id            bigserial PRIMARY KEY,
  candidate_id  text NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,  -- M-D, user 는 부모 상속
  action        text NOT NULL CHECK (action IN ('confirm','exclude','pending','edit')),
  prev_decision text NOT NULL,
  prev_text     text NOT NULL,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hist_cand ON candidate_history (candidate_id, id);

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
