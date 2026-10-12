#!/usr/bin/env python3
"""정본 항목 읽기용 풀어쓰기 — C·WD·AC 한 항목의 칸을 읽는 사람 말로 다시 쓴 **조립물** (2026-09-22 사용자 결정 ②).

  python3 scripts/canon_plain.py WD-03            # → _workspace/checkpoint/plain/WD-03.json (없거나 낡은 칸만)
  python3 scripts/canon_plain.py WD-03 --force    # 그 항목 전부 다시
  python3 scripts/canon_plain.py --all            # 모든 항목 — 낡은 칸이 있는 것만
  python3 scripts/canon_plain.py WD-03 --units    # 단위(칸)와 낡음 여부만 stdout (모델 안 부름)

왜: WD-03 「내 기준」처럼 세션이 압축해 쓴 칸(T0~T3 같은 코드)은 정본에 그대로 둔다 — 정본은 대화 마감(refine_close)으로
계속 자라는 자리다. 읽는 화면에서만 풀어 보인다. 정본은 안 건드린다 — 고칠 문장은 정본에서 고치면 다음 조립에 따라온다
(axis_page 와 같은 원칙: 조립물).
단위 = 칸 하나(값 + 그 밑 entries 중 답할 필요 없는 것). 키 = 그 칸에 속한 줄 sha 를 이은 것 — 정본이 한 줄이라도 바뀌면
그 칸만 다시 푼다. 답해야 하는 ⚠️AI 문장(pending)은 풀지 않는다 — 서랍에서 그 문장 자체를 판정하는 자리라 원문이 서야 한다.
계약(valid_plain): 모든 단위 있음 · 비어 있지 않음 · 마크다운(굵게·백틱·불릿) 없음 · 풀어쓴 문장의 숫자열은 전부 블록 원문에
있어야 한다 · 원문의 3배+120자 이내. 불합격이면 형식 고치기 1회, 그래도 불합격이면 **안 쓴다**.
모델은 models.json 배정(canon.plain = opus), 도구 없음, 턴 2. 하트비트 키 `plain-<code>`. Python 3.9.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import canon_parse  # noqa: E402
import worker_claude  # noqa: E402
import models  # noqa: E402  (모델 ID 한 자리 — scripts/models.json)
import worker_status  # noqa: E402

import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
PLAIN_DIR = P / "_workspace/checkpoint/plain"
KST = timezone(timedelta(hours=9))
DEFAULT_MODEL = models.use("canon.plain")
SKIP_NAMES = ("장면",)   # 빈 장면은 서랍에서도 안 채운다 — 풀 것도 없다

CONTRACT = """출력은 JSON 하나: {"units": [{"key": "<주어진 key 그대로>", "plain": "<풀어 쓴 글>"}, ...]}
- 주어진 단위를 하나도 빠뜨리지 말고, 없는 key 를 만들지 마라.
- plain 은 문장 1~4개의 한 단락. 마크다운(굵게 **, 백틱 `, 불릿 -, 번호 목록, 제목 #) 금지. 줄바꿈 금지.
- 뜻을 더하거나 빼지 마라. 원문에 없는 수치·날짜·이름·주장을 넣지 마라. 원문의 숫자·날짜·경로·파일명은 그대로 쓴다.
- 코드·약어(예: T0, T2 같은 것)는 **이 항목 본문 안에 정의나 설명이 있을 때만** 그 뜻으로 풀어 쓴다.
  본문 안에 정의가 없으면 추측하지 말고 그대로 두고, 그 뒤에 (뜻은 본문에 없음)이라고 적는다.
- 일상적으로 쓰는 영어 낱말(engagement, seed, vibe, 스캐폴딩 같은 것)은 풀지도 표시하지도 말고 그대로 쓴다 — 표시가 붙으면 읽기가 더 어려워진다.
- 이미 평이한 한 문장짜리 칸은 거의 그대로 둬도 된다 — 억지로 늘리지 마라."""


def now_iso():
    return datetime.now(KST).isoformat(timespec="seconds")


def units_of(it):
    """칸 → 단위 목록. 서버(canon_block_api)도 같은 함수로 셈해서 화면과 워커가 어긋나지 않는다.

    각 단위: {key, name, shas, texts:[(sha, text)], src} — src 는 모델에 주는 원문(값 + entries 이어 붙임).
    pending(답해야 하는 ⚠️AI) 줄은 뺀다. 이름 없는 첫 줄(본인 발화 요약)과 빈 칸은 단위가 아니다.
    """
    out = []
    for s in it.get("slots") or []:
        name = (s.get("name") or "").strip()
        if not name or name in SKIP_NAMES:
            continue
        parts = []
        if (s.get("vtext") or "").strip() and not s.get("pending"):
            parts.append((s["sha"], s["vtext"].strip()))
        for x in s.get("entries") or []:
            if (x.get("vtext") or "").strip() and not x.get("pending"):
                parts.append((x["sha"], x["vtext"].strip()))
        if not parts:
            continue
        shas = [p[0] for p in parts]
        out.append({"key": "+".join(shas), "name": name, "shas": shas, "texts": parts,
                    "src": "\n".join(p[1] for p in parts)})
    return out


def cache_path(code):
    return PLAIN_DIR / ("%s.json" % code)


def load_cache(code):
    try:
        d = json.loads(cache_path(code).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def stale_units(it, cache, force=False):
    """다시 풀어야 하는 단위 — 캐시에 key 가 없는 것(정본 줄이 바뀌면 key 가 바뀐다)."""
    have = (cache.get("units") or {}) if not force else {}
    return [u for u in units_of(it) if u["key"] not in have]


def write_cache(code, it, cache, new_units, model):
    """현재 단위에 있는 key 만 남기고(정본에서 사라진 칸은 버린다) 새 것을 얹어 원자 교체."""
    keep = {u["key"] for u in units_of(it)}
    units = {k: v for k, v in (cache.get("units") or {}).items() if k in keep}
    at = now_iso()
    for u, plain in new_units:
        units[u["key"]] = {"plain": plain, "name": u["name"], "shas": u["shas"], "at": at, "model": model}
    d = {"code": code, "title": it.get("title") or "", "at": at, "units": units}
    PLAIN_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(PLAIN_DIR), prefix=".%s." % code, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cache_path(code))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return d


NUM_RE = re.compile(r"\d[\d,.:/~\-]*\d|\d")


def _numbers(s):
    return {n for n in NUM_RE.findall(s or "") if len(n) >= 2}


def valid_plain(o, units, block_text):
    """계약 검증 — 어긋난 첫 이유를 돌려주고, 통과면 None. 통과한 값은 o["_plain"] 에 {key: plain} 로 남긴다."""
    if not isinstance(o, dict) or not isinstance(o.get("units"), list):
        return "units 목록이 없다"
    got, raw = {}, {}
    for x in o["units"]:
        if isinstance(x, dict) and isinstance(x.get("key"), str):
            raw[x["key"]] = str(x.get("plain") or "")
            got[x["key"]] = " ".join(raw[x["key"]].split())
    want = {u["key"]: u for u in units}
    missing = [want[k]["name"] for k in want if k not in got]
    if missing:
        return "빠진 칸: " + ", ".join(missing[:4])
    extra = [k for k in got if k not in want]
    if extra:
        return "없는 key 를 만들었다: %s" % extra[0][:20]
    block_nums = _numbers(block_text)
    for k, u in want.items():
        p = got[k]
        if not p:
            return "「%s」이 비었다" % u["name"]
        # 불릿·제목은 **줄 머리**에서만 본다 — 문장 안 " - "(WD-09 원문 "as is - to be")를 불릿으로 오판해 안 썼다 (2026-09-22 실측)
        if "**" in p or "`" in p or re.search(r"(?m)^\s*(?:[-*•]|\d+[.)]|#{1,6})\s", raw[k]):
            return "「%s」에 마크다운·불릿이 있다" % u["name"]
        if len(p) > len(u["src"]) * 3 + 120:
            return "「%s」이 원문보다 너무 길다 (%d자)" % (u["name"], len(p))
        bad = sorted(_numbers(p) - block_nums)
        if bad:
            return "「%s」에 본문에 없는 숫자가 있다: %s" % (u["name"], bad[0])
    o["_plain"] = got
    return None


def prompt_for(it, units):
    block = "".join(it.get("lines") or [])
    unit_lines = "\n".join("- key: %s · 칸: %s\n  원문: %s" % (u["key"], u["name"], u["src"].replace("\n", " / "))
                           for u in units)
    return ("아래는 내 판단 기록(정본) 한 항목의 원문이다. 세션이 압축해 쓴 칸이 있어 코드·약어·영어 낱말이 섞여 있다. "
            "이 항목을 **처음 읽는 사람**이 문맥 없이도 뜻을 알 수 있게, 지정한 칸들을 읽기 쉬운 한국어로 풀어 써라. "
            "설명을 덧붙이는 게 아니라 같은 뜻을 쉬운 말로 다시 쓰는 것이다.\n\n"
            "## 항목 원문 (%s)\n%s\n\n## 풀어 쓸 칸\n%s\n\n## 계약\n%s" % (it["code"], block, unit_lines, CONTRACT))


def run_code(code, model=DEFAULT_MODEL, force=False, quiet=True):
    model = models.resolve(model, "canon.plain")
    it = canon_parse.item(code, P)
    if it is None:
        print("항목 없음: %s" % code, file=sys.stderr)
        return 2
    cache = load_cache(code)
    units = stale_units(it, cache, force)
    if not units:
        print("%s: 낡은 칸 없음" % code)
        return 0
    with worker_status.Beat("plain-" + code, "plain", "풀어쓰기 · %s" % code, model=model, quiet=quiet) as beat:
        beat.phase("칸 %d개 풀어쓰는 중" % len(units))
        raw = worker_claude.run(prompt_for(it, units), beat, [], model=model, timeout=300, max_turns=2)
        o = worker_claude.extract_json(raw)
        block_text = "".join(it.get("lines") or [])
        err = valid_plain(o, units, block_text) if o else worker_claude.failure_text("답에 JSON 이 없다")
        if err and o is not None:
            fixed = worker_claude.extract_json(
                worker_claude.repair_json(worker_claude.LAST_SID, model, beat, CONTRACT + "\n어긋난 점: " + err))
            err2 = valid_plain(fixed, units, block_text) if fixed else "형식 고치기 답이 없다"
            if not err2:
                o, err = fixed, None
        if err:
            beat.finish(False, "안 썼다 — " + err[:80])
            print("%s: 실패 — %s" % (code, err), file=sys.stderr)
            return 1
        write_cache(code, it, cache, [(u, o["_plain"][u["key"]]) for u in units], model)
        beat.finish(True, "칸 %d개" % len(units))
        print("%s: 칸 %d개 씀" % (code, len(units)))
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--units", action="store_true", help="단위·낡음만 보고 (모델 안 부름)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args()
    if a.units:
        it = canon_parse.item(a.code, P)
        if it is None:
            return 2
        cache = load_cache(a.code)
        st = {u["key"] for u in stale_units(it, cache)}
        for u in units_of(it):
            print("%s %-8s %s | %s" % ("낡음" if u["key"] in st else "있음", u["name"], u["key"][:24], u["src"][:70].replace("\n", " / ")))
        return 0
    if a.all:
        rc = 0
        for it in canon_parse.items(P):
            if stale_units(it, load_cache(it["code"]), a.force):
                rc = max(rc, run_code(it["code"], a.model, a.force))
                time.sleep(1)
        return rc
    if not a.code:
        ap.print_help()
        return 2
    return run_code(a.code, a.model, a.force)


if __name__ == "__main__":
    sys.exit(main())
