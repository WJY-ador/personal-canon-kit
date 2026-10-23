"""무인 요약 — 사람이 지정·확인한 자기 기록 폴더만 입력, 고정된 요약 작업 하나.

조건(r3 T4 「무인 요약」):
- 입력 = 설정 summary.input_dir 안의 파일만(심링크·숨김 파일 제외). 혼합 자료에서 타인 말을 모델이 골라내지 않는다.
- 모델에 업로드 토큰·HMAC 키·신원 매핑을 넘기지 않는다: 최소 환경변수, 빈 작업 폴더, 입력은 표준 입력으로.
- 모델의 셸·파일 쓰기·원격 MCP 권한을 끈다(빈 MCP 설정 + 도구 차단 + 1턴).
- 입력 ID·해시·범위는 업로더가 쓴다(모델 출력은 title·body 뿐).
- 성공 = exit code 0 AND 계약 검증 성공. 실패 출력은 전송 대기에 넣지 않는다.
- 5분 제한 · 동시 1개(파일 잠금) · PC 당 KST 하루 10회 시도.
- 로그인·구독·권한 오류는 summary 멈춤(halt) — 사용자가 `run-once --resume summary` 로 풀 때까지.
- 스위치(summary.enabled)는 약관·권한 제한·CLI 보관 동작 확인 전 꺼 둔다(기본 false).
- execution 신고: 사람이 직접 돌린 run-once = attended(서버 IMPORTS 스위치만), 예약(--scheduled)·호출자 불명 = unattended
  (서버 UNATTENDED_SUMMARY 스위치도 필요).
- 서버가 요약 원본을 「삭제됨」으로 답했으면 모델을 돌리지 않는다. 영구 삭제(purged)는 계속 건너뛰고, 휴지통은 응답 뒤
  168시간이 지나 입력이 바뀌어 있으면 1회 다시 요약해 보낸다(복원됐으면 접수, 그새 영구 삭제됐으면 purged 로 바뀐다).
시험은 모의 실행기로만 한다. 실제 구독 CLI 리허설은 원작자 수동.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess

from . import jcs, osutil, runner as _runner, store as _store

TRANSFORM = "self-summary/1"
PROMPT = (
    "You summarize the user's own journal entry given on standard input. "
    "Return ONLY a JSON object with keys \"title\" (string, at most 80 characters, or null) and "
    "\"body\" (string, at most 1200 characters) written in the same language as the input. "
    "Do not include URLs, e-mail addresses, file paths, or names of other people. "
    "Do not use tools."
)
# 권한 = 허용 목록(검토 반영 2026-10-20): 도구 전부 끔(--tools "") · 빈 MCP · 작업 폴더 설정만(사용자 settings·hooks 안 읽음)
# · 묻지 않고 거절(dontAsk) · 한 턴. 로컬 claude CLI --help 로 옵션 존재 확인(2026-10-20)
_LOGIN_RE = re.compile(r"(?i)(not logged in|log ?in|authenticat|unauthori[sz]ed|subscription|"
                       r"permission denied|invalid api key|credit balance|oauth|forbidden)")
MAX_INPUT_BYTES = 64 * 1024
TRASH_RETRY = _dt.timedelta(hours=168)   # 서버 휴지통 보관 기간 — 지나면 복원됐거나 영구 삭제 표식만 남는다
MAX_OUTPUT_BYTES = 256 * 1024


class SummaryError(RuntimeError):
    pass


def _subprocess_runner(argv, *, stdin_bytes, env, cwd, timeout):
    p = subprocess.run(argv, input=stdin_bytes, capture_output=True, env=env, cwd=cwd, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def build_argv(cli_path: str) -> list:
    return [cli_path, "-p", PROMPT, "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "project", "--permission-mode", "dontAsk", "--max-turns", "1", "--output-format", "json"]


def child_env(paths, cli_path: str | None = None) -> dict:
    """구독 CLI 가 로그인 정보를 찾는 데 필요한 최소 값만. 토큰·키·에이전트 설정은 없다.
    PATH 앞에 CLI 가 있는 폴더를 둔다(node 실행기 등 같은 폴더의 런타임을 찾게).
    macOS·Linux 는 USER·LOGNAME 도 넘긴다 — 없으면 구독 CLI 가 로그인을 못 찾는다(B7 실측 2026-10-22)."""
    base = "/usr/bin:/bin:/usr/sbin:/sbin"
    if cli_path:
        base = os.path.dirname(os.path.realpath(cli_path)) + os.pathsep + os.path.dirname(cli_path) + os.pathsep + base
    env = {"PATH": base, "LANG": os.environ.get("LANG", "en_US.UTF-8")}
    home = os.environ.get("HOME") or os.path.expanduser("~")
    env["HOME"] = home
    if osutil.IS_WINDOWS:
        for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot"):
            if os.environ.get(k):
                env[k] = os.environ[k]
    else:
        for k in ("USER", "LOGNAME"):
            if os.environ.get(k):
                env[k] = os.environ[k]
    return env


def list_inputs(input_dir: str, suffix: str):
    root = os.path.realpath(input_dir)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                             and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            if fn.startswith(".") or not fn.endswith(suffix):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            real = os.path.realpath(full)
            if os.path.commonpath([root, real]) != root:
                continue
            out.append((os.path.relpath(real, root), real))
    return out


def parse_model_output(stdout: bytes):
    """claude -p --output-format json 결과 → (title, body). 형식이 다르면 SummaryError."""
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise SummaryError("output-too-large")
    try:
        outer = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SummaryError("output-not-json") from None
    if not isinstance(outer, dict) or outer.get("is_error") is True or not isinstance(outer.get("result"), str):
        raise SummaryError("output-shape")
    try:
        inner = json.loads(outer["result"])
    except ValueError:
        raise SummaryError("result-not-json") from None
    if not isinstance(inner, dict) or set(inner.keys()) - {"title", "body"} or "body" not in inner:
        raise SummaryError("result-shape")
    title, body = inner.get("title"), inner["body"]
    if title is not None and not isinstance(title, str):
        raise SummaryError("result-shape")
    if not isinstance(body, str):
        raise SummaryError("result-shape")
    return (title or None), body


def _retry_after_trash(rev, now: _dt.datetime) -> bool:
    """「삭제됨」 응답 개정 — 영구 삭제(purged)는 다시 요약하지 않는다. 휴지통은 응답 받은 뒤 TRASH_RETRY 가 지나야 1회."""
    if rev["server_state"] != _store.SERVER_DELETED:
        return False
    try:
        at = _dt.datetime.fromisoformat(rev["updated_at"])
    except (TypeError, ValueError):
        return False
    return at.tzinfo is not None and now >= at + TRASH_RETRY


def summarize(st, cfg: dict, *, paths, exec_runner=None, now=osutil.kst_now) -> dict:
    sc = cfg.get("summary") or {}
    # 사람이 직접 돌린 run-once(--scheduled 없이) = attended — 서버 마스터 스위치만으로 접수. 예약 실행·호출자 불명 = unattended
    execution = "attended" if cfg.get("_execution") == "attended" else "unattended"
    out = {"attempted": 0, "staged": 0, "failed": 0, "deleted": 0, "skipped": None}
    if not sc.get("enabled"):
        out["skipped"] = "disabled"
        return out
    if st.halted("summary"):
        out["skipped"] = "halted"
        return out
    input_dir, cli_path = sc.get("input_dir"), sc.get("cli_path")
    if not (isinstance(input_dir, str) and os.path.isabs(input_dir) and os.path.isdir(input_dir)):
        raise SummaryError("summary.input_dir must be an existing absolute directory")
    if not (isinstance(cli_path, str) and os.path.isabs(cli_path)):
        raise SummaryError("summary.cli_path must be an absolute path")
    exec_runner = exec_runner or _subprocess_runner
    limit = int(sc.get("daily_attempts", 10))
    timeout = float(sc.get("timeout_sec", 300))
    day = osutil.kst_today()
    lock = osutil.FileLock(paths.lock("summary"))
    try:
        lock.acquire()
    except osutil.LockBusy:
        out["skipped"] = "already-running"
        return out
    try:
        counts = _runner.new_counts()
        for rel, real in list_inputs(input_dir, sc.get("file_glob_suffix", ".md")):
            with open(real, "rb") as f:
                data = f.read(MAX_INPUT_BYTES + 1)
            if len(data) > MAX_INPUT_BYTES:
                continue
            sha = hashlib.sha256(data).hexdigest()
            in_ev = st.event_get("summary-input", rel)
            if in_ev is None:
                in_ev = st.event_create("summary-input", rel, "summary_input", None, raw_obj=None)
            prev = st.summary_status(in_ev["original_id"], sha)
            if prev is not None and (prev["status"] == "staged" or prev["day"] == day):
                continue
            sum_ev = st.event_get("summary", in_ev["original_id"])
            gone = st.deleted_response(sum_ev["original_id"]) if sum_ev is not None else None
            if gone is not None and not _retry_after_trash(gone, now()):
                # 서버가 이 요약 원본을 「삭제됨」으로 답했다 — 모델을 돌리지 않는다(구독 낭비 방지). 시도·실패로 세지 않고
                # 기록도 남기지 않는다. 휴지통이면 168시간 뒤 바뀐 입력으로 1회 다시 요약(복원됐으면 접수, 영구 삭제됐으면 purged)
                out["deleted"] += 1
                continue
            if st.summary_attempts_today(day) >= limit:
                out["skipped"] = "daily-limit"
                break
            st.summary_attempt_bump(day)
            out["attempted"] += 1
            try:
                code, so, se = exec_runner(build_argv(cli_path), stdin_bytes=data, env=child_env(paths, cli_path),
                                           cwd=paths.summary_work, timeout=timeout)
            except subprocess.TimeoutExpired:
                st.summary_set(in_ev["original_id"], sha, "failed", day, "timeout")
                out["failed"] += 1
                continue
            except OSError:
                st.summary_set(in_ev["original_id"], sha, "failed", day, "exec")
                out["failed"] += 1
                continue
            text = ((so or b"")[:4096] + b" " + (se or b"")[:4096]).decode("utf-8", "replace")
            if code != 0:
                if _LOGIN_RE.search(text):
                    st.halt("summary", "login-or-permission")
                    st.summary_set(in_ev["original_id"], sha, "failed", day, "login")
                    out["failed"] += 1
                    out["skipped"] = "halted"
                    break
                st.summary_set(in_ev["original_id"], sha, "failed", day, f"exit-{code}")
                out["failed"] += 1
                continue
            try:
                title, body = parse_model_output(so or b"")
            except SummaryError as e:
                st.summary_set(in_ev["original_id"], sha, "failed", day, str(e))
                out["failed"] += 1
                continue
            in_rev = st.input_revision(in_ev["original_id"], sha)
            fields = {
                "body": body,
                "generation": {
                    "method": "ai_summary",
                    "tool": sc.get("tool_code", "claude-cli"),
                    "model": sc.get("model_code", "unknown"),
                    "input_scope": "self_only",
                    "inputs": [{"original_id": in_ev["original_id"], "revision": in_rev, "sha256": sha}],
                },
            }
            if title:
                fields["title"] = title
            ok = _runner.stage_self_text(
                st, cfg, adapter_name="summary", dedupe_key=in_ev["original_id"],
                fingerprint=jcs.sha256_hex(["summary", in_ev["original_id"]]), kind="self_summary",
                transform_version=TRANSFORM, execution=execution, fields=fields, counts=counts)
            if ok:
                st.summary_set(in_ev["original_id"], sha, "staged", day)
                out["staged"] += 1
            else:
                st.summary_set(in_ev["original_id"], sha, "failed", day, "contract-or-screen")
                out["failed"] += 1
        return out
    finally:
        lock.release()
