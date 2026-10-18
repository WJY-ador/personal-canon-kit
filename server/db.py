"""저장소 — 지금은 SQLite(개발), 나중에 Postgres. SQL 은 표준만 쓴다.

데이터 흐름은 로컬 판과 같다: 기록(원문 보존) → 후보(pending) → 사용자 결정(confirm/exclude/edit) → 이력 보존.
사용자 구분은 users.id. 모든 조회·쓰기는 user_id 로 묶인다.
"""
import json
import os
import secrets
import sqlite3
import datetime as _dt
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("CANON_DB", str(Path(__file__).parent / "data" / "canon.sqlite3")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  created TEXT NOT NULL, updated TEXT);
CREATE TABLE IF NOT EXISTS records (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '대화에서 입력', question_id TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, question_id TEXT NOT NULL, record_id TEXT NOT NULL,
  text TEXT NOT NULL, quote TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  edited_by_user INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL,
  UNIQUE (user_id, record_id, quote));
CREATE TABLE IF NOT EXISTS candidate_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, action TEXT NOT NULL,
  prev_status TEXT NOT NULL, prev_text TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profile (
  user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
  updated TEXT NOT NULL, PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS tabs (
  user_id TEXT PRIMARY KEY, doc TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cand_user_q ON candidates (user_id, question_id, status);
CREATE INDEX IF NOT EXISTS ix_rec_user ON records (user_id, created);
"""

PROFILE_KEYS = {
    "purpose": "이 노트로 하려는 일",
    "refind": "최근 다시 찾았던 자료와 그 이유",
    "daily_views": "매일·매주 열어 보고 싶은 것 (최대 3개)",
    "first_material": "처음 연결할 자기 기록 1종",
    "separate": "따로 빼 둘 민감·회사·타인 자료",
    "help_level": "직접 할 범위와 도움받고 싶은 범위",
}
STATUSES = ("confirmed", "guessed", "empty", "off")
CAND_ACTIONS = {"confirm": "confirmed", "exclude": "excluded", "pending": "pending"}


class CanonError(Exception):
    def __init__(self, code, message, action=""):
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


def now():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().replace(microsecond=0).isoformat()


def new_id(prefix):
    return f"{prefix}_{secrets.token_urlsafe(8)}"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


_con = None


def con():
    global _con
    if _con is None:
        _con = connect()
    return _con


@contextmanager
def tx():
    c = con()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


# ── 사용자 ─────────────────────────────────────────────

def user_by_email(email):
    email = email.strip().lower()
    with tx() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if r:
            return dict(r)
        u = {"id": new_id("u"), "email": email, "created": now()}
        c.execute("INSERT INTO users VALUES (?,?,?)", (u["id"], u["email"], u["created"]))
        return u


# ── 질문 ───────────────────────────────────────────────

def question_add(uid, text):
    text = (text or "").strip()
    if not text:
        raise CanonError("ARG-INVALID", "질문 문장이 비었어요.")
    q = {"id": new_id("q"), "text": text, "status": "active", "created": now()}
    with tx() as c:
        c.execute("INSERT INTO questions (id,user_id,text,status,created) VALUES (?,?,?,?,?)",
                  (q["id"], uid, q["text"], q["status"], q["created"]))
    return q


def questions(uid):
    return [dict(r) for r in con().execute("SELECT id,text,status,created FROM questions WHERE user_id=? ORDER BY created", (uid,))]


def question_get(uid, qid):
    r = con().execute("SELECT id,text,status,created FROM questions WHERE user_id=? AND id=?", (uid, qid)).fetchone()
    if not r:
        raise CanonError("ARG-INVALID", f"질문을 찾을 수 없어요: {qid}", "question_list 로 질문 ID 를 확인하세요.")
    return dict(r)


# ── 기록 ───────────────────────────────────────────────

def record_add(uid, title, body, question_id=None, source="대화에서 입력"):
    title, body = (title or "").strip(), (body or "").rstrip()
    if not body.strip():
        raise CanonError("ARG-INVALID", "기록 내용이 비었어요.")
    if not title:
        title = body.strip().splitlines()[0][:40]
    if question_id:
        question_get(uid, question_id)
    r = {"id": new_id("j"), "title": title, "body": body, "source": source, "question_id": question_id or None, "created": now()}
    with tx() as c:
        c.execute("INSERT INTO records (id,user_id,title,body,source,question_id,created) VALUES (?,?,?,?,?,?,?)",
                  (r["id"], uid, r["title"], r["body"], r["source"], r["question_id"], r["created"]))
    return r


def record_get(uid, rid):
    r = con().execute("SELECT * FROM records WHERE user_id=? AND id=?", (uid, rid)).fetchone()
    if not r:
        raise CanonError("RECORD-MISSING", f"기록을 찾을 수 없어요: {rid}")
    d = dict(r)
    d.pop("user_id", None)
    return d


def records(uid, limit=50):
    out = []
    for r in con().execute("SELECT id,title,body,source,question_id,created FROM records WHERE user_id=? ORDER BY created DESC LIMIT ?", (uid, limit)):
        d = dict(r)
        d["excerpt"] = _excerpt(d.pop("body"))
        out.append(d)
    return out


def _excerpt(body, n=60):
    for line in str(body or "").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or t.startswith("-"):
            continue
        return t[:n] + ("…" if len(t) > n else "")
    return ""


# ── 후보 ───────────────────────────────────────────────

def candidates_propose(uid, record_id, question_id, items, origin):
    """items: [{"text","quote"}]. quote 가 원문에 글자 그대로 없으면 버린다. 같은 기록·같은 근거 구간은 다시 넣지 않는다."""
    if origin not in ("ai", "self"):
        raise CanonError("ARG-INVALID", "origin 은 ai 또는 self 예요.")
    rec = record_get(uid, record_id)
    question_get(uid, question_id)
    added, dropped = [], []
    with tx() as c:
        for it in items or []:
            text = (it.get("text") or "").strip()
            quote = (it.get("quote") or "").strip()
            if not text or not quote:
                dropped.append({"text": text, "why": "판단 문장이나 근거 인용이 비었음"})
                continue
            if quote not in rec["body"]:
                dropped.append({"text": text, "why": "근거 인용이 원문에 글자 그대로 없음"})
                continue
            dup = c.execute("SELECT id FROM candidates WHERE user_id=? AND record_id=? AND quote=?", (uid, record_id, quote)).fetchone()
            if dup:
                dropped.append({"text": text, "why": "이미 있는 후보", "id": dup["id"]})
                continue
            cid = new_id("c")
            t = now()
            c.execute("INSERT INTO candidates (id,user_id,question_id,record_id,text,quote,origin,status,edited_by_user,created,updated) "
                      "VALUES (?,?,?,?,?,?,?,?,0,?,?)", (cid, uid, question_id, record_id, text, quote, origin, "pending", t, t))
            added.append(cid)
    return added, dropped


def candidate_get(uid, cid):
    r = con().execute("SELECT * FROM candidates WHERE user_id=? AND id=?", (uid, cid)).fetchone()
    if not r:
        raise CanonError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
    return _cand(r)


def _cand(r):
    d = dict(r)
    d.pop("user_id", None)
    d["edited_by_user"] = bool(d["edited_by_user"])
    return d


def candidates(uid, question_id=None, status=None):
    sql, args = "SELECT * FROM candidates WHERE user_id=?", [uid]
    if question_id:
        sql += " AND question_id=?"; args.append(question_id)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY created"
    return [_cand(r) for r in con().execute(sql, args)]


def candidate_decide(uid, cid, action, text=None):
    """confirm / exclude / pending(되돌리기) / edit. 바꾸기 전 상태·문장을 이력에 남긴다."""
    if action not in ("confirm", "exclude", "pending", "edit"):
        raise CanonError("ARG-INVALID", "action 은 confirm·exclude·pending·edit 중 하나예요.")
    with tx() as c:
        r = c.execute("SELECT * FROM candidates WHERE user_id=? AND id=?", (uid, cid)).fetchone()
        if not r:
            raise CanonError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
        t = now()
        c.execute("INSERT INTO candidate_history (candidate_id,action,prev_status,prev_text,at) VALUES (?,?,?,?,?)",
                  (cid, action, r["status"], r["text"], t))
        if action == "edit":
            new = (text or "").strip()
            if not new:
                raise CanonError("ARG-INVALID", "고친 문장이 비었어요.")
            c.execute("UPDATE candidates SET text=?, edited_by_user=1, updated=? WHERE id=?", (new, t, cid))
        else:
            c.execute("UPDATE candidates SET status=?, updated=? WHERE id=?", (CAND_ACTIONS[action], t, cid))
        return _cand(c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone())


def candidate_history(uid, cid):
    candidate_get(uid, cid)
    return [dict(r) for r in con().execute("SELECT action,prev_status,prev_text,at FROM candidate_history WHERE candidate_id=? ORDER BY id", (cid,))]


# ── 프로필 ──────────────────────────────────────────────

def profile(uid):
    rows = {r["key"]: dict(r) for r in con().execute("SELECT key,value,status,source,updated FROM profile WHERE user_id=?", (uid,))}
    out = {}
    for k, label in PROFILE_KEYS.items():
        r = rows.get(k)
        out[k] = {"label": label, "value": json.loads(r["value"]) if r else ("" if k != "daily_views" else []),
                  "status": r["status"] if r else "empty", "source": r["source"] if r else "", "updated": r["updated"] if r else ""}
    return out


def profile_set(uid, key, value, status, source=""):
    if key not in PROFILE_KEYS:
        raise CanonError("ARG-INVALID", f"프로필에 없는 항목: {key}", "가능한 항목: " + ", ".join(PROFILE_KEYS))
    if status not in STATUSES:
        raise CanonError("ARG-INVALID", f"상태는 {', '.join(STATUSES)} 중 하나예요.")
    if key == "daily_views":
        value = [v.strip() for v in (value.split("|") if isinstance(value, str) else value) if str(v).strip()]
        if len(value) > 3:
            raise CanonError("ARG-INVALID", "매일 볼 것은 최대 3개예요.")
    with tx() as c:
        c.execute("INSERT INTO profile (user_id,key,value,status,source,updated) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,status=excluded.status,source=excluded.source,updated=excluded.updated",
                  (uid, key, json.dumps(value, ensure_ascii=False), status, source or "", now()))
    return profile(uid)[key]


# ── 탭 ─────────────────────────────────────────────────

def tabs(uid):
    r = con().execute("SELECT doc,revision FROM tabs WHERE user_id=?", (uid,)).fetchone()
    return json.loads(r["doc"]) if r else {"tabs": [], "requests": [], "revision": 0}


# ── 상태 한 벌 (화면과 도구가 같은 것을 본다) ─────────────

def next_action(uid):
    qs, recs, cands = questions(uid), records(uid, 5), candidates(uid)
    if not qs and not recs:
        return {"kind": "record", "text": "질문이 아직 없어도 돼요. 최근 경험 메모 1편부터 남겨 보세요."}
    if not recs:
        return {"kind": "record", "text": "기록 1편을 남겨 보세요."}
    used = {c["record_id"] for c in cands}
    fresh = [r for r in recs if r["id"] not in used]
    if qs and fresh:
        return {"kind": "digest", "target": fresh[0]["id"], "text": f"「{fresh[0]['title']}」에서 판단 후보를 뽑아 보세요."}
    pend = [c for c in cands if c["status"] == "pending"]
    if pend:
        return {"kind": "review", "target": pend[0]["id"], "text": f"아직 안 정한 문장 {len(pend)}개를 원문과 대조해 보세요."}
    if not qs:
        return {"kind": "question", "text": "기록을 다시 볼 때 궁금한 점을 질문 하나로 남겨 보세요."}
    return {"kind": "free", "text": "기록 1편 추가 또는 질문 수정 중 하나를 고르세요."}


def state(uid):
    return {
        "generated": now(),
        "profile": profile(uid),
        "questions": questions(uid),
        "tabs": tabs(uid).get("tabs", []),
        "candidates": candidates(uid),
        "journal": records(uid),
        "next": next_action(uid),
    }
