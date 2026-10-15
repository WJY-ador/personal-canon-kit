"""시험용 가짜 claude -p. stdin 프롬프트에서 기록 원문 첫 문장을 근거로 후보 하나를 돌려준다.
FAKE_MODE=ungrounded 면 원문에 없는 인용을, FAKE_MODE=garbage 면 JSON 이 아닌 답을 낸다."""
import json
import os
import sys

prompt = sys.stdin.read()
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
