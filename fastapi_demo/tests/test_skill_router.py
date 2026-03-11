import unittest

from software_recommend_system.config import settings
from software_recommend_system.skill_router import route_skill


class SkillRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = {
            "SKILL_ROUTER_ENABLE": settings.SKILL_ROUTER_ENABLE,
            "SKILL_ROUTER_USE_LLM": settings.SKILL_ROUTER_USE_LLM,
            "SKILL_ROUTER_RULE_THRESHOLD": settings.SKILL_ROUTER_RULE_THRESHOLD,
            "SKILL_ROUTER_FALLBACK_SKILL": settings.SKILL_ROUTER_FALLBACK_SKILL,
        }
        settings.SKILL_ROUTER_ENABLE = True
        settings.SKILL_ROUTER_USE_LLM = False
        settings.SKILL_ROUTER_RULE_THRESHOLD = 0.2
        settings.SKILL_ROUTER_FALLBACK_SKILL = "generic_rag"

    def tearDown(self) -> None:
        settings.SKILL_ROUTER_ENABLE = self._original["SKILL_ROUTER_ENABLE"]
        settings.SKILL_ROUTER_USE_LLM = self._original["SKILL_ROUTER_USE_LLM"]
        settings.SKILL_ROUTER_RULE_THRESHOLD = self._original["SKILL_ROUTER_RULE_THRESHOLD"]
        settings.SKILL_ROUTER_FALLBACK_SKILL = self._original["SKILL_ROUTER_FALLBACK_SKILL"]

    def test_route_skill_compare(self) -> None:
        result = route_skill("Compare Redis vs Memcached for cache layer")
        self.assertEqual(result.selected_skill, "software_compare")
        self.assertGreater(result.confidence, 0.0)

    def test_route_skill_architecture(self) -> None:
        result = route_skill("Need architecture for high availability microservice system")
        self.assertEqual(result.selected_skill, "architecture_design")

    def test_route_skill_fallback(self) -> None:
        settings.SKILL_ROUTER_RULE_THRESHOLD = 0.7
        result = route_skill("hello")
        self.assertEqual(result.selected_skill, "generic_rag")
        self.assertTrue(result.fallback_used)

    def test_route_skill_fact_question_prefers_quick_fact(self) -> None:
        result = route_skill("How can I transfer an SPSS 25 license to a new computer?")
        self.assertEqual(result.selected_skill, "quick_fact_qa")
        self.assertGreaterEqual(result.confidence, 0.7)


if __name__ == "__main__":
    unittest.main()
