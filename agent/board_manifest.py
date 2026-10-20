"""board 정본 → `board-migration/1` 정제 명세 + AI 검토표 생성기 (PC 전용).

무엇: 사용자 PC 의 board 정본 마크다운(C·WD·AC·D 색인·craft·숙제·발행·축·지원 문서·일지·세션 스토리)을 읽어
  ① AI 검토표(사람이 고치는 마크다운) 를 만들고 ② 검토가 끝나면 서버 이전 CLI(`server/migrate_cli.py`)가 받는
  정제 명세 JSON 을 만든다. ③ `validate` 는 명세를 쓰기 없이 검사한다.

경계:
- 입력 경로는 전부 로컬 설정 파일(JSON)로 받는다. 이 파일에 사용자 경로·이름을 적지 않는다.
- 설정의 `forbidden_roots` 아래 경로는 거절한다(고객사 원문 — D 본문·E·W 를 두는 폴더). 코드 기본값은 비어 있다 — 사용자가 설정에 적는다.
  심볼릭 링크로 그 아래를 가리켜도 거절한다.
- insights·assignments·수집층은 입력 키 자체를 받지 않는다.
- 원본 ID 는 최초 1회 난수로 발급해 상태 디렉터리의 대응표(idmap.json)에 두고 재사용한다. 파일 경로·줄 번호로 만들지 않는다.
- 정규화(공용 계약 정의) = json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False), 실수(float) 금지.
  revision = `v_` + 정제 내용 정규화의 SHA-256 앞 32 hex. manifest_sha256 = 자기 필드를 뺀 명세 정규화의 SHA-256(64 hex).
- 검토표 차단 항목(전부 골라야 명세가 나온다): `⚠️AI` 가 든 줄(블록)·제목 → 확인·수정 /
  `(AI)` 로 끝나는 헤딩의 절 → 「고쳐서 남김·그대로 남김·뺌」 / URL·로컬 경로 꼴이 든 줄 → 「남김·뺌」(기본값 없음) /
  D 색인 제목(고객사명) · craft 제목 대괄호(이슈키) → 확인·수정. 「관심 축」 코드는 선택 항목.
  `⟨세션⟩`·`AI✓`·`✍️정리` 는 그 글자만 지운다(공백·괄호는 그대로).
- 출력 꼴 = 서버 계약 server/contract/imports/board-migration-1.schema.json. 서버 ID 는 모른다 — 대상 오브젝트는
  object_ref(std_id·author@1 슬롯·key)로 적고 해석은 이전 CLI 가 한다. 직접 추가 오브젝트(AC·지원 문서)는
  설정 custom_objects 의 key 로만 가리킨다 — 오브젝트 정의는 명세에 싣지 않는다(서버에 먼저 있어야 한다).

사용:
  python3 agent/board_manifest.py sheet    --config CFG --state-dir DIR --out SHEET.md
  python3 agent/board_manifest.py build    --config CFG --state-dir DIR --sheet SHEET.md --out MANIFEST.json \
                                           --target-user-id U --source-instance-id S --reviewed-at 2026-10-20T21:00:00+09:00
  python3 agent/board_manifest.py validate MANIFEST.json [--config CFG --state-dir DIR --sheet SHEET.md]

표준 라이브러리만 쓴다(Python ≥3.9).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import textwrap
from pathlib import Path

CONTRACT = "board-migration/1"
DEFAULT_TRANSFORM_VERSION = "board-manifest/1"
SHEET_MAGIC = "board-review-sheet/1"
IDMAP_VERSION = 1

# ── 표기 토큰(정확한 코드 포인트) ─────────────────────────────────────────
AI_UNCONFIRMED = "⚠️AI"            # ⚠️AI
STRIP_TOKENS = ("⟨세션⟩",   # ⟨세션⟩
                "AI✓",                   # AI✓
                "✍️정리")    # ✍️정리
ALL_MARKERS = (AI_UNCONFIRMED,) + STRIP_TOKENS

# ── 단위 ─────────────────────────────────────────────────────────────────
# 내부 단위 이름 → 서버 계약(board-migration-1.schema.json) unit_kind
EMIT_UNIT_KIND = {
    "c": "principle_c", "wd": "principle_wd", "ac": "application_axis", "d_index": "decision_index",
    "craft": "craft", "open_question": "open_question", "pub": "publication", "axis": "axis",
    "application": "application_doc", "journal": "journal", "session_story": "session_story",
}
UNIT_TARGET_KIND = {k: ("record" if k in ("journal", "session_story") else "item") for k in EMIT_UNIT_KIND}
EMIT_TARGET_KIND = {EMIT_UNIT_KIND[k]: v for k, v in UNIT_TARGET_KIND.items()}
EXCLUDED_UNIT_KINDS = {"d_body", "evidence", "w_session", "insights", "assignments", "collection", "verbatim_ai"}
ALLOWED_SOURCE_KEYS = {"decisions", "work_decisions", "application_craft", "craft", "open_questions",
                       "published_log", "axes", "applications", "journal_dir", "session_story_dir"}
EXCLUDED_SOURCE_KEYS = {"insights", "assignments", "d_body", "evidence", "sessions", "w_session", "collection",
                        "feed", "reactions", "likes", "reposts", "moves", "mail", "experience", "kakao",
                        "verbatim_ai"}
UNIT_ORDER = ["axis", "c", "wd", "d_index", "craft", "ac", "application",
              "journal", "session_story", "open_question", "pub"]


# 논리 대상 키(object_ref) — 이전 CLI 가 std_id → template_slot → key 순으로 해석한다.
def _obj(key, std=True):
    d = {"template_slot": f"author@1:{key}", "key": key}
    if std:
        d["std_id"] = f"std:{key}"
    return d


OBJECT_REF = {
    "c": _obj("principles"), "wd": _obj("principles"), "d_index": _obj("decision_events"),
    "axis": _obj("axes"), "craft": _obj("craft", std=False), "open_question": _obj("open_questions", std=False),
    "pub": _obj("publications", std=False),
}
EMIT_OBJECT_REF = {EMIT_UNIT_KIND[k]: v for k, v in OBJECT_REF.items()}
MATURITY = {"\U0001f331": "seed", "\U0001f33f": "growing", "\U0001f333": "mature"}   # 🌱 🌿 🌳

ROW_ALLOWED = {"original_id", "revision", "previous_revision", "target_kind", "unit_kind", "object_ref", "code",
               "title", "body", "fields", "decision", "source_created_at", "source_date", "references", "attach",
               "expected_target_revision"}
ROW_REQUIRED = {"original_id", "revision", "previous_revision", "target_kind", "unit_kind"}
TOP_KEYS = {"contract", "target_user_id", "source_instance_id", "transform_version", "manifest_sha256",
            "reviewed_at", "rows"}
FORBIDDEN_ROW_KEYS = {"ai_markers", "legacy_ai_markers", "migration_acceptance", "review_assertions", "generation_kind"}

RE_OID = re.compile(r"^o_[0-9a-f]{32}$")
RE_REV = re.compile(r"^v_[0-9a-f]{32}$")
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
RE_SID = re.compile(r"^is_[0-9a-f]{24}$")
RE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RE_STD = re.compile(r"^std:[a-z][a-z0-9_]*$")
RE_SLOT = re.compile(r"^[a-z]+@[0-9]+:[a-z][a-z0-9_]*$")
RE_TV = re.compile(r"^board-manifest/[0-9]+$")
RE_SERVER_IDS = {"record_id": re.compile(r"^r_[A-Za-z0-9_-]{4,40}$"), "item_id": re.compile(r"^i_[A-Za-z0-9_-]{4,40}$"),
                 "candidate_id": re.compile(r"^c_[A-Za-z0-9_-]{4,40}$")}
RE_URL = re.compile(r"https?://", re.I)
RE_LOCALPATH = re.compile(r"(?:~/|/Users/|/home/|[A-Za-z]:\\)")
RE_CODE_REF = re.compile(r"\[\[([A-Z]{1,3})-(\d{1,3})\]\]")
DEP_WARN = "연결 버림 — "
# 참조 target(references·attach.attach_to·attach.axes)에 revision 을 함께 싣는가 — 서버 스키마(board-migration-1.schema.json)
# $defs.target 을 따른다. 서버 스키마는 original_id 참조에 revision 이 필수(정확한 판 — r3)라 True. False 는 옛 꼴(키 하나)이고
# 서버 스키마가 거절한다(ServerSchemaTest). 참조 대상이 새 판이면 그 판이 들어가 참조하는 행도 새 판이 된다(새 판 추종).
TARGET_WITH_REVISION = True
RE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


class ManifestError(Exception):
    """사용자에게 보여 줄 거절 사유. 여러 줄이면 messages 에."""

    def __init__(self, message, messages=None):
        super().__init__(message)
        self.messages = list(messages or [])


# ═══════════════════════════════════════════════════════════════════════
# 해시·JCS
# ═══════════════════════════════════════════════════════════════════════
class CanonicalError(ValueError):
    pass


def _check_canonical(v) -> None:
    if isinstance(v, float):
        raise CanonicalError("실수(float)는 계약에 없어요")
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise CanonicalError("객체 키는 문자열이어야 해요")
            _check_canonical(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _check_canonical(x)
    elif v is not None and not isinstance(v, (str, int, bool)):
        raise CanonicalError("지원 안 하는 값: " + type(v).__name__)


def canonical(v) -> str:
    """공용 계약 정규화: json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False). 실수 금지."""
    _check_canonical(v)
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(v) -> str:
    return hashlib.sha256(canonical(v).encode("utf-8")).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def manifest_hash(manifest: dict) -> str:
    """manifest_sha256 = 자기 필드를 뺀 명세 정규화의 SHA-256(소문자 hex 64)."""
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return canonical_sha256(body)


def row_revision(content: dict) -> str:
    return "v_" + canonical_sha256(content)[:32]


# ═══════════════════════════════════════════════════════════════════════
# 표기 토큰
# ═══════════════════════════════════════════════════════════════════════
def strip_tokens(s: str) -> str:
    """⟨세션⟩·AI✓·✍️정리 의 글자만 지운다. 주변 공백·괄호·다른 ⚠️ 표기는 건드리지 않는다."""
    for t in STRIP_TOKENS:
        s = s.replace(t, "")
    return s


def has_ai_unconfirmed(s: str) -> bool:
    return AI_UNCONFIRMED in s


def residual_markers(s: str) -> list:
    return [m for m in ALL_MARKERS if m in s]


# ═══════════════════════════════════════════════════════════════════════
# 마크다운 조각
# ═══════════════════════════════════════════════════════════════════════
RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
RE_FENCE = re.compile(r"^\s*(```+|~~~+)")


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def headings(lines: list) -> list:
    """[(index, level, title)] — 코드 펜스 안은 건너뛴다."""
    out, fence = [], None
    for i, line in enumerate(lines):
        m = RE_FENCE.match(line)
        if m:
            tok = m.group(1)[0]
            if fence is None:
                fence = tok
            elif fence == tok:
                fence = None
            continue
        if fence:
            continue
        h = RE_HEADING.match(line)
        if h:
            out.append((i, len(h.group(1)), h.group(2)))
    return out


def section_end(lines: list, hs: list, idx: int, level: int) -> int:
    for j, lv, _ in hs:
        if j > idx and lv <= level:
            return j
    return len(lines)


class Block:
    """본문 조각. kind='blank' 는 빈 줄, 'block' 은 들여쓰기 0 줄 + 이어지는 들여쓴 줄."""
    __slots__ = ("kind", "lines", "ordinal")

    def __init__(self, kind, lines, ordinal=None):
        self.kind, self.lines, self.ordinal = kind, lines, ordinal

    @property
    def text(self):
        return "\n".join(self.lines)


def split_blocks(lines: list) -> list:
    out, cur, n = [], None, 0
    for line in lines:
        if line.strip() == "":
            cur = None
            out.append(Block("blank", [line]))
            continue
        if cur is not None and line[:1] in (" ", "\t"):
            cur.lines.append(line)
            continue
        n += 1
        cur = Block("block", [line], n)
        out.append(cur)
    return out


RE_LABEL_BOLD = re.compile(r"^\s*[-*]\s+\*\*(.+?)\*\*([^:\n]*?):[ \t]?(.*)$")
RE_LABEL_PLAIN = re.compile(r"^\s*[-*]\s+([^:*\[\n]{1,24}?):[ \t]?(.*)$")


def block_label(text: str):
    first = text.split("\n", 1)[0]
    m = RE_LABEL_BOLD.match(first)
    if m:
        return strip_tokens(m.group(1)).strip()
    m = RE_LABEL_PLAIN.match(first)
    if m:
        return strip_tokens(m.group(1)).strip()
    return None


def block_value(text: str) -> str:
    """`- **라벨** …: 값` + 이어지는 줄 → 값(이어지는 줄은 공통 들여쓰기만 벗김)."""
    lines = text.split("\n")
    m = RE_LABEL_BOLD.match(lines[0]) or RE_LABEL_PLAIN.match(lines[0])
    first = (m.group(m.lastindex) if m else lines[0])
    if m and m.re is RE_LABEL_BOLD:
        # 라벨 꼬리(`(2026-01-09 덧붙임)` 같은 주석)는 버리지 않는다. 표기 토큰만 지우고 값 앞에 둔다.
        tail = strip_tokens(m.group(2)).replace(AI_UNCONFIRMED, "").strip()
        if tail:
            first = tail + (" " + first if first else "")
    rest = textwrap.dedent("\n".join(lines[1:])) if len(lines) > 1 else ""
    return (first + ("\n" + rest if rest else "")).strip()


# ═══════════════════════════════════════════════════════════════════════
# 단위
# ═══════════════════════════════════════════════════════════════════════
class Unit:
    def __init__(self, unit_kind, locator, code=None, title="", blocks=None):
        self.unit_kind = unit_kind
        self.locator = locator          # PC 대응표에만 남는다. 명세에 안 나간다.
        self.code = code
        self.title = title
        self.title_raw = title
        self.blocks = blocks or []
        self.fields = {}
        self.refs = []                  # [(field, (prefix, num))] 같은 명세 안 코드 참조
        self.material_dates = []        # 발행 재료(일지 날짜)
        self.object_ref = None          # 직접 추가 오브젝트 단위 kind('ac'|'application')
        self.source_date = None
        self.source_created_at = None
        self.attach = None              # 기록: {'codes': [...], 'use_at', 'unattached', 'axes_text'}
        self.title_review = None        # 'client_name' | 'issue_key'
        self.warnings = []
        self.original_id = None

    @property
    def key(self):
        return f"{self.unit_kind}|{self.locator}"

    def label(self):
        return self.code or self.locator


def _code_key(prefix: str, num: str):
    return (prefix, int(num))


RE_UNIT_HEAD = re.compile(r"^(C|WD|AC|D)-(\d{1,3})\.\s*(.*)$")


def _unit_bodies(text: str, prefix: str):
    """`<#…> PREFIX-NN. 제목` 헤딩(레벨 무관) 단위로 자른다. 단위 끝 = 같은·상위 레벨 다음 헤딩."""
    lines = strip_frontmatter(text).split("\n")
    hs = headings(lines)
    out = []
    for i, lv, title in hs:
        m = RE_UNIT_HEAD.match(title)
        if not m or m.group(1) != prefix:
            continue
        end = section_end(lines, hs, i, lv)
        out.append((f"{prefix}-{m.group(2)}", m.group(3), lines[i + 1:end]))
    return out


def _trim_blank(blocks):
    while blocks and blocks[0].kind == "blank":
        blocks.pop(0)
    while blocks and blocks[-1].kind == "blank":
        blocks.pop()
    return blocks


def parse_principles(text: str, prefix: str, unit_kind: str, register: str) -> list:
    units = []
    for code, title, body in _unit_bodies(text, prefix):
        u = Unit(unit_kind, code, code=code)
        t = title.strip()
        # 성숙도 이모지: 제목 맨 앞 또는 맨 끝의 🌱·🌿·🌳 한 개만 maturity 로 옮긴다.
        for emo, val in MATURITY.items():
            if t.startswith(emo + " "):
                u.fields["maturity"] = val
                t = t[len(emo) + 1:]
                break
            if t.endswith(" " + emo):
                u.fields["maturity"] = val
                t = t[: -len(emo) - 1]
                break
        u.title = u.title_raw = t
        u.fields["register"] = register
        u.blocks = _trim_blank(split_blocks(body))
        units.append(u)
    return units


def parse_d_index(text: str) -> list:
    """D 색인: 제목만 옮긴다. 본문 줄(`본문`·`근거` 경로·링크)은 D 본문 참조라 버린다. 제목은 원작자 검토 대상."""
    units = []
    for code, title, _body in _unit_bodies(text, "D"):
        u = Unit("d_index", code, code=code, title=title.strip())
        u.title_review = "client_name"
        units.append(u)
    return units


RE_ISSUE_BRACKET = re.compile(r"\s*\[([^\[\]]*\b[A-Z][A-Z0-9]{0,9}-\d{1,6}\b[^\[\]]*)\]\s*$")


def parse_craft(text: str) -> list:
    """craft: `## 분해 축` 아래 `###` 조각. 헤딩 끝 대괄호에 이슈키 꼴이 있으면 제목 검토 대상."""
    lines = strip_frontmatter(text).split("\n")
    hs = headings(lines)
    units, seen = [], {}
    for i, lv, title in hs:
        if lv != 3:
            continue
        end = section_end(lines, hs, i, lv)
        t = title.strip()
        base = RE_ISSUE_BRACKET.sub("", t).strip()
        n = seen.get(base, 0) + 1
        seen[base] = n
        u = Unit("craft", base if n == 1 else f"{base}#{n}", title=t)
        if RE_ISSUE_BRACKET.search(t):
            u.title_review = "issue_key"
        u.blocks = _trim_blank(split_blocks(lines[i + 1:end]))
        units.append(u)
    return units


def parse_ac(text: str) -> list:
    """AC: 칸 4종(설정의 source_label) → text 필드. 나머지 줄은 본문."""
    units = []
    for code, title, body in _unit_bodies(text, "AC"):
        u = Unit("ac", code, code=code, title=title.strip())
        u.object_ref = "ac"
        u.blocks = _trim_blank(split_blocks(body))
        units.append(u)
    return units


def ac_fields(u: Unit, field_map: list):
    """검토 반영 뒤 블록에서 칸을 떼어 fields 로. 칸 블록은 본문에서 뺀다."""
    by_label = {f["source_label"]: f["key"] for f in field_map}
    keep = []
    for b in u.blocks:
        lab = block_label(b.text) if b.kind == "block" else None
        if lab is not None and lab in by_label and by_label[lab] not in u.fields:
            v = block_value(b.text)
            if v:
                u.fields[by_label[lab]] = v
            continue
        keep.append(b)
    u.blocks = _trim_blank(keep)


RE_OQ_REF = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\s*(.*)$")


def parse_open_questions(text: str, section: str, registered_word: str) -> list:
    lines = strip_frontmatter(text).split("\n")
    hs = headings(lines)
    units = []
    for i, lv, title in hs:
        if strip_tokens(title).strip() != section:
            continue
        end = section_end(lines, hs, i, lv)
        seen = {}
        for b in split_blocks(lines[i + 1:end]):
            if b.kind != "block":
                continue
            m = RE_OQ_REF.match(b.lines[0])
            if not m:
                continue
            ref = m.group(1).strip()
            rest = [m.group(2)] + b.lines[1:]
            body_text = textwrap.dedent("\n".join(rest[1:])) if len(rest) > 1 else ""
            body_text = (rest[0] + ("\n" + body_text if body_text else "")).strip()
            key = sha256_text(ref + "\n" + body_text)[:16]
            n = seen.get(key, 0) + 1
            seen[key] = n
            u = Unit("open_question", key if n == 1 else f"{key}#{n}")
            first_line = body_text.split("\n", 1)[0]
            u.title = first_line[:200]
            u.blocks = [Block("block", body_text.split("\n"), 1)]
            u.fields["state"] = "open"
            dm = re.findall(r"\(" + re.escape(registered_word) + r"\s+(\d{4}-\d{2}-\d{2})\)", body_text)
            if len(dm) == 1 and _is_date(dm[0]):
                u.fields["registered_on"] = dm[0]
            single = re.fullmatch(r"(C|WD|D)-(\d{1,3})", ref)
            if single:
                fld = "subject_event" if single.group(1) == "D" else "subject_principle"
                u.refs.append((fld, _code_key(single.group(1), single.group(2))))
            else:
                u.warnings.append("숙제 대상이 기준(C·WD)·결정(D) 한 곳이 아님 — exactly_one_reference 위반이라 이전하지 않음")
            units.append(u)
    return units


RE_PUB_HEAD = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\s+—\s+(.*)$")
RE_PUB_CHANNEL = re.compile(r"\((\w+)\s*,\s*(.*?)\)\s*$|\((\w+)\)\s*$", re.S)
RE_MATERIAL = re.compile(r"재료\s*:\s*\S*?(\d{4}-\d{2}-\d{2})\.md")
RE_KEYLINE = re.compile(r"^\s*·\s*핵심\s*:\s*(.*)$")


def parse_pub(text: str) -> list:
    units = []
    lines = strip_frontmatter(text).split("\n")
    for b in split_blocks(lines):
        if b.kind != "block":
            continue
        m = RE_PUB_HEAD.match(b.lines[0])
        if not m:
            continue
        date = m.group(1)
        head = [m.group(2)]
        tail = []
        for line in b.lines[1:]:
            s = line.strip()
            if tail or s.startswith("←") or s.startswith("·"):
                tail.append(s)
            else:
                head.append(s)
        head_text = " ".join(head)
        title, note, channel = head_text, "", "other"
        cm = RE_PUB_CHANNEL.search(head_text)
        if cm:
            ch = (cm.group(1) or cm.group(3) or "").lower()
            channel = ch if ch in ("linkedin", "threads") else "other"
            note = (cm.group(2) or "").strip()
            title = head_text[: cm.start()].strip()
        u = Unit("pub", f"{date}|{sha256_text(title)[:12]}", title=title)
        u.fields.update({"channel": channel, "published_on": date if _is_date(date) else None})
        for s in tail:
            km = RE_KEYLINE.match(s)
            if km:
                u.fields["key_line"] = km.group(1).strip()
            for mm in RE_MATERIAL.finditer(s):
                u.material_dates.append(mm.group(1))
            if RE_URL.search(s):
                u.warnings.append("발행 URL 은 서버에 두지 않음(결정 15) — 버림")
        u.blocks = [Block("block", note.split("\n"), 1)] if note else []
        units.append(u)
    return units


def _split_cells(row: str) -> list:
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


RE_AXIS_CODE = re.compile(r"^([A-Z](?:-\d{1,2})?)\.\s+(.*)$")


def parse_axes(text: str, section: str, columns: dict) -> list:
    """축 정의 표 — 설정의 절(기본 「관심 축」) 안의 표만 읽는다. 다른 절(작성자 목록 등)은 읽지 않는다."""
    lines = strip_frontmatter(text).split("\n")
    hs = headings(lines)
    units = []
    for i, lv, title in hs:
        if not strip_tokens(title).strip().startswith(section):
            continue
        end = section_end(lines, hs, i, lv)
        rows = [ln for ln in lines[i + 1:end] if ln.strip().startswith("|")]
        for r in rows[2:]:   # 머리글·구분 줄
            cells = _split_cells(r)
            name = cells[columns["name"]] if columns["name"] < len(cells) else ""
            m = RE_AXIS_CODE.match(name)
            if not m:
                continue
            u = Unit("axis", m.group(1), code=m.group(1), title=m.group(2).strip())
            kws = []
            for ci in columns.get("keywords", []):
                if ci < len(cells):
                    kws += [k.strip() for k in cells[ci].split(",") if k.strip()]
            if kws:
                u.fields["keywords"] = kws
            bi = columns.get("body")
            if bi is not None and bi < len(cells) and cells[bi]:
                u.blocks = [Block("block", [cells[bi]], 1)]
            units.append(u)
        break
    return units


def _is_date(s) -> bool:
    if not isinstance(s, str) or not RE_DATE.match(s):
        return False
    try:
        _dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def parse_application(text: str, locator: str) -> Unit:
    lines = strip_frontmatter(text).split("\n")
    hs = headings(lines)
    title, start = "", 0
    for i, lv, t in hs:
        if lv == 1:
            title, start = t, i + 1
            break
    u = Unit("application", locator, title=title)
    u.object_ref = "application"
    if not title:
        u.warnings.append("첫 `#` 제목이 없어 제목을 비움(파일명은 쓰지 않음)")
    u.blocks = _trim_blank(split_blocks(lines[start:]))
    return u


ATTACH_LABELS = (("항목", "items"), ("쓸 자리", "use_at"), ("아직 안 붙는 것", "unattached"), ("관심 축", "axes"))


def parse_record(text: str, unit_kind: str, locator: str, source_date, attach_heading: str, exclude_sections) -> Unit:
    """일지·세션 스토리 → 기록 1편. 「어디에 붙나」 절은 record_attach 로 떼고, 설정의 제외 절은 뺀다."""
    lines = strip_frontmatter(text).split("\n")
    hs = headings(lines)
    drop = set()
    title = ""
    attach = None
    for i, lv, t in hs:
        tt = strip_tokens(t).strip()
        if lv == 1 and not title:
            title = strip_tokens(t).strip()
            drop.add(i)
            continue
        if tt.startswith(attach_heading) or tt in exclude_sections:
            end = section_end(lines, hs, i, lv)
            if tt.startswith(attach_heading) and attach is None:
                attach = _parse_attach(lines[i + 1:end])
            drop.update(range(i, end))
    body = [ln for j, ln in enumerate(lines) if j not in drop]
    u = Unit(unit_kind, locator, title=title)
    u.title_raw = title
    u.source_date = source_date
    u.attach = attach
    u.blocks = _trim_blank(split_blocks(body))
    return u


def _parse_attach(lines: list) -> dict:
    out = {"codes": [], "use_at": None, "unattached": None, "axes_text": None, "dropped": 0}
    for b in split_blocks(lines):
        if b.kind != "block":
            continue
        lab = block_label(b.text)
        if lab is None:
            continue
        for name, slot in ATTACH_LABELS:
            if lab.startswith(name):
                val = strip_tokens(block_value(b.text))
                if slot == "items":
                    for m in RE_CODE_REF.finditer(val):
                        out["codes"].append((m.group(1), int(m.group(2))))
                elif slot == "axes":
                    out["axes_text"] = val or None
                else:
                    out[slot] = val or None
                break
    return out


# ═══════════════════════════════════════════════════════════════════════
# 설정
# ═══════════════════════════════════════════════════════════════════════
DEFAULTS = {
    "open_questions_section": "큐",
    "open_questions_registered_word": "등록",
    "axes_section": "관심 축",
    "axes_columns": {"name": 0, "keywords": [1, 2], "body": 3},
    "attach_heading": "어디에 붙나",
    "exclude_sections": {"journal": [], "session_story": ["팀과의 과정"]},
    "forbidden_roots": [],
}


def _forbidden_roots(cfg: dict, base: Path) -> list:
    """설정 forbidden_roots(설정 파일 기준 상대 경로 또는 ~/·절대 경로) → 실제 경로 목록. 코드에 박아 둔 경로는 없다."""
    fr = cfg.get("forbidden_roots", [])
    if not isinstance(fr, list) or not all(isinstance(r, str) and r.strip() for r in fr):
        raise ManifestError("forbidden_roots 는 경로 문자열 목록이어야 해요")
    roots = []
    for r in fr:
        roots.append((base / os.path.expanduser(r)))
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            out.append(r)
    return out


def _under(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def load_config(path) -> dict:
    path = Path(path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ManifestError("설정 파일은 JSON 객체여야 해요")
    base = path.resolve().parent
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if k != "sources"})
    sources = cfg.get("sources") or {}
    bad = sorted(k for k in sources if k in EXCLUDED_SOURCE_KEYS)
    if bad:
        raise ManifestError("이전 제외 범위 입력이에요: " + ", ".join(bad) + " (insights·assignments·D 본문·E·W·수집층은 받지 않아요)")
    unknown = sorted(k for k in sources if k not in ALLOWED_SOURCE_KEYS)
    if unknown:
        raise ManifestError("모르는 입력 키: " + ", ".join(unknown))
    roots = _forbidden_roots(merged, base)
    resolved = {}
    for k, v in sources.items():
        items = v if isinstance(v, list) else [v]
        ps = []
        for item in items:
            p = (base / os.path.expanduser(str(item))).resolve()
            for r in roots:
                if _under(p, r):
                    raise ManifestError(f"제외 경로라 읽지 않아요(입력 키 {k}) — 고객사 원문 경로 아래예요")
            ps.append(p)
        resolved[k] = ps if isinstance(v, list) else ps[0]
    merged["sources"] = resolved
    merged["_roots"] = roots
    return merged


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _files_in(src, roots) -> list:
    if src is None:
        return []
    items = src if isinstance(src, list) else [src]
    out = []
    for p in items:
        if p.is_dir():
            for f in sorted(p.glob("*.md")):
                rf = f.resolve()
                if any(_under(rf, r) for r in roots):
                    raise ManifestError("제외 경로라 읽지 않아요 — 심볼릭 링크가 고객사 원문 경로를 가리켜요")
                out.append(rf)
        else:
            out.append(p)
    return out


RE_FILE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:$|[^0-9])")


def collect_units(cfg: dict) -> list:
    s = cfg["sources"]
    roots = cfg["_roots"]
    units = []
    co = cfg.get("custom_objects") or {}
    if "decisions" in s:
        t = _read(s["decisions"])
        units += parse_principles(t, "C", "c", "customer")
        units += parse_d_index(t)
    if "work_decisions" in s:
        units += parse_principles(_read(s["work_decisions"]), "WD", "wd", "work")
    if "application_craft" in s:
        if "ac" not in co:
            raise ManifestError("AC 를 읽으려면 설정 custom_objects.ac(key·label·fields) 가 필요해요")
        units += parse_ac(_read(s["application_craft"]))
    if "craft" in s:
        units += parse_craft(_read(s["craft"]))
    if "open_questions" in s:
        units += parse_open_questions(_read(s["open_questions"]), cfg["open_questions_section"],
                                      cfg["open_questions_registered_word"])
    if "published_log" in s:
        units += parse_pub(_read(s["published_log"]))
    if "axes" in s:
        units += parse_axes(_read(s["axes"]), cfg["axes_section"], cfg["axes_columns"])
    if "applications" in s:
        if "application" not in co:
            raise ManifestError("지원 문서를 읽으려면 설정 custom_objects.application(key·label) 이 필요해요")
        for f in _files_in(s["applications"], roots):
            units.append(parse_application(_read(f), "app|" + f.stem))
    ex = cfg.get("exclude_sections") or {}
    for key, kind in (("journal_dir", "journal"), ("session_story_dir", "session_story")):
        if key not in s:
            continue
        for f in _files_in(s[key], roots):
            m = RE_FILE_DATE.match(f.name)
            if not m:
                continue
            d = m.group(1) if _is_date(m.group(1)) else None
            units.append(parse_record(_read(f), kind, f.stem, d, cfg["attach_heading"], set(ex.get(kind, []))))
    for kind in ("ac", "application"):
        if kind in co:
            spec = co[kind]
            if not isinstance(spec.get("key"), str) or not RE_KEY.match(spec["key"]):
                raise ManifestError(f"custom_objects.{kind}.key 는 영문 소문자·숫자·_ 키여야 해요")
            for f in spec.get("fields", []):
                if not isinstance(f.get("key"), str) or not RE_KEY.match(f["key"]):
                    raise ManifestError(f"custom_objects.{kind}.fields 의 key 형식")
    keys = [u.key for u in units]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ManifestError("같은 단위가 두 번 나와요(코드 중복): " + ", ".join(k.split("|", 1)[1] for k in dup))
    return units


# ═══════════════════════════════════════════════════════════════════════
# 대응표(PC 전용) — 원본 ID·revision
# ═══════════════════════════════════════════════════════════════════════
def load_idmap(state_dir) -> dict:
    p = Path(state_dir) / "idmap.json"
    if not p.exists():
        return {"version": IDMAP_VERSION, "units": {}}
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("version") != IDMAP_VERSION:
        raise ManifestError("대응표 판이 달라요")
    return d


def save_idmap(state_dir, idmap: dict) -> None:
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / "idmap.json", json.dumps(idmap, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def assign_ids(units: list, idmap: dict) -> bool:
    """대응표에 없는 단위만 난수 ID 를 새로 발급한다. 바뀌었으면 True."""
    changed = False
    used = {v["original_id"] for v in idmap["units"].values()}
    for u in units:
        ent = idmap["units"].get(u.key)
        if ent is None:
            while True:
                oid = "o_" + secrets.token_hex(16)
                if oid not in used:
                    break
            used.add(oid)
            ent = {"original_id": oid, "revision": None}
            idmap["units"][u.key] = ent
            changed = True
        u.original_id = ent["original_id"]
    return changed


# ═══════════════════════════════════════════════════════════════════════
# 검토표
# ═══════════════════════════════════════════════════════════════════════
class ReviewItem:
    """검토 항목 하나. choices 중 정확히 하나를 고르면 완료. uses_after 면 `after` 칸을 읽는다."""

    def __init__(self, item_id, kind, unit, where, before, default_after, blocking, choices, uses_after=True,
                 target=None):
        self.item_id = item_id
        self.kind = kind
        self.unit = unit
        self.where = where
        self.before = before
        self.before_sha = sha256_text(before)
        self.default_after = default_after
        self.blocking = blocking
        self.choices = list(choices)
        self.uses_after = uses_after
        self.target = target          # 블록 / 절(블록 목록) / (블록, 줄 번호)
        self.flags = []
        self.checked = []
        self.after = default_after

    @property
    def done(self):
        return len([c for c in self.checked if c in self.choices]) == 1


CONFIRM = "확인 완료"
SEC_EDIT, SEC_KEEP, SEC_DROP = "고쳐서 남김", "그대로 남김", "뺌"
LINE_KEEP, LINE_DROP = "남김", "뺌"
KIND_LABEL = {"ai_unconfirmed": "⚠️AI 미확정 줄", "ai_section": "(AI) 절",
              "client_name": "D 색인 제목 — 고객사명 확인", "issue_key": "craft 제목 — 이슈키 확인",
              "url_line": "URL 이 든 줄", "path_line": "로컬 경로 꼴이 든 줄", "axes_codes": "관심 축 코드(선택)"}
RE_AI_SECTION = re.compile(r"\(AI\)\s*$")


def _no_ai(s: str) -> str:
    return strip_tokens(s).replace(AI_UNCONFIRMED, "")


def _heading_of(b):
    if b.kind != "block":
        return None
    h = RE_HEADING.match(b.lines[0])
    return (len(h.group(1)), h.group(2)) if h else None


def _ai_sections(blocks: list) -> list:
    """`#… 제목 (AI)` 헤딩부터 같은·상위 레벨 다음 헤딩 전까지의 블록 목록들."""
    out, i = [], 0
    while i < len(blocks):
        h = _heading_of(blocks[i])
        if h and RE_AI_SECTION.search(strip_tokens(h[1])):
            j = i + 1
            while j < len(blocks):
                h2 = _heading_of(blocks[j])
                if h2 and h2[0] <= h[0]:
                    break
                j += 1
            span = _trim_blank(list(blocks[i:j]))
            out.append(span)
            i = j
        else:
            i += 1
    return out


def _cid(oid: str, prefix: str, text: str, seen: dict) -> str:
    base = f"{oid}/{prefix}{sha256_text(text)[:12]}"
    n = seen.get(base, 0) + 1
    seen[base] = n
    return base if n == 1 else f"{base}.{n}"


def _line_kind(line: str):
    if RE_URL.search(line):
        return "url_line"
    if RE_LOCALPATH.search(line):
        return "path_line"
    return None


def review_items(units: list) -> list:
    items = []
    for u in units:
        seen = {}
        if u.title_review:
            items.append(ReviewItem(f"{u.original_id}/title", u.title_review, u, "제목", u.title,
                                    strip_tokens(u.title), True, [CONFIRM]))
        elif has_ai_unconfirmed(u.title):
            items.append(ReviewItem(f"{u.original_id}/title", "ai_unconfirmed", u, "제목", u.title,
                                    _no_ai(u.title), True, [CONFIRM]))
        covered = set()
        for span in _ai_sections(u.blocks):
            text = "\n".join(b.text for b in span)
            it = ReviewItem(_cid(u.original_id, "s", text, seen), "ai_section", u,
                            f"절 「{strip_tokens(_heading_of(span[0])[1]).strip()}」", text, _no_ai(text), True,
                            [SEC_EDIT, SEC_KEEP, SEC_DROP], target=span)
            if any(_line_kind(ln) for b in span for ln in b.lines):
                it.flags.append("URL·경로 포함 — 남길 때 확인")
            items.append(it)
            covered.update(id(b) for b in span)
        for b in u.blocks:
            if b.kind != "block" or id(b) in covered:
                continue
            lab = block_label(b.text)
            where = f"블록 {b.ordinal}" + (f" 「{lab}」" if lab else "")
            if has_ai_unconfirmed(b.text):
                it = ReviewItem(_cid(u.original_id, "a", b.text, seen), "ai_unconfirmed", u, where, b.text,
                                _no_ai(b.text), True, [CONFIRM], target=b)
                if any(_line_kind(ln) for ln in b.lines):
                    it.flags.append("URL·경로 포함 — 고칠 때 확인")
                items.append(it)
                continue
            for k, ln in enumerate(b.lines):
                lk = _line_kind(ln)
                if lk:
                    items.append(ReviewItem(_cid(u.original_id, "l", ln, seen), lk, u, f"{where} 줄 {k + 1}",
                                            ln, "", True, [LINE_KEEP, LINE_DROP], uses_after=False,
                                            target=(b, k)))
        if u.attach:
            for slot, name in (("use_at", "쓸 자리"), ("unattached", "아직 안 붙는 것")):
                for k, ln in enumerate((u.attach.get(slot) or "").split("\n")):
                    lk = _line_kind(ln)
                    if lk:
                        items.append(ReviewItem(_cid(u.original_id, "l", ln, seen), lk, u,
                                                f"어디에 붙나 › {name} 줄 {k + 1}", ln, "", True,
                                                [LINE_KEEP, LINE_DROP], uses_after=False, target=("attach", slot, k)))
        if u.attach and u.attach.get("axes_text"):
            items.append(ReviewItem(f"{u.original_id}/axes", "axes_codes", u, "어디에 붙나 › 관심 축",
                                    u.attach["axes_text"], "", False, [CONFIRM]))
    return items


def _fence(text: str) -> str:
    longest = max([len(m) for m in re.findall(r"~+", text)] + [0])
    return "~" * max(4, longest + 1)


RE_ANCHOR = re.compile(r"<!-- item=(\S+) kind=(\S+) before_sha256=([0-9a-f]{64}) -->")
RE_CHECKED = re.compile(r"^- \[[xX]\] (.+?)\s*$", re.M)


def render_sheet(items: list, units: list) -> str:
    out = ["# board 이전 AI 검토표", "", f"<!-- {SHEET_MAGIC} -->", "",
           "PC 전용 파일이다. 서버에 올리지 않는다.", "",
           "고치는 법: 항목마다 선택지 하나를 `- [x]` 로 바꾼다. `after` 칸이 있는 항목은 이전할 문장으로 고친다.",
           "- ⚠️AI 줄·제목: after 를 확인·수정한 뒤 「확인 완료」.",
           "- (AI) 절: 「고쳐서 남김」(after 사용) · 「그대로 남김」(표기 글자만 지우고 원문) · 「뺌」(절 전체 제외).",
           "- URL·경로 줄: 「남김」 또는 「뺌」. 기본값 없음.",
           "`<!-- item=… -->` 줄과 `before` 칸은 고치지 않는다. 원본이 바뀌면 그 항목의 선택은 무효가 된다.", ""]
    blocking = [i for i in items if i.blocking]
    optional = [i for i in items if not i.blocking]
    for title, group in (("차단 항목 — 전부 골라야 명세가 나온다", blocking),
                         ("선택 항목 — 비워 두면 연결하지 않는다", optional)):
        out += [f"## {title} ({len(group)})", ""]
        for n, it in enumerate(group, 1):
            fb = _fence(it.before)
            flag = "".join(f" · {f}" for f in it.flags)
            out += [f"### {n}. {KIND_LABEL[it.kind]} · {it.unit.label()} · {it.where}{flag}",
                    f"<!-- item={it.item_id} kind={it.kind} before_sha256={it.before_sha} -->"]
            out += [f"- [{'x' if c in it.checked else ' '}] {c}" for c in it.choices]
            out += ["", "수정 전:", f"{fb}before", it.before, fb, ""]
            if it.uses_after:
                fa = _fence(it.after)
                out += ["수정 후:", f"{fa}after", it.after, fa, ""]
    warns = [(u, w) for u in units for w in u.warnings]
    out += [f"## 참고 경고 — 막지 않음 ({len(warns)})", ""]
    for u, w in warns:
        out.append(f"- {u.unit_kind} · {u.label()}: {w}")
    out.append("")
    return "\n".join(out)


def parse_sheet(text: str) -> dict:
    """{item_id: {'kind','before_sha','checked','after'}} — 앵커 주석만 신뢰한다."""
    if SHEET_MAGIC not in text:
        raise ManifestError("검토표 파일이 아니에요(머리 표시 없음)")
    out = {}
    anchors = list(RE_ANCHOR.finditer(text))
    for k, m in enumerate(anchors):
        seg = text[m.end(): anchors[k + 1].start() if k + 1 < len(anchors) else len(text)]
        head = seg.split("수정 전:", 1)[0]
        am = re.search(r"^(~{4,})after\n(.*?)\n\1[ \t]*$", seg, re.S | re.M)
        if am is None:
            am2 = re.search(r"^(~{4,})after\n\1[ \t]*$", seg, re.M)
            after = "" if am2 else None
        else:
            after = am.group(2)
        out[m.group(1)] = {"kind": m.group(2), "before_sha": m.group(3),
                           "checked": RE_CHECKED.findall(head), "after": after}
    return out


def _valid_prev(it, sheet):
    prev = sheet.get(it.item_id)
    if prev and prev["before_sha"] == it.before_sha and prev["kind"] == it.kind:
        return prev
    return None


def merge_sheet(items: list, sheet: dict) -> None:
    """이전 검토표의 결과를 현재 항목에 옮긴다. 수정 전 원문 해시가 다르면 옮기지 않는다(무효)."""
    for it in items:
        prev = _valid_prev(it, sheet)
        if not prev:
            continue
        it.checked = [c for c in prev["checked"] if c in it.choices]
        if prev["after"] is not None and it.uses_after:
            it.after = prev["after"]


def _choice(it, prev):
    picked = [c for c in prev["checked"] if c in it.choices]
    return picked[0] if len(picked) == 1 else None


def check_sheet(items: list, sheet: dict) -> list:
    """명세 생성 거절 사유 목록. 빈 목록이면 통과."""
    errs = []
    for it in items:
        prev = sheet.get(it.item_id)
        tag = f"{it.unit.unit_kind} {it.unit.label()} {it.where}"
        if prev is None:
            if it.blocking:
                errs.append(f"검토표에 없는 항목: {tag} — `sheet` 로 다시 만드세요")
            continue
        if prev["before_sha"] != it.before_sha or prev["kind"] != it.kind:
            if it.blocking:
                errs.append(f"원본이 바뀌어 선택 무효: {tag}")
            continue
        if not it.blocking:
            continue
        picked = [c for c in prev["checked"] if c in it.choices]
        if not picked:
            errs.append(f"미완료: {tag}")
            continue
        if len(picked) > 1:
            errs.append(f"선택이 둘 이상: {tag}")
            continue
        if picked[0] in (CONFIRM, SEC_EDIT):
            if prev["after"] is None:
                errs.append(f"after 칸을 못 읽음: {tag}")
                continue
            if residual_markers(prev["after"]):
                errs.append(f"after 에 표기가 남음: {tag}")
                continue
            if it.where == "제목" and not prev["after"].strip():
                errs.append(f"제목이 비었음: {tag}")
    return errs


# ═══════════════════════════════════════════════════════════════════════
# 명세 만들기
# ═══════════════════════════════════════════════════════════════════════
def merge_verbatim_ai(units: list, entries: list) -> None:
    """`answers.verbatim_ai` 처리 규칙: 표시를 떼고 소속 원본 본문 끝에 합친다. 독립 행을 만들지 않는다.
    이번 이전 입력 범위(assignments 제외, 결정 11) 밖이라 기본 입력은 0건이다."""
    by_code = {u.code: u for u in units if u.code}
    for e in entries:
        u = by_code.get(e["owner_code"])
        if u is None:
            raise ManifestError("verbatim_ai 소속 원본이 명세에 없어요")
        text = strip_tokens(e["text"])
        u.blocks += [Block("blank", [""]), Block("block", text.split("\n"), len(u.blocks) + 1)]


def apply_review(units: list, items: list, sheet: dict) -> None:
    """검토 결과를 단위에 반영한다. 검토 항목이 없는 줄은 ⟨세션⟩·AI✓·✍️정리 글자만 지운다."""
    repl, drop_lines, attach_drop = {}, {}, {}
    titles = {}
    axes = {}
    for it in items:
        prev = _valid_prev(it, sheet)
        if it.kind == "axes_codes":
            axes[it.unit.key] = prev["after"] if prev and prev["after"] else ""
            continue
        ch = _choice(it, prev)
        if it.where == "제목":
            titles[it.unit.key] = prev["after"].strip()
        elif it.kind == "ai_unconfirmed":
            repl[id(it.target)] = prev["after"].split("\n")
        elif it.kind == "ai_section":
            span = it.target
            if ch == SEC_DROP:
                for b in span:
                    repl[id(b)] = None
            else:
                text = prev["after"] if ch == SEC_EDIT else it.default_after
                repl[id(span[0])] = text.split("\n")
                for b in span[1:]:
                    repl[id(b)] = None
        elif it.kind in ("url_line", "path_line") and ch == LINE_DROP:
            if it.target[0] == "attach":
                attach_drop.setdefault((it.unit.key, it.target[1]), set()).add(it.target[2])
            else:
                b, k = it.target
                drop_lines.setdefault(id(b), set()).add(k)
    for u in units:
        u.title = titles.get(u.key, strip_tokens(u.title))
        kept, dropped = [], False
        for b in u.blocks:
            r = repl.get(id(b), False)
            if r is None:
                dropped = True
                continue
            if r is not False:
                b.lines = r
            elif b.kind == "block":
                dl = drop_lines.get(id(b), set())
                b.lines = [strip_tokens(x) for k, x in enumerate(b.lines) if k not in dl]
                if not b.lines:
                    dropped = True
                    continue
            if dropped and b.kind == "blank" and kept and kept[-1].kind == "blank":
                continue
            kept.append(b)
        u.blocks = kept
        if u.attach:
            for slot in ("use_at", "unattached"):
                dl = attach_drop.get((u.key, slot))
                if dl and u.attach.get(slot):
                    kept_lines = [x for k, x in enumerate(u.attach[slot].split("\n")) if k not in dl]
                    u.attach[slot] = "\n".join(kept_lines).strip() or None
            a = axes.get(u.key, "")
            u.attach["axis_codes"] = [c for c in re.split(r"[\s,·]+", a.strip()) if c]


def _body(u: Unit) -> str:
    return "\n".join(b.text for b in _trim_blank(list(u.blocks)))


def resolve_deps(units: list, with_axes: bool = True) -> tuple:
    """명세 안 참조를 단위로 해소한다. 못 해소한 참조는 버리고 단위 경고에 남긴다(검토표에 보인다).
    반환: (deps{key: [(field, unit)]}, skip{이전하지 않을 단위 key})."""
    for u in units:
        u.warnings = [w for w in u.warnings if not w.startswith(DEP_WARN)]
    by_code = {}
    for u in units:
        if u.code and u.unit_kind in ("c", "wd", "d_index", "axis"):
            m = re.match(r"^([A-Z]{1,3})-?(\d{1,3})?$", u.code)
            if u.unit_kind == "axis":
                by_code[("AXIS", u.code)] = u
            elif m:
                by_code[(m.group(1), int(m.group(2)))] = u
    journals = {}
    for u in units:
        if u.unit_kind == "journal" and u.source_date:
            journals.setdefault(u.source_date, []).append(u)

    deps = {}      # key → [(field, unit)]
    skip = set()
    for u in units:
        d = []
        for fld, ck in u.refs:
            t = by_code.get(ck)
            if t is None:
                u.warnings.append(DEP_WARN + f"참조 대상 {ck[0]}-{ck[1]:02d} 이 명세에 없어 버림")
                if u.unit_kind == "open_question":
                    skip.add(u.key)
                continue
            d.append((fld, t))
        if u.unit_kind == "open_question" and not u.refs:
            skip.add(u.key)
        if u.unit_kind == "open_question" and not u.fields.get("registered_on"):
            u.warnings.append(DEP_WARN + "숙제에 `(등록 YYYY-MM-DD)` 날짜가 없어 이전하지 않음(필수 등록일)")
            skip.add(u.key)
        for date in u.material_dates:
            js = journals.get(date, [])
            if len(js) == 1:
                d.append(("materials", js[0]))
            else:
                u.warnings.append(DEP_WARN + ("재료 일지를 하나로 못 정해 버림" if js else "재료 일지가 명세에 없어 버림"))
        if u.attach:
            for ck in u.attach["codes"]:
                t = by_code.get(ck)
                if t is None or t.unit_kind not in ("c", "wd", "d_index"):
                    u.warnings.append(DEP_WARN + f"어디에 붙나 › 항목 {ck[0]}-{ck[1]:02d} 이 명세에 없어 버림")
                    continue
                d.append(("attach_to", t))
            for c in (u.attach.get("axis_codes", []) if with_axes else []):
                t = by_code.get(("AXIS", c))
                if t is None:
                    raise ManifestError(f"검토표 관심 축 코드 {c!r} 가 축 정의에 없어요")
                d.append(("axes", t))
        deps[u.key] = d
    return deps, skip


def build_manifest(cfg: dict, units: list, items: list, sheet: dict, idmap: dict, *,
                   target_user_id: str, source_instance_id: str, reviewed_at: str,
                   transform_version: str = DEFAULT_TRANSFORM_VERSION) -> dict:
    errs = check_sheet(items, sheet)
    if errs:
        raise ManifestError("검토가 끝나지 않아 명세를 만들지 않아요", errs)
    apply_review(units, items, sheet)
    co = cfg.get("custom_objects") or {}
    for u in units:
        if u.unit_kind == "ac":
            ac_fields(u, co["ac"]["fields"])

    deps, skip = resolve_deps(units)
    live = [u for u in units if u.key not in skip]
    order = _topo(live, deps)

    rows = []
    pending_idmap = {}
    rev_of = {}

    def tgt(oid):
        return {"original_id": oid, "revision": rev_of[oid]} if TARGET_WITH_REVISION else {"original_id": oid}

    for u in order:
        targets = sorted({(fld, t.original_id) for fld, t in deps[u.key]})
        tk = UNIT_TARGET_KIND[u.unit_kind]
        row = {"target_kind": tk, "unit_kind": EMIT_UNIT_KIND[u.unit_kind], "code": u.code,
               "title": u.title, "body": _body(u),
               "source_created_at": u.source_created_at, "source_date": u.source_date}
        if tk == "item":
            row["object_ref"] = (dict(OBJECT_REF[u.unit_kind]) if u.unit_kind in OBJECT_REF
                                 else {"key": co[u.object_ref]["key"]})
            row["fields"] = {k: v for k, v in u.fields.items() if v is not None}
            row["references"] = [{"field_key": f, "target": tgt(oid)} for f, oid in targets]
        elif u.attach is not None:
            a = u.attach
            row["attach"] = {
                "attach_to": [tgt(oid) for f, oid in targets if f == "attach_to"],
                "axes": [tgt(oid) for f, oid in targets if f == "axes"],
                "use_at": [strip_tokens(a["use_at"])] if a.get("use_at") else [],
                "unattached": [strip_tokens(a["unattached"])] if a.get("unattached") else [],
            }
        rev = row_revision(row)
        rev_of[u.original_id] = rev
        # previous_revision 은 생성기가 정하지 않는다 — 이전 CLI 가 서버의 최신 접수 판으로 채운다.
        pending_idmap[u.key] = {"original_id": u.original_id, "revision": rev}
        full = {"original_id": u.original_id, "revision": rev, "previous_revision": None,
                "expected_target_revision": None}
        full.update(row)
        rows.append(full)

    manifest = {"contract": CONTRACT, "target_user_id": target_user_id, "source_instance_id": source_instance_id,
                "transform_version": transform_version, "reviewed_at": reviewed_at, "rows": rows}
    manifest["manifest_sha256"] = manifest_hash(manifest)
    errors, _warn = validate_manifest(manifest)
    if errors:
        raise ManifestError("만든 명세가 검사를 통과하지 못했어요", errors)
    idmap["units"].update(pending_idmap)
    return manifest


def _topo(units: list, deps: dict) -> list:
    rank = {k: i for i, k in enumerate(UNIT_ORDER)}
    by_key = {u.key: u for u in units}
    sort_key = lambda u: (rank[u.unit_kind], u.locator)  # noqa: E731
    state, out = {}, []

    def visit(u, stack):
        st = state.get(u.key)
        if st == 2:
            return
        if st == 1:
            raise ManifestError("참조 순환이 있어요: " + " → ".join(x.label() for x in stack + [u]))
        state[u.key] = 1
        for _f, t in sorted(deps.get(u.key, []), key=lambda x: sort_key(x[1])):
            if t.key in by_key:
                visit(t, stack + [u])
        state[u.key] = 2
        out.append(u)

    for u in sorted(units, key=sort_key):
        visit(u, [])
    return out


# ═══════════════════════════════════════════════════════════════════════
# 검사(쓰기 없음)
# ═══════════════════════════════════════════════════════════════════════
REQUIRED_FIELDS = {"principle_c": ["register"], "principle_wd": ["register"],
                   "open_question": ["state", "registered_on"], "publication": ["channel", "published_on"]}
DATE_FIELDS = {"registered_on", "published_on", "happened_on"}
ATTACH_TO_KINDS = {"principle_c", "principle_wd", "decision_index"}


def _strings(v, path=""):
    if isinstance(v, str):
        yield path, v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _strings(x, f"{path}.{k}" if path else k)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _strings(x, f"{path}[{i}]")


def _ts(v) -> bool:
    return isinstance(v, str) and bool(RE_TS.match(v))


def _check_target(t, tag, E):
    """target = {original_id | record_id | item_id | candidate_id} 하나."""
    if isinstance(t, dict) and "original_id" in t and TARGET_WITH_REVISION:
        if set(t) != {"original_id", "revision"} or not isinstance(t["revision"], str) or not RE_REV.match(t["revision"]):
            E.append(f"{tag}: target 은 {{original_id, revision}}")
            return None
        t = {"original_id": t["original_id"]}
    if not isinstance(t, dict) or len(t) != 1:
        E.append(f"{tag}: target 은 키 하나짜리 객체")
        return None
    (k, v), = t.items()
    if k == "original_id":
        if not isinstance(v, str) or not RE_OID.match(v):
            E.append(f"{tag}: original_id 형식")
            return None
        return v
    if k in RE_SERVER_IDS:
        if not isinstance(v, str) or not RE_SERVER_IDS[k].match(v):
            E.append(f"{tag}: {k} 형식")
        return None
    E.append(f"{tag}: 모르는 target 키 {k}")
    return None


def validate_manifest(m) -> tuple:
    """(errors, warnings). 서버 계약 board-migration-1.schema.json + 의미 검사(참조 그래프·표기·제외·필수 값). 쓰기 없음."""
    E, W = [], []
    if not isinstance(m, dict):
        return ["명세가 JSON 객체가 아니에요"], W
    extra, missing = set(m) - TOP_KEYS, TOP_KEYS - set(m)
    if extra:
        E.append("최상위 추가 필드 금지: " + ", ".join(sorted(extra)))
    if missing:
        E.append("최상위 필드 없음: " + ", ".join(sorted(missing)))
    if m.get("contract") != CONTRACT:
        E.append(f"contract 는 {CONTRACT} 여야 해요")
    tu = m.get("target_user_id")
    if not isinstance(tu, str) or not 1 <= len(tu) <= 80:
        E.append("target_user_id 형식(1~80자)")
    if not isinstance(m.get("source_instance_id"), str) or not RE_SID.match(m["source_instance_id"]):
        E.append("source_instance_id 형식(is_ + 24 hex)")
    if not isinstance(m.get("transform_version"), str) or not RE_TV.match(m["transform_version"]):
        E.append("transform_version 형식(board-manifest/N)")
    if not _ts(m.get("reviewed_at")):
        E.append("reviewed_at 은 시간대가 붙은 ISO 8601 이어야 해요")
    ms = m.get("manifest_sha256")
    if not isinstance(ms, str) or not re.match(r"^[0-9a-f]{64}$", ms):
        E.append("manifest_sha256 형식")
    try:
        if ms != manifest_hash(m):
            E.append("manifest_sha256 불일치")
    except CanonicalError as e:
        E.append(f"정규화 불가: {e}")
    rows = m.get("rows")
    if not isinstance(rows, list) or not rows:
        E.append("rows 가 비었어요")
        return E, W

    ids, edges = {}, {}
    for n, r in enumerate(rows):
        tag = f"rows[{n}]"
        if not isinstance(r, dict):
            E.append(f"{tag}: 객체 아님")
            continue
        fb = set(r) & FORBIDDEN_ROW_KEYS
        if fb:
            E.append(f"{tag}: 금지 필드 " + ", ".join(sorted(fb)))
        extra = set(r) - ROW_ALLOWED - FORBIDDEN_ROW_KEYS
        if extra:
            E.append(f"{tag}: 추가 필드 금지 " + ", ".join(sorted(extra)))
        miss = ROW_REQUIRED - set(r)
        if miss:
            E.append(f"{tag}: 필드 없음 " + ", ".join(sorted(miss)))
            continue
        uk = r["unit_kind"]
        if uk in EXCLUDED_UNIT_KINDS:
            E.append(f"{tag}: 이전 제외 범위 단위 {uk}")
            continue
        if uk not in EMIT_TARGET_KIND:
            E.append(f"{tag}: 모르는 unit_kind {uk!r}")
            continue
        tk = r["target_kind"]
        if tk != EMIT_TARGET_KIND[uk]:
            E.append(f"{tag}: target_kind 가 unit_kind 와 안 맞아요")
            continue
        if not isinstance(r["original_id"], str) or not RE_OID.match(r["original_id"]):
            E.append(f"{tag}: original_id 형식")
        if not isinstance(r["revision"], str) or not RE_REV.match(r["revision"]):
            E.append(f"{tag}: revision 형식")
        if r["previous_revision"] is not None:
            E.append(f"{tag}: previous_revision 은 생성기가 정하지 않아요(null) — 이전 CLI 가 채운다")
        etr = r.get("expected_target_revision")
        if etr is not None and (not isinstance(etr, int) or isinstance(etr, bool) or etr < 0):
            E.append(f"{tag}: expected_target_revision 형식")
        if r.get("code") is not None and (not isinstance(r["code"], str) or len(r["code"]) > 80):
            E.append(f"{tag}: code 형식")
        if "title" in r and (not isinstance(r["title"], str) or len(r["title"]) > 1024):
            E.append(f"{tag}: title 은 1024자 이하 문자열")
        if "body" in r and (not isinstance(r["body"], str) or len(r["body"]) > 65536):
            E.append(f"{tag}: body 는 65536자 이하 문자열")
        if r.get("decision") not in (None, "pending", "confirmed", "excluded"):
            E.append(f"{tag}: decision 값")
        sd, sc = r.get("source_date"), r.get("source_created_at")
        if sd is not None and sc is not None:
            E.append(f"{tag}: source_date 와 source_created_at 을 함께 채울 수 없어요")
        if sd is not None and not _is_date(sd):
            E.append(f"{tag}: source_date 형식")
        if sc is not None and not _ts(sc):
            E.append(f"{tag}: source_created_at 은 시간대가 붙은 시각이어야 해요")
        oid = r["original_id"]
        if oid in ids:
            E.append(f"{tag}: original_id 중복")
        ids[oid] = (n, r)
        out = edges.setdefault(oid, [])
        if tk == "item":
            if "title" not in r:
                E.append(f"{tag}: item 은 title 필수")
            o = r.get("object_ref")
            if not isinstance(o, dict) or not o or set(o) - {"std_id", "template_slot", "key"}:
                E.append(f"{tag}: object_ref 형식(std_id·template_slot·key 중 하나 이상)")
            else:
                if "std_id" in o and not (isinstance(o["std_id"], str) and RE_STD.match(o["std_id"])):
                    E.append(f"{tag}: object_ref.std_id 형식")
                if "template_slot" in o and not (isinstance(o["template_slot"], str) and RE_SLOT.match(o["template_slot"])):
                    E.append(f"{tag}: object_ref.template_slot 형식")
                if "key" in o and not (isinstance(o["key"], str) and RE_KEY.match(o["key"])):
                    E.append(f"{tag}: object_ref.key 형식")
                if uk in EMIT_OBJECT_REF and o != EMIT_OBJECT_REF[uk]:
                    E.append(f"{tag}: object_ref 가 논리 키와 달라요")
            f = r.get("fields", {})
            if not isinstance(f, dict):
                E.append(f"{tag}: fields 는 객체")
                f = {}
            for k in REQUIRED_FIELDS.get(uk, []):
                if f.get(k) in (None, ""):
                    E.append(f"{tag}: 필수 필드 {k} 가 비었어요")
            for k in DATE_FIELDS & set(f):
                if not _is_date(f[k]):
                    E.append(f"{tag}: {k} 날짜 형식")
            refs = r.get("references", [])
            if not isinstance(refs, list):
                E.append(f"{tag}: references 는 배열")
                refs = []
            for k, ref in enumerate(refs):
                if not isinstance(ref, dict) or set(ref) != {"field_key", "target"}:
                    E.append(f"{tag}.references[{k}]: 형식({{field_key, target}})")
                    continue
                if not isinstance(ref["field_key"], str) or not RE_KEY.match(ref["field_key"]):
                    E.append(f"{tag}.references[{k}]: field_key 형식")
                t = _check_target(ref["target"], f"{tag}.references[{k}]", E)
                if t:
                    out.append((t, "ref", None))
            if "attach" in r:
                E.append(f"{tag}: attach 는 기록 행만")
        else:
            for k in ("object_ref", "fields", "decision"):
                if k in r:
                    E.append(f"{tag}: record 행엔 {k} 가 없어요")
            if "body" not in r:
                E.append(f"{tag}: record 는 body 필수")
            a = r.get("attach")
            if a is not None:
                if not isinstance(a, dict) or set(a) - {"attach_to", "axes", "use_at", "unattached"}:
                    E.append(f"{tag}: attach 형식")
                else:
                    for k in ("attach_to", "axes"):
                        for i, t in enumerate(a.get(k, [])):
                            oid2 = _check_target(t, f"{tag}.attach.{k}[{i}]", E)
                            if oid2:
                                out.append((oid2, k, None))
                    for k in ("use_at", "unattached"):
                        v = a.get(k, [])
                        if not isinstance(v, list) or any(not isinstance(x, str) or len(x) > 1024 for x in v):
                            E.append(f"{tag}: attach.{k} 는 1024자 이하 문자열 배열")
        for p, sv in _strings(r):
            rm = residual_markers(sv)
            if rm:
                E.append(f"{tag}.{p}: 표기가 남아 있어요 ({', '.join(rm)}) — 검토 미완료")
            if RE_URL.search(sv):
                W.append(f"{tag}.{p}: URL 이 들어 있어요")
            if RE_LOCALPATH.search(sv):
                W.append(f"{tag}.{p}: 로컬 경로 꼴이 들어 있어요")

    graph = {}
    for oid, outs in edges.items():
        n = ids[oid][0]
        g = []
        for t, how, _ in outs:
            if t not in ids:
                E.append(f"rows[{n}]: 참조 대상이 명세에 없어요")
                continue
            tu_kind = ids[t][1].get("unit_kind")
            if how == "attach_to" and tu_kind not in ATTACH_TO_KINDS:
                E.append(f"rows[{n}]: attach_to 대상은 기준·결정 항목이어야 해요")
            if how == "axes" and tu_kind != "axis":
                E.append(f"rows[{n}]: axes 대상은 축 항목이어야 해요")
            g.append(t)
        graph[oid] = g
    cyc = _find_cycle(graph)
    if cyc:
        E.append("참조 순환: " + " → ".join(f"rows[{ids[o][0]}]" for o in cyc))
    else:
        for oid, g in graph.items():
            for t in g:
                if ids[t][0] > ids[oid][0]:
                    E.append(f"rows[{ids[oid][0]}]: 참조 대상이 뒤에 있어요(의존 순서)")
    return E, W


def _find_cycle(graph: dict):
    state, stack = {}, []

    def dfs(v):
        state[v] = 1
        stack.append(v)
        for w in graph.get(v, []):
            if state.get(w) == 1:
                return stack[stack.index(w):] + [w]
            if state.get(w) is None:
                c = dfs(w)
                if c:
                    return c
        stack.pop()
        state[v] = 2
        return None

    for v in sorted(graph):
        if state.get(v) is None:
            c = dfs(v)
            if c:
                return c
    return None


# ═══════════════════════════════════════════════════════════════════════
# 명령
# ═══════════════════════════════════════════════════════════════════════
def cmd_sheet(config, state_dir, out) -> dict:
    cfg = load_config(config)
    units = collect_units(cfg)
    idmap = load_idmap(state_dir)
    if assign_ids(units, idmap):
        save_idmap(state_dir, idmap)
    items = review_items(units)
    resolve_deps(units, with_axes=False)
    outp = Path(out)
    if outp.exists():
        merge_sheet(items, parse_sheet(outp.read_text(encoding="utf-8")))
    outp.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(outp, render_sheet(items, units))
    return {"units": len(units), "blocking": sum(1 for i in items if i.blocking),
            "blocking_done": sum(1 for i in items if i.blocking and i.done),
            "optional": sum(1 for i in items if not i.blocking),
            "warnings": sum(len(u.warnings) for u in units)}


def cmd_build(config, state_dir, sheet_path, out, *, target_user_id, source_instance_id, reviewed_at,
              transform_version=DEFAULT_TRANSFORM_VERSION) -> dict:
    cfg = load_config(config)
    units = collect_units(cfg)
    idmap = load_idmap(state_dir)
    if assign_ids(units, idmap):
        raise ManifestError("검토표에 없는 새 단위가 있어요 — `sheet` 를 다시 실행하세요")
    items = review_items(units)
    sheet = parse_sheet(Path(sheet_path).read_text(encoding="utf-8"))
    manifest = build_manifest(cfg, units, items, sheet, idmap, target_user_id=target_user_id,
                              source_instance_id=source_instance_id, reviewed_at=reviewed_at,
                              transform_version=transform_version)
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(outp, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    save_idmap(state_dir, idmap)
    return {"rows": len(manifest["rows"]), "manifest_sha256": manifest["manifest_sha256"],
            "warnings": [f"{u.unit_kind} · {u.label()}: {w}" for u in units for w in u.warnings]}


def cmd_validate(manifest_path, config=None, state_dir=None, sheet_path=None) -> tuple:
    raw = Path(manifest_path).read_text(encoding="utf-8")
    m = json.loads(raw, object_pairs_hook=_no_dup_keys)
    errors, warnings = validate_manifest(m)
    if config and state_dir and sheet_path:
        cfg = load_config(config)
        units = collect_units(cfg)
        idmap = load_idmap(state_dir)      # 읽기만 — 새 ID 는 대응표에 쓰지 않는다
        if assign_ids(units, idmap):
            errors.append("검토표·대응표에 없는 새 단위가 있어요 — `sheet` 를 다시 실행하세요")
        items = review_items(units)
        errors += check_sheet(items, parse_sheet(Path(sheet_path).read_text(encoding="utf-8")))
    return errors, warnings


def _no_dup_keys(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ManifestError(f"중복 키: {k}")
        d[k] = v
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="board_manifest", description="board 정본 → board-migration/1 정제 명세(PC 전용)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet", help="AI 검토표 만들기·갱신")
    s.add_argument("--config", required=True)
    s.add_argument("--state-dir", required=True)
    s.add_argument("--out", required=True)
    b = sub.add_parser("build", help="검토 완료 뒤 정제 명세 만들기")
    b.add_argument("--config", required=True)
    b.add_argument("--state-dir", required=True)
    b.add_argument("--sheet", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--target-user-id", required=True)
    b.add_argument("--source-instance-id", required=True)
    b.add_argument("--reviewed-at", required=True, help="검토 완료 시각, 시간대 포함 ISO 8601")
    b.add_argument("--transform-version", default=DEFAULT_TRANSFORM_VERSION)
    v = sub.add_parser("validate", help="명세 검사(쓰기 없음)")
    v.add_argument("manifest")
    v.add_argument("--config")
    v.add_argument("--state-dir")
    v.add_argument("--sheet")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "sheet":
            r = cmd_sheet(a.config, a.state_dir, a.out)
            print(f"단위 {r['units']} · 차단 항목 {r['blocking']}(완료 {r['blocking_done']}) · 선택 {r['optional']} · 경고 {r['warnings']}")
            return 0
        if a.cmd == "build":
            r = cmd_build(a.config, a.state_dir, a.sheet, a.out, target_user_id=a.target_user_id,
                          source_instance_id=a.source_instance_id, reviewed_at=a.reviewed_at,
                          transform_version=a.transform_version)
            for w in r["warnings"]:
                print("경고:", w)
            print(f"행 {r['rows']} · manifest_sha256 {r['manifest_sha256']}")
            return 0
        errors, warnings = cmd_validate(a.manifest, a.config, a.state_dir, a.sheet)
        for w in warnings:
            print("경고:", w)
        for e in errors:
            print("오류:", e)
        print("통과" if not errors else f"실패 — 오류 {len(errors)}")
        return 0 if not errors else 1
    except ManifestError as e:
        print("거절:", e, file=sys.stderr)
        for x in e.messages:
            print("  -", x, file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, CanonicalError) as e:
        print("거절:", type(e).__name__, e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
