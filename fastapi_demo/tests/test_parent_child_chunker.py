import unittest

from software_recommend_system.ingestion.chunker import chunk_documents_parent_child


class ParentChildChunkerTests(unittest.TestCase):
    def test_parent_child_ids_and_metadata_linkage(self) -> None:
        content = "abcdefghijklmnopqrstuvwxyz0123456789" * 25
        documents = [
            {
                "id": "doc-1",
                "content": content,
                "metadata": {"source": "unit_test"},
            }
        ]

        parent_chunks, child_chunks = chunk_documents_parent_child(
            documents=documents,
            parent_chunk_size=100,
            parent_chunk_overlap=10,
            child_chunk_size=40,
            child_chunk_overlap=8,
        )

        self.assertGreater(len(parent_chunks), 0)
        self.assertGreater(len(child_chunks), 0)

        parent_bounds = {}
        for parent_index, parent_chunk in enumerate(parent_chunks):
            parent_id = parent_chunk["id"]
            metadata = parent_chunk["metadata"]
            self.assertEqual(parent_id, f"doc-1:p:{parent_index}")
            self.assertEqual(metadata["source_doc_id"], "doc-1")
            self.assertEqual(metadata["parent_id"], parent_id)
            self.assertEqual(metadata["parent_index"], parent_index)
            self.assertLess(metadata["parent_start"], metadata["parent_end"])
            parent_bounds[parent_id] = (metadata["parent_start"], metadata["parent_end"])

        parent_child_counts = {parent_id: 0 for parent_id in parent_bounds}
        for child_chunk in child_chunks:
            child_id = child_chunk["id"]
            metadata = child_chunk["metadata"]
            parent_id = metadata["parent_id"]

            self.assertIn(parent_id, parent_bounds)
            self.assertTrue(child_id.startswith(f"{parent_id}:c:"))
            self.assertEqual(metadata["source_doc_id"], "doc-1")
            self.assertLess(metadata["chunk_start"], metadata["chunk_end"])

            parent_start, parent_end = parent_bounds[parent_id]
            self.assertGreaterEqual(metadata["chunk_start"], parent_start)
            self.assertLessEqual(metadata["chunk_end"], parent_end)
            parent_child_counts[parent_id] += 1

        self.assertTrue(all(count > 0 for count in parent_child_counts.values()))


if __name__ == "__main__":
    unittest.main()
