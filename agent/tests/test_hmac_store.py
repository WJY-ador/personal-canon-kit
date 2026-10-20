"""HMAC 안정성·제공자 구분·키 공간 분리 · 저장소 권한·원자적 대기 파일·ID 1회 발급."""
from __future__ import annotations

import os
import stat
import unittest

from agent import hmacid, osutil, store as _store
from agent.tests.helpers import TempHome

K1 = bytes(range(32))
K2 = bytes(range(1, 33))


class HmacTest(unittest.TestCase):
    def test_stable(self):
        a = hmacid.person_hmac(K1, "synthnet", "synth_author_0")
        self.assertEqual(a, hmacid.person_hmac(K1, "synthnet", "synth_author_0"))
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_provider_separation(self):
        self.assertNotEqual(hmacid.person_hmac(K1, "synthnet", "same"), hmacid.person_hmac(K1, "othernet", "same"))

    def test_key_space_separation(self):
        self.assertNotEqual(hmacid.person_hmac(K1, "synthnet", "same"), hmacid.person_hmac(K2, "synthnet", "same"))

    def test_jcs_input_no_ambiguity(self):
        # 구분자 충돌 없음: ("a:b","c") 와 ("a","b:c") 는 다른 입력
        self.assertNotEqual(hmacid.person_hmac(K1, "synthnet", "a\",\"b"), hmacid.person_hmac(K1, "synthnet", "a"))

    def test_key_rules(self):
        with self.assertRaises(hmacid.HmacError):
            hmacid.person_hmac(b"short", "synthnet", "x")
        with self.assertRaises(hmacid.HmacError):
            hmacid.key_from_hex("00" * 16)
        with self.assertRaises(hmacid.HmacError):
            hmacid.person_hmac(K1, "Bad Provider", "x")
        with self.assertRaises(hmacid.HmacError):
            hmacid.person_hmac(K1, "synthnet", "  ")
        self.assertEqual(len(hmacid.key_from_hex(hmacid.new_key_hex())), 32)

    def test_mapping_keeps_namespace(self):
        th = TempHome()
        try:
            st = _store.Store(th.paths)
            h1 = st.person_hmac("hn_SpaceOne01", "synthnet", "acct", lambda: hmacid.person_hmac(K1, "synthnet", "acct"))
            h2 = st.person_hmac("hn_SpaceTwo02", "synthnet", "acct", lambda: hmacid.person_hmac(K2, "synthnet", "acct"))
            self.assertNotEqual(h1, h2)
            # 같은 키 공간은 계산 없이 대응표 값 재사용
            h1b = st.person_hmac("hn_SpaceOne01", "synthnet", "acct", lambda: self.fail("recomputed"))
            self.assertEqual(h1, h1b)
            st.close()
        finally:
            th.cleanup()


@unittest.skipIf(osutil.IS_WINDOWS, "POSIX 권한")
class PermissionTest(unittest.TestCase):
    def test_dirs_and_files_private(self):
        th = TempHome()
        try:
            st = _store.Store(th.paths)
            st.event_create("likes", "k1", "activity", "fp", raw_obj={"text": "synthetic"})
            ev = st.event_get("likes", "k1")
            st.add_revision(original_id=ev["original_id"], revision=_store.new_revision(), previous_revision=None,
                            projection_sha="p", idem_key="ci1_" + "a" * 64, body=b"{}")
            st.close()
            for d in (th.paths.home, th.paths.raw, th.paths.pending, th.paths.locks, th.paths.logs):
                self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700, d)
            for f in (th.paths.db, os.path.join(th.paths.raw, ev["raw_path"]),
                      os.path.join(th.paths.pending, "ci1_" + "a" * 64 + ".json")):
                self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600, f)
        finally:
            th.cleanup()


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()
        self.st = _store.Store(self.th.paths)

    def tearDown(self):
        self.st.close()
        self.th.cleanup()

    def test_ids_issued_once_random(self):
        e1 = self.st.event_create("likes", "synthnet:P1", "activity", "fp", raw_obj={"a": 1})
        self.assertRegex(e1["original_id"], r"^o_[0-9a-f]{32}$")
        self.assertRegex(e1["raw_id"], r"^[0-9a-f]{48}$")
        self.assertNotIn("P1", e1["original_id"])
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.st.event_create("likes", "synthnet:P1", "activity", "fp", raw_obj={"a": 1})
        self.assertEqual(self.st.event_get("likes", "synthnet:P1")["original_id"], e1["original_id"])
        # 실패한 두 번째 발급은 원문 파일을 남기지 않는다
        self.assertEqual(len(os.listdir(self.th.paths.raw)), 1)

    def test_atomic_pending_and_failure_rollback(self):
        e = self.st.event_create("likes", "k", "activity", "fp")
        key = "ci1_" + "b" * 64
        orig = osutil.atomic_write

        def boom(path, data):
            raise OSError("disk full")
        _store.osutil.atomic_write = boom
        try:
            with self.assertRaises(OSError):
                self.st.add_revision(original_id=e["original_id"], revision=_store.new_revision(),
                                     previous_revision=None, projection_sha="p", idem_key=key, body=b"{}")
        finally:
            _store.osutil.atomic_write = orig
        self.assertEqual(self.st.pending_revisions(), [])
        self.assertFalse(os.path.exists(self.st.pending_file(key)))
        leftovers = [n for n in os.listdir(self.th.paths.pending)]
        self.assertEqual(leftovers, [])

    def test_reconcile_removes_orphans(self):
        orphan = os.path.join(self.th.paths.pending, "ci1_" + "c" * 64 + ".json")
        with open(orphan, "wb") as f:
            f.write(b"{}")
        self.assertEqual(self.st.reconcile(), 1)
        self.assertFalse(os.path.exists(orphan))

    def test_pending_tamper_detected(self):
        e = self.st.event_create("likes", "k", "activity", "fp")
        key = "ci1_" + "d" * 64
        self.st.add_revision(original_id=e["original_id"], revision=_store.new_revision(), previous_revision=None,
                             projection_sha="p", idem_key=key, body=b'{"a":1}')
        with open(self.st.pending_file(key), "wb") as f:
            f.write(b'{"a":2}')
        with self.assertRaises(_store.StoreError):
            self.st.read_pending(self.st.pending_revisions()[0])

    def test_quarantine_keeps_only_digest(self):
        self.st.quarantine("likes", "bad-json", "SYNTHETIC-SECRET-LINE")
        rows = self.st.conn.execute("SELECT * FROM quarantine").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("SYNTHETIC", " ".join(str(x) for x in tuple(rows[0])))
        for name in os.listdir(self.th.paths.home):
            if name.startswith("state.sqlite"):
                with open(os.path.join(self.th.paths.home, name), "rb") as f:
                    self.assertNotIn(b"SYNTHETIC-SECRET-LINE", f.read())


class LockTest(unittest.TestCase):
    def test_second_lock_busy(self):
        th = TempHome()
        try:
            p = th.paths.lock("run")
            with osutil.FileLock(p):
                with self.assertRaises(osutil.LockBusy):
                    osutil.FileLock(p).acquire()
            osutil.FileLock(p).acquire().release()
        finally:
            th.cleanup()


if __name__ == "__main__":
    unittest.main()
