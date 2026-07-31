"""Timeout, retry and token-cost governance for LLM calls.

Every non-streaming chat completion should go through :func:`governed_chat_completion`
so that one place owns:

* a bounded per-request timeout,
* exponential backoff with jitter on *transient* failures only,
* token usage and cost accounting.

Streaming calls deliberately do not retry: tokens are forwarded to the client as
they arrive, so a mid-stream retry would emit a duplicated or spliced answer.
They still inherit the timeout via the shared client factory.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import openai

from .config import settings
from .logging_utils import elapsed_ms, error_fields, log_event, new_trace_id

logger = logging.getLogger(__name__)

# Transport hiccups, rate limits and upstream 5xx are worth another attempt.
# BadRequest / Authentication / NotFound are deliberately excluded: replaying a
# malformed or unauthorized request cannot succeed and only burns the budget.
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_field(obj: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


@dataclass
class SceneUsage:
    calls: int = 0
    failed_calls: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class UsageLedger:
    """Process-wide token/cost accumulator.

    Guarded by a lock because LangGraph `@task` nodes and the recall fan-out can
    invoke the LLM from several threads at once.
    """

    overall: SceneUsage = field(default_factory=SceneUsage)
    by_scene: dict[str, SceneUsage] = field(default_factory=dict)


_LEDGER = UsageLedger()
_LEDGER_LOCK = threading.Lock()


def reset_usage() -> None:
    """Clear the ledger. Intended for tests and per-process reporting windows."""
    with _LEDGER_LOCK:
        _LEDGER.overall = SceneUsage()
        _LEDGER.by_scene = {}


def usage_snapshot() -> dict[str, Any]:
    with _LEDGER_LOCK:
        return {
            "overall": _LEDGER.overall.as_dict(),
            "by_scene": {scene: usage.as_dict() for scene, usage in sorted(_LEDGER.by_scene.items())},
        }


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Cost in USD from configured per-1K rates.

    Both rates default to 0.0: without real pricing configured we report 0
    rather than inventing a number.
    """
    prompt_rate = _safe_float(getattr(settings, "LLM_COST_PROMPT_PER_1K_USD", 0.0), 0.0)
    completion_rate = _safe_float(getattr(settings, "LLM_COST_COMPLETION_PER_1K_USD", 0.0), 0.0)
    return (prompt_tokens / 1000.0) * prompt_rate + (completion_tokens / 1000.0) * completion_rate


def _extract_usage(response: Any) -> tuple[int, int, int]:
    usage = _get_field(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    prompt_tokens = _safe_int(_get_field(usage, "prompt_tokens", 0))
    completion_tokens = _safe_int(_get_field(usage, "completion_tokens", 0))
    total_tokens = _safe_int(_get_field(usage, "total_tokens", 0))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def record_usage(
    *,
    scene: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float = 0.0,
    retries: int = 0,
    failed: bool = False,
) -> None:
    with _LEDGER_LOCK:
        buckets = (_LEDGER.overall, _LEDGER.by_scene.setdefault(scene, SceneUsage()))
        for bucket in buckets:
            bucket.calls += 1
            bucket.retries += retries
            bucket.prompt_tokens += prompt_tokens
            bucket.completion_tokens += completion_tokens
            bucket.total_tokens += total_tokens
            bucket.cost_usd += cost_usd
            if failed:
                bucket.failed_calls += 1


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    return _safe_int(code, 0) or None


def is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    code = _status_code(exc)
    return code in _RETRYABLE_STATUS_CODES if code is not None else False


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Honor a server-provided Retry-After header when it is present and sane."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    value = _safe_float(raw, default=-1.0)
    if value < 0:
        return None
    return min(value, _max_backoff_seconds())


def _max_backoff_seconds() -> float:
    return max(0.0, _safe_float(getattr(settings, "LLM_RETRY_BACKOFF_MAX_SECONDS", 8.0), 8.0))


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter.

    ``attempt`` is 1-based. Jitter spreads retries so concurrent nodes hitting the
    same rate limit do not all wake at the same instant.
    """
    base = max(0.0, _safe_float(getattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0.5), 0.5))
    if base <= 0:
        return 0.0
    window = min(base * (2 ** max(0, attempt - 1)), _max_backoff_seconds())
    return random.uniform(0.0, window)  # noqa: S311  not security-sensitive


def _max_attempts() -> int:
    retries = _safe_int(getattr(settings, "LLM_MAX_RETRIES", 2), 2)
    return max(1, retries + 1)


def request_timeout_seconds() -> float:
    return max(1.0, _safe_float(getattr(settings, "LLM_TIMEOUT_SECONDS", 60.0), 60.0))


def governed_chat_completion(
    *,
    client: Any,
    scene: str,
    model: str,
    sleep: Any = time.sleep,
    **create_kwargs: Any,
) -> Any:
    """Run a non-streaming chat completion under timeout, retry and accounting.

    Raises the last exception when every attempt fails, so callers keep their
    existing failure handling.
    """
    attempts = _max_attempts()
    trace_id = new_trace_id("llm")
    started_at = time.perf_counter()
    retries_used = 0
    last_exc: BaseException | None = None

    log_event(
        logger,
        logging.INFO,
        "llm.invoke.start",
        component="llm",
        trace_id=trace_id,
        scene=scene,
        model=model,
        max_attempts=attempts,
        timeout_seconds=request_timeout_seconds(),
    )

    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(model=model, **create_kwargs)
        except Exception as exc:  # noqa: BLE001  boundary: classified below
            last_exc = exc
            retryable = is_retryable_error(exc)
            has_attempts_left = attempt < attempts
            if not (retryable and has_attempts_left):
                record_usage(scene=scene, retries=retries_used, failed=True)
                log_event(
                    logger,
                    logging.ERROR,
                    "llm.invoke.fail",
                    component="llm",
                    trace_id=trace_id,
                    scene=scene,
                    model=model,
                    attempt=attempt,
                    attempts=attempts,
                    retryable=retryable,
                    elapsed_ms=elapsed_ms(started_at),
                    **error_fields(exc),
                )
                raise

            delay = _retry_after_seconds(exc)
            if delay is None:
                delay = _backoff_delay(attempt)
            retries_used += 1
            log_event(
                logger,
                logging.WARNING,
                "llm.invoke.retry",
                component="llm",
                trace_id=trace_id,
                scene=scene,
                model=model,
                attempt=attempt,
                attempts=attempts,
                delay_seconds=round(delay, 3),
                status_code=_status_code(exc),
                **error_fields(exc),
            )
            if delay > 0:
                sleep(delay)
            continue

        prompt_tokens, completion_tokens, total_tokens = _extract_usage(response)
        cost = _estimate_cost(prompt_tokens, completion_tokens)
        record_usage(
            scene=scene,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            retries=retries_used,
        )
        log_event(
            logger,
            logging.INFO,
            "llm.invoke.done",
            component="llm",
            trace_id=trace_id,
            scene=scene,
            model=model,
            attempt=attempt,
            retries=retries_used,
            elapsed_ms=elapsed_ms(started_at),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=round(cost, 6),
        )
        return response

    # Defensive: the loop either returns or raises.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"governed_chat_completion exhausted attempts for scene={scene!r}")
