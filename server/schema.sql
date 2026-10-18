-- personal-canon-kit — Postgres 스키마 (2026-10-18 확정). 설계 근거: data-model.md
-- 행 격리: 모든 조회·쓰기는 user_id 로 묶는다 (OWD Private). RLS 는 선택.

CREATE TABLE users (
  id            text PRIMARY KEY,                 -- u_...
  email         text NOT NULL UNIQUE,             -- 소문자 정규화. 매직링크·Google 같은 이메일이면 같은 행
  auth_subject  text UNIQUE,                      -- AuthKit sub (External ID)
  created       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agreements (
  id          bigserial PRIMARY KEY,
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('terms','privacy')),
  version     text NOT NULL,
  accepted_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, kind, version)
);

CREATE TABLE questions (
  id       text PRIMARY KEY,                      -- q_...
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text     text NOT NULL,
  status   text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),  -- 삭제 없음, archive 만
  created  timestamptz NOT NULL DEFAULT now(),
  updated  timestamptz
);

CREATE TABLE records (
  id          text PRIMARY KEY,                   -- r_...
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id text REFERENCES questions(id) ON DELETE SET NULL,   -- Lookup: 기록은 질문보다 오래 산다
  title       text NOT NULL,
  body        text NOT NULL,                      -- 원문 그대로. 수정 없음
  source      text NOT NULL DEFAULT '대화에서 입력',
  created     timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz                         -- soft delete. 하드 삭제는 계정 삭제(users cascade) 때만
);
CREATE INDEX ix_records_user ON records (user_id, created DESC) WHERE deleted_at IS NULL;

CREATE TABLE candidates (
  id             text PRIMARY KEY,                -- c_...
  user_id        text NOT NULL REFERENCES users(id) ON DELETE CASCADE,     -- 반정규화: 격리·인덱스용. 서버가 record.user_id 로만 채움
  record_id      text NOT NULL REFERENCES records(id) ON DELETE CASCADE,   -- M-D: 근거 없는 판단 없음
  question_id    text NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  text           text NOT NULL,
  quote          text NOT NULL,                   -- record.body 안 연속 구간. 저장 시 서버 검사
  origin         text NOT NULL CHECK (origin IN ('ai','self')),
  decision       text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','confirmed','excluded')),
  edited_by_user boolean NOT NULL DEFAULT false,
  created        timestamptz NOT NULL DEFAULT now(),
  updated        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, record_id, quote)              -- 근거 구간 하나에 판단 하나
);
CREATE INDEX ix_candidates_user_q ON candidates (user_id, question_id, decision);

CREATE TABLE candidate_history (
  id            bigserial PRIMARY KEY,
  candidate_id  text NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,  -- M-D, user 는 부모 상속
  action        text NOT NULL CHECK (action IN ('confirm','exclude','pending','edit')),
  prev_decision text NOT NULL,
  prev_text     text NOT NULL,
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_hist_cand ON candidate_history (candidate_id, at);

CREATE TABLE profile (
  user_id  text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key      text NOT NULL CHECK (key IN ('purpose','refind','daily_views','first_material','separate','help_level')),
  value    text NOT NULL,
  status   text NOT NULL CHECK (status IN ('confirmed','guessed','empty','off')),
  source   text NOT NULL DEFAULT '',
  updated  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

CREATE TABLE tabs (
  user_id   text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  doc       jsonb NOT NULL,                       -- 적용된 탭
  draft_doc jsonb,                                -- tabs_propose 결과(미리보기). NULL 이면 제안 없음. tabs_apply 가 doc 으로 옮기고 비운다
  revision  integer NOT NULL DEFAULT 0,
  updated   timestamptz NOT NULL DEFAULT now()
);
