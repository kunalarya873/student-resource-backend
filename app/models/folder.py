import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class Folder(Base, TimestampMixin):
    __tablename__ = "folders"
    __table_args__ = (
        Index("idx_folders_owner_parent", "owner_id", "parent_folder_id"),
        Index("idx_folders_parent_id", "parent_folder_id"),
    )

    folder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("folders.folder_id", ondelete="RESTRICT"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)

    # Relationships
    owner = relationship("User", back_populates="folders")
    parent_folder = relationship("Folder", remote_side=[folder_id], back_populates="subfolders")
    subfolders = relationship("Folder", back_populates="parent_folder")
    resources = relationship("Resource", back_populates="folder")
    access_grants = relationship(
        "FolderAccess", back_populates="folder", cascade="all, delete-orphan"
    )


class FolderAccess(Base):
    """Explicit share grant giving a user VIEW or EDIT access to a folder and,
    cascading, to everything inside its subtree (subfolders and resources)."""

    __tablename__ = "folder_access"
    __table_args__ = (
        UniqueConstraint("folder_id", "user_id", name="uq_folder_user_access"),
        Index("idx_folder_access_user_id", "user_id"),
    )

    access_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    folder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("folders.folder_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    permission: Mapped[str] = mapped_column(
        String(30), default="VIEW", nullable=False
    )  # VIEW | EDIT
    granted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    folder = relationship("Folder", back_populates="access_grants")
    user = relationship("User", foreign_keys=[user_id])
    granter = relationship("User", foreign_keys=[granted_by])
