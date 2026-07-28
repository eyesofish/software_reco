from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemoryLevel = Literal["working", "episodic", "semantic"]


class MemoryItem(BaseModel):
    memory_id: str
    session_id: str
    level: MemoryLevel
    content: str
    tags: list[str] = Field(default_factory=list)
    salience: float = 0.0
    created_at: float
    updated_at: float
    score: float = 0.0
