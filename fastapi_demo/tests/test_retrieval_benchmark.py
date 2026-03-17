import unittest

from evaluation.retrieval_benchmark import compute_retrieval_metrics


class RetrievalBenchmarkMetricTests(unittest.TestCase):
    def test_recall_hit_and_ndcg_compute_from_ranked_doc_ids(self) -> None:
        metrics = compute_retrieval_metrics(
            gold_doc_ids=["doc_a", "doc_b"],
            retrieved_doc_ids=["doc_x", "doc_a", "doc_y", "doc_b"],
        )

        self.assertAlmostEqual(metrics["recall_at_5"], 1.0)
        self.assertAlmostEqual(metrics["recall_at_10"], 1.0)
        self.assertEqual(metrics["hit_at_5"], 1.0)
        self.assertEqual(metrics["hit_at_10"], 1.0)
        self.assertGreater(metrics["ndcg_at_10"], 0.0)
        self.assertLess(metrics["ndcg_at_10"], 1.0)

    def test_metrics_handle_no_hits(self) -> None:
        metrics = compute_retrieval_metrics(
            gold_doc_ids=["doc_a"],
            retrieved_doc_ids=["doc_x", "doc_y", "doc_z"],
        )

        self.assertEqual(metrics["recall_at_5"], 0.0)
        self.assertEqual(metrics["recall_at_10"], 0.0)
        self.assertEqual(metrics["hit_at_5"], 0.0)
        self.assertEqual(metrics["hit_at_10"], 0.0)
        self.assertEqual(metrics["ndcg_at_10"], 0.0)

    def test_metrics_require_gold_doc_ids(self) -> None:
        with self.assertRaises(ValueError):
            compute_retrieval_metrics(gold_doc_ids=[], retrieved_doc_ids=["doc_a"])


if __name__ == "__main__":
    unittest.main()
