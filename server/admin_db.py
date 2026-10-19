"""운영 경계(β PR 3) — 운영 화면(/admin)·운영 POST·/app 관리자 경로의 DB 접근은 전부 이 모듈을 거친다.

규칙:
- 읽기는 허용표 ALLOW 의 키로만 한다. 키 = 데이터셋/조회, 값 = 고정 SQL · 허용 열 · 조회자 조건 · 동의 조건.
  사용자 입력은 열 이름이 아니라 이 열거 키로만 받는다. SQL 은 리터럴 + 바인드 파라미터 — 열·테이블 이름을 보간하지 않는다.
- 「본문」(items.title·body·data, 기록 원문, questions.text, tabs.doc, object_defs.history, 후보 문장)은 조회하지 않는다.
- tickets.text·admin_note 는 문의 상세 조회(tickets.detail)에서만 읽는다. 예외는 이행용 tickets.legacy_inbox 하나(P5 에서 삭제).
  object_defs.label·field_defs.label·field_defs.options 는 이름 공유 분기(consent=struct_names)에서만 읽는다.
- 별표(전체 열) 조회 금지. 시험(test_tools 「11) 운영 경계」)이 이 파일과 ALLOW 를 정적으로 검사한다.
쓰기는 db 의 트랜잭션 함수를 얇게 감싼다(규칙·감사는 db 에 있다).
"""
import datetime as _dt

import db

VIEWER_ADMIN = "admin"

# key → sql(리터럴 하나) · cols(응답 열 = 행 키) · viewer(조회자 role) · consent(필요한 선택 동의 kind, 없으면 None)
ALLOW = {
    # /admin/setup 사용자 표. consented = 현재 처리방침 버전 동의 여부(버전이 없으면 NULL).
    "setup.users": {
        "sql": "SELECT u.id,u.email,u.created,u.role,u.blocked_at,u.web_revoked_before,"
               "CASE WHEN %s::text IS NULL THEN NULL ELSE EXISTS (SELECT 1 FROM agreements g WHERE g.user_id=u.id "
               "AND g.kind='privacy' AND g.version=%s) END AS consented FROM users u ORDER BY u.created, u.id",
        "cols": ("id", "email", "created", "role", "blocked_at", "web_revoked_before", "consented"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # /admin/setup 감사 로그 — 실행자·대상 이메일(지워진 계정이면 NULL).
    "audit.list": {
        "sql": "SELECT a.id,a.at,a.action,a.actor_id,a.target_id,ua.email AS actor_email,ut.email AS target_email "
               "FROM admin_audit a LEFT JOIN users ua ON ua.id=a.actor_id LEFT JOIN users ut ON ut.id=a.target_id "
               "ORDER BY a.at DESC, a.id DESC LIMIT %s",
        "cols": ("id", "at", "action", "actor_id", "target_id", "actor_email", "target_email"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 관리자 띠 — open = 아직 확인 안 한 문의 · privacy_open = 종결 안 된 개인정보 요청.
    "tickets.counts": {
        "sql": "SELECT count(*) FILTER (WHERE status='open') AS open, "
               "count(*) FILTER (WHERE kind='privacy' AND status NOT IN ('done','wontfix')) AS privacy_open FROM tickets",
        "cols": ("open", "privacy_open"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 문의 상세 — 원문(text)·운영자 메모(admin_note)를 읽는 유일한 조회.
    "tickets.detail": {
        "sql": "SELECT t.id,t.kind,t.text,t.status,t.via,t.page,t.tool,t.ua_family,t.hint,t.admin_note,"
               "t.created,t.updated,t.closed_at,t.reply,t.reply_at,t.public_updated,u.email,"
               "CASE WHEN t.kind='privacy' THEN (SELECT min(p.received) FROM privacy_log p WHERE p.ticket_id=t.id) END AS privacy_received "
               "FROM tickets t JOIN users u ON u.id=t.user_id WHERE t.id=%s",
        "cols": ("id", "kind", "text", "status", "via", "page", "tool", "ua_family", "hint", "admin_note", "created", "updated",
                 "closed_at", "reply", "reply_at", "public_updated", "email", "privacy_received"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 문의 이력 전부(공개·내부) — 운영자 레코드 페이지용. 값은 열거값(상태·kind·사유)뿐.
    "tickets.history": {
        "sql": "SELECT h.id,h.at,h.actor_id,u.email AS actor_email,h.visibility,h.kind,h.from_val,h.to_val,h.reason "
               "FROM ticket_history h LEFT JOIN users u ON u.id=h.actor_id WHERE h.ticket_id=%s ORDER BY h.at, h.id",
        "cols": ("id", "at", "actor_id", "actor_email", "visibility", "kind", "from_val", "to_val", "reason"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 이행용(P5 삭제) — /app 관리자 「문의함」(GET /api/feedback 관리자 분기) 최근 100건. 원문·메모를 그리는 옛 화면이라 text·admin_note 를 읽는다.
    "tickets.legacy_inbox": {
        "sql": "SELECT t.id,t.kind,t.text,t.status,t.via,t.page,t.tool,t.hint,t.admin_note,"
               "t.created,t.updated,t.closed_at,u.email FROM tickets t JOIN users u ON u.id=t.user_id ORDER BY t.created DESC LIMIT %s",
        "cols": ("id", "kind", "text", "status", "via", "page", "tool", "hint", "admin_note", "created", "updated", "closed_at",
                 "email"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 이름 공유 분기 — 현재 이름(label·options)만. 선택 동의(P2)·/admin/schema(P4)에서 쿼리를 단다. 지금은 선언만(실행 불가).
    "schema.names": {
        "sql": None,
        "cols": ("object_defs.label", "field_defs.label", "field_defs.options"),
        "viewer": VIEWER_ADMIN, "consent": "struct_names",
    },
}

AUDIT_LIST = db.AUDIT_LIST
TICKET_LEGACY_LIST = db.TICKET_ADMIN_LIST   # /app 관리자 「문의함」 최근 건수(이행용, P5 삭제)


def _check(key, role):
    spec = ALLOW.get(key)
    if spec is None or spec["sql"] is None:
        raise db.CrmError("ARG-INVALID", "그런 조회는 없어요.")
    if role != spec["viewer"]:
        raise db.CrmError("FORBIDDEN", "관리자만 볼 수 있어요.")
    if spec["consent"] is not None:   # 동의 분기는 P2 의 선택 동의 저장이 생긴 뒤 연결한다
        raise db.CrmError("FORBIDDEN", "동의 조건이 있는 조회예요.")
    return spec["sql"]


def _rows(key, role, args=()):
    sql = _check(key, role)
    with db.tx() as c:
        return db._all(c, sql, args)


def _row(key, role, args=()):
    sql = _check(key, role)
    with db.tx() as c:
        return db._one(c, sql, args)


# ── 읽기 ────────────────────────────────────────────────

def users(role):
    """/admin/setup 사용자 표. datetime 그대로."""
    ver = db.CONSENT_VERSIONS.get("privacy")
    return _rows("setup.users", role, (ver, ver))


def audit(role, limit=AUDIT_LIST):
    """감사 로그 최근 limit 건. datetime 그대로."""
    return _rows("audit.list", role, (limit,))


def ticket_counts(role):
    """관리자 띠 숫자 {open, privacy_open}."""
    return dict(_row("tickets.counts", role))


def tickets_legacy_inbox(role, limit=TICKET_LEGACY_LIST):
    """이행용(P5 삭제) — /app 관리자 「문의함」 전체 사용자 문의 최근 limit 건(원문·메모·이메일 포함)."""
    return [db._iso(r) for r in _rows("tickets.legacy_inbox", role, (limit,))]


def ticket_detail(role, tid):
    """문의 한 건 전부(원문·메모·이메일). 없으면 NOT-FOUND."""
    r = _row("tickets.detail", role, (str(tid),))
    if not r:
        raise db.CrmError("NOT-FOUND", "그 문의를 찾지 못했어요.", "새로 고침한 뒤 다시 해 보세요.")
    out = db._iso(r)
    if out["privacy_received"] is not None:   # date — JSON 으로 나가게 ISO 날짜 문자열
        out["privacy_received"] = out["privacy_received"].isoformat()
    return out


def ticket_history(role, tid):
    """문의 이력 전부(공개·내부 배지용 visibility 포함), 오래된 순."""
    return [db._iso(r) for r in _rows("tickets.history", role, (str(tid),))]


def privacy_deadline(received, today=None):
    """개인정보 요청 기한 — (기한 날짜, 초과 여부). received = 받은 KST 날짜(date·ISO 문자열) 또는 None.
    기한 = received + 10일. 오늘(KST)이 기한을 지났으면 초과."""
    if received is None:
        return None, False
    if isinstance(received, str):
        received = _dt.date.fromisoformat(received)
    due = received + _dt.timedelta(days=db.PRIVACY_DAYS)
    today = today or _dt.datetime.now(db.KST).date()
    return due, today > due


# ── 쓰기(db 트랜잭션 함수를 감싼다) ────────────────────

def act(actor_id, op, target_id, role=None):
    """/admin POST 한 건(부여·회수·끊기·차단·해제) — db.admin_act(한 트랜잭션·감사·실행자 재조회)."""
    return db.admin_act(actor_id, op, target_id, role)


def _admin_only(role):
    if role != VIEWER_ADMIN:
        raise db.CrmError("FORBIDDEN", "운영자만 할 수 있어요.")


def ticket_status(role, actor_id, tid, status):
    """문의 상태 바꾸기(공개 사건) → 바뀐 상세(커밋 뒤 다시 읽음)."""
    _admin_only(role)
    db.ticket_set_status(actor_id, tid, status)
    return ticket_detail(role, tid)


def ticket_note(role, actor_id, tid, note):
    """운영자 메모(내부 사건, public_updated 그대로) → 바뀐 상세."""
    _admin_only(role)
    db.ticket_set_note(actor_id, tid, note)
    return ticket_detail(role, tid)


def ticket_reply(role, actor_id, tid, reply):
    """공개 답변 쓰기·고치기·지우기(빈 문자열) → 바뀐 상세."""
    _admin_only(role)
    db.ticket_set_reply(actor_id, tid, reply)
    return ticket_detail(role, tid)


def ticket_reclassify(role, actor_id, tid, kind, reason=None):
    """문의 종류 바꾸기(내부 사건). privacy 에서 뺄 때 사유 필수 → 바뀐 상세."""
    _admin_only(role)
    db.ticket_reclassify(actor_id, tid, kind, reason)
    return ticket_detail(role, tid)
