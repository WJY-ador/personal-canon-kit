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
    # ── 운영 화면 P4(개요·스키마·리포트) ─────────────────────
    # 오브젝트 id·필드 key 는 사용자가 지은 이름일 수 있어 그대로 꺼내지 않는다 — md5 불투명 참조(*_ref)로만 묶고 화면에 쓰지 않는다.
    # 상태 띠 — args (privacy 버전, struct_names 버전, 임박 기준일, 개인정보 기한일, 개인정보 기한일). 미종결 = closed_at 없음.
    "overview.band": {
        "sql": "SELECT (SELECT count(*) FROM users) AS users, "
               "(SELECT count(DISTINCT g.user_id) FROM agreements g WHERE g.kind='privacy' AND g.version=%s) AS consented, "
               "(SELECT count(*) FROM optional_consents oc WHERE oc.kind='struct_names' AND oc.state='granted' AND oc.version=%s) AS names, "
               "(SELECT count(*) FROM tickets t WHERE t.closed_at IS NULL) AS open_tickets, "
               "(SELECT count(DISTINCT t.id) FROM tickets t JOIN privacy_log p ON p.ticket_id=t.id WHERE t.kind='privacy' "
               "AND t.closed_at IS NULL AND p.received + %s::int - (now() AT TIME ZONE 'Asia/Seoul')::date BETWEEN 0 AND %s::int) AS privacy_soon, "
               "(SELECT count(DISTINCT t.id) FROM tickets t JOIN privacy_log p ON p.ticket_id=t.id WHERE t.kind='privacy' "
               "AND t.closed_at IS NULL AND p.received + %s::int < (now() AT TIME ZONE 'Asia/Seoul')::date) AS privacy_over",
        "cols": ("users", "consented", "names", "open_tickets", "privacy_soon", "privacy_over"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 사용자별 마지막 활동일(usage_daily 의 가장 늦은 day, 전체 기간). user_id '' = 미연결.
    "overview.last_active": {
        "sql": "SELECT user_id, max(day) AS last_day FROM usage_daily GROUP BY user_id ORDER BY user_id",
        "cols": ("user_id", "last_day"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 리포트 「사용자」 — 가입 KST 날짜가 [from, to] 인 사용자. args (privacy 버전, struct_names 버전, from, to).
    "report.users": {
        "sql": "SELECT u.id, u.created, u.role, u.blocked_at IS NOT NULL AS blocked, "
               "EXISTS (SELECT 1 FROM agreements g WHERE g.user_id=u.id AND g.kind='privacy' AND g.version=%s) AS consented, "
               "EXISTS (SELECT 1 FROM optional_consents oc WHERE oc.user_id=u.id AND oc.kind='struct_names' AND oc.state='granted' "
               "AND oc.version=%s) AS names, (SELECT max(d.day) FROM usage_daily d WHERE d.user_id=u.id) AS last_day "
               "FROM users u WHERE (u.created AT TIME ZONE 'Asia/Seoul')::date BETWEEN %s AND %s ORDER BY u.created, u.id",
        "cols": ("id", "created", "role", "blocked", "consented", "names", "last_day"),
        "viewer": VIEWER_ADMIN, "consent": None,
        "time": "가입 시각(users.created) 의 KST 날짜",
        "dims": {"signup_month": "가입 월", "signup_week": "가입 주", "consent": "처리방침 현재 판 동의", "names": "구조 이름 공유",
                 "role": "역할", "blocked": "차단", "active14": "14일 안 활동"},
        "filters": {"consent": ("yes", "no"), "names": ("yes", "no"), "role": ("user", "admin"), "blocked": ("yes", "no"),
                    "active14": ("yes", "no")},
        "measures": {"users": {"label": "사용자 수", "src": "users", "time": "가입 시각(KST 날짜)",
                               "missing": "지운 계정은 없다(행째 삭제)", "agg": "행 수"}},
    },
    # 리포트 「사용량」 — usage_daily 원행. args (from, to). user_id 는 연결 여부·사용자 수 세기에만 쓴다.
    "report.usage": {
        "sql": "SELECT day, user_id, name, code, n FROM usage_daily WHERE day BETWEEN %s AND %s ORDER BY day, user_id, name, code",
        "cols": ("day", "user_id", "name", "code", "n"),
        "viewer": VIEWER_ADMIN, "consent": None,
        "time": "usage_daily.day(호출한 KST 날짜)",
        "dims": {"day": "일", "week": "주(월요일)", "month": "월", "name": "도구·화면", "code": "결과 코드", "result": "정상·오류",
                 "linked": "사용자 연결"},
        "filters": {"result": ("ok", "error"), "linked": ("yes", "no")},
        "measures": {"calls": {"label": "호출 수", "src": "usage_daily.n", "time": "호출한 KST 날짜(day)",
                               "missing": "5분 주기 기록 전 재시작분·v2 전 기록은 없다", "agg": "sum(n)"},
                     "users": {"label": "사용자 수", "src": "usage_daily.user_id", "time": "호출한 KST 날짜(day)",
                               "missing": "미연결('')은 세지 않는다", "agg": "count(distinct user_id ≠ '')"}},
    },
    # 리포트 「구조」 — 사용자 × 적용된 오브젝트(revision>0, 보관 포함) × 필드. 이름 없음. 사용자에 오브젝트가 없으면 object_ref NULL 한 행.
    "report.structure": {
        "sql": "SELECT u.id AS user_id, md5(o.id) AS object_ref, o.semantic_kind, o.template_slot AS object_slot, "
               "o.archived_at AS object_archived, f.value_type, f.required, f.multiple, f.target_kind, f.template_slot AS field_slot, "
               "f.archived_at AS field_archived FROM users u LEFT JOIN object_defs o ON o.user_id=u.id AND o.revision > 0 "
               "LEFT JOIN field_defs f ON f.user_id=o.user_id AND f.object_id=o.id ORDER BY u.created, u.id, o.created_at, o.id, f.position, f.key",
        "cols": ("user_id", "object_ref", "semantic_kind", "object_slot", "object_archived", "value_type", "required", "multiple",
                 "target_kind", "field_slot", "field_archived"),
        "viewer": VIEWER_ADMIN, "consent": None,
        "time": None,   # 지금 상태(스냅샷) — 기간 없음
        "dims": {"semantic_kind": "오브젝트 종류", "origin": "오브젝트 출처", "value_type": "필드 타입", "field_origin": "필드 출처",
                 "required": "필수", "multiple": "다중", "target": "관계 대상", "archived": "보관",
                 "objects_bucket": "사용자별 오브젝트 수 구간", "fields_bucket": "사용자별 필드 수 구간"},
        "user_dims": ("objects_bucket", "fields_bucket"),   # 사용자 단위 차원 — 서로끼리만 묶고 필터와 같이 쓰지 않는다
        "filters": {"semantic_kind": ("collection", "event", "principle"), "origin": ("template", "user"),
                    "value_type": ("text", "number", "boolean", "date", "select", "text_list", "reference"),
                    "field_origin": ("template", "user"), "required": ("yes", "no"), "multiple": ("yes", "no"),
                    "target": ("item", "record", "candidate", "none"), "archived": ("yes", "no")},
        "measures": {"users": {"label": "사용자 수", "src": "users · object_defs", "time": "지금",
                               "missing": "행 단위: 오브젝트 없는 사용자 제외 · 구간: 전원(0 구간 포함)",
                               "agg": "행 단위 count(distinct user) · 구간 행 수"},
                     "objects": {"label": "오브젝트 수", "src": "object_defs(revision>0)", "time": "지금",
                                 "missing": "적용 전 제안(revision 0) 제외 · 필드 필터를 걸면 그 필드가 있는 오브젝트만",
                                 "agg": "행 단위 count(distinct object) · 구간 합(보관 제외)"},
                     "fields": {"label": "필드 수", "src": "field_defs", "time": "지금", "missing": "필드 없는 오브젝트는 0",
                                "agg": "행 단위 행 수 · 구간 합(보관 제외)"}},
    },
    # 리포트 「문의」 — 원문·메모 없이 분류 열만. args (from, to).
    "report.tickets": {
        "sql": "SELECT t.kind, t.status, t.page, t.via, t.created, t.closed_at FROM tickets t "
               "WHERE (t.created AT TIME ZONE 'Asia/Seoul')::date BETWEEN %s AND %s ORDER BY t.created, t.id",
        "cols": ("kind", "status", "page", "via", "created", "closed_at"),
        "viewer": VIEWER_ADMIN, "consent": None,
        "time": "접수 시각(tickets.created) 의 KST 날짜",
        "dims": {"kind": "종류", "status": "상태", "page": "화면", "via": "경로", "created_month": "접수 월", "created_week": "접수 주",
                 "closed_month": "종결 월", "closed_week": "종결 주"},
        "filters": {"kind": ("bug", "request", "question", "privacy"), "status": ("open", "ack", "doing", "done", "wontfix"),
                    "page": ("today", "question", "record", "structure", "map", "review", "start", "help", "privacy", "other"),
                    "via": ("web", "mcp")},
        "measures": {"tickets": {"label": "문의 수", "src": "tickets", "time": "접수 시각(KST 날짜)",
                                 "missing": "종결 180일 뒤 파기된 문의는 없다", "agg": "행 수"},
                     "closed": {"label": "종결 수", "src": "tickets.closed_at", "time": "접수 시각(KST 날짜)",
                                "missing": "미종결은 세지 않는다", "agg": "count(closed_at)"}},
    },
    # 사용자 한 명의 구조 트리 — 이름 없음. args (user_id).
    "schema.tree": {
        "sql": "SELECT md5(o.id) AS object_ref, o.semantic_kind, o.template_slot AS object_slot, o.archived_at AS object_archived, "
               "md5(f.key) AS field_ref, f.value_type, f.required, f.multiple, f.target_kind, md5(f.target_object_id) AS target_ref, "
               "f.template_slot AS field_slot, f.archived_at AS field_archived FROM object_defs o "
               "LEFT JOIN field_defs f ON f.user_id=o.user_id AND f.object_id=o.id "
               "WHERE o.user_id=%s AND o.revision > 0 ORDER BY o.created_at, o.id, f.position, f.key",
        "cols": ("object_ref", "semantic_kind", "object_slot", "object_archived", "field_ref", "value_type", "required", "multiple",
                 "target_kind", "target_ref", "field_slot", "field_archived"),
        "viewer": VIEWER_ADMIN, "consent": None,
    },
    # 이름 공유 분기 — 그 사용자의 유효 동의(granted · 현재 버전)가 같은 문장 안에서 참일 때만 행이 나온다. args (user_id, 버전).
    "schema.object_names": {
        "sql": "SELECT md5(o.id) AS object_ref, o.label FROM object_defs o WHERE o.user_id=%s AND o.revision > 0 "
               "AND EXISTS (SELECT 1 FROM optional_consents oc WHERE oc.user_id=o.user_id AND oc.kind='struct_names' "
               "AND oc.state='granted' AND oc.version=%s) ORDER BY o.created_at, o.id",
        "cols": ("object_ref", "label"),
        "viewer": VIEWER_ADMIN, "consent": "struct_names",
    },
    # 필드 이름·선택지 이름(label 만 — 선택지 값은 꺼내지 않는다). args (user_id, 버전).
    "schema.field_names": {
        "sql": "SELECT md5(f.object_id) AS object_ref, md5(f.key) AS field_ref, f.label, "
               "jsonb_path_query_array(f.options, '$[*].label') AS option_labels FROM field_defs f "
               "JOIN object_defs o ON o.user_id=f.user_id AND o.id=f.object_id WHERE f.user_id=%s AND o.revision > 0 "
               "AND EXISTS (SELECT 1 FROM optional_consents oc WHERE oc.user_id=f.user_id AND oc.kind='struct_names' "
               "AND oc.state='granted' AND oc.version=%s) ORDER BY o.created_at, o.id, f.position, f.key",
        "cols": ("object_ref", "field_ref", "label", "option_labels"),
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


# ── 문의 리스트(P4-4) ───────────────────────────────────
# 리스트엔 원문·메모·화면·도구·UA 가 없다(원문은 상세 화면에서만). 필터·정렬은 열거 키 → 바인드 파라미터(고정 SQL 하나).
TICKET_LIST_MAX = 200
TICKET_LIST_STATUS = ("active", "all", "closed") + db.TICKET_STATUSES   # active = 미종결(기본)
TICKET_LIST_KIND = ("all",) + db.TICKET_KINDS
TICKET_LIST_SORT = ("deadline", "new", "updated")   # deadline = 개인정보 기한 오름차순(초과 최우선) → 나머지 접수순(기본)
ALLOW["tickets.list"] = {
    "sql": "SELECT t.id,t.kind,t.status,t.via,t.hint,t.created,t.updated,t.closed_at,(t.reply IS NOT NULL) AS has_reply,t.reply_at,"
           "u.email,CASE WHEN t.kind='privacy' THEN (SELECT min(p.received) FROM privacy_log p WHERE p.ticket_id=t.id) END AS privacy_received "
           "FROM tickets t JOIN users u ON u.id=t.user_id "
           "WHERE (%(kind)s = 'all' OR t.kind = %(kind)s) "
           "AND (CASE %(status)s WHEN 'all' THEN true WHEN 'active' THEN t.status NOT IN ('done','wontfix') "
           "WHEN 'closed' THEN t.status IN ('done','wontfix') ELSE t.status = %(status)s END) "
           "ORDER BY CASE WHEN %(sort)s = 'deadline' AND t.kind = 'privacy' "
           "THEN (SELECT min(p.received) FROM privacy_log p WHERE p.ticket_id=t.id) END ASC NULLS LAST, "
           "CASE WHEN %(sort)s = 'updated' THEN t.updated END DESC NULLS LAST, "
           "CASE WHEN %(sort)s = 'new' THEN t.created END DESC NULLS LAST, t.created ASC, t.id "
           "LIMIT %(lim)s",
    "cols": ("id", "kind", "status", "via", "hint", "created", "updated", "closed_at", "has_reply", "reply_at", "email",
             "privacy_received"),
    "viewer": VIEWER_ADMIN, "consent": None,
}


def tickets_list(role, status="active", kind="all", sort="deadline"):
    """/admin/tickets — (행 ≤ TICKET_LIST_MAX, 넘쳤는지). 모르는 키는 ARG-INVALID(다른 목록으로 대체하지 않는다). datetime·date 그대로."""
    if status not in TICKET_LIST_STATUS or kind not in TICKET_LIST_KIND or sort not in TICKET_LIST_SORT:
        raise db.CrmError("ARG-INVALID", "모르는 필터·정렬이에요.")
    rows = _rows("tickets.list", role, {"status": status, "kind": kind, "sort": sort, "lim": TICKET_LIST_MAX + 1})
    return rows[:TICKET_LIST_MAX], len(rows) > TICKET_LIST_MAX


def privacy_days_left(received, today=None):
    """화면용 — (기한 날짜, 남은 일수(음수 = 초과 일수), 초과 여부). received 없으면 (None, None, False)."""
    due, over = privacy_deadline(received, today)
    if due is None:
        return None, None, False
    return due, (due - (today or _dt.datetime.now(db.KST).date())).days, over


# ── 운영 화면 P4 — 개요 · 스키마 · 리포트 ─────────────────
# 조회는 위 ALLOW 키(평평한 행)만. 묶기·세기는 아래 파이썬이 한다 — SQL 은 고정 문장 + 바인드, 차원·필터는 열거 키로만 받는다.
PRIVACY_DUE_DAYS = db.PRIVACY_DAYS   # 개인정보 요청 처리 기한(받은 날부터) — privacy_deadline 과 같은 상수
PRIVACY_SOON_DAYS = 3        # 기한 임박 = 남은 날 0~3
REPORT_MAX_DAYS = 180        # 리포트 기간 상한(양끝 포함)
REPORT_ROWS = 200            # 결과 행 상한 — 넘으면 「상위 200」
RECENT_DAYS = 14             # 개요 「14일」 창(오늘 포함)
REPORT_DATASETS = {"users": "report.users", "usage": "report.usage", "structure": "report.structure", "tickets": "report.tickets"}
REPORT_DS_LABELS = {"users": "사용자", "usage": "사용량", "structure": "구조", "tickets": "문의"}
# 순서가 있는 차원 — 모든 묶음이 이 차원이면 값 순서로, 아니면 첫 측정 큰 순으로 정렬한다.
_ORDERED_DIMS = frozenset({"day", "week", "month", "signup_month", "signup_week", "created_month", "created_week", "closed_month",
                           "closed_week", "objects_bucket", "fields_bucket"})
OBJECT_BUCKETS = ((0, 0, "0"), (1, 1, "1"), (2, 5, "2–5"), (6, 10, "6–10"), (11, None, "11+"))
FIELD_BUCKETS = ((0, 0, "0"), (1, 10, "1–10"), (11, 30, "11–30"), (31, 60, "31–60"), (61, None, "61+"))
# 값 표시 이름(열거 값 → 화면 글자). 없는 값은 그대로 보인다.
VALUE_LABELS = {
    "consent": {"yes": "동의", "no": "미동의"}, "names": {"yes": "동의", "no": "안 함"},
    "blocked": {"yes": "차단", "no": "아님"}, "active14": {"yes": "활동", "no": "없음"},
    "role": {"user": "사용자", "admin": "관리자"},
    "result": {"ok": "정상", "error": "오류"}, "linked": {"yes": "연결", "no": "미연결"},
    "origin": {"template": "템플릿", "user": "사용자"}, "field_origin": {"template": "템플릿", "user": "사용자"},
    "required": {"yes": "필수", "no": "선택"}, "multiple": {"yes": "다중", "no": "단일"}, "archived": {"yes": "보관", "no": "사용 중"},
    "target": {"item": "항목", "record": "기록", "candidate": "후보", "none": "관계 아님"},
    "kind": {"bug": "버그", "request": "요청", "question": "질문", "privacy": "개인정보 요청"},
    "status": {"open": "접수", "ack": "확인", "doing": "처리중", "done": "완료", "wontfix": "보류"},
}
# 프리셋 — 개요 표와 같은 정의(같은 데이터셋·필터·창). days 가 있으면 오늘까지 그 일수(from·to 로 바꾼다).
REPORT_PRESETS = (
    ("errors14", "오류 14일", {"ds": "usage", "g1": "name", "g2": "code", "result": "error", "days": RECENT_DAYS}),
    ("linked14", "연결·미연결 호출 14일", {"ds": "usage", "g1": "day", "g2": "linked", "days": RECENT_DAYS}),
    ("defs", "정의 분포", {"ds": "structure", "g1": "objects_bucket", "g2": "fields_bucket"}),
    ("types", "필드 타입 × 출처", {"ds": "structure", "g1": "value_type", "g2": "field_origin", "archived": "no"}),
    ("consent", "사용자 동의", {"ds": "users", "g1": "consent", "g2": "names"}),
    ("tickets", "문의 종류 × 상태", {"ds": "tickets", "g1": "kind", "g2": "status"}),
)
VALUE_TYPES = db.VALUE_TYPES
KST = db.KST


def _bad(msg):
    return db.CrmError("ARG-INVALID", msg)


def kst_today():
    return _dt.datetime.now(KST).date()


def _kdate(ts):
    return ts.astimezone(KST).date() if ts else None


def _month(d):
    return d.strftime("%Y-%m") if d else None


def _week(d):
    return (d - _dt.timedelta(days=d.weekday())).isoformat() if d else None


def _yn(v):
    return "yes" if v else "no"


def _bucket(n, table):
    for lo, hi, label in table:
        if n >= lo and (hi is None or n <= hi):
            return label
    return table[-1][2]


def _named_rows(key, role, subject_uid):
    """이름 공유 분기 — 조회자 admin + 대상 사용자의 유효 선택 동의. 동의는 같은 문장의 EXISTS 가 본다(동의가 없으면 0행)."""
    spec = ALLOW.get(key)
    if spec is None or spec["sql"] is None or spec["consent"] is None:
        raise _bad("그런 조회는 없어요.")
    if role != spec["viewer"]:
        raise db.CrmError("FORBIDDEN", "관리자만 볼 수 있어요.")
    with db.tx() as c:
        return db._all(c, spec["sql"], (subject_uid, db.OPTIONAL_VERSIONS[spec["consent"]]))


# ── 읽기: 평평한 행 ────────────────────────────────────

def overview_band(role):
    """개요 상태 띠 숫자."""
    args = (db.CONSENT_VERSIONS.get("privacy"), db.OPTIONAL_VERSIONS["struct_names"], PRIVACY_DUE_DAYS, PRIVACY_SOON_DAYS,
            PRIVACY_DUE_DAYS)
    return dict(_row("overview.band", role, args))


def last_active(role):
    """[{user_id, last_day}] — 전체 기간. user_id '' = 미연결."""
    return _rows("overview.last_active", role)


def report_rows(role, ds, d_from=None, d_to=None):
    """리포트 데이터셋의 원행(평평한 dict). 기간이 없는 데이터셋(structure)은 from·to 를 받지 않는다."""
    key = REPORT_DATASETS.get(ds)
    if key is None:
        raise _bad("그런 데이터셋은 없어요.")
    if ds == "structure":
        return _rows(key, role)
    if d_from is None or d_to is None:
        raise _bad("기간이 필요해요.")
    if ds == "users":
        return _rows(key, role, (db.CONSENT_VERSIONS.get("privacy"), db.OPTIONAL_VERSIONS["struct_names"], d_from, d_to))
    return _rows(key, role, (d_from, d_to))


def schema_tree(role, uid):
    """사용자 한 명의 오브젝트 × 필드(이름 없음)."""
    return _rows("schema.tree", role, (str(uid),))


def schema_names(role, uid):
    """(오브젝트 이름 행, 필드 이름 행) — 그 사용자가 유효 동의자가 아니면 둘 다 빈 목록."""
    return _named_rows("schema.object_names", role, str(uid)), _named_rows("schema.field_names", role, str(uid))


# ── 리포트: 열거 키 검사 → 원행 → 묶기 ───────────────────

def report_spec(ds):
    key = REPORT_DATASETS.get(ds)
    if key is None:
        raise _bad("그런 데이터셋은 없어요.")
    return ALLOW[key]


def report_window(ds, f, t, today=None):
    """from·to(KST 날짜 문자열 또는 None) 검사 → (from, to) date. 기간 없는 데이터셋에 기간을 주면 오류.
    둘 다 없으면 오늘까지 REPORT_MAX_DAYS 일. 하나만 주면 다른 쪽을 상한 안에서 채운다."""
    spec = report_spec(ds)
    if spec["time"] is None:
        if f is not None or t is not None:
            raise _bad("구조는 지금 상태만 봐요 — 기간(from·to)을 넣지 마세요.")
        return None, None
    today = today or kst_today()
    try:
        d_to = _dt.date.fromisoformat(t) if t is not None else today
        d_from = _dt.date.fromisoformat(f) if f is not None else d_to - _dt.timedelta(days=REPORT_MAX_DAYS - 1)
    except (TypeError, ValueError):
        raise _bad("기간은 YYYY-MM-DD(KST 날짜)예요.") from None
    if d_from > d_to:
        raise _bad("from 이 to 보다 늦어요.")
    if (d_to - d_from).days + 1 > REPORT_MAX_DAYS:
        raise _bad("기간은 최대 180일이에요.")
    return d_from, d_to


def report_check(ds, dims, filters):
    """묶기 차원(≤2)·필터(열거 값)가 그 데이터셋의 허용표 안인지. 아니면 ARG-INVALID."""
    spec = report_spec(ds)
    if len(dims) > 2 or len(set(dims)) != len(dims):
        raise _bad("묶기는 서로 다른 차원 2개까지예요.")
    for d in dims:
        if d not in spec["dims"]:
            raise _bad("이 데이터셋에 없는 묶기 차원이에요.")
    for k, v in filters.items():
        if k not in spec["filters"]:
            raise _bad("이 데이터셋에 없는 필터예요.")
        if v not in spec["filters"][k]:
            raise _bad("필터 값이 허용 목록에 없어요.")
    user_dims = set(spec.get("user_dims", ()))
    if user_dims and set(dims) & user_dims:
        if not set(dims) <= user_dims:
            raise _bad("사용자별 구간 차원은 구간 차원끼리만 묶어요.")
        if filters:
            raise _bad("사용자별 구간 차원에는 필터를 걸지 않아요.")
    return spec


def _facts(ds, r, today):
    """원행 하나 → 차원 값 dict(열거 값 또는 날짜 문자열, 해당 없음 None)."""
    if ds == "users":
        d = _kdate(r["created"])
        last = r["last_day"]
        return {"signup_month": _month(d), "signup_week": _week(d), "consent": _yn(r["consented"]), "names": _yn(r["names"]),
                "role": r["role"], "blocked": _yn(r["blocked"]),
                "active14": _yn(last is not None and last >= today - _dt.timedelta(days=RECENT_DAYS - 1))}
    if ds == "usage":
        return {"day": r["day"].isoformat(), "week": _week(r["day"]), "month": _month(r["day"]), "name": r["name"], "code": r["code"],
                "result": "ok" if r["code"] == "OK" else "error", "linked": _yn(r["user_id"] != "")}
    if ds == "tickets":
        c, z = _kdate(r["created"]), _kdate(r["closed_at"])
        return {"kind": r["kind"], "status": r["status"], "page": r["page"], "via": r["via"], "created_month": _month(c),
                "created_week": _week(c), "closed_month": _month(z), "closed_week": _week(z)}
    has_f = r["value_type"] is not None
    return {"semantic_kind": r["semantic_kind"], "origin": "template" if r["object_slot"] else "user",
            "value_type": r["value_type"], "field_origin": ("template" if r["field_slot"] else "user") if has_f else None,
            "required": _yn(r["required"]) if has_f else None, "multiple": _yn(r["multiple"]) if has_f else None,
            "target": (r["target_kind"] or "none") if has_f else None,
            "archived": _yn(r["object_archived"] is not None or (has_f and r["field_archived"] is not None))}


def structure_counts(rows):
    """구조 원행 → {user_id: (오브젝트 수, 필드 수)} — 보관 제외. 오브젝트가 없는 사용자도 (0, 0) 으로 들어 있다."""
    objs, fields = {}, {}
    for r in rows:
        u = r["user_id"]
        objs.setdefault(u, set())
        fields.setdefault(u, 0)
        if r["object_ref"] is None or r["object_archived"] is not None:
            continue
        objs[u].add(r["object_ref"])
        if r["value_type"] is not None and r["field_archived"] is None:
            fields[u] += 1
    return {u: (len(objs[u]), fields[u]) for u in objs}


def aggregate(ds, rows, dims, filters, today=None):
    """원행 → 묶음 행. 반환 {dims, measures, rows: [{keys, values}], total, capped}. 검사는 report_check 가 먼저 한다."""
    spec = report_spec(ds)
    today = today or kst_today()
    measures = tuple(spec["measures"])
    groups = {}
    user_dims = set(spec.get("user_dims", ()))
    if dims and set(dims) <= user_dims:   # 구조의 사용자 단위 — 사용자마다 한 행
        for u, (no, nf) in structure_counts(rows).items():
            facts = {"objects_bucket": _bucket(no, OBJECT_BUCKETS), "fields_bucket": _bucket(nf, FIELD_BUCKETS)}
            g = groups.setdefault(tuple(facts[d] for d in dims), {"users": set(), "objects": 0, "fields": 0})
            g["users"].add(u)
            g["objects"] += no
            g["fields"] += nf
    else:
        for r in rows:
            if ds == "structure" and r["object_ref"] is None:
                continue
            facts = _facts(ds, r, today)
            if any(facts[k] != v for k, v in filters.items()):
                continue
            g = groups.setdefault(tuple(facts[d] for d in dims), {"tickets": 0, "calls": 0, "users": set(), "objects": set(),
                                                                   "fields": 0, "closed": 0})
            g["tickets"] += 1
            if ds == "users":
                g["users"].add(r["id"])
            elif ds == "usage":
                g["calls"] += r["n"]
                if r["user_id"] != "":
                    g["users"].add(r["user_id"])
            elif ds == "tickets":
                g["closed"] += 1 if r["closed_at"] is not None else 0
            else:
                g["users"].add(r["user_id"])
                g["objects"].add(r["object_ref"])
                g["fields"] += 1 if r["value_type"] is not None else 0
    out = []
    for keys, g in groups.items():
        out.append({"keys": keys, "values": {m: len(g[m]) if isinstance(g[m], set) else g[m] for m in measures}})
    order = {"objects_bucket": {b[2]: i for i, b in enumerate(OBJECT_BUCKETS)},
             "fields_bucket": {b[2]: i for i, b in enumerate(FIELD_BUCKETS)}}

    def kv(keys):
        return tuple((order.get(d, {}).get(k, 0), "" if k is None else k) for d, k in zip(dims, keys))
    if all(d in _ORDERED_DIMS for d in dims):
        out.sort(key=lambda x: kv(x["keys"]))
    else:
        out.sort(key=lambda x: (-x["values"][measures[0]], kv(x["keys"])))
    return {"dims": tuple(dims), "measures": measures, "rows": out[:REPORT_ROWS], "total": len(out), "capped": len(out) > REPORT_ROWS}


def report(role, ds, dims, filters, f=None, t=None, today=None):
    """리포트 한 번 — 검사(열거 키·조합·기간) → 원행 → 묶음. 반환에 ds·from·to(date 또는 None)를 더한다."""
    report_check(ds, dims, filters)
    d_from, d_to = report_window(ds, f, t, today)
    out = aggregate(ds, report_rows(role, ds, d_from, d_to), dims, filters, today)
    out.update({"ds": ds, "from": d_from, "to": d_to})
    return out


def preset_params(params, today=None):
    """프리셋 정의 → 쿼리스트링 dict(days 를 from·to 로)."""
    q = {k: v for k, v in params.items() if k != "days"}
    if "days" in params:
        today = today or kst_today()
        q["from"] = (today - _dt.timedelta(days=params["days"] - 1)).isoformat()
        q["to"] = today.isoformat()
    return q
