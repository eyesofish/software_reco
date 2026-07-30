import atexit
import logging
import os
import sqlite3
import time
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph

from .nodes import (
    answer_generation_node,
    candidate_generation_node,
    chat_answer_generation_node,
    coverage_check_node,
    draw_image_node,
    entry_node,
    evidence_collection_node,
    evidence_evaluation_node,
    human_confirmation_node,
    planning_node,
    pre_drawing_node,
    query_normalization_node,
    retrieve_node,
    routing_node,
    skill_routing_node,
    sub_question_generation_node,
)
from .state import AgentState

logger = logging.getLogger(__name__)
_SQLITE_CHECKPOINTER_RESOURCE = None

try:
    from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore

    _SQLITE_CHECKPOINTER_IMPORT_ERROR = None
    _SQLITE_CHECKPOINTER_IMPORT_SOURCE = "langgraph.checkpoint.sqlite"
except Exception as primary_exc:  # pragma: no cover - optional dependency
    try:
        from langgraph_checkpoint.sqlite import SqliteSaver  # type: ignore

        _SQLITE_CHECKPOINTER_IMPORT_ERROR = None
        _SQLITE_CHECKPOINTER_IMPORT_SOURCE = "langgraph_checkpoint.sqlite"
    except Exception as secondary_exc:  # pragma: no cover - optional dependency
        _SQLITE_CHECKPOINTER_IMPORT_SOURCE = None
        _SQLITE_CHECKPOINTER_IMPORT_ERROR = (
            f"primary={primary_exc}; fallback={secondary_exc}"
        )
        SqliteSaver = None  # type: ignore


def _build_sqlite_checkpointer(checkpoint_path: Path):
    global _SQLITE_CHECKPOINTER_RESOURCE
    if SqliteSaver is None:
        raise RuntimeError("SqliteSaver is unavailable")

    maybe_checkpointer = SqliteSaver.from_conn_string(str(checkpoint_path))
    if hasattr(maybe_checkpointer, "put") and hasattr(maybe_checkpointer, "get_tuple"):
        logger.info("Using SqliteSaver checkpointer at %s", checkpoint_path)
        return maybe_checkpointer

    # Some versions return a context manager that yields a saver.
    enter = getattr(maybe_checkpointer, "__enter__", None)
    exit_ = getattr(maybe_checkpointer, "__exit__", None)
    if callable(enter) and callable(exit_):
        saver = enter()
        if not (hasattr(saver, "put") and hasattr(saver, "get_tuple")):
            raise TypeError(
                "SqliteSaver.from_conn_string() context manager returned invalid saver type: "
                f"{type(saver).__name__}"
            )
        _SQLITE_CHECKPOINTER_RESOURCE = maybe_checkpointer
        atexit.register(exit_, None, None, None)
        logger.info(
            "Using SqliteSaver checkpointer via context manager at %s", checkpoint_path
        )
        return saver

    # Final fallback for compatibility with older/newer APIs.
    logger.warning(
        "SqliteSaver.from_conn_string returned %s; fallback to direct sqlite3 connection.",
        type(maybe_checkpointer).__name__,
    )
    conn = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
    except Exception:
        conn.close()
        raise
    _SQLITE_CHECKPOINTER_RESOURCE = conn
    atexit.register(conn.close)
    logger.info(
        "Using SqliteSaver checkpointer via direct sqlite3 connection at %s", checkpoint_path
    )
    return saver


def _build_checkpointer():
    checkpoint_path = Path(
        os.getenv("LANGGRAPH_CHECKPOINT_PATH", ".runtime/langgraph_checkpoints.sqlite")
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Initializing LangGraph checkpointer: path=%s sqlite_imported=%s import_source=%s",
        checkpoint_path,
        SqliteSaver is not None,
        _SQLITE_CHECKPOINTER_IMPORT_SOURCE,
    )

    if SqliteSaver is None and _SQLITE_CHECKPOINTER_IMPORT_ERROR is not None:
        logger.warning(
            "SqliteSaver import unavailable, fallback to MemorySaver. "
            "Install dependency with: pip install -U langgraph-checkpoint-sqlite",
        )

    if SqliteSaver is not None:
        try:
            return _build_sqlite_checkpointer(checkpoint_path)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning(
                "Failed to initialize SqliteSaver at %s, fallback to MemorySaver (error_type=%s)",
                checkpoint_path,
                type(exc).__name__,
            )

    logger.warning(
        "Using in-memory LangGraph checkpointer. Install sqlite checkpointer for durable resume."
    )
    return MemorySaver()


_CHECKPOINTER = _build_checkpointer()
logger.info("Active LangGraph checkpointer type: %s", type(_CHECKPOINTER).__name__)


def create_rag_with_routing_agent():
    """Create RAG agent graph with routing, skill routing, and planning."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("entry", entry_node)
    workflow.add_node("routing", routing_node)
    workflow.add_node("skill_routing", skill_routing_node)
    workflow.add_node("planning", planning_node)
    workflow.add_node("query_normalization", query_normalization_node)
    workflow.add_node("sub_question_generation", sub_question_generation_node)
    workflow.add_node("human_confirmation", human_confirmation_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("evidence_collection", evidence_collection_node)
    workflow.add_node("evidence_evaluation", evidence_evaluation_node)
    workflow.add_node("candidate_generation", candidate_generation_node)
    workflow.add_node("coverage_check", coverage_check_node)
    workflow.add_node("rag_answer_generation", answer_generation_node)
    workflow.add_node("chat_answer_generation", chat_answer_generation_node)
    workflow.add_node("pre_drawing", pre_drawing_node)
    workflow.add_node("draw_image", draw_image_node)

    # Entry
    workflow.set_entry_point("entry")
    workflow.add_edge("entry", "query_normalization")
    workflow.add_edge("query_normalization", "routing")

    # Branching by mode
    def route_based_on_mode(state: AgentState) -> str:
        if state.mode in {"rag", "hitl"}:
            return "rag"
        if state.mode == "draw":
            return "draw"
        return "direct"

    workflow.add_conditional_edges(
        "routing",
        route_based_on_mode,
        {
            "rag": "skill_routing",
            "direct": "chat_answer_generation",
            "draw": "pre_drawing",
        },
    )

    # RAG branch
    workflow.add_edge("skill_routing", "planning")
    workflow.add_edge("planning", "sub_question_generation")
    workflow.add_edge("sub_question_generation", "human_confirmation")
    workflow.add_edge("human_confirmation", "retrieve")
    workflow.add_edge("retrieve", "evidence_collection")
    workflow.add_edge("evidence_collection", "evidence_evaluation")
    # candidate_generation sits *after* the loop: it issues an LLM call and only
    # its final output is consumed by answer generation, so running it on every
    # refinement iteration would waste tokens and latency.
    workflow.add_edge("evidence_evaluation", "coverage_check")

    # Loop control
    def should_continue(state: AgentState) -> str:
        logger.info(
            "Coverage check: coverage=%.2f threshold=%.2f iteration=%d/%d",
            state.coverage,
            state.coverage_threshold,
            state.iteration_count,
            state.max_iterations,
        )
        coverage_met = state.coverage >= state.coverage_threshold
        max_iterations_reached = state.iteration_count >= state.max_iterations
        needs_refinement = state.needs_refinement
        elapsed_time = (time.time() - state.start_time) if state.start_time else 0
        is_timeout = elapsed_time > state.timeout_budget

        if coverage_met or max_iterations_reached or is_timeout or not needs_refinement:
            logger.info("Termination conditions met; entering answer generation.")
            return "terminate"
        logger.info("Continue retrieval iteration.")
        return "continue"

    workflow.add_conditional_edges(
        "coverage_check",
        should_continue,
        {
            "continue": "retrieve",
            "terminate": "candidate_generation",
        },
    )

    # Exit edges
    workflow.add_edge("candidate_generation", "rag_answer_generation")
    workflow.add_edge("rag_answer_generation", "__end__")
    workflow.add_edge("chat_answer_generation", "__end__")
    workflow.add_edge("pre_drawing", "draw_image")
    workflow.add_edge("draw_image", "__end__")

    return workflow.compile(checkpointer=_CHECKPOINTER)


def create_rag_agent():
    """Create baseline RAG graph (without mode routing)."""
    workflow = StateGraph(AgentState)

    workflow.add_node("entry", entry_node)
    workflow.add_node("query_normalization", query_normalization_node)
    workflow.add_node("sub_question_generation", sub_question_generation_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("evidence_collection", evidence_collection_node)
    workflow.add_node("evidence_evaluation", evidence_evaluation_node)
    workflow.add_node("candidate_generation", candidate_generation_node)
    workflow.add_node("coverage_check", coverage_check_node)
    workflow.add_node("answer_generation", answer_generation_node)

    workflow.set_entry_point("entry")
    workflow.add_edge("entry", "query_normalization")
    workflow.add_edge("query_normalization", "sub_question_generation")
    workflow.add_edge("sub_question_generation", "retrieve")
    workflow.add_edge("retrieve", "evidence_collection")
    workflow.add_edge("evidence_collection", "evidence_evaluation")
    workflow.add_edge("evidence_evaluation", "coverage_check")

    def should_continue(state: AgentState) -> str:
        logger.info(
            "Coverage check: coverage=%.2f threshold=%.2f iteration=%d/%d",
            state.coverage,
            state.coverage_threshold,
            state.iteration_count,
            state.max_iterations,
        )
        coverage_met = state.coverage >= state.coverage_threshold
        max_iterations_reached = state.iteration_count >= state.max_iterations
        needs_refinement = state.needs_refinement
        elapsed_time = (time.time() - state.start_time) if state.start_time else 0
        is_timeout = elapsed_time > state.timeout_budget

        if coverage_met or max_iterations_reached or is_timeout or not needs_refinement:
            logger.info("Termination conditions met; entering answer generation.")
            return "terminate"
        logger.info("Continue retrieval iteration.")
        return "continue"

    workflow.add_conditional_edges(
        "coverage_check",
        should_continue,
        {
            "continue": "retrieve",
            "terminate": "candidate_generation",
        },
    )
    workflow.add_edge("candidate_generation", "answer_generation")
    workflow.add_edge("answer_generation", "__end__")
    return workflow.compile()
