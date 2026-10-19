"""CRM_ 접두 env 읽기 헬퍼 — env("PUBLIC_URL") = CRM_PUBLIC_URL.

없으면 default. 빈 문자열은 그대로 돌려준다(os.environ.get 과 같다).
매번 os.environ 을 읽는다 — 시험이 실행 중에 값을 바꾼다.
"""

import os

PREFIX = "CRM_"


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(PREFIX + name, default)
