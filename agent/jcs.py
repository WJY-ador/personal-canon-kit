"""JCS(RFC 8785) 직렬화 — 이 계약이 쓰는 값(객체·배열·문자열·정수·불리언·null)만.

부동소수는 계약에 없으므로 거절한다(숫자 표기 차이로 해시가 갈리는 일을 막는다).
키 정렬은 UTF-16 코드 단위 순서(RFC 8785 §3.2.3).
"""
from __future__ import annotations

import hashlib
import json

_MAX_SAFE_INT = 2 ** 53 - 1


class JCSError(ValueError):
    pass


def _check_str(s: str) -> None:
    for ch in s:
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:
            raise JCSError("lone surrogate")


def _ser(v, out: list) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int):
        if abs(v) > _MAX_SAFE_INT:
            raise JCSError("integer out of I-JSON range")
        out.append(str(v))
    elif isinstance(v, float):
        raise JCSError("float not allowed in this contract")
    elif isinstance(v, str):
        _check_str(v)
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _ser(x, out)
        out.append("]")
    elif isinstance(v, dict):
        keys = list(v.keys())
        for k in keys:
            if not isinstance(k, str):
                raise JCSError("object key must be string")
            _check_str(k)
        keys.sort(key=lambda k: k.encode("utf-16-be"))
        out.append("{")
        for i, k in enumerate(keys):
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _ser(v[k], out)
        out.append("}")
    else:
        raise JCSError("unsupported type: " + type(v).__name__)


def canonicalize(v) -> str:
    out: list = []
    _ser(v, out)
    return "".join(out)


def canonical_bytes(v) -> bytes:
    return canonicalize(v).encode("utf-8")


def sha256_hex(v) -> str:
    return hashlib.sha256(canonical_bytes(v)).hexdigest()
