"""websearch 单元测试（全部 mock 网络，不消耗博查额度）：python3 tests/test_websearch.py"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import websearch

FAKE = {"code": 200, "data": {"webPages": {"value": [
    {"name": "商业转载", "url": "https://www.example.com/law", "snippet": "s2",
     "summary": "", "siteName": "示例网", "dateLastCrawled": "2026-01-02T00:00:00Z"},
    {"name": "官方法规", "url": "https://flk.npc.gov.cn/detail.html?id=1", "snippet": "s1",
     "summary": "全文…", "siteName": "国家法律法规数据库", "dateLastCrawled": "2026-01-01T08:00:00Z"},
]}}}


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class TestIsOfficial(unittest.TestCase):
    def test_gov_domains(self):
        self.assertTrue(websearch.is_official("https://flk.npc.gov.cn/x"))
        self.assertTrue(websearch.is_official("https://rsj.sh.gov.cn/y"))
        self.assertTrue(websearch.is_official("https://www.gov.cn/zhengce"))

    def test_non_official_and_spoof(self):
        self.assertFalse(websearch.is_official("https://www.example.com/law"))
        self.assertFalse(websearch.is_official("https://gov.cn.evil.com/x"))  # 后缀伪装
        self.assertFalse(websearch.is_official("not a url"))


class TestSearch(unittest.TestCase):
    def _search(self, payload, **kw):
        with mock.patch.object(websearch.urllib.request, "urlopen",
                               return_value=FakeResp(payload)), \
             mock.patch.object(websearch, "_api_key", return_value="sk-test"):
            return websearch.search("劳动合同法", **kw)

    def test_parse_and_official_first(self):
        rs = self._search(FAKE)
        self.assertEqual(len(rs), 2)
        self.assertTrue(rs[0]["official"])           # 官方源排前
        self.assertEqual(rs[0]["site"], "国家法律法规数据库")
        self.assertEqual(rs[0]["date"], "2026-01-01")  # 日期截到天

    def test_official_only_filter(self):
        rs = self._search(FAKE, official_only=True)
        self.assertEqual(len(rs), 1)
        self.assertTrue(all(x["official"] for x in rs))

    def test_api_error_code(self):
        with self.assertRaises(RuntimeError):
            self._search({"code": 403, "msg": "Invalid token"})

    def test_empty_pages_ok(self):
        self.assertEqual(self._search({"code": 200, "data": {}}), [])

    def test_missing_key_raises(self):
        with mock.patch.dict(websearch.os.environ, {"BOCHA_API_KEY": ""}, clear=False), \
             mock.patch.object(websearch, "ROOT", Path("/nonexistent")):
            with self.assertRaises(RuntimeError):
                websearch._api_key()


if __name__ == "__main__":
    unittest.main(verbosity=2)
