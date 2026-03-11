from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SkillSpec(BaseModel):
    skill_id: str
    description: str
    triggers: list[str] = Field(default_factory=list)
    retrieval_profile: str = "balanced"
    planner_template: str = "default"
    risk_level: str = "normal"


DEFAULT_SKILL_ID = "generic_rag"

SKILLS: list[SkillSpec] = [
    SkillSpec(
        skill_id="software_compare",
        description="Software selection and tradeoff comparison",
        triggers=[
            "compare",
            "comparison",
            "vs",
            "versus",
            "tradeoff",
            "selection",
            "benchmark",
            "选型",
            "对比",
            "比较",
            "优缺点",
        ],
        retrieval_profile="deep",
        planner_template="compare_template",
    ),
    SkillSpec(
        skill_id="architecture_design",
        description="Architecture and system design",
        triggers=[
            "architecture",
            "system design",
            "high availability",
            "scalability",
            "distributed",
            "microservice",
            "部署架构",
            "架构",
            "高可用",
            "扩展性",
        ],
        retrieval_profile="deep",
        planner_template="architecture_template",
        risk_level="high",
    ),
    SkillSpec(
        skill_id="implementation_guidance",
        description="Implementation and integration guidance",
        triggers=[
            "implement",
            "integration",
            "coding",
            "example",
            "best practice",
            "migration",
            "实现",
            "落地",
            "集成",
            "迁移",
        ],
        retrieval_profile="balanced",
        planner_template="implementation_template",
    ),
    SkillSpec(
        skill_id="quick_fact_qa",
        description="Factoid or definition question",
        triggers=[
            "what is",
            "define",
            "introduction",
            "概念",
            "定义",
            "是什么",
        ],
        retrieval_profile="fast",
        planner_template="qa_template",
    ),
    SkillSpec(
        skill_id=DEFAULT_SKILL_ID,
        description="General RAG fallback",
        triggers=[],
        retrieval_profile="balanced",
        planner_template="default",
    ),
]


def get_skill_map() -> Dict[str, SkillSpec]:
    return {skill.skill_id: skill for skill in SKILLS}


def get_skill(skill_id: str, default_skill_id: str = DEFAULT_SKILL_ID) -> SkillSpec:
    skill_map = get_skill_map()
    normalized = str(skill_id or "").strip()
    if normalized and normalized in skill_map:
        return skill_map[normalized]
    return skill_map.get(default_skill_id, SKILLS[-1])


def list_skills(include_default: bool = True) -> List[SkillSpec]:
    if include_default:
        return list(SKILLS)
    return [skill for skill in SKILLS if skill.skill_id != DEFAULT_SKILL_ID]


def validate_skill_id(skill_id: str) -> Optional[str]:
    normalized = str(skill_id or "").strip()
    if not normalized:
        return None
    if normalized in get_skill_map():
        return normalized
    return None
