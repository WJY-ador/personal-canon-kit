"""launchd 파이프라인 상태 — 기록·조회·큐 적재 (모델 호출 없음, stdlib만).

2026-08-11 이원화 배경: 무인 증류가 5연속 조용히 죽었다 (07-14~08-11, 성공은 수동
재실행뿐). 세션 시작 0단계가 로그 tail 대신 이 파일 하나를 읽는다.

CLI:
  python3 pipeline_status.py record --job X --exit-code N --secs S
  python3 pipeline_status.py report            # 세션 시작용 한 줄 요약
  python3 pipeline_status.py queue-merge --queue F --detected YYYY-MM-DD  (stdin=파일 목록)
  python3 pipeline_status.py defer --job X [--cwd D] -- cmd...   # 덮개 닫힘 — 실행 대신 미룸
  python3 pipeline_status.py deferred-list     # JSON {job: {ts, cmd, cwd}}
  python3 pipeline_status.py clear-deferred --job X

미룸(2026-10-04): 덮개 닫힌 동안 Power Nap DarkWake(약 2초)가 놓친 잡을 띄우면 잡이 실행 도중
얼어붙는다(실측: 수집 잡 988초 exit 5). run_with_status.sh 가 덮개 닫힘이면 실행 대신
슬롯의 `deferred` 필드에 명령을 남기고, catch_up.py 가 덮개 열린 뒤 차례로 다시 돌린다.
history 에는 넣지 않는다 — 실행이 아니고, ts 를 바꾸면 정체 판정이 틀어진다.
"""
import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
STATUS = str(paths.root() / ".runtime" / ".pipeline-status.json")
HISTORY_CAP = 8
STALE_DAYS = 9  # 주 1회 잡 기준 — 한 주기 + 이틀 여유

# exit 코드의 잡별 의미 — 0/실패 이분법이 아닌 잡은 여기 등록한다.
PENDING_CODES = {
    "career-evidence-distill": {4: "판별대기"},
    "threads-reposts": {4: "og 강등 — --backfill 필요", 6: "Threads API 토큰 만료 — 재발급 뒤 다음 회차"},
    "linkedin-collect": {4: "재로그인 필요 — zsh scripts/linkedin_relogin.sh"},
    # 수집기 규약(0=신규 · 3=신규 없음 · 4=재로그인 · 5=점검) 중 3 은 정상이다.
    "threads-likes": {3: "신규 좋아요 없음", 4: "Threads 재로그인 — 브라우저에서"},
    # 축 한 장 — 재료(글·정본·대화)가 그대로면 모델을 안 부르고 3. 5 는 어느 축이 계약에 걸렸다는 뜻.
    "axis-pages": {3: "재료 안 바뀜"},
    # 주간 트렌드 스캔 — 3(전 레인 부족)·4(수집 미완)는 등록하지 않는다: 3 은 QUIET_CODE 라 등록하면 초록이 된다.
    # 5·6 은 judgment 까지 끝났지만 한 레인이 비었다 (2026-09-29 canon 25건 전부 seen). 쿼리 교체는 사람 몫.
    "threads-trend-scan": {5: "canon 쿼리 소진 — trend-interests.md 교체 필요",
                           6: "일부 레인 후보 부족 — 나머지 레인만 판단"},
    # 따라잡기 — 미룬 잡을 한 건 이상 돌렸을 때만 기록한다. 0 = 다 돌림, 3 = 덮개가 다시 닫혔거나
    # 다른 잡이 도는 중이라 남은 것을 다음 회차로 넘김. 돌린 잡의 결과는 그 잡 자리에 따로 남는다.
    "catch-up": {3: "남은 것 다음 회차로"},
}

# 3 은 규약상 '할 일이 없었다' — 사람이 볼 것이 없다. 등록된 나머지 코드(4=재로그인·5=점검)는
# 돌긴 돌았지만 사람 손이 있어야 끝난다 (2026-09-15: 재로그인 4 가 초록이라 이틀을 못 봤다).
QUIET_CODE = 3


def entry_state(job, code):
    """실행 한 건의 상태 — ("ok"|"attn"|"fail", 화면에 그대로 쓰는 말)."""
    if code == 0:
        return "ok", "성공"
    why = PENDING_CODES.get(job, {}).get(code)
    if why is None:
        return "fail", "실패 exit=%d" % code
    return ("ok", why) if code == QUIET_CODE else ("attn", why)


def deferred_word(slot):
    """미룬 잡의 화면 문구 — 없으면 None. 대기이지 실패가 아니다 (2026-10-04)."""
    d = (slot or {}).get("deferred")
    if not d:
        return None
    ts = d.get("ts", "")
    when = ts[11:16]
    if ts[:10] != datetime.now().strftime("%Y-%m-%d"):
        when = ts[5:10].replace("-", "/") + " " + when
    return "미룸 — 덮개 닫힘, 열리면 따라잡기 (%s)" % when


def _locked_update(fn):
    lock = STATUS + ".lock"
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        data = {}
        if os.path.exists(STATUS):
            try:
                data = json.load(open(STATUS, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        fn(data)
        tmp = STATUS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATUS)


def record(job, exit_code, secs):
    entry = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "exit": exit_code, "secs": secs}

    def upd(data):
        slot = data.setdefault(job, {})
        slot.update(entry)
        # 실행이 끝났으면 미룬 것은 채워졌다 — 정규 회차가 먼저 돌아도 따라잡기가 또 돌리지 않게
        slot.pop("deferred", None)
        hist = slot.setdefault("history", [])
        hist.append(entry)
        del hist[:-HISTORY_CAP]

    _locked_update(upd)


def defer(job, cmd, cwd):
    """실행 대신 미룸 — 같은 잡이 여러 번 미뤄지면 최신 명령 한 건으로 합친다."""
    d = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "cmd": list(cmd), "cwd": cwd}
    _locked_update(lambda data: data.setdefault(job, {}).__setitem__("deferred", d))


def deferred_all():
    if not os.path.exists(STATUS):
        return {}
    try:
        data = json.load(open(STATUS, encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {j: s["deferred"] for j, s in data.items() if isinstance(s, dict) and s.get("deferred")}


def take_deferred(job):
    """잠금 안에서 꺼내며 지운다 — 돌리기 직전에 불러 두 번 돌지 않게. 없으면 None."""
    out = {}

    def upd(data):
        slot = data.get(job)
        if isinstance(slot, dict) and slot.get("deferred"):
            out["d"] = slot.pop("deferred")
            if not slot:
                del data[job]

    _locked_update(upd)
    return out.get("d")


def report():
    if not os.path.exists(STATUS):
        print("상태 파일 없음 — 아직 기록된 실행이 없다:", STATUS)
        return
    data = json.load(open(STATUS, encoding="utf-8"))
    now = datetime.now()
    problems = 0
    for job, s in sorted(data.items()):
        wait = deferred_word(s)
        if "ts" not in s:  # 미루기만 하고 아직 한 번도 안 돈 잡
            print("%-26s %s" % (job, wait or "실행 기록 없음"))
            continue
        ts = datetime.strptime(s["ts"], "%Y-%m-%d %H:%M:%S")
        age = (now - ts).days
        kind, state = entry_state(job, s["exit"])
        if wait:  # 대기 — 덮개 열리면 따라잡기가 돌린다. 직전 판정은 뒤에 붙인다
            state = wait + " / 직전 " + state
        elif kind != "ok":
            problems += 1
        elif age > STALE_DAYS:
            state = "정체 %d일" % age
            problems += 1
        fails = sum(1 for h in s.get("history", []) if entry_state(job, h["exit"])[0] != "ok")
        print("%-26s %-10s %s (%d일 전, %ds, 최근 %d회 중 손 필요 %d)"
              % (job, state, s["ts"], age, s["secs"], len(s.get("history", [])), fails))
    sys.exit(1 if problems else 0)


def queue_merge(queue_path, detected):
    files = [ln.strip() for ln in sys.stdin if ln.strip()]
    q = []
    if os.path.exists(queue_path):
        try:
            q = json.load(open(queue_path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            q = []
    known = {e["file"] for e in q}
    added = 0
    for f in files:
        if f not in known:
            q.append({"file": f, "detected": detected})
            known.add(f)
            added += 1
    tmp = queue_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(q, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, queue_path)
    print("큐 적재: 신규 %d, 총 %d건 -> %s" % (added, len(q), queue_path))


if __name__ == "__main__":
    # defer 의 명령은 첫 `--` 뒤 전부 — argparse 에 넘기지 않는다 (공백 인자·`-`로 시작하는 인자 보존)
    argv = sys.argv[1:]
    tail = []
    if "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--job", required=True)
    r.add_argument("--exit-code", type=int, required=True)
    r.add_argument("--secs", type=int, default=0)
    sub.add_parser("report")
    qm = sub.add_parser("queue-merge")
    qm.add_argument("--queue", required=True)
    qm.add_argument("--detected", required=True)
    df = sub.add_parser("defer")
    df.add_argument("--job", required=True)
    df.add_argument("--cwd", default=None)
    sub.add_parser("deferred-list")
    cd = sub.add_parser("clear-deferred")
    cd.add_argument("--job", required=True)
    a = p.parse_args(argv)
    if a.cmd == "record":
        record(a.job, a.exit_code, a.secs)
    elif a.cmd == "report":
        report()
    elif a.cmd == "defer":
        if not tail:
            p.error("defer 는 -- 뒤에 명령이 있어야 한다")
        defer(a.job, tail, a.cwd or os.getcwd())
    elif a.cmd == "deferred-list":
        print(json.dumps(deferred_all(), ensure_ascii=False, indent=1))
    elif a.cmd == "clear-deferred":
        take_deferred(a.job)
    else:
        queue_merge(a.queue, a.detected)
