"""Real MemorySaver pause/resume, with model and persistence boundaries stubbed."""

import asyncio
import time
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.api.v1 import routing_api as api
from app.api.v1.handlers import recommend_orchestrator as orchestration
from app.api.v1.models import (
    CancelWaitingRequest,
    ClarificationRequest,
    RecommendationConfirmRequest,
    RecommendationRequest,
)
from app.api.v1.stream_utils import _execution_budget, run_agent_async
from software_recommend_system.intent_routing import RoutingResolution, RoutingSnapshot


class Flow(TypedDict, total=False):
    session_id: str
    user_query: str
    timeout_budget: float
    active_elapsed_ms: int
    pending_input: dict | None
    awaiting_human_confirmation: bool
    human_confirmation_done: bool
    pending_sub_questions: list[str]
    execution_cancelled: bool
    final_answer: str
    evidence_incomplete: bool
    mode: str
    coverage: float
    route_decisions: list
    dynamic_stop_reason: str


def graph_with_pause(calls, *, first="input", second=None, incomplete=False):
    graph = StateGraph(Flow)

    def decide(state):
        calls["decide"] += 1
        time.sleep(0.01)
        return {
            "pending_input": None
            if first == "confirmation"
            else {"clarification_id": "clarify-1", "prompt": "Which constraint?"}
        }

    def pause(state):
        calls["pause"] += 1
        if first == "confirmation":
            interrupt({"type": "human_confirmation", "sub_questions": ["q1"]})
        else:
            reply = interrupt({"type": "awaiting_input", "pending_input": state["pending_input"]})
            assert reply["clarification_id"] == "clarify-1"
        return {"pending_input": None}

    def next_pause(state):
        if second == "input":
            interrupt({"type": "awaiting_input", "pending_input": {"clarification_id": "clarify-2", "prompt": "More?"}})
        elif second == "confirmation":
            interrupt({"type": "human_confirmation", "sub_questions": ["q2"]})
        return {}

    def finish(state):
        calls["finish"] += 1
        get_stream_writer()({"type": "token", "delta": "An answer"})
        return {
            "final_answer": "An answer",
            "mode": "rag",
            "coverage": 0.5,
            "evidence_incomplete": incomplete,
            "dynamic_stop_reason": "test_gap" if incomplete else "",
        }

    graph.add_node("decide", decide)
    graph.add_node("pause", pause)
    graph.add_node("next_pause", next_pause)
    graph.add_node("finish", finish)
    graph.add_edge(START, "decide")
    graph.add_edge("decide", "pause")
    graph.add_edge("pause", "next_pause")
    graph.add_edge("next_pause", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=MemorySaver())


@pytest.fixture
def no_writes(monkeypatch):
    append = Mock()
    memory = Mock()
    monkeypatch.setattr(api, "_append_session_message_once", append)
    monkeypatch.setattr(orchestration, "_append_session_message_once", append)
    monkeypatch.setattr(orchestration, "_safe_write_turn_memories", memory)
    return append, memory


def settings(**changes):
    values = {
        "JEV_TIMEOUT_SECONDS": 8,
        "ROUTING_DEPLOYMENT_MODE": "live",
        "ROUTING_TRAFFIC_PERCENT": 100,
        "ROUTING_AUTO_SESSION_APPROVED": True,
        "ROUTING_GATE_REPORT": "missing-report.json",
        "DYNAMIC_ROUTING_ENABLE": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_unresolved_route_needs_evidence_not_only_operator_latch(monkeypatch):
    monkeypatch.setattr(api, "settings", settings())
    resolver = Mock(side_effect=AssertionError("not approved"))
    monkeypatch.setattr(api, "resolve_routing", resolver)
    response = await api.routing_resolve(RoutingSnapshot(query="continue", current_conversation_id="s1"))
    assert response.status == "routing_clarification"
    resolver.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_timeout_is_bounded_and_retains_structured_target(monkeypatch):
    monkeypatch.setattr(api, "settings", settings(JEV_TIMEOUT_SECONDS=0.02))

    def slow(snapshot):
        time.sleep(0.15)
        return RoutingResolution(snapshot_id=snapshot.snapshot_id, target="existing", target_session_id="wrong")

    monkeypatch.setattr(api, "resolve_routing", slow)
    started = time.monotonic()
    response = await api.bounded_resolve(RoutingSnapshot(query="q", binding="existing", target_session_id="fixed"))
    assert time.monotonic() - started < 0.12
    assert response.target_session_id == "fixed"
    assert response.mode == "rag"
    assert response.source == "fallback"


@pytest.mark.asyncio
async def test_preflight_cancellation_does_not_turn_into_rag(monkeypatch):
    monkeypatch.setattr(api, "settings", settings())
    monkeypatch.setattr(api, "resolve_routing", lambda snapshot: time.sleep(0.1))
    task = asyncio.create_task(api.bounded_resolve(RoutingSnapshot(binding="new", query="q")))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_pause_budget_excludes_user_wait_and_decision_is_not_replayed(monkeypatch, no_writes):
    calls = {"decide": 0, "pause": 0, "finish": 0}
    graph = graph_with_pause(calls)
    config = {"configurable": {"thread_id": "budget-pause"}}
    result = await run_agent_async(
        graph,
        {"session_id": "budget-pause", "timeout_budget": 1.0, "active_elapsed_ms": 200, "user_query": "q"},
        config=config,
    )
    assert result["__interrupt__"]
    values = graph.get_state(config).values
    assert values["active_elapsed_ms"] >= 200
    remaining_before, spent_before = _execution_budget(graph, Command(resume={}), config)
    await asyncio.sleep(0.12)
    remaining_after, spent_after = _execution_budget(graph, Command(resume={}), config)
    assert remaining_before == remaining_after
    assert spent_before == spent_after
    monkeypatch.setattr(api, "_graph", lambda: graph)
    response = await api.clarify_stream(
        ClarificationRequest(
            session_id="budget-pause",
            clarification_id="clarify-1",
            answer="cost limit 10",
        )
    )
    frames = [frame async for frame in response.body_iterator]
    assert "event: resume_accepted" in frames[0]
    assert any("event: token" in frame for frame in frames)
    assert "event: final" in frames[-1]
    assert calls == {"decide": 1, "pause": 2, "finish": 1}
    assert graph.get_state(config).values["active_elapsed_ms"] < 500
    with pytest.raises(HTTPException) as stale:
        await api.clarify(ClarificationRequest(session_id="budget-pause", clarification_id="clarify-1", answer="again"))
    assert stale.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("second,terminal", [("confirmation", "awaiting_confirmation"), ("input", "awaiting_input")])
async def test_resume_stream_stops_at_each_kind_of_nested_interrupt(monkeypatch, no_writes, second, terminal):
    calls = {"decide": 0, "pause": 0, "finish": 0}
    graph = graph_with_pause(calls, second=second)
    session = "nested-" + second
    await run_agent_async(graph, {"timeout_budget": 3}, config={"configurable": {"thread_id": session}})
    monkeypatch.setattr(api, "_graph", lambda: graph)
    response = await api.clarify_stream(
        ClarificationRequest(session_id=session, clarification_id="clarify-1", answer="x")
    )
    frames = [frame async for frame in response.body_iterator]
    assert f"event: {terminal}" in frames[-1]
    assert not any("event: final" in frame for frame in frames)
    assert calls["finish"] == 0
    no_writes[1].assert_not_called()


@pytest.mark.asyncio
async def test_pending_input_guard_and_cancelled_stale_requests(monkeypatch, no_writes):
    calls = {"decide": 0, "pause": 0, "finish": 0}
    graph = graph_with_pause(calls)
    session = "guard-pause"
    await run_agent_async(graph, {"timeout_budget": 3}, config={"configurable": {"thread_id": session}})
    monkeypatch.setattr(api, "_graph", lambda: graph)
    with pytest.raises(HTTPException) as blocked:
        await api.preflight(RecommendationRequest(session_id=session, query="new question"))
    assert blocked.value.status_code == 409
    with pytest.raises(HTTPException) as confirmation:
        await api.reserve_resume(session, kind="confirmation")
    assert confirmation.value.status_code == 409
    await api.cancel_waiting(CancelWaitingRequest(session_id=session, clarification_id="clarify-1"))
    with pytest.raises(HTTPException) as cancelled:
        await api.clarify(ClarificationRequest(session_id=session, clarification_id="clarify-1", answer="late"))
    assert cancelled.value.status_code == 409
    no_writes[0].assert_not_called()


@pytest.mark.asyncio
async def test_incomplete_final_is_not_written_to_long_term_memory(monkeypatch, no_writes):
    calls = {"decide": 0, "pause": 0, "finish": 0}
    graph = graph_with_pause(calls, incomplete=True)
    session = "incomplete-pause"
    await run_agent_async(graph, {"timeout_budget": 3}, config={"configurable": {"thread_id": session}})
    monkeypatch.setattr(api, "_graph", lambda: graph)
    response = await api.clarify(ClarificationRequest(session_id=session, clarification_id="clarify-1", answer="x"))
    assert response.status == "incomplete"
    assert response.stop_reason == "test_gap"
    no_writes[1].assert_not_called()


@pytest.mark.asyncio
async def test_double_stream_release_cannot_unlock_another_resume():
    raw = asyncio.Lock()
    await raw.acquire()
    lease = api.ResumeLease(raw)
    api.release_resume(lease)
    await raw.acquire()
    api.release_resume(lease)
    assert raw.locked()
    raw.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("shortcut", [False, True])
async def test_old_confirmation_preserves_nested_task_input(monkeypatch, no_writes, stream, shortcut):
    from app.api.v1 import routes

    calls = {"decide": 0, "pause": 0, "finish": 0}
    graph = graph_with_pause(calls, first="confirmation", second="input")
    session = f"confirm-nested-{stream}-{shortcut}"
    await run_agent_async(graph, {"timeout_budget": 3}, config={"configurable": {"thread_id": session}})
    monkeypatch.setattr(api, "_graph", lambda: graph)
    monkeypatch.setattr(routes, "AGENT", graph)
    monkeypatch.setattr(api, "resolve_routing", Mock(side_effect=AssertionError("resume must skip classification")))
    if shortcut:
        endpoint = routes.stream_software_recommendation if stream else routes.get_software_recommendation
        request = RecommendationRequest(session_id=session, query="确认")
    else:
        endpoint = routes.confirm_software_recommendation_stream if stream else routes.confirm_software_recommendation
        request = RecommendationConfirmRequest(session_id=session)
    response = await endpoint(request)
    if stream:
        frames = [frame async for frame in response.body_iterator]
        assert "event: resume_accepted" in frames[0]
        assert "event: awaiting_input" in frames[-1]
        assert not any("event: final" in frame for frame in frames)
    else:
        assert response.status == "awaiting_input"
        assert response.pending_input["clarification_id"] == "clarify-2"
    assert calls["decide"] == 1
    assert calls["finish"] == 0
    no_writes[1].assert_not_called()


@pytest.mark.asyncio
async def test_no_pending_short_confirmation_does_not_create_session_or_classify(monkeypatch):
    from app.api.v1 import routes

    monkeypatch.setattr(api, "resolve_routing", Mock(side_effect=AssertionError("no classification")))
    monkeypatch.setattr(routes, "_upsert_name_fact_from_query", Mock(side_effect=AssertionError("no session write")))
    response = await routes.get_software_recommendation(RecommendationRequest(query="确认"))
    assert response.status == "success"
    assert response.session_id is None
    assert "没有待确认任务" in response.final_answer


def test_cohort_uses_same_unsigned_sha256_bucket_as_java(monkeypatch):
    monkeypatch.setattr(api, "settings", settings(ROUTING_TRAFFIC_PERCENT=3))
    assert not api._in_cohort("owner-1:session-1")
    monkeypatch.setattr(api, "settings", settings(ROUTING_TRAFFIC_PERCENT=4))
    assert api._in_cohort("owner-1:session-1")
    assert not api._in_cohort("owner-1:new")


@pytest.mark.asyncio
async def test_multimodal_keeps_captured_kb_when_publication_changes(monkeypatch, tmp_path):
    from app.api.v1 import routes
    from software_recommend_system import kb_versions as kb

    monkeypatch.setattr(kb, "manifest_path", lambda: tmp_path / "manifest.sqlite3")
    store = kb.get_store()
    store.adopt_legacy(lambda: {})
    published = store.stage({})
    seen = []

    async def prepare(_request):
        seen.append(kb.current_snapshot_id())
        store.publish(published)
        seen.append(await asyncio.to_thread(kb.current_snapshot_id))
        await asyncio.sleep(0.01)
        return "q", "q", [], []

    monkeypatch.setattr(routes, "_prepare_multimodal_request", prepare)
    request = RecommendationRequest(query="q", execution_budget_seconds=1)
    pinned, *prepared = await routes._prepare_pinned_request(request)
    assert pinned == "legacy"
    assert seen == ["legacy", "legacy"]
    assert kb.capture_snapshot() == published
    assert kb.current_snapshot_id() is None
    assert prepared == ["q", "q", [], []]
    assert 0 < request.execution_budget_seconds < 1


@pytest.mark.asyncio
async def test_multimodal_preparation_obeys_remaining_deadline(monkeypatch, tmp_path):
    from app.api.v1 import routes
    from software_recommend_system import kb_versions as kb

    monkeypatch.setattr(kb, "manifest_path", lambda: tmp_path / "manifest.sqlite3")

    async def prepare(_request):
        await asyncio.sleep(0.2)

    monkeypatch.setattr(routes, "_prepare_multimodal_request", prepare)
    with pytest.raises(HTTPException) as stopped:
        await routes._prepare_pinned_request(RecommendationRequest(query="q", execution_budget_seconds=0.01))
    assert stopped.value.status_code == 408
    assert kb.current_snapshot_id() is None
