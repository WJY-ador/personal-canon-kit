"""board_manifest 시험 — 합성 픽스처만 쓴다(agent/tests/fixtures/board).

실행(레포 루트): python3 -m unittest agent/tests/test_board_manifest.py
"""
from __future__ import annotations

import copy
import hashlib
import re
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

import board_manifest as bm  # noqa: E402

FIX = AGENT / "tests" / "fixtures" / "board"
TS = "2026-01-20T10:00:00+09:00"
SID = "is_" + "0" * 24
AI = "⚠️AI"
SESSION = "⟨세션⟩"


class Workspace:
    """픽스처를 임시 폴더로 복사해 고칠 수 있게 한다."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bm-test-"))
        self.src = self.tmp / "src"
        shutil.copytree(FIX, self.src)
        self.cfg = self.src / "config.example.json"
        self.state = self.tmp / "state"
        self.sheet = self.tmp / "out" / "sheet.md"
        self.manifest = self.tmp / "out" / "manifest.json"

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_sheet(self):
        return bm.cmd_sheet(self.cfg, self.state, self.sheet)

    def complete_all(self, axes=None, section="그대로 남김", line="남김"):
        """항목 종류별로 선택지 하나를 고른다(이미 고른 것은 그대로)."""
        t = self.sheet.read_text(encoding="utf-8")
        parts = re.split(r"(<!-- item=\S+ kind=\S+ before_sha256=[0-9a-f]{64} -->)", t)
        for k in range(1, len(parts), 2):
            kind = re.search(r"kind=(\S+)", parts[k]).group(1)
            seg = parts[k + 1]
            head, sep, rest = seg.partition("수정 전:")
            if "- [x] " in head:
                continue
            pick = {"ai_section": section, "url_line": line, "path_line": line}.get(kind, "확인 완료")
            head = re.sub(r"^- \[ \] " + re.escape(pick) + r"$", "- [x] " + pick, head, count=1, flags=re.M)
            parts[k + 1] = head + sep + rest
        t = "".join(parts)
        if axes is not None:
            t = t.replace("~~~~after\n\n~~~~", f"~~~~after\n{axes}\n~~~~")
        self.sheet.write_text(t, encoding="utf-8")

    def build(self, out=None):
        return bm.cmd_build(self.cfg, self.state, self.sheet, out or self.manifest, target_user_id="u_test",
                            source_instance_id=SID, reviewed_at=TS)

    def load(self, path=None):
        return json.loads(Path(path or self.manifest).read_text(encoding="utf-8"))

    def edit(self, rel, old, new):
        p = self.src / rel
        t = p.read_text(encoding="utf-8")
        assert old in t, old
        p.write_text(t.replace(old, new), encoding="utf-8")


def rows_by(m, kind):
    kind = bm.EMIT_UNIT_KIND.get(kind, kind)
    return [r for r in m["rows"] if r["unit_kind"] == kind]


def row_code(m, code):
    return next(r for r in m["rows"] if r["code"] == code)


# ─────────────────────────────────────────────────────────────────────────
class CanonicalTest(unittest.TestCase):
    def test_matches_contract_definition(self):
        a = {"b": 1, "a": [True, None, "가"], "c": {"y": "x", "x": "y"}}
        b = {"c": {"x": "y", "y": "x"}, "a": [True, None, "가"], "b": 1}
        self.assertEqual(bm.canonical(a), bm.canonical(b))
        self.assertEqual(bm.canonical(a), json.dumps(a, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        self.assertEqual(bm.canonical(a), '{"a":[true,null,"가"],"b":1,"c":{"x":"y","y":"x"}}')
        self.assertEqual(bm.canonical("a\n\u0001/\u00e9"), '"a\\n\\u0001/\u00e9"')

    def test_float_refused(self):
        for v in ({"x": 1.5}, [0.0], {"a": {"b": float("nan")}}):
            with self.assertRaises(bm.CanonicalError):
                bm.canonical(v)

    def test_hash_formats(self):
        m = {"contract": bm.CONTRACT, "rows": [], "manifest_sha256": "x"}
        h = bm.manifest_hash(m)
        m["manifest_sha256"] = "y"
        self.assertEqual(h, bm.manifest_hash(m))
        self.assertEqual(h, hashlib.sha256(bm.canonical({"contract": bm.CONTRACT, "rows": []}).encode()).hexdigest())
        self.assertRegex(h, r"^[0-9a-f]{64}$")
        self.assertRegex(bm.row_revision({"a": 1}), r"^v_[0-9a-f]{32}$")


class TokenTest(unittest.TestCase):
    def test_exact_token_removal(self):
        self.assertEqual(bm.strip_tokens(f"가 {SESSION}."), "가 .")
        self.assertEqual(bm.strip_tokens("근거 (AI✓) 끝"), "근거 () 끝")
        self.assertEqual(bm.strip_tokens("  - ✍️정리 (2026-01-07): 답"), "  -  (2026-01-07): 답")
        s = "공백  두 칸·(괄호)·[대괄호] 그대로"
        self.assertEqual(bm.strip_tokens(s), s)

    def test_ai_marker_is_not_stripped_and_lookalikes_ignored(self):
        self.assertEqual(bm.strip_tokens(f"{AI} 줄"), f"{AI} 줄")
        for other in ("⚠️미확인", "⚠️GAP", "⚠️Drop", "⚠️이슈키", "⚠️미기록",
                      "⚠AI", "〈세션〉", "<세션>", "AI✔"):
            self.assertFalse(bm.has_ai_unconfirmed(other), other)
            self.assertEqual(bm.strip_tokens(other), other)


class ParserTest(unittest.TestCase):
    def test_principles_labels_and_variants(self):
        t = (FIX / "career" / "decisions.md").read_text(encoding="utf-8")
        cs = bm.parse_principles(t, "C", "c", "customer")
        self.assertEqual([u.code for u in cs], ["C-01", "C-02"])
        labels = [bm.block_label(b.text) for b in cs[0].blocks if b.kind == "block"]
        self.assertEqual(labels, ["머리 불릿", "장면", "근거", "한계", None, "경계", "연결"])
        # 이어지는 줄은 같은 블록
        self.assertIn("이어지는 줄도", cs[0].blocks[2].text)
        # 「승격 대기」 절 불릿은 C 단위에 안 들어간다
        self.assertNotIn("한 번만 본 판단", "\n".join(b.text for b in cs[1].blocks))

    def test_heading_level_variants(self):
        t = "# x\n\n### WD-03. 🌳 세 단계 헤딩\n- **장면**: 가\n\n#### WD-04. 네 단계\n- 나\n\n## 다른 절\n- 다\n"
        ws = bm.parse_principles(t, "WD", "wd", "work")
        self.assertEqual([u.code for u in ws], ["WD-03", "WD-04"])
        self.assertEqual(ws[0].fields["maturity"], "mature")
        self.assertEqual(ws[0].title, "세 단계 헤딩")
        self.assertNotIn("다", "\n".join(b.text for b in ws[1].blocks))

    def test_heading_inside_code_fence_ignored(self):
        t = "## WD-01. 가\n```\n## WD-02. 펜스 안\n```\n- 끝\n"
        ws = bm.parse_principles(t, "WD", "wd", "work")
        self.assertEqual([u.code for u in ws], ["WD-01"])

    def test_legend_only_has_no_review_items(self):
        t = (FIX / "career" / "work-decisions.md").read_text(encoding="utf-8")
        legend = t.split("## WD-01.")[0] + "\n## 승격 후보\n\n" + AI + " — 준비 절\n"
        units = bm.parse_principles(legend, "WD", "wd", "work")
        self.assertEqual(units, [])
        self.assertEqual(bm.review_items(units), [])

    def test_axes_only_axes_section(self):
        us = bm.parse_axes((FIX / "trend.md").read_text(encoding="utf-8"), "관심 축", bm.DEFAULTS["axes_columns"])
        self.assertEqual([(u.code, u.title) for u in us], [("A", "합성 축 하나"), ("B-1", "합성 축 둘")])
        self.assertEqual(us[0].fields["keywords"], ["키워드1", "키워드2", "kw-one", "kw-two"])

    def test_pub_channel_url_material(self):
        us = bm.parse_pub((FIX / "drafts" / "published-log.md").read_text(encoding="utf-8"))
        self.assertEqual([u.fields["channel"] for u in us], ["linkedin", "threads"])
        self.assertEqual(us[0].material_dates, ["2026-01-10"])
        self.assertEqual(us[1].title, "합성 글 둘 / 부제")
        self.assertTrue(any("URL" in w for w in us[1].warnings))
        self.assertNotIn("example.invalid", json.dumps([u.title for u in us] + [u.fields for u in us]))

    def test_record_sections(self):
        u = bm.parse_record((FIX / "sessions" / "2026-01-12.md").read_text(encoding="utf-8"), "session_story",
                            "2026-01-12", "2026-01-12", "어디에 붙나", {"팀과의 과정"})
        body = "\n".join(b.text for b in u.blocks)
        self.assertIn("합성 결과", body)
        self.assertNotIn("다른 사람과", body)
        self.assertNotIn("title:", body)          # frontmatter 제거
        j = bm.parse_record((FIX / "journal" / "2026-01-10.md").read_text(encoding="utf-8"), "journal",
                            "2026-01-10", "2026-01-10", "어디에 붙나", set())
        self.assertEqual(j.attach["codes"], [("C", 1), ("D", 2), ("E", 9)])
        self.assertNotIn("쓸 자리", "\n".join(b.text for b in j.blocks))


# ─────────────────────────────────────────────────────────────────────────
class FlowTest(unittest.TestCase):
    def setUp(self):
        self.w = Workspace()

    def tearDown(self):
        self.w.close()

    def test_incomplete_review_refuses(self):
        r = self.w.make_sheet()
        self.assertEqual(r["blocking"], 12)
        with self.assertRaises(bm.ManifestError) as cm:
            self.w.build()
        self.assertEqual(len(cm.exception.messages), 12)
        self.assertFalse(self.w.manifest.exists())
        # 하나만 남겨도 거절
        self.w.complete_all()
        t = self.w.sheet.read_text(encoding="utf-8").replace("- [x] 확인 완료", "- [ ] 확인 완료", 1)
        self.w.sheet.write_text(t, encoding="utf-8")
        with self.assertRaises(bm.ManifestError) as cm:
            self.w.build()
        self.assertEqual(len(cm.exception.messages), 1)
        self.assertIn("미완료", cm.exception.messages[0])

    def test_build_and_mapping(self):
        self.w.make_sheet()
        self.w.complete_all(axes="A")
        self.w.build()
        m = self.w.load()
        self.assertEqual(bm.validate_manifest(m)[0], [])
        self.assertEqual(set(m), bm.TOP_KEYS)
        self.assertEqual(m["transform_version"], "board-manifest/1")
        for r in m["rows"]:
            self.assertLessEqual(set(r), bm.ROW_ALLOWED)
            self.assertLessEqual(bm.ROW_REQUIRED, set(r))
        blob = json.dumps(m, ensure_ascii=False)
        for tok in bm.ALL_MARKERS:
            self.assertNotIn(tok, blob)
        # 제외: 다른 사람 절·작성자 표·발행 URL·D 본문 경로·범례
        for s in ("가상인물갑", "다른 사람과", "example.invalid/post/1", "_x/decisions.md", "준비 절", "외부 글"):
            self.assertNotIn(s, blob)
        c1 = row_code(m, "C-01")
        self.assertEqual((c1["target_kind"], c1["unit_kind"]), ("item", "principle_c"))
        self.assertEqual(c1["object_ref"], {"std_id": "std:principles", "template_slot": "author@1:principles",
                                            "key": "principles"})
        self.assertEqual(c1["fields"], {"register": "customer"})
        self.assertIn("- **근거** : 작은 표본", c1["body"])          # 토큰 글자만 빠짐
        self.assertIn("\u26a0\ufe0f미확인", c1["body"])             # 다른 ⚠️ 표기는 그대로
        wd1 = row_code(m, "WD-01")
        self.assertEqual(wd1["unit_kind"], "principle_wd")
        self.assertEqual(wd1["fields"], {"register": "work", "maturity": "growing"})
        self.assertIn("하루 안이면 충분하다", wd1["body"])            # 누적 답은 본문
        self.assertEqual(row_code(m, "D-01")["object_ref"]["key"], "decision_events")
        craft = rows_by(m, "craft")
        self.assertEqual(craft[0]["object_ref"], {"template_slot": "author@1:craft", "key": "craft"})
        ac = row_code(m, "AC-01")
        self.assertEqual((ac["unit_kind"], ac["object_ref"]), ("application_axis", {"key": "application_axes"}))
        self.assertEqual(ac["fields"]["wanted"], "문제를 쪼개는 사람.")
        self.assertEqual(ac["fields"]["io"], "(2026-01-09) 입력은 요청, 출력은 쪼갠 목록.\n두 번째 줄.")
        self.assertNotIn("as_is", ac["fields"])                      # 빈 칸은 안 넣음
        self.assertEqual(rows_by(m, "application")[0]["object_ref"], {"key": "application_docs"})
        self.assertFalse(any(r["target_kind"] == "object" for r in m["rows"]))
        # 숙제: 한 곳만 가리키는 것만
        oq = rows_by(m, "open_question")
        self.assertEqual(len(oq), 2)
        self.assertEqual(oq[0]["fields"], {"state": "open", "registered_on": "2026-01-10"})
        self.assertEqual(oq[0]["references"], [{"field_key": "subject_principle",
                                                "target": {"original_id": c1["original_id"], "revision": c1["revision"]}}])
        # 일지: 날짜·record_attach
        j = next(r for r in rows_by(m, "journal") if r["source_date"] == "2026-01-10")
        self.assertEqual((j["target_kind"], j["source_created_at"]), ("record", None))
        for k in ("object_ref", "fields", "decision", "references"):
            self.assertNotIn(k, j)
        self.assertEqual(j["attach"]["use_at"], ["발행 글 재료 ."])
        self.assertEqual(j["attach"]["unattached"], ["새 축 후보.\n참고 https://example.invalid/post/3"])  # 「남김」
        self.assertEqual(sorted(t["original_id"] for t in j["attach"]["attach_to"]),
                         sorted([c1["original_id"], row_code(m, "D-02")["original_id"]]))
        ax = row_code(m, "A")
        self.assertEqual(j["attach"]["axes"], [{"original_id": ax["original_id"], "revision": ax["revision"]}])
        self.assertNotIn("attach", next(r for r in rows_by(m, "journal") if r["source_date"] == "2026-01-11"))
        # 발행: 재료 = 같은 명세의 일지, URL 없음
        pub = rows_by(m, "pub")
        self.assertEqual(pub[0]["references"], [{"field_key": "materials",
                                                 "target": {"original_id": j["original_id"], "revision": j["revision"]}}])
        self.assertNotIn("url", pub[1]["fields"])
        # 순서: 참조 대상이 앞
        pos = {r["original_id"]: i for i, r in enumerate(m["rows"])}
        for r in m["rows"]:
            ts = [x["target"] for x in r.get("references", [])]
            ts += [t for k in ("attach_to", "axes") for t in r.get("attach", {}).get(k, [])]
            for t in ts:
                self.assertLess(pos[t["original_id"]], pos[r["original_id"]])
        # 날짜 없는 파일은 일지로 안 읽음
        self.assertEqual(len(rows_by(m, "journal")), 2)

    def test_ids_stable_and_revision_chain(self):
        self.w.make_sheet()
        self.w.complete_all()
        self.w.build()
        m1 = self.w.load()
        idmap1 = json.loads((self.w.state / "idmap.json").read_text(encoding="utf-8"))
        # 같은 입력 재실행 — ID·판·해시·바이트 동일
        out2 = self.w.tmp / "out" / "m2.json"
        self.w.make_sheet()
        self.w.build(out2)
        self.assertEqual(self.w.manifest.read_bytes(), out2.read_bytes())
        self.assertTrue(all(r["previous_revision"] is None for r in m1["rows"]))
        # ID 는 경로·줄 번호에서 오지 않는다: 줄을 밀어도 같은 ID
        self.w.edit("career/decisions.md", "# 합성 판단 정본\n", "# 합성 판단 정본\n\n\n추가 줄.\n")
        self.w.edit("career/decisions.md", "한 번은 우연일 수 있다", "한 번은 우연일 수도 있다")
        self.w.make_sheet()
        out3 = self.w.tmp / "out" / "m3.json"
        self.w.build(out3)
        m3 = self.w.load(out3)
        old, new = row_code(m1, "C-02"), row_code(m3, "C-02")
        self.assertEqual(old["original_id"], new["original_id"])
        self.assertNotEqual(old["revision"], new["revision"])
        self.assertIsNone(new["previous_revision"])          # 이전 CLI 가 채운다
        idm = json.loads((self.w.state / "idmap.json").read_text(encoding="utf-8"))
        ent = next(v for v in idm["units"].values() if v["original_id"] == new["original_id"])
        self.assertEqual(ent, {"original_id": new["original_id"], "revision": new["revision"]})
        self.assertEqual(row_code(m1, "C-01")["revision"], row_code(m3, "C-01")["revision"])
        self.assertIsNone(row_code(m3, "C-01")["previous_revision"])
        # 같은 내용 재실행: 판·이전 판 유지
        out4 = self.w.tmp / "out" / "m4.json"
        self.w.build(out4)
        self.assertEqual(out3.read_bytes(), out4.read_bytes())
        # 원본 ID 는 난수 형식, 대응표에만 위치가 있다
        for r in m3["rows"]:
            self.assertRegex(r["original_id"], r"^o_[0-9a-f]{32}$")
        self.assertEqual(set(idmap1["units"]) <= set(json.loads((self.w.state / "idmap.json").read_text())["units"]), True)

    def test_source_change_invalidates_review(self):
        self.w.make_sheet()
        self.w.complete_all()
        self.w.edit("career/work-decisions.md", "작은 일에는 과하다.", "작은 일에는 과하다고 본다.")
        with self.assertRaises(bm.ManifestError) as cm:
            self.w.build()
        # 블록 내용이 바뀌면 항목 ID 도 바뀌어(내용 기반) 「검토표에 없는 항목」으로 막힌다
        self.assertTrue(any("무효" in x or "검토표에 없는" in x for x in cm.exception.messages))
        # 검토표를 다시 만들면 그 항목만 미완료로 돌아가고 나머지는 유지
        r = self.w.make_sheet()
        self.assertEqual((r["blocking"], r["blocking_done"]), (12, 11))

    def test_after_edit_is_applied_and_markers_refused(self):
        self.w.make_sheet()
        self.w.complete_all()
        t = self.w.sheet.read_text(encoding="utf-8")
        t = t.replace("-  표본 크기 기준은 아직 확인하지 않았다(가정 메모).",
                      "- 표본은 열 건 안쪽으로 본다(원작자 확인).")
        self.w.sheet.write_text(t, encoding="utf-8")
        self.w.build()
        self.assertIn("- 표본은 열 건 안쪽으로 본다(원작자 확인).", row_code(self.w.load(), "C-01")["body"])
        # after 에 ⚠️AI 를 남기면 거절
        t = self.w.sheet.read_text(encoding="utf-8").replace("- 표본은 열 건", f"- {AI} 표본은 열 건")
        self.w.sheet.write_text(t, encoding="utf-8")
        with self.assertRaises(bm.ManifestError) as cm:
            self.w.build()
        self.assertTrue(any("표기가 남음" in x for x in cm.exception.messages))

    def test_dropped_links_visible_in_sheet_and_build(self):
        self.w.make_sheet()
        t = self.w.sheet.read_text(encoding="utf-8")
        self.assertIn("항목 E-09 이 명세에 없어 버림", t)
        self.assertIn("한 곳이 아님", t)
        self.w.complete_all()
        r = self.w.build()
        self.assertTrue(any("E-09" in w for w in r["warnings"]))

    def test_edited_after_survives_sheet_regeneration(self):
        self.w.make_sheet()
        self.w.complete_all(axes="A")
        t = self.w.sheet.read_text(encoding="utf-8").replace("-  이 관찰은 확인 전이다.", "- 이 관찰은 원작자가 확인했다.")
        self.w.sheet.write_text(t, encoding="utf-8")
        r = self.w.make_sheet()
        self.assertEqual(r["blocking_done"], r["blocking"])
        t = self.w.sheet.read_text(encoding="utf-8")
        self.assertIn("- 이 관찰은 원작자가 확인했다.", t)
        self.assertIn("~~~~after\nA\n~~~~", t)

    def test_optional_item_stale_does_not_block(self):
        self.w.make_sheet()
        self.w.complete_all(axes="A")
        self.w.edit("journal/2026-01-10.md", "A(합성 축 하나) 쪽.", "A(합성 축 하나) 쪽이다.")
        self.w.build()
        j = next(r for r in rows_by(self.w.load(), "journal") if r["source_date"] == "2026-01-10")
        self.assertEqual(j["attach"]["axes"], [])

    def test_open_question_without_registered_date_skipped(self):
        self.w.edit("career/open-questions.md", "이어지는 줄. (등록 2026-01-11)", "이어지는 줄. (다른말 2026-01-11)")
        self.w.make_sheet()
        self.assertIn("등록 YYYY-MM-DD", self.w.sheet.read_text(encoding="utf-8"))
        self.w.complete_all()
        self.w.build()
        self.assertEqual(len(rows_by(self.w.load(), "open_question")), 1)

    def _journal(self, date):
        return next(r for r in rows_by(self.w.load(), "journal") if r["source_date"] == date)

    def test_ai_section_is_one_blocking_item(self):
        self.w.make_sheet()
        t = self.w.sheet.read_text(encoding="utf-8")
        self.assertEqual(t.count("kind=ai_section"), 1)
        self.assertNotIn("절 안 미확정 줄은 절 항목에 포함된다.\n~~~~\n\n수정 후", t.split("kind=ai_section")[0])
        self.assertEqual(t.count("kind=ai_unconfirmed"), 5)    # 절 안 ⚠️AI 는 따로 안 나온다
        for c in ("고쳐서 남김", "그대로 남김", "뺌"):
            self.assertIn(f"- [ ] {c}", t)

    def test_ai_section_choices(self):
        self.w.make_sheet()
        self.w.complete_all(section="뺌")
        self.w.build()
        body = self._journal("2026-01-10")["body"]
        self.assertNotIn("보충 관점", body)
        self.assertNotIn("하위 절", body)
        self.assertIn("## 인사이트 \n\n함께 정리한 합성 인사이트.", body)   # ⟨세션⟩ 헤딩은 토큰만 지움
        self.assertNotIn("\n\n\n", body)
        self.w.make_sheet()
        t = self.w.sheet.read_text(encoding="utf-8").replace("- [x] 뺌\n", "- [ ] 뺌\n", 1)
        t = t.replace("- [ ] 고쳐서 남김", "- [x] 고쳐서 남김")
        j = t.index("~~~~after", t.index("kind=ai_section"))
        t = t[:j] + t[j:].replace("AI 가 덧붙인 합성 문단.", "원작자가 고친 문단.", 1)
        self.w.sheet.write_text(t, encoding="utf-8")
        self.w.build()
        body = self._journal("2026-01-10")["body"]
        self.assertIn("원작자가 고친 문단.", body)
        self.assertNotIn("AI 가 덧붙인 합성 문단.", body)
        self.w.make_sheet()
        t = self.w.sheet.read_text(encoding="utf-8").replace("- [x] 고쳐서 남김", "- [ ] 고쳐서 남김")
        t = t.replace("- [ ] 그대로 남김", "- [x] 그대로 남김")
        self.w.sheet.write_text(t, encoding="utf-8")
        self.w.build()
        body = self._journal("2026-01-10")["body"]
        self.assertIn("AI 가 덧붙인 합성 문단.", body)
        self.assertIn("## 보충 관점 & 고민할 방향 (AI)", body)
        self.assertIn("-  절 안 미확정 줄은", body)

    def test_url_and_path_lines(self):
        r = self.w.make_sheet()
        t = self.w.sheet.read_text(encoding="utf-8")
        self.assertEqual((t.count("kind=url_line"), t.count("kind=path_line")), (2, 1))
        self.w.complete_all(line="뺌")
        self.w.build()
        self.assertEqual(self._journal("2026-01-10")["attach"]["unattached"], ["새 축 후보."])
        body = self._journal("2026-01-11")["body"]
        self.assertNotIn("example.invalid", body)
        self.assertNotIn("~/notes", body)
        self.assertIn("- 보통 줄.", body)
        self.w.make_sheet()
        t = self.w.sheet.read_text(encoding="utf-8").replace("- [x] 뺌", "- [x] 남김\n- [x] 뺌").replace(
            "- [ ] 남김\n- [x] 남김", "- [x] 남김")
        self.w.sheet.write_text(t, encoding="utf-8")
        t = self.w.sheet.read_text(encoding="utf-8")
        self.assertIn("- [x] 남김\n- [x] 뺌", t)              # 둘 다 고르면 거절
        with self.assertRaises(bm.ManifestError) as cm:
            self.w.build()
        self.assertTrue(any("둘 이상" in x for x in cm.exception.messages))
        self.w.sheet.write_text(t.replace("- [x] 남김\n- [x] 뺌", "- [x] 남김\n- [ ] 뺌"), encoding="utf-8")
        self.w.build()
        self.assertIn("https://example.invalid/post/2", self._journal("2026-01-11")["body"])

    def test_target_revision_default_on(self):
        # 기본값 = 정확한 판 참조(서버 스키마 필수). references·attach 둘 다 {original_id, revision}
        self.assertIs(bm.TARGET_WITH_REVISION, True)
        self.w.make_sheet()
        self.w.complete_all(axes="A")
        self.w.build()
        m = self.w.load()
        self.assertEqual(bm.validate_manifest(m)[0], [])
        c1 = row_code(m, "C-01")
        oq = rows_by(m, "open_question")[0]
        self.assertEqual(oq["references"][0]["target"], {"original_id": c1["original_id"], "revision": c1["revision"]})
        tgts = [t for r in m["rows"] for k in ("attach_to", "axes") for t in (r.get("attach") or {}).get(k, [])]
        self.assertTrue(tgts)
        self.assertTrue(all(set(t) == {"original_id", "revision"} for t in tgts), tgts)

    def test_validate_cli_writes_nothing(self):
        self.w.make_sheet()
        self.w.complete_all()
        self.w.build()
        before = {p: p.stat().st_mtime_ns for p in self.w.tmp.rglob("*") if p.is_file()}
        names = set(before)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = bm.main(["validate", str(self.w.manifest), "--config", str(self.w.cfg), "--state-dir",
                          str(self.w.state), "--sheet", str(self.w.sheet)])
        self.assertEqual(rc, 0, buf.getvalue())
        after = {p: p.stat().st_mtime_ns for p in self.w.tmp.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(names, set(after))
        # 원본에 새 단위가 생기면 validate 가 잡되 대응표엔 안 쓴다
        self.w.edit("career/decisions.md", "## 승격 대기", "### C-03. 새 판단\n- **장면**: 가\n\n## 승격 대기")
        idmap = (self.w.state / "idmap.json").read_bytes()
        errs, _ = bm.cmd_validate(self.w.manifest, self.w.cfg, self.w.state, self.w.sheet)
        self.assertTrue(any("새 단위" in e for e in errs))
        self.assertEqual(idmap, (self.w.state / "idmap.json").read_bytes())


class ExclusionTest(unittest.TestCase):
    def setUp(self):
        self.w = Workspace()
        self.home = os.environ.get("HOME")

    def tearDown(self):
        if self.home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.home
        self.w.close()

    def _cfg(self, sources, **extra):
        cfg = json.loads(self.w.cfg.read_text(encoding="utf-8"))
        cfg["sources"] = sources
        cfg.update(extra)
        p = self.w.src / "cfg2.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return p

    def test_excluded_keys_refused(self):
        for k in ("insights", "assignments", "verbatim_ai", "feed"):
            with self.assertRaises(bm.ManifestError) as cm:
                bm.load_config(self._cfg({k: "x.md"}))
            self.assertIn("제외", str(cm.exception))
        with self.assertRaises(bm.ManifestError):
            bm.load_config(self._cfg({"anything_else": "x.md"}))

    def test_customer_root_refused(self):
        # 고객사 폴더는 설정 forbidden_roots 로만 정한다 — 코드에 박힌 경로가 없다
        client = self.w.tmp / "client-root"
        sf = client / "Project" / "x"
        sf.mkdir(parents=True)
        (sf / "decisions.md").write_text("### C-01. 가\n", encoding="utf-8")
        self.assertEqual(bm.DEFAULTS["forbidden_roots"], [])
        bm.load_config(self._cfg({"decisions": str(sf / "decisions.md")}))   # 설정에 없으면 막지 않는다
        with self.assertRaises(bm.ManifestError):
            bm.load_config(self._cfg({"decisions": str(sf / "decisions.md")}, forbidden_roots=[str(client)]))
        # 심볼릭 링크로 들어가도 거절
        link = self.w.src / "linked"
        os.symlink(sf, link)
        with self.assertRaises(bm.ManifestError):
            bm.load_config(self._cfg({"journal_dir": "linked"}, forbidden_roots=[str(client)]))
        # 형식이 틀린 forbidden_roots 는 거절
        with self.assertRaises(bm.ManifestError):
            bm.load_config(self._cfg({"journal_dir": "journal"}, forbidden_roots="journal"))

    def test_forbidden_roots_config(self):
        with self.assertRaises(bm.ManifestError):
            bm.load_config(self._cfg({"journal_dir": "journal"}, forbidden_roots=["journal"]))

    def test_validate_rejects_excluded_and_forbidden_fields(self):
        self.w.make_sheet()
        self.w.complete_all()
        self.w.build()
        m = self.w.load()
        bad = copy.deepcopy(m)
        bad["rows"][0]["unit_kind"] = "insights"
        bad["manifest_sha256"] = bm.manifest_hash(bad)
        self.assertTrue(any("제외" in e for e in bm.validate_manifest(bad)[0]))
        bad = copy.deepcopy(m)
        bad["rows"][0]["ai_markers"] = []
        bad["manifest_sha256"] = bm.manifest_hash(bad)
        self.assertTrue(any("금지 필드" in e for e in bm.validate_manifest(bad)[0]))
        bad = copy.deepcopy(m)
        row_code(bad, "C-02")["body"] += " " + SESSION
        bad["manifest_sha256"] = bm.manifest_hash(bad)
        self.assertTrue(any("표기가 남아" in e for e in bm.validate_manifest(bad)[0]))
        bad = copy.deepcopy(m)
        bad["rows"][0]["title"] = "바뀜"
        self.assertTrue(any("manifest_sha256" in e for e in bm.validate_manifest(bad)[0]))
        bad = copy.deepcopy(m)
        r = next(x for x in bad["rows"] if x["unit_kind"] == "journal")
        r["source_created_at"] = TS
        bad["manifest_sha256"] = bm.manifest_hash(bad)
        self.assertTrue(any("함께" in e for e in bm.validate_manifest(bad)[0]))


class CycleTest(unittest.TestCase):
    def _row(self, n, refs):
        return {"original_id": "o_" + f"{n:032x}", "revision": "v_" + f"{n:032x}", "previous_revision": None,
                "target_kind": "item", "unit_kind": "craft", "code": None, "object_ref": bm.OBJECT_REF["craft"],
                "source_created_at": None, "source_date": None, "title": f"t{n}", "body": "", "fields": {},
                "references": refs, "expected_target_revision": None}

    def _m(self, rows):
        m = {"contract": bm.CONTRACT, "target_user_id": "u", "source_instance_id": SID,
             "transform_version": "board-manifest/1", "reviewed_at": TS, "rows": rows}
        m["manifest_sha256"] = bm.manifest_hash(m)
        return m

    def test_cycle_refused(self):
        a, b = self._row(1, []), self._row(2, [])
        a["references"] = [{"field_key": "related_events", "target": {"original_id": b["original_id"], "revision": b["revision"]}}]
        b["references"] = [{"field_key": "related_events", "target": {"original_id": a["original_id"], "revision": a["revision"]}}]
        errs = bm.validate_manifest(self._m([a, b]))[0]
        self.assertTrue(any("순환" in e for e in errs), errs)

    def test_missing_target_and_order_refused(self):
        a, b = self._row(1, []), self._row(2, [])
        b["references"] = [{"field_key": "related_events", "target": {"original_id": a["original_id"], "revision": a["revision"]}}]
        errs = bm.validate_manifest(self._m([b, a]))[0]
        self.assertTrue(any("뒤에" in e for e in errs))
        b["references"] = [{"field_key": "related_events", "target": {"original_id": "o_" + "e" * 32, "revision": "v_" + "e" * 32}}]
        errs = bm.validate_manifest(self._m([a, b]))[0]
        self.assertTrue(any("명세에 없어요" in e for e in errs))
        b["references"] = [{"field_key": "related_events", "target": {"original_id": a["original_id"], "item_id": "i_abcd"}}]
        errs = bm.validate_manifest(self._m([a, b]))[0]
        self.assertTrue(any("{original_id, revision}" in e for e in errs), errs)
        b["references"] = [{"field_key": "related_events", "target": {"original_id": a["original_id"]}}]   # revision 빠짐
        errs = bm.validate_manifest(self._m([a, b]))[0]
        self.assertTrue(any("{original_id, revision}" in e for e in errs), errs)

    def test_topo_cycle_in_builder(self):
        u1, u2 = bm.Unit("craft", "a"), bm.Unit("craft", "b")
        with self.assertRaises(bm.ManifestError):
            bm._topo([u1, u2], {u1.key: [("x", u2)], u2.key: [("x", u1)]})


class VerbatimAiRuleTest(unittest.TestCase):
    def test_merges_into_owner_without_new_row(self):
        u = bm.Unit("wd", "WD-01", code="WD-01")
        u.blocks = [bm.Block("block", ["- 원래 줄"], 1)]
        bm.merge_verbatim_ai([u], [{"owner_code": "WD-01", "text": f"답 {SESSION}"}])
        self.assertEqual(bm._body(u), "- 원래 줄\n\n답 ")
        with self.assertRaises(bm.ManifestError):
            bm.merge_verbatim_ai([u], [{"owner_code": "WD-99", "text": "x"}])


class CliTest(unittest.TestCase):
    def test_build_refusal_exit_code(self):
        w = Workspace()
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(bm.main(["sheet", "--config", str(w.cfg), "--state-dir", str(w.state),
                                          "--out", str(w.sheet)]), 0)
            err = io.StringIO()
            with redirect_stderr(err):
                rc = bm.main(["build", "--config", str(w.cfg), "--state-dir", str(w.state), "--sheet", str(w.sheet),
                              "--out", str(w.manifest), "--target-user-id", "u", "--source-instance-id", SID,
                              "--reviewed-at", TS])
            self.assertEqual(rc, 2)
            self.assertIn("미완료", err.getvalue())
            self.assertFalse(w.manifest.exists())
        finally:
            w.close()


# ─────────────────────────────────────────────────────────────────────────
SCHEMA = AGENT.parent / "server" / "contract" / "imports" / "board-migration-1.schema.json"
_IGNORED = {"$schema", "$id", "$comment", "title", "description", "examples", "default", "format", "$defs",
            "definitions", "deprecated", "readOnly", "writeOnly"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _schema_errors(v, sch, root, path="$"):
    """표준 라이브러리 JSON Schema 부분 구현(시험용). 모르는 키워드는 오류로 드러낸다."""
    errs = []
    if sch is True or sch == {}:
        return errs
    if sch is False:
        return [f"{path}: false 스키마"]
    if "$ref" in sch:
        ref = sch["$ref"]
        if not ref.startswith("#/"):
            return [f"{path}: 외부 $ref 미지원 {ref}"]
        node = root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        errs += _schema_errors(v, node, root, path)
    for k, x in sch.items():
        if k in _IGNORED or k == "$ref":
            continue
        if k == "type":
            ts = x if isinstance(x, list) else [x]
            ok = False
            for t in ts:
                if t == "integer":
                    ok |= isinstance(v, int) and not isinstance(v, bool)
                elif t == "number":
                    ok |= isinstance(v, (int, float)) and not isinstance(v, bool)
                else:
                    ok |= isinstance(v, _TYPES[t]) and not (t != "boolean" and isinstance(v, bool) and t not in ("boolean",))
            if not ok:
                errs.append(f"{path}: type {x}")
        elif k == "properties":
            if isinstance(v, dict):
                for pk, ps in x.items():
                    if pk in v:
                        errs += _schema_errors(v[pk], ps, root, f"{path}.{pk}")
        elif k == "patternProperties":
            if isinstance(v, dict):
                for pat, ps in x.items():
                    for pk in v:
                        if re.search(pat, pk):
                            errs += _schema_errors(v[pk], ps, root, f"{path}.{pk}")
        elif k == "additionalProperties":
            if isinstance(v, dict):
                known = set(sch.get("properties", {}))
                pats = list(sch.get("patternProperties", {}))
                for pk in v:
                    if pk in known or any(re.search(p, pk) for p in pats):
                        continue
                    errs += (_schema_errors(v[pk], x, root, f"{path}.{pk}") if isinstance(x, dict)
                             else ([] if x else [f"{path}: 추가 필드 {pk}"]))
        elif k == "required":
            if isinstance(v, dict):
                errs += [f"{path}: 필수 {r}" for r in x if r not in v]
        elif k == "items":
            if isinstance(v, list):
                for i, it in enumerate(v):
                    errs += _schema_errors(it, x, root, f"{path}[{i}]")
        elif k == "enum":
            if v not in x:
                errs.append(f"{path}: enum")
        elif k == "const":
            if v != x:
                errs.append(f"{path}: const")
        elif k == "pattern":
            if isinstance(v, str) and not re.search(x, v):
                errs.append(f"{path}: pattern {x}")
        elif k in ("minLength", "maxLength"):
            if isinstance(v, str) and (len(v) < x if k == "minLength" else len(v) > x):
                errs.append(f"{path}: {k}")
        elif k in ("minItems", "maxItems"):
            if isinstance(v, list) and (len(v) < x if k == "minItems" else len(v) > x):
                errs.append(f"{path}: {k}")
        elif k in ("minimum", "maximum"):
            if isinstance(v, int) and not isinstance(v, bool) and (v < x if k == "minimum" else v > x):
                errs.append(f"{path}: {k}")
        elif k in ("minProperties", "maxProperties"):
            if isinstance(v, dict) and (len(v) < x if k == "minProperties" else len(v) > x):
                errs.append(f"{path}: {k}")
        elif k == "uniqueItems":
            if x and isinstance(v, list) and len({json.dumps(i, sort_keys=True) for i in v}) != len(v):
                errs.append(f"{path}: uniqueItems")
        elif k == "allOf":
            for sub in x:
                errs += _schema_errors(v, sub, root, path)
        elif k == "anyOf":
            if all(_schema_errors(v, sub, root, path) for sub in x):
                errs.append(f"{path}: anyOf")
        elif k == "oneOf":
            if sum(1 for sub in x if not _schema_errors(v, sub, root, path)) != 1:
                errs.append(f"{path}: oneOf")
        elif k == "not":
            if not _schema_errors(v, x, root, path):
                errs.append(f"{path}: not")
        elif k == "if":
            if not _schema_errors(v, x, root, path):
                errs += _schema_errors(v, sch.get("then", {}), root, path)
            else:
                errs += _schema_errors(v, sch.get("else", {}), root, path)
        elif k in ("then", "else"):
            continue
        else:
            errs.append(f"{path}: 시험 검증기가 모르는 키워드 {k} — 시험을 넓히세요")
    return errs


@unittest.skipUnless(SCHEMA.exists(), "서버 정본 스키마(board-migration-1.schema.json)가 아직 없음")
class ServerSchemaTest(unittest.TestCase):
    def test_manifest_passes_server_schema(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        w = Workspace()
        try:
            w.make_sheet()
            w.complete_all(axes="A")
            w.build()
            m = w.load()
            self.assertEqual(_schema_errors(m, schema, schema), [])
            # 검증기가 실제로 잡는지(음성 사례)
            bad = copy.deepcopy(m)
            bad["rows"][0]["target_kind"] = "object"
            bad["transform_version"] = "board-md/1"
            j = next(r for r in bad["rows"] if r["target_kind"] == "record")
            j["fields"] = {}
            self.assertGreaterEqual(len(_schema_errors(bad, schema, schema)), 3)
        finally:
            w.close()

    def test_flag_off_is_refused_by_server_schema(self):
        # TARGET_WITH_REVISION=False(옛 꼴 — original_id 만) 명세는 생성기 자체 검사는 통과해도 서버 스키마가 거절한다
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        old = bm.TARGET_WITH_REVISION
        bm.TARGET_WITH_REVISION = False
        w = Workspace()
        try:
            w.make_sheet()
            w.complete_all(axes="A")
            w.build()
            m = w.load()
            self.assertEqual(bm.validate_manifest(m)[0], [])
            errs = _schema_errors(m, schema, schema)
            self.assertTrue(any(".references[" in e for e in errs), errs)
            self.assertTrue(any(".attach.attach_to[" in e or ".attach.axes[" in e for e in errs), errs)
        finally:
            bm.TARGET_WITH_REVISION = old
            w.close()


if __name__ == "__main__":
    unittest.main()
