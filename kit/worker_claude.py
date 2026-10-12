#!/usr/bin/env python3
"""구독 CLI(claude -p) 스트리밍 실행 — 무엇을 읽고 있는지 실시간으로 흘린다.

`--output-format stream-json` 이벤트를 한 줄씩 받아 도구 호출을 사람 말로 바꿔
Beat.log 에 넣는다. 보드는 그걸 카드에 띄운다 (뒷단이 도는 걸 앞단이 본다).
반환은 최종 텍스트. 종량제 키 없음 — 구독 범위.
"""
import json, os, subprocess, sys
from pathlib import Path

import models  # noqa: E402  (모델 ID 한 자리 — scripts/models.json)

CLAUDE = str(Path.home() / ".local/bin/claude")
if not Path(CLAUDE).exists():   # 설치 위치가 다르면 PATH 에서 찾는다
    import shutil
    CLAUDE = shutil.which("claude") or CLAUDE
# 생각 깊이 (low·medium·high·xhigh·max). 2026-09-08 실측 4건: 도구는 42~74초에 끝나는데 마지막 턴(생각+답)이
# 50~100초, 출력 3.5k~10k 토큰 — 걸린 시간의 절반 이상이 여기다. 터미널에서 사람이 답할 때보다 두 배 느리다는
# 신고가 이것. 형식 고치기·마무리(--resume 1턴)는 내용 재작성이라 low.
EFFORT = os.environ.get("WORKER_EFFORT", "medium")
import paths  # noqa: E402  (루트 한 자리 — CANON_ROOT)
P = paths.root()
CODE = paths.code()
LAST_SID = None   # run() 이 마지막으로 연 claude 세션 — 형식 틀린 답을 이어받아 고칠 때 쓴다 (2026-09-07)
LAST_REASON = None   # run() 의 마지막 실패 원인 — "auth"(인증 만료) / "network"(API 서버 못 감) /
#                      subtype 문자열(그 외 실패) / None(원인 이름 없음·성공) (2026-09-11, network 2026-09-16)
LAST_DETAIL = None   # run() 이 None 으로 끝난 이유 한 줄(사람 말). 호출자는 failure_text() 로만 읽는다.
#                      2026-09-16: 원인을 매 회차 열거로 추가하다 네트워크 끊김을 "JSON 없음"으로 4회 오귀인했다.
# 인증 만료 로그 문구 — checkpoint_board.cli_login_ok 가 하트비트 로그에서 이 상수로 찾는다.
# 생산자·소비자가 문자열을 따로 안 들고 이 한 자리만 본다 (2026-09-11 Opus 지적).
AUTH_LINE = "로그인 만료 — 터미널에서 claude 를 열고 /login"
AUTH_FIX = AUTH_LINE + " 한 뒤 [다시]"
NETWORK_MARK = "Can't reach the API server"
NETWORK_FIX = "네트워크 끊김 — 인터넷·DNS 확인 뒤 [다시]"


def failure_text(default):
    """마지막 run() 실패를 사람 말 한 줄로 바꾼다.

    원인 열거는 여기 한 곳에만 둔다 — 호출자는 if 사슬 없이 이걸 부르고, 원인을 모를 때만
    자기 default(형식 불합격 문구)가 쓰인다.
    """
    if LAST_REASON == "auth":
        return AUTH_FIX
    if LAST_REASON == "network":
        return NETWORK_FIX
    if LAST_DETAIL:
        return "실패: " + LAST_DETAIL[:120]
    return default


def _label(name, inp):
    """도구 호출 한 줄 — 경로는 프로젝트 상대로 줄인다."""
    def rel(p):
        p = str(p or "")
        return p.replace(str(P) + "/", "").replace(str(Path.home()) + "/", "~/")
    if name == "Read":
        return f"읽는 중 {rel(inp.get('file_path'))}"
    if name in ("Grep", "Glob"):
        return f"찾는 중 {inp.get('pattern') or inp.get('query') or ''}"
    if name.endswith("search_docs"):
        return f"정본 검색 “{(inp.get('query') or '')[:40]}”"
    if name == "Bash":
        return f"실행 {(inp.get('command') or '')[:50]}"
    return f"{name}"


def run(prompt, beat, tools, model=None, timeout=420, max_turns=18, effort=None):
    # 사전 로그인 검사(claude auth status --json)는 뗐다 — 무효 토큰에도 loggedIn:true 를 줘서
    # 2026-09-11 사고(자격증명 있음·refresh 실패)를 못 잡았다. 실제로 잡히는 자리는 아래
    # result 이벤트의 "Failed to authenticate" 감지뿐이라 그것만 남긴다 (사용자 결정).
    global LAST_REASON, LAST_DETAIL
    LAST_REASON, LAST_DETAIL = None, None
    model = models.resolve(model)   # 비었으면 기본(opus), 옛 ID 는 현재 ID 로 — models.json
    if not Path(CLAUDE).exists():
        LAST_DETAIL = f"claude CLI 없음: {CLAUDE}"
        beat.log(LAST_DETAIL)
        return None
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    cmd = [CLAUDE, "-p", prompt, "--model", model, "--effort", effort or EFFORT, "--permission-mode", "acceptEdits",
           "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns)] + (["--allowedTools", *tools] if tools else [])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=P, env=env, bufsize=1)
    final, reads, sid, reason = None, 0, None, None
    result_err, api_err = "", ""   # result 의 오류 문구 / 마지막 "API Error:" assistant 텍스트
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "tool_use":
                        reads += 1
                        beat.phase(f"자료 확인 {reads}건")
                        beat.log(_label(c.get("name", ""), c.get("input") or {}))
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        # CLI 가 result 이벤트 없이 죽는 회차(네트워크 끊김)는 이 텍스트가 유일한 단서다
                        txt = c["text"].strip()
                        if txt.startswith("API Error:"):
                            api_err = " ".join(txt.split())
                            beat.log(api_err[:140])
                        else:
                            beat.log("생각 정리 중")
            elif t == "result":
                final = ev.get("result")
                sid = ev.get("session_id") or sid
                if ev.get("is_error"):
                    sub = ev.get("subtype") or ""
                    text = str(final or "")
                    result_err = " ".join(text.split())
                    known = {"error_max_turns": f"턴 한도({max_turns}) 초과 — 탐색만 하다 끝났다",
                             "error_during_execution": "실행 중 오류"}
                    # 인증 만료 판정은 subtype 이 이미 아는 실패 종류(턴 한도·실행 중 오류)가
                    # 아닐 때만 본다 — 아니면 "실행 중 오류"인데 모델이 마침 OAuth 관련 글을
                    # 요약하던 본문이 남아 오판한다 (2026-09-11 Opus 검증 지적). 문구도
                    # "authenticate"·"OAuth" 처럼 느슨하게 잡지 않고 CLI가 실제로 내는
                    # "Failed to authenticate" 로 좁힌다.
                    if sub not in known and "Failed to authenticate" in text:
                        beat.log(AUTH_LINE)
                        LAST_REASON = "auth"
                        reason = "auth"   # _finish() 는 "error_max_turns"만 재시도하므로 여기서 막힌다
                    else:
                        # subtype·result 문구를 둘 다 남긴다 — 전엔 문구가 있으면 subtype 이
                        # 로그에서 사라졌다(2026-09-11 지적: unknown subtype + 본문 있으면 why=본문뿐).
                        why = text[:120] if text else (known.get(sub) or "알 수 없음")
                        beat.log(f"CLI 오류: {sub or '알 수 없음'}: {why}")
                        LAST_REASON = "error" if sub in ("", "success") else sub
                        reason = sub or "error"
                    final = None
            elif t == "system" and ev.get("subtype") == "init":
                sid = ev.get("session_id") or sid
                beat.sid(sid)   # 관제판에서 이 실행 전문을 열 수 있게
                global LAST_SID
                LAST_SID = sid
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        LAST_DETAIL = f"시간 초과 {timeout}초"
        beat.log(LAST_DETAIL)
        return None
    if final is None:
        err = (proc.stderr.read() or "").strip().splitlines()
        if err:
            beat.log(err[-1][:140])
        # 턴 한도로 끝난 건 탐색만 하다 만 것 — 그 세션을 이어받아 "이제 JSON만" 한 번 더.
        # 도구를 안 주므로 더 못 뒤지고, 이미 읽은 내용으로 답을 낸다 (호출 1회 추가).
        if reason == "error_max_turns" and sid:
            beat.phase("탐색 중단 — 지금까지 확인한 것으로 정리")
            final = _finish(sid, model, beat)
        if final is None:
            # 원인 문구를 항상 남긴다 — 호출자가 "JSON 없음"으로 오귀인하지 않게 (2026-09-16).
            LAST_DETAIL = (result_err or api_err
                           or (err[-1].strip() if err else "")
                           or "출력 없음 (result 이벤트 없음)")[:200]
            if NETWORK_MARK in (result_err + " " + api_err):
                LAST_REASON = "network"
                beat.log(NETWORK_FIX)
    return final


def _finish(sid, model, beat):
    """턴 한도로 끊긴 세션을 이어받아 결론만 받는다 (도구 없음)."""
    model = models.resolve(model)
    cmd = [CLAUDE, "-p", "탐색은 여기까지다. 새로 파일을 열지 말고 지금까지 확인한 것만으로 "
           "앞서 지시한 JSON 한 개만 출력해라. 확인 못 한 것은 body에 '확인 못 함'으로 적는다.",
           "--resume", sid, "--model", model, "--effort", "low", "--output-format", "json", "--max-turns", "1"]
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=P, env=env, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        beat.log("이어받기 실패")
        return None
    try:
        o = json.loads(r.stdout)
    except json.JSONDecodeError:
        beat.log("이어받기 출력 파싱 실패")
        return None
    if o.get("is_error"):
        beat.log(f"이어받기도 실패: {str(o.get('result') or '')[:120]}")
        return None
    return o.get("result")


def repair_json(sid, model, beat, contract):
    """형식이 계약과 안 맞는 답을 같은 세션에서 한 번 고쳐 받는다 (도구 없음, 호출 1회).

    2026-09-01 지도 후속 2건이 여기서 떨어져 pending 으로 되돌아간 채 아무도 다시 안 돌렸다.
    내용은 멀쩡했으므로 다시 탐색시키지 않고 "형식만 맞춰 다시" 가 맞다.
    """
    if not sid:
        return None
    model = models.resolve(model)
    beat.phase("답 형식 고치는 중")
    cmd = [CLAUDE, "-p", "방금 답의 형식이 계약과 맞지 않았다. 새로 파일을 열지 말고, 같은 내용을 "
           "아래 계약에 맞는 JSON 한 개로만 다시 출력해라 (코드펜스·설명 금지).\n" + contract,
           "--resume", sid, "--model", model, "--effort", "low", "--output-format", "json", "--max-turns", "1"]
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=P, env=env, timeout=180)
        o = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        beat.log("형식 고치기 실패")
        return None
    if o.get("is_error"):
        beat.log(f"형식 고치기도 실패: {str(o.get('result') or '')[:120]}")
        return None
    return o.get("result")


def extract_json_array(text):
    """모델 출력에서 JSON 배열 하나 건지기."""
    if not text:
        return None
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


def extract_json(text):
    """모델 출력에서 JSON 하나 건지기 (코드펜스·잡담 섞여도)."""
    if not text:
        return None
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None
