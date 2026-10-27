"""L1/L2 检索 + L3 知识 + L4 教训的单元测试。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

import knowledge  # noqa: E402
from memory_store import MemoryStore, save_key  # noqa: E402

SNAP = {"instanceId": "t1", "saveGeneration": 1, "location": "Farm"}


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = SNAP
        self.scope = save_key(SNAP)

    def tearDown(self):
        self.store.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def _note(self, text, kind="reflection"):
        self.store.append(kind, SNAP, data={"text": text})

    def test_bigram_query_finds_substring(self):
        """LIKE 只能整词命中；bigram 检索要能找到"种子"和"防风草"。"""
        self._note("去小镇买防风草种子")
        self._note("今天天气不错")
        hits = self.store.query("种子", limit=5, snap=SNAP)
        texts = [h["data"].get("text", "") for h in hits]
        self.assertTrue(any("防风草种子" in t for t in texts), texts)

    def test_ranking_prefers_more_relevant(self):
        self._note("出口在农场北边 (78,17)")
        self._note("随便记一笔：今天浇水了")
        self._note("出口坐标要记住，出口在 Town 西边 (0,54)")
        hits = self.store.query("出口 坐标", limit=2, snap=SNAP)
        self.assertEqual(len(hits), 2)
        self.assertIn("出口", hits[0]["data"].get("text", ""), hits)

    def test_lessons_are_returned_and_persist(self):
        self.store.add_lesson("没有种子时先浇水，别去找种子", snap=SNAP)
        self.store.add_lesson("农舍门不是移动目标", snap=SNAP)
        lessons = self.store.lessons(snap=SNAP)
        self.assertEqual(len(lessons), 2)
        self.assertIn("农舍门", " ".join(lessons))

    def test_lesson_kind_has_no_like_fallback_breakage(self):
        """查询空文本时不应报错，仍按时间返回。"""
        self._note("abc")
        self.assertTrue(self.store.query("", limit=3, snap=SNAP))

    def test_rebuild_grams_indexes_existing_events(self):
        self._note("历史数据：皮埃尔种子店在小镇")
        self.store.db.execute("DELETE FROM grams")
        self.store.db.commit()
        n = self.store.rebuild_grams()
        self.assertGreaterEqual(n, 1)
        hits = self.store.query("皮埃尔", limit=3, snap=SNAP)
        self.assertTrue(hits, "重建索引后应该能检索到")

    def test_scope_isolation(self):
        self._note("农场的事")
        other = dict(SNAP, instanceId="t2")
        self.assertEqual(self.store.query("农场", limit=5, snap=other), [],
                         "不同存档/实例的记忆不能混")


class KnowledgeTests(unittest.TestCase):
    def test_digest_has_exits_and_rules(self):
        lines = knowledge.digest()
        joined = "\n".join(lines)
        self.assertIn("Farm → BusStop", joined)
        self.assertIn("规则：", joined)
        self.assertLessEqual(len(lines), 28)

    def test_unknown_shop_is_marked_unknown(self):
        joined = "\n".join(knowledge.digest())
        self.assertIn("皮埃尔", joined)
        self.assertTrue(("坐标未知" in joined) or ("未知" in joined), joined)


if __name__ == "__main__":
    unittest.main()
