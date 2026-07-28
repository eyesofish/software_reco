from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import openai

from .config import settings
from .observability import wrap_openai

logger = logging.getLogger(__name__)

VALID_ROUTER_MODES = frozenset({"rag", "direct", "hitl"})
_ROUTER_CIRCUIT_OPEN_UNTIL: float = 0.0


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _normalize_mode(raw_mode: Any, default: str = "rag") -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in VALID_ROUTER_MODES:
        return normalized
    return default


def _clamp_confidence(raw_value: Any) -> float:
    try:
        confidence = float(raw_value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, confidence))


def _extract_text_content(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return raw_content.strip()

    if isinstance(raw_content, list):
        fragments: list[str] = []
        for item in raw_content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
            else:
                text = getattr(item, "text", None) or getattr(item, "content", "") or ""
            if text:
                fragments.append(str(text))
        return "\n".join(fragments).strip()

    return str(raw_content or "").strip()


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("empty_router_response")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("router_non_json_response") from None
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError("router_response_not_object")
    return parsed


def _build_router_messages(user_query: str, context_hint: str | None) -> list[dict[str, str]]:
    payload = {
        "user_query": str(user_query or ""),
        "context_hint": str(context_hint or ""),
        "allowed_modes": ["rag", "direct", "hitl"],
    }
    system_prompt = "\n".join(
        [
            "You are a strict routing classifier for a software QA pipeline.",
            "Classify each user query into one mode:",
            "- rag: needs external retrieval/evidence to answer reliably.",
            "- direct: can be answered directly without retrieval.",
            "- hitl: ambiguous/high-risk query requiring human review.",
            "Return JSON object only with keys: mode, confidence, reason.",
            "confidence must be in [0,1].",
            "Do not output markdown.",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _build_router_prompt(user_query: str, context_hint: str | None) -> str:
    payload = {
        "user_query": str(user_query or ""),
        "context_hint": str(context_hint or ""),
        "allowed_modes": ["rag", "direct", "hitl"],
    }
    return "\n".join(
        [
            "You are a strict routing classifier for a software QA pipeline.",
            "Return JSON object only with keys: mode, confidence, reason.",
            "mode must be one of: rag, direct, hitl.",
            "confidence must be in [0,1].",
            "Input:",
            json.dumps(payload, ensure_ascii=False),
            "Output JSON:",
        ]
    )


def _get_router_client() -> openai.OpenAI:
    api_key = (
        str(settings.ROUTER_API_KEY or "").strip()
        or str(settings.OPENAI_API_KEY or "").strip()
        or str(settings.DASHSCOPE_API_KEY or "").strip()
        or "LOCAL_DUMMY_KEY"
    )
    base_url = str(settings.ROUTER_BASE_URL or "").strip() or None
    timeout_seconds = max(1.0, float(settings.ROUTER_TIMEOUT_SECONDS))
    return wrap_openai(
        openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
    )


def _router_circuit_ttl_seconds() -> float:
    try:
        ttl = float(getattr(settings, "ROUTER_CIRCUIT_BREAKER_SECONDS", 30))
    except Exception:
        ttl = 30.0
    return max(0.0, ttl)


def _is_router_circuit_open() -> bool:
    return time.time() < _ROUTER_CIRCUIT_OPEN_UNTIL


def _open_router_circuit() -> None:
    global _ROUTER_CIRCUIT_OPEN_UNTIL
    ttl = _router_circuit_ttl_seconds()
    if ttl <= 0:
        _ROUTER_CIRCUIT_OPEN_UNTIL = 0.0
        return
    _ROUTER_CIRCUIT_OPEN_UNTIL = time.time() + ttl


def _close_router_circuit() -> None:
    global _ROUTER_CIRCUIT_OPEN_UNTIL
    _ROUTER_CIRCUIT_OPEN_UNTIL = 0.0


def _is_transient_connection_error(exc: Exception) -> bool:
    error_name = type(exc).__name__.lower()
    message = str(exc or "").lower()
    return (
        "timeout" in error_name
        or "connection" in error_name
        or "connection error" in message
        or "timed out" in message
    )


@dataclass(frozen=True)
class RoutingDecision:
    mode: str
    confidence: float
    reason: str
    fallback_used: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "confidence": self.confidence,
            "reason": self.reason,
            "fallback_used": self.fallback_used,
        }


def route_query(
    user_query: str,
    *,
    context_hint: str | None = None,
    client: Any | None = None,
) -> RoutingDecision:
    fallback_mode = _normalize_mode(settings.ROUTER_FALLBACK_MODE, default="rag")
    threshold = _clamp_confidence(settings.ROUTER_CONFIDENCE_THRESHOLD)
    normalized_query = str(user_query or "").strip()

    if not normalized_query:
        return RoutingDecision(
            mode=fallback_mode,
            confidence=0.0,
            reason="empty_query_fallback",
            fallback_used=True,
        )

    # Only apply circuit breaker on default remote client path.
    if client is None and _is_router_circuit_open():
        return RoutingDecision(
            mode=fallback_mode,
            confidence=0.0,
            reason="router_circuit_open",
            fallback_used=True,
        )

    active_client = client or _get_router_client()
    try:
        try:
            response = active_client.chat.completions.create(
                model=settings.ROUTER_MODEL,
                messages=_build_router_messages(normalized_query, context_hint=context_hint),
                temperature=0,
                response_format={"type": "json_object"},
            )
            first_choice = (_get_field(response, "choices", []) or [{}])[0]
            message = _get_field(first_choice, "message", {}) or {}
            content = _extract_text_content(_get_field(message, "content", ""))
        except Exception as chat_exc:
            if "chat template" not in str(chat_exc).lower():
                raise
            response = active_client.completions.create(
                model=settings.ROUTER_MODEL,
                prompt=_build_router_prompt(normalized_query, context_hint=context_hint),
                temperature=0,
                max_tokens=180,
            )
            first_choice = (_get_field(response, "choices", []) or [{}])[0]
            content = _extract_text_content(_get_field(first_choice, "text", ""))
        parsed = _parse_json_object(content)

        parsed_mode = _normalize_mode(parsed.get("mode"), default="")
        if not parsed_mode:
            raise ValueError("router_invalid_mode")

        confidence = _clamp_confidence(parsed.get("confidence", 0.0))
        reason = str(parsed.get("reason", "") or "").strip() or "router_model_decision"

        if confidence < threshold:
            return RoutingDecision(
                mode=fallback_mode,
                confidence=confidence,
                reason=f"below_threshold:{confidence:.3f}",
                fallback_used=True,
            )

        if client is None:
            _close_router_circuit()
        return RoutingDecision(
            mode=parsed_mode,
            confidence=confidence,
            reason=reason,
            fallback_used=False,
        )
    except Exception as exc:
        if client is None and _is_transient_connection_error(exc):
            _open_router_circuit()
        logger.warning("router invocation failed: %s", exc)
        return RoutingDecision(
            mode=fallback_mode,
            confidence=0.0,
            reason=f"router_fallback:{type(exc).__name__}",
            fallback_used=True,
        )
