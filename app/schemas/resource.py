import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.schemas.common import CoreSchema


class ResourceBase(CoreSchema):
    title: str = Field(..., min_length=1, max_length=200, description="Resource title")
    description: str | None = Field(
        default=None, max_length=2000, description="Resource description"
    )
    folder_id: uuid.UUID | None = Field(default=None, description="Enclosing folder ID")
    subject_id: int | None = Field(default=None, description="Academic subject ID")
    academic_year_id: int | None = Field(default=None, description="Academic year ID")


class ResourceCreate(ResourceBase):
    """Payload to create a new resource backed by an uploaded file or safe external link."""

    reservation_id: uuid.UUID | None = Field(
        default=None,
        description="Completed upload reservation ID for file-backed resource",
    )
    external_url: str | None = Field(
        default=None,
        description="Public HTTPS/HTTP URL for link-backed resource",
    )

    @model_validator(mode="after")
    def validate_one_source(self) -> "ResourceCreate":
        has_file = self.reservation_id is not None
        has_link = self.external_url is not None and len(str(self.external_url).strip()) > 0
        if not has_file and not has_link:
            raise ValueError("Must provide either a completed reservation_id or an external_url.")
        if has_file and has_link:
            raise ValueError("Cannot provide both reservation_id and external_url simultaneously.")
        return self


class ResourceUpdate(CoreSchema):
    """Payload to edit resource metadata or visibility."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    folder_id: uuid.UUID | None = None
    subject_id: int | None = None
    academic_year_id: int | None = None
    visibility: str | None = Field(default=None, description="PRIVATE | DEPARTMENT | PUBLIC")


class ResourceVersionCreate(CoreSchema):
    """Payload to add a new version to an existing resource."""

    reservation_id: uuid.UUID | None = None
    external_url: str | None = None

    @model_validator(mode="after")
    def validate_one_source(self) -> "ResourceVersionCreate":
        has_file = self.reservation_id is not None
        has_link = self.external_url is not None and len(str(self.external_url).strip()) > 0
        if not has_file and not has_link:
            raise ValueError("Must provide either a reservation_id or an external_url.")
        if has_file and has_link:
            raise ValueError("Cannot provide both reservation_id and external_url simultaneously.")
        return self


class ResourceVersionRead(CoreSchema):
    """Resource version metadata representation."""

    version_id: uuid.UUID
    resource_id: uuid.UUID
    uploaded_by: uuid.UUID
    version_number: int
    content_type: str
    external_url: str | None = None
    size_bytes: int
    checksum: str | None = None
    created_at: datetime


class ResourceRead(CoreSchema):
    """Full resource representation with current version and version history."""

    resource_id: uuid.UUID
    owner_id: uuid.UUID
    folder_id: uuid.UUID | None = None
    subject_id: int | None = None
    academic_year_id: int | None = None
    title: str
    description: str | None = None
    visibility: str
    status: str
    created_at: datetime
    updated_at: datetime
    current_version: ResourceVersionRead | None = None
    versions: list[ResourceVersionRead] = Field(default_factory=list)


class ResourceDownloadResponse(CoreSchema):
    """Response containing secure download URL or redirect for resource asset."""

    resource_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    is_external: bool
    download_url: str
    filename: str
    content_type: str
    size_bytes: int
