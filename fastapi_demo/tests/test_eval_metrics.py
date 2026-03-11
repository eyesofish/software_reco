import unittest

from evaluation.langsmith_evaluate import retrieval_recall


class EvalMetricsTests(unittest.TestCase):
    def test_retrieval_recall_prefers_full_doc_ids_when_available(self) -> None:
        run = {
            "outputs": {
                "retrieved_doc_ids": ["doc_a"],
                "retrieved_doc_ids_full": ["doc_b", "doc_c"],
            }
        }
        example = {"outputs": {"gold_doc_ids": ["doc_c"]}}

        result = retrieval_recall(run, example)
        self.assertEqual(result.key, "retrieval_recall")
        self.assertAlmostEqual(float(result.score), 1.0)
        self.assertEqual((result.metadata or {}).get("retrieved_source"), "retrieved_doc_ids_full")

    def test_retrieval_recall_fallback_to_top_doc_ids(self) -> None:
        run = {"outputs": {"retrieved_doc_ids": ["doc_a", "doc_b"]}}
        example = {"outputs": {"gold_doc_ids": ["doc_b", "doc_x"]}}

        result = retrieval_recall(run, example)
        self.assertAlmostEqual(float(result.score), 0.5)
        self.assertEqual((result.metadata or {}).get("retrieved_source"), "retrieved_doc_ids")


if __name__ == "__main__":
    unittest.main()
