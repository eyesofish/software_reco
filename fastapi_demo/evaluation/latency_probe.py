"""Reproducible graph scheduling probe with controlled recall delays.

This measures graph mechanics, not remote model/service performance. Optional
cached reranker measurements use local files only and report missing models.
"""

import argparse
import json
import os
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from langgraph.graph import END, StateGraph

from software_recommend_system import nodes, retriever
from software_recommend_system.document_schema import Document, Metadata
from software_recommend_system.performance import span
from software_recommend_system.state import AgentState


def fixture_doc(identifier):
    return Document(content="Redis persistence supports AOF and RDB snapshots. " + identifier,
                    metadata=Metadata(source="vector", doc_id=identifier), score=0.9)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=float, default=150)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--cached-reranker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.delay_ms <= 0 or args.rounds < 3:
        parser.error("delay must be positive and rounds >= 3")
    output = args.output or Path("evaluation/reports/latency") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    graph = StateGraph(AgentState)
    for name, function in [("retrieve", nodes.retrieve_node), ("candidates", nodes.candidate_generation_node),
                           ("answer", nodes.answer_generation_node)]:
        graph.add_node(name, function)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "candidates")
    graph.add_edge("candidates", "answer")
    graph.add_edge("answer", END)
    agent = graph.compile()
    delay = args.delay_ms / 1000
    web_calls = 0

    def vector(query, **kwargs):
        time.sleep(delay)
        return [fixture_doc("common"), fixture_doc(query)]

    def web(**kwargs):
        nonlocal web_calls
        web_calls += 1
        time.sleep(delay * 2)
        return [fixture_doc("web")]

    measurements = {}
    configuration = {
        "AGENT_LOOP_ENABLE": False, "RECALL_ENABLE_VECTOR": True,
        "RECALL_ENABLE_KEYWORD": False, "RECALL_ENABLE_MEMORY": False,
        "RECALL_ENABLE_IMAGE_VECTOR": False, "RECALL_ENABLE_WEB": True,
        "RECALL_WEB_MIN_LOCAL_DOCS": 2, "RECALL_WEB_MIN_LOCAL_SCORE": 0.5,
        "RETRIEVAL_ENABLE_RERANK": False, "RERANK_MODEL_ENABLED": False,
    }
    with patch.multiple(nodes.settings, **configuration), \
            patch.object(retriever, "recall_vector", side_effect=vector), \
            patch.object(retriever, "recall_web", side_effect=web), \
            patch.object(nodes, "candidate_generation_with_llm") as candidate_model, \
            patch.object(nodes, "_stream_chat_completion_text", return_value="Redis supports AOF and RDB."):
        for name, concurrency, policy in [("serial_auto", 1, "auto"), ("parallel_auto", 3, "auto"),
                                          ("parallel_always", 3, "always")]:
            durations = []
            with patch.multiple(nodes.settings, SUBQUERY_MAX_CONCURRENCY=concurrency, RECALL_WEB_POLICY=policy):
                before_calls = web_calls
                for index in range(args.rounds + 1):
                    with span("probe.graph", profile=name, warmup=index == 0):
                        started = time.perf_counter()
                        result = agent.invoke(AgentState(user_query="Redis persistence", selected_skill="generic_rag",
                                                        sub_questions=["q1", "q2", "q3"]))
                        duration = (time.perf_counter() - started) * 1000
                    if result["final_answer"] != "Redis supports AOF and RDB.":
                        raise AssertionError("graph answer failed")
                    if index:
                        durations.append(duration)
                measurements[name] = {"samples_ms": durations, "p50_ms": statistics.median(durations),
                                      "web_calls_including_warmup": web_calls - before_calls}
        candidate_model.assert_not_called()

    rerank = {}
    for name, enabled in [("linear", False)] + ([("cached_cross_encoder", True)] if args.cached_reranker else []):
        durations = []
        with patch.multiple(nodes.settings, RERANK_MODEL_ENABLED=enabled, RERANK_MODEL_LOCAL_FILES_ONLY=True):
            for _ in range(args.rounds + 1):
                started = time.perf_counter()
                docs = retriever.rerank_documents("Redis persistence", [fixture_doc(str(i)) for i in range(30)],
                                                 5, use_model=enabled)
                durations.append((time.perf_counter() - started) * 1000)
            actual_mode = docs[0].metadata.rerank_features.get("mode", "unknown")
        rerank[name] = {"cold_ms": durations[0], "warm_p50_ms": statistics.median(durations[1:]),
                        "mode": actual_mode, "docs": 30,
                        "model": nodes.settings.RERANK_MODEL_NAME if enabled else None,
                        "device": nodes.settings.RERANK_MODEL_DEVICE if enabled else None,
                        "available": not enabled or actual_mode.startswith("cross_encoder")}
    report = {"measurement_type": "controlled_recall_graph_and_local_rerank", "rounds": args.rounds,
              "recall_delay_ms": args.delay_ms, "graph": measurements, "rerank": rerank,
              "generic_rag_candidate_model_calls": 0,
              "environment": {"python": platform.python_version(), "os": platform.platform(),
                              "cpu_count": os.cpu_count()},
              "limitations": "Controlled recalls and stubbed answer model; not real endpoint latency or quality."}
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), **report}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
