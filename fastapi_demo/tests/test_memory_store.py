import os
import tempfile
import unittest

from software_recommend_system.config import settings
from software_recommend_system.memory_store import (
    get_facts,
    get_recent,
    reset_memory_store_for_tests,
    semantic_search,
    write_episode,
    write_fact,
    write_semantic,
)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original = {
            "MEMORY_STORE_FILE": settings.MEMORY_STORE_FILE,
            "MEMORY_SEMANTIC_MIN_SALIENCE": settings.MEMORY_SEMANTIC_MIN_SALIENCE,
        }
        settings.MEMORY_STORE_FILE = os.path.join(self._tmpdir.name, "memory_store.json")
        settings.MEMORY_SEMANTIC_MIN_SALIENCE = 0.3
        reset_memory_store_for_tests()

    def tearDown(self) -> None:
        reset_memory_store_for_tests()
        settings.MEMORY_STORE_FILE = self._original["MEMORY_STORE_FILE"]
        settings.MEMORY_SEMANTIC_MIN_SALIENCE = self._original["MEMORY_SEMANTIC_MIN_SALIENCE"]
        self._tmpdir.cleanup()

    def test_write_fact_overwrites_latest_value(self) -> None:
        write_fact("s1", "user_name", "alice")
        write_fact("s1", "user_name", "bob")
        facts = get_facts("s1")
        self.assertEqual(facts.get("user_name"), "bob")

    def test_write_episode_and_recent_roundtrip(self) -> None:
        write_episode("s1", "Prefer open source stack", tags=["preference"], salience=0.6)
        write_episode("s1", "Need HA architecture", tags=["availability"], salience=0.7)
        items = get_recent("s1", limit=5, levels=["episodic"])
        self.assertGreaterEqual(len(items), 2)
        self.assertTrue(all(item.level == "episodic" for item in items))

    def test_semantic_search_returns_relevant_memory(self) -> None:
        write_semantic("s1", "Redis cache latency benchmark", tags=["redis", "cache"], salience=0.9)
        write_semantic("s1", "Kotlin Android migration guide", tags=["android"], salience=0.8)

        hits = semantic_search("s1", "redis cache", top_k=2)
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("Redis", hits[0].content)


if __name__ == "__main__":
    unittest.main()
