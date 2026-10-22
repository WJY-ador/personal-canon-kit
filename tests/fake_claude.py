"""시험용 가짜 claude -p. stdin 프롬프트에서 기록 원문 첫 문장을 근거로 후보 하나를 돌려준다.
FAKE_MODE=ungrounded 면 원문에 없는 인용을, FAKE_MODE=garbage 면 JSON 이 아닌 답을 낸다.
FAKE_JOB=1 이면 agent/jobs.py 작업 결과 꼴(정리 텍스트·변경 목록)을 낸다 — 아래 분기.
FAKE_JOB=1 이고 표준 입력에 '=== STACK/1 ===' 가 있으면 stack/1 본문을 낸다(FAKE_STACK 시나리오, _stack_body)."""
import json
import os
import sys
import time

prompt = sys.stdin.read()


def _section(text, name):
    """'=== NAME ===' 다음 줄부터 다음 '=== ' 줄 전까지(입력 절은 끝까지)."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("=== " + name):
            out = []
            for nx in lines[i + 1:]:
                if nx.startswith("=== ") and not name.startswith(("INPUT", "EDITED")):
                    break
                out.append(nx)
            return "\n".join(out).strip("\n")
    return None


def _stack_body(prompt, smode):
    """FAKE_STACK 시나리오 — 정의 절에서 첫 정의(판단 종류 우선)를 골라 stack/1 본문을 만든다.
    ok · contract-bad · forbidden-field · unref-link · inferred · blocked · restack · cycle · oversize · candidate
    FAKE_STACK 에 쉼표로 여러 값을 주고 FAKE_COUNTER(파일 경로)를 주면 실행 차례마다 다음 값(재시도 시험)."""
    counter = os.environ.get("FAKE_COUNTER")
    if counter and "," in smode:
        n = 0
        if os.path.exists(counter):
            with open(counter) as f:
                n = int(f.read() or 0)
        with open(counter, "w") as f:
            f.write(str(n + 1))
        seq = smode.split(",")
        smode = seq[min(n, len(seq) - 1)]
    defs = json.loads(_section(prompt, "DEFINITIONS"))
    objs = defs["objects"]
    obj = next((o for o in objs if o.get("kind") == "judgment"), objs[0])
    fields = {f["key"]: (f["enum"][0] if f.get("enum") else "가짜 값") for f in obj["fields"] if f.get("required")}
    text = _section(prompt, "INPUT") or _section(prompt, "EDITED TEXT") or ""
    first = text.splitlines()[0][:40] if text else ""
    ch = {"cid": "c1", "op": "create", "object_id": obj["object_id"], "target": {"ref_kind": "none"}, "fields": fields,
          "links": [], "depends_on": []}
    body = {"units": [{"uid": "u1", "text": "정리: " + first, "change_ids": ["c1"]}], "changes": [ch], "blocked": []}
    if smode == "contract-bad":
        body["changes"][0]["op"] = "delete"
    elif smode == "forbidden-field":
        body["changes"][0]["stage"] = "confirmed"
    elif smode == "unref-link":
        rel = obj["relations"][0] if obj["relations"] else {"key": "axes"}
        body["changes"][0]["links"] = [{"relation": rel["key"], "target": {"value": "same-date"}}]
    elif smode == "inferred":
        body["changes"].append({"cid": "c2", "op": "update", "object_id": obj["object_id"],
                                "target": {"ref_kind": "inferred", "value": "item-guess-01"}, "fields": {}})
        body["units"].append({"uid": "u2", "text": "추정: 앞 판단을 고친다", "change_ids": ["c2"]})
    elif smode == "blocked":
        body["changes"].append({"cid": "c2", "op": "update", "object_id": obj["object_id"],
                                "target": {"ref_kind": "unresolved"}, "fields": {}})
        body["units"].append({"uid": "u2", "text": "어느 항목인지 모름", "change_ids": ["c2"]})
        body["blocked"] = [{"cid": "c2", "code": "unresolved_target", "question": "어느 항목을 고칠까요?"}]
    elif smode == "restack":
        prev = json.loads(_section(prompt, "PREVIOUS UNITS") or "[]")
        uid = prev[0]["uid"] if prev else "u1"
        body["units"] = [{"uid": uid, "text": "재정리: " + first, "change_ids": ["c1"]}]
    elif smode == "cycle":
        body["changes"].append({"cid": "c2", "op": "create", "object_id": obj["object_id"], "target": {"ref_kind": "none"},
                                "fields": fields, "depends_on": ["c1"]})
        body["units"][0]["change_ids"].append("c2")
        body["changes"][0]["depends_on"] = ["c2"]
    elif smode == "candidate":
        # 후보 표의 첫 항목·사람을 explicit_id 로(후보가 없으면 ok 와 같다)
        cands = json.loads(_section(prompt, "CANDIDATES") or "{}")
        links = []
        for rel in obj["relations"]:
            tk, to = rel.get("target_kind"), rel.get("target_object")
            pool = cands.get("people" if tk == "person" else "sources" if tk == "source" else "items", [])
            if tk == "item" or to:
                pool = [c for c in pool if not to or c.get("object_id") == to]
            elif tk not in ("person", "source"):
                pool = []
            if pool:
                links.append({"relation": rel["key"], "target": {"ref_kind": "explicit_id", "target_kind": tk,
                                                                 "value": pool[0]["id"]}})
        body["changes"][0]["links"] = links
    elif smode == "oversize":
        body["units"][0]["text"] = "가" * 4001
    return body


if os.environ.get("FAKE_JOB") == "1":
    # agent/jobs.py 시험용 분기(가산만 — 아래 기존 [기록 원문] 경로는 그대로).
    # FAKE_MODE: ok · garbage(JSON 아님) · shape(바깥 꼴 틀림) · inner(result 안 꼴 틀림) · login(로그인 오류, exit 1)
    #            · exit(exit 2) · slow(FAKE_SLEEP 초 잠듦 뒤 ok) · is_error(is_error true)
    #            · hang(손자 프로세스를 띄우고 FAKE_SLEEP 초 잠듦 뒤 ok — serve 종료 시험. FAKE_PID_FILE 에 자기·손자 pid,
    #              FAKE_IGNORE_TERM=1 이면 자기·손자 모두 SIGTERM 무시 → SIGKILL 단계 확인)
    # --resume <id> 가 argv 에 있으면 그 id 를 session_id 로 되돌린다(실 CLI 와 같은 꼴). 없으면 FAKE_SESSION 또는 고정값.
    mode = os.environ.get("FAKE_MODE", "ok")
    argv = sys.argv[1:]
    resumed = argv[argv.index("--resume") + 1] if "--resume" in argv and argv.index("--resume") + 1 < len(argv) else None
    sid = resumed or os.environ.get("FAKE_SESSION", "fake-session-0001")
    if mode == "hang":
        import signal
        import subprocess
        ign = os.environ.get("FAKE_IGNORE_TERM") == "1"
        code = ("import signal,time\n" + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ign else "")
                + "time.sleep(600)\n")
        gc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ign:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        pid_file = os.environ.get("FAKE_PID_FILE")
        if pid_file:
            with open(pid_file + ".tmp", "w") as f:
                f.write("%d %d" % (os.getpid(), gc.pid))
            os.replace(pid_file + ".tmp", pid_file)
        time.sleep(float(os.environ.get("FAKE_SLEEP", "600")))
        mode = "ok"
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
    if "=== STACK/1 ===" in prompt:
        inner = _stack_body(prompt, os.environ.get("FAKE_STACK") or ("contract-bad" if mode == "inner" else "ok"))
    else:
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
