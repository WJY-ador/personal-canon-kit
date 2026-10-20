"""무인 요약 — 모의 CLI 실행기로만. 성공 = exit 0 AND 계약 검증. 실패 출력 미저장 · 하루 10회 · 5분 · 동시 1
· 로그인 오류 멈춤 · 모델에 토큰·키·매핑 미전달 · 기본 꺼짐."""
from __future__ import annotations

import json
import os
import subprocess
import unittest

from agent import config as _config, osutil, runner, store as _store, summary
from agent.tests.helpers import (NAMESPACE, TOKEN, FakeImportServer, TempHome, connected_config,
                                 keystore_with_secrets)


def ok_output(title="합성 요약 제목", body="합성 요약 본문입니다."):
    return json.dumps({"type": "result", "is_error": False,
                       "result": json.dumps({"title": title, "body": body}, ensure_ascii=False)}).encode()


class MockCLI:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, argv, *, stdin_bytes, env, cwd, timeout):
        self.calls.append({"argv": argv, "stdin": stdin_bytes, "env": env, "cwd": cwd, "timeout": timeout})
        r = self.responses.pop(0) if self.responses else (0, ok_output(), b"")
        if isinstance(r, BaseException):
            raise r
        return r


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()
        self.journal = os.path.join(self.th.root, "journal")
        os.makedirs(self.journal)
        self.ks = keystore_with_secrets()
        self.cfg = connected_config(self.th, "http://127.0.0.1:9")
        self.cfg["summary"].update({"enabled": True, "input_dir": self.journal, "cli_path": "/usr/bin/false"})
        _config.save(self.th.paths, self.cfg)
        self.st = _store.Store(self.th.paths)

    def tearDown(self):
        self.st.close()
        self.th.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.journal, name), "w", encoding="utf-8") as f:
            f.write(text)

    def go(self, cli):
        return summary.summarize(self.st, _config.load(self.th.paths), paths=self.th.paths, exec_runner=cli)

    def pending(self):
        out = []
        for n in sorted(os.listdir(self.th.paths.pending)):
            with open(os.path.join(self.th.paths.pending, n), "rb") as f:
                out.append(json.loads(f.read()))
        return out

    def test_disabled_by_default(self):
        self.assertFalse(_config.default_config()["summary"]["enabled"])
        cfg = _config.load(self.th.paths)
        cfg["summary"]["enabled"] = False
        cli = MockCLI()
        self.assertEqual(summary.summarize(self.st, cfg, paths=self.th.paths, exec_runner=cli)["skipped"], "disabled")
        self.assertEqual(cli.calls, [])

    def test_success_builds_valid_self_summary(self):
        self.write("2026-10-01.md", "오늘 내가 한 일 합성 기록")
        cli = MockCLI()
        r = self.go(cli)
        self.assertEqual((r["attempted"], r["staged"]), (1, 1))
        (obj,) = self.pending()
        self.assertEqual(obj["kind"], "self_summary")
        self.assertEqual(obj["execution"], "unattended")
        g = obj["generation"]
        self.assertEqual((g["method"], g["input_scope"]), ("ai_summary", "self_only"))
        self.assertRegex(g["inputs"][0]["original_id"], r"^o_[0-9a-f]{32}$")
        self.assertNotIn("2026-10-01.md", json.dumps(obj))       # 파일명이 ID 로 새지 않음
        # 다시 돌려도 같은 입력은 다시 요약하지 않는다
        r = self.go(cli)
        self.assertEqual(r["attempted"], 0)

    def test_execution_follows_run_kind(self):
        # 사람이 돌린 run-once = attended, 예약 = unattended, 호출자 불명(키 없음) = unattended
        for i, (mark, want) in enumerate((("attended", "attended"), ("unattended", "unattended"), (None, "unattended"))):
            self.write(f"e{i}.md", f"합성 기록 {i}")
            cfg = _config.load(self.th.paths)
            if mark is not None:
                cfg["_execution"] = mark
            before = {o["generation"]["inputs"][0]["original_id"] for o in self.pending()}
            r = summary.summarize(self.st, cfg, paths=self.th.paths, exec_runner=MockCLI())
            self.assertEqual(r["staged"], 1)
            (new,) = [o for o in self.pending() if o["generation"]["inputs"][0]["original_id"] not in before]
            self.assertEqual(new["execution"], want, mark)

    def test_model_gets_no_secrets(self):
        self.write("a.md", "합성 기록")
        cli = MockCLI()
        os.environ["MYCRM_SYNTH_SECRET"] = TOKEN
        try:
            self.go(cli)
        finally:
            del os.environ["MYCRM_SYNTH_SECRET"]
        call = cli.calls[0]
        blob = json.dumps(call["argv"]) + json.dumps(call["env"]) + call["stdin"].decode()
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn(self.ks.get(_config.hmac_account(NAMESPACE)), blob)
        self.assertNotIn(NAMESPACE, blob)
        self.assertEqual(set(call["env"]) - {"PATH", "HOME", "LANG"}, set())
        self.assertEqual(call["cwd"], self.th.paths.summary_work)
        self.assertEqual(call["timeout"], 300)
        argv = call["argv"]
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "", "도구 전부 끔(허용 목록 비움)")
        self.assertEqual(argv[argv.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--disallowedTools", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")

    def test_exit0_but_contract_fails_not_staged(self):
        self.write("a.md", "합성 기록")
        r = self.go(MockCLI([(0, ok_output(body="자세한 건 https://example.invalid/x 참고"), b"")]))
        self.assertEqual((r["staged"], r["failed"]), (0, 1))
        self.assertEqual(self.pending(), [])

    def test_nonzero_exit_not_staged(self):
        self.write("a.md", "합성 기록")
        r = self.go(MockCLI([(1, ok_output(), b"boom")]))
        self.assertEqual((r["staged"], r["failed"]), (0, 1))
        self.assertEqual(self.pending(), [])

    def test_bad_shapes_not_staged(self):
        for i, out in enumerate((b"not json", json.dumps({"result": "not json"}).encode(),
                                 json.dumps({"is_error": True, "result": "{}"}).encode(),
                                 json.dumps({"result": json.dumps({"body": "x", "people": ["a"]})}).encode())):
            self.write(f"f{i}.md", f"합성 기록 {i}")
        r = self.go(MockCLI([(0, o, b"") for o in (b"not json", json.dumps({"result": "not json"}).encode(),
                                                     json.dumps({"is_error": True, "result": "{}"}).encode(),
                                                     json.dumps({"result": json.dumps(
                                                         {"body": "x", "people": ["a"]})}).encode())]))
        self.assertEqual((r["attempted"], r["staged"], r["failed"]), (4, 0, 4))
        self.assertEqual(self.pending(), [])

    def test_timeout_not_staged(self):
        self.write("a.md", "합성 기록")
        r = self.go(MockCLI([subprocess.TimeoutExpired("claude", 300)]))
        self.assertEqual((r["staged"], r["failed"]), (0, 1))

    def test_login_error_halts_until_resume(self):
        self.write("a.md", "합성 기록 1")
        self.write("b.md", "합성 기록 2")
        cli = MockCLI([(1, b"", b"Error: Not logged in. Please run /login")])
        r = self.go(cli)
        self.assertEqual(r["skipped"], "halted")
        self.assertEqual(len(cli.calls), 1)
        r = self.go(cli)
        self.assertEqual(r["skipped"], "halted")
        self.assertEqual(len(cli.calls), 1)
        self.assertTrue(self.st.clear_halt("summary"))

    def test_daily_attempt_limit(self):
        for i in range(12):
            self.write(f"{i:02d}.md", f"합성 기록 {i}")
        cli = MockCLI([(1, b"", b"x")] * 12)
        r = self.go(cli)
        self.assertEqual(r["attempted"], 10)
        self.assertEqual(r["skipped"], "daily-limit")
        self.assertEqual(self.st.summary_attempts_today(osutil.kst_today()), 10)
        r = self.go(MockCLI())
        self.assertEqual(r["attempted"], 0)

    def test_concurrency_one(self):
        self.write("a.md", "합성 기록")
        with osutil.FileLock(self.th.paths.lock("summary")):
            r = self.go(MockCLI())
        self.assertEqual(r["skipped"], "already-running")

    def test_identity_in_body_quarantined(self):
        self.st.person_hmac(NAMESPACE, "synthnet", "synth_author_9", lambda: "a" * 64)
        self.write("a.md", "합성 기록")
        r = self.go(MockCLI([(0, ok_output(body="오늘 synth_author_9 의 글을 봤다"), b"")]))
        self.assertEqual(r["staged"], 0)
        self.assertEqual(self.pending(), [])

    def test_symlink_and_outside_inputs_ignored(self):
        outside = os.path.join(self.th.root, "outside.md")
        with open(outside, "w") as f:
            f.write("타인 자료")
        if not osutil.IS_WINDOWS:
            os.symlink(outside, os.path.join(self.journal, "link.md"))
        self.write(".hidden.md", "숨김")
        cli = MockCLI()
        r = self.go(cli)
        self.assertEqual(r["attempted"], 0)

    def test_retry_uses_stored_request_not_model(self):
        """업로드 실패 뒤 재시도는 저장한 요청 재전송 — 모델 재실행 없음."""
        srv = FakeImportServer()
        try:
            self.cfg["server_url"] = srv.url
            _config.save(self.th.paths, self.cfg)
            self.write("a.md", "합성 기록")
            cli = MockCLI()
            srv.script = [(503, {"code": "DOWN"})]

            def summarizer(st, c):
                return summary.summarize(st, c, paths=self.th.paths, exec_runner=cli)
            runner.run_once(self.th.paths, ks=self.ks, summarizer=summarizer)
            st = _store.Store(self.th.paths)
            st.conn.execute("UPDATE revisions SET next_attempt_at=NULL")
            st.close()
            runner.run_once(self.th.paths, ks=self.ks, summarizer=summarizer)
            self.assertEqual(len(cli.calls), 1)
            self.assertEqual(srv.bodies()[0], srv.bodies()[1])
        finally:
            srv.close()

    def test_run_once_manual_is_attended_scheduled_is_unattended(self):
        """run-once(사람) → attended · run-once --scheduled(예약) → unattended — 서버로 가는 요청 본문 기준."""
        srv = FakeImportServer()
        try:
            self.cfg["server_url"] = srv.url
            _config.save(self.th.paths, self.cfg)

            def summarizer(st, c):
                return summary.summarize(st, c, paths=self.th.paths, exec_runner=MockCLI())
            self.write("m.md", "합성 기록 손으로")
            runner.run_once(self.th.paths, ks=self.ks, summarizer=summarizer)
            self.write("s.md", "합성 기록 예약")
            runner.run_once(self.th.paths, ks=self.ks, summarizer=summarizer, scheduled=True, jitter_max=0)
            got = [json.loads(b)["execution"] for b in srv.bodies() if json.loads(b)["kind"] == "self_summary"]
            self.assertEqual(got, ["attended", "unattended"])
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
