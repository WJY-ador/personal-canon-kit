"""구독 Claude Code CLI(`claude -p`) 호출. API 키는 받지 않는다.

프롬프트는 argv 가 아니라 stdin 으로 넘긴다 — Windows 의 claude.cmd 인자 이스케이프·길이 제한을 피한다.
CANON_CLAUDE 환경변수로 실행 파일을 바꿀 수 있다(시험용 가짜 모델).
"""
import json
import os
import re
import shutil
import subprocess
import sys

from .store import CanonError

TIMEOUT = 300


def find_claude():
    override = os.environ.get("CANON_CLAUDE")
    if override:
        return override
    return shutil.which("claude")       # Windows 는 PATHEXT 로 claude.cmd 도 찾는다


def ask(prompt, cwd=None):
    exe = find_claude()
    if not exe:
        raise CanonError("CLI-MISSING", "Claude Code CLI(claude)를 찾지 못했습니다.",
                         "guides/02-install-macos.md 또는 03-install-windows.md 의 CLI 설치 절을 따르세요.")
    cmd = [exe, "-p", "--output-format", "json", "--max-turns", "1"]
    if exe.endswith(".py"):                 # 시험용 가짜 모델 — OS 무관하게 같은 파이썬으로 돌린다
        cmd = [sys.executable] + cmd
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=TIMEOUT, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise CanonError("MODEL-TIMEOUT", f"모델 응답이 {TIMEOUT}초 안에 오지 않았습니다.",
                         "잠시 뒤 한 번만 다시 실행하세요. 반복되면 python3 canon.py diag 로 진단을 만드세요.")
    except OSError as e:
        raise CanonError("CLI-FAILED", f"claude 실행 실패: {e}", "python3 canon.py doctor 로 CLI 경로를 확인하세요.")
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        low = (out + err).lower()
        if "login" in low or "auth" in low or "/login" in low:
            raise CanonError("CLI-LOGIN", "Claude Code 로그인이 필요합니다.",
                             "터미널에서 claude 를 열고 /login 한 뒤 다시 실행하세요.")
        raise CanonError("MODEL-FAILED", f"모델 실행 실패 (exit {r.returncode})",
                         "한 번만 다시 실행하세요. 반복되면 diag 로 진단을 만드세요.")
    try:
        env = json.loads(out)
        if env.get("is_error"):
            raise CanonError("MODEL-FAILED", "모델이 오류를 돌려줬습니다.", "한 번만 다시 실행하세요.")
        return env.get("result", "")
    except ValueError:
        return out


def extract_json(text):
    """응답에서 JSON 객체 하나를 꺼낸다. 코드펜스가 있어도 된다."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(raw)
    except ValueError:
        raise CanonError("MODEL-FORMAT", "모델 답이 약속한 형식(JSON)이 아닙니다.",
                         "이전 정상본은 그대로입니다. 한 번만 다시 실행하세요.")


DIGEST_PROMPT = """너는 사용자의 기록에서 '판단 후보'를 찾는 도우미다. 판단을 확정하지 않는다 — 후보만 낸다.

[사용자의 질문]
{question}

[기록 원문]
{body}

규칙:
- 이 기록에서 위 질문에 답이 되는 판단 후보를 최대 3개 낸다. 없으면 빈 목록.
- 각 후보에 근거 인용(quote)을 붙인다. quote 는 원문에서 글자 그대로 복사한 연속 구간이어야 한다(한 문장 이내).
- 원문에 없는 사실·수치를 만들지 않는다. 타인의 말로 보이는 문장은 후보로 내지 않는다.
- 다른 설명 없이 JSON 하나만 출력한다.

형식:
{{"candidates": [{{"text": "판단 후보 한 문장", "quote": "원문 그대로의 근거 구간"}}]}}
"""
