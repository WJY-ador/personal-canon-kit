"""likes 어댑터 — 수집기 likes JSONL → `like` 활동.

필드 확정(S0, 키·타입만 확인):
  id           제공자 게시물 ID(문자열)        → PC 중복 기준. 서버로 안 감
  author       게시물 작성자 계정 핸들          → HMAC 입력(제공자 코드, 핸들). role=author
  first_seen   수집기가 처음 본 날(YYYY-MM-DD)  → observed_on
  kind / seed  행 필터(설정 include_kinds·include_null_kind·include_seed). 코드 기본은 아무 행도 안 보냄.
               kind 는 사람이 누른 분류 태그라 의미를 코드에 두지 않는다(topic 처럼 축 매핑에도 안 씀).
               설정 예시(config.LIKES_EXAMPLE): include_kinds ["*"] · include_null_kind true · include_seed true
  나머지(text·posted_at·original_url·parent·topic·source) → PC 원문 저장소에만
좋아요 행동 날짜를 주는 필드가 없다 → occurred_on 은 항상 None. posted_at(게시 시각)·first_seen 으로 대체하지 않는다.
parent.author 는 역할 정의가 없어 보내지 않는다. topic 은 축 매핑 근거로 쓰지 않는다.
파일에서 행이 사라져도 취소(retracted)를 만들지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

from .. import jcs
from .base import Adapter, AdapterError, Observation, Quarantine

_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_LINE_BYTES = 256 * 1024


def _date_or_none(v):
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return None
    try:
        _dt.date.fromisoformat(v)
    except ValueError:
        return None
    return v


class LikesAdapter(Adapter):
    name = "likes"
    kind = "activity"
    transform_version = "board-likes/1"

    def check_config(self, section: dict) -> None:
        p = section.get("input_path")
        if not isinstance(p, str) or not p or not os.path.isabs(os.path.expanduser(p)):
            raise AdapterError("likes.input_path must be an absolute path in config")
        pc = section.get("provider_code")
        if not isinstance(pc, str) or not _PROVIDER_RE.match(pc):
            raise AdapterError("likes.provider_code must be set in config")
        kinds = section.get("include_kinds")
        if not isinstance(kinds, list) or not all(isinstance(k, str) and k for k in kinds):
            raise AdapterError("likes.include_kinds must be a list of strings")
        for b in ("include_null_kind", "include_seed"):
            if not isinstance(section.get(b, False), bool):
                raise AdapterError("likes." + b + " must be boolean")

    def _wanted(self, row: dict, section: dict) -> bool:
        if row.get("seed") is True and not section.get("include_seed", False):
            return False
        k = row.get("kind")
        if k is None:
            return bool(section.get("include_null_kind", False))
        kinds = section.get("include_kinds") or []
        return isinstance(k, str) and ("*" in kinds or k in kinds)

    def collect(self, section: dict):
        """Observation · Quarantine 를 낸다. 같은 id 가 파일 안에서 신원이 다르게 겹치면 그 id 전체를 격리."""
        self.check_config(section)
        path = os.path.expanduser(section["input_path"])
        provider_code = section["provider_code"]
        axes = list(section.get("axes") or [])
        try:
            fh = open(path, "rb")
        except OSError:
            raise AdapterError("likes input not readable") from None
        groups: dict = {}
        order: list = []
        with fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                if len(line) > MAX_LINE_BYTES:
                    yield Quarantine("line-too-large", f"likes:{lineno}:{len(line)}")
                    continue
                try:
                    row = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    yield Quarantine("bad-json", line.decode("utf-8", "replace"))
                    continue
                if not isinstance(row, dict):
                    yield Quarantine("not-object", line.decode("utf-8", "replace"))
                    continue
                if not self._wanted(row, section):
                    continue
                rid = row.get("id")
                if not isinstance(rid, str) or not rid.strip():
                    # 안정적 이벤트 ID 없음 + 중복 기준 확정 불가 → 격리
                    yield Quarantine("no-event-id", line.decode("utf-8", "replace"))
                    continue
                key = f"{provider_code}:{rid}"
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(row)
        for key in order:
            rows = groups[key]
            fps = {self.fingerprint(provider_code, r) for r in rows}
            if len(fps) != 1:
                yield Quarantine("ambiguous-duplicate", key)
                continue
            yield self._observe(key, rows[0], provider_code, axes)

    @staticmethod
    def fingerprint(provider_code: str, row: dict) -> str:
        author = row.get("author") if isinstance(row.get("author"), str) else None
        return jcs.sha256_hex([provider_code, row.get("id"), author])

    def _observe(self, key: str, row: dict, provider_code: str, axes: list) -> Observation:
        people = []
        author = row.get("author")
        if isinstance(author, str) and author.strip():
            people.append((provider_code, author, "author"))
        return Observation(
            dedupe_key=key,
            fingerprint=self.fingerprint(provider_code, row),
            raw=row,
            action="like",
            state="active",
            occurred_on=None,
            observed_on=_date_or_none(row.get("first_seen")),
            provider="sns",
            people=people,
            axes=axes,
        )
