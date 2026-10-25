import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import CoreSchema


class StorageQuotaResponse(CoreSchema):
    """Current storage quota breakdown for an authenticated user."""

    user_id: uuid.UUID
    quota_bytes: int
    used_bytes: int
    reserved_bytes: int
    available_bytes: int
    percent_used: float = Field(
        ...,
        description="Percentage of storage used based on (used_bytes / quota_bytes) * 100",
    )


class UploadInitiateRequest(CoreSchema):
    """Payload to initiate a two-phase atomic upload reservation."""

    filename: str = Field(..., min_length=1, max_length=255, description="Original filename")
    content_type: str = Field(
        default="application/octet-stream",
        max_length=100,
        description="MIME type of the file",
    )
    size_bytes: int = Field(..., gt=0, description="Exact file size in bytes")


class UploadInitiateResponse(CoreSchema):
    """Response returned upon successful quota reservation with presigned S3 PUT URL."""

    reservation_id: uuid.UUID
    upload_url: str
    object_key: str
    expires_at: datetime
    reserved_bytes: int


class UploadCompleteRequest(CoreSchema):
    """Request to finalize and verify an upload after transferring payload to S3."""

    reservation_id: uuid.UUID
    checksum_sha256: str | None = Field(
        default=None,
        description="Optional SHA256 checksum to verify against the object",
    )


class UploadCompleteResponse(CoreSchema):
    """Response confirming that object was verified in S3 and storage quota was committed."""

    storage_object_id: uuid.UUID
    reservation_id: uuid.UUID
    object_key: str
    byte_size: int
    checksum: str | None = None
    verification_status: str
    message: str = "Upload successfully verified and quota committed."


class DownloadUrlResponse(CoreSchema):
    """Presigned download URL response for stored assets."""

    download_url: str
    object_key: str
    filename: str
    expires_in_seconds: int


class UploadStatusResponse(CoreSchema):
    """Live upload progress and finalization state for frontend tracking."""

    reservation_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    progress_percent: int
    bytes_uploaded: int
    total_bytes: int
    object_key: str | None = None
    filename: str | None = None
    content_type: str | None = None
    resource_id: uuid.UUID | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class UploadProgressUpdateRequest(CoreSchema):
    """Payload sent by client to update in-flight byte transmission progress."""

    progress_percent: int = Field(
        ..., ge=0, le=100, description="Upload completion percentage (0-100)"
    )
    bytes_uploaded: int | None = Field(default=None, ge=0, description="Bytes transferred so far")


class SupabaseWebhookResponse(CoreSchema):
    """Response returned upon processing a Supabase storage webhook event."""

    status: str = "success"
    message: str
    event_type: str | None = None
    object_key: str | None = None
    resource_id: uuid.UUID | None = None
