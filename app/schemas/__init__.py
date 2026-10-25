"""API Schemas package."""

from app.schemas.admin import (
    AuditEventRead,
    DepartmentStorageSummary,
    SystemStorageReport,
    UserQuotaUpdate,
)
from app.schemas.common import CoreSchema, PaginationParams, StandardMessageResponse
from app.schemas.resource import (
    ResourceCreate,
    ResourceDownloadResponse,
    ResourceRead,
    ResourceUpdate,
    ResourceVersionCreate,
    ResourceVersionRead,
)
from app.schemas.search import (
    SearchResponse,
    SearchResultItem,
)
from app.schemas.sharing import (
    ShareGrantCreate,
    ShareGrantRead,
    ShareGrantUpdate,
)
from app.schemas.storage import (
    DownloadUrlResponse,
    StorageQuotaResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitiateRequest,
    UploadInitiateResponse,
)

__all__ = [
    "AuditEventRead",
    "CoreSchema",
    "DepartmentStorageSummary",
    "DownloadUrlResponse",
    "PaginationParams",
    "ResourceCreate",
    "ResourceDownloadResponse",
    "ResourceRead",
    "ResourceUpdate",
    "ResourceVersionCreate",
    "ResourceVersionRead",
    "SearchResponse",
    "SearchResultItem",
    "ShareGrantCreate",
    "ShareGrantRead",
    "ShareGrantUpdate",
    "StandardMessageResponse",
    "StorageQuotaResponse",
    "SystemStorageReport",
    "UploadCompleteRequest",
    "UploadCompleteResponse",
    "UploadInitiateRequest",
    "UploadInitiateResponse",
    "UserQuotaUpdate",
]
