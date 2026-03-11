import unittest

from software_recommend_system.config import settings
from software_recommend_system.planner import build_execution_plan


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_max_steps = settings.PLANNER_MAX_STEPS
        settings.PLANNER_MAX_STEPS = 4

    def tearDown(self) -> None:
        settings.PLANNER_MAX_STEPS = self._original_max_steps

    def test_compare_template_plan(self) -> None:
        plan = build_execution_plan(
            selected_skill="software_compare",
            user_query="Compare Redis and Memcached",
            normalized_query="Redis vs Memcached comparison",
            constraints={"language": "Python"},
        )
        self.assertEqual(plan.skill_id, "software_compare")
        self.assertGreaterEqual(len(plan.steps), 2)
        self.assertTrue(all(step.action == "retrieve" for step in plan.steps))

    def test_unknown_skill_falls_back_to_generic(self) -> None:
        plan = build_execution_plan(
            selected_skill="unknown_skill",
            user_query="Need recommendation",
            normalized_query="",
        )
        self.assertEqual(plan.skill_id, "generic_rag")
        self.assertGreaterEqual(len(plan.steps), 1)

    def test_respects_max_steps(self) -> None:
        settings.PLANNER_MAX_STEPS = 2
        plan = build_execution_plan(
            selected_skill="software_compare",
            user_query="Compare search engines",
            normalized_query="search engine comparison",
        )
        self.assertLessEqual(len(plan.steps), 2)

    def test_generic_question_uses_fact_style_template(self) -> None:
        plan = build_execution_plan(
            selected_skill="generic_rag",
            user_query="Why does transaction timeout happen when deleting a virtual portal?",
            normalized_query="",
        )
        self.assertEqual(plan.skill_id, "generic_rag")
        self.assertTrue(plan.planner_reason.startswith("template:qa_template"))
        self.assertGreaterEqual(len(plan.steps), 1)


if __name__ == "__main__":
    unittest.main()
