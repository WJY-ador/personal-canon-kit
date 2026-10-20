"""입력 어댑터 — 수집기 산출물을 「관측 행동」으로 바꾼다. 모델을 쓰지 않는다.

활성 구현: likes. 골격(의미 규칙만, 기본 비활성): feed · reactions · mail · kakao.
전송 분기 차단: linkedin_moves(LinkedIn 이직 정보는 PC 에만).
"""
from __future__ import annotations

from .base import AdapterError, AdapterNotActive, Observation, Quarantine, TransmissionBlocked
from .likes import LikesAdapter
from .skeletons import SKELETONS, LinkedInMovesAdapter

ACTIVE = {"likes": LikesAdapter}
BLOCKED = {"linkedin_moves": LinkedInMovesAdapter}


def get(name: str):
    if name in BLOCKED:
        raise TransmissionBlocked(name)
    if name in ACTIVE:
        return ACTIVE[name]()
    if name in SKELETONS:
        return SKELETONS[name]()
    raise AdapterError("unknown adapter")


__all__ = ["AdapterError", "AdapterNotActive", "Observation", "Quarantine", "TransmissionBlocked",
           "LikesAdapter", "get", "ACTIVE", "BLOCKED", "SKELETONS"]
