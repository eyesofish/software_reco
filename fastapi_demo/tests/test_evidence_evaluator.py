import unittest
from types import SimpleNamespace
from unittest.mock import patch

from software_recommend_system import evidence_evaluator as ev
from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.nodes import evidence_evaluation_node
from software_recommend_system.state import AgentState


def _doc(content: str, score: float) -> Document:
    return Document(content=content, metadata=Metadata(source="vector", doc_id="d"), score=score)


def _item(content: str, score: float) -> dict:
    return {
        "question": "q",
        "documents": [_doc(content, score)],
        "search_results": [],
        "quality_score": 0.0,
    }


def _judge_response(payload: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class _FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

        def create(**kwargs):
            self.calls += 1
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class HeuristicScoreTests(unittest.TestCase):
    def test_empty_documents_score_zero(self) -> None:
        self.assertEqual(ev.heuristic_quality_score("kafka", {"documents": []}), 0.0)

    def test_relevant_substantial_doc_beats_irrelevant_one(self) -> None:
        relevant = _item("kafka partitions provide ordering guarantees " * 8, 0.9)
        irrelevant = _item("unrelated cooking recipe text " * 8, 0.9)
        self.assertGreater(
            ev.heuristic_quality_score("kafka partitions ordering", relevant),
            ev.heuristic_quality_score("kafka partitions ordering", irrelevant),
        )

    def test_near_empty_snippet_is_penalized(self) -> None:
        """Old `avg + 0.1` gave a one-word snippet the same credit as a passage."""
        tiny = _item("kafka", 0.9)
        full = _item("kafka " * 200, 0.9)
        self.assertLess(
            ev.heuristic_quality_score("kafka", tiny),
            ev.heuristic_quality_score("kafka", full),
        )

    def test_score_never_exceeds_one_or_drops_below_zero(self) -> None:
        for score in (-5.0, 0.0, 0.5, 1.0, 99.0):
            value = ev.heuristic_quality_score("kafka", _item("kafka " * 100, score))
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_is_deterministic(self) -> None:
        item = _item("kafka partitions " * 20, 0.7)
        first = ev.heuristic_quality_score("kafka partitions", item)
        for _ in range(5):
            self.assertEqual(ev.heuristic_quality_score("kafka partitions", item), first)

    def test_zero_retrieval_score_no_overlap_is_low(self) -> None:
        item = _item("completely different subject matter", 0.0)
        self.assertLess(ev.heuristic_quality_score("kafka partitions", item), 0.2)


class LlmJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(ev.settings, "EVIDENCE_EVAL_MODEL", "judge-model")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_payload_is_used(self) -> None:
        client = _FakeClient(_judge_response('{"scores":[{"index":1,"support":0.9},{"index":2,"support":0.1}]}'))
        scores = ev.llm_quality_scores("q", [_item("a", 0.5), _item("b", 0.5)], client=client)
        self.assertEqual(scores, [0.9, 0.1])

    def test_out_of_range_support_is_clamped(self) -> None:
        client = _FakeClient(_judge_response('{"scores":[{"index":1,"support":7}]}'))
        self.assertEqual(ev.llm_quality_scores("q", [_item("a", 0.5)], client=client), [1.0])

    def test_code_fenced_json_is_parsed(self) -> None:
        client = _FakeClient(_judge_response('```json\n{"scores":[{"index":1,"support":0.4}]}\n```'))
        self.assertEqual(ev.llm_quality_scores("q", [_item("a", 0.5)], client=client), [0.4])

    def test_length_mismatch_returns_none(self) -> None:
        client = _FakeClient(_judge_response('{"scores":[{"index":1,"support":0.9}]}'))
        self.assertIsNone(ev.llm_quality_scores("q", [_item("a", 0.5), _item("b", 0.5)], client=client))

    def test_duplicate_index_returns_none(self) -> None:
        client = _FakeClient(_judge_response('{"scores":[{"index":1,"support":0.9},{"index":1,"support":0.2}]}'))
        self.assertIsNone(ev.llm_quality_scores("q", [_item("a", 0.5), _item("b", 0.5)], client=client))

    def test_malformed_json_returns_none(self) -> None:
        client = _FakeClient(_judge_response("not json at all"))
        self.assertIsNone(ev.llm_quality_scores("q", [_item("a", 0.5)], client=client))

    def test_exception_returns_none(self) -> None:
        client = _FakeClient(RuntimeError("upstream down"))
        self.assertIsNone(ev.llm_quality_scores("q", [_item("a", 0.5)], client=client))

    def test_batch_larger_than_limit_returns_none(self) -> None:
        client = _FakeClient(_judge_response('{"scores":[]}'))
        items = [_item("a", 0.5) for _ in range(9)]
        with patch.object(ev.settings, "EVIDENCE_EVAL_MAX_DOCS", 8):
            self.assertIsNone(ev.llm_quality_scores("q", items, client=client))
        self.assertEqual(client.calls, 0)

    def test_missing_model_returns_none_without_calling(self) -> None:
        client = _FakeClient(_judge_response('{"scores":[{"index":1,"support":0.9}]}'))
        with (
            patch.object(ev.settings, "EVIDENCE_EVAL_MODEL", ""),
            patch.object(ev.settings, "LLM_MODEL", ""),
        ):
            self.assertIsNone(ev.llm_quality_scores("q", [_item("a", 0.5)], client=client))
        self.assertEqual(client.calls, 0)


class EvaluateEvidenceTests(unittest.TestCase):
    def test_flag_off_uses_heuristic_and_never_calls_llm(self) -> None:
        with (
            patch.object(ev.settings, "EVIDENCE_EVAL_USE_LLM", False),
            patch.object(ev, "llm_quality_scores") as mocked,
        ):
            scores, mode = ev.evaluate_evidence("kafka", [_item("kafka " * 50, 0.8)])
        mocked.assert_not_called()
        self.assertEqual(mode, "heuristic")
        self.assertEqual(len(scores), 1)

    def test_flag_on_uses_judge(self) -> None:
        with (
            patch.object(ev.settings, "EVIDENCE_EVAL_USE_LLM", True),
            patch.object(ev, "llm_quality_scores", return_value=[0.42]),
        ):
            scores, mode = ev.evaluate_evidence("kafka", [_item("kafka", 0.8)])
        self.assertEqual(scores, [0.42])
        self.assertEqual(mode, "llm_judge")

    def test_judge_failure_falls_back_to_heuristic(self) -> None:
        """Offline / no API key must degrade, not raise."""
        with (
            patch.object(ev.settings, "EVIDENCE_EVAL_USE_LLM", True),
            patch.object(ev, "llm_quality_scores", return_value=None),
        ):
            scores, mode = ev.evaluate_evidence("kafka", [_item("kafka " * 50, 0.8)])
        self.assertEqual(mode, "heuristic")
        self.assertEqual(len(scores), 1)

    def test_empty_items(self) -> None:
        self.assertEqual(ev.evaluate_evidence("q", []), ([], "heuristic"))


class EvidenceEvaluationNodeTests(unittest.TestCase):
    def test_node_writes_scores_for_every_item(self) -> None:
        """AgentState coerces evidence dicts into EvidenceItem models, so the
        evaluator must work on pydantic objects, not just dicts."""
        state = AgentState(
            user_query="kafka partitions ordering",
            evidence=[_item("kafka partitions ordering " * 20, 0.9), _item("noise", 0.1)],
        )
        with patch.object(ev.settings, "EVIDENCE_EVAL_USE_LLM", False):
            result = evidence_evaluation_node(state)

        items = result["evidence"]
        self.assertFalse(any(isinstance(item, dict) for item in items))
        scores = [item.quality_score for item in items]
        self.assertEqual(len(scores), 2)
        self.assertGreater(scores[0], scores[1])
        for value in scores:
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_scoring_works_on_plain_dicts_too(self) -> None:
        items = [_item("kafka partitions " * 20, 0.9)]
        scores, _ = ev.evaluate_evidence("kafka partitions", items)
        self.assertEqual(len(scores), 1)
        self.assertGreater(scores[0], 0.0)

    def test_node_handles_empty_evidence(self) -> None:
        self.assertEqual(evidence_evaluation_node(AgentState(evidence=[]))["evidence"], [])


if __name__ == "__main__":
    unittest.main()
