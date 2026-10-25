import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class Subject(Base):
    __tablename__ = "subjects"

    subject_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.department_id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    semester: Mapped[int] = mapped_column(Integer, nullable=False)

    department = relationship("Department", back_populates="subjects")
    resources = relationship("Resource", back_populates="subject")


class AcademicYear(Base):
    __tablename__ = "academic_years"

    academic_year_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    resources = relationship("Resource", back_populates="academic_year")


class Resource(Base, TimestampMixin):
    __tablename__ = "resources"
    __table_args__ = (
        Index("idx_resources_owner_status", "owner_id", "status"),
        Index("idx_resources_title", "title"),
        Index("idx_resources_folder_id", "folder_id"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("folders.folder_id", ondelete="SET NULL"),
        nullable=True,
    )
    subject_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subjects.subject_id", ondelete="SET NULL"), nullable=True
    )
    academic_year_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("academic_years.academic_year_id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(30), default="PRIVATE", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_VALIDATION", nullable=False)

    # Relationships
    owner = relationship("User", back_populates="resources")
    folder = relationship("Folder", back_populates="resources")
    subject = relationship("Subject", back_populates="resources")
    academic_year = relationship("AcademicYear", back_populates="resources")
    versions = relationship(
        "ResourceVersion",
        back_populates="resource",
        cascade="all, delete-orphan",
        order_by="ResourceVersion.version_number",
    )
    access_grants = relationship(
        "ResourceAccess", back_populates="resource", cascade="all, delete-orphan"
    )
    tags = relationship("Tag", secondary="resource_tags", back_populates="resources")


class ResourceVersion(Base):
    __tablename__ = "resource_versions"
    __table_args__ = (
        UniqueConstraint("resource_id", "version_number", name="uq_resource_version_number"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resources.resource_id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    resource = relationship("Resource", back_populates="versions")
    storage_object = relationship(
        "StorageObject",
        back_populates="version",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Tag(Base):
    __tablename__ = "tags"

    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    resources = relationship("Resource", secondary="resource_tags", back_populates="tags")


class ResourceTag(Base):
    __tablename__ = "resource_tags"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resources.resource_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.tag_id", ondelete="CASCADE"), primary_key=True
    )


class ResourceAccess(Base):
    __tablename__ = "resource_access"
    __table_args__ = (
        UniqueConstraint("resource_id", "user_id", name="uq_resource_user_access"),
        Index("idx_resource_access_user_id", "user_id"),
    )

    access_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resources.resource_id", ondelete="CASCADE"),
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

    resource = relationship("Resource", back_populates="access_grants")
    user = relationship("User", foreign_keys=[user_id])
    granter = relationship("User", foreign_keys=[granted_by])
