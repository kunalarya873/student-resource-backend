import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SearchResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    title: str
    description: str | None = None
    subject_code: str | None = None
    subject_name: str | None = None
    owner_name: str
    visibility: str
    status: str
    rank: float
    created_at: datetime | None = None


class SearchResponse(BaseModel):
    total_count: int
    page: int
    limit: int
    results: list[SearchResultItem]
