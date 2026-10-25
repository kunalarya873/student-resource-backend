import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import EmailOTP, User
from app.services.email_service import EmailService


@pytest.mark.asyncio
async def test_signup_generates_otp_and_sends_email(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify registration creates unverified user, generates 6-digit OTP, and dispatches email."""
    test_email = f"otp_student_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True) as mock_send:
        res = await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "OTP Test Student",
                "role_name": "Student",
                "department_id": 1,
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["email"] == test_email
        assert data["status"] == "PENDING_VERIFICATION"
        assert "otp" in data["message"].lower()

        # Verify DB records
        user = await db_session.scalar(select(User).where(User.email == test_email))
        assert user is not None
        assert user.status == "PENDING_VERIFICATION"

        # Verify EmailOTP record exists in database
        otp_record = await db_session.scalar(
            select(EmailOTP).where(EmailOTP.user_id == user.user_id, EmailOTP.is_used.is_(False))
        )
        assert otp_record is not None
        assert otp_record.attempts == 0
        assert otp_record.max_attempts == 5

        # Verify email dispatch received OTP code
        mock_send.assert_called_once()
        _args, kwargs = mock_send.call_args
        assert kwargs["email"] == test_email
        assert kwargs["otp_code"] is not None
        assert len(kwargs["otp_code"]) == 6
        assert kwargs["otp_code"].isdigit()


@pytest.mark.asyncio
async def test_verify_otp_success(async_client: AsyncClient, db_session: AsyncSession):
    """Verify submitting correct 6-digit OTP activates user account and marks OTP used."""
    test_email = f"verify_{uuid.uuid4().hex[:8]}@amu.edu"

    # Step 1: Sign up
    with patch.object(EmailService, "send_verification_email", return_value=True) as mock_send:
        res = await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Verification Candidate",
                "role_name": "Student",
                "department_id": 1,
            },
        )
        assert res.status_code == 201
        mock_send.assert_called_once()
        sent_otp = mock_send.call_args.kwargs["otp_code"]

    # Step 2: Verify OTP
    res_verify = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": sent_otp},
    )
    assert res_verify.status_code == 200
    assert "verified successfully" in res_verify.json()["message"].lower()

    # Step 3: Check DB status is ACTIVE
    user = await db_session.scalar(select(User).where(User.email == test_email))
    assert user is not None
    assert user.status == "ACTIVE"

    # Check OTP record is now used
    otp_record = await db_session.scalar(select(EmailOTP).where(EmailOTP.user_id == user.user_id))
    assert otp_record is not None
    assert otp_record.is_used is True


@pytest.mark.asyncio
async def test_verify_otp_already_active_user(async_client: AsyncClient, db_session: AsyncSession):
    """Verify submitting OTP for already active user returns informative success message."""
    test_email = "student@amu.edu"  # Seeded active user

    res = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": "123456"},
    )
    assert res.status_code == 200
    assert "already verified" in res.json()["message"].lower()


@pytest.mark.asyncio
async def test_verify_otp_invalid_code_decrements_attempts(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify incorrect OTP code increments attempt counter and informs user of remaining attempts."""
    test_email = f"wrong_otp_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Wrong OTP Student",
                "role_name": "Student",
                "department_id": 1,
            },
        )

    # Submit bad OTP
    res = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": "000000"},
    )
    assert res.status_code == 400
    assert "4 attempt(s) remaining" in res.json()["detail"]


@pytest.mark.asyncio
async def test_verify_otp_max_attempts_exceeded(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify 5 failed attempts locks OTP code permanently."""
    test_email = f"bruteforce_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Brute Force Candidate",
                "role_name": "Student",
                "department_id": 1,
            },
        )

    # 4 attempts
    for _ in range(4):
        res = await async_client.post(
            "/api/v1/auth/verify-otp",
            json={"email": test_email, "otp": "999999"},
        )
        assert res.status_code == 400

    # 5th attempt should lock it
    res5 = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": "999999"},
    )
    assert res5.status_code == 400
    assert "maximum attempts exceeded" in res5.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_otp_expired(async_client: AsyncClient, db_session: AsyncSession):
    """Verify expired OTP code is rejected."""
    test_email = f"expired_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Expired Candidate",
                "role_name": "Student",
                "department_id": 1,
            },
        )

    user = await db_session.scalar(select(User).where(User.email == test_email))
    assert user is not None

    # Manually expire the OTP in DB
    otp_record = await db_session.scalar(
        select(EmailOTP).where(EmailOTP.user_id == user.user_id, EmailOTP.is_used.is_(False))
    )
    assert otp_record is not None
    otp_record.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.commit()

    res = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": "123456"},
    )
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_otp_malformed_pattern_rejected(async_client: AsyncClient):
    """Verify non-numeric or length != 6 OTP payloads are rejected by Pydantic schema."""
    # 5 digits
    res1 = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": "test@amu.edu", "otp": "12345"},
    )
    assert res1.status_code == 422

    # letters
    res2 = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": "test@amu.edu", "otp": "abcdef"},
    )
    assert res2.status_code == 422


@pytest.mark.asyncio
async def test_resend_otp_rate_limiting_cooldown(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify attempting to resend OTP within 60s cooldown triggers 429 Too Many Requests."""
    test_email = f"cooldown_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Cooldown Candidate",
                "role_name": "Student",
                "department_id": 1,
            },
        )

    # Immediately request resend
    res = await async_client.post(
        "/api/v1/auth/resend-otp",
        json={"email": test_email},
    )
    assert res.status_code == 429
    assert "seconds before requesting a new otp" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_resend_otp_success_after_cooldown(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify resending OTP succeeds after cooldown window and sends fresh code."""
    test_email = f"resend_ok_{uuid.uuid4().hex[:8]}@amu.edu"

    with patch.object(EmailService, "send_verification_email", return_value=True):
        await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Resend Candidate",
                "role_name": "Student",
                "department_id": 1,
            },
        )

    user = await db_session.scalar(select(User).where(User.email == test_email))
    assert user is not None

    # Simulate 65 seconds passed
    otp_record = await db_session.scalar(
        select(EmailOTP).where(EmailOTP.user_id == user.user_id, EmailOTP.is_used.is_(False))
    )
    assert otp_record is not None
    otp_record.created_at = datetime.now(UTC) - timedelta(seconds=65)
    await db_session.commit()

    with patch.object(EmailService, "send_otp_email", return_value=True) as mock_otp_send:
        res = await async_client.post(
            "/api/v1/auth/resend-otp",
            json={"email": test_email},
        )
        assert res.status_code == 200
        assert "new otp code has been sent" in res.json()["message"].lower()

        # Check email sent
        mock_otp_send.assert_called_once()
        sent_otp = mock_otp_send.call_args.kwargs["otp_code"]
        assert len(sent_otp) == 6

    # Verify that the newly sent OTP works to verify the account
    res_verify = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": sent_otp},
    )
    assert res_verify.status_code == 200
    assert "verified successfully" in res_verify.json()["message"].lower()


@pytest.mark.asyncio
async def test_resend_otp_anti_enumeration(async_client: AsyncClient):
    """Verify non-existent email returns generic message without revealing account existence."""
    res = await async_client.post(
        "/api/v1/auth/resend-otp",
        json={"email": "nonexistent_person_12345@amu.edu"},
    )
    assert res.status_code == 200
    assert "if your account is pending verification" in res.json()["message"].lower()


@pytest.mark.asyncio
async def test_verify_otp_returns_tokens_and_allows_authenticated_request(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify that verifying OTP directly issues JWT tokens so user does not need to login again."""
    test_email = f"autologin_{uuid.uuid4().hex[:8]}@amu.edu"
    sent_otp = None

    async def fake_verification_email(email, full_name, token, otp_code, *args, **kwargs):
        nonlocal sent_otp
        sent_otp = otp_code
        return True

    with patch.object(EmailService, "send_verification_email", side_effect=fake_verification_email):
        signup_res = await async_client.post(
            "/api/v1/auth/signup",
            json={
                "email": test_email,
                "password": "StrongPassword123!",
                "full_name": "Auto Login User",
            },
        )
        assert signup_res.status_code == 201

    assert sent_otp is not None

    # Verify OTP
    verify_res = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"email": test_email, "otp": sent_otp},
    )
    assert verify_res.status_code == 200
    data = verify_res.json()
    assert "access_token" in data
    assert data["access_token"] is not None
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "verified successfully" in data["message"].lower()

    # Verify that the issued access_token allows immediate access without logging in again
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me_res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["email"] == test_email
    assert me_res.json()["full_name"] == "Auto Login User"
