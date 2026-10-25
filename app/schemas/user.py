import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    role_id: int
    role_name: str
    department_id: int | None = None
    department_name: str | None = None
    status: str
    created_at: datetime


class UserUpdate(BaseModel):
    full_name: str | None = None
    # `department_id` distinguishes "not provided" from an explicit null (clear)
    # via model_fields_set in the handler.
    department_id: int | None = None


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department_id: int
    code: str
    name: str
