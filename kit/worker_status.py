#!/usr/bin/env python3
"""워커 진행 상태(하트비트) — 뒷단이 도는 걸 앞단이 보게 하는 자리.

파일: _workspace/checkpoint/runs/<ts>.json  (배정 1건당 1개, 최신 실행으로 덮어씀)
쓰는 쪽: brief_worker.py / run_worker.py
읽는 쪽: checkpoint_board.py `/api/state` → 보드 카드의 진행 표시

상태 파일은 **표시 전용**이다. 정본도 큐도 아니므로 지워도 데이터는 안 잃는다.
"""
import fcntl, json, os, random, shutil, subprocess, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)

KST = timezone(timedelta(hours=9))
RUNS = paths.root() / "_workspace/checkpoint/runs"
KEEP_SECONDS = 6 * 3600   # 끝난 기록은 6시간 뒤 청소
KEEP_FAILED_SECONDS = 7 * 24 * 3600   # 실패만 7일 — 사후 진단 수명은 화면 수명과 다르다 (2026-09-16)
FAIL_LOG = paths.root() / ".runtime/.worker_fail.log"   # 실패 회차 한 줄 요약 (삭제 직전 append)
ERR_LOG_MAX = 256 * 1024   # stderr 로그가 이보다 크면 프로세스 시작 때 한 번 회전 (.1 하나만 유지)
BOARD_URL_FILE = paths.root() / ".runtime/.board-url"   # Tailscale 로 여는 판 주소 (한 줄, 2026-09-07). 없으면 폰 알림 없음
MAX_LOG = 12              # 마지막 12줄만 (화면에 그만큼만 뜬다)
# 알림 첫 줄 — 무작위로 하나 (2026-09-07 "좀 귀엽게")
OK_LINES = ["🐣 다 했어요!", "🌱 답 나왔어요", "🍞 따끈한 결과예요", "🎈 끝났어요, 보러 와요", "🐾 다녀왔어요"]
BAD_LINES = ["🥺 이번엔 못 했어요", "🌧 중간에 넘어졌어요", "🐢 멈춰 버렸어요"]


def _self_err_path():
    """자기 stderr 가 붙은 실제 파일 경로 — macOS `fcntl F_GETPATH`. tty·파이프·/dev/null 이면 None."""
    try:
        if os.isatty(2):
            return None
    except OSError:
        return None
    try:
        raw = fcntl.fcntl(2, getattr(fcntl, "F_GETPATH", 50), bytes(1024))
        p = Path(raw.split(b"\0")[0].decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    try:
        return p if p.is_file() else None
    except OSError:
        return None


def _rotate_file(p, max_bytes=ERR_LOG_MAX, fd=None):
    """`p` 가 max_bytes 를 넘을 때만 `<p>.1` 로 복사하고 제자리에서 비운다.

    이름을 바꾸지 않는다 — launchd 가 쥔 fd 는 inode 를 따라가므로 rename 하면 이후 출력이
    `.1` 로 들어가고 본 파일이 안 생긴다. 복사 + `truncate(0)` 이라 fd 는 같은 파일을 계속 쓴다.
    `.1` 은 하나만 유지(덮어씀). 넘지 않으면 아무것도 안 한다 — 재시작마다 자르면 직전 오류가 사라진다.
    """
    try:
        if p.stat().st_size <= max_bytes:
            return None
    except OSError:
        return None
    bak = p.with_name(p.name + ".1")
    try:
        shutil.copyfile(str(p), str(bak))
        os.truncate(str(p), 0)
        if fd is not None:
            # O_APPEND 가 아니면 offset 이 남아 널 패딩이 생긴다 — 직접 0 으로 돌린다
            try:
                if not fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_APPEND:
                    os.lseek(fd, 0, os.SEEK_SET)
            except OSError:
                pass
    except OSError:
        return None
    return bak


def rotate_err_log(name=None, max_bytes=ERR_LOG_MAX):
    """프로세스 시작 시 한 줄 호출 — 자기 stderr 로그가 커졌으면 회전한다.

    경로는 자기 fd 2 에서 얻고(F_GETPATH), 못 얻으면 launchd plist 와 같은 규칙
    `scripts/.<name>.err.log` 로 물러선다. 터미널 실행이면 아무것도 안 한다.
    """
    p = _self_err_path()
    fd = 2 if p else None
    if p is None and name:
        cand = paths.root() / (".runtime/.%s.err.log" % name)
        p = cand if cand.is_file() else None
    if p is None:
        return None
    return _rotate_file(p, max_bytes, fd=fd)


def _log_failed(d):
    """삭제 직전의 실패 회차를 한 줄로 남긴다 — runs/*.json 이 사라져도 사후 진단이 된다."""
    try:
        at = datetime.fromtimestamp(d.get("ended") or d.get("updated") or time.time(), KST)
        last = ((d.get("log") or [""])[-1] or "").replace("\n", " ")[:200]
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write("\t".join([at.strftime("%Y-%m-%d %H:%M"), str(d.get("ts", "")),
                               str(d.get("kind", "")), str(d.get("model", "")),
                               str(d.get("phase", "")), last]) + "\n")
        _rotate_file(FAIL_LOG)
    except (OSError, ValueError, OverflowError):
        pass


def _path(ts):
    return RUNS / (ts.replace(":", "").replace("+", "_") + ".json")


class Beat:
    """한 배정의 실행 한 번. with 블록을 벗어나면 반드시 끝 상태가 남는다."""

    def __init__(self, ts, kind, title="", model="", quiet=False):
        self.ts, self.kind, self.quiet = ts, kind, quiet   # quiet: 끝나도 맥 알림 없음 (읽기 판 요점처럼 사람이 화면을 보고 있는 실행)
        self.retry_note = ""   # 실패 알림에 덧붙일 말 ("곧 다시 해볼게요 2/3") — run_worker 가 채운다
        self.d = {"ts": ts, "kind": kind, "title": title, "model": model, "phase": "시작",
                  "log": [], "pid": os.getpid(), "state": "running",
                  "started": time.time(), "updated": time.time(), "error": None}
        RUNS.mkdir(parents=True, exist_ok=True)
        self._flush()

    def _flush(self):
        self.d["updated"] = time.time()
        try:
            _path(self.ts).write_text(json.dumps(self.d, ensure_ascii=False))
        except OSError:
            pass

    def phase(self, text):
        self.d["phase"] = text
        self._flush()

    def sid(self, session_id):
        """claude 세션 ID — 관제판(agent-log-viewer)에서 이 실행의 전문을 여는 열쇠."""
        if session_id:
            self.d["sid"] = session_id
            self._flush()

    def log(self, line):
        line = line.strip()
        if not line:
            return
        self.d["log"] = (self.d["log"] + [line])[-MAX_LOG:]
        self._flush()

    def finish(self, ok, msg=""):
        self.d["state"] = "done" if ok else "failed"
        self.d["phase"] = msg or ("완료" if ok else "실패")
        if not ok:
            self.d["error"] = msg
        self.d["ended"] = time.time()
        self._flush()
        if not self.quiet:
            self._notify(ok)
            self._notify_phone(ok)

    def _notify_phone(self, ok):
        """폰 — Slack 봇 메시지에 딥링크(?tab=&focus=). 판 주소 파일이 있을 때만 (Tailscale 켠 뒤)."""
        try:
            base = BOARD_URL_FILE.read_text().strip().rstrip("/")
        except OSError:
            return
        if not base:
            return
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            import slack_notify
            tok = slack_notify.bot_token()
            if not tok:
                return
            what = {"brief": "정리", "run": "답", "apply": "반영안", "feed-digest": "인박스 묶음",
                    "axis": "축 한 장"}.get(self.kind, self.kind)
            tab = {"feed-digest": "today", "axis": "today"}.get(self.kind, "queue")
            from urllib.parse import quote
            link = f"{base}/?tab={tab}" + (f"&focus={quote(self.ts, safe='')}" if tab == "queue" else "")
            title = (self.d.get("title") or "").strip().replace("\n", " ")[:50]
            head = "🐣 다 했어요" if ok else "🥺 못 했어요"
            slack_notify.post_as_bot(tok, f"{head} · {what} — {title}\n{link}")
        except Exception:
            pass

    def _notify(self, ok):
        """끝나면 맥 알림 — 보드를 쳐다보고 있지 않아도 안다. 말투는 귀엽게 (2026-09-07 사용자 요청)."""
        what = {"brief": "정리", "run": "답", "apply": "반영안", "feed-digest": "인박스 묶음",
                "axis": "축 한 장"}.get(self.kind, self.kind)
        secs = int(self.d["ended"] - self.d["started"])
        took = f"{secs // 60}분 {secs % 60}초" if secs >= 60 else f"{secs}초"
        head = random.choice(OK_LINES if ok else BAD_LINES)
        title = (self.d.get("title") or "").strip().replace('"', "'").replace("\n", " ")
        title = (title[:38] + "…") if len(title) > 38 else title
        if ok:
            body = f"{what} 나왔어요 · {took} 걸렸어요"
        else:
            body = f"{what}을 못 냈어요 · {took}" + (f" · {self.retry_note}" if self.retry_note else "")
        if title:
            body = f"「{title}」\n{body}"
        # 클릭하면 대시보드 창으로 (2026-09-04 사용자 실측: osascript 알림은 클릭하면 Script Editor 가
        # 열렸다 — 보낸 주인이 osascript 라서). terminal-notifier 가 있으면 그걸로 보내고 클릭 시
        # 대시보드.app 을 연다(applet 이 float 창을 띄우거나 앞으로 가져온다). 없으면 예전 방식.
        tn = shutil.which("terminal-notifier") or "/opt/homebrew/bin/terminal-notifier"
        try:
            sent = False
            if os.path.exists(tn):
                # 클릭 → 보드에 "이 카드로" 를 남기고(/api/goto) 앱을 앞으로. 열린 보드는 다음 폴링에 그 탭·카드로 간다.
                tab = {"brief": "queue", "run": "queue", "apply": "queue", "feed-digest": "today",
                       "axis": "today"}.get(self.kind, "queue")
                goto = json.dumps({"tab": tab, "ts": self.ts if tab == "queue" else None}, ensure_ascii=False).replace("'", "")
                r = subprocess.run([tn, "-title", f"대시보드 {head}", "-message", body,
                                    "-group", f"checkpoint-{self.kind}",
                                    "-execute", f"curl -s -m 3 -X POST http://127.0.0.1:8787/api/goto "
                                                f"-H 'Content-Type: application/json' -d '{goto}' >/dev/null; open /Applications/대시보드.app"],
                                   capture_output=True, text=True, timeout=5)
                # 알림 권한이 없으면 exit 3 + "not allowed" — 그때는 예전 방식으로라도 알린다
                sent = r.returncode == 0 and "not allowed" not in (r.stderr or "")
            if not sent:
                subprocess.run(["osascript", "-e",
                                f'display notification "{body.replace(chr(10), " ")}" with title "대시보드 {head}"'],
                               capture_output=True, timeout=5)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if self.d["state"] == "running":   # 예외로 죽어도 running 으로 남기지 않는다
            self.finish(False, f"{et.__name__}: {ev}" if et else "중단됨")
        return False


def read_all(include_old_failed=False):
    """보드가 읽는다. 오래된 종료 기록은 읽는 김에 치운다.

    성공은 6시간(KEEP_SECONDS)에 파일까지 지운다. 실패는 파일을 7일 두되 화면에는 6시간까지만
    돌려준다 — 옛 실패가 큐 카드 상태·세션 목록(최근 30)에 다시 끼어들면 안 된다.
    `include_old_failed=True` 면 그 6시간 넘은 실패까지 준다(사후 진단·'모두 지우기').
    """
    out, now = {}, time.time()
    if not RUNS.exists():
        return out
    for f in RUNS.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        age = now - d.get("updated", 0)
        if d.get("state") == "failed":
            if age > KEEP_FAILED_SECONDS:
                _log_failed(d)
                f.unlink(missing_ok=True)
                continue
            if age > KEEP_SECONDS and not include_old_failed:
                continue   # 파일은 남긴다 — 화면에서만 뺀다
        elif d.get("state") != "running" and age > KEEP_SECONDS:
            f.unlink(missing_ok=True)
            continue
        # 프로세스가 죽었는데 running 으로 남은 것 (강제 종료·재부팅)
        if d.get("state") == "running" and now - d.get("updated", 0) > 900:
            d["state"], d["phase"] = "failed", "응답 없음 (워커가 죽었다)"
        out[d["ts"]] = d
    return out


# ── 외부 로그인 벽 한 자리 (2026-09-16) — ISSUE-202608-EXTERNAL-SESSION-EXPIRY
# 수집기는 로그인 벽을 만나면 hit=True, 정상 통과하면 hit=False 로 같은 파일을 갱신한다.
# 그래야 사람이 재로그인한 다음 회차에 화면 문구가 저절로 내려간다.
# 보드는 읽기만 한다(`/api/state` login_walls) — 자격증명은 이 계층에 절대 없다.
LOGIN_WALLS = paths.root() / "_workspace/checkpoint/login_walls.json"
# 서비스 키 → (풀린 것, 다시 붙이는 방법). 사람 말은 여기 한 곳 — 화면은 그대로 쓴다.
LOGIN_SERVICES = {
    "linkedin": ("LinkedIn 로그인", "브라우저에서 linkedin.com 다시 로그인"),
    "threads": ("Threads 로그인", "브라우저에서 threads.com 다시 로그인"),
    "threads_api": ("Threads 수집 토큰", "터미널에서 python3 scripts/threads_auth.py url 로 다시 발급"),
}


def login_wall(service, hit, url=None):
    """외부 로그인 벽 상태를 서비스별로 한 줄 기록하고, 사람이 읽을 문장을 돌려준다.

    hit=True 면 벽, False 면 정상 통과(이번 회차에 붙었다). 돌려주는 문장은 벽일 때만 있고
    수집기가 stderr 에 그대로 찍는다. 기록 실패가 수집을 죽이면 안 되므로 전부 삼킨다."""
    label, how = LOGIN_SERVICES.get(service, (service + " 로그인", "다시 로그인"))
    line = "%s이 풀렸다 — %s하면 다음 회차에 붙는다." % (label, how)
    row = {"wall": bool(hit), "at": datetime.now(KST).isoformat(timespec="seconds"),
           "label": label, "how": how, "line": line}
    if url:
        row["url"] = url
    try:
        LOGIN_WALLS.parent.mkdir(parents=True, exist_ok=True)
        with open(str(LOGIN_WALLS) + ".lock", "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                data = json.loads(LOGIN_WALLS.read_text())
                if not isinstance(data, dict):
                    data = {}
            except (OSError, json.JSONDecodeError):
                data = {}
            data[service] = row
            tmp = str(LOGIN_WALLS) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, str(LOGIN_WALLS))
    except OSError:
        pass
    return line if hit else None


def login_walls():
    """벽인 서비스만 {키: row} — 보드가 읽는다. 파일이 없거나 깨졌으면 빈 dict."""
    try:
        data = json.loads(LOGIN_WALLS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("wall")}
