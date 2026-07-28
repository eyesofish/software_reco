from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import openai

from .config import settings
from .observability import wrap_openai
from .skill_registry import DEFAULT_SKILL_ID, SkillSpec, get_skill, list_skills, validate_skill_id

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> set[str]:
    normalized = str(text or "").lower()
    latin_tokens = re.findall(r"[a-z0-9_]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    return set(latin_tokens + cjk_chars)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _is_factoid_like_query(query: str) -> bool:
    text = _normalize_text(query).lower()
    if not text:
        return False
    if "?" in text or "？" in text:
        return True

    en_prefixes = {
        "what",
        "how",
        "why",
        "when",
        "where",
        "which",
        "who",
        "can",
        "could",
        "should",
        "is",
        "are",
        "does",
        "do",
    }
    zh_prefixes = ("什么", "如何", "怎么", "为何", "为什么", "是否", "能否", "哪个", "哪种", "怎样")

    match = re.match(r"^\s*([a-z]+)", text)
    if match and match.group(1) in en_prefixes:
        return True
    return any(text.startswith(prefix) for prefix in zh_prefixes)


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("empty_skill_router_response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("skill_router_non_json_response") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("skill_router_response_not_object")
    return parsed


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


@dataclass(frozen=True)
class SkillRoutingResult:
    selected_skill: str
    confidence: float
    reason: str
    candidates: list[dict[str, Any]]
    fallback_used: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_skill": self.selected_skill,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidates": self.candidates,
            "fallback_used": self.fallback_used,
        }


def _get_router_client() -> openai.OpenAI:
    api_key = (
        _normalize_text(getattr(settings, "SKILL_ROUTER_API_KEY", ""))
        or _normalize_text(getattr(settings, "ROUTER_API_KEY", ""))
        or _normalize_text(getattr(settings, "OPENAI_API_KEY", ""))
        or _normalize_text(getattr(settings, "DASHSCOPE_API_KEY", ""))
        or "LOCAL_DUMMY_KEY"
    )
    base_url = _normalize_text(getattr(settings, "SKILL_ROUTER_BASE_URL", "")) or None
    timeout_seconds = max(1.0, float(getattr(settings, "SKILL_ROUTER_TIMEOUT_SECONDS", 8)))
    return wrap_openai(
        openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
    )


def _rule_score_skill(query: str, skill: SkillSpec) -> dict[str, Any]:
    query_text = _normalize_text(query).lower()
    query_tokens = _tokenize(query_text)

    matched_triggers: list[str] = []
    for trigger in skill.triggers:
        trigger_text = _normalize_text(trigger).lower()
        if not trigger_text:
            continue
        if re.search(r"[a-z0-9]", trigger_text):
            pattern = rf"(?<![a-z0-9]){re.escape(trigger_text)}(?![a-z0-9])"
            if re.search(pattern, query_text):
                matched_triggers.append(trigger)
                continue
        elif trigger_text in query_text:
            matched_triggers.append(trigger)
            continue

        trigger_tokens = _tokenize(trigger_text)
        if trigger_tokens and trigger_tokens <= query_tokens:
            matched_triggers.append(trigger)

    if not skill.triggers:
        score = 0.05
    else:
        coverage_score = len(matched_triggers) / max(len(skill.triggers), 1)
        hit_strength = min(1.0, len(matched_triggers) / 3.0)
        score = max(coverage_score, 0.8 * hit_strength)
        score = min(max(score, 0.0), 1.0)

    return {
        "skill_id": skill.skill_id,
        "score": round(score, 4),
        "matched_triggers": matched_triggers[:8],
        "retrieval_profile": skill.retrieval_profile,
    }


def _rule_route(
    user_query: str,
    normalized_query: str = "",
) -> SkillRoutingResult:
    query = " ".join(part for part in [user_query, normalized_query] if _normalize_text(part))
    all_skills = list_skills(include_default=False)
    candidates = [_rule_score_skill(query, skill) for skill in all_skills]

    if _is_factoid_like_query(query):
        fact_boost = max(
            0.0,
            min(1.0, float(getattr(settings, "SKILL_ROUTER_FACT_QUESTION_BOOST", 0.72))),
        )
        for candidate in candidates:
            if candidate.get("skill_id") != "quick_fact_qa":
                continue
            candidate["score"] = round(max(float(candidate.get("score", 0.0)), fact_boost), 4)
            matched = list(candidate.get("matched_triggers", []) or [])
            if "__factoid_heuristic__" not in matched:
                matched.append("__factoid_heuristic__")
            candidate["matched_triggers"] = matched[:8]
            break

    candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)

    threshold = max(0.0, min(1.0, float(getattr(settings, "SKILL_ROUTER_RULE_THRESHOLD", 0.25))))
    fallback_skill = (
        _normalize_text(getattr(settings, "SKILL_ROUTER_FALLBACK_SKILL", DEFAULT_SKILL_ID)) or DEFAULT_SKILL_ID
    )

    if candidates and float(candidates[0].get("score", 0.0)) >= threshold:
        top = candidates[0]
        selected_skill = top["skill_id"]
        confidence = min(1.0, max(0.0, float(top["score"])))
        reason = f"rule_match:{selected_skill}"
        fallback_used = False
    else:
        selected_skill = get_skill(fallback_skill).skill_id
        confidence = float(candidates[0]["score"]) if candidates else 0.0
        reason = f"rule_fallback:{selected_skill}"
        fallback_used = True

    # keep fallback skill visible in candidate list for observability.
    fallback_present = any(item.get("skill_id") == selected_skill for item in candidates)
    if not fallback_present:
        candidates.append(
            {
                "skill_id": selected_skill,
                "score": round(confidence, 4),
                "matched_triggers": [],
                "retrieval_profile": get_skill(selected_skill).retrieval_profile,
            }
        )

    return SkillRoutingResult(
        selected_skill=selected_skill,
        confidence=round(confidence, 4),
        reason=reason,
        candidates=candidates[:5],
        fallback_used=fallback_used,
    )


def _build_llm_messages(
    user_query: str,
    normalized_query: str,
    rule_candidates: list[dict[str, Any]],
) -> list[dict[str, str]]:
    allowed = [skill.skill_id for skill in list_skills(include_default=True)]
    payload = {
        "user_query": _normalize_text(user_query),
        "normalized_query": _normalize_text(normalized_query),
        "allowed_skills": allowed,
        "rule_candidates": rule_candidates[:3],
    }
    system_prompt = "\n".join(
        [
            "You are a strict skill router for software recommendation pipeline.",
            "Choose one selected_skill from allowed_skills.",
            "Return JSON only with keys: selected_skill, confidence, reason.",
            "confidence must be in [0,1].",
            "No markdown.",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _llm_route(
    user_query: str,
    normalized_query: str,
    rule_candidates: list[dict[str, Any]],
    *,
    client: Any | None = None,
) -> SkillRoutingResult | None:
    active_client = client or _get_router_client()
    model = _normalize_text(getattr(settings, "SKILL_ROUTER_MODEL", "")) or _normalize_text(
        getattr(settings, "ROUTER_MODEL", "")
    )
    confidence_threshold = max(
        0.0,
        min(1.0, float(getattr(settings, "SKILL_ROUTER_CONFIDENCE_THRESHOLD", 0.55))),
    )

    try:
        response = active_client.chat.completions.create(
            model=model,
            messages=_build_llm_messages(
                user_query=user_query,
                normalized_query=normalized_query,
                rule_candidates=rule_candidates,
            ),
            temperature=0,
            response_format={"type": "json_object"},
        )
        first_choice = (getattr(response, "choices", None) or [{}])[0]
        message = (
            first_choice.get("message", {})
            if isinstance(first_choice, dict)
            else getattr(first_choice, "message", {})
        )
        content = _extract_text_content(
            message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
        )
        parsed = _parse_json_object(content)
    except Exception as exc:
        logger.warning("skill router llm invocation failed: %s", exc)
        return None

    selected_skill = validate_skill_id(parsed.get("selected_skill", ""))
    confidence = float(parsed.get("confidence", 0.0)) if parsed.get("confidence") is not None else 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = _normalize_text(parsed.get("reason", "")) or "llm_skill_router"

    if not selected_skill:
        return None
    if confidence < confidence_threshold:
        return None

    merged_candidates = list(rule_candidates)
    existing = {str(item.get("skill_id", "")): item for item in merged_candidates}
    if selected_skill not in existing:
        merged_candidates.insert(
            0,
            {
                "skill_id": selected_skill,
                "score": round(confidence, 4),
                "matched_triggers": [],
                "retrieval_profile": get_skill(selected_skill).retrieval_profile,
            },
        )

    return SkillRoutingResult(
        selected_skill=selected_skill,
        confidence=round(confidence, 4),
        reason=reason,
        candidates=merged_candidates[:5],
        fallback_used=False,
    )


def route_skill(
    user_query: str,
    *,
    normalized_query: str = "",
    client: Any | None = None,
) -> SkillRoutingResult:
    if not bool(getattr(settings, "SKILL_ROUTER_ENABLE", True)):
        fallback_skill = (
            _normalize_text(getattr(settings, "SKILL_ROUTER_FALLBACK_SKILL", DEFAULT_SKILL_ID)) or DEFAULT_SKILL_ID
        )
        selected = get_skill(fallback_skill).skill_id
        return SkillRoutingResult(
            selected_skill=selected,
            confidence=0.0,
            reason="skill_router_disabled",
            candidates=[
                {
                    "skill_id": selected,
                    "score": 0.0,
                    "matched_triggers": [],
                    "retrieval_profile": get_skill(selected).retrieval_profile,
                }
            ],
            fallback_used=True,
        )

    rule_result = _rule_route(
        user_query=user_query,
        normalized_query=normalized_query,
    )
    use_llm = bool(getattr(settings, "SKILL_ROUTER_USE_LLM", False))
    if not use_llm:
        return rule_result
    if rule_result.confidence >= float(getattr(settings, "SKILL_ROUTER_CONFIDENCE_THRESHOLD", 0.55)):
        return rule_result

    llm_result = _llm_route(
        user_query=user_query,
        normalized_query=normalized_query,
        rule_candidates=rule_result.candidates,
        client=client,
    )
    if llm_result is not None:
        return llm_result

    return SkillRoutingResult(
        selected_skill=rule_result.selected_skill,
        confidence=rule_result.confidence,
        reason=f"{rule_result.reason}|llm_fallback",
        candidates=rule_result.candidates,
        fallback_used=True,
    )
