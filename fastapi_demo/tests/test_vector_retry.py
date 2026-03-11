import unittest
from unittest.mock import MagicMock, patch

from software_recommend_system.tools import similarity_search


class VectorRecallRetryTests(unittest.TestCase):
    def test_similarity_search_retries_once_on_tenant_error(self) -> None:
        tenant_error = ValueError("Could not connect to tenant default_tenant. Are you sure it exists?")

        collection = MagicMock()
        collection.query.return_value = {
            "documents": [["doc content"]],
            "metadatas": [[{"source": "vector", "source_doc_id": "doc-1"}]],
            "distances": [[0.12]],
        }

        client = MagicMock()
        client.list_collections.return_value = [type("Col", (), {"name": "software_recommendations"})()]
        client.get_collection.return_value = collection

        with patch("software_recommend_system.tools.chromadb.PersistentClient", side_effect=[tenant_error, client]) as mocked_client, patch(
            "software_recommend_system.tools.embed_texts", return_value=[[0.01, 0.02]]
        ):
            docs = similarity_search("test query", k=1, trace_id="test-vector")

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata.doc_id, "doc-1")
        self.assertEqual(mocked_client.call_count, 2)


if __name__ == "__main__":
    unittest.main()
