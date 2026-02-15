from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RecommendationRequest(BaseModel):
    query: str = Field(..., description="User query text", min_length=1, max_length=1000)
    timeout: int = Field(default=60, description="Timeout in seconds", ge=10, le=300)
    max_iterations: int = Field(
        default=3,
        description="Max iteration count",
        ge=1,
        le=10,
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Client session id for resume; if empty server will generate one",
    )


class RecommendationConfirmRequest(BaseModel):
    session_id: str = Field(..., description="Session id returned by /recommend", min_length=1)
    action: str = Field(default="confirm", pattern="^(confirm|edit)$")
    sub_questions: Optional[List[str]] = Field(default=None, description="Required when action=edit")
    comment: Optional[str] = Field(default=None, description="Human comment")


class RecommendationResponse(BaseModel):
    status: str = Field(..., description="Request status")
    final_answer: str = Field(..., description="Final answer")
    candidates: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Candidate recommendations",
    )
    mode: Optional[str] = Field(None, description="Mode used (rag/chat/draw)")
    iteration_count: Optional[int] = Field(None, description="Iteration count")
    coverage: Optional[float] = Field(None, description="Coverage score")
    session_id: Optional[str] = Field(None, description="Session id for resume")
    awaiting_human_confirmation: Optional[bool] = Field(
        None,
        description="Whether pipeline is waiting for human input",
    )
    pending_sub_questions: Optional[List[str]] = Field(
        None,
        description="Sub-questions waiting for confirmation",
    )


class HealthCheckResponse(BaseModel):
    status: str = Field(..., description="Health status")
