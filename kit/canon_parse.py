"""정본(C·WD·AC) 블록을 읽는 **한 파서**. 서버·lineage·워커·craft 가 각자 정규식으로 읽던 것을 여기로.

왜 한 자리인가 (2026-09-17, Codex 검토 Q4): 같은 파일을 네 곳이 다른 정규식으로 읽어 숫자가 갈렸다 —
구조 칩 19 대 서랍 17(issue-log), 지도와 구조가 다른 ⚠️AI 셈(2026-09-16), 서버가 lineage 결과를
다시 스캔해 보충하는 중복(checkpoint_board 1465). 판정이 흩어지면 화면마다 다른 답이 나온다.

무엇을 파나
  - 항목: `### C-XX.`(decisions.md) · `## WD-XX.`(work-decisions.md) · `## AC-XX.`(application-craft.md)
  - 칸(slot): `- **이름**: 값` 과 `- **이름** ⟨세션⟩: 값`, `- **이름** ⚠️AI (2026-08-25): 값` —
    **표기가 콜론 앞에 붙는 형태를 반드시 잡는다** (옛 craft·canon_slot_fill 정규식이 못 잡던 것).
  - 누적 답(entry): 칸 밑 하위 불릿 `  - …` 과 4칸 들여쓴 `출처:` 줄.
  - 이력: `**이력**` 아래 불릿 — 같은 구조에 hist=True.
  - 손 필요(pending): `⚠️AI` 표기 불릿(본문·이력 모두) + 항목 안의 블록 표식 줄.
    출처 줄 안에 인용된 "⚠️AI" 문자열, 파일 머리의 출처 표·규칙문·범례는 **세지 않는다**
    (항목 span 밖이라 애초에 안 본다).

표기의 뜻은 `career/work-decisions.md` 헤더 「출처 표기」가 정본이다 — 무표기=본인, `⟨세션⟩`=함께 구조화,
`⚠️AI`=AI 제안·미확정(손 필요), `AI✓`=AI 문장을 본인이 명시 승인, `✍️정리`=본인 판단을 AI가 문장화하고
본인이 제출(손 필요 아님). 이 파일은 그 표를 코드로 옮긴 것이고, 문서를 고치지 않는다.

sha 는 서버가 쓰던 값과 **같다** — `sha1(불릿 첫 줄 rstrip("\\n"))[:10]`. 화면·`canon_ai_answer`·
`canon_ai_prune`·assignments 에 이미 박힌 값이라 바꾸면 여물리기 버튼이 전부 깨진다.

모델 호출 없음·쓰기 없음. 순수 stdlib. Python 3.9 (서버가 부르는 인터프리터).
"""
import hashlib
import re
from pathlib import Path

HOME = Path.home()
import paths  # noqa: E402
P = paths.root()

REL = {"C": "career/decisions.md", "WD": "career/work-decisions.md",
       "AC": "career/application-craft.md"}
HEAD = {"C": re.compile(r"^### (C-\d+)\.[ \t]*(.*)$"),
        "WD": re.compile(r"^## (WD-\d+)\.[ \t]*(.*)$"),
        "AC": re.compile(r"^## (AC-\d+)\.[ \t]*(.*)$")}
NEXT = {"C": re.compile(r"^### |^## "), "WD": re.compile(r"^## "), "AC": re.compile(r"^## ")}

MARKS = ("✍️정리", "⚠️AI", "AI✓", "⟨세션⟩")
PENDING_MARK = "⚠️AI"

# 콜론 앞 표기까지 잡는다 — `- **긴장** ⟨세션⟩:` · `- **닫는 조건** ⚠️AI (2026-08-25):`
SLOT_RE = re.compile(r"^- \*\*([^*\n]+)\*\*([^:\n]*):[ \t]*(.*)$")
BARE_RE = re.compile(r"^- [ \t]*(.*)$")          # 라벨 없는 불릿 (`- ⚠️AI …`)
SUB_RE = re.compile(r"^  - [ \t]*(.*)$")         # 칸 밑 누적 답
# 저장 형식 2단 — 판단 문장과 분리된 출처 줄. **2칸 이상**이다: 하위 불릿 밑은 4칸이지만
# 빈 칸을 채운 경우(`- **장면** ✍️정리 …`)의 출처 줄은 2칸이라, 3칸으로 잡으면 그 줄이 칸 값에
# 섞여 대표 문장·근거 조립에 출처가 딸려 들어갔다 (2026-09-17 실측).
SOURCE_RE = re.compile(r"^[ \t]{2,}출처:")
HIST_RE = re.compile(r"^\*\*이력\*\*|^## 이력")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
BLOCK_RE = re.compile(r"^⚠️AI")                  # 줄머리 블록 표식 ("아래 묶음 문장은 AI가 쓴 것이다")
MATURITY = ("🌳", "🌿", "🌱")


def prefix_of(code):
    for k in ("WD", "AC", "C"):                  # WD 를 C 보다 먼저 본다 (둘 다 -로 끝나는 접두어)
        if str(code or "").startswith(k + "-"):
            return k
    return None


def path_of(code, root=P):
    pref = prefix_of(code)
    return (Path(root) / REL[pref]) if pref else None


def sha_of(line):
    """서버·화면이 쓰는 그 값 — 바꾸면 여물리기 버튼이 옛 sha 를 못 찾는다."""
    return hashlib.sha1(str(line).rstrip("\n").encode()).hexdigest()[:10]


def _marks(seg):
    return [m for m in MARKS if m in (seg or "")]


def _flat(lines):
    """불릿 한 덩어리를 화면 문자열로 — 서버 rescan 과 같은 규칙(앞 '- ' 제거 + 줄 합치기)."""
    return re.sub(r"^-\s*", "", " ".join(x.strip() for x in lines))[:400]


def _entry(lines, first_idx, name, mark_seg, value, hist, form="bullet"):
    first = lines[0]
    src = next((l.strip() for l in lines[1:] if SOURCE_RE.match(l)), "")
    body = [l for l in lines if not SOURCE_RE.match(l)]
    m = DATE_RE.search(mark_seg or "") or DATE_RE.search(first)
    marks = _marks(mark_seg) or _marks(first)
    # vtext = **값만** 이어 붙인 문장 (라벨·표기·출처 줄 제외) — 대표 문장이 쓰는 자리다.
    cont = [x.strip() for x in body[1:] if x.strip()]
    vtext = re.sub(r"\s+", " ", (str(value or "") + " " + " ".join(cont)).strip())
    return {"name": name, "marks": marks, "mark": (marks[0] if marks else ""),
            "value": value, "vtext": vtext, "text": _flat(body), "raw": "".join(lines),
            "date": m.group(1) if m else "", "source_line": src,
            "span": (first_idx, first_idx + len(lines)), "sha": sha_of(first),
            "hist": hist, "form": form,
            "pending": PENDING_MARK in (mark_seg or "") or (
                form == "block" and PENDING_MARK in first)}


def parse_text(text, pref, path=None):
    """항목 목록 — [{code,title,maturity,path,span,lines,slots,history,pending}].

    span 은 (시작 줄, 끝 줄) 0-based, 끝은 제외. lines 는 keepends 로 자른 그 구간이다.
    """
    lines = text.splitlines(keepends=True)
    heads = [(i, HEAD[pref].match(l.rstrip("\n"))) for i, l in enumerate(lines)]
    heads = [(i, m) for i, m in heads if m]
    out = []
    for n, (i, m) in enumerate(heads):
        end = next((j for j in range(i + 1, len(lines)) if NEXT[pref].match(lines[j])), len(lines))
        title = m.group(2).strip()
        item = {"code": m.group(1), "title": re.sub(r"[🌱🌿🌳]\s*", "", title).strip(),
                "maturity": next((x for x in MATURITY if x in title), "승격"),
                "path": str(path) if path else REL[pref], "prefix": pref,
                "span": (i, end), "lines": lines[i:end],
                "slots": [], "history": [], "pending": []}
        _parse_body(item, lines, i + 1, end)
        out.append(item)
    return out


def _parse_body(item, lines, start, end):
    hist = False
    k = start
    while k < end:
        ln = lines[k]
        bare = ln.rstrip("\n")
        if HIST_RE.match(bare):
            hist = True
            k += 1
            continue
        if bare.startswith(">") or not bare.strip():
            k += 1
            continue
        if BLOCK_RE.match(bare):                  # 항목 안의 블록 표식 — 그 블록 1건
            e = _entry([ln], k, None, bare, "", hist, form="block")
            (item["history"] if hist else item["slots"]).append(e)
            if e["pending"]:
                item["pending"].append(e)
            k += 1
            continue
        ms = SLOT_RE.match(bare)
        mb = None if ms else BARE_RE.match(bare)
        if not (ms or mb):
            k += 1
            continue
        name = ms.group(1).strip() if ms else None
        seg = (ms.group(2) if ms else (mb.group(1) if mb else ""))
        value = (ms.group(3).strip() if ms else (mb.group(1).strip() if mb else ""))
        buf, entries, j = [ln], [], k + 1
        while j < end:
            nxt = lines[j]
            if SUB_RE.match(nxt.rstrip("\n")):     # 칸 밑 누적 답 — 별도 entry 로 뗀다
                sub = [nxt]
                j += 1
                while j < end and lines[j].startswith("    ") and lines[j].strip():
                    sub.append(lines[j])
                    j += 1
                stext = SUB_RE.match(sub[0].rstrip("\n")).group(1).strip()
                entries.append(_entry(sub, j - len(sub), None, stext, stext, hist))
                continue
            if nxt.startswith("  ") and nxt.strip():
                buf.append(nxt)
                j += 1
                continue
            break
        e = _entry(buf, k, name, seg if ms else value, value, hist)
        e["entries"] = entries
        (item["history"] if hist else item["slots"]).append(e)
        for x in [e] + entries:
            if x["pending"]:
                item["pending"].append(x)
        k = j


def parse_file(pref, root=P):
    p = Path(root) / REL[pref]
    try:
        return parse_text(p.read_text(encoding="utf-8"), pref, path=REL[pref])
    except OSError:
        return []


def items(root=P, prefixes=("C", "WD", "AC")):
    out = []
    for pref in prefixes:
        out += parse_file(pref, root)
    return out


def item(code, root=P):
    pref = prefix_of(code)
    if not pref:
        return None
    return next((x for x in parse_file(pref, root) if x["code"] == code), None)


def locate(code, root=P):
    """(path, lines, start, end) — 옛 `checkpoint_board.canon_locate` 와 같은 반환."""
    pref = prefix_of(code)
    if not pref:
        return None, None, None, None
    p = Path(root) / REL[pref]
    try:
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return None, None, None, None
    for it in parse_text("".join(lines), pref, path=REL[pref]):
        if it["code"] == code:
            return p, lines, it["span"][0], it["span"][1]
    return None, None, None, None


def block_text(code, root=P, full=True, limit=2500):
    """항목 블록 본문 — full 이면 전문, 아니면 limit 자."""
    it = item(code, root)
    if it is None:
        return "(%s 항목을 못 찾았다)" % code
    t = "".join(it["lines"]).rstrip("\n")
    return t if full else t[:limit]


# ── 대표 문장 ────────────────────────────────────────────────────────────────
KEY_LABELS = {"C": ("기준", "내 기준", "맥락", "판단"), "WD": ("기준", "내 기준", "맥락", "판단"),
              "AC": ("회사가 뽑는 것", "as-is 장면", "input / output")}
EXCERPT_LINES = 15      # lineage 가 노드 excerpt 를 자르던 길이 — 결과를 바꾸지 않으려고 같은 창을 본다


def key_line(it, window=EXCERPT_LINES):
    """항목의 '중요한 문장' — 무표기·✍️정리 값을 우선하고 ⚠️AI·출처 줄은 뺀다. 모델 없음.

    옛 `checkpoint_board.key_line` 과 같은 값을 내야 한다 — 그쪽은 노드 excerpt(블록 앞 15줄)에서
    라벨을 찾았으므로 여기서도 같은 창(window)만 본다. 없으면 빈 문자열이 정직한 상태다.
    """
    if not it:
        return ""
    lo, hi = it["span"][0], it["span"][0] + window
    ok = [s for s in it["slots"]
          if s["span"][0] < hi and PENDING_MARK not in s["marks"] and s.get("value")]
    for lb in KEY_LABELS.get(it["prefix"], ()):
        for s in ok:
            if s["name"] == lb:
                return s["vtext"]
    return ""


def first_body(it, window=EXCERPT_LINES):
    """라벨을 못 찾았을 때 쓰는 본문 첫 줄 — 값 없는 라벨 줄·⚠️AI 줄은 건너뛴다."""
    if not it:
        return ""
    hi = it["span"][0] + window
    for s in it["slots"]:
        if s["span"][0] >= hi or PENDING_MARK in s["marks"] or not s.get("value"):
            continue
        return s["vtext"]
    return ""


# ── 손 필요 (⚠️AI 미확정) ────────────────────────────────────────────────────
def pending_items(it):
    """화면·서랍이 쓰는 모양 — [{sha, text, hist, form}] (등록 순서)."""
    return [{"sha": x["sha"], "text": x["text"], "hist": x["hist"], "form": x["form"]}
            for x in it["pending"]]


def pending_total(root=P, prefixes=("C", "WD", "AC")):
    """정본 **항목 안**의 ⚠️AI 건수 — 구조 칩과 서랍이 같은 수를 보게 하는 한 자리."""
    return sum(len(x["pending"]) for x in items(root, prefixes))


def pending_outside(root=P, extra=("career-frame.md",)):
    """항목 밖(코드 없는 자리)의 ⚠️AI — 승격 후보 묶음·career-frame 처럼 서랍에서 답할 수 없는 것.

    칩에 섞으면 "서랍에 없는 손 필요"가 생긴다(issue-log 칩19/서랍17). 그래서 따로 센다.
    """
    n = 0
    for pref in ("C", "WD", "AC"):
        try:
            text = (Path(root) / REL[pref]).read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.splitlines()
        inside = set()
        for it in parse_text(text, pref, path=REL[pref]):
            inside |= set(range(it["span"][0], it["span"][1]))
        for i, ln in enumerate(lines):
            if i in inside or ln.startswith(">"):
                continue
            if BLOCK_RE.match(ln) or re.match(r"^[-*][ \t]+(?:\*\*[^*\n]+\*\*[ \t]*)?⚠️AI", ln):
                n += 1
    for rel in extra:
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            if ln.startswith(">"):
                continue
            if BLOCK_RE.match(ln) or re.match(r"^[-*][ \t]+(?:\*\*[^*\n]+\*\*[ \t]*)?⚠️AI", ln):
                n += 1
    return n
