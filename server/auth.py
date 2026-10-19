"""AuthKit 토큰 검증과 사용자 결정.

- AuthKitVerifier: MCP SDK TokenVerifier. JWKS(RS256)로 서명·iss·exp·aud 를 검사한다. 실패는 전부 None(→ 401).
  aud 는 클라이언트가 요청한 resource indicator 다. resource 없이 받은 토큰(aud = 환경 client ID)은 거절된다.
- user_id_for: 검증된 토큰의 sub 로 사용자를 찾는다. 처음 보는 sub 면 userinfo 에서 검증된 이메일을 받아 연결한다.
  AuthKit 액세스 토큰엔 email 클레임이 없다(공식 token-claims 문서). userinfo 의 email·email_verified 는
  토큰에 email scope 가 있어야 나온다 → main 의 required_scopes=["openid","email"] 로 PRM 이 광고하게 한다.
"""
import anyio.to_thread
import httpx
import jwt

from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError

import db

ALGORITHMS = ["RS256"]


def scopes_of(claims: dict) -> list[str]:
    """scope(공백 구분 문자열)·scp(배열 또는 문자열) 둘 다 읽는다. 없으면 빈 목록 — 거절은 미들웨어(403)가 한다."""
    out: list[str] = []
    for k in ("scope", "scp"):
        v = claims.get(k)
        items = v.split() if isinstance(v, str) else v if isinstance(v, list) else []
        out += [str(x) for x in items if str(x) and str(x) not in out]
    return out


class AuthKitVerifier:
    def __init__(self, issuer: str, resource_url: str):
        self.issuer = issuer.rstrip("/")
        self.resource_url = resource_url.rstrip("/")
        self.jwks = jwt.PyJWKClient(f"{self.issuer}/oauth2/jwks", cache_keys=True, timeout=10)

    def decode(self, token: str) -> dict:
        """서명·iss·exp·aud 검사. 실패하면 jwt 예외."""
        key = self.jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token, key.key, algorithms=ALGORITHMS,
            audience=[self.resource_url, self.resource_url + "/"],
            issuer=[self.issuer, self.issuer + "/"],
            options={"require": ["exp", "iss", "aud", "sub"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        # 미들웨어는 AuthenticationError 만 잡는다. 다른 예외는 500 이 되므로 여기서 전부 None 으로.
        try:
            claims = await anyio.to_thread.run_sync(self.decode, token)
        except Exception:
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or ""),
            scopes=scopes_of(claims),
            expires_at=int(claims["exp"]),
            resource=self.resource_url,  # aud 검사를 통과했으니 이 서버용 토큰이다
            subject=str(claims["sub"]),
            claims=claims,
        )


def _diag(tok: AccessToken, info: dict) -> str:
    """진단 꼬리표. 키 이름·scope 이름만 — email·sub 값은 넣지 않는다."""
    return f" (토큰 scope: {sorted(tok.scopes) or '없음'}, userinfo 키: {sorted(info) or '없음'})"


def _userinfo_email(tok: AccessToken) -> str:
    """WorkOS 문서: POST {issuer}/oauth2/userinfo, Bearer 액세스 토큰. 응답 클레임은 토큰 발급 때 받은 scope 에 따른다."""
    issuer = str((tok.claims or {}).get("iss", "")).rstrip("/")
    try:
        r = httpx.post(f"{issuer}/oauth2/userinfo", headers={"Authorization": f"Bearer {tok.token}"}, timeout=10)
        r.raise_for_status()
        info = r.json()
        if not isinstance(info, dict):
            raise ValueError
    except (httpx.HTTPError, ValueError):
        raise ToolError("[AUTH-USERINFO] 로그인 서버에서 이메일을 받지 못했어요. 잠시 뒤 다시 시도하거나 커넥터를 다시 연결하세요.")
    if info.get("sub") != tok.subject:
        raise ToolError("[AUTH-INVALID] 로그인 정보가 맞지 않아요. 커넥터를 다시 연결하세요." + _diag(tok, info))
    email = str(info.get("email") or "").strip()
    if not email:
        raise ToolError("[AUTH-NO-EMAIL] 로그인 서버가 이메일을 주지 않았어요. 커넥터를 지우고 다시 연결해 email 권한을 허용하세요."
                        + _diag(tok, info))
    if info.get("email_verified") is not True:
        raise ToolError("[AUTH-INVALID] 인증된 이메일이 아니에요." + _diag(tok, info))
    return email


def user_id_for(tok: AccessToken) -> str:
    """sub 로 먼저 찾고, 모르는 sub 면 userinfo 의 이메일로 연결·생성. CrmError 는 호출부 guard 가 ToolError 로 바꾼다.
    β2: uid 를 정한 뒤 그 행으로 차단(AUTH-BLOCKED)·MCP 끊기(토큰 iat < mcp_revoked_before → AUTH-REVOKED)를 본다(db.check_token_user).
    웹 로그인 콜백도 이 함수를 지난다 — 차단 계정은 웹 로그인도 안 된다. 웹 로그인 끊기(web_revoked_before)는 여기서 보지 않는다."""
    sub = tok.subject or ""
    try:
        row = db.user_by_subject(sub, "")
    except db.CrmError as e:
        if e.code != "AUTH-INVALID" or not sub:
            raise
        row = db.user_by_subject(sub, _userinfo_email(tok))
    db.check_token_user(row, (tok.claims or {}).get("iat"))
    return row["id"]
