import unittest
from unittest.mock import Mock

from software_recommend_system.config import settings
from software_recommend_system.router import route_query


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = {
            "ROUTER_MODEL": settings.ROUTER_MODEL,
            "ROUTER_FALLBACK_MODE": settings.ROUTER_FALLBACK_MODE,
            "ROUTER_CONFIDENCE_THRESHOLD": settings.ROUTER_CONFIDENCE_THRESHOLD,
        }
        settings.ROUTER_MODEL = "qwen3-0.6b-instruct-router"
        settings.ROUTER_FALLBACK_MODE = "rag"
        settings.ROUTER_CONFIDENCE_THRESHOLD = 0.65

    def tearDown(self) -> None:
        settings.ROUTER_MODEL = self._original["ROUTER_MODEL"]
        settings.ROUTER_FALLBACK_MODE = self._original["ROUTER_FALLBACK_MODE"]
        settings.ROUTER_CONFIDENCE_THRESHOLD = self._original["ROUTER_CONFIDENCE_THRESHOLD"]

    @staticmethod
    def _mock_client_with_content(content: str) -> Mock:
        client = Mock()
        client.chat.completions.create.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return client

    def test_route_query_parses_valid_json(self) -> None:
        client = self._mock_client_with_content(
            '{"mode":"direct","confidence":0.91,"reason":"small talk query"}'
        )

        decision = route_query("What is Python?", client=client)

        self.assertEqual(decision.mode, "direct")
        self.assertAlmostEqual(decision.confidence, 0.91)
        self.assertEqual(decision.reason, "small talk query")
        self.assertFalse(decision.fallback_used)

    def test_route_query_non_json_falls_back(self) -> None:
        client = self._mock_client_with_content("I think this should be rag")

        decision = route_query("Need architecture advice", client=client)

        self.assertEqual(decision.mode, "rag")
        self.assertEqual(decision.confidence, 0.0)
        self.assertTrue(decision.fallback_used)
        self.assertIn("router_fallback", decision.reason)

    def test_route_query_timeout_falls_back(self) -> None:
        client = Mock()
        client.chat.completions.create.side_effect = TimeoutError("router timeout")

        decision = route_query("Compare Redis and Memcached", client=client)

        self.assertEqual(decision.mode, "rag")
        self.assertEqual(decision.confidence, 0.0)
        self.assertTrue(decision.fallback_used)
        self.assertEqual(decision.reason, "router_fallback:TimeoutError")


if __name__ == "__main__":
    unittest.main()

