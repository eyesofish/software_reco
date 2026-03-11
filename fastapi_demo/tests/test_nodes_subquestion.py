import unittest
from unittest.mock import patch

from software_recommend_system.nodes import sub_question_generation_node
from software_recommend_system.state import AgentState


class SubQuestionGenerationTests(unittest.TestCase):
    def test_quick_fact_qa_uses_planner_seeds_without_llm(self) -> None:
        state = AgentState(
            normalized_query="How do I transfer SPSS license to another computer?",
            selected_skill="quick_fact_qa",
            planner_reason="template:qa_template",
            plan_steps=[
                {
                    "step_id": "s1_direct_answer",
                    "objective": "Retrieve direct answer span from authoritative docs",
                    "action": "retrieve",
                    "query": "SPSS license transfer steps",
                    "retrieval_profile": "fast",
                    "required": True,
                }
            ],
        )

        with patch("software_recommend_system.nodes.sub_question_generation_with_llm") as mocked_llm:
            result = sub_question_generation_node(state)

        mocked_llm.assert_not_called()
        self.assertTrue(result["sub_questions"])
        self.assertEqual(result["sub_questions"][0], "SPSS license transfer steps")


if __name__ == "__main__":
    unittest.main()

