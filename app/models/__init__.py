"""Domain models package exporting all 15 relational entities."""

from app.models.audit import AuditEvent
from app.models.base import TimestampMixin, UUIDMixin
from app.models.folder import Folder, FolderAccess
from app.models.resource import (
    AcademicYear,
    Resource,
    ResourceAccess,
    ResourceTag,
    ResourceVersion,
    Subject,
    Tag,
)
from app.models.storage import StorageObject, StorageQuota, UploadReservation
from app.models.user import (
    Department,
    EmailOTP,
    Role,
    StudentSubject,
    TeacherSubject,
    User,
)

__all__ = [
    "AcademicYear",
    "AuditEvent",
    "Department",
    "EmailOTP",
    "Folder",
    "FolderAccess",
    "Resource",
    "ResourceAccess",
    "ResourceTag",
    "ResourceVersion",
    "Role",
    "StorageObject",
    "StorageQuota",
    "StudentSubject",
    "Subject",
    "Tag",
    "TeacherSubject",
    "TimestampMixin",
    "UUIDMixin",
    "UploadReservation",
    "User",
]
