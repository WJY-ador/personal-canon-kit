"""마감(정리) 계약 — 프롬프트·근거 스키마·순수 validator·저장 문자열 한 자리.

왜 (2026-09-17 사용자 R4): 채팅 구어를 그대로 정본에 박지 않는다. **판단·의미는 사용자 것,
문장은 AI**가 문어로 다듬고 경위를 2~3문장으로 붙인다. 사용자는 미리보기에서 확인만 하고
다시 치지 않는다(R5 — 모델 1회·클릭 2회·재타이핑 0). 표기는 `✍️정리`(손 필요 아님).

이 모듈은 **모델을 부르지 않고 파일도 쓰지 않는다.** 워커(run_worker)가 프롬프트를 쓰고 모델을
부르며, 서버(checkpoint_board)가 목적지 후보를 발급하고 저장한다. 검증은 양쪽이 **같은 함수**를
부른다 — 워커가 받을 때 한 번, [제출] 때 한 번 (Codex Q3: 화면에 판정 규칙을 두지 않는다).

근거(evidence) 세 갈래 — `turn`(이번 대화에서 내가 친 말) · `record`(과거 기록의 내 판단,
grounding 이 찾아 온 것) · `background`(배경 자료·AI 노트·외부 글). **판단 문장이 background
뿐이면 반려한다** — 그건 내 판단이 아니라 남의 글이다.

Python 3.9, 순수 stdlib. 서버·워커 양쪽이 import 한다.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canon_parse  # noqa: E402  (정본 무표기·✍️정리 값 = 내 어휘 말뭉치)
import grounding    # noqa: E402  (인용 대조 정규화 한 자리)

import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
MARK = "✍️정리"
VERSION = 2

KINDS = ("turn", "record", "background")
LIMITS = {"text_n": 3, "text_len": 200, "why_n": 3, "why_len": 160, "quote_len": 240}

PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스 마감 워커다. 대화가 끝났고 사용자가 [완료]를 눌렀다.
네 일은 **사용자의 판단을 정본 문장으로 옮겨 적는 것**이다. 판단·의미는 사용자 것이고, 문장만 네가 쓴다.
사용자는 이 결과를 화면에서 **확인만** 한다 — 다시 타이핑하게 만들지 마라.

**이 대화** (누가 쳤는지 표시돼 있다)
{turns}

**근거 후보** — 문장마다 여기 id 를 붙인다. 없는 id 를 지어내면 그 목적지는 통째로 버려진다.
{pool}

**목적지 후보** — 아래 target_id 중 **하나**를 고른다. 경로·앵커를 네가 만들지 마라.
{targets}

{grounded}

**규칙**
- `text` 는 정본에 그대로 들어갈 **문어** 1~{text_n}문장(각 {text_len}자 이내). 구어·말버릇·이모지·감탄사 없이,
  주어를 세우고 판단을 한 문장에 하나씩. 채팅 원문을 그대로 옮기지 마라 — 그건 카드에 남는다.
- `why` 는 경위 2~3문장(각 {why_len}자 이내): **무엇을 묻다가 / 무엇이 뒤집혔고 / 어디에 붙나**.
  대화에 없는 사정을 지어내지 마라. 없으면 없다고 짧게 쓴다.
- **어휘는 사용자 문장에 있는 단어로 쓴다.** 새 개념어·조어를 만들지 말고(예: "캡슐"·"프레임" 같은
  이름을 새로 붙이지 말고) 풀어서 문장으로 설명한다. 사용자가 쓰지 않은 명사는 화면에 밑줄로 표시된다.
- **가리키는 말은 풀어 쓴다.** 사용자가 "N번"·"요점 N"·"그 문장"·"이거"처럼 무언가를 가리키면 가리킨
  원문(근거 후보의 요점·Claude 답)을 찾아 그 **내용**을 문장에 쓴다. 정본 문장은 이 대화와 글을 안 본
  사람이 읽어도 서야 한다 — `text`·`why` 에 "요점 6"·"6번"·"위 문장" 같은 지시어를 남기지 마라.
  가리킨 원문은 `evidence` 에 background 로 인용한다.
- **문장 끝마다 근거 id 를 괄호로 붙인다** — 예: `… 되돌린다. (t2)` · `… 기준이 뒤집혔다. (t1, r3)`.
  이 표기는 저장할 때 자동으로 떨어진다(정본에는 판단 문장만 들어간다). 붙지 않은 문장이 있으면 반려된다.
- `evidence` 는 그 목적지의 `text`·`why` 를 떠받치는 근거다. 각 항목은 {{"id","kind","quote","source"}} —
  `quote` 는 그 후보의 **원문에서 글자 그대로** 옮긴 대목이다(줄바꿈·기호는 접어도 된다).
- **판단 문장의 근거는 `turn` 또는 `record` 여야 한다.** `background` 만 붙은 목적지는 반려된다.
- 목적지가 여럿이면 dests 를 여럿 낸다. 억지로 늘리지 마라 — 보통 하나다. 넣을 판단이 없으면 dests 는 빈 배열.

**출력** — JSON 하나만. 코드펜스·설명 금지.
{{"version": 2,
 "dests": [{{"target_id": "후보 중 하나",
            "text": "정본 문장 1~{text_n}개",
            "why": "경위 2~3문장",
            "evidence": [{{"id": "t1", "kind": "turn", "quote": "원문 그대로", "source": "대화 2026-09-17"}}]}}]}}
"""


# ── 근거 후보 ────────────────────────────────────────────────────────────────
def candidates(turns=None, records=None, background=None):
    """[{id, kind, text, source}] — 워커가 모아 프롬프트에 싣고, validator 가 인용을 대조하는 그 목록.

    turns = 이번 대화에서 **사용자가 친** 문장, records = grounding 이 찾은 과거 내 답·정본 값,
    background = 배경 자료(AI 노트·외부 글·재료 본문). id 접두어로 갈래가 보인다.
    """
    out = []
    for pre, kind, rows in (("t", "turn", turns), ("r", "record", records),
                            ("b", "background", background)):
        for i, row in enumerate(rows or [], 1):
            if isinstance(row, dict):
                text, src = row.get("text") or "", row.get("source") or ""
            else:
                text, src = str(row), ""
            if not str(text).strip():
                continue
            out.append({"id": "%s%d" % (pre, i), "kind": kind,
                        "text": " ".join(str(text).split()), "source": src})
    return out


def pool_block(pool, cap=700):
    if not pool:
        return "(없음 — 근거가 없으면 dests 는 빈 배열이다)"
    name = {"turn": "이번 대화(내 말)", "record": "과거 기록(내 판단)", "background": "배경 자료"}
    return "\n".join("- [%s · %s] %s%s" % (c["id"], name.get(c["kind"], c["kind"]),
                                           c["text"][:cap], (" — " + c["source"]) if c["source"] else "")
                     for c in pool)


def targets_block(targets):
    if not targets:
        return "(없음)"
    out = []
    for t in targets:
        cur = " ".join(str(t.get("current") or "").split())
        out.append("- [%s] %s%s" % (t["id"], t.get("label") or t["id"],
                                    ("\n    지금 적힌 것: " + cur[:300]) if cur else "\n    지금 비어 있다"))
    return "\n".join(out)


def prompt(turns_text, pool, targets, grounded=""):
    return PROMPT.format(turns=turns_text or "(대화 없음)", pool=pool_block(pool),
                         targets=targets_block(targets),
                         grounded=grounded or "", **LIMITS)


# ── 어휘 (R7) ────────────────────────────────────────────────────────────────
_WORD = re.compile(r"[가-힣]{2,}")
# 조사·어미가 붙은 채로 비교하면 전부 낯설어진다 — 꼬리를 떼고 본다.
_TAIL = ("으로써", "이라는", "라는", "에서의", "에게서", "으로", "에서", "에게", "까지", "부터", "보다",
         "처럼", "이나", "하고", "이며", "면서", "지만", "이다", "한다", "된다", "했다", "이고",
         "은", "는", "이", "가", "을", "를", "의", "도", "에", "와", "과", "로", "만", "요")
_COMMON = set(("그리고 그러나 하지만 그래서 때문 위해 대한 대해 있다 없다 한다 하는 되는 있는 없는 "
               "것이 것은 것을 지금 여기 우리 내가 나는 경우 정도 관련 내용 부분 이번 그것 무엇 "
               "사용자 사람 문장 질문 기준 판단 결정 기록 정리 확인 설명 이유 경위 근거 출처 "
               "대화 화면 파일 자리 상태 방식 구조 과정 결과 목적 대상 조건 변화 문제 방법").split())


def _stem(w):
    for t in _TAIL:
        if w.endswith(t) and len(w) - len(t) >= 2:
            return w[:-len(t)]
    return w


def corpus_words(texts):
    out = set()
    for t in texts:
        for w in _WORD.findall(str(t or "")):
            out.add(w)
            out.add(_stem(w))
    return out


CORPUS_CACHE = ".runtime/.corpus-words.json"    # 재생성 가능한 런타임 캐시 (.lineage-data.json 과 같은 자리)
CORPUS_DIRS = ("journal", "career")             # 내 문장이 사는 자리 — 일지·커리어 날짜 기록


def corpus(root=P, cache=True):
    """내 어휘 말뭉치 — 일지·커리어 기록 본문 + 정본의 무표기·✍️정리 값.

    2026-09-17 사용자 결정: 일지를 넣되 **마감마다 수십 파일을 다시 읽지 않는다** — 파일별
    mtime 을 키로 캐시하고 바뀐 파일만 다시 읽는다. 캐시가 깨지면 그냥 다시 만든다(정확성 손해 없음).
    """
    files = []
    for d in CORPUS_DIRS:
        p = Path(root) / d
        if p.is_dir():
            files += sorted(str(q) for q in p.glob("*.md"))
    old = {}
    cp = Path(root) / CORPUS_CACHE
    if cache:
        try:
            old = json.loads(cp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
    fresh, words, changed = {}, set(), False
    for f in files:
        try:
            key = "%s:%d" % (f, int(Path(f).stat().st_mtime))
        except OSError:
            continue
        hit = (old.get("files") or {}).get(f)
        if hit and hit.get("key") == key:
            fresh[f] = hit
        else:
            changed = True
            try:
                fresh[f] = {"key": key, "words": sorted(corpus_words([Path(f).read_text(encoding="utf-8", errors="replace")]))}
            except OSError:
                continue
        words |= set(fresh[f]["words"])
    if cache and (changed or len(fresh) != len(old.get("files") or {})):
        try:
            cp.write_text(json.dumps({"files": fresh}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return words | corpus_words(canon_corpus(root))


def canon_corpus(root=P):
    """내 어휘 말뭉치의 정본 몫 — 무표기·✍️정리 칸 값만 (⚠️AI 값은 내 말이 아니다)."""
    out = []
    for it in canon_parse.items(Path(root)):
        for s in it["slots"] + it["history"]:
            if canon_parse.PENDING_MARK in s["marks"]:
                continue
            if s["marks"] and MARK not in s["marks"] and "⟨세션⟩" not in s["marks"]:
                continue
            if s.get("vtext"):
                out.append(s["vtext"])
    return out


# 용언(동사·형용사) 꼴은 새 개념어가 아니다 — 잡아도 고칠 게 없다. 명사류만 본다
# (2026-09-17 실측: "적는"·"자리다"·"뒤집혔는지"가 줄줄이 잡혀 진짜 조어 "캡슐"이 묻혔다).
_VERBISH = ("다", "는", "고", "며", "서", "면", "게", "지", "야", "네", "죠", "요", "던", "듯", "라", "든", "킨", "된")


def unfamiliar(text, corpus):
    """말뭉치에 없는 2자 이상 한글 **명사류** — 표시용이다. 대체어를 만들지 않는다."""
    out = []
    for w in _WORD.findall(str(text or "")):
        s = _stem(w)
        if w in _COMMON or s in _COMMON:
            continue
        if w in corpus or s in corpus:
            continue
        if s.endswith(_VERBISH) or len(s) < 2:
            continue
        if w not in out:
            out.append(w)
    return out


# ── 검증 (워커 수신 때와 [제출] 때 같은 함수) ────────────────────────────────
_REF_GROUP = r"[\(\[][ ]*[trb]\d{1,2}(?:[ ,·/]+[trb]\d{1,2})*[ ]*[\)\]]"

# 가리키는 말 — 정본에 남으면 대화·글을 안 본 사람이 못 읽는다 (2026-09-08 "요점 6" 실측).
POINTER_RE = re.compile(r"요점\s?\d+|(?<![\d.\-])\d+\s?번(?!째)|위 문장|그 문장|앞의 문장|이 글에서")


def _sentences(t):
    """문장 나누기 — 근거 표기 `(t1)` 는 **앞 문장에 붙인다**(따로 떨어지면 표기 없는 문장으로 오판한다)."""
    s = " ".join(str(t or "").split())
    s = re.sub(r"\s+(" + _REF_GROUP + ")", r"\1", s)
    parts = [x.strip() for x in re.split(r"(?<=[.!?…다])\s+", s) if x.strip()]
    parts = [x for x in parts if not re.fullmatch(_REF_GROUP, x)]
    return parts or ([s] if s else [])


def _refs(t):
    """문장에 붙은 evidence id — `(t1)`·`[r2]`·`t1,b3` 어느 표기든 읽는다."""
    return set(re.findall(r"\b([trb]\d{1,2})\b", str(t or "")))


def strip_refs(t):
    """문장 끝의 근거 표기 `(t1)`·`[r2, b1]` 를 뗀다 — 정본에는 판단 문장만 들어간다."""
    s = re.sub(_REF_GROUP, "", str(t or ""))
    return " ".join(s.split())


def validate(result, ctx, strict=True):
    """(ok_dests, rejected, warnings) — 순수 함수. ctx = {pool, targets, corpus?, root?}.

    ok_dests 의 각 항목은 입력 dest 에 `unfamiliar`·`sentences` 를 더하고 `text`·`why` 는
    근거 표기를 뗀 문장이다. 형식 위반은 rejected 에 사유가 남고, 조용히 옛 형식으로 넘기지 않는다.

    strict=True (워커가 모델 답을 받을 때): **문장마다 근거 id** 가 붙어 있어야 한다.
    strict=False ([제출] 때): 사용자가 미리보기에서 고치면 그 표기가 사라진다 — 그래서 문장별
    표기는 다시 요구하지 않고 **근거 인용·갈래·길이·목적지**만 같은 규칙으로 다시 본다.
    """
    pool = {c["id"]: c for c in (ctx.get("pool") or [])}
    targets = {t["id"]: t for t in (ctx.get("targets") or [])}
    vocab = ctx.get("corpus")
    if vocab is None:
        # 말뭉치 = 이번 대화·근거 + 일지·커리어 기록(캐시) + 정본의 내 문장
        vocab = corpus_words([c["text"] for c in (ctx.get("pool") or [])]) | corpus(ctx.get("root") or P)
    ok, rejected, warnings = [], [], []
    if not isinstance(result, dict):
        return [], [{"dest": None, "reason": "결과가 JSON 객체가 아니다"}], []
    if result.get("version") != VERSION:
        return [], [{"dest": None, "reason": "version %s — 이 계약은 version %d 다" % (result.get("version"), VERSION)}], []
    dests = result.get("dests")
    if not isinstance(dests, list):
        return [], [{"dest": None, "reason": "dests 가 배열이 아니다"}], []
    for d in dests:
        if not isinstance(d, dict):
            rejected.append({"dest": None, "reason": "목적지 형식이 아니다"})
            continue
        tid = str(d.get("target_id") or "")
        text = " ".join(str(d.get("text") or "").split())
        why = " ".join(str(d.get("why") or "").split())
        ev = d.get("evidence")
        def bad(reason):
            rejected.append({"dest": tid or None, "text": text[:200], "reason": reason})
        if tid not in targets:
            bad("목적지 %s 는 후보에 없다" % (tid or "(빈 값)"))
            continue
        if not text:
            bad("정본 문장이 비었다")
            continue
        if not isinstance(ev, list) or not ev:
            bad("근거가 없다")
            continue
        sents = _sentences(text)
        if len(sents) > LIMITS["text_n"]:
            bad("정본 문장이 %d개 — %d개 이내" % (len(sents), LIMITS["text_n"]))
            continue
        if any(len(s) > LIMITS["text_len"] for s in sents):
            bad("문장 하나가 %d자를 넘는다" % LIMITS["text_len"])
            continue
        wsents = _sentences(why)
        if len(wsents) > LIMITS["why_n"] or any(len(s) > LIMITS["why_len"] for s in wsents):
            bad("경위가 %d문장·각 %d자를 넘는다" % (LIMITS["why_n"], LIMITS["why_len"]))
            continue
        kinds, bad_ev = set(), None
        for e in ev:
            if not isinstance(e, dict):
                bad_ev = "근거 형식이 아니다"
                break
            eid, quote = str(e.get("id") or ""), str(e.get("quote") or "")
            if eid not in pool:
                bad_ev = "근거 id %s 가 후보에 없다" % (eid or "(빈 값)")
                break
            if e.get("kind") and e["kind"] != pool[eid]["kind"]:
                bad_ev = "근거 %s 의 갈래가 후보와 다르다 (%s ≠ %s)" % (eid, e["kind"], pool[eid]["kind"])
                break
            q = grounding.norm_quote(quote)
            if len(q) < 4:
                bad_ev = "근거 %s 의 인용이 너무 짧다" % eid
                break
            if q not in grounding.norm_quote(pool[eid]["text"]):
                bad_ev = "근거 %s 의 인용이 원문에 없다: %s" % (eid, quote[:40])
                break
            kinds.add(pool[eid]["kind"])
        if bad_ev:
            bad(bad_ev)
            continue
        if not (kinds & {"turn", "record"}):
            bad("판단의 근거가 배경 자료뿐이다 — 내 말이나 내 기록이 있어야 한다")
            continue
        if strict:
            ids = {str(e.get("id")) for e in ev}
            miss = [s for s in sents + wsents if not (_refs(s) & ids)]
            if miss:
                bad("근거 id 가 없는 문장이 %d개 (문장 끝에 (t1) 처럼 붙인다): %s" % (len(miss), miss[0][:40]))
                continue
        hit = POINTER_RE.search(strip_refs(text))
        if hit and strict:
            bad("가리키는 말 '%s'이 남았다 — 대화·글을 안 본 사람이 읽어도 서게 내용을 풀어 쓴다" % hit.group(0))
            continue
        if hit:   # 제출 때(사용자가 고친 뒤)는 막지 않는다 — 사용자 문장이 우선이다
            warnings.append({"dest": tid, "reason": "가리키는 말 '%s' 이 정본 문장에 남았다" % hit.group(0)})
        whit = POINTER_RE.search(strip_refs(why))
        if whit:
            warnings.append({"dest": tid, "reason": "경위에 가리키는 말 '%s'" % whit.group(0)})
        # 근거 표기를 먼저 뗀다 — `(r11, r3)` 의 11 을 '근거 밖 숫자'로 경고하던 오탐
        # (2026-09-17 WD-01 실사격 경고 2건이 전부 이것). 진짜 숫자·날짜는 그대로 잡힌다.
        nums = set(re.findall(r"\d[\d,.]*", strip_refs(text)))
        pool_nums = set()
        for e in ev:
            pool_nums |= set(re.findall(r"\d[\d,.]*", pool.get(str(e.get("id")), {}).get("text", "")))
        out_nums = [x for x in nums if x not in pool_nums and len(x) > 1]
        if out_nums:
            warnings.append({"dest": tid, "reason": "근거 밖 숫자·날짜: %s" % ", ".join(sorted(out_nums)[:4])})
        text, why = strip_refs(text), strip_refs(why)
        sents, wsents = _sentences(text), _sentences(why)
        unf = unfamiliar(text, vocab) + [w for w in unfamiliar(why, vocab) if w]
        seen, uniq = set(), []
        for w in unf:
            if w not in seen:
                seen.add(w)
                uniq.append(w)
        ok.append(dict(d, target_id=tid, text=text, why=why, evidence=ev,
                       unfamiliar=uniq, sentences=sents,
                       label=targets[tid].get("label"), target=targets[tid]))
    return ok, rejected, warnings


def legacy(result):
    """옛 proposal(version 없음)인가 — 서버가 옛 화면·저장 경로로 보낸다."""
    return not (isinstance(result, dict) and result.get("version") == VERSION)


# ── 저장 문자열 (생성은 여기 한 곳) ──────────────────────────────────────────
def source_note(dest, today, lane="", paths=None):
    """출처 줄 — 판단 문장과 **다른 줄**에 둔다 (한 줄에 이어 붙이면 대표 문장에 경위가 섞인다)."""
    bits = ["출처: %s %s" % (lane or "대시보드 대화", today)]
    if dest.get("why"):
        bits.append("경위: " + " ".join(str(dest["why"]).split()))
    srcs = []
    for e in (dest.get("evidence") or []):
        s = (e.get("source") or "").strip()
        if s and s not in srcs:
            srcs.append(s)
    srcs += [p for p in (paths or []) if p not in srcs]
    if srcs:
        bits.append(" · ".join(srcs[:3]))
    return " · ".join(bits)


def render_entry(dest, today, lane="", indent="  "):
    """정본 칸 밑 하위 불릿 두 줄 — 판단 문장과 출처 줄을 분리한다 (설계 「저장 형식」).

    ```
    - **한계** ⟨세션⟩: (기존 문장 그대로)
      - ✍️정리 (2026-09-17): 가공 문장.
        출처: 열린 질문 대화 2026-09-17 · 경위: … · journal/2026-07-19.md
    ```
    """
    text = " ".join(str(dest.get("text") or "").split())
    line = "%s- %s (%s): %s\n" % (indent, MARK, today, text)
    src = "%s  %s\n" % (indent, source_note(dest, today, lane))
    return line, src


def render_insight(dest, today, lane="피드 대화"):
    """insights.md 「#### 내 생각」 자리 — 같은 문장·출처 구조, 불릿 깊이만 다르다."""
    text = " ".join(str(dest.get("text") or "").split())
    return "%s %s (%s): %s\n  %s\n" % ("-", MARK, today, text, source_note(dest, today, lane))


def entry_sha(dest, today, lane="", indent="  "):
    """저장 중복 판정 — 같은 완료 건을 재시도로 두 번 붙이지 않게 (Codex Q3)."""
    line, _ = render_entry(dest, today, lane, indent)
    return canon_parse.sha_of(line)
