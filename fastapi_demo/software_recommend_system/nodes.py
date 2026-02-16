from typing import Dict, Any, List
from .state import AgentState
from .document_schema import Document, Metadata
from .config import settings
import openai
import time
import re
import json
from datetime import datetime
import logging
from langgraph.types import interrupt

# 导入新的工具系统
from .tools import unified_search, pre_drawing_tool, draw_image_tool

logger = logging.getLogger(__name__)

def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)

def _set_field(obj: Any, field: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[field] = value
    else:
        setattr(obj, field, value)

# OpenAI client factory with explicit auth/base_url wiring.
def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return openai.OpenAI(api_key=api_key, base_url=base_url)

# 分流词表
TECH_TERMS = {
    "frameworks": [
        "Spring Boot", "React", "Angular", "Vue", "Django", "Flask", 
        "Express", "FastAPI", "Laravel", "Ruby on Rails", "ASP.NET"
    ],
    "technologies": [
        "ORM", "CI/CD", "microservices", "containerization", 
        "kubernetes", "docker", "cache", "database", "API", "REST"
    ],
    "languages": [
        "Python", "JavaScript", "TypeScript", "Java", "C#", 
        "Go", "Rust", "PHP", "Ruby", "Swift", "Kotlin"
    ],
    "tools": [
        "git", "jenkins", "terraform", "ansible", "puppet", 
        "prometheus", "grafana", "elasticsearch", "kibana"
    ],
    "architecture": [
    "distributed system", "high availability", "scalability",
    "load balancing", "service discovery",
    "event-driven", "CQRS", "DDD",
    "monolith", "SOA"
],"data": [
    "MySQL", "PostgreSQL", "MongoDB", "Redis",
    "NoSQL", "SQL",
    "vector database", "embedding",
    "FAISS", "Milvus", "Pinecone",
    "data warehouse", "ETL"
],
"ai": [
    "LLM", "RAG", "agent", "prompt",
    "fine-tuning", "LoRA",
    "embedding model",
    "LangChain", "LangGraph",
    "OpenAI", "Hugging Face",
    "transformer"
],
"backend": [
    "message queue", "Kafka", "RabbitMQ",
    "Redis", "distributed lock",
    "rate limiting", "idempotency",
    "transaction", "eventual consistency"
],
"devops": [
    "AWS", "GCP", "Azure",
    "CI", "CD", "GitHub Actions",
    "Helm", "ArgoCD",
    "logging", "tracing", "monitoring"
],
"non_functional": [
    "performance", "latency", "throughput",
    "concurrency", "high QPS",
    "fault tolerance", "reliability",
    "security", "authentication", "authorization"
]






}

DRAWING_KEYWORDS = [
    "画图", "画一个", "生成图", "生成图表", "绘图", "绘制", "图表",
    "柱状图", "折线图", "饼图",
    "chart", "plot", "bar chart", "line chart", "pie chart"
]

def _is_drawing_request(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for keyword in DRAWING_KEYWORDS:
        if keyword in text or keyword in text_lower:
            return True
    return False

KNOWN_FACTS_MARKER = "[Known User Facts]"
ALL_TECH_TERMS = tuple(
    str(term).strip()
    for group in TECH_TERMS.values()
    for term in group
    if str(term).strip()
)
SHORT_AMBIGUOUS_TECH_TERMS = {"go", "ci", "cd"}
CHAT_FIRST_PATTERNS = (
    re.compile(r"^\s*(你好|您好|嗨|哈喽|早上好|下午好|晚上好)\s*[!！。?？]?\s*$"),
    re.compile(r"^\s*(谢谢|多谢|再见|拜拜)\s*[!！。]?\s*$"),
    re.compile(r"(?i)^\s*(hi|hello|hey|good morning|good afternoon|good evening)\b"),
    re.compile(r"(?i)^\s*(thanks|thank you|bye|goodbye)\b"),
    re.compile(r"(我是谁|我叫什么|你记得我吗|你还记得我|你是谁|你能做什么)"),
    re.compile(r"(?i)\b(who am i|what is my name|what's my name|whats my name|who are you)\b"),
    re.compile(r"^\s*(?:我叫|我是)\s*[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_\-]{0,31}\s*$"),
    re.compile(r"(?i)^\s*(?:my name is|i am|i'm)\s+[A-Za-z][A-Za-z\-' ]{0,40}\s*$"),
)
RAG_INTENT_PATTERNS = (
    re.compile(
        r"(?i)\b(recommend|comparison|compare|versus|vs|architecture|design|implement|"
        r"build|deploy|optimize|tech stack|framework|database|vector database|"
        r"rag|llm|agent|langgraph|langchain)\b"
    ),
    re.compile(r"(推荐|对比|比较|选型|架构|方案|实现|部署|优化|技术栈|数据库|向量库|检索|召回)"),
)

def _extract_current_user_query(raw_query: str) -> str:
    query = (raw_query or "").strip()
    if not query:
        return ""
    marker_index = query.find(KNOWN_FACTS_MARKER)
    if marker_index >= 0:
        return query[:marker_index].strip()
    return query

def _contains_tech_term(query_lower: str, term: str) -> bool:
    normalized_term = (term or "").strip().lower()
    if not normalized_term:
        return False
    if re.search(r"[a-z0-9]", normalized_term):
        boundary_pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
        return re.search(boundary_pattern, query_lower) is not None
    return normalized_term in query_lower

def _collect_tech_hits(query: str) -> List[str]:
    lowered = (query or "").lower()
    if not lowered:
        return []
    hits: List[str] = []
    for term in ALL_TECH_TERMS:
        if _contains_tech_term(lowered, term):
            hits.append(term)
    return hits

def _is_chat_first_query(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return True
    return any(pattern.search(normalized) for pattern in CHAT_FIRST_PATTERNS)

def _has_rag_intent(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in RAG_INTENT_PATTERNS)

def entry_node(state: AgentState) -> Dict[str, Any]:
    """用户输入节点"""
    logger.info(f"接收用户查询: {state.user_query[:50]}...")
    
    # 初始化时间戳
    messages = list(_get_field(state, "messages", []) or [])
    messages.append({"role": "user", "content": state.user_query})

    return {
        "start_time": time.time(),
        "messages": messages
    }

def routing_node(state: AgentState) -> Dict[str, Any]:
    """Routing node: decide between rag/chat/draw."""
    user_query = _get_field(state, "user_query", "")
    routing_query = _extract_current_user_query(user_query)

    if _is_drawing_request(routing_query):
        mode = "draw"
        tech_hits: List[str] = []
    elif _is_chat_first_query(routing_query) and not _has_rag_intent(routing_query):
        mode = "chat"
        tech_hits = []
    else:
        tech_hits = _collect_tech_hits(routing_query)
        has_rag_intent = _has_rag_intent(routing_query)
        if has_rag_intent:
            mode = "rag"
        elif len(tech_hits) >= 2:
            mode = "rag"
        elif len(tech_hits) == 1 and tech_hits[0].lower() not in SHORT_AMBIGUOUS_TECH_TERMS:
            mode = "rag"
        else:
            mode = "chat"

    logger.info(
        "route mode=%s raw_query=%r routing_query=%r tech_hits=%s",
        mode,
        user_query[:120],
        routing_query[:120],
        tech_hits[:8],
    )
    return {"mode": mode}

def query_normalization_node(state: AgentState) -> Dict[str, Any]:
    """查询规范化节点"""
    user_query = _get_field(state, "user_query", "")
    logger.info(f"规范化查询: {user_query}")

    normalized_queries = []
    prompt = "\n".join([
        '你是一个软件工程查询规范化助手。',
        '',
        '你的任务是：',
        '将用户的自然语言问题，转换为一个或多个“工程化、可检索”的标准问题，',
        '使其尽可能贴近以下类别的技术关键词：',
        '- 软件架构 / 分布式系统',
        '- 后端工程 / 中间件',
        '- 数据库 / 向量数据库 / 数据工程',
        '- AI / LLM / RAG / Agent',
        '- DevOps / 云 / 非功能性需求',
        '- 常见编程语言与框架',
        '',
        '转换规则：',
        '1. 删除情绪化、目标导向、模糊表达（如“我想”“有没有大神推荐”“最好”“比较厉害的”）。',
        '2. 将口语化描述替换为明确的技术概念（如“扛并发”→“high concurrency”）。',
        '3. 若存在多个技术关注点，拆分为多个规范化子查询。',
        '4. 不要引入用户未提及的新需求。',
        '5. 不要给出解决方案或建议。',
        '',
        '输出格式要求：',
        '- 只输出 JSON',
        '- 使用 normalized_queries 字段',
        '- 每个 query 都应像搜索引擎或技术文档标题',
        '',
        '示例：',
        '用户输入：',
        '“我想做一个能扛住很多人同时用的 RAG 系统，有没有好点的方案？”',
        '',
        '输出：',
        '{',
        '  "normalized_queries": [',
        '    "RAG system architecture for high concurrency",',
        '    "vector database selection for RAG",',
        '    "distributed system design for AI services"',
        '  ]',
        '}',
    ])

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_query}
            ],
            temperature=0.2
        )

        content = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                content = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", "") if message else ""

        parsed = json.loads(content) if content else {}
        if isinstance(parsed, dict):
            normalized_queries = parsed.get("normalized_queries", [])
        elif isinstance(parsed, list):
            normalized_queries = parsed

        if not isinstance(normalized_queries, list):
            raise ValueError("LLM返回格式不是列表")

        normalized_queries = [str(item).strip() for item in normalized_queries if str(item).strip()]
    except Exception as e:
        logger.error(f"调用 LLM 规范化查询失败: {str(e)}")

    normalized_query = "\n".join(normalized_queries) if normalized_queries else user_query.strip()

    constraints = {"language": "", "scenario": "", "preference": ""}

    if "python" in user_query.lower():
        constraints["language"] = "Python"
    elif "java" in user_query.lower():
        constraints["language"] = "Java"
    elif "javascript" in user_query.lower() or "js" in user_query.lower():
        constraints["language"] = "JavaScript"
    elif "go" in user_query.lower():
        constraints["language"] = "Go"
    elif "rust" in user_query.lower():
        constraints["language"] = "Rust"

    if "web" in user_query.lower() or "前端" in user_query.lower():
        constraints["scenario"] = "Web开发"
    elif "mobile" in user_query.lower() or "移动端" in user_query.lower():
        constraints["scenario"] = "移动开发"
    elif "ml" in user_query.lower() or "机器学习" in user_query.lower():
        constraints["scenario"] = "机器学习"
    elif "数据" in user_query.lower() or "database" in user_query.lower():
        constraints["scenario"] = "数据处理"

    if "轻量" in user_query.lower() or "lightweight" in user_query.lower():
        constraints["preference"] = "轻量级"
    elif "企业" in user_query.lower() or "enterprise" in user_query.lower():
        constraints["preference"] = "企业级"
    elif "开源" in user_query.lower() or "open source" in user_query.lower():
        constraints["preference"] = "开源"

    return {
        "normalized_query": normalized_query,
        "constraints": constraints
    }

def sub_question_generation_node(state: AgentState) -> Dict[str, Any]:
    """子问题生成节点"""
    normalized_query = _get_field(state, "normalized_query", "")
    constraints = _get_field(state, "constraints", {})

    logger.info(f"为查询生成子问题: {normalized_query}")

    sub_questions = []
    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "\n".join(
                        [
                            "你是软件推荐系统的需求分析助手。",
                            "该软件要解决的核心问题是什么，目标用户是谁？",
                            "该需求主要属于哪类软件工程领域（如推荐系统、RAG、AI 工具）？",
                            "是否有明确的技术栈或运行环境限制（如 Java/Spring Boot、Python/FastAPI）？",
                            "系统是否有性能、并发或可扩展性的非功能性要求？",
                            "该系统涉及哪些数据类型，是否需要数据库或向量数据库？",
                            "最终软件的交付形态是什么（Web 服务、API、工具平台）？",
                            "只返回JSON数组或包含 sub_questions 字段的JSON对象，不要输出其它文本。"
                        ]
                    )
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"query": normalized_query, "constraints": constraints},
                        ensure_ascii=False
                    )
                }
            ],
            temperature=0.3
        )

        content = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                content = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", "") if message else ""

        parsed = json.loads(content) if content else []
        if isinstance(parsed, dict):
            parsed = parsed.get("sub_questions", [])

        if not isinstance(parsed, list):
            raise ValueError("LLM返回格式不是子问题列表")

        sub_questions = [str(item).strip() for item in parsed if str(item).strip()]
    except Exception as e:
        logger.error(f"调用 LLM 生成子问题失败: {str(e)}")

    if not sub_questions:
        sub_questions = [normalized_query]
        if "和" in normalized_query or "and" in normalized_query.lower():
            parts = re.split(r'和|and', normalized_query)
            if len(parts) > 1:
                sub_questions = [part.strip() for part in parts if part.strip()]

    return {
        "sub_questions": sub_questions
    }


def _normalize_sub_questions(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    cleaned: List[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


async def human_confirmation_node(state: AgentState) -> Dict[str, Any]:
    """
    Human-in-the-loop 阻塞节点：
    1) 首次到达时通过 interrupt 暂停；
    2) 外部 resume 后拿到用户确认内容；
    3) 写回最终 sub_questions，继续后续检索流程。
    """
    original_sub_questions = _normalize_sub_questions(_get_field(state, "sub_questions", []))
    if not original_sub_questions:
        fallback_query = str(_get_field(state, "normalized_query", "")).strip()
        original_sub_questions = [fallback_query] if fallback_query else []

    request_payload = {
        "type": "human_confirmation",
        "query": _get_field(state, "user_query", ""),
        "sub_questions": original_sub_questions,
        "instruction": "请返回 JSON: {\"action\":\"confirm|edit\", \"sub_questions\":[...], \"comment\":\"...\"}",
    }

    try:
        human_input = interrupt(request_payload)
    except RuntimeError as exc:
        # Python 3.10 async runtime may not provide LangGraph runnable context for interrupt().
        if "Called get_config outside of a runnable context" not in str(exc):
            raise
        logger.warning(
            "LangGraph interrupt context unavailable; auto-confirming sub-questions."
        )
        return {
            "sub_questions": original_sub_questions,
            "awaiting_human_confirmation": False,
            "pending_sub_questions": [],
            "human_feedback": "",
        }

    action = str(_get_field(human_input, "action", "confirm")).strip().lower()
    if action not in {"confirm", "edit"}:
        action = "confirm"

    edited_sub_questions = _normalize_sub_questions(_get_field(human_input, "sub_questions", []))
    comment = str(_get_field(human_input, "comment", "")).strip()
    final_sub_questions = edited_sub_questions if action == "edit" and edited_sub_questions else original_sub_questions

    return {
        "sub_questions": final_sub_questions,
        "awaiting_human_confirmation": False,
        "pending_sub_questions": [],
        "human_feedback": comment,
    }


def evidence_collection_node(state: AgentState) -> Dict[str, Any]:
    """证据收集节点"""
    sub_questions = _get_field(state, "sub_questions", [])
    evidence = _get_field(state, "evidence", [])
    iteration_count = _get_field(state, "iteration_count", 0)
    
    logger.info(f"收集证据，迭代次数: {iteration_count}, 子问题数量: {len(sub_questions)}")
    
    # 为每个子问题收集证据
    for question in sub_questions:
        # 使用统一搜索入口获取相关文档
        search_results = unified_search(question, k=settings.TOP_K)
        
        # 计算质量分数（简化实现，实际应用中会更复杂）
        quality_score = min(0.9, 0.5 + (len(search_results) * 0.1))
        
        evidence_item = {
            "question": question,
            "documents": search_results,
            "search_results": [{"content": doc.content, "score": doc.score} for doc in search_results],
            "quality_score": quality_score
        }
        
        evidence.append(evidence_item)
    
    return {
        "evidence": evidence,
        "iteration_count": iteration_count + 1
    }

def evidence_evaluation_node(state: AgentState) -> Dict[str, Any]:
    """证据评估节点"""
    evidence = _get_field(state, "evidence", [])
    
    logger.info(f"评估 {len(evidence)} 个证据项")
    
    # 在实际应用中，这里会使用 LLM 来评估证据的质量
    # 简化实现：根据文档数量和分数评估
    
    for item in evidence:
        # 重新计算质量分数，结合相关性、时效性和权威性
        docs = _get_field(item, "documents", [])
        if docs:
            avg_score = sum(doc.score for doc in docs) / len(docs) if docs else 0
            # 简化的质量分数计算，实际应用中会更复杂
            _set_field(item, "quality_score", min(1.0, avg_score + 0.1))
    
    return {
        "evidence": evidence
    }

def candidate_generation_node(state: AgentState) -> Dict[str, Any]:
    """候选方案生成节点"""
    evidence = _get_field(state, "evidence", [])
    candidates = _get_field(state, "candidates", [])

    logger.info(f"基于 {len(evidence)} 个证据项生成候选方案")

    if not evidence:
        return {"candidates": candidates}

    def _truncate(text: str, max_len: int = 600) -> str:
        if not text:
            return ""
        return text if len(text) <= max_len else text[:max_len] + "..."

    evidence_payload = []
    for item in evidence:
        docs = _get_field(item, "documents", [])
        doc_summaries = []
        for doc in docs[:3]:
            content = _get_field(doc, "content", "")
            metadata = _get_field(doc, "metadata", {})
            source = _get_field(metadata, "source", "")
            doc_summaries.append({
                "source": source,
                "content": _truncate(content, 600),
                "score": _get_field(doc, "score", 0.0)
            })
        evidence_payload.append({
            "question": _get_field(item, "question", ""),
            "quality_score": _get_field(item, "quality_score", 0.0),
            "documents": doc_summaries
        })

    llm_candidates = []
    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是软件推荐助手。请根据给定证据生成候选方案列表。"
                        "只返回JSON数组或包含 candidates 字段的JSON对象，不要输出其它文本。"
                        "每个候选方案必须包含字段: solution, rationale, pros, cons, relevance_score(0-1)。"
                    )
                },
                {"role": "user", "content": json.dumps(evidence_payload, ensure_ascii=False)}
            ],
            temperature=0.4
        )

        content = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                content = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                content = getattr(message, "content", "") if message else ""

        parsed = json.loads(content) if content else []
        if isinstance(parsed, dict):
            parsed = parsed.get("candidates", [])

        if not isinstance(parsed, list):
            raise ValueError("LLM返回格式不是候选列表")

        for item in parsed:
            if not isinstance(item, dict):
                continue
            solution = str(item.get("solution", "")).strip()
            if not solution:
                continue
            rationale = str(item.get("rationale", "")).strip()
            pros = item.get("pros") if isinstance(item.get("pros"), list) else []
            cons = item.get("cons") if isinstance(item.get("cons"), list) else []
            score = item.get("relevance_score", 0.0)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(1.0, score))

            llm_candidates.append({
                "solution": solution,
                "rationale": rationale,
                "pros": pros,
                "cons": cons,
                "relevance_score": score
            })
    except Exception as e:
        logger.error(f"调用 LLM 生成候选方案失败: {str(e)}")

    if llm_candidates:
        candidates.extend(llm_candidates)
    else:
        for item in evidence:
            docs = _get_field(item, "documents", [])
            if docs:
                first_doc = docs[0]
                solution_text = first_doc.content[:200] + "..." if len(first_doc.content) > 200 else first_doc.content

                relevance_score = _get_field(item, "quality_score", 0.5)

                candidate_solution = {
                    "solution": solution_text,
                    "rationale": f"根据文档来源 {first_doc.metadata.source} 推荐",
                    "pros": ["相关性强", "来源可靠"] if relevance_score > 0.7 else ["有一定参考价值"],
                    "cons": ["信息可能不够全面"] if relevance_score < 0.8 else [],
                    "relevance_score": relevance_score
                }

                candidates.append(candidate_solution)

    candidates.sort(key=lambda x: _get_field(x, "relevance_score", 0.0), reverse=True)

    return {
        "candidates": candidates
    }

def coverage_check_node(state: AgentState) -> Dict[str, Any]:
    """覆盖率检查节点"""
    sub_questions = _get_field(state, "sub_questions", [])
    evidence = _get_field(state, "evidence", [])
    candidates = _get_field(state, "candidates", [])
    coverage_threshold = _get_field(state, "coverage_threshold", 0.8)
    max_iterations = _get_field(state, "max_iterations", 3)
    iteration_count = _get_field(state, "iteration_count", 0)
    
    logger.info(f"检查覆盖率: {len(evidence)}/{len(sub_questions)}")
    
    # 计算覆盖率：已解答的子问题数量 / 总子问题数量
    covered_questions = len([item for item in evidence if _get_field(item, "documents", [])])
    total_questions = len(sub_questions)
    
    coverage = covered_questions / total_questions if total_questions > 0 else 0
    
    # 检查是否需要继续细化
    needs_refinement = (
        coverage < coverage_threshold and 
        iteration_count < max_iterations and
        len(candidates) == 0  # 如果没有生成任何候选方案，也需要继续
    )
    
    logger.info(f"覆盖率: {coverage:.2f}, 阈值: {coverage_threshold}, 需要细化: {needs_refinement}")
    
    return {
        "coverage": coverage,
        "needs_refinement": needs_refinement
    }

def answer_generation_node(state: AgentState) -> Dict[str, Any]:
    """答案生成节点（RAG模式）"""
    user_query = _get_field(state, "user_query", "")
    candidates = _get_field(state, "candidates", [])
    evidence = _get_field(state, "evidence", [])
    
    logger.info(f"生成最终答案，基于 {len(candidates)} 个候选方案")
    
    if candidates:
        # 选择得分最高的候选方案
        best_candidate = candidates[0]
        
        # 构造最终答案
        final_answer = f"根据您的查询 '{user_query}'，我推荐:\n\n"
        final_answer += f"方案: {_get_field(best_candidate, 'solution', '')}\n"
        final_answer += f"理由: {_get_field(best_candidate, 'rationale', '')}\n"
        pros = _get_field(best_candidate, 'pros', [])
        cons = _get_field(best_candidate, 'cons', [])
        final_answer += f"优点: {', '.join(pros) if pros else '无'}\n"
        if cons:
            final_answer += f"缺点: {', '.join(cons)}\n"
    else:
        final_answer = f"抱歉，基于现有知识库，我无法为您的查询 '{user_query}' 找到合适的软件推荐方案。"
    
    messages = list(_get_field(state, "messages", []) or [])
    messages.append({"role": "assistant", "content": final_answer})

    return {
        "final_answer": final_answer,
        "messages": messages
    }

def chat_answer_generation_node(state: AgentState) -> Dict[str, Any]:
    """答案生成节点（Chat模式）"""
    user_query = _get_field(state, "user_query", "")

    logger.info(f"使用聊天模式回答: {user_query}")

    messages = list(_get_field(state, "messages", []) or [])
    if not messages:
        messages = [{"role": "user", "content": user_query}]

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            temperature=0.7
        )

        final_answer = ""
        if hasattr(response, "choices") and response.choices:
            first_choice = response.choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message") or {}
                final_answer = message.get("content") or ""
            else:
                message = getattr(first_choice, "message", None)
                final_answer = getattr(message, "content", "") if message else ""

        if not final_answer:
            final_answer = "抱歉，暂时无法获取LLM回复，请稍后再试。"
    except Exception as e:
        logger.error(f"调用 LLM 失败: {str(e)}")
        final_answer = "抱歉，暂时无法获取LLM回复，请稍后再试。"

    messages.append({"role": "assistant", "content": final_answer})

    return {
        "final_answer": final_answer,
        "messages": messages
    }

def pre_drawing_node(state: AgentState) -> Dict[str, Any]:
    """画图前处理节点"""
    user_query = _get_field(state, "user_query", "")
    structured_params = pre_drawing_tool(user_query)
    return {"drawing_params": structured_params}

def draw_image_node(state: AgentState) -> Dict[str, Any]:
    """画图执行节点"""
    structured_params = _get_field(state, "drawing_params", "")
    image_url = draw_image_tool(structured_params)
    final_answer = f"图像已生成: {image_url}"
    return {"image_result": image_url, "final_answer": final_answer}

