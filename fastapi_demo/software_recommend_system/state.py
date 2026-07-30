from typing import Any

from pydantic import BaseModel, Field

from .document_schema import Document


class EvidenceItem(BaseModel):
    question: str
    documents: list[Document]
    search_results: list[dict[str, Any]]
    quality_score: float


class CandidateSolution(BaseModel):
    solution: str
    rationale: str
    pros: list[str]
    cons: list[str]
    relevance_score: float


class AgentState(BaseModel):
    user_query: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    input_images: list[dict[str, str]] = Field(default_factory=list)
    query_image_candidates: list[Document] = Field(default_factory=list)
    mode: str | None = None
    normalized_query: str = ""
    constraints: dict[str, str] = Field(
        default_factory=lambda: {"language": "", "scenario": "", "preference": ""}
    )
    skill_candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_skill: str = ""
    skill_router_reason: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    plan_steps: list[dict[str, Any]] = Field(default_factory=list)
    planner_reason: str = ""
    sub_questions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    retrieval_records: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_doc_ids: list[str] = Field(default_factory=list)
    retrieved_doc_ids_full: list[str] = Field(default_factory=list)
    retrieved_images: list[dict[str, Any]] = Field(default_factory=list)
    memory_context: list[dict[str, Any]] = Field(default_factory=list)
    memory_doc_ids: list[str] = Field(default_factory=list)
    candidates: list[CandidateSolution] = Field(default_factory=list)
    final_answer: str = ""
    drawing_params: str = ""
    image_result: str = ""
    coverage: float = 0.0
    previous_coverage: float = 0.0
    coverage_threshold: float = 0.8
    needs_refinement: bool = False
    iteration_count: int = 0
    max_iterations: int = 3
    timeout_budget: int = 60
    start_time: float | None = None
    awaiting_human_confirmation: bool = False
    human_feedback: str = ""
    pending_sub_questions: list[str] = Field(default_factory=list)
    human_confirmation_done: bool = False
    hitl_policy: str = "human"
    oracle_edits: list[str] = Field(default_factory=list)
    hitl: dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
