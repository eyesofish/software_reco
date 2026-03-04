import json
import logging
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
    return {
        "status": "error",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {"event": event}
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger.log(level, _serialize_payload(payload))


def log_exception(
    logger: logging.Logger,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {"event": event}
    payload.update({key: value for key, value in fields.items() if value is not None})
    logger.exception(_serialize_payload(payload))
