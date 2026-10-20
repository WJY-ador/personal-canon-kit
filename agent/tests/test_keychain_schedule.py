"""Keychain(모의 security 실행기) · launchd plist 내용 · 등록·해제 멱등(모의 launchctl)."""
from __future__ import annotations

import os
import plistlib
import subprocess
import unittest
from unittest import mock

from agent import keystore, osutil, schedule
from agent.tests.helpers import TOKEN, TempHome


class FakeSecurity:
    """security CLI 흉내 — argv·stdin 기록, 메모리에 저장."""

    def __init__(self, fail_code=None):
        self.calls = []
        self.items = {}
        self.fail_code = fail_code

    def __call__(self, argv, stdin_text=None, timeout=30):
        self.calls.append((list(argv), stdin_text))
        if self.fail_code is not None:
            return subprocess.CompletedProcess(argv, self.fail_code, "", "denied")
        if argv[1] == "-i":
            import shlex
            parts = shlex.split(stdin_text)
            assert parts[0] == "add-generic-password"
            s = parts[parts.index("-s") + 1]
            a = parts[parts.index("-a") + 1]
            w = parts[parts.index("-w") + 1]
            self.items[(s, a)] = w
            return subprocess.CompletedProcess(argv, 0, "", "")
        s = argv[argv.index("-s") + 1]
        a = argv[argv.index("-a") + 1]
        if argv[1] == "find-generic-password":
            if (s, a) not in self.items:
                return subprocess.CompletedProcess(argv, 44, "", "not found")
            return subprocess.CompletedProcess(argv, 0, self.items[(s, a)] + "\n", "")
        if argv[1] == "delete-generic-password":
            if self.items.pop((s, a), None) is None:
                return subprocess.CompletedProcess(argv, 44, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError("unexpected")


class KeychainTest(unittest.TestCase):
    def test_roundtrip_secret_never_in_argv(self):
        fs = FakeSecurity()
        kc = keystore.MacKeychain("my-crm-agent", runner=fs)
        kc.set("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a", TOKEN)
        self.assertEqual(kc.get("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a"), TOKEN)
        for argv, _stdin in fs.calls:
            self.assertNotIn(TOKEN, " ".join(argv))
        self.assertTrue(any(TOKEN in (stdin or "") for _, stdin in fs.calls))   # 표준 입력으로만
        self.assertTrue(kc.delete("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a"))
        self.assertIsNone(kc.get("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a"))
        self.assertFalse(kc.delete("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a"))

    def test_access_denied_fails_closed(self):
        kc = keystore.MacKeychain("my-crm-agent", runner=FakeSecurity(fail_code=51))
        with self.assertRaises(keystore.KeyStoreError) as cm:
            kc.get("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a")
        self.assertNotIn(TOKEN, str(cm.exception))
        with self.assertRaises(keystore.KeyStoreError):
            kc.set("upload-token:is_5a5a5a5a5a5a5a5a5a5a5a5a", TOKEN)

    def test_unsafe_names_and_secrets_refused(self):
        kc = keystore.MacKeychain("my-crm-agent", runner=FakeSecurity())
        with self.assertRaises(keystore.KeyStoreError):
            kc.set('a" -w "x', TOKEN)
        with self.assertRaises(keystore.KeyStoreError):
            kc.set("upload-token:x", 'abc"; delete-keychain')
        with self.assertRaises(keystore.KeyStoreError):
            keystore.MacKeychain("bad service")

    def test_windows_adapter_not_implemented(self):
        w = keystore.WindowsCredentialStore("my-crm-agent")
        for fn in (lambda: w.get("a"), lambda: w.set("a", "b" * 20), lambda: w.delete("a")):
            with self.assertRaises(keystore.KeyStoreUnavailable):
                fn()

    def test_platform_factory(self):
        with mock.patch.object(osutil, "IS_MAC", False), mock.patch.object(osutil, "IS_WINDOWS", True):
            self.assertIsInstance(keystore.for_platform("my-crm-agent"), keystore.WindowsCredentialStore)
        with mock.patch.object(osutil, "IS_MAC", False), mock.patch.object(osutil, "IS_WINDOWS", False):
            self.assertIsInstance(keystore.for_platform("my-crm-agent"), keystore.UnsupportedKeyStore)


class FakeLaunchctl:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


@unittest.skipUnless(osutil.IS_MAC, "launchd 는 macOS")
class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()
        self.la = os.path.join(self.th.root, "LaunchAgents")
        self.lc = FakeLaunchctl()

    def tearDown(self):
        self.th.cleanup()

    def test_plist_content(self):
        r = schedule.install(home=self.th.home, launch_agents_dir=self.la, runner=self.lc)
        self.assertEqual(r, "installed")
        p = schedule.plist_path(self.la)
        with open(p, "rb") as f:
            raw = f.read()
        d = plistlib.loads(raw)
        self.assertEqual(d["Label"], schedule.LABEL)
        self.assertEqual(d["StartInterval"], 3600)
        self.assertFalse(d["RunAtLoad"])
        args = d["ProgramArguments"]
        self.assertTrue(os.path.isabs(args[0]))
        self.assertEqual(args[1:3], ["-m", "agent"])
        self.assertIn("--scheduled", args)
        self.assertEqual(args[args.index("--home") + 1], os.path.abspath(self.th.home))
        self.assertTrue(os.path.isabs(d["WorkingDirectory"]))
        # 토큰·비밀 없음
        for needle in (TOKEN.encode(), b"imp_", b"Bearer", b"secret", b"token"):
            self.assertNotIn(needle, raw)
        self.assertEqual(set(d["EnvironmentVariables"]), {"PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"})
        self.assertEqual(self.lc.calls[-1][:2], ["/bin/launchctl", "bootstrap"])

    def test_install_remove_idempotent(self):
        self.assertEqual(schedule.install(home=self.th.home, launch_agents_dir=self.la, runner=self.lc), "installed")
        n = len(self.lc.calls)
        self.assertEqual(schedule.install(home=self.th.home, launch_agents_dir=self.la, runner=self.lc), "unchanged")
        self.assertEqual(len(self.lc.calls), n)
        self.assertEqual(schedule.remove(launch_agents_dir=self.la, runner=self.lc), "removed")
        self.assertEqual(schedule.remove(launch_agents_dir=self.la, runner=self.lc), "absent")
        self.assertFalse(schedule.installed(self.la))
        # 예약 해제는 원문·대응표를 지우지 않는다
        self.assertTrue(os.path.isdir(self.th.paths.raw))

    def test_bootstrap_failure_reported(self):
        def bad(argv):
            return subprocess.CompletedProcess(argv, 5 if argv[1] == "bootstrap" else 0, "", "")
        with self.assertRaises(schedule.ScheduleError):
            schedule.install(home=self.th.home, launch_agents_dir=self.la, runner=bad)

    def test_windows_refused(self):
        with mock.patch.object(osutil, "IS_MAC", False):
            with self.assertRaises(schedule.ScheduleError):
                schedule.install(home=self.th.home, launch_agents_dir=self.la, runner=self.lc)

    def test_jitter_inside_run_once(self):
        from agent import runner
        from agent.tests.helpers import connected_config, keystore_with_secrets
        connected_config(self.th, "http://127.0.0.1:9")
        slept = []
        runner.run_once(self.th.paths, ks=keystore_with_secrets(), scheduled=True, upload=False,
                        sleep=slept.append)
        self.assertEqual(len(slept), 1)
        self.assertTrue(0 <= slept[0] <= 300)
        slept.clear()
        runner.run_once(self.th.paths, ks=keystore_with_secrets(), scheduled=False, upload=False,
                        sleep=slept.append)
        self.assertEqual(slept, [])

    def test_concurrent_run_blocked_by_lock(self):
        from agent import runner
        from agent.tests.helpers import connected_config, keystore_with_secrets
        connected_config(self.th, "http://127.0.0.1:9")
        with osutil.FileLock(self.th.paths.lock("run")):
            r = runner.run_once(self.th.paths, ks=keystore_with_secrets(), upload=False)
        self.assertEqual(r, {"skipped": "already-running"})


if __name__ == "__main__":
    unittest.main()
