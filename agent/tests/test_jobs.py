"""작업 전달(②-a) PC 쪽 — 계약 §5·G3 를 재현한 스텁 서버로 실패표(r2 R5) 1·2·3·5·7·10·20 행.

전부 합성 자료. 실 claude CLI·운영 서버·외부 모델 없음. 모델 실행 = tests/fake_claude.py(FAKE_JOB=1).
스텁 포트 기본 0(OS 배정), JOBS_STUB_PORT 로 고정 가능. PC 쪽 시각·대기는 가상 시계(실제로 30초 자지 않는다).
"""
from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from agent import jobs
from agent.tests.helpers import TOKEN, TempHome

TOKEN_B = "imp_is_6b6b6b6b6b6b6b6b6b6b6b6b.tok_Synth02." + "T" * 43
SECRET_TEXT = "합성 원문 — 서버 일반 요청에 나오면 안 되는 문장 SYNTH-INPUT-7731"
LEASE_SEC = 300
EXPIRE_SEC = 24 * 3600
DEFS_V = "defs-v1"
DEFAULT_DEFS = {
    "definitions_version": DEFS_V, "rule_version": 1,
    "objects": [
        {"id": "obj-judgment", "key": "judgment", "origin": "core", "label": "판단", "kind": "judgment",
         "fields": [{"key": "text", "type": "text", "required": True, "max": 2000},
                    {"key": "status", "type": "enum", "required": False, "enum": ["open", "closed"]}],
         "relations": [{"key": "axes", "target_object": "obj-axis", "cardinality": "many"}],
         "identity": [{"scheme": "board", "field": "board_id"}],
         "protected_fields": ["board_id"], "stage_values": ["draft", "organized", "confirmed"],
         "confirm_required": True},
        {"id": "obj-axis", "key": "axis", "origin": "core", "label": "축", "kind": "classify",
         "fields": [{"key": "name", "type": "text", "required": True}],
         "relations": [], "identity": [], "protected_fields": [], "stage_values": ["draft", "organized"],
         "confirm_required": False},
    ],
    "content_rules": ["타인의 말은 옮기지 않는다"],
}


class StubJobsServer:
    """§5 계약 재현. 상태 = queued|leased|running|result_ready|failed|cancelled|expired|needs_check (+deleted).
    서버 시각은 수동(tick). 강제 응답(script)·연결 끊기(drop)·세대 올리기·삭제를 시험이 주입한다."""

    def __init__(self, tokens=None):
        self.tokens = tokens or {TOKEN: "dev-a", TOKEN_B: "dev-b"}
        self.lock = threading.Lock()
        self.clock = 0.0
        self.jobs = {}
        self.log = []                 # {method, path, query, body, device}
        self.poll_script = []         # (status, headers, payload) 앞에서부터
        self.drop = {}                # (route, job_id) -> {"n": 남은 횟수, "apply": 적용 뒤 끊기}
        self.route_script = {}        # (route, job_id) -> [(status, payload, headers)] 앞에서부터 강제 응답
        self.defs = DEFAULT_DEFS       # 정의 스냅샷(최초 발급 때 job 에 고정 — §3-2)
        self.latest_edit = {}          # session_ref -> 받은 결과의 최대 edit_generation(재정리 역전 거부 흉내)
        self.inflight = {}
        self.max_inflight = {}
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _reply(self, status, payload=None, headers=None):
                data = b"" if payload is None else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if status != 204:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def _handle(self, method):
                dev = stub.tokens.get((self.headers.get("Authorization") or "").replace("Bearer ", "", 1))
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                u = urlsplit(self.path)
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = raw.decode("utf-8", "replace")
                with stub.lock:
                    stub.log.append({"method": method, "path": u.path, "query": parse_qs(u.query), "body": body,
                                     "raw": raw, "device": dev})
                    stub.inflight[dev] = stub.inflight.get(dev, 0) + 1
                    stub.max_inflight[dev] = max(stub.max_inflight.get(dev, 0), stub.inflight[dev])
                try:
                    if dev is None:
                        return self._reply(401, {"code": "AUTH-INVALID"})
                    r = stub.route(method, u.path, parse_qs(u.query), body, dev)
                    if r == "drop":
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return None
                    return self._reply(*r)
                finally:
                    with stub.lock:
                        stub.inflight[dev] -= 1

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        port = int(os.environ.get("JOBS_STUB_PORT", "0"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.th.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # ── 시험 조작 ──
    def create(self, kind="converse", text=SECRET_TEXT, session_ref=None, input_version=1) -> str:
        jid = str(uuid.uuid4())
        with self.lock:
            self.jobs[jid] = {"id": jid, "kind": kind, "status": "queued", "device": None, "generation": 0,
                              "lease_expires": None, "expires": self.clock + EXPIRE_SEC, "text": text,
                              "input_version": input_version, "session_ref": session_ref, "attempts": 0,
                              "result": None, "reason_code": None, "seq": len(self.jobs),
                              "definitions_version": None, "edit_generation": 0, "parent_result_version": None,
                              "input_kind": "converse", "restack": None, "defs_calls": 0}
        return jid

    def tick(self, sec: float):
        with self.lock:
            self.clock += sec
            self._sweep()

    def _sweep(self):
        for j in self.jobs.values():
            if j["status"] in ("queued", "leased", "running") and self.clock >= j["expires"]:
                j["status"], j["text"] = "expired", None
            elif j["status"] in ("leased", "running") and self.clock >= j["lease_expires"]:
                j["status"] = "needs_check"          # 재할당하지 않는다(중복 실행 금지)

    def delete(self, jid):
        with self.lock:
            j = self.jobs[jid]
            j["status"], j["text"] = "deleted", None

    def bump_generation(self, jid, device="dev-b"):
        """임대 교체 흉내 — 같은 작업이 다른 세대로 넘어감."""
        with self.lock:
            j = self.jobs[jid]
            j["generation"] += 1
            j["device"] = device
            j["lease_expires"] = self.clock + LEASE_SEC

    def status(self, jid):
        with self.lock:
            return self.jobs[jid]["status"]

    def requests(self, route=None, method=None):
        with self.lock:
            out = list(self.log)
        if method:
            out = [r for r in out if r["method"] == method]
        if route:
            out = [r for r in out if r["path"].endswith("/" + route) or (route == "poll" and r["path"] == jobs.JOBS_PATH)]
        return out

    # ── 계약 ──
    def route(self, method, path, q, body, dev):
        parts = path.strip("/").split("/")          # api device jobs [id] [route]
        if parts[:3] != ["api", "device", "jobs"]:
            return (404, {"code": "NOT-FOUND"})
        with self.lock:
            self._sweep()
            if len(parts) == 3 and method == "GET":
                if self.poll_script:
                    return self.poll_script.pop(0)
                cands = sorted((j for j in self.jobs.values() if j["status"] == "queued"), key=lambda j: j["seq"])
                if not cands:
                    return (204, None)
                j = cands[0]                         # 원자 할당(잠금 안)
                j["status"], j["device"] = "leased", dev
                j["generation"] += 1
                j["lease_expires"] = self.clock + LEASE_SEC
                return (200, {"id": j["id"], "kind": j["kind"], "session_ref": j["session_ref"],
                              "input_ref": "in_" + j["id"][:8], "input_version": j["input_version"],
                              "definitions_version": j["definitions_version"],
                              "edit_generation": j["edit_generation"],
                              "parent_result_version": j["parent_result_version"],
                              "lease": {"generation": j["generation"], "expires_at": j["lease_expires"]}})
            if len(parts) != 5:
                return (404, {"code": "NOT-FOUND"})
            jid, rt = parts[3], parts[4]
            j = self.jobs.get(jid)
            if j is None or j["status"] in ("deleted", "expired", "cancelled"):
                return (410, {"code": "JOB-GONE"})
            key = (rt, jid)
            if self.route_script.get(key):
                return self.route_script[key].pop(0)
            d = self.drop.get(key)
            if d and d["n"] > 0 and not d["apply"]:
                d["n"] -= 1
                return "drop"
            if rt == "input" and method == "GET":
                gen = int((q.get("lease_generation") or ["-1"])[0])
                if j["device"] != dev or gen != j["generation"] or j["status"] not in ("leased", "running"):
                    return (409, {"code": "JOB-LEASE"})
                j["status"] = "running"
                j["attempts"] += 1
                out = {"text": j["text"], "input_version": j["input_version"]}
                if j["kind"] == "converse":
                    out.update(input_kind=j["input_kind"], edit_generation=j["edit_generation"], restack=j["restack"])
                return (200, out)
            if rt == "definitions" and method == "GET":
                gen = int((q.get("lease_generation") or ["-1"])[0])
                if j["device"] != dev or gen != j["generation"] or j["status"] != "running":
                    return (409, {"code": "JOB-LEASE"})
                j["defs_calls"] += 1
                if j["definitions_version"] is None:          # 최초 발급 때 고정, 이후 같은 값
                    j["definitions_version"] = self.defs["definitions_version"]
                    j["defs_snapshot"] = self.defs
                return (200, j["defs_snapshot"])
            if rt == "result" and method == "POST":
                if not isinstance(body, dict):
                    return (400, {"code": "BAD-REQUEST"})
                env = body.get("envelope") if isinstance(body.get("envelope"), dict) else {}
                rv = env.get("result_version", body.get("result_version"))
                iv = env.get("input_version", body.get("input_version"))
                if (j["status"] == "result_ready" and j["result"] is not None
                        and _rv(j["result"]) == rv):
                    r = (200, {"duplicate": True})
                elif j["device"] != dev or body.get("lease_generation") != j["generation"] or j["status"] != "running":
                    return (409, {"code": "JOB-LEASE"})
                elif iv != j["input_version"]:
                    return (409, {"code": "JOB-VERSION"})
                elif env and (env.get("definitions_version") != j["definitions_version"]
                              or env.get("edit_generation") != j["edit_generation"]):
                    return (409, {"code": "JOB-DEFS" if env.get("definitions_version") != j["definitions_version"]
                                  else "JOB-VERSION"})
                elif env and j["session_ref"] and env["edit_generation"] < self.latest_edit.get(j["session_ref"], 0):
                    return (409, {"code": "JOB-VERSION"})            # 늦게 온 옛 편집 세대 결과
                else:
                    if env and j["session_ref"]:
                        self.latest_edit[j["session_ref"]] = max(self.latest_edit.get(j["session_ref"], 0),
                                                                 env["edit_generation"])
                    j["status"], j["result"] = "result_ready", body
                    r = (200, {"status": "result_ready"})
                if d and d["n"] > 0 and d["apply"]:
                    d["n"] -= 1
                    return "drop"                    # 저장은 됐고 응답만 유실
                return r
            if rt == "fail" and method == "POST":
                if not isinstance(body, dict) or set(body) != {"lease_generation", "reason_code"} \
                        or body["reason_code"] not in jobs.REASON_CODES:
                    return (400, {"code": "BAD-REQUEST"})
                if j["device"] != dev or body["lease_generation"] != j["generation"] or j["status"] != "running":
                    return (409, {"code": "JOB-LEASE"})
                j["status"], j["reason_code"] = "failed", body["reason_code"]
                return (200, {"status": "failed"})
            return (404, {"code": "NOT-FOUND"})


def _rv(body):
    env = body.get("envelope") if isinstance(body.get("envelope"), dict) else {}
    return env.get("result_version", body.get("result_version"))


class VClock:
    """PC 쪽 가상 시계 — sleep 은 시각만 넘긴다."""

    def __init__(self):
        self.t = 1000.0
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def sleep(self, s):
        with self.lock:
            self.t += max(0.0, s)


class CountingRunner:
    """실행 횟수·argv 기록 + 실행 중 끼어들기(hook) — 실제 실행은 fake_claude 서브프로세스."""

    def __init__(self, hook=None):
        self.calls = []
        self.lock = threading.Lock()
        self.hook = hook

    def __call__(self, argv, *, stdin_bytes, env, cwd, timeout):
        from agent.summary import _subprocess_runner
        with self.lock:
            self.calls.append(list(argv))
        if self.hook:
            self.hook()
        return _subprocess_runner(argv, stdin_bytes=stdin_bytes, env=env, cwd=cwd, timeout=timeout)


class Base(unittest.TestCase):
    def setUp(self):
        self.srv = StubJobsServer()
        self.th = TempHome()

    def tearDown(self):
        self.srv.close()
        self.th.cleanup()

    def worker(self, mode="ok", token=TOKEN, hook=None, state=None, **adapter_kw):
        clock = VClock()
        runner = CountingRunner(hook)
        adapter = jobs.fake_adapter(mode, exec_runner=runner, **adapter_kw)
        w = jobs.Worker(jobs.Client(self.srv.url, token, wait=1), adapter, state or jobs.MemoryState(),
                        monotonic=clock.now, sleep=clock.sleep, today=lambda: "2026-10-22")
        w.runner, w.clock = runner, clock
        return w

    def assert_no_secret(self, routes=("poll", "input", "result", "fail")):
        """PC → 서버 요청 본문·쿼리에 입력 원문이 없다(결과 정리 텍스트만 허용)."""
        for r in self.srv.log:
            self.assertNotIn(SECRET_TEXT.encode("utf-8"), r["raw"])
            self.assertNotIn("SYNTH-INPUT-7731", json.dumps(r["query"], ensure_ascii=False))


class HappyPathTest(Base):
    def test_poll_input_result(self):
        """converse 외 종류(summarize_reading)는 ②-a 꼴 그대로. converse(stack/1) 꼴은 test_stack."""
        jid = self.srv.create(kind="summarize_reading")
        w = self.worker()
        out = w.poll_once()
        self.assertEqual(out["outcome"], "result-sent")
        self.assertEqual(self.srv.status(jid), "result_ready")
        self.assertEqual(len(w.runner.calls), 1)
        res = self.srv.jobs[jid]["result"]
        self.assertEqual(set(res), {"lease_generation", "input_version", "result_version", "summary_text", "changes",
                                    "blocked", "text_map", "model_meta"})
        self.assertEqual(set(res["model_meta"]), {"tool", "model", "duration_ms"})   # CLI 세션 ID 없음
        self.assertNotIn("session", json.dumps(res))
        self.assertEqual(self.srv.jobs[jid]["attempts"], 1)
        self.assertEqual(w.state.attempts("2026-10-22"), 1)
        self.assertEqual(w.state.leases(), {})
        inp = self.srv.requests("input")[0]
        self.assertEqual(inp["query"]["lease_generation"], ["1"])
        self.assert_no_secret()

    def test_idle_204(self):
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "idle")
        self.assertEqual(w.runner.calls, [])

    def test_resume_map_stays_on_pc(self):
        w = self.worker()
        self.srv.create(session_ref="sess-ref-01")
        w.poll_once()
        self.assertNotIn("--resume", w.runner.calls[0])
        self.assertEqual(w.state.session_get("sess-ref-01"), "fake-session-0001")
        self.srv.create(session_ref="sess-ref-01")
        w.poll_once()
        self.assertEqual(w.runner.calls[1][-2:], ["--resume", "fake-session-0001"])
        self.srv.create(session_ref="sess-ref-02")                 # 다른 세션엔 섞이지 않음
        w.poll_once()
        self.assertNotIn("--resume", w.runner.calls[2])
        for r in self.srv.log:
            self.assertNotIn(b"fake-session-0001", r["raw"])


class Row1PcOffTest(Base):
    """1 PC 꺼짐 — 실행으로 오표시하지 않고 대기·만료가 구분된다."""

    def test_never_polled_then_expired(self):
        jid = self.srv.create()
        self.assertEqual(self.srv.status(jid), "queued")              # 대기
        self.srv.tick(EXPIRE_SEC)
        self.assertEqual(self.srv.status(jid), "expired")             # 만료(실행 아님)
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "idle")
        self.assertEqual(w.runner.calls, [])

    def test_expired_between_lease_and_input(self):
        jid = self.srv.create()
        orig = self.srv.route

        def route(method, path, q, body, dev):
            if path.endswith("/input"):
                self.srv.jobs[jid]["expires"] = self.srv.clock          # 할당 뒤 만료
            return orig(method, path, q, body, dev)
        self.srv.route = route
        w = self.worker()
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("gone", "JOB-GONE"))
        self.assertEqual(w.runner.calls, [])
        self.assertEqual(self.srv.requests("fail"), [])
        self.assertEqual(w.state.leases(), {})


class Row2MultiPcTest(Base):
    """2 다중 PC 동시 할당 — 유효한 실행권 하나."""

    def test_two_workers_one_job(self):
        jid = self.srv.create()
        a, b = self.worker(), self.worker(token=TOKEN_B)
        outs = []
        go = threading.Barrier(2)

        def run(w):
            go.wait()
            outs.append(w.poll_once()["outcome"])
        ts = [threading.Thread(target=run, args=(w,)) for w in (a, b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(sorted(outs), ["idle", "result-sent"])
        self.assertEqual(len(a.runner.calls) + len(b.runner.calls), 1)
        self.assertEqual(self.srv.jobs[jid]["generation"], 1)
        self.assertEqual(self.srv.jobs[jid]["attempts"], 1)

    def test_other_device_cannot_use_lease(self):
        jid = self.srv.create()
        a = self.worker()
        self.srv.route("GET", jobs.JOBS_PATH, {}, None, "dev-a")      # dev-a 가 임대
        b = self.worker(token=TOKEN_B)
        st, _h, p = b.client.input(jid, 1)
        self.assertEqual((st, p.get("code")), (409, "JOB-LEASE"))
        self.assertEqual(a.runner.calls + b.runner.calls, [])


class Row3DisconnectTest(Base):
    """3 실행 중 연결 끊김 — 실행 여부 불명을 보존, 무단 중복 실행 없음."""

    def test_request_lost_retry_same_bytes(self):
        jid = self.srv.create()
        self.srv.drop[("result", jid)] = {"n": 2, "apply": False}
        w = self.worker()
        out = w.poll_once()
        self.assertEqual(out["outcome"], "result-sent")
        posts = self.srv.requests("result")
        self.assertEqual(len(posts), 3)
        self.assertEqual(len({p["raw"] for p in posts}), 1)           # 같은 바이트·같은 result_version
        self.assertEqual(len(w.runner.calls), 1)
        self.assertEqual(self.srv.status(jid), "result_ready")

    def test_response_lost_duplicate_ok(self):
        jid = self.srv.create()
        self.srv.drop[("result", jid)] = {"n": 1, "apply": True}
        w = self.worker()
        out = w.poll_once()
        self.assertEqual({k: out[k] for k in ("job_id", "kind", "outcome", "duplicate")},
                         {"job_id": jid, "kind": "converse", "outcome": "result-sent", "duplicate": True})
        self.assertEqual(len(w.runner.calls), 1)

    def test_undeliverable_then_needs_check_not_rerun(self):
        jid = self.srv.create()
        self.srv.drop[("result", jid)] = {"n": 99, "apply": False}
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "result-undelivered")
        self.assertEqual(len(self.srv.requests("result")), jobs.RESULT_RETRIES + 1)
        self.assertEqual(self.srv.status(jid), "running")              # 서버: 실행 여부 불명
        self.srv.tick(LEASE_SEC)
        self.assertEqual(self.srv.status(jid), "needs_check")
        self.assertEqual(w.poll_once()["outcome"], "idle")             # 재할당·재실행 없음
        self.assertEqual(len(w.runner.calls), 1)

    def test_input_lost_no_run(self):
        jid = self.srv.create()
        self.srv.drop[("input", jid)] = {"n": 1, "apply": False}
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "input-network")
        self.assertEqual(w.runner.calls, [])
        self.assertEqual(self.srv.requests("fail"), [])


class Row20RateLimitedSendTest(Base):
    """20 보강 — /result·/fail 429: 임대가 남아 있는 동안 Retry-After 를 지켜 같은 바이트 재전송, 임대 만료면 중단."""

    def _times(self, w, name):
        times, orig = [], getattr(w.client, name)

        def wrapped(*a):
            times.append(w.clock.now())
            return orig(*a)
        setattr(w.client, name, wrapped)
        return times

    def test_result_429_retry_after_same_bytes(self):
        jid = self.srv.create()
        rl = (429, {"code": "RATE-LIMIT"}, {"Retry-After": "40"})
        self.srv.route_script[("result", jid)] = [rl, rl]
        w = self.worker()
        times = self._times(w, "result_bytes")
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        posts = self.srv.requests("result")
        self.assertEqual(len(posts), 3)
        self.assertEqual(len({p["raw"] for p in posts}), 1)
        self.assertEqual([round(b - a) for a, b in zip(times, times[1:])], [40, 40])
        self.assertEqual(len(w.runner.calls), 1)
        self.assertEqual(self.srv.status(jid), "result_ready")

    def test_result_429_stops_at_lease_expiry(self):
        jid = self.srv.create()
        rl = (429, {"code": "RATE-LIMIT"}, {"Retry-After": "200"})
        self.srv.route_script[("result", jid)] = [rl, rl, rl]
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "result-undelivered")
        self.assertEqual(len(self.srv.requests("result")), 2)          # 0초·200초 — 400초는 임대(300초) 밖
        self.assertEqual(len(w.runner.calls), 1)
        self.srv.tick(LEASE_SEC)
        self.assertEqual(self.srv.status(jid), "needs_check")

    def test_fail_429_retry_after(self):
        jid = self.srv.create()
        self.srv.route_script[("fail", jid)] = [(429, {"code": "RATE-LIMIT"}, None)]
        w = self.worker("garbage")
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["status"]), ("failed", 200))
        fails = self.srv.requests("fail")
        self.assertEqual(len(fails), 2)
        self.assertEqual(fails[0]["raw"], fails[1]["raw"])
        self.assertEqual(self.srv.status(jid), "failed")


class Row5ModelFailTest(Base):
    """5 모델 실패·잘못된 계약 — 미검증 값 전송 없음, 실행마다 시도 차감, 실패 = 코드만.
    ②-a 경로(summarize_reading, CLI 재시도 없음). converse(stack/1 — 재시도 1·종료 불명확)는 test_stack."""

    def _fail_case(self, mode, expect, **kw):
        jid = self.srv.create(kind="summarize_reading")
        w = self.worker(mode, **kw)
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["reason_code"]), ("failed", expect))
        self.assertEqual(self.srv.requests("result"), [])
        fails = self.srv.requests("fail")
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["body"], {"lease_generation": 1, "reason_code": expect})
        self.assertEqual(self.srv.status(jid), "failed")
        self.assertEqual(w.state.attempts("2026-10-22"), 1)
        self.assertEqual(self.srv.jobs[jid]["attempts"], 1)
        self.assert_no_secret()
        return w

    def test_garbage(self):
        self._fail_case("garbage", "output-not-json")

    def test_outer_shape(self):
        self._fail_case("shape", "output-shape")

    def test_inner_shape(self):
        self._fail_case("inner", "result-shape")

    def test_login(self):
        self._fail_case("login", "login")

    def test_exit(self):
        self._fail_case("exit", "cli-exit")

    def test_timeout(self):
        self._fail_case("slow", "timeout", timeout=0.5, extra_env={"FAKE_SLEEP": "5"})

    def test_input_too_large_not_run(self):
        for text in ("가" * (jobs.MAX_INPUT_CHARS + 1), "😀" * (jobs.MAX_INPUT_BYTES // 4 + 1)):
            with self.subTest(n=len(text)):
                self.srv.create(text=text)
                w = self.worker()
                out = w.poll_once()
                self.assertEqual(out["reason_code"], "input-too-large")
                self.assertEqual(w.runner.calls, [])
                self.assertEqual(w.state.attempts("2026-10-22"), 0)   # CLI 시작 전이라 PC 시도 없음

    def test_max_size_input_escaped_runs(self):
        """경계값 안(8,000 코드포인트·32KiB) 이모지 입력은 JSON 이스케이프로 ~96KB 가 돼도 잘리지 않고 실행된다."""
        self.srv.create(text="😀" * jobs.MAX_INPUT_CHARS)
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        self.assertEqual(len(w.runner.calls), 1)

    def test_oversized_input_response_not_run(self):
        self.srv.create(text="가" * (jobs.MAX_RESPONSE_BYTES // 6 + 10))   # 응답 상한 초과 → 빈 payload
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "input-error")
        self.assertEqual(w.runner.calls, [])

    def test_each_rerun_counts(self):
        for _ in range(3):
            self.srv.create(kind="summarize_reading")
        w = self.worker("garbage")
        for _ in range(3):
            w.poll_once()
        self.assertEqual(w.state.attempts("2026-10-22"), 3)
        self.assertEqual(len(w.runner.calls), 3)


class Row7LateResultTest(Base):
    """7 삭제 후 늦은 결과 — 부활 없음, 재전송·실패 전송 없음."""

    def test_deleted_while_running(self):
        jid = self.srv.create()
        w = self.worker(hook=lambda: self.srv.delete(jid))
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("gone", "JOB-GONE"))
        self.assertEqual(len(self.srv.requests("result")), 1)
        self.assertEqual(self.srv.requests("fail"), [])
        self.assertEqual(self.srv.status(jid), "deleted")
        self.assertIsNone(self.srv.jobs[jid]["result"])
        self.assertEqual(w.state.leases(), {})

    def test_cancelled_while_running(self):
        jid = self.srv.create()

        def cancel():
            with self.srv.lock:
                self.srv.jobs[jid]["status"] = "cancelled"
        w = self.worker(hook=cancel)
        self.assertEqual(w.poll_once()["outcome"], "gone")
        self.assertEqual(len(self.srv.requests("result")), 1)


class Row10LeaseReplacedTest(Base):
    """10 임대 교체 후 옛 PC 결과 — 옛 세대 거부, 재전송·재실행 없음."""

    def test_old_generation_rejected(self):
        jid = self.srv.create()
        w = self.worker(hook=lambda: self.srv.bump_generation(jid))
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("lease-lost", "JOB-LEASE"))
        self.assertEqual(len(self.srv.requests("result")), 1)
        self.assertEqual(self.srv.requests("result")[0]["body"]["lease_generation"], 1)
        self.assertEqual(self.srv.requests("fail"), [])
        self.assertIsNone(self.srv.jobs[jid]["result"])
        self.assertEqual(len(w.runner.calls), 1)

    def test_old_generation_fail_rejected(self):
        jid = self.srv.create()
        w = self.worker("garbage", hook=lambda: self.srv.bump_generation(jid))
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["status"], out["code"]), ("failed", 409, "JOB-LEASE"))
        self.assertEqual(len(self.srv.requests("fail")), 1)
        self.assertNotEqual(self.srv.status(jid), "failed")

    def test_version_mismatch(self):
        jid = self.srv.create()

        def bump_version():
            with self.srv.lock:
                self.srv.jobs[jid]["input_version"] = 2
        w = self.worker(hook=bump_version)
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("version-mismatch", "JOB-VERSION"))
        self.assertEqual(len(self.srv.requests("result")), 1)


class Row20PollingTest(Base):
    """20 폴링 폭주·동시 연결 초과 — 간격·429·백오프·동시 1, 업무 한도와 분리."""

    def test_instant_204_paced_to_4_per_minute(self):
        w = self.worker()
        times = []
        orig = w.client.poll

        def poll():
            times.append(w.clock.now())
            return orig()
        w.client.poll = poll
        res = w.run_forever(max_iterations=12)
        self.assertEqual(res["outcomes"], {"idle": 12})
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(all(g >= jobs.MIN_POLL_INTERVAL for g in gaps), gaps)
        for i, t in enumerate(times):
            self.assertLessEqual(sum(1 for u in times[i:] if u < t + 60), 4)
        self.assertEqual(w.state.attempts("2026-10-22"), 0)            # 폴링은 시도 차감 없음

    def test_429_retry_after(self):
        self.srv.poll_script = [(429, {"code": "RATE-LIMIT"}, {"Retry-After": "120"})]
        w = self.worker()
        times = []
        orig = w.client.poll

        def poll():
            times.append(w.clock.now())
            return orig()
        w.client.poll = poll
        self.assertEqual(w.poll_once()["outcome"], "rate-limit")
        self.assertEqual(w.poll_once()["outcome"], "idle")
        self.assertGreaterEqual(times[1] - times[0], 120)

    def test_429_without_header_and_cap(self):
        self.srv.poll_script = [(429, {"code": "RATE-LIMIT"}, None), (429, {"code": "RATE-LIMIT"}, {"Retry-After": "99999"})]
        w = self.worker()
        self.assertEqual(w.poll_once()["retry_in"], jobs.RETRY_AFTER_DEFAULT)
        self.assertEqual(w.poll_once()["retry_in"], jobs.RETRY_AFTER_MAX)

    def test_5xx_switch_off_backoff_grows_and_resets(self):
        off = (503, {"code": "SWITCH-OFF"}, None)
        self.srv.poll_script = [off, off, off]
        w = self.worker()
        waits = [w.poll_once()["retry_in"] for _ in range(3)]
        self.assertEqual(waits, [30, 60, 120])
        self.assertEqual(w.poll_once()["outcome"], "idle")
        self.assertEqual(w._poll_failures, 0)

    def test_network_backoff(self):
        w = self.worker()
        self.srv.close()
        self.srv = StubJobsServer()        # tearDown 용 — 원래 주소는 닫힘
        out = w.poll_once()
        self.assertEqual(out["outcome"], "network")
        self.assertEqual(out["retry_in"], jobs.POLL_BACKOFF_BASE)

    def test_auth_halts_loop(self):
        w = self.worker(token="imp_is_000000000000000000000000.tok_Bad00001." + "Z" * 43)
        res = w.run_forever(max_iterations=5)
        self.assertEqual((res["stopped"], res["iterations"]), ("auth-halted", 1))
        self.assertEqual(w.poll_once()["outcome"], "halted")

    def test_concurrency_one(self):
        jid = self.srv.create()
        started, release = threading.Event(), threading.Event()

        def hold():
            started.set()
            release.wait(10)
        w = self.worker(hook=hold)
        t = threading.Thread(target=w.poll_once)
        t.start()
        self.assertTrue(started.wait(10))
        self.assertEqual(w.poll_once()["outcome"], "busy")          # 같은 워커 두 번째 호출 거절
        release.set()
        t.join(30)
        self.assertEqual(self.srv.status(jid), "result_ready")
        self.assertEqual(self.srv.max_inflight.get("dev-a"), 1)

    def test_home_lock_one_worker(self):
        lock_path = self.th.paths.lock("jobs")
        from agent import osutil
        held = osutil.FileLock(lock_path).acquire()
        try:
            res = self.worker().run_forever(max_iterations=1, lock_path=lock_path)
            self.assertEqual(res["stopped"], "already-running")
        finally:
            held.release()
        self.assertNotEqual(lock_path, self.th.paths.lock("run"))


class AdapterTest(unittest.TestCase):
    def test_argv_isolation(self):
        argv = jobs.build_argv(["/abs/claude"], "converse", "0f1e2d3c-aaaa-bbbb-cccc-111122223333")
        for flag in ("--strict-mcp-config", "--permission-mode", "--output-format", "--max-turns",
                     "--setting-sources", "--mcp-config"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertEqual(argv[-2:], ["--resume", "0f1e2d3c-aaaa-bbbb-cccc-111122223333"])

    def test_resume_injection_rejected(self):
        with self.assertRaises(jobs.JobsError):
            jobs.build_argv(["/abs/claude"], "converse", "--dangerously-skip-permissions")

    def test_real_cli_blocked_without_env(self):
        old = os.environ.pop(jobs.REAL_CLI_ENV, None)
        try:
            with self.assertRaises(jobs.JobsError):
                jobs.ClaudeAdapter(["/abs/claude"])
        finally:
            if old is not None:
                os.environ[jobs.REAL_CLI_ENV] = old

    def test_fail_reason_whitelist(self):
        with self.assertRaises(ValueError):
            jobs.JobFail("raw stderr text")


class StateTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()

    def tearDown(self):
        self.th.cleanup()

    def test_json_state_private_no_body(self):
        st = jobs.JsonState.for_paths(self.th.paths)
        st.lease_set("0f1e2d3c-aaaa-bbbb-cccc-111122223333", 3)
        st.session_set("sess-ref-01", "fake-session-0001")
        st.attempt_bump("2026-10-22")
        path = os.path.join(self.th.paths.home, jobs.STATE_FILE)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        again = jobs.JsonState(path)
        self.assertEqual(again.session_get("sess-ref-01"), "fake-session-0001")
        self.assertEqual(again.attempts("2026-10-22"), 1)
        self.assertEqual(again.leases(), {"0f1e2d3c-aaaa-bbbb-cccc-111122223333": {"generation": 3}})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)), {"v", "sessions", "attempts", "cli_retries", "leases"})

    def test_v1_state_upgraded(self):
        path = os.path.join(self.th.paths.home, jobs.STATE_FILE)
        with open(path, "w") as f:
            json.dump({"v": 1, "sessions": {"sess-ref-01": "fake-session-0001"}, "attempts": {"2026-10-21": 2},
                       "leases": {}}, f)
        st = jobs.JsonState(path)
        self.assertEqual(st.session_get("sess-ref-01"), "fake-session-0001")
        self.assertEqual(st.cli_retries("2026-10-22"), 0)
        st.cli_retry_bump("2026-10-22")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual((data["v"], data["cli_retries"]), (jobs.STATE_VERSION, {"2026-10-22": 1}))
        with open(path, "w") as f:
            json.dump({"v": 3, "sessions": {}, "attempts": {}, "leases": {}}, f)
        with self.assertRaises(jobs.JobsError):                   # 모르는 판 = 멈춤
            jobs.JsonState(path)

    def test_worker_state_file_has_no_input(self):
        srv = StubJobsServer()
        try:
            srv.create(session_ref="sess-ref-01")
            st = jobs.JsonState.for_paths(self.th.paths)
            clock = VClock()
            w = jobs.Worker(jobs.Client(srv.url, TOKEN, wait=1), jobs.fake_adapter("ok", exec_runner=CountingRunner()),
                            st, monotonic=clock.now, sleep=clock.sleep)
            self.assertEqual(w.poll_once()["outcome"], "result-sent")
            with open(st.path, "rb") as f:
                data = f.read()
            self.assertNotIn(SECRET_TEXT.encode("utf-8"), data)
            self.assertNotIn("정리".encode("utf-8"), data)               # 결과 본문도 없음
        finally:
            srv.close()

    def test_corrupt_state_stops(self):
        path = os.path.join(self.th.paths.home, jobs.STATE_FILE)
        with open(path, "w") as f:
            f.write("{not json")
        with self.assertRaises(jobs.JobsError):
            jobs.JsonState(path)

    def test_attempt_days_bounded(self):
        st = jobs.MemoryState()
        for d in range(1, 12):
            st.attempt_bump("2026-10-%02d" % d)
        self.assertEqual(len(st.data["attempts"]), jobs.ATTEMPT_DAYS_KEPT)


# ── J3 실 API 재시험(opt-in) ───────────────────────────────
# 환경변수가 없으면 건너뛴다(CI 포함). 시험 DB·로컬 서버만:
#   JOBS_LIVE_URL=http://127.0.0.1:8807  JOBS_LIVE_TOKEN=<장치 A>  JOBS_LIVE_TOKEN_B=<같은 사용자 장치 B>
#   JOBS_LIVE_DB=postgresql://localhost/crm_test_jobs  (JOBS_LIVE_ORIGIN 기본 http://127.0.0.1:8800)
# DB 는 psql 로 ① 상태 읽기(status·attempts·reason_code — 본문 없음) ② **서버 시각 경과 흉내**(expires_at·lease_expires_at
# 을 지금 이전으로)만 쓴다. 판정·저장은 전부 API 를 거친다. 토큰은 파일에 적지 않는다.
LIVE_URL = os.environ.get("JOBS_LIVE_URL", "")
LIVE_TOKEN = os.environ.get("JOBS_LIVE_TOKEN", "")
LIVE_TOKEN_B = os.environ.get("JOBS_LIVE_TOKEN_B", "")
LIVE_DB = os.environ.get("JOBS_LIVE_DB", "")
LIVE_ORIGIN = os.environ.get("JOBS_LIVE_ORIGIN", "http://127.0.0.1:8800")
LIVE_GAP = 15.5                      # 장치 폴링 버킷(분당 4) 안 — 같은 토큰의 폴 시작 간격
_live_last = {}
_live_lock = threading.Lock()


def _live_ok() -> bool:
    return bool(LIVE_URL and LIVE_TOKEN and LIVE_DB) and LIVE_URL.startswith(("http://127.0.0.1:", "http://localhost:")) \
        and "/crm_test_" in LIVE_DB


@unittest.skipUnless(_live_ok(), "J3 실 API 재시험 — JOBS_LIVE_* 없음(로컬·crm_test_* 전용)")
class LiveApiTest(unittest.TestCase):
    """같은 실패표 행을 실 서버(J2)로. 모델 = fake. 연결 끊김은 PC 쪽에서 주입(서버는 그대로)."""

    @classmethod
    def setUpClass(cls):
        import re as _re
        m = _re.match(r"^imp_(is_[0-9a-f]{24})\.", LIVE_TOKEN)
        cls.stream = m.group(1) if m else ""
        cls.uid = cls.sql("SELECT user_id FROM import_streams WHERE id='%s'" % cls.stream)

    @staticmethod
    def sql(q: str) -> str:
        import subprocess
        r = subprocess.run(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", LIVE_DB, "-c", q],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise AssertionError("psql failed: exit %d" % r.returncode)
        return r.stdout.strip()

    def setUp(self):
        # 앞 시험이 남긴 살아 있는 작업을 API 로 취소(다음 시험에 끼어들지 않게)
        ids = self.sql("SELECT id FROM jobs WHERE user_id='%s' AND status IN ('queued','leased','running')" % self.uid)
        for jid in filter(None, ids.splitlines()):
            self.web("POST", "/api/jobs/%s/cancel" % jobs._job_id(jid), {})

    def web(self, method, path, body=None):
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(LIVE_URL + path, data=data, method=method)
        req.add_header("Origin", LIVE_ORIGIN)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else {})

    def create(self, text=SECRET_TEXT, session_id=None) -> str:
        body = {"kind": "converse", "text": text}
        if session_id:
            body["session_id"] = session_id
        st, p = self.web("POST", "/api/jobs", body)
        self.assertEqual(st, 201, p)
        return p["id"]

    def row(self, jid):
        st, att, rc = self.sql("SELECT status, attempts, COALESCE(reason_code,'') FROM jobs WHERE id='%s'"
                               % jobs._job_id(jid)).split("|")
        return st, int(att), rc

    def past(self, jid, col):
        assert col in ("expires_at", "lease_expires_at")
        self.sql("UPDATE jobs SET %s = now() - interval '1 second' WHERE id='%s'" % (col, jobs._job_id(jid)))

    def worker(self, mode="ok", token=LIVE_TOKEN, hook=None, wait=2, **kw):
        runner = CountingRunner(hook)
        client = jobs.Client(LIVE_URL, token, wait=wait)
        sent = []
        orig_send, orig_poll = client._send, client.poll

        def send(method, path, data, extra):
            sent.append((method, path, data))
            return orig_send(method, path, data, extra)

        def poll():
            with _live_lock:
                gap = _live_last.get(token, -1e9) + LIVE_GAP - time.monotonic()
            if gap > 0:
                time.sleep(gap)
            with _live_lock:
                _live_last[token] = time.monotonic()
            return orig_poll()
        client._send, client.poll = send, poll
        w = jobs.Worker(client, jobs.fake_adapter(mode, exec_runner=runner), jobs.MemoryState(), min_interval=0, **kw)
        w.runner, w.sent = runner, sent
        return w

    def assert_no_secret(self, w):
        for _m, _p, data in w.sent:
            self.assertNotIn(SECRET_TEXT.encode("utf-8"), data or b"")
            self.assertNotIn("SYNTH-INPUT-7731", _p)

    def test_a_happy(self):
        jid = self.create()
        w = self.worker()
        out = w.poll_once()
        self.assertEqual(out["outcome"], "result-sent", out)
        self.assertEqual(self.row(jid)[:2], ("result_ready", 1))
        st, p = self.web("GET", "/api/jobs/%s?wait=0" % jid)
        self.assertEqual(st, 200)
        self.assertEqual(p.get("status"), "result_ready")
        self.assertNotIn("fake-session", json.dumps(p))           # CLI 세션 ID 는 서버에 없음
        self.assert_no_secret(w)

    def test_r1_queued_then_expired_not_run(self):
        jid = self.create()
        self.assertEqual(self.row(jid)[0], "queued")              # PC 꺼짐 = 대기(실행 아님)
        self.past(jid, "expires_at")
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "idle")
        self.assertEqual(self.row(jid)[0], "expired")
        self.assertEqual(w.runner.calls, [])

    def test_r1_cancelled_between_lease_and_input(self):
        jid = self.create()
        w = self.worker()
        orig = w.client.input

        def input_after_cancel(j, g):
            self.web("POST", "/api/jobs/%s/cancel" % j, {})
            return orig(j, g)
        w.client.input = input_after_cancel
        out = w.poll_once()
        self.assertEqual((out["outcome"], out.get("code")), ("gone", "JOB-GONE"))
        self.assertEqual(w.runner.calls, [])
        self.assertEqual(self.row(jid)[0], "cancelled")

    @unittest.skipUnless(LIVE_TOKEN_B, "장치 B 토큰 없음")
    def test_r2_two_devices_one_lease(self):
        jid = self.create()
        a, b = self.worker(), self.worker(token=LIVE_TOKEN_B)
        outs, go = [], threading.Barrier(2)

        def run(w):
            go.wait()
            outs.append(w.poll_once()["outcome"])
        ts = [threading.Thread(target=run, args=(w,)) for w in (a, b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(90)
        self.assertEqual(sorted(outs), ["idle", "result-sent"])
        self.assertEqual(len(a.runner.calls) + len(b.runner.calls), 1)
        self.assertEqual(self.row(jid)[:2], ("result_ready", 1))

    def _drop_result(self, w, n, after_send):
        orig, left = w.client.result_bytes, {"n": n}

        def result_bytes(j, data):
            if left["n"] > 0:
                left["n"] -= 1
                if after_send:
                    orig(j, data)                                  # 서버는 받았고 응답만 유실
                raise jobs.NetworkError()
            return orig(j, data)
        w.client.result_bytes = result_bytes

    def test_r3_request_lost_same_bytes(self):
        jid = self.create()
        w = self.worker()
        self._drop_result(w, 1, after_send=False)
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        self.assertEqual(len(w.runner.calls), 1)
        self.assertEqual(self.row(jid)[0], "result_ready")

    def test_r3_response_lost_duplicate(self):
        jid = self.create()
        w = self.worker()
        self._drop_result(w, 1, after_send=True)
        out = w.poll_once()
        self.assertEqual((out["outcome"], out.get("duplicate")), ("result-sent", True))
        posts = [d for m, p, d in w.sent if p.endswith("/result")]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0], posts[1])
        self.assertEqual(len(w.runner.calls), 1)
        self.assertEqual(self.row(jid)[0], "result_ready")

    def test_r3_undeliverable_needs_check(self):
        jid = self.create()
        w = self.worker(result_retries=1)
        self._drop_result(w, 99, after_send=False)
        self.assertEqual(w.poll_once()["outcome"], "result-undelivered")
        self.assertEqual(self.row(jid)[0], "running")             # 실행 여부 불명 보존
        self.past(jid, "lease_expires_at")
        w2 = self.worker()
        self.assertEqual(w2.poll_once()["outcome"], "idle")       # 재할당 없음
        self.assertEqual(self.row(jid)[0], "needs_check")
        self.assertEqual(len(w.runner.calls) + len(w2.runner.calls), 1)

    def test_r5_garbage_fail_code_only(self):
        jid = self.create()
        w = self.worker("garbage")
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["reason_code"], out["status"]), ("failed", "output-not-json", 200))
        self.assertEqual(self.row(jid), ("failed", 1, "output-not-json"))
        fails = [json.loads(d) for m, p, d in w.sent if p.endswith("/fail")]
        self.assertEqual(fails, [{"lease_generation": fails[0]["lease_generation"], "reason_code": "output-not-json"}])
        self.assertEqual([d for m, p, d in w.sent if p.endswith("/result")], [])
        self.assert_no_secret(w)

    def test_r5_login(self):
        jid = self.create()
        out = self.worker("login").poll_once()
        self.assertEqual(out["reason_code"], "login")
        self.assertEqual(self.row(jid), ("failed", 1, "login"))

    def test_r7_cancel_while_running(self):
        holder = {}
        w = self.worker(hook=lambda: self.web("POST", "/api/jobs/%s/cancel" % holder["jid"], {}))
        holder["jid"] = jid = self.create()
        out = w.poll_once()
        self.assertEqual((out["outcome"], out.get("code")), ("gone", "JOB-GONE"))
        self.assertEqual(len([1 for m, p, d in w.sent if p.endswith("/result")]), 1)
        self.assertEqual([1 for m, p, d in w.sent if p.endswith("/fail")], [])
        self.assertEqual(self.row(jid)[0], "cancelled")

    def test_r10_lease_expired_during_run(self):
        holder = {}
        w = self.worker(hook=lambda: self.past(holder["jid"], "lease_expires_at"))
        holder["jid"] = jid = self.create()
        out = w.poll_once()
        self.assertEqual((out["outcome"], out.get("code")), ("lease-lost", "JOB-LEASE"))
        self.assertEqual(len([1 for m, p, d in w.sent if p.endswith("/result")]), 1)
        self.assertIsNone(self.sql("SELECT 1 FROM tmp.job_results WHERE job_id='%s'" % jid) or None)   # 늦은 결과 미저장
        st, p = self.web("GET", "/api/jobs/%s?wait=0" % jid)     # 상태 조회가 정리(sweep)를 부른다 — 그 뒤 needs_check
        self.assertEqual((st, p.get("status")), (200, "needs_check"))
        self.assertEqual(len(w.runner.calls), 1)

    def test_z_r20_rate_limit_and_concurrency(self):
        # 정상: 자체 간격(15초)으로 3회 → 429 없음
        w = self.worker()
        w.min_interval = jobs.MIN_POLL_INTERVAL
        t0 = time.monotonic()
        res = w.run_forever(max_iterations=3)
        self.assertEqual(res["outcomes"], {"idle": 3})
        self.assertGreaterEqual(time.monotonic() - t0, 2 * jobs.MIN_POLL_INTERVAL - 1)
        # 같은 장치 동시 롱폴 2 → 하나는 429(동시 1)
        c1, c2 = jobs.Client(LIVE_URL, LIVE_TOKEN, wait=5), jobs.Client(LIVE_URL, LIVE_TOKEN, wait=5)
        sts, go = [], threading.Barrier(2)

        def p(c):
            go.wait()
            sts.append(c.poll()[0])
        ts = [threading.Thread(target=p, args=(c,)) for c in (c1, c2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertIn(429, sts)
        # 폭주: 간격 없이 → 버킷(분당 4) 넘으면 429 + Retry-After 를 워커가 지킴
        w2 = jobs.Worker(jobs.Client(LIVE_URL, LIVE_TOKEN, wait=0), jobs.fake_adapter("ok"), jobs.MemoryState(),
                         min_interval=0, sleep=lambda s: None)
        outs = [w2.poll_once() for _ in range(6)]
        rl = [o for o in outs if o["outcome"] == "rate-limit"]
        self.assertTrue(rl, outs)
        self.assertGreater(rl[0]["retry_in"], 0)


if __name__ == "__main__":
    unittest.main()
