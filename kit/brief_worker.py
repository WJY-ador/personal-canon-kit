#!/usr/bin/env python3
"""분배 즉시 브리핑 생성 워커 — 앞단에서 옮기면 뒷단이 바로 돈다.

사용: python3 scripts/brief_worker.py            # brief 없는 pending 전부
      python3 scripts/brief_worker.py --ts <ts>  # 한 건만 (서버가 분배 직후 호출)

무인 경계 (CLAUDE.md 비용·외부모델 전제):
  이 워커는 **정본을 읽기만** 하고 절대 수정하지 않는다. 산출은
  assignments.jsonl 의 `brief` 필드뿐이고, 출력은 스키마로 검증한다(실패 시 폐기,
  status 는 pending 유지 → 세션이 다시 시도). 판별은 여전히 사람 —
  사용자가 브리핑을 보고 지시(`ask`)를 내려야 실행으로 넘어간다.
  구독 CLI(claude -p) 범위 안에서만 돈다. 종량제 키 사용 없음.

브리핑 = 그 글을 **사용자 본인의 일지·정본과 엮어** 정리한 것. 앵커는 일지다
(2026-09-02 — 사용자는 C·WD 코드가 아니라 날짜·장면으로 기억한다. 코드 참조는 기계용):
  scene(일지 날짜 + 본인 문장 그대로 + 이 글이 그 장면에 더하는 것 — 없으면 null)
  / fork(갈림길 한 줄) / default(기본 판정 + 뒤집을 조건) / options(시킬 수 있는 일 3개)
  / claim·links·tension(정본 코드 연결 — 보드에선 접힘. 실행 워커 프롬프트에 함께 넘어간다)

소비자: checkpoint_board.html 카드, run_worker.py(brief_block). lineage·360 은 읽지 않는다.
사용자 되먹임: 카드의 '아니다'(brief_fix.scene 날짜·brief_fix.links 코드)는 재정리 때
같은 자리로 못 돌아오게 valid() 가 막고, '뒤집기'(take)는 실행 워커에 사용자 판정으로 넘어간다.
"""
import argparse, json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import worker_status, worker_claude   # noqa: E402
import models                         # noqa: E402  (모델 ID 한 자리 — scripts/models.json)
import checkpoint_store as store      # noqa: E402  (큐 쓰기는 여기로만 — flock+원자 교체)
import grounding                      # noqa: E402  (질문 만들기 전에 쌓인 것 먼저 읽기 — 공통)

KST = timezone(timedelta(hours=9))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
ASSIGN = P / "_workspace/checkpoint/assignments.jsonl"
FEED = P / "visionary/linkedin/feed-queue.jsonl"
LINEAGE = P / ".runtime/.lineage-data.json"
TOOLS = ["Read", "Grep", "Glob", "ToolSearch", "mcp__docsearch__search_docs"]

LANE_TASK = {
    "cross": "insights.md 에 인용 조립 (내 판단과 교차)",
    "draft": "발행 글감·초안 (drafts/)",
    "canon": "C·WD 근거란 한 줄 (career/)",
    "research": "원 출처·수치·반증 확인",
}


def rows(path):
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except OSError:
        return []


def canon_list():
    try:
        d = json.loads(LINEAGE.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    return "\n".join(f"- {n['id']} {n.get('maturity','')} {n.get('title','')}"
                     for n in d.get("nodes", []) if n.get("type") in ("C", "WD"))


def source_text(item_id):
    for r in rows(FEED):
        if r.get("id") == item_id:
            return r.get("text", ""), r.get("canon_fit") or []
    return "", []


def journal_candidates(codes):
    """정본 계보(lineage)에서 이 코드들에 '어디에 붙나'로 닿아 있는 일지를 코드로 뽑는다.

    모델 창작 없이 결정적으로 후보를 좁히는 장치 (CLAUDE.md 비용 규칙 ②). 단, 이 엣지는
    일지의 '어디에 붙나' 절에서 기계가 파싱한 것이고 2026-09-01 이전 절은 AI 문안이 많다 —
    후보일 뿐 본인 확정이 아니라고 프롬프트에 밝힌다.
    """
    try:
        d = json.loads(LINEAGE.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    want = {c.get("code") if isinstance(c, dict) else c for c in codes}
    want.discard(None)
    if not want:
        return []
    nodes = {n["id"]: n for n in d.get("nodes", []) if n.get("type") == "journal"}
    hits = {}
    for e in d.get("edges", []):
        if e.get("src") in nodes and e.get("dst") in want:
            hits.setdefault(e["src"], set()).add(e["dst"])
    out = []
    for jid, cs in hits.items():
        n = nodes[jid]
        out.append((n.get("date", ""), n.get("path", ""), sorted(cs)))
    return sorted(out, reverse=True)


def fixes(a):
    """사용자가 카드에서 '아니다'라고 한 연결 — 재정리 때 같은 자리로 못 돌아오게 한다."""
    f = a.get("brief_fix") or {}
    return {"scene": set(f.get("scene") or []), "links": set(f.get("links") or [])}


SCHEMA_KEYS = {"scene", "fork", "default", "claim", "links", "tension", "options"}
ANCHOR_PREFIX = ("journal/", "drafts/")   # 본인 발화가 날짜와 함께 사는 자리 (정본 인용은 links)


def _s(x):
    return isinstance(x, str) and bool(x.strip())


def _norm(s):
    """인용 대조용 정규화 — grounding 한 자리 (마크다운 기호·따옴표를 지우고 공백을 접는다)."""
    return grounding.norm_quote(s)


def quoted_in_source(quote, source, scope="any"):
    """'본인 문장 그대로'를 코드로 확인한다 — source("journal/x.md — 섹션")의 파일에 quote 가 있나.

    2026-09-17: 경로 해석·정규화·원문 대조·캐시를 `grounding.quoted_in` 한 자리로 옮겼다
    (Codex Q6 "인용 판정 한 자리가 아직 사실이 아니다"). 용도별 제약은 여기 남는다 —
    scene 은 일지·drafts 만(ANCHOR_PREFIX, valid() 가 본다), links 는 존재하는 `.md` 면 대조한다.
    scope="own" 을 주면 `grounding.quotable`(career/·journal/·drafts/·DEMO)로 좁힌다.
    """
    if not _s(quote) or not _s(source):
        return False
    return grounding.quoted_in(quote, source, root=P, scope=scope)


def valid(b, fix=None):
    """코드 계약 — 이걸 통과 못 하면 폐기한다. fix = 사용자가 이미 아니라고 한 연결.

    인용 대조: scene.quote 가 그 파일에 없으면 폐기(장면을 지어낸 것), links 의 quote 가
    없으면 그 link 만 뺀다(프롬프트 규칙과 동일 — 찾지 못하면 빼라). 통과한 b 는 제자리 수정된다.
    """
    fix = fix or {"scene": set(), "links": set()}
    if not isinstance(b, dict) or not SCHEMA_KEYS <= set(b):
        return False
    # scene: 일지에 닿는 자리. null 허용(억지 연결 금지) — 있으면 본인 문장·출처가 있어야 한다
    sc = b["scene"]
    if sc is not None:
        if not isinstance(sc, dict) or not _s(sc.get("quote")) or not _s(sc.get("source")):
            return False
        if not isinstance(sc.get("date", ""), str) or not isinstance(sc.get("then", ""), str):
            return False
        if not sc["source"].startswith(ANCHOR_PREFIX):
            return False
        if sc.get("date") in fix["scene"]:
            return False
        if not quoted_in_source(sc["quote"], sc["source"], scope="own"):
            print(f"scene 인용이 {sc['source']} 에 없다 — 폐기", file=sys.stderr)
            return False
    if not _s(b["fork"]):
        return False
    d = b["default"]
    if not isinstance(d, dict) or not _s(d.get("verdict")) or not isinstance(d.get("flip", ""), str):
        return False
    if not _s(b["claim"]):
        return False
    if not isinstance(b["links"], list) or not isinstance(b["options"], list):
        return False
    if not 1 <= len(b["options"]) <= 4:
        return False
    kept = []
    for l in b["links"]:
        if not isinstance(l, dict) or "code" not in l or "rel" not in l:
            return False
        if l["rel"] not in ("근거", "반증", "인접"):
            return False
        if l["code"] in fix["links"]:
            return False
        if l.get("quote") and not quoted_in_source(l["quote"], l.get("source", "")):
            print(f"link {l['code']} 인용이 {l.get('source')} 에 없다 — 이 link 만 뺀다", file=sys.stderr)
            continue
        kept.append(l)
    b["links"] = kept
    return True


PROMPT = """너는 """ + paths.owner_fmt() + """의 Personal 워크스페이스에서 도는 브리핑 워커다.
사용자가 방금 아래 글을 "{lane_name}" 레인으로 분배했다. 사용자가 지시를 내리기 전에,
**이 글을 사용자 본인의 일지(journal/)와 먼저 엮고**, 그다음 정본과 엮어 정리하라.

사용자는 C·WD 같은 정본 코드가 아니라 **날짜와 그날의 장면**으로 기억한다. 그래서 사람이
보는 앵커는 scene(일지)이고, 코드 연결(links)은 기계(lineage·360)용이다.

**절대 규칙**
- 정본·일지 파일을 절대 수정하지 마라. 읽기만 한다 (Read·Grep·search_docs).
- scene 은 **journal/YYYY-MM-DD.md 또는 drafts/ 에서 사용자가 쓴 문장을 찾아 그대로** 옮긴다.
  지어내지 마라. 이 글이 실제로 닿는 장면이 없으면 scene 은 null — 억지로 붙이지 마라.
  없음도 정보다. source 는 반드시 `journal/` 또는 `drafts/` 로 시작한다 (정본 인용은 links 쪽).
  drafts/ 는 본인 발화 인용으로 시작하는 프로세스 기록이라 날짜·장면을 가진다 — 일지에
  없을 때 두 번째로 본다.
- links 의 quote 도 **사용자가 쓴 문장을 정본에서 찾아 그대로** 옮긴다. 찾지 못하면 그 link 를 빼라.
- 원문이 실제로 다루지 않는 항목을 추측으로 연결하지 마라. 없으면 links 는 빈 배열.
- fork 는 갈림길 **하나**를 사용자 말투로 한 줄. default 는 네가 보기에 기본이 되는 쪽과,
  사용자가 그걸 뒤집을 조건. 사용자는 기본 판정을 보고 틀린 것만 뒤집는다.
- 출력은 JSON 하나뿐. 설명·코드펜스 금지.

**정본 항목 목록** (links 의 code 는 여기서만)
{canon}

**이미 매겨진 연결 후보** (검증하고 필요하면 고쳐라): {fits}

**먼저 볼 일지 후보** — 정본 계보에서 위 코드에 '어디에 붙나'로 닿아 있는 날. 기계가
파싱한 후보이고 본인 확정이 아니다. 여기서 시작하되 인용은 반드시 본인 문장을 찾아서:
{candidates}

**사용자가 이미 아니라고 한 연결** (다시 붙이면 폐기된다): {rejected}

{rule}

{grounded}

**분배된 글 (원문)**
{text}

**출력 스키마**
{{"scene": {{"date":"2026-08-20","quote":"일지에서 사용자가 쓴 문장 그대로",
            "source":"journal/2026-08-20.md — 섹션","then":"이 글이 그 장면에 뭘 더하나 한 줄"}} ,
  "fork": "갈림길 한 줄 — A 냐 B 냐",
  "default": {{"verdict":"기본 판정 한 줄","flip":"이걸 뒤집을 조건 한 줄"}},
  "claim": "이 글이 말하는 것 한 문장",
  "links": [{{"code":"C-02","rel":"근거|반증|인접","note":"어디서 만나는지 한 줄",
              "quote":"사용자 본인 문장 그대로","source":"파일 경로 — 항목"}}],
  "tension": "걸리는 지점 — 부딪히거나 확인이 필요한 대목 (없으면 빈 문자열)",
  "options": ["여기서 시킬 수 있는 일 (명령문, 3개)"]}}
scene 이 없으면 `"scene": null`.

fork·default 를 쓸 때도 위 「이미 적혀 있는 것」·「내 기록·프로젝트 문서」를 먼저 본다 — 거기서
이미 정해진 것을 갈림길로 되묻지 마라. 이미 있으면 그 문장을 default.verdict 의 근거로 쓴다.

찾는 순서: ① 위 일지 후보를 Read 로 본다. ② `search_docs` 로 journal/ 을 훑는다 (질문을
원문의 주제로, 안 나오면 표현을 바꿔 한 번 더). ③ 최근 일지 파일(Glob journal/*.md 마지막
8개)을 Read 로 직접 본다. ④ 일지에 없으면 drafts/ 를 search_docs 로 본다.
⑤ 그다음 정본(career/)을 links 용으로 본다.
레인의 기본 작업은 "{lane_task}" 이므로 options 는 그 방향으로 낸다."""


def build(a, beat):
    beat.phase("원문 읽는 중")
    text, fits = source_text(a.get("id", ""))
    if not text:
        text = a.get("title", "")
    fix = fixes(a)
    # 후보 코드 = canon_fit + 옛 brief 의 links (재정리 때) — 아니라고 한 코드는 뺀다
    codes = [c.get("code") for c in fits if isinstance(c, dict)]
    codes += [l.get("code") for l in ((a.get("brief") or {}).get("links") or [])
              if isinstance(l, dict)]
    codes = [c for c in codes if c and c not in fix["links"]]
    cands = [(d, p, cs) for d, p, cs in journal_candidates(codes) if d not in fix["scene"]]
    cand_txt = "\n".join(f"- {d} `{p}` ({', '.join(cs)})" for d, p, cs in cands[:8]) or "(없음)"
    rej = []
    if fix["scene"]:
        rej.append("일지 " + ", ".join(sorted(fix["scene"])))
    if fix["links"]:
        rej.append("코드 " + ", ".join(sorted(fix["links"])))
    # 질문·갈림길을 만들기 전에 이미 쌓인 것을 통째로 넣는다 (2026-09-17) — 일지 후보 본문 전량 +
    # 글 본문에 닿는 프로젝트 문서(current-work.md `자료:`) + 이미 답한 것.
    # code/slot 이 없는 자리다 — 대신 **목적지 후보**(canon_fit 코드·이 글의 id)를 넘겨 과거 답을
    # 찾게 한다 (2026-09-17 Codex Q6: brief·map·journal 호출이 code 를 안 넘겨 대화 답이 안 실렸다).
    grounded = grounding.grounding_block([p for _, p, _ in cands[:3]], budget=6500,
                                         targets=codes + ([a.get("id")] if a.get("id") else []),
                                         thread_id=a.get("ts"),
                                         text=text[:4000] + "\n" + (a.get("title") or ""))
    prompt = PROMPT.format(
        lane_name=a.get("lane", ""), lane_task=LANE_TASK.get(a.get("lane"), ""),
        canon=canon_list(), fits=json.dumps(fits, ensure_ascii=False),
        candidates=cand_txt, rejected=" / ".join(rej) or "(없음)",
        rule=grounding.RULE, grounded=grounded,
        text=text[:4000])
    out = worker_claude.run(prompt, beat, TOOLS, max_turns=16, timeout=420,
                            model=models.resolve(a.get("model"), "brief"))
    beat.phase("결과 검증 중")
    b = worker_claude.extract_json(out)
    return b if valid(b, fix) else None


def main():
    worker_status.rotate_err_log("brief_worker")   # 커졌으면 시작 때 한 번 회전 (.1 하나)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", help="이 배정 하나만 처리 — brief 가 이미 있어도 다시 정리한다 (재정리)")
    args = ap.parse_args()

    data = store.read()
    # feed 에서 온 건만 브리핑한다 — 다른 소스(개인 항목)는 건드리지 않는다.
    # --ts 없이 돌면 brief 없는 pending 만, --ts 로 지목하면 끝나지 않은 건은 다시 정리한다
    # (옛 형식 brief 를 새 형식으로 바꾸는 길 — 2026-09-02).
    if args.ts:
        targets = [a for a in data if a.get("ts") == args.ts and a.get("source") == "feed"
                   and a.get("status") in ("pending", "briefed")]
    else:
        targets = [a for a in data
                   if a.get("status") == "pending" and not a.get("brief")
                   and a.get("source") == "feed"]
    if not targets:
        print("브리핑할 건 없음")
        return 0

    done = 0
    for a in targets:
        with worker_status.Beat(a["ts"], "brief", a.get("title", ""),
                                model=models.resolve(a.get("model"), "brief")) as beat:
            b = build(a, beat)
            if not b:
                beat.finish(False, worker_claude.failure_text("브리핑이 계약을 통과 못 함 — 다시 시도할 수 있다"))
                print(f"브리핑 실패 (스키마/응답) — pending 유지: {a['title'][:40]}", file=sys.stderr)
                continue
            beat.finish(True, "브리핑 완료")
        # 잠금 아래 병합 — 워커가 도는 사이 사용자가 쓴 걸 덮지 않는다 (checkpoint_store)
        def _save(cur):
            for r in cur:
                if r.get("ts") == a["ts"]:
                    r["brief"] = b
                    r["briefed_at"] = datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z")
                    r["take"] = None   # 새 기본 판정이 나왔으니 옛 판정 채택·반박은 무효
                    if r.get("status") == "pending":
                        r["status"] = "briefed"
        store.mutate(_save)
        done += 1
        print(f"브리핑 완료: {a['title'][:40]}")
    return 0 if done else 3


if __name__ == "__main__":
    sys.exit(main())
