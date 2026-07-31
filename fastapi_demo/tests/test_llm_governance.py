import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai

from software_recommend_system import llm_governance as gov


def _usage(prompt: int, completion: int, total: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total if total is not None else prompt + completion,
    )


def _response(prompt: int = 10, completion: int = 5) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                           usage=_usage(prompt, completion))


class _FakeCompletions:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes):
        self.completions = _FakeCompletions(outcomes)
        self.chat = SimpleNamespace(completions=self.completions)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _status_error(code: int) -> openai.APIStatusError:
    response = httpx.Response(status_code=code, request=_request())
    return openai.APIStatusError("boom", response=response, body=None)


class RetryClassificationTests(unittest.TestCase):
    def test_timeout_is_retryable(self) -> None:
        self.assertTrue(gov.is_retryable_error(openai.APITimeoutError(request=_request())))

    def test_connection_error_is_retryable(self) -> None:
        self.assertTrue(
            gov.is_retryable_error(openai.APIConnectionError(request=_request()))
        )

    def test_server_and_rate_limit_codes_are_retryable(self) -> None:
        for code in (429, 500, 502, 503, 504):
            self.assertTrue(gov.is_retryable_error(_status_error(code)), code)

    def test_client_errors_are_not_retryable(self) -> None:
        """Replaying a malformed/unauthorized request cannot succeed."""
        for code in (400, 401, 403, 404, 422):
            self.assertFalse(gov.is_retryable_error(_status_error(code)), code)

    def test_arbitrary_exception_is_not_retryable(self) -> None:
        self.assertFalse(gov.is_retryable_error(ValueError("nope")))


class GovernedChatCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        gov.reset_usage()
        self.addCleanup(gov.reset_usage)
        self.slept: list[float] = []

    def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def test_success_records_usage_and_cost(self) -> None:
        client = _FakeClient([_response(prompt=1000, completion=2000)])
        with (
            patch.object(gov.settings, "LLM_COST_PROMPT_PER_1K_USD", 0.001),
            patch.object(gov.settings, "LLM_COST_COMPLETION_PER_1K_USD", 0.002),
        ):
            gov.governed_chat_completion(
                client=client, scene="unit", model="m", messages=[], sleep=self._sleep
            )

        snap = gov.usage_snapshot()
        self.assertEqual(snap["overall"]["calls"], 1)
        self.assertEqual(snap["overall"]["prompt_tokens"], 1000)
        self.assertEqual(snap["overall"]["completion_tokens"], 2000)
        self.assertEqual(snap["overall"]["total_tokens"], 3000)
        # 1000/1000*0.001 + 2000/1000*0.002 = 0.005
        self.assertAlmostEqual(snap["overall"]["cost_usd"], 0.005, places=6)
        self.assertEqual(snap["by_scene"]["unit"]["calls"], 1)

    def test_cost_is_zero_when_pricing_unset(self) -> None:
        """No configured pricing must report 0, not a made-up number."""
        client = _FakeClient([_response(prompt=1000, completion=1000)])
        gov.governed_chat_completion(
            client=client, scene="unit", model="m", messages=[], sleep=self._sleep
        )
        self.assertEqual(gov.usage_snapshot()["overall"]["cost_usd"], 0.0)

    def test_retries_transient_then_succeeds(self) -> None:
        client = _FakeClient([_status_error(503), _status_error(429), _response()])
        with patch.object(gov.settings, "LLM_MAX_RETRIES", 2):
            gov.governed_chat_completion(
                client=client, scene="unit", model="m", messages=[], sleep=self._sleep
            )

        self.assertEqual(client.completions.calls, 3)
        self.assertEqual(len(self.slept), 2)
        self.assertEqual(gov.usage_snapshot()["overall"]["retries"], 2)

    def test_does_not_retry_client_error(self) -> None:
        client = _FakeClient([_status_error(400), _response()])
        with patch.object(gov.settings, "LLM_MAX_RETRIES", 3), self.assertRaises(openai.APIStatusError):
            gov.governed_chat_completion(
                client=client, scene="unit", model="m", messages=[], sleep=self._sleep
            )

        self.assertEqual(client.completions.calls, 1)
        self.assertEqual(self.slept, [])
        self.assertEqual(gov.usage_snapshot()["overall"]["failed_calls"], 1)

    def test_exhausts_attempts_then_raises(self) -> None:
        client = _FakeClient([_status_error(500), _status_error(500), _status_error(500)])
        with patch.object(gov.settings, "LLM_MAX_RETRIES", 2), self.assertRaises(openai.APIStatusError):
            gov.governed_chat_completion(
                client=client, scene="unit", model="m", messages=[], sleep=self._sleep
            )

        self.assertEqual(client.completions.calls, 3)
        self.assertEqual(gov.usage_snapshot()["overall"]["failed_calls"], 1)

    def test_zero_retries_setting_means_single_attempt(self) -> None:
        client = _FakeClient([_status_error(503), _response()])
        with patch.object(gov.settings, "LLM_MAX_RETRIES", 0), self.assertRaises(openai.APIStatusError):
            gov.governed_chat_completion(
                client=client, scene="unit", model="m", messages=[], sleep=self._sleep
            )
        self.assertEqual(client.completions.calls, 1)

    def test_backoff_is_bounded_and_grows(self) -> None:
        with (
            patch.object(gov.settings, "LLM_RETRY_BACKOFF_SECONDS", 1.0),
            patch.object(gov.settings, "LLM_RETRY_BACKOFF_MAX_SECONDS", 4.0),
        ):
            for attempt in range(1, 8):
                self.assertLessEqual(gov._backoff_delay(attempt), 4.0)

    def test_missing_usage_does_not_crash(self) -> None:
        client = _FakeClient([SimpleNamespace(choices=[])])
        gov.governed_chat_completion(
            client=client, scene="unit", model="m", messages=[], sleep=self._sleep
        )
        self.assertEqual(gov.usage_snapshot()["overall"]["total_tokens"], 0)

    def test_scene_totals_are_separated(self) -> None:
        for scene in ("alpha", "beta", "alpha"):
            gov.governed_chat_completion(
                client=_FakeClient([_response(prompt=10, completion=1)]),
                scene=scene,
                model="m",
                messages=[],
                sleep=self._sleep,
            )
        snap = gov.usage_snapshot()
        self.assertEqual(snap["by_scene"]["alpha"]["calls"], 2)
        self.assertEqual(snap["by_scene"]["beta"]["calls"], 1)
        self.assertEqual(snap["overall"]["calls"], 3)


class ClientFactoryTests(unittest.TestCase):
    def test_client_disables_sdk_retries_to_avoid_compounding(self) -> None:
        """The SDK defaults to max_retries=2; combined with our own retry loop
        that would fan out to 9 requests worst case."""
        from software_recommend_system.llm_utils import _get_openai_client

        client = _get_openai_client()
        self.assertEqual(client.max_retries, 0)
        self.assertEqual(client.timeout, gov.request_timeout_seconds())


if __name__ == "__main__":
    unittest.main()
