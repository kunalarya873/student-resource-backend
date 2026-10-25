import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.storage import StorageQuota
from app.models.user import User
from app.services.email_service import EmailService


@pytest.mark.asyncio
async def test_signup_success_and_email_dispatched(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify registration creates unverified user, allocates quota, and triggers verification email."""
    test_email = f"student_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True) as mock_send:
        res = await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Test Candidate",
                "role_name": "Student",
                "department_id": 1,
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["email"] == test_email
        assert data["status"] == "PENDING_VERIFICATION"
        assert "verification link" in data["message"].lower()

        # Verify DB records
        user = await db_session.scalar(select(User).where(User.email == test_email))
        assert user is not None
        assert user.status == "PENDING_VERIFICATION"

        # Verify quota record created
        quota = await db_session.scalar(
            select(StorageQuota).where(StorageQuota.user_id == user.user_id)
        )
        assert quota is not None
        assert quota.quota_bytes == 1073741824  # 1 GiB for Student

        # Verify email was dispatched
        mock_send.assert_called_once()
        _args, kwargs = mock_send.call_args
        assert kwargs["email"] == test_email
        assert kwargs["full_name"] == "Test Candidate"
        assert len(kwargs["token"]) > 20


@pytest.mark.asyncio
async def test_signup_duplicate_email_conflict(async_client: AsyncClient):
    """Verify attempting to signup with existing active email returns 409 Conflict."""
    res = await async_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "student@amu.edu",  # Seeded user
            "password": "Password123!",
            "full_name": "Duplicate Student",
            "role_name": "Student",
        },
    )
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signup_short_password_rejected(async_client: AsyncClient):
    """Verify password shorter than 8 characters is rejected."""
    res = await async_client.post(
        "/api/v1/auth/signup",
        json={
            "email": f"short_{uuid.uuid4().hex[:8]}@amu.edu",
            "password": "short",
            "full_name": "Short Pass User",
        },
    )
    assert res.status_code == 400
    assert "at least 8 characters" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signup_admin_role_forbidden(async_client: AsyncClient):
    """Verify self-registration as Administrator is strictly rejected."""
    res = await async_client.post(
        "/api/v1/auth/signup",
        json={
            "email": f"fake_admin_{uuid.uuid4().hex[:8]}@amu.edu",
            "password": "AdminPassword123!",
            "full_name": "Hacker Admin",
            "role_name": "Administrator",
        },
    )
    assert res.status_code == 403
    assert "not permitted" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_blocked_while_pending_verification(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify unverified user cannot log in and receives informative message."""
    test_email = f"pending_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Pending Student",
            },
        )

    # Attempt login
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": "SecurePassword123!"},
    )
    assert login_res.status_code == 403
    assert "verify your email" in login_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_email_post_flow(async_client: AsyncClient, db_session: AsyncSession):
    """Verify email verification transitions user to ACTIVE and unlocks login."""
    test_email = f"verify_{uuid.uuid4().hex[:8]}@amu.edu"
    captured_token = None

    async def fake_send(email, full_name, token, *args, **kwargs):
        nonlocal captured_token
        captured_token = token
        return True

    with patch.object(EmailService, "send_verification_email", side_effect=fake_send):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "ValidPassword123!",
                "full_name": "Verify Me",
            },
        )

    assert captured_token is not None

    # Call POST /verify-email
    verify_res = await async_client.post(
        "/api/v1/auth/verify-email",
        json={"token": captured_token},
    )
    assert verify_res.status_code == 200
    assert "verified successfully" in verify_res.json()["message"].lower()

    # Verify user can now log in
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": "ValidPassword123!"},
    )
    assert login_res.status_code == 200
    assert "access_token" in login_res.json()

    # Calling verify again reports already verified
    again_res = await async_client.post(
        "/api/v1/auth/verify-email",
        json={"token": captured_token},
    )
    assert again_res.status_code == 200
    assert "already verified" in again_res.json()["message"].lower()


@pytest.mark.asyncio
async def test_verify_email_get_flow(async_client: AsyncClient, db_session: AsyncSession):
    """Verify email verification works via GET endpoint for direct email clicks."""
    test_email = f"getverify_{uuid.uuid4().hex[:8]}@amu.edu"
    captured_token = None

    async def fake_send(email, full_name, token, *args, **kwargs):
        nonlocal captured_token
        captured_token = token
        return True

    with patch.object(EmailService, "send_verification_email", side_effect=fake_send):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "ValidPassword123!",
                "full_name": "Get Verifier",
            },
        )

    get_res = await async_client.get(f"/api/v1/auth/verify-email?token={captured_token}")
    assert get_res.status_code == 200
    assert "verified successfully" in get_res.json()["message"].lower()


@pytest.mark.asyncio
async def test_verify_email_invalid_token(async_client: AsyncClient):
    """Verify bad token produces 400 Bad Request."""
    res = await async_client.post(
        "/api/v1/auth/verify-email",
        json={"token": "totally.invalid.token"},
    )
    assert res.status_code == 400
    assert "invalid or expired" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_resend_verification_email(async_client: AsyncClient, db_session: AsyncSession):
    """Verify resending verification link for unverified account."""
    test_email = f"resend_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "ValidPassword123!",
                "full_name": "Resend Tester",
            },
        )

    with patch.object(EmailService, "send_verification_email", return_value=True) as mock_resend:
        res = await async_client.post(
            "/api/v1/auth/resend-verification",
            json={"email": test_email},
        )
        assert res.status_code == 200
        mock_resend.assert_called_once()


@pytest.mark.asyncio
async def test_forgot_and_reset_password_flow(async_client: AsyncClient, db_session: AsyncSession):
    """Verify forgot-password emails a reset OTP and reset-password updates credentials."""
    user = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert user is not None

    async def capture_otp():
        captured = {}

        async def fake_send_otp(email, full_name, otp_code, expire_minutes=None):
            captured["otp"] = otp_code
            return True

        return captured, fake_send_otp

    captured, fake_send_otp = await capture_otp()
    with patch.object(EmailService, "send_otp_email", side_effect=fake_send_otp) as mock_reset:
        forgot_res = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "student@amu.edu"},
        )
        assert forgot_res.status_code == 200
        assert "code has been sent" in forgot_res.json()["message"].lower()
        mock_reset.assert_called_once()

    reset_otp = captured.get("otp")
    assert reset_otp is not None and reset_otp.isdigit() and len(reset_otp) == 6

    # Reset password with the valid OTP
    new_password = "BrandNewSecurePassword2026!"
    with patch.object(
        EmailService, "send_password_reset_confirmation", return_value=True
    ) as mock_confirm:
        reset_res = await async_client.post(
            "/api/v1/auth/reset-password",
            json={"email": "student@amu.edu", "otp": reset_otp, "new_password": new_password},
        )
        assert reset_res.status_code == 200
        assert "reset successfully" in reset_res.json()["message"].lower()
        mock_confirm.assert_called_once()

    # Login with old password must fail
    old_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "amu_demo_2026!"},
    )
    assert old_login.status_code == 401

    # Login with new password must succeed
    new_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": new_password},
    )
    assert new_login.status_code == 200
    assert "access_token" in new_login.json()

    # Restore original password via a fresh reset OTP for test consistency
    restore, fake_restore = await capture_otp()
    with patch.object(EmailService, "send_otp_email", side_effect=fake_restore):
        await async_client.post("/api/v1/auth/forgot-password", json={"email": "student@amu.edu"})
    with patch.object(EmailService, "send_password_reset_confirmation", return_value=True):
        await async_client.post(
            "/api/v1/auth/reset-password",
            json={
                "email": "student@amu.edu",
                "otp": restore.get("otp"),
                "new_password": "amu_demo_2026!",
            },
        )


@pytest.mark.asyncio
async def test_reset_password_invalid_code_or_password(async_client: AsyncClient):
    """Verify validation on the reset OTP and password constraints."""
    # A wrong (but well-formed) code for an active user is rejected by business logic
    bad_res = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"email": "student@amu.edu", "otp": "000000", "new_password": "ValidPassword123!"},
    )
    assert bad_res.status_code == 400

    # Short password is rejected by schema validation
    short_res = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"email": "student@amu.edu", "otp": "123456", "new_password": "short"},
    )
    assert short_res.status_code == 422

    # Malformed (non 6-digit) OTP is rejected by schema validation
    malformed_res = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"email": "student@amu.edu", "otp": "abc", "new_password": "ValidPassword123!"},
    )
    assert malformed_res.status_code == 422


@pytest.mark.asyncio
async def test_email_service_smtp_dispatch():
    """Verify EmailService handles SMTP operations and error trapping cleanly."""
    with patch("smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        success = await EmailService.send_email_async(
            to_email="test@example.com",
            subject="Test Subject",
            html_content="<p>Test Content</p>",
        )
        assert success is True
        mock_server.starttls.assert_called_once()
        mock_server.sendmail.assert_called_once()
