#!/usr/bin/env python3
"""자산 계보 수집기 — 결정적 파싱만 (모델 호출 없음).

정본·수집·산출을 훑어 nodes/edges JSON을 만들고, 템플릿에 주입해
<CANON_ROOT>/.runtime/personal-360.html (로컬 전용 360 뷰)을 생성한다.

원천:
  - career/decisions.md        : C-XX 기준 + D 색인
  - career/work-decisions.md   : WD-XX
  - career/sf-process-craft.md : craft 조각 (### 헤더)
  - journal/*.md               : "## 어디에 붙나" 코드 인용 → 엣지
  - drafts/published-log.md    : 발행 이력 (괄호 prose + `← 재료:` 줄)
  - visionary/**/*.md          : 본문 코드 인용 → 엣지

사용:
  python3 kit/lineage_scan.py            # JSON + HTML 생성
  python3 kit/lineage_scan.py --json-only
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canon_parse  # noqa: E402  (정본 블록·칸·표기 판정은 이 파서 한 자리 — 2026-09-17)


def nfc(s: str) -> str:
    """macOS 파일명은 NFD(자소 분해) — 비교 전 NFC 정규화 필수."""
    return unicodedata.normalize("NFC", s)

import paths  # noqa: E402
PERSONAL = paths.root()

TEMPLATE = paths.code() / "personal_360_template.html"
OUT_HTML = PERSONAL / ".runtime" / "personal-360.html"
OUT_JSON = PERSONAL / ".runtime" / ".lineage-data.json"

# 주의: \b는 한글 앞뒤에서 실패한다 ("C-05로") — lookaround로 대체
CODE_RE = re.compile(r"(?<![A-Za-z0-9-])(C|D|WD|CE|WF)-(\d{2,3})(?![0-9])")
EXCERPT_LINES = 15


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# ⚠️AI 셈 — 지도(personal_map)와 구조 탭(checkpoint_board)이 이 함수 하나를 부른다.
# 2026-09-16 ISSUE-202609-MAP-DUP-JUDGEMENT-RULES: 지도는 줄머리만(`^\s*[-*]?\s*⚠️AI`),
# 구조는 불릿 아무 자리(`^- .*⚠️AI`)를 세서 같은 파일에 다른 답을 냈다 (WD 13줄을 지도가 다 놓쳤다).
# 규칙 (2026-09-16 사용자 확정):
#   ① 불릿 머리가 ⚠️AI 인 줄 — `- ⚠️AI …` 와 `- **라벨** ⚠️AI: …` 둘 다
#   ② 줄 시작이 ⚠️AI 인 블록 표식 줄은 그 블록 1건 (work-decisions.md "아래 묶음 문장은 AI가 쓴 것이다")
#   ③ 규칙 설명문은 0 — ⚠️AI 를 설명 단어로 쓴다 (CLAUDE.md·self.md·README·issue-log)
AI_PENDING_FILES = ("career/decisions.md", "career/work-decisions.md", "career-frame.md")
AI_RULE_DOCS = {"CLAUDE.md", "self.md", "README.md", "issue-log.md"}
# \s 는 개행을 먹는다 — 줄 안 공백은 [^\S\n] 로 (pitfalls)
AI_BULLET_RE = re.compile(r"^[-*][ \t]+(?:\*\*[^*\n]+\*\*[ \t]*)?⚠️AI", re.M)
AI_BLOCK_RE = re.compile(r"^⚠️AI", re.M)


CANON_BY_NAME = {"decisions.md": "C", "work-decisions.md": "WD", "application-craft.md": "AC"}


def ai_pending_count(rel, text: str) -> int:
    """파일 하나의 ⚠️AI 미확정 건수. rel 은 경로 문자열이나 파일명.

    2026-09-17: 정본 세 파일은 `canon_parse` 가 **항목 안**만 센다 — 파일 머리의 출처 표·규칙문,
    항목 밖 「승격 후보」 묶음 표식은 서랍에서 답할 수 없어 여기 섞으면 칩과 서랍이 갈린다
    (issue-log 칩19/서랍17). 그 밖 파일(일지·career-frame 등)은 예전 줄 규칙 그대로다.
    """
    name = Path(str(rel)).name
    if name in AI_RULE_DOCS:
        return 0
    pref = CANON_BY_NAME.get(name)
    if not pref:
        return len(AI_BULLET_RE.findall(text)) + len(AI_BLOCK_RE.findall(text))
    # 정본 파일: **항목 안**은 파서가 세고(표기를 정확히 읽는다), 항목 밖(「승격 후보」 묶음 표식 등)은
    # 옛 줄 규칙 그대로 센다. 파일 합계는 예전과 같고, 서랍에서 답할 수 있는 몫만 파서 쪽에 있다.
    parsed = canon_parse.parse_text(text, pref, path=rel)
    inside = set()
    for it in parsed:
        inside |= set(range(it["span"][0], it["span"][1]))
    n = sum(len(it["pending"]) for it in parsed)
    for i, ln in enumerate(text.splitlines()):
        if i in inside:
            continue
        if AI_BULLET_RE.match(ln) or AI_BLOCK_RE.match(ln):
            n += 1
    return n


def ai_pending_total(root: Path = PERSONAL, rels=AI_PENDING_FILES) -> int:
    """정본 파일 묶음의 합 — 지도 카드가 쓴다. 구조 칩은 `canon_parse.pending_total` 을 쓴다."""
    return sum(ai_pending_count(r, read(root / r)) for r in rels)


def excerpt_around(text: str, start_line: int, n: int = EXCERPT_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[start_line : start_line + n]).strip()


def section_excerpt(text: str, heading_pat: str) -> tuple[str, int]:
    """heading regex에 걸리는 섹션의 발췌와 시작 줄 번호."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(heading_pat, ln):
            return excerpt_around(text, i), i + 1
    return "", 0


def maturity_of(title_line: str) -> str:
    if "🌳" in title_line:
        return "🌳"
    if "🌿" in title_line:
        return "🌿"
    if "🌱" in title_line:
        return "🌱"
    return "승격"  # 초기 승격분 — 이모지 표기 이전


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def node(self, nid: str, **kw):
        if nid in self.nodes:
            self.nodes[nid].update({k: v for k, v in kw.items() if v})
        else:
            self.nodes[nid] = {"id": nid, **kw}
        return self.nodes[nid]

    def edge(self, src: str, dst: str, kind: str):
        key = (src, dst, kind)
        if not any((e["src"], e["dst"], e["kind"]) == key for e in self.edges):
            self.edges.append({"src": src, "dst": dst, "kind": kind})



def scene_and_ai(body: str, pref: str = "WD"):
    """장면 슬롯 값과 ⚠️AI 불릿들 — 파서 결과 그대로 (360·구조 서랍 재료).

    2026-09-17: 옛 구현은 이력 앞만 봐서 서랍이 비었고, 서버가 블록 전체를 다시 스캔해 보충했다
    (checkpoint_board 1465). 이제 **이력 아래 불릿까지 파서가 준다** — 서버 재스캔은 지웠다.
    sha 는 예전과 같은 값이다.
    """
    parsed = canon_parse.parse_text(body, pref)
    if not parsed:
        return None, []
    it = parsed[0]
    scene = next((s["value"] for s in it["slots"] if s["name"] == "장면"), None)
    return scene, canon_parse.pending_items(it)


def parse_decisions(g: Graph):
    text = read(PERSONAL / "career" / "decisions.md")
    rel = "career/decisions.md"
    lines = text.splitlines()
    # C 블록
    c_idx = [
        (i, ln) for i, ln in enumerate(lines) if re.match(r"^### C-\d", ln)
    ]
    for pos, (i, ln) in enumerate(c_idx):
        m = re.match(r"^### (C-\d+)\.\s*(.*)$", ln)
        cid, title = m.group(1), m.group(2)
        end = c_idx[pos + 1][0] if pos + 1 < len(c_idx) else len(lines)
        body = "\n".join(lines[i:end])
        crit = re.search(r"\*\*기준\*\*:\s*(.+)", body)
        promo = re.search(r"\*\*승격\*\*:\s*([0-9-]+)", body)
        g.node(
            cid,
            type="C",
            title=re.sub(r"[🌱🌿🌳]\s*", "", title),
            maturity=maturity_of(title),
            summary=(crit.group(1)[:160] if crit else ""),
            date=(promo.group(1) if promo else ""),
            path=rel,
            anchor=f"### {cid}",
            excerpt=excerpt_around(body, 0),
            unnamed=("이름 미확정" in body or "미명명" in body),
            ai_pending=bool(scene_and_ai(body, "C")[1]),
            scene=scene_and_ai(body, "C")[0],
            ai_items=scene_and_ai(body, "C")[1],
        )
        for dm in re.finditer(r"\[\[(D-\d+)\]\]", body):
            g.edge(dm.group(1), cid, "근거")
    # D 색인 (제목만)
    for i, ln in enumerate(lines):
        m = re.match(r"^### (D-\d+)\.\s*(.*)$", ln)
        if m:
            g.node(
                m.group(1),
                type="D",
                title=re.sub(r"[🌱🌿🌳]\s*", "", m.group(2)),
                maturity=maturity_of(m.group(2)),
                path=rel,
                anchor=f"### {m.group(1)}",
            )


def parse_work_decisions(g: Graph):
    text = read(PERSONAL / "career" / "work-decisions.md")
    rel = "career/work-decisions.md"
    lines = text.splitlines()
    idx = [(i, ln) for i, ln in enumerate(lines) if re.match(r"^## WD-\d", ln)]
    for pos, (i, ln) in enumerate(idx):
        m = re.match(r"^## (WD-\d+)\.\s*(.*)$", ln)
        end = idx[pos + 1][0] if pos + 1 < len(idx) else len(lines)
        wd_body = "\n".join(lines[i:end])
        g.node(
            m.group(1),
            type="WD",
            title=re.sub(r"[🌱🌿🌳]\s*", "", m.group(2)),
            maturity=maturity_of(ln),
            summary=(lambda mm: mm.group(1)[:160] if mm else "")(
                re.search(r"\*\*(?:내 )?(?:기준|판단)(?:[^*]*)?\*\*[^:]*:\s*(.+)", wd_body)),
            path=rel,
            anchor=f"## {m.group(1)}",
            excerpt=excerpt_around(text, i),
            unnamed=("이름 미확정" in wd_body or "미명명" in wd_body),
            ai_pending=bool(scene_and_ai(wd_body)[1]),
            scene=scene_and_ai(wd_body)[0],
            ai_items=scene_and_ai(wd_body)[1],
        )


def parse_craft(g: Graph):
    text = read(PERSONAL / "career" / "sf-process-craft.md")
    rel = "career/sf-process-craft.md"
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if re.match(r"^### ", ln)]
    for n, i in enumerate(idx, 1):
        cid = f"CRAFT-{n:02d}"
        end = idx[n] if n < len(idx) else len(lines)
        body = "\n".join(lines[i:end])
        g.node(
            cid,
            type="craft",
            title=lines[i].lstrip("# ").strip()[:80],
            path=rel,
            anchor=lines[i].lstrip("# ").strip()[:60],
            excerpt=excerpt_around(text, i),
        )
        for m in CODE_RE.finditer(body):
            if m.group(1) in ("C", "D", "WD"):
                g.edge(f"{m.group(1)}-{m.group(2)}", cid, "craft 근거")


def parse_journal(g: Graph):
    for f in sorted((PERSONAL / "journal").glob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        text = read(f)
        sec, line_no = section_excerpt(text, r"^## 어디에 붙나")
        if not sec:
            continue
        jid = f"J:{f.stem}"
        g.node(
            jid,
            type="journal",
            title=f"일지 {f.stem}",
            date=f.stem,
            path=f"journal/{f.name}",
            anchor="## 어디에 붙나",
            excerpt=sec,
        )
        for m in CODE_RE.finditer(sec):
            code = f"{m.group(1)}-{m.group(2)}"
            if m.group(1) in ("C", "D", "WD"):
                g.edge(jid, code, "어디에 붙나")


def parse_published(g: Graph):
    text = read(PERSONAL / "drafts" / "published-log.md")
    entries = re.findall(
        r"^- (\d{4}-\d{2}-\d{2}) — (.+?)(?:\n(\s+← 재료:.*))?$",
        text,
        re.M,
    )
    for i, (date, rest, material_line) in enumerate(entries, 1):
        pid = f"PUB-{i:02d}"
        title = re.sub(r"\s*\(.*\)\s*$", "", rest)[:60]
        g.node(
            pid,
            type="publication",
            title=title,
            date=date,
            path="drafts/published-log.md",
            excerpt=(rest + ("\n" + material_line.strip() if material_line else "")),
        )
        blob = rest + (material_line or "")
        for jm in re.finditer(r"journal/(\d{4}-\d{2}-\d{2})\.md", blob):
            jid = f"J:{jm.group(1)}"
            if jid not in g.nodes:
                jf = PERSONAL / "journal" / f"{jm.group(1)}.md"
                g.node(
                    jid,
                    type="journal",
                    title=f"일지 {jm.group(1)}",
                    date=jm.group(1),
                    path=f"journal/{jf.name}",
                    excerpt="\n".join(read(jf).splitlines()[:EXCERPT_LINES]),
                )
            g.edge(jid, pid, "재료")
        for m in CODE_RE.finditer(blob):
            if m.group(1) in ("C", "D", "WD"):
                g.edge(f"{m.group(1)}-{m.group(2)}", pid, "재료")


def parse_visionary(g: Graph):
    for f in sorted((PERSONAL / "visionary").rglob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        text = read(f)
        codes = {
            f"{m.group(1)}-{m.group(2)}"
            for m in CODE_RE.finditer(text)
            if m.group(1) in ("C", "D", "WD")
        }
        if not codes:
            continue
        vid = f"V:{f.stem}"
        first = section_excerpt(text, r"^## 어디에 붙나")[0] or "\n".join(
            text.splitlines()[:EXCERPT_LINES]
        )
        g.node(
            vid,
            type="visionary",
            title=f.stem.replace("_", " ")[:60],
            path=str(f.relative_to(PERSONAL)),
            excerpt=first,
        )
        for code in sorted(codes):
            g.edge(vid, code, "수집 연결")


def parse_usage(g: Graph, path: Path | None = None):
    """usage/start 인용만 활용으로 센다. close는 start 인용의 중복이다."""
    p = path or (PERSONAL / "career" / "canon-usage.jsonl")
    if not p.exists():
        return
    for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p}:{line_no}: invalid JSON: {exc.msg}") from exc
        if row.get("event") not in {"usage", "start"}:
            continue
        for cited in row.get("cited", []):
            if cited in g.nodes:
                g.nodes[cited]["used"] = g.nodes[cited].get("used", 0) + 1


def compute_downstream(g: Graph):
    """정본 노드의 하류(활용) 집계 — 발행·evidence 엣지 + ask-canon 인용."""
    for n in g.nodes.values():
        if n["type"] in ("C", "D", "WD"):
            outs = [
                e
                for e in g.edges
                if e["src"] == n["id"]
                and g.nodes.get(e["dst"], {}).get("type")
                in ("publication", "evidence")
            ]
            ins = [e for e in g.edges if e["dst"] == n["id"]]
            n["downstream"] = len(outs) + n.get("used", 0)
            n["upstream"] = len(ins)


def main():
    g = Graph()
    parse_decisions(g)
    parse_work_decisions(g)
    parse_craft(g)
    parse_journal(g)
    parse_published(g)
    parse_visionary(g)
    parse_usage(g)
    compute_downstream(g)

    # 엣지의 미해결 참조 제거 (색인에 없는 코드 인용 등)
    g.edges = [
        e for e in g.edges if e["src"] in g.nodes and e["dst"] in g.nodes
    ]

    data = {"nodes": list(g.nodes.values()), "edges": g.edges}
    OUT_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    counts = {}
    for n in g.nodes.values():
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    print(f"nodes={len(g.nodes)} edges={len(g.edges)} {counts}")

    if "--json-only" not in sys.argv:
        tpl = read(TEMPLATE)
        if not tpl:
            print(f"템플릿 없음: {TEMPLATE} — JSON만 생성", file=sys.stderr)
            return
        html = tpl.replace(
            "__LINEAGE_DATA_JSON__", json.dumps(data, ensure_ascii=False)
        )
        OUT_HTML.write_text(html, encoding="utf-8")
        print(f"→ {OUT_HTML}")


if __name__ == "__main__":
    main()
