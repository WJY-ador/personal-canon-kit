"""my-crm 로컬 에이전트 — PC 수집물을 구조화 데이터로 바꿔 /api/imports 로 올린다.

원문·실명·URL·HMAC 키는 PC 에만 둔다. 서버엔 canon-inbox/1 계약이 허용한 필드만 보낸다.
표준 라이브러리만 쓴다(Python ≥3.9).
"""
from __future__ import annotations

AGENT_VERSION = "0.1.0"
