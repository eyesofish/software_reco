from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver
from .state import AgentState
from .nodes import (
    entry_node, 
    routing_node, 
    query_normalization_node, 
    sub_question_generation_node,
    human_confirmation_node,
    retrieve_node,
    evidence_collection_node,
    evidence_evaluation_node,
    candidate_generation_node,
    coverage_check_node,
    answer_generation_node,
    chat_answer_generation_node,
    pre_drawing_node,
    draw_image_node
)
import logging
import atexit
import sqlite3
import time
import os
from pathlib import Path

logger = logging.getLogger(__name__)
_SQLITE_CHECKPOINTER_RESOURCE = None

try:
    from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
    _SQLITE_CHECKPOINTER_IMPORT_ERROR = None
    _SQLITE_CHECKPOINTER_IMPORT_SOURCE = "langgraph.checkpoint.sqlite"
except Exception as primary_exc:  # pragma: no cover - optional dependency
    try:
        # Compatibility fallback for environments exposing sqlite saver via top-level package.
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
    logger.info("Using SqliteSaver checkpointer via direct sqlite3 connection at %s", checkpoint_path)
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
            checkpointer = _build_sqlite_checkpointer(checkpoint_path)
            return checkpointer
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
    """创建带有路由功能的 RAG Agent 图"""
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("entry", entry_node)
    workflow.add_node("routing", routing_node)  # 添加路由节点
    workflow.add_node("query_normalization", query_normalization_node)
    workflow.add_node("sub_question_generation", sub_question_generation_node)
    workflow.add_node("human_confirmation", human_confirmation_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("evidence_collection", evidence_collection_node)
    workflow.add_node("evidence_evaluation", evidence_evaluation_node)
    workflow.add_node("candidate_generation", candidate_generation_node)
    workflow.add_node("coverage_check", coverage_check_node)
    workflow.add_node("rag_answer_generation", answer_generation_node)  # RAG 答案生成
    workflow.add_node("chat_answer_generation", chat_answer_generation_node)  # Chat 答案生成
    workflow.add_node("pre_drawing", pre_drawing_node)
    workflow.add_node("draw_image", draw_image_node)
    
    # 设置入口
    workflow.set_entry_point("entry")
    
    # 添加边 - 先规范化，然后路由
    workflow.add_edge("entry", "query_normalization")
    workflow.add_edge("query_normalization", "routing")
    
    # 根据路由结果决定流程走向
    def route_based_on_mode(state: AgentState) -> str:
        """根据模式决定下一步"""
        if state.mode in {"rag", "hitl"}:
            return "rag"
        if state.mode == "draw":
            return "draw"
        else:
            return "direct"
    
    # 添加条件边处理路由
    workflow.add_conditional_edges(
        "routing",
        route_based_on_mode,
        {
            "rag": "sub_question_generation",  # RAG 分支
            "direct": "chat_answer_generation",  # Direct 分支
            "draw": "pre_drawing"              # 画图分支
        }
    )
    
    # RAG 分支流程
    workflow.add_edge("sub_question_generation", "human_confirmation")
    workflow.add_edge("human_confirmation", "retrieve")
    workflow.add_edge("retrieve", "evidence_collection")
    workflow.add_edge("evidence_collection", "evidence_evaluation")
    workflow.add_edge("evidence_evaluation", "candidate_generation")
    workflow.add_edge("candidate_generation", "coverage_check")
    
    # 添加条件边：根据覆盖率和迭代次数决定是否继续
    def should_continue(state: AgentState) -> str:
        """决定下一步操作的条件函数"""
        logger.info(f"检查继续条件: 覆盖率 {state.coverage:.2f}, 阈值 {state.coverage_threshold}, "
                   f"迭代次数 {state.iteration_count}, 最大 {state.max_iterations}")
        
        # 检查是否满足覆盖率要求
        coverage_met = state.coverage >= state.coverage_threshold
        # 检查是否达到最大迭代次数
        max_iterations_reached = state.iteration_count >= state.max_iterations
        # 检查是否需要细化
        needs_refinement = state.needs_refinement
        # 检查是否超时
        elapsed_time = (time.time() - state.start_time) if state.start_time else 0
        is_timeout = elapsed_time > state.timeout_budget
        
        # 如果覆盖率达标或达到最大迭代次数或超时或不需要细化，则终止
        if coverage_met or max_iterations_reached or is_timeout or not needs_refinement:
            logger.info("满足终止条件，进入答案生成")
            return "terminate"
        else:
            # 否则继续收集证据
            logger.info("继续收集证据")
            return "continue"
    
    import time  # 导入time模块
    
    # 添加条件边
    workflow.add_conditional_edges(
        "coverage_check",
        should_continue,
        {
            "continue": "retrieve",
            "terminate": "rag_answer_generation"
        }
    )
    
    # 添加最终边
    workflow.add_edge("rag_answer_generation", "__end__")
    workflow.add_edge("chat_answer_generation", "__end__")
    workflow.add_edge("pre_drawing", "draw_image")
    workflow.add_edge("draw_image", "__end__")
    
    return workflow.compile(checkpointer=_CHECKPOINTER)


def create_rag_agent():
    """创建 RAG Agent 图 - 默认走 RAG 流程"""
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("entry", entry_node)
    workflow.add_node("query_normalization", query_normalization_node)
    workflow.add_node("sub_question_generation", sub_question_generation_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("evidence_collection", evidence_collection_node)
    workflow.add_node("evidence_evaluation", evidence_evaluation_node)
    workflow.add_node("candidate_generation", candidate_generation_node)
    workflow.add_node("coverage_check", coverage_check_node)
    workflow.add_node("answer_generation", answer_generation_node)
    
    # 设置入口
    workflow.set_entry_point("entry")
    
    # 添加边
    workflow.add_edge("entry", "query_normalization")
    workflow.add_edge("sub_question_generation", "retrieve")
    workflow.add_edge("retrieve", "evidence_collection")
    workflow.add_edge("evidence_collection", "evidence_evaluation")
    workflow.add_edge("evidence_evaluation", "candidate_generation")
    workflow.add_edge("candidate_generation", "coverage_check")
    
    # 添加条件边：根据覆盖率、迭代次数、细化需求和超时情况决定是否继续
    def should_continue(state: AgentState) -> str:
        """决定下一步操作的条件函数"""
        logger.info(f"检查继续条件: 覆盖率 {state.coverage:.2f}, 阈值 {state.coverage_threshold}, "
                   f"迭代次数 {state.iteration_count}, 最大 {state.max_iterations}")
        
        # 检查是否满足覆盖率要求
        coverage_met = state.coverage >= state.coverage_threshold
        # 检查是否达到最大迭代次数
        max_iterations_reached = state.iteration_count >= state.max_iterations
        # 检查是否需要细化
        needs_refinement = state.needs_refinement
        # 检查是否超时
        elapsed_time = (time.time() - state.start_time) if state.start_time else 0
        is_timeout = elapsed_time > state.timeout_budget
        
        # 如果覆盖率达标或达到最大迭代次数或超时或不需要细化，则终止
        if coverage_met or max_iterations_reached or is_timeout or not needs_refinement:
            logger.info("满足终止条件，进入答案生成")
            return "terminate"
        else:
            # 否则继续收集证据
            logger.info("继续收集证据")
            return "continue"
    
    # 添加条件边
    workflow.add_conditional_edges(
        "coverage_check",
        should_continue,
        {
            "continue": "retrieve",
            "terminate": "answer_generation"
        }
    )
    
    # 添加最终边
    workflow.add_edge("answer_generation", "__end__")
    
    return workflow.compile()
