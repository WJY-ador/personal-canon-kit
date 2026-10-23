"""PC 로컬 상태 — SQLite 대응표 · 원문 저장소 · 전송 대기 파일.

- events: 수집 이벤트(어댑터, 중복 기준) ↔ 불투명 original_id · raw_id · 원문 상대 경로
- revisions: 개정 사슬과 전송 상태. 요청 바이트는 pending/<멱등키>.json 에 원자적으로 둔다.
  서버가 「삭제됨」으로 답한 개정 = status 'accepted' + server_state 'deleted'(휴지통) | 'purged'(영구 삭제 표식),
  updated_at = 그 응답을 받은 시각
  (같은 개정은 다시 보내지 않는다. 내용이 바뀐 새 개정은 이 판 뒤로 보낸다 — 서버가 복원했으면 받는다.
  스키마 그대로라 이전 판 프로그램도 같은 DB 를 연다)
- people: 키 공간 · 제공자 · 계정 식별자 ↔ HMAC (PC 전용 매핑)
- quarantine: 판별 불가 중복·형식 오류. 원문·식별자는 넣지 않고 다이제스트만.
- halts: 사용자 조치 전까지 멈춘 범위(인증·요약 로그인 등)
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3

from . import contract, osutil

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  adapter      TEXT NOT NULL,
  dedupe_key   TEXT NOT NULL,
  kind         TEXT NOT NULL,
  original_id  TEXT NOT NULL UNIQUE,
  raw_id       TEXT UNIQUE,
  raw_path     TEXT,
  fingerprint  TEXT,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (adapter, dedupe_key)
);
CREATE TABLE IF NOT EXISTS revisions (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  original_id      TEXT NOT NULL REFERENCES events(original_id),
  revision         TEXT NOT NULL UNIQUE,
  previous_revision TEXT,
  projection_sha   TEXT NOT NULL,
  idempotency_key  TEXT NOT NULL UNIQUE,
  request_sha      TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('pending','accepted','rejected','conflict','halted')),
  attempts         INTEGER NOT NULL DEFAULT 0,
  next_attempt_at  TEXT,
  last_error       TEXT,
  import_id        TEXT,
  server_state     TEXT,
  target_id        TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_revisions_original ON revisions(original_id, seq);
CREATE TABLE IF NOT EXISTS people (
  namespace     TEXT NOT NULL,
  provider_code TEXT NOT NULL,
  account_id    TEXT NOT NULL,
  hmac          TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (namespace, provider_code, account_id)
);
CREATE TABLE IF NOT EXISTS quarantine (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  adapter     TEXT NOT NULL,
  reason      TEXT NOT NULL,
  key_digest  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (adapter, reason, key_digest)
);
CREATE TABLE IF NOT EXISTS halts (
  scope   TEXT PRIMARY KEY,
  reason  TEXT NOT NULL,
  at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS summary_attempts (
  day  TEXT PRIMARY KEY,
  n    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS input_versions (
  original_id TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  revision    TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (original_id, sha256)
);
CREATE TABLE IF NOT EXISTS summary_done (
  input_original_id TEXT NOT NULL,
  sha256            TEXT NOT NULL,
  status            TEXT NOT NULL CHECK (status IN ('staged','failed')),
  day               TEXT NOT NULL,
  reason            TEXT,
  PRIMARY KEY (input_original_id, sha256)
);
"""

OPEN_STATUSES = ("pending", "halted")
SERVER_DELETED = "deleted"   # revisions.server_state — 서버가 그 개정을 휴지통에 있는 원본이라 받지 않았다
SERVER_PURGED = "purged"     # revisions.server_state — 서버가 그 개정을 영구 삭제된 원본(표식)이라 받지 않았다
DELETED_STATES = (SERVER_DELETED, SERVER_PURGED)


def _now() -> str:
    return osutil.kst_now().isoformat(timespec="seconds")


def new_original_id() -> str:
    return "o_" + secrets.token_hex(16)


def new_revision() -> str:
    return "v_" + secrets.token_hex(16)


def new_raw_id() -> str:
    return secrets.token_hex(24)


def digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, paths):
        self.paths = paths.ensure()
        self.conn = sqlite3.connect(paths.db, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        osutil.set_private_file(paths.db)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ---- 트랜잭션 ----
    def tx(self):
        return _Tx(self.conn)

    # ---- 이벤트 ----
    def event_get(self, adapter: str, dedupe_key: str):
        return self.conn.execute("SELECT * FROM events WHERE adapter=? AND dedupe_key=?",
                                 (adapter, dedupe_key)).fetchone()

    def event_by_raw(self, raw_id: str):
        if not contract.RAW_ID_RE.match(raw_id or ""):
            return None
        return self.conn.execute("SELECT * FROM events WHERE raw_id=?", (raw_id,)).fetchone()

    def event_create(self, adapter: str, dedupe_key: str, kind: str, fingerprint: str | None,
                     raw_obj=None):
        """original_id(·raw_id) 를 최초 한 번만 난수 발급. 원문이 있으면 원문 저장소에 쓴다."""
        oid = new_original_id()
        raw_id = raw_path = None
        if raw_obj is not None:
            raw_id = new_raw_id()
            raw_path = raw_id + ".json"
            data = json.dumps(raw_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
            osutil.atomic_write(os.path.join(self.paths.raw, raw_path), data)
        try:
            self.conn.execute(
                "INSERT INTO events(adapter, dedupe_key, kind, original_id, raw_id, raw_path, fingerprint, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (adapter, dedupe_key, kind, oid, raw_id, raw_path, fingerprint, _now()))
        except sqlite3.IntegrityError:
            if raw_path:
                try:
                    os.unlink(os.path.join(self.paths.raw, raw_path))
                except OSError:
                    pass
            raise
        return self.event_get(adapter, dedupe_key)

    def raw_update(self, raw_id: str, raw_obj) -> None:
        ev = self.event_by_raw(raw_id)
        if ev is None or not ev["raw_path"]:
            raise StoreError("raw not mapped")
        data = json.dumps(raw_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        osutil.atomic_write(self.resolve_raw_path(raw_id), data)

    def resolve_raw_path(self, raw_id: str) -> str:
        """대응표 경로가 원문 저장소 안의 실제 파일인지 확인. 심링크·경로 탈출은 거절."""
        ev = self.event_by_raw(raw_id)
        if ev is None or not ev["raw_path"]:
            raise StoreError("not found")
        rel = ev["raw_path"]
        # 대응표 경로는 「<raw_id>.json」 한 단계 이름만 허용 — 경로 구분자·상위 경로 차단
        if not isinstance(rel, str) or rel != raw_id + ".json":
            raise StoreError("bad path")
        if os.path.islink(self.paths.raw):
            raise StoreError("symlink")
        root = os.path.realpath(self.paths.raw)
        cand = os.path.join(root, rel)
        if os.path.islink(cand):
            raise StoreError("symlink")
        real = os.path.realpath(cand)
        if os.path.dirname(real) != root:
            raise StoreError("escape")
        if not os.path.isfile(real):
            raise StoreError("not found")
        return real

    # ---- 개정 ----
    def latest_revision(self, original_id: str):
        return self.conn.execute(
            "SELECT * FROM revisions WHERE original_id=? ORDER BY seq DESC LIMIT 1", (original_id,)).fetchone()

    def latest_accepted(self, original_id: str):
        return self.conn.execute(
            "SELECT * FROM revisions WHERE original_id=? AND status='accepted' ORDER BY seq DESC LIMIT 1",
            (original_id,)).fetchone()

    def deleted_response(self, original_id: str):
        """마지막 접수 응답이 「삭제됨」이면 그 개정 행(server_state·updated_at), 아니면 None. 다음 개정이 접수되면 None."""
        r = self.latest_accepted(original_id)
        return r if r is not None and r["server_state"] in DELETED_STATES else None

    def has_open_revision(self, original_id: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM revisions WHERE original_id=? AND status IN ('pending','halted') LIMIT 1",
            (original_id,)).fetchone()
        return r is not None

    def pending_file(self, idem_key: str) -> str:
        if not contract.IDEMPOTENCY_RE.match(idem_key or ""):
            raise StoreError("bad idempotency key")
        return os.path.join(self.paths.pending, idem_key + ".json")

    def add_revision(self, *, original_id: str, revision: str, previous_revision: str | None,
                     projection_sha: str, idem_key: str, body: bytes) -> None:
        """DB 행과 전송 대기 파일을 함께 만든다. 파일 쓰기가 실패하면 행도 남기지 않는다."""
        req_sha = hashlib.sha256(body).hexdigest()
        now = _now()
        with self.tx():
            self.conn.execute(
                "INSERT INTO revisions(original_id, revision, previous_revision, projection_sha, idempotency_key,"
                " request_sha, status, created_at, updated_at) VALUES (?,?,?,?,?,?,'pending',?,?)",
                (original_id, revision, previous_revision, projection_sha, idem_key, req_sha, now, now))
            osutil.atomic_write(self.pending_file(idem_key), body)

    def rebase_pending(self, rebuild) -> dict:
        """허용 규칙(정책 판)이 바뀐 뒤 — 아직 접수되지 않은 전송 대기 요청을 rebuild(옛 바이트) → (새 바이트, 새 projection_sha)
        로 다시 쓴다. 개정 ID·멱등 키는 그대로(서버는 POLICY-STALE 요청을 저장하지 않는다). rebuild 가 ContractError 를 내면
        (새 규칙이 그 행동·축·변환을 허용하지 않음) rejected + 격리. 반환 {"rebased", "rejected"}."""
        out = {"rebased": 0, "rejected": 0}
        for r in self.conn.execute("SELECT * FROM revisions WHERE status='pending' ORDER BY seq").fetchall():
            key = r["idempotency_key"]
            try:
                body, proj = rebuild(self.read_pending(r))
            except contract.ContractError as e:
                self.mark(key, "rejected", error="policy-refresh:" + e.code)
                self.quarantine("upload", "policy-refresh", key)
                out["rejected"] += 1
                continue
            with self.tx():
                self.conn.execute(
                    "UPDATE revisions SET request_sha=?, projection_sha=?, attempts=0, next_attempt_at=NULL, last_error=NULL,"
                    " updated_at=? WHERE idempotency_key=?", (hashlib.sha256(body).hexdigest(), proj, _now(), key))
                osutil.atomic_write(self.pending_file(key), body)
            out["rebased"] += 1
        return out

    def pending_revisions(self):
        return self.conn.execute(
            "SELECT * FROM revisions WHERE status='pending' ORDER BY seq").fetchall()

    def read_pending(self, rev) -> bytes:
        with open(self.pending_file(rev["idempotency_key"]), "rb") as f:
            body = f.read()
        if hashlib.sha256(body).hexdigest() != rev["request_sha"]:
            raise StoreError("pending file changed")
        return body

    def mark(self, idem_key: str, status: str, *, error: str | None = None, result: dict | None = None,
             next_attempt_at: str | None = None, bump_attempt: bool = False) -> None:
        sets = ["status=?", "updated_at=?", "last_error=?", "next_attempt_at=?"]
        vals = [status, _now(), error, next_attempt_at]
        if bump_attempt:
            sets.append("attempts=attempts+1")
        if result is not None:
            sets += ["import_id=?", "server_state=?", "target_id=?"]
            vals += [result.get("import_id"), result.get("state"), result.get("target_id")]
        vals.append(idem_key)
        self.conn.execute(f"UPDATE revisions SET {', '.join(sets)} WHERE idempotency_key=?", vals)
        if status in ("accepted", "rejected", "conflict"):
            try:
                os.unlink(self.pending_file(idem_key))
            except FileNotFoundError:
                pass

    def bump_attempt(self, idem_key: str, error: str, next_attempt_at: str | None) -> None:
        self.conn.execute(
            "UPDATE revisions SET attempts=attempts+1, last_error=?, next_attempt_at=?, updated_at=?"
            " WHERE idempotency_key=?", (error, next_attempt_at, _now(), idem_key))

    def reconcile(self) -> int:
        """DB 행 없는 전송 대기 파일(쓰기 도중 중단)을 지운다. 지운 수."""
        known = {r[0] for r in self.conn.execute("SELECT idempotency_key FROM revisions WHERE status='pending'")}
        n = 0
        for name in os.listdir(self.paths.pending):
            if not name.endswith(".json"):
                continue
            key = name[:-5]
            if key not in known:
                try:
                    os.unlink(os.path.join(self.paths.pending, name))
                    n += 1
                except OSError:
                    pass
        return n

    # ---- 사람 ----
    def person_hmac(self, namespace: str, provider_code: str, account_id: str, compute):
        row = self.conn.execute(
            "SELECT hmac FROM people WHERE namespace=? AND provider_code=? AND account_id=?",
            (namespace, provider_code, account_id)).fetchone()
        if row:
            return row[0]
        h = compute()
        self.conn.execute("INSERT INTO people(namespace, provider_code, account_id, hmac, created_at)"
                          " VALUES (?,?,?,?,?)", (namespace, provider_code, account_id, h, _now()))
        return h

    def known_account_ids(self):
        return [r[0] for r in self.conn.execute("SELECT DISTINCT account_id FROM people")]

    # ---- 격리 ----
    def quarantine(self, adapter: str, reason: str, key: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO quarantine(adapter, reason, key_digest, created_at)"
                          " VALUES (?,?,?,?)", (adapter, reason, digest(key), _now()))

    # ---- 멈춤 ----
    def halt(self, scope: str, reason: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO halts(scope, reason, at) VALUES (?,?,?)", (scope, reason, _now()))

    def halted(self, scope: str):
        return self.conn.execute("SELECT * FROM halts WHERE scope=?", (scope,)).fetchone()

    def clear_halt(self, scope: str) -> bool:
        return self.conn.execute("DELETE FROM halts WHERE scope=?", (scope,)).rowcount > 0

    # ---- 요약 시도 수 ----
    def summary_attempts_today(self, day: str) -> int:
        r = self.conn.execute("SELECT n FROM summary_attempts WHERE day=?", (day,)).fetchone()
        return r[0] if r else 0

    def summary_attempt_bump(self, day: str) -> int:
        self.conn.execute("INSERT INTO summary_attempts(day, n) VALUES (?,1)"
                          " ON CONFLICT(day) DO UPDATE SET n=n+1", (day,))
        return self.summary_attempts_today(day)

    # ---- 요약 입력 판 ----
    def input_revision(self, original_id: str, sha256: str) -> str:
        r = self.conn.execute("SELECT revision FROM input_versions WHERE original_id=? AND sha256=?",
                              (original_id, sha256)).fetchone()
        if r:
            return r[0]
        rev = new_revision()
        self.conn.execute("INSERT INTO input_versions(original_id, sha256, revision, created_at) VALUES (?,?,?,?)",
                          (original_id, sha256, rev, _now()))
        return rev

    def summary_status(self, input_original_id: str, sha256: str):
        return self.conn.execute("SELECT * FROM summary_done WHERE input_original_id=? AND sha256=?",
                                 (input_original_id, sha256)).fetchone()

    def summary_set(self, input_original_id: str, sha256: str, status: str, day: str, reason=None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO summary_done(input_original_id, sha256, status, day, reason)"
                          " VALUES (?,?,?,?,?)", (input_original_id, sha256, status, day, reason))

    # ---- 상태 ----
    def counts(self) -> dict:
        out = {s: 0 for s in ("pending", "accepted", "rejected", "conflict", "halted")}
        for s, n in self.conn.execute("SELECT status, COUNT(*) FROM revisions GROUP BY status"):
            out[s] = n
        # 삭제 응답을 받은 개정은 접수(accepted)에서 뺀다. 삭제됨 = 마지막 접수 응답이 「삭제됨」인 원본 수
        # (서버에서 복원한 뒤 다음 개정이 접수되면 풀린다)
        out["accepted"] -= self.conn.execute(
            "SELECT COUNT(*) FROM revisions WHERE status='accepted' AND server_state IN (?,?)", DELETED_STATES).fetchone()[0]
        out["deleted"] = self.conn.execute(
            "SELECT COUNT(*) FROM revisions r WHERE r.status='accepted' AND r.server_state IN (?,?) AND NOT EXISTS"
            " (SELECT 1 FROM revisions n WHERE n.original_id=r.original_id AND n.status='accepted' AND n.seq>r.seq)",
            DELETED_STATES).fetchone()[0]
        out["events"] = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        out["quarantined"] = self.conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
        out["people"] = self.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        out["halts"] = [r[0] for r in self.conn.execute("SELECT scope FROM halts ORDER BY scope")]
        return out


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, et, ev, tb):
        if et is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False
