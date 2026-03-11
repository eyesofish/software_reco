from __future__ import annotations

import re
from typing import Dict, List

from pydantic import BaseModel, Field

from .config import settings
from .skill_registry import DEFAULT_SKILL_ID, get_skill


class PlanStep(BaseModel):
    step_id: str
    objective: str
    action: str
    query: str = ""
    retrieval_profile: str = "balanced"
    required: bool = True


class ExecutionPlan(BaseModel):
    skill_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    stop_condition: str = "enough_evidence"
    planner_reason: str = ""


def _compact(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _query_base(user_query: str, normalized_query: str) -> str:
    normalized = _compact(normalized_query)
    if normalized:
        return normalized
    return _compact(user_query)


def _truncate_query(text: str, max_chars: int) -> str:
    value = _compact(text)
    if len(value) <= max_chars:
        return value
    truncated = value[:max_chars].strip()
    last_space = truncated.rfind(" ")
    if last_space >= 40:
        truncated = truncated[:last_space]
    return truncated


def _is_factoid_like_query(query: str) -> bool:
    text = _compact(query).lower()
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


def _step(step_id: str, objective: str, action: str, query: str, profile: str, required: bool = True) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        objective=_compact(objective),
        action=_compact(action),
        query=_compact(query),
        retrieval_profile=_compact(profile) or "balanced",
        required=required,
    )


def _compare_template(base_query: str, profile: str) -> list[PlanStep]:
    return [
        _step(
            "s1_candidates",
            "Identify top candidate tools and alternatives",
            "retrieve",
            f"{base_query} candidate options and alternatives",
            profile,
        ),
        _step(
            "s2_tradeoff",
            "Collect performance, reliability, and cost tradeoffs",
            "retrieve",
            f"{base_query} performance reliability cost tradeoff",
            profile,
        ),
        _step(
            "s3_constraints",
            "Collect integration constraints and migration risks",
            "retrieve",
            f"{base_query} integration constraints migration risk",
            profile,
        ),
    ]


def _architecture_template(base_query: str, profile: str) -> list[PlanStep]:
    return [
        _step(
            "s1_context",
            "Clarify workload and system constraints",
            "retrieve",
            f"{base_query} workload constraints scale latency",
            profile,
        ),
        _step(
            "s2_components",
            "Find architecture patterns and component choices",
            "retrieve",
            f"{base_query} architecture pattern component design",
            profile,
        ),
        _step(
            "s3_reliability",
            "Collect high availability and failure handling practices",
            "retrieve",
            f"{base_query} high availability failure recovery best practice",
            profile,
        ),
    ]


def _implementation_template(base_query: str, profile: str) -> list[PlanStep]:
    return [
        _step(
            "s1_prereq",
            "Collect implementation prerequisites and dependencies",
            "retrieve",
            f"{base_query} prerequisites dependency checklist",
            profile,
        ),
        _step(
            "s2_steps",
            "Collect implementation sequence and key decisions",
            "retrieve",
            f"{base_query} implementation steps design decisions",
            profile,
        ),
        _step(
            "s3_validation",
            "Collect validation and rollout strategy",
            "retrieve",
            f"{base_query} testing rollout validation strategy",
            profile,
        ),
    ]


def _qa_template(base_query: str, profile: str) -> list[PlanStep]:
    max_chars = max(80, int(getattr(settings, "QA_PLAN_QUERY_MAX_CHARS", 220)))
    concise_query = _truncate_query(base_query, max_chars=max_chars)
    return [
        _step(
            "s1_direct_answer",
            "Retrieve direct answer span from authoritative docs",
            "retrieve",
            concise_query,
            profile,
        )
    ]


def _default_template(base_query: str, profile: str) -> list[PlanStep]:
    return [
        _step(
            "s1_context",
            "Retrieve context and requirements",
            "retrieve",
            f"{base_query} requirements constraints",
            profile,
        ),
        _step(
            "s2_solution",
            "Retrieve solution options and recommendations",
            "retrieve",
            f"{base_query} solution options recommendation",
            profile,
        ),
    ]


def _template_steps(template_name: str, base_query: str, profile: str) -> list[PlanStep]:
    template_map = {
        "compare_template": _compare_template,
        "architecture_template": _architecture_template,
        "implementation_template": _implementation_template,
        "qa_template": _qa_template,
        "default": _default_template,
    }
    builder = template_map.get(template_name, _default_template)
    return builder(base_query, profile)


def build_execution_plan(
    *,
    selected_skill: str,
    user_query: str,
    normalized_query: str = "",
    constraints: Dict[str, str] | None = None,
) -> ExecutionPlan:
    skill = get_skill(selected_skill or DEFAULT_SKILL_ID)
    base_query = _query_base(user_query=user_query, normalized_query=normalized_query)
    profile = skill.retrieval_profile or "balanced"
    template_name = skill.planner_template
    if template_name == "default" and _is_factoid_like_query(base_query):
        template_name = "qa_template"
        if profile == "balanced":
            profile = "fast"

    steps = _template_steps(template_name, base_query=base_query, profile=profile)

    max_steps = max(1, int(getattr(settings, "PLANNER_MAX_STEPS", 4)))
    steps = steps[:max_steps]

    constraints = constraints or {}
    constraints_hint = ", ".join(f"{k}={v}" for k, v in constraints.items() if str(v or "").strip())
    reason = f"template:{template_name}"
    if constraints_hint:
        reason = f"{reason}|constraints:{constraints_hint}"

    return ExecutionPlan(
        skill_id=skill.skill_id,
        steps=steps,
        stop_condition="enough_evidence_or_max_iterations",
        planner_reason=reason,
    )
