import unittest
from unittest.mock import MagicMock, patch

from software_recommend_system import retrieval_channels
from software_recommend_system.document_schema import Document


class KeywordRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        retrieval_channels._KEYWORD_RECALL_UNAVAILABLE_UNTIL = 0.0

    def tearDown(self) -> None:
        retrieval_channels._KEYWORD_RECALL_UNAVAILABLE_UNTIL = 0.0

    def test_es_results_preserve_existing_channel_contract(self) -> None:
        index = MagicMock()
        index.search.return_value = [
            Document(
                content="streamtool",
                metadata={"source": "startup_ingest", "doc_id": "guide"},
                score=2.3,
            )
        ]
        with patch.object(retrieval_channels, "get_keyword_index", return_value=index):
            docs = retrieval_channels.recall_keyword("streamtool application-ev", top_k=3, trace_id="test")
        index.search.assert_called_once_with("streamtool application-ev", 3)
        self.assertEqual(docs[0].metadata.doc_id, "guide")
        self.assertEqual(docs[0].score, 2.3)

    def test_blank_query_does_not_connect(self) -> None:
        with patch.object(retrieval_channels, "get_keyword_index") as get_index:
            self.assertEqual(retrieval_channels.recall_keyword("   ", top_k=3), [])
        get_index.assert_not_called()

    def test_disabled_channel_does_not_connect_even_for_agent_tool_calls(self) -> None:
        with patch.object(retrieval_channels.settings, "RECALL_ENABLE_KEYWORD", False), patch.object(
            retrieval_channels, "get_keyword_index",
        ) as get_index:
            self.assertEqual(retrieval_channels.recall_keyword("redis", 3), [])
        get_index.assert_not_called()

    def test_failure_opens_circuit_then_recovers(self) -> None:
        index = MagicMock()
        index.search.side_effect = TimeoutError("ES unavailable")
        with (
            patch.object(retrieval_channels, "get_keyword_index", return_value=index),
            patch.object(
                retrieval_channels.time,
                "time",
                return_value=100.0,
            ),
            patch.object(retrieval_channels.settings, "KEYWORD_RECALL_CIRCUIT_BREAKER_SECONDS", 30),
        ):
            self.assertEqual(retrieval_channels.recall_keyword("redis", 3), [])
            self.assertEqual(retrieval_channels.recall_keyword("redis", 3), [])
            self.assertEqual(index.search.call_count, 1)
        index.search.side_effect = None
        index.search.return_value = []
        with (
            patch.object(retrieval_channels, "get_keyword_index", return_value=index),
            patch.object(
                retrieval_channels.time,
                "time",
                return_value=131.0,
            ),
        ):
            self.assertEqual(retrieval_channels.recall_keyword("redis", 3), [])
        self.assertEqual(index.search.call_count, 2)
