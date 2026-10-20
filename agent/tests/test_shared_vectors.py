"""서버·에이전트 공용 테스트 벡터(server/contract/imports/vectors/canon-inbox-1.json)와 클라이언트 검증기 대조.

벡터 파일이 없는 배포(키트 단독)에서는 건너뛴다. 판정(통과/거절)과 거절 필드가 같아야 한다.
규칙 이름은 서버(snake_case)와 클라이언트(대문자 코드)가 달라 아래 대응표로 비교한다.
대응표에서 클라이언트 코드 하나가 서버 규칙 여럿을 덮는 곳(GENERATION·FORMAT·DATE·ENUM)은 클라이언트가 더 거칠다.
"""
from __future__ import annotations

import json
import os
import unittest

from agent import contract, jcs

VEC = os.path.join(os.path.dirname(__file__), "..", "..", "server", "contract", "imports", "vectors",
                   "canon-inbox-1.json")


# 클라이언트 코드 → 허용되는 서버 규칙
RULE_MAP = {
    "UNKNOWN-FIELD": {"unknown_field"}, "MISSING": {"required"}, "CONTRACT": {"const"},
    "SCHEMA-REVISION": {"const"}, "ENUM": {"enum"}, "FORMAT": {"format", "positive_int"},
    "SELF-PARENT": {"same_as_revision"}, "UNREGISTERED-TRANSFORM": {"unregistered"},
    "TRANSFORM-KIND": {"kind_mismatch"}, "NOT-IN-TRANSFORM": {"not_in_transform"},
    "DATE": {"date_required", "date_invalid"}, "DUPLICATE": {"duplicate"}, "TOO-MANY": {"list_max_10"},
    "EMPTY": {"empty"}, "TOO-LONG": {"too_long"}, "BLOCKED-URL": {"url_not_allowed"},
    "BLOCKED-EMAIL": {"email_not_allowed"}, "BLOCKED-PATH": {"path_not_allowed"},
    "SINGLE-LINE": {"single_line"}, "BOTH-DATES": {"exclusive_with_source_created_at"},
    "DATETIME": {"timestamp_with_offset_required"}, "GENERATION": {"code", "enum", "list_1_20"},
    "DUPLICATE-KEY": {"duplicate_key"}, "NON-JSON-NUMBER": {"float_not_allowed", "nan_not_allowed"},
    "NOT-OBJECT": {"object_required"}, "NOT-JSON": {"json"},
}


@unittest.skipUnless(os.path.exists(VEC), "공용 벡터 없음")
class SharedVectorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(VEC, encoding="utf-8") as f:
            cls.v = json.load(f)

    def test_header(self):
        self.assertEqual(self.v["contract"], contract.CONTRACT)
        self.assertEqual(self.v["schema_revision"], contract.SCHEMA_REVISION)

    def test_request_cases(self):
        for c in self.v["request_cases"]:
            with self.subTest(c["name"]):
                exp = c["expect"]
                if exp == "valid":
                    contract.validate(c["request"])
                    contract.encode_request(c["request"])
                else:
                    with self.assertRaises(contract.ContractError) as cm:
                        contract.validate(c["request"])
                    self.assertEqual(cm.exception.field, exp["field"], cm.exception.code)
                    self.assertIn(exp["rule"], RULE_MAP.get(cm.exception.code, set()), cm.exception.code)

    def test_raw_cases(self):
        for c in self.v["raw_cases"]:
            with self.subTest(c["name"]):
                with self.assertRaises(contract.ContractError) as cm:
                    contract.validate_bytes(c["raw"].encode("utf-8"))
                # 원시 본문 오류의 필드는 서버가 「$」, 클라이언트는 키 이름·빈 값 — 규칙만 비교
                self.assertIn(c["expect"]["rule"], RULE_MAP.get(cm.exception.code, set()), cm.exception.code)

    def test_idempotency_keys(self):
        for k in self.v["idempotency_keys"]:
            self.assertEqual(contract.idempotency_key(k["source_instance_id"], k["original_id"], k["revision"]),
                             k["key"])

    def test_jcs_cases(self):
        for c in self.v["jcs_cases"]:
            self.assertEqual(jcs.canonical_bytes(c["value"]), c["jcs_utf8"].encode("utf-8"))
            self.assertEqual(jcs.sha256_hex(c["value"]), c["sha256"])


if __name__ == "__main__":
    unittest.main()
