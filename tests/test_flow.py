"""처음부터 끝까지 흐름 시험 — 한글·공백 경로, 가짜 모델, 읽기 전용 서버, 깨뜨리고 복구.

python3 -m unittest discover -s tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crmkit import server  # noqa: E402

FAKE = str(ROOT / "tests" / "fake_claude.py")
# 화면에 나오면 안 되는 내부 ID·명령어 (next.text 는 화면에 그대로 쓰인다)
SCREEN_LEAK = re.compile(r"c-\d{4}|q-\d{3}|j-\d{8}|t-\d{3}|crm\.py")


def snapshot(d):
    return {str(p.relative_to(d)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in Path(d).rglob("*") if p.is_file()}


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="crm test "))
        cls.data = cls.tmp / "내 데이터 폴더"
        cls.notes = cls.tmp / "메모 폴더"
        cls.notes.mkdir()
        (cls.notes / "회의 메모.md").write_text(
            "도입 회의에서 입력 담당을 먼저 정했다. 도구 비교는 그다음이었다.\n\n"
            "## 이게 어디에 붙나\n\n- 질문: q-001\n- 판단: 입력 담당을 먼저 정한다\n- 다음:\n- 출처:\n",
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, *args, ok=True, env=None):
        e = dict(os.environ, CRM_CLAUDE=FAKE, **(env or {}))
        e.pop("CRM_DATA", None)
        r = subprocess.run([sys.executable, str(ROOT / "crm.py"), "--data", str(self.data), *args],
                           capture_output=True, text=True, encoding="utf-8", env=e)
        if ok and r.returncode != 0:
            self.fail(f"{args} 실패 rc={r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def test_full_flow(self):
        c = self.run_cli
        lc = ROOT / "local.json"
        lc_before = lc.read_bytes() if lc.exists() else None
        c("init", "--no-remember")
        self.assertTrue((self.data / "profile.json").exists())
        self.assertEqual(lc_before, lc.read_bytes() if lc.exists() else None, "--no-remember 인데 local.json 이 바뀜")

        # 동의 전 파일 읽기 거부
        r = c("record", "add", "--title", "x", "--file", str(self.notes / "회의 메모.md"), ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("CONSENT-MISSING", r.stderr)

        c("consent", "--read", str(self.notes), "--model-send", "no")
        c("profile", "set", "purpose", "회고 때 판단 다시 쓰기", "--status", "confirmed")
        c("profile", "set", "daily_views", "회고|글감", "--status", "guessed")
        c("question", "add", "새 도구를 들일 때 무엇부터 확인하나")
        c("source", "add", str(self.notes), "--label", "메모")

        # 탭: 카탈로그 밖 블록 거부, 미리보기 → 취소는 변경 없음 → 적용
        r = c("tabs", "propose", "--tab", "회고=sheet,chat", ok=False)
        self.assertIn("BLOCK-UNKNOWN", r.stderr)
        before = (self.data / "tabs.json").read_text(encoding="utf-8")
        c("tabs", "propose", "--tab", "주간 회고=sheet,candidates,next", "--tab", "글감=journal,status")
        c("tabs", "cancel")
        self.assertEqual(before, (self.data / "tabs.json").read_text(encoding="utf-8"))
        r = c("tabs", "propose", "--tab", "주간 회고=sheet,candidates,next", "--tab", "글감=journal,status")
        self.assertIn("되돌리기", r.stdout)
        c("tabs", "apply")
        tabs = json.loads((self.data / "tabs.json").read_text(encoding="utf-8"))
        self.assertEqual([t["id"] for t in tabs["tabs"]], ["t-001", "t-002"])
        # 이름만 바꾸면 내부 ID 유지
        c("tabs", "rename", "t-001", "회고 재료")
        c("tabs", "apply")
        tabs = json.loads((self.data / "tabs.json").read_text(encoding="utf-8"))
        self.assertEqual(tabs["tabs"][0], {"id": "t-001", "label": "회고 재료", "blocks": ["sheet", "candidates", "next"]})

        # 기록 → 내 메모 후보
        c("record", "add", "--title", "도입 회의", "--file", str(self.notes / "회의 메모.md"), "--question", "q-001")
        jid = sorted(p.stem for p in (self.data / "journal").glob("j-*.md"))[0]
        self.assertIn("입력 담당을 먼저 정했다", (self.data / "journal" / f"{jid}.md").read_text(encoding="utf-8"))
        c("digest", "--record", jid, "--question", "q-001", "--no-model")
        c("digest", "--record", jid, "--question", "q-001", "--no-model")      # 두 번째는 중복 없음
        cands = json.loads((self.data / "candidates.json").read_text(encoding="utf-8"))["candidates"]
        self.assertEqual(len(cands), 1)
        self.assertEqual((cands[0]["origin"], cands[0]["status"]), ("self", "pending"))

        # 모델 전송 동의 없이는 모델 정리 거부
        r = c("digest", "--record", jid, "--question", "q-001", ok=False)
        self.assertIn("CONSENT-MISSING", r.stderr)
        c("consent", "--model-send", "yes")
        c("digest", "--record", jid, "--question", "q-001")
        cands = json.loads((self.data / "candidates.json").read_text(encoding="utf-8"))["candidates"]
        ai = [x for x in cands if x["origin"] == "ai"]
        self.assertEqual(len(ai), 1)
        self.assertEqual(ai[0]["status"], "pending")

        # 확인 전에는 '내가 확인한 판단'이 비어 있다
        sheet = (self.data / "sheets" / "q-001.md").read_text(encoding="utf-8")
        mine = sheet.split("## 내가 확인한 판단")[1].split("## 확인 대기")[0]
        self.assertNotIn("가짜 모델의 후보", mine)
        c("candidate", "confirm", "c-0001")
        c("candidate", "exclude", ai[0]["id"])
        sheet = (self.data / "sheets" / "q-001.md").read_text(encoding="utf-8")
        mine = sheet.split("## 내가 확인한 판단")[1].split("## 확인 대기")[0]
        self.assertIn("입력 담당을 먼저 정한다", mine)
        self.assertNotIn("가짜 모델의 후보", sheet)

        # 문장을 고친 뒤 같은 정리를 다시 돌려도 원래 문장이 새 후보로 되살아나지 않음
        c("candidate", "edit", "c-0001", "입력 담당부터 정한다 — 내 말로 고침")
        c("digest", "--record", jid, "--question", "q-001", "--no-model")
        c("digest", "--record", jid, "--question", "q-001")
        cands = json.loads((self.data / "candidates.json").read_text(encoding="utf-8"))["candidates"]
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["text"], "입력 담당부터 정한다 — 내 말로 고침")

        # 모델 실패 — 근거 없는 인용·형식 오류: 실패로 기록되고 이전 한 장 유지
        good = (self.data / "sheets" / "q-001.md").read_bytes()
        for mode, code in (("ungrounded", "MODEL-GROUNDING"), ("garbage", "MODEL-FORMAT")):
            r = c("digest", "--record", jid, "--question", "q-001", ok=False, env={"FAKE_MODE": mode})
            self.assertIn(code, r.stderr)
            self.assertEqual(good, (self.data / "sheets" / "q-001.md").read_bytes())
            runs = json.loads((self.data / ".crm" / "runs.json").read_text(encoding="utf-8"))
            self.assertEqual(runs["digest"]["state"], "failed")
            self.assertTrue(runs["digest"]["last_success"])

        # 읽기 전용 서버: 조회·새로고침이 파일을 바꾸지 않음, POST 거부, 경로 이탈 거부
        httpd = server.serve(self.data, 0)
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            before = snapshot(self.data)
            base = f"http://127.0.0.1:{port}"
            for _ in range(3):
                st = json.loads(urllib.request.urlopen(base + "/api/state").read())
            self.assertEqual([t["label"] for t in st["tabs"]], ["회고 재료", "글감"])
            self.assertEqual(st["errors"], [])
            self.assertTrue(all(v["ok"] for v in st["read"].values()), st["read"])
            self.assertEqual(set(st["next"]), {"kind", "target", "text", "command"})
            self.assertIsNone(SCREEN_LEAK.search(st["next"]["text"]), st["next"]["text"])
            self.assertEqual(urllib.request.urlopen(base + "/").status, 200)
            self.assertEqual(json.loads(urllib.request.urlopen(f"{base}/api/journal?id={jid}").read())["id"], jid)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/journal?id=../../profile")
            self.assertEqual(cm.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(base + "/api/state", data=b"x", method="POST"))
            self.assertEqual(cm.exception.code, 405)
            self.assertEqual(before, snapshot(self.data), "조회가 파일을 바꿈")
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(httpd.server_address[0], "127.0.0.1")

        # 복구 리허설: 깨뜨림 → doctor 실패 → restore → 같은 탭
        r = c("drill", "start")
        bid = json.loads((self.data / ".crm" / "drill.json").read_text(encoding="utf-8"))["backup"]
        # 깨진 탭 설정은 「못 읽음」으로 따로 내려가고, 나머지는 정상으로 읽힌다 (빈 값으로 숨기지 않음)
        st = server.build_state(self.data)
        self.assertFalse(st["read"]["tabs"]["ok"])
        self.assertEqual(st["read"]["tabs"]["error"]["code"], "FILE-CORRUPT")
        self.assertTrue(st["read"]["questions"]["ok"] and st["read"]["candidates"]["ok"])
        self.assertEqual(st["next"]["kind"], "blocked")
        r = c("doctor", "--port", "1", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DATA-CORRUPT", r.stdout)
        r = c("drill", "finish", ok=False)
        self.assertIn("DRILL-NOT-RECOVERED", r.stderr)
        # 백업 뒤에 쓴 기록은 복구해도 남는다
        c("record", "add", "--title", "복구 중 쓴 메모", "--text", "지우면 안 되는 새 기록")
        n_before = len(list((self.data / "journal").glob("j-*.md")))
        c("restore", bid)
        self.assertEqual(n_before, len(list((self.data / "journal").glob("j-*.md"))))
        c("drill", "finish")
        st = json.loads((self.data / "setup-state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["steps"].get("drill"), "done")

        # 진단: 원문·질문·경로 없음
        r = c("diag")
        self.assertNotIn("입력 담당", r.stdout)
        self.assertNotIn("새 도구를 들일 때", r.stdout)
        self.assertNotIn(str(self.tmp), r.stdout.split("저장:")[0])

    def test_next_action_shape_and_cli(self):
        from crmkit import data as D
        d = self.tmp / "다음 행동 시험"
        shutil.copytree(ROOT / "sample-data", d)
        act = D.next_action(d)
        self.assertEqual(set(act), {"kind", "target", "text", "command"})
        self.assertEqual(act["kind"], "setup")
        self.assertIsNone(SCREEN_LEAK.search(act["text"]))
        st = json.loads((d / "setup-state.json").read_text(encoding="utf-8"))
        st["steps"]["env"] = "done"
        (d / "setup-state.json").write_text(json.dumps(st), encoding="utf-8")
        p = json.loads((d / "profile.json").read_text(encoding="utf-8"))
        p["consent"]["updated"] = "2026-10-15T00:00:00+09:00"
        (d / "profile.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        act = D.next_action(d)
        self.assertEqual((act["kind"], act["target"]), ("review", "c-0002"))
        self.assertIsNone(SCREEN_LEAK.search(act["text"]))
        self.assertIn("candidate confirm c-0002", act["command"])
        r = subprocess.run([sys.executable, str(ROOT / "crm.py"), "--data", str(d), "next"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("아직 안 정한 문장이 2개", r.stdout)
        self.assertIn("→ python3 crm.py candidate confirm c-0002", r.stdout)
        # 깨진 후보 파일: 그 부분만 못 읽음, 질문은 정상, 0개로 위장하지 않음
        (d / "candidates.json").write_text("{ 깨짐", encoding="utf-8")
        before = snapshot(d)
        st = server.build_state(d)
        self.assertFalse(st["read"]["candidates"]["ok"])
        self.assertTrue(st["read"]["questions"]["ok"])
        self.assertEqual(st["read"]["candidates"]["error"]["code"], "FILE-CORRUPT")
        self.assertEqual(before, snapshot(d), "build_state 가 파일을 바꿈")

    def test_lock_blocks_second_writer(self):
        from crmkit import store
        d = self.tmp / "잠금 시험"
        d.mkdir()
        with store.lock(d, "첫 작업"):
            with self.assertRaises(store.CrmError) as cm:
                with store.lock(d, "둘째 작업"):
                    pass
            self.assertEqual(cm.exception.code, "RUN-LOCKED")
        with store.lock(d, "다시"):
            pass

    def test_atomic_write_keeps_old_on_failed_check(self):
        from crmkit import store
        f = self.tmp / "원자 시험.json"
        store.write_json_atomic(f, {"schemaVersion": 1, "tabs": []})
        old = f.read_text(encoding="utf-8")
        with self.assertRaises(store.CrmError):
            store.write_json_atomic(f, {"tabs": []}, validate=lambda d: ["schemaVersion 이 없습니다"])
        self.assertEqual(old, f.read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in self.tmp.glob(".원자 시험.json.tmp-*")], [])

    def test_sample_data_is_valid_and_marked(self):
        from crmkit import paths, schema
        for name in paths.SETTINGS:
            d = json.loads((ROOT / "sample-data" / name).read_text(encoding="utf-8"))
            self.assertEqual(schema.VALIDATORS[name](d), [], name)
        self.assertEqual(json.loads((ROOT / "sample-data" / "meta.json").read_text(encoding="utf-8"))["kind"], "sample")


if __name__ == "__main__":
    unittest.main()
