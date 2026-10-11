import unittest
from types import SimpleNamespace
from unittest.mock import patch

from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.nodes import answer_generation_node, candidate_generation_node
from software_recommend_system.state import AgentState


class CandidateGenerationTests(unittest.TestCase):
    def test_generic_rag_skips_unused_llm_candidates(self) -> None:
        state = AgentState(
            selected_skill="generic_rag",
            evidence=[{
                "question": "Redis persistence?",
                "documents": [Document(content="Redis supports AOF.", metadata=Metadata(source="vector"), score=0.9)],
                "search_results": [],
                "quality_score": 0.9,
            }],
        )
        with patch("software_recommend_system.nodes.candidate_generation_with_llm") as model:
            result = candidate_generation_node(state)
        model.assert_not_called()
        self.assertTrue(result["candidates"])
        state.candidates = result["candidates"]
        with patch("software_recommend_system.nodes._stream_chat_completion_text", return_value="Use AOF.") as answer:
            output = answer_generation_node(state)
        self.assertEqual(output["final_answer"], "Use AOF.")
        self.assertIn("Redis supports AOF.", answer.call_args.kwargs["messages"][1]["content"])

    def test_recommendation_still_uses_model_candidates(self) -> None:
        state = AgentState(selected_skill="software_selection", evidence=[{
            "question": "Which database?", "documents": [], "search_results": [], "quality_score": 0.9,
        }])
        response = SimpleNamespace(choices=[{"message": {"content":
            '[{"solution":"PostgreSQL","rationale":"ACID","pros":[],"cons":[],"relevance_score":0.9}]'}}])
        with patch("software_recommend_system.nodes.candidate_generation_with_llm",
                   return_value=SimpleNamespace(result=lambda: response)) as model:
            result = candidate_generation_node(state)
        model.assert_called_once()
        self.assertEqual(result["candidates"][0].solution, "PostgreSQL")

    def test_quick_fact_qa_skips_llm_candidate_generation(self) -> None:
        evidence_doc = Document(
            content="Use streamtool setproperty --application-ev to set required environment variables.",
            metadata=Metadata(source="vector", doc_id="swg21996508.txt"),
            score=0.92,
        )
        state = AgentState(
            selected_skill="quick_fact_qa",
            evidence=[
                {
                    "question": "How to fix environment variables not picked up?",
                    "documents": [evidence_doc],
                    "search_results": [],
                    "quality_score": 0.9,
                }
            ],
        )

        with patch("software_recommend_system.nodes.candidate_generation_with_llm") as mocked_llm:
            result = candidate_generation_node(state)

        mocked_llm.assert_not_called()
        self.assertTrue(result["candidates"])


if __name__ == "__main__":
    unittest.main()

