"""CRM_ 접두 env 읽기 헬퍼 — env("PUBLIC_URL") = CRM_PUBLIC_URL.

없으면 default. 빈 문자열은 그대로 돌려준다(os.environ.get 과 같다).
매번 os.environ 을 읽는다 — 시험이 실행 중에 값을 바꾼다.
"""

import os

PREFIX = "CRM_"


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(PREFIX + name, default)


# board 이전·연결 장치 스위치(구현계획 §2-10·§2-11 D5) — 코드 기본값 꺼짐. 값이 정확히 "1" 일 때만 켜진다.
# 운영 env 는 켤 때만 하나씩 더한다(배포 때 미리 넣지 않는다). 각 쓰기 직전에 매번 읽는다(재시작 없이 시험이 바꾼다).
SWITCHES = ("IMPORTS_ENABLED",                       # /api/imports 수신 전체
            "ACTIVITY_IMPORTS_ENABLED",              # 활동 즉시 적재
            "UNATTENDED_SUMMARY_IMPORTS_ENABLED",    # 무인(execution=unattended) AI 요약 접수 — 약관 확인 전 꺼 둔다
            "INBOX_APPROVALS_ENABLED",               # 승인 대기 승인(제외·만료는 스위치와 무관)
            "BOARD_MIGRATION_ENABLED",               # board 이전 CLI 적용
            "CONVERSE_ENABLED", "ROUTINE_ENABLED")   # PR ②-a 작업 전달 — 대화(+원문 던지기)·루틴 작업. 읽기 자료(READING)는 ②-d 에서


def switch_on(name: str) -> bool:
    """SWITCHES 의 하나가 켜졌나 — env CRM_<name> == "1". 모르는 이름은 ValueError(오타로 늘 꺼지는 것을 막는다)."""
    if name not in SWITCHES:
        raise ValueError(name)
    return env(name, "") == "1"
