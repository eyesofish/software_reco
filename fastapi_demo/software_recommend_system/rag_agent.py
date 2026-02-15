from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver
from .state import AgentState
from .nodes import (
    entry_node, 
    routing_node, 
    query_normalization_node, 
    sub_question_generation_node,
    human_confirmation_node,
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
import time
import os
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    SqliteSaver = None  # type: ignore


def _build_checkpointer():
    checkpoint_path = Path(
        os.getenv("LANGGRAPH_CHECKPOINT_PATH", ".runtime/langgraph_checkpoints.sqlite")
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    if SqliteSaver is not None:
        try:
            return SqliteSaver.from_conn_string(str(checkpoint_path))
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Failed to initialize SqliteSaver, fallback to MemorySaver: %s", exc)

    logger.warning(
        "Using in-memory LangGraph checkpointer. Install sqlite checkpointer for durable resume."
    )
    return MemorySaver()


_CHECKPOINTER = _build_checkpointer()

def create_rag_with_routing_agent():
    """创建带有路由功能的 RAG Agent 图"""
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("entry", entry_node)
    workflow.add_node("routing", routing_node)  # 添加路由节点
    workflow.add_node("query_normalization", query_normalization_node)
    workflow.add_node("sub_question_generation", sub_question_generation_node)
    workflow.add_node("human_confirmation", human_confirmation_node)
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
        if state.mode == "rag":
            return "rag"
        if state.mode == "draw":
            return "draw"
        else:
            return "chat"
    
    # 添加条件边处理路由
    workflow.add_conditional_edges(
        "routing",
        route_based_on_mode,
        {
            "rag": "sub_question_generation",  # RAG 分支
            "chat": "chat_answer_generation",  # Chat 分支
            "draw": "pre_drawing"              # 画图分支
        }
    )
    
    # RAG 分支流程
    workflow.add_edge("sub_question_generation", "human_confirmation")
    workflow.add_edge("human_confirmation", "evidence_collection")
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
            "continue": "evidence_collection",
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
    workflow.add_node("evidence_collection", evidence_collection_node)
    workflow.add_node("evidence_evaluation", evidence_evaluation_node)
    workflow.add_node("candidate_generation", candidate_generation_node)
    workflow.add_node("coverage_check", coverage_check_node)
    workflow.add_node("answer_generation", answer_generation_node)
    
    # 设置入口
    workflow.set_entry_point("entry")
    
    # 添加边
    workflow.add_edge("entry", "query_normalization")
    workflow.add_edge("sub_question_generation", "evidence_collection")
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
            "continue": "evidence_collection",
            "terminate": "answer_generation"
        }
    )
    
    # 添加最终边
    workflow.add_edge("answer_generation", "__end__")
    
    return workflow.compile()
