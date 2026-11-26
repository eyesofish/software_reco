from typing import Any

from pydantic import BaseModel, Field

from .document_schema import Document


class EvidenceItem(BaseModel):
    subquery_id: str = ""
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
    validated_query_expansion: str = ""
    rewrite_audit: dict[str, Any] = Field(default_factory=dict)
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
    timeout_budget: float = 60
    start_time: float | None = None
    awaiting_human_confirmation: bool = False
    human_feedback: str = ""
    pending_sub_questions: list[str] = Field(default_factory=list)
    human_confirmation_done: bool = False
    hitl_policy: str = "human"
    oracle_edits: list[str] = Field(default_factory=list)
    hitl: dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
    kb_snapshot_id: str = ""
    run_id: str | None = None
    routing_decision: dict[str, Any] | None = None
    dynamic_routing_enabled: bool = False
    route_stage: str = "planning"
    next_step: str = "retrieve"
    route_decisions: list[dict[str, Any]] = Field(default_factory=list)
    routing_reviews_used: int = 0
    refinement_count: int = 0
    clarification_count: int = 0
    pending_input: dict[str, Any] | None = None
    execution_cancelled: bool = False
    active_elapsed_ms: int = 0
    uncovered_subqueries: list[str] = Field(default_factory=list)
    subquery_ids: dict[str, str] = Field(default_factory=dict)
    retrieval_queries: dict[str, str] = Field(default_factory=dict)
    attempted_queries: list[str] = Field(default_factory=list)
    evidence_incomplete: bool = False
    dynamic_stop_reason: str = ""
