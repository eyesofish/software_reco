from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Metadata(BaseModel):
    """Normalized metadata used by retrieval and downstream nodes."""

    source: str
    doc_id: Optional[str] = None
    score: float = 0.0
    author: Optional[str] = None
    published_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source_ranking: float = 0.0


class Document(BaseModel):
    """Document payload shared by chat/rag retrieval outputs."""

    content: str
    metadata: Metadata
    score: float
    freshness_score: float = 0.0
    authority_score: float = 0.0
