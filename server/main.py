"""personal-canon-kit 서버 — 원격 MCP(Streamable HTTP) + 웹 화면.

사용자 결정: AuthKit JWT 검증(sub 우선, 처음 연결 땐 userinfo 의 검증된 이메일). 개발·시험은 CANON_DEV=1 + CANON_DEV_USER.
모델 호출은 없다 — 후보 추출은 사용자의 대화(ChatGPT·Claude)가 하고, 서버는 근거 검사·저장만 한다.
환경변수: DATABASE_URL · CANON_PUBLIC_URL(예 https://xxx.onrender.com) · AUTHKIT_ISSUER(예 https://xxx.authkit.app)
          · AUTHKIT_WEB_CLIENT_ID · CANON_SESSION_SECRET(웹 로그인, web.py) · CANON_DEV/CANON_DEV_USER(개발만)
"""
import functools
import os
from urllib.parse import urlparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

import auth
import db
import web

INSTRUCTIONS = """이 서버는 사용자의 「내 판단 노트」다. 사용자의 기록에서 판단 후보를 찾고, 사용자가 원문과 대조해 남길지 정한다.

규칙:
- 기록(record_add)은 사용자의 말을 그대로 저장한다. 다듬거나 요약하지 않는다.
- 후보(candidates_propose)는 네가 뽑되, 각 후보의 quote 는 그 기록 원문에서 글자 그대로 복사한 연속 구간이어야 한다. 원문에 같은 구간이 두 번 이상 나오면 앞뒤를 포함해 한 곳만 가리키게 더 길게 인용한다. 원문에 없는 사실·수치를 만들지 않는다. 타인의 말로 보이는 문장은 후보로 내지 않는다. 기록 1편당 최대 3개 — 넘기면 거절된다.
- 후보는 사용자의 판단이 아니다. 사용자가 원문과 대조해 "남길게/빼줘/고쳐서 남길게"라고 말할 때만 candidate_decide 를 부른다. 네가 대신 confirm 하지 않는다. 확정 전에 후보 문장을 사용자에게 그대로 보여 준다.
- 사용자가 묻지 않은 설정(프로필·질문)을 추정으로 확정하지 않는다. 추정은 status=guessed 로만 저장한다.
- 기준(principle 항목)은 후보가 아니다. 여러 확정 판단을 종합해 사용자가 정한 한 문장이다. 후보 문장을 기준으로 옮겨 적거나, 사용자가 말하기 전에 item_decide 로 적용·확정하지 않는다. 정의·항목은 *_propose 로 제안만 한다.
- 승격 조건(promotion)은 사실일 뿐이다. 충족 여부와 상관없이 기준으로 남길지는 사용자가 정한다.
- 질문에 답하지 않는 후보는 candidates_propose 의 question_id 를 빈 문자열("")로 보낸다. 화면은 기록별로 묶는다.
- 이 노트의 쓰는 법·용어 질문은 help_get 으로 해당 topic(start 또는 words) 하나만 읽고 그 문서 안에서 답한다. 같은 대화에서 이미 읽었으면 다시 부르지 않는다. 문서에 없으면 '문서에서 확인되지 않아요' 라고 하고 가장 가까운 절을 안내한다. 제품 밖 일반 질문엔 적용하지 않는다.
"""

DEV = os.environ.get("CANON_DEV") == "1"
PUBLIC_URL = os.environ.get("CANON_PUBLIC_URL", "http://127.0.0.1:8800").rstrip("/")
ISSUER = os.environ.get("AUTHKIT_ISSUER", "").rstrip("/")

if not DEV and not ISSUER:
    raise SystemExit("AUTHKIT_ISSUER 가 없어요. 운영은 인증 없이 뜨지 않아요 (개발은 CANON_DEV=1).")
if not DEV and os.environ.get("CANON_DEV_USER"):
    raise SystemExit("CANON_DEV_USER 는 CANON_DEV=1 일 때만 쓸 수 있어요.")

_auth_kwargs = {}
if ISSUER:
    _auth_kwargs = dict(
        token_verifier=auth.AuthKitVerifier(ISSUER, f"{PUBLIC_URL}/mcp"),
        # 문자열로 넘긴다. AnyHttpUrl(...) 을 밖에서 만들면 issuer 끝에 / 가 붙어 PRM authorization_servers 가 AS 메타데이터 issuer 와 어긋난다.
        auth=AuthSettings(issuer_url=ISSUER, resource_server_url=f"{PUBLIC_URL}/mcp",
                          required_scopes=["openid", "email"], validate_token_resource=True),
    )

mcp = MCPServer("personal-canon-kit", instructions=INSTRUCTIONS, **_auth_kwargs)


def uid_of(ctx: Context) -> str:
    tok = get_access_token()
    if tok is not None:
        return auth.user_id_for(tok)
    if DEV:
        return db.user_by_email(os.environ.get("CANON_DEV_USER", "dev@local"))["id"]
    raise ToolError("[AUTH] 로그인 정보가 없어요. 커넥터를 다시 연결하세요.")


def guard(fn):
    """CanonError 를 모델이 읽을 수 있는 ToolError 로 바꾼다. wraps 로 서명을 보존해야 입력 스키마가 유지된다."""
    @functools.wraps(fn)
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except db.CanonError as e:
            raise ToolError(f"[{e.code}] {e.message}" + (f" 다음 조치: {e.action}" if e.action else ""))
    return run


# ── 도구 ───────────────────────────────────────────────

@mcp.tool()
@guard
def state_get(ctx: Context) -> dict:
    """내 노트의 현재 상태 한 벌 — 프로필·질문·탭·후보·기록 목록·다음 할 일. 대화를 시작할 때 먼저 부른다."""
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
    source: Annotated[str, Field(description="출처 (예: 대화에서 입력, 회의 메모)")] = "대화에서 입력",
) -> dict:
    """기록 1편을 원문 그대로 남긴다. 저장 뒤 사용자에게 '지금 후보를 뽑을까요?' 하나만 묻는다."""
    r = db.record_add(uid_of(ctx), title, text, question_id or None, source)
    return {"id": r["id"], "title": r["title"], "created": r["created"]}


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
    """사용자가 원문과 대조한 뒤 말한 결정을 저장한다. 사용자가 말하기 전에 부르지 않는다. 이전 상태·문장은 이력에 남아 되돌릴 수 있다."""
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
        raise db.CanonError("ARG-INVALID", f"{name} 가 JSON 이 아니에요.")
    if not isinstance(v, dict):
        raise db.CanonError("ARG-INVALID", f"{name} 는 JSON 객체여야 해요.")
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
    """내가 정의한 오브젝트(기준·결정 같은 자료 구조)와 필드. item_propose 전에 object_id·revision·필드 key 를 여기서 확인한다."""
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
                      Field(description="apply=제안 적용, discard=제안 버리기, confirm=기준을 내 판단으로 남기기, exclude=남기지 않기, "
                                        "pending=아직 안 정함으로, archive=보관, restore=history_id 시점으로 되돌리기")],
    history_id: Annotated[int | None, Field(description="restore 때 이력 ID. 그 외 null")],
    ctx: Context,
) -> dict:
    """사용자가 말한 결정을 항목에 저장한다. 사용자가 말하기 전에 부르지 않는다. confirm·exclude·pending 은 기준(principle)만.
    confirm 결과의 promotion 은 승격 조건 충족 여부(사실)일 뿐 — 미충족이어도 저장되고, 사용자에게 그대로 알린다."""
    return db.item_decide(uid_of(ctx), item_id, expected_revision, proposal_id or None, action, history_id)


@mcp.tool()
@guard
def record_attach(
    record_id: Annotated[str, Field(description="기록 ID")],
    ctx: Context,
    attach_to: Annotated[list[Link] | None, Field(description="이 기록이 붙는 기준·결정 항목 또는 후보. field_key 는 attach_to. null 이면 그대로")] = None,
    axes: Annotated[list[str] | None, Field(description="관심 축 항목 ID 목록. null 이면 그대로")] = None,
    use_at: Annotated[str | None, Field(description="쓸 자리 메모. null 이면 그대로")] = None,
    unattached: Annotated[str | None, Field(description="아직 안 붙는 것 메모(새 축 후보 등). null 이면 그대로")] = None,
) -> dict:
    """기록이 어디에 붙는지 남긴다(기준·결정·후보 연결, 관심 축, 쓸 자리, 아직 안 붙는 것). 원문은 바뀌지 않는다. 값을 준 칸만 바뀐다."""
    return db.record_attach(uid_of(ctx), record_id, [ln.model_dump() for ln in attach_to] if attach_to is not None else None,
                            axes, use_at, unattached)


# ── 도움말 ─────────────────────────────────────────────
# 웹 /help 와 같은 원문(guides/09·10, web.Web.load_help 가 시작 때 읽음)을 돌려준다.

@mcp.tool()
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


# ── 웹 ─────────────────────────────────────────────────

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


# Render Free 는 유휴 때 프로세스가 죽는다 → 세션을 메모리에 들지 않는 stateless + JSON 응답.
mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=_transport_security())


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    # Mount 된 앱의 lifespan 은 돌지 않으므로 session_manager 를 여기서 돌린다.
    await run_in_threadpool(db.init_schema)
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        db.close()


# 웹 화면·로그인. Mount("/") 가 나머지를 전부 받으므로 웹 경로는 그 앞에 둔다.
site = web.Web(ISSUER, PUBLIC_URL, _auth_kwargs.get("token_verifier"), DEV, os.environ.get("CANON_DEV_USER", "dev@local"))
if not DEV and not site.login_ready:
    print("[web] AUTHKIT_WEB_CLIENT_ID·CANON_SESSION_SECRET 이 없어 웹 로그인은 503 — MCP 는 정상", flush=True)

app = Starlette(
    routes=[
        Route("/health", health),
        *site.routes(),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT") or os.environ.get("CANON_PORT") or "8800")
    uvicorn.run(app, host="0.0.0.0", port=port)
