"""축 한 페이지 — 관심 축 하나에 대해 "내 경험 + 모은 글 + 아직 답 없는 것"을 A4 한 장 분량 .md 로 **매번 다시 조립**한다.

  python3 scripts/axis_page.py C-1              # → _workspace/axis/C-1.md (1인칭 서술, 워커가 문장을 잇는다)
  python3 scripts/axis_page.py C-1 --open       # 다 되면 Obsidian 으로 연다 (보드 버튼의 첫 쓰기)
  python3 scripts/axis_page.py --auto           # 모든 축 — 재료가 바뀐 축만 다시 쓴다 (하루 한 번 잡, 안 연다)
  python3 scripts/axis_page.py C-1 --if-stale   # 그 축만 — 재료가 바뀌었을 때만 (버튼이 열고 나서 뒤에서)
  python3 scripts/axis_page.py C-1 --force      # 재료가 같아도 다시
  python3 scripts/axis_page.py C-1 --outline    # 옛 양식(절·불릿) — 재료가 뭐가 모였는지 눈으로 볼 때
  python3 scripts/axis_page.py C-1 --material   # 재료 전문을 stdout 으로 (모델 안 부름, 파일 안 남김)
  python3 scripts/axis_page.py --list           # 축 목록과 재료 건수

2026-09-10 사용자: "이런 축들의 인사이트의 모아 나의 경험과 합치거나 추가해 … 최대 a4 한페이지로 내가 이 축에 대해 어떤
경험이 있고 인사이트를 수집한 글, 경험을 활용해 나에 대해 한 페이지" / "축을 누르면 .md로 나올 수 있게".
양식은 그 뒤 사용자가 바로잡았다: "양식이 있는게 아니라 너가 나의 입장에서 포함된 글을 합쳐서 하나의 '나'를 소개하는
문장으로 적어줬으면 좋겠어. 살을 붙인다고 하지. 나눌 수 있는건 소제목정도야." → **1인칭 경어체 서술, 소제목만.**

원칙 — 이 파일은 **조립물**이다(자소서 craft 와 최종본의 구분과 같다). 정본은 안 건드린다. 손으로 고치고 싶은 문장은
여기가 아니라 정본(career/*.md · insights.md)에 고친다 — 다음 조립에 따라온다.
재료 수집은 결정적(모델 없음)이고, **문장 잇기만** 워커가 한다(사용자가 버튼을 누를 때 — 무인 아님).
뼈는 정본·대화의 **내 문장**이고 살은 모은 글의 방식이다. 계약 검증(valid_prose)이 유일한 방어선:
내 문장이 재료에 없거나 본문에 안 쓰였으면, 재료에 없는 숫자가 있으면, 불릿·표·코드 표기가 섞이면 **페이지를 안 덮어쓴다**.

재료(전부 읽기만):
  trend-interests.md 관심 축 표(축 이름) · feed-queue.jsonl(그 축 글: summary·long·canon_fit·status·grade)
  .lineage-data.json(C·WD 노드: 제목·장면·기준) → 없으면 career/*.md 직접 파싱
  insights.md 그 축 절의 「내 생각」 · open-questions.md 큐 · career/sf-process-craft.md
  assignments.jsonl 피드 스레드의 **사용자 턴** (질문·요청문은 뺀다 — run_worker.asked_like 선례)
축 ↔ 내 판단의 연결은 글의 canon_fit(근거·반증·인접)로 역추적한다 — C·WD 항목엔 축 표기가 없다(2026-09-10 실측).
Python 3.9.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import journal_writing  # noqa: E402  (관심 축 표 파서 재사용)
import checkpoint_store  # noqa: E402  (assignments.jsonl 읽기 — 쓰지 않는다)
import worker_claude  # noqa: E402
import models  # noqa: E402  (모델 ID 한 자리 — scripts/models.json)
import worker_status  # noqa: E402

KST = timezone(timedelta(hours=9))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
FEED = P / "visionary/linkedin/feed-queue.jsonl"
LINEAGE = P / ".runtime/.lineage-data.json"
INSIGHTS = P / "visionary/linkedin/insights.md"
OPENQ = P / "career/open-questions.md"
ASSIGN = P / "_workspace/checkpoint/assignments.jsonl"
CRAFT = P / "career/sf-process-craft.md"
OUT_DIR = P / "_workspace/axis"
CANON = {"C": P / "career/decisions.md", "WD": P / "career/work-decisions.md", "AC": P / "career/application-craft.md"}
REL_NAME = {"근거": "같은 결론에 닿은 것", "반증": "반대로 본 것", "인접": "옆에 놓인 것"}
MAX_SCENES = 3          # 내 장면 — A4 한 장이라 셋까지
MAX_PER_BUCKET = 4      # 같은 결론/반대/인접 글 — 묶음당 넷까지, 나머지는 건수
MAX_UNLINKED = 5        # 내 판단과 안 닿은 글 — 다섯까지
MAX_NUMBERS = 8
MODEL = models.use("axis.page")   # 버튼 + launchd axis-pages 매일 14:00 --auto(무인, 재료 바뀐 축만). 배정은 models.json
MIN_CHARS, MAX_CHARS = 1200, 2600
MAX_FLESH = 24          # 살로 쓸 글 — 프롬프트엔 여기까지
MAX_TURNS_THREADS = 8   # 대화 스레드 — 여덟까지
SEP = " ¦ "             # 내 문장 묶음 구분 — 문장을 이어 붙여 통과하는 것 방지
# 재료 파일 — 화면의 "새 재료 있음" 은 이 파일들의 수정 시각만 본다 (재료 계산 없이 싸게)
SOURCES = [FEED, LINEAGE, INSIGHTS, OPENQ, ASSIGN, CRAFT, P / "trend-interests.md"] + list(CANON.values())
NOISE = ("fingerprint", "at", "checked_at")   # 재료가 아니라 이 코드의 살림 — 지문에서 뺀다
# 판의 양식 판 — 올리면 재료가 같아도 모든 축이 한 번 다시 쓰인다 (2: 사례 절 추가 2026-09-23)
PAGE_FORMAT = 2
CASES_HEAD = "## 사례로 다시 보기"
CASE_MAX = 350          # 사례 칸 하나의 상한 (자)
CASE_LABELS = (("scene", "그때 상황"), ("pivot", "바꾼 것과 포기한 것"),
               ("result", "관측한 결과"), ("principle", "남은 기준"))
NO_RESULT = "아직 관측한 결과가 없습니다."


def _rows():
    out = []
    try:
        for line in FEED.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out


def axis_rows(rows, axis):
    return [r for r in rows if axis in (r.get("axes_hint") or []) or axis in (r.get("axes") or [])]


def _sq(t):
    return re.sub(r"\s+", " ", t or "").strip()


def _norm(t):
    """대조용 정규화 — 강조·인용 기호 제거 + 공백 한 칸 (run_worker._norm_words 와 같은 결)."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`\"“”'‘’]", "", t or "")).strip()


# ── 내 판단(C·WD·AC) — lineage 우선, 없으면 md 파싱 ────────────────────────────
def canon_nodes():
    """{code: {title, scene, summary(기준), maturity, ai, path}}"""
    out = {}
    try:
        d = json.loads(LINEAGE.read_text(encoding="utf-8"))
        for n in d.get("nodes", []):
            if n.get("type") in ("C", "WD"):
                out[n["id"]] = {"title": n.get("title") or "", "scene": (n.get("scene") or "").strip(),
                                "summary": (n.get("summary") or "").strip(), "maturity": n.get("maturity") or "",
                                "ai": len(n.get("ai_items") or []), "path": n.get("path") or ""}
    except (OSError, json.JSONDecodeError):
        pass
    return out


def canon_block(code):
    """md 에서 직접 — lineage 에 없는 코드(AC-XX 등)용. (title, scene, first_rule, answers[])"""
    pref = code.split("-")[0]
    path = CANON.get(pref)
    if not path or not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    head = re.compile(r"^#{2,3} " + re.escape(code) + r"\.\s*(.*)$")
    start = next((i for i, l in enumerate(lines) if head.match(l)), None)
    if start is None:
        return None
    title = head.match(lines[start]).group(1).strip()
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^#{2,3} ", lines[i])), len(lines))
    scene, rule, answers = "", "", []
    for l in _unwrap(lines[start + 1:end]):
        m = re.match(r"^- \*\*장면\*\*:\s*(.*)$", l)
        if m and not scene:
            scene = m.group(1).strip()
        m = re.match(r"^- \*\*기준\*\*:\s*(.*)$", l)
        if m and not rule:
            rule = m.group(1).strip()
        m = re.match(r"^- \*\*(?:열린 질문 답|가르침 답)\*\*[^:]*:\s*(.*)$", l)
        if m:
            answers.append(re.sub(r"\s*\((?:Q|가르침):.*$", "", m.group(1)).strip())
    return {"title": title, "scene": scene, "summary": rule, "answers": answers, "path": str(path.relative_to(P))}


BULLET = re.compile(r"^(?:[-*+•][ \t]|#{1,6}[ \t]|\d+[.)][ \t])")


def _unwrap(lines):
    """정본 md 는 손으로 줄을 접는다 — 불릿 하나가 여러 줄에 걸친다. 이어 붙여 한 줄로 돌린다.
    (2026-09-10 실측: 접힌 자리에서 기준·장면 값이 잘려 프롬프트에도 반쪽만 갔고, 모델이 접힌 채 인용한
    문장은 대조에서 떨어졌다.)"""
    out = []
    for l in lines:
        if out and l[:1] in (" ", "\t") and not BULLET.match(l.strip()):
            out[-1] = out[-1].rstrip() + " " + l.strip()
        else:
            out.append(l.rstrip("\n"))
    return out


def _blocks(text):
    """대조용 덩어리 — 빈 줄·불릿·헤딩에서 끊고, 접힌 줄은 이어 붙인다. 덩어리 밖으로는 이어 붙지 않는다."""
    out, cur = [], []
    for line in (text or "").splitlines():
        st = line.strip()
        if not st:
            if cur:
                out.append(" ".join(cur))
                cur = []
            continue
        if BULLET.match(st):
            if cur:
                out.append(" ".join(cur))
                cur = []
            st = BULLET.sub("", st)
        cur.append(st)
    if cur:
        out.append(" ".join(cur))
    return [x for x in out if x]


# ── insights.md 그 축 절의 내 생각 ────────────────────────────────────────────
def insights_mine(axis):
    """[{who, when, text}] — `## <axis>.` 절 안의 `#### 내 생각` 블록만 (본인 문장). ⟨세션⟩·⚠️AI 표기 블록은 뺀다."""
    try:
        text = INSIGHTS.read_text(encoding="utf-8")
    except OSError:
        return []
    # 같은 코드의 절이 **여러 개** 있을 수 있다 — 이름이 달라도 다 모은다 (2026-09-11 실측: insights.md 에
    # "## C. Salesforce·Agentforce 현장"과 "## C. Salesforce/Agentforce 현장 …" 두 절이 있어 뒤 절의 「내 생각」
    # 2건이 C 한 장에서 빠졌다). 본문 병합은 사용자 몫 — 여기서는 읽기만 한다.
    secs = []
    for m in re.finditer(r"^## " + re.escape(axis) + r"\.\s.*$", text, re.M):
        sec = text[m.end():]
        nxt = re.search(r"^## ", sec, re.M)
        secs.append(sec[:nxt.start()] if nxt else sec)
    if not secs:
        return []
    out = []
    for sec in secs:
        for post in re.split(r"^### ", sec, flags=re.M)[1:]:
            who = post.splitlines()[0].strip()
            for blk in re.finditer(r"^#### 내 생각(.*?)$\n(.*?)(?=^####|\Z)", post, re.M | re.S):
                tag, body = blk.group(1), blk.group(2).strip()
                if "⟨세션" in tag or "⚠️AI" in body:
                    continue
                body = "\n".join(l for l in body.splitlines() if l.strip() and not l.startswith("---")).strip()
                if body:
                    out.append({"who": who, "when": _sq(tag).strip("() "), "text": body})
    return out


# ── 열린 질문 — 그 코드들을 겨눈 것 ────────────────────────────────────────────
def _targets(codes):
    out = []
    for tok in re.split(r"[·,/]|\s+and\s+", codes):
        m = re.fullmatch(r"\s*(C|WD|AC)-(\d+)(?:\s*[~–-]\s*(?:\1-)?(\d+))?\s*", tok)
        if not m:
            continue
        a, b = int(m.group(2)), int(m.group(3) or m.group(2))
        out += ["%s-%02d" % (m.group(1), n) for n in range(a, b + 1)]
    return out


def open_questions(codes):
    """큐(`## 큐` 절) 불릿 중 대상이 codes 와 겹치는 것 — [{codes, q}]"""
    try:
        text = OPENQ.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^## 큐\s*$", text, re.M)
    if not m:
        return []
    sec = text[m.end():]
    nxt = re.search(r"^## ", sec, re.M)
    if nxt:
        sec = sec[:nxt.start()]
    out = []
    for b in re.finditer(r"^- \[([^\]]+)\]\s*(.*?)(?=^- \[|\Z)", sec, re.M | re.S):
        tg = _targets(b.group(1))
        hit = [c for c in tg if c in codes]
        if hit:
            q = _sq(re.sub(r"^\s+→ .*$", "", b.group(2), flags=re.M))
            q = re.sub(r"\(등록(?:일)? ?\d{4}-\d{2}-\d{2}\)\s*$", "", q).strip()
            out.append({"codes": hit, "q": q})
    return out


# ── 대화 속 내 문장 — 피드 스레드의 사용자 턴 ──────────────────────────────────
ASKED_LIKE = re.compile(r"(\?|？|줘|주세요|해봐|볼까|해줘|알려|보자|궁금해|궁금하다|붙여놔|적어놔|정리해)[.!。]?$")
ACK = re.compile(r"^(?:ㅇㅇ|ㅇㅋ|오케이|오키|굳|응|알겠|고맙|감사|맞아|좋아|넵)")


def _sents(t):
    """줄·문장 단위로 쪼갠다 — 한 턴에 질문과 판단이 같이 오므로 문장별로 걸러야 한다."""
    out = []
    for line in re.split(r"[\n\r]+", t or ""):
        for s in re.split(r"(?<=[.?!…])\s+", line):
            s = _sq(s)
            if s:
                out.append(s)
    return out


def _is_mine(s):
    """내 판단 문장인가 — 질문·요청문(run_worker.asked_like 선례)·수긍·짧은 말은 아니다."""
    if len(s) < 14:
        return False
    if ASKED_LIKE.search(s) or ACK.match(s):
        return False
    return True


def thread_turns(axis):
    """피드 스레드(뿌리 source=="feed")의 **사용자 턴** 중 그 스레드 axes 에 이 축이 있는 것.
    [{title, turns:[문장]}] — 읽기만 한다(checkpoint_store.read)."""
    rows_all = checkpoint_store.read(ASSIGN)
    by = {r.get("ts"): r for r in rows_all if r.get("ts")}
    out = {}
    for r in rows_all:
        cur, hop = r, 0
        while cur.get("parent_ts") and by.get(cur["parent_ts"]) and hop < 24:
            cur = by[cur["parent_ts"]]
            hop += 1
        root = cur
        if root.get("source") != "feed":
            continue
        ax = (root.get("axes") or r.get("axes") or [])
        if not isinstance(ax, list) or axis not in ax:
            continue
        slot = out.setdefault(root.get("ts"), {"title": _sq(root.get("title") or "")[:70], "turns": []})
        cand = [r.get("ask") or ""]
        for x in r.get("answers") or []:
            if isinstance(x, dict) and not x.get("final"):
                cand.append(x.get("a") or "")
        for t in cand:
            for s in _sents(t):
                if _is_mine(s) and s not in slot["turns"]:
                    slot["turns"].append(s)
    return [v for v in out.values() if v["turns"]][:MAX_TURNS_THREADS]


# ── 재료 모으기 (결정적 — 모델 없음) ──────────────────────────────────────────
def _link(r):
    return r.get("post_url") or ""


def _who(r):
    a = _sq(r.get("author") or "")
    src = r.get("source") or "linkedin"
    tag = {"reddit": "Reddit", "brunch": "브런치", "naver_blog": "네이버", "web": "웹"}.get(src, "LinkedIn")
    return "%s · %s" % (a[:24], tag) if a else tag


def _one_line(r):
    return _sq(r.get("summary") or r.get("text") or "")[:110]


def _collect(axis):
    """축 하나의 재료 — 옛 양식(build_outline)과 새 양식(material)이 같은 것을 본다."""
    axes = journal_writing.axes(P)
    if axis not in axes:
        raise SystemExit("모르는 축: %s — trend-interests.md 관심 축 표의 코드만 (%s)" % (axis, ", ".join(axes)))
    name = re.split(r"\s+—\s+|\s*\(", axes[axis]["name"], maxsplit=1)[0].strip()   # 괄호·대시 뒤 설명은 표의 것
    view = _sq(re.sub(r"\*\*", "", axes[axis].get("view") or ""))
    view = re.split(r"(?<=[.다])\s+", view, maxsplit=1)[0][:160]                    # 첫 문장만
    rows = axis_rows(_rows(), axis)
    live = [r for r in rows if r.get("status") in ("pending", "kept")]
    dropped = [r for r in rows if r.get("status") == "dropped"]
    nodes = canon_nodes()

    buckets = {"근거": {}, "반증": {}, "인접": {}}
    unlinked = []
    for r in live:
        fits = r.get("canon_fit") or []
        if not fits:
            unlinked.append(r)
            continue
        for f in fits:
            rel = f.get("rel") if f.get("rel") in buckets else "인접"
            buckets[rel].setdefault(f.get("code"), []).append((r, f.get("why") or ""))
    codes = []
    for rel in ("근거", "반증", "인접"):
        for c in buckets[rel]:
            if c and c not in codes:
                codes.append(c)

    def node(c):
        n = dict(nodes.get(c) or {})
        b = canon_block(c)
        if not n:
            n = b or {"title": "", "scene": "", "summary": "", "answers": [], "path": ""}
        elif b:
            if len(b.get("summary") or "") > len(n.get("summary") or ""):
                n["summary"] = b["summary"]
            n["answers"] = b.get("answers") or []
        return n

    ranked = sorted(unlinked, key=lambda r: (r.get("status") != "kept", r.get("grade") != "signal",
                                             r.get("source") == "reddit", -(len(r.get("long") or {}))))
    return {"axis": axis, "name": name, "view": view, "rows": rows, "live": live, "dropped": dropped,
            "buckets": buckets, "codes": codes, "node": node, "unlinked": unlinked, "ranked": ranked,
            "mine": insights_mine(axis), "oq": open_questions(codes)}


def material(axis):
    """프롬프트·검증이 함께 쓰는 재료 한 묶음 (JSON 으로 그대로 스냅숏 된다)."""
    c = _collect(axis)
    fit_of = {}
    for rel in ("근거", "반증", "인접"):
        for code, items in c["buckets"][rel].items():
            for r, why in items:
                fit_of.setdefault(r["id"], []).append({"code": code, "rel": rel, "why": _sq(why)[:140],
                                                       "title": (c["node"](code) or {}).get("title", "")})
    canon = []
    for code in c["codes"]:
        n = c["node"](code)
        canon.append({"code": code, "title": n.get("title", ""), "scene": n.get("scene", ""),
                      "rule": n.get("summary", ""), "answers": n.get("answers") or []})
    flesh_rows = [r for r in c["live"] if r.get("status") == "kept" or r.get("canon_fit")]
    flesh_rows = sorted(flesh_rows, key=lambda r: (r.get("status") != "kept", not r.get("canon_fit"),
                                                   r.get("grade") != "signal"))[:MAX_FLESH]
    flesh = []
    for r in flesh_rows:
        L = r.get("long") or {}
        flesh.append({"id": r["id"], "who": _who(r), "kept": r.get("status") == "kept", "one": _one_line(r),
                      "points": [_sq(x)[:160] for x in (L.get("points") or [])][:5],
                      "numbers": [_sq(x) for x in (L.get("numbers") or [])][:6],
                      "fit": fit_of.get(r["id"]) or []})
    waiting = [{"who": _who(r), "one": _one_line(r)} for r in c["ranked"][:MAX_UNLINKED]]
    craft = ""
    if any(x["code"].startswith("C-") for x in canon):    # SF 프로세스 craft — C 계열 판단이 닿은 축에서만
        try:
            craft = CRAFT.read_text(encoding="utf-8")
        except OSError:
            craft = ""
    return {"axis": c["axis"], "name": c["name"], "view": c["view"],
            "counts": {"rows": len(c["rows"]), "live": len(c["live"]), "dropped": len(c["dropped"]),
                       "codes": len(c["codes"]), "insights": len(c["mine"]), "oq": len(c["oq"])},
            "canon": canon, "flesh": flesh, "waiting": waiting,
            "unlinked_n": len(c["unlinked"]),
            "insights": [{"who": m["who"][:40], "text": m["text"]} for m in c["mine"]],
            "threads": thread_turns(c["axis"]), "open_questions": c["oq"], "craft": craft}


def mine_pool(m):
    """뼈로 쓸 수 있는 **내 문장** 전부 — used_mine 은 여기 안에 글자 그대로 있어야 한다."""
    out = []
    for x in m["canon"]:
        for t in [x.get("rule"), x.get("scene")] + list(x.get("answers") or []):
            if t and _sq(t):
                out.append(_sq(t))
    for x in m["insights"]:
        out += _blocks(x["text"])
        out += [_sq(s) for s in _sents(x["text"]) if _sq(s)]
    for t in m["threads"]:
        out += t["turns"]
    for q in m["open_questions"]:
        out.append(_sq(q["q"]))
    if m.get("craft"):
        for b in _blocks(m["craft"]):
            if len(b) >= 10:
                out.append(b)
                out += [_sq(x) for x in _sents(b) if len(_sq(x)) >= 10]
    seen, uniq = set(), []
    for s in out:
        k = _norm(s)
        if k and k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def all_text(m):
    """재료 전문 — 숫자 대조용 (본문의 숫자는 여기 있어야 한다)."""
    parts = [m.get("view") or "", m.get("craft") or ""]
    for x in m["canon"]:
        parts += [x.get("title") or "", x.get("rule") or "", x.get("scene") or ""] + list(x.get("answers") or [])
    for r in m["flesh"]:
        parts += [r["one"]] + r["points"] + r["numbers"] + [f.get("why", "") for f in r["fit"]]
    for r in m["waiting"]:
        parts.append(r["one"])
    for x in m["insights"]:
        parts.append(x["text"])
    for t in m["threads"]:
        parts += [t["title"]] + t["turns"]
    for q in m["open_questions"]:
        parts.append(q["q"])
    return "\n".join(parts)


# ── 계약 검증 ────────────────────────────────────────────────────────────────
LABEL = re.compile(r"^(?:기준|장면|답|요점|숫자|요약|규칙)\s*[:：]\s*")


def _strip_label(s):
    """재료 블록의 이름표("기준: ", "장면: ")를 떼고 대조한다 — 이름표는 내 문장이 아니라 이 코드의 서식이다."""
    return LABEL.sub("", _sq(s))


def _tokens(s):
    return [w for w in re.findall(r"[가-힣A-Za-z0-9]+", s or "") if len(w) >= 2]


def _used_in_prose(sent, prose_norm):
    """뼈가 본문에 실제로 쓰였나 — 경어체로 다듬으면 글자 그대로는 아니게 되므로 **어휘 겹침**으로 본다.
    (내용어 절반 이상, 최소 둘. 어미 한두 글자 차이는 잘라 맞춘다)"""
    toks = _tokens(sent)
    if not toks:
        return False
    hit = 0
    for w in toks:
        if w in prose_norm or (len(w) >= 3 and w[:-1] in prose_norm) or (len(w) >= 4 and w[:-2] in prose_norm):
            hit += 1
    need = max(2, -(-len(toks) // 2))
    return hit >= need


def _nums(text):
    """숫자 표현 — 연도·날짜는 뺀다(조립 날짜·게시 연도는 재료에 없어도 정상)."""
    t = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text or "")
    t = re.sub(r"(?:19|20)\d{2}\s*년?", " ", t)
    return {x.replace(",", "") for x in re.findall(r"\d[\d,]*", t) if x.strip("," )}


CONTRACT = ('{"prose": "## 소제목\\n서술문…", "used_mine": ["재료의 내 문장에서 글자 그대로 옮긴 문장", …], '
            '"numbers": ["본문에 쓴 숫자 표현", …], "question": "마지막 절을 끝내는 질문?", '
            '"cases": [{"head": "prose 의 소제목 그대로", "scene": "…", "pivot": "…", "result": "… 또는 빈 문자열", '
            '"principle": "…", "used_mine": ["내 문장 그대로", …]}, …]}')


def valid_prose(o, m):
    """계약 — 어긋나면 페이지를 덮어쓰지 않는다. 반환: 사유 문자열(불합격) 또는 None(통과)."""
    if not isinstance(o, dict):
        return "객체가 아니다"
    prose = o.get("prose")
    if not isinstance(prose, str) or not prose.strip():
        return "prose 가 없다"
    prose = prose.strip()
    if not MIN_CHARS <= len(prose) <= MAX_CHARS:
        return "본문 %d자 — %d~%d자여야 한다" % (len(prose), MIN_CHARS, MAX_CHARS)
    if prose.startswith("# "):
        return "본문에 큰 제목이 있다 (머리는 코드가 붙인다)"
    heads = [h.strip() for h in re.findall(r"^## +(.+)$", prose, re.M)]
    if not 2 <= len(heads) <= 6:
        return "소제목 %d개 — 2~6개여야 한다" % len(heads)
    if re.search(r"^[ \t]*(?:[-*+•][ \t]|\d+[.)][ \t])", prose, re.M):
        return "불릿·번호 목록이 있다 (서술문만)"
    if "|" in prose:
        return "표가 있다 (서술문만)"
    if re.search(r"\b(?:C|WD|AC|D|E)-\d", prose):
        return "본문에 내부 코드 표기가 있다"
    if heads[-1] != "아직 답하지 못한 것":
        return "마지막 절이 '아직 답하지 못한 것' 이 아니다: " + heads[-1][:30]
    used = o.get("used_mine")
    if isinstance(used, list) and not used:
        return "used_mine 이 비었다 — 뼈로 쓸 내 문장을 하나도 안 골랐다"
    if not isinstance(used, list) or not all(isinstance(x, str) for x in used):
        return "used_mine 이 문장 목록이 아니다"
    pool = SEP.join(_norm(s) for s in mine_pool(m))
    pnorm = _norm(prose)
    for s in used:
        n = _norm(_strip_label(s))
        if len(n) < 10:
            return "used_mine 문장이 너무 짧다: " + _sq(s)[:30]
        if n not in pool and n.rstrip(".!?…,·") not in pool:
            return "used_mine 이 내 문장 재료에 없다: " + _sq(s)[:44]
        if not _used_in_prose(s, pnorm):
            return "used_mine 이 본문에 안 쓰였다: " + _sq(s)[:44]
    bad = sorted(_nums(prose) - _nums(all_text(m)))
    if bad:
        return "재료에 없는 숫자: " + ", ".join(bad[:6])
    q = o.get("question")
    if not isinstance(q, str) or not q.strip().endswith("?"):
        return "question 이 물음표로 끝나지 않는다"
    last = prose[prose.rfind("## "):]
    if _norm(q) not in _norm(last):
        return "question 이 마지막 절 안에 없다"
    return None


def valid_cases(o, m):
    """사례 절 계약 — prose 가 통과한 뒤에만 본다. 불합격이어도 prose 는 쓴다(사례 절 자리에 사유 한 줄).
    result 는 비어도 된다 — 파트너사 자리에서 정착 결과는 안 보이니, 지어내게 하지 않는다."""
    heads = [h.strip() for h in re.findall(r"^## +(.+)$", o["prose"], re.M)][:-1]
    cases = o.get("cases")
    if not isinstance(cases, list) or not cases:
        return "cases 가 없다"
    pool = SEP.join(_norm(s) for s in mine_pool(m))
    nums_ok = _nums(all_text(m))
    seen = set()
    for c in cases:
        if not isinstance(c, dict):
            return "cases 항목이 객체가 아니다"
        h = _case_head(c)
        if h not in heads:
            return "사례 head 가 본문 소제목이 아니다: " + h[:30]
        if h in seen:
            return "같은 소제목에 사례가 둘이다: " + h[:30]
        seen.add(h)
        cells = []
        for k, _lab in CASE_LABELS:
            v = c.get(k)
            if not isinstance(v, str) or (k != "result" and not v.strip()):
                return "사례 '%s' 의 %s 칸이 비었다" % (h[:20], k)
            v = v.strip()
            if len(v) > CASE_MAX:
                return "사례 '%s' 의 %s 칸 %d자 — %d자 이하" % (h[:20], k, len(v), CASE_MAX)
            if "\n" in v or "|" in v or re.search(r"^[ \t]*(?:[-*+•][ \t]|\d+[.)][ \t])", v):
                return "사례 '%s' 의 %s 칸이 한 단락 서술문이 아니다" % (h[:20], k)
            cells.append(v)
        text = " ".join(cells)
        if re.search(r"\b(?:C|WD|AC|D|E)-\d", text):
            return "사례 '%s' 에 내부 코드 표기가 있다" % h[:20]
        bad = sorted(_nums(text) - nums_ok)
        if bad:
            return "사례 '%s' 에 재료에 없는 숫자: %s" % (h[:20], ", ".join(bad[:6]))
        used = c.get("used_mine")
        if not isinstance(used, list) or not used or not all(isinstance(x, str) for x in used):
            return "사례 '%s' 의 used_mine 이 비었다" % h[:20]
        tnorm = _norm(text)
        for u in used:
            n = _norm(_strip_label(u))
            if len(n) < 10 or (n not in pool and n.rstrip(".!?…,·") not in pool):
                return "사례 '%s' 의 used_mine 이 내 문장 재료에 없다: %s" % (h[:20], _sq(u)[:40])
            if not _used_in_prose(u, tnorm):
                return "사례 '%s' 의 used_mine 이 칸에 안 쓰였다: %s" % (h[:20], _sq(u)[:40])
    return None


def _case_head(c):
    """사례의 소제목 — 모델이 `## ` 까지 붙여 오는 회차가 있다 (2026-09-23 A 축 실측)."""
    return re.sub(r"^#+\s*", "", str(c.get("head") or "").strip())


def render_cases(o, err=None):
    """사례 절 — 칸 배치는 코드가 그린다(모델은 칸 내용만). 본문 소제목 순서를 따른다."""
    L = ["---", "", CASES_HEAD, ""]
    if err:
        L.append("> 이번 판은 사례 절을 못 썼다 — %s. [다시 쓰기]로 재시도." % err)
        return "\n".join(L) + "\n"
    order = [h.strip() for h in re.findall(r"^## +(.+)$", o["prose"], re.M)]
    for c in sorted(o["cases"], key=lambda c: order.index(_case_head(c))):
        L += ["### " + _case_head(c), ""]
        for k, lab in CASE_LABELS:
            L += ["**%s** %s" % (lab, (c.get(k) or "").strip() or NO_RESULT), ""]
    return "\n".join(L)


def _prose_part(text):
    """판에서 서술 절만 — 사례 절은 뺀다 (바뀐 점 셈이 사례 절 때문에 튀지 않게)."""
    i = text.find("\n---\n\n" + CASES_HEAD)
    return text if i < 0 else text[:i]


# ── 프롬프트 ─────────────────────────────────────────────────────────────────
PROSE_PROMPT = """관심 축 하나에 대해 **사용자 자신을 소개하는 한 페이지**를 쓴다. 사용자가 1인칭으로 말하는 글이다.
사용자의 판단을 새로 만드는 일이 아니다 — 아래 「내 문장」을 뼈로 두고, 「모은 글」을 살로 붙여 **하나의 사람 소개로 잇는다**.

**양식** (사용자 확정 2026-09-10: "양식이 있는게 아니라 … 하나의 '나'를 소개하는 문장으로. 나눌 수 있는건 소제목정도야")
- 소제목(`## `) 2~6개로만 나뉘는 **이어지는 서술문**. 불릿·번호 목록·표·굵은 글씨 금지.
  (2026-09-23: 같은 내용을 소제목별 네 칸으로 다시 보는 사례 절은 아래 cases 로 따로 낸다 — prose 안에 칸을 쓰지 마라.)
- 1인칭 담백한 경어체(~습니다 / ~죠 / ~더군요 / ~니까요). 과장·감탄·수사 부사("한 발 더 들어가", "아무도") 금지.
- 경험·장면에서 시작한다. 개념 정의로 시작하지 않는다.
- hedge("~수도 있겠다고 생각했습니다", "~인 것 같습니다")는 사용자 목소리다 — 단정으로 바꾸지 마라.
- 이중 피동("작성되어 공유되고 있습니다")·"결론적으로/요약하자면" 류 금지.
- 전체 {mn}~{mx}자 (A4 한 장). **{mx}자를 넘으면 코드가 버린다** — 절을 줄이지 말고 각 절을 짧게 써서 맞춘다.
- 마지막 절 제목은 **정확히** `## 아직 답하지 못한 것` — 가장 큰 구멍 **하나**만 쓰고 질문 한 문장으로 끝낸다.
- 내부 코드 표기(C-01·WD-02 같은 것) 금지. 고객사명은 내부용이므로 그대로 써도 된다.

**뼈와 살**
- 뼈 = 「내 문장」. 뜻과 어휘를 유지한 채 경어체로 잇는다. 재료에 없는 판단·주장을 보태지 마라.
- 살 = 「모은 글」이 말하는 방식과 그 판단이 태어난 장면. 남의 방식은 "~라는 글들이 있습니다" 처럼 남의 것으로 두고,
  내 판단과 다르면 어디가 다른지 내 문장 쪽에서 말한다. 보관한 글과 내 판단에 닿은 글이 살이고,
  「아직 안 닿은 글」은 마지막 절에서 제목만 스쳐도 된다.
- 숫자는 재료에 있는 것만. 재료에 없는 숫자가 하나라도 있으면 이 결과는 버려진다(코드가 대조한다).

**출력** — JSON 객체 하나뿐 (설명·코드펜스 금지)
{contract}
- prose: 본문만. `# ` 제목·머리글·조립 날짜는 코드가 붙인다.
- used_mine: 「내 문장」에서 **글자 그대로 복사한** 문장 2~8개 — 본문의 뼈가 그 문장이라는 증거다(코드가 부분일치로 대조한다).
  재료의 이름표("기준: ", "장면: ", "답: ")는 떼고 문장만 옮긴다.
  본문에서는 경어체로 다듬어도 되지만 핵심 어휘는 그대로 남긴다.
- numbers: 본문에 쓴 숫자 표현 전부. question: 마지막 절을 끝내는 질문 한 문장(본문 안에도 그대로 있어야 한다).
- cases: **같은 내용을 사례로 다시 정리한 칸** (2026-09-23 사용자 "하나의 파일에서 섹션으로"). prose 의 소제목마다
  (마지막 `아직 답하지 못한 것` 제외) 그 절의 바탕이 된 장면 하나를 네 칸으로 다시 쓴다. 장면이 재료에 없는 절은 건너뛴다.
  - head: 그 절의 소제목을 **글자 그대로**.
  - scene: 그때 상황 — 어느 고객·어느 자리에서 무엇이 어떻게 돌고 있었나(as-is). 재료에 있는 규모 숫자(건수·사람 수·단계 수)가 있으면 여기에.
  - pivot: 바꾼 것과 포기한 것 — 요청·가설이 어떻게 바뀌었고, 무엇을 골랐고 무엇을 안 만들었나. 근거가 된 발화가 있으면 그대로.
  - result: 관측한 결과 — 고객 말이 바뀐 지점, 실측으로 뒤집은 것, 재료에 있는 숫자만. **관측한 게 없으면 빈 문자열** — 지어내지 마라.
  - principle: 남은 기준 — 이 장면에서 남은 내 판단 기준 한두 문장(prose 의 뼈와 같은 어휘).
  - 칸마다 {cmax}자 이하, 줄바꿈·불릿 없는 한 단락 경어체. 숫자는 재료에 있는 것만(코드가 대조한다).
  - used_mine: 이 사례 칸들에 쓴 「내 문장」 1~4개, 글자 그대로.

# 축 {axis}. {name}
이 축이 묻는 것: {view}
재료 건수: 글 {n_rows} (살아 있는 {n_live}, 이미 아는 것으로 버림 {n_drop}) · 닿은 내 판단 {n_codes} · insights 내 생각 {n_ins} · 열린 질문 {n_oq}

## 내 문장 — 뼈 (정본·대화에서 그대로)
{mine}

## 모은 글 — 살 (남의 방식. 보관·내 판단에 닿은 것)
{flesh}

## 아직 안 닿은 글 (제목만)
{waiting}

## 아직 답 없는 것 (큐의 열린 질문)
{oq}
"""


def _mine_block(m):
    L = []
    for x in m["canon"]:
        L.append("### 내 판단 — %s" % (x["title"] or "제목 없음"))
        if x.get("rule"):
            L.append("- 기준: %s" % x["rule"])
        if x.get("scene"):
            L.append("- 장면: %s" % x["scene"])
        for a in x.get("answers") or []:
            L.append("- 답: %s" % a)
        if not (x.get("rule") or x.get("scene") or x.get("answers")):
            L.append("- (내 문장 아직 없음)")
    for x in m["insights"]:
        L.append("### insights 내 생각 (%s 글에)" % x["who"])
        L.append(x["text"])
    for t in m["threads"]:
        L.append("### 대화에서 내가 한 말 (글: %s)" % t["title"])
        for s in t["turns"]:
            L.append("- %s" % s)
    if m.get("craft"):
        L.append("### SF 프로세스 craft (career/sf-process-craft.md — 이 축에 해당하는 부분만 쓴다)")
        L.append(m["craft"][:9000])
    return "\n".join(L) or "(아직 없음)"


def _flesh_block(m):
    L = []
    for r in m["flesh"]:
        head = "### %s%s" % (r["who"], " · 보관" if r["kept"] else "")
        L.append(head)
        L.append("- 요약: %s" % r["one"])
        for f in r["fit"]:
            L.append("- 내 판단과: %s (%s) — %s" % (f.get("title", "")[:40], f.get("rel", ""), f.get("why", "")))
        for p in r["points"]:
            L.append("- 요점: %s" % p)
        for n in r["numbers"]:
            L.append("- 숫자: %s" % n)
    return "\n".join(L) or "(아직 없음)"


def prose_prompt(m):
    c = m["counts"]
    return PROSE_PROMPT.format(
        mn=MIN_CHARS, mx=MAX_CHARS, cmax=CASE_MAX, contract=CONTRACT, axis=m["axis"], name=m["name"],
        view=m["view"] or "(축 표에 설명 없음)", n_rows=c["rows"], n_live=c["live"], n_drop=c["dropped"],
        n_codes=c["codes"], n_ins=c["insights"], n_oq=c["oq"],
        mine=_mine_block(m), flesh=_flesh_block(m),
        waiting="\n".join("- %s: %s" % (w["who"], w["one"]) for w in m["waiting"]) or "(없음)",
        oq="\n".join("- %s" % q["q"] for q in m["open_questions"]) or "(없음)")


# ── 재료 지문 · 파일 상태 (모델 없음) ────────────────────────────────────────
def page_path(axis):
    return OUT_DIR / ("%s.md" % axis)


def mat_path(axis):
    return OUT_DIR / ("%s.material.json" % axis)


def fingerprint(m):
    """재료 지문 — 재료가 바뀌었을 때만 모델을 부르기 위한 것. 키를 정렬해 직렬화하므로
    dict 순서·조립 시각·하트비트 같은 잡음은 안 들어간다(재료 자체가 아닌 NOISE 키만 뺀다)."""
    src = {k: v for k, v in m.items() if k not in NOISE}
    src["_page_format"] = PAGE_FORMAT
    return hashlib.sha1(json.dumps(src, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def material_digest(m, fp=None):
    """파일로 남기는 것은 **지문과 셈만**. 본문·인용 전문은 원천(feed-queue·career/*.md·assignments)에
    이미 있고, 스냅숏이 축당 20~28KB 로 페이지의 5배였다 (2026-09-10 사용자 "prev도 있고 중복 데이터 아니야?").
    전체 재료가 필요하면 `--material` 로 stdout 에 뽑는다 — 파일로 안 남긴다.
    지문은 여기 요약이 아니라 **재료 전체**로 계산한다 — 그래야 요약에 안 실린 문장이 바뀌어도 잡는다."""
    mine = mine_pool(m)
    return {
        "axis": m["axis"], "name": m["name"], "fingerprint": fp,
        "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "counts": m["counts"],
        "canon": [x["code"] for x in m["canon"]],
        "flesh": [r["id"] for r in m["flesh"]],
        "waiting": len(m["waiting"]), "unlinked_n": m["unlinked_n"],
        "threads": [len(t["turns"]) for t in m["threads"]],
        "open_questions": len(m["open_questions"]),
        "mine_sentences": len(mine), "mine_chars": sum(len(x) for x in mine),
        "insights_chars": sum(len(x["text"]) for x in m["insights"]),
        "craft_chars": len(m.get("craft") or ""),
        "material_chars": len(json.dumps(m, ensure_ascii=False)),
    }


def _save_material(axis, m, fp=None):
    """재료 스냅숏(요약) — `fingerprint` 는 **페이지를 쓴 뒤에만** 채운다(실패한 회차는 다음에 다시 쓴다).
    `at` 은 '언제 재료를 다시 봤나' — 지문이 같아 건너뛴 회차에도 갱신된다."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mat_path(axis).write_text(json.dumps(material_digest(m, fp), ensure_ascii=False, indent=1),
                              encoding="utf-8")
    return fp


def stored_fingerprint(axis):
    try:
        d = json.loads(mat_path(axis).read_text(encoding="utf-8"))
        return d.get("fingerprint") if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _max_source_mtime():
    t = 0.0
    for path in SOURCES:
        try:
            t = max(t, path.stat().st_mtime)
        except OSError:
            pass
    return t


_FP_CACHE = {"src": None, "fps": {}}


def current_fingerprints(src_mtime=None):
    """지금 재료의 지문 — 원천 파일 시각이 그대로면 다시 계산하지 않는다.

    보드가 5초마다 부르므로 캐시가 없으면 낭비다. 실측(2026-09-10): 열 축 전부 0.058초 —
    원천이 바뀐 회차에만 그 값을 내고 나머지 회차는 0. 시각만 보는 어림(원천이 바뀌면 모든 축이
    낡아 보인다)을 쓰지 않는 이유는 그 어림이 **다른 축 때문에 붙은 점**을 사람에게 보여주기 때문이다."""
    src = _max_source_mtime() if src_mtime is None else src_mtime
    if _FP_CACHE["src"] != src:
        try:
            fps = {a: fingerprint(material(a)) for a in journal_writing.axes(P)}
        except Exception:                     # noqa: BLE001 — 못 재면 마지막 값으로 그린다
            return _FP_CACHE["fps"]
        _FP_CACHE.update(src=src, fps=fps)
    return _FP_CACHE["fps"]


def page_state(axis, src_mtime=None, fps=None):
    """화면용 한 축의 상태 — {at, chars, missing, stale}.
    stale = 지금 재료의 지문이 마지막으로 쓴 판의 지문과 다르다 (워커의 판정과 같은 기준).
    지문을 못 구하면 파일 시각으로 떨어진다(그때만 어림)."""
    pg = page_path(axis)
    if not pg.exists():
        return {"missing": True, "stale": False, "at": None, "chars": 0}
    try:
        chars = len(pg.read_text(encoding="utf-8"))
    except OSError:
        chars = 0
    fps = current_fingerprints(src_mtime) if fps is None else fps
    if axis in fps:
        stale = fps[axis] != stored_fingerprint(axis)
    else:
        src = _max_source_mtime() if src_mtime is None else src_mtime
        try:
            checked = mat_path(axis).stat().st_mtime
        except OSError:
            checked = 0.0
        stale = src > checked
    return {"missing": False, "stale": stale, "chars": chars,
            "at": datetime.fromtimestamp(pg.stat().st_mtime, KST).strftime("%m/%d %H:%M")}


def all_page_states():
    """축 표의 모든 축 (보드 /api/state 가 5초마다 부른다)."""
    src = _max_source_mtime()
    fps = current_fingerprints(src)
    return {a: page_state(a, src, fps) for a in journal_writing.axes(P)}


# ── 페이지 쓰기 ──────────────────────────────────────────────────────────────
def _head(m, mine_n):
    return ("# %s. %s — 나에 대해 한 장\n\n> %s 조립 · 초안 · 내부용(고객사명 그대로) · 쓴 내 문장 %d · 글 %d\n"
            % (m["axis"], m["name"], datetime.now(KST).strftime("%Y-%m-%d %H:%M"), mine_n, m["counts"]["rows"]))


def _diff_line(prev, new, used):
    """이전 판과 바뀐 점 — 코드가 센다(절 수·글자 수·이전 판에 없던 내 문장)."""
    if not prev:
        return "> 첫 판이다 — 다음 조립에서 바뀐 점을 여기 적는다."
    prev = _prose_part(prev)
    pn = _norm(prev)
    fresh = sum(1 for s in used if _norm(s) not in pn and not _used_in_prose(s, pn))
    return ("> 이전 판과 바뀐 점: 절 %d→%d · %d→%d자 · 새로 들어간 내 문장 %d"
            % (len(re.findall(r"^## ", prev, re.M)), len(re.findall(r"^## ", new, re.M)),
               len(prev), len(new), fresh))


def _write_page(axis, text):
    """이전 판을 `<축>.prev.md` 로 보존하고 덮어쓴다."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / ("%s.md" % axis)
    prev = ""
    if out.exists():
        prev = out.read_text(encoding="utf-8")
        (OUT_DIR / ("%s.prev.md" % axis)).write_text(prev, encoding="utf-8")
    out.write_text(text, encoding="utf-8")
    return out, prev


def _open(path):
    """산출물 열기 — 규칙은 한 자리(checkpoint_board.open_local: 볼트 안 .md 는 Obsidian)."""
    try:
        import checkpoint_board
        checkpoint_board.open_local(path)
    except Exception:
        subprocess.Popen(["open", str(path)])


def _stub_no_mine(m):
    return ("## 아직 제 문장이 없다\n\n이 축으로 남의 글 %d건이 모였지만, 이 축을 겨눈 제 판단이나 생각은 아직 한 줄도 없습니다. "
            "남의 글만으로 저를 소개하면 제가 하지 않은 판단을 제 말로 적게 되니 비워 둡니다. "
            "읽기 판에서 이 축 글에 제 문장이 하나 남으면 여기에 씁니다.\n\n"
            "## 아직 답하지 못한 것\n\n이 축을 저는 왜 열어 두었는지부터가 아직 말이 안 됩니다. "
            "이 축에서 제가 알고 싶은 것은 무엇입니까?\n" % len(m["flesh"]))


def _stub(m):
    return ("## 아직 재료가 없다\n\n이 축으로 모인 글이 없고, 이 축을 겨눈 제 판단도 아직 없습니다. "
            "이 축 글이 들어오고 읽기 판에서 이어서 묻기로 제 문장이 하나 남으면 여기에 씁니다.\n\n"
            "## 아직 답하지 못한 것\n\n이 축을 저는 왜 열어 두었는지부터가 아직 말이 안 됩니다. "
            "이 축에서 제가 알고 싶은 것은 무엇입니까?\n")


def run_prose(axis, do_open=False, model=MODEL, only_if_stale=False):
    """문장 잇기 — 재료는 코드가 모으고 문장은 워커가 잇는다. 계약 불합격이면 페이지를 안 덮어쓴다.

    only_if_stale=True 면 **재료 지문이 그대로면 모델을 안 부르고** 3 을 돌려준다 (--auto·뒤에서 갱신).
    반환: 0 새로 썼다 · 1 계약 불합격 · 3 재료 안 바뀜(건너뜀).
    끝나고 여는 것은 do_open 뿐이다 — 뒤에서 쓴 판은 사용자가 보던 창을 뺏지 않는다."""
    model = models.resolve(model, "axis.page")
    m = material(axis)
    fp = fingerprint(m)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    err_f = OUT_DIR / ("%s.error.txt" % axis)
    out_p = page_path(axis)
    if only_if_stale and out_p.exists() and stored_fingerprint(axis) == fp:
        _save_material(axis, m, fp)      # 재료를 다시 봤다는 표식 — 화면의 '새 재료 있음' 이 내려간다
        print("%s 건너뜀 — 재료 안 바뀜 (%s 조립 그대로)" % (axis, page_state(axis)["at"]))
        return 3
    _save_material(axis, m)              # 지문은 페이지를 쓴 뒤에 채운다
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    with worker_status.Beat("axis-%s" % axis, "axis", "축 한 장 · %s" % m["name"][:20],
                            model=model, quiet=not do_open) as beat:
        if not (m["canon"] or m["flesh"] or m["insights"] or m["threads"]):
            out, prev = _write_page(axis, _head(m, 0) + "\n" + _stub(m))
            err_f.unlink(missing_ok=True)
            _save_material(axis, m, fp)
            beat.finish(True, "재료가 아직 없다 — 빈 판을 썼다")
            if do_open:
                _open(out)
            print("%s · 재료 없음" % out.relative_to(P))
            return 0
        if not mine_pool(m):
            # 남의 글만 모이고 내 문장이 0 이면 계약(used_mine)을 절대 못 채운다 — 모델을 안 부른다 (2026-09-23 D 축 exit 5)
            out, prev = _write_page(axis, _head(m, 0) + "\n" + _stub_no_mine(m))
            err_f.unlink(missing_ok=True)
            (OUT_DIR / ("%s.bad.json" % axis)).unlink(missing_ok=True)
            _save_material(axis, m, fp)
            beat.finish(True, "남의 글만 있고 내 문장이 없다 — 빈 판을 썼다")
            if do_open:
                _open(out)
            print("%s · 내 문장 없음 (남의 글 %d)" % (out.relative_to(P), len(m["flesh"])))
            return 0
        beat.phase("문장 잇는 중 (뼈는 내 문장)")
        raw = worker_claude.run(prose_prompt(m), beat, [], model=model, timeout=480, max_turns=2)
        o = worker_claude.extract_json(raw)
        # 답이 아예 없을 때의 문구는 워커가 안다 (원인 열거는 worker_claude.failure_text 한 곳)
        def check(x):
            e = valid_prose(x, m)
            return (e, "prose") if e else (valid_cases(x, m), "cases")
        if o:
            err, kind = check(o)
        else:
            err, kind = worker_claude.failure_text("답에 JSON 이 없다 — [다시]로 재시도"), "prose"
        if err and o is not None:
            fixed = worker_claude.extract_json(
                worker_claude.repair_json(worker_claude.LAST_SID, model, beat, CONTRACT + "\n어긋난 점: " + err))
            if fixed is not None:
                e2, k2 = check(fixed)
                if e2 is None or kind == "prose":
                    o, err, kind = fixed, e2, k2
                # 원래 판이 사례만 틀렸고 고친 판이 서술까지 틀렸으면 원래 판을 둔다
        cases_err = err if err and kind == "cases" else None
        if cases_err:
            err = None
        if err:
            err_f.write_text("%s · %s\n%s\n" % (stamp, axis, err), encoding="utf-8")
            (OUT_DIR / ("%s.bad.json" % axis)).write_text(
                json.dumps(o if o is not None else {"raw": (raw or "")[:4000]}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            beat.finish(False, "안 썼다 — " + err[:70])
            print("불합격: %s" % err)
            return 1
        used = [s.strip() for s in o["used_mine"]]
        body = o["prose"].strip()
        text = _head(m, len(used)) + "\n" + body + "\n"
        out = OUT_DIR / ("%s.md" % axis)
        prev = out.read_text(encoding="utf-8") if out.exists() else ""
        text = text + "\n" + _diff_line(prev, text, used) + "\n\n" + render_cases(o, cases_err)
        out, prev = _write_page(axis, text)
        if cases_err:   # 서술은 썼고 사례만 못 썼다 — 사유는 판 안 한 줄 + 오류 파일 (화면 빨간 점)
            err_f.write_text("%s · %s\n사례 절만 못 씀: %s\n" % (stamp, axis, cases_err), encoding="utf-8")
            (OUT_DIR / ("%s.bad.json" % axis)).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            err_f.unlink(missing_ok=True)
            (OUT_DIR / ("%s.bad.json" % axis)).unlink(missing_ok=True)
        _save_material(axis, m, fp)
        n_cases = 0 if cases_err else len(o["cases"])
        beat.finish(True, "%d자 · 내 문장 %d · 사례 %d%s" % (len(body), len(used), n_cases,
                                                        " (사례 절 못 씀)" if cases_err else ""))
    print("%s · %d자 · 내 문장 %d · 사례 %d%s" % (out.relative_to(P), len(body), len(used), n_cases,
                                             " — 사례 절 못 씀: " + cases_err if cases_err else ""))
    return 0


def run_auto(model=MODEL, force=False, codes=None):
    """모든 축을 돌며 **필요할 때만** 쓴다 — 페이지가 없거나 재료 지문이 다를 때(또는 --force).
    아무것도 안 연다. 종료 코드: 0 하나라도 썼다 · 3 전부 최신 · 5 실패 있음."""
    codes = codes or list(journal_writing.axes(P))
    wrote, skipped, failed = [], [], []
    for a in codes:
        try:
            rc = run_prose(a, do_open=False, model=model, only_if_stale=not force)
        except SystemExit as e:                     # 모르는 축 (_collect 가 던진다)
            failed.append("%s(%s)" % (a, str(e)[:40]))
            continue
        except Exception as e:                      # noqa: BLE001 — 한 축이 죽어도 나머지는 돈다
            failed.append("%s(%s)" % (a, repr(e)[:60]))
            continue
        (wrote if rc == 0 else skipped if rc == 3 else failed).append(a)
    print("자동 조립: 새로 쓴 축 %d [%s] · 건너뜀 %d [%s] · 실패 %d [%s]"
          % (len(wrote), " ".join(wrote), len(skipped), " ".join(skipped), len(failed), " ".join(failed)))
    if failed:
        return 5
    return 0 if wrote else 3


# ── 옛 양식 (재료 확인용) ─────────────────────────────────────────────────────
def build_outline(axis):
    c = _collect(axis)
    axis, name, view = c["axis"], c["name"], c["view"]
    rows, live, dropped = c["rows"], c["live"], c["dropped"]
    buckets, codes, node = c["buckets"], c["codes"], c["node"]
    unlinked, ranked, mine, oq = c["unlinked"], c["ranked"], c["mine"], c["oq"]
    today = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    L = []
    L.append("# %s. %s — 나에 대해 한 장 (재료 판)" % (axis, name))
    L.append("")
    L.append("> 조립 %s · 이 파일은 정본이 아니다 — 고칠 문장은 `career/*.md`·`insights.md` 에서 고치고 다시 조립한다." % today)
    if view:
        L.append("> 이 축이 묻는 것: %s" % view)
    L.append("> 재료: 글 %d (대기·보관 %d, 이미 아는 것으로 버림 %d) · 닿은 내 판단 %d · 내 생각 %d · 열린 질문 %d"
             % (len(rows), len(live), len(dropped), len(codes), len(mine), len(oq)))
    L.append("")

    L.append("## 한 줄")
    if mine:
        first = mine[0]["text"].splitlines()[0]
        L.append(first)
        L.append("<sub>— insights.md %s · %s</sub>" % (axis, mine[0]["who"][:40]))
    else:
        L.append("아직 없음 — 이 축 글 %d건을 모았지만 「내 생각」으로 남긴 문장이 0이다. 첫 문장은 아래 글 하나를 읽기 판에 놓고 [Claude] 이어서 묻기로." % len(rows))
    L.append("")

    L.append("## 내 장면 — 이 축이 태어난 자리")
    scenes, empty = [], []
    for c2 in codes:
        n = node(c2)
        if n.get("scene"):
            scenes.append((c2, n))
        else:
            empty.append((c2, n))
    for c2, n in scenes[:MAX_SCENES]:
        L.append("- **%s %s** — %s" % (c2, n.get("title", ""), n["scene"]))
    if not scenes:
        L.append("- 장면이 채워진 판단이 아직 없다.")
    if empty:
        L.append("- 장면 칸이 빈 판단: %s — 구조 탭 열린 질문에서 장면을 채우면 여기 들어온다."
                 % ", ".join("%s(%s)" % (c2, (n.get("title") or "")[:18]) for c2, n in empty[:6]))
    L.append("")

    L.append("## 남들이 말하는 방식 vs 나")
    any_bucket = False
    for rel in ("근거", "반증", "인접"):
        b = buckets[rel]
        if not b:
            continue
        any_bucket = True
        L.append("### %s" % REL_NAME[rel])
        for c2, items in b.items():
            n = node(c2)
            my = n.get("summary") or (n.get("answers") or [""])[0]
            L.append("- **%s %s**%s" % (c2, n.get("title", ""), (" — 내 기준: " + my) if my else " — 내 문장 아직 없음"))
            for r, why in items[:MAX_PER_BUCKET]:
                link = _link(r)
                L.append("  - %s: %s%s%s" % (_who(r), _one_line(r), (" — " + _sq(why)[:80]) if why else "",
                                            (" [↗](%s)" % link) if link else ""))
            if len(items) > MAX_PER_BUCKET:
                L.append("  - 같은 방식 %d건 더" % (len(items) - MAX_PER_BUCKET))
    if not any_bucket:
        L.append("아직 내 판단과 연결된 글이 없다.")
    if unlinked:
        L.append("### 내 판단과 아직 안 닿은 글 %d" % len(unlinked))
        for r in ranked[:MAX_UNLINKED]:
            link = _link(r)
            L.append("- %s: %s%s" % (_who(r), _one_line(r), (" [↗](%s)" % link) if link else ""))
        if len(unlinked) > MAX_UNLINKED:
            L.append("- 그 외 %d건" % (len(unlinked) - MAX_UNLINKED))
    L.append("")

    nums = []
    for r in live:
        if not (r.get("status") == "kept" or r.get("grade") == "signal"):
            continue
        for x in (r.get("long") or {}).get("numbers") or []:
            x = _sq(x)
            if x and len(x) >= 6 and re.search(r"[%÷×/=<>≥≤]|배|건|원|\$|만|억|MRR|ASP|중앙값|전환율", x) and x not in nums:
                nums.append(x)
    if nums:
        L.append("## 숫자·산식 (글에서 그대로)")
        for x in nums[:MAX_NUMBERS]:
            L.append("- " + x)
        L.append("")

    if mine:
        L.append("## 내 생각 (insights.md 에 남긴 것)")
        for mm in mine[:4]:
            L.append("- %s <sub>(%s %s)</sub>" % (mm["text"].splitlines()[0][:160], mm["who"][:30], mm["when"]))
        L.append("")

    tt = thread_turns(axis)
    if tt:
        L.append("## 대화에서 내가 한 말")
        for t in tt[:4]:
            L.append("- **%s**" % t["title"])
            for s in t["turns"][:3]:
                L.append("  - %s" % s)
        L.append("")

    L.append("## 아직 답 없는 것")
    if oq:
        for q in oq:
            L.append("- [%s] %s" % ("·".join(q["codes"]), q["q"][:200]))
    else:
        L.append("- 이 축의 판단을 겨눈 열린 질문이 큐에 없다.")
    L.append("")
    text = "\n".join(L)
    gaps = {"one_line": not mine, "scenes_empty": len(empty), "unlinked": len(unlinked), "open_questions": len(oq)}
    return text, gaps


def list_axes():
    rows = _rows()
    axes = journal_writing.axes(P)
    for code, a in axes.items():
        rs = axis_rows(rows, code)
        live = sum(1 for r in rs if r.get("status") in ("pending", "kept"))
        print("%-4s %-26s 글 %3d (살아 있는 %d) · 내 생각 %d" % (code, a["name"][:26], len(rs), live, len(insights_mine(code))))


def main():
    worker_status.rotate_err_log("axis_page")   # 커졌으면 시작 때 한 번 회전 (.1 하나)
    ap = argparse.ArgumentParser()
    ap.add_argument("axis", nargs="?")
    ap.add_argument("--prose", action="store_true", help="문장 잇기 (기본값)")
    ap.add_argument("--outline", action="store_true", help="옛 양식 — 재료가 뭐가 모였는지 볼 때")
    ap.add_argument("--material", action="store_true", help="재료 전문을 stdout 으로 (파일엔 셈만 남는다)")
    ap.add_argument("--open", action="store_true", help="다 되면 Obsidian(또는 기본 앱)으로 연다")
    ap.add_argument("--auto", action="store_true", help="모든 축 — 재료가 바뀐 축만 (안 연다). 하루 한 번 잡")
    ap.add_argument("--if-stale", dest="if_stale", action="store_true", help="재료가 바뀌었을 때만 쓴다")
    ap.add_argument("--force", action="store_true", help="재료가 같아도 다시 쓴다")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true", help="{path, chars, gaps} 만 — 옛 양식용")
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    if a.auto:
        return run_auto(model=a.model, force=a.force, codes=[a.axis] if a.axis else None)
    if a.list or not a.axis:
        list_axes()
        return 0
    if a.material:
        print(json.dumps(material(a.axis), ensure_ascii=False, indent=1))
        return 0
    if a.outline:
        text, gaps = build_outline(a.axis)
        if a.stdout:
            print(text)
            return 0
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / ("%s.outline.md" % a.axis)
        out.write_text(text, encoding="utf-8")
        if a.json:
            print(json.dumps({"path": str(out.relative_to(P)), "chars": len(text), "gaps": gaps}, ensure_ascii=False))
        else:
            print("%s · %d자 · 빈칸 %s" % (out.relative_to(P), len(text), gaps))
        return 0
    return run_prose(a.axis, do_open=a.open, model=a.model, only_if_stale=a.if_stale and not a.force)


if __name__ == "__main__":
    sys.exit(main())
