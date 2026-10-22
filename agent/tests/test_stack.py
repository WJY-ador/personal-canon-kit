"""쌓기 단계(PR ②-b B4) PC 쪽 — agent/stack.py 계약 검증 + jobs.py 연결(정의 스냅샷·재시도·세션 직렬화).

전부 합성 자료. 실 claude CLI·운영 서버·외부 모델 없음. 모델 = tests/fake_claude.py(FAKE_JOB=1 + FAKE_STACK 시나리오).
스텁 서버 = test_jobs.StubJobsServer(definitions 경로·stack/1 봉투 대조 포함).
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import unittest

from agent import jobs, stack
from agent.tests.helpers import TOKEN, TempHome
from agent.tests.test_jobs import (DEFAULT_DEFS, DEFS_V, LEASE_SEC, SECRET_TEXT, TOKEN_B, CountingRunner,
                                   StubJobsServer, VClock)

DAY = "2026-10-22"
DEFS = stack.Definitions(DEFAULT_DEFS)


def body(**over):
    b = {"units": [{"uid": "u1", "text": "정리 한 문단", "change_ids": ["c1"]}],
         "changes": [{"cid": "c1", "op": "create", "object_id": "obj-judgment", "target": None,
                      "fields": {"text": "판단 문장"}, "links": [], "depends_on": []}],
         "blocked": []}
    b.update(over)
    return b


class StdinRunner(CountingRunner):
    """argv·표준 입력·시작/끝 시각 기록."""

    def __init__(self, hook=None):
        super().__init__(hook)
        self.stdins, self.spans = [], []

    def __call__(self, argv, *, stdin_bytes, env, cwd, timeout):
        t0 = time.monotonic()
        with self.lock:
            self.stdins.append(stdin_bytes.decode("utf-8"))
        try:
            return super().__call__(argv, stdin_bytes=stdin_bytes, env=env, cwd=cwd, timeout=timeout)
        finally:
            with self.lock:
                self.spans.append((t0, time.monotonic()))


# ── 계약 검증(단위) ─────────────────────────────────────────

class ValidateTest(unittest.TestCase):
    def bad(self, b, code):
        with self.assertRaises(stack.ContractError) as cm:
            stack.validate(b, DEFS)
        self.assertEqual(cm.exception.code, code)

    def test_ok_normalized(self):
        out = stack.validate(body(), DEFS)
        self.assertEqual(out["changes"][0]["object_id"], "obj-judgment")
        self.assertEqual(out["changes"][0]["target"], {"ref_kind": "none"})    # create = 대상 없음(서버 꼴)
        for t in (None, {}, {"ref_kind": "none"}):
            b = body()
            b["changes"][0]["target"] = t
            self.assertEqual(stack.validate(b, DEFS)["changes"][0]["target"], {"ref_kind": "none"})
        self.assertEqual(stack.joined_text(out["units"]), "정리 한 문단")

    def test_ref_kinds(self):
        for t in ({"ref_kind": "source_id", "scheme": "code", "value": "J-1"},
                  {"ref_kind": "explicit_id", "value": "item-1"},
                  {"ref_kind": "inferred", "value": "item-2"},
                  {"ref_kind": "unresolved"}):
            with self.subTest(t=t["ref_kind"]):
                b = body()
                b["changes"][0].update(op="update", target=t)
                self.assertEqual(stack.validate(b, DEFS)["changes"][0]["target"], t)

    def test_ref_rejects(self):
        for t in ({"ref_kind": "source_id", "scheme": "unknown-scheme", "value": "B-1"},   # 정의에 없는 원천 체계
                  {"ref_kind": "source_id", "value": "B-1"},
                  {"ref_kind": "same_date", "value": "2026-10-22"},                       # 날짜 연결 = 거부
                  {"value": "item-1"},                                                     # ref_kind 없음
                  {"ref_kind": "unresolved", "value": "x"},
                  {"ref_kind": "explicit_id", "scheme": "code", "value": "x"},
                  {"ref_kind": "none"},                                                    # update 에 none
                  None):
            with self.subTest(t=t):
                b = body()
                b["changes"][0].update(op="update", target=t)
                self.bad(b, "ref")
        b = body()
        b["changes"][0]["target"] = {"ref_kind": "explicit_id", "value": "item-1"}      # create 에 대상
        self.bad(b, "ref")

    def test_links_need_ref_kind(self):
        b = body()
        b["changes"][0]["links"] = [{"relation": "axes", "target": {"value": "axis-1"}}]
        self.bad(b, "ref")
        b["changes"][0]["links"] = [{"relation": "axes", "target": {"ref_kind": "similar_text", "value": "a"}}]
        self.bad(b, "ref")
        b["changes"][0]["links"] = [{"relation": "axes", "target": {"ref_kind": "explicit_id", "value": "axis-1"}}]
        self.assertEqual(len(stack.validate(b, DEFS)["changes"][0]["links"]), 1)
        self.assertEqual(stack.validate(b, DEFS)["changes"][0]["links"][0]["target"]["target_kind"], "classify")
        b["changes"][0]["links"] = [{"relation": "axes", "target": {"ref_kind": "source_id", "scheme": "code",
                                                                     "value": "AX-1"}}]
        stack.validate(b, DEFS)                                                # 축 = 정의 안 대상, 원천 ID 가능
        b["changes"][0]["links"] = [{"relation": "evidence", "target": {"ref_kind": "explicit_id", "value": "r"}}]
        self.bad(b, "schema")                                                  # 정의에 없는 관계

    def test_reference_field_via_fields_rejected(self):
        """연결 칸(axes·people)을 fields 로 넣으면 ref_kind 검사를 피한다 — 거부, links 로만."""
        for k, v in (("axes", ["AX-1"]), ("people", ["p-1"])):
            b = body()
            b["changes"][0]["fields"][k] = v
            with self.subTest(k=k):
                self.bad(b, "ref")

    def test_external_target_explicit_only(self):
        """사람·출처(정의 밖, target_kind) 연결은 explicit_id 만. target_kind 는 관계와 같아야."""
        b = body()
        b["changes"][0]["links"] = [{"relation": "people", "target": {"ref_kind": "explicit_id", "value": "p-1"}}]
        self.assertEqual(stack.validate(b, DEFS)["changes"][0]["links"][0]["target"],
                         {"ref_kind": "explicit_id", "target_kind": "person", "value": "p-1"})
        for t in ({"ref_kind": "inferred", "value": "p-1"}, {"ref_kind": "unresolved"},
                  {"ref_kind": "source_id", "scheme": "code", "value": "p"},
                  {"ref_kind": "explicit_id", "target_kind": "source", "value": "p-1"}):
            b["changes"][0]["links"] = [{"relation": "people", "target": t}]
            with self.subTest(t=t):
                self.bad(b, "ref")

    def test_every_cid_in_exactly_one_unit(self):
        b = body()
        b["units"][0]["change_ids"] = []
        self.bad(b, "unit")                                                    # 정리 글에 안 보이는 변경
        b = body()
        b["units"].append({"uid": "u2", "text": "둘째", "change_ids": ["c1"]})
        self.bad(b, "unit")                                                    # 두 문단에 같은 변경
        b = body()
        b["units"].append({"uid": "u2", "text": "변경 없는 문단", "change_ids": []})
        stack.validate(b, DEFS)                                                # unit 은 변경 0개 가능

    def test_leak(self):
        for mut in (lambda b: b["units"][0].update(text="참고 https://example.com/a"),
                    lambda b: b["units"][0].update(text="메일 someone@example.com 로"),
                    lambda b: b["changes"][0]["fields"].update(text="파일 /Users/me/x.md 참고"),
                    lambda b: b["changes"][0]["fields"].update(status="open", text="www.example.com 보기")):
            b = body()
            mut(b)
            with self.subTest():
                self.bad(b, "leak")

    def test_forbidden(self):
        for mut in (lambda b: b["changes"][0].update(stage="confirmed"),
                    lambda b: b["changes"][0].update(decision="confirmed"),
                    lambda b: b["changes"][0]["fields"].update(confirmed=True),
                    lambda b: b["changes"][0]["fields"].update(code="J-1"),         # 보호 필드
                    lambda b: b.update(citation=True),
                    lambda b: b.update(definitions_version="x"),                   # 봉투 값은 모델이 못 낸다
                    lambda b: b["changes"][0].update(links=[{"relation": "axes", "quota": 1,
                                                             "target": {"ref_kind": "unresolved"}}])):
            b = body()
            mut(b)
            with self.subTest():
                self.bad(b, "forbidden")

    def test_schema(self):
        cases = [
            lambda b: b["changes"][0].update(op="delete"),
            lambda b: b["changes"][0].update(object_id="obj-nope"),
            lambda b: b["changes"][0]["fields"].update(nope="x"),
            lambda b: b["changes"][0]["fields"].update(status="maybe"),            # enum 밖
            lambda b: b["changes"].append(dict(b["changes"][0])),                   # cid 중복
            lambda b: b["units"].append({"uid": "u1", "text": "둘째", "change_ids": []}),   # uid 중복
            lambda b: b["units"][0].update(change_ids=["c9"]),
            lambda b: b["changes"][0].update(depends_on=["c9"]),
            lambda b: b["changes"][0].update(depends_on=["c1"]),                     # 자기 의존
            lambda b: b.update(blocked=[{"cid": "c9", "code": "ambiguous", "question": "?"}]),
            lambda b: b.update(blocked=[{"cid": None, "code": "please_confirm", "question": "?"}]),
            lambda b: b.update(units=[]),
            lambda b: b["units"][0].update(text="  "),
            lambda b: b["changes"][0].update(extra=1),
        ]
        for i, mut in enumerate(cases):
            b = copy.deepcopy(body())
            mut(b)
            with self.subTest(i=i):
                self.bad(b, "schema")

    def test_cycle(self):
        b = body()
        b["changes"] = [
            {"cid": "a", "op": "create", "object_id": "obj-axis", "fields": {"name": "x"}, "depends_on": ["c"]},
            {"cid": "b", "op": "create", "object_id": "obj-axis", "fields": {"name": "y"}, "depends_on": ["a"]},
            {"cid": "c", "op": "create", "object_id": "obj-axis", "fields": {"name": "z"}, "depends_on": ["b"]}]
        b["units"][0]["change_ids"] = ["a", "b", "c"]
        self.bad(b, "cycle")
        b["changes"][0]["depends_on"] = []
        self.assertEqual(len(stack.validate(b, DEFS)["changes"]), 3)            # 사슬은 허용

    def test_limits(self):
        b = body()
        b["units"] = [{"uid": "u%d" % i, "text": "t", "change_ids": []} for i in range(stack.MAX_UNITS + 1)]
        self.bad(b, "limit")
        b = body()
        b["changes"] = [{"cid": "c%d" % i, "op": "create", "object_id": "obj-axis", "fields": {"name": "n"}}
                        for i in range(stack.MAX_CHANGES + 1)]
        b["units"][0]["change_ids"] = []
        self.bad(b, "limit")
        b = body()
        b["units"] = [{"uid": "u1", "text": "가" * 2000, "change_ids": ["c1"]},
                      {"uid": "u2", "text": "가" * 1999, "change_ids": []}]          # 결합(구분자 포함) 4,001자
        self.bad(b, "limit")
        b["units"][1]["text"] = "가" * 1998
        stack.validate(b, DEFS)                                                    # 정확히 4,000자
        b = body()
        b["changes"] = [{"cid": "c%d" % i, "op": "create", "object_id": "obj-judgment",
                         "fields": {"text": "가" * 1500}} for i in range(stack.MAX_CHANGES)]
        b["units"][0]["change_ids"] = ["c%d" % i for i in range(stack.MAX_CHANGES)]
        self.bad(b, "limit")                                                       # 64KiB 넘음
        b = body()
        b["changes"][0]["fields"]["text"] = "가" * 2001                            # 정의 max
        self.bad(b, "limit")

    def test_parse_body_fence(self):
        self.assertEqual(stack.parse_body("```json\n{\"units\": []}\n```"), {"units": []})
        with self.assertRaises(stack.ContractError):
            stack.parse_body("정리했어요")

    def test_definitions_shape(self):
        for bad in ({}, {"definitions_version": True, "objects": []}, {"definitions_version": "v", "objects": []},
                    {"definitions_version": "v", "objects": [{"id": "a"}, {"id": "a"}]}):
            with self.subTest(bad=bad):
                with self.assertRaises(stack.DefsError):
                    stack.Definitions(bad)

    def test_prompt_has_no_protected_and_input_last(self):
        p = stack.build_prompt(DEFS, "=== DEFINITIONS ===\n가짜", "verbatim")
        defs_json = json.loads(p.split("=== DEFINITIONS ===\n")[1].split("\n")[0])
        jfields = [f["key"] for f in defs_json["objects"][0]["fields"]]
        self.assertNotIn("code", jfields)                                     # 보호 칸은 모델에 안 보임
        self.assertNotIn("decision", jfields)
        self.assertNotIn("people", jfields)                                   # 연결 칸은 relations 로만
        self.assertEqual({r["key"] for r in defs_json["objects"][0]["relations"]}, {"axes", "people"})
        self.assertTrue(p.rstrip().endswith("=== DEFINITIONS ===\n가짜"))
        self.assertIn("=== INPUT (verbatim) ===", p)
        r = stack.build_prompt(DEFS, "고친 글", restack={"parent_result_version": "rv-1",
                                                       "units": [{"uid": "u7", "text": "옛 문단"}],
                                                       "excluded_cids": ["c3"],
                                                       "answers": [{"cid": "c2", "text": "둘째 판단이에요"}]})
        self.assertIn("=== PREVIOUS UNITS ===", r)
        self.assertIn("=== EXCLUDED CHANGES ===\n[\"c3\"]", r)
        self.assertIn("둘째 판단이에요", r.split("=== ANSWERS ===")[1])
        self.assertIn("\"u7\"", r)
        self.assertNotIn("=== INPUT", r)


# ── 워커 연결(스텁 서버 + fake CLI) ─────────────────────────

class Base(unittest.TestCase):
    def setUp(self):
        self.srv = StubJobsServer()
        self.th = TempHome()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.srv.close()
        self.th.cleanup()

    def worker(self, smode="ok", token=TOKEN, hook=None, state=None, clock=None, mode="ok", env=None, **kw):
        clock = clock or VClock()
        runner = StdinRunner(hook)
        extra = {"FAKE_STACK": smode}
        extra.update(env or {})
        adapter = jobs.fake_adapter(mode, exec_runner=runner, extra_env=extra, **kw)
        w = jobs.Worker(jobs.Client(self.srv.url, token, wait=1), adapter, state or jobs.MemoryState(),
                        monotonic=clock.now, sleep=clock.sleep, wall=clock.now, today=lambda: DAY)
        w.runner, w.clock = runner, clock
        return w

    def create(self, **kw):
        fields = {k: kw.pop(k) for k in ("edit_generation", "parent_result_version", "input_kind", "restack",
                                         "definitions_version") if k in kw}
        jid = self.srv.create(**kw)
        self.srv.jobs[jid].update(fields)
        return jid

    def result(self, jid):
        return self.srv.jobs[jid]["result"]

    def assert_no_secret(self):
        for r in self.srv.log:
            self.assertNotIn(SECRET_TEXT.encode("utf-8"), r["raw"])


class ScenarioTest(Base):
    def test_ok_envelope_and_body(self):
        jid = self.create(session_ref="sess-a")
        w = self.worker()
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["cli_attempts"]), ("result-sent", 1))
        res = self.result(jid)
        self.assertEqual(set(res), {"lease_generation", "envelope", "units", "changes", "blocked", "model_meta"})
        self.assertEqual(res["envelope"], {"contract": "stack/1", "job_id": jid, "input_version": 1,
                                           "definitions_version": DEFS_V, "result_version":
                                               res["envelope"]["result_version"],
                                           "edit_generation": 0, "parent_result_version": None})
        self.assertEqual(set(res["model_meta"]), {"tool", "model", "duration_ms", "cli_attempts"})
        self.assertNotIn("fake-session", json.dumps(res))                      # CLI 세션 ID 는 PC 에만
        self.assertTrue(res["units"][0]["text"].startswith("정리: "))
        self.assertEqual(self.srv.jobs[jid]["defs_calls"], 1)
        argv = w.runner.calls[0]
        self.assertEqual(argv[argv.index("-p") + 1], stack.ARGV_PROMPT)        # 정의·입력은 argv 에 없다
        self.assertIn("=== INPUT (converse) ===", w.runner.stdins[0])
        self.assertIn(SECRET_TEXT, w.runner.stdins[0])
        self.assertEqual(w.state.leases(), {})
        self.assert_no_secret()

    def _contract_fail(self, smode, code):
        jid = self.create(session_ref="sess-a")
        w = self.worker(smode)
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["reason_code"], out["contract"]), ("failed", "result-shape", code))
        self.assertEqual(len(w.runner.calls), 2)                              # 실제 CLI 재시도 1
        self.assertEqual(out["cli_attempts"], 2)
        self.assertEqual(w.state.cli_retries(DAY), 1)
        self.assertEqual(w.state.attempts(DAY), 1)                            # 작업당 시도 1(재시도와 분리)
        self.assertEqual(self.srv.jobs[jid]["attempts"], 1)                   # 서버 attempts = 입력 조회 1
        self.assertEqual(self.srv.requests("result"), [])
        fails = self.srv.requests("fail")
        self.assertEqual([f["body"] for f in fails], [{"lease_generation": 1, "reason_code": "result-shape"}])
        self.assertEqual(self.srv.jobs[jid]["defs_calls"], 1)                 # 재시도에도 정의 재조회 없음
        self.assertIsNone(w.state.session_get("sess-a"))                       # 실패 실행의 세션 ID 는 안 남김
        self.assert_no_secret()

    def test_contract_bad(self):
        self._contract_fail("contract-bad", "schema")

    def test_forbidden_field(self):
        self._contract_fail("forbidden-field", "forbidden")

    def test_unref_link(self):
        self._contract_fail("unref-link", "ref")

    def test_cycle(self):
        self._contract_fail("cycle", "cycle")

    def test_oversize(self):
        self._contract_fail("oversize", "limit")

    def test_garbage_retried_once(self):
        self.create()
        w = self.worker(mode="garbage")
        out = w.poll_once()
        self.assertEqual((out["reason_code"], len(w.runner.calls)), ("output-not-json", 2))

    def test_inferred_passes_as_proposal(self):
        jid = self.create()
        out = self.worker("inferred").poll_once()
        self.assertEqual(out["outcome"], "result-sent")
        c2 = [c for c in self.result(jid)["changes"] if c["cid"] == "c2"][0]
        self.assertEqual(c2["target"], {"ref_kind": "inferred", "value": "item-guess-01"})

    def test_blocked(self):
        jid = self.create()
        self.assertEqual(self.worker("blocked").poll_once()["outcome"], "result-sent")
        res = self.result(jid)
        self.assertEqual(res["blocked"], [{"cid": "c2", "code": "unresolved_target", "question": "어느 항목을 고칠까요?"}])
        self.assertEqual([c["target"] for c in res["changes"] if c["cid"] == "c2"], [{"ref_kind": "unresolved"}])

    def test_retry_then_ok_same_resume(self):
        self.create(session_ref="sess-a")
        w = self.worker()
        w.poll_once()                                                          # 세션 ID 확보
        counter = os.path.join(self.tmp, "n")
        jid = self.create(session_ref="sess-a")
        w.adapter.extra_env.update(FAKE_STACK="contract-bad,ok", FAKE_COUNTER=counter)
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["cli_attempts"]), ("result-sent", 2))
        self.assertEqual(self.result(jid)["model_meta"]["cli_attempts"], 2)
        self.assertEqual(w.runner.calls[1][-2:], ["--resume", "fake-session-0001"])
        self.assertEqual(w.runner.calls[2][-2:], ["--resume", "fake-session-0001"])   # 재시도도 같은 resume
        self.assertEqual((w.state.attempts(DAY), w.state.cli_retries(DAY)), (2, 1))

    def test_no_retry_when_lease_short(self):
        self.create()
        holder = {}
        w = self.worker("contract-bad", hook=lambda: holder["w"].clock.sleep(LEASE_SEC - 60))
        holder["w"] = w
        out = w.poll_once()
        self.assertEqual((out["reason_code"], len(w.runner.calls)), ("result-shape", 1))
        self.assertEqual(w.state.cli_retries(DAY), 0)

    def test_login_not_retried(self):
        self.create()
        w = self.worker(mode="login")
        out = w.poll_once()
        self.assertEqual((out["reason_code"], len(w.runner.calls)), ("login", 1))

    def test_verbatim_input_kind(self):
        jid = self.create(input_kind="verbatim")
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        self.assertIn("=== INPUT (verbatim) ===", w.runner.stdins[0])
        self.assertIsNotNone(self.result(jid))

    def test_bad_input_kind_not_run(self):
        self.create(input_kind="poem")
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "input-error")
        self.assertEqual(w.runner.calls, [])


class RestackTest(Base):
    def test_restack_envelope_and_prompt(self):
        prev = {"parent_result_version": "rv-parent-01", "units": [{"uid": "u7", "text": "옛 문단 OLD-UNIT-TEXT",
                                                                    "change_ids": ["c1", "c3"]}],
                "excluded_cids": ["c3"], "answers": [{"cid": None, "text": "답 ANSWER-TEXT"}]}
        jid = self.create(session_ref="sess-r", edit_generation=2, parent_result_version="rv-parent-01",
                          restack=prev, text="고친 글 EDITED-TEXT")
        w = self.worker("restack")
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        res = self.result(jid)
        self.assertEqual((res["envelope"]["edit_generation"], res["envelope"]["parent_result_version"]),
                         (2, "rv-parent-01"))
        self.assertEqual(res["units"][0]["uid"], "u7")                         # 바뀌지 않은 단위 uid 유지
        sin = w.runner.stdins[0]
        self.assertIn("=== PREVIOUS UNITS ===", sin)
        self.assertIn("OLD-UNIT-TEXT", sin)
        self.assertIn("=== EXCLUDED CHANGES ===\n[\"c3\"]", sin)
        self.assertIn("ANSWER-TEXT", sin.split("=== ANSWERS ===")[1])
        self.assertIn("=== EDITED TEXT (converse) ===\n고친 글 EDITED-TEXT", sin)
        self.assertNotIn("재정리", sin.split("=== EDITED TEXT")[1])             # 본문에 표시 문자열을 붙이지 않음

    def test_restack_bad_shape_not_run(self):
        w = self.worker("restack")
        for bad in ({"parent_result_version": "rv", "units": "not-a-list"},
                    {"parent_result_version": "rv", "units": [], "excluded_cids": "c1"},
                    {"parent_result_version": "rv", "units": [], "answers": [{"cid": 1, "text": "x"}]},
                    {"parent_result_version": "rv", "units": [], "other": 1}):
            with self.subTest(bad=bad):
                self.create(restack=bad)
                self.assertEqual(w.poll_once()["outcome"], "input-error")
        self.assertEqual(w.runner.calls, [])

    def test_restack_order_reversal(self):
        """편집 세대 2 결과가 먼저 들어간 뒤 늦은 세대 1 결과 — 서버 409, PC 는 재전송·재실행 없음."""
        unit = [{"uid": "u1", "text": "옛"}]
        j2 = self.create(session_ref="sess-r", edit_generation=2, restack={"parent_result_version": "rv1",
                                                                           "units": unit})
        j1 = self.create(session_ref="sess-r", edit_generation=1, restack={"parent_result_version": "rv0",
                                                                           "units": unit})
        w = self.worker("restack")
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("version-mismatch", "JOB-VERSION"))
        self.assertEqual(len([r for r in self.srv.requests("result") if r["path"].split("/")[4] == j1]), 1)
        self.assertIsNone(self.result(j1))
        self.assertEqual(self.result(j2)["envelope"]["edit_generation"], 2)
        self.assertEqual(len(w.runner.calls), 2)


class DefsSnapshotTest(Base):
    def test_snapshot_fixed_per_job(self):
        j1 = self.create()
        w = self.worker()
        w.poll_once()
        changed = copy.deepcopy(DEFAULT_DEFS)
        changed["definitions_version"] = "4b" + "c" * 62
        self.srv.defs = changed                                                # 정의 변경 — 이미 발급된 job 은 그대로
        st, _h, p = jobs.Client(self.srv.url, TOKEN, wait=1).definitions(j1, 1)
        self.assertEqual(st, 409)                                              # 끝난 job 은 다시 못 받음
        j2 = self.create()
        w.poll_once()
        self.assertEqual(self.result(j1)["envelope"]["definitions_version"], DEFS_V)
        self.assertEqual(self.result(j2)["envelope"]["definitions_version"], "4b" + "c" * 62)

    def test_server_defs_changed_on_result(self):
        """결과 수신 때 서버 정의가 바뀌었으면 409 JOB-DEFS — 버리고 재전송·재실행 없음(재정리는 화면 몫)."""
        jid = self.create()
        self.srv.route_script[("result", jid)] = [(409, {"code": "JOB-DEFS"}, None)]
        w = self.worker()
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("defs-changed", "JOB-DEFS"))
        self.assertEqual((len(self.srv.requests("result")), len(w.runner.calls)), (1, 1))

    def test_poll_version_must_match_snapshot(self):
        jid = self.create(definitions_version=DEFS_V)
        other = copy.deepcopy(DEFAULT_DEFS)
        other["definitions_version"] = "defs-other"
        self.srv.route_script[("definitions", jid)] = [(200, other, None)]
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "defs-mismatch")
        self.assertEqual(w.runner.calls, [])
        self.assertEqual(self.srv.requests("result") + self.srv.requests("fail"), [])

    def test_refetch_after_drop_same_value_only(self):
        """정의 조회가 끊겨 두 번 조회하면 — 폴에서 받은 값과 같을 때만 수용."""
        jid = self.create(definitions_version=DEFS_V)
        self.srv.drop[("definitions", jid)] = {"n": 1, "apply": False}
        self.srv.jobs[jid]["defs_snapshot"] = DEFAULT_DEFS
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        self.assertEqual(len(self.srv.requests("definitions")), 2)
        jid2 = self.create(definitions_version=DEFS_V)
        other = copy.deepcopy(DEFAULT_DEFS)
        other["definitions_version"] = "defs-v9"
        self.srv.route_script[("definitions", jid2)] = [(503, {"code": "X"}, None), (200, other, None)]
        self.assertEqual(w.poll_once()["outcome"], "defs-mismatch")
        self.assertEqual(len(w.runner.calls), 1)

    def test_defs_invalid_or_gone(self):
        jid = self.create()
        self.srv.route_script[("definitions", jid)] = [(200, {"definitions_version": "v", "objects": []}, None)]
        w = self.worker()
        self.assertEqual(w.poll_once()["outcome"], "defs-invalid")
        jid2 = self.create()
        self.srv.route_script[("definitions", jid2)] = [(410, {"code": "JOB-GONE"}, None)]
        self.assertEqual(w.poll_once()["outcome"], "gone")
        self.assertEqual(w.runner.calls, [])


class SessionSerialTest(Base):
    def test_concurrent_resume_serialized(self):
        """같은 세션 두 job 을 두 워커(같은 홈 상태)가 동시에 — 두 번째는 앞 CLI 가 끝난 뒤 그 세션 ID 로 resume."""
        state = jobs.MemoryState()
        started = threading.Event()

        def slow():
            started.set()
            time.sleep(1.0)
        ra, rb = StdinRunner(slow), StdinRunner()
        wa = jobs.Worker(jobs.Client(self.srv.url, TOKEN, wait=1),
                         jobs.fake_adapter("ok", exec_runner=ra, extra_env={"FAKE_STACK": "ok"}), state,
                         min_interval=0, session_wait_step=0.05)
        wb = jobs.Worker(jobs.Client(self.srv.url, TOKEN_B, wait=1),
                         jobs.fake_adapter("ok", exec_runner=rb, extra_env={"FAKE_STACK": "ok"}), state,
                         min_interval=0, session_wait_step=0.05)
        self.create(session_ref="sess-c")
        self.create(session_ref="sess-c")
        outs = {}
        ta = threading.Thread(target=lambda: outs.setdefault("a", wa.poll_once()))
        ta.start()
        self.assertTrue(started.wait(10))
        outs["b"] = wb.poll_once()
        ta.join(30)
        self.assertEqual((outs["a"]["outcome"], outs["b"]["outcome"]), ("result-sent", "result-sent"))
        self.assertNotIn("--resume", ra.calls[0])
        self.assertEqual(rb.calls[0][-2:], ["--resume", "fake-session-0001"])
        self.assertGreaterEqual(rb.spans[0][0], ra.spans[0][1])               # B CLI 시작 ≥ A CLI 끝
        self.assertEqual(state.leases(), {})

    def test_other_session_not_blocked(self):
        state = jobs.MemoryState()
        state.session_acquire("0f1e2d3c-aaaa-bbbb-cccc-111122223333", 1, "sess-x", 10_000.0, 1000.0)
        self.create(session_ref="sess-y")
        w = self.worker(state=state)
        self.assertEqual(w.poll_once()["outcome"], "result-sent")

    def test_unclear_end_holds_session(self):
        """CLI 시간 초과 = 종료 불명확: /fail 없이 임대 행 유지 → 같은 세션 다음 job 은 입력도 안 부름 → 만료 뒤 진행."""
        clock = VClock()
        w0 = self.worker(clock=clock)
        self.create(session_ref="sess-u")
        w0.poll_once()                                                         # 세션 ID 확보
        j1 = self.create(session_ref="sess-u")
        w = self.worker(mode="slow", clock=clock, state=w0.state, timeout=0.3, env={"FAKE_SLEEP": "5"})
        out = w.poll_once()
        self.assertEqual(out["outcome"], "unclear-end")
        self.assertEqual(len(w.runner.calls), 1)                               # 재시도 없음
        self.assertEqual(self.srv.requests("fail"), [])
        self.assertEqual(len(self.srv.requests("result")), 1)                  # 앞 job 것만
        lease = w.state.leases()[j1]
        self.assertEqual(lease["session_ref"], "sess-u")
        self.assertEqual(self.srv.status(j1), "running")
        self.srv.tick(LEASE_SEC)
        self.assertEqual(self.srv.status(j1), "needs_check")                   # 서버가 확인 필요로

        j2 = self.create(session_ref="sess-u")
        w2 = self.worker(clock=clock, state=w0.state)
        n_input = len(self.srv.requests("input"))
        out = w2.poll_once()
        self.assertEqual(out["outcome"], "session-busy")
        self.assertEqual(len(self.srv.requests("input")), n_input)             # 입력 조회 안 함
        self.assertEqual(w2.runner.calls, [])
        self.assertEqual(self.srv.status(j2), "leased")

        clock.sleep(LEASE_SEC)                                                 # 앞 임대 만료 지남
        j3 = self.create(session_ref="sess-u")
        self.srv.tick(LEASE_SEC)                                               # j2 는 서버에서 needs_check
        self.assertEqual(w2.poll_once()["outcome"], "result-sent")
        self.assertEqual(w2.runner.calls[0][-2:], ["--resume", "fake-session-0001"])
        self.assertIsNotNone(self.result(j3))
        self.assertNotIn(j1, w2.state.leases())

    def test_wait_then_run_within_lease(self):
        """앞 세션 임대가 이 job 임대 안(최소 실행 시간 남김)에 끝나면 기다렸다 실행."""
        clock = VClock()
        state = jobs.MemoryState()
        state.session_acquire("0f1e2d3c-aaaa-bbbb-cccc-111122223333", 1, "sess-w", clock.now() + 100, clock.now())
        self.create(session_ref="sess-w")
        w = self.worker(clock=clock, state=state)
        t0 = clock.now()
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        self.assertGreaterEqual(clock.now() - t0, 100)

    def test_lease_lost_during_run(self):
        """임대 유실 중 결과 — 409 로 버리고 세션 잠금은 풀린다(CLI 는 끝났음)."""
        jid = self.create(session_ref="sess-l")
        w = self.worker(hook=lambda: self.srv.bump_generation(jid))
        out = w.poll_once()
        self.assertEqual((out["outcome"], out["code"]), ("lease-lost", "JOB-LEASE"))
        self.assertEqual(len(self.srv.requests("result")), 1)
        self.assertEqual(self.srv.requests("fail"), [])
        self.assertEqual(w.state.leases(), {})
        self.assertEqual(w.state.session_get("sess-l"), "fake-session-0001")


class StateFileTest(Base):
    def test_state_file_has_no_body(self):
        st = jobs.JsonState.for_paths(self.th.paths)
        self.create(session_ref="sess-f", text=SECRET_TEXT)
        self.create(session_ref="sess-f", text="두 번째 BODY-TWO", edit_generation=1,
                    restack={"parent_result_version": "rv-1", "units": [{"uid": "u1", "text": "OLD-UNIT-TEXT"}]})
        w = self.worker("blocked", state=st)
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        w.adapter.extra_env["FAKE_STACK"] = "restack"
        self.assertEqual(w.poll_once()["outcome"], "result-sent")
        with open(st.path, "rb") as f:
            data = f.read()
        for s in (SECRET_TEXT, "BODY-TWO", "OLD-UNIT-TEXT", "정리", "어느 항목", "판단", "타인의 말", DEFS_V):
            self.assertNotIn(s.encode("utf-8"), data, s)
        self.assertEqual(set(json.loads(data)), {"v", "sessions", "attempts", "cli_retries", "leases"})


if __name__ == "__main__":
    unittest.main()
