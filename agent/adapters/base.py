"""어댑터 공통 형식."""
from __future__ import annotations

from dataclasses import dataclass, field


class AdapterError(RuntimeError):
    pass


class AdapterNotActive(AdapterError):
    """의미 규칙만 있는 골격 어댑터 — 샘플로 필드를 확정하기 전에는 켤 수 없다."""


class TransmissionBlocked(AdapterError):
    """서버 전송 분기 자체가 막힌 입력원(LinkedIn 이직 정보)."""


@dataclass
class Observation:
    """사용자가 실제로 한 행동 1건. 서버로 갈 수 있는 값은 action·state·날짜·provider·people·axes 뿐.

    dedupe_key·raw·people 의 account_id 는 PC 에만 남는다.
    """
    dedupe_key: str                 # PC 대응표 중복 기준(제공자 이벤트 ID 등) — 서버로 안 감
    fingerprint: str                # 같은 dedupe_key 의 신원 일치 확인용 다이제스트
    raw: dict                       # PC 원문 저장소에 넣을 원 행
    action: str
    state: str
    occurred_on: str | None         # 명시적 행동 날짜. 없으면 None(수집 시각으로 대체 금지)
    observed_on: str | None         # 수집기가 처음 본 날
    provider: str                   # 서버 activity_provider enum
    people: list = field(default_factory=list)   # [(provider_code, account_id, role)]
    axes: list = field(default_factory=list)


@dataclass
class Quarantine:
    reason: str
    key: str                        # 다이제스트로만 저장된다


class Adapter:
    name = ""
    kind = "activity"
    transform_version = ""

    def check_config(self, section: dict) -> None:
        raise NotImplementedError

    def collect(self, section: dict):
        raise NotImplementedError
