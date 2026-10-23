"""My CRM 서버 — 원격 MCP(Streamable HTTP) + 웹 화면.

사용자 결정: AuthKit JWT 검증(sub 우선, 처음 연결 땐 userinfo 의 검증된 이메일). 개발·시험은 CRM_DEV=1 + CRM_DEV_USER.
모델 호출은 없다 — 후보 추출은 사용자의 대화(ChatGPT·Claude)가 하고, 서버는 근거 검사·저장만 한다.
환경변수: DATABASE_URL · CRM_PUBLIC_URL(예 https://xxx.onrender.com) · AUTHKIT_ISSUER(예 https://xxx.authkit.app)
          · AUTHKIT_WEB_CLIENT_ID · CRM_SESSION_SECRET(웹 로그인, web.py) · CRM_DEV/CRM_DEV_USER(개발만)
          · CRM_ADMIN_USER_IDS(쉼표 구분 내부 user id — 첫 관리자 지정에만. 감사에 bootstrap·recover 행이 없을 때 서버 시작 1회만 읽는다(β2).
            그 뒤 관리자 = users.role — /admin/setup 에서 부여·회수, 잠기면 admin_cli.py grant)
          · CRM_CONTACT(선택 — 처리방침의 보조 메일. 없어도 뜬다. 문의·개인정보 요청 경로는 대시보드 「문의」 하나)
"""
import asyncio
import contextvars
import functools
import inspect
import json
import os
from urllib.parse import urlparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

import activity
import auth
import db
import imports
import web
from envnames import env

INSTRUCTIONS = """이 서버는 사용자의 「내 판단 대시보드」다. 사용자의 기록에서 판단 후보를 찾고, 사용자가 원문과 대조해 남길지 정한다.

규칙:
- 기록(record_add)은 사용자의 말을 그대로 저장한다. 다듬거나 요약하지 않는다. 받은 원문은 최초 판으로 보존하고, 사용자가 저장한 수정본은 최신 확정 판으로 보관한다.
- 후보(candidates_propose)는 네가 뽑되, 각 후보의 quote 는 그 기록 원문에서 글자 그대로 복사한 연속 구간이어야 한다. 원문에 같은 구간이 두 번 이상 나오면 앞뒤를 포함해 한 곳만 가리키게 더 길게 인용한다. 원문에 없는 사실·수치를 만들지 않는다. 타인의 말로 보이는 문장은 후보로 내지 않는다. 기록 1편당 최대 3개 — 넘기면 거절된다.
- 후보는 사용자의 판단이 아니다. 사용자가 원문과 대조해 "남길게/빼줘/고쳐서 남길게"라고 말할 때만 candidate_decide 를 부른다. 네가 대신 confirm 하지 않는다. 확정 전에 후보 문장을 사용자에게 그대로 보여 준다.
- 사용자가 묻지 않은 설정(프로필·질문)을 추정으로 확정하지 않는다. 추정은 status=guessed 로만 저장한다.
- 판단 종류 항목(semantic_kind principle — 기준 등)은 후보가 아니다. 사용자가 읽고 확인한 최종 글만 사용자의 판단이다. 후보 문장을 그대로 옮겨 적거나, 사용자가 확인하기 전에 item_decide 로 적용·확정하지 않는다. 정의·항목은 *_propose 로 제안만 한다.
- 항목 stage(draft 초안 → organized 정리됨 → confirmed 확정)는 서버가 정한다. 필수 칸이 차면 organized, 판단 종류만 사용자의 확인 1번(item_decide confirm 또는 화면의 대화 확정)으로 confirmed. organized 는 확정이 아니다 — 확정 여부는 decision 으로만 말한다. 되돌리기는 사용자만 정한다.
- 승격 조건(promotion)은 사실일 뿐이다. 충족 여부와 상관없이 기준으로 남길지는 사용자가 정한다.
- 질문에 답하지 않는 후보는 candidates_propose 의 question_id 를 빈 문자열("")로 보낸다. 화면은 기록별로 묶는다.
- 사용자가 '안 돼요/고쳐 주세요/이건 왜 이래요' 류를 말하면 feedback_send 로 운영자에게 문의하자고 제안한다. 보낼 글을 사용자에게 그대로 보여 주고, 사용자가 확인하면 그때 호출한다.
- 이 노트의 쓰는 법·용어 질문은 help_get 으로 해당 topic(start 또는 words) 하나만 읽고 그 문서 안에서 답한다. 같은 대화에서 이미 읽었으면 다시 부르지 않는다. 문서에 없으면 '문서에서 확인되지 않아요' 라고 하고 가장 가까운 절을 안내한다. 제품 밖 일반 질문엔 적용하지 않는다.
- 사람(person_save)은 사용자가 말한 별칭 하나만 저장한다. 연락처·소속·SNS·그 사람의 말·평가는 넣지 않는다.
- 출처 요약/반응(source_reflection_save)은 사용자가 출처를 접한 뒤 한 자기 생각만 저장한다. 타인의 말·경험·생각을 옮기거나 다시 정리하지 않는다. 검토 완료는 판단 확정이 아니고 근거로 인용할 수 없다 — 사용자가 말할 때만 source_reflection_review 를 부른다.
- 삭제는 휴지통이다(휴지통이 켜졌을 때). 내 글·항목(기준·결정·축·커스텀)·사람·읽고 든 생각·활동은 사용자가 지우라고 말할 때만 trash_move 로 옮긴다. 삭제 시각부터 168시간 안에 trash_restore 로 복원할 수 있고, 그 뒤엔 서버 정리 때 영구 삭제된다. trash_purge(영구 삭제 요청)는 되돌릴 수 없으니 사용자가 분명히 말할 때만 부른다. 휴지통 대상은 다른 도구에서 보이지 않는다. 휴지통이 켜지면 기준의 남기지 않기·되돌리기(item_decide exclude·pending)는 없다 — 남기지 않을 기준은 삭제한다. 승인 대기 글·항목/정의 제안·확정 전 대화 정리도 사용자가 지우라고 말할 때만 trash_move(kind=pending·item_proposal·definition_proposal·stack_session)로 옮긴다 — 휴지통이 켜지면 item_decide discard 는 없다. 복원은 승인·적용·확정이 아니다.
"""

DEV = env("DEV") == "1"
PUBLIC_URL = env("PUBLIC_URL", "http://127.0.0.1:8800").rstrip("/")
ISSUER = os.environ.get("AUTHKIT_ISSUER", "").rstrip("/")

if not DEV and not ISSUER:
    raise SystemExit("AUTHKIT_ISSUER 가 없어요. 운영은 인증 없이 뜨지 않아요 (개발은 CRM_DEV=1).")
if not DEV and env("DEV_USER"):
    raise SystemExit("CRM_DEV_USER 는 CRM_DEV=1 일 때만 쓸 수 있어요.")
USAGE_FLUSH_EVERY = 300      # usage_daily flush 주기(초). 수면·재배포 때 ≤5분치 유실 허용
PURGE_EVERY = 24 * 3600      # 파기(문의·감사) 주기(초) — 실행 중 매일 1회, 시작 때 1회는 따로

_auth_kwargs = {}
if ISSUER:
    _auth_kwargs = dict(
        token_verifier=auth.AuthKitVerifier(ISSUER, f"{PUBLIC_URL}/mcp"),
        # 문자열로 넘긴다. AnyHttpUrl(...) 을 밖에서 만들면 issuer 끝에 / 가 붙어 PRM authorization_servers 가 AS 메타데이터 issuer 와 어긋난다.
        auth=AuthSettings(issuer_url=ISSUER, resource_server_url=f"{PUBLIC_URL}/mcp",
                          required_scopes=["openid", "email"], validate_token_resource=True),
    )

mcp = MCPServer("my-crm", instructions=INSTRUCTIONS, **_auth_kwargs)


# ── 도구 분류 (PR B 결정 3 — 표시 이름 관문) ──
# 쓰기 = 표시 이름이 없으면 NAME-REQUIRED(db.write_gate 가 판정). 허용 = 이름 없이도 된다(읽기·help·문의·표시 이름 설정).
# 등록된 도구는 둘 중 하나에 꼭 들어간다 — 빠지면 시험(test_tools 24)이 실패한다. WRITE_TOOLS = db.WRITE_LIMITS 키 − db.NAME_EXEMPT.
WRITE_TOOLS = frozenset({
    "question_add", "record_add", "candidates_propose", "candidate_decide", "profile_set", "definition_propose", "definition_apply",
    "item_propose", "item_decide", "record_attach",
    "person_save", "person_delete", "source_create", "source_reflection_save", "source_reflection_review", "source_reflection_delete",
    "record_provenance_set", "definition_archive", "definition_restore",   # 이 줄 셋 = R5 결함(PR B 범위 추가)
    "trash_move", "trash_restore", "trash_purge"})                          # 휴지통 PR-1 — 전역 크기 관문 면제(db.QUOTA_EXEMPT)
ALLOWED_TOOLS = frozenset({
    "state_get", "question_list", "candidate_list", "record_get", "definitions_get", "help_get", "feedback_send", "ticket_list_mine",
    "ticket_get_mine", "display_name_set", "me_get", "person_list", "person_get", "source_list", "source_get", "source_reflection_list",
    "source_reflection_get",
    # board 이전(2026-10-20) 읽기 7 — 활동·사람/출처 문맥·「나」 집계·승인 대기. 승인·제외는 웹에서만
    "activity_list", "activity_get", "person_context_get", "source_context_get", "me_aggregate_get", "inbox_list", "inbox_get",
    "trash_list"})   # 휴지통 PR-1 목록(본문 없음)

# 도구 호출 하나 안에서 푼 uid — guard 가 호출마다 새 dict 를 꽂는다. 쓰기 게이트(전역 크기)와 도구 본문이 같은 조회를 한 번만 하고,
# 사용 집계(usage_count)가 추가 DB 조회 없이 uid 를 읽는다. guard 밖(직접 호출)에선 None — 매번 푼다.
_call_uid: contextvars.ContextVar = contextvars.ContextVar("crm_call_uid", default=None)


def uid_of(ctx: Context) -> str:
    memo = _call_uid.get()
    if memo is not None and "uid" in memo:
        return memo["uid"]
    tok = get_access_token()
    if tok is not None:
        uid = auth.user_id_for(tok)
    elif DEV:
        uid = db.user_by_email(env("DEV_USER", "dev@local"))["id"]
    else:
        raise ToolError("[AUTH] 로그인 정보가 없어요. 커넥터를 다시 연결하세요.")
    if memo is not None:
        memo["uid"] = uid
    return uid


def guard(fn):
    """CrmError 를 모델이 읽을 수 있는 ToolError 로 바꾼다. wraps 로 서명을 보존해야 입력 스키마가 유지된다.
    쓰기 도구(db.WRITE_LIMITS 의 키)는 실행 전에 db.write_gate — 인자 길이(PAYLOAD-LIMIT)
    ·전역 DB 크기(QUOTA-GLOBAL, QUOTA_EXEMPT 는 빼고). 모든 도구(읽기 포함)를 db.usage_count 로 센다 — 정상 OK, CrmError 는 그 code,
    그 밖 예외(ToolError 포함)는 EXC. uid 는 이 호출 안에서 이미 푼 것만 쓴다(못 풀었으면 '')."""
    sig = inspect.signature(fn)
    name = fn.__name__
    write = name in db.WRITE_LIMITS
    quota = name not in db.QUOTA_EXEMPT

    @functools.wraps(fn)
    def run(*a, **k):
        memo = {}
        token = _call_uid.set(memo)
        code = "EXC"
        try:
            if write:
                args = sig.bind(*a, **k).arguments
                db.write_gate(name, {n: v for n, v in args.items() if n != "ctx"}, lambda: uid_of(args["ctx"]), quota)
            out = fn(*a, **k)
            code = "OK"
            return out
        except db.CrmError as e:
            code = e.code
            raise ToolError(f"[{e.code}] {e.message}" + (f" 다음 조치: {e.action}" if e.action else ""))
        finally:
            _call_uid.reset(token)
            db.usage_count(memo.get("uid"), name, code)
    return run


# ── 도구 ───────────────────────────────────────────────

@mcp.tool()
@guard
def state_get(ctx: Context) -> dict:
    """대시보드의 현재 상태 한 벌 — 프로필·질문·탭·후보·기록 목록·다음 할 일. 대화를 시작할 때 먼저 부른다."""
    return db.state(uid_of(ctx))


@mcp.tool()
@guard
def question_add(text: Annotated[str, Field(description="사용자가 말한 문장 그대로")], ctx: Context) -> dict:
    """계속 돌아오는 질문을 하나 추가한다. 사용자가 질문을 말하지 않으면 만들지 않는다."""
    return db.question_add(uid_of(ctx), text)


@mcp.tool()
@guard
def question_list(ctx: Context) -> list[dict]:
    """질문 목록."""
    return db.questions(uid_of(ctx))


@mcp.tool()
@guard
def record_add(
    text: Annotated[str, Field(description="사용자가 말한 기록 본문 그대로. 다듬지 않는다.")],
    ctx: Context,
    title: Annotated[str, Field(description="제목 한 줄. 비우면 첫 줄에서 만든다.")] = "",
    question_id: Annotated[str, Field(description="연결할 질문 ID (선택)")] = "",
    source: Annotated[str, Field(description="출처 메모 문자열 (예: 대화에서 입력, 회의 메모)")] = "대화에서 입력",
    source_name: Annotated[str, Field(description="출처 연결: 이 기록이 나온 파일·페이지·대화의 이름 (선택). 비우면 연결 안 함")] = "",
    source_kind: Annotated[Literal["", "file", "url", "chat", "other"],
                           Field(description="출처 연결: 종류 (선택, 비우면 other)")] = "",
    source_location: Annotated[str, Field(description="출처 연결: 위치 — 파일 경로·URL 등 (선택)")] = "",
) -> dict:
    """기록 1편을 원문 그대로 남긴다. 직접 쓴 내 기록만 넣는다 — 남의 말·AI 요약은 넣지 않는다.
    남이 쓴 글은 넣지 않는다. 이미 넣었다면 record_provenance_set 으로 author_kind=external 표시한다.
    서버는 메타데이터만 검사하고 실제 작성자·가공 방식은 검증하지 않는다(내용 준수는 사용자 몫).
    source_name 을 주면 같은 이름·종류·위치의 출처를 찾거나 만들어 잇는다(source_linked=true).
    저장 뒤 사용자에게 '지금 후보를 뽑을까요?' 하나만 묻는다."""
    ref = {"kind": source_kind, "name": source_name, "location": source_location} if source_name.strip() else None
    r = db.record_add(uid_of(ctx), title, text, question_id or None, source, source_ref=ref)
    out = {"id": r["id"], "title": r["title"], "created": r["created"]}
    if ref is not None:
        out["source_id"], out["source_linked"] = r["source_id"], r["source_linked"]
    return out


class CandidateIn(BaseModel):
    text: str = Field(description="판단 후보 한 문장")
    quote: str = Field(description="원문에서 글자 그대로 복사한 근거 구간 (한 문장 이내)")


@mcp.tool()
@guard
def candidates_propose(
    record_id: Annotated[str, Field(description="후보를 뽑은 기록 ID")],
    question_id: Annotated[str, Field(description="이 후보들이 답하는 질문 ID")],
    candidates: Annotated[list[CandidateIn], Field(description="후보 목록, 최대 3개")],
    ctx: Context,
    origin: Annotated[Literal["ai", "self"], Field(description="ai=네가 뽑음, self=사용자가 기록에 직접 쓴 판단 메모")] = "ai",
) -> dict:
    """기록에서 뽑은 판단 후보를 제출한다. 서버가 근거 인용이 원문에 있는지 검사하고, 통과한 것만 '아직 안 정함' 상태로 저장한다.
    돌려주는 dropped 에 버린 이유가 있다. 저장된 후보는 사용자의 판단이 아니다."""
    if len(candidates) > db.MAX_CANDIDATES_PER_RECORD:
        raise ToolError("기록 1편당 후보는 3개까지예요. 3개만 골라 다시 보내세요.")
    items = [c.model_dump() for c in candidates]
    uid = uid_of(ctx)
    added, dropped = db.candidates_propose(uid, record_id, question_id, items, origin)
    return {"added": [db.candidate_get(uid, cid) for cid in added], "dropped": dropped,
            "note": "후보는 아직 내 판단이 아니에요. 사용자가 원문과 대조해 정하게 하세요."}


@mcp.tool()
@guard
def candidate_decide(
    candidate_id: Annotated[str, Field(description="후보 ID")],
    action: Annotated[Literal["confirm", "exclude", "pending", "edit"],
                      Field(description="confirm=내 판단으로 남기기, exclude=남기지 않기, pending=되돌리기(아직 안 정함으로), edit=문장 고치기(text 필요)")],
    ctx: Context,
    text: Annotated[str, Field(description="edit 일 때 사용자가 고친 문장. 사용자 표현 그대로.")] = "",
) -> dict:
    """사용자가 원문과 대조한 뒤 말한 결정을 저장한다. 사용자가 말하기 전에 부르지 않는다. 이전 상태·문장은 이력에 남아 되돌릴 수 있다.
    confirm 은 근거 기록이 지금도 인용 적격(본인이 쓴 원문 그대로·삭제 안 됨)일 때만 된다 — 아니면 CANDIDATE-REVIEW 로 거절되고
    exclude·pending·edit 은 그대로 된다. 후보의 review 에 사유 코드가 있으면 사용자에게 그대로 알린다. 이미 확정한 판단은 바뀌지 않는다.
    review 가 author_unknown·processed 이고 사용자가 메타데이터가 실제로 틀렸다고 하면, 연결한 AI 대화에서 record_provenance_set 으로 정정한다."""
    return db.candidate_decide(uid_of(ctx), candidate_id, action, text or None)


@mcp.tool()
@guard
def candidate_list(
    ctx: Context,
    question_id: Annotated[str, Field(description="질문 ID 로 거르기 (선택)")] = "",
    status: Annotated[Literal["", "pending", "confirmed", "excluded"], Field(description="상태로 거르기 (선택)")] = "",
) -> list[dict]:
    """후보 목록. 각 후보에 근거 기록 ID 와 인용이 있다."""
    return db.candidates(uid_of(ctx), question_id or None, status or None)


@mcp.tool()
@guard
def record_get(record_id: Annotated[str, Field(description="기록 ID")], ctx: Context) -> dict:
    """기록 원문 전체. 후보를 뽑거나 사용자와 대조할 때 읽는다."""
    return db.record_get(uid_of(ctx), record_id)


@mcp.tool()
@guard
def profile_set(
    key: Annotated[Literal["purpose", "refind", "daily_views", "first_material", "separate", "help_level"], Field(description="프로필 항목")],
    value: Annotated[str, Field(description="사용자 표현 그대로. daily_views 는 | 로 구분한 최대 3개")],
    status: Annotated[Literal["confirmed", "guessed", "empty", "off"], Field(description="confirmed=사용자가 맞다고 함, guessed=글에서 추정(확인 전), off=쓰지 않기로 함")],
    ctx: Context,
    source: Annotated[str, Field(description="어디서 알았나 (예: 사용자 답, 글 추정)")] = "",
) -> dict:
    """프로필 항목 하나를 저장한다. 추정은 반드시 guessed 로."""
    return db.profile_set(uid_of(ctx), key, value, status, source)


# ── 사용자 정의 층 (2단계): 정의·항목·관계 ─────────────────
# 동적 내용(정의 문서·항목 값)은 JSON 문자열로 받는다 — 도구 입력 스키마는 고정하고 내용은 서버가 검증한다.

def _json_obj(raw: str, name: str) -> dict:
    import json
    try:
        v = json.loads(raw or "{}")
    except ValueError:
        raise db.CrmError("ARG-INVALID", f"{name} 가 JSON 이 아니에요.")
    if not isinstance(v, dict):
        raise db.CrmError("ARG-INVALID", f"{name} 는 JSON 객체여야 해요.")
    return v


class Link(BaseModel):
    field_key: str = Field(description="관계 필드 key (definitions_get 의 reference 필드)")
    target_kind: Literal["item", "record", "candidate"] = Field(description="대상 종류")
    target_id: str = Field(description="대상 ID (i_… / r_… / c_…)")


@mcp.tool()
@guard
def definitions_get(
    object_ids: Annotated[list[str] | None, Field(description="볼 오브젝트 ID 목록. null 이면 전부")],
    include_archived: Annotated[bool, Field(description="보관된 오브젝트·필드도 볼지")],
    ctx: Context,
) -> list[dict]:
    """내가 정의한 오브젝트(기준·결정 같은 자료 구조)와 필드. item_propose 전에 object_id·revision·필드 key 를 여기서 확인한다.
    object_ids 로 콕 집어 물으면 아직 적용 안 된 새 오브젝트 제안(revision 0)도 draft=true 로 함께 돌려준다."""
    return db.definitions(uid_of(ctx), object_ids or None, include_archived)


@mcp.tool()
@guard
def definition_propose(
    object_id: Annotated[str | None, Field(description="고칠 오브젝트 ID. 새 오브젝트면 null")],
    expected_revision: Annotated[int, Field(description="지금 revision. 새 오브젝트면 0")],
    definition_json: Annotated[str, Field(description='정의 JSON 문자열: {"key","label","semantic_kind":"collection|event|principle","policy":{},'
                                                      '"fields":[{"key","label","value_type":"text|number|boolean|date|select|text_list|reference",'
                                                      '"required","options","target_kind","target_object_id","multiple"}]}. 고칠 땐 필드 전체를 보낸다(빠진 필드는 보관)')],
    ctx: Context,
) -> dict:
    """오브젝트 정의를 제안한다(draft 로만 저장). 사용자에게 제안 내용을 보여 주고, 사용자가 적용하라고 말할 때만 definition_apply 를 부른다."""
    return db.definition_propose(uid_of(ctx), object_id or None, expected_revision, _json_obj(definition_json, "definition_json"))


@mcp.tool()
@guard
def definition_apply(
    object_id: Annotated[str, Field(description="오브젝트 ID")],
    proposal_id: Annotated[str, Field(description="definition_propose 가 돌려준 제안 ID")],
    expected_revision: Annotated[int, Field(description="제안할 때의 revision")],
    ctx: Context,
) -> dict:
    """제안한 정의를 적용한다. 사용자가 적용하라고 말했을 때만 부른다. 기존 항목이 깨지는 변경(필수 추가·타입 변경 등)은 거절된다."""
    return db.definition_apply(uid_of(ctx), object_id, proposal_id, expected_revision)


@mcp.tool()
@guard
def item_propose(
    object_id: Annotated[str, Field(description="오브젝트 ID")],
    schema_revision: Annotated[int, Field(description="definitions_get 의 오브젝트 revision")],
    item_id: Annotated[str | None, Field(description="고칠 항목 ID. 새 항목이면 null")],
    expected_revision: Annotated[int, Field(description="항목의 지금 revision. 새 항목이면 0")],
    code: Annotated[str | None, Field(description="항목 코드(예: C-07). 없으면 null. 적용 뒤엔 바꿀 수 없다")],
    title: Annotated[str, Field(description="제목 한 줄")],
    body: Annotated[str, Field(description="본문")],
    data_json: Annotated[str, Field(description="일반 필드 값 JSON 객체 문자열. 관계는 넣지 않고 links 로")],
    links: Annotated[list[Link], Field(description="관계 전체 목록(적용하면 이 목록으로 바뀐다)")],
    ctx: Context,
) -> dict:
    """항목(기준·결정 등)을 제안한다(draft 로만 저장). 웹 화면이나 대화에서 사용자가 적용하라고 할 때만 item_decide(apply)."""
    return db.item_propose(uid_of(ctx), object_id, schema_revision, item_id or None, expected_revision, code, title, body,
                           _json_obj(data_json, "data_json"), [ln.model_dump() for ln in links])


@mcp.tool()
@guard
def item_decide(
    item_id: Annotated[str, Field(description="항목 ID")],
    expected_revision: Annotated[int, Field(description="항목의 지금 revision")],
    proposal_id: Annotated[str | None, Field(description="apply·discard 때 제안 ID. 그 외 null")],
    action: Annotated[Literal["apply", "confirm", "exclude", "pending", "archive", "restore", "discard"],
                      Field(description="apply=제안 적용, discard=제안 버리기, confirm=판단 종류 항목을 내 판단으로 남기기(확인 1번), exclude=남기지 않기, "
                                        "pending=아직 안 정함으로, archive=보관, restore=history_id 시점으로 되돌리기. "
                                        "휴지통이 켜지면 exclude·pending·discard 는 ACTION-RETIRED(아무것도 안 바뀜) — 남기지 않을 기준은 trash_move(kind=item), "
                                        "버릴 제안은 trash_move(kind=item_proposal)")],
    history_id: Annotated[int | None, Field(description="restore 때 이력 ID. 그 외 null")],
    ctx: Context,
) -> dict:
    """사용자가 말한 결정을 항목에 저장한다. 사용자가 말하기 전에 부르지 않는다. confirm·exclude·pending 은 판단 종류(semantic_kind principle) 항목만.
    confirm 결과의 promotion 은 승격 조건 충족 여부(사실)일 뿐 — 미충족이어도 저장되고, 사용자에게 그대로 알린다."""
    return db.item_decide(uid_of(ctx), item_id, expected_revision, proposal_id or None, action, history_id)


@mcp.tool()
@guard
def record_attach(
    record_id: Annotated[str, Field(description="기록 ID")],
    ctx: Context,
    attach_to: Annotated[list[Link] | None, Field(description="이 기록이 붙는 기준·결정 항목 또는 후보. field_key 는 attach_to. null 이면 그대로")] = None,
    axes: Annotated[list[str] | None, Field(description="분류 대상 오브젝트의 항목 ID 목록(관심 축). null 이면 그대로")] = None,
    use_at: Annotated[str | None, Field(description="쓸 자리 메모. null 이면 그대로")] = None,
    unattached: Annotated[str | None, Field(description="아직 안 붙는 것 메모(새 축 후보 등). null 이면 그대로")] = None,
) -> dict:
    """기록이 어디에 붙는지 남긴다(기준·결정·후보 연결, 분류 대상 항목, 쓸 자리, 아직 안 붙는 것). 원문은 바뀌지 않는다. 값을 준 칸만 바뀐다.
    axes 는 분류 대상 오브젝트의 항목만 받는다 — 분류 대상이 없으면 CLASSIFY-OFF(다른 칸은 그대로 쓸 수 있다)."""
    return db.record_attach(uid_of(ctx), record_id, [ln.model_dump() for ln in attach_to] if attach_to is not None else None,
                            axes, use_at, unattached)


@mcp.tool()
@guard
def record_provenance_set(
    record_id: Annotated[str, Field(description="기록 ID")],
    author_kind: Annotated[Literal["self", "external", "unknown"], Field(description="self=사용자가 직접 씀, external=남이 쓴 글, unknown=모름")],
    text_process: Annotated[Literal["verbatim", "structured", "rewritten", "generated", "unknown"],
                            Field(description="verbatim=원문 그대로, structured=구조만 정리, rewritten=다시 씀, generated=AI 가 만듦, unknown=모름")],
    reason: Annotated[str, Field(description="사용자가 말한 정정 사유 그대로")],
    ctx: Context,
) -> dict:
    """기록의 출처 판정(작성자·가공 방식)을 정정한다. 본문은 바뀌지 않는다. 사용자가 「실제로 틀렸다」고 말했을 때만 부른다 — 확정을 풀려고 바꾸지 않는다.
    남이 쓴 글을 기록으로 넣었으면 author_kind=external 로 표시한다. 이 기록을 근거로 한 후보가 있는데 본인 원문이 아니라고 바꾸면 PROVENANCE-LOCKED.
    정정은 provenance_history 에 사유와 함께 남는다."""
    return db.record_provenance_set(uid_of(ctx), record_id, author_kind, text_process, reason, by="tool")


@mcp.tool()
@guard
def definition_archive(object_id: Annotated[str, Field(description="보관할 사용자 오브젝트 ID")], ctx: Context) -> dict:
    """사용자가 만든 오브젝트를 보관한다(목록에서 빠짐, 항목은 지우지 않음). 사용자가 보관하라고 말했을 때만 부른다.
    먼저 그 오브젝트의 항목 수·이 오브젝트를 가리키는 다른 필드를 사용자에게 알린다 — 결과의 impact 에도 있다.
    표준 오브젝트는 STANDARD-LOCKED. 되돌리기는 definition_restore. 정의 JSON 에 archived 같은 키를 넣어 보관하지 않는다(ARG-INVALID)."""
    return db.definition_archive(uid_of(ctx), object_id)


@mcp.tool()
@guard
def definition_restore(object_id: Annotated[str, Field(description="복구할 사용자 오브젝트 ID")], ctx: Context) -> dict:
    """보관한 사용자 오브젝트를 되살린다. 사용자가 말했을 때만 부른다. 보관된 오브젝트 ID 는 definitions_get(include_archived=true) 로 본다."""
    return db.definition_restore(uid_of(ctx), object_id)


# ── 도움말 ─────────────────────────────────────────────
# 웹 /help 와 같은 원문(guides/09·10, web.Web.load_help 가 시작 때 읽음)을 돌려준다.

@mcp.tool()
@guard
def help_get(
    ctx: Context,
    topic: Annotated[Literal["start", "words", "all"],
                     Field(description="start=연결·첫 하루·화면 탭·막힐 때, words=말 뜻·자주 묻는 것, all=둘 다(꼭 필요할 때만)")] = "words",
) -> dict:
    """사용자가 이 노트의 쓰는 법·말 뜻을 물으면 먼저 이걸 읽고 그 문서 안에서 답한다.
    쓰는 법·용어 질문은 해당 topic(start 또는 words) 하나만 읽는다. 같은 대화에서 이미 읽었으면 다시 부르지 않는다. 문서에 없으면 '문서에서 확인되지 않아요' 라고 하고 가장 가까운 절을 안내한다. 제품 밖 일반 질문엔 적용하지 않는다."""
    docs = site.guides
    if not docs:
        raise ToolError("[HELP-MISSING] 서버에 도움말 문서가 없어요. 운영자에게 알려 주세요.")
    text = "\n\n".join(docs[k] for k in web.HELP_FILES) if topic == "all" else docs[topic]
    return {"topic": topic, "text": text}


# ── 문의 (desk-0) ──────────────────────────────────────

@mcp.tool()
@guard
def feedback_send(
    kind: Annotated[Literal["bug", "request", "question", "privacy"],
                    Field(description="bug=안 되는 것, request=바랐으면 하는 것, question=궁금한 것, privacy=내 정보 열람·정정·삭제·탈퇴 요청")],
    text: Annotated[str, Field(description="사용자가 확인한 문의 글 그대로. 기록 원문·다른 사람 정보는 넣지 않는다.")],
    ctx: Context,
    tool: Annotated[str, Field(description="문제가 난 이 서버의 도구 이름(예: record_add). 없으면 빈 문자열")] = "",
) -> dict:
    """운영자에게 문의를 보낸다. 보낼 글을 사용자에게 먼저 그대로 보여 주고, 사용자가 확인한 뒤에만 부른다.
    글은 서버에 저장되고 운영자가 직접 읽는다. 처리 상태는 대시보드 「내 문의」에서 보인다. 응답의 notice 를 사용자에게 그대로 전한다."""
    tool = tool if mcp._tool_manager.get_tool(tool) is not None else ""   # 이 서버의 도구 이름만 남긴다 — 자유 문자열은 저장하지 않는다
    t = db.ticket_add(uid_of(ctx), kind, text, "mcp", tool=tool)
    return {"id": t["id"], "kind": t["kind"], "status": t["status"], "text": text,
            "notice": f"{db.NOTICE} 처리 상태는 대시보드 「내 문의」에서 볼 수 있어요. 개인정보 처리방침: {PUBLIC_URL}/privacy"}


@mcp.tool()
@guard
def ticket_list_mine(ctx: Context) -> list[dict]:
    """내가 보낸 문의 목록(50건, 최근 공개 갱신순 — 갱신이 없으면 접수 시각) — id·종류·상태·접수 시각·답변 유무·공개 갱신 시각. 글·답변 본문은 ticket_get_mine 으로 하나씩 본다.
    사용자가 「내 문의 어떻게 됐어?」 류를 물을 때 부른다."""
    return [{k: t[k] for k in ("id", "kind", "status", "created", "has_reply", "public_updated")} for t in db.tickets_mine(uid_of(ctx))]


@mcp.tool()
@guard
def ticket_get_mine(
    ticket_id: Annotated[str, Field(description="ticket_list_mine 의 id")],
    ctx: Context,
) -> dict:
    """내 문의 하나 — 보낸 글·운영자 답변·공개 이력(상태 변경·답변 시각). 내 문의가 아니면 NOT-FOUND."""
    return db.ticket_get_mine(uid_of(ctx), ticket_id)


# ── 나 · 사람 · 출처 · 출처 요약/반응 (PR B 코어) ─────────────
# 사용자 id 는 인증에서만 얻는다. 목록 limit 기본 50·최대 100, cursor = 불투명 문자열(next_cursor 를 그대로 다시 보낸다).

@mcp.tool()
@guard
def display_name_set(display_name: Annotated[str, Field(description="사용자가 정한 표시 이름 그대로(1~80자, 한 줄). 실명이 아니어도 된다")],
                     ctx: Context) -> dict:
    """「나」의 표시 이름을 저장한다. 사용자가 이름을 말했을 때만 부른다 — 이메일 등에서 추측해 채우지 않는다.
    표시 이름이 없으면 다른 쓰기 도구가 NAME-REQUIRED 로 거절된다. 표시 이름은 다른 사용자·운영자 화면에 보이지 않는다."""
    return db.display_name_set(uid_of(ctx), display_name)


@mcp.tool()
@guard
def me_get(ctx: Context) -> dict:
    """「나」 — 표시 이름 · 새 탐색 수락 여부(nav: new|old) · 개수(확정 판단·기준·결정·사람·출처·요약/반응)."""
    return db.me_home(uid_of(ctx))


@mcp.tool()
@guard
def person_list(
    ctx: Context,
    query: Annotated[str, Field(description="별칭 부분 일치 (선택)")] = "",
    cursor: Annotated[str, Field(description="다음 쪽: 앞 응답의 next_cursor 그대로 (처음은 빈 값)")] = "",
    limit: Annotated[int, Field(description="한 번에 볼 수 (기본 50, 최대 100)")] = 50,
) -> dict:
    """사람(사용자가 관계를 구분하려고 적은 별칭 이름표) 목록. 최근 순."""
    return db.person_list(uid_of(ctx), query, cursor, limit)


@mcp.tool()
@guard
def person_get(person_id: Annotated[str, Field(description="사람 ID (p_…)")], ctx: Context) -> dict:
    """사람 하나 — id·별칭·revision."""
    return db.person_get(uid_of(ctx), person_id)


@mcp.tool()
@guard
def person_save(
    alias: Annotated[str, Field(description="사용자가 정한 별칭 그대로(1~80자, 한 줄)")],
    ctx: Context,
    person_id: Annotated[str, Field(description="고칠 사람 ID. 새로 만들면 빈 값")] = "",
    expected_revision: Annotated[int | None, Field(description="고칠 때 지금 revision(person_get). 새로 만들면 null")] = None,
) -> dict:
    """사람(별칭 이름표)을 만들거나 별칭을 고친다. 별칭만. 연락처·소속·SNS·그 사람의 말·평가는 넣지 않는다.
    별칭도 그 사람을 알아볼 수 있으면 개인정보다. 사용자가 말한 별칭만 저장한다."""
    return db.person_save(uid_of(ctx), alias, person_id, expected_revision)


@mcp.tool()
@guard
def person_delete(
    person_id: Annotated[str, Field(description="사람 ID")],
    expected_revision: Annotated[int, Field(description="지금 revision")],
    ctx: Context,
) -> dict:
    """사람 하나를 지운다. 사용자가 지우라고 말했을 때만 부른다."""
    return db.person_delete(uid_of(ctx), person_id, expected_revision)


@mcp.tool()
@guard
def source_list(
    ctx: Context,
    query: Annotated[str, Field(description="출처 이름·위치 부분 일치 (선택)")] = "",
    cursor: Annotated[str, Field(description="다음 쪽: 앞 응답의 next_cursor 그대로 (처음은 빈 값)")] = "",
    limit: Annotated[int, Field(description="한 번에 볼 수 (기본 50, 최대 100)")] = 50,
) -> dict:
    """출처(기록·요약/반응이 나온 파일·페이지·대화) 목록 — id·kind·name·location. 최근 순."""
    return db.source_list(uid_of(ctx), query, cursor, limit)


@mcp.tool()
@guard
def source_get(source_id: Annotated[str, Field(description="출처 ID (s_…)")], ctx: Context) -> dict:
    """출처 하나 + 개수(기록·내 요약·내 반응)."""
    return db.source_get(uid_of(ctx), source_id)


@mcp.tool()
@guard
def source_create(
    kind: Annotated[Literal["file", "url", "chat", "other"], Field(description="출처 종류")],
    name: Annotated[str, Field(description="출처 이름 (파일·페이지·대화 이름)")],
    ctx: Context,
    location: Annotated[str, Field(description="위치 — 파일 경로·URL 등 (선택)")] = "",
) -> dict:
    """출처를 찾거나 만든다(같은 종류·이름·위치면 있는 것을 돌려준다). 기록 없이 만들 수 있다."""
    return db.source_create(uid_of(ctx), kind, name, location)


@mcp.tool()
@guard
def source_reflection_list(
    ctx: Context,
    source_id: Annotated[str, Field(description="출처 ID 로 거르기 (선택)")] = "",
    kind: Annotated[Literal["", "summary", "reaction"], Field(description="summary=내 요약, reaction=내 반응 (선택)")] = "",
    review: Annotated[Literal["", "pending", "reviewed"], Field(description="pending=검토 대기, reviewed=검토 완료 (선택)")] = "",
    cursor: Annotated[str, Field(description="다음 쪽: 앞 응답의 next_cursor 그대로 (처음은 빈 값)")] = "",
    limit: Annotated[int, Field(description="한 번에 볼 수 (기본 50, 최대 100)")] = 50,
) -> dict:
    """출처 요약/반응 목록. 최근 순. 검토 완료여도 판단이 아니고 근거로 인용할 수 없다."""
    return db.source_reflection_list(uid_of(ctx), source_id, kind, review, cursor, limit)


@mcp.tool()
@guard
def source_reflection_get(reflection_id: Annotated[str, Field(description="요약/반응 ID (sr_…)")], ctx: Context) -> dict:
    """출처 요약/반응 하나 — 출처·유형·본문·검토 상태·revision."""
    return db.source_reflection_get(uid_of(ctx), reflection_id)


@mcp.tool()
@guard
def source_reflection_save(
    source_id: Annotated[str, Field(description="출처 ID (source_list·source_create)")],
    kind: Annotated[Literal["summary", "reaction"], Field(description="summary=내 요약, reaction=내 반응")],
    body: Annotated[str, Field(description="사용자가 쓴 자기 생각 그대로 (8000자까지)")],
    ctx: Context,
    reflection_id: Annotated[str, Field(description="고칠 요약/반응 ID. 새로 만들면 빈 값")] = "",
    expected_revision: Annotated[int | None, Field(description="고칠 때 지금 revision. 새로 만들면 null")] = None,
) -> dict:
    """출처 요약/반응을 만들거나 고친다(새로 만들면 검토 대기). 출처를 접한 뒤 사용자 자신의 생각만.
    타인의 말·경험·생각을 옮기거나 다시 정리하지 않는다. 검토 완료는 판단 확정이 아니고 근거로 인용할 수 없다.
    사용자가 말할 때만 review 를 부른다. 검토 완료 뒤 고치면 검토 대기로 돌아간다."""
    return db.source_reflection_save(uid_of(ctx), source_id, kind, body, reflection_id, expected_revision)


@mcp.tool()
@guard
def source_reflection_review(
    reflection_id: Annotated[str, Field(description="요약/반응 ID")],
    expected_revision: Annotated[int, Field(description="지금 revision")],
    reviewed: Annotated[bool, Field(description="true=검토 완료, false=검토 대기로")],
    ctx: Context,
) -> dict:
    """출처 요약/반응의 검토 상태를 바꾼다. 사용자가 말할 때만 부른다. 검토 완료는 판단 확정이 아니고 근거로 인용할 수 없다."""
    return db.source_reflection_review(uid_of(ctx), reflection_id, expected_revision, reviewed)


@mcp.tool()
@guard
def source_reflection_delete(
    reflection_id: Annotated[str, Field(description="요약/반응 ID")],
    expected_revision: Annotated[int, Field(description="지금 revision")],
    ctx: Context,
) -> dict:
    """출처 요약/반응 하나를 지운다. 사용자가 지우라고 말했을 때만 부른다."""
    return db.source_reflection_delete(uid_of(ctx), reflection_id, expected_revision)


# ── 휴지통 (2026-10-23 PR-1) — 스위치 CRM_TRASH_ENABLED 꺼짐 = SWITCH-OFF. 목록은 본문 없음(제목·별칭·첫 줄만) ──
# PR-2(2026-10-23) — 종류 4 추가: pending(승인 대기 글, id = 접수 ID) · item_proposal(항목 제안, id = 제안 ID ip_…) ·
# definition_proposal(정의 제안, id = 제안 ID dp_…) · stack_session(확정 전 대화 정리, id = 세션 ID). Literal 이 늘어 커넥터 스키마 재승인 대상
TrashKind = Literal["record", "item", "person", "reflection", "activity", "pending", "item_proposal", "definition_proposal", "stack_session"]
_TRASH_REV_DESC = "item·person·reflection 의 지금 revision(이동 때 필수). 그 밖 종류는 null"
_TRASH_KIND_DESC = ("record=내 글, item=항목(기준·결정·축·커스텀), person=사람, reflection=읽고 든 생각, activity=활동(원천 전체), "
                    "pending=승인 대기 글(id=접수 ID), item_proposal=항목 제안(id=제안 ID), definition_proposal=정의 제안(id=제안 ID), "
                    "stack_session=확정 전 대화 정리(id=세션 ID)")


@mcp.tool()
@guard
def trash_list(
    ctx: Context,
    kind: Annotated[Literal["", "record", "item", "person", "reflection", "activity", "pending", "item_proposal", "definition_proposal",
                            "stack_session"], Field(description="종류로 거르기 (선택, 빈 값 = 전부)")] = "",
    cursor: Annotated[str, Field(description="다음 쪽: 앞 응답의 next_cursor 그대로 (처음은 빈 값)")] = "",
    limit: Annotated[int, Field(description="한 번에 볼 수 (기본 50, 최대 100)")] = 50,
) -> dict:
    """휴지통 목록 — 복원 기한(purge_at) 전인 것만, 최근 삭제 순. 기한이 지난 것은 복원할 수 없고 목록에서도 빠진다.
    restore_state = 복원될 상태, remaining_seconds = 승인 대기·확정 전 정리의 멈춘 남은 시간(복원하면 그만큼 다시 흐른다)."""
    return db.trash_list(uid_of(ctx), kind, cursor, limit)


@mcp.tool()
@guard
def trash_move(
    kind: Annotated[TrashKind, Field(description=_TRASH_KIND_DESC)],
    target_id: Annotated[str, Field(description="대상 ID")],
    expected_revision: Annotated[int | None, Field(description=_TRASH_REV_DESC)],
    ctx: Context,
) -> dict:
    """대상을 휴지통으로 옮긴다(삭제). 사용자가 지우라고 말했을 때만 부른다. 168시간 안에 trash_restore 로 복원할 수 있다.
    연결된 상대(글·항목·활동)는 그대로 남는다. 장치 허용 규칙이 쓰는 축은 AXIS-IN-POLICY — 사용자에게 장치 화면에서 먼저 빼라고 안내한다.
    제안 삭제는 지금 저장된 값을 바꾸지 않는다. 승인 대기·확정 전 정리는 남은 대기·보관 시간이 휴지통에서 멈춘다."""
    return db.trash_move(uid_of(ctx), kind, target_id, expected_revision)


@mcp.tool()
@guard
def trash_restore(
    kind: Annotated[TrashKind, Field(description=_TRASH_KIND_DESC)],
    target_id: Annotated[str, Field(description="대상 ID")],
    expected_revision: Annotated[int | None, Field(description="item·person·reflection 의 지금 revision(선택). 그 밖 종류는 null")],
    ctx: Context,
) -> dict:
    """휴지통 대상을 삭제 직전 상태로 복원한다(같은 ID). 복원 기한이 지났으면 TRASH-EXPIRED. 복원은 승인·적용·확정·작업 재개가 아니다.
    제안은 같은 제안 ID 로 돌아온다(적용 전 다시 확인) — 그 자리에 새 제안이 있으면 CONFLICT. 승인 대기는 같은 원본의 새 대기가 있으면 CONFLICT."""
    return db.trash_restore(uid_of(ctx), kind, target_id, expected_revision)


@mcp.tool()
@guard
def trash_purge(
    kind: Annotated[TrashKind, Field(description=_TRASH_KIND_DESC)],
    target_id: Annotated[str, Field(description="대상 ID")],
    expected_revision: Annotated[int | None, Field(description="item·person·reflection 의 지금 revision(선택). 그 밖 종류는 null")],
    ctx: Context,
) -> dict:
    """휴지통 대상의 영구 삭제를 요청한다 — 지금부터 복원할 수 없다. 본문은 서버 정리 때 지워진다. 사용자가 분명히 말할 때만 부른다."""
    return db.trash_purge(uid_of(ctx), kind, target_id, expected_revision)


# ── board 이전 읽기 도구 (2026-10-20) — 활동·관계·「나」 집계·승인 대기. 쓰기 없음(승인·제외는 웹에서만) ──

@mcp.tool()
@guard
def activity_list(
    ctx: Context,
    action: Annotated[Literal["", "like", "repost", "comment", "read", "save", "send", "reply"], Field(description="행동으로 거르기 (선택)")] = "",
    person_id: Annotated[str, Field(description="사람 ID 로 거르기 (선택)")] = "",
    source_id: Annotated[str, Field(description="출처 ID 로 거르기 (선택)")] = "",
    axis_item_id: Annotated[str, Field(description="축 항목 ID 로 거르기 (선택)")] = "",
    dated: Annotated[Literal["", "yes", "no"], Field(description="yes=날짜 확인된 것만, no=날짜 미확인만 (선택)")] = "",
    cursor: Annotated[str, Field(description="다음 쪽: 앞 응답의 next_cursor 그대로 (처음은 빈 값)")] = "",
    limit: Annotated[int, Field(description="한 번에 볼 수 (기본 50, 최대 100)")] = 50,
) -> dict:
    """연결 장치가 보낸 내 활동(좋아요 등) 목록 — 현재 판만, 받은 순 최근 먼저. 행동·날짜·출처 종류·사람 별칭·축만 있고 원문은 없다
    (원문은 사용자 PC 에만). 활동량을 성격·능력으로 해석하지 않는다."""
    return activity.activity_list(uid_of(ctx), action, person_id, source_id, axis_item_id, dated, cursor, limit)


@mcp.tool()
@guard
def activity_get(activity_id: Annotated[str, Field(description="활동 ID (a_…)")], ctx: Context) -> dict:
    """활동 하나 — 행동·날짜(occurred_on, 모르면 null)·처음 수집한 날·사람 별칭·축·승인된 메모·판 이력. 원문·원 URL 은 서버에 없다."""
    return activity.activity_get(uid_of(ctx), activity_id)


@mcp.tool()
@guard
def person_context_get(person_id: Annotated[str, Field(description="사람 ID (p_…)")], ctx: Context) -> dict:
    """사람 하나에 연결된 활동·기록·항목(역방향). 별칭만 — 실명·연락처는 서버에 없다."""
    return activity.person_context(uid_of(ctx), person_id)


@mcp.tool()
@guard
def source_context_get(source_id: Annotated[str, Field(description="출처 ID (s_…)")], ctx: Context) -> dict:
    """출처 하나에 속한 활동 — 행동별 수와 최근 활동."""
    return activity.source_context(uid_of(ctx), source_id)


@mcp.tool()
@guard
def me_aggregate_get(
    ctx: Context,
    period: Annotated[Literal["7d", "30d", "90d", "all"], Field(description="기간 — KST 오늘 포함 최근 N일 또는 전체")] = "30d",
) -> dict:
    """「나」 집계 — 정해진 계산 규칙(행동별·축별·자주 반응한 사람·출처별·날짜 미확인·기준의 근거 연결 수). 성향·점수가 아니다.
    이 숫자로 사용자를 해석하거나 정본에 저장하지 않는다."""
    return activity.me_aggregate(uid_of(ctx), period)


@mcp.tool()
@guard
def inbox_list(
    ctx: Context,
    state: Annotated[Literal["pending", "applied", "noop", "excluded", "expired", "all"], Field(description="상태로 거르기")] = "pending",
    cursor: Annotated[str, Field(description="다음 쪽: 앞 응답의 next_cursor 그대로 (처음은 빈 값)")] = "",
    limit: Annotated[int, Field(description="한 번에 볼 수 (기본 50, 최대 100)")] = 50,
) -> dict:
    """승인 대기(연결 장치가 보낸 자기 글·AI 요약) 목록. 본문은 inbox_get 으로. 승인·제외는 사용자가 웹에서만 한다 — 대신 하지 않는다."""
    return imports.inbox_list(uid_of(ctx), state, cursor, limit)


@mcp.tool()
@guard
def inbox_get(import_id: Annotated[str, Field(description="승인 대기 ID (ie_…)")], ctx: Context) -> dict:
    """승인 대기 하나 + 본문(대기 중일 때만). AI 요약은 승인해도 직접 쓴 원문이 아니고 인용할 수 없다."""
    return imports.inbox_get(uid_of(ctx), import_id)


# ── 웹 ─────────────────────────────────────────────────

async def api_imports(request):
    """POST /api/imports — 연결 장치 토큰(Bearer)만. 웹 쿠키·MCP 토큰은 받지 않는다. 응답 본문에 요청 값을 되돌려 주지 않는다.
    오류 = {"code", "msg"} + imports.http_status(code). 사용 집계 name = api:imports."""
    raw = await request.body()
    uid, code = None, "EXC"
    try:
        status, body = await run_in_threadpool(imports.submit, request.headers.get("authorization", ""),
                                                request.headers.get("idempotency-key", ""), raw)
        uid, code = None, "OK"
        return JSONResponse(body, status_code=status, headers={"cache-control": "no-store"})
    except db.CrmError as e:
        code = e.code
        return _imports_err(e)
    finally:
        db.usage_count(uid, "api:imports", code)


def _imports_err(e):
    """장치 경로 오류 — {code, msg[, resume_at]}. 요청 값은 넣지 않는다."""
    body = {"code": e.code, "msg": e.message}
    if getattr(e, "resume_at", None):
        body["resume_at"] = e.resume_at
    return JSONResponse(body, status_code=imports.http_status(e.code), headers={"cache-control": "no-store"})


async def api_import_device(request):
    """GET /api/imports/device — 장치 토큰으로 자기 입력원(정책·키 공간·만료) 확인. POST /api/imports/device/revoke — 자기 토큰 폐기."""
    fn = imports.device_self_revoke if request.method == "POST" else imports.device_self
    code = "EXC"
    try:
        body = await run_in_threadpool(fn, request.headers.get("authorization", ""))
        code = "OK"
        return JSONResponse(body, headers={"cache-control": "no-store"})
    except db.CrmError as e:
        code = e.code
        return _imports_err(e)
    finally:
        db.usage_count(None, "api:import_device", code)


async def api_import_status(request):
    """GET /api/imports/{import_id} — 같은 장치가 낸 접수 결과(응답 유실 뒤 확인용)."""
    code = "EXC"
    try:
        body = await run_in_threadpool(imports.entry_status, request.headers.get("authorization", ""), request.path_params["import_id"])
        code = "OK"
        return JSONResponse(body, headers={"cache-control": "no-store"})
    except db.CrmError as e:
        code = e.code
        return _imports_err(e)
    finally:
        db.usage_count(None, "api:import_status", code)


async def health(request):
    try:
        ok = await run_in_threadpool(db.ping)
    except Exception:
        ok = False
    return JSONResponse({"ok": ok, "db": ok}, status_code=200 if ok else 503)


def _transport_security() -> TransportSecuritySettings:
    """DNS rebinding 보호. 공개 호스트와 로컬만 받는다."""
    public = urlparse(PUBLIC_URL)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public.netloc, "127.0.0.1:*", "localhost:*"],
        allowed_origins=[f"{public.scheme}://{public.netloc}", "http://127.0.0.1:*", "http://localhost:*"],
    )


class BodyLimit:
    """① 요청 본문 상한 — 순수 ASGI(receive 만 다룬다, send 는 손대지 않는다 → /mcp SSE 스트리밍과 무관).
    /mcp·/api/* 에만. Content-Length 가 있으면 그 값으로 판정하고 그대로 통과(선언 길이는 HTTP 서버가 강제한다).
    없으면(chunked) 상한+1 바이트까지 읽어 판정하고, 통과하면 읽은 본문을 한 메시지로 다시 넘긴다. 넘으면 413 + PAYLOAD-LIMIT."""

    def __init__(self, app, limit: int = db.MAX_BODY_BYTES):
        self.app, self.limit = app, limit

    @staticmethod
    def covered(path: str) -> bool:
        return path == "/mcp" or path.startswith("/mcp/") or path.startswith("/api/")

    async def reject(self, scope, send):
        msg = f"요청 본문이 너무 커요 (상한 {self.limit} 바이트)."
        if scope["path"].startswith("/api/"):
            body = {"code": "PAYLOAD-LIMIT", "msg": msg}
        else:   # MCP transport 의 오류 응답과 같은 꼴(JSON-RPC error, id null)
            body = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": f"[PAYLOAD-LIMIT] {msg}"}}
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": raw})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.covered(scope["path"]):
            return await self.app(scope, receive, send)
        cl = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        if cl is not None:
            try:
                n = int(cl)
            except ValueError:
                n = None
            if n is not None:
                if n > self.limit:
                    return await self.reject(scope, send)
                return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            m = await receive()
            if m["type"] != "http.request":   # 끊김 — 받은 그대로 앱에 넘긴다
                break
            chunks.append(m.get("body", b""))
            size += len(chunks[-1])
            if size > self.limit:
                return await self.reject(scope, send)
            if not m.get("more_body", False):
                break
        first = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}] if m["type"] == "http.request" else [m]

        async def replay():
            return first.pop() if first else await receive()

        return await self.app(scope, replay, send)


# Render Free 는 유휴 때 프로세스가 죽는다 → 세션을 메모리에 들지 않는 stateless + JSON 응답.
mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=_transport_security())


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    # Mount 된 앱의 lifespan 은 돌지 않으므로 session_manager 를 여기서 돌린다.
    await run_in_threadpool(db.init_schema)
    try:   # β2 첫 관리자 지정 — 감사에 bootstrap·recover 행이 없을 때만 env 를 쓴다. 실패해도 기동은 계속(승격 없음, 다음 시작 때 다시)
        ids = [x.strip() for x in env("ADMIN_USER_IDS", "").split(",") if x.strip()]
        n = await run_in_threadpool(db.admin_bootstrap, ids)
        if n is not None:
            print(f"[admin] 첫 관리자 지정(bootstrap) — {n}명 승격", flush=True)
    except Exception as e:
        print(f"[admin] bootstrap 실패 — 기동은 계속: {type(e).__name__}", flush=True)
    await purge_once()   # 시작 때 1회 — 그 뒤는 _purger 가 PURGE_EVERY 마다
    flusher = asyncio.create_task(_usage_flusher())
    purger = asyncio.create_task(_purger())
    try:
        async with mcp.session_manager.run():
            yield
    finally:   # 순서 고정: 주기 태스크 취소 → 마지막 flush 1회 → db.close()
        for task in (flusher, purger):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await run_in_threadpool(db.usage_flush)
        db.close()


async def purge_once():
    """파기 3종 — 종결 180일 지난 문의(답변·이력은 cascade, 삭제 통지·contact 문의 제외) · 1년 지난 감사 · purge_after 지난 contact(PR B).
    각자 실패해도 예외를 내지 않는다(다음 회차에 다시). 로그는 건수만."""
    try:
        n = await run_in_threadpool(db.tickets_purge)
        if n:
            print(f"[desk] 종결 {db.TICKET_KEEP_DAYS}일 지난 문의 {n}건 파기", flush=True)
    except Exception as e:
        print(f"[desk] 문의 파기 실패 — 계속: {type(e).__name__}", flush=True)
    try:
        n = await run_in_threadpool(db.admin_audit_purge)
        if n:
            print(f"[admin] {db.AUDIT_KEEP_DAYS}일 지난 감사 {n}건 파기", flush=True)
    except Exception as e:
        print(f"[admin] 감사 파기 실패 — 계속: {type(e).__name__}", flush=True)
    try:
        n = await run_in_threadpool(db.contacts_purge)
        if n:
            print(f"[delete] 보관 기간 지난 삭제 contact {n}건 파기", flush=True)
    except Exception as e:
        print(f"[delete] contact 파기 실패 — 계속: {type(e).__name__}", flush=True)
    try:   # board 이전 — 보관 기간(30일) 지난 승인 대기 본문 파기 · 지난 한도 카운터 파기
        n = await run_in_threadpool(imports.expire_pending)
        m = await run_in_threadpool(imports.counters_purge)
        if n or m:
            print(f"[imports] 승인 대기 만료 {n}건 · 지난 한도 카운터 {m}행 파기", flush=True)
    except Exception as e:
        print(f"[imports] 승인 대기 만료·카운터 파기 실패 — 계속: {type(e).__name__}", flush=True)
    try:   # PR ②-a — 작업 만료·임대 만료 정리 · 임시 보관 본문 삭제 · 종료 표식 보유 기간 지난 행 삭제
        n = await run_in_threadpool(db.jobs_purge)
        if n:
            print(f"[jobs] 작업 정리 {n}건", flush=True)
    except Exception as e:
        print(f"[jobs] 작업 정리 실패 — 계속: {type(e).__name__}", flush=True)


async def trash_purge_round():
    """휴지통 물리 정리 한 회차 — 활동 원천 → 승인 대기(PR-2) → 기록 → 항목 → 사람 → 생각 → 세션·보존 제안(PR-2).
    회차 전체 시간·대상 수 상한(db.TRASH_PURGE_SECONDS·LIMIT),
    대상마다 트랜잭션·statement_timeout, 실패 대상은 건너뛰고 다음 회차. 예외를 내지 않는다. 로그는 건수만."""
    import time
    t0 = time.monotonic()
    a = {"activities": 0, "failed": 0, "left": False}
    try:   # 활동 원천을 먼저 — 활동 출발 관계(activity_links)가 사람·축·기록을 가리킨다
        a = await run_in_threadpool(activity.trash_purge_due, db.TRASH_PURGE_SECONDS, db.TRASH_PURGE_LIMIT)
    except Exception as e:
        print(f"[trash] 활동 원천 정리 실패 — 계속: {type(e).__name__}", flush=True)
    p = {"inbox": 0, "failed": 0, "left": False}
    try:   # PR-2 승인 대기 본문 — 남은 시간·대상 수만 이어서(회차 전체 상한)
        left = max(0.0, db.TRASH_PURGE_SECONDS - (time.monotonic() - t0))
        p = await run_in_threadpool(imports.trash_purge_due, left, max(0, db.TRASH_PURGE_LIMIT - a["activities"] - a["failed"]))
    except Exception as e:
        print(f"[trash] 승인 대기 정리 실패 — 계속: {type(e).__name__}", flush=True)
    try:   # 남은 시간·대상 수만 이어서(회차 전체 상한)
        left = max(0.0, db.TRASH_PURGE_SECONDS - (time.monotonic() - t0))
        used = a["activities"] + a["failed"] + p["inbox"] + p["failed"]
        out = await run_in_threadpool(db.trash_purge_due, left, max(0, db.TRASH_PURGE_LIMIT - used))
        n = sum(v for k, v in out.items() if k not in ("failed", "left"))
        failed = a["failed"] + p["failed"] + out["failed"]
        if a["activities"] or p["inbox"] or n or failed:
            print(f"[trash] 휴지통 정리 — 활동 원천 {a['activities']}건 · 승인 대기 {p['inbox']}건 · 그 밖 {n}건 · 실패 {failed}건"
                  + (" · 상한 도달(다음 회차에 이어서)" if a["left"] or p["left"] or out["left"] else ""), flush=True)
    except Exception as e:
        print(f"[trash] 휴지통 정리 실패 — 계속: {type(e).__name__}", flush=True)


async def _purger():
    """실행 중 매일 1회 파기(P3). 시작 때 1회 purge_once 는 lifespan 이 따로 부른다.
    휴지통 물리 정리는 이 태스크의 첫 회차(서빙 시작 뒤 — lifespan 이 기다리지 않는다)부터 매 회차. Render Free 는 잠들면 안 돈다 —
    기한 판정은 조회 조건이라 정리가 늦어도 복원·열람은 막힌다."""
    while True:
        await trash_purge_round()
        await asyncio.sleep(PURGE_EVERY)
        await purge_once()


async def _usage_flusher():
    """usage_daily 주기 flush(α2). db.usage_flush 는 실패해도 예외를 내지 않는다."""
    while True:
        await asyncio.sleep(USAGE_FLUSH_EVERY)
        await run_in_threadpool(db.usage_flush)


# 웹 화면·로그인. Mount("/") 가 나머지를 전부 받으므로 웹 경로는 그 앞에 둔다.
site = web.Web(ISSUER, PUBLIC_URL, _auth_kwargs.get("token_verifier"), DEV, env("DEV_USER", "dev@local"))
if not DEV and not site.login_ready:
    print("[web] AUTHKIT_WEB_CLIENT_ID·CRM_SESSION_SECRET 이 없어 웹 로그인은 503 — MCP 는 정상", flush=True)

app = Starlette(
    routes=[
        Route("/health", health),
        Route("/api/imports", api_imports, methods=["POST"]),                 # board 이전 — 장치 토큰(웹 세션 아님)
        Route("/api/imports/device", api_import_device, methods=["GET"]),              # 경로 순서: {import_id} 보다 먼저
        Route("/api/imports/device/revoke", api_import_device, methods=["POST"]),
        Route("/api/imports/{import_id}", api_import_status, methods=["GET"]),
        *site.routes(),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
    middleware=[Middleware(BodyLimit)],
)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT") or env("PORT") or "8800")
    uvicorn.run(app, host="0.0.0.0", port=port)
