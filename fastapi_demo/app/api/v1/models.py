from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator


class ImageAttachment(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    media_type: str = Field(..., pattern="^image/(jpeg|png|webp)$")
    data_url: str = Field(..., min_length=32, max_length=8_000_000)

    @model_validator(mode="after")
    def validate_data_url_header(self) -> Self:
        expected_prefix = f"data:{self.media_type};base64,"
        if not self.data_url.startswith(expected_prefix):
            raise ValueError("data_url must match media_type and use base64 encoding")
        return self


class RecommendationRequest(BaseModel):
    routing_mode: Literal["explicit", "auto", "new"] | None = None
    current_conversation_id: str | None = None
    thread_id: str | None = None
    # Internal service-key boundary; the business API never accepts these from a browser.
    routing_decision: dict[str, Any] | None = None
    routing_prechecked: bool = False
    execution_budget_seconds: float | None = Field(default=None, gt=0, le=300)
    query: str = Field(default="", description="User query text", max_length=1000)
    images: list[ImageAttachment] = Field(
        default_factory=list,
        description="Optional image attachments for multimodal retrieval",
        max_length=4,
    )
    timeout: int = Field(default=60, description="Timeout in seconds", ge=10, le=300)
    max_iterations: int = Field(
        default=3,
        description="Max iteration count",
        ge=1,
        le=10,
    )
    session_id: str | None = Field(
        default=None,
        description="Client session id for resume; if empty server will generate one",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Conversation id alias for session_id",
    )
    hitl_policy: str | None = Field(
        default=None,
        pattern="^(human|auto_confirm|oracle_edit)$",
        description="HITL policy for evaluation mode",
    )
    oracle_edits: list[str] | None = Field(
        default=None,
        description="Optional oracle-edited sub-questions for oracle_edit policy",
    )

    @model_validator(mode="after")
    def normalize_ids(self) -> Self:
        ids = {value.strip() for value in (self.session_id, self.conversation_id, self.thread_id)
               if value and value.strip()}
        if len(ids) > 1:
            raise ValueError("Conflicting session identifiers")
        if self.routing_mode == "new" and ids:
            raise ValueError("new routing cannot include an existing session")
        self.session_id = next(iter(ids), None)
        if self.routing_decision is not None:
            from software_recommend_system.intent_routing import RoutingResolution
            self.routing_decision = RoutingResolution.model_validate(self.routing_decision).model_dump()
        if not self.query.strip() and not self.images:
            raise ValueError("query or at least one image is required")
        return self


class RecommendationConfirmRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        description="Session id returned by /recommend",
        min_length=1,
    )
    action: str = Field(default="confirm", pattern="^(confirm|edit)$")
    sub_questions: list[str] | None = Field(default=None, description="Required when action=edit")
    comment: str | None = Field(default=None, description="Human comment")
    conversation_id: str | None = Field(
        default=None,
        description="Conversation id alias for session_id",
    )

    @model_validator(mode="after")
    def normalize_ids(self) -> Self:
        if not self.session_id and self.conversation_id:
            self.session_id = self.conversation_id
        if not self.session_id:
            raise ValueError("session_id or conversation_id is required")
        return self


class RetrievedImage(BaseModel):
    doc_id: str
    filename: str
    media_type: str
    url: str
    caption: str = ""
    score: float = 0.0


class RecommendationResponse(BaseModel):
    status: str = Field(..., description="Request status")
    final_answer: str = Field(..., description="Final answer")
    clarification: dict[str, Any] | None = None
    pending_input: dict[str, Any] | None = None
    route_decisions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_incomplete: bool = False
    candidates: list[dict[str, Any]] | None = Field(
        default=None,
        description="Candidate recommendations",
    )
    mode: str | None = Field(default=None, description="Mode used (rag/direct/hitl)")
    iteration_count: int | None = Field(default=None, description="Iteration count")
    coverage: float | None = Field(default=None, description="Coverage score")
    session_id: str | None = Field(default=None, description="Session id for resume")
    awaiting_human_confirmation: bool | None = Field(
        default=None,
        description="Whether pipeline is waiting for human input",
    )
    pending_sub_questions: list[str] | None = Field(
        default=None,
        description="Sub-questions waiting for confirmation",
    )
    hitl: dict[str, Any] | None = Field(
        default=None,
        description="HITL decision payload used for tracing/evaluation",
    )
    retrieval_records: list[dict[str, Any]] | None = Field(
        default=None,
        description="Per-subquery retrieval records",
    )
    retrieved_doc_ids: list[str] | None = Field(
        default=None,
        description="Deduplicated union of retrieved doc ids across all subqueries",
    )
    retrieved_images: list[RetrievedImage] | None = Field(
        default=None,
        description="Image knowledge assets included in retrieved evidence",
    )
    selected_skill: str | None = Field(
        default=None,
        description="Skill selected by skill router",
    )
    plan_steps: list[dict[str, Any]] | None = Field(
        default=None,
        description="Planner output steps",
    )
    run_id: str | None = Field(default=None, description="Identifier of this execution")
    stop_reason: str | None = Field(default=None, description="Reason the execution did not complete")
    elapsed_ms: int | None = Field(default=None, description="Elapsed execution time in milliseconds")


class SessionStateUpdateRequest(BaseModel):
    facts: dict[str, str] = Field(default_factory=dict, description="Structured session facts")


class ClarificationRequest(BaseModel):
    session_id: str = Field(min_length=1)
    clarification_id: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)


class CancelWaitingRequest(BaseModel):
    session_id: str = Field(min_length=1)
    clarification_id: str | None = None


class SessionStateResponse(BaseModel):
    session_id: str = Field(..., description="Session id")
    facts: dict[str, str] = Field(default_factory=dict, description="Structured session facts")
    messages: list[dict[str, str]] = Field(
        default_factory=list,
        description="Session message history",
    )
    updated_at: float = Field(..., description="Unix timestamp in seconds")


class HealthCheckResponse(BaseModel):
    status: str = Field(..., description="Health status")
