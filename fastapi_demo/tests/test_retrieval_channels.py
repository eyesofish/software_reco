import unittest
from unittest.mock import MagicMock, patch

from software_recommend_system import retrieval_channels


class KeywordRecallFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        retrieval_channels._KEYWORD_RECALL_UNAVAILABLE_UNTIL = 0.0

    def tearDown(self) -> None:
        retrieval_channels._KEYWORD_RECALL_UNAVAILABLE_UNTIL = 0.0

    def test_keyword_recall_retries_with_fresh_client_on_tenant_error(self) -> None:
        tenant_error = ValueError("Could not connect to tenant default_tenant. Are you sure it exists?")

        collection = MagicMock()
        collection.get.return_value = {
            "documents": ["streamtool setproperty --application-ev ODBCINI=/tmp/odbc.ini"],
            "metadatas": [
                {
                    "source": "keyword_index",
                    "source_doc_id": "swg21996508.txt",
                    "tags": ["streamtool", "application-ev"],
                }
            ],
        }

        client = MagicMock()
        client.get_or_create_collection.return_value = collection

        with patch.object(retrieval_channels, "get_chroma_collection", side_effect=tenant_error), patch.object(
            retrieval_channels.chromadb, "PersistentClient", return_value=client
        ):
            docs = retrieval_channels.recall_keyword("streamtool application-ev", top_k=3, trace_id="test-keyword")

        self.assertEqual(len(docs), 1)
        self.assertEqual(getattr(docs[0].metadata, "doc_id", ""), "swg21996508.txt")
        self.assertGreater(docs[0].score, 0.0)
        client.get_or_create_collection.assert_called_once()

    def test_keyword_recall_uses_bm25_and_indexes_tags(self) -> None:
        with patch.object(
            retrieval_channels,
            "_collection_get_documents",
            return_value={
                "documents": [
                    "streamtool setproperty updates environment values",
                    "streamtool command reference for operators",
                    "java spring deployment guide",
                ],
                "metadatas": [
                    {
                        "source": "keyword_index",
                        "doc_id": "best-match",
                        "tags": ["application-ev", "odbcini"],
                    },
                    {
                        "source": "keyword_index",
                        "doc_id": "partial-match",
                        "tags": [],
                    },
                    {
                        "source": "keyword_index",
                        "doc_id": "no-match",
                        "tags": ["spring"],
                    },
                ],
            },
        ):
            docs = retrieval_channels.recall_keyword("streamtool application-ev", top_k=3, trace_id="test-keyword")

        self.assertEqual([getattr(doc.metadata, "doc_id", "") for doc in docs], ["best-match", "partial-match"])
        self.assertGreater(docs[0].score, docs[1].score)
        self.assertEqual(getattr(docs[0].metadata, "tags", []), ["application-ev", "odbcini"])

    def test_keyword_recall_returns_empty_for_blank_query_without_collection_read(self) -> None:
        with patch.object(retrieval_channels, "_collection_get_documents") as mocked_get_documents:
            docs = retrieval_channels.recall_keyword("   ", top_k=3, trace_id="test-keyword")

        self.assertEqual(docs, [])
        mocked_get_documents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
