"""질문을 만들기 전에 **이미 쌓인 것**을 프롬프트에 통째로 넣는 공통 모듈.

왜 한 자리인가 (2026-09-17 사용자 지시): 대시보드가 "에이블리 데모에서 문제를 유형별로 나누고
우선순위 정한 기준이 뭐였나"를 물었는데 그 답은 이미 `journal/2026-09-07.md` §3 과
`projects/<프로젝트>/docs/feature-lock.md` 에 적혀 있었다. 같은 답을 두 번 타이핑하게 만든
원인은 셋이었다 — ① 재료 본문을 6000자에서 잘랐고 ② 이미 채운 칸(값)을 프롬프트에 안 넣었고
③ 데이터 루트 밖 프로젝트 문서를 아예 안 봤다. 질문을 만드는 워커가 여섯 곳이라 규칙 문장과
블록 조립을 여기 하나에 둔다 (summarize_feed·brief_worker·journal_writing·run_worker 의
일반·지도·열린질문·이어서묻기·후속 프롬프트).

2026-09-17 2차(Codex 검토 Q6): 구멍 여덟을 닫았다 — ① 후속 턴(TALK·FOLLOWUP)에도 이 블록이
간다 ② code/slot 이 없어도 **스레드 id·제목·ask·목적지**로 과거 답을 찾는다 ③ 답의 저자를
보존한다(`verbatim_ai` = AI 문장을 내가 승인한 것 → 내 판단과 섞지 않고 따로 표기) ④ craft 칸의
하위 불릿(누적 답)까지 읽는다 ⑤ 읽기 실패와 기록 부재를 구분해 블록에 적는다(조용히 "없음"으로
두면 같은 질문이 또 나온다) ⑥ 예산은 **최종 렌더 문자열** 기준이다 ⑦ 문장 중간을 자르지 않는다
— 헤딩 덩어리는 통째로 넣거나 목록으로 뺀다 ⑧ 인용 대조(경로 해석·정규화·원문 확인)도 여기 하나.

**자료 열람 범위와 인용 허용 범위는 다르다.** 프롬프트에 본문을 싣는 것(`sources_block`)은
호출자가 준 `.md` 면 되지만, "사용자가 쓴 문장"으로 **인용**해도 되는 파일은 `quotable()` 이
정한다(career/·journal/·drafts/ 와 projects/**). 읽었다고 인용해도 되는 것이 아니다.

모델 호출 없음·쓰기 없음. 순수 stdlib. Python 3.9 (서버·워커가 부르는 인터프리터).

의존 방향: grounding → application_craft (단방향). craft 는 grounding 을 import 하지 않는다.
"""
import json
import re
from pathlib import Path

HOME = Path.home()
import paths  # noqa: E402
P = paths.root()

# 여섯 프롬프트가 공유하는 한 문장 — 문구를 프롬프트마다 복제하지 않는다.
RULE = ("질문을 만들기 전에 아래 「이미 적혀 있는 것」과 「내 기록·프로젝트 문서」를 먼저 읽는다. "
        "거기 답이 있으면 질문 대신 그 대목을 인용해 '이렇게 적혀 있다 — 맞나?'로 확인만 한다. "
        "진짜 비어 있는 자리만 묻는다.")

ANSWERED_HEAD = "**이미 적혀 있는 것 — 여기 답이 있으면 묻지 말고 확인만 한다**"
SOURCES_HEAD = ("**내 기록·프로젝트 문서** (주제와 겹치는 헤딩만 실었다 — "
                "그 밖 헤딩은 제목만 있고 필요하면 Read 로 그 파일을 연다)")

# 블록 안 세 갈래 — 저자·용도가 다르므로 섞지 않는다 (2026-09-17 Codex Q6).
G_MINE = "[내가 답한 내용 — 내 판단이다]"
G_AI = "[AI 문장을 내가 승인한 것 — 내 판단과 섞지 말고 출처로만 본다]"
G_ASKED = "[이미 물은 질문 — 같은 것을 또 묻지 않는다]"

WORK_REL = "current-work.md"
ASSIGN_REL = "_workspace/checkpoint/assignments.jsonl"
FEED_REL = "visionary/linkedin/feed-queue.jsonl"

BACKTICK = re.compile(r"`([^`]+)`")
WCODE = re.compile(r"^\s*-\s+\*\*(W\d+)\.\s*(.+?)\*\*")
MATERIAL = re.compile(r"^\s*자료:")


# ── 경로 ─────────────────────────────────────────────────────────────────────
def resolve(p, root=P):
    """`journal/x.md`(root 기준) · `~/...`(홈) · 절대경로 → Path. 존재 확인은 호출자."""
    s = str(p or "").strip().strip("`")
    if not s:
        return None
    if s.startswith("~/"):
        return HOME / s[2:]
    q = Path(s)
    return q if q.is_absolute() else Path(root) / s


def display(path, root=P):
    """프롬프트에 찍는 경로 — root 밑이면 상대, 홈 밑이면 `~/`, 그 밖은 절대."""
    s = str(path)
    r, h = str(Path(root)) + "/", str(HOME) + "/"
    if s.startswith(r):
        return s[len(r):]
    if s.startswith(h):
        return "~/" + s[len(h):]
    return s


# ── 인용 가능한 파일인가 (한 자리) ───────────────────────────────────────────
# 2026-09-17 사용자 결정: `projects/**` 의 `.md` 도 인용 재료다 (current-work 선언 여부 무관).
# 인용 검증이 네 곳에 흩어져 있었으므로(application_craft.quote_ok · journal_writing.quote_exists ·
# summarize_feed.valid_lesson · brief_worker.quoted_in_source) 판정은 여기 하나만 두고 전부 이걸 부른다.
QUOTE_DIRS = ("career", "journal", "drafts")     # Personal 밑 — 본인이 쓴 글이 사는 자리
DEMO = P / "projects"                  # 데모 프로젝트 문서 (본인이 쓴 설계·확정표)
AI_MARK = ("⚠️AI", "⟨세션⟩")                     # 이 줄은 본인 문장이 아니다 — 인용 대조에서 뺀다


def quotable(path, root=P):
    """이 파일에서 **본인 문장으로 인용**해도 되나 — career/·journal/·drafts/ 와 projects/** 의 `.md`.

    프롬프트에 본문을 싣는 범위(`sources_block`)와 다르다. 읽어서 배경으로 쓰는 것은 더 넓다.
    """
    q = resolve(path, root)
    if q is None or q.suffix != ".md":
        return False
    try:
        q = q.resolve()
    except OSError:
        return False
    if not q.is_file():
        return False
    try:
        if q.is_relative_to(DEMO.resolve()):
            return True
    except (OSError, AttributeError):
        pass
    for d in QUOTE_DIRS:
        try:
            if q.is_relative_to((Path(root) / d).resolve()):
                return True
        except OSError:
            continue
    return False


def norm_quote(s):
    """인용 대조용 정규화 — 마크다운 기호·따옴표를 지우고 공백을 접는다.

    2026-09-02 실측(brief): 모델이 **굵게**·줄바꿈을 벗겨 옮긴다. craft 는 공백만 접었지만
    더 느슨한 쪽으로 합친다 — 기호 차이로 진짜 인용을 떨어뜨리던 자리를 없앤다.
    """
    return re.sub(r"\s+", " ", re.sub(r"[*_`>#\"“”'‘’]", "", s or "")).strip()


_file_cache = {}


def _file_norm(rel, root=P, marks=()):
    """대조용 파일 본문 (정규화·캐시). 못 읽으면 None. marks 가 있는 줄은 통째로 뺀다."""
    key = (str(root), str(rel), tuple(marks))
    if key not in _file_cache:
        q = resolve(rel, root)
        try:
            text = q.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            _file_cache[key] = None
            return None
        if marks:
            text = "\n".join(l if not any(m in l for m in marks) else "\0"
                             for l in text.splitlines())
        _file_cache[key] = norm_quote(text)
    return _file_cache[key]


def quoted_in(quote, source, root=P, scope="own", marks=(), min_len=1):
    """인용이 그 파일에 **글자 그대로** 있나 — 경로 해석·허용 판정·원문 대조 한 자리.

    source 는 `journal/x.md` 또는 `journal/x.md — 섹션` 둘 다 받는다 (brief 형식).
    scope="own" 이면 `quotable()` 이 허용한 파일만(본인 문장 인용), "any" 면 존재하는 `.md` 면 된다
    (배경 자료의 원문 대조 — 지어낸 출처는 여전히 걸린다).
    """
    if not isinstance(quote, str) or not isinstance(source, str):
        return False
    q = norm_quote(quote)
    if len(q) < min_len or not source.strip():
        return False
    rel = source.split(" — ")[0].strip()
    if scope == "own":
        if not quotable(rel, root):
            return False
    else:
        p = resolve(rel, root)
        if p is None or p.suffix != ".md" or not p.is_file():
            return False
    text = _file_norm(rel, root, marks)
    return bool(text) and q in text


def quote_ok(root, rel, quote, min_len=10):
    """내 재료 인용이 그 파일에 글자 그대로 있나 (⚠️AI·⟨세션⟩ 줄 제외).

    2026-09-17 craft 에서 이리로 옮겼다 — application_craft 가 grounding 을 import 하지 않게
    (의존 방향 단방향). journal 날짜 파일은 `journal_writing.quote_exists` 를 그대로 쓴다
    (AI 초안 절 제외 규칙이 거기 산다).
    """
    if not isinstance(rel, str) or not isinstance(quote, str) or len(norm_quote(quote)) < min_len:
        return False
    if any(m in quote for m in AI_MARK):
        return False
    if re.fullmatch(r"journal/\d{4}-\d{2}-\d{2}\.md", rel):
        import journal_writing          # 지연 import — journal_writing 이 이 모듈을 쓴다
        return journal_writing.quote_exists(Path(root), {"path": rel, "quote": quote})
    return quoted_in(quote, rel, root=Path(root), scope="own", marks=AI_MARK, min_len=min_len)


# ── 읽기 실패와 부재를 구분한다 ──────────────────────────────────────────────
def read_rows(path):
    """JSONL → (rows, err). err 는 사람이 읽는 한 줄이고, 성공이면 None.

    한 줄이 깨졌다고 전체를 "기록 없음"으로 바꾸지 않는다 — 성한 줄은 살리고 몇 줄이 깨졌는지 남긴다
    (2026-09-17 Codex Q6: 데이터가 있는데 같은 질문을 또 하는 원인이 여기 숨었다).
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return [], "%s — %s" % (display(p), e.__class__.__name__ + ": " + str(e))
    rows, bad = [], 0
    for line in raw:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    return rows, ("%s — %d줄이 깨졌다 (나머지 %d줄만 읽었다)" % (display(p), bad, len(rows))
                  if bad else None)


def _one_line(t, cap=400):
    t = " ".join(str(t or "").split())
    return t if len(t) <= cap else t[:cap] + "…"


# ── 프로젝트 선언 (current-work.md) ───────────────────────────────────────────
def projects(root=P):
    """「## 진행 중」의 W 항목 — [{code, title, paths:[Path...]}]. 존재하는 파일만 담는다.

    자료 선언은 항목 줄 아래 `자료:` 라벨 뒤 백틱 경로다. 없으면 paths 는 빈 리스트 (없는 게 정상).
    """
    try:
        text = (Path(root) / WORK_REL).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        section = text.split("## 진행 중", 1)[1].split("\n## ", 1)[0]
    except IndexError:
        return []
    out, cur = [], None
    for line in section.splitlines():
        m = WCODE.match(line)
        if m:
            cur = {"code": m.group(1), "title": m.group(2).strip(), "paths": [], "text": line}
            out.append(cur)
            continue
        if cur is None:
            continue
        if line.strip() and not line.startswith(" ") and not line.startswith("-"):
            cur = None
            continue
        cur["text"] += "\n" + line
        if MATERIAL.match(line):
            for raw in BACKTICK.findall(line):
                q = resolve(raw, root)
                if q is not None and q.exists() and q not in cur["paths"]:
                    cur["paths"].append(q)
    return out


def _keywords(title):
    """제목에서 본문 매칭에 쓸 낱말 — 두 글자 이상, 흔한 말은 뺀다."""
    stop = {"데모", "구축", "작업", "사이드", "수강", "발표", "관리", "분석"}
    return [w for w in re.split(r"[^\w가-힣]+", title or "")
            if len(w) >= 2 and w not in stop and not w.isdigit()]


TITLE_HITS = 2      # 제목 낱말 매칭 최소 개수 (2026-09-17: 흔한 낱말 하나로 붙던 오탐을 막는다)
UNIQ_LEN = 3        # 단, 그 프로젝트에만 있는 세 글자 이상 낱말은 하나로도 인정 ("에이블리")


def _uniq_keywords(prs):
    """한 프로젝트 제목에만 나오는 낱말 — 이건 하나만 겹쳐도 그 프로젝트를 가리킨다."""
    seen = {}
    for pr in prs:
        for k in set(_keywords(pr["title"])):
            seen[k] = seen.get(k, 0) + 1
    return {k for k, n in seen.items() if n == 1 and len(k) >= UNIQ_LEN}


def projects_for(paths_or_text, root=P):
    """재료 경로·본문에 닿는 프로젝트 — [{code,title,paths}]. 닿는 게 없으면 빈 리스트.

    매칭 셋: ① 재료 경로가 그 프로젝트 `자료:` 에 있다 ② 본문에 그 프로젝트 자료 경로(또는 그
    상위 폴더)가 박혀 있다 ③ 본문에 **제목 낱말이 둘 이상**, 또는 그 프로젝트에만 있는 세 글자
    이상 낱말이 하나 있다. ③을 아무 낱말 하나로 두면 "지표"·"정리" 같은 흔한 말로 엉뚱한
    프로젝트가 붙는다 (2026-09-17 Codex Q6 지적 → TITLE_HITS=2 + 고유 낱말 예외).
    """
    if isinstance(paths_or_text, (str, Path)):
        text, given = str(paths_or_text), []
    else:
        given = [str(resolve(p, root)) for p in (paths_or_text or []) if resolve(p, root)]
        text = "\n".join(str(p) for p in (paths_or_text or []))
    prs = projects(root)
    uniq = _uniq_keywords(prs)
    hit = []
    for pr in prs:
        own = [str(p) for p in pr["paths"]]
        match = any(o in given for o in own)
        if not match:
            for o in own:
                d = display(o, root)
                if d in text or display(Path(o).parent, root) in text:
                    match = True
                    break
        if not match:
            hits = [k for k in _keywords(pr["title"]) if k in text]
            if len(hits) >= TITLE_HITS or any(k in uniq for k in hits):
                match = True
        if match:
            hit.append(pr)
    return hit


# ── 이미 쌓인 것 ──────────────────────────────────────────────────────────────
def _item(group, src, text, when=""):
    return {"group": group, "src": src, "text": text, "when": when or ""}


def craft_answered(root=P, code=None, slot=None):
    """자소서 craft 정본에서 이미 채운 칸 + **칸 밑에 쌓인 하위 불릿** → (items, err).

    저장은 칸 첫 줄을 채운 뒤 두 번째부터 `  - …` 로 누적한다(checkpoint_board.canon_slot_fill).
    첫 줄만 읽으면 두 번째부터 낸 답이 grounding 에서 통째로 빠진다 (2026-09-17 Codex Q6).
    """
    items = []
    try:
        import application_craft as craft
        rows = craft.slots(Path(root))
    except (OSError, ValueError, KeyError, ImportError) as e:
        return [], "career/application-craft.md — %s: %s" % (e.__class__.__name__, e)
    for s in rows:
        if slot and s["key"] != slot:
            continue
        if code and not slot and s["code"] != code:
            continue
        where = "정본 %s (career/application-craft.md)" % s["key"]
        if s.get("value"):
            items.append(_item("mine", where, s["value"]))
        for i, ent in enumerate(s.get("entries") or []):
            items.append(_item("mine", "%s 누적 %d" % (where, i + 1), ent))
    return items, None


def _row_text(r):
    return " ".join(str(r.get(k) or "") for k in ("title", "ask", "code", "slot", "path"))


def _matches(r, code, slot, targets, toks):
    if code and (r.get("code") or "").strip() == code:
        return True
    if slot and (r.get("slot") or "").strip() == slot:
        return True
    for t in targets:
        if not t:
            continue
        for k in ("code", "slot", "path", "id"):
            if (r.get(k) or "") == t:
                return True
    if toks:
        blob = _row_text(r).lower()
        if len([t for t in toks if t in blob]) >= 2:
            return True
    return False


def thread_answered(root=P, code=None, slot=None, thread_id=None, targets=None, topic=None,
                    max_roots=8, exclude_ts=None):
    """끝났거나 도는 대시보드 스레드에서 **내가 친 답** → (items, err).

    code/slot 이 없어도 찾는다 (2026-09-17 Codex Q6): 스레드 id(현재 대화의 뿌리), 목적지
    (path·id·code·slot), 제목·ask 낱말 두 개 이상 겹침. 뿌리를 고른 뒤 `parent_ts` 사슬을
    **방문 집합**으로 끝까지 따라간다 (옛 코드는 4단계에서 멈췄다).

    저자를 보존한다 — `answers[].verbatim_ai` 가 참이면 AI 초안을 내가 그대로 승인한 글이므로
    "AI 문장(내가 승인)" 갈래로 넘긴다. 빼지는 않는다 (빼면 이미 한 답을 또 잃는다).
    """
    rows, err = read_rows(Path(root) / ASSIGN_REL)
    if not rows:
        return [], err
    targets = [str(t) for t in (targets or []) if t]
    toks = _tokens(topic, cap=40) if topic else []
    roots = [r for r in rows
             if (thread_id and r.get("ts") == thread_id) or _matches(r, code, slot, targets, toks)]
    roots.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    ids = {r.get("ts") for r in roots[:max_roots] if r.get("ts")}
    if thread_id:
        ids.add(thread_id)
    while True:                                  # 후속 사슬 — 방문 집합으로 끝까지 (사슬 길이 제한 없음)
        more = {r.get("ts") for r in rows if r.get("parent_ts") in ids and r.get("ts")} - ids
        if not more:
            break
        ids |= more
    items = []
    for r in rows:
        if r.get("ts") not in ids:
            continue
        # 지금 답하는 그 턴의 `ask` 는 뺀다 — 안 빼면 사용자가 방금 한 말이 「이미 적혀 있는 것」으로
        # 들어가고, 워커가 그걸 "이미 답이 있다"로 읽어 되짚기만 한다 (2026-09-18 실측).
        # 같은 줄의 `answers`(전에 제출한 글)·질문은 남긴다 — 그건 이번 말이 아니다.
        now_turn = bool(exclude_ts) and r.get("ts") == exclude_ts
        when = str(r.get("done_at") or r.get("ts") or "")[:10]
        if (r.get("ask") or "").strip() and not now_turn:
            items.append(_item("mine", "내가 쓴 답 %s" % when, r["ask"], when))
        for a in (r.get("answers") or []):
            if not isinstance(a, dict) or not (a.get("a") or "").strip():
                continue
            at = str(a.get("at") or when)[:10]
            if a.get("verbatim_ai"):
                items.append(_item("ai_ok", "AI 초안을 그대로 제출 %s" % at, a["a"], at))
            else:
                items.append(_item("mine", "내가 제출한 글 %s" % at, a["a"], at))
        q = ((r.get("for_me") or {}).get("question") or {})
        if isinstance(q, dict) and (q.get("q") or "").strip():
            items.append(_item("asked", "이미 물은 것 %s (대화)" % when, q["q"], when))
    return items, err


def asked_lessons(root=P, code=None, slot=None, cap=8):
    """이미 나간 가르침 질문 → (items, err). 칸을 지정 안 했으면 **최근 것부터** cap 개.

    옛 코드는 code/slot 이 없으면 통째로 버렸다 — 그래서 가르침 생성(summarize_feed.lesson_one)
    경로에는 중복 방지가 아예 안 걸렸다 (2026-09-17 Codex Q6).
    """
    rows, err = read_rows(Path(root) / FEED_REL)
    items = []
    for r in rows:
        les = r.get("lesson")
        if not isinstance(les, dict) or not les.get("question"):
            continue
        if slot and les.get("slot") != slot:
            continue
        if code and not slot and les.get("code") != code:
            continue
        at = str(les.get("at") or "")[:10]
        items.append(_item("asked", "이미 물은 것 %s (%s)" % (at, les.get("slot") or ""),
                           les["question"], at))
    items.sort(key=lambda x: x["when"], reverse=True)
    if not (slot or code):
        items = items[:cap]
    return items, err


def answered(code=None, slot=None, root=P, thread_id=None, targets=None, topic=None,
             exclude_ts=None):
    """이미 답이 나온 것 → (items, notes).

    items 는 {group(mine|ai_ok|asked), src, text, when}. notes 는 읽기 실패 한 줄들 —
    **부재와 실패를 구분한다**. 호출자는 code/slot 이 없어도 thread_id·targets·topic 으로 찾는다.
    """
    items, notes = [], []
    for got, err in (craft_answered(root, code=code, slot=slot),
                     thread_answered(root, code=code, slot=slot, thread_id=thread_id,
                                     targets=targets, topic=topic, exclude_ts=exclude_ts),
                     asked_lessons(root, code=code, slot=slot)):
        items += got
        if err:
            notes.append(err)
    seen, uniq = set(), []
    for it in items:
        key = (it["group"], " ".join(str(it["text"]).split())[:120])
        if key[1] and key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq, notes


ORDER = (("mine", G_MINE), ("ai_ok", G_AI), ("asked", G_ASKED))


def answered_block(code=None, slot=None, root=P, cap=6, budget=2400,
                   thread_id=None, targets=None, topic=None, line_cap=220, exclude_ts=None):
    """세 갈래로 나눠 찍는다 — 내 답 / AI 문장(내가 승인) / 이미 물은 질문.

    cap 은 갈래마다의 개수 상한이고 budget 은 **이 블록 전체**의 글자 상한이다. 넘치면 오래된
    것부터 빼고 "그 밖 N건 생략"을 남긴다. 읽기 실패는 `(읽기 실패: …)` 로 반드시 찍는다 —
    조용히 "(없음)" 으로 두면 데이터가 있는데 같은 질문을 또 하게 된다.
    """
    items, notes = answered(code=code, slot=slot, root=root, thread_id=thread_id,
                            targets=targets, topic=topic, exclude_ts=exclude_ts)
    lines, used, dropped = [], 0, 0
    for group, head in ORDER:
        got = [x for x in items if x["group"] == group]
        got.sort(key=lambda x: x["when"], reverse=True)
        picked = []
        for it in got[:cap]:
            ln = "- [%s] %s" % (it["src"], _one_line(it["text"], line_cap))
            if used + len(ln) + len(head) + 2 > budget:
                dropped += 1
                continue
            picked.append(ln)
            used += len(ln) + 1
        dropped += max(len(got) - cap, 0)
        if picked:
            lines.append(head)
            lines += picked
            used += len(head) + 1
    if dropped:
        lines.append("(그 밖 %d건 생략 — 필요하면 그 파일·카드를 직접 연다)" % dropped)
    for n in notes:
        # 실패 줄은 예산 밖이다 — 이것까지 잘리면 "왜 비었나"를 못 본다. 대신 한 줄로 줄인다.
        lines.append("(읽기 실패: %s — 이 자리는 '답 없음'이 아니라 '못 읽었다'다)" % _one_line(n, 160))
    return "\n".join(lines) if lines else "(없음)"


# ── 재료 본문 (자르지 않는다) ─────────────────────────────────────────────────
def _chunks(text):
    """헤딩 단위로 자른다 — 헤딩 줄부터 다음 헤딩 앞까지가 한 덩어리 (헤딩 없으면 통째 하나)."""
    lines = text.splitlines(keepends=True)
    out, cur = [], []
    for l in lines:
        if l.startswith("#") and cur:
            out.append("".join(cur))
            cur = [l]
        else:
            cur.append(l)
    if cur:
        out.append("".join(cur))
    return out or [""]


_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_STOP = set(("그리고 그러나 하지만 이것 저것 때문 위해 대한 대해 있다 없다 한다 하는 되는 있는 없는 "
             "것이 것은 지금 여기 우리 내가 나는 경우 정도 관련 내용 부분 사용자 이번 그것 무엇 "
             "the and for with that this from what your").split())


def _tokens(text, cap=80):
    """주제 낱말 — 한글은 2자 이상, 영문·숫자 혼합은 3자 이상(짧은 ASCII 는 'po'·'feat' 처럼
    다른 낱말 안에서 걸려 점수를 흐린다 — 2026-09-17 실측)."""
    out = []
    for w in _TOKEN.findall(str(text or "")):
        w = w.lower()
        if w in _STOP or w.isdigit():
            continue
        if w.isascii() and len(w) < 4:
            continue
        if w not in out:
            out.append(w)
    return out[:cap]


def _headed(paths, root=P):
    """([{rel, head, text, n}], [읽기 실패 한 줄...]) — 파일들을 헤딩 덩어리로 편다 (중복 파일 제거)."""
    out, seen, errs = [], set(), []
    for p in (paths or []):
        q = resolve(p, root)
        if q is None:
            errs.append("%s — 경로를 해석할 수 없다" % p)
            continue
        if not q.is_file():
            errs.append("%s — 파일이 없다" % display(q, root))
            continue
        key = str(q.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            body = q.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errs.append("%s — %s" % (display(q, root), e))
            continue
        rel = display(q, root)
        for c in _chunks(body):
            first = c.splitlines()[0] if c.splitlines() else ""
            head = first.strip() if first.startswith("#") else "(머리말)"
            out.append({"rel": rel, "head": head, "text": c, "n": len(c)})
    return out, errs


def _render(c):
    return "### %s — %s\n%s" % (c["rel"], c["head"], c["text"].rstrip())


REST_HEAD = "**그 밖 헤딩 목록** (본문은 안 실었다 — 필요하면 Read 로 그 파일을 연다)"


def sources_block(paths, budget=6500, root=P, topic=None, rest_share=0.3):
    """재료 본문 — **질문 주제와 겹치는 헤딩 덩어리만**. 나머지는 제목만 목록으로 준다.

    2026-09-17 사용자: 전량 주입으로 프롬프트가 2~3만 자가 됐다. 대신 주제(글 본문·지시·열린 질문·
    칸 이름)와 낱말이 겹치는 덩어리를 점수순으로 budget 까지 싣고, 안 실린 것은 `파일 — 헤딩` 목록으로
    남긴다 (워커 도구에 Read 가 있으니 필요하면 직접 연다). 주제가 없으면 파일 순서대로 싣는다.
    **문장 중간을 베지 않는다** — 덩어리 단위로 넣거나 빼고, 첫 덩어리가 커서 안 들어가면 그 덩어리도
    목록으로 뺀다 (2026-09-17 Codex Q6: 옛 코드는 첫 덩어리만 문자열로 잘랐다).
    budget 은 **이 함수가 돌려주는 문자열 전체**의 글자 수 상한이다 — 머리글·헤딩 목록·안내문 포함.
    """
    chunks, errs = _headed(paths, root)
    fail = ["(읽기 실패: %s)" % e for e in errs]
    if not chunks:
        return "\n".join(fail) if fail else "(없음)"
    toks = _tokens(topic) if topic else []
    for i, c in enumerate(chunks):
        low = c["text"].lower()
        head = c["head"].lower()
        c["score"] = sum(3 if t in head else (1 if t in low else 0) for t in toks)
        c["order"] = i
    if toks:
        # 파일별로 번갈아 고른다 — 한 파일이 예산을 다 먹어 다른 재료가 통째로 빠지는 것을 막는다
        # (2026-09-17 실측: 정본 3개가 예산을 채워 일지의 에이블리 절이 전부 밀렸다).
        by_file = {}
        for c in chunks:
            by_file.setdefault(c["rel"], []).append(c)
        for rel in by_file:
            by_file[rel].sort(key=lambda c: (-c["score"], c["order"]))
        ranked, i = [], 0
        while any(len(v) > i for v in by_file.values()):
            for rel in by_file:
                if len(by_file[rel]) > i:
                    ranked.append(by_file[rel][i])
            i += 1
    else:
        ranked = chunks
    head_cost = sum(len(f) + 1 for f in fail)
    picked, rest, used = [], [], head_cost
    room = int(budget * (1 - rest_share))     # 나머지는 「그 밖 헤딩 목록」 자리로 뗀다
    for c in ranked:
        if toks and c["score"] == 0 and picked:
            rest.append(c)
            continue
        cost = len(_render(c)) + 2            # 덩어리 사이 빈 줄까지 센다 (최종 문자열 기준 예산)
        if used + cost <= room:
            picked.append(c)
            used += cost
        else:
            rest.append(c)                    # 잘라 싣지 않는다 — 통째로 목록으로 간다
    picked.sort(key=lambda c: c["order"])
    out = list(fail) + [_render(c) for c in picked]
    if rest:
        # 목록도 파일별로 번갈아 — 한 파일의 헤딩이 목록을 다 먹으면 다른 파일은 존재 자체가 안 보인다.
        # 안 보이면 워커가 Read 할 생각을 못 한다 (2026-09-17 실측: 일지 §3 이 목록에서도 빠졌다).
        # 파일 안에서는 **점수순** — 예산이 줄면 목록도 줄어드는데, 파일 순서대로 나열하면 주제와
        # 가까운 헤딩이 뒤로 밀려 잘린다 (2026-09-17 실측: budget 6500 에서 일지 §3 이 목록에서 빠졌다).
        by = {}
        for c in sorted(rest, key=lambda c: (-c["score"], c["order"])):
            by.setdefault(c["rel"], []).append(c)
        lines, left, i, dropped = [], max(budget - used - len(REST_HEAD) - 2, 0), 0, 0
        while any(len(v) > i for v in by.values()):
            for rel in by:
                if len(by[rel]) <= i:
                    continue
                c = by[rel][i]
                ln = "- %s — %s" % (c["rel"], c["head"])
                if len(ln) + 1 <= left:
                    lines.append(ln)
                    left -= len(ln) + 1
                else:
                    dropped += 1
            i += 1
        if dropped:
            tail = "- (그 밖 %d개 — 파일을 Read 로 열면 다 있다)" % dropped
            if len(tail) + 1 > left and lines:
                left += len(lines[-1]) + 1
                lines.pop()
            lines.append(tail)
        if lines:
            out.append(REST_HEAD + "\n" + "\n".join(lines))
    return "\n\n".join(out)


def grounding_block(material_paths, code=None, slot=None, root=P, budget=6500,
                    extra_paths=None, text=None, topic=None, thread_id=None, targets=None,
                    answered_share=0.35, exclude_ts=None):
    """질문 만들기 전에 읽을 것 한 덩어리 — 이미 답한 것 + 재료·프로젝트 문서.

    material_paths(와 text)에 닿는 프로젝트(current-work.md `자료:`)의 문서를 같은 블록에 싣는다.
    budget 은 **돌려주는 문자열 전체**의 상한이다 (머리글·안내문·헤딩 목록 포함) — 2026-09-17
    Codex Q6: 옛 코드는 본문 길이만 세고 머리글·목록을 빼놓아 실제 길이가 예산을 넘었다.
    """
    paths = [p for p in (material_paths or [])]
    for p in (extra_paths or []):
        if p not in paths:
            paths.append(p)
    for pr in projects_for(paths, root=root) + (projects_for(text, root=root) if text else []):
        for q in pr["paths"]:
            if q not in paths and display(q, root) not in [str(x) for x in paths]:
                paths.append(q)
    fixed = len(ANSWERED_HEAD) + len(SOURCES_HEAD) + 4
    ans = answered_block(code=code, slot=slot, root=root, thread_id=thread_id,
                         targets=targets, topic=topic if topic is not None else text,
                         budget=max(int((budget - fixed) * answered_share), 400),
                         exclude_ts=exclude_ts)
    room = budget - fixed - len(ans)
    if room < 200:
        return "%s\n%s\n\n%s\n%s" % (ANSWERED_HEAD, ans, SOURCES_HEAD,
                                     "(예산이 다 찼다 — 재료 본문은 안 실었다. 필요하면 Read 로 연다)")
    return "%s\n%s\n\n%s\n%s" % (ANSWERED_HEAD, ans, SOURCES_HEAD,
                                 sources_block(paths, budget=room, root=root,
                                               topic=topic if topic is not None else text))
