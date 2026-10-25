import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.user import Department, EmailOTP, Role, User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Data access repository for User entities, roles, and OTP credentials."""

    def __init__(self) -> None:
        super().__init__(User)

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        """Find a user by normalized email address."""
        stmt = (
            select(User)
            .where(User.email == email.strip().lower())
            .options(joinedload(User.role), joinedload(User.department))
        )
        return await session.scalar(stmt)

    async def get_by_id_with_relations(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> User | None:
        """Fetch user by primary key eager-loading role, department, and storage quota relationships."""
        stmt = (
            select(User)
            .where(User.user_id == user_id)
            .options(
                joinedload(User.role),
                joinedload(User.department),
                joinedload(User.storage_quota),
            )
        )
        return await session.scalar(stmt)

    async def get_role_by_name(self, session: AsyncSession, role_name: str) -> Role | None:
        """Fetch a system role by unique name."""
        stmt = select(Role).where(Role.name == role_name)
        return await session.scalar(stmt)

    async def get_department_by_code(self, session: AsyncSession, code: str) -> Department | None:
        """Lookup department by code."""
        stmt = select(Department).where(Department.code == code)
        return await session.scalar(stmt)

    async def get_active_otp(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        purpose: str,
    ) -> EmailOTP | None:
        """Retrieve the latest unexpired OTP for a user and specific purpose."""
        stmt = (
            select(EmailOTP)
            .where(EmailOTP.user_id == user_id, EmailOTP.purpose == purpose)
            .order_by(EmailOTP.created_at.desc())
        )
        return await session.scalar(stmt)

    async def list_active_users(
        self,
        session: AsyncSession,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[User]:
        """List active users with pagination."""
        stmt = (
            select(User)
            .where(User.status == "ACTIVE")
            .options(selectinload(User.role))
            .offset(skip)
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return result.all()


user_repository = UserRepository()
