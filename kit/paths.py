"""루트 경로·주인 이름 한 자리.

- root(): 데이터 루트. 환경변수 CANON_ROOT 가 있으면 그것, 없으면 repo 의 data/.
- code(): 이 kit 폴더 (서버·워커·html 이 사는 곳). 데이터와 섞지 않는다.
- owner(): 프롬프트에 들어가는 주인 이름. CANON_OWNER, 기본 "나".
- port(): CANON_PORT, 기본 8787.
"""
import os
from pathlib import Path

_CODE = Path(__file__).resolve().parent


def root():
    env = os.environ.get("CANON_ROOT")
    r = Path(env).expanduser().resolve() if env else _CODE.parent / "data"
    # 로그·캐시 자리 (<root>/.runtime) — 쓰는 쪽이 여럿이라 여기서 한 번 만든다
    try:
        (r / ".runtime").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return r


def code():
    return _CODE


def owner():
    return os.environ.get("CANON_OWNER") or "나"


def port():
    try:
        return int(os.environ.get("CANON_PORT", "8787"))
    except ValueError:
        return 8787


def owner_fmt():
    """str.format 템플릿 안에 넣을 이름 — 중괄호를 이스케이프한다."""
    return owner().replace("{", "{{").replace("}", "}}")
