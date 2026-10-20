"""CLI 흐름(macOS): connect → schedule install(모의) → run-once(가짜 서버) → status → 원문 열기 → disconnect
→ schedule remove. 출력에 토큰·키·원문 없음. Windows 스모크: 계약 검증·run-once 가 OS 분기로 깨지지 않음."""
from __future__ import annotations

import http.client
import io
import json
import os
import re
import subprocess
import sys
import threading
import unittest
from unittest import mock

from agent import cli, config as _config, keystore, osutil
from agent.tests.helpers import (NAMESPACE, STREAM_ID, TOKEN, FailingKeyStore, FakeImportServer, MemoryKeyStore,
                                 TempHome, run_cli, synth_row, write_jsonl)


class CliFlowTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()
        self.srv = FakeImportServer()
        self.ks = MemoryKeyStore()
        self.la = os.path.join(self.th.root, "LaunchAgents")
        self.lc_calls = []
        self.kw = dict(ks_factory=lambda service: self.ks, getpass_fn=lambda prompt: TOKEN,
                       schedule_runner=self._lc, launch_agents_dir=self.la)

    def tearDown(self):
        self.srv.close()
        self.th.cleanup()

    def _lc(self, argv):
        self.lc_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def cli(self, *argv):
        return run_cli(["--home", self.th.home] + list(argv), **self.kw)

    def connect(self):
        # 토큰만 — 키 공간·정책·이름은 GET /api/imports/device 에서 받는다
        return self.cli("connect", "--server", self.srv.url)

    def enable_likes(self):
        path = os.path.join(self.th.root, "likes.jsonl")
        write_jsonl(path, [synth_row(1), synth_row(2)])
        cfg = _config.load(self.th.paths)
        cfg["adapters"]["likes"].update({"enabled": True, "input_path": path, "provider_code": "synthnet",
                                         "include_kinds": ["kindA"]})
        cfg["upload"]["min_interval_sec"] = 0
        _config.save(self.th.paths, cfg)

    def test_full_flow(self):
        code, out = self.connect()
        self.assertEqual(code, 0, out)
        self.assertNotIn(TOKEN, out)
        self.assertEqual(self.ks.get(_config.token_account(STREAM_ID)), TOKEN)
        hk = self.ks.get(_config.hmac_account(NAMESPACE))
        self.assertRegex(hk, r"^[0-9a-f]{64}$")
        with open(self.th.paths.config, encoding="utf-8") as f:
            raw_cfg = f.read()
        self.assertNotIn(TOKEN, raw_cfg)
        self.assertNotIn(hk, raw_cfg)

        if osutil.IS_MAC:
            code, out = self.cli("schedule", "install")
            self.assertEqual(code, 0, out)
            self.assertTrue(any(c[1] == "bootstrap" for c in self.lc_calls))

        self.enable_likes()
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(res["upload"]["accepted"], 2)
        self.assertEqual(len(self.srv.by_key), 2)          # 서버 접수
        for b in self.srv.bodies():
            self.assertNotIn(b"SYNPOST", b)
            self.assertNotIn(b"example.invalid", b)

        code, out = self.cli("status")
        self.assertEqual(code, 0)
        self.assertIn("접수 2", out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(hk, out)

        code, out = self.cli("validate")
        self.assertEqual(code, 0, out)

        # 원문 열기 — 서버로 간 raw_id 로 PC 에서만 열림
        raw_id = json.loads(self.srv.bodies()[0])["raw_id"]
        a = cli._parser().parse_args(["--home", self.th.home, "serve", "--port", "0"])
        ctx = cli.Ctx(self.th.home, out=io.StringIO(), **self.kw)
        srv = cli.cmd_serve(ctx, a, serve_forever=False)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            port = srv.server_address[1]
            host, origin = f"127.0.0.1:{port}", f"http://127.0.0.1:{port}"
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", f"/raw/{raw_id}", headers={"Host": host})
            r = c.getresponse()
            sid = re.search(r"mcrm_sid=([^;]+)", r.getheader("Set-Cookie")).group(1)
            r.read()
            c.request("POST", "/raw/token", body=json.dumps({"raw_id": raw_id}),
                      headers={"Host": host, "Origin": origin, "Cookie": f"mcrm_sid={sid}",
                               "Content-Type": "application/json"})
            r = c.getresponse()
            tok = json.loads(r.read())["token"]
            c.request("POST", "/raw/open", body=f"raw_id={raw_id}&token={tok}",
                      headers={"Host": host, "Origin": origin, "Cookie": f"mcrm_sid={sid}",
                               "Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse()
            body = r.read()
            self.assertEqual(r.status, 200)
            self.assertIn(b"SYNTHETIC-BODY-", body)
            c.close()
        finally:
            srv.shutdown()
            srv.server_close()

        code, out = self.cli("disconnect")
        self.assertEqual(code, 0)
        self.assertIn("서버 토큰: 폐기함", out)
        self.assertTrue(self.srv.revoked)
        self.assertIn(("POST", "/api/imports/device/revoke"), self.srv.device_calls)
        self.assertIsNone(self.ks.get(_config.token_account(STREAM_ID)))
        self.assertIsNotNone(self.ks.get(_config.hmac_account(NAMESPACE)))   # 기본은 키 유지
        code, out = self.cli("run-once")
        self.assertEqual(code, 1)
        self.assertIn("connect", out)

        if osutil.IS_MAC:
            code, out = self.cli("schedule", "remove")
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(os.path.join(self.la, "local.mycrm.agent.plist")))
        # 원문·대응표는 남는다
        self.assertTrue(os.listdir(self.th.paths.raw))

    def test_connect_uses_device_info(self):
        code, out = self.connect()
        self.assertEqual(code, 0, out)
        cfg = _config.load(self.th.paths)
        self.assertEqual(cfg["hmac_namespace"], NAMESPACE)
        self.assertEqual(cfg["device_label"], "synthetic-device")
        self.assertEqual(cfg["allowed_actions"], ["like"])
        self.assertEqual(cfg["allowed_transforms"], ["board-likes/1", "self-text/1", "self-summary/1"])
        self.assertEqual(cfg["token_expires_at"], "2027-01-18T00:00:00+09:00")
        self.assertEqual(self.srv.device_calls[0], ("GET", "/api/imports/device"))
        self.assertNotIn("덮어씀", out)
        code, out = self.cli("status")
        self.assertIn("토큰 만료: 2027-01-18", out)

    def test_connect_overrides_reported(self):
        code, out = self.cli("connect", "--server", self.srv.url, "--hmac-namespace", "hn_override01",
                             "--policy-revision", "2")
        self.assertEqual(code, 0, out)
        self.assertIn("덮어씀: hmac_namespace, policy_revision", out)
        self.assertEqual(_config.load(self.th.paths)["hmac_namespace"], "hn_override01")

    def test_connect_rejected_token_saves_nothing(self):
        for forced, expect in (((401, {"code": "TOKEN-EXPIRED"}), "TOKEN-EXPIRED"), ((503, {"code": "IMPORTS-OFF"}), "503")):
            with self.subTest(expect):
                self.srv.device_script = [forced]
                code, out = self.connect()
                self.assertEqual(code, 1)
                self.assertIn(expect, out)
                self.assertEqual(self.ks.items, {})
                self.assertFalse(os.path.exists(self.th.paths.config))

    def test_connect_stream_mismatch_saves_nothing(self):
        self.srv.device_info = dict(self.srv.device_info, stream_id="is_" + "9" * 24)
        code, out = self.connect()
        self.assertEqual(code, 1)
        self.assertEqual(self.ks.items, {})
        self.assertFalse(os.path.exists(self.th.paths.config))

    def test_disconnect_server_failure_keeps_local_token(self):
        self.connect()
        self.srv.device_script = [(503, {"code": "IMPORTS-OFF"})]
        code, out = self.cli("disconnect")
        self.assertEqual(code, 1)
        self.assertIn("폐기 못 함", out)
        self.assertEqual(self.ks.get(_config.token_account(STREAM_ID)), TOKEN)
        code, out = self.cli("disconnect", "--local-only")
        self.assertEqual(code, 0)
        self.assertIn("서버 토큰: 건너뜀", out)
        self.assertIsNone(self.ks.get(_config.token_account(STREAM_ID)))
        self.assertFalse(self.srv.revoked)

    def test_disconnect_already_revoked(self):
        self.connect()
        self.srv.revoked = True
        code, out = self.cli("disconnect")
        self.assertEqual(code, 0)
        self.assertIn("이미 무효(TOKEN-REVOKED)", out)
        self.assertIsNone(self.ks.get(_config.token_account(STREAM_ID)))

    def test_revoked_token_halts_upload(self):
        self.connect()
        self.enable_likes()
        self.srv.revoked = True
        code, out = self.cli("run-once")
        self.assertEqual(code, 3)

    def test_connect_rejects_bad_token(self):
        self.kw["getpass_fn"] = lambda prompt: "not-a-token"
        code, out = self.connect()
        self.assertEqual(code, 2)
        self.assertNotIn("not-a-token", out)

    def test_connect_keystore_failure_no_plaintext(self):
        self.kw["ks_factory"] = lambda service: FailingKeyStore()
        code, out = self.connect()
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.th.paths.config))
        for root, _dirs, files in os.walk(self.th.root):
            for fn in files:
                with open(os.path.join(root, fn), "rb") as f:
                    self.assertNotIn(TOKEN.encode(), f.read())

    def test_connect_token_stdin(self):
        self.kw["stdin"] = io.StringIO(TOKEN + "\n")
        self.kw["getpass_fn"] = lambda prompt: self.fail("getpass used")
        code, out = self.cli("connect", "--server", self.srv.url, "--label", "x", "--hmac-namespace", NAMESPACE,
                             "--token-stdin")
        self.assertEqual(code, 0, out)

    def test_auth_halt_exit_code(self):
        self.connect()
        self.enable_likes()
        self.srv.token = "imp_" + STREAM_ID + ".other." + "Z" * 43
        code, out = self.cli("run-once")
        self.assertEqual(code, 3)
        self.srv.token = TOKEN
        code, out = self.cli("run-once", "--resume", "auth")
        self.assertEqual(code, 0, out)

    # ---- 허용 규칙 변경(POLICY-STALE) — 토큰 재입력 없이 정책을 다시 받아 한 번 재시도 ----
    def _stale_server(self, new_policy=None):
        """첫 POST 를 POLICY-STALE 로 거절하고, 장치 정보의 정책 판을 2로 올린다(웹에서 허용 규칙을 바꾼 상황)."""
        self.srv.script = [(409, {"code": "POLICY-STALE", "msg": "stale"})]
        self.srv.device_info = dict(self.srv.device_info, policy_revision=2)
        if new_policy is not None:
            self.srv.device_info["policy"] = new_policy

    def _sent_policy_revisions(self):
        return [json.loads(b)["policy_revision"] for b in self.srv.bodies()]

    def test_policy_stale_refreshes_and_retries_once(self):
        self.connect()
        self.enable_likes()
        self._stale_server()
        gets_before = sum(1 for m, _ in self.srv.device_calls if m == "GET")
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(res["policy_refresh"]["status"], "ok")
        self.assertEqual(res["policy_refresh"]["policy_revision"], 2)
        self.assertEqual(res["upload"]["accepted"], 2)
        self.assertEqual(sum(1 for m, _ in self.srv.device_calls if m == "GET"), gets_before + 1)
        self.assertEqual(self._sent_policy_revisions(), [1, 2, 2])     # 거절 1 → 새 판으로 2건
        self.assertEqual(_config.load(self.th.paths)["policy_revision"], 2)
        self.assertNotIn(TOKEN, out)
        code, out = self.cli("status")
        self.assertNotIn("멈춤 범위", out)
        # 다음 실행은 같은 내용이라 새 개정을 만들지 않는다(정책 판이 바뀐 대기 요청의 projection 도 갱신됨)
        n = len(self.srv.bodies())
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.srv.bodies()), n)

    def test_policy_change_makes_no_new_revision_for_accepted(self):
        # 정책 판이 바뀌어도 이미 접수된 원본은 새 개정을 만들지 않는다(정책 판은 요청 메타데이터 — projection 밖)
        self.connect()
        self.enable_likes()
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._sent_policy_revisions(), [1, 1])
        # 웹에서 허용 규칙을 바꿈 → 새 행 하나가 POLICY-STALE 를 받고 자동 갱신 뒤 판 2 로 접수
        path = _config.load(self.th.paths)["adapters"]["likes"]["input_path"]
        write_jsonl(path, [synth_row(1), synth_row(2), synth_row(3)])
        self._stale_server()
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._sent_policy_revisions(), [1, 1, 1, 2])
        # 다음 수집: 판 1 로 접수된 두 원본도, 판 2 로 접수된 새 원본도 새 개정 0
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(res["collect"]["staged"], 0)
        self.assertEqual(len(self.srv.bodies()), 4)

    def test_policy_stale_refresh_failure_keeps_halt(self):
        self.connect()
        self.enable_likes()
        self._stale_server()
        self.srv.device_script = [(503, {"code": "DOWN"})]
        code, out = self.cli("run-once")
        self.assertEqual(code, 3, out)
        res = json.loads(out.strip().splitlines()[-2])
        self.assertEqual(res["policy_refresh"]["status"], "device-server")
        self.assertEqual(_config.load(self.th.paths)["policy_revision"], 1)
        self.assertIn("--resume policy", out)
        # 다음 실행: 장치 정보가 살아나면 멈춤 상태에서도 다시 받아 이어 간다
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        self.assertEqual(_config.load(self.th.paths)["policy_revision"], 2)

    def test_policy_stale_new_policy_drops_action(self):
        self.connect()
        self.enable_likes()
        self._stale_server({"actions": [], "transform_versions": ["board-likes/1"], "axes": []})
        code, out = self.cli("run-once")
        self.assertEqual(code, 0, out)
        res = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((res["policy_refresh"]["rebased"], res["policy_refresh"]["rejected"]), (0, 2))
        self.assertEqual(self._sent_policy_revisions(), [1])           # 새 규칙이 막은 요청은 보내지 않는다

    def test_policy_stale_namespace_change_not_applied(self):
        self.connect()
        self.enable_likes()
        self._stale_server()
        self.srv.device_info["hmac_namespace"] = "hn_" + "0" * 24
        code, out = self.cli("run-once")
        self.assertEqual(code, 3, out)
        self.assertIn('"namespace-changed"', out)
        self.assertEqual(_config.load(self.th.paths)["hmac_namespace"], NAMESPACE)

    def test_reconnect_keeps_hmac_key(self):
        self.connect()
        k1 = self.ks.get(_config.hmac_account(NAMESPACE))
        self.connect()
        self.assertEqual(self.ks.get(_config.hmac_account(NAMESPACE)), k1)


class WindowsSmokeTest(unittest.TestCase):
    """Windows 분기 모의 — 계약 검증·run-once(업로드 포함)·로컬 HTTP 가 깨지지 않는지."""

    def test_windows_branches(self):
        th = TempHome()
        srv = FakeImportServer()
        try:
            with mock.patch.object(osutil, "IS_WINDOWS", True), mock.patch.object(osutil, "IS_MAC", False):
                self.assertIsInstance(keystore.for_platform("my-crm-agent"), keystore.WindowsCredentialStore)
                # 자격 증명 관리자 미구현 → connect 는 실패 종료(평문 대체 없음)
                code, out = run_cli(["--home", th.home, "connect", "--server", srv.url, "--label", "win",
                                     "--hmac-namespace", NAMESPACE], getpass_fn=lambda p: TOKEN)
                self.assertEqual(code, 1)
                self.assertFalse(os.path.exists(th.paths.config))
                # 예약 도우미는 Phase 2
                code, out = run_cli(["--home", th.home, "schedule", "install"])
                self.assertEqual(code, 1)
            # run-once 경로 자체(모의 저장소)는 OS 와 무관하게 동작 — chmod 분기는 IS_WINDOWS 에서 건너뜀
            ks = MemoryKeyStore()
            import types
            fake_msvcrt = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda fd, mode, n: None)
            with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}), \
                    mock.patch.object(osutil, "IS_WINDOWS", True), mock.patch.object(osutil, "IS_MAC", False), \
                    mock.patch("os.fchmod", side_effect=AssertionError("chmod on windows")), \
                    mock.patch("os.chmod", side_effect=AssertionError("chmod on windows")):
                code, out = run_cli(["--home", th.home, "connect", "--server", srv.url, "--label", "win",
                                     "--hmac-namespace", NAMESPACE],
                                    ks_factory=lambda s: ks, getpass_fn=lambda p: TOKEN)
                self.assertEqual(code, 0, out)
                path = os.path.join(th.root, "likes.jsonl")
                write_jsonl(path, [synth_row(1)])
                cfg = _config.load(th.paths)
                cfg["adapters"]["likes"].update({"enabled": True, "input_path": path, "provider_code": "synthnet",
                                                 "include_kinds": ["kindA"]})
                cfg["upload"]["min_interval_sec"] = 0
                _config.save(th.paths, cfg)
                code, out = run_cli(["--home", th.home, "run-once"], ks_factory=lambda s: ks)
                self.assertEqual(code, 0, out)
                code, out = run_cli(["--home", th.home, "validate"], ks_factory=lambda s: ks)
                self.assertEqual(code, 0, out)
            self.assertEqual(len(srv.by_key), 1)
        finally:
            srv.close()
            th.cleanup()

    def test_modules_import_without_fcntl(self):
        # fcntl 은 잠금 시점에만 import — 모듈 import 단계에서 POSIX 전용 모듈을 요구하지 않는다
        import importlib
        import agent.osutil as ou
        with open(ou.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotRegex(src.split("class FileLock")[0], r"^import fcntl", )
        for m in ("agent.contract", "agent.jcs", "agent.runner", "agent.uploader", "agent.viewer"):
            importlib.import_module(m)


if __name__ == "__main__":
    unittest.main()
