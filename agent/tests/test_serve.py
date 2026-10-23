"""상주 실행(③-0.1, jobs.serve_jobs) — 시작·인증·단일 잠금·통신 실패 대기·종료 신호·유예·자식 그룹 종료·불확실 실행 보존
·응답 유실 시 CLI 재호출 0·세션 직렬화·resume 유지·로그 본문 0.

전부 합성 자료. 실 claude CLI·운영 서버 없음. 모델 실행 = tests/fake_claude.py(FAKE_JOB=1) — GroupRunner 로 띄운다.
신호는 이벤트(stopping·abort)를 직접 켠다. 처리기 설치는 메인 스레드에서 SIGTERM 1건으로 확인.
유예·대기 시간은 짧게 주입한다(실제로 30초 기다리지 않는다).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from unittest import mock

from agent import cli, config as _config, jobs, osutil
from agent.tests.helpers import NAMESPACE, STREAM_ID, TOKEN, MemoryKeyStore, TempHome, run_cli
from agent.tests.test_jobs import SECRET_TEXT, StubJobsServer

POSIX = not osutil.IS_WINDOWS


class CountingGroup(jobs.GroupRunner):
    """GroupRunner + 실행 횟수·argv 기록."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        return super().__call__(argv, **kw)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                   # 좀비(부모가 아직 거두지 않음)는 죽은 것으로 본다
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    except OSError:
        return True
    return bool(out) and not out.startswith("Z")


def _wait_for(cond, timeout=10.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.srv = StubJobsServer()
        self.th = TempHome()
        self.tmp = tempfile.mkdtemp(prefix="mcrm-serve-")
        self.lock_path = self.th.paths.lock("jobs")
        self.state_path = os.path.join(self.th.home, jobs.STATE_FILE)
        self.logs = []
        self.workers = []

    def tearDown(self):
        for w in self.workers:                     # 데몬 워커가 남아 있으면 멈춘다
            w.stopping.set()
            w.abort.set()
        self.srv.close()
        self.th.cleanup()

    def serve(self, *, mode="ok", env=None, auth=None, grace=5.0, stopping=None, abort=None, runner=None,
              kill_wait=1.0, **kw):
        """serve_jobs 를 스레드에서 돌린다. 반환 (스레드, 결과 상자, runner, 이벤트 둘)."""
        stopping = stopping or threading.Event()
        abort = abort or threading.Event()
        runner = runner or CountingGroup(kill_wait=kill_wait)
        extra = {}
        extra.update(env or {})

        def make_worker(st, ab):
            adapter = jobs.fake_adapter(mode, exec_runner=runner, extra_env=extra, timeout=60)
            w = jobs.Worker(jobs.Client(self.srv.url, TOKEN, wait=1), adapter, jobs.JsonState(self.state_path),
                            sleep=lambda s: ab.wait(min(s, 0.05)), min_interval=0.05, today=lambda: "2026-10-22",
                            stopping=st, abort=ab)
            self.workers.append(w)
            return w

        box = {}

        def run():
            box["res"] = jobs.serve_jobs(lock_path=self.lock_path, authenticate=auth or (lambda: "ok"),
                                         make_worker=make_worker, runner=runner, log=self.logs.append, grace=grace,
                                         stopping=stopping, abort=abort, install_signals=False, kill_join=10,
                                         **dict(dict(backoff_base=0.01, backoff_max=0.05), **kw))

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t, box, runner, stopping, abort

    def finish(self, t, box, timeout=20):
        t.join(timeout)
        self.assertFalse(t.is_alive(), "serve 가 끝나지 않음")
        return box["res"]

    def polls(self):
        return len(self.srv.requests("poll"))

    def log_text(self):
        return "\n".join(self.logs)


class StartTest(Base):
    def test_auth_fail_exits_without_cli(self):
        t, box, runner, *_ = self.serve(auth=lambda: "auth")
        res = self.finish(t, box)
        self.assertEqual(res["stopped"], "auth")
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.polls(), 0)            # 폴도 안 함
        self.assertEqual(self.workers, [])

    def test_network_waits_and_retries(self):
        seq = ["network", "network", "ok"]
        calls = []

        def auth():
            calls.append(1)
            return seq.pop(0)

        t, box, runner, stopping, _ = self.serve(auth=auth)
        self.assertTrue(_wait_for(lambda: self.polls() >= 1))
        stopping.set()
        res = self.finish(t, box)
        self.assertEqual(len(calls), 3)
        self.assertEqual(res["stopped"], "signal")
        self.assertEqual(self.log_text().count('"outcome": "network"'), 2)

    def test_network_wait_ends_on_signal(self):
        t, box, runner, stopping, _ = self.serve(auth=lambda: "network", backoff_max=30)
        time.sleep(0.2)
        stopping.set()
        res = self.finish(t, box, timeout=5)
        self.assertEqual(res["stopped"], "signal")
        self.assertEqual(runner.calls, [])

    def test_single_lock_and_restart_after_hard_kill(self):
        """두 번째 serve 거부(OS 잠금) · 잠금 쥔 프로세스가 SIGKILL 로 죽으면 다음 serve 가 바로 시작."""
        code = ("import sys,time; sys.path.insert(0, sys.argv[1]); from agent import osutil; "
                "L = osutil.FileLock(sys.argv[2]).acquire(); print('locked', flush=True); time.sleep(600)")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = subprocess.Popen([sys.executable, "-c", code, root, self.lock_path], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "locked")
            p.stdout.close()
            auth_calls = []
            t, box, runner, *_ = self.serve(auth=lambda: auth_calls.append(1) or "ok")
            res = self.finish(t, box)
            self.assertEqual(res["stopped"], "already-running")
            self.assertEqual(auth_calls, [])            # 잠금 먼저 — 인증·폴·CLI 0
            self.assertEqual(self.polls(), 0)
            self.assertTrue(os.path.exists(self.lock_path))
        finally:
            p.kill()
            p.wait()
        # 잠금 파일은 남아 있지만(PID 파일 아님) OS 잠금은 풀렸다
        self.assertTrue(os.path.exists(self.lock_path))
        t, box, runner, stopping, _ = self.serve()
        self.assertTrue(_wait_for(lambda: self.polls() >= 1))
        stopping.set()
        self.assertEqual(self.finish(t, box)["stopped"], "signal")

    def test_second_serve_in_process_rejected(self):
        t, box, runner, stopping, _ = self.serve()
        self.assertTrue(_wait_for(lambda: self.polls() >= 1))
        t2, box2, *_ = self.serve()
        self.assertEqual(self.finish(t2, box2)["stopped"], "already-running")
        stopping.set()
        self.finish(t, box)


class ShutdownTest(Base):
    def test_signal_while_idle_stops_new_polls(self):
        t, box, runner, stopping, _ = self.serve()
        self.assertTrue(_wait_for(lambda: self.polls() >= 2))
        stopping.set()
        res = self.finish(t, box, timeout=3)               # 작업이 없으면 유예를 기다리지 않는다
        self.assertEqual(res["stopped"], "signal")
        self.assertFalse(res["killed"])
        n = self.polls()
        jid = self.srv.create(kind="summarize_reading")
        time.sleep(0.3)
        self.assertLessEqual(self.polls(), n + 1)           # 진행 중이던 폴 하나까지만
        self.assertEqual(self.srv.status(jid), "queued")    # 새 작업 수신 0
        self.assertEqual(runner.calls, [])

    def test_signal_waits_running_job_to_finish(self):
        """첫 신호 = 새 수신 중단 + 실행 중 작업은 유예 안에서 완료·결과 전송."""
        jid = self.srv.create(kind="summarize_reading")
        t, box, runner, stopping, _ = self.serve(mode="slow", env={"FAKE_SLEEP": "0.8"}, grace=10)
        self.assertTrue(_wait_for(lambda: self.srv.status(jid) == "running"))
        stopping.set()
        jid2 = self.srv.create(kind="summarize_reading")
        res = self.finish(t, box)
        self.assertEqual(res["stopped"], "signal")
        self.assertFalse(res["killed"])
        self.assertEqual(self.srv.status(jid), "result_ready")
        self.assertEqual(self.srv.status(jid2), "queued")   # 신호 뒤 새 작업 시작 0
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(self.srv.requests("fail"), [])

    @unittest.skipUnless(POSIX, "프로세스 그룹 = POSIX")
    def test_grace_exceeded_kills_group_and_keeps_unclear(self):
        """유예 초과 = 자식·손자 그룹 종료 확인 · /fail 0(정상 실패 아님) · 같은 세션 임대 행 보존 → 서버 needs_check · 재실행 0."""
        pid_file = os.path.join(self.tmp, "pids")
        jid = self.srv.create(kind="converse", session_ref="sess-a")
        t, box, runner, stopping, _ = self.serve(mode="hang", env={"FAKE_PID_FILE": pid_file}, grace=0.5)
        self.assertTrue(_wait_for(lambda: os.path.exists(pid_file)))
        child, grand = map(int, open(pid_file).read().split())
        stopping.set()
        res = self.finish(t, box)
        self.assertEqual(res["stopped"], "signal")
        self.assertTrue(res["killed"])
        self.assertTrue(runner.confirmed)
        self.assertTrue(_wait_for(lambda: not _alive(child) and not _alive(grand), timeout=5))
        self.assertEqual(self.srv.requests("fail"), [])     # 불확실 = 실패로 보내지 않는다
        self.assertEqual(self.srv.requests("result"), [])
        self.assertEqual(self.srv.status(jid), "running")
        st = jobs.JsonState(self.state_path)
        self.assertEqual(st.leases()[jid]["session_ref"], "sess-a")   # 세션 잠금 보존(서버 임대 만료까지)
        self.assertIn('"outcome": "interrupted"', self.log_text())
        self.srv.tick(jobs.LEASE_SEC)
        self.assertEqual(self.srv.status(jid), "needs_check")
        # 재시작 — 같은 작업을 다시 받지도, CLI 를 다시 부르지도 않는다
        self.logs.clear()
        t, box, runner2, stopping2, _ = self.serve()
        self.assertTrue(_wait_for(lambda: self.polls() >= 3))
        stopping2.set()
        self.finish(t, box)
        self.assertEqual(runner2.calls, [])
        self.assertEqual(self.srv.jobs[jid]["attempts"], 1)

    @unittest.skipUnless(POSIX, "프로세스 그룹 = POSIX")
    def test_second_signal_kills_now(self):
        pid_file = os.path.join(self.tmp, "pids")
        jid = self.srv.create(kind="summarize_reading")
        t, box, runner, stopping, abort = self.serve(mode="hang", env={"FAKE_PID_FILE": pid_file}, grace=60)
        self.assertTrue(_wait_for(lambda: os.path.exists(pid_file)))
        stopping.set()
        time.sleep(0.2)
        self.assertTrue(t.is_alive())                       # 유예 중
        t0 = time.monotonic()
        abort.set()                                         # 두 번째 신호
        res = self.finish(t, box)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertTrue(res["killed"])
        self.assertEqual(self.srv.requests("fail"), [])
        self.assertEqual(self.srv.status(jid), "running")
        self.assertEqual(jobs.JsonState(self.state_path).leases(), {})   # converse 아님 — 세션 잠금 행 없음

    @unittest.skipUnless(POSIX, "프로세스 그룹 = POSIX")
    def test_term_ignored_escalates_to_sigkill(self):
        pid_file = os.path.join(self.tmp, "pids")
        self.srv.create(kind="summarize_reading")
        t, box, runner, stopping, _ = self.serve(mode="hang", env={"FAKE_PID_FILE": pid_file, "FAKE_IGNORE_TERM": "1"},
                                                 grace=0.3, kill_wait=0.3)
        self.assertTrue(_wait_for(lambda: os.path.exists(pid_file)))
        child, grand = map(int, open(pid_file).read().split())
        stopping.set()
        res = self.finish(t, box)
        self.assertTrue(res["killed"])
        self.assertTrue(runner.confirmed)
        self.assertTrue(_wait_for(lambda: not _alive(child) and not _alive(grand), timeout=5))

    def test_killed_runner_refuses_new_cli(self):
        r = jobs.GroupRunner()
        self.assertFalse(r.kill())                          # 끊을 자식 없음
        with self.assertRaises(jobs.CliInterrupted):
            r([sys.executable, "-c", "pass"], stdin_bytes=b"", env=dict(os.environ), cwd=self.tmp, timeout=5)

    def test_job_arriving_after_signal_not_started(self):
        """진행 중이던 폴이 신호 뒤 작업을 돌려줘도 입력을 부르지 않는다(시작 0 — 서버 임대 만료 needs_check)."""
        jid = self.srv.create(kind="summarize_reading")
        w = jobs.Worker(jobs.Client(self.srv.url, TOKEN, wait=1), jobs.fake_adapter(exec_runner=CountingGroup()),
                        jobs.MemoryState(), min_interval=0)
        orig = w.client.poll

        def poll():
            r = orig()
            w.stopping.set()                                # 응답 도착 직전 신호
            return r

        w.client.poll = poll
        out = w.poll_once()
        self.assertEqual(out["outcome"], "stopped-before-input")
        self.assertEqual(self.srv.requests("input"), [])
        self.assertEqual(self.srv.status(jid), "leased")
        self.assertEqual(w.poll_once()["outcome"], "stopped")


class DeliveryTest(Base):
    def test_response_lost_resumes_same_result_no_cli_rerun(self):
        """서버는 저장했는데 응답이 유실 → 같은 작업·같은 result_version 으로 다시 보냄(duplicate) · CLI 재호출 0."""
        jid = self.srv.create(kind="converse", session_ref="sess-d")
        self.srv.drop[("result", jid)] = {"n": 1, "apply": True}
        t, box, runner, stopping, _ = self.serve()
        self.assertTrue(_wait_for(lambda: len(self.srv.requests("result")) >= 2))
        stopping.set()
        self.finish(t, box)
        sent = self.srv.requests("result")
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["raw"], sent[1]["raw"])    # 같은 바이트(같은 결과 ID)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(self.srv.status(jid), "result_ready")
        self.assertIn('"duplicate": true', self.log_text())

    def test_undelivered_at_abort_no_rerun_no_fail(self):
        """유예 끝까지 결과를 못 보내면 그만둔다 — /fail 0, 재시작 뒤 CLI 재호출 0(서버 임대 만료 needs_check)."""
        jid = self.srv.create(kind="summarize_reading")
        self.srv.drop[("result", jid)] = {"n": 1000, "apply": False}
        t, box, runner, stopping, abort = self.serve(grace=0.5)
        self.assertTrue(_wait_for(lambda: len(self.srv.requests("result")) >= 1))
        stopping.set()
        res = self.finish(t, box)
        self.assertEqual(res["stopped"], "signal")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(self.srv.requests("fail"), [])
        self.srv.tick(jobs.LEASE_SEC)
        self.assertEqual(self.srv.status(jid), "needs_check")
        t, box, runner2, stopping2, _ = self.serve()
        self.assertTrue(_wait_for(lambda: self.polls() >= 3))
        stopping2.set()
        self.finish(t, box)
        self.assertEqual(runner2.calls, [])


class SessionTest(Base):
    def test_serialization_and_resume_kept_across_restart(self):
        """resume = 같은 session_ref 의 세션 맵(계약)으로만. 재시작은 근거가 아니다 — 맵은 그대로, 다른 세션엔 섞이지 않음."""
        self.srv.create(kind="converse", session_ref="sess-s")
        t, box, runner, stopping, _ = self.serve()
        self.assertTrue(_wait_for(lambda: len(runner.calls) >= 1 and self.polls() >= 2))
        stopping.set()
        self.finish(t, box)
        self.assertNotIn("--resume", runner.calls[0])
        # 재시작 — 같은 세션 새 작업은 세션 맵으로 resume, 다른 세션은 새로
        j2 = self.srv.create(kind="converse", session_ref="sess-s")
        j3 = self.srv.create(kind="converse", session_ref="sess-t")
        t, box, runner2, stopping2, _ = self.serve()
        self.assertTrue(_wait_for(lambda: self.srv.status(j3) == "result_ready"))
        stopping2.set()
        self.finish(t, box)
        self.assertEqual(self.srv.status(j2), "result_ready")
        self.assertEqual(runner2.calls[0][-2:], ["--resume", "fake-session-0001"])
        self.assertNotIn("--resume", runner2.calls[1])
        self.assertEqual(len(runner2.calls), 2)              # 앞 실행(완료)을 다시 부르지 않는다

    def test_kept_session_lease_blocks_same_session_until_expiry(self):
        """끊긴 converse 의 임대 행이 남아 있으면 같은 세션 새 작업은 입력도 부르지 않는다(세션 직렬화 유지)."""
        st = jobs.JsonState(self.state_path)
        st.session_acquire("00000000-0000-4000-8000-000000000001", 1, "sess-k", time.time() + 600, time.time())
        jid = self.srv.create(kind="converse", session_ref="sess-k")
        w = jobs.Worker(jobs.Client(self.srv.url, TOKEN, wait=1), jobs.fake_adapter(exec_runner=CountingGroup()),
                        jobs.JsonState(self.state_path), min_interval=0,
                        session_wait_step=jobs.LEASE_SEC)   # 대기 한 걸음이 예산을 넘음(시험) — 바로 포기
        out = w.poll_once()
        self.assertEqual(out["outcome"], "session-busy")
        self.assertEqual(self.srv.requests("input"), [])
        self.assertEqual(self.srv.status(jid), "leased")


class LogTest(Base):
    def test_log_has_no_body_or_secret(self):
        """로그 = 코드·ID·소요 시간만. 입력 원문·토큰·예외 메시지가 섞이지 않는다."""
        secret_exc = "SECRET-EXC-MSG " + SECRET_TEXT
        seq = [RuntimeError(secret_exc), "ok"]

        def auth():
            v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return v

        # 인증 예외 = "error" 로 끝(메시지 미기록)
        t, box, *_ = self.serve(auth=auth)
        self.assertEqual(self.finish(t, box)["stopped"], "error")
        self.srv.create(kind="summarize_reading")
        self.srv.create(kind="converse", session_ref="sess-l")
        t, box, runner, stopping, _ = self.serve()
        self.assertTrue(_wait_for(lambda: len(runner.calls) >= 2 and self.polls() >= 3))
        # 워커 안 예외도 클래스 이름만
        w = self.workers[-1]
        orig = w.client.poll
        w.client.poll = lambda: (_ for _ in ()).throw(ValueError(secret_exc))
        self.assertTrue(_wait_for(lambda: '"error": "ValueError"' in self.log_text()))
        w.client.poll = orig
        stopping.set()
        self.finish(t, box)
        text = self.log_text()
        for bad in (SECRET_TEXT, "SYNTH-INPUT-7731", "SECRET-EXC-MSG", TOKEN, "정리:", "fake-session", self.srv.url,
                    "127.0.0.1"):
            self.assertNotIn(bad, text)
        self.assertIn('"event": "poll"', text)
        self.assertIn('"outcome": "result-sent"', text)
        self.assertIn('"duration_ms"', text)

    def test_log_line_filters_values(self):
        line = jobs._log_line({"event": "poll", "outcome": "x y 본문", "code": "JOB-GONE", "status": 410,
                               "detail": "본문", "error": "a/b"})
        self.assertEqual(line, '{"code": "JOB-GONE", "error": "?", "event": "poll", "outcome": "?", "status": 410}')


class SignalTest(Base):
    @unittest.skipUnless(POSIX, "SIGTERM 전달 = POSIX")
    def test_sigterm_handler_main_thread(self):
        """메인 스레드에서 실제 SIGTERM → 멈춤. 끝나면 원래 처리기로 되돌린다."""
        before = signal.getsignal(signal.SIGTERM)
        runner = CountingGroup()

        def make_worker(st, ab):
            w = jobs.Worker(jobs.Client(self.srv.url, TOKEN, wait=1), jobs.fake_adapter(exec_runner=runner),
                            jobs.MemoryState(), min_interval=0.05, sleep=lambda s: ab.wait(min(s, 0.05)),
                            stopping=st, abort=ab)
            self.workers.append(w)
            return w

        threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
        res = jobs.serve_jobs(lock_path=self.lock_path, authenticate=lambda: "ok", make_worker=make_worker,
                              runner=runner, log=self.logs.append, grace=1)
        self.assertEqual(res["stopped"], "signal")
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        self.assertGreaterEqual(self.polls(), 1)


class FakeDevice:
    """GET /api/imports/device 흉내 — 결과(또는 예외)와 호출 수."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, server, token, timeout=30):
        return self

    def device(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class CliServeTest(Base):
    """cli `serve --jobs` — 종료 코드·잠금·인증·실 CLI 차단·SIGTERM. 뷰어(`serve`·`serve --port`)는 그대로."""

    def setUp(self):
        super().setUp()
        cfg = _config.load(self.th.paths)
        cfg.update(source_instance_id=STREAM_ID, hmac_namespace=NAMESPACE, policy_revision=1, server_url=self.srv.url)
        _config.save(self.th.paths, cfg)
        self.ks = MemoryKeyStore()
        self.ks.set(_config.token_account(STREAM_ID), TOKEN)
        self.dev = FakeDevice({"stream_id": STREAM_ID})
        self.runner_box = []

        def adapter(runner):
            self.runner_box.append(runner)
            return jobs.fake_adapter(exec_runner=runner)

        self.kw = dict(ks_factory=lambda service: self.ks, uploader_factory=self.dev, jobs_adapter_factory=adapter,
                       jobs_serve_kw={"grace": 2})

    def cli(self, *argv, **over):
        kw = dict(self.kw, **over)
        return run_cli(["--home", self.th.home, "serve", "--jobs"] + list(argv), **kw)

    def test_viewer_flag_unchanged(self):
        a = cli._parser().parse_args(["serve", "--port", "0"])
        self.assertFalse(a.jobs)

    def test_auth_rejected_exit_halt(self):
        from agent.uploader import DeviceError
        self.dev.result = DeviceError("auth", 401, "TOKEN-REVOKED")
        code, out = self.cli()
        self.assertEqual(code, cli.EXIT_HALT)
        self.assertEqual(self.polls(), 0)
        self.assertIn('"stopped": "auth"', out)
        self.assertNotIn(TOKEN, out)

    def test_auth_rejected_while_running_exit_halt(self):
        """돌다가 폴이 401 → 멈춤(auth-halted) · 종료 코드 3 · CLI 0."""
        self.srv.poll_script.append((401, {"code": "AUTH-INVALID"}, None))
        self.srv.create(kind="summarize_reading")
        code, out = self.cli()
        self.assertEqual(code, cli.EXIT_HALT)
        self.assertIn('"stopped": "auth-halted"', out)
        self.assertEqual(self.srv.requests("input"), [])

    def test_no_token_exit_halt(self):
        self.ks.delete(_config.token_account(STREAM_ID))
        code, out = self.cli()
        self.assertEqual(code, cli.EXIT_HALT)
        self.assertEqual(self.dev.calls, 0)

    def test_stream_mismatch_exit_err(self):
        self.dev.result = {"stream_id": "is_" + "0" * 24}
        code, out = self.cli()
        self.assertEqual(code, cli.EXIT_ERR)
        self.assertEqual(self.polls(), 0)

    def test_lock_busy_exit_err(self):
        lk = osutil.FileLock(self.lock_path).acquire()
        try:
            code, out = self.cli()
        finally:
            lk.release()
        self.assertEqual(code, cli.EXIT_ERR)
        self.assertIn('"stopped": "already-running"', out)
        self.assertEqual(self.dev.calls, 0)

    def test_real_cli_blocked_without_env(self):
        cfg = _config.load(self.th.paths)
        cfg["summary"]["cli_path"] = "/nonexistent/claude-synth"
        _config.save(self.th.paths, cfg)
        with mock.patch.dict(os.environ):
            os.environ.pop(jobs.REAL_CLI_ENV, None)
            code, out = self.cli(jobs_adapter_factory=None)
        self.assertEqual(code, cli.EXIT_ERR)
        self.assertIn('"stopped": "adapter"', out)
        self.assertEqual((self.dev.calls, self.polls()), (0, 0))

    def _clear_cli_path(self):
        cfg = _config.load(self.th.paths)
        cfg["summary"]["cli_path"] = None
        _config.save(self.th.paths, cfg)

    @unittest.skipUnless(POSIX, "실행 비트로 찾기 = POSIX")
    def test_cli_path_empty_uses_claude_on_path(self):
        """cli_path 가 비면 PATH 의 claude 절대 경로로 어댑터를 만든다. 인증 거절로 끝내 CLI 는 부르지 않는다."""
        from agent.uploader import DeviceError
        self._clear_cli_path()
        bindir = tempfile.mkdtemp(dir=self.th.home)
        fake = os.path.join(bindir, "claude")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nexit 99\n")
        os.chmod(fake, 0o755)
        self.dev.result = DeviceError("auth", 401, "TOKEN-REVOKED")
        with mock.patch.dict(os.environ, {"PATH": bindir, jobs.REAL_CLI_ENV: "1"}), \
                mock.patch.object(jobs, "ClaudeAdapter", wraps=jobs.ClaudeAdapter) as made:
            code, out = self.cli(jobs_adapter_factory=None)
        self.assertEqual(made.call_count, 1)
        self.assertEqual(made.call_args[0][0], [fake])
        self.assertTrue(os.path.isabs(made.call_args[0][0][0]))
        self.assertEqual(code, cli.EXIT_HALT)          # 어댑터 단계를 지나 인증에서 멈춤
        self.assertEqual((self.dev.calls, self.polls()), (1, 0))
        self.assertNotIn('"stopped": "adapter"', out)
        self.assertEqual(_config.load(self.th.paths)["summary"]["cli_path"], None)   # 찾은 경로를 저장하지 않음

    def test_cli_path_empty_not_on_path_stops(self):
        """cli_path 도 비고 PATH 에도 claude 가 없으면 adapter 정지 + cli-not-found. 경로는 출력하지 않는다."""
        self._clear_cli_path()
        bindir = tempfile.mkdtemp(dir=self.th.home)
        with mock.patch.dict(os.environ, {"PATH": bindir, jobs.REAL_CLI_ENV: "1"}), \
                mock.patch.object(jobs, "ClaudeAdapter", wraps=jobs.ClaudeAdapter) as made:
            code, out = self.cli(jobs_adapter_factory=None)
        self.assertEqual(code, cli.EXIT_ERR)
        self.assertEqual(made.call_count, 0)
        self.assertIn('"stopped": "adapter"', out)
        self.assertIn('"reason_code": "cli-not-found"', out)
        self.assertNotIn(bindir, out)
        self.assertNotIn(self.th.home, out)
        self.assertEqual((self.dev.calls, self.polls()), (0, 0))

    def test_cli_path_config_wins_over_path(self):
        """config 값이 있으면 PATH 를 보지 않는다(지금 동작 유지)."""
        cfg = _config.load(self.th.paths)
        cfg["summary"]["cli_path"] = "/nonexistent/claude-synth"
        _config.save(self.th.paths, cfg)
        with mock.patch.dict(os.environ, {jobs.REAL_CLI_ENV: "1"}), \
                mock.patch.object(cli.shutil, "which") as which, \
                mock.patch.object(jobs, "ClaudeAdapter", wraps=jobs.ClaudeAdapter) as made:
            from agent.uploader import DeviceError
            self.dev.result = DeviceError("auth", 401, "TOKEN-REVOKED")
            code, out = self.cli(jobs_adapter_factory=None)
        which.assert_not_called()
        self.assertEqual(made.call_args[0][0], ["/nonexistent/claude-synth"])
        self.assertEqual(code, cli.EXIT_HALT)

    @unittest.skipUnless(POSIX, "SIGTERM 전달 = POSIX")
    def test_sigterm_runs_job_then_exits_ok(self):
        jid = self.srv.create(kind="summarize_reading")
        before = signal.getsignal(signal.SIGTERM)

        def killer():
            _wait_for(lambda: self.srv.status(jid) == "result_ready")
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=killer, daemon=True).start()
        code, out = self.cli()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(self.srv.status(jid), "result_ready")
        self.assertIsInstance(self.runner_box[0], jobs.GroupRunner)     # 자식 = 새 프로세스 그룹
        self.assertEqual(self.srv.jobs[jid]["attempts"], 1)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        for bad in (TOKEN, SECRET_TEXT, self.srv.url, "정리:"):
            self.assertNotIn(bad, out)
        self.assertIn('"outcome": "result-sent"', out)
        self.assertIn('"stopped": "signal"', out)


if __name__ == "__main__":
    unittest.main()
