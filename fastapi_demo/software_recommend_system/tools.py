from typing import List
import json
import logging
import os
import time

import chromadb
import openai

from .config import settings
from .document_schema import Document
from .ingestion.embedder import embed_texts
from .logging_utils import elapsed_ms, error_fields, log_event, log_exception, new_trace_id, text_preview
from .observability import wrap_openai

logger = logging.getLogger(__name__)
TAVILY_MAX_RESULTS = 3


def _search_tool_impl_name() -> str:
    if not search_tool:
        return "none"
    return f"{search_tool.__class__.__module__}.{search_tool.__class__.__name__}"


def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return wrap_openai(openai.OpenAI(api_key=api_key, base_url=base_url))


def similarity_search(query: str, k: int = 5, trace_id: str | None = None) -> List[Document]:
    """
    执行向量库相似性搜索，查找与查询最相关的文档
    """
    current_trace_id = trace_id or new_trace_id("search")
    query_hint = text_preview(query)
    start_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "search.vector.start",
        component="search",
        trace_id=current_trace_id,
        engine="chroma",
        query=query_hint,
        top_k=k,
        collection="software_recommendations",
    )
    try:
        client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
        collections = client.list_collections()
        if not any(col.name == "software_recommendations" for col in collections):
            log_event(
                logger,
                logging.INFO,
                "search.vector.skip",
                component="search",
                trace_id=current_trace_id,
                engine="chroma",
                query=query_hint,
                reason="collection_missing",
                collection="software_recommendations",
            )
            return []
        collection = client.get_collection("software_recommendations")

        embed_started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "search.vector.embedding.start",
            component="search",
            trace_id=current_trace_id,
            query=query_hint,
        )
        query_embeddings = embed_texts([query])
        embedding_dim = len(query_embeddings[0]) if query_embeddings and query_embeddings[0] else 0
        log_event(
            logger,
            logging.INFO,
            "search.vector.embedding.done",
            component="search",
            trace_id=current_trace_id,
            query=query_hint,
            elapsed_ms=elapsed_ms(embed_started_at),
            vectors=len(query_embeddings),
            dimension=embedding_dim,
        )

        vector_query_started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "search.vector.query.start",
            component="search",
            trace_id=current_trace_id,
            engine="chroma",
            query=query_hint,
            top_k=k,
        )
        results = collection.query(
            query_embeddings=query_embeddings,
            n_results=k,
        )
        raw_documents = results.get("documents") or [[]]
        raw_metadatas = results.get("metadatas") or [[]]
        raw_distances = results.get("distances") or [[]]
        doc_row = raw_documents[0] if raw_documents else []
        metadata_row = raw_metadatas[0] if raw_metadatas else []
        distance_row = raw_distances[0] if raw_distances else []
        log_event(
            logger,
            logging.INFO,
            "search.vector.query.done",
            component="search",
            trace_id=current_trace_id,
            engine="chroma",
            query=query_hint,
            elapsed_ms=elapsed_ms(vector_query_started_at),
            raw_count=len(doc_row),
        )

        documents: List[Document] = []
        for index, doc_content in enumerate(doc_row):
            doc_metadata = metadata_row[index] if index < len(metadata_row) and metadata_row[index] else {}
            doc_score = distance_row[index] if index < len(distance_row) and distance_row[index] is not None else 0.0
            metadata_obj = {
                "source": doc_metadata.get("source", ""),
                # Preserve the upstream source document id so recall eval can
                # match retrieved ids with dataset gold_doc_ids.
                "doc_id": (
                    doc_metadata.get("source_doc_id")
                    or doc_metadata.get("doc_id")
                    or doc_metadata.get("filename")
                ),
                "author": doc_metadata.get("author"),
                "published_date": doc_metadata.get("published_date"),
                "updated_date": doc_metadata.get("updated_date"),
                "url": doc_metadata.get("url"),
                "tags": doc_metadata.get("tags", []),
                "source_ranking": doc_metadata.get("source_ranking", 0.0),
            }
            documents.append(
                Document(
                    content=doc_content,
                    metadata=metadata_obj,
                    score=doc_score,
                )
            )

        log_event(
            logger,
            logging.INFO,
            "search.vector.done",
            component="search",
            trace_id=current_trace_id,
            engine="chroma",
            query=query_hint,
            docs=len(documents),
            elapsed_ms=elapsed_ms(start_at),
        )
        return documents
    except Exception as exc:
        if "Collection [software_recommendations] does not exist" in str(exc):
            log_event(
                logger,
                logging.INFO,
                "search.vector.skip",
                component="search",
                trace_id=current_trace_id,
                engine="chroma",
                query=query_hint,
                reason="collection_missing",
                collection="software_recommendations",
            )
            return []
        log_event(
            logger,
            logging.WARNING,
            "search.vector.fail",
            component="search",
            trace_id=current_trace_id,
            engine="chroma",
            query=query_hint,
            degraded_to_empty=True,
            elapsed_ms=elapsed_ms(start_at),
            **error_fields(exc),
        )
        return []


# Tavily search tool (langchain_tavily wrapper only)
try:
    from langchain_tavily import TavilySearch  # type: ignore
    _TAVILY_NEW_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    TavilySearch = None  # type: ignore
    _TAVILY_NEW_IMPORT_ERROR = exc

tavily_api_key = os.getenv("TAVILY_API_KEY")
search_tool = None
if not tavily_api_key:
    log_event(
        logger,
        logging.WARNING,
        "search.web.init.skip",
        component="search",
        engine="tavily",
        reason="api_key_missing",
    )
else:
    if TavilySearch is None:
        log_event(
            logger,
            logging.WARNING,
            "search.web.init.skip",
            component="search",
            engine="tavily",
            reason="dependency_unavailable",
            error=str(_TAVILY_NEW_IMPORT_ERROR),
        )
    else:
        search_tool = TavilySearch(
            max_results=TAVILY_MAX_RESULTS,
            include_answer=False,
            include_raw_content=False,
        )
        log_event(
            logger,
            logging.INFO,
            "search.web.init.done",
            component="search",
            engine="tavily",
            impl=_search_tool_impl_name(),
            max_results=TAVILY_MAX_RESULTS,
        )


def _tavily_search(query: str, trace_id: str | None = None) -> List[Document]:
    current_trace_id = trace_id or new_trace_id("search")
    query_hint = text_preview(query)
    if not search_tool:
        log_event(
            logger,
            logging.INFO,
            "search.web.skip",
            component="search",
            trace_id=current_trace_id,
            engine="tavily",
            query=query_hint,
            reason="search_tool_not_initialized",
        )
        return []

    dispatch_method = "unknown"
    started_at = time.perf_counter()
    log_event(
        logger,
        logging.INFO,
        "search.web.start",
        component="search",
        trace_id=current_trace_id,
        engine="tavily",
        query=query_hint,
        impl=_search_tool_impl_name(),
        has_invoke=hasattr(search_tool, "invoke"),
        has_run=hasattr(search_tool, "run"),
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
        log_event(
            logger,
            logging.WARNING,
            "search.web.fail",
            component="search",
            trace_id=current_trace_id,
            engine="tavily",
            query=query_hint,
            impl=_search_tool_impl_name(),
            dispatch=dispatch_method,
            elapsed_ms=elapsed_ms(started_at),
            **error_fields(exc),
        )
        return []

    if not raw_results:
        log_event(
            logger,
            logging.INFO,
            "search.web.empty",
            component="search",
            trace_id=current_trace_id,
            engine="tavily",
            query=query_hint,
            impl=_search_tool_impl_name(),
            dispatch=dispatch_method,
            elapsed_ms=elapsed_ms(started_at),
        )
        return []

    if isinstance(raw_results, dict):
        raw_results = raw_results.get("results") or raw_results.get("data") or [raw_results]
    if not isinstance(raw_results, list):
        raw_results = [raw_results]

    documents: List[Document] = []
    top_urls: List[str] = []
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

        documents.append(
            Document(
                content=doc_content,
                metadata={
                    "source": doc_source,
                    "url": doc_url,
                    "source_ranking": 0.0,
                    "tags": [],
                },
                score=float(doc_score) if doc_score is not None else 0.0,
            )
        )
        if doc_url:
            top_urls.append(str(doc_url))

    log_event(
        logger,
        logging.INFO,
        "search.web.done",
        component="search",
        trace_id=current_trace_id,
        engine="tavily",
        query=query_hint,
        impl=_search_tool_impl_name(),
        dispatch=dispatch_method,
        raw_count=len(raw_results),
        docs=len(documents),
        top_urls=top_urls[:3],
        elapsed_ms=elapsed_ms(started_at),
    )
    return documents


# 画图相关工具
def pre_drawing(user_request: str) -> str:
    """
    翻译官工具：将用户的自然语言画图需求转换为结构化画图参数
    """
    log_event(
        logger,
        logging.INFO,
        "tool.pre_drawing.start",
        component="tool",
        request=text_preview(user_request),
    )

    chart_type = "bar"
    if "饼图" in user_request or "pie" in user_request.lower():
        chart_type = "pie"
    elif "折线" in user_request or "line" in user_request.lower():
        chart_type = "line"
    elif "柱状" in user_request or "bar" in user_request.lower():
        chart_type = "bar"

    result = {
        "chart_type": chart_type,
        "x_label": "X轴标签",
        "y_label": "Y轴标签",
        "data": {
            "labels": ["类别A", "类别B", "类别C"],
            "values": [10, 20, 15],
        },
        "style": "business",
        "constraints": {
            "no_guessing": True,
        },
    }
    log_event(
        logger,
        logging.INFO,
        "tool.pre_drawing.done",
        component="tool",
        chart_type=chart_type,
    )
    return json.dumps(result, ensure_ascii=False)


def draw_image(structured_params: str) -> str:
    """
    执行器工具：使用结构化参数生成图像
    """
    trace_id = new_trace_id("llm")
    try:
        params = json.loads(structured_params)
        required_fields = ["chart_type", "x_label", "y_label", "data"]
        for field in required_fields:
            if field not in params:
                raise ValueError(f"缺少必要字段: {field}")
        if "labels" not in params["data"] or "values" not in params["data"]:
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

        client = _get_openai_client()
        started_at = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            "llm.invoke.start",
            component="llm",
            trace_id=trace_id,
            scene="image_generation",
            model=settings.IMAGE_MODEL,
            request_hint=f"chart_type={params['chart_type']}",
        )
        response = client.images.generate(
            model=settings.IMAGE_MODEL,
            prompt=prompt,
            size=settings.IMAGE_SIZE,
        )
        response_items = len(getattr(response, "data", []) or [])
        log_event(
            logger,
            logging.INFO,
            "llm.invoke.done",
            component="llm",
            trace_id=trace_id,
            scene="image_generation",
            model=settings.IMAGE_MODEL,
            elapsed_ms=elapsed_ms(started_at),
            response_items=response_items,
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
                log_event(
                    logger,
                    logging.INFO,
                    "llm.output.image_url",
                    component="llm",
                    trace_id=trace_id,
                    scene="image_generation",
                    has_url=True,
                )
                return image_url
            if image_b64:
                log_event(
                    logger,
                    logging.INFO,
                    "llm.output.image_b64",
                    component="llm",
                    trace_id=trace_id,
                    scene="image_generation",
                    has_b64=True,
                )
                return f"data:image/png;base64,{image_b64}"

        raise RuntimeError("图像生成响应为空或缺少数据")
    except json.JSONDecodeError as exc:
        log_event(
            logger,
            logging.ERROR,
            "tool.draw_image.invalid_json",
            component="tool",
            trace_id=trace_id,
            **error_fields(exc),
        )
        raise ValueError(f"输入不是有效的JSON格式: {structured_params}")
    except Exception as exc:
        log_exception(
            logger,
            "llm.invoke.fail",
            component="llm",
            trace_id=trace_id,
            scene="image_generation",
            model=settings.IMAGE_MODEL,
            **error_fields(exc),
        )
        raise


# 将画图工具包装为可直接调用的函数
def pre_drawing_tool(user_request: str) -> str:
    """将用户的自然语言画图需求转换为结构化画图参数"""
    return pre_drawing(user_request)


def draw_image_tool(structured_params: str) -> str:
    """使用结构化参数生成图像"""
    return draw_image(structured_params)


def unified_search(query: str, k: int | None = None) -> List[Document]:
    """Backward-compatible retrieval entrypoint."""
    from .retriever import retrieve

    vector_k = k if k is not None else settings.TOP_K
    return retrieve(query=query, top_k=vector_k)


def get_all_tools():
    """获取所有可用工具的列表"""
    tools = [
        unified_search,     # 统一搜索入口
        similarity_search,  # 现有的相似性搜索工具
        pre_drawing_tool,   # 画图前处理工具
        draw_image_tool,    # 画图执行工具
    ]

    # 如果搜索工具可用，添加到工具列表
    if search_tool:
        tools.append(search_tool)

    return tools
