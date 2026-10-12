"""자기소개서·이력서 craft 정본(`career/application-craft.md`) 파서 — 칸 목록·후보 재료·인용 대조.

왜 여기 한 자리인가: 칸(축 × 네 칸)을 세 곳이 쓴다 — 생산(`summarize_feed.py` 가르침 생성·검증),
화면(`checkpoint_board.py` 개인 › 자소서 진행), 반영(`canon_slot_fill`). 목록을 하드코딩하면
정본에 축을 하나 더 얹는 순간 셋이 어긋난다. **파일이 정본이고 코드는 매번 다시 읽는다.**

모델 호출 없음·쓰기 없음. Python 3.9 (서버가 부르는 인터프리터).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canon_parse  # noqa: E402  (블록·칸·누적 답 파싱은 그 파서 한 자리 — 2026-09-17)

# 의존 방향: grounding → application_craft → canon_parse 단방향. 이 모듈은 grounding 을 import
# 하지 않는다 (인용 허용·대조가 필요하면 `grounding.quote_ok`).

REL = "career/application-craft.md"
MATERIAL = re.compile(r"^- 후보 재료:")
BACKTICK = re.compile(r"`([^`]+)`")
AI_MARK = ("⚠️AI", "⟨세션⟩")


def _text(root):
    return (Path(root) / REL).read_text(encoding="utf-8")


def sections(root):
    """[{code, title, lines, item}] — `## AC-XX.` 블록. 헤딩·경계 판정은 canon_parse 한 자리."""
    out = []
    for it in canon_parse.parse_file("AC", Path(root)):
        out.append({"code": it["code"], "title": it["title"],
                    "lines": [l.rstrip("\n") for l in it["lines"][1:]], "item": it})
    return out


def slots(root):
    """축 × 칸 전부 — [{code, axis, name, key, value, entries, filled}]. key 가 화면·모델·id 표기다.

    `entries` 는 칸 **밑에 쌓인 하위 불릿**(`  - …`)이다. 칸은 최종 데이터가 아니라 장면이 생길
    때마다 쌓는 자리라(2026-09-10 사용자) 저장은 첫 줄을 채운 뒤 두 번째부터 불릿으로 누적한다.
    첫 줄만 읽으면 두 번째부터 낸 답이 grounding·화면에서 통째로 빠진다 (2026-09-17 Codex Q6).
    한 불릿 안에서 더 들여쓴 줄(출처·경위)은 그 불릿에 이어 붙인다.
    `filled` 의 뜻은 바꾸지 않는다 — 첫 줄이 찼나 (개인 › 자소서 진행 숫자가 이 값을 쓴다).
    """
    out = []
    for sec in sections(root):
        for s in sec["item"]["slots"]:
            if not s["name"]:
                continue                                       # 라벨 없는 불릿(후보 재료·열린 질문)은 칸이 아니다
            out.append({"code": sec["code"], "axis": sec["title"], "name": s["name"],
                        "key": "%s/%s" % (sec["code"], s["name"]),
                        "marks": s["marks"],
                        "value": s["value"],
                        "entries": [e["vtext"] for e in (s.get("entries") or [])],
                        "filled": bool(s["value"])})
    return out


def slot_keys(root):
    return [s["key"] for s in slots(root)]


def parse_key(key):
    """"AC-01/as-is 장면" → ("AC-01", "as-is 장면"). 형식이 아니면 (None, None)."""
    m = re.fullmatch(r"(AC-\d+)/(.+)", (key or "").strip())
    return (m.group(1), m.group(2).strip()) if m else (None, None)


def progress(root):
    """축별 진행 — [{code, axis, total, filled, slots:[...]}] (개인 › 자소서 진행 줄)."""
    by = {}
    for s in slots(root):
        b = by.setdefault(s["code"], {"code": s["code"], "axis": s["axis"], "slots": []})
        b["slots"].append({"name": s["name"], "filled": s["filled"], "value": s["value"]})
    out = []
    for code in sorted(by):
        b = by[code]
        b["total"] = len(b["slots"])
        b["filled"] = sum(1 for x in b["slots"] if x["filled"])
        out.append(b)
    return out


def material_paths(root):
    """축별 「후보 재료」 줄의 백틱 경로 — 가르침 프롬프트가 먼저 볼 파일 (정본이 스스로 선언한다).

    2026-09-17: `~/...` 처럼 데이터 루트 밖 절대경로도 받는다 (프로젝트 문서를 재료로
    선언할 수 있게). 반환 문자열은 선언된 그대로다 — 파일을 열 때는 `grounding.resolve` 를 쓴다.
    존재 확인은 그대로 유지한다. 인용 허용 범위는 `grounding.quotable` 이 정한다.
    """
    out = {}
    for sec in sections(root):
        paths = []
        for line in sec["lines"]:
            if MATERIAL.match(line):
                for p in BACKTICK.findall(line):
                    p = p.strip()
                    if not p.endswith(".md") or p in paths:
                        continue
                    target = (Path(p).expanduser() if p.startswith("~/") or p.startswith("/")
                              else Path(root) / p)
                    if target.exists():
                        paths.append(p)
        out[sec["code"]] = paths
    return out


def _squash(t):
    return re.sub(r"\s+", " ", t or "").strip()


# `quote_ok` 는 2026-09-17 `grounding.quote_ok` 로 옮겼다 — 인용 허용 판정(quotable)·경로 해석·
# 원문 대조가 한 자리여야 하고, 이 모듈이 grounding 을 import 하지 않게 하기 위해서다.
