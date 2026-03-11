import unittest
from unittest.mock import patch

from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.nodes import candidate_generation_node
from software_recommend_system.state import AgentState


class CandidateGenerationTests(unittest.TestCase):
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

