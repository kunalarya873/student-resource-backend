import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BreadcrumbItem(BaseModel):
    folder_id: uuid.UUID
    name: str


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_folder_id: uuid.UUID | None = None


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    parent_folder_id: uuid.UUID | None = None


class FolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    folder_id: uuid.UUID
    owner_id: uuid.UUID
    parent_folder_id: uuid.UUID | None
    name: str
    is_public: bool = False
    share_token: str | None = None
    created_at: datetime
    updated_at: datetime


class FolderResourceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None
    visibility: str
    status: str
    is_public: bool = False
    share_token: str | None = None
    created_at: datetime


class FolderContentsResponse(BaseModel):
    folder: FolderRead
    breadcrumbs: list[BreadcrumbItem]
    subfolders: list[FolderRead]
    resources: list[FolderResourceItem]
    page: int = Field(default=1, description="Current page number")
    limit: int = Field(default=50, description="Items per page")
    total_subfolders: int = Field(default=0, description="Total count of subfolders")
    total_resources: int = Field(default=0, description="Total count of resources")


class PaginatedFolderResponse(BaseModel):
    items: list[FolderRead]
    page: int = Field(default=1, description="Current page number")
    limit: int = Field(default=50, description="Items per page")
    total: int = Field(default=0, description="Total count of folders")
    total_pages: int = Field(default=0, description="Total pages available")
