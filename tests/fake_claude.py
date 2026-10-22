"""시험용 가짜 claude -p. stdin 프롬프트에서 기록 원문 첫 문장을 근거로 후보 하나를 돌려준다.
FAKE_MODE=ungrounded 면 원문에 없는 인용을, FAKE_MODE=garbage 면 JSON 이 아닌 답을 낸다.
FAKE_JOB=1 이면 agent/jobs.py 작업 결과 꼴(정리 텍스트·변경 목록)을 낸다 — 아래 분기."""
import json
import os
import sys
import time

prompt = sys.stdin.read()

if os.environ.get("FAKE_JOB") == "1":
    # agent/jobs.py 시험용 분기(가산만 — 아래 기존 [기록 원문] 경로는 그대로).
    # FAKE_MODE: ok · garbage(JSON 아님) · shape(바깥 꼴 틀림) · inner(result 안 꼴 틀림) · login(로그인 오류, exit 1)
    #            · exit(exit 2) · slow(FAKE_SLEEP 초 잠듦 뒤 ok) · is_error(is_error true)
    # --resume <id> 가 argv 에 있으면 그 id 를 session_id 로 되돌린다(실 CLI 와 같은 꼴). 없으면 FAKE_SESSION 또는 고정값.
    mode = os.environ.get("FAKE_MODE", "ok")
    argv = sys.argv[1:]
    resumed = argv[argv.index("--resume") + 1] if "--resume" in argv and argv.index("--resume") + 1 < len(argv) else None
    sid = resumed or os.environ.get("FAKE_SESSION", "fake-session-0001")
    if mode == "slow":
        time.sleep(float(os.environ.get("FAKE_SLEEP", "5")))
        mode = "ok"
    if mode == "login":
        sys.stderr.write("Invalid API key - Please run /login\n")
        sys.exit(1)
    if mode == "exit":
        sys.stderr.write("fake failure\n")
        sys.exit(2)
    if mode == "garbage":
        print("죄송하지만 형식을 못 맞췄습니다")
        sys.exit(0)
    if mode == "shape":
        print(json.dumps(["not", "an", "object"]))
        sys.exit(0)
    inner = {"summary_text": "정리: 입력 %d자" % len(prompt), "changes": [], "blocked": [], "text_map": []}
    if mode == "inner":
        inner = {"unexpected": True}
    out = {"type": "result", "is_error": mode == "is_error", "result": json.dumps(inner, ensure_ascii=False),
           "session_id": sid, "model": "fake-model", "resumed": resumed is not None}
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)

body = prompt.split("[기록 원문]", 1)[1].split("규칙:", 1)[0].strip()
first = body.splitlines()[0].split(". ")[0].strip()
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "garbage":
    result = "죄송하지만 형식을 못 맞췄습니다"
elif mode == "ungrounded":
    result = json.dumps({"candidates": [{"text": "지어낸 판단", "quote": "원문에 없는 문장"}]}, ensure_ascii=False)
else:
    result = "```json\n" + json.dumps({"candidates": [{"text": "가짜 모델의 후보", "quote": first}]},
                                      ensure_ascii=False) + "\n```"
print(json.dumps({"type": "result", "is_error": False, "result": result}, ensure_ascii=False))
