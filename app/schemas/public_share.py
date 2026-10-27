import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.folder import BreadcrumbItem, FolderRead, FolderResourceItem


class PublicLinkToggleRequest(BaseModel):
    is_public: bool = Field(description="Set to true to make publicly accessible via link, false to restrict")


class PublicLinkResponse(BaseModel):
    is_public: bool
    share_token: str | None = None
    share_url: str | None = None
    permission: str = "VIEW"


class PublicShareItemResponse(BaseModel):
    kind: str = Field(description="'folder' or 'resource'")
    share_token: str
    name: str
    owner_name: str
    created_at: datetime
    size_bytes: int | None = None
    content_type: str | None = None


class PublicFolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    folder_id: uuid.UUID
    name: str
    owner_name: str
    created_at: datetime
    share_token: str
    is_public: bool


class PublicFolderContentsResponse(BaseModel):
    folder: PublicFolderRead
    breadcrumbs: list[BreadcrumbItem]
    subfolders: list[FolderRead]
    resources: list[FolderResourceItem]
    page: int = Field(default=1)
    limit: int = Field(default=50)
    total_subfolders: int = Field(default=0)
    total_resources: int = Field(default=0)


class PublicResourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    title: str
    description: str | None = None
    content_type: str | None = None
    size_bytes: int = 0
    is_external: bool = False
    external_url: str | None = None
    owner_name: str
    created_at: datetime
    share_token: str
    is_public: bool


class PublicResourceDownloadResponse(BaseModel):
    download_url: str
    filename: str
    content_type: str
    size_bytes: int
    is_external: bool


class PublicJoinResponse(BaseModel):
    message: str
    kind: str
    item_id: uuid.UUID
    already_member: bool = False
