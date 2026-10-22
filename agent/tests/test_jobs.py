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
                              "result": None, "reason_code": None, "seq": len(self.jobs)}
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
                              "definitions_version": 1,
                              "lease": {"generation": j["generation"], "expires_at": j["lease_expires"]}})
            if len(parts) != 5:
                return (404, {"code": "NOT-FOUND"})
            jid, rt = parts[3], parts[4]
            j = self.jobs.get(jid)
            if j is None or j["status"] in ("deleted", "expired", "cancelled"):
                return (410, {"code": "JOB-GONE"})
            key = (rt, jid)
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
                return (200, {"text": j["text"], "input_version": j["input_version"]})
            if rt == "result" and method == "POST":
                if not isinstance(body, dict):
                    return (400, {"code": "BAD-REQUEST"})
                if (j["status"] == "result_ready" and j["result"] is not None
                        and j["result"]["result_version"] == body.get("result_version")):
                    r = (200, {"duplicate": True})
                elif j["device"] != dev or body.get("lease_generation") != j["generation"] or j["status"] != "running":
                    return (409, {"code": "JOB-LEASE"})
                elif body.get("input_version") != j["input_version"]:
                    return (409, {"code": "JOB-VERSION"})
                else:
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
        jid = self.srv.create()
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
        self.assertEqual(out, {"job_id": jid, "kind": "converse", "outcome": "result-sent", "duplicate": True})
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


class Row5ModelFailTest(Base):
    """5 모델 실패·잘못된 계약 — 미검증 값 전송 없음, 실행마다 시도 차감, 실패 = 코드만."""

    def _fail_case(self, mode, expect, **kw):
        jid = self.srv.create()
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
            self.srv.create()
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
            self.assertEqual(set(json.load(f)), {"v", "sessions", "attempts", "leases"})

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


if __name__ == "__main__":
    unittest.main()
