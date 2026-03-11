import unittest
from unittest.mock import Mock

from software_recommend_system.config import settings
from software_recommend_system import router as router_module
from software_recommend_system.router import route_query


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = {
            "ROUTER_MODEL": settings.ROUTER_MODEL,
            "ROUTER_FALLBACK_MODE": settings.ROUTER_FALLBACK_MODE,
            "ROUTER_CONFIDENCE_THRESHOLD": settings.ROUTER_CONFIDENCE_THRESHOLD,
            "ROUTER_CIRCUIT_BREAKER_SECONDS": settings.ROUTER_CIRCUIT_BREAKER_SECONDS,
        }
        settings.ROUTER_MODEL = "qwen3-0.6b-instruct-router"
        settings.ROUTER_FALLBACK_MODE = "rag"
        settings.ROUTER_CONFIDENCE_THRESHOLD = 0.65
        settings.ROUTER_CIRCUIT_BREAKER_SECONDS = 60.0
        router_module._ROUTER_CIRCUIT_OPEN_UNTIL = 0.0

    def tearDown(self) -> None:
        settings.ROUTER_MODEL = self._original["ROUTER_MODEL"]
        settings.ROUTER_FALLBACK_MODE = self._original["ROUTER_FALLBACK_MODE"]
        settings.ROUTER_CONFIDENCE_THRESHOLD = self._original["ROUTER_CONFIDENCE_THRESHOLD"]
        settings.ROUTER_CIRCUIT_BREAKER_SECONDS = self._original["ROUTER_CIRCUIT_BREAKER_SECONDS"]
        router_module._ROUTER_CIRCUIT_OPEN_UNTIL = 0.0

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

    def test_route_query_circuit_breaker_shortcuts_repeated_connection_failures(self) -> None:
        failing_client = Mock()
        failing_client.chat.completions.create.side_effect = TimeoutError("connection error")

        original_factory = router_module._get_router_client
        try:
            router_module._get_router_client = Mock(return_value=failing_client)

            first = route_query("Need architecture advice")
            second = route_query("Need architecture advice again")
        finally:
            router_module._get_router_client = original_factory

        self.assertEqual(first.mode, "rag")
        self.assertTrue(first.fallback_used)
        self.assertEqual(first.reason, "router_fallback:TimeoutError")
        self.assertEqual(second.mode, "rag")
        self.assertTrue(second.fallback_used)
        self.assertEqual(second.reason, "router_circuit_open")
        self.assertEqual(failing_client.chat.completions.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
