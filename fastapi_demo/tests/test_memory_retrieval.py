import os
import tempfile
import unittest

from software_recommend_system.config import settings
from software_recommend_system.memory_retriever import build_memory_context
from software_recommend_system.memory_store import (
    reset_memory_store_for_tests,
    write_episode,
    write_fact,
    write_semantic,
)
from software_recommend_system.retrieval_channels import recall_memory


class MemoryRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original = {
            "MEMORY_STORE_FILE": settings.MEMORY_STORE_FILE,
            "FEATURE_LAYERED_MEMORY": settings.FEATURE_LAYERED_MEMORY,
            "MEMORY_WORKING_TOP_K": settings.MEMORY_WORKING_TOP_K,
            "MEMORY_EPISODIC_TOP_K": settings.MEMORY_EPISODIC_TOP_K,
            "MEMORY_SEMANTIC_TOP_K": settings.MEMORY_SEMANTIC_TOP_K,
            "MEMORY_FACT_TOP_K": settings.MEMORY_FACT_TOP_K,
            "MEMORY_CONTEXT_MAX_ITEMS": settings.MEMORY_CONTEXT_MAX_ITEMS,
            "MEMORY_SEMANTIC_MIN_SALIENCE": settings.MEMORY_SEMANTIC_MIN_SALIENCE,
        }
        settings.MEMORY_STORE_FILE = os.path.join(self._tmpdir.name, "memory_store.json")
        settings.FEATURE_LAYERED_MEMORY = True
        settings.MEMORY_WORKING_TOP_K = 3
        settings.MEMORY_EPISODIC_TOP_K = 3
        settings.MEMORY_SEMANTIC_TOP_K = 3
        settings.MEMORY_FACT_TOP_K = 3
        settings.MEMORY_CONTEXT_MAX_ITEMS = 10
        settings.MEMORY_SEMANTIC_MIN_SALIENCE = 0.3
        reset_memory_store_for_tests()

    def tearDown(self) -> None:
        reset_memory_store_for_tests()
        settings.MEMORY_STORE_FILE = self._original["MEMORY_STORE_FILE"]
        settings.FEATURE_LAYERED_MEMORY = self._original["FEATURE_LAYERED_MEMORY"]
        settings.MEMORY_WORKING_TOP_K = self._original["MEMORY_WORKING_TOP_K"]
        settings.MEMORY_EPISODIC_TOP_K = self._original["MEMORY_EPISODIC_TOP_K"]
        settings.MEMORY_SEMANTIC_TOP_K = self._original["MEMORY_SEMANTIC_TOP_K"]
        settings.MEMORY_FACT_TOP_K = self._original["MEMORY_FACT_TOP_K"]
        settings.MEMORY_CONTEXT_MAX_ITEMS = self._original["MEMORY_CONTEXT_MAX_ITEMS"]
        settings.MEMORY_SEMANTIC_MIN_SALIENCE = self._original["MEMORY_SEMANTIC_MIN_SALIENCE"]
        self._tmpdir.cleanup()

    def test_build_memory_context_contains_working_and_long_term(self) -> None:
        write_fact("s1", "user_name", "alice")
        write_episode("s1", "Need high availability architecture", tags=["ha"], salience=0.7)
        write_semantic("s1", "Redis cache benchmark and tradeoff", tags=["redis", "cache"], salience=0.8)

        context = build_memory_context(
            session_id="s1",
            query="redis cache 选型",
            messages=[
                {"role": "user", "content": "please compare redis and memcached"},
                {"role": "assistant", "content": "sure, let's compare them"},
            ],
            facts={"team_size": "small"},
        )
        self.assertTrue(context)
        levels = {str(item.get("level", "")) for item in context}
        self.assertIn("working", levels)
        self.assertIn("episodic", levels)
        self.assertIn("semantic", levels)
        self.assertTrue(any("user_name: alice" in str(item.get("content", "")) for item in context))

    def test_recall_memory_prefers_query_match(self) -> None:
        memory_context = [
            {
                "memory_id": "m1",
                "session_id": "s1",
                "level": "semantic",
                "content": "Redis cache latency benchmark",
                "tags": ["redis", "cache"],
                "score": 0.3,
                "salience": 0.6,
            },
            {
                "memory_id": "m2",
                "session_id": "s1",
                "level": "semantic",
                "content": "Kotlin Android migration",
                "tags": ["android"],
                "score": 0.95,
                "salience": 0.95,
            },
        ]

        docs = recall_memory(
            query="redis cache",
            session_id="s1",
            top_k=1,
            memory_context=memory_context,
        )
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata.doc_id, "m1")


if __name__ == "__main__":
    unittest.main()
