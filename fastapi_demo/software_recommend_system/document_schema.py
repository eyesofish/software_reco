from typing import Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime


class Metadata(BaseModel):
    source: str  # 来源网站或文档
    author: Optional[str] = None  # 作者
    published_date: Optional[datetime] = None  # 发布日期
    updated_date: Optional[datetime] = None  # 更新日期
    url: Optional[str] = None  # 原始URL
    tags: Optional[list[str]] = []  # 标签
    source_ranking: float = 0.0  # 权威性评分 (0-1)


class Document(BaseModel):
    content: str
    metadata: Metadata
    score: float  # 相似度分数
    freshness_score: float = 0.0  # 新鲜度评分 (0-1)
    authority_score: float = 0.0  # 权威性评分 (0-1)