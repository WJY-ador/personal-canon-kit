"""골격 어댑터 — 의미 규칙만 담는다. 실제 필드를 샘플로 확정하기 전에는 켤 수 없다(기본 비활성).

변환 버전도 계약 TRANSFORMS 에 등록하지 않았다 — 켜도 계약 검증에서 막힌다.
공통 규칙(r3 T4):
- 파일 경로·행 번호만으로 영구 이벤트 ID 를 만들지 않는다.
- 제공자 이벤트 ID 가 있으면 PC 대응표에서 재사용, 없으면 중복 기준 확정 뒤 난수 ID 1회 발급.
- 판별할 수 없는 중복은 격리하고 집계하지 않는다.
- 명시적 행동 날짜가 없으면 occurred_on=None. 시간대를 모르면 KST 로 임의 변환하지 않는다.
- 활동 수집에 모델을 쓰지 않는다.
"""
from __future__ import annotations

from .base import Adapter, AdapterNotActive, TransmissionBlocked


class _Skeleton(Adapter):
    RULES: dict = {}

    def check_config(self, section: dict) -> None:
        if section.get("enabled"):
            raise AdapterNotActive(self.name + " adapter is a skeleton; fields not confirmed")

    def collect(self, section: dict):
        raise AdapterNotActive(self.name + " adapter is a skeleton; fields not confirmed")


class FeedAdapter(_Skeleton):
    name = "feed"
    transform_version = "board-feed/1"
    RULES = {
        "pc_only": "게시물·제목·URL·작성자 정보",
        "to_server": "명시된 읽음·보관 행동이 있을 때만 read/save",
        "forbidden": "큐에 있다는 이유로 read 생성 금지",
    }


class ReactionsAdapter(_Skeleton):
    name = "reactions"
    transform_version = "board-reactions/1"
    RULES = {
        "pc_only": "원문·인용·상대 정보",
        "to_server": "명시된 댓글·리포스트·답장·좋아요 이벤트 → comment/repost/reply/like",
        "self_text": "자기 반응 문장은 별도 self_text(검토 대기). 타인 인용은 PC",
    }


class MailAdapter(_Skeleton):
    name = "mail"
    transform_version = "board-mail/1"
    RULES = {
        "pc_only": "메일 원문·제목·주소·첨부",
        "to_server": "명시된 읽음·보관·발송·답장 이벤트 → read/save/send/reply",
        "forbidden": "수신·목록 존재만으로 사용자 행동 생성 금지",
    }


class KakaoAdapter(_Skeleton):
    name = "kakao"
    transform_version = "board-kakao/1"
    RULES = {
        "pc_only": "대화·방 이름·참여자 실명·원문",
        "to_server": "확인된 사용자 발송·답장 등 명시 이벤트 → send/reply",
        "forbidden": "타인 메시지 수·대화 요약을 활동으로 변환 금지",
    }


class LinkedInMovesAdapter(Adapter):
    """LinkedIn 이직 정보 — 서버 전송 분기 자체 차단(결정 16)."""
    name = "linkedin_moves"

    def check_config(self, section: dict) -> None:
        raise TransmissionBlocked(self.name)

    def collect(self, section: dict):
        raise TransmissionBlocked(self.name)


SKELETONS = {c.name: c for c in (FeedAdapter, ReactionsAdapter, MailAdapter, KakaoAdapter)}
