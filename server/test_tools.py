"""도구 흐름 시험 — SDK 내장 클라이언트로 서버를 직접 부른다 (HTTP·인증 없이).

cd server && .venv/bin/python test_tools.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["CANON_DB"] = str(Path(tempfile.mkdtemp(prefix="canon-db ")) / "t.sqlite3")
os.environ["CANON_DEV_USER"] = "tester@example.com"
sys.path.insert(0, str(Path(__file__).parent))

from mcp.client import Client  # noqa: E402

import main  # noqa: E402


def val(res):
    """도구 결과에서 구조화 값 또는 텍스트를 꺼낸다."""
    if getattr(res, "structured_content", None) is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


async def run():
    async with Client(main.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert {"state_get", "record_add", "candidates_propose", "candidate_decide"} <= tools, tools

        st = val(await c.call_tool("state_get", {}))
        assert st["next"]["kind"] == "record"

        q = val(await c.call_tool("question_add", {"text": "새 도구를 팀에 들일 때 무엇부터 확인해야 하나"}))
        r = val(await c.call_tool("record_add", {"text": "도입 회의에서 입력 담당을 먼저 정했다. 도구 비교는 그다음이었다.",
                                                  "title": "도입 회의", "question_id": q["id"]}))
        # 근거 검사: 원문에 없는 인용은 버림, 있는 것만 저장
        out = val(await c.call_tool("candidates_propose", {
            "record_id": r["id"], "question_id": q["id"], "origin": "ai",
            "candidates": [{"text": "입력 담당을 먼저 정한다", "quote": "입력 담당을 먼저 정했다"},
                           {"text": "지어낸 판단", "quote": "원문에 없는 문장"}]}))
        assert len(out["added"]) == 1 and out["added"][0]["status"] == "pending", out
        assert out["dropped"][0]["why"].startswith("근거 인용이 원문에"), out
        cid = out["added"][0]["id"]
        # 같은 근거 구간은 중복 저장 안 됨 (문장을 바꿔도)
        again = val(await c.call_tool("candidates_propose", {
            "record_id": r["id"], "question_id": q["id"], "origin": "ai",
            "candidates": [{"text": "다른 문장", "quote": "입력 담당을 먼저 정했다"}]}))
        assert not again["added"] and again["dropped"][0]["why"] == "이미 있는 후보", again

        # 결정과 되돌리기, 이력
        c1 = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "edit", "text": "입력 담당부터 정한다"}))
        assert c1["text"] == "입력 담당부터 정한다" and c1["edited_by_user"] is True
        c2 = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "confirm"}))
        assert c2["status"] == "confirmed"
        c3 = val(await c.call_tool("candidate_decide", {"candidate_id": cid, "action": "pending"}))
        assert c3["status"] == "pending"
        hist = main.db.candidate_history(main.db.user_by_email("tester@example.com")["id"], cid)
        assert [h["action"] for h in hist] == ["edit", "confirm", "pending"] and hist[0]["prev_text"] == "입력 담당을 먼저 정한다"

        st = val(await c.call_tool("state_get", {}))
        assert st["next"]["kind"] == "review" and len(st["candidates"]) == 1 and len(st["journal"]) == 1

        # 오류는 ToolError 로 모델에 전달
        bad = await c.call_tool("candidate_decide", {"candidate_id": "c_nope", "action": "confirm"})
        assert bad.is_error and "ARG-INVALID" in bad.content[0].text, bad

        # 사용자 격리: 다른 이메일은 빈 상태
        os.environ["CANON_DEV_USER"] = "other@example.com"
        st2 = val(await c.call_tool("state_get", {}))
        assert st2["questions"] == [] and st2["journal"] == []
    print("OK — 도구 흐름 통과")


if __name__ == "__main__":
    asyncio.run(run())
