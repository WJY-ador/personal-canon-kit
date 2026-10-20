"""사람 식별 HMAC — r3 T4.

HMAC-SHA256(PC 256비트 키, JCS(["person/1", 제공자 코드, 확인된 원본 계정 식별자]))
- 표시 이름만으로 만들지 않는다(호출자가 계정 식별자를 넘긴다).
- 제공자가 다르면 같은 계정 문자열이라도 다른 값.
- 키 공간(hmac_namespace)마다 키가 다르다. 키 교체 = 새 키 공간, 자동 재연결 없음.
- HMAC 은 인증 수단이 아니다.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import re
import secrets

from . import jcs

KEY_BYTES = 32
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class HmacError(ValueError):
    pass


def new_key_hex() -> str:
    return secrets.token_hex(KEY_BYTES)


def key_from_hex(key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(key_hex or "")
    except ValueError:
        raise HmacError("key not hex") from None
    if len(key) != KEY_BYTES:
        raise HmacError("key must be 256 bits")
    return key


def person_hmac(key: bytes, provider_code: str, account_id: str) -> str:
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_BYTES:
        raise HmacError("key must be 256 bits")
    if not isinstance(provider_code, str) or not _PROVIDER_RE.match(provider_code):
        raise HmacError("provider code")
    if not isinstance(account_id, str) or not account_id.strip():
        raise HmacError("account id empty")
    msg = jcs.canonical_bytes(["person/1", provider_code, account_id])
    return _hmac.new(bytes(key), msg, hashlib.sha256).hexdigest()
