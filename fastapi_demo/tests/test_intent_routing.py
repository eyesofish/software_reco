"""Provider-free routing contract tests. No graph or paid model is imported/called."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from software_recommend_system import intent_routing as ir
from software_recommend_system import jev_provider as jp


def answer(question, choice, p=0.95):
    options = list(question["criteria"])
    probabilities = {option: (1 - p) / (len(options) - 1) for option in options}
    probabilities[choice] = p
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": (p - 1 / len(options)) / (1 - 1 / len(options)),
    }


class ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def decide(self, state, questions, **kwargs):
        self.calls.append(deepcopy({"state": state, "questions": questions, **kwargs}))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return {
            "model": "jev-1.14.0",
            "answers": {key: answer(question, *step[key]) for key, question in questions.items()},
        }


def semantics(p=0.95):
    return {"request_type_v1": ("query", p), "answer_mode_v1": ("rag", p)}


def test_explicit_session_skips_relation_and_no_review():
    client = ScriptedProvider([semantics()])
    reviewer = Mock(side_effect=AssertionError("must not review"))
    result = ir.resolve_routing(
        ir.RoutingSnapshot(
            query="Redis 现在有哪些部署方式",
            binding="existing",
            target_session_id="s1",
            current_conversation_id="other",
            context={"facts": {"topic": "Redis"}},
        ),
        client=client,
        reviewer=reviewer,
    )
    assert result.status == "ready"
    assert result.target_session_id == "s1"
    assert result.decisions["task_relation_v1"].source == "structured"
    assert list(client.calls[0]["questions"]) == ["request_type_v1", "answer_mode_v1"]
    assert reviewer.call_count == 0


def test_new_relation_clears_history_before_semantic_classification():
    client = ScriptedProvider([{"task_relation_v1": ("new", 0.95)}, semantics()])
    snapshot = ir.RoutingSnapshot(query="换个话题", current_conversation_id="s1", context={"facts": {"old": "secret"}})
    result = ir.resolve_routing(snapshot, client=client)
    assert result.target == "new"
    assert client.calls[1]["state"]["context"] == {}
    assert snapshot.context == {"facts": {"old": "secret"}}


def test_unavailable_candidate_cannot_be_invented():
    client = ScriptedProvider([{"task_relation_v1": ("continue", 0.99)}])
    result = ir.resolve_routing(ir.RoutingSnapshot(query="继续"), client=client)
    assert result.status == "routing_clarification"
    assert result.target_session_id is None
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "p,expected_source,review_count", [(0.59, "clarify", 0), (0.60, "llm", 1), (0.89, "llm", 1), (0.90, "jev", 0)]
)
def test_probability_boundaries(p, expected_source, review_count):
    provider = ScriptedProvider([{"request": ("query", p)}])
    reviewer = Mock(return_value={"choice": "query", "confidence": 0.9})
    result = ir.decide_choices({"request": ir.REQUEST_QUESTION}, {}, client=provider, reviewer=reviewer)
    assert result.decisions["request"].source == expected_source
    assert reviewer.call_count == review_count


def test_high_confidence_uncertain_is_abstention_not_execution():
    provider = ScriptedProvider([{"request": ("uncertain", 0.99)}])
    result = ir.decide_choices({"request": ir.REQUEST_QUESTION}, {}, client=provider)
    assert result.decisions["request"].source == "clarify"


def test_review_budget_is_shared_between_questions_and_existing_task():
    provider = ScriptedProvider([semantics(0.75)])
    reviewer = Mock(return_value={"choice": "query", "confidence": 0.9})
    result = ir.decide_choices(
        {"request_type_v1": ir.REQUEST_QUESTION, "answer_mode_v1": ir.MODE_QUESTION},
        {},
        reviews_used=2,
        client=provider,
        reviewer=reviewer,
    )
    assert reviewer.call_count == 1
    assert result.reviews_used == 3
    assert result.decisions["answer_mode_v1"].reason == "review_budget_exhausted"


def test_review_invalid_label_never_becomes_executable():
    provider = ScriptedProvider([{"request": ("query", 0.75)}])
    result = ir.decide_choices(
        {"request": ir.REQUEST_QUESTION},
        {},
        client=provider,
        reviewer=lambda *a, **kw: {"choice": "delete_everything", "confidence": 1},
    )
    assert result.decisions["request"].source == "clarify"


def test_review_transport_failure_is_distinct_from_abstention():
    provider = ScriptedProvider([{"request": ("query", 0.75)}])
    reviewer = Mock(side_effect=TimeoutError())
    result = ir.decide_choices({"request": ir.REQUEST_QUESTION}, {}, client=provider, reviewer=reviewer)
    assert result.decisions["request"].source == "fallback"
    assert result.decisions["request"].reason == "review_failed"
    assert result.usage[-1]["cost_usd"] is None


def test_malformed_dimension_does_not_discard_valid_dimension():
    provider = Mock()
    provider.decide.return_value = {
        "answers": {
            "request": answer(ir.REQUEST_QUESTION, "query"),
            "mode": {"type": "choice", "choice": "direct", "probabilities": {"direct": 1}},
        }
    }
    result = ir.decide_choices({"request": ir.REQUEST_QUESTION, "mode": ir.MODE_QUESTION}, {}, client=provider)
    assert result.decisions["request"].source == "jev"
    assert result.decisions["mode"].source == "fallback"


@pytest.mark.parametrize("binding,expected", [("existing", "ready"), ("unresolved", "routing_clarification")])
def test_provider_failure_is_stage_specific(binding, expected):
    provider = ScriptedProvider([TimeoutError()])
    result = ir.resolve_routing(
        ir.RoutingSnapshot(query="hello", binding=binding, target_session_id="s1" if binding == "existing" else None),
        client=provider,
    )
    assert result.status == expected
    assert result.source == "fallback"
    assert result.mode == "rag"


def test_low_confidence_with_known_session_still_clarifies():
    provider = ScriptedProvider([semantics(0.59)])
    result = ir.resolve_routing(
        ir.RoutingSnapshot(query="那个", binding="existing", target_session_id="s1"), client=provider
    )
    assert result.status == "routing_clarification"
    assert result.target_session_id == "s1"


def test_image_only_known_binding_preserves_vision_path_without_sending_base64():
    provider = Mock(side_effect=AssertionError("no model call"))
    result = ir.resolve_routing(
        ir.RoutingSnapshot(has_images=True, binding="existing", target_session_id="s1"), client=provider
    )
    assert result.status == "ready"
    assert result.mode == "rag"
    provider.decide.assert_not_called()
    result = ir.resolve_routing(ir.RoutingSnapshot(has_images=True), client=provider)
    assert result.status == "routing_clarification"


def test_cancellation_base_exception_is_not_swallowed():
    class Cancelled(BaseException):
        pass

    provider = Mock()
    provider.decide.side_effect = Cancelled()
    with pytest.raises(Cancelled):
        ir.decide_choices({"request": ir.REQUEST_QUESTION}, {}, client=provider)


def test_http_contract_circuit_is_per_provider_and_cost_unknown_without_prices():
    calls = []

    def transport(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(529, json={"message": "private upstream body"})
        return httpx.Response(
            200, json={"model": "jev-1.14.0", "answers": {}, "usage": {"input_tokens": 10, "output_tokens": 2}}
        )

    cfg = SimpleNamespace(JEV_API_KEY="test", JEV_MODEL="jev-1.14.0", JEV_BASE_URL="https://api.typesafe.ai")
    with patch.object(jp, "settings", cfg):
        live = jp.JevProvider(transport=httpx.MockTransport(transport))
        shadow = jp.JevProvider(transport=httpx.MockTransport(transport))
        with pytest.raises(jp.ProviderFailure, match="http_529"):
            live.decide({}, {}, timeout_seconds=1)
        with pytest.raises(jp.ProviderFailure, match="circuit_open"):
            live.decide({}, {}, timeout_seconds=1)
        result = shadow.decide({}, {}, timeout_seconds=1, request_id="r1")
    assert len(calls) == 2
    assert str(calls[0].url) == "https://api.typesafe.ai/v1/systemone"
    assert result["_routing_usage"]["cost_usd"] is None
    assert result["_routing_usage"]["request_id"] == "r1"
