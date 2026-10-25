import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserQuotaUpdate(BaseModel):
    new_quota_bytes: int = Field(gt=0, description="New storage quota limit in bytes")


class DepartmentStorageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department_id: int | None
    department_name: str
    total_users: int
    total_quota_bytes: int
    total_used_bytes: int
    utilization_percentage: float


class SystemStorageReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_allocated_bytes: int
    total_used_bytes: int
    overall_utilization_percentage: float
    department_breakdown: list[DepartmentStorageSummary]


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_email: str | None = None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    details: dict
    created_at: datetime
