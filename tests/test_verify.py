"""法条核验流水线测试（T1.4）：python3 tests/test_verify.py

diff 核心是纯函数，直接测；approve 用临时 seed 目录隔离，不碰真实数据。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import verify_articles as va


class TestNorm(unittest.TestCase):
    def test_strips_whitespace_keeps_chars(self):
        self.assertEqual(va.norm("第一条　本法\n规定。 "), "第一条本法规定。")
        self.assertEqual(va.norm("a b\tc\n"), "abc")


class TestDiff(unittest.TestCase):
    def test_identical(self):
        seed = [{"article_no": "第一条", "clause_no": None, "text": "本法 规定。"}]
        d = va.diff_articles(seed, [("第一条", "本法规定。")])
        self.assertTrue(d["clean"])
        self.assertEqual(d["counts"]["identical"], 1)

    def test_text_differs_reports_first_diff(self):
        seed = [{"article_no": "第一条", "clause_no": None, "text": "本法自2008年施行。分享"}]
        d = va.diff_articles(seed, [("第一条", "本法自2008年施行。")])
        self.assertFalse(d["clean"])
        self.assertEqual(d["counts"]["differs"], 1)
        self.assertIn("首差", d["rows"][0]["detail"])

    def test_missing_in_source(self):
        seed = [{"article_no": "第九十九条", "clause_no": None, "text": "x"}]
        d = va.diff_articles(seed, [("第一条", "y")])
        self.assertEqual(d["counts"]["missing_in_source"], 1)
        self.assertFalse(d["clean"])

    def test_clause_substring_match(self):
        # 拆款行：款文须落在父条官方原文内
        seed = [
            {"article_no": "第五条", "clause_no": None, "text": "甲。\n乙。\n丙。"},
            {"article_no": "第五条", "clause_no": "三", "text": "丙。"},
        ]
        d = va.diff_articles(seed, [("第五条", "甲。\n乙。\n丙。")])
        self.assertTrue(d["clean"])
        self.assertEqual(d["counts"]["identical"], 2)

    def test_clause_not_in_parent_differs(self):
        seed = [
            {"article_no": "第五条", "clause_no": None, "text": "甲。\n乙。"},
            {"article_no": "第五条", "clause_no": "三", "text": "不存在的款。"},
        ]
        d = va.diff_articles(seed, [("第五条", "甲。\n乙。")])
        self.assertEqual(d["counts"]["differs"], 1)

    def test_empty_seed_not_clean(self):
        self.assertFalse(va.diff_articles([], [])["clean"])


class TestApprove(unittest.TestCase):
    """approve 用临时目录隔离：clean 才置 verified，有差异拒绝。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.laws = Path(self.tmp.name) / "laws"
        self.raw = Path(self.tmp.name) / "raw"
        self.laws.mkdir(); self.raw.mkdir()
        self._orig = (va.LAWS, va.RAW)
        va.LAWS, va.RAW = self.laws, self.raw

    def tearDown(self):
        va.LAWS, va.RAW = self._orig
        self.tmp.cleanup()

    def _write(self, slug, articles, raw_text):
        (self.laws / f"{slug}.json").write_text(
            json.dumps({"title": "测试法", "articles": articles}, ensure_ascii=False),
            encoding="utf-8")
        (self.raw / f"{slug}.txt").write_text(raw_text, encoding="utf-8")

    def test_approve_clean_sets_verified(self):
        self._write("t", [{"article_no": "第一条", "clause_no": None,
                           "text": "本法规定。", "verified": False}],
                    "第一条 本法规定。")
        rc = va.cmd_approve("t", "测试员", {"laws": []})
        self.assertEqual(rc, 0)
        seed = json.loads((self.laws / "t.json").read_text(encoding="utf-8"))
        self.assertTrue(seed["articles"][0]["verified"])
        self.assertEqual(seed["articles"][0]["verified_by"], "测试员")
        self.assertIn("verified_at", seed["articles"][0])

    def test_approve_refuses_when_differs(self):
        self._write("t", [{"article_no": "第一条", "clause_no": None,
                           "text": "本法规定。脏数据", "verified": False}],
                    "第一条 本法规定。")
        rc = va.cmd_approve("t", "测试员", {"laws": []})
        self.assertEqual(rc, 1)  # 拒绝
        seed = json.loads((self.laws / "t.json").read_text(encoding="utf-8"))
        self.assertFalse(seed["articles"][0]["verified"])  # 未被置位


if __name__ == "__main__":
    unittest.main(verbosity=2)
