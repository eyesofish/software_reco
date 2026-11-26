"""Actual usage accounting, stream completion and per-request isolation without providers."""

from unittest.mock import Mock

import pytest

from software_recommend_system import llm_governance as gov
from software_recommend_system.execution_control import run_controlled
from software_recommend_system.performance import TimingMiddleware


def response(prompt=1000, completion=500):
    return {"usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}}


@pytest.fixture(autouse=True)
def accounting(monkeypatch):
    gov.reset_usage()
    monkeypatch.setattr(gov.settings, "LLM_COST_PROMPT_PER_1K_USD", None)
    monkeypatch.setattr(gov.settings, "LLM_COST_COMPLETION_PER_1K_USD", None)
    monkeypatch.setattr(gov.settings, "LLM_COST_MODEL_RATES", {})
    yield
    gov.reset_usage()


def priced(monkeypatch):
    monkeypatch.setattr(gov.settings, "LLM_COST_PROMPT_PER_1K_USD", 0.01)
    monkeypatch.setattr(gov.settings, "LLM_COST_COMPLETION_PER_1K_USD", 0.02)


def test_unknown_is_distinct_from_explicit_zero_and_missing_usage(monkeypatch):
    assert gov.record_response_usage(scene="test", model="m", response=response())["cost_usd"] is None
    monkeypatch.setattr(gov.settings, "LLM_COST_PROMPT_PER_1K_USD", 0.0)
    monkeypatch.setattr(gov.settings, "LLM_COST_COMPLETION_PER_1K_USD", 0.0)
    assert gov.record_response_usage(scene="test", model="m", response=response())["cost_usd"] == 0.0
    missing = gov.record_response_usage(scene="test", model="m", response={})
    assert missing["cost_usd"] is None
    assert missing["prompt_tokens"] is None
    total = gov.usage_snapshot()["overall"]
    assert total["cost_usd"] is None
    assert total["unknown_cost_calls"] == 2
    assert total["unknown_usage_calls"] == 1
    assert not total["token_totals_complete"]


def test_model_specific_rates_require_both_prices(monkeypatch):
    priced(monkeypatch)
    monkeypatch.setattr(
        gov.settings,
        "LLM_COST_MODEL_RATES",
        {
            "m": {"prompt_per_1k_usd": 0.001, "completion_per_1k_usd": 0.002},
            "partial": {"prompt_per_1k_usd": 0},
        },
    )
    assert gov.record_response_usage(scene="test", model="m", response=response())["cost_usd"] == 0.002
    assert gov.record_response_usage(scene="test", model="partial", response=response())["cost_usd"] is None


def test_success_after_unobserved_retry_has_only_a_known_subtotal(monkeypatch):
    priced(monkeypatch)
    record = gov.record_response_usage(scene="test", model="m", response=response(), retries=1)
    assert record["cost_usd"] is None
    assert record["known_cost_usd"] == 0.02
    assert not record["usage_complete"]


@pytest.mark.asyncio
async def test_usage_correlation_survives_worker_without_trace_file(monkeypatch):
    monkeypatch.delenv("PERFORMANCE_TRACE_FILE", raising=False)

    async def app(scope, receive, send):
        await run_controlled(
            lambda _control: gov.record_response_usage(scene="answer", model="m", response=response()),
            session_id="cost-session",
            timeout_seconds=3,
        )
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(_message):
        pass

    await TimingMiddleware(app)(
        {"type": "http", "method": "POST", "path": "/recommend", "headers": [(b"x-request-id", b"request-cost-1")]},
        receive,
        send,
    )
    selected = gov.usage_snapshot(request_id="request-cost-1")
    record = selected["records"][0]
    assert record["run_id"]
    assert record["session_id"] == "cost-session"
    assert selected["overall"]["calls"] == 1
    assert gov.usage_snapshot(request_id="different")["overall"]["calls"] == 0


def test_single_call_wrapper_preserves_arguments_and_does_not_retry(monkeypatch):
    priced(monkeypatch)
    create = Mock(return_value=response())
    assert gov.accounted_completion(create=create, scene="router", model="m", timeout=2) == response()
    create.assert_called_once_with(model="m", timeout=2)
    create.side_effect = TimeoutError("timeout")
    with pytest.raises(TimeoutError):
        gov.accounted_completion(create=create, scene="router", model="m", timeout=2)
    assert create.call_count == 2
    assert gov.usage_snapshot()["overall"]["cost_usd"] is None
