import unittest

from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.nodes import coverage_check_node
from software_recommend_system.state import AgentState, CandidateSolution


def _evidence(question: str, quality_score: float, *, with_docs: bool = True) -> dict:
    doc = Document(
        content=f"evidence for {question}",
        metadata=Metadata(source="vector", doc_id=f"doc::{question}"),
        score=quality_score,
    )
    return {
        "question": question,
        "documents": [doc] if with_docs else [],
        "search_results": [],
        "quality_score": quality_score,
    }


def _candidate() -> CandidateSolution:
    return CandidateSolution(
        solution="some solution",
        rationale="because",
        pros=["p"],
        cons=[],
        relevance_score=0.9,
    )


class CoverageCheckTests(unittest.TestCase):
    def test_full_high_quality_coverage_terminates(self) -> None:
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.85)],
            iteration_count=1,
        )

        result = coverage_check_node(state)

        self.assertEqual(result["coverage"], 1.0)
        self.assertFalse(result["needs_refinement"])

    def test_low_quality_evidence_does_not_count_as_covered(self) -> None:
        """Documents alone must not satisfy the gate; quality_score must clear
        QUALITY_THRESHOLD (0.6). Previously any non-empty recall counted."""
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.1)],
            iteration_count=1,
        )

        result = coverage_check_node(state)

        self.assertEqual(result["coverage"], 0.5)
        self.assertTrue(result["needs_refinement"])

    def test_empty_documents_are_not_covered(self) -> None:
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.9, with_docs=False)],
            iteration_count=1,
        )

        result = coverage_check_node(state)

        self.assertEqual(result["coverage"], 0.5)
        self.assertTrue(result["needs_refinement"])

    def test_existing_candidates_do_not_suppress_refinement(self) -> None:
        """Regression: needs_refinement used to require len(candidates) == 0.

        Because candidate_generation ran before coverage_check and always
        synthesized a fallback candidate from evidence, the refinement loop
        could never fire on a real under-covered turn.
        """
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.1)],
            candidates=[_candidate()],
            iteration_count=1,
        )

        result = coverage_check_node(state)

        self.assertTrue(result["needs_refinement"])

    def test_stops_when_iteration_makes_no_progress(self) -> None:
        """Retrieval re-runs the same sub-questions, so a non-improving pass
        cannot become productive later."""
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.1)],
            iteration_count=2,
            previous_coverage=0.5,
        )

        result = coverage_check_node(state)

        self.assertEqual(result["coverage"], 0.5)
        self.assertFalse(result["needs_refinement"])

    def test_continues_when_iteration_improves_coverage(self) -> None:
        state = AgentState(
            sub_questions=["q1", "q2", "q3", "q4"],
            evidence=[
                _evidence("q1", 0.9),
                _evidence("q2", 0.9),
                _evidence("q3", 0.1),
                _evidence("q4", 0.1),
            ],
            iteration_count=2,
            previous_coverage=0.25,
            max_iterations=3,
        )

        result = coverage_check_node(state)

        self.assertEqual(result["coverage"], 0.5)
        self.assertTrue(result["needs_refinement"])

    def test_stops_at_max_iterations(self) -> None:
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.1)],
            iteration_count=3,
            max_iterations=3,
            previous_coverage=0.0,
        )

        result = coverage_check_node(state)

        self.assertFalse(result["needs_refinement"])

    def test_previous_coverage_is_propagated(self) -> None:
        state = AgentState(
            sub_questions=["q1", "q2"],
            evidence=[_evidence("q1", 0.9), _evidence("q2", 0.1)],
            iteration_count=1,
        )

        result = coverage_check_node(state)

        self.assertEqual(result["previous_coverage"], result["coverage"])

    def test_no_sub_questions_is_safe(self) -> None:
        state = AgentState(sub_questions=[], evidence=[], iteration_count=1)

        result = coverage_check_node(state)

        self.assertEqual(result["coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
