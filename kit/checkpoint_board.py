#!/usr/bin/env python3
"""오늘의 체크포인트 보드 — 로컬 관제 앱 (순수 stdlib, AI 미사용).

사용: python3 scripts/checkpoint_board.py   → http://127.0.0.1:8787 자동 오픈

역할 분담 (무인 이원화 원칙):
  앱 = 관제 — 사용자가 카드를 보고 판별(클릭=확정)하고 업무를 레인에 분배한다.
  세션 = 실행 — Claude 세션이 assignments.jsonl 의 pending 을 읽어 실행하고 done 처리.
  서버 = 파일 중계만 — 모델 호출 없음, 127.0.0.1 바인드, 정본 jsonl 에 직접 기록.

쓰는 파일:
  visionary/linkedin/feed-queue.jsonl   : status(kept/dropped)·axes 확정
  visionary/linkedin/reactions.jsonl    : kind(taste/record/social) 확정
  _workspace/checkpoint/assignments.jsonl : 분배 큐 (append; 세션이 소비)
읽는 파일: 위 + scripts/.pipeline-status.json, career/open-questions.md
"""
import errno, json, os, re, socketserver, subprocess, sys, tempfile, threading, time, webbrowser
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

KST = timezone(timedelta(hours=9))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
FEED = P / "visionary/linkedin/feed-queue.jsonl"
SLOW_LOG = P / ".runtime/.board_slow.log"   # 250ms 넘는 요청만 (handle_one_request)
REAC = P / "visionary/linkedin/reactions.jsonl"
LIKES = P / "visionary/threads/likes.jsonl"          # Threads 좋아요 (공감 신호층) — kind 태깅은 반응과 같은 버튼
ASSIGN = P / "_workspace/checkpoint/assignments.jsonl"
STATUS = P / ".runtime/.pipeline-status.json"
TEMPLATE = CODE / "checkpoint_board.html"

# 앱에서 볼 수 있는 로그 (화이트리스트 — 이 밖은 못 읽는다). 터미널을 안 열고 원인을 본다.
LOGS = {
    # 서버 자신의 로그가 여기 없어서 09-17 부터 쌓인 500 을 아무도 못 봤다 (2026-09-18)
    "board": ("보드 서버", P / ".runtime/.checkpoint_board.err.log"),
    "run": ("실행 워커", P / ".runtime/.run_worker.err.log"),
    "brief": ("정리 워커", P / ".runtime/.brief_worker.err.log"),
    "collect": ("LinkedIn 수집", P / ".runtime/.linkedin_collect.err.log"),
}
PORT = paths.port()
LOGIN_FAIL_WINDOW = 1800   # cli_login_ok 판정 창(30분) — 하트비트의 AUTH_LINE 흔적이 이 안에 있으면 본다 (2026-09-11)

# ── 동시 처리 (2026-09-16, ISSUE-202609-SINGLE-THREAD-SLOW-GET)
# 서버는 ThreadingTCPServer 라 요청마다 스레드가 뜬다. 느린 GET(/360 의 lineage_scan 재실행,
# /api/structure 0.4~1.6초) 하나가 사용자의 POST 를 줄 세우던 구조를 없애는 게 목적이다
# (BUTTON-NO-FEEDBACK 원인 ③). 공유 상태는 아래 잠금으로만 만진다.
# 잠금 순서 — WRITE_LOCK → SCAN_LOCK / REVIVE_LOCK → RUNS_LOCK. 나머지는 잎(안에서 다른 잠금을 잡지 않는다).
WRITE_LOCK = threading.RLock()   # 상태를 바꾸는 모든 경로 = do_POST 전체 + GET /api/feed_read(FEED 를 다시 쓴다).
                                 # 단일 스레드 때와 똑같이 직렬 — 정본 .md 는 읽고-고쳐-쓰기라 겹치면 한쪽이 사라진다.
SCAN_LOCK = threading.Lock()     # 파생 산출물 재조립 (/360 · /map · lineage_scan · process_scan) — 같은 파일을 쓴다. 한 번에 하나.
REVIVE_LOCK = threading.Lock()   # _revived + revive_once — 배경 revive_loop 와 POST /api/revive 가 같이 부른다.
RUNS_LOCK = threading.Lock()     # worker_status.read_all() — 읽으면서 오래된 하트비트를 지운다(실패 로그 이중 기록 방지).
MODELS_LOCK = threading.Lock()   # _models_cache · _models_bg
GOTO_LOCK = threading.Lock()     # GOTO — 쓰는 쪽이 clear+update 라 읽는 쪽은 통째로 복사해 간다.


def awrite(path, text, encoding="utf-8"):
    """원자 교체 쓰기 — 같은 디렉터리 임시 파일에 다 쓴 뒤 os.replace.

    스레드 서버로 바꾸면서 필요해졌다: 쓰는 POST 와 읽는 GET 이 진짜로 겹친다(/api/state 는
    5초마다 FEED·정본 json 을 전부 읽는다). `write_text` 는 truncate 후 쓰기라 그 사이에
    읽으면 반쪽 파일이 온다. `os.replace` 는 원자라 읽는 쪽은 옛것 아니면 새것만 본다.
    checkpoint_store._write 와 같은 장치 — 거기는 큐 전용이라 이 모듈에도 한 벌 둔다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:   # 새 파일은 0600 으로 뜬다 — 원본이 있었으면 그 권한을 그대로
            os.chmod(tmp, os.stat(str(path)).st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def runs_all(include_old_failed=False):
    """worker_status.read_all() 한 자리 — 그 함수는 읽으면서 오래된 하트비트 파일을 지운다.
    요청 스레드 둘이 동시에 들어가면 같은 실패 기록을 실패 로그에 두 번 적는다."""
    with RUNS_LOCK:
        return worker_status.read_all(include_old_failed)


sys.path.insert(0, str(CODE))
import model_usage  # noqa: E402  (로컬 파일 스캔만 — API 호출 0)
import models  # noqa: E402  (모델 ID 한 자리 — scripts/models.json. 화면 목록·옛 ID 매핑)
import canon_parse  # noqa: E402  (정본 블록·칸·⚠️AI 판정 한 파서 — 2026-09-17)
import refine       # noqa: E402  (마감 계약 — 프롬프트·검증·저장 문자열, 2026-09-17)
import canon_plain  # noqa: E402  (읽기용 풀어쓰기 — 단위 셈·캐시 읽기만, 모델은 워커 프로세스, 2026-09-22)
_models_cache = {"at": 0, "data": None}



CANON_PATHS = {"C": P / "career/decisions.md", "WD": P / "career/work-decisions.md",
               "AC": P / "career/application-craft.md"}   # AC = 자기소개서·이력서 craft (2026-09-10), 헤딩은 WD 와 같은 `## AC-XX.`


def canon_locate(code):
    """C-XX/WD-XX/AC-XX 항목 블록의 (path, lines, start, end). 못 찾으면 (None,)*4.

    2026-09-17: 헤딩 규약·경계 판정은 `canon_parse` 한 자리 — 서버·lineage·워커가 각자 정규식을
    들고 있으면 같은 파일에 다른 답이 나온다 (칩 19 대 서랍 17).
    """
    return canon_parse.locate(code, P)


def canon_scene_fill(code, text):
    """장면 슬롯 채움 — 사용자가 보드에서 직접 타이핑한 문장 = 본인 발화 (마킹 없음)."""
    path, lines, s, e = canon_locate(code)
    if path is None:
        return "항목을 못 찾았다: " + code
    for i in range(s, e):
        if re.match(r"^- \*\*장면\*\*:[ \t]*$", lines[i].rstrip("\n")):
            lines[i] = f"- **장면**: {text}\n"
            awrite(path, "".join(lines), encoding="utf-8")
            return None
        if re.match(r"^- \*\*장면\*\*:[ \t]*\S", lines[i]):
            return "장면이 이미 있다 — 파일에서 직접 고쳐라"
    return "장면 슬롯이 없는 항목이다"


def open_local(target):
    """산출물 열기 — `.md` 가 Obsidian 볼트(데이터 루트) 안이면 Obsidian 으로, 아니면 기본 앱 (2026-09-10 사용자 ".md는 obsidian으로").
    Obsidian 이 없거나 볼트 밖이면 `open` 으로 떨어진다. 볼트 이름은 폴더 이름 = Personal."""
    from urllib.parse import quote
    target = Path(target)
    try:
        in_vault = target.suffix == ".md" and str(target.resolve()).startswith(str(P.resolve()) + "/") \
            and (P / ".obsidian").exists() and Path("/Applications/Obsidian.app").exists()
    except OSError:
        in_vault = False
    if in_vault:
        rel = str(target.resolve().relative_to(P.resolve()))[:-3]   # 볼트 상대경로, 확장자 없이
        subprocess.Popen(["open", "obsidian://open?vault=%s&file=%s" % (quote(P.name), quote(rel))])
    else:
        subprocess.Popen(["open", str(target)])


def canon_entry_append(code, name, dest, today, lane=""):
    """정본 칸에 `✍️정리` 문장을 넣는다 — 저장 문자열은 `refine.render_entry` 한 곳에서 온다.

    2026-09-17 (설계 ③): 칸 찾기는 `canon_parse` 로 바꿨다 — 옛 `canon_slot_fill` 정규식은
    `- **긴장** ⟨세션⟩:` 처럼 **표기가 붙은 칸**을 못 찾았다. 칸이 비어 있으면 그 줄을 채우고
    (진행 숫자가 늘도록), 이미 차 있으면 칸 밑에 하위 불릿으로 쌓는다(craft 는 최종본이 아니라
    장면이 생길 때마다 쌓는 자리 — 2026-09-10 사용자). 판단 문장과 출처 줄은 다른 줄이다.
    같은 문장이 이미 있으면 다시 붙이지 않는다(재시도 중복 방지). 반환 (dest, err)."""
    path, lines, s0, e0 = canon_parse.locate(code, P)
    if path is None:
        return None, "항목을 못 찾았다: " + code
    it = canon_parse.item(code, P)
    # 이력 밑 절(WD-02 「닫는 조건」)도 자리다 — 표준 칸에서 못 찾으면 거기서 찾는다 (2026-09-18)
    slot = next((x for x in it["slots"] + it["history"] if x.get("name") == name), None)
    if slot is None:
        return None, "그 칸이 없다: %s / %s" % (code, name)
    entry, src = refine.render_entry(dest, today, lane)
    where = "%s › %s %s" % (path.relative_to(P), code, name)
    if any(canon_parse.sha_of(entry) == e["sha"] for e in (slot.get("entries") or [])):
        return where + " (이미 같은 문장이 있어 건너뜀)", None
    if not slot.get("value"):
        # 빈 칸 — 첫 줄을 채운다. 표기는 ✍️정리 (본인 판단을 AI가 문장화하고 본인이 제출)
        text = " ".join(str(dest.get("text") or "").split())
        lines[slot["span"][0]] = "- **%s** %s (%s): %s\n" % (name, refine.MARK, today, text)
        lines[slot["span"][0] + 1:slot["span"][0] + 1] = ["  " + refine.source_note(dest, today, lane) + "\n"]
    else:
        at = slot["span"][1]
        for e in (slot.get("entries") or []):
            at = max(at, e["span"][1])
        lines[at:at] = [entry, src]
    awrite(path, "".join(lines), encoding="utf-8")
    return where, None


def canon_ai_answer(code, sha, mode, text):
    """⚠️AI 불릿 여물리기 — replace=본인 문장으로 교체(칸 이름은 남기고 표기만 제거, 원래 AI
    문장은 바로 밑 `출처:` 줄로 접는다), append=⚠️AI는 남기고 본인 답을 아래 줄로 (미확정 유지).

    2026-09-17: 줄 찾기를 `canon_parse.item(code).pending` 으로 바꿨다 — 옛 방식은 `- ` 로
    시작하는 **들여쓰기 없는 줄**만 봐서 칸 밑 하위 불릿(`  - ⚠️AI …`)을 못 찾았고, 찾은
    경우에도 `- **이름** ⚠️AI: 값` 줄을 통째로 바꿔 **칸 이름을 지웠다**. sha 는 그대로다
    (`canon_parse.sha_of` = 옛 sha1(첫 줄)[:10] 와 같은 값). 반환 err 또는 None.
    """
    path, lines, s, e = canon_parse.locate(code, P)
    if path is None:
        return "항목을 못 찾았다: " + code
    it = canon_parse.item(code, P)
    note = next((x for x in it["pending"] if x["sha"] == sha), None)
    if note is None:
        return "그 ⚠️AI 줄을 못 찾았다 — 파일이 바뀌었을 수 있다. 새로고침."
    a, b = note["span"]
    pad = re.match(r"^[ \t]*", lines[a]).group(0)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if mode != "replace":
        lines[b:b] = ["%s- 본인 답 (%s): %s\n" % (pad, today, text)]
        awrite(path, "".join(lines), encoding="utf-8")
        return None
    # 원래 AI 문장 — 지우지 않고 출처 줄로 접는다 (canon_note_fold 와 같은 모양)
    body = str(note.get("vtext") or "") or re.sub(
        r"^-\s*", "", " ".join(x.strip() for x in lines[a:b] if not canon_parse.SOURCE_RE.match(x)))
    body = " ".join(body.replace(canon_parse.PENDING_MARK, "").split()).lstrip(": ")[:300]
    keep = [l for l in lines[a + 1:b] if canon_parse.SOURCE_RE.match(l)]   # 옛 출처 줄은 살린다
    if note.get("name"):                      # 칸 줄 자체 — 이름은 남기고 표기만 없앤다 (무표기=본인)
        head = "%s- **%s**: %s\n" % (pad, note["name"], text)
    else:                                     # 칸 밑 하위 불릿·블록 표식
        head = "%s- %s (본인, %s)\n" % (pad, text, today)
    src = "%s  출처: 본인 %s · 원래 AI 문장 — %s\n" % (pad, today, body)
    lines[a:b] = [head, src] + keep
    awrite(path, "".join(lines), encoding="utf-8")
    return None


def canon_ai_prune(code, shas, keep):
    """⚠️AI 불릿 여러 개 → 사용자가 고른 문장만 남겨 **한 줄로 합치고** 나머지는 지운다 (2026-09-03).

    문장 생성 없음 — keep 의 각 문장은 지워지는 불릿 안에 실제로 있어야 한다(마크다운 기호·
    공백만 정규화해 대조). 고른 게 없으면 그 불릿들을 전부 지운다. 합친 줄도 ⚠️AI 표기를
    단다 — 고르기는 본인 판단이지만 문장은 아직 AI 것이라서. 복구는 git.
    """
    import hashlib
    path, lines, s, e = canon_locate(code)
    if path is None:
        return "항목을 못 찾았다: " + code
    spans = []
    for i in range(s, e):
        ln = lines[i]
        if ln.startswith("- ") and "⚠️AI" in ln and hashlib.sha1(ln.rstrip("\n").encode()).hexdigest()[:10] in shas:
            j = i + 1
            while j < e and lines[j].startswith("  "):
                j += 1
            spans.append((i, j))
    if not spans:
        return "그 ⚠️AI 줄을 못 찾았다 — 파일이 바뀌었을 수 있다. 새로고침."
    norm = lambda t: re.sub(r"\s+", " ", re.sub(r"[*_`]", "", t)).strip()
    pool = norm(" ".join("".join(lines[i:j]) for i, j in spans))
    kept = [norm(k) for k in keep if norm(k)]
    for k in kept:
        if k not in pool:
            return "고른 문장이 원문에 없다 — 화면을 새로고침하고 다시"
    today = datetime.now(KST).strftime("%Y-%m-%d")
    merged = [f"- ⚠️AI (대시보드 추림 {today}): {' '.join(kept)}\n"] if kept else []
    first = spans[0][0]
    for i, j in reversed(spans):
        del lines[i:j]
    lines[first:first] = merged
    awrite(path, "".join(lines), encoding="utf-8")
    return None


_models_bg = {"running": False}


def _models_refresh():
    try:
        data = model_usage.snapshot()
    except Exception as e:
        data = {"error": str(e)}
    with MODELS_LOCK:   # 스캔은 잠금 밖에서 — 0.3~0.75초를 들고 있으면 /api/models 가 다시 줄 선다
        _models_cache["data"] = data
        _models_cache["at"] = time.time()
        _models_bg["running"] = False


def models_snapshot(force=False):
    """모델 사용량 — 스캔(~/.claude 330MB jsonl + 키체인 + 원격 조회, 실측 0.3~0.75초)은 뒤에서 돌리고
    마지막 값을 바로 준다. 서버가 단일 스레드라 여기서 기다리면 그 사이 누른 버튼이 전부 밀렸다
    (2026-09-08 .board_slow.log 실측). force(사용자가 새로 조회)와 첫 호출만 동기."""
    with MODELS_LOCK:   # "한 번에 하나" 게이트 — 검사와 표시를 한 잠금 안에서 (스레드 서버)
        sync = force or not _models_cache["data"]
        start_bg = (not sync) and time.time() - _models_cache["at"] > 60 and not _models_bg["running"]
        if start_bg:
            _models_bg["running"] = True
    if sync:
        _models_refresh()
    elif start_bg:
        threading.Thread(target=_models_refresh, daemon=True).start()
    with MODELS_LOCK:
        return _models_cache["data"]

# launchd 스케줄 정본은 scripts/launchd/README.md 표 — 여기는 표시용 사본 (변경 시 같이)
# 스케줄 표시용 — kit 엔 무인 잡이 없다. 켠 잡을 여기 적는다 {label, when, what}.
SCHEDULE = [
]
LANES = [
    {"key": "cross", "name": "내 판단과 교차", "desc": "insights.md · 인용 조립"},
    {"key": "draft", "name": "글로 쓰기", "desc": "일지의 내 생각 → 축별 글감 → 말로 편집"},
    {"key": "canon", "name": "근거로 박기", "desc": "career/ C·WD 근거란 한 줄"},
    {"key": "research", "name": "사실 확인하기", "desc": "원 출처·수치·반증"},
    # 드래그 대상은 아니다 — 지도(/map)에서 시킨 건의 이름표 (2026-08-27)
    {"key": "map", "name": "지도에서 시킴", "desc": "Personal 지도에서 파일·폴더에 시킨 일",
     "drag": False},
    # 피드 [Claude] 의 기본 — 요약된 글을 놓고 그냥 이어서 묻는다 (2026-09-10 사용자: "레인 고르는 게 힘들다").
    # 드롭 칸 아님. [완료]는 열린 질문과 같은 own_words 마감 (run_worker.close_openq), 빈손이면 기록 없이 닫는다.
    {"key": "talk", "name": "이어서 묻기", "desc": "이 글을 놓고 Claude와 이어서 묻는다", "drag": False},
]


def read_jsonl(path):
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except OSError:
        return []


def write_jsonl(path, rows):
    awrite(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def now():
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S%z")


def unique_ts(rows, ts):
    """ts는 카드의 기본키인데 해상도가 1초다 — 같은 초에 두 건이 들어오면 patch·치우기·
    하트비트가 둘을 한 건으로 취급한다. 잠금 안에서 겹치면 1초씩 민다 (여전히 유효한 ISO)."""
    taken = {r.get("ts") for r in rows}
    while ts in taken:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
        ts = (dt + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S%z")
    return ts


def pipe_row(job, h):
    """실행 한 건 + 판정 — 화면은 exit 숫자 대신 이 말을 읽는다 (2026-09-15)."""
    kind, why = pipeline_status.entry_state(job, h["exit"])
    return dict(h, state=kind, why=why)


# 서버가 스스로 죽은 횟수 — 로그는 아무도 안 읽어서 09-17 의 500 을 하루 못 봤다 (2026-09-18).
# 재시작하면 0 이다. 화면의 [알았다] 는 지금까지 본 것만 지운다(`seen`).
ERRS = {"n": 0, "seen": 0, "last": "", "at": "", "path": ""}
ERRS_LOCK = threading.Lock()


def note_error(path, exc):
    with ERRS_LOCK:
        ERRS["n"] += 1
        ERRS["last"] = ("%s: %s" % (type(exc).__name__, exc))[:200]
        ERRS["at"] = now()
        ERRS["path"] = (path or "")[:120]


def errs_snapshot():
    """화면에 줄 것 — 아직 안 본 것이 있을 때만."""
    with ERRS_LOCK:
        if ERRS["n"] <= ERRS["seen"]:
            return None
        return {"n": ERRS["n"] - ERRS["seen"], "last": ERRS["last"],
                "at": ERRS["at"], "path": ERRS["path"]}


def state():
    feed = read_jsonl(FEED)
    reac = read_jsonl(REAC)
    pipe = {}
    try:
        pipe = json.loads(STATUS.read_text())
    except OSError:
        pass
    questions = openq_list()
    try:
        ui_version = int(TEMPLATE.stat().st_mtime)
    except OSError:
        ui_version = 0
    digest = ""
    dg = P / "_workspace/checkpoint/digest.md"
    try:
        digest = dg.read_text()
    except OSError:
        pass
    runs = runs_all()
    return {
        "generated": now(),
        "ui_version": ui_version,
        # 지도 화면 판본 — 열어둔 /map 이 낡으면 스스로 갈아끼운다 (보드와 같은 장치)
        "map_version": map_version(),
        "map_presets": map_presets(),
        "digest": digest,
        "signals": read_signals(),
        "canon": canon_index(),
        "digest_at": datetime.fromtimestamp(dg.stat().st_mtime, KST).strftime("%m/%d %H:%M")
        if dg.exists() else None,
        # kept 도 보낸다 (2026-09-11) — 보관함·"읽는 중 N"·Linkin 소스 후보·피드 조련이 전부 kept 행을 보는데
        # 여기서 걸러 0건이었다. 화면 소비자는 모두 status 로 다시 거른다 (인박스는 pending 만).
        "feed": [r for r in feed if r.get("status") in ("pending", "excluded", "kept")],
        "kept_count": sum(1 for r in feed if r.get("status") == "kept"),
        "reactions_untagged": [dict(r, src="linkedin") for r in reac if r.get("kind") is None],
        "likes_untagged": [dict(r, src="threads") for r in read_jsonl(LIKES) if r.get("kind") is None],
        "assignments": store.read(),
        "structure": structure_counts(),
        "runs": runs,
        "pipeline": {k: [pipe_row(k, h) for h in v.get("history", [])[-3:]] for k, v in pipe.items()},
        "pipeline_ok": {k: [0] + sorted(v.keys()) for k, v in pipeline_status.PENDING_CODES.items()},
        # 덮개 닫혀 미룬 잡 — 실패가 아니라 대기 (2026-10-04). 문구는 pipeline_status.deferred_word 가 정본.
        # pipeline 의 배열 모양은 그대로 두고 따로 보낸다 — 미루기만 하고 아직 한 번도 안 돈 잡은 pipeline 에 빈 배열이다.
        "pipeline_deferred": {k: {"ts": v["deferred"].get("ts", ""), "word": pipeline_status.deferred_word(v)}
                              for k, v in pipe.items() if isinstance(v, dict) and v.get("deferred")},
        "schedule": SCHEDULE,
        "lanes": LANES,
        "questions": sorted(questions, key=lambda x: x["date"])[:8],
        "craft": craft_state(),
        "axis_errors": axis_errors(),
        # 축 한 장의 신선도 — {축: {at, stale, chars, missing}}. stat 만 본다(재료 계산 없음, 실측 0.05초)
        "axis_pages": axis_page.all_page_states(),
        "cli_login": cli_login_ok(runs),
        # 외부 로그인 벽 — 수집기가 쓴 것을 읽기만 (2026-09-16). 벽인 서비스만 온다; 없으면 {}
        "login_walls": worker_status.login_walls(),
        # 서버가 스스로 죽은 것 — 없으면 None (2026-09-18)
        "server_errs": errs_snapshot(),
    }


SIGNALS = P / ".runtime/.feed-signals.json"


BRIEF_WORKER = CODE / "brief_worker.py"
RUN_WORKER = CODE / "run_worker.py"
HUMANIZE_WORKER = CODE / "humanize_worker.py"
import worker_status  # noqa: E402  (하트비트 읽기 — 모델 호출 없음)
from worker_claude import AUTH_LINE  # noqa: E402  (인증 만료 로그 문구 — 생산자 상수 그대로 쓴다)
import application_craft as craft
import pipeline_status  # noqa: E402  (잡별 정상 exit 코드 — 수집기의 3=변화 없음 을 실패로 칠하지 않기, 2026-09-04)
import lineage_scan  # noqa: E402  (⚠️AI 셈 — 지도와 같은 함수, 2026-09-16 ISSUE-202609-MAP-DUP-JUDGEMENT-RULES)
import checkpoint_store as store  # noqa: E402  (큐의 유일한 쓰기 자리 — flock+원자 교체)
import journal_writing
import grounding  # noqa: E402  (마감 근거 후보 — 과거 기록 조회 한 자리, 2026-09-17)
import axis_page  # noqa: E402  (축 한 장의 파일 상태·경로 — 규칙을 두 자리에 두지 않는다)


def spawn(worker, ts, tag, extra=()):
    """워커를 백그라운드로 띄운다 (응답을 막지 않는다). extra 는 --ts 뒤에 붙는 추가 인자.

    워커는 정본을 읽기만 하고 결과를 스키마로 검증한다 — 실패하면 상태를 되돌린다.
    진행 상황은 worker_status 하트비트로 흘러나와 /api/state 에 실린다.
    """
    try:
        # launchd 환경엔 PATH가 없다 — 인터프리터는 절대경로(sys.executable)로
        # start_new_session: 서버를 죽여도(재시작·포트 정리) 워커는 계속 돈다
        subprocess.Popen([sys.executable, str(worker), "--ts", ts, *extra], cwd=str(P),
                         start_new_session=True,
                         stdout=open(P / f".runtime/.{tag}.log", "a"),
                         stderr=open(P / f".runtime/.{tag}.err.log", "a"))
        return True
    except OSError:
        return False


def spawn_brief(ts):
    return spawn(BRIEF_WORKER, ts, "brief_worker")


def spawn_run(ts):
    return spawn(RUN_WORKER, ts, "run_worker")


def run_alive(ts):
    """이 배정의 워커가 지금 살아 있나 — (살았나, 하트비트). run_worker와 같은 판정."""
    beat = runs_all().get(ts)
    if not (beat and beat.get("state") == "running" and beat.get("pid")):
        return False, beat
    try:
        os.kill(beat["pid"], 0)
        return True, beat
    except (OSError, ProcessLookupError):
        return False, beat


def kill_run(ts, why="강제 재시작"):
    """멈춘 워커를 끊는다. 하트비트를 실패로 닫아 화면이 '실행 중'에 갇히지 않게."""
    alive, beat = run_alive(ts)
    if alive:
        try:
            os.kill(beat["pid"], 15)
        except (OSError, ProcessLookupError):
            pass
        time.sleep(0.4)
    if beat:
        beat["state"], beat["phase"] = "failed", f"사용자가 끊음 ({why})"
        beat["error"], beat["ended"] = why, time.time()
        try:
            awrite(worker_status._path(ts), json.dumps(beat, ensure_ascii=False))
        except OSError:
            pass


def _transcript_path(sid):
    """claude -p 가 남기는 세션 전문 — ~/.claude/projects/<프로젝트 슬러그>/<sid>.jsonl."""
    if not sid or not re.fullmatch(r"[0-9a-f-]{36}", sid):
        return None
    hits = list((Path.home() / ".claude" / "projects").glob(f"*/{sid}.jsonl"))
    return hits[0] if hits else None


def session_events(sid, limit=300):
    """전문을 사람이 보는 줄로. 모델 문장(claude) / 도구 호출(tool, worker_claude._label 로 번역) /
    도구 결과(result, 앞 240자). thinking 은 뺀다 — 화면은 '무엇을 했나'지 속생각이 아니다."""
    f = _transcript_path(sid)
    if not f:
        return [], False
    try:
        from worker_claude import _label
    except Exception:
        _label = lambda n, i: n
    out = []
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t, at = ev.get("type"), ev.get("timestamp", "")
                msg = ev.get("message") or {}
                blocks = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(blocks, list):
                    continue
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    if t == "assistant" and b.get("type") == "text" and (b.get("text") or "").strip():
                        out.append({"who": "claude", "at": at, "text": b["text"].strip()})
                    elif t == "assistant" and b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        raw = json.dumps(inp, ensure_ascii=False)
                        out.append({"who": "tool", "at": at, "text": _label(b.get("name", ""), inp),
                                    "raw": f"{b.get('name', '')} {raw[:400]}"})
                    elif t == "user" and b.get("type") == "tool_result":
                        c = b.get("content")
                        if isinstance(c, list):
                            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                        c = re.sub(r"\s+", " ", str(c or "")).strip()
                        if c:
                            out.append({"who": "result", "at": at, "text": c[:240], "more": len(c) > 240})
    except OSError:
        return [], False
    return out[-limit:], True


def session_detail(ts):
    beat = runs_all().get(ts)
    if not beat:
        return None
    alive, _ = run_alive(ts)
    events, has = session_events(beat.get("sid"))
    return {"ok": True, "beat": beat, "alive": alive, "events": events, "has_transcript": has}


GOTO = {}   # 알림 클릭이 남긴 "이 화면으로" {tab, sub, ts, at} — /api/state 에 120초만 실려 나간다 (2026-09-07)
REVIVE_EVERY, REVIVE_COOLDOWN, REVIVE_MAX = 90, 180, 3
# 마무리 턴(closing_at)이 이만큼 지나도록 워커 하트비트가 없으면 끊긴 것으로 본다 —
# 화면 draftStuck(checkpoint_board.html)의 15분과 같은 값이어야 한다.
CLOSE_STUCK = 15 * 60
_revived = {}   # ts -> 마지막으로 이 감시가 띄운 시각 (같은 건을 연달아 띄우지 않기)


def cli_login_ok(runs):
    """claude -p 가 지금 인증 만료 상태인가 — 사전 검사(claude auth status --json) 는 뗐다.

    무효 토큰에도 loggedIn:true 를 줘서 2026-09-11 사고(자격증명 있음·refresh 실패)를 못 잡았기
    때문 — 실제로 잡히는 자리는 result 이벤트의 "Failed to authenticate" 뿐이라(worker_claude.run
    이 하트비트 로그에 AUTH_LINE 그대로 남긴다) 그 흔적을 본다. 문구는 worker_claude.AUTH_LINE
    한 자리 — 양쪽 계약(2026-09-11 Opus 지적, 문구가 갈라져 있으면 한쪽만 고쳤을 때 조용히 죽는다).
    runs 는 호출자가 이미 읽어 넘긴다(worker_status.read_all() — subprocess·추가 파일 읽기 없음).

    판정: 최근 LOGIN_FAIL_WINDOW(30분) 안에 AUTH_LINE 이 찍힌 실패가 있고, 그 마지막 실패 시각
    이후에 성공(state done)으로 끝난 run 이 없으면 False. 재로그인 뒤 [다시]로 하나만 성공해도
    바로 True 로 돌아간다 — 창이 다 지나가길 기다리지 않는다.
    """
    now_t = time.time()
    last_fail = 0.0
    last_ok = 0.0
    for d in runs.values():
        ended = d.get("ended") or d.get("updated") or 0
        if d.get("state") == "failed" and any(AUTH_LINE in ln for ln in (d.get("log") or [])):
            if now_t - ended <= LOGIN_FAIL_WINDOW:
                last_fail = max(last_fail, ended)
        elif d.get("state") == "done":
            last_ok = max(last_ok, ended)
    return not (last_fail and last_fail > last_ok)


def revive_once():
    """지시는 있는데 안 돌고 있는 건을 다시 띄운다 — 끊겨도 결과가 나오게 (2026-09-07 사용자 지시).

    대상: (1) 도는 중이라 적혀 있는데 워커가 죽은 건, (2) 지시가 있는데 하트비트가 없는 건
    (띄우기 실패·형식 불합격으로 pending 에 되돌아간 건 — 2026-09-01 지도 후속 2건이 이렇게 6일을 묵었다),
    (3) 피드 판정은 됐는데 정리(brief)가 없는 건. 한 건 최대 3회(`attempts`), 마지막 시작 뒤 3분 쉼.

    로그인이 만료된 동안은 아무것도 다시 안 띄운다 — attempts 만 태우고 다 실패한다 (2026-09-11).
    """
    with REVIVE_LOCK:   # 배경 revive_loop 와 POST /api/revive 가 같은 _revived 를 본다 — 같은 건을 두 번 띄우지 않기
        return _revive_once()


def _revive_once():
    now_t = time.time()
    try:
        runs = runs_all()
        rows = store.read()
    except Exception:
        return []
    if not cli_login_ok(runs):
        return []
    out = []
    for a in rows:
        ts, src, st = a.get("ts"), a.get("source"), a.get("status")
        if src not in ("feed", "map", "openq") or st not in ("pending", "briefed", "in_progress"):
            continue
        if int(a.get("attempts") or 0) >= REVIVE_MAX or now_t - _revived.get(ts, 0) < 600:
            continue
        alive, beat = run_alive(ts)
        if alive:
            continue
        started = a.get("started_at") or a.get("ask_at") or ts or ""
        try:
            t0 = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            t0 = 0
        if now_t - t0 < REVIVE_COOLDOWN:
            continue
        ask = (a.get("ask") or "").strip()
        if st == "in_progress" or ask:
            job = spawn_run
        elif src == "feed" and st == "pending" and not a.get("brief"):
            job = spawn_brief
        else:
            continue
        if job(ts):
            _revived[ts] = now_t
            out.append((ts, job.__name__))
    return out


def reap_closing():
    """끊긴 마무리 턴을 닫는다 (2026-09-22 실측: 09-17 13:53 카드가 5일째 "초안 만드는 중").

    워커가 proposal 도 close_error 도 안 쓰고 죽으면 closing_at 을 되돌리는 경로가 없어
    카드가 영원히 도는 것처럼 보인다. 판정은 화면 draftStuck 과 같다 — closing_at 있음 +
    close-<ts> 하트비트 죽음 + CLOSE_STUCK 초과. proposal 유무는 보지 않는다([아니다] 재작성
    중에는 옛 proposal 이 남아 있다). 닫으면 화면이 실패 줄을 그리고 사용자가 다시 [완료]를 누른다.
    """
    now_t = time.time()
    try:
        rows = store.read()
    except Exception:
        return []
    out = []
    for a in rows:
        ts = a.get("ts")
        if not ts or not a.get("closing_at") or a.get("applied_at"):
            continue
        if run_alive("close-" + ts)[0]:
            continue
        try:
            t0 = datetime.fromisoformat(str(a["closing_at"]).replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            t0 = 0
        if t0 and now_t - t0 < CLOSE_STUCK:
            continue
        if store.patch(ts, closing_at=None, close_error="문장 만드는 워커가 끊겼다"):
            out.append(ts)
    return out


def revive_loop():
    time.sleep(20)   # 서버가 먼저 뜨고 나서
    while True:
        try:
            for ts, what in revive_once():
                print(f"다시 띄움 {what} {ts}", flush=True)
            for ts in reap_closing():
                print(f"끊긴 마무리 턴 닫음 {ts}", flush=True)
        except Exception as e:   # 감시가 죽으면 아무도 다시 안 띄운다 — 삼키고 계속
            print(f"revive 오류: {e}", flush=True)
        time.sleep(REVIVE_EVERY)


def spawn_apply(ts):
    """이미 나온 결과에서 반영안만 뽑는다 (결과·상태는 그대로)."""
    try:
        subprocess.Popen([sys.executable, str(RUN_WORKER), "--ts", ts, "--apply-only"],
                         cwd=str(P), start_new_session=True,
                         stdout=open(P / ".runtime/.run_worker.log", "a"),
                         stderr=open(P / ".runtime/.run_worker.err.log", "a"))
        return True
    except OSError:
        return False


# 아래 둘은 검사-후-표시(연타 방지)다 — 닿는 경로가 POST 전체와 GET /api/feed_read 뿐이라
# WRITE_LOCK 아래에서만 만져진다. 다른 곳에서 부르게 되면 그 자리에도 WRITE_LOCK 을 건다.
LONG_SPAWNED = {}   # feed id -> 띄운 시각. 같은 글을 연타해도 워커 한 번 (표시 전용, 재시작하면 비워진다)
AXIS_SPAWNED = {}   # 축 한 장 마지막 띄운 시각 (연타 방지)
HUMANIZE_SPAWNED = {}   # 보관한 글 다듬기 마지막 띄운 시각 (연타 방지)


def long_running(item_id):
    beat = runs_all().get(f"feed-long-{item_id}")
    return bool(beat and beat.get("state") == "running")


def spawn_long(item_id, force=False):
    """읽기 판 요점 워커 — `summarize_feed.py --long <id>` (models.json feed.long, 원문 발췌만, 정본 안 건드림).

    2026-09-04 사용자 요청: 한 줄 요약으로는 글이 무엇을 말했는지 못 본다("5가지가 뭔데?").
    글을 누르면 원문 전체 + 요점 목록 + 축과의 관계를 오른쪽 아래 읽기 판에 펼친다. 모델은
    서버가 아니라 워커가 부른다(서버 원칙). 도는 중이거나 2분 안에 띄운 건 다시 안 띄운다."""
    if long_running(item_id) or (not force and time.time() - LONG_SPAWNED.get(item_id, 0) < 120):
        return False
    LONG_SPAWNED[item_id] = time.time()
    try:
        subprocess.Popen([sys.executable, str(CODE / "summarize_feed.py"), "--long", item_id],
                         cwd=str(P), start_new_session=True,
                         stdout=open(P / ".runtime/.summarize_feed.log", "a"),
                         stderr=open(P / ".runtime/.summarize_feed.err.log", "a"))
        return True
    except OSError:
        return False


def axis_running(axis):
    beat = runs_all().get("axis-%s" % axis)
    return bool(beat and beat.get("state") == "running")


PLAIN_SPAWNED = {}   # 읽기용 풀어쓰기 마지막 띄운 시각 (코드별 연타 방지)
PLAIN_COOLDOWN, PLAIN_MAX = 60, 2


def plain_running(code):
    return run_alive("plain-" + code)[0]


def spawn_plain(code, force=False):
    """읽기용 풀어쓰기 워커(canon_plain.py) — 도는 중이면 안 띄운다. force 가 아니면: 60초 쿨다운 ·
    최근 10분 안 실패면 안 띄움(GET 이 60초마다 실패 워커를 되살리는 루프 방지) · 동시 2."""
    runs = runs_all()
    if plain_running(code):
        return False
    beat = runs.get("plain-" + code) or {}
    if not force:
        if time.time() - PLAIN_SPAWNED.get(code, 0) < PLAIN_COOLDOWN:
            return False
        if beat.get("state") == "failed" and time.time() - (beat.get("ended") or 0) < 600:
            return False
        if sum(1 for r in runs.values() if r.get("kind") == "plain" and r.get("state") == "running") >= PLAIN_MAX:
            return False
    PLAIN_SPAWNED[code] = time.time()
    try:
        subprocess.Popen([sys.executable, str(CODE / "canon_plain.py"), code] + (["--force"] if force else []),
                         cwd=str(P), start_new_session=True,
                         stdout=open(P / ".runtime/.canon_plain.log", "a"),
                         stderr=open(P / ".runtime/.canon_plain.err.log", "a"))
        return True
    except OSError:
        return False


def plain_fields(code, it):
    """canon_block 응답의 읽기용 풀어쓰기 몫 — 화면은 표시만. 단위 셈은 워커와 같은 `canon_plain.units_of`.

    낡은 칸이 있고 워커가 안 돌면 **띄우기만** 한다(응답은 안 막는다). `plain_units` 는 줄 sha → 단위 key 라
    화면이 칸의 sha 로 풀어쓴 단락을 찾는다. 답해야 하는 ⚠️AI 줄은 단위에 없다(늘 원문).
    """
    units = canon_plain.units_of(it)
    cache = canon_plain.load_cache(code).get("units") or {}
    plain, by_sha = {}, {}
    for u in units:
        for sha in u["shas"]:
            by_sha[sha] = u["key"]
        if u["key"] in cache:
            plain[u["key"]] = {"text": cache[u["key"]].get("plain") or "", "at": cache[u["key"]].get("at")}
    missing = [u for u in units if u["key"] not in plain]
    running = plain_running(code)
    spawned = spawn_plain(code) if missing and not running else False
    beat = runs_all().get("plain-" + code) or {}
    err = beat.get("error") if beat.get("state") == "failed" else None
    return {"plain": plain, "plain_units": by_sha, "plain_missing": len(missing),
            "plain_pending": bool(missing) and (running or spawned),
            "plain_error": err if (missing and not (running or spawned)) else None}


def spawn_axis(axis, mode="open"):
    """축 한 장 — 재료는 코드가 모으고(결정적) 문장은 워커가 잇는다(1~2분, models.json axis.page). 도는 중이면 안 띄운다.

    mode: `open` 처음 쓰기(끝나면 Obsidian 으로 연다) · `stale` 뒤에서 갱신 · `force` 재료가 같아도 다시.
    stale·force 는 **안 연다** — 사용자가 다른 일을 하는 중에 창을 앞으로 끌어오지 않는다 (2026-09-10).
    """
    if axis_running(axis) or time.time() - AXIS_SPAWNED.get(axis, 0) < 20:
        return False
    AXIS_SPAWNED[axis] = time.time()
    flag = {"open": "--open", "stale": "--if-stale", "force": "--force"}.get(mode, "--open")
    try:
        subprocess.Popen([sys.executable, str(CODE / "axis_page.py"), axis, flag],
                         cwd=str(P), start_new_session=True,
                         stdout=open(P / ".runtime/.axis_page.log", "a"),
                         stderr=open(P / ".runtime/.axis_page.err.log", "a"))
        return True
    except OSError:
        return False


def axis_errors():
    """계약에 걸려 **안 써진** 축 한 장 — 범례 버튼 옆 붉은 점의 원천. 성공하면 워커가 이 파일을 지운다."""
    out = {}
    try:
        for f in (P / "_workspace/axis").glob("*.error.txt"):
            out[f.name[:-len(".error.txt")]] = " ".join(f.read_text(encoding="utf-8").split())[:200]
    except OSError:
        pass
    return out


def humanize_running(ts):
    beat = runs_all().get("humanize-%s" % ts)
    return bool(beat and beat.get("state") == "running")


def shelve_publish(row, at):
    """다시 [완료]한 글의 옛 다듬은 판을 publish_history 로 민다 — 바꾼 게 있으면 True (순수 함수, 2026-10-04).

    [이어서 쓰기]로 연 글이 다시 닫히면 humanize 가 새 판을 쓴다. humanize_worker.save 는 publish 에
    병합하므로 비워 두지 않으면 옛 edited_at·hidden·run_id 가 새 판에 묻는다("내가 고친 뒤" 거짓 표시).
    첫 [완료]는 publish 가 없어 아무 일도 없다. 숨김·오류만 있던 판(본문 없음)은 보존할 게 없어 그대로 비운다."""
    pub = row.get("publish")
    if not isinstance(pub, dict) or not pub:
        return False
    if (pub.get("text") or "").strip():
        row.setdefault("publish_history", []).append({**pub, "shelved_at": at})
    row.pop("publish", None)
    return True


def archive_publish(ts):
    def _do(rows):
        row = next((r for r in rows if r.get("ts") == ts), None)
        if not row or not shelve_publish(row, now()):
            return store.STOP
    store.mutate(_do)


def spawn_humanize(ts):
    """보관한 글 다듬기 — humanize_worker.py <ts> (models.json humanize, humanize-korean 스킬 한 번, 2~4분).

    2026-09-16 사용자 결정: 보관하면 자동으로 한 번 돌고, 카드의 [다시 다듬기]로 또 돌린다.
    도는 중이거나 20초 안에 띄운 건 다시 안 띄운다 (spawn_axis 와 같은 연타 방지).
    """
    if humanize_running(ts) or time.time() - HUMANIZE_SPAWNED.get(ts, 0) < 20:
        return False
    HUMANIZE_SPAWNED[ts] = time.time()
    try:
        subprocess.Popen([sys.executable, str(HUMANIZE_WORKER), ts], cwd=str(P),
                         start_new_session=True,
                         stdout=open(P / ".runtime/.humanize_worker.log", "a"),
                         stderr=open(P / ".runtime/.humanize_worker.err.log", "a"))
        return True
    except OSError:
        return False


def spawn_digest():
    """인박스 전체 묶음 다시 — summarize_feed.py --digest (models.json feed.synth 한 번). 도는 중이면 안 띄운다."""
    beat = runs_all().get("feed-digest")
    if beat and beat.get("state") == "running":
        return False
    try:
        subprocess.Popen([sys.executable, str(CODE / "summarize_feed.py"), "--digest"],
                         cwd=str(P), start_new_session=True,
                         stdout=open(P / ".runtime/.summarize_feed.log", "a"),
                         stderr=open(P / ".runtime/.summarize_feed.err.log", "a"))
        return True
    except OSError:
        return False


def feed_read(item_id, retry=False):
    """읽기 판 한 건 — 상태 무관(pending·kept·dropped 다 읽는다). long 이 없으면 워커를 띄운다.
    retry 는 실패 기록(long.error)을 지우고 다시 띄운다. decided_at 은 안 건드린다(판별이 아니다)."""
    rows = read_jsonl(FEED)
    row = next((r for r in rows if r.get("id") == item_id), None)
    if not row:
        return {"ok": False, "error": "없는 글"}
    # 댓글 없이 뽑힌 옛 Reddit 요점은 낡은 것 — 댓글 받아 다시 (2026-09-04, comments 키 유무로 신·구 분기)
    stale = row.get("source") == "reddit" and row.get("long") and "comments_at" not in row
    if (retry or stale) and row.get("long"):
        row.pop("long", None)
        if row.get("comments_error"):   # 댓글 못 받았던 건은 댓글도 다시
            for k in ("comments_error", "comments_at", "comments"):
                row.pop(k, None)
        write_jsonl(FEED, rows)
        retry = True
    spawned = spawn_long(item_id, force=retry) if not row.get("long") else False
    return {"ok": True, "row": row, "running": spawned or long_running(item_id)}


def tail_log(key, n=40):
    """로그 꼬리 n줄 — 화이트리스트 키만. 없으면 빈 목록."""
    ent = LOGS.get(key)
    if not ent:
        return None
    name, path = ent
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    try:
        at = int(path.stat().st_mtime)
    except OSError:
        at = None
    return {"key": key, "name": name, "path": str(path.relative_to(P)),
            "at": at, "lines": lines[-n:]}


def restart_server():
    """launchd가 이 서비스를 keepalive로 붙들고 있다 — 죽이면 곧바로 새로 뜬다.

    코드를 고친 뒤 터미널을 열지 않고 앱에서 되살리는 자리. 워커는 start_new_session
    으로 떠 있어 서버가 죽어도 계속 돈다.
    """
    def bye():
        time.sleep(0.4)
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()


def map_version():
    """지도 템플릿·프리셋 mtime — 둘 중 하나만 바뀌어도 화면을 새로 받아야 한다."""
    v = 0
    for rel in ("personal_map_template.html", "map-presets.json"):
        try:
            v = max(v, int((CODE / rel).stat().st_mtime))
        except OSError:
            pass
    return v


def map_presets():
    """지도·구조 탭이 공유하는 지시문 등록부 (scripts/map-presets.json)."""
    try:
        return json.loads((CODE / "map-presets.json").read_text())["presets"]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def axis_names():
    """축 코드 → 이름. trend-interests.md '## 관심 축' 표를 런타임에 읽는다 (journal_writing.axes 와 같은 원천).
    옛 상수 AXN 이 c1be9b0 에서 지워져 피드 [최종 제출]→insights.md 가 NameError 로 죽어 있었다 (2026-09-10 실사격에서 발견)."""
    try:
        return {k: v["name"] for k, v in journal_writing.axes(P).items()}
    except (OSError, ValueError, KeyError):
        return {}


def insights_insert(a, text, question=None, conclusion=None):
    """내 문장(text)을 insights.md 축 섹션에 쌓는다 — ⚠️AI 마킹 없음, 기계적 삽입.

    워커의 요지는 ⚠️AI로 재료로 붙고, question이 있으면 '#### 교차 질문'으로 남아
    어느 물음에 대한 답인지 보인다. 반환: 축 코드."""
    item = next((f for f in read_jsonl(FEED) if f.get("id") == a.get("id")), {})
    ax = (item.get("axes") or item.get("axes_hint") or ["-"])[0]
    target = P / "visionary/linkedin/insights.md"
    body = target.read_text()
    AXN = axis_names()
    head = f"## {ax}. {AXN.get(ax, '축 없음')}"
    lines = body.splitlines()
    # 같은 코드의 절이면 이름이 달라도 거기에 붙인다 — 이름이 바뀌었다고 절을 새로 열면 한 축이 두 군데로 갈라진다
    # (2026-09-11 실측: "## C. Salesforce·Agentforce 현장"과 "## C. Salesforce/Agentforce 현장 …" 둘이 생겨
    # axis_page.insights_mine 이 앞 절만 읽었다). 여러 개면 마지막 절 = 최근에 쓰던 자리.
    hits = [i for i, l in enumerate(lines) if l.startswith(f"## {ax}. ")]
    if not hits:            # 그 축 섹션이 없으면 맨 끝에 새로 연다
        lines += ["", f"{head} (1)", ""]
        at = len(lines)
    else:
        h = hits[-1]
        at = len(lines)
        for i in range(h + 1, len(lines)):
            if lines[i].startswith("## "):
                at = i
                break
        n = lines[h].rsplit("(", 1)
        if len(n) == 2 and n[1].rstrip().endswith(")"):
            try:
                lines[h] = f"{n[0]}({int(n[1].rstrip()[:-1]) + 1})"
            except ValueError:
                pass
    stamp = datetime.now(KST).strftime("%Y-%m-%d")
    fm = a.get("for_me") or {}
    gist = (fm.get("answer") or a.get("result") or "").strip()
    blk = [f"### {a.get('title', '')[:80]} ({stamp} 대시보드)", ""]
    if item.get("post_url"):
        blk.append(f"- 원본: {item['post_url']}")
    blk += [f"- 축: {ax}({AXN.get(ax, '—')}) / 카드: {a['ts']}", ""]
    if gist:
        blk += ["#### 관점 요지", f"> ⚠️AI 워커 정리 — {esc_none(gist)}", ""]
    if question:
        blk += ["#### 교차 질문", f"> ⚠️AI {question}", ""]
    if conclusion:
        blk += ["#### 결론 (대화 전체)", f"> ⚠️AI {esc_none(conclusion['line'])}",
                f"> 경위(⚠️AI): {esc_none(conclusion.get('trail') or '')}", ""]
    blk += ["#### 내 생각", text, ""]
    lines[at:at] = blk
    awrite(target, "\n".join(lines).rstrip() + "\n")
    store.patch(a["ts"], my_take=text, applied_at=now(),
                seen_at=a.get("seen_at") or now(),
                applied_note=f"insights.md · {ax}축 섹션 (내 문장)")
    return ax


APPLY_ALLOW = ["career/decisions.md", "career/work-decisions.md", "visionary/linkedin/insights.md"]


def insert_apply_line(ap, mine, limit=250):
    """반영안(apply) 자리에 사용자 문장 한 줄 삽입 — 기계적 계약: 화이트리스트 파일 · 앵커 정확히 1회.
    반환 (err, note). /api/apply_line 과 같은 규칙 (완료 흐름이 재사용, 2026-09-03)."""
    if not isinstance(ap, dict) or ap.get("file") not in APPLY_ALLOW:
        return "허용 밖 파일 — 세션에서", None
    target = P / ap["file"]
    try:
        body = target.read_text()
    except OSError:
        return "파일을 읽을 수 없음", None
    anchor = (ap.get("anchor") or "").strip()
    lines = body.splitlines()
    hits = [i for i, l in enumerate(lines) if l.strip() == anchor]
    if len(hits) != 1:
        return f"앵커가 {len(hits)}회 일치 — 세션에서 직접", None
    if len(mine) > limit:
        return f"정본엔 한 줄로 — {limit}자 이내", None
    stamp = datetime.now(KST).strftime("%Y-%m-%d")
    lines.insert(hits[0] + 1, f"- {mine} (대시보드 문답 {stamp})")
    awrite(target, "\n".join(lines) + ("\n" if body.endswith("\n") else ""))
    return None, f"{ap['file']} · {anchor[:40]} (내 문장)"


def thread_of(ts, rows=None):
    """(root, 시간순 스레드). 없으면 (None, [])."""
    rows = store.read() if rows is None else rows
    by = {r["ts"]: r for r in rows}
    if ts not in by:
        return None, []
    def root_of(r):
        c = r
        while c.get("parent_ts") and by.get(c["parent_ts"]):
            c = by[c["parent_ts"]]
        return c["ts"]
    root = by[root_of(by[ts])]
    return root, sorted([r for r in rows if root_of(r) == root["ts"]], key=lambda r: r["ts"])


def last_apply(thread):
    return next((r["apply"] for r in reversed(thread) if isinstance(r.get("apply"), dict) and r["apply"].get("file")), None)


def harvest_close(root):
    """빈 칸 [완료]도 **거두기**인 건 — 열린 질문 대화와 피드 전 레인.

    2026-09-17: 옛 이름은 own_words_close 였고 워커의 '문장 고르기' 마감과 한 쌍이었다. 그 마감은
    `refine` 계약으로 합쳐 지웠고, 여기 남은 것은 "답 칸이 비어도 마무리 턴을 띄운다"는 판별뿐이다.
    글쓰기(journal_writing)는 아니다 — 편집 본문을 카드에 보관하는 게 목적지다."""
    if journal_writing.enabled(root):
        return False
    return root.get("source") in ("openq", "feed")


def my_turns(thread):
    """이 스레드에서 사용자가 친 문장 수 — 첫 지시(ask)와 답 칸 입력(answers, final 제외).
    빈 칸 [완료]를 '거두기'로 볼 수 있는지의 판별 (2026-09-10)."""
    n = 0
    for r in thread:
        if (r.get("ask") or "").strip():
            n += 1
        n += sum(1 for x in (r.get("answers") or []) if (x.get("a") or "").strip() and not x.get("final"))
    return n


def close_eligible(root, thread, text):
    """[완료]가 정본에 닿는 건인가 — 그러면 마무리 턴(run_worker --close)이 대화에서 내 문장을 거둔 뒤 넣는다.

    2026-09-10: 빈 칸 [완료]도 **거두기**다. 실측 완료 12건 중 8건이 빈 칸 [완료] → "닫기만 — 반영 없음"
    이었다 — 사용자는 [완료]를 "대화 끝"으로 누르고 답 칸엔 안 쓴다. own_words 마감(피드·열린 질문)이고
    사용자 턴이 하나라도 있으면 text 가 비어도 마무리 턴을 띄운다. 글쓰기·사용자 턴 없는 카드는 그대로 닫는다.
    옛 흐름(map·AI 초안)은 text 가 있어야 한다 — 그쪽은 모델이 문장을 쓰기 때문."""
    if journal_writing.enabled(root) or not any(r.get("status") == "done" for r in thread):
        return False
    if not text and not (harvest_close(root) and my_turns(thread)):
        return False
    ap = last_apply(thread)
    if ap and ap.get("file") in APPLY_ALLOW:
        return True
    return root.get("source") in ("feed", "openq") or bool(root.get("source") == "map" and root.get("path"))


def _dest_detail(dest, mine):
    """dest 문자열('career/xxx.md › 자리')에서 파일 경로와 자리를 떼어낸다 — [최종 제출] 뒤 결과 팝업이
    "어디에 붙었나"를 보여주는 데만 쓴다. 문장은 만들지 않는다 (넣은 그대로 mine)."""
    d = {"inserted": mine}
    head = (dest or "").split(" › ")
    rel = head[0].strip()
    if rel.endswith(".md") and (P / rel).exists():
        d["path"] = str(P / rel)
        if len(head) > 1:
            d["anchor"] = head[1].strip()
    return d


_craft_cache = {"key": None, "prog": None}


def craft_progress():
    """자소서 칸 진행 — 파일 mtime 이 같으면 다시 파싱하지 않는다 (구조 탭·개인 탭이 같이 쓴다)."""
    f = P / craft.REL
    try:
        key = int(f.stat().st_mtime)
    except OSError:
        return []
    if _craft_cache["key"] != key:
        _craft_cache.update(key=key, prog=craft.progress(P))
    return _craft_cache["prog"] or []


def ac_items():
    """구조 탭 판단 열의 AC 상자 재료 — 다른 상자와 같은 필드 (id·title·line·ai·path…).

    2026-09-17 (설계 ④): 자소서 craft 는 lineage 노드가 없어 구조 판에서 안 보였다. 파싱은
    `canon_parse`, 진행 숫자는 `craft_progress()` 한 자리 — 개인 탭과 다른 숫자가 나오지 않게.
    """
    prog = {b["code"]: b for b in craft_progress()}
    out = []
    for it in canon_parse.parse_file("AC", P):
        b = prog.get(it["code"]) or {}
        out.append({"id": it["code"], "title": it["title"], "path": craft.REL,
                    "date": "", "maturity": it["maturity"],
                    "scene": None, "has_scene_slot": False,
                    "ai": canon_parse.pending_items(it),
                    "line": canon_parse.key_line(it) or canon_parse.first_body(it),
                    "filled": b.get("filled", 0), "total": b.get("total", 0),
                    "in": 0, "out": 0})
    return out


def canon_block_api(code):
    """`/api/canon_block?code=` — 항목 전문(칸·표기·누적 답·출처 줄·이력·손 필요) + 읽기용 풀어쓰기 캐시.

    lineage 재생성은 붙이지 않고, 모델은 이 요청 안에서 안 부른다 (issue-log: 느린 GET). 풀어쓰기 칸이 낡았으면
    워커를 **띄우기만** 하고 바로 응답한다(`plain_fields`, 2026-09-22). 화면은 이걸 **표시만** 한다.
    """
    it = canon_parse.item(code, P)
    if it is None:
        return {"error": "그 항목을 못 찾았다: %s" % code}
    def ent(e):
        return {"name": e.get("name"), "marks": e["marks"], "value": e["value"], "text": e["vtext"],
                "date": e["date"], "source": e["source_line"], "sha": e["sha"],
                "pending": bool(e["pending"]), "form": e["form"],
                "entries": [{"text": x["vtext"], "marks": x["marks"], "date": x["date"],
                             "source": x["source_line"], "sha": x["sha"], "pending": bool(x["pending"])}
                            for x in (e.get("entries") or [])]}
    # draft_shas = 서랍에서 **미리 쓴 문장을 만들 수 있는** ⚠️AI 줄 (칸 이름을 물려받는 것만).
    # 화면이 같은 셈을 따로 하면 초안 카드와 어긋나 다시 만들기가 멈추지 않는다 — 서버 한 자리.
    _tg, _shas = draft_targets(it)
    out = plain_fields(code, it)
    out.update({"code": it["code"], "title": it["title"], "maturity": it["maturity"], "path": it["path"],
            "slots": [ent(s) for s in it["slots"]], "history": [ent(h) for h in it["history"]],
            "pending": canon_parse.pending_items(it),
            "draft_shas": sorted({x for v in _shas.values() for x in v}),
            # 바깥 인용 줄 — 서랍은 판단만 다룬다. 화면이 같은 셈을 따로 하지 않게 여기서 준다.
            "cite_shas": sorted({x["sha"] for x in it["pending"] if is_citation(x)})})
    return out


def outside_items():
    """항목 밖(코드 없는 자리)의 ⚠️AI 표식 — 서랍에서 **읽기만** 한다. 저장 경로를 만들지 않는다.

    id 는 서버가 발급하는 읽기 전용 위치(`outside:<파일>:<줄 sha>`)다 — 모델·화면이 경로를 만들지 않게.
    """
    out = []
    for rel in list(canon_parse.REL.values()) + ["career-frame.md"]:
        try:
            text = (P / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        inside = set()
        pref = next((k for k, v in canon_parse.REL.items() if v == rel), None)
        if pref:
            for it in canon_parse.parse_text(text, pref, path=rel):
                inside |= set(range(it["span"][0], it["span"][1]))
        for i, ln in enumerate(text.splitlines()):
            if i in inside or ln.startswith(">"):
                continue
            if canon_parse.BLOCK_RE.match(ln) or re.match(r"^[-*][ \t]+(?:\*\*[^*\n]+\*\*[ \t]*)?⚠️AI", ln):
                out.append({"id": "outside:%s:%s" % (rel, canon_parse.sha_of(ln)), "file": rel,
                            "line": i + 1, "text": " ".join(ln.split())[:300]})
    return out


def canon_note_fold(code, sha, today, slot_name):
    """⚠️AI 노트를 **접는다** — 불릿 본문을 지우고 그 문장을 대상 칸 밑 `출처:` 줄로 옮긴다.

    2026-09-17 (설계 ④): 지우지 않는다. 내 문장이 칸에 들어간 뒤에도 "그 문장이 어디서 왔나"가
    남아야 한다. 접히면 그 불릿은 손 필요에서 빠진다(⚠️AI 표기가 출처 줄 안으로 들어가므로 파서는
    그것을 pending 으로 세지 않는다 — 출처 줄은 판단 문장이 아니다). 반환 err 또는 None.

    2026-09-17 저녁 (Codex Q8 재검토): 옛 구현은 `slot_name` 의 위치(`at`)를 따로 계산해 `at > b`
    (뒤에 하위 항목이 이미 붙었다) 이면 `lines[a:b]` 를 통째로 지웠다 — pending 이 **칸 줄 자체**
    (`- **한계** ⚠️AI: 값`)일 때 이 삭제가 `**한계**` 이름까지 지웠다(`canon_ai_answer` 에서 이미
    한 번 잡은 것과 같은 버그, `canon_note_fold` 에는 안 옮겨져 있었다 — `draft_apply` 가 이 함수를
    쓰므로 서랍 [반영] 이 정본 칸을 날릴 뻔했다, 실사격 전에 발견). `note["span"]` 은 그 불릿 **자기
    줄만**이라 `at` 없이 `lines[a:b]` 교체만으로 충분하다 — 뒤에 붙은 항목은 span 밖이라 안 건드린다.
    """
    path, lines, s0, e0 = canon_parse.locate(code, P)
    if path is None:
        return "항목을 못 찾았다: " + code
    it = canon_parse.item(code, P)
    note = next((x for x in it["pending"] if x["sha"] == sha), None)
    if note is None:
        return "그 ⚠️AI 줄을 못 찾았다 — 파일이 바뀌었을 수 있다. 새로고침."
    a, b = note["span"]
    pad = re.match(r"^[ \t]*", lines[a]).group(0)
    body = str(note.get("vtext") or "") or re.sub(
        r"^-\s*", "", " ".join(x.strip() for x in lines[a:b] if not canon_parse.SOURCE_RE.match(x)))
    body = " ".join(body.replace(canon_parse.PENDING_MARK, "").split()).lstrip(": ")
    keep = [l for l in lines[a + 1:b] if canon_parse.SOURCE_RE.match(l)]   # 기존 출처 줄은 살린다
    if note.get("name"):   # 칸 줄 자체 — 이름은 남기고 값은 비운다(문장은 이미 하위 항목에 있다)
        head = ["%s- **%s**:\n" % (pad, note["name"])]
        src_pad = pad + "  "
    else:                  # 칸 밑 하위 불릿·블록 표식 — 통째로 접는다
        head = []
        src_pad = pad
    src = "%s출처: AI 노트 %s 원문 — %s\n" % (src_pad, today, " ".join(body.split())[:300])
    lines[a:b] = head + [src] + keep
    awrite(path, "".join(lines), encoding="utf-8")
    return None


FEED_CODE_RE = re.compile(r"\b(?:WD|AC|C)-\d+\b")   # 내 말에서 코드를 알아본다 (lineage_scan.CODE_RE 와 별개 — 접두어가 다르다)
FEED_TARGET_CAP = 18                            # 피드 갈래 목적지 후보 상한 (insights 포함)
FEED_CODE_CAP = 3                               # 그중 코드는 근거 강한 순 셋까지 (2026-09-17 사용자 결정)


def my_turns_text(root, thread):
    """스레드에서 **사용자가 친** 글만 이어 붙인다 (Claude 답·[최종 제출] 문장 제외)."""
    out = []
    for r in (thread or [root]):
        if (r.get("ask") or "").strip():
            out.append(r["ask"].strip())
        for a in (r.get("answers") or []):
            if isinstance(a, dict) and (a.get("a") or "").strip() and not a.get("final"):
                out.append(a["a"].strip())
    return "\n".join(out)


def feed_codes(root, thread):
    """피드 스레드가 가리키는 정본 코드 — [(code, why)] 를 **연결 근거가 강한 순**으로.

    코드는 **근거 강한 순 셋**까지다 (FEED_CODE_CAP) — 얕게 여럿보다 깊게 셋.

    2026-09-17: 피드 갈래의 목적지가 insights 하나뿐이라 요약 위에서 내린 판단이 판단 정본
    (C·WD·AC)으로 갈 자리가 없었다. 근거 순서 — ① 그 글의 canon_fit(수집 때 붙은 연결)
    ② 브리핑이 이은 links ③ 내가 대화에서 부른 코드 · 내 말과 제목이 낱말 2개 이상 겹치는 항목
    ④ 가르침 칸(lesson slot). ①②와 제목 겹침에서는 카드에서 '아니다'라고 한 코드
    (brief_fix.links)를 뺀다 — `brief_worker.fixes` 와 같은 규칙. 내가 직접 부른 코드·가르침
    칸은 그 뺌을 받지 않는다 (사용자가 나중에 다시 부른 것이 더 최근 신호다).
    """
    rej = set()
    for r in (thread or [root]):
        rej |= set(((r.get("brief_fix") or {}).get("links")) or [])
    hits = []
    def add(code, why):
        code = (code or "").strip()
        if code and canon_parse.prefix_of(code) and all(code != c for c, _ in hits):
            hits.append((code, why))
    item = next((f for f in read_jsonl(FEED) if f.get("id") == root.get("id")), {})
    for c in (item.get("canon_fit") or []):
        code = c.get("code") if isinstance(c, dict) else c
        if code not in rej:
            add(code, "이 글이 가리키는")
    for r in (thread or [root]):
        for l in ((r.get("brief") or {}).get("links") or []):
            code = l.get("code") if isinstance(l, dict) else l
            if code not in rej:
                add(code, "브리핑이 이은")
    mine = my_turns_text(root, thread)
    for code in FEED_CODE_RE.findall(mine):
        add(code, "내가 대화에서 부른")
    # 낱말 대조는 **꼬리를 뗀 뒤** 본다 — "난이도가"·"난이도로"가 서로 다른 낱말로 잡혀 겹침이
    # 0이 됐다 (2026-09-17 실측). 불용어 거르기는 grounding, 꼬리 떼기는 refine 것을 쓴다.
    def stems(text, cap):
        return set(refine._stem(w) for w in grounding._tokens(text, cap=cap))
    toks = stems(mine, 120)
    if toks:
        items = [(it, stems(it["title"], 20)) for it in canon_parse.items(P)]
        # 여러 제목에 두루 나오는 낱말은 연결 근거가 아니다 — **2개 이상** 제목에 걸리면 센 데서
        # 뺀다 (2026-09-17 사용자 결정: "업무·데이터·경험"으로 AC-01 이 매 스레드에 걸렸다).
        wide = set(w for w in set().union(*[t for _, t in items]) if sum(w in t for _, t in items) >= 2) if items else set()
        for it, tt in items:
            if it["code"] in rej:
                continue
            if len((toks & tt) - wide) >= 2:
                add(it["code"], "내 말과 제목이 겹치는")
    slot = root.get("slot") or (root.get("lesson") or {}).get("slot")
    if slot:
        add(craft.parse_key(slot)[0], "자소서")
    return hits[:FEED_CODE_CAP]


def close_targets(root, thread):
    """[완료] 때 서버가 발급하는 **목적지 후보** — 모델은 이 중 id 하나를 고르고 경로를 만들지 않는다.

    갈래별 (설계 ③): 열린 질문 → 그 결정의 칸들 + 끝불릿 / 가르침 → 그 자소서 칸들 /
    피드 → 그 글이 닿는 판단 칸들(`feed_codes`) + insights 축 / map → 그 파일 문답.
    label 은 **사람 말**이다 (화면에 그대로 뜬다).
    """
    def slots_of(code, why, titled=False, first=None):
        out = []
        it = canon_parse.item(code, P) if code else None
        title = " ".join(str((it or {}).get("title") or "").split())[:28]
        for s in (it or {}).get("slots") or []:
            if not s.get("name"):
                continue
            cur = s["vtext"]
            for e in (s.get("entries") or []):
                cur = (cur + " / " + e["vtext"]).strip(" /")
            label = ("%s %s 「%s」 › %s 칸" % (why, code, title, s["name"]) if titled
                     else "%s %s 의 「%s」 칸" % (why, code, s["name"]))
            out.append({"id": "slot:%s/%s" % (code, s["name"]), "kind": "canon",
                        "label": label,
                        "file": str(canon_parse.path_of(code, P).relative_to(P)),
                        "code": code, "slot": s["name"], "current": cur})
        if first:                      # 가리켜 온 칸은 그 코드 안에서 맨 앞 (가르침 갈래와 같게)
            out.sort(key=lambda x: x["slot"] != first)
        return out
    src = root.get("source")
    if root.get("origin") == "lesson" and root.get("slot"):
        code = craft.parse_key(root["slot"])[0]
        got = slots_of(code, "자소서")
        pick = [x for x in got if x["slot"] == craft.parse_key(root["slot"])[1]]
        return (pick + [x for x in got if x not in pick])[:8]
    if src == "openq":
        code = (root.get("code") or "").strip()
        out = slots_of(code, "결정") if code else []
        out.append({"id": "openq_tail", "kind": "openq",
                    "label": "어느 칸도 아니면 — 열린 질문 답으로 항목 끝에",
                    "file": "career/open-questions.md", "code": code or None, "slot": None, "current": ""})
        return out[:9]
    if src == "feed":
        item = next((f for f in read_jsonl(FEED) if f.get("id") == root.get("id")), {})
        ax = (item.get("axes") or item.get("axes_hint") or ["-"])[0]
        # 코드별 칸을 **돌아가며** 담는다 — 앞선 코드의 칸이 6개면 상한을 혼자 다 먹어 내가 대화에서
        # 부른 코드가 후보에서 밀린다. 담고 나서 코드 순서로 다시 묶어 보여준다.
        les = root.get("slot") or (root.get("lesson") or {}).get("slot") or ""
        lcode, lslot = craft.parse_key(les) if les else (None, None)
        queues, seen = [], set()
        for code, why in feed_codes(root, thread):
            got = slots_of(code, why, titled=True, first=(lslot if code == lcode else None))
            q = [x for x in got if not (x["id"] in seen or seen.add(x["id"]))]
            if q:
                queues.append(q)
        picked, i = [], 0
        while len(picked) < FEED_TARGET_CAP - 1 and any(i < len(q) for q in queues):
            for ci, q in enumerate(queues):
                if i < len(q) and len(picked) < FEED_TARGET_CAP - 1:
                    picked.append((ci, i, q[i]))
            i += 1
        out = [x for _, _, x in sorted(picked, key=lambda t: (t[0], t[1]))]
        # insights 는 **항상 마지막** — 어느 판단 칸도 아니면 여기로 (피드의 원래 자리)
        out.append({"id": "insights", "kind": "insights",
                    "label": "수집 기록 %s축 「내 생각」 자리" % ax,
                    "file": "visionary/linkedin/insights.md", "code": None, "slot": None, "current": ""})
        return out
    if src == "map" and root.get("path"):
        return [{"id": "map", "kind": "map", "label": "%s 끝 문답" % root["path"],
                 "file": root["path"], "code": None, "slot": None, "current": ""}]
    return []


def source_points(root):
    """글(피드) 위에서 한 대화의 **배경 후보** — 화면 `<ol>` 과 같은 번호로 "요점 N" 을 싣는다.

    2026-09-08 실측: 사용자가 "6번을 풀어서 답해줘" 라고 쳤는데 마감 문장이 C-06 밑에
    "⚠️AI 요점 6(…)이 선행이다" 로 저장됐다 — 정본만 읽는 사람은 그게 뭔지 모른다. 모델이
    가리킨 원문을 풀어 쓰려면 그 원문이 근거 후보에 있어야 한다. feed 행이 아니면 빈 목록,
    읽기 실패도 빈 목록(마감 자체를 막지 않는다). 행 읽기는 기존 로더(`read_jsonl(FEED)`)만 쓴다.
    """
    rid = (root or {}).get("id")
    if not rid:
        return []
    try:
        item = next((f for f in read_jsonl(FEED) if f.get("id") == rid), None)
    except (OSError, ValueError):
        return []
    if not item:
        return []
    who = " ".join(str(item.get("author") or "글").split())[:24]
    out = []
    for i, pt in enumerate(((item.get("long") or {}).get("points") or []), 1):
        t = " ".join(str(pt or "").split())
        if t:
            out.append({"text": "요점 %d: %s" % (i, t), "source": "글 요점 %d · %s" % (i, who)})
    if not out:
        body = " ".join(str(item.get("text") or "").split())[:700]
        if body:
            out.append({"text": body, "source": "글 원문 앞부분 · " + who})
    summary = " ".join(str(item.get("summary") or "").split())
    if summary:
        out.append({"text": summary, "source": "글 한 줄 요약 · " + who})
    return out


def close_pool(root, thread, text=""):
    """근거 후보 — 내가 친 말(turn) · grounding 이 찾은 과거 기록(record) · 배경(background).

    저장해 둔다([제출] 때 같은 ctx 로 다시 검증해야 하므로). 서버가 문장을 만들지 않는다 — 원문만 모은다.
    배경 차례는 **글 요점·요약 → Claude 답 → grounding** 이다 (가리킨 원문이 먼저 보여야 한다).
    """
    turns, background = [], source_points(root)
    for r in thread:
        if (r.get("ask") or "").strip():
            turns.append({"text": r["ask"].strip(), "source": "대화 " + str(r.get("ts") or "")[:10]})
        for a in (r.get("answers") or []):
            if isinstance(a, dict) and (a.get("a") or "").strip() and not a.get("final"):
                turns.append({"text": a["a"].strip(), "source": "대화 " + str(a.get("at") or "")[:10]})
        fm = r.get("for_me") or {}
        # 피드 첫 턴은 `for_me.answer` 가 비어 있고 `result`(한 줄)·`result_body`(전문)에 있다 —
        # 그래서 옛 코드에선 Claude 가 푼 내용이 근거 후보에 아예 없었다 (2026-09-08 실측).
        gist = (r.get("result_body") or fm.get("answer") or r.get("result")
                or (fm.get("question") or {}).get("q") or "")
        if str(gist).strip():
            background.append({"text": " ".join(str(gist).split())[:700], "source": "Claude 답 " + str(r.get("ts") or "")[:10]})
    if (text or "").strip():
        turns.append({"text": text.strip(), "source": "완료 때 내 말"})
    records = []
    try:
        items, _ = grounding.answered(code=(root.get("code") or None), slot=(root.get("slot") or None),
                                      root=P, thread_id=root.get("ts"),
                                      targets=[root.get("id"), root.get("path")],
                                      topic=(root.get("title") or "") + " " + (root.get("question") or ""))
        for it in items:
            if it["group"] == "mine":
                records.append({"text": it["text"], "source": it["src"]})
            elif it["group"] == "ai_ok":
                background.append({"text": it["text"], "source": it["src"] + " (AI 문장을 내가 승인)"})
    except (OSError, ValueError):
        pass
    back = background[:14]
    while len(back) > 1 and sum(len(b.get("text") or "") for b in back) > 5000:
        back.pop()
    return refine.candidates(turns=turns[:12], records=records[:8], background=back)


# ── 구조 탭 서랍 ② — 미리 쓴 문장 (설계 `docs/superpowers/plans/2026-09-17-drawer-read-and-draft.md`) ──
# 서랍에서 항목을 펼치면 그 항목의 ⚠️AI 칸마다 **쌓인 내 기록으로 쓴 문장**을 하나씩 만든다.
# 서버는 모델을 부르지 않는다 — 목적지·근거만 모아 카드에 싣고 마감 워커(`--close`)를 띄운다.
DRAFT_FILES = 4        # 일지 덩어리를 가져올 파일 수 상한
DRAFT_CHUNK = 700      # 덩어리 하나의 글자 상한
DRAFT_JOURNAL_N = 8    # record 로 넣는 일지 덩어리 수 상한
DRAFT_BG_CHUNKS = 2    # 남의 글·AI 정리 덩어리 상한
DRAFT_STALE_MIN = 15   # 이 분을 넘겼는데 살아 있는 run 이 없으면 멈춘 것으로 본다 (서랍이 [다시]를 띄운다)


def lineage_linked(code, kinds=("어디에 붙나",), prefixes=("journal/",)):
    """계보 엣지로 그 코드에 이어진 파일 경로 — 최근 것부터. **읽기만** 한다 (재조립 없음).

    `grounding.answered` 는 일지를 읽지 않는다(2026-09-17 감사: 손 필요 11건 중 2건만 내 기록을 찾았다).
    같은 11건이 이 엣지로는 일지·인사이트 3~12파일씩 이어져 있다 — 그 구멍을 메우는 자리다.
    """
    try:
        d = json.loads((P / ".runtime/.lineage-data.json").read_text())
    except (OSError, json.JSONDecodeError):
        d = _struct_last.get("d") or {"nodes": [], "edges": []}
    nodes = {n.get("id"): n for n in (d.get("nodes") or [])}
    out = []
    for e in (d.get("edges") or []):
        if e.get("dst") != code:
            continue
        if kinds and e.get("kind") not in kinds:
            continue
        rel = (nodes.get(e.get("src")) or {}).get("path") or ""
        if rel and rel.startswith(tuple(prefixes)) and rel not in out:
            out.append(rel)
    return sorted(out, reverse=True)      # 파일 이름이 날짜다 — 최근 것부터


def draft_chunks(paths, topic, code, cap_files=DRAFT_FILES, cap_n=DRAFT_JOURNAL_N, quote_only=True):
    """그 파일들의 헤딩 덩어리 중 **코드나 제목 낱말이 든 것**만 — 근거가 강한 순.

    quote_only 면 `grounding.quotable`(career·journal·drafts·DEMO) 을 통과한 파일만 본다 —
    남의 글을 내 판단 근거(record)로 쓰지 않기 위해서다.
    """
    picked = []
    for rel in paths:
        if quote_only and not grounding.quotable(rel, P):
            continue
        picked.append(rel)
        if len(picked) >= cap_files:
            break
    chunks, _errs = grounding._headed(picked, root=P)
    toks = grounding._tokens(topic or "")
    low_code = (code or "").lower()
    out = []
    for c in chunks:
        body = " ".join(c["text"].split())
        low = body.lower()
        hits = sum(1 for t in toks if t in low)
        has_code = bool(low_code) and low_code in low
        if not has_code and hits < 2:
            continue
        out.append({"text": body[:DRAFT_CHUNK],
                    "source": "%s %s" % (c["rel"], c["head"]), "score": (3 if has_code else 0) + hits})
    out.sort(key=lambda x: -x["score"])
    return [{"text": c["text"], "source": c["source"]} for c in out[:cap_n]]


CITE_RE = re.compile(r"https?://")


def is_citation(x):
    """바깥 인용인가 — 판단이 아니라 남의 글을 옮겨 둔 줄.

    서랍은 판단만 다룬다(2026-09-18 사용자 ㉯). 판정은 **바깥 링크 유무** 하나다 — 정본 9개
    ⚠️AI 줄 중 링크가 있는 것은 WD-02 「같은 방향의 외부 관측」(arXiv) 하나뿐이었다. 인용 줄에
    "나는 이렇게 본다"를 붙이면 근거와 판단이 한 자리에 섞인다 — 그 줄은 근거로 옮길 자리다.
    """
    return bool(CITE_RE.search(str(x.get("vtext") or x.get("text") or "")))


def draft_targets(it):
    """pending 칸 → (목적지 후보, {목적지: [⚠️AI sha…]}) — `close_targets` 와 같은 형식이다.

    하위 불릿·블록 표식은 **그 위 칸**의 이름을 물려받는다. 이름을 못 찾으면 건너뛴다 —
    저장(`canon_entry_append`)이 이름으로 자리를 찾기 때문에 서랍에서 답할 수 없는 자리다.

    2026-09-18: 이력 밑에 AI가 제 이름으로 붙인 절(WD-02 「닫는 조건」 등)도 받는다. 전에는
    표준 칸 이름만 받아 배지는 "⚠️AI 3"이라 하고 서랍은 아무것도 못 주던 자리였다.
    """
    code = it["code"]
    rel = str(canon_parse.path_of(code, P).relative_to(P))
    title = " ".join(str(it.get("title") or "").split())[:28]
    named = {s["name"] for s in it["slots"] if s.get("name")} | {
        h["name"] for h in it["history"] if h.get("name")}
    order, targets, shas = [], {}, {}
    for group in (it["slots"], it["history"]):
        last = None
        for s in group:
            if s.get("name"):
                last = s["name"]
            for x in [s] + list(s.get("entries") or []):
                if not x.get("pending") or is_citation(x):
                    continue
                name = s.get("name") or last
                if not name or name not in named:
                    continue
                tid = "slot:%s/%s" % (code, name)
                if tid not in targets:
                    targets[tid] = {"id": tid, "kind": "canon",
                                    "label": "%s 「%s」 › %s 칸" % (code, title, name),
                                    "file": rel, "code": code, "slot": name, "current": ""}
                    order.append(tid)
                cur = " ".join(str(x.get("vtext") or x.get("text") or "").split())
                targets[tid]["current"] = (targets[tid]["current"] + " / " + cur).strip(" /")[:600]
                shas.setdefault(tid, []).append(x["sha"])
    return [targets[t] for t in order], shas


def draft_pool(it, turns=()):
    """근거 후보 — record ① 내 칸 값 ② 일지 덩어리 ③ 과거 내 답 / background AI 문장·남의 글.

    판단 근거가 background 뿐이면 validator 가 반려한다 — 그게 서랍의 "못 만듦" 상태다.
    """
    code, title = it["code"], it["title"]
    records = [{"text": s["vtext"], "source": "%s %s 칸" % (code, s["name"])}
               for s in it["slots"] if s.get("name") and s.get("vtext")
               and canon_parse.PENDING_MARK not in s["marks"]][:6]
    records += draft_chunks(lineage_linked(code, ("어디에 붙나",), ("journal/",)), title, code)
    background = [{"text": " ".join(str(x.get("text") or "").split())[:DRAFT_CHUNK],
                   "source": "%s 의 AI 문장" % code} for x in it["pending"]][:8]
    background += draft_chunks(lineage_linked(code, (), ("visionary/", "AI HUB/")), title, code,
                               cap_files=DRAFT_BG_CHUNKS, cap_n=DRAFT_BG_CHUNKS, quote_only=False)
    try:
        got, _ = grounding.answered(code=code, root=P, topic=title)
        records += [{"text": g["text"], "source": g["src"]} for g in got if g["group"] == "mine"][:8]
        background += [{"text": g["text"], "source": g["src"] + " (AI 문장을 내가 승인)"}
                       for g in got if g["group"] == "ai_ok"][:4]
    except (OSError, ValueError):
        pass
    back = background[:14]
    while len(back) > 1 and sum(len(b.get("text") or "") for b in back) > 5000:
        back.pop()
    return refine.candidates(turns=[{"text": t, "source": "서랍에서 내가 친 말"} for t in turns if str(t).strip()],
                             records=records[:20], background=back)


def pool_add_turn(pool, text, source):
    """근거 후보에 내 말 한 줄을 **덧붙인다** — 이미 나온 문장의 id 가 밀리면 옛 dest 의 인용 대조가 깨진다."""
    used = [int(c["id"][1:]) for c in (pool or [])
            if str(c.get("id", "")).startswith("t") and str(c["id"])[1:].isdigit()]
    return list(pool or []) + [{"id": "t%d" % (max(used or [0]) + 1), "kind": "turn",
                                "text": " ".join(str(text).split()), "source": source}]


def draft_merge(prev, prop):
    """서랍 초안 — **다시 쓴 칸의 dest 만** 갈아 끼우고 나머지 칸 문장은 그대로 둔다.

    [아니다]+내 말로 한 칸을 다시 쓸 때 다른 칸의 문장까지 사라지면 사용자가 두 번 읽는다.
    """
    prop = dict(prop)
    if (prev or {}).get("version") != refine.VERSION:
        return prop
    fresh = {d.get("target_id") for d in (prop.get("dests") or [])}
    kept = [d for d in (prev.get("dests") or []) if d.get("target_id") not in fresh]
    prop["dests"] = kept + list(prop.get("dests") or [])
    done = fresh | {d.get("target_id") for d in kept}
    prop["rejected"] = list(prop.get("rejected") or []) + [
        r for r in (prev.get("rejected") or []) if r.get("dest") not in done]
    return prop


def draft_apply(root, tid, text=None):
    """[반영] — 그 칸 하나를 정본에 넣는다. 반환 (알림 문구, 카드에 쓸 필드, err).

    저장 전에 같은 validator 를 다시 돌린다(화면에 판정 규칙을 두지 않는다). ⚠️AI 원문은
    지우지 않고 `canon_note_fold` 로 접고, 접힌 sha 는 카드 `shas` 에서 뺀다 — 그래야
    "정본이 바뀌었다" 판정이 내 [반영]까지 낡음으로 세지 않는다.

    `text` 는 사용자가 서랍에서 그 문장을 직접 고친 것이다 — 고쳤으면 그 말이 우선이고
    출처 줄의 갈래에 '내가 고침'이 남는다. 판정은 [제출]과 같은 strict=False 경로다
    (근거 표기 재요구 없음·길이·근거 인용만 다시 본다).
    """
    prop = dict(root.get("proposal") or {})
    if prop.get("version") != refine.VERSION:
        return None, None, "아직 문장이 없다"
    dests = list(prop.get("dests") or [])
    idx = next((i for i, d in enumerate(dests)
                if d.get("target_id") == tid and not d.get("applied_at")), None)
    if idx is None:
        return None, None, "그 칸의 문장이 없다 — 새로고침"
    mine = " ".join((text or "").split())
    if mine and mine != " ".join(str(dests[idx].get("text") or "").split()):
        dests[idx] = dict(dests[idx], text=mine)
    else:
        mine = ""
    ctx = {"pool": prop.get("pool") or [], "root": P,
           "targets": root.get("targets_all") or root.get("targets") or []}
    ok, rejected, _warn = refine.validate(
        {"version": refine.VERSION, "dests": [dests[idx]]}, ctx, strict=False)
    if not ok:
        return None, None, ("; ".join(r.get("reason", "") for r in rejected)[:300] or "그 문장을 저장할 수 없다")
    d = ok[0]
    tgt = d.get("target") or {}
    if tgt.get("kind") != "canon":
        return None, None, "정본 칸이 아닌 자리다"
    today = datetime.now(KST).strftime("%Y-%m-%d")
    where, err = canon_entry_append(tgt["code"], tgt["slot"], d, today,
                                    "구조 탭·내가 고침" if mine else "구조 탭")
    if err:
        return None, None, err
    folded, notes = [], [where]
    for sha in (root.get("slot_shas") or {}).get(tid, []):
        ferr = canon_note_fold(tgt["code"], sha, today, tgt["slot"])
        if ferr:
            notes.append("(원래 AI 문장 접기 실패: %s)" % ferr)
        else:
            folded.append(sha)
    dests[idx] = dict(dests[idx], applied_at=now())
    prop["dests"] = dests
    fields = {"proposal": prop, "shas": [s for s in (root.get("shas") or []) if s not in folded]}
    if all(x.get("applied_at") or x.get("dismissed_at") for x in dests):
        fields["applied_at"] = now()
    return " ".join(n for n in notes if n), fields, None


def draft_live(code=None):
    """살아 있는 초안 카드 — 아직 전부 반영되지 않았고 낡지도 않은 것."""
    return [r for r in store.read() if r.get("origin") == "draft"
            and (code is None or r.get("code") == code)
            and not r.get("applied_at") and not r.get("stale_at")]


def complete_refine(root, thread, dests, det):
    """검증을 통과한 dest 들을 실제 자리에 넣는다 — **전부 저장한 뒤** 완료 표시는 호출자가 한다.

    순서 (Codex Q3: 여러 파일 트랜잭션이 없다): 하나라도 저장 실패하면 그 사유를 돌려주고 완료로
    넘어가지 않는다. 같은 문장이 이미 있으면 건너뛴다(재시도 중복 방지).
    """
    today = datetime.now(KST).strftime("%Y-%m-%d")
    lane = {"openq": "열린 질문 대화", "feed": "피드 대화", "map": "파일 문답"}.get(root.get("source"), "대시보드 대화")
    if root.get("origin") == "lesson":
        lane = "가르침 대화"
    notes = []
    for d in dests:
        tgt = d.get("target") or {}
        kind = tgt.get("kind")
        if kind == "canon":
            where, err = canon_entry_append(tgt["code"], tgt["slot"], d, today, lane)
            if err:
                return None, err
            notes.append(where)
            det.update(_dest_detail(where, d.get("text") or ""))
        elif kind == "openq":
            qid = (root.get("id") or "").split(":")[0]
            code = (tgt.get("code") or root.get("code") or "").strip() or None
            body = refine.render_insight(d, today, lane).strip()
            where, err = openq_answer(qid, body, code)
            if err and code and "못 찾았다" in err:
                item = {"q": root.get("question") or "", "short": (root.get("question") or "")[:70]}
                where, err = _openq_write_code(code, body, item, today)
            if err:
                return None, err
            notes.append(where)
            det.update(_dest_detail(where, d.get("text") or ""))
        elif kind == "insights":
            ax = insights_insert(root, refine.render_insight(d, today, lane).strip())
            notes.append("insights.md %s축 섹션 (✍️정리)" % ax)
            det.update(path=str(P / "visionary/linkedin/insights.md"),
                       anchor="## %s. %s › 내 생각" % (ax, axis_names().get(ax, "축 없음")),
                       inserted=d.get("text") or "")
        elif kind == "map":
            tgt_path = (P / (tgt.get("file") or root.get("path") or "")).resolve()
            if not (str(tgt_path).startswith(str(P)) and tgt_path.suffix == ".md" and tgt_path.exists()):
                return None, "붙일 파일이 없다 — 카드에만 남긴다"
            head = "#### 대시보드 문답 (%s)" % today
            body = tgt_path.read_text()
            blk = ([] if head in body else ["", head, ""]) + [refine.render_insight(d, today, lane).rstrip(), ""]
            if refine.render_insight(d, today, lane).strip() in body:
                notes.append("%s (이미 같은 문장이 있어 건너뜀)" % tgt.get("file"))
                continue
            awrite(tgt_path, body.rstrip() + "\n" + "\n".join(blk) + "\n")
            notes.append("%s 끝 문답 (✍️정리)" % tgt.get("file"))
            det.update(path=str(tgt_path), anchor=head, inserted=d.get("text") or "")
        else:
            return None, "모르는 목적지 갈래: %s" % kind
    # ⚠️AI 노트에서 시작한 스레드 — 문장이 칸에 들어갔으면 원 노트를 출처 줄로 접는다 (삭제 아님)
    # 옛 카드용 — `/api/note_refine` 은 2026-09-17(설계 ③)에 지웠다(draft 가 대체). 새 note 카드는
    # 더 만들어지지 않지만 assignments.jsonl 에 남은 3건(WD-03 미반영 1·C-05·C-06 반영됨)이 이 길로 닫힌다.
    if root.get("origin") == "note" and root.get("sha") and dests:
        tgt = (dests[0].get("target") or {})
        if tgt.get("kind") == "canon":
            err = canon_note_fold(tgt["code"], root["sha"], today, tgt["slot"])
            if err:
                notes.append("(노트 접기 실패: %s)" % err)
            else:
                notes.append("원 AI 노트는 출처 줄로 접었다")
    return " / ".join(notes) if notes else None, None


def complete_thread(ts, text, dest_note=None, detail=None, dests=None):
    """[최종 제출] — 세션(원 카드 + 되물은 것)을 닫고 text 를 원래 자리에 그대로 넣는다.

    2026-09-03 사용자: "완료 클릭하면 최종 내용을 반영해. 사라지는 걸로." 목적지는 카드의 출처가
    기계적으로 정한다: 반영안(apply)이 있으면 그 항목 밑 한 줄 / feed 는 insights.md 축 섹션 /
    map(파일 작업)은 그 파일 끝 문답 블록 / 그 외는 카드 기록만. text 가 비면 닫기만.
    2026-09-08 흐름: [완료] → 마무리 턴이 대화 전체로 반영글 **초안**(root.proposal)을 만들어 카드에 띄움 →
    사용자가 고쳐 [최종 제출] → 여기. 들어가는 건 사용자가 제출한 글 그대로. 초안을 한 글자도 안 고치고
    제출했으면 ⚠️AI 표기를 앞에 붙인다(정본 출처 규칙). 서버가 문장을 만들지 않는다. 반환 (dest, err).
    2026-09-11: detail 에 dict 를 주면 실제로 붙은 자리(path·anchor)·넣은 문장(inserted)·보관 여부(kept)를 채운다
    (결과 팝업용). 피드 글은 정본·insights 에 실제로 붙었을 때만 보관(kept)으로 옮긴다 — 제출 = 살로 쓴 글.
    """
    root, thread = thread_of(ts)
    if not root:
        return None, "배정 없음"
    det = detail if isinstance(detail, dict) else {}
    text = (text or "").strip()
    prop = (root.get("proposal") or {}).get("text", "")
    own = (root.get("proposal") or {}).get("kind") == "own_words"   # 사용자 문장 조립 — 안 고쳐도 AI 글자가 없다 (2026-09-10)
    # 2026-09-17 (설계 ③): `verbatim`(한 글자 고쳤나) 판정은 폐기했다 — 저자는 근거(evidence) 갈래가
    # 정한다. version 2 마감은 dests 로 들어오고, 옛 proposal(version 없음)은 아래 옛 경로 그대로다.
    verbatim = False
    mine = text
    conc = None
    dest = "글쓰기 카드 기록 (편집 본문 보존)" if journal_writing.enabled(root) else (dest_note or "닫기만 — 반영 없음")
    if dests:
        dest, err = complete_refine(root, thread, dests, det)
        if err:
            return None, err
        dest = dest or "넣을 문장이 없어 기록 없이 닫았다"
    elif text:
        ap = last_apply(thread)
        qq = ((thread[-1].get("for_me") or {}).get("question") or {}).get("q")
        if journal_writing.enabled(root):
            # 글쓰기 편집본을 내 판단 정본이나 journal에 자동으로 밀어 넣지 않는다.
            dest = "글쓰기 카드 기록 (최종 입력·편집 본문 보존)"
        elif ap and ap.get("file") in APPLY_ALLOW:
            err, note = insert_apply_line(ap, mine, limit=700)
            if err:
                return None, err
            dest = note + (" — AI 초안 그대로, ⚠️AI 표기" if verbatim else "")
            det.update(path=str(P / ap["file"]), anchor=(ap.get("anchor") or "").strip(), inserted=mine)
        elif root.get("source") == "feed":
            ax = insights_insert(root, mine, question=qq)
            dest = f"insights.md {ax}축 섹션 (내 문장)"
            det.update(path=str(P / "visionary/linkedin/insights.md"),
                       anchor="## %s. %s › 내 생각" % (ax, axis_names().get(ax, "축 없음")), inserted=mine)
        elif root.get("source") == "openq":
            # 열린 질문 대화의 끝 — 기존 답 저장 경로 그대로 (결정 본문/장면 칸 + 큐 '답한 것'). 서버는 문장을 안 만든다.
            qid = (root.get("id") or "").split(":")[0]
            code = (root.get("code") or "").strip() or None
            dest, err = openq_answer(qid, mine, code)
            if err and code and "못 찾았다" in err:
                # 큐 불릿이 대화 사이에 바뀌어 id 가 안 맞는다 — 결정 블록엔 넣고 큐 이동만 건너뛴다 (함정 기록 2026-09-10)
                item = {"q": root.get("question") or "", "short": (root.get("question") or "")[:70]}
                dest, err = _openq_write_code(code, mine, item, datetime.now(KST).strftime("%Y-%m-%d"))
                if not err:
                    dest += " (큐 불릿은 못 찾아 그대로 — 손으로 '답한 것'으로)"
            if err:
                return None, err
            det.update(_dest_detail(dest, mine))
            if not det.get("path"):     # 대상 결정이 없어 '답한 것' 절에만 남은 경우
                det.update(path=str(P / "career/open-questions.md"), anchor="답한 것")
        elif root.get("source") == "map" and root.get("path"):
            tgt = (P / root["path"]).resolve()
            if not (str(tgt).startswith(str(P)) and tgt.suffix == ".md" and tgt.exists()):
                return None, "붙일 파일이 없다 — 카드에만 남긴다"
            stamp = datetime.now(KST).strftime("%Y-%m-%d")
            blk = [f"\n#### 대시보드 문답 ({stamp})", ""]
            if root.get("ask"):
                blk.append(f"- 첫 물음: {root['ask'].strip()}")
            if qq:
                blk.append(f"- Q(⚠️AI): {qq}")
            blk += [f"- A: {mine}", ""]
            awrite(tgt, tgt.read_text().rstrip() + "\n" + "\n".join(blk))
            dest = f"{root['path']} 끝 문답 (내 문장)"
            det.update(path=str(tgt), anchor=f"#### 대시보드 문답 ({stamp})", inserted=mine)
        else:
            dest = "카드 기록 (내 문장)"
    # 제출 = 살로 쓴 글 — 정본·insights 에 실제로 붙었으면 그 피드 글을 보관으로 옮긴다 (2026-09-11 사용자 결정).
    # 닫기만·반영 없음(det.path 없음)이면 그대로 두고, 사람이 버린 글(dropped)은 되살리지 않는다.
    # feed-queue 쓰기는 update_feed 한 곳 — 여기서 새 경로를 만들지 않는다.
    if det.get("path") and root.get("source") == "feed" and not journal_writing.enabled(root):
        row = next((f for f in read_jsonl(FEED) if f.get("id") == root.get("id")), None)
        if row is not None and row.get("status") == "pending":
            update_feed(root["id"], {"status": "kept"})
            det["kept"] = True

    ids = {r["ts"] for r in thread}
    def _mark(rs):
        for r in rs:
            if r.get("ts") in ids:
                r["applied_at"] = now()
                r["applied_note"] = f"완료 → {dest}"
                r["seen_at"] = r.get("seen_at") or now()
                for k in ("closing_at", "close_error", "close_text"):
                    r.pop(k, None)
                if text and r["ts"] == root["ts"]:
                    r.setdefault("answers", []).append({"q": None, "a": text, "at": now(), "final": True, "verbatim_ai": verbatim})
    store.mutate(_mark)
    return dest, None


def openq_bullets():
    """'## 큐' 절의 질문 불릿 — (path, lines, [(start, end)]). 불릿은 '- [' 로 시작하고 들여쓴 줄이 이어진다."""
    path = P / "career/open-questions.md"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    qs = next((i for i, l in enumerate(lines) if l.startswith("## 큐")), None)
    if qs is None:
        return path, lines, []
    qe = next((i for i in range(qs + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    spans, i = [], qs + 1
    while i < qe:
        if lines[i].startswith("- ["):
            j = i + 1
            while j < qe and (lines[j].startswith("  ") and lines[j].strip()):
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return path, lines, spans


def openq_targets(codes):
    """'C-01~06 · WD-01~07' 같은 대상 표기를 코드 목록으로 — 범위(~)와 구분자(· , /)를 푼다.
    (2026-09-08 "정확히 내 글에서 빠진 부분을 내가 채울 수 있게" — 대상이 여럿인 질문은 코드별로 한 줄씩 묻는다)"""
    out = []
    for tok in re.split(r"[·,/]|\s+and\s+", codes):
        m = re.fullmatch(r"\s*(C|WD|AC)-(\d+)(?:\s*[~–-]\s*(?:\1-)?(\d+))?\s*", tok)
        if not m:
            continue
        a, b = int(m.group(2)), int(m.group(3) or m.group(2))
        out += [f"{m.group(1)}-{n:02d}" for n in range(a, b + 1)]
    return out


def openq_short(q):
    """질문의 묻는 부분만 — 굵은 표기와 25자 넘는 괄호(왜 묻는지 설명)를 떼고 마침표를 정리한다.
    떼어낸 설명은 why 로 돌려준다 (펼친 카드에서 흐리게)."""
    q = q.replace("**", "")
    why = []
    def _take(m):
        inner = m.group(1).strip()
        if len(inner) >= 25:
            why.append(inner)
            return ""
        return m.group(0)
    q = re.sub(r"\(([^()]*)\)", _take, q)
    q = re.sub(r"\s+", " ", q).strip(" .。")
    # "장면 슬롯이 비어 있다 — 이 기준이 태어난 장면은 무엇이었나" 꼴이면 앞의 진술은 떼고 묻는 뒤쪽만
    head, sep, tail = q.partition(" — ")
    if sep and re.search(r"[나가까?]$", tail.split(". ")[0].rstrip(" .")) and not re.search(r"[나가까?]$", head.rstrip(" .")):
        why = [head] + why
        q = tail
    return q, " / ".join(why)


def openq_list():
    """구조 탭 '열린 질문' 서랍이 그리는 목록 — 질문마다 id(sha10)·대상 코드·본문·등록일 (2026-09-08 답 칸용).
    id 는 '→ … 답함' 진행 줄을 뺀 본문으로 만들어 코드별 답이 쌓여도 바뀌지 않는다."""
    import hashlib
    out = []
    try:
        _, lines, spans = openq_bullets()
    except OSError:
        return out
    for s, e in spans:
        body = [l for l in lines[s:e] if not re.match(r"^\s+→ ", l)]
        answered = re.findall(r"^\s+→ ((?:C|WD)-\d+) 답함", "".join(lines[s:e]), re.M)
        flat = " ".join("".join(body).split())
        m = re.match(r"- \[([^\]]+)\]\s*(.*?)\s*\((?:등록일|등록) (\d{4}-\d{2}-\d{2})\)\s*$", flat)
        codes, q, date = (m.group(1), m.group(2), m.group(3)) if m else (re.match(r"- \[([^\]]+)\]", flat).group(1), flat[flat.find("]") + 1:].strip(), "")
        code = codes.strip() if re.fullmatch(r"(C|WD|AC)-\d+", codes.strip()) else None
        short, why = openq_short(q)
        out.append({"id": hashlib.sha1(flat.encode()).hexdigest()[:10], "codes": codes.strip(), "code": code,
                    "targets": openq_targets(codes), "answered": answered,
                    "q": q, "short": short, "why": why, "date": date})
    return out


def _openq_write_code(code, text, item, today):
    """답을 그 결정 블록에 — 장면을 묻는 질문이고 장면 칸이 비어 있으면 장면 칸에, 아니면 '열린 질문 답' 불릿으로.
    반환 (dest, err)."""
    if "장면" in item["q"] and canon_scene_fill(code, text) is None:
        cpath, *_ = canon_locate(code)
        return f"{cpath.relative_to(P)} › {code} 장면", None
    cpath, clines, cs, ce = canon_locate(code)
    if cpath is None:
        return None, "항목을 못 찾았다: " + code
    k = ce
    while k > cs + 1 and not clines[k - 1].strip():
        k -= 1
    clines.insert(k, f"- **열린 질문 답** ({today}): {text} (Q: {item['short'][:70]}{'…' if len(item['short']) > 70 else ''})\n")
    awrite(cpath, "".join(clines), encoding="utf-8")
    return f"{cpath.relative_to(P)} › {code} 본문", None


def openq_answer(qid, text, code=None):
    """열린 질문에 답 — 본인 타이핑 그대로. 서버는 문장을 만들지 않는다. 반환 (dest, err).
    대상 결정(C-XX/WD-XX)이 하나면 그 블록에 들어가고 큐 불릿은 '## 답한 것' 절로.
    대상이 여럿이면 code 하나씩 — 그 블록에 쓰고 불릿 밑에 '→ C-05 답함 날짜' 줄을 더한다.
    마지막 대상까지 답하면 불릿이 '답한 것'으로 옮겨진다. code 없이 오면 '답한 것' 줄에만 남는다."""
    import hashlib
    path, lines, spans = openq_bullets()
    items = {x["id"]: x for x in openq_list()}
    item = items.get(qid)
    hit = None
    for s, e in spans:
        body = [l for l in lines[s:e] if not re.match(r"^\s+→ ", l)]
        if hashlib.sha1(" ".join("".join(body).split()).encode()).hexdigest()[:10] == qid:
            hit = (s, e)
    if not hit or not item:
        return None, "질문을 못 찾았다 — 파일이 바뀌었으면 새로고침"
    s, e = hit
    today = datetime.now(KST).strftime("%Y-%m-%d")
    code = code or item["code"]
    dest = None
    if code:
        if code not in (item["targets"] or [code]):
            return None, f"{code} 는 이 질문의 대상이 아니다"
        if code in item["answered"]:
            return None, f"{code} 는 이미 답했다"
        dest, err = _openq_write_code(code, text, item, today)
        if err:
            return None, err
        remaining = [c for c in item["targets"] if c != code and c not in item["answered"]]
        if remaining:
            lines.insert(e, f"  → {code} 답함 {today}, {dest}\n")
            awrite(path, "".join(lines), encoding="utf-8")
            return dest, None
    moved = "".join(lines[s:e]).rstrip("\n")
    tail = f"  → 답함 {today}" + (f", {dest}" if dest else f": {text}") + "\n"
    del lines[s:e]
    # 질문 뒤 빈 줄 하나 정리
    if s < len(lines) and not lines[s].strip() and (s == 0 or not lines[s - 1].strip()):
        del lines[s]
    sec = next((i for i, l in enumerate(lines) if l.startswith("## 답한 것")), None)
    if sec is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines += ["\n", "## 답한 것 (대시보드 문답)\n", "\n"]
        sec = len(lines) - 2
    ins = next((i for i in range(sec + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while ins > sec + 1 and not lines[ins - 1].strip():
        ins -= 1
    lines[ins:ins] = [moved + "\n", tail]
    awrite(path, "".join(lines), encoding="utf-8")
    return dest or "open-questions.md '답한 것' 절 (대상 결정이 하나가 아니라 여기만)", None


def openq_count():
    """활성 큐만 — '## 큐' 절부터 다음 ## 까지 (제외 이력은 빼고)."""
    try:
        body = (P / "career/open-questions.md").read_text()
    except OSError:
        return None
    i = body.find("\n## 큐")
    if i < 0:
        return None
    seg = body[i + 1:]
    j = seg.find("\n## ", 1)
    return len(re.findall(r"^- \[", seg[:j if j > 0 else None], re.M))


def structure_counts():
    """구조 탭이 쓰는 실물 수치 — 각 파일에 지금 몇 개가 있나 (읽기만)."""
    def n(path, pat):
        try:
            return len(re.findall(pat, (P / path).read_text(), re.M))
        except OSError:
            return None
    def lines(path):
        try:
            return sum(1 for l in (P / path).read_text().splitlines() if l.strip())
        except OSError:
            return None
    feed = read_jsonl(FEED)
    return {
        "inbox": sum(1 for f in feed if f.get("status") == "pending"),
        "kept": sum(1 for f in feed if f.get("status") == "kept"),
        "cards": len(store.read()),
        "insights_axes": n("visionary/linkedin/insights.md", r"^## [A-H](?:-1)?\."),
        "insights_posts": n("visionary/linkedin/insights.md", r"^### "),
        "C": n("career/decisions.md", r"^#{2,3} C-\d+"),
        "WD": n("career/work-decisions.md", r"^#{2,3} WD-\d+"),
        "openq": openq_count(),
        "journal": sum(f.name.lower() != "readme.md" for f in (P / "journal").glob("*.md")) if (P / "journal").exists() else None,
        "visionary": sum(f.name.lower() != "readme.md" for f in (P / "visionary").rglob("*.md")) if (P / "visionary").exists() else None,
        "drafts": sum(f.name.lower() != "readme.md" for f in (P / "drafts").glob("*.md")) if (P / "drafts").exists() else None,
    }


def _first_body_line(ex):
    for l in (ex or "").splitlines():
        s = l.strip()
        if s and not s.startswith(("#", "---", "title:", "description:", ">")):
            s = s.lstrip("-> ").strip()
            if re.fullmatch(r"\*\*[^*]+\*\*[^:：]*[:：]?\s*", s):   # "**장면**:" 처럼 값 없는 라벨 줄
                continue
            return s
    return ""


def key_line(n):
    """노드 하나의 '중요한 문장' — 사용자 본인 문장을 결정적으로 뽑는다 (모델 없음).

    C·WD 는 기준/맥락 줄, D 는 내 기준/결정 줄, evidence 는 claim, 일지·visionary 는
    '어디에 붙나'의 항목 줄. 없으면 본문 첫 줄, 그것도 없으면 제목.
    """
    t, ex = n.get("type"), n.get("excerpt") or ""
    # ⚠️AI 줄은 본인 문장이 아니다 — 후보에서 뺀다. 남는 게 없으면 빈 문자열 = "내 문장 아직 없음"
    ex = "\n".join(l for l in ex.splitlines() if "⚠️AI" not in l)
    def grab(*labels):
        # 라벨 줄 + 이어지는 들여쓴 줄(두 칸)까지 한 문장으로 — 마크다운 줄바꿈으로 끊긴 문장 복원
        for lb in labels:
            m = re.search(r"\*\*" + lb + r"\*\*[^:]*:\s*(.+(?:\n  [^\n]+)*)", ex)
            if m and m.group(1).strip():
                return re.sub(r"\s+", " ", m.group(1)).strip()
        return ""
    if t in ("C", "WD"):
        # 2026-09-17: 라벨 값은 파서가 준다 — 옛 정규식은 ⚠️AI 불릿의 **첫 줄만** 지우고 이어진
        # 들여쓴 줄을 앞 라벨 값에 붙여 AI 문장을 본인 문장으로 노출했다 (WD-01·WD-04 실측).
        it = canon_parse.item(n.get("id"), P)
        line = canon_parse.key_line(it) or canon_parse.first_body(it)
        return line
    elif t == "D":
        line = grab("내 기준", "결정", "긴장", "프로세스 지점")
    elif t == "evidence":
        m = re.search(r"^claim:\s*(.+)$", ex, re.M)
        line = m.group(1).strip() if m else ""
    elif t in ("journal", "visionary", "aihub"):
        line = grab("항목", "인사이트")
    else:
        line = ""
    if t in ("C", "WD"):
        return line or _first_body_line(ex)   # 제목으로 대체하지 않는다 — 빈 값이 정직한 상태
    return line or _first_body_line(ex) or n.get("title", "")


def git_moves(days=7):
    """최근 n일 파일별 커밋 횟수 — '들어옴(이동)' 신호. git 이 없으면 빈 dict."""
    try:
        out = subprocess.run(["git", "log", f"--since={days}.days", "--name-only", "--pretty=format:"],
                             cwd=str(P), capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    c = {}
    for l in out.splitlines():
        l = l.strip()
        if l:
            c[l] = c.get(l, 0) + 1
    return c


_struct_last = {}
_lineage_bg = {"proc": None}


def lineage_refresh(wait=False):
    """lineage_scan --json-only 를 돌린다. wait=False 면 30초 안에 돌렸거나 도는 중이면 건너뛰고 바로 돌아온다."""
    cmd = [sys.executable, str(CODE / "lineage_scan.py"), "--json-only"]
    if wait:
        with SCAN_LOCK:   # /360 의 전체 재조립과 같은 파일(.lineage-data.json)을 쓴다
            try:
                subprocess.run(cmd, capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return
    if not SCAN_LOCK.acquire(False):
        return   # 다른 재조립이 도는 중 — 폴링은 기다리지 않는다 (느린 GET 을 만들지 않는 게 이 전환의 목적)
    try:
        pr = _lineage_bg["proc"]
        if pr is not None and pr.poll() is None:
            return
        try:
            age = time.time() - (P / ".runtime/.lineage-data.json").stat().st_mtime
        except OSError:
            age = 1e9
        if age < 30:
            return
        try:
            _lineage_bg["proc"] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    finally:
        SCAN_LOCK.release()


def structure_api(fresh=False):
    """구조 탭(흐름판) 데이터 — 계보 노드·엣지 합계, 관리 신호, 이동 신호, 상자별 핵심 문장.

    2026-09-03 사용자 지시: 밖(D·수집) → 데이터 루트 축적 → 판단(정본) → 결과의 흐름을
    한 판에서 보고, 단계마다 중요한 문장을 모델 없이 꺼내 본다. lineage_scan 을 열 때마다
    재실행(--json-only, ~0.1초)해서 낡은 화면을 막는다. 2026-09-08: 재실행은 뒤에서(Popen) — 단일 스레드
    서버가 여기서 0.4~1.6초 멈추는 동안 버튼이 밀렸다(.board_slow.log). 저장 직후 재조립(fresh=1)만 기다린다.
    """
    lineage_refresh(wait=fresh)
    try:
        d = json.loads((P / ".runtime/.lineage-data.json").read_text())
        _struct_last["d"] = d
    except (OSError, json.JSONDecodeError):   # 뒤에서 쓰는 중이면 마지막 정상값
        d = _struct_last.get("d") or {"nodes": [], "edges": []}
    nodes, edges = d.get("nodes", []), d.get("edges", [])
    N = {n["id"]: n for n in nodes}
    indeg, outdeg = {}, {}
    for e in edges:
        indeg[e["dst"]] = indeg.get(e["dst"], 0) + 1
        outdeg[e["src"]] = outdeg.get(e["src"], 0) + 1
    items = {}
    for n in nodes:
        items.setdefault(n["type"], []).append({
            "id": n["id"], "title": (n.get("title") or "")[:140], "path": n.get("path", ""),
            "date": n.get("date", ""), "maturity": n.get("maturity", ""),
            "scene": n.get("scene", None) if "scene" in n else None,   # None=슬롯 없음 / ""=비어 있음
            "has_scene_slot": "scene" in n,
            "ai": [{"sha": a.get("sha"), "text": (a.get("text") or "")[:400]} for a in (n.get("ai_items") or [])],
            "line": key_line(n)[:320], "in": indeg.get(n["id"], 0), "out": outdeg.get(n["id"], 0)})
    flows = {}
    for e in edges:
        k = (N.get(e["src"], {}).get("type", "?"), N.get(e["dst"], {}).get("type", "?"), e.get("kind", ""))
        flows[k] = flows.get(k, 0) + 1
    flows = [{"src": a, "dst": b, "kind": c, "n": v}
             for (a, b, c), v in sorted(flows.items(), key=lambda x: -x[1])]
    def cnt(rel, pat):
        try:
            return len(re.findall(pat, (P / rel).read_text()))
        except OSError:
            return 0
    cw = [n for n in nodes if n["type"] in ("C", "WD")]
    rows = store.read()
    runs = runs_all()
    feed = read_jsonl(FEED)
    # 2026-09-17: 여기서 C·WD 블록을 **다시 스캔하지 않는다** — lineage 의 `ai_items` 가 이미
    # `canon_parse` 결과(본문 + 이력 아래 불릿, 같은 sha)다. 옛 코드는 lineage 가 이력 앞만 봐서
    # 서랍이 비는 것을 서버에서 메우던 보충이었고, 그 때문에 판정이 두 곳에 살았다 (Codex Q4).
    # kind 는 문자열로 판별 가능한 것만: talk = 대화 끝에 승인까지 간 문장, title = 제목이 그 문단에서 나옴.
    by_id = {x["id"]: x for arr in items.values() for x in arr}
    def ai_kind(text):
        return "talk" if "대시보드 문답" in text else "title" if "제목이 이 문단" in text else ""
    rank = {"talk": 0, "title": 1}
    for n in cw:
        item = by_id.get(n["id"])
        if item is None:
            continue
        for a, src in zip(item["ai"], (n.get("ai_items") or [])):
            a["hist"] = bool(src.get("hist"))
            a["form"] = src.get("form", "bullet")
            a["kind"] = ai_kind(a.get("text") or "")
        item["ai"].sort(key=lambda a: (rank.get(a.get("kind"), 2), 1 if a.get("hist") else 0))
    # 오늘 하나 — 사람이 오늘 손댈 것 하나만 고른다(순서: 대화 끝에 승인까지 간 ⚠️AI → 제목이 AI 문장 → 나머지 → 열린 질문).
    # 서버는 문장을 만들지 않는다 — 원문·코드·이유 표식만 넘긴다. 해소되면 다음 것이 자동으로 올라온다.
    why = {"talk": "대화 끝에 승인한 문장 — 내 말로 다시", "title": "제목이 아직 AI 문장", "": "AI 문장 그대로"}
    cands = sorted(((rank.get(a.get("kind"), 2), k, n["id"], a) for k, n in enumerate(cw) for a in (by_id.get(n["id"], {}).get("ai") or [])),
                   key=lambda t: (t[0], t[1]))
    oq_hand = [q for q in openq_list() if not ("장면" in q["q"] and len(q.get("targets") or []) > 1)]   # 장면 묶음 질문은 '장면 빈 칸'과 같은 것
    if cands:
        r, _, code, a = cands[0]
        today = {"kind": "ai", "code": code, "sha": a["sha"], "text": re.sub(r"^(\*\*[^*]+\*\*\s*)?⚠️AI[:\s]*", "", a["text"])[:160], "why": why.get(a.get("kind"), "")}
    elif oq_hand:
        q = oq_hand[0]
        today = {"kind": "openq", "id": q["id"], "code": q.get("code") or (q.get("targets") or [None])[0], "text": (q.get("short") or q["q"])[:160], "why": "열린 질문"}
    else:
        today = None
    def cntm(rel, pat):
        return cnt(rel, "(?m)" + pat)
    scene_empty = sum(1 for n in cw if not n.get("scene"))
    # ⚠️AI 셈은 `canon_parse` 한 곳 — 칩과 서랍이 **같은 목록의 길이**를 본다 (2026-09-17).
    # 항목 밖(코드 없는 자리: work-decisions 「승격 후보」 묶음 표식·career-frame.md)은 서랍에서
    # 답할 수 없으므로 칩에 섞지 않고 ai_outside 로 따로 센다 — 칩 19 대 서랍 17 사건의 종결.
    ai_bullets = canon_parse.pending_total(P)
    outside = outside_items()
    ai_outside = len(outside)
    items["AC"] = ac_items()          # 판단 열 AC 상자 (2026-09-17) — lineage 노드가 없는 정본
    seed_items = sum(cntm(f, r"^#{2,3} .*🌱") for f in ("career/decisions.md", "career/work-decisions.md", "career-frame.md"))
    manage = {
        # 옛 키(문자열 카운트)는 다른 소비자 때문에 그대로 둔다 — 화면은 아래 hand/pile 을 쓴다 (2026-09-10)
        "ai": cnt("career/decisions.md", "⚠️AI") + cnt("career/work-decisions.md", "⚠️AI"),
        "seed": cnt("career/decisions.md", "🌱") + cnt("career/work-decisions.md", "🌱") + cnt("career-frame.md", "🌱"),
        "openq": openq_count(),
        "scene_empty": scene_empty, "scene_total": len(cw),
        # 글쓰기 스레드는 목적지가 정본이 아니라 카드·drafts 다 — 미반영으로 세지 않는다 (2026-09-16, 지도와 같은 셈).
        "unseen": sum(1 for r in rows if r.get("status") == "done" and not r.get("seen_at")
                      and not r.get("applied_at") and not journal_writing.enabled(r)),
        "unapplied": sum(1 for r in rows if r.get("status") == "done" and not r.get("applied_at")
                         and not journal_writing.enabled(r)),
        "not_started": sum(1 for r in rows if r.get("status") == "pending" and r.get("source") != "feed"
                           and (r.get("ask") or "").strip() and r.get("ts") not in runs),
        "inbox": sum(1 for f in feed if f.get("status") == "pending"),
        # 손 필요 = 사람이 답해야 끝나는 것만: ⚠️AI 불릿(규칙문·범례 제외) + 열린 질문(장면 묶음 제외) + 장면은 오늘 몫 하나.
        # 쌓이는 것 = 설계상 세션을 거치며 여무는 상태(씨앗·빈 장면·인박스) — 손 필요에 안 넣는다.
        "ai_bullets": ai_bullets, "ai_outside": ai_outside, "outside": outside,
        "ac_filled": sum(b.get("filled", 0) for b in craft_progress()),
        "ac_total": sum(b.get("total", 0) for b in craft_progress()),
        "seed_items": seed_items, "openq_hand": len(oq_hand),
        "hand": ai_bullets + len(oq_hand) + (1 if scene_empty else 0),
        "pile": seed_items + scene_empty + sum(1 for f in feed if f.get("status") == "pending"),
        "today": today,
    }
    try:
        pub = sum(1 for l in (P / "drafts/published-log.md").read_text().splitlines() if l.startswith("- "))
    except OSError:
        pub = 0
    moves_between = {
        "feed_total": len(feed),
        "feed_kept": sum(1 for f in feed if f.get("status") == "kept"),
        "queue_feed": sum(1 for r in rows if r.get("source") == "feed"),
        "queue_map": sum(1 for r in rows if r.get("source") == "map"),
        "applied": sum(1 for r in rows if r.get("applied_at")),
        "applied_canon": sum(1 for r in rows if r.get("applied_at") and "career/" in (r.get("applied_note") or "")),
        "applied_insights": sum(1 for r in rows if r.get("applied_at") and "insights" in (r.get("applied_note") or "")),
        "published": pub,
        "journal_files": sum(f.name.lower() != "readme.md" for f in (P / "journal").glob("*.md")),
        "journal_linked": len(items.get("journal", [])),
        "visionary_files": sum(f.name.lower() != "readme.md" for f in (P / "visionary").rglob("*.md")),
        "visionary_linked": len(items.get("visionary", [])),
        "drafts_files": sum(f.name.lower() != "readme.md" for f in (P / "drafts").glob("*.md")),
        "insights_posts": cnt("visionary/linkedin/insights.md", r"(?m)^### "),
        "openq": openq_count(),
    }
    return {"generated": now(), "items": items, "flows": flows,
            "edges": [{"src": e["src"], "dst": e["dst"], "kind": e.get("kind", "")} for e in edges],
            "manage": manage, "moves": git_moves(), "between": moves_between}


_proc_last = {}
_proc_bg = {"proc": None}


def process_refresh(wait=False):
    """process_scan.py 를 돌린다 — lineage_refresh 와 같은 계약: wait=False 면 30초 안에 돌렸거나 도는 중이면 건너뛴다."""
    cmd = [sys.executable, str(CODE / "process_scan.py")]
    if wait:
        with SCAN_LOCK:
            try:
                subprocess.run(cmd, capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return
    if not SCAN_LOCK.acquire(False):
        return
    try:
        pr = _proc_bg["proc"]
        if pr is not None and pr.poll() is None:
            return
        try:
            age = time.time() - (P / ".runtime/.process-data.json").stat().st_mtime
        except OSError:
            age = 1e9
        if age < 30:
            return
        try:
            _proc_bg["proc"] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    finally:
        SCAN_LOCK.release()


def scan_settle(timeout=20):
    """뒤에서 도는 재조립(Popen)이 끝날 때까지 기다린다. SCAN_LOCK 을 쥔 채로만 부른다 —
    /360 · /map 의 전체 재조립이 .lineage-data.json 을 뒤에서 쓰는 중인 프로세스와 겹치면 안 된다."""
    for bg in (_lineage_bg, _proc_bg):
        pr = bg.get("proc")
        if pr is not None and pr.poll() is None:
            try:
                pr.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass


def processes_api(fresh=False):
    """구조 탭 프로세스 판 데이터 — structure_api 와 같은 모양(items·flows·edges·manage).

    2026-09-10 사용자 결정: 손으로 관리하는 인벤토리는 낡는다 → 데이터는 scripts/process_scan.py 가 코드에서 매번
    다시 뽑는다(모델 없음, 0.4초). 재실행은 lineage_refresh 처럼 뒤에서(Popen, 30초 간격)라 단일 스레드 서버가 안 멈춘다.
    SCHEDULE(손으로 쓴 표)과 plist 가 어긋나면 manage.schedule_drift 로 낸다 — 문서 낡음을 판이 스스로 드러낸다.
    """
    process_refresh(wait=fresh)
    try:
        d = json.loads((P / ".runtime/.process-data.json").read_text())
        _proc_last["d"] = d
    except (OSError, json.JSONDecodeError):
        d = _proc_last.get("d") or {"nodes": [], "edges": []}
    nodes, edges = d.get("nodes", []), d.get("edges", [])
    N = {n["id"]: n for n in nodes}
    indeg, outdeg = {}, {}
    for e in edges:
        indeg[e["dst"]] = indeg.get(e["dst"], 0) + 1
        outdeg[e["src"]] = outdeg.get(e["src"], 0) + 1
    items = {}
    for n in nodes:
        it = dict(n)
        it["in"], it["out"] = indeg.get(n["id"], 0), outdeg.get(n["id"], 0)
        items.setdefault(n["type"], []).append(it)
    flows = {}
    for e in edges:
        k = (N.get(e["src"], {}).get("type", "?"), N.get(e["dst"], {}).get("type", "?"), e.get("kind", ""))
        flows[k] = flows.get(k, 0) + 1
    flows = [{"src": a, "dst": b, "kind": c, "n": v} for (a, b, c), v in sorted(flows.items(), key=lambda x: -x[1])]
    procs = [n for n in nodes if n.get("type") != "file"]
    st_labels = {n.get("status_label") for n in procs if n.get("last_ts")}
    failing = [n["id"] for n in procs if n.get("ok") is False]
    stale = [n["id"] for n in procs if n.get("stale")]
    # 래퍼 없이 스크립트가 직접·조건부로 기록하는 잡(catch-up)은 기록 없음이 정상 — process_scan 이 self_records 로 표시한다.
    unrecorded = [s["label"] for s in SCHEDULE if s["label"] not in st_labels and not N.get(s["label"], {}).get("self_records")]
    # 표(SCHEDULE) ↔ plist 어긋남
    lw = d.get("launchd_when", {})
    norm = lambda w: "변경 시" if "변경 시" in (w or "") else (w or "")
    drift = []
    for s_ in SCHEDULE:
        if s_["label"] not in lw:
            drift.append({"label": s_["label"], "table": s_["when"], "plist": None, "why": "표에는 있는데 plist 가 없다"})
        elif norm(lw[s_["label"]]) != norm(s_["when"]):
            drift.append({"label": s_["label"], "table": s_["when"], "plist": lw[s_["label"]], "why": "시각이 다르다"})
    tbl = {s_["label"] for s_ in SCHEDULE}
    for lab, w in lw.items():
        if w and lab not in tbl:
            drift.append({"label": lab, "table": None, "plist": w, "why": "plist 에는 있는데 표에 없다"})
    manage = {"jobs": sum(1 for n in procs if n.get("type") == "launchd" and not n.get("parent")),
              "failing": failing, "unrecorded": unrecorded, "stale": stale, "schedule_drift": drift,
              "unloaded": [n["id"] for n in procs if n.get("type") == "launchd" and n.get("loaded") is False and not n.get("parent")]}
    return {"generated": now(), "scanned": d.get("generated"), "items": items, "flows": flows,
            "edges": [{"src": e["src"], "dst": e["dst"], "kind": e.get("kind", ""), "via": e.get("via", "")} for e in edges],
            "manage": manage, "moves": git_moves(), "libraries": d.get("libraries", [])}


def esc_none(t):
    """정본에 넣기 전 최소 정리 — 줄바꿈을 한 줄로 (인용 블록이 깨지지 않게)."""
    return " ".join((t or "").split())


def canon_index():
    """C·WD 항목 제목 색인 — lineage_scan 산출물 재사용 (없으면 빈 dict)."""
    try:
        d = json.loads((P / ".runtime/.lineage-data.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {n["id"]: {"title": n.get("title", ""), "maturity": n.get("maturity", "")}
            for n in d.get("nodes", []) if n.get("type") in ("C", "WD")}


def read_signals():
    try:
        return json.loads(SIGNALS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_signals(sig):
    awrite(SIGNALS, json.dumps(sig, ensure_ascii=False, indent=1))


def update_feed(item_id, patch):
    rows = read_jsonl(FEED)
    for r in rows:
        if r["id"] == item_id:
            r.update(patch)
            r["decided_at"] = now()
    write_jsonl(FEED, rows)


def update_reaction(item_id, kind):
    update_kind(REAC, [item_id], kind)


def update_kind(path, ids, kind):
    """반응·좋아요 공통 — kind(taste/record/social) 확정. ids 여러 개면 일괄(작성자 묶음)."""
    ids = set(ids)
    rows = read_jsonl(path)
    for r in rows:
        if r.get("id") in ids:
            r["kind"] = kind
    write_jsonl(path, rows)


def craft_state():
    """개인 › 자소서 — 가르침 카드 목록(답함/안 함)과 칸 진행. 재활용 입구다 (2026-09-10).

    답함 판정: 그 가르침으로 만든 스레드가 끝났나(applied_at) — 정본 파일 문장을 되짚지 않는다(사람이 손으로 고칠 수 있다)."""
    try:
        prog = craft.progress(P)
    except OSError:
        prog = []
    done = {}
    for a in store.read():
        if a.get("origin") == "lesson" and (a.get("lesson") or {}).get("feed_id"):
            fid = a["lesson"]["feed_id"]
            if a.get("applied_at"):
                done[fid] = a["applied_at"]
            else:
                done.setdefault(fid, None)
    cards = []
    for r in read_jsonl(FEED):
        les = r.get("lesson")
        if not les:
            continue
        cards.append({"id": r["id"], "room": r.get("sub") or "", "author": r.get("author") or "",
                      "title": " ".join((r.get("text") or "").split())[:60],
                      "at": r.get("published_at") or r.get("collected_at"),
                      "slot": les.get("slot"), "code": les.get("code"), "slot_name": les.get("slot_name"),
                      "question": les.get("question"), "rules": les.get("rules") or [],
                      "before": les.get("before") or "", "after": les.get("after") or "",
                      "materials": les.get("materials") or [],
                      "answered": bool(done.get(r["id"])), "asked": r["id"] in done,
                      "status": r.get("status")})
    cards.sort(key=lambda c: c["at"] or "", reverse=True)
    cards.sort(key=lambda c: c["answered"])   # 안 한 것 먼저, 그 안에서 최신순
    filled = sum(p["filled"] for p in prog)
    total = sum(p["total"] for p in prog)
    return {"cards": cards, "progress": prog, "filled": filled, "total": total,
            "todo": sum(1 for c in cards if not c["answered"]),
            "path": "career/application-craft.md"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 콘솔 소음 컷
        pass

    def handle_one_request(self):
        # 느린 요청만 남긴다 (2026-09-08 "버튼 동작이 너무 느려" — 단일 스레드라 한 요청이 늦으면 뒤가 다 밀린다).
        # 250ms 넘는 것만 scripts/.board_slow.log 에 한 줄. 화면 쪽은 localStorage slowlog.
        t = time.time()
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            # 브라우저가 폴링 중 탭을 닫거나 새로고침하면 늘 난다 — 무해한데 traceback 이 err 로그를
            # 채워 옛 오류를 현재 오류로 오독하게 만들었다 (2026-09-16 실측 74건). 다른 예외는 그대로 올린다.
            self.close_connection = True
            return
        except Exception:
            # 로그에만 남으면 아무도 안 읽는다 — 화면에 올린다 (2026-09-18). 올린 뒤 그대로 다시
            # 던져 stderr 의 traceback 은 유지한다.
            note_error(getattr(self, "path", ""), sys.exc_info()[1])
            raise
        dt = time.time() - t
        if dt > 0.25 and getattr(self, "command", None):
            try:
                with open(SLOW_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{now()} {dt * 1000:.0f}ms {self.command} {self.path}\n")
            except OSError:
                pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]   # 캐시 버스팅 쿼리(?v=…) 무시
        if path == "/":
            body = TEMPLATE.read_text().replace(   # 모델 목록·기본값은 models.json 한 자리에서 싣는다
                "/*__MODELS_JSON__*/null", json.dumps(models.client_config(), ensure_ascii=False)).encode()
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")   # WKWebView 캐시 고정 방지
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/manifest.webmanifest":   # 폰 홈 화면 앱 (PWA, 2026-09-07)
            body = json.dumps({"name": "대시보드", "short_name": "대시보드", "start_url": "/?tab=today",
                               "display": "standalone", "background_color": "#F7F8F6", "theme_color": "#186A4B",
                               "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}]},
                              ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif path == "/icon.png":
            try:
                body = (CODE / "board-icon.png").read_bytes()
            except OSError:
                return self._json({"error": "icon 없음"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif path == "/api/canon_block":   # ?code=C-05 — 항목 전문 (로컬 파싱만, 모델·재조립 없음)
            code = (parse_qs(urlparse(self.path).query).get("code") or [""])[0]
            return self._json(canon_block_api(code))
        elif path == "/api/structure":   # 구조 탭 흐름판 — 계보 재조립 + 합계 (모델 없음)
            return self._json(structure_api(fresh="fresh=1" in self.path))
        elif path == "/api/processes":   # 구조 탭 프로세스 판 — process_scan 재생성(뒤에서) + 합계 (모델 없음, 2026-09-10)
            return self._json(processes_api(fresh="fresh=1" in self.path))
        elif path == "/map" and not (CODE / "personal_map.py").exists():
            return self._json({"error": "지도는 이 kit 에 없다 (personal_map.py 미포함)"}, 404)
        elif path == "/360" and not (CODE / "personal_360_template.html").exists():
            return self._json({"error": "360 화면은 이 kit 에 없다 (personal_360_template.html 미포함)"}, 404)
        elif path == "/map":
            # Personal 지도 — 열 때마다 재조립 (결정적, ~1초). 실패하면 마지막 산출물로.
            with SCAN_LOCK:   # personal_map.py 가 .lineage-data.json 을 읽고 personal-map.html 을 쓴다
                scan_settle()
                try:
                    subprocess.run([sys.executable, str(CODE / "personal_map.py")],
                                   capture_output=True, timeout=20)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            try:
                body = (CODE / "personal-map.html").read_text().encode()
            except OSError:
                return self._json({"error": "지도 생성 실패 — python3 kit/personal_map.py 확인"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/360":
            # Personal 360 (정본 계보 뷰) — 열 때마다 lineage_scan 재조립 (결정적, 빠름)
            with SCAN_LOCK:   # lineage_scan 이 .lineage-data.json + personal-360.html 을 쓴다 — 동시에 둘이 쓰면 반쪽이 나온다
                scan_settle()
                try:
                    subprocess.run([sys.executable, str(CODE / "lineage_scan.py")],
                                   capture_output=True, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            try:
                body = (P / ".runtime/personal-360.html").read_text().encode()
            except OSError:
                return self._json({"error": "Personal 360 생성 실패 — python3 kit/lineage_scan.py 확인"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            d = state()
            with GOTO_LOCK:
                g = dict(GOTO)   # 복사 — 쓰는 쪽의 clear() 중에 직렬화하면 터진다
            if g and time.time() - g.get("at", 0) < 120:
                d["goto"] = g
            self._json(d)
        elif path == "/api/feed_read":   # ?id=… 읽기 판 (long 없으면 워커 띄움)
            q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            with WRITE_LOCK:   # GET 이지만 FEED 를 다시 쓰고 LONG_SPAWNED 를 검사-후-표시한다
                return self._json(feed_read((q.get("id") or [""])[0]))
        elif path == "/api/source_search":   # ?q=… [자료 직접 보강] 검색 칸 — 좋아요·리포스트 (모델 없음, 2026-10-04)
            q = parse_qs(urlparse(self.path).query)
            self._json({"items": journal_writing.social_search(P, (q.get("q") or [""])[0])})
        elif path == "/api/models":
            self._json(models_snapshot(force="force=1" in self.path))  # 쿼리는 원본에서
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        # 상태를 바꾸는 건 전부 여기 한 줄 아래 — 정본 .md·jsonl 이 읽고-고쳐-쓰기라
        # 두 POST 가 겹치면 한쪽 변경이 통째로 사라진다 (스레드 서버 전환의 대가, 2026-09-16).
        WRITE_LOCK.acquire()
        try:
            if self.path == "/api/feed":       # {id, status, axes?}
                patch = {"status": req["status"]}
                if "axes" in req:
                    patch["axes"] = req["axes"]
                update_feed(req["id"], patch)
            elif self.path == "/api/digest":     # {} — 인박스 전체 묶음 다시
                return self._json({"ok": True, "started": spawn_digest()})
            elif self.path == "/api/feed_long":  # {id} — 요점 다시 뽑기 (실패 기록 지우고 재실행)
                return self._json(feed_read(req["id"], retry=True))
            elif self.path == "/api/reaction":  # {id, kind}
                update_reaction(req["id"], req["kind"])
            elif self.path == "/api/like":  # {ids:[...], kind} — Threads 좋아요 태깅 (작성자 묶음 일괄 포함)
                update_kind(LIKES, req.get("ids") or [req.get("id")], req["kind"])
            elif self.path == "/api/assign":    # {source, id, lane, title, note?}
                rec = {"ts": now(), "status": "pending", **req}
                if rec.get("source") == "feed" and not journal_writing.enabled(rec):
                    # 피드 레인은 '이어서 묻기' 하나다 (2026-09-11 사용자 결정). 옛 뿌리에 저장된 레인 값은
                    # 그대로 살아 카드 이름표·후속 프롬프트로 쓰이고, 새 뿌리만 talk 로 생긴다.
                    # 글쓰기(journal_draft / lane draft)는 Linkin 의 자리라 건드리지 않는다.
                    rec["lane"] = "talk"
                if rec.get("source") == "feed" and rec.get("lane") == "talk" and not (rec.get("ask") or "").strip():
                    # 이어서 묻기는 물을 것이 있어야 한다 — 지시 없는 talk 이 들어오면 revive 가 정리(brief)를 띄운다 (2026-09-10).
                    # 판정은 **강제 뒤의 rec** 으로 한다 — req 로 보면 옛 레인 이름을 단 지시 없는 요청이 가드를 비켜
                    # talk 뿌리로 저장됐다 (2026-09-11 검증에서 잡힘).
                    return self._json({"error": "무엇을 물을지 쓴다 — 빈 채로는 못 보낸다"}, 400)
                if journal_writing.enabled(rec):
                    rec["workflow"] = journal_writing.WORKFLOW
                    if not (rec.get("ask") or "").strip():
                        rec["ask"] = journal_writing.START
                def _add(rows):   # 중복 검사와 추가가 같은 잠금 안 — 사이 끼어들기 없음
                    rec["ts"] = unique_ts(rows, rec["ts"])   # 같은 초 두 건 = 같은 기본키 방지
                    same = [r for r in rows if req.get("id") and r.get("id") == req["id"] and r.get("status") != "cancelled"]
                    if req.get("source") == "openq":   # 열린 질문: 닫힌(applied) 스레드는 같은 id 라도 새 대화 (2026-09-10)
                        same = [r for r in same if r.get("source") == "openq" and not r.get("applied_at")]
                    if same and req.get("source") in ("feed", "openq") and (req.get("ask") or "").strip() and not journal_writing.enabled(rec):
                        # 이미 스레드가 있는 글에 [Claude]로 다시 물으면 그 스레드의 되묻기로 이어진다 (2026-09-08 "질문을 했는데
                        # 그 다음 동작을 안 해" — 409 만 돌아가고 화면은 성공 토스트를 덮어썼다). 마지막 줄에 붙이고, 뜬 초안(proposal)은
                        # 대화가 이어지므로 버린다 — 다음 [완료]가 새 초안을 만든다.
                        parent = max(same, key=lambda r: r.get("ts") or "")
                        root, seen = parent, set()
                        while root.get("parent_ts") and root["ts"] not in seen:
                            seen.add(root["ts"])
                            up = next((r for r in rows if r.get("ts") == root["parent_ts"]), None)
                            if not up:
                                break
                            root = up
                        root.pop("proposal", None)
                        for k in ("lane", "title", "url", "path", "axes", "code", "question", "origin", "slot", "lesson"):
                            if parent.get(k) is not None:
                                rec[k] = parent[k]
                        rec["parent_ts"] = parent["ts"]
                        rec["followup"] = True
                        rows.append(rec)
                        return None
                    if same:
                        return "dup_id"
                    # 같은 자리에 같은 지시를 두 번 넣지 않는다 (버튼 두 번 눌림 방지, 2026-08-27)
                    if req.get("source") == "map" and any(
                            r.get("source") == "map" and r.get("path") == req.get("path")
                            and (r.get("ask") or "") == (req.get("ask") or "")
                            and r.get("status") != "cancelled"
                            and (not journal_writing.enabled(rec) or not r.get("applied_at")) for r in rows):
                        return "dup_ask"
                    rows.append(rec)
                dup = {}
                def _add_check(rows):
                    dup["why"] = _add(rows)
                    return store.STOP if dup["why"] else None
                store.mutate(_add_check)
                if dup.get("why") == "dup_id":
                    return self._json({"error": "이미 큐에 있는 글 — 그 카드에서 되묻기로 이어라"}, 409)
                if dup.get("why") == "dup_ask":
                    return self._json({"error": "같은 지시가 이미 있다 — 그 카드에서 되묻거나 [다시]"}, 409)
                if req.get("source") == "feed" and journal_writing.enabled(rec):
                    # 원문 브리핑을 먼저 만들면 외부 글이 주제를 결정한다. journal부터 실행.
                    if not (req.get("ask") or "").strip():
                        update_feed(req["id"], {"status": "kept"})
                    spawn_run(rec["ts"])
                elif req.get("source") == "feed" and (rec.get("ask") or "").strip():
                    # 지시가 있으면 지도와 같이 바로 실행 (2026-09-04 사용자 실측: "요약해봐" 하고 결과를
                    # 못 찾았다 — 브리핑만 돌고 판정을 기다리고 있었다). 보관 여부는 결과를 본 뒤 사람이 누른다.
                    spawn_run(rec["ts"])
                elif req.get("source") == "feed":
                    update_feed(req["id"], {"status": "kept"})
                    spawn_brief(rec["ts"])   # 지시 없음 = 레인 기본 — 정리(브리핑) 먼저, 판정 뒤 실행
                elif req.get("source") in ("map", "openq") and rec.get("ask"):
                    spawn_run(rec["ts"])     # 시키기 = 사람 판별 끝 — 바로 실행 (openq = 열린 질문 대화, 2026-09-10)
                return self._json({"ok": True, "ts": rec["ts"], "followup": bool(rec.get("followup"))})
            elif self.path == "/api/assignment":  # {ts, status?, ask?, model?} — ask 저장 시 실행
                def _upd(rows):
                    run_after = None
                    for r in rows:
                        if r["ts"] == req["ts"]:
                            if "status" in req:
                                r["status"] = req["status"]
                            if "model" in req:
                                r["model"] = req["model"] or None
                            if "ask" in req:
                                r["ask"] = req["ask"] or None
                                r["ask_at"] = now()
                                # 이미 끝난 건에 새 지시가 붙으면 결과를 비우고 다시 돌린다
                                if r.get("status") == "done" and req.get("ask"):
                                    r["result"] = None
                                # 기본 판정(brief.default)을 뒤집지 않고 지시를 내렸다 = 판정 수용.
                                # 기계적 기록이다 — 사용자는 틀린 것만 뒤집는다 (2026-09-02)
                                if (req.get("ask") and not r.get("take")
                                        and isinstance((r.get("brief") or {}).get("fork"), str)):
                                    r["take"] = {"verdict": "accepted", "text": None, "at": now()}
                                if req.get("ask") and r.get("source") in ("feed", "map", "openq"):
                                    run_after = r["ts"]
                    return run_after
                run_after = store.mutate(_upd)
                # 지시를 저장하면 뒷단이 바로 돈다 — 저장만 하고 멈추지 않는다
                if run_after:
                    spawn_run(run_after)
            elif self.path == "/api/take":  # {ts, text|null} — 기본 판정 뒤집기 (본인 문장) / 되돌리기
                # 정리 워커의 default.verdict 는 AI 문안이다. 사용자가 뒤집으면 그 문장이
                # 사용자 판정으로 실행 워커에 넘어간다 (run_worker.brief_block). text 없으면 되돌림.
                text = (req.get("text") or "").strip()
                def _take(rows):
                    for r in rows:
                        if r["ts"] == req["ts"]:
                            r["take"] = ({"verdict": "flipped", "text": text, "at": now()}
                                         if text else None)
                store.mutate(_take)
            elif self.path == "/api/brief_fix":  # {ts, kind: scene|link, value, on} — 연결 '아니다'
                # 기계가 엮은 일지 날짜(scene)·정본 코드(link)를 사용자가 반박한 기록.
                # 재정리 때 brief_worker.valid() 가 같은 자리로 못 돌아오게 막고,
                # 실행 워커 프롬프트에서도 빠진다. 데이터는 날짜·코드만 — 문장 생성 없음.
                kind, val = req.get("kind"), str(req.get("value") or "").strip()
                if kind not in ("scene", "link") or not val:
                    return self._json({"error": "kind(scene|link)·value 필요"}, 400)
                key = "scene" if kind == "scene" else "links"
                def _fix(rows):
                    for r in rows:
                        if r["ts"] == req["ts"]:
                            fx = r.get("brief_fix") or {"scene": [], "links": []}
                            lst = [x for x in (fx.get(key) or []) if x != val]
                            if req.get("on"):
                                lst.append(val)
                            fx[key] = lst
                            r["brief_fix"] = fx
                store.mutate(_fix)
            elif self.path == "/api/my_take":  # {ts, text} — 내 생각을 insights.md 축 섹션에
                a = next((r for r in store.read() if r["ts"] == req["ts"]), None)
                text = (req.get("text") or "").strip()
                if not a or not text:
                    return self._json({"error": "내용이 비었다"}, 400)
                ax = insights_insert(a, text)
                return self._json({"ok": True, "axis": ax})
            elif self.path == "/api/answer":   # {ts, text} — 역질문에 대한 내 답 = 데이터로 쌓기
                # 답의 목적지는 카드 출처가 정한다 (기계적): feed → insights.md 축 섹션 /
                # map(파일 작업) → 그 파일 끝 문답 블록 / 그 외 → 카드 기록만.
                # 질문(⚠️AI)과 답(내 문장, 마킹 없음)이 함께 남는다.
                a = next((r for r in store.read() if r["ts"] == req["ts"]), None)
                text = (req.get("text") or "").strip()
                if not a or not text:
                    return self._json({"error": "답이 비었다"}, 400)
                qq = ((a.get("for_me") or {}).get("question") or {}).get("q")
                dest = "카드 기록"
                if a.get("source") == "feed":
                    ax = insights_insert(a, text, question=qq)
                    dest = f"insights.md {ax}축 섹션"
                elif a.get("source") == "map" and a.get("path"):
                    tgt = (P / a["path"]).resolve()
                    if not (str(tgt).startswith(str(P)) and tgt.suffix == ".md" and tgt.exists()):
                        return self._json({"error": "붙일 파일이 없다 — 카드에만 남긴다"}, 400)
                    stamp = datetime.now(KST).strftime("%Y-%m-%d")
                    blk = [f"\n#### 대시보드 문답 ({stamp})", ""]
                    if qq:
                        blk.append(f"- Q(⚠️AI): {qq}")
                    blk += [f"- A: {text}", ""]
                    awrite(tgt, tgt.read_text().rstrip() + "\n" + "\n".join(blk))
                    dest = a["path"]
                def _rec(rows):
                    for r in rows:
                        if r["ts"] == req["ts"]:
                            r.setdefault("answers", []).append({"q": qq, "a": text, "at": now()})
                            r["seen_at"] = r.get("seen_at") or now()
                            if dest != "카드 기록":
                                r["applied_at"] = now()
                                r["applied_note"] = f"{dest} (문답 — 내 문장)"
                store.mutate(_rec)
                return self._json({"ok": True, "dest": dest})
            elif self.path == "/api/apply_line":  # {ts, text?} — 반영안 자리에 한 줄 삽입
                # 정본에 들어가는 문장의 원칙 (2026-08-28 사용자): **대화를 거쳐 확정된
                # 내 언어**다. text(사용자가 쓴 한 줄)가 오면 마킹 없이 들어가고,
                # text 없이 오면(예외 경로 — ⋯의 'AI 문장 그대로') ⚠️AI 마킹을 단다.
                # 삽입은 기계적 계약: 화이트리스트 파일 · 앵커 정확히 1회 · 한 줄만.
                a = next((r for r in store.read() if r["ts"] == req["ts"]), None)
                if not a or not a.get("apply"):
                    return self._json({"error": "반영안 없음"}, 400)
                ap = a["apply"]
                ALLOW = ["career/decisions.md", "career/work-decisions.md",
                         "visionary/linkedin/insights.md"]
                if ap.get("file") not in ALLOW:
                    return self._json({"error": "허용 밖 파일 — 세션에서"}, 400)
                target = P / ap["file"]
                try:
                    body = target.read_text()
                except OSError:
                    return self._json({"error": "파일을 읽을 수 없음"}, 400)
                anchor = ap["anchor"].strip()
                lines = body.splitlines()
                hits = [i for i, l in enumerate(lines) if l.strip() == anchor]
                if len(hits) != 1:
                    return self._json({"error": f"앵커가 {len(hits)}회 일치 — 세션에서 직접"}, 400)
                stamp = datetime.now(KST).strftime("%Y-%m-%d")
                mine = (req.get("text") or "").strip()
                if mine and len(mine) > 250:
                    return self._json({"error": "한 줄로 — 250자 이내"}, 400)
                new = (f"- {mine} (대시보드 문답 {stamp})" if mine
                       else f"- ⚠️AI {ap['line'].strip()} (대시보드 {stamp})")
                lines.insert(hits[0] + 1, new)
                awrite(target, "\n".join(lines) + ("\n" if body.endswith("\n") else ""))
                store.patch(req["ts"], applied_at=now(),
                            seen_at=a.get("seen_at") or now(),
                            applied_note=f"{ap['file']} · {anchor[:40]} "
                                         + ("(내 문장)" if mine else "(⚠️AI 삽입)"))
                return self._json({"ok": True, "inserted": new})
            elif self.path == "/api/topic_source":  # {ts(글쓰기 뿌리), topic(번호), id(수집글), on} — 글감에 소스 붙이기/떼기 (2026-09-08)
                ts, idx, fid, on = req.get("ts", ""), req.get("topic"), req.get("id", ""), bool(req.get("on", True))
                if not (ts and fid and isinstance(idx, int)):
                    return self._json({"error": "ts·topic·id 필요"}, 400)
                # src: 좋아요·리포스트(reaction|like|repost)면 같이 온다 — 없으면 수집글(옛 항목과 같다, 2026-10-04).
                # 화면 상태(S)엔 그 글들이 없어서, 붙일 때 작성자·앞부분·주소를 스냅샷으로 함께 남긴다.
                src = req.get("src") or None
                snap = None
                if src:
                    if src not in journal_writing.SOURCE_LABEL or src == "feed":
                        return self._json({"error": "모르는 자료 종류: %s" % src}, 400)
                    if on:
                        hit = journal_writing.lookup_source(journal_writing.source_index(P), fid, src)
                        if not hit:
                            return self._json({"error": "그 글을 못 찾았다"}, 404)
                        snap = {"author": hit["author"], "summary": hit["text"][:120], "url": hit["url"], "date": hit["date"]}
                out = {}
                def _ts(rows):
                    root = next((r for r in rows if r.get("ts") == ts), None)
                    if not root:
                        out["err"] = "글쓰기 건이 없다"
                        return store.STOP
                    ext = [e for e in (root.get("ext_sources") or [])
                           if not (e.get("topic") == idx and e.get("id") == fid and (e.get("src") or None) == src)]
                    if on:
                        ext.append({"topic": idx, "id": fid, "added_at": now(), **({"src": src, **snap} if src else {})})
                    root["ext_sources"] = ext
                    out["n"] = len(ext)
                store.mutate(_ts)
                if "err" in out:
                    return self._json({"error": out["err"]}, 404)
                return self._json({"ok": True, "n": out["n"]})
            elif self.path == "/api/seen":  # {ts, on} — 답을 읽었다고 표시 (안 본 답 구분용)
                store.patch(req["ts"], seen_at=now() if req.get("on", True) else None)
            elif self.path == "/api/oq_answer":  # {id, text} — 구조 탭 열린 질문에 답 (본인 타이핑) → 결정 본문 + 큐에서 이동
                text = " ".join((req.get("text") or "").split())
                if not text:
                    return self._json({"error": "내용이 비었다"}, 400)
                dest, err = openq_answer(req.get("id", ""), text, (req.get("code") or "").strip() or None)
                if err:
                    return self._json({"error": err}, 400)
                return self._json({"ok": True, "dest": dest})
            elif self.path == "/api/scene":  # {code, text} — 360 장면 슬롯 채움 (본인 타이핑)
                text = " ".join((req.get("text") or "").split())
                if not text:
                    return self._json({"error": "내용이 비었다"}, 400)
                err = canon_scene_fill(req.get("code", ""), text)
                if err:
                    return self._json({"error": err}, 400)
                return self._json({"ok": True})
            elif self.path == "/api/ai_answer":  # {code, sha, mode, text} — ⚠️AI 여물리기
                text = " ".join((req.get("text") or "").split())
                if not text:
                    return self._json({"error": "내용이 비었다"}, 400)
                if req.get("mode") not in ("replace", "append"):
                    return self._json({"error": "mode는 replace|append"}, 400)
                err = canon_ai_answer(req.get("code", ""), req.get("sha", ""), req["mode"], text)
                if err:
                    return self._json({"error": err}, 400)
                return self._json({"ok": True})
            elif self.path == "/api/complete":  # {ts, text?, final?} — [완료]: 정본에 닿는 건은 마무리 턴이 반영글 초안을 만들어 카드에 띄운다. final 이면 그대로 넣고 닫는다
                root, thread = thread_of(req.get("ts", ""))
                text = (req.get("text") or "").strip()
                if root and close_eligible(root, thread, text) and not req.get("final"):
                    # 목적지·근거 후보는 **서버가 발급한다** — 모델이 경로·앵커를 만들지 못하게 (설계 ③).
                    # 워커는 스레드에 실린 이 목록만 읽는다. 없으면(갈래 밖) 옛 마감 경로로 간다.
                    tg, pool = close_targets(root, thread), close_pool(root, thread, text)
                    store.patch(root["ts"], closing_at=now(), close_text=text, close_error=None,
                                targets=tg, close_pool=pool)
                    if not spawn(RUN_WORKER, root["ts"], "run_worker", extra=("--close", "--text", text)):
                        store.patch(root["ts"], closing_at=None)
                        return self._json({"error": "마무리 워커를 못 띄웠다"}, 500)
                    return self._json({"ok": True, "closing": True,
                                       "dest": "정리하는 중 — 내 판단을 정본 문장으로 옮겨 오면 확인하고 제출한다"})
                det = {}
                dests = None
                prop = (root or {}).get("proposal") or {}
                if req.get("final") and prop.get("version") == refine.VERSION:
                    # [제출] — 화면에서 고친 dests 를 **같은 validator 로 다시** 본다 (화면에 판정 규칙 없음).
                    ctx = {"pool": prop.get("pool") or [], "targets": root.get("targets") or [], "root": P}
                    ok, rejected, warnings = refine.validate(
                        {"version": refine.VERSION, "dests": req.get("dests") or prop.get("dests") or []},
                        ctx, strict=False)
                    if rejected and not ok:
                        return self._json({"error": "; ".join(r["reason"] for r in rejected)[:300]}, 400)
                    dests = ok
                    if not dests:
                        text = ""      # 넣을 문장이 없으면 기록 없이 닫는다
                dest, err = complete_thread(req.get("ts", ""), text, detail=det, dests=dests)
                if err:
                    return self._json({"error": err}, 400)
                # 글쓰기 카드는 보관과 동시에 한 번 다듬는다 (2026-09-16 사용자 결정).
                # 카드에 뜨는 건 다듬은 글이고, 원문은 접어 둔다 — 실패해도 보관은 이미 끝났다.
                if root and journal_writing.enabled(root):
                    archive_publish(root["ts"])
                    spawn_humanize(root["ts"])
                return self._json({"ok": True, "dest": dest, "path": det.get("path"),
                                   "anchor": det.get("anchor"), "inserted": det.get("inserted"),
                                   "kept": bool(det.get("kept"))})
            elif self.path == "/api/publish_edit":  # {ts, text} — 보관한 글 본문을 본인이 고친 그대로 저장
                root, _ = thread_of(req.get("ts", ""))
                if not root:
                    return self._json({"error": "배정 없음"}, 400)
                pub = dict(root.get("publish") or {})
                if not pub.get("text"):   # 숨김만 켜 둔 옛 카드에 빈 글을 심지 않는다
                    return self._json({"error": "아직 다듬은 글이 없다"}, 400)
                # 서버는 문장을 만들거나 고치지 않는다 — 사용자 타이핑이 곧 본문이다 (정본 출처 규칙과 같은 층).
                pub["text"] = req.get("text") or ""
                pub["edited_at"] = now()
                store.patch(root["ts"], publish=pub)
                return self._json({"ok": True})
            elif self.path == "/api/publish_hide":  # {ts, on} — 카드에서 숨김 (데이터는 그대로)
                root, _ = thread_of(req.get("ts", ""))
                if not root:
                    return self._json({"error": "배정 없음"}, 400)
                pub = dict(root.get("publish") or {})
                pub["hidden"] = bool(req.get("on"))
                store.patch(root["ts"], publish=pub)
                return self._json({"ok": True, "hidden": pub["hidden"]})
            elif self.path == "/api/humanize":  # {ts} — 보관한 글 다시 다듬기
                root, _ = thread_of(req.get("ts", ""))
                if not root:
                    return self._json({"error": "배정 없음"}, 400)
                if not journal_writing.enabled(root):
                    return self._json({"error": "글쓰기 카드가 아니다"}, 400)
                if humanize_running(root["ts"]):
                    return self._json({"error": "이미 다듬는 중이다 — 끝나면 카드에 뜬다"}, 409)
                if not spawn_humanize(root["ts"]):
                    return self._json({"error": "방금 띄웠다 — 잠시 뒤에 다시"}, 409)
                return self._json({"ok": True})
            elif self.path == "/api/draft":  # {code} — 펼친 항목의 AI 칸마다 '미리 쓴 문장' 한 장 (서랍 ②)
                code = (req.get("code") or "").strip()
                it = canon_parse.item(code, P) if code else None
                if it is None:
                    return self._json({"error": "그 항목을 못 찾았다: %s" % code}, 400)
                tg, slot_shas = draft_targets(it)
                if not tg:
                    return self._json({"error": "서랍에서 채울 AI 칸이 없다"}, 400)
                shas = sorted({s for v in slot_shas.values() for s in v})
                live = draft_live(code)
                # [다시] 는 같은 칸이라도 새로 만든다 (force) — 아니면 dup 로 돌아와 아무 일도 안 일어난다
                dup = next((r for r in live if sorted(r.get("shas") or []) == shas), None)
                if dup and not req.get("force"):
                    return self._json({"ok": True, "ts": dup["ts"], "dup": True})
                ts = now()
                row = {"ts": ts, "source": "openq", "origin": "draft", "code": code,
                       "shas": shas, "slot_shas": slot_shas,
                       "scene_empty": not any(s.get("name") == "장면" and s.get("vtext") for s in it["slots"]),
                       "title": "구조 탭 — %s %s" % (code, (it.get("title") or "")[:40]),
                       "question": "이 칸을 내 기록으로 쓰면 무엇이 되나", "ask": "", "status": "done",
                       "result": " / ".join(t["current"][:120] for t in tg)[:300],
                       "done_at": ts, "seen_at": ts,
                       "targets": tg, "targets_all": tg, "close_pool": draft_pool(it), "closing_at": ts}
                old = [r["ts"] for r in live]      # 정본이 바뀐 것 — 옛 카드는 낡음으로 두고 새로 만든다
                def _add(rows):
                    row["ts"] = unique_ts(rows, row["ts"])   # 같은 초 두 건 = 같은 기본키 방지 (Codex Q8)
                    for r in rows:
                        if r["ts"] in old:
                            r["stale_at"] = row["ts"]
                    rows.append(row)
                store.mutate(_add)
                ts = row["ts"]
                if not spawn(RUN_WORKER, ts, "run_worker", extra=("--close", "--text", "")):
                    store.patch(ts, closing_at=None, close_error="문장 만드는 워커를 못 띄웠다")
                    return self._json({"error": "문장 만드는 워커를 못 띄웠다"}, 500)
                return self._json({"ok": True, "ts": ts, "stale": len(old)})
            elif self.path == "/api/draft_apply":  # {ts, target_id, text?} — 미리 쓴 문장을 그 칸에 [반영]
                root, _ = thread_of(req.get("ts", ""))
                if not root or root.get("origin") != "draft":
                    return self._json({"error": "그 초안 카드가 없다 — 새로고침"}, 400)
                note, fields, err = draft_apply(root, req.get("target_id"), req.get("text"))
                if err:
                    return self._json({"error": err}, 400)
                store.patch(root["ts"], **fields)
                return self._json({"ok": True, "dest": note})
            elif self.path == "/api/canon_plain":  # {code, force?} — 읽기용 풀어쓰기 다시 (모델은 워커, 서버는 띄우기만)
                code = (req.get("code") or "").strip()
                if not code or canon_parse.item(code, P) is None:
                    return self._json({"error": "그 항목이 없다"}, 400)
                started = spawn_plain(code, force=bool(req.get("force")))
                return self._json({"ok": True, "started": started, "running": plain_running(code)})
            elif self.path == "/api/draft_redo":  # {ts, target_id, text} — [아니다] + 내 말 → 그 칸만 다시 쓴다
                root, _ = thread_of(req.get("ts", ""))
                if not root or root.get("origin") != "draft":
                    return self._json({"error": "그 초안 카드가 없다 — 새로고침"}, 400)
                text = " ".join((req.get("text") or "").split())
                if not text:
                    return self._json({"error": "내용이 비었다"}, 400)
                tid = req.get("target_id")
                all_tg = root.get("targets_all") or root.get("targets") or []
                tg = next((t for t in all_tg if t.get("id") == tid), None)
                if tg is None:
                    return self._json({"error": "그 자리는 후보에 없다"}, 400)
                # run_alive 는 bare ts 를 본다 — refine_close 워커의 하트비트는 "close-<ts>" 라
                # bare 로 물으면 항상 죽은 것으로 보여 이 방어가 조용히 안 걸렸다(Codex Q5).
                if root.get("closing_at") and run_alive("close-" + root["ts"])[0]:
                    return self._json({"error": "이미 문장을 만드는 중이다 — 끝나면 뜬다"}, 409)
                ts = now()
                store.patch(root["ts"],
                            answers=list(root.get("answers") or []) + [{"a": text, "at": ts, "target_id": tid}],
                            close_pool=pool_add_turn(root.get("close_pool") or [], text, "아니다 — 내 말"),
                            targets=[tg], targets_all=all_tg, closing_at=ts, close_error=None)
                if not spawn(RUN_WORKER, root["ts"], "run_worker", extra=("--close", "--text", "")):
                    store.patch(root["ts"], closing_at=None, close_error="문장 만드는 워커를 못 띄웠다")
                    return self._json({"error": "문장 만드는 워커를 못 띄웠다"}, 500)
                return self._json({"ok": True, "dest": "그 칸만 다시 쓴다"})
            elif self.path == "/api/refine_target":  # {ts, from, to} — 미리보기에서 목적지만 바꾼다 (문장은 그대로)
                root, _ = thread_of(req.get("ts", ""))
                prop = dict((root or {}).get("proposal") or {})
                if not root or prop.get("version") != refine.VERSION:
                    return self._json({"error": "정리안이 없다"}, 400)
                tg = {x["id"]: x for x in (root.get("targets") or [])}
                to = req.get("to")
                if to not in tg:
                    return self._json({"error": "그 자리는 후보에 없다"}, 400)
                dests = []
                for d in (prop.get("dests") or []):
                    if d.get("target_id") == req.get("from"):
                        d = dict(d, target_id=to, target=tg[to], label=tg[to].get("label"))
                    dests.append(d)
                prop["dests"] = dests
                store.patch(root["ts"], proposal=prop)
                return self._json({"ok": True, "dest": tg[to].get("label")})
            elif self.path == "/api/proposal_drop":  # {ts} — 초안을 버리고 대화 상태로 돌아간다 (정본 변화 없음)
                root, _ = thread_of(req.get("ts", ""))
                if not root:
                    return self._json({"error": "배정 없음"}, 400)
                store.mutate(lambda rs: [r.pop("proposal", None) for r in rs if r.get("ts") == root["ts"]])
                return self._json({"ok": True})
            elif self.path == "/api/close_final":  # {ts, text, conclusion:{line,trail}} — run_worker --close 의 초안. 정본엔 안 쓴다, 카드에 띄우기만
                root, _ = thread_of(req.get("ts", ""))
                conc = req.get("conclusion") or {}
                if root and conc.get("kind") == "own_words" and not conc.get("picked") and root.get("source") == "feed":
                    # 피드 대화에서 내 문장이 하나도 없으면 에러가 아니라 그냥 닫는다 — 정본·insights 어디에도 안 쓴다
                    # (2026-09-10: 이어서 묻기에서 정한 규칙을 피드 전 레인으로. 읽고 묻기만 한 대화가 정상이다).
                    # 열린 질문(openq)은 기존대로 close_error 로 남긴다 (미확정이 잘못 확정보다 낫다).
                    dest, err = complete_thread(root["ts"], "", dest_note="내 문장이 없어 기록 없이 닫았다")
                    if err:
                        return self._json({"error": err}, 400)
                    return self._json({"ok": True, "closed": True, "dest": dest})
                ref = req.get("refine")
                if root and isinstance(ref, dict) and ref.get("version") == refine.VERSION:
                    # 새 계약 — 워커가 이미 validate 를 거친 dests 를 준다. 서버는 카드에 띄우기만 하고
                    # 정본엔 안 쓴다 ([제출] 때 다시 검증하고 저장한다).
                    prop = {"kind": "refine", "version": refine.VERSION,
                            "dests": ref.get("dests") or [], "rejected": ref.get("rejected") or [],
                            "warnings": ref.get("warnings") or [],
                            "pool": (root.get("close_pool") or []),
                            "my_words": (req.get("text") or "").strip(), "at": now(),
                            "text": " / ".join(" ".join(str(d.get("text") or "").split())
                                               for d in (ref.get("dests") or []))}
                    if root.get("origin") == "draft":
                        # 서랍 초안 — 카드를 닫지 않는다. 다시 쓴 칸의 dest 만 갈아 끼우고 나머지는 그대로 둔다.
                        prop = draft_merge(root.get("proposal") or {}, prop)
                        # 근거 후보는 카드에 실린 것 하나다 — 다시 쓰기는 이 목록에 **덧붙이기만** 하므로
                        # 먼저 받은 dest 의 근거 id 도 그대로 맞는다 ([반영] 때 같은 pool 로 다시 검증한다).
                        prop["targets"] = root.get("targets_all") or root.get("targets") or []
                        store.patch(root["ts"], closing_at=None, close_error=None, proposal=prop,
                                    targets=prop["targets"])
                        return self._json({"ok": True, "dest": "서랍에 미리 쓴 문장을 올렸다"})
                    if not prop["dests"] and not prop["rejected"]:
                        dest, err = complete_thread(root["ts"], "", dest_note="넣을 판단이 없어 기록 없이 닫았다")
                        if err:
                            return self._json({"error": err}, 400)
                        return self._json({"ok": True, "closed": True, "dest": dest})
                    if not prop["dests"]:
                        prop["text"] = "정리가 반려됐다 — " + "; ".join(r.get("reason", "") for r in prop["rejected"])[:200]
                    store.patch(root["ts"], closing_at=None, close_error=None, proposal=prop)
                    return self._json({"ok": True, "dest": "카드에 정리안을 띄웠다 — 확인하고 제출"})
                if not (root and (conc.get("line") or "").strip()):
                    return self._json({"error": "초안 없음"}, 400)
                line, trail = " ".join(conc["line"].split()), " ".join((conc.get("trail") or "").split())
                prop = {"text": line + (f" — 경위: {trail}" if trail else ""), "line": line, "trail": trail,
                        "my_words": (req.get("text") or "").strip(), "at": now()}
                if conc.get("kind") == "own_words":   # 열린 질문: AI 문장 0 — 사용자 턴에서 고른 문장만 (2026-09-10)
                    prop.update(kind="own_words", text=line, picked=list(conc.get("picked") or []),
                                why=" ".join((conc.get("why") or "").split())[:160])
                store.patch(root["ts"], closing_at=None, close_error=None, proposal=prop)
                return self._json({"ok": True, "dest": "카드에 초안을 띄웠다 — 고쳐서 최종 제출"})
            elif self.path == "/api/ai_prune":  # {code, shas, keep} — ⚠️AI 초안 추리기: 고른 문장만 한 줄로, 나머지 삭제
                err = canon_ai_prune(req.get("code", ""), list(req.get("shas") or []), list(req.get("keep") or []))
                if err:
                    return self._json({"error": err}, 400)
            elif self.path == "/api/applied":  # {ts, on, note?} — 정본에 옮겼는지 표시
                def _mark(rows):
                    for r in rows:
                        if r["ts"] == req["ts"]:
                            if req.get("on"):
                                r["applied_at"] = now()
                                r["seen_at"] = r.get("seen_at") or now()
                                r["applied_note"] = (req.get("note") or "").strip() or None
                            else:
                                r["applied_at"] = None
                                r["applied_note"] = None
                store.mutate(_mark)
            elif self.path == "/api/forget":  # {ts} — 끝난 건 치우기 (결과도 같이 사라진다)
                out = {}
                def _forget(rows):   # 검사~삭제~묘비까지 한 잠금 안 (사이 끼어들기 없음)
                    a = next((r for r in rows if r["ts"] == req["ts"]), None)
                    if not a:
                        out["err"], out["code"] = "항목 없음", 400
                        return store.STOP
                    if a.get("status") not in ("done", "cancelled"):
                        out["err"], out["code"] = "끝난 건만 치울 수 있다", 400
                        return store.STOP
                    # 후속 질문이 달려 있으면 부모만 지우면 고아가 된다 — 자손 전체를 함께
                    doomed = {req["ts"]}
                    grew = True
                    while grew:
                        grew = False
                        for r in rows:
                            if r.get("parent_ts") in doomed and r["ts"] not in doomed:
                                doomed.add(r["ts"]); grew = True
                    kids = [r for r in rows if r["ts"] in doomed and r["ts"] != req["ts"]]
                    if any(r.get("status") not in ("done", "cancelled") for r in kids):
                        out["err"], out["code"] = "진행 중인 후속이 있다 — 끝나고 치운다", 400
                        return store.STOP
                    # 학습용 묘비 — 레코드는 지워도 '정본까지 갔는가'는 신호로 남긴다
                    if a.get("source") == "feed":
                        tomb = {"ts": a["ts"], "id": a.get("id"), "lane": a.get("lane"),
                                "status": a.get("status"),
                                "applied": bool(a.get("applied_at")), "forgotten": now()}
                        with (ASSIGN.parent / "forgotten.jsonl").open("a") as f:
                            f.write(json.dumps(tomb, ensure_ascii=False) + "\n")
                    rows[:] = [r for r in rows if r["ts"] not in doomed]
                    out["removed"] = 1 + len(kids)
                store.mutate(_forget)
                if "err" in out:
                    return self._json({"error": out["err"]}, out["code"])
                return self._json({"ok": True, "removed": out["removed"]})
            elif self.path == "/api/goto":     # {tab, sub?, ts?} — 알림 클릭: 열린 보드가 이 화면으로 간다
                with GOTO_LOCK:
                    GOTO.clear()
                    GOTO.update({"tab": req.get("tab") or "queue", "sub": req.get("sub"),
                                 "ts": req.get("ts"), "at": time.time()})
            elif self.path == "/api/session":  # {ts} — 세션 관제: 하트비트 + 전문 (2026-09-08)
                d = session_detail(req.get("ts", ""))
                if d is None:
                    return self._json({"error": "그 실행의 기록이 없다 (오래돼 지워졌거나 아직 시작 전)"}, 404)
                return self._json(d)
            elif self.path == "/api/session_kill":  # {ts} — 세션 관제에서 끊기
                kill_run(req.get("ts", ""), why="세션 관제")
                return self._json({"ok": True})
            elif self.path == "/api/revive":   # {} — 멈춘 건 지금 다시 띄우기 (감시 루프와 같은 판정)
                return self._json({"ok": True, "spawned": revive_once()})
            elif self.path == "/api/run":  # {ts, force} — 지금 실행 (지시 없으면 레인 기본)
                # 워커가 조용히 거절하던 것들을 여기서 먼저 걸러 화면에 이유를 돌려준다.
                # (예전엔 spawn만 하고 ok를 줘서, 워커가 "이미 실행 중"으로 죽어도
                #  화면은 "실행 시작"이라 떴다 — 눌러도 아무 일 없는 것처럼 보였다.)
                ts = req["ts"]
                a = next((r for r in store.read() if r["ts"] == ts), None)
                if not a:
                    return self._json({"error": "항목 없음"}, 400)
                if a.get("source") in ("map", "openq") and not (a.get("ask") or "").strip():
                    return self._json({"error": "이 건은 지시부터 적는다"}, 400)
                if a.get("source") not in ("feed", "map", "openq"):
                    return self._json({"error": "무인 실행 대상 아님 — 세션에서"}, 400)
                if req.get("force"):
                    kill_run(ts)
                elif run_alive(ts)[0]:
                    return self._json({"error": "이미 도는 중 — 멈춘 것 같으면 [강제 재시작]"}, 409)
                if not spawn_run(ts):
                    return self._json({"error": "워커를 못 띄웠다 — scripts/.run_worker.err.log"}, 500)
            elif self.path == "/api/make_apply":  # {ts} — 결과에서 반영안 뽑기
                a = next((r for r in store.read() if r["ts"] == req["ts"]), None)
                if not a or not a.get("result_body"):
                    return self._json({"error": "결과가 없다"}, 400)
                if run_alive(req["ts"])[0]:
                    return self._json({"error": "이미 도는 중 — 끝나고 다시"}, 409)
                if not spawn_apply(req["ts"]):
                    return self._json({"error": "워커를 못 띄웠다"}, 500)
            elif self.path == "/api/rebrief":  # {ts} — 브리핑 다시
                if run_alive(req["ts"])[0]:
                    return self._json({"error": "이미 도는 중 — 끝나고 다시"}, 409)
                if not spawn_brief(req["ts"]):
                    return self._json({"error": "워커를 못 띄웠다 — scripts/.brief_worker.err.log"}, 500)
            elif self.path == "/api/followup":  # {ts, ask} — 결과 보고 되묻기
                out = {}
                def _fu(rows):   # 부모 확인·중복 검사·추가가 한 잠금 안
                    parent = next((r for r in rows if r["ts"] == req["ts"]), None)
                    if not parent:
                        out["err"], out["code"] = "원 항목 없음", 400
                        return store.STOP
                    if any(r.get("parent_ts") == parent["ts"]
                           and (r.get("ask") or "") == (req.get("ask") or "")
                           and r.get("status") != "cancelled" for r in rows):
                        out["err"], out["code"] = "같은 되묻기가 이미 있다", 409
                        return store.STOP
                    rec = {"ts": unique_ts(rows, now()), "status": "pending", "lane": parent.get("lane"),
                           "source": parent.get("source"), "id": parent.get("id"),
                           "title": parent["title"], "ask": req["ask"],
                           "parent_ts": parent["ts"], "url": parent.get("url"),
                           "path": parent.get("path"), "axes": parent.get("axes")}
                    if journal_writing.enabled(parent):
                        # [이어서 쓰기] (2026-10-04) — 보관한(닫힌) 글은 resume 일 때만, 그 스레드의 마지막 행에서만 잇는다.
                        # root.publish 는 여기서 안 건드린다. 다시 [완료]하면 /api/complete 가 옛 판을 publish_history 로 민다.
                        resume = bool(req.get("resume"))
                        if resume:
                            wroot, wthread = thread_of(parent["ts"], rows)
                            live = [r for r in wthread if r.get("status") != "cancelled"]
                            if not parent.get("applied_at") or not live or live[-1]["ts"] != parent["ts"]:
                                out["err"], out["code"] = "보관한 글의 마지막 결과에서만 이어 쓸 수 있다", 409
                                return store.STOP
                            if humanize_running(wroot["ts"]):
                                out["err"], out["code"] = "글을 다듬는 중이다 — 끝난 뒤에 이어 쓰기", 409
                                return store.STOP
                        if (parent.get("status") != "done" or (parent.get("applied_at") and not resume)
                                or any(r.get("parent_ts") == parent["ts"] and r.get("status") != "cancelled" for r in rows)):
                            out["err"], out["code"] = "최신 글쓰기 결과에서 이어가 주세요", 409
                            return store.STOP
                        rec["workflow"] = journal_writing.WORKFLOW
                        _pm = parent.get("model") or parent.get("ran_model")
                        rec["model"] = models.resolve(_pm) if _pm else None   # 옛 ID 대물림 차단 — 읽는 시점 변환
                        if "selected_topic" in parent:
                            rec["selected_topic"] = parent["selected_topic"]
                        elif resume:   # 부모에 없으면 스레드에서 가장 최근 선택을 잇는다
                            _sel = next((r for r in reversed(wthread) if "selected_topic" in r), None)
                            if _sel:
                                rec["selected_topic"] = _sel["selected_topic"]
                        if resume:
                            rec["resume"] = True
                        if req.get("refresh_topics"):
                            rec["selected_topic"] = None
                            rec["refresh_topics"] = True
                            root, _ = thread_of(parent["ts"], rows)
                            if root.get("ext_sources"):
                                root.setdefault("source_history", []).append({"at": now(), "ext_sources": root.pop("ext_sources")})
                        if "topic_index" in req:
                            _, thread = thread_of(parent["ts"], rows)
                            candidates = []
                            for result in reversed(thread):
                                writing = result.get("writing") or {}
                                if result.get("status") != "done" or not writing:
                                    continue
                                if writing.get("stage") == "topics":
                                    candidates = writing.get("topics") if isinstance(writing.get("topics"), list) else []
                                    break
                                # stage가 없던 예전 결과는 nonempty topics를 계속 사용한다.
                                if isinstance(writing.get("topics"), list) and writing["topics"]:
                                    candidates = writing["topics"]
                                    break
                            idx = req["topic_index"]
                            if type(idx) is not int or not 0 <= idx < len(candidates):
                                out["err"], out["code"] = "글감 번호가 맞지 않는다", 400
                                return store.STOP
                            rec["selected_topic"] = {"index": idx, "topic": candidates[idx]}
                    rows.append(rec)
                    out["rec"] = rec
                store.mutate(_fu)
                if "err" in out:
                    return self._json({"error": out["err"]}, out["code"])
                if out["rec"].get("source") in ("feed", "map", "openq"):
                    spawn_run(out["rec"]["ts"])   # 후속 질문도 바로 돈다
                return self._json({"ok": True, "ts": out["rec"]["ts"]})   # 세션 관제가 새 실행으로 갈아탄다
            elif self.path == "/api/session_forget":  # {ts} | {all: true} — 끝난 세션 기록 지우기 (하트비트만, 결과는 그대로)
                runs = runs_all(include_old_failed=True)   # 화면에서 뺀 옛 실패도 같이 지운다
                if req.get("all"):
                    targets = [t for t, r in runs.items() if r.get("state") != "running"]
                else:
                    r = runs.get(req.get("ts", ""))
                    if not r:
                        return self._json({"error": "그 기록이 없다"}, 404)
                    if r.get("state") == "running":
                        return self._json({"error": "도는 중인 건 못 지운다 — 먼저 끊기"}, 400)
                    targets = [req["ts"]]
                for t in targets:
                    try:
                        worker_status._path(t).unlink()
                    except OSError:
                        pass
                return self._json({"ok": True, "removed": len(targets)})
            elif self.path == "/api/mute":  # {kind: author|token, value, on}
                sig = read_signals()
                key = "approved_mutes" if req["kind"] == "author" else "approved_tokens"
                lst = sig.setdefault(key, [])
                if req.get("on"):
                    if req["value"] not in lst:
                        lst.append(req["value"])
                elif req["value"] in lst:
                    lst.remove(req["value"])
                write_signals(sig)
            elif self.path == "/api/logs":       # {key} — 워커 로그 꼬리 (화이트리스트)
                t = tail_log(req.get("key", "run"))
                if t is None:
                    return self._json({"error": "그런 로그 없음"}, 400)
                return self._json({"ok": True, "log": t})
            elif self.path == "/api/errs_seen":  # {} — 서버 오류 띠 닫기 (센 것만 본 것으로)
                with ERRS_LOCK:
                    ERRS["seen"] = ERRS["n"]
                return self._json({"ok": True})
            elif self.path == "/api/restart":    # 보드 서버 재시작 (launchd가 즉시 되살린다)
                restart_server()
                return self._json({"ok": True})
            elif self.path == "/api/learn":  # 판별 결과 재학습
                subprocess.run(["python3", str(CODE / "feed_learn.py")],
                               capture_output=True, timeout=60)
            elif self.path == "/api/trained":  # {id, on} — 원문에 좋아요 했음 (사람 손)
                update_feed(req["id"], {"trained": bool(req.get("on"))})
            elif self.path == "/api/open_url":  # {url} — 기본 브라우저로 (화이트리스트만)
                u = str(req.get("url") or "")
                # LinkedIn 원문 + 로컬 도구(세션 관제판)만. 그 밖은 거절.
                if not re.match(r"^(https://([a-z]{2}\.)?linkedin\.com/|http://127\.0\.0\.1:8898(/|$))", u):
                    return self._json({"error": "허용 밖 URL"}, 400)
                subprocess.Popen(["open", u])
            elif self.path == "/api/axis_page":  # {axis, force} — 축 한 장. 있으면 **바로 열고** 낡았으면 뒤에서 다시 쓴다
                axis = str(req.get("axis") or "").strip()
                if not re.fullmatch(r"[A-Z](?:-\d+)?", axis):
                    return self._json({"error": "축 코드가 아니다"}, 400)
                st = axis_page.page_state(axis)
                running = axis_running(axis)
                if st["missing"]:      # 처음 쓰는 축만 기다림이 있다 — 끝나면 워커가 연다
                    if running:
                        return self._json({"ok": True, "running": True})
                    return self._json({"ok": True, "missing": True, "started": spawn_axis(axis, "open")})
                # 써 둔 장이 있으면 **뒤에서 다시 쓰는 중이어도 지금 판을 연다** — 기다리게 하지 않는다
                open_local(axis_page.page_path(axis))
                force = bool(req.get("force"))
                spawned = False
                if not running:
                    spawned = spawn_axis(axis, "force") if force else (
                        spawn_axis(axis, "stale") if st["stale"] else False)
                return self._json({"ok": True, "opened": True, "stale": st["stale"],
                                   "forced": force, "spawned": spawned, "running": running})
            elif self.path == "/api/open":  # {path} — 산출물 열기 (로컬 전용)
                target = Path(req["path"]).expanduser()
                if not target.is_absolute():      # 구조 탭은 프로젝트 상대경로를 준다
                    target = P / target
                allowed = (P,)
                if not any(str(target.resolve()).startswith(str(a)) for a in allowed):
                    return self._json({"error": "허용 밖 경로"}, 400)
                open_local(target)
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"ok": True})
        except (KeyError, OSError) as e:
            self._json({"error": str(e)}, 400)
        finally:
            WRITE_LOCK.release()


class _Srv(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """요청마다 스레드 (2026-09-16, ISSUE-202609-SINGLE-THREAD-SLOW-GET).

    바뀌기 전엔 /360 의 lineage_scan 재실행 한 번이 뒤따르는 POST 를 전부 줄 세웠다
    (BUTTON-NO-FEEDBACK 원인 ③). 쓰기는 WRITE_LOCK 으로 여전히 직렬이고, 느린 읽기만 흩어진다.
    daemon_threads: /api/restart 의 os._exit(0) 가 스레드를 기다리지 않는다."""
    daemon_threads = True
    allow_reuse_address = True


def main():
    worker_status.rotate_err_log("checkpoint_board")   # 커졌으면 시작 때 한 번 회전 (.1 하나)
    try:
        srv = _Srv(("127.0.0.1", PORT), H)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # launchd KeepAlive 가 10초마다 되살리며 traceback 을 쌓던 자리 (2026-09-16, 86건)
        print(f"{PORT} 을 다른 인스턴스가 쓰고 있다 — 수동 실행분을 끄거나 /api/restart 를 쓴다 "
              f"(launchd 가 이미 띄웠으면 이 프로세스는 필요 없다)", file=sys.stderr)
        return 0
    with srv:
        url = f"http://127.0.0.1:{PORT}"
        print(f"체크포인트 보드: {url}  (Ctrl+C 종료)")
        if "--no-open" not in sys.argv:
            webbrowser.open(url)
        threading.Thread(target=revive_loop, daemon=True).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
