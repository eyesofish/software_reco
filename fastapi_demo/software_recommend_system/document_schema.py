from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Metadata(BaseModel):
    """Normalized metadata used by retrieval and downstream nodes."""

    source: str
    doc_id: Optional[str] = None
    retrieval_source: Optional[str] = None
    channel: Optional[str] = None
    channel_score: float = 0.0
    score: float = 0.0
    author: Optional[str] = None
    published_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    memory_level: Optional[str] = None
    session_id: Optional[str] = None
    source_ranking: float = 0.0
    rerank_features: dict = Field(default_factory=dict)


class Document(BaseModel):
    """Document payload shared by chat/rag retrieval outputs."""

    content: str
    metadata: Metadata
    score: float
    freshness_score: float = 0.0
    authority_score: float = 0.0
