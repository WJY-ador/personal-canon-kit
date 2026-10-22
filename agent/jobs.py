"""작업 전달(②-a) — 서버 작업 요청을 롱폴로 받아 PC 에서 `claude -p` 로 한 번 실행하고 결과를 돌려준다.

계약(구현계획 §5 + J0 판정 G3, 서버와 같은 값):
- GET  /api/device/jobs?wait=30                      → 200 {id,kind,session_ref,input_ref,input_version,
                                                         definitions_version,lease:{generation,expires_at}} · 204 · 429
- GET  /api/device/jobs/<id>/input?lease_generation=N → 200 {text, input_version} (성공 = 서버 leased→running·시도 +1)
- POST /api/device/jobs/<id>/result {lease_generation,input_version,result_version,summary_text,changes,blocked,
                                     text_map,model_meta} → 200 · 409 JOB-LEASE·JOB-VERSION · 410 JOB-GONE
- POST /api/device/jobs/<id>/fail   {lease_generation, reason_code} — 원문·stdout·stderr 금지, 코드만

규칙:
- 동시 1: 한 워커는 한 번에 작업 하나(스레드 잠금) + 한 홈에 워커 하나(파일 잠금 `jobs` — 예약 run-once 의 `run` 과 다른 이름).
- 폴링: 롱폴 30초. 폴링 시작 간격 ≥15초(장치 분당 4). 429 = Retry-After(없으면 60초, 최대 1시간).
  5xx(스위치 꺼짐 포함)·네트워크 = 지수 대기(30초→최대 15분). 401·폐기 = 멈춤(auth).
- 임대: 받은 세대를 입력 조회·결과·실패에 그대로 싣는다. 409·410 을 받으면 그 작업을 버린다(재실행·재전송 없음).
- 결과 전송이 끊기면 **같은 바이트(같은 result_version)** 로 다시 보낸다. 모델은 다시 돌리지 않는다.
  `/result`·`/fail` 이 429 면 임대가 남아 있는 동안 Retry-After 를 지켜 같은 바이트로 다시 보낸다(임대 만료면 중단).
  끝내 못 보내면 버린다 — 서버 임대 만료가 needs_check 로 남긴다(중복 실행 금지).
- 일반 저장(`jobs-state.json`, 0600, 원자적 쓰기)엔 본문 0: 임대 세대·작업 ID·시도 수·session_ref→CLI 세션 ID 만.
  입력·결과·프롬프트 원문은 메모리에서만 다룬다. CLI 세션 ID 는 서버로 보내지 않는다.
- 결과 반환·로그엔 상태 코드·규칙 코드만(본문·토큰 없음).
- 실 CLI: argv 는 summary.py 와 같은 격리(도구 끔·빈 MCP·strict·project 설정만·dontAsk·1턴·json).
  약관 확인 전에는 실행하지 않는다 — fake 가 아니면 환경변수 MYCRM_JOBS_ALLOW_REAL_CLI=1 이 있어야 어댑터가 만들어진다.
쌓기 단계(②-b, kind=converse) = agent/stack.py:
- 입력 조회 뒤 정의 스냅샷(`GET …/definitions?lease_generation=N`)을 한 번 받아 job 동안 고정한다.
- /result 본문 = {lease_generation, envelope:{contract:"stack/1",…}, units, changes, blocked, model_meta}.
- 같은 세션(session_ref) 실행 직렬화: jobs-state.json 의 임대 행에 session_ref·만료(벽시계)를 적고, 같은 세션의
  다른 임대가 살아 있으면 앞 실행이 끝날 때까지 기다린다(임대 안에서). 못 기다리면 입력도 부르지 않는다.
  CLI 종료 불명확(시간 초과)이면 실패를 보내지 않고 임대 행을 남긴다 — 서버 임대 만료(needs_check)까지 같은 세션 잠금.
- `attempts`(일별, 작업당 CLI 시작 1) 와 실제 CLI 재시도 `cli_retries`(일별)를 따로 센다.
상주 진입점(cli.py `serve` 연결)은 J4 후속. 여기는 `Worker.poll_once()`·`Worker.run_forever()` 호출 함수만 낸다.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import osutil, stack
from .summary import _LOGIN_RE, _subprocess_runner, child_env
from .uploader import AUTH_CODES, _opener

JOBS_PATH = "/api/device/jobs"
KINDS = ("converse", "summarize_reading", "routine_ingest")
REASON_CODES = ("output-not-json", "output-shape", "result-shape", "timeout", "cli-exit", "login", "input-too-large")

WAIT_SEC = 30                  # 롱폴
MIN_POLL_INTERVAL = 15.0       # 장치 분당 4
RETRY_AFTER_DEFAULT = 60
RETRY_AFTER_MAX = 3600
POLL_BACKOFF_BASE = 30
POLL_BACKOFF_MAX = 900
RESULT_RETRIES = 5             # 결과·실패 전송 재시도(같은 바이트)
RESULT_RETRY_BASE = 2.0
LEASE_SEC = 300                # 임대 5분 — PC 는 받은 시각(단조 시계) 기준으로 센다(서버·PC 시각 차이 무관)
CLI_TIMEOUT = 300
MAX_INPUT_CHARS = 8000         # 코드포인트
MAX_INPUT_BYTES = 32 * 1024    # UTF-8 32KiB
MAX_OUTPUT_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 256 * 1024   # 입력 32KiB 가 JSON 이스케이프(\uXXXX)되면 최대 ~6배 — 업로더 64KiB 상한으로는 잘린다
REAL_CLI_ENV = "MYCRM_JOBS_ALLOW_REAL_CLI"
STATE_FILE = "jobs-state.json"
STATE_VERSION = 2              # v1(임대 = 세대만) 파일은 읽을 때 v2 로 올린다
SESSION_WAIT_STEP = 1.0        # 같은 세션 앞 실행을 기다릴 때 확인 간격
MIN_RUN_SEC = 60.0             # 기다린 뒤 임대 안에 남아야 할 최소 실행 시간
DEFS_PATH_SUFFIX = "definitions"
MAX_SESSIONS = 500
ATTEMPT_DAYS_KEPT = 7

_JOB_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CLI_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")   # 앞자리 '-' 금지(인자 주입 방지)

# 종류별 고정 지시(argv). converse 는 stack/1 — 정의·계약·입력은 표준 입력(agent/stack.py build_prompt)으로.
PROMPTS = {
    "converse": stack.ARGV_PROMPT,
    "summarize_reading": (
        "You summarize the reading material given on standard input for the user. "
        "Return ONLY a JSON object with keys \"summary_text\" (string), \"changes\" (array), \"blocked\" (array) "
        "and \"text_map\" (array). Do not include URLs or names of other people. Do not use tools."),
    "routine_ingest": (
        "You file the routine result given on standard input into the user's structure. "
        "Return ONLY a JSON object with keys \"summary_text\" (string), \"changes\" (array), \"blocked\" (array) "
        "and \"text_map\" (array). Do not decide confirmation, citation or limits. Do not use tools."),
}


class JobsError(RuntimeError):
    """설정·상태 오류. 메시지에 본문·토큰을 넣지 않는다."""


class JobFail(Exception):
    """모델 실행 실패 — reason_code 하나만 담는다."""

    def __init__(self, reason_code: str):
        if reason_code not in REASON_CODES:
            raise ValueError("unknown reason_code")
        self.reason_code = reason_code
        super().__init__(reason_code)


class NetworkError(Exception):
    pass


# ── PC 일반 저장(본문 0) ──────────────────────────────────

def _empty_state() -> dict:
    return {"v": STATE_VERSION, "sessions": {}, "attempts": {}, "cli_retries": {}, "leases": {}}


class MemoryState:
    """주입 가능한 저장 인터페이스. 값 = 작업 ID·임대 세대·session_ref·임대 만료(벽시계 초)·시도 수·CLI 재시도 수
    ·session_ref→CLI 세션 ID 뿐(본문 0)."""

    def __init__(self, data: dict | None = None):
        self._lock = threading.Lock()
        self.data = data if data is not None else _empty_state()

    def _save(self) -> None:
        pass

    def session_get(self, ref: str):
        with self._lock:
            return self.data["sessions"].get(ref)

    def session_set(self, ref: str, cli_session: str) -> None:
        with self._lock:
            s = self.data["sessions"]
            s.pop(ref, None)
            s[ref] = cli_session
            while len(s) > MAX_SESSIONS:
                s.pop(next(iter(s)))
            self._save()

    def attempt_bump(self, day: str) -> int:
        with self._lock:
            a = self.data["attempts"]
            a[day] = int(a.get(day, 0)) + 1
            for d in sorted(a)[:-ATTEMPT_DAYS_KEPT]:
                del a[d]
            self._save()
            return a[day]

    def attempts(self, day: str) -> int:
        with self._lock:
            return int(self.data["attempts"].get(day, 0))

    def cli_retry_bump(self, day: str) -> int:
        """실제 CLI 재시도(같은 작업의 두 번째 실행). 서버 attempts(입력 조회 수)·일별 attempts 와 별개."""
        with self._lock:
            a = self.data["cli_retries"]
            a[day] = int(a.get(day, 0)) + 1
            for d in sorted(a)[:-ATTEMPT_DAYS_KEPT]:
                del a[d]
            self._save()
            return a[day]

    def cli_retries(self, day: str) -> int:
        with self._lock:
            return int(self.data["cli_retries"].get(day, 0))

    def lease_set(self, job_id: str, generation: int) -> None:
        with self._lock:
            self.data["leases"][job_id] = {"generation": int(generation)}
            self._save()

    def session_acquire(self, job_id: str, generation: int, ref: str, until: float, now: float) -> bool:
        """같은 session_ref 의 다른 임대가 살아 있으면(until > now) False. 아니면 이 작업 임대를 적고 True.
        만료된 세션 임대 행은 지운다(서버도 그 뒤엔 needs_check)."""
        with self._lock:
            leases = self.data["leases"]
            dead = [k for k, v in leases.items() if "until" in v and float(v["until"]) <= now]
            for k in dead:
                del leases[k]
            busy = any(k != job_id and v.get("session_ref") == ref for k, v in leases.items())
            if not busy:
                leases[job_id] = {"generation": int(generation), "session_ref": ref, "until": float(until)}
            if dead or not busy:
                self._save()
            return not busy

    def lease_clear(self, job_id: str) -> None:
        with self._lock:
            if self.data["leases"].pop(job_id, None) is not None:
                self._save()

    def leases(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self.data["leases"].items()}


class JsonState(MemoryState):
    """기본 저장 — 에이전트 홈의 jobs-state.json(0600, 원자적 쓰기). 깨진 파일은 고치지 않고 멈춘다."""

    def __init__(self, path: str):
        self.path = path
        data = _empty_state()
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    raw = json.loads(f.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                raise JobsError("jobs state unreadable") from None
            if not isinstance(raw, dict) or raw.get("v") not in (1, STATE_VERSION) or not all(
                    isinstance(raw.get(k), dict) for k in ("sessions", "attempts", "leases")):
                raise JobsError("jobs state shape")
            if raw["v"] == 1:                                  # v1 → v2: 재시도 칸만 보탠다
                raw = dict(raw, v=STATE_VERSION, cli_retries={})
            if not isinstance(raw.get("cli_retries"), dict) or set(raw) != set(_empty_state()):
                raise JobsError("jobs state shape")
            data = raw
        super().__init__(data)

    @classmethod
    def for_paths(cls, paths) -> "JsonState":
        return cls(os.path.join(paths.home, STATE_FILE))

    def _save(self) -> None:
        osutil.atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        osutil.set_private_file(self.path)


# ── 장치 API 클라이언트 ───────────────────────────────────

class Client:
    """업로더와 같은 꼴 — Bearer 헤더·리다이렉트 금지(토큰이 다른 주소로 가지 않게)·응답 크기 제한.
    응답 상한만 다르다(MAX_RESPONSE_BYTES). (status, headers, payload) 를 돌려준다. 잘린·깨진 JSON = 빈 payload."""

    def __init__(self, server_url: str, token: str, *, wait: int = WAIT_SEC, opener=None):
        self.wait = int(wait)
        self.base = server_url.rstrip("/")
        self._token = token
        self.timeout = self.wait + 15
        self.opener = opener or _opener()

    def __repr__(self):
        return "jobs.Client(<redacted>)"

    def _call(self, method: str, path: str, body: dict | None = None):
        data = None
        extra = {}
        if method == "POST":
            data = json.dumps(body or {}, ensure_ascii=False, sort_keys=True).encode("utf-8")
            extra["Content-Type"] = "application/json"
        return self._send(method, path, data, extra)

    def _send(self, method: str, path: str, data: bytes | None, extra: dict):
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("Accept", "application/json")
        for k, v in extra.items():
            req.add_header(k, v)
        try:
            try:
                resp = self.opener.open(req, timeout=self.timeout)
                status, headers, body = resp.status, resp.headers, resp.read(MAX_RESPONSE_BYTES + 1)
                resp.close()
            except urllib.error.HTTPError as e:
                status, headers = e.code, e.headers
                try:
                    body = e.read(MAX_RESPONSE_BYTES + 1)
                except Exception:
                    body = b""
                e.close()
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            raise NetworkError() from None
        payload = {}
        if body and len(body) <= MAX_RESPONSE_BYTES:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return status, headers, payload

    def poll(self):
        return self._call("GET", f"{JOBS_PATH}?wait={self.wait}")

    def input(self, job_id: str, generation: int):
        return self._call("GET", f"{JOBS_PATH}/{_job_id(job_id)}/input?lease_generation={int(generation)}")

    def definitions(self, job_id: str, generation: int):
        return self._call("GET", f"{JOBS_PATH}/{_job_id(job_id)}/{DEFS_PATH_SUFFIX}?lease_generation={int(generation)}")

    def result_bytes(self, job_id: str, data: bytes):
        return self._send("POST", f"{JOBS_PATH}/{_job_id(job_id)}/result", data, {"Content-Type": "application/json"})

    def fail(self, job_id: str, generation: int, reason_code: str):
        if reason_code not in REASON_CODES:
            raise ValueError("unknown reason_code")
        return self._call("POST", f"{JOBS_PATH}/{_job_id(job_id)}/fail",
                          {"lease_generation": int(generation), "reason_code": reason_code})


def _job_id(v) -> str:
    if not isinstance(v, str) or not _JOB_ID_RE.match(v):
        raise JobsError("job id shape")
    return v


def _code(payload) -> str:
    c = payload.get("code") if isinstance(payload, dict) else None
    return c if isinstance(c, str) else ""


def _is_auth(status: int, code: str) -> bool:
    return status == 401 or code in AUTH_CODES


# ── claude -p 어댑터 ──────────────────────────────────────

def build_argv(cli: list, kind: str, resume: str | None = None) -> list:
    """cli = 실행 앞부분(실제: [절대 경로 claude], 시험: [python, fake_claude.py])."""
    if kind not in PROMPTS:
        raise JobsError("kind")
    argv = list(cli) + ["-p", PROMPTS[kind], "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                        "--setting-sources", "project", "--permission-mode", "dontAsk", "--max-turns", "1",
                        "--output-format", "json"]
    if resume is not None:
        if not _CLI_SESSION_RE.match(resume):
            raise JobsError("cli session shape")
        argv += ["--resume", resume]
    return argv


def parse_outer(stdout: bytes) -> dict:
    """claude -p --output-format json 바깥 꼴 → {result(문자열), session_id, model}. 실패는 JobFail."""
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise JobFail("output-shape")
    try:
        outer = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise JobFail("output-not-json") from None
    if not isinstance(outer, dict) or not isinstance(outer.get("result"), str):
        raise JobFail("output-shape")
    if outer.get("is_error") is True:
        raise JobFail("login" if _LOGIN_RE.search(outer["result"]) else "output-shape")
    sid = outer.get("session_id")
    model = outer.get("model")
    return {"result": outer["result"],
            "session_id": sid if isinstance(sid, str) and _CLI_SESSION_RE.match(sid) else None,
            "model": model if isinstance(model, str) and _REF_RE.match(model) else None}


def parse_output(stdout: bytes) -> dict:
    """claude -p --output-format json → {summary_text, changes, blocked, text_map, session_id, model}. 실패는 JobFail.
    (converse 외 종류 — 기존 꼴)"""
    o = parse_outer(stdout)
    try:
        inner = json.loads(o["result"])
    except ValueError:
        raise JobFail("result-shape") from None
    if (not isinstance(inner, dict) or set(inner) - {"summary_text", "changes", "blocked", "text_map"}
            or not isinstance(inner.get("summary_text"), str)):
        raise JobFail("result-shape")
    out = {"summary_text": inner["summary_text"]}
    for k in ("changes", "blocked", "text_map"):
        v = inner.get(k, [])
        if not isinstance(v, list):
            raise JobFail("result-shape")
        out[k] = v
    out["session_id"], out["model"] = o["session_id"], o["model"]
    return out


class ClaudeAdapter:
    """실행만 주입 가능(exec_runner). fake=False(실 CLI)는 약관 확인 전 막는다."""

    def __init__(self, cli: list, *, fake: bool = False, exec_runner=None, timeout: float = CLI_TIMEOUT,
                 extra_env: dict | None = None, paths=None, monotonic=time.monotonic):
        if not cli or not all(isinstance(x, str) for x in cli) or not os.path.isabs(cli[0]):
            raise JobsError("cli must start with an absolute path")
        if not fake and os.environ.get(REAL_CLI_ENV) != "1":
            raise JobsError("real claude CLI blocked until terms are confirmed")
        self.cli = list(cli)
        self.fake = fake
        self.exec_runner = exec_runner or _subprocess_runner
        self.timeout = timeout
        self.extra_env = dict(extra_env or {}) if fake else {}   # 실 CLI 에는 최소 환경만
        self.paths = paths
        self.monotonic = monotonic

    def run(self, kind: str, text: str, resume: str | None = None) -> dict:
        out, dur = self._exec(kind, text, resume, None)
        res = parse_output(out)
        res["duration_ms"] = dur
        return res

    def run_raw(self, kind: str, text: str, resume: str | None = None, timeout: float | None = None) -> dict:
        """바깥 꼴만 검사 → {result(모델 문자열), session_id, model, duration_ms}. 안쪽 계약은 호출자(stack)가."""
        out, dur = self._exec(kind, text, resume, timeout)
        res = parse_outer(out)
        res["duration_ms"] = dur
        return res

    def _exec(self, kind: str, text: str, resume, timeout):
        import subprocess
        argv = build_argv(self.cli, kind, resume)
        env = child_env(self.paths, self.cli[0] if not self.fake else None)
        env.update(self.extra_env)
        cwd = self._workdir()
        t0 = self.monotonic()
        try:
            rc, out, err = self.exec_runner(argv, stdin_bytes=text.encode("utf-8"), env=env, cwd=cwd,
                                            timeout=self.timeout if timeout is None else timeout)
        except subprocess.TimeoutExpired:
            raise JobFail("timeout") from None
        except OSError:
            raise JobFail("cli-exit") from None
        dur = int((self.monotonic() - t0) * 1000)
        if rc != 0:
            msg = (err or b"")[:4096].decode("utf-8", "replace") + (out or b"")[:4096].decode("utf-8", "replace")
            raise JobFail("login" if _LOGIN_RE.search(msg) else "cli-exit")
        return out or b"", max(dur, 0)

    def _workdir(self) -> str:
        """빈 작업 폴더 — 모델이 읽을 파일이 없게."""
        import tempfile
        if self.paths is not None:
            d = os.path.join(self.paths.home, "jobs-work")
            osutil.ensure_private_dir(d)
            return d
        return tempfile.gettempdir()


def fake_cli(repo_root: str | None = None) -> list:
    """시험 기본 실행기 — tests/fake_claude.py(FAKE_JOB=1 로 부른다)."""
    root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.realpath(sys.executable), os.path.join(root, "tests", "fake_claude.py")]


def fake_adapter(mode: str = "ok", **kw) -> ClaudeAdapter:
    env = {"FAKE_JOB": "1", "FAKE_MODE": mode}
    env.update(kw.pop("extra_env", {}) or {})
    return ClaudeAdapter(fake_cli(), fake=True, extra_env=env, **kw)


# ── 워커 ──────────────────────────────────────────────────

def _check_input(text) -> bool:
    return isinstance(text, str) and len(text) <= MAX_INPUT_CHARS and len(text.encode("utf-8")) <= MAX_INPUT_BYTES


def _retry_after(headers) -> int:
    ra = headers.get("Retry-After") if headers is not None else None
    sec = int(ra) if ra and str(ra).isdigit() else RETRY_AFTER_DEFAULT
    return min(sec, RETRY_AFTER_MAX)


def _job_shape(p) -> dict | None:
    """200 응답 → 쓸 값만. 꼴이 틀리면 None(임대를 쓰지 않는다 — 서버 임대 만료가 needs_check 로 남긴다)."""
    if not isinstance(p, dict):
        return None
    lease = p.get("lease")
    jid, kind, iv = p.get("id"), p.get("kind"), p.get("input_version")
    if not (isinstance(jid, str) and _JOB_ID_RE.match(jid) and kind in KINDS and isinstance(lease, dict)):
        return None
    gen = lease.get("generation")
    if isinstance(gen, bool) or not isinstance(gen, int) or isinstance(iv, bool) or not isinstance(iv, int):
        return None
    ref = p.get("session_ref")
    if ref is not None and not (isinstance(ref, str) and _REF_RE.match(ref)):
        return None
    dv = p.get("definitions_version")
    if dv is not None and not stack._is_version(dv):
        return None
    eg = p.get("edit_generation", 0)
    prv = p.get("parent_result_version")
    if not stack._is_int(eg) or eg < 0 or (prv is not None and not (isinstance(prv, str) and _REF_RE.match(prv))):
        return None
    return {"id": jid, "kind": kind, "generation": gen, "input_version": iv, "session_ref": ref,
            "definitions_version": dv, "edit_generation": eg, "parent_result_version": prv}


def _restack_shape(r):
    """재정리 입력(서버가 준 원 결과 units·뺄 변경·막힘 답) — 꼴이 틀리면 None."""
    if not isinstance(r, dict) or set(r) - {"parent_result_version", "units", "excluded_cids", "answers"}:
        return None
    prv, units = r.get("parent_result_version"), r.get("units")
    exc, ans = r.get("excluded_cids", []), r.get("answers", [])
    if not (isinstance(prv, str) and _REF_RE.match(prv)) or not isinstance(units, list) \
            or len(units) > stack.MAX_UNITS or not isinstance(exc, list) or not isinstance(ans, list) \
            or len(exc) > stack.MAX_CHANGES or len(ans) > stack.MAX_CHANGES:
        return None
    out = []
    for u in units:
        if not isinstance(u, dict) or not isinstance(u.get("uid"), str) or not isinstance(u.get("text"), str):
            return None
        ids = u.get("change_ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return None
        out.append({"uid": u["uid"], "text": u["text"], "change_ids": list(ids)})
    if not all(isinstance(c, str) and _REF_RE.match(c) for c in exc):
        return None
    answers = []
    for a in ans:
        if not isinstance(a, dict) or set(a) - {"cid", "text"} or not isinstance(a.get("text"), str) \
                or not (a.get("cid") is None or isinstance(a.get("cid"), str)):
            return None
        answers.append({"cid": a.get("cid"), "text": a["text"]})
    return {"parent_result_version": prv, "units": out, "excluded_cids": list(exc), "answers": answers}


class Worker:
    def __init__(self, client: Client, adapter: ClaudeAdapter, state: MemoryState, *,
                 monotonic=time.monotonic, sleep=time.sleep, today=osutil.kst_today, wall=time.time,
                 min_interval: float = MIN_POLL_INTERVAL, result_retries: int = RESULT_RETRIES,
                 session_wait_step: float = SESSION_WAIT_STEP):
        self.client, self.adapter, self.state = client, adapter, state
        self.monotonic, self.sleep, self.today, self.wall = monotonic, sleep, today, wall
        self.session_wait_step = float(session_wait_step)
        self._keep_lease = False
        self.min_interval = float(min_interval)
        self.result_retries = int(result_retries)
        self._busy = threading.Lock()
        self._last_poll = None
        self._not_before = None
        self._poll_failures = 0
        self.halted = None

    def __repr__(self):
        return "jobs.Worker(<redacted>)"

    # 폴링 간격 — 정상 롱폴이면 분당 ~2, 서버가 즉시 응답해도 시작 간격 ≥ min_interval
    def _pace(self) -> None:
        now = self.monotonic()
        at = now
        if self._last_poll is not None:
            at = max(at, self._last_poll + self.min_interval)
        if self._not_before is not None:
            at = max(at, self._not_before)
        if at > now:
            self.sleep(at - now)
        self._last_poll = self.monotonic()
        self._not_before = None

    def _defer(self, seconds: float) -> None:
        self._not_before = self.monotonic() + max(0.0, float(seconds))

    def _poll_backoff(self) -> float:
        self._poll_failures += 1
        wait = min(POLL_BACKOFF_BASE * (2 ** min(self._poll_failures - 1, 10)), POLL_BACKOFF_MAX)
        self._defer(wait)
        return wait

    def poll_once(self) -> dict:
        """폴링 1번 + (받았으면) 작업 1개 처리. 반환 {"outcome": …} — 코드만, 본문 없음."""
        if self.halted:
            return {"outcome": "halted", "scope": self.halted}
        if not self._busy.acquire(blocking=False):
            return {"outcome": "busy"}
        try:
            self._pace()
            try:
                status, headers, payload = self.client.poll()
            except NetworkError:
                return {"outcome": "network", "retry_in": self._poll_backoff()}
            code = _code(payload)
            if status == 204:
                self._poll_failures = 0
                return {"outcome": "idle"}
            if _is_auth(status, code):
                self.halted = "auth"
                return {"outcome": "auth", "status": status, "code": code}
            if status == 429:
                sec = _retry_after(headers)
                self._defer(sec)
                return {"outcome": "rate-limit", "retry_in": sec, "code": code}
            if status != 200:
                return {"outcome": "server", "status": status, "code": code, "retry_in": self._poll_backoff()}
            self._poll_failures = 0
            job = _job_shape(payload)
            if job is None:
                return {"outcome": "bad-job"}
            job["deadline"] = self.monotonic() + LEASE_SEC
            return self._process(job)
        finally:
            self._busy.release()

    def _process(self, job: dict) -> dict:
        jid, gen = job["id"], job["generation"]
        ref = job["session_ref"] if job["kind"] == "converse" else None
        if ref:
            if not self._acquire_session(job, ref):
                # 같은 세션 앞 실행이 끝나지 않음 — 입력도 부르지 않는다(실패 전송 없음, 서버 임대 만료에 맡김)
                return {"job_id": jid, "kind": job["kind"], "outcome": "session-busy"}
        else:
            self.state.lease_set(jid, gen)
        self._keep_lease = False
        try:
            return self._process_leased(job)
        finally:
            if not self._keep_lease:
                self.state.lease_clear(jid)
            self._keep_lease = False

    def _acquire_session(self, job: dict, ref: str) -> bool:
        """같은 세션 직렬화. 앞 임대가 풀릴 때까지 기다리되, 이 작업 임대 안에 MIN_RUN_SEC 이 남는 동안만."""
        budget_end = job["deadline"] - MIN_RUN_SEC
        while True:
            now = self.wall()
            if self.state.session_acquire(job["id"], job["generation"], ref, now + LEASE_SEC, now):
                return True
            if self.monotonic() + self.session_wait_step > budget_end:
                return False
            self.sleep(self.session_wait_step)

    def _process_leased(self, job: dict) -> dict:
        jid, gen = job["id"], job["generation"]
        base = {"job_id": jid, "kind": job["kind"]}
        try:
            status, _h, payload = self.client.input(jid, gen)
            while status == 429:                            # 요청 속도 한도 — 서버가 처리 전 거절, 임대 안에서만 다시
                sec = _retry_after(_h)
                if self.monotonic() + sec >= job["deadline"] - MIN_RUN_SEC:
                    return dict(base, outcome="input-error", status=status, code=_code(payload))
                self.sleep(sec)
                status, _h, payload = self.client.input(jid, gen)
        except NetworkError:
            return dict(base, outcome="input-network")      # 실행 안 함. 임대 만료 → 서버 needs_check
        code = _code(payload)
        if status == 410:
            return dict(base, outcome="gone", code=code)
        if _is_auth(status, code):
            self.halted = "auth"
            return dict(base, outcome="auth", status=status, code=code)
        if status == 409:
            return dict(base, outcome="lease-lost", code=code)
        if status != 200 or not isinstance(payload, dict):
            return dict(base, outcome="input-error", status=status, code=code)
        text = payload.get("text")
        iv = payload.get("input_version")
        if not isinstance(text, str) or isinstance(iv, bool) or not isinstance(iv, int):
            return dict(base, outcome="input-error", status=status)   # 꼴 틀림·잘림 — 실행 안 함
        if iv != job["input_version"]:
            return dict(base, outcome="input-version")
        if not _check_input(text):
            return dict(base, **self._send_fail(jid, gen, "input-too-large", job["deadline"]))

        ref = job["session_ref"] if job["kind"] == "converse" else None
        resume = self.state.session_get(ref) if ref else None
        if job["kind"] == "converse":
            return self._process_stack(job, payload, text, resume, base)
        self.state.attempt_bump(self.today())         # CLI 시작 직전 = 시도 1
        try:
            res = self.adapter.run(job["kind"], text, resume)
        except JobFail as e:
            return dict(base, **self._send_fail(jid, gen, e.reason_code, job["deadline"]))
        finally:
            text = None   # noqa: F841 — 원문 참조를 빨리 놓는다
        if ref and res.get("session_id"):
            self.state.session_set(ref, res["session_id"])   # CLI 세션 ID 는 PC 에만
        body = {"lease_generation": gen, "input_version": job["input_version"], "result_version": str(uuid.uuid4()),
                "summary_text": res["summary_text"], "changes": res["changes"], "blocked": res["blocked"],
                "text_map": res["text_map"],
                "model_meta": {"tool": "claude-cli", "model": res.get("model"), "duration_ms": res["duration_ms"]}}
        data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        body = res = None
        return dict(base, **self._send_result(jid, data, job["deadline"]))

    def _process_stack(self, job: dict, payload: dict, text: str, resume, base: dict) -> dict:
        """쌓기 단계: 입력 꼴 → 정의 스냅샷 1회 → stack.run → /result. 본문은 이 함수 밖으로 나가지 않는다."""
        jid, gen = job["id"], job["generation"]
        ik = payload.get("input_kind", "converse")
        eg = payload.get("edit_generation", job["edit_generation"])
        rs = payload.get("restack")
        restack = None if rs is None else _restack_shape(rs)
        if ik not in stack.INPUT_KINDS or not stack._is_int(eg) or eg < 0 or (rs is not None and restack is None):
            return dict(base, outcome="input-error")
        prv = restack["parent_result_version"] if restack else job["parent_result_version"]
        defs = self._fetch_defs(job, base)
        if not isinstance(defs, stack.Definitions):
            return defs
        sjob = {"id": jid, "generation": gen, "input_version": job["input_version"], "text": text,
                "input_kind": ik, "edit_generation": eg, "parent_result_version": prv, "restack": restack}
        day = self.today()
        self.state.attempt_bump(day)                  # CLI 시작 직전 = 시도 1(작업당)
        try:
            res = stack.run(sjob, defs, self.adapter, resume=resume, deadline=job["deadline"],
                            monotonic=self.monotonic, on_retry=lambda: self.state.cli_retry_bump(day))
        except stack.UnclearEnd:
            self._keep_lease = True                   # 같은 세션 잠금 유지 — 서버 임대 만료(needs_check)까지
            return dict(base, outcome="unclear-end")
        except JobFail as e:
            out = dict(base, **self._send_fail(jid, gen, e.reason_code, job["deadline"]))
            out["contract"] = getattr(e, "detail", e.reason_code)
            out["cli_attempts"] = getattr(e, "cli_attempts", 1)
            return out
        finally:
            sjob = text = None   # noqa: F841
        if job["session_ref"] and res.get("session_id"):
            self.state.session_set(job["session_ref"], res["session_id"])   # CLI 세션 ID 는 PC 에만
        out = dict(base, **self._send_result(jid, res["data"], job["deadline"], gen))
        out["cli_attempts"] = res["cli_attempts"]
        return out

    def _fetch_defs(self, job: dict, base: dict):
        """정의 스냅샷 1회(네트워크·5xx 면 한 번 더). 폴 응답 definitions_version 이 있으면 같은 값만 받는다."""
        jid, gen = job["id"], job["generation"]
        r = self._retrying(lambda: self.client.definitions(jid, gen), job["deadline"] - MIN_RUN_SEC,
                           retries=1)                    # 끊김·5xx = 한 번 더 · 429 = Retry-After(임대 안)
        if r is None:
            return dict(base, outcome="defs-network")
        status, payload = r
        code = _code(payload)
        if status == 410:
            return dict(base, outcome="gone", code=code)
        if _is_auth(status, code):
            self.halted = "auth"
            return dict(base, outcome="auth", status=status, code=code)
        if status == 409:
            return dict(base, outcome="lease-lost", code=code)
        if status != 200:
            return dict(base, outcome="defs-error", status=status, code=code)
        try:
            defs = stack.Definitions(payload)
        except stack.DefsError:
            return dict(base, outcome="defs-invalid")
        if job["definitions_version"] is not None and defs.version != job["definitions_version"]:
            return dict(base, outcome="defs-mismatch")
        return defs

    def _retrying(self, send, deadline: float, retries: int | None = None):
        """같은 요청(같은 바이트)을 다시 보낸다. 네트워크·5xx = 지수 대기로 result_retries 번까지.
        429 = Retry-After 를 지키되 그 뒤가 임대 만료(deadline) 이후면 중단. (status, payload) 또는 None(끝내 실패)."""
        tries = 0
        while True:
            try:
                status, headers, payload = send()
            except NetworkError:
                status, headers, payload = None, None, {}
            if status == 429:
                sec = _retry_after(headers)
                if self.monotonic() + sec >= deadline:
                    return None
                self.sleep(sec)
                continue
            if status is None or status >= 500:
                tries += 1
                if tries > (self.result_retries if retries is None else retries):
                    return None
                self.sleep(RESULT_RETRY_BASE * (2 ** (tries - 1)))
                continue
            return status, payload

    def _send_result(self, jid: str, data: bytes, deadline: float, gen: int | None = None) -> dict:
        r = self._retrying(lambda: self.client.result_bytes(jid, data), deadline)
        if r is None:
            return {"outcome": "result-undelivered"}
        status, payload = r
        code = _code(payload)
        if status in (200, 201):
            return {"outcome": "result-sent", "duplicate": payload.get("duplicate") is True}
        if status == 410:
            return {"outcome": "gone", "code": code}
        if _is_auth(status, code):
            self.halted = "auth"
            return {"outcome": "auth", "status": status, "code": code}
        if status == 409:
            label = {"JOB-VERSION": "version-mismatch", "JOB-DEFS": "defs-changed"}.get(code, "lease-lost")
            return {"outcome": label, "code": code}
        if status == 400 and code == "CONTRACT-INVALID" and gen is not None:
            # 서버 재검증 거절(예: 없는 대상 target_not_found) — 실패로 바로 끝낸다(임대 만료 needs_check 를 기다리지 않음).
            # msg = "<경로>: <규칙>"(값 없음) — 규칙 이름만 남긴다.
            msg = payload.get("msg") if isinstance(payload.get("msg"), str) else ""
            rule = msg.rsplit(": ", 1)[-1] if ": " in msg else ""
            out = self._send_fail(jid, gen, "result-shape", deadline)
            return dict(out, outcome="server-contract", rule=rule if re.match(r"^[a-z_]{1,64}$", rule) else "",
                        fail_status=out.get("status"))
        return {"outcome": "result-rejected", "status": status, "code": code}

    def _send_fail(self, jid: str, gen: int, reason_code: str, deadline: float) -> dict:
        r = self._retrying(lambda: self.client.fail(jid, gen, reason_code), deadline)
        if r is None:
            return {"outcome": "failed-undelivered", "reason_code": reason_code}
        status, payload = r
        code = _code(payload)
        if _is_auth(status, code):
            self.halted = "auth"
        return {"outcome": "failed", "reason_code": reason_code, "status": status, "code": code}

    def run_forever(self, *, stop: threading.Event | None = None, max_iterations: int | None = None,
                    lock_path: str | None = None) -> dict:
        """상주 루프. 멈춤(auth)·stop·max_iterations 에서 끝난다. lock_path 가 있으면 홈당 워커 하나."""
        lock = None
        if lock_path:
            lock = osutil.FileLock(lock_path)
            try:
                lock.acquire()
            except osutil.LockBusy:
                return {"stopped": "already-running", "iterations": 0, "outcomes": {}}
        counts = {}
        n = 0
        try:
            while True:
                if stop is not None and stop.is_set():
                    return {"stopped": "stop", "iterations": n, "outcomes": counts}
                if max_iterations is not None and n >= max_iterations:
                    return {"stopped": "max-iterations", "iterations": n, "outcomes": counts}
                out = self.poll_once()
                n += 1
                counts[out["outcome"]] = counts.get(out["outcome"], 0) + 1
                if self.halted:
                    return {"stopped": self.halted + "-halted", "iterations": n, "outcomes": counts}
        finally:
            if lock is not None:
                lock.release()
