import asyncio
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


def get_password_hash(password: str) -> str:
    """Hash password using direct bcrypt with salt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


async def get_password_hash_async(password: str) -> str:
    """Non-blocking async bcrypt password hashing offloaded to thread pool."""
    return await asyncio.to_thread(get_password_hash, password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Non-blocking async bcrypt password verification offloaded to thread pool."""
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)


def create_access_token(
    subject: str | Any, role: str, expires_delta: timedelta | None = None
) -> str:
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(subject), "role": role, "type": "access", "exp": expire}
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str | Any, expires_delta: timedelta | None = None) -> str:
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {"sub": str(subject), "type": "refresh", "exp": expire}
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc


def create_email_verification_token(
    subject: str | Any, email: str, expires_delta: timedelta | None = None
) -> str:
    """Generate signed JWT token for email verification."""
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS)
    to_encode = {
        "sub": str(subject),
        "email": email.strip().lower(),
        "type": "email_verification",
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_email_verification_token(token: str) -> dict[str, Any]:
    """Validate and decode email verification token."""
    payload = decode_token(token)
    if payload.get("type") != "email_verification":
        raise ValueError("Invalid token type for email verification")
    return payload


def create_password_reset_token(
    subject: str | Any, email: str, expires_delta: timedelta | None = None
) -> str:
    """Generate signed JWT token for password reset."""
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(subject),
        "email": email.strip().lower(),
        "type": "password_reset",
        "exp": expire,
    }
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_password_reset_token(token: str) -> dict[str, Any]:
    """Validate and decode password reset token."""
    payload = decode_token(token)
    if payload.get("type") != "password_reset":
        raise ValueError("Invalid token type for password reset")
    return payload


def generate_numeric_otp(length: int = 6) -> str:
    """Generate cryptographically secure numeric OTP of fixed length."""
    import secrets

    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp(otp: str) -> str:
    """Hash OTP using HMAC-SHA256 with JWT_SECRET_KEY as salt."""
    import hashlib
    import hmac

    key = (settings.JWT_SECRET_KEY or "fallback-secret-for-testing").encode("utf-8")
    return hmac.new(key, otp.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def verify_otp_hash(plain_otp: str, hashed_otp: str) -> bool:
    """Constant-time comparison of plain OTP against its HMAC-SHA256 hash."""
    import hmac

    expected = hash_otp(plain_otp)
    return hmac.compare_digest(expected, hashed_otp)
