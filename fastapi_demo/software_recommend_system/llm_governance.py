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
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import openai

from .config import settings
from .execution_control import check_current_run
from .logging_utils import elapsed_ms, error_fields, log_event, new_trace_id
from .performance import current_correlation, write_record

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
    unknown_cost_calls: int = 0
    unknown_usage_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 9) if not self.unknown_cost_calls else None,
            "known_cost_usd": round(self.cost_usd, 9),
            "unknown_cost_calls": self.unknown_cost_calls,
            "unknown_usage_calls": self.unknown_usage_calls,
            "token_totals_complete": self.unknown_usage_calls == 0,
        }


@dataclass
class UsageLedger:
    """Process-wide token/cost accumulator.

    Guarded by a lock because LangGraph `@task` nodes and the recall fan-out can
    invoke the LLM from several threads at once.
    """

    overall: SceneUsage = field(default_factory=SceneUsage)
    by_scene: dict[str, SceneUsage] = field(default_factory=dict)
    records: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=10000))
    discarded_records: int = 0


_LEDGER = UsageLedger()
_LEDGER_LOCK = threading.Lock()


def reset_usage() -> None:
    """Clear diagnostics. Request reporting should filter by correlation, not reset globally."""
    with _LEDGER_LOCK:
        _LEDGER.overall = SceneUsage()
        _LEDGER.by_scene = {}
        _LEDGER.records.clear()
        _LEDGER.discarded_records = 0


def _accumulate(bucket: SceneUsage, record: dict[str, Any]) -> None:
    bucket.calls += 1
    bucket.retries += record["retries"]
    bucket.failed_calls += int(record["failed"])
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        setattr(bucket, key, getattr(bucket, key) + (record[key] or 0))
    bucket.unknown_usage_calls += int(not record["usage_complete"])
    bucket.unknown_cost_calls += int(record["cost_usd"] is None)
    bucket.cost_usd += record["known_cost_usd"] or 0.0


def usage_snapshot(
    *, request_id: str | None = None, run_id: str | None = None, session_id: str | None = None
) -> dict[str, Any]:
    filters = {
        key: value
        for key, value in {
            "request_id": request_id,
            "run_id": run_id,
            "session_id": session_id,
        }.items()
        if value is not None
    }
    with _LEDGER_LOCK:
        records = [
            dict(record)
            for record in _LEDGER.records
            if all(record.get(key) == value for key, value in filters.items())
        ]
        overall, scenes = _LEDGER.overall, _LEDGER.by_scene
        if filters:
            overall, scenes = SceneUsage(), {}
            for record in records:
                _accumulate(overall, record)
                _accumulate(scenes.setdefault(record["scene"], SceneUsage()), record)
        totals = overall.as_dict()
        if filters and _LEDGER.discarded_records:
            totals["cost_usd"] = None
            totals["token_totals_complete"] = False
        return {
            "overall": totals,
            "by_scene": {scene: usage.as_dict() for scene, usage in sorted(scenes.items())},
            "records": records,
            "records_truncated": _LEDGER.discarded_records > 0,
        }


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _estimate_cost(prompt_tokens: int | None, completion_tokens: int | None, model: str = "") -> float | None:
    """Missing prices or actual usage are unknown; explicitly configured zero is valid."""
    if prompt_tokens is None or completion_tokens is None:
        return None
    rates = getattr(settings, "LLM_COST_MODEL_RATES", {}).get(model)
    if rates is not None:
        prompt_rate = _price(rates.get("prompt_per_1k_usd"))
        completion_rate = _price(rates.get("completion_per_1k_usd"))
    else:
        prompt_rate = _price(getattr(settings, "LLM_COST_PROMPT_PER_1K_USD", None))
        completion_rate = _price(getattr(settings, "LLM_COST_COMPLETION_PER_1K_USD", None))
    if prompt_rate is None or completion_rate is None:
        return None
    return (prompt_tokens / 1000) * prompt_rate + (completion_tokens / 1000) * completion_rate


def _extract_usage(response: Any) -> tuple[int | None, int | None, int | None]:
    usage = _get_field(response, "usage", None)
    prompt = _get_field(usage, "prompt_tokens", None)
    completion = _get_field(usage, "completion_tokens", None)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (prompt, completion)):
        return None, None, None
    total = _get_field(usage, "total_tokens", None)
    if total is None:
        total = prompt + completion
    if isinstance(total, bool) or not isinstance(total, int) or total < prompt + completion:
        return None, None, None
    return prompt, completion, total


def record_usage(
    *,
    scene: str,
    model: str = "",
    call_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cost_usd: float | None = None,
    retries: int = 0,
    failed: bool = False,
    elapsed_ms: int | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    usage_complete = all(value is not None for value in (prompt_tokens, completion_tokens, total_tokens))
    # Unobserved attempts and partial streams may have incurred charges too.
    complete = usage_complete and not failed and retries == 0
    correlation = current_correlation()
    if request_id and not correlation["request_id"]:
        correlation["request_id"] = request_id
    record = {
        "kind": "llm_usage",
        "call_id": call_id or new_trace_id("llm"),
        "scene": scene,
        "model": model,
        **correlation,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd if complete else None,
        "known_cost_usd": cost_usd,
        "usage_complete": complete,
        "retries": retries,
        "failed": failed,
        "elapsed_ms": elapsed_ms,
    }
    with _LEDGER_LOCK:
        _accumulate(_LEDGER.overall, record)
        _accumulate(_LEDGER.by_scene.setdefault(scene, SceneUsage()), record)
        if len(_LEDGER.records) == _LEDGER.records.maxlen:
            _LEDGER.discarded_records += 1
        _LEDGER.records.append(record)
    write_record(record)
    return dict(record)


def record_response_usage(
    *,
    scene: str,
    model: str,
    response: Any,
    call_id: str | None = None,
    retries: int = 0,
    failed: bool = False,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    prompt, completion, total = _extract_usage(response)
    return record_usage(
        scene=scene,
        model=model,
        call_id=call_id,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cost_usd=_estimate_cost(prompt, completion, model),
        retries=retries,
        failed=failed,
        elapsed_ms=elapsed_ms,
    )


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


def accounted_completion(*, create: Any, scene: str, model: str, **kwargs: Any) -> Any:
    """Account for exactly one existing client call, without changing retry/timeout policy."""
    check_current_run()
    call_id = new_trace_id("llm")
    started = time.perf_counter()
    try:
        response = create(model=model, **kwargs)
    except Exception:
        record_usage(scene=scene, model=model, call_id=call_id, failed=True, elapsed_ms=elapsed_ms(started))
        raise
    record_response_usage(scene=scene, model=model, response=response, call_id=call_id, elapsed_ms=elapsed_ms(started))
    return response


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
        check_current_run()
        try:
            response = client.chat.completions.create(model=model, **create_kwargs)
        except Exception as exc:  # noqa: BLE001  boundary: classified below
            last_exc = exc
            retryable = is_retryable_error(exc)
            has_attempts_left = attempt < attempts
            if not (retryable and has_attempts_left):
                record_usage(
                    scene=scene,
                    model=model,
                    call_id=trace_id,
                    retries=retries_used,
                    failed=True,
                    elapsed_ms=elapsed_ms(started_at),
                )
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
            try:
                if delay > 0:
                    sleep(delay)
                check_current_run()
            except BaseException:
                record_usage(
                    scene=scene,
                    model=model,
                    call_id=trace_id,
                    retries=retries_used,
                    failed=True,
                    elapsed_ms=elapsed_ms(started_at),
                )
                raise
            continue

        usage = record_response_usage(
            scene=scene,
            model=model,
            response=response,
            call_id=trace_id,
            retries=retries_used,
            elapsed_ms=elapsed_ms(started_at),
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
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            cost_usd=usage["cost_usd"],
        )
        return response

    # Defensive: the loop either returns or raises.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"governed_chat_completion exhausted attempts for scene={scene!r}")
