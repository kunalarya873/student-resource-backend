"""
Data access repository package for the AMU Student Resource Management System.
Implements the Repository Pattern to abstract data persistence logic from business services.
"""

from app.repositories.audit_repository import AuditRepository, audit_repository
from app.repositories.base import BaseRepository
from app.repositories.folder_repository import FolderRepository, folder_repository
from app.repositories.resource_repository import ResourceRepository, resource_repository
from app.repositories.sharing_repository import SharingRepository, sharing_repository
from app.repositories.storage_repository import StorageRepository, storage_repository
from app.repositories.user_repository import UserRepository, user_repository

__all__ = [
    "AuditRepository",
    "BaseRepository",
    "FolderRepository",
    "ResourceRepository",
    "SharingRepository",
    "StorageRepository",
    "UserRepository",
    "audit_repository",
    "folder_repository",
    "resource_repository",
    "sharing_repository",
    "storage_repository",
    "user_repository",
]
