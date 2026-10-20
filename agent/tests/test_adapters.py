"""어댑터 — likes 의미 규칙 · 중복 격리 · 개정 · 금지 필드 유입 차단 · 골격 비활성 · 이직 입력원 차단."""
from __future__ import annotations

import json
import os
import unittest

from agent import adapters, config as _config, runner, store as _store
from agent.adapters import AdapterNotActive, Observation, Quarantine, TransmissionBlocked
from agent.tests.helpers import (NAMESPACE, TempHome, connected_config, keystore_with_secrets, synth_row,
                                 write_jsonl)


def likes_section(path, **over):
    s = {"enabled": True, "input_path": path, "provider_code": "synthnet", "include_kinds": ["kindA"],
         "include_null_kind": False, "include_seed": False, "axes": []}
    s.update(over)
    return s


class LikesSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.th = TempHome()
        self.path = os.path.join(self.th.root, "likes.jsonl")

    def tearDown(self):
        self.th.cleanup()

    def collect(self, rows, **over):
        write_jsonl(self.path, rows)
        return list(adapters.LikesAdapter().collect(likes_section(self.path, **over)))

    def test_default_filter_emits_nothing(self):
        write_jsonl(self.path, [synth_row(1)])
        sec = _config.default_config()["adapters"]["likes"]
        sec.update({"input_path": self.path, "provider_code": "synthnet"})
        self.assertEqual(list(adapters.LikesAdapter().collect(sec)), [])

    def test_occurred_on_never_from_collection_or_post_time(self):
        out = self.collect([synth_row(1)])
        self.assertEqual(len(out), 1)
        ob = out[0]
        self.assertIsInstance(ob, Observation)
        self.assertIsNone(ob.occurred_on)               # 수집 시각≠행동 날짜
        self.assertEqual(ob.observed_on, "2026-10-01")   # 처음 수집한 날은 별도 칸
        self.assertEqual((ob.action, ob.state, ob.provider), ("like", "active", "sns"))
        self.assertEqual(ob.people, [("synthnet", "synth_author_1", "author")])

    def test_filters_kind_seed_null(self):
        rows = [synth_row(1), synth_row(2, kind="kindB"), synth_row(3, seed=True), synth_row(4, kind=None)]
        self.assertEqual([o.dedupe_key for o in self.collect(rows)], ["synthnet:SYNPOST0001"])
        got = self.collect(rows, include_seed=True, include_null_kind=True)
        self.assertEqual(len(got), 3)

    def test_example_config_sends_all_rows(self):
        """설정 예시(include_kinds ["*"]·null·seed 포함) = 분류 태그·첫 실행 표시와 무관하게 전부."""
        rows = [synth_row(1), synth_row(2, kind="kindB"), synth_row(3, seed=True), synth_row(4, kind=None)]
        write_jsonl(self.path, rows)
        sec = dict(_config.LIKES_EXAMPLE, input_path=self.path)
        out = list(adapters.LikesAdapter().collect(sec))
        self.assertEqual(len(out), 4)
        self.assertTrue(all(isinstance(o, Observation) and o.state == "active" for o in out))
        self.assertEqual({o.people[0][0] for o in out}, {"threads"})
        _config.validate_config(_config.example_config())
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "config.example.json"), encoding="utf-8") as f:
            ex = json.load(f)
        self.assertEqual(ex["adapters"]["likes"], _config.LIKES_EXAMPLE)

    def test_viewer_default_port(self):
        self.assertEqual(_config.default_config()["viewer_port"], 8790)

    def test_missing_id_and_bad_json_quarantined(self):
        out = self.collect([synth_row(1, id=""), "{not json", synth_row(2)])
        kinds = [type(o).__name__ for o in out]
        self.assertEqual(sorted(kinds), ["Observation", "Quarantine", "Quarantine"])
        reasons = {o.reason for o in out if isinstance(o, Quarantine)}
        self.assertEqual(reasons, {"no-event-id", "bad-json"})

    def test_ambiguous_duplicate_quarantined(self):
        out = self.collect([synth_row(1), synth_row(1, author="someone_else")])
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Quarantine)
        self.assertEqual(out[0].reason, "ambiguous-duplicate")

    def test_exact_duplicate_merged(self):
        out = self.collect([synth_row(1), synth_row(1, first_seen="2026-10-02")])
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Observation)

    def test_config_required(self):
        with self.assertRaises(adapters.AdapterError):
            list(adapters.LikesAdapter().collect(likes_section(None)))
        with self.assertRaises(adapters.AdapterError):
            list(adapters.LikesAdapter().collect(likes_section("relative/likes.jsonl")))
        with self.assertRaises(adapters.AdapterError):
            list(adapters.LikesAdapter().collect(likes_section(self.path, provider_code=None)))


class SkeletonTest(unittest.TestCase):
    def test_skeletons_cannot_be_enabled(self):
        for name in ("feed", "reactions", "mail", "kakao"):
            a = adapters.get(name)
            a.check_config({"enabled": False})
            with self.assertRaises(AdapterNotActive):
                a.check_config({"enabled": True})
            with self.assertRaises(AdapterNotActive):
                list(a.collect({"enabled": True}))
            self.assertTrue(a.RULES)

    def test_linkedin_moves_blocked(self):
        with self.assertRaises(TransmissionBlocked):
            adapters.get("linkedin_moves")
        cfg = _config.default_config()
        cfg["adapters"]["linkedin_moves"] = {"enabled": True}
        with self.assertRaises(_config.ConfigError):
            _config.validate_config(cfg)

    def test_blocked_adapter_never_creates_pending(self):
        th = TempHome()
        try:
            connected_config(th, "http://127.0.0.1:9")
            with open(th.paths.config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["adapters"]["linkedin_moves"] = {"enabled": True}
            with open(th.paths.config, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            with self.assertRaises(_config.ConfigError):
                runner.run_once(th.paths, ks=keystore_with_secrets(), upload=False)
            self.assertEqual(os.listdir(th.paths.pending), [])
        finally:
            th.cleanup()


class StagingTest(unittest.TestCase):
    """수집 → 변환 → 검증 → 전송 대기(업로드 없이)."""

    def setUp(self):
        self.th = TempHome()
        self.path = os.path.join(self.th.root, "likes.jsonl")
        self.ks = keystore_with_secrets()

    def tearDown(self):
        self.th.cleanup()

    def run_stage(self):
        return runner.run_once(self.th.paths, ks=self.ks, upload=False)

    def pending_bodies(self):
        out = []
        for n in sorted(os.listdir(self.th.paths.pending)):
            with open(os.path.join(self.th.paths.pending, n), "rb") as f:
                out.append(f.read())
        return out

    def test_no_forbidden_values_reach_requests(self):
        rows = [synth_row(i) for i in range(1, 6)]
        rows[0]["parent"] = {"author": "synth_parent_author", "code": "SYNPARENT1",
                             "url": "https://example.invalid/p/SYNPARENT1", "text": "SYNTHETIC-PARENT-TEXT"}
        write_jsonl(self.path, rows)
        connected_config(self.th, "http://127.0.0.1:9", self.path)
        res = self.run_stage()
        self.assertEqual(res["collect"]["staged"], 5)
        blob = b"\n".join(self.pending_bodies())
        for r in rows:
            for k in ("id", "author", "text", "original_url", "topic", "posted_at", "source", "kind"):
                self.assertNotIn(r[k].encode("utf-8"), blob, k)
        for leak in (b"synth_parent_author", b"SYNPARENT1", b"SYNTHETIC-PARENT", b"example.invalid", b"synthnet",
                     NAMESPACE.encode()):
            self.assertNotIn(leak, blob)
        # HMAC 키도 요청에 없다
        self.assertNotIn(self.ks.get(_config.hmac_account(NAMESPACE)).encode(), blob)
        for b in self.pending_bodies():
            o = json.loads(b)
            self.assertIsNone(o["occurred_on"])
            self.assertEqual(o["execution"], "attended")
            self.assertEqual(len(o["people"]), 1)

    def test_same_author_same_hmac_and_rerun_unchanged(self):
        write_jsonl(self.path, [synth_row(1), synth_row(4)])   # 둘 다 synth_author_1
        connected_config(self.th, "http://127.0.0.1:9", self.path)
        self.run_stage()
        a, b = [json.loads(x) for x in self.pending_bodies()]
        self.assertEqual(a["people"][0]["hmac"], b["people"][0]["hmac"])
        self.assertNotEqual(a["original_id"], b["original_id"])
        res = self.run_stage()
        self.assertEqual(res["collect"]["staged"], 0)
        self.assertEqual(res["collect"]["unchanged"], 2)

    def test_disappeared_row_no_retraction(self):
        write_jsonl(self.path, [synth_row(1), synth_row(2)])
        connected_config(self.th, "http://127.0.0.1:9", self.path)
        self.run_stage()
        write_jsonl(self.path, [synth_row(1)])
        res = self.run_stage()
        self.assertEqual(res["collect"]["staged"], 0)
        for b in self.pending_bodies():
            self.assertEqual(json.loads(b)["state"], "active")

    def test_new_revision_after_accept_and_identity_change_quarantine(self):
        write_jsonl(self.path, [synth_row(1)])
        connected_config(self.th, "http://127.0.0.1:9", self.path)
        self.run_stage()
        st = _store.Store(self.th.paths)
        rev = st.pending_revisions()[0]
        st.mark(rev["idempotency_key"], "accepted", result={"import_id": "ie_x", "state": "applied"})
        st.close()
        # 처음 수집한 날이 바뀌면 새 판(부모 = 접수된 판)
        write_jsonl(self.path, [synth_row(1, first_seen="2026-10-05")])
        res = self.run_stage()
        self.assertEqual(res["collect"]["staged"], 1)
        new = json.loads(self.pending_bodies()[0])
        self.assertEqual(new["previous_revision"], rev["revision"])
        self.assertEqual(new["observed_on"], "2026-10-05")
        # 같은 id 인데 작성자가 바뀌면 판별 불가 → 격리, 새 판 없음
        st = _store.Store(self.th.paths)
        st.mark(st.pending_revisions()[0]["idempotency_key"], "accepted", result={"import_id": "ie_y"})
        st.close()
        write_jsonl(self.path, [synth_row(1, author="other_author", first_seen="2026-10-06")])
        res = self.run_stage()
        self.assertEqual(res["collect"]["staged"], 0)
        self.assertEqual(res["collect"]["quarantined"], 1)

    def test_open_revision_waits(self):
        write_jsonl(self.path, [synth_row(1)])
        connected_config(self.th, "http://127.0.0.1:9", self.path)
        self.run_stage()
        write_jsonl(self.path, [synth_row(1, first_seen="2026-10-09")])
        res = self.run_stage()
        self.assertEqual(res["collect"]["waiting"], 1)
        self.assertEqual(len(self.pending_bodies()), 1)

    def test_axes_policy(self):
        write_jsonl(self.path, [synth_row(1)])
        cfg = connected_config(self.th, "http://127.0.0.1:9", self.path)
        cfg["allowed_axes"] = ["i_AxisAAAA"]
        cfg["adapters"]["likes"]["axes"] = ["i_AxisAAAA"]
        _config.save(self.th.paths, cfg)
        self.run_stage()
        self.assertEqual(json.loads(self.pending_bodies()[0])["axes"], ["i_AxisAAAA"])
        cfg["adapters"]["likes"]["axes"] = ["i_AxisBBBB"]
        with self.assertRaises(_config.ConfigError):
            _config.save(self.th.paths, cfg)

    def test_raw_kept_on_pc(self):
        write_jsonl(self.path, [synth_row(1)])
        connected_config(self.th, "http://127.0.0.1:9", self.path)
        self.run_stage()
        files = os.listdir(self.th.paths.raw)
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.th.paths.raw, files[0]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["text"], synth_row(1)["text"])


if __name__ == "__main__":
    unittest.main()
