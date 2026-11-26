"""Small, typed TypeSafe transport. No graph, persistence, retries, or tool calls."""

from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
from typing import Any, cast

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class ProviderFailure(Exception):
    """A transport/configuration failure, not a low-confidence model answer."""


def _rate(name: str) -> float | None:
    raw = getattr(settings, name, None)
    if raw is None:
        return None
    value = float(raw)
    return value if math.isfinite(value) and value >= 0 else None


def usage_record(
    response: dict[str, Any],
    *,
    provider: str,
    model: str,
    request_id: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    usage = response.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    prefix = "JEV" if provider == "typesafe" else "ROUTING_REVIEW"
    input_rate = _rate(prefix + "_INPUT_COST_PER_MILLION_USD")
    output_rate = _rate(prefix + "_OUTPUT_COST_PER_MILLION_USD")
    valid_usage = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (input_tokens, output_tokens))
    cost = None
    if valid_usage and input_rate is not None and output_rate is not None:
        cost = (cast(int, input_tokens) * input_rate + cast(int, output_tokens) * output_rate) / 1_000_000
    record = {
        "event": "routing.usage",
        "request_id": request_id,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens if valid_usage else None,
        "output_tokens": output_tokens if valid_usage else None,
        "cost_usd": cost,
        "cost_known": cost is not None,
        "elapsed_ms": elapsed_ms,
        "price_version": {"input_per_million_usd": input_rate, "output_per_million_usd": output_rate},
    }
    logger.info(json.dumps(record, ensure_ascii=False))
    return record


class JevProvider:
    """Each instance owns its circuit; shadow must use a separate instance."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport
        self._open_until = 0.0
        self._lock = threading.Lock()

    def decide(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        timeout_seconds: float,
        request_id: str = "",
    ) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise ProviderFailure("deadline_exhausted")
        with self._lock:
            if time.monotonic() < self._open_until:
                raise ProviderFailure("circuit_open")
        key = str(getattr(settings, "JEV_API_KEY", "") or "").strip()
        if not key:
            raise ProviderFailure("missing_api_key")
        model = str(getattr(settings, "JEV_MODEL", "jev-1.14.0"))
        base_url = str(getattr(settings, "JEV_BASE_URL", "https://api.typesafe.ai")).rstrip("/")
        url = base_url if base_url.endswith("/v1/systemone") else base_url + "/v1/systemone"
        started = time.monotonic()
        try:
            # A fresh client has no SDK retry machinery, shared mutable state, or tracing of prompts.
            with httpx.Client(timeout=timeout_seconds, transport=self.transport, follow_redirects=False) as client:
                response = client.post(
                    url,
                    headers={"Authorization": "Bearer " + key},
                    json={"model": model, "state": copy.deepcopy(state), "questions": copy.deepcopy(questions)},
                )
                response.raise_for_status()
                result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
                raise ProviderFailure("invalid_response")
        except (httpx.HTTPError, ValueError, ProviderFailure) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(exc, httpx.TransportError) or status in {429, 529} or (status and status >= 500):
                with self._lock:
                    self._open_until = time.monotonic() + max(0, float(getattr(settings, "JEV_CIRCUIT_SECONDS", 30)))
            # Do not propagate remote bodies, headers, or inputs into logs or user responses.
            reason = f"http_{status}" if status else type(exc).__name__
            raise ProviderFailure(reason) from None
        with self._lock:
            self._open_until = 0.0
        elapsed = int((time.monotonic() - started) * 1000)
        result["_routing_usage"] = usage_record(
            result,
            provider="typesafe",
            model=str(result.get("model") or model),
            request_id=request_id,
            elapsed_ms=elapsed,
        )
        return result


LIVE_PROVIDER = JevProvider()
