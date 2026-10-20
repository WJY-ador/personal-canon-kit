"""계약 — JCS · 세 종류 허용/금지 필드 · 중복 키 · 크기 · 날짜 · URL·이메일·경로 차단 · 멱등 키."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest

from agent import contract, jcs
from agent.contract import ContractError

SID = "is_5a5a5a5a5a5a5a5a5a5a5a5a"
OID = "o_" + "1" * 32
REV = "v_" + "2" * 32
REV2 = "v_" + "3" * 32
HM = "a" * 64


def activity(**over):
    o = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": SID, "original_id": OID,
         "revision": REV, "previous_revision": None, "kind": "activity", "execution": "unattended",
         "transform_version": "board-likes/1", "policy_revision": 1, "action": "like", "state": "active",
         "occurred_on": None, "observed_on": "2026-10-01", "provider": "sns", "raw_id": "0" * 48,
         "people": [{"hmac": HM, "role": "author"}], "axes": []}
    o.update(over)
    return o


def self_text(**over):
    o = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": SID, "original_id": OID,
         "revision": REV, "previous_revision": None, "kind": "self_text", "execution": "attended",
         "transform_version": "self-text/1", "policy_revision": 1, "title": "합성 제목", "body": "합성 본문",
         "generation": {"method": "none"}}
    o.update(over)
    return o


def self_summary(**over):
    o = {"contract": "canon-inbox/1", "schema_revision": 2, "source_instance_id": SID, "original_id": OID,
         "revision": REV, "previous_revision": None, "kind": "self_summary", "execution": "unattended",
         "transform_version": "self-summary/1", "policy_revision": 1, "body": "합성 요약",
         "generation": {"method": "ai_summary", "tool": "claude-cli", "model": "unknown",
                        "input_scope": "self_only",
                        "inputs": [{"original_id": "o_" + "4" * 32, "revision": REV2, "sha256": "b" * 64}]}}
    o.update(over)
    return o


class JCSTest(unittest.TestCase):
    def test_sorting_and_spacing(self):
        self.assertEqual(jcs.canonicalize({"b": 1, "a": [True, None, "x"]}), '{"a":[true,null,"x"],"b":1}')

    def test_utf16_key_order(self):
        # U+E000(BMP 사설 영역) 은 UTF-16 에서 서로게이트(U+1F600 = D83D...) 보다 뒤
        s = jcs.canonicalize({"\ue000": 1, "\U0001F600": 2})
        self.assertTrue(s.index("\U0001F600") < s.index("\ue000"))

    def test_escaping(self):
        self.assertEqual(jcs.canonicalize("a\"\\\n\x01é"), '"a\\"\\\\\\n\\u0001é"')

    def test_float_rejected(self):
        with self.assertRaises(jcs.JCSError):
            jcs.canonicalize({"a": 1.5})

    def test_lone_surrogate_rejected(self):
        with self.assertRaises(jcs.JCSError):
            jcs.canonicalize("\ud800")


class ValidKindsTest(unittest.TestCase):
    def test_three_kinds_pass(self):
        for o in (activity(), self_text(), self_summary()):
            contract.validate(o)
            raw = contract.encode_request(o)
            self.assertEqual(contract.validate_bytes(raw), o)

    def test_self_text_optional_fields_absent(self):
        o = self_text()
        del o["title"]
        contract.validate(o)


class ForbiddenFieldsTest(unittest.TestCase):
    def assertCode(self, o, code, field=None):
        with self.assertRaises(ContractError) as cm:
            contract.validate(o)
        self.assertEqual(cm.exception.code, code)
        if field is not None:
            self.assertEqual(cm.exception.field, field)

    def test_activity_has_no_free_text(self):
        for extra in ("title", "body", "memo", "summary", "url", "email", "account", "name", "affiliation",
                      "original_url", "author", "text"):
            self.assertCode(activity(**{extra: "x"}), "UNKNOWN-FIELD", extra)

    def test_self_text_rejects_activity_fields(self):
        self.assertCode(self_text(people=[]), "UNKNOWN-FIELD", "people")
        self.assertCode(self_text(raw_id="0" * 48), "UNKNOWN-FIELD", "raw_id")

    def test_self_summary_rejects_activity_ref(self):
        self.assertCode(self_summary(activity_ref="a_SynthAct01"), "UNKNOWN-FIELD", "activity_ref")

    def test_missing_common_field(self):
        for f in contract.COMMON_FIELDS:
            o = activity()
            del o[f]
            with self.assertRaises(ContractError):
                contract.validate(o)

    def test_missing_activity_field(self):
        for f in contract.KIND_FIELDS["activity"]["required"]:
            o = activity()
            del o[f]
            self.assertCode(o, "MISSING", f)

    def test_enums(self):
        self.assertCode(activity(action="view"), "ENUM", "action")
        self.assertCode(activity(state="deleted"), "ENUM", "state")
        self.assertCode(activity(provider="linkedin"), "ENUM", "provider")
        self.assertCode(activity(execution="auto"), "ENUM", "execution")
        self.assertCode(activity(kind="note"), "ENUM", "kind")
        self.assertCode(activity(people=[{"hmac": HM, "role": "friend"}]), "ENUM", "people[0].role")

    def test_schema_revision_strict(self):
        self.assertCode(activity(schema_revision=1), "SCHEMA-REVISION")
        self.assertCode(activity(schema_revision=True), "SCHEMA-REVISION")
        self.assertCode(activity(contract="canon-inbox/2"), "CONTRACT")

    def test_bool_is_not_int(self):
        self.assertCode(activity(policy_revision=True), "FORMAT", "policy_revision")
        self.assertCode(activity(policy_revision=0), "FORMAT", "policy_revision")

    def test_id_formats(self):
        self.assertCode(activity(original_id="o_https"), "FORMAT", "original_id")
        self.assertCode(activity(original_id="post-123"), "FORMAT", "original_id")
        self.assertCode(activity(revision="v_1"), "FORMAT", "revision")
        self.assertCode(activity(previous_revision=REV), "SELF-PARENT")
        self.assertCode(activity(raw_id="0" * 47), "FORMAT", "raw_id")
        self.assertCode(activity(raw_id="G" * 48), "FORMAT", "raw_id")
        self.assertCode(activity(source_instance_id="device1"), "FORMAT", "source_instance_id")
        self.assertCode(activity(people=[{"hmac": "A" * 64, "role": "author"}]), "FORMAT", "people[0].hmac")

    def test_people_limits(self):
        many = [{"hmac": format(i, "064x"), "role": "author"} for i in range(11)]
        self.assertCode(activity(people=many), "TOO-MANY", "people")
        self.assertCode(activity(people=[{"hmac": HM, "role": "author"}] * 2), "DUPLICATE")
        self.assertCode(activity(people=[{"hmac": HM, "role": "author", "name": "x"}]), "UNKNOWN-FIELD",
                        "people[0].name")

    def test_axes_limits(self):
        self.assertCode(activity(axes=[f"i_Axis{i:04d}" for i in range(11)]), "TOO-MANY", "axes")
        self.assertCode(activity(axes=["i_AxisAAAA", "i_AxisAAAA"]), "DUPLICATE", "axes")
        self.assertCode(activity(axes=[1]), "FORMAT")

    def test_dates(self):
        self.assertCode(activity(occurred_on="2026-02-30"), "DATE", "occurred_on")
        self.assertCode(activity(observed_on="2026/10/01"), "DATE", "observed_on")
        self.assertCode(activity(occurred_on="2026-10-01T00:00:00Z"), "DATE", "occurred_on")
        contract.validate(activity(occurred_on="2026-10-01"))
        self.assertCode(self_text(source_created_at="2026-10-01T01:02:03+09:00", source_date="2026-10-01"),
                        "BOTH-DATES")
        self.assertCode(self_text(source_created_at="2026-10-01T01:02:03"), "DATETIME")   # 시간대 없음
        contract.validate(self_text(source_created_at="2026-10-01T01:02:03Z"))

    def test_generation_rules(self):
        self.assertCode(self_text(generation={"method": "ai_summary"}), "GENERATION")
        self.assertCode(self_text(generation={"method": "none", "model": "x"}), "UNKNOWN-FIELD", "generation.model")
        g = copy.deepcopy(self_summary()["generation"])
        g["input_scope"] = "mixed"
        self.assertCode(self_summary(generation=g), "GENERATION", "generation.input_scope")
        g = copy.deepcopy(self_summary()["generation"])
        g["model"] = "some-free-text-model-name"
        self.assertCode(self_summary(generation=g), "GENERATION", "generation.model")
        g = copy.deepcopy(self_summary()["generation"])
        g["inputs"] = [g["inputs"][0]] * 21
        self.assertCode(self_summary(generation=g), "GENERATION", "generation.inputs")
        g = copy.deepcopy(self_summary()["generation"])
        g["inputs"][0]["path"] = "x"
        self.assertCode(self_summary(generation=g), "SHAPE")

    def test_transform_registry(self):
        self.assertCode(activity(transform_version="board-feed/1"), "UNREGISTERED-TRANSFORM")
        self.assertCode(activity(transform_version="self-summary/1"), "TRANSFORM-KIND")
        self.assertCode(activity(transform_version="x" * 100), "UNREGISTERED-TRANSFORM")


class PatternBlockTest(unittest.TestCase):
    def test_url_email_path_blocked_in_strings(self):
        bad = ["보러 가기 https://example.invalid/p/1", "www.example.com 참고", "example.com/path 참고",
               "연락 someone@example.invalid", "파일 ~" + "/Documents/x.md", "경로 /" + "Users/someone/a",
               "C:" + "\\" + "Users" + "\\x", "\\\\server\\share"]
        for s in bad:
            with self.assertRaises(ContractError, msg=s) as cm:
                contract.validate(self_text(body=s))
            self.assertTrue(cm.exception.code.startswith("BLOCKED-"))
            with self.assertRaises(ContractError):
                contract.validate(self_text(title=s[:200]))

    def test_error_carries_no_value(self):
        try:
            contract.validate(self_text(body="비밀 someone@example.invalid"))
        except ContractError as e:
            self.assertNotIn("someone", str(e))
            self.assertNotIn("비밀", str(e))


class SizeTest(unittest.TestCase):
    def test_body_limits(self):
        contract.validate(self_text(body="가" * 8000))
        with self.assertRaises(ContractError) as cm:
            contract.validate(self_text(body="가" * 8001))
        self.assertEqual(cm.exception.code, "TOO-LONG")

    def test_body_bytes_and_title_limit(self):
        contract.validate(self_text(body="\U0001F600" * 8000))   # 8,000 코드 포인트 = 32,000 B ≤ 32 KiB
        with self.assertRaises(ContractError) as cm:
            contract.validate(self_text(title="t" * 201))
        self.assertEqual(cm.exception.code, "TOO-LONG")

    def test_activity_size_limit(self):
        # 활동 8 KiB 한도는 필드 한도로 먼저 막히지만, 한도 상수는 계약 값과 같아야 한다
        self.assertEqual(contract.MAX_ACTIVITY_BYTES, 8192)
        self.assertLess(len(contract.encode_request(activity(
            people=[{"hmac": format(i, "064x"), "role": "author"} for i in range(10)],
            axes=[f"i_Axis{i:04d}" for i in range(10)]))), 8192)

    def test_request_limits(self):
        with self.assertRaises(ContractError) as cm:
            contract.parse_request(b" " * (64 * 1024 + 1))
        self.assertEqual(cm.exception.code, "REQUEST-TOO-LARGE")


class ParseTest(unittest.TestCase):
    def test_duplicate_key(self):
        raw = b'{"kind":"activity","kind":"self_text"}'
        with self.assertRaises(ContractError) as cm:
            contract.parse_request(raw)
        self.assertEqual(cm.exception.code, "DUPLICATE-KEY")

    def test_not_object_not_utf8_float_nan(self):
        for raw, code in ((b"[]", "NOT-OBJECT"), (b"\xff\xfe", "NOT-UTF8"), (b'{"a":1.0}', "NON-JSON-NUMBER"),
                          (b'{"a":NaN}', "NON-JSON-NUMBER"), (b"{", "NOT-JSON"),
                          ("\ufeff{}".encode("utf-8"), "BOM")):
            with self.assertRaises(ContractError) as cm:
                contract.parse_request(raw)
            self.assertEqual(cm.exception.code, code, raw)


class PolicyTest(unittest.TestCase):
    def test_observed_on_required_key_null_ok(self):
        o = activity()
        del o["observed_on"]
        with self.assertRaises(ContractError) as cm:
            contract.validate(o)
        self.assertEqual((cm.exception.code, cm.exception.field), ("MISSING", "observed_on"))
        contract.validate(activity(observed_on=None))

    def test_policy_actions_and_transforms(self):
        pol = {"source_instance_id": SID, "policy_revision": 1, "allowed_axes": [], "actions": ["repost"],
               "transforms": ["board-likes/1"]}
        with self.assertRaises(ContractError) as cm:
            contract.validate(activity(), policy=pol)
        self.assertEqual(cm.exception.code, "POLICY-ACTION")
        pol["actions"] = ["like"]
        contract.validate(activity(), policy=pol)
        with self.assertRaises(ContractError) as cm:
            contract.validate(self_text(), policy=pol)
        self.assertEqual(cm.exception.code, "POLICY-TRANSFORM")

    def test_policy(self):
        pol = {"source_instance_id": SID, "policy_revision": 1, "allowed_axes": ["i_AxisAAAA"]}
        contract.validate(activity(axes=["i_AxisAAAA"]), policy=pol)
        for o, code in ((activity(axes=["i_AxisBBBB"]), "POLICY-AXIS"),
                        (activity(policy_revision=2), "POLICY-REVISION"),
                        (activity(source_instance_id="is_6b6b6b6b6b6b6b6b6b6b6b6b"), "POLICY-SOURCE")):
            with self.assertRaises(ContractError) as cm:
                contract.validate(o, policy=pol)
            self.assertEqual(cm.exception.code, code)


class IdempotencyTest(unittest.TestCase):
    def test_key_rule(self):
        k = contract.idempotency_key(SID, OID, REV)
        expect = "ci1_" + hashlib.sha256(
            json.dumps(["canon-inbox/1", 2, SID, OID, REV], separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(k, expect)
        self.assertNotEqual(k, contract.idempotency_key(SID, OID, REV2))
        self.assertRegex(k, contract.IDEMPOTENCY_RE)

    def test_encode_is_deterministic(self):
        a = contract.encode_request(activity())
        b = contract.encode_request(dict(reversed(list(activity().items()))))
        self.assertEqual(a, b)

    def test_token_parse(self):
        tok = "imp_" + SID + ".tok_Synth01." + "S" * 43
        self.assertEqual(contract.parse_token(tok), (SID, "tok_Synth01"))
        for bad in ("Bearer x", "imp_is_x.y.z", tok + " ", "imp_" + SID + ".tok.S"):
            with self.assertRaises(ContractError):
                contract.parse_token(bad)


if __name__ == "__main__":
    unittest.main()
