import time
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from software_recommend_system import dynamic_routing as dynamic
from software_recommend_system import intent_routing, nodes, rag_agent
from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.intent_routing import ChoiceBatch, ChoiceDecision
from software_recommend_system.state import AgentState


def batch(choice, source="jev"):
    return ChoiceBatch(decisions={"next_step_v1": ChoiceDecision(
        choice=choice, source=source, probability=0.95, reason="fixture",
    )})


def evidence(question, doc_id="doc", score=0.9):
    return {"question": question, "documents": [Document(
        content="Redis persistence durability documentation", metadata=Metadata(source="vector", doc_id=doc_id),
        score=score,
    )], "search_results": [], "quality_score": score}


def test_coverage_counts_objectives_not_document_count():
    state = AgentState(sub_questions=["q1", "q2", "q3"],
                       evidence=[evidence("q1", "a"), evidence("q1", "b"), evidence("q1", "c")])
    result = nodes.coverage_check_node(state)
    assert result["coverage"] == pytest.approx(1 / 3)
    assert result["uncovered_subqueries"] == ["q2", "q3"]


def test_resolved_routing_does_not_call_legacy_model(monkeypatch):
    legacy = Mock(side_effect=AssertionError("duplicate classification"))
    monkeypatch.setattr(nodes, "route_query", legacy)
    state = AgentState(routing_decision={"snapshot_id": "s", "status": "ready", "mode": "direct"})
    assert nodes.routing_node(state) == {"mode": "direct"}
    legacy.assert_not_called()


def test_runtime_cannot_answer_below_evidence_threshold(monkeypatch):
    monkeypatch.setattr(intent_routing, "decide_choices", lambda *a, **kw: batch("answer"))
    state = AgentState(dynamic_routing_enabled=True, coverage=0.1, sub_questions=["q"],
                       uncovered_subqueries=["q"], iteration_count=1)
    result = dynamic.evidence_decision_node(state)
    assert result["next_step"] == "clarify"
    assert result["pending_input"]["clarification_id"]


def test_budget_exhaustion_does_not_call_model(monkeypatch):
    model = Mock(side_effect=AssertionError("budget exhausted"))
    monkeypatch.setattr(intent_routing, "decide_choices", model)
    state = AgentState(dynamic_routing_enabled=True, iteration_count=3, max_iterations=3)
    result = dynamic.evidence_decision_node(state)
    assert result["next_step"] == "answer"
    assert result["evidence_incomplete"]
    model.assert_not_called()


def test_technical_failure_uses_deterministic_remaining_action(monkeypatch):
    monkeypatch.setattr(intent_routing, "decide_choices", lambda *a, **kw: batch(None, "fallback"))
    state = AgentState(dynamic_routing_enabled=True, iteration_count=1, coverage=0.2)
    assert dynamic.evidence_decision_node(state)["next_step"] == "refine"
    state.refinement_count = 1
    assert dynamic.evidence_decision_node(state)["evidence_incomplete"]


def test_full_graph_clarification_does_not_repeat_decision(monkeypatch):
    monkeypatch.setattr(rag_agent, "_CHECKPOINTER", InMemorySaver())
    monkeypatch.setattr(rag_agent, "query_normalization_node", lambda state: {})
    monkeypatch.setattr(rag_agent, "skill_routing_node", lambda state: {"selected_skill": "quick_fact_qa"})
    monkeypatch.setattr(rag_agent, "planning_node", lambda state: {})
    monkeypatch.setattr(rag_agent, "sub_question_generation_node", lambda state: {"sub_questions": ["q1", "q2"]})
    monkeypatch.setattr(rag_agent, "retrieve_node", lambda state: {
        "evidence": [evidence("q1")], "iteration_count": state.iteration_count,
    })
    monkeypatch.setattr(rag_agent, "evidence_evaluation_node", lambda state: {})
    monkeypatch.setattr(rag_agent, "refinement_node", lambda state: {
        "refinement_count": state.refinement_count + 1, "next_step": "retrieve",
    })
    answer = Mock(side_effect=lambda state: {"final_answer": "Answer based on available evidence"})
    answer.__name__ = "answer_generation_node"
    candidates = Mock(side_effect=AssertionError("fact QA must skip candidates"))
    candidates.__name__ = "candidate_generation_node"
    monkeypatch.setattr(rag_agent, "answer_generation_node", answer)
    monkeypatch.setattr(rag_agent, "candidate_generation_node", candidates)
    model = Mock(side_effect=[batch("retrieve"), batch("refine"), batch("clarify")])
    monkeypatch.setattr(intent_routing, "decide_choices", model)
    graph = rag_agent.create_rag_with_routing_agent()
    config = {"configurable": {"thread_id": "runtime-clarify"}}
    state = AgentState(user_query="Compare persistence", session_id="runtime-clarify",
                       dynamic_routing_enabled=True, hitl_policy="auto_confirm",
                       routing_decision={"snapshot_id": "s", "mode": "rag"})
    interrupted = graph.invoke(state, config)
    pending = interrupted["__interrupt__"][0].value["pending_input"]
    assert model.call_count == 3
    assert interrupted["refinement_count"] == 1
    resumed = graph.invoke(Command(resume={"clarification_id": pending["clarification_id"],
                                         "answer": "Only durability matters"}), config)
    assert resumed["final_answer"]
    assert resumed["pending_input"] is None
    assert len(resumed["evidence"]) == 1
    assert model.call_count == 3
    assert resumed["clarification_count"] == 1
    candidates.assert_not_called()


def test_next_normal_turn_has_no_previous_preflight():
    assert AgentState().routing_decision is None
    assert AgentState().routing_reviews_used == 0


def test_refinement_retains_supported_objective_without_rejudging(monkeypatch):
    judge = Mock(return_value=([0.7], "fixture"))
    monkeypatch.setattr(nodes, "evaluate_evidence", judge)
    state = AgentState(dynamic_routing_enabled=True, iteration_count=2,
                       evidence=[evidence("covered", "a", 0.9), evidence("uncovered", "b", 0)])
    result = nodes.evidence_evaluation_node(state)
    assert result["evidence"][0].quality_score == 0.9
    assert result["evidence"][1].quality_score == 0.7
    assert judge.call_count == 1
    assert judge.call_args.args[0] == "uncovered"


def test_multiple_clarifications_preserve_earlier_constraints(monkeypatch):
    state = AgentState(normalized_query="Redis 7 持久化", sub_questions=["部署方案"],
                       pending_input={"clarification_id": "c1"}, route_stage="planning")
    monkeypatch.setattr(dynamic, "interrupt", lambda _: {
        "clarification_id": "c1", "answer": "必须本地部署，不要云服务",
    })
    first = dynamic.clarification_node(state)
    state = state.model_copy(update={**first, "pending_input": {"clarification_id": "c2"}})
    monkeypatch.setattr(dynamic, "interrupt", lambda _: {"clarification_id": "c2", "answer": "预算100"})
    second = dynamic.clarification_node(state)
    state = state.model_copy(update=second)
    query = dynamic.search_query_for_objective(state, "部署方案")
    assert all(constraint in query for constraint in ("Redis 7", "必须本地部署", "不要云服务", "预算100"))
    assert second["retrieval_queries"]["部署方案"] == query


@pytest.mark.parametrize("factory", ["create_rag_with_routing_agent", "create_rag_agent"])
def test_legacy_refinement_uses_active_time_not_waiting_time(monkeypatch, factory):
    monkeypatch.setattr(rag_agent, "_CHECKPOINTER", InMemorySaver())
    for name in ("entry_node", "query_normalization_node", "planning_node", "evidence_evaluation_node",
                 "candidate_generation_node"):
        monkeypatch.setattr(rag_agent, name, lambda state: {})
    monkeypatch.setattr(rag_agent, "skill_routing_node", lambda state: {"selected_skill": "quick_fact_qa"})
    monkeypatch.setattr(rag_agent, "sub_question_generation_node", lambda state: {"sub_questions": ["q"]})
    retrieved = []

    def recall(state):
        retrieved.append(state.iteration_count)
        return {"evidence": [evidence("q")]}

    monkeypatch.setattr(rag_agent, "retrieve_node", recall)
    monkeypatch.setattr(rag_agent, "coverage_check_node", lambda state: {
        "coverage": 1.0 if state.iteration_count >= 2 else 0.0, "needs_refinement": state.iteration_count < 2,
    })
    monkeypatch.setattr(rag_agent, "answer_generation_node", lambda state: {"final_answer": "done"})
    state = AgentState(user_query="q", start_time=time.time() - 3600, active_elapsed_ms=1000,
                       timeout_budget=60, hitl_policy="auto_confirm",
                       routing_decision={"snapshot_id": "old-task", "mode": "rag"})
    graph = getattr(rag_agent, factory)()
    result = graph.invoke(state, {"configurable": {"thread_id": factory}})
    assert result["final_answer"] == "done"
    assert retrieved == [0, 1]


def test_low_confidence_does_not_act_like_transport_failure(monkeypatch):
    monkeypatch.setattr(intent_routing, "decide_choices", lambda *a, **kw: batch(None, "clarify"))
    result = dynamic.planning_decision_node(AgentState(dynamic_routing_enabled=True))
    assert result["next_step"] == "clarify"
