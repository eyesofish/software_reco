from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from .document_schema import Document


class EvidenceItem(BaseModel):
    question: str
    documents: List[Document]
    search_results: List[Dict[str, Any]]
    quality_score: float


class CandidateSolution(BaseModel):
    solution: str
    rationale: str
    pros: List[str]
    cons: List[str]
    relevance_score: float


class AgentState(BaseModel):
    user_query: str = ""
    messages: List[Dict[str, str]] = Field(default_factory=list)
    mode: Optional[str] = None
    normalized_query: str = ""
    constraints: Dict[str, str] = {"language": "", "scenario": "", "preference": ""}
    sub_questions: List[str] = []
    evidence: List[EvidenceItem] = []
    candidates: List[CandidateSolution] = []
    final_answer: str = ""
    drawing_params: str = ""
    image_result: str = ""
    coverage: float = 0.0
    coverage_threshold: float = 0.8
    needs_refinement: bool = False
    iteration_count: int = 0
    max_iterations: int = 3
    timeout_budget: int = 60
    start_time: Optional[float] = None
    awaiting_human_confirmation: bool = False
    human_feedback: str = ""
    pending_sub_questions: List[str] = []
    session_id: str = ""
