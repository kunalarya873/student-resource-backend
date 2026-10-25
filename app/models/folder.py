import uuid

from sqlalchemy import ForeignKey, Index, String
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
