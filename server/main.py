"""personal-canon-kit 서버 — 원격 MCP(Streamable HTTP) + 웹 화면.

사용자 결정: AuthKit JWT 검증(sub 우선, 처음 연결 땐 userinfo 의 검증된 이메일). 개발·시험은 CANON_DEV=1 + CANON_DEV_USER.
모델 호출은 없다 — 후보 추출은 사용자의 대화(ChatGPT·Claude)가 하고, 서버는 근거 검사·저장만 한다.
환경변수: DATABASE_URL · CANON_PUBLIC_URL(예 https://xxx.onrender.com) · AUTHKIT_ISSUER(예 https://xxx.authkit.app) · CANON_DEV/CANON_DEV_USER(개발만)
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

INSTRUCTIONS = """이 서버는 사용자의 「내 판단 노트」다. 사용자의 기록에서 판단 후보를 찾고, 사용자가 원문과 대조해 남길지 정한다.

규칙:
- 기록(record_add)은 사용자의 말을 그대로 저장한다. 다듬거나 요약하지 않는다.
- 후보(candidates_propose)는 네가 뽑되, 각 후보의 quote 는 그 기록 원문에서 글자 그대로 복사한 연속 구간이어야 한다. 원문에 같은 구간이 두 번 이상 나오면 앞뒤를 포함해 한 곳만 가리키게 더 길게 인용한다. 원문에 없는 사실·수치를 만들지 않는다. 타인의 말로 보이는 문장은 후보로 내지 않는다. 기록 1편당 최대 3개 — 넘기면 거절된다.
- 후보는 사용자의 판단이 아니다. 사용자가 원문과 대조해 "남길게/빼줘/고쳐서 남길게"라고 말할 때만 candidate_decide 를 부른다. 네가 대신 confirm 하지 않는다. 확정 전에 후보 문장을 사용자에게 그대로 보여 준다.
- 사용자가 묻지 않은 설정(프로필·질문)을 추정으로 확정하지 않는다. 추정은 status=guessed 로만 저장한다.
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


# ── 웹 ─────────────────────────────────────────────────

async def api_state(request):
    if not DEV:
        return JSONResponse({"error": "unauthorized", "message": "웹 로그인은 아직 없어요."}, status_code=401)
    uid = (await run_in_threadpool(db.user_by_email, os.environ.get("CANON_DEV_USER", "dev@local")))["id"]
    return JSONResponse(await run_in_threadpool(db.state, uid))


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


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/api/state", api_state),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT") or os.environ.get("CANON_PORT") or "8800")
    uvicorn.run(app, host="0.0.0.0", port=port)
