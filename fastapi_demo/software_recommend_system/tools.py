from typing import List, Dict, Any
from .document_schema import Document
from .config import settings
from .ingestion.embedder import embed_texts
import chromadb
import openai
import logging
import json
import os
import time

logger = logging.getLogger(__name__)
TAVILY_MAX_RESULTS = 3


def _search_tool_impl_name() -> str:
    if not search_tool:
        return "none"
    return f"{search_tool.__class__.__module__}.{search_tool.__class__.__name__}"


def _query_preview(query: str, max_len: int = 120) -> str:
    cleaned = " ".join((query or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return f"{cleaned[:max_len]}..."

def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def similarity_search(query: str, k: int = 5) -> List[Document]:
    """
    执行向量库相似性搜索，查找与查询最相关的文档
    """
    try:
        # 初始化向量数据库客户端
        client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        collections = client.list_collections()
        if not any(col.name == "software_recommendations" for col in collections):
            logger.info("向量库无集合 software_recommendations，跳过 similarity_search。")
            return []
        collection = client.get_collection("software_recommendations")
        
        # 对查询进行嵌入
        query_embeddings = embed_texts([query])
        
        # 执行相似性搜索
        results = collection.query(
            query_embeddings=query_embeddings,
            n_results=k
        )
        
        # 将结果转换为 Document 对象
        documents = []
        for i in range(len(results['documents'][0])):
            doc_content = results['documents'][0][i]
            doc_metadata = results['metadatas'][0][i] if results['metadatas'] and len(results['metadatas']) > 0 else {}
            doc_score = results['distances'][0][i] if results['distances'] and len(results['distances']) > 0 else 0.0
            
            # 创建 Metadata 对象
            metadata_obj = {
                'source': doc_metadata.get('source', ''),
                'author': doc_metadata.get('author'),
                'published_date': doc_metadata.get('published_date'),
                'updated_date': doc_metadata.get('updated_date'),
                'url': doc_metadata.get('url'),
                'tags': doc_metadata.get('tags', []),
                'source_ranking': doc_metadata.get('source_ranking', 0.0)
            }
            
            # 创建 Document 对象
            doc = Document(
                content=doc_content,
                metadata=metadata_obj,
                score=doc_score
            )
            documents.append(doc)
        
        logger.info(f"从向量库找到 {len(documents)} 个相关文档")
        return documents
    except Exception as e:
        if "Collection [software_recommendations] does not exist" in str(e):
            logger.info("向量库无集合 software_recommendations，跳过 similarity_search。")
            return []
        logger.warning("向量库搜索失败，降级为空结果: %s", e)
        return []

# Tavily搜索工具（仅使用 langchain_tavily 封装）
try:
    from langchain_tavily import TavilySearch  # type: ignore
    _TAVILY_NEW_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    TavilySearch = None  # type: ignore
    _TAVILY_NEW_IMPORT_ERROR = exc

tavily_api_key = os.getenv("TAVILY_API_KEY")
search_tool = None
if not tavily_api_key:
    logger.warning("[tools.init_tavily_search] status=api_key_missing message=TAVILY_API_KEY 未设置，跳过 Tavily 搜索工具。")
else:
    if TavilySearch is None:
        logger.warning(
            "[tools.init_tavily_search] status=dependency_unavailable "
            "message=Tavily search dependencies unavailable; install with: pip install -U langchain-tavily. "
            "new_import_error=%s",
            _TAVILY_NEW_IMPORT_ERROR,
        )
    else:
        search_tool = TavilySearch(max_results=TAVILY_MAX_RESULTS,include_answer=False,include_raw_content=False)
        logger.info(
            "[tools.init_tavily_search] selected_impl=%s max_results=%s",
            _search_tool_impl_name(),
            TAVILY_MAX_RESULTS,
        )

def _tavily_search(query: str) -> List[Document]:
    if not search_tool:
        logger.info(
            "[tools._tavily_search] skipped reason=search_tool_not_initialized query=%r",
            _query_preview(query),
        )
        return []
    dispatch_method = "unknown"
    logger.info(
        "[tools._tavily_search] start query=%r impl=%s has_invoke=%s has_run=%s ",
        _query_preview(query),
        _search_tool_impl_name(),
        hasattr(search_tool, "invoke"),
        hasattr(search_tool, "run"),
        
    )
    try:
        if hasattr(search_tool, "invoke"):
            dispatch_method = "invoke"
            raw_results = search_tool.invoke({"query": query})
        elif hasattr(search_tool, "run"):
            dispatch_method = "run"
            raw_results = search_tool.run(query)
        else:
            dispatch_method = "__call__"
            raw_results = search_tool(query)
    except Exception as exc:
        logger.warning(
            "[tools._tavily_search] failed dispatch=%s impl=%s query=%r error=%s",
            dispatch_method,
            _search_tool_impl_name(),
            _query_preview(query),
            exc,
        )
        return []

    if not raw_results:
        logger.info(
            "[tools._tavily_search] empty_result dispatch=%s impl=%s query=%r",
            dispatch_method,
            _search_tool_impl_name(),
            _query_preview(query),
        )
        return []
    if isinstance(raw_results, dict):
        raw_results = raw_results.get("results") or raw_results.get("data") or [raw_results]
    if not isinstance(raw_results, list):
        raw_results = [raw_results]
    logger.info(
        "[tools._tavily_search] raw_normalized dispatch=%s impl=%s raw_count=%s query=%r",
        dispatch_method,
        _search_tool_impl_name(),
        len(raw_results),
        _query_preview(query),
    )

    documents = []
    top_urls = []
    for item in raw_results[:TAVILY_MAX_RESULTS]:
        if isinstance(item, dict):
            title = item.get("title") or ""
            snippet = item.get("snippet") or ""
            content = item.get("content") or item.get("body") or ""
            parts = [part for part in [title, snippet, content] if part]
            doc_content = "\n".join(parts) if parts else str(item)
            doc_url = item.get("url")
            doc_source = item.get("source") or "tavily"
            doc_score = item.get("score") or 0.0
        else:
            doc_content = str(item)
            doc_url = None
            doc_source = "tavily"
            doc_score = 0.0

        doc = Document(
            content=doc_content,
            metadata={
                "source": doc_source,
                "url": doc_url,
                "source_ranking": 0.0,
                "tags": []
            },
            score=float(doc_score) if doc_score is not None else 0.0
        )
        documents.append(doc)
        if doc_url:
            top_urls.append(str(doc_url))

    logger.info(
        "[tools._tavily_search] done dispatch=%s impl=%s returned_docs=%s top_urls=%s",
        dispatch_method,
        _search_tool_impl_name(),
        len(documents),
        top_urls[:3],
    )

    return documents

# 画图相关工具
def pre_drawing(user_request: str) -> str:
    """
    翻译官工具：将用户的自然语言画图需求转换为结构化画图参数
    """
    logger.info(f"处理画图请求: {user_request}")
    
    # 这里只是示例实现，实际应用中可能需要调用LLM来解析用户请求
    # 为了演示目的，这里使用简单的关键词识别
    chart_type = "bar"
    if "饼图" in user_request or "pie" in user_request.lower():
        chart_type = "pie"
    elif "折线" in user_request or "line" in user_request.lower():
        chart_type = "line"
    elif "柱状" in user_request or "bar" in user_request.lower():
        chart_type = "bar"
    
    # 示例结构化输出，实际应用中应该更智能地解析用户需求
    result = {
        "chart_type": chart_type,
        "x_label": "X轴标签",
        "y_label": "Y轴标签",
        "data": {
            "labels": ["类别A", "类别B", "类别C"],
            "values": [10, 20, 15]
        },
        "style": "business",
        "constraints": {
            "no_guessing": True
        }
    }
    
    return json.dumps(result, ensure_ascii=False)

def draw_image(structured_params: str) -> str:
    """
    执行器工具：使用结构化参数生成图像
    """
    try:
        params = json.loads(structured_params)
        
        # 校验必要字段
        required_fields = ['chart_type', 'x_label', 'y_label', 'data']
        for field in required_fields:
            if field not in params:
                raise ValueError(f"缺少必要字段: {field}")
        
        if 'labels' not in params['data'] or 'values' not in params['data']:
            raise ValueError("data 字段必须包含 labels 和 values")
        
        prompt = (
            f"Create a {params['chart_type']} chart. "
            f"X-axis: {params['x_label']}. "
            f"Y-axis: {params['y_label']}. "
            f"Labels: {params['data']['labels']}. "
            f"Values: {params['data']['values']}. "
            f"Style: {params.get('style', 'business')}. "
            f"Constraints: no guessing."
        )
        logger.info(f"生成 {params['chart_type']} 类型的图表")

        client = _get_openai_client()
        started_at = time.perf_counter()
        logger.info(
            "LLM_INVOKE_START scene=image_generation model=%s request_hint=chart_type=%s",
            settings.IMAGE_MODEL,
            params["chart_type"],
        )
        response = client.images.generate(
            model=settings.IMAGE_MODEL,
            prompt=prompt,
            size=settings.IMAGE_SIZE
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "LLM_INVOKE_DONE scene=image_generation model=%s elapsed_ms=%d",
            settings.IMAGE_MODEL,
            elapsed_ms,
        )

        if hasattr(response, "data") and response.data:
            image_data = response.data[0]
            if isinstance(image_data, dict):
                image_url = image_data.get("url")
                image_b64 = image_data.get("b64_json")
            else:
                image_url = getattr(image_data, "url", None)
                image_b64 = getattr(image_data, "b64_json", None)

            if image_url:
                return image_url
            if image_b64:
                return f"data:image/png;base64,{image_b64}"

        raise RuntimeError("图像生成响应为空或缺少数据")
    except json.JSONDecodeError as e:
        logger.error(f"解析结构化参数失败: {str(e)}")
        raise ValueError(f"输入不是有效的JSON格式: {structured_params}")
    except Exception as e:
        logger.exception(
            "LLM_INVOKE_FAILED scene=image_generation model=%s",
            settings.IMAGE_MODEL,
        )
        logger.error(f"绘制图像失败: {str(e)}")
        raise e

# 将画图工具包装为可直接调用的函数
def pre_drawing_tool(user_request: str) -> str:
    """将用户的自然语言画图需求转换为结构化画图参数"""
    return pre_drawing(user_request)

def draw_image_tool(structured_params: str) -> str:
    """使用结构化参数生成图像"""
    return draw_image(structured_params)

def unified_search(query: str, k: int | None = None) -> List[Document]:
    """
    统一搜索入口：向量库检索 + 可用时的 Tavily 实时检索
    """
    vector_k = k if k is not None else settings.TOP_K
    logger.info(
        "[tools.unified_search] start query=%r vector_k=%s tavily_enabled=%s tavily_impl=%s",
        _query_preview(query),
        vector_k,
        bool(search_tool),
        _search_tool_impl_name(),
    )
    documents = similarity_search(query, k=vector_k)
    logger.info(
        "[tools.unified_search] after_similarity vector_docs=%s query=%r",
        len(documents),
        _query_preview(query),
    )
    if search_tool:
        tavily_docs = _tavily_search(query)
        documents.extend(tavily_docs)
        logger.info(
            "[tools.unified_search] after_tavily tavily_docs=%s total_docs=%s query=%r tavily_docs=%s",
            len(tavily_docs),
            len(documents),
            _query_preview(query),
            tavily_docs,
        )
    else:
        logger.info(
            "[tools.unified_search] skipped_tavily reason=search_tool_not_initialized query=%r",
            _query_preview(query),
        )
    return documents

# 定义工具列表
def get_all_tools():
    """获取所有可用工具的列表"""
    tools = [
        unified_search,     # 统一搜索入口
        similarity_search,  # 现有的相似性搜索工具
        pre_drawing_tool,   # 画图前处理工具
        draw_image_tool     # 画图执行工具
    ]
    
    # 如果搜索工具可用，添加到工具列表
    if search_tool:
        tools.append(search_tool)
    
    return tools
