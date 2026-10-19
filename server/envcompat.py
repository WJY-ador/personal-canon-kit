"""서버 env 이름 읽기 — 새 이름 CRM_* 를 먼저, 없으면 옛 이름을 읽는다(이름 변경 배포 동안의 호환).

env("PUBLIC_URL") = CRM_PUBLIC_URL 이 있으면(빈 문자열 아님) 그것, 없으면 옛 이름. 옛 이름을 쓰면 이름마다 시작 로그 1줄.
매번 os.environ 을 읽는다 — 시험이 실행 중에 값을 바꾼다.
"""

import os

NEW_PREFIX = "CRM_"
OLD_PREFIX = "CANON_"   # 호환 기간만. 옛 이름 문자열은 이 한 곳에만 둔다.
_warned: set[str] = set()


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(NEW_PREFIX + name)
    if v not in (None, ""):
        return v
    old = os.environ.get(OLD_PREFIX + name)
    if old is not None:
        if name not in _warned:
            _warned.add(name)
            print(f"[env] 옛 env 이름 {OLD_PREFIX}{name} — {NEW_PREFIX}{name} 로 옮기세요", flush=True)
        return old
    return v if v is not None else default


def names(name: str) -> tuple[str, str]:
    """새·옛 이름 둘 다 — 시험이 env 를 지울 때 둘 다 지우게."""
    return NEW_PREFIX + name, OLD_PREFIX + name
