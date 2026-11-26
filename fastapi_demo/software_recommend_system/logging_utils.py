import json
import logging
import sys
import time
import uuid
from typing import Any


def new_trace_id(prefix: str = "trace") -> str:
    normalized_prefix = "".join(
        ch for ch in str(prefix or "trace").strip().lower()
        if ch.isalnum() or ch in {"-", "_"}
    ) or "trace"
    return f"{normalized_prefix}-{uuid.uuid4().hex[:12]}"


def text_preview(text: str, max_len: int = 120) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[:max_len]}..."


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def error_fields(exc: BaseException) -> dict[str, str]:
    fields = {"status": "error", "error_type": type(exc).__name__}
    tb = exc.__traceback__
    if tb is not None:
        while tb.tb_next is not None:
            tb = tb.tb_next
        fields["error_at"] = f"{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}"
    return fields


def _serialize_payload(payload: dict[str, Any]) -> str:
    # Tool requests, model rationales, retrieved documents and remote error text can contain the full chat.
    private_fields = {"query", "request", "request_hint", "reason", "filename", "error", "top5", "top_urls"}
    metadata = {key: value for key, value in payload.items() if key not in private_fields}
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), default=str)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {"event": event}
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger.log(level, _serialize_payload(payload))
    from .performance import record_elapsed_event
    record_elapsed_event(event, fields)


def log_exception(
    logger: logging.Logger,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {"event": event}
    payload.update({key: value for key, value in fields.items() if value is not None})
    exc = sys.exception()
    if exc is not None:
        payload.update(error_fields(exc))
    logger.error(_serialize_payload(payload))
    from .performance import record_elapsed_event
    record_elapsed_event(event, fields)
