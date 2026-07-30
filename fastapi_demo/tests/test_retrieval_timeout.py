import threading
import time
import unittest
from unittest.mock import patch

from software_recommend_system import retriever
from software_recommend_system.document_schema import Document, Metadata


def _doc(doc_id: str, channel: str) -> Document:
    return Document(
        content=f"content {doc_id}",
        metadata=Metadata(source=channel, doc_id=doc_id),
        score=0.9,
    )


class ResolveChannelTimeoutTests(unittest.TestCase):
    def test_positive_setting_is_used(self) -> None:
        with patch.object(retriever.settings, "RECALL_CHANNEL_TIMEOUT_SECONDS", 12.5):
            self.assertEqual(retriever._resolve_channel_timeout(), 12.5)

    def test_zero_disables_budget(self) -> None:
        with patch.object(retriever.settings, "RECALL_CHANNEL_TIMEOUT_SECONDS", 0):
            self.assertIsNone(retriever._resolve_channel_timeout())

    def test_negative_disables_budget(self) -> None:
        with patch.object(retriever.settings, "RECALL_CHANNEL_TIMEOUT_SECONDS", -3):
            self.assertIsNone(retriever._resolve_channel_timeout())

    def test_override_wins_over_setting(self) -> None:
        with patch.object(retriever.settings, "RECALL_CHANNEL_TIMEOUT_SECONDS", 30.0):
            self.assertEqual(retriever._resolve_channel_timeout(2.0), 2.0)

    def test_garbage_setting_falls_back_to_disabled(self) -> None:
        with patch.object(retriever.settings, "RECALL_CHANNEL_TIMEOUT_SECONDS", "not-a-number"):
            self.assertIsNone(retriever._resolve_channel_timeout())


class RetrieveChannelTimeoutTests(unittest.TestCase):
    """A slow channel must not hold the whole fan-out past its budget."""

    def setUp(self) -> None:
        self.settings_patches = [
            patch.object(retriever.settings, "RECALL_ENABLE_VECTOR", True),
            patch.object(retriever.settings, "RECALL_ENABLE_IMAGE_VECTOR", False),
            patch.object(retriever.settings, "RECALL_ENABLE_WEB", True),
            patch.object(retriever.settings, "RECALL_ENABLE_KEYWORD", False),
            patch.object(retriever.settings, "RECALL_ENABLE_MEMORY", False),
            patch.object(retriever.settings, "RETRIEVAL_ENABLE_RERANK", False),
        ]
        for item in self.settings_patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in self.settings_patches])

    def test_slow_channel_is_dropped_and_fast_channel_survives(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_web(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            release.wait(timeout=30)
            return [_doc("web-1", "web")]

        def fast_vector(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            return [_doc("vec-1", "vector")]

        with (
            patch.object(retriever, "recall_web", side_effect=slow_web),
            patch.object(retriever, "recall_vector", side_effect=fast_vector),
        ):
            started = time.perf_counter()
            docs = retriever.retrieve("kafka vs pulsar", top_k=5, channel_timeout_seconds=0.4)
            elapsed = time.perf_counter() - started

        # Returned on budget rather than waiting on the straggler.
        self.assertLess(elapsed, 10.0)

        doc_ids = {doc.metadata.doc_id for doc in docs}
        self.assertIn("vec-1", doc_ids)
        self.assertNotIn("web-1", doc_ids)

    def test_all_channels_returned_when_none_are_slow(self) -> None:
        def fast_web(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            return [_doc("web-1", "web")]

        def fast_vector(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            return [_doc("vec-1", "vector")]

        with (
            patch.object(retriever, "recall_web", side_effect=fast_web),
            patch.object(retriever, "recall_vector", side_effect=fast_vector),
        ):
            docs = retriever.retrieve("kafka vs pulsar", top_k=5, channel_timeout_seconds=10.0)

        doc_ids = {doc.metadata.doc_id for doc in docs}
        self.assertIn("vec-1", doc_ids)
        self.assertIn("web-1", doc_ids)

    def test_channel_exception_still_degrades_to_empty(self) -> None:
        def boom(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            raise RuntimeError("tavily exploded")

        def fast_vector(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            return [_doc("vec-1", "vector")]

        with (
            patch.object(retriever, "recall_web", side_effect=boom),
            patch.object(retriever, "recall_vector", side_effect=fast_vector),
        ):
            docs = retriever.retrieve("kafka vs pulsar", top_k=5, channel_timeout_seconds=10.0)

        self.assertEqual({doc.metadata.doc_id for doc in docs}, {"vec-1"})

    def test_disabled_budget_waits_for_every_channel(self) -> None:
        def slowish_web(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            time.sleep(0.3)
            return [_doc("web-1", "web")]

        def fast_vector(query: str, top_k: int, trace_id: str | None = None) -> list[Document]:
            return [_doc("vec-1", "vector")]

        with (
            patch.object(retriever, "recall_web", side_effect=slowish_web),
            patch.object(retriever, "recall_vector", side_effect=fast_vector),
        ):
            docs = retriever.retrieve("kafka vs pulsar", top_k=5, channel_timeout_seconds=0)

        doc_ids = {doc.metadata.doc_id for doc in docs}
        self.assertIn("web-1", doc_ids)


if __name__ == "__main__":
    unittest.main()
