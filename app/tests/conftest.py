from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import get_db_session
from app.main import app


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    test_engine = create_async_engine(settings.ASYNC_DATABASE_URI, poolclass=NullPool)
    session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


async def get_auth_token(
    async_client: AsyncClient,
    email: str = "student@amu.edu",
    password: str = "amu_demo_2026!",
) -> str:
    """Centralized test authentication helper to eliminate duplicate login routines."""
    res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if res.status_code != 200:
        raise ValueError(f"Failed to authenticate {email}: {res.text}")
    return res.json()["access_token"]
