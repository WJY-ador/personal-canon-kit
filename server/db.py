"""저장소 — Postgres (psycopg 3, 동기, 작은 풀). 스키마는 schema.sql, 설계 근거는 data-model.md.

데이터 흐름: 기록(원문 그대로) → 후보(pending) → 사용자 결정(confirm/exclude/edit/pending) → 이력 보존.
사용자 구분은 users.id. 모든 조회·쓰기는 user_id 로 묶이고, 자식 테이블은 복합 FK 로 다른 사용자 행을 참조 못 한다.
연결: 환경변수 DATABASE_URL (Supabase 는 Session pooler, 5432).
"""
import json
import os
import secrets
import datetime as _dt
from contextlib import contextmanager
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

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
MAX_CANDIDATES_PER_RECORD = 3


class CanonError(Exception):
    def __init__(self, code, message, action=""):
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


def now():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().replace(microsecond=0)


def new_id(prefix):
    return f"{prefix}_{secrets.token_urlsafe(8)}"


# ── 연결 ───────────────────────────────────────────────

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise CanonError("DB-CONFIG", "DATABASE_URL 이 없어요.", "서버 환경변수에 Postgres 연결 문자열을 넣으세요.")
        _pool = ConnectionPool(url, min_size=1, max_size=4, kwargs={"row_factory": dict_row}, open=True)
    return _pool


def close():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema():
    """schema.sql 을 적용한다(IF NOT EXISTS 라 몇 번 돌려도 된다)."""
    with pool().connection() as c:
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def ping():
    with pool().connection() as c:
        return c.execute("SELECT 1 AS ok").fetchone()["ok"] == 1


@contextmanager
def tx():
    with pool().connection() as c:
        with c.transaction():
            yield c


def _one(c, sql, args=()):
    return c.execute(sql, args).fetchone()


def _all(c, sql, args=()):
    return c.execute(sql, args).fetchall()


def _iso(d):
    """timestamptz → ISO 문자열. JSON 으로 나갈 행은 전부 이걸 거친다."""
    return {k: (v.isoformat() if isinstance(v, _dt.datetime) else v) for k, v in d.items()}


# ── 사용자 ─────────────────────────────────────────────

def user_by_email(email):
    """이메일로 찾고 없으면 만든다. 개발·시험용. 운영은 user_by_subject."""
    email = email.strip().lower()
    with tx() as c:
        r = _one(c, "SELECT * FROM users WHERE email=%s", (email,))
        if r:
            return _iso(r)
        uid = new_id("u")
        c.execute("INSERT INTO users (id,email) VALUES (%s,%s)", (uid, email))
        return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (uid,)))


def user_by_subject(sub, email):
    """인증된 sub 로 먼저 찾는다. 없으면 검증된 이메일로 찾아 sub 를 붙이고, 그것도 없으면 새로 만든다.
    같은 이메일 = 한 사용자(Google·매직링크 어느 쪽이든)."""
    sub = (sub or "").strip()
    email = (email or "").strip().lower()
    if not sub:
        raise CanonError("AUTH-INVALID", "토큰에 사용자 식별자(sub)가 없어요.")
    with tx() as c:
        r = _one(c, "SELECT * FROM users WHERE auth_subject=%s", (sub,))
        if r:
            return _iso(r)
        if not email:
            raise CanonError("AUTH-INVALID", "처음 연결할 땐 검증된 이메일이 필요해요.")
        r = _one(c, "SELECT * FROM users WHERE email=%s", (email,))
        if r:
            if r["auth_subject"] and r["auth_subject"] != sub:
                raise CanonError("AUTH-CONFLICT", "이 이메일은 다른 로그인 계정에 이미 연결돼 있어요.")
            c.execute("UPDATE users SET auth_subject=%s WHERE id=%s", (sub, r["id"]))
            return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (r["id"],)))
        uid = new_id("u")
        c.execute("INSERT INTO users (id,email,auth_subject) VALUES (%s,%s,%s)", (uid, email, sub))
        return _iso(_one(c, "SELECT * FROM users WHERE id=%s", (uid,)))


def user_get(uid):
    with tx() as c:
        r = _one(c, "SELECT id,email,created FROM users WHERE id=%s", (uid,))
    if not r:
        raise CanonError("AUTH-INVALID", "사용자를 찾을 수 없어요. 다시 로그인하세요.")
    return _iso(r)


def user_delete(uid):
    """계정 삭제 = 그 사용자 데이터 전체 삭제(cascade)."""
    with tx() as c:
        c.execute("DELETE FROM users WHERE id=%s", (uid,))


# ── 질문 ───────────────────────────────────────────────

def question_add(uid, text):
    text = (text or "").strip()
    if not text:
        raise CanonError("ARG-INVALID", "질문 문장이 비었어요.")
    qid = new_id("q")
    with tx() as c:
        c.execute("INSERT INTO questions (id,user_id,text) VALUES (%s,%s,%s)", (qid, uid, text))
        return _iso(_one(c, "SELECT id,text,status,created FROM questions WHERE id=%s", (qid,)))


def questions(uid):
    with tx() as c:
        return [_iso(r) for r in _all(c, "SELECT id,text,status,created FROM questions WHERE user_id=%s ORDER BY created", (uid,))]


def question_get(uid, qid):
    with tx() as c:
        r = _one(c, "SELECT id,text,status,created FROM questions WHERE user_id=%s AND id=%s", (uid, qid))
    if not r:
        raise CanonError("ARG-INVALID", f"질문을 찾을 수 없어요: {qid}", "question_list 로 질문 ID 를 확인하세요.")
    return _iso(r)


# ── 기록 ───────────────────────────────────────────────

def record_add(uid, title, body, question_id=None, source="대화에서 입력"):
    """body 는 받은 그대로 저장한다. 공백도 안 다듬는다."""
    title = (title or "").strip()
    body = body or ""
    if not body.strip():
        raise CanonError("ARG-INVALID", "기록 내용이 비었어요.")
    if not title:
        title = body.strip().splitlines()[0][:40]
    if question_id:
        question_get(uid, question_id)
    rid = new_id("r")
    with tx() as c:
        c.execute("INSERT INTO records (id,user_id,title,body,source,question_id) VALUES (%s,%s,%s,%s,%s,%s)",
                  (rid, uid, title, body, source, question_id or None))
        return _iso(_one(c, "SELECT id,title,body,source,question_id,created FROM records WHERE id=%s", (rid,)))


def record_get(uid, rid):
    with tx() as c:
        r = _one(c, "SELECT id,title,body,source,question_id,created FROM records WHERE user_id=%s AND id=%s AND deleted_at IS NULL", (uid, rid))
    if not r:
        raise CanonError("RECORD-MISSING", f"기록을 찾을 수 없어요: {rid}")
    return _iso(r)


def records(uid, limit=50):
    out = []
    with tx() as c:
        rows = _all(c, "SELECT id,title,body,source,question_id,created FROM records WHERE user_id=%s AND deleted_at IS NULL "
                       "ORDER BY created DESC LIMIT %s", (uid, limit))
    for r in rows:
        d = _iso(r)
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

def _occurrences(body, quote):
    """겹치는 출현까지 센다. 코드포인트 오프셋."""
    out, i = [], body.find(quote)
    while i != -1:
        out.append(i)
        i = body.find(quote, i + 1)
    return out


def candidates_propose(uid, record_id, question_id, items, origin):
    """items: [{"text","quote"}]. quote 는 원문에 글자 그대로 **한 곳**에만 있어야 저장한다.
    0곳 → 버림, 2곳 이상 → 더 길게 인용하라고 버림. 같은 구간 재제출은 중복. 기록당 누적 3개까지."""
    if origin not in ("ai", "self"):
        raise CanonError("ARG-INVALID", "origin 은 ai 또는 self 예요.")
    rec = record_get(uid, record_id)
    question_get(uid, question_id)
    body = rec["body"]
    added, dropped = [], []
    with tx() as c:
        have = _one(c, "SELECT count(*) AS n FROM candidates WHERE user_id=%s AND record_id=%s", (uid, record_id))["n"]
        for it in items or []:
            text = (it.get("text") or "").strip()
            quote = it.get("quote") or ""
            if not text or not quote.strip():
                dropped.append({"text": text, "why": "판단 문장이나 근거 인용이 비었음"})
                continue
            pos = _occurrences(body, quote)
            if not pos:
                dropped.append({"text": text, "why": "근거 인용이 원문에 글자 그대로 없음"})
                continue
            if len(pos) > 1:
                dropped.append({"text": text, "why": f"원문에 같은 구간이 {len(pos)}곳 — 앞뒤를 포함해 더 길게 인용하세요"})
                continue
            start, end = pos[0], pos[0] + len(quote)
            dup = _one(c, "SELECT id FROM candidates WHERE user_id=%s AND record_id=%s AND quote_start=%s AND quote_end=%s",
                       (uid, record_id, start, end))
            if dup:
                dropped.append({"text": text, "why": "이미 있는 후보", "id": dup["id"]})
                continue
            if have >= MAX_CANDIDATES_PER_RECORD:
                dropped.append({"text": text, "why": f"기록 1편당 후보는 {MAX_CANDIDATES_PER_RECORD}개까지예요"})
                continue
            cid = new_id("c")
            c.execute("INSERT INTO candidates (id,user_id,question_id,record_id,text,quote,quote_start,quote_end,origin) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (cid, uid, question_id, record_id, text, quote, start, end, origin))
            have += 1
            added.append(cid)
    return added, dropped


_CAND_COLS = "id,question_id,record_id,text,quote,quote_start,quote_end,origin,decision AS status,edited_by_user,created,updated"


def candidate_get(uid, cid):
    with tx() as c:
        r = _one(c, f"SELECT {_CAND_COLS} FROM candidates WHERE user_id=%s AND id=%s", (uid, cid))
    if not r:
        raise CanonError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
    return _iso(r)


def candidates(uid, question_id=None, status=None):
    """status 인자는 도구 계약 이름. 내부 컬럼은 decision. 지워진 기록의 후보는 빠진다."""
    sql = (f"SELECT {_CAND_COLS} FROM candidates c WHERE c.user_id=%s "
           "AND EXISTS (SELECT 1 FROM records r WHERE r.id=c.record_id AND r.deleted_at IS NULL)")
    args = [uid]
    if question_id:
        sql += " AND c.question_id=%s"; args.append(question_id)
    if status:
        sql += " AND c.decision=%s"; args.append(status)
    sql += " ORDER BY c.created, c.quote_start, c.id"
    with tx() as c:
        return [_iso(r) for r in _all(c, sql, args)]


def candidate_decide(uid, cid, action, text=None):
    """confirm / exclude / pending(확정 취소) / edit / edit_confirm(웹 전용). 바꾸기 전 결정·문장을 이력에 남긴다.
    같은 결정 반복은 no-op. 확정된 문장을 고치면 pending 으로 돌아간다(새 문장 확정은 별도 결정).
    edit_confirm 은 edit → confirm 을 한 트랜잭션으로 한다(이력 두 줄). 같은 문장 + 이미 confirmed 면 no-op."""
    if action not in ("confirm", "exclude", "pending", "edit", "edit_confirm"):
        raise CanonError("ARG-INVALID", "action 은 confirm·exclude·pending·edit 중 하나예요.")
    with tx() as c:
        r = _one(c, "SELECT * FROM candidates WHERE user_id=%s AND id=%s FOR UPDATE", (uid, cid))
        if not r:
            raise CanonError("ARG-INVALID", f"후보를 찾을 수 없어요: {cid}")
        if action == "edit_confirm":
            new = (text or "").strip()
            if not new:
                raise CanonError("ARG-INVALID", "고친 문장이 비었어요.")
            decision = r["decision"]
            if new != r["text"]:
                c.execute("INSERT INTO candidate_history (candidate_id,action,prev_decision,prev_text) VALUES (%s,%s,%s,%s)",
                          (cid, "edit", decision, r["text"]))
                c.execute("UPDATE candidates SET text=%s, edited_by_user=true, decision='pending', updated=now() WHERE id=%s", (new, cid))
                decision = "pending"
            if decision != "confirmed":
                c.execute("INSERT INTO candidate_history (candidate_id,action,prev_decision,prev_text) VALUES (%s,%s,%s,%s)",
                          (cid, "confirm", decision, new))
                c.execute("UPDATE candidates SET decision='confirmed', updated=now() WHERE id=%s", (cid,))
        elif action == "edit":
            new = (text or "").strip()
            if not new:
                raise CanonError("ARG-INVALID", "고친 문장이 비었어요.")
            if new == r["text"]:
                return candidate_get(uid, cid)
            c.execute("INSERT INTO candidate_history (candidate_id,action,prev_decision,prev_text) VALUES (%s,%s,%s,%s)",
                      (cid, action, r["decision"], r["text"]))
            c.execute("UPDATE candidates SET text=%s, edited_by_user=true, decision='pending', updated=now() WHERE id=%s", (new, cid))
        else:
            target = CAND_ACTIONS[action]
            if target == r["decision"]:
                return candidate_get(uid, cid)
            c.execute("INSERT INTO candidate_history (candidate_id,action,prev_decision,prev_text) VALUES (%s,%s,%s,%s)",
                      (cid, action, r["decision"], r["text"]))
            c.execute("UPDATE candidates SET decision=%s, updated=now() WHERE id=%s", (target, cid))
    return candidate_get(uid, cid)


def candidate_history(uid, cid):
    candidate_get(uid, cid)
    with tx() as c:
        rows = _all(c, "SELECT action,prev_decision AS prev_status,prev_text,at FROM candidate_history WHERE candidate_id=%s ORDER BY id", (cid,))
    return [_iso(r) for r in rows]


# ── 프로필 ──────────────────────────────────────────────

def profile(uid):
    with tx() as c:
        rows = {r["key"]: r for r in _all(c, "SELECT key,value,status,source,updated FROM profile WHERE user_id=%s", (uid,))}
    out = {}
    for k, label in PROFILE_KEYS.items():
        r = rows.get(k)
        out[k] = {"label": label, "value": r["value"] if r else ("" if k != "daily_views" else []),
                  "status": r["status"] if r else "empty", "source": r["source"] if r else "",
                  "updated": r["updated"].isoformat() if r else ""}
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
        c.execute("INSERT INTO profile (user_id,key,value,status,source,updated) VALUES (%s,%s,%s,%s,%s,now()) "
                  "ON CONFLICT (user_id,key) DO UPDATE SET value=excluded.value,status=excluded.status,source=excluded.source,updated=now()",
                  (uid, key, json.dumps(value, ensure_ascii=False), status, source or ""))
    return profile(uid)[key]


# ── 탭 ─────────────────────────────────────────────────

def tabs(uid):
    with tx() as c:
        r = _one(c, "SELECT doc,revision FROM tabs WHERE user_id=%s", (uid,))
    return r["doc"] if r else {"tabs": [], "requests": [], "revision": 0}


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
        "generated": now().isoformat(),
        "profile": profile(uid),
        "questions": questions(uid),
        "tabs": tabs(uid).get("tabs", []),
        "candidates": candidates(uid),
        "journal": records(uid),
        "next": next_action(uid),
    }
