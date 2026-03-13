import unittest
from unittest.mock import MagicMock, patch

from software_recommend_system import tools


class SimilaritySearchParentChildTests(unittest.TestCase):
    @staticmethod
    def _mock_client_for_collection(collection: MagicMock) -> MagicMock:
        client = MagicMock()
        client.list_collections.return_value = [type("Col", (), {"name": "software_recommendations"})()]
        client.get_collection.return_value = collection
        return client

    def test_similarity_search_returns_parent_content_and_keeps_doc_id_compat(self) -> None:
        collection = MagicMock()
        collection.query.return_value = {
            "documents": [["child doc 1", "child doc 1 alt", "child doc 2"]],
            "metadatas": [
                [
                    {"source": "vector", "source_doc_id": "doc-1", "parent_id": "doc-1:p:0"},
                    {"source": "vector", "source_doc_id": "doc-1", "parent_id": "doc-1:p:0"},
                    {"source": "vector", "source_doc_id": "doc-2", "parent_id": "doc-2:p:0"},
                ]
            ],
            "distances": [[0.11, 0.28, 0.35]],
        }
        client = self._mock_client_for_collection(collection)
        parent_lookup = {
            "doc-1:p:0": {
                "id": "doc-1:p:0",
                "content": "parent doc 1",
                "metadata": {"source": "vector_parent"},
            },
            "doc-2:p:0": {
                "id": "doc-2:p:0",
                "content": "parent doc 2",
                "metadata": {"source": "vector_parent", "source_doc_id": "doc-2"},
            },
        }

        with patch.object(tools.settings, "ENABLE_PARENT_CHILD_CHUNKING", True), patch.object(
            tools.settings, "PARENT_COLLECTION_NAME", "software_recommendations_parent"
        ), patch("software_recommend_system.tools.chromadb.PersistentClient", return_value=client), patch(
            "software_recommend_system.tools.embed_texts", return_value=[[0.01, 0.02]]
        ), patch(
            "software_recommend_system.tools.get_parent_documents_by_ids", return_value=parent_lookup
        ) as mocked_parent_get:
            docs = tools.similarity_search("test query", k=2, trace_id="test-parent-child")

        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0].content, "parent doc 1")
        self.assertEqual(docs[0].metadata.doc_id, "doc-1")
        self.assertEqual(docs[1].content, "parent doc 2")
        self.assertEqual(docs[1].metadata.doc_id, "doc-2")
        mocked_parent_get.assert_called_once()

    def test_similarity_search_falls_back_to_child_content_when_parent_missing(self) -> None:
        collection = MagicMock()
        collection.query.return_value = {
            "documents": [["child fallback content"]],
            "metadatas": [[{"source": "vector", "source_doc_id": "doc-1", "parent_id": "doc-1:p:0"}]],
            "distances": [[0.19]],
        }
        client = self._mock_client_for_collection(collection)

        with patch.object(tools.settings, "ENABLE_PARENT_CHILD_CHUNKING", True), patch.object(
            tools.settings, "PARENT_COLLECTION_NAME", "software_recommendations_parent"
        ), patch("software_recommend_system.tools.chromadb.PersistentClient", return_value=client), patch(
            "software_recommend_system.tools.embed_texts", return_value=[[0.03, 0.04]]
        ), patch(
            "software_recommend_system.tools.get_parent_documents_by_ids", return_value={}
        ) as mocked_parent_get:
            docs = tools.similarity_search("test query", k=1, trace_id="test-parent-miss")

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].content, "child fallback content")
        self.assertEqual(docs[0].metadata.doc_id, "doc-1")
        mocked_parent_get.assert_called_once_with(
            ["doc-1:p:0"],
            collection_name="software_recommendations_parent",
        )


if __name__ == "__main__":
    unittest.main()
