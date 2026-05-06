import logging
import os
import time
from typing import Any, Callable, Dict, List

import openai
from langgraph.config import get_stream_writer

from .config import settings
from .document_schema import Document
from .logging_utils import elapsed_ms, error_fields, log_event, log_exception, new_trace_id
from .observability import wrap_openai

logger = logging.getLogger(__name__)


def _llm_invoke_start(scene: str, model: str, request_hint: str = "") -> tuple[str, float]:
    trace_id = new_trace_id("llm")
    log_event(
        logger,
        logging.INFO,
        "llm.invoke.start",
        component="llm",
        trace_id=trace_id,
        scene=scene,
        model=model,
        request_hint=request_hint,
    )
    return trace_id, time.perf_counter()


def _llm_invoke_done(scene: str, model: str, trace_id: str, started_at: float, response_obj: Any) -> None:
    choices = _get_field(response_obj, "choices", []) or []
    log_event(
        logger,
        logging.INFO,
        "llm.invoke.done",
        component="llm",
        trace_id=trace_id,
        scene=scene,
        model=model,
        elapsed_ms=elapsed_ms(started_at),
        choices=len(choices),
    )


def _llm_invoke_failed(
    scene: str,
    model: str,
    trace_id: str,
    exc: Exception,
    started_at: float | None = None,
    with_stack: bool = True,
) -> None:
    fields: Dict[str, Any] = {
        "component": "llm",
        "trace_id": trace_id,
        "scene": scene,
        "model": model,
    }
    if started_at is not None:
        fields["elapsed_ms"] = elapsed_ms(started_at)
    fields.update(error_fields(exc))
    if with_stack:
        log_exception(logger, "llm.invoke.fail", **fields)
    else:
        log_event(logger, logging.ERROR, "llm.invoke.fail", **fields)


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _set_field(obj: Any, field: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[field] = value
    else:
        setattr(obj, field, value)


def _truncate_text(text: str, max_chars: int) -> str:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _rough_messages_tokens(messages: List[Dict[str, str]]) -> int:
    total = 0
    for item in messages:
        total += 6
        total += len(str(item.get("content", "") or ""))
    return total + 2


def _prepare_messages_for_small_context(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    max_messages = max(2, int(os.getenv("CHAT_MAX_MESSAGES", "12")))
    max_input_tokens = max(256, int(os.getenv("CHAT_MAX_INPUT_TOKENS", "1700")))
    max_system_chars = max(256, int(os.getenv("CHAT_MAX_SYSTEM_CHARS", "1200")))
    max_message_chars = max(128, int(os.getenv("CHAT_MAX_MESSAGE_CHARS", "800")))

    normalized: List[Dict[str, str]] = []
    for raw in messages:
        role = str(_get_field(raw, "role", "user") or "user").strip() or "user"
        content = str(_get_field(raw, "content", "") or "")
        limit = max_system_chars if role == "system" else max_message_chars
        normalized.append({"role": role, "content": _truncate_text(content, limit)})

    if len(normalized) > max_messages:
        if normalized and normalized[0].get("role") == "system":
            normalized = [normalized[0]] + normalized[-(max_messages - 1):]
        else:
            normalized = normalized[-max_messages:]

    while len(normalized) > 1 and _rough_messages_tokens(normalized) > max_input_tokens:
        if normalized[0].get("role") == "system" and len(normalized) > 2:
            normalized.pop(1)
        else:
            normalized.pop(0)

    if normalized and _rough_messages_tokens(normalized) > max_input_tokens:
        target_idx = 1 if normalized[0].get("role") == "system" and len(normalized) > 1 else 0
        current = normalized[target_idx]["content"]
        overflow = _rough_messages_tokens(normalized) - max_input_tokens
        keep_chars = max(32, len(current) - overflow - 16)
        normalized[target_idx]["content"] = _truncate_text(current, keep_chars)

    return normalized


def _merge_doc_ids(existing: List[str], extra: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for value in list(existing or []) + list(extra or []):
        doc_id = str(value or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        merged.append(doc_id)
    return merged


def _extract_doc_id(doc: Document, fallback_prefix: str, index: int) -> str:
    metadata = _get_field(doc, "metadata", {}) or {}
    doc_id = str(_get_field(metadata, "doc_id", "") or "").strip()
    if doc_id:
        return doc_id
    doc_url = str(_get_field(metadata, "url", "") or "").strip()
    if doc_url:
        return doc_url
    return f"{fallback_prefix}_{index}"


def _build_retrieval_record(
    *,
    subquery_id: str,
    subquery: str,
    docs: List[Document],
) -> Dict[str, Any]:
    retrieved_doc_ids: List[str] = []
    retrieved_contexts: List[str] = []
    channel_counts: Dict[str, int] = {}
    for index, doc in enumerate(docs, start=1):
        doc_id = _extract_doc_id(doc, fallback_prefix=subquery_id, index=index)
        if doc_id not in retrieved_doc_ids:
            retrieved_doc_ids.append(doc_id)

        content = str(_get_field(doc, "content", "") or "").strip().replace("\n", " ")
        if content:
            retrieved_contexts.append(content[:280] + "..." if len(content) > 280 else content)

        metadata = _get_field(doc, "metadata", {}) or {}
        channel = str(
            _get_field(metadata, "channel", "")
            or _get_field(metadata, "retrieval_source", "")
            or "unknown"
        ).strip().lower()
        if channel:
            channel_counts[channel] = channel_counts.get(channel, 0) + 1

    return {
        "subquery_id": subquery_id,
        "subquery": str(subquery or ""),
        "retrieved_doc_ids": retrieved_doc_ids,
        "retrieved_contexts": retrieved_contexts[:8],
        "channel_counts": channel_counts,
        "channels_used": sorted(channel_counts.keys()),
    }


def _get_openai_client() -> openai.OpenAI:
    api_key = settings.DASHSCOPE_API_KEY or settings.OPENAI_API_KEY
    base_url = settings.OPENAI_BASE_URL or None
    return wrap_openai(openai.OpenAI(api_key=api_key, base_url=base_url))


def _safe_get_stream_writer() -> Callable[[Dict[str, Any]], None] | None:
    try:
        writer = get_stream_writer()
    except Exception:
        return None
    return writer


def _extract_stream_delta_text(chunk: Any) -> str:
    choices = _get_field(chunk, "choices", []) or []
    if not choices:
        return ""

    first_choice = choices[0]
    delta = _get_field(first_choice, "delta", None)
    if delta is None and isinstance(first_choice, dict):
        delta = first_choice.get("delta")

    content = _get_field(delta, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            text = _get_field(item, "text", "") if isinstance(item, dict) else ""
            if not text and isinstance(item, dict):
                text = _get_field(item, "content", "")
            if text:
                parts.append(str(text))
        return "".join(parts)
    return ""


def _stream_chat_completion_text(
    *,
    scene: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    stream_node: str,
    max_tokens: int | None = None,
) -> str:
    client = _get_openai_client()
    writer = _safe_get_stream_writer()
    trace_id, started_at = _llm_invoke_start(
        scene=scene,
        model=model,
        request_hint=f"messages={len(messages)}",
    )
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    try:
        stream = client.chat.completions.create(**kwargs)
        parts: List[str] = []
        for chunk in stream:
            delta = _extract_stream_delta_text(chunk)
            if not delta:
                continue
            parts.append(delta)
            if writer is not None:
                writer({"type": "token", "node": stream_node, "delta": delta})

        response_text = "".join(parts).strip()
        if not response_text:
            kwargs["stream"] = False
            fallback = client.chat.completions.create(**kwargs)
            if hasattr(fallback, "choices") and fallback.choices:
                first_choice = fallback.choices[0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message") or {}
                    response_text = str(message.get("content") or "").strip()
                else:
                    message = getattr(first_choice, "message", None)
                    response_text = str(
                        getattr(message, "content", "") if message else ""
                    ).strip()

        _llm_invoke_done(scene, model, trace_id, started_at, {"choices": [1]})
        return response_text
    except Exception as exc:
        _llm_invoke_failed(
            scene=scene, model=model, trace_id=trace_id, exc=exc,
            started_at=started_at, with_stack=True,
        )
        raise
