import contextvars
import json
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from software_recommend_system import execution_control, nodes, retrieval_channels, retriever
from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.performance import span
from software_recommend_system.state import AgentState


def retrieval_graph(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("retrieve", nodes.retrieve_node)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", END)
    return graph.compile(checkpointer=checkpointer)


def doc(identifier, score=0.9):
    return Document(content="Redis persistence AOF " + identifier,
                    metadata=Metadata(source="vector", doc_id=identifier), score=score)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for key, value in {
        "RECALL_ENABLE_VECTOR": True, "RECALL_ENABLE_WEB": True,
        "RECALL_ENABLE_KEYWORD": False, "RECALL_ENABLE_MEMORY": False,
        "RECALL_ENABLE_IMAGE_VECTOR": False, "RETRIEVAL_ENABLE_RERANK": False,
        "RERANK_MODEL_ENABLED": False, "RECALL_WEB_POLICY": "auto",
        "RECALL_WEB_MIN_LOCAL_DOCS": 2, "RECALL_WEB_MIN_LOCAL_SCORE": 0.5,
        "SUBQUERY_MAX_CONCURRENCY": 2, "AGENT_LOOP_ENABLE": False,
    }.items():
        monkeypatch.setattr(nodes.settings, key, value)


def test_local_evidence_skips_web():
    with patch.object(retriever, "recall_vector", return_value=[doc("a"), doc("b")]), \
            patch.object(retriever, "recall_web") as web:
        result = retriever.retrieve("Redis persistence")
    web.assert_not_called()
    assert {d.metadata.doc_id for d in result} == {"a", "b"}


@pytest.mark.parametrize("local", [[], [doc("a", 0.1), doc("b", 0.1)], [doc("same"), doc("same")]])
def test_insufficient_or_duplicate_local_evidence_uses_web(local):
    with patch.object(retriever, "recall_vector", return_value=local), \
            patch.object(retriever, "recall_web", return_value=[doc("web")]) as web:
        result = retriever.retrieve("Redis persistence")
    web.assert_called_once()
    assert "web" in {d.metadata.doc_id for d in result}


@pytest.mark.parametrize("query", ["Redis 最新版本", "latest Redis release", "搜索网页 Redis"])
def test_explicit_external_query_uses_web_even_with_local_docs(query):
    with patch.object(retriever, "recall_vector", return_value=[doc("a"), doc("b")]), \
            patch.object(retriever, "recall_web", return_value=[]) as web:
        retriever.retrieve(query)
    web.assert_called_once()


@pytest.mark.parametrize("policy,query", [("never", "latest Redis"), ("auto", "不要联网 Redis"),
                                         ("always", "offline Redis")])
def test_policy_or_user_opt_out_disables_web(monkeypatch, policy, query):
    monkeypatch.setattr(nodes.settings, "RECALL_WEB_POLICY", policy)
    with patch.object(retriever, "recall_vector", return_value=[]), patch.object(retriever, "recall_web") as web:
        retriever.retrieve(query)
    web.assert_not_called()


def test_always_policy_keeps_previous_fanout(monkeypatch):
    monkeypatch.setattr(nodes.settings, "RECALL_WEB_POLICY", "always")
    with patch.object(retriever, "recall_vector", return_value=[doc("a"), doc("b")]), \
            patch.object(retriever, "recall_web", return_value=[]) as web:
        retriever.retrieve("Redis persistence")
    web.assert_called_once()


def test_memory_does_not_replace_factual_evidence(monkeypatch):
    monkeypatch.setattr(nodes.settings, "RECALL_ENABLE_MEMORY", True)
    with patch.object(retriever, "recall_vector", return_value=[]), \
            patch.object(retriever, "recall_memory", return_value=[doc("a"), doc("b")]), \
            patch.object(retriever, "recall_web", return_value=[]) as web:
        retriever.retrieve("Redis persistence")
    web.assert_called_once()


def test_fallback_web_shares_local_timeout_budget():
    release = threading.Event()

    def blocked(*args, **kwargs):
        release.wait(2)
        return []

    try:
        with patch.object(retriever, "recall_vector", side_effect=blocked), \
                patch.object(retriever, "recall_web") as web:
            retriever.retrieve("Redis persistence", channel_timeout_seconds=0.05)
        web.assert_not_called()
    finally:
        release.set()


def test_real_graph_tasks_overlap_with_bound_order_and_context():
    active = peak = 0
    lock = threading.Lock()
    marker = contextvars.ContextVar("test_marker", default="missing")
    marker.set("request")
    observed = []

    def retrieve(query, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            observed.append(marker.get())
        try:
            time.sleep(0.04 if query != "q1" else 0.09)
            return [doc(query)]
        finally:
            with lock:
                active -= 1

    with patch.object(nodes, "retrieve", side_effect=retrieve):
        result = retrieval_graph().invoke(AgentState(user_query="Redis", sub_questions=["q1", "q2", "q3", "q4"]))
    assert peak == 2
    assert observed == ["request"] * 4
    assert [r["subquery"] for r in result["retrieval_records"]] == ["q1", "q2", "q3", "q4"]


def test_subquery_wait_observes_cancellation():
    release, started = threading.Event(), threading.Event()
    control = execution_control.RunControl("cancel-test", 5)
    token = execution_control._CURRENT_CONTROL.set(control)

    def retrieve(*args, **kwargs):
        started.set()
        while not release.wait(0.01):
            execution_control.check_current_run()
        return []

    def cancel():
        started.wait(2)
        control.cancel("cancelled")

    canceller = threading.Thread(target=cancel)
    canceller.start()
    try:
        with patch.object(nodes, "retrieve", side_effect=retrieve), \
                pytest.raises(execution_control.ExecutionStopped):
            retrieval_graph().invoke(AgentState(sub_questions=["q1", "q2", "q3"]))
    finally:
        release.set()
        canceller.join()
        execution_control._CURRENT_CONTROL.reset(token)


def test_worker_failure_propagates():
    with patch.object(nodes, "retrieve", side_effect=ValueError("failed")), \
            pytest.raises(ValueError, match="failed"):
        retrieval_graph().invoke(AgentState(sub_questions=["q1"]))


def test_checkpoint_replay_keeps_subquery_identity():
    graph = retrieval_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "subquery-replay"}}
    calls = []

    def retrieve(query, **kwargs):
        calls.append(query)
        if query == "q2" and calls.count("q2") == 1:
            time.sleep(0.05)
            raise ValueError("transient")
        return [doc(query)]

    with patch.object(nodes, "retrieve", side_effect=retrieve):
        with pytest.raises(ValueError):
            graph.invoke(AgentState(sub_questions=["q1", "q2"]), config)
        result = graph.invoke(None, config)
    assert calls.count("q1") == 1
    assert calls.count("q2") == 2
    assert [r["subquery"] for r in result["retrieval_records"]] == ["q1", "q2"]
    assert [r["retrieved_doc_ids"][0] for r in result["retrieval_records"]] == ["q1", "q2"]


def test_original_request_preserves_web_opt_out(monkeypatch):
    monkeypatch.setattr(nodes.settings, "AGENT_LOOP_ENABLE", True)
    with patch.object(nodes, "run_agent_loop") as loop, \
            patch.object(retriever, "recall_vector", return_value=[]), \
            patch.object(retriever, "recall_web") as web:
        retrieval_graph().invoke(AgentState(user_query="不要联网，Redis persistence", sub_questions=["Redis AOF"]))
    loop.assert_not_called()
    web.assert_not_called()


def test_original_request_preserves_freshness_intent():
    with patch.object(retriever, "recall_vector", return_value=[doc("a"), doc("b")]), \
            patch.object(retriever, "recall_web", return_value=[]) as web:
        retriever.retrieve("Redis release", web_query_hint="latest Redis release")
    web.assert_called_once()


@pytest.mark.parametrize("key,value", [("RECALL_ENABLE_WEB", False), ("RECALL_WEB_POLICY", "never")])
def test_agent_web_tool_obeys_disable_flags(monkeypatch, key, value):
    monkeypatch.setattr(nodes.settings, key, value)
    with patch.object(retrieval_channels, "_tavily_search") as search:
        assert retrieval_channels.recall_web("latest Redis", 3) == []
    search.assert_not_called()


def test_static_subqueries_rerank_once_after_global_dedup(monkeypatch):
    monkeypatch.setattr(nodes.settings, "RETRIEVAL_ENABLE_RERANK", True)
    monkeypatch.setattr(nodes.settings, "RERANK_MODEL_ENABLED", True)
    monkeypatch.setattr(nodes.settings, "RECALL_ENABLE_WEB", False)
    with patch.object(retriever, "recall_vector", side_effect=lambda **kw: [doc("shared"), doc(kw["query"])]), \
            patch.object(retriever, "_score_with_cross_encoder", side_effect=lambda q, ds: [0.9] * len(ds)) as predict:
        result = retrieval_graph().invoke(AgentState(user_query="Redis", sub_questions=["q1", "q2"]))
    predict.assert_called_once()
    assert len(predict.call_args.args[1]) == 3
    assert len(result["retrieved_doc_ids_full"]) == 3


def test_disabled_rerank_prevents_global_model_inference():
    with patch.object(nodes, "retrieve", return_value=[doc("a")]), \
            patch.object(retriever, "_score_with_cross_encoder") as predict:
        retrieval_graph().invoke(AgentState(sub_questions=["q1"]))
    predict.assert_not_called()


def test_rerank_cold_load_and_predict_have_separate_spans(monkeypatch, tmp_path):
    trace = tmp_path / "spans.jsonl"
    monkeypatch.setenv("PERFORMANCE_TRACE_FILE", str(trace))
    monkeypatch.setattr(nodes.settings, "RERANK_MODEL_ENABLED", True)
    monkeypatch.setattr(retriever, "_CROSS_ENCODER_MODEL", None)
    monkeypatch.setattr(retriever, "_CROSS_ENCODER_INIT_FAILED", False)
    model = SimpleNamespace(predict=lambda pairs, **kwargs: [0.7] * len(pairs))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=lambda **kwargs: model))
    with span("request"):
        for _ in range(2):
            retriever.rerank_documents("Redis", [doc("a"), doc("b")], 2, use_model=True)
    spans = [json.loads(line) for line in trace.read_text().splitlines()]
    stages = [s["stage"] for s in spans]
    assert stages.count("retrieval.rerank.model_load") == 1
    assert stages.count("retrieval.rerank.predict") == 2
    assert stages.count("retrieval.rerank") == 2
    assert all(s["docs"] == 2 for s in spans if s["stage"] == "retrieval.rerank.predict")
