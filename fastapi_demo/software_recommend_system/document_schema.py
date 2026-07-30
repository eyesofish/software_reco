from datetime import datetime

from pydantic import BaseModel, Field


class Metadata(BaseModel):
    """Normalized metadata used by retrieval and downstream nodes."""

    source: str
    doc_id: str | None = None
    retrieval_source: str | None = None
    channel: str | None = None
    channel_score: float = 0.0
    score: float = 0.0
    author: str | None = None
    published_date: datetime | None = None
    updated_date: datetime | None = None
    url: str | None = None
    filename: str | None = None
    media_type: str | None = None
    modality: str | None = None
    asset_path: str | None = None
    asset_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    memory_level: str | None = None
    session_id: str | None = None
    source_ranking: float = 0.0
    matched_channels: list[str] = Field(default_factory=list)
    query_modalities: list[str] = Field(default_factory=list)
    channel_rank: int | None = None
    fusion_score: float = 0.0
    vector_distance: float | None = None
    rerank_features: dict = Field(default_factory=dict)


class Document(BaseModel):
    """Document payload shared by chat/rag retrieval outputs."""

    content: str
    metadata: Metadata
    score: float
    freshness_score: float = 0.0
    authority_score: float = 0.0
