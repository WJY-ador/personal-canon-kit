"""AuthKit 토큰 검증과 사용자 결정.

- AuthKitVerifier: MCP SDK TokenVerifier. JWKS(RS256)로 서명·iss·exp·aud 를 검사한다. 실패는 전부 None(→ 401).
  aud 는 클라이언트가 요청한 resource indicator 다. resource 없이 받은 토큰(aud = 환경 client ID)은 거절된다.
- user_id_for: 검증된 토큰의 sub 로 사용자를 찾는다. 처음 보는 sub 면 userinfo 에서 검증된 이메일을 받아 연결한다.
  AuthKit 액세스 토큰엔 email 클레임이 없다(공식 token-claims 문서).
"""
import anyio.to_thread
import httpx
import jwt

from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError

import db

ALGORITHMS = ["RS256"]


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
            scopes=str(claims.get("scope") or "").split(),
            expires_at=int(claims["exp"]),
            resource=self.resource_url,  # aud 검사를 통과했으니 이 서버용 토큰이다
            subject=str(claims["sub"]),
            claims=claims,
        )


def _userinfo_email(tok: AccessToken) -> str:
    issuer = str((tok.claims or {}).get("iss", "")).rstrip("/")
    try:
        r = httpx.get(f"{issuer}/oauth2/userinfo", headers={"Authorization": f"Bearer {tok.token}"}, timeout=10)
        r.raise_for_status()
        info = r.json()
    except (httpx.HTTPError, ValueError):
        raise ToolError("[AUTH-USERINFO] 로그인 서버에서 이메일을 받지 못했어요. 잠시 뒤 다시 시도하거나 커넥터를 다시 연결하세요.")
    if info.get("sub") != tok.subject:
        raise ToolError("[AUTH-INVALID] 로그인 정보가 맞지 않아요. 커넥터를 다시 연결하세요.")
    if info.get("email_verified") is False:
        raise ToolError("[AUTH-INVALID] 이메일 인증이 안 된 계정이에요.")
    return str(info.get("email") or "")


def user_id_for(tok: AccessToken) -> str:
    """sub 로 먼저 찾고, 모르는 sub 면 userinfo 의 이메일로 연결·생성. CanonError 는 호출부 guard 가 ToolError 로 바꾼다."""
    sub = tok.subject or ""
    try:
        return db.user_by_subject(sub, "")["id"]
    except db.CanonError as e:
        if e.code != "AUTH-INVALID" or not sub:
            raise
    return db.user_by_subject(sub, _userinfo_email(tok))["id"]
