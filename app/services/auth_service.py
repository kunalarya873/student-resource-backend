import uuid
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_email_verification_token,
    create_refresh_token,
    decode_email_verification_token,
    decode_token,
    generate_numeric_otp,
    get_password_hash_async,
    hash_otp,
    verify_otp_hash,
    verify_password_async,
)
from app.models.storage import StorageQuota
from app.models.user import Department, EmailOTP, Role, User
from app.schemas.auth import (
    SignupRequest,
    SignupResponse,
    TokenResponse,
    VerifyResponse,
)
from app.schemas.common import StandardMessageResponse
from app.services.email_service import EmailService

# Dummy hash for timing attack mitigation on unknown user lookups
DUMMY_BCRYPT_HASH = "$2b$12$e8rO8vM182g262D/d1K/Z.vNlO2vE9rF0p8Zz2g4K1h3g5k7m9q1s"


class AuthService:
    @classmethod
    async def authenticate_user(cls, session: AsyncSession, email: str, password: str) -> User:
        stmt = (
            select(User)
            .options(
                selectinload(User.role),
                selectinload(User.department),
                selectinload(User.storage_quota),
            )
            .where(User.email == email.strip().lower())
        )
        user = await session.scalar(stmt)

        if not user:
            # Run dummy verification to keep constant-time response (prevent timing analysis) without stalling event loop
            await verify_password_async(password, DUMMY_BCRYPT_HASH)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not await verify_password_async(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.status != "ACTIVE":
            if user.status == "PENDING_VERIFICATION":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Please verify your email address before logging in",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is suspended or inactive",
            )

        return user

    @classmethod
    def issue_tokens(cls, user: User) -> TokenResponse:
        access_token = create_access_token(
            subject=user.user_id,
            role=user.role.name,
        )
        refresh_token = create_refresh_token(subject=user.user_id)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    @classmethod
    async def rotate_refresh_token(
        cls, session: AsyncSession, refresh_token_str: str
    ) -> TokenResponse:
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
        try:
            payload = decode_token(refresh_token_str)
            if payload.get("type") != "refresh":
                raise credentials_exception
            user_id_str = payload.get("sub")
            if not user_id_str:
                raise credentials_exception
            user_id = uuid.UUID(user_id_str)
        except Exception as exc:
            raise credentials_exception from exc

        stmt = select(User).options(selectinload(User.role)).where(User.user_id == user_id)
        user = await session.scalar(stmt)
        if not user or user.status != "ACTIVE":
            raise credentials_exception

        return cls.issue_tokens(user)

    @classmethod
    async def register_user(
        cls,
        session: AsyncSession,
        payload: SignupRequest,
        background_tasks: BackgroundTasks | None = None,
    ) -> SignupResponse:
        """Register a new student or teacher account in PENDING_VERIFICATION status with quota."""
        clean_email = payload.email.strip().lower()

        # Check for existing account
        existing_user = await session.scalar(select(User).where(User.email == clean_email))
        if existing_user:
            if existing_user.status == "PENDING_VERIFICATION":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this email is pending verification. Please check your inbox or request a new verification email.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            )

        # Validate password strength
        if len(payload.password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters long",
            )

        # Resolve role (default to Student, prevent Administrator self-assignment)
        target_role_name = (payload.role_name or "Student").strip()
        role = await session.scalar(
            select(Role).where(func.lower(Role.name) == target_role_name.lower())
        )
        if not role:
            # Fallback to Student
            role = await session.scalar(select(Role).where(Role.role_id == 3))
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Default Student role is not configured",
                )

        if role.name.lower() == "administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Self-registration as Administrator is not permitted",
            )

        # Validate department if provided
        if payload.department_id is not None:
            dept = await session.scalar(
                select(Department).where(Department.department_id == payload.department_id)
            )
            if not dept:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Selected department does not exist",
                )

        # Hash password asynchronously
        password_hash = await get_password_hash_async(payload.password)

        # Create user record (GDPR compliance Rule 02: consent version & timestamp recorded)
        user = User(
            email=clean_email,
            full_name=payload.full_name.strip(),
            password_hash=password_hash,
            role_id=role.role_id,
            department_id=payload.department_id,
            status="PENDING_VERIFICATION",
            consent_version="1.0",
            consent_given_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()

        # Allocate default quota based on role
        quota_bytes = (
            settings.TEACHER_DEFAULT_QUOTA_BYTES
            if role.name.lower() == "teacher"
            else settings.STUDENT_DEFAULT_QUOTA_BYTES
        )
        session.add(
            StorageQuota(
                user_id=user.user_id,
                quota_bytes=quota_bytes,
                used_bytes=0,
                reserved_bytes=0,
            )
        )

        # Generate cryptographic 6-digit OTP & JWT token
        otp_code = generate_numeric_otp(6)
        otp_record = EmailOTP(
            user_id=user.user_id,
            email=user.email,
            otp_hash=hash_otp(otp_code),
            purpose="EMAIL_VERIFICATION",
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
            max_attempts=settings.OTP_MAX_ATTEMPTS,
        )
        session.add(otp_record)
        await session.commit()

        token = create_email_verification_token(user.user_id, user.email)
        if background_tasks is not None:
            background_tasks.add_task(
                EmailService.send_verification_email,
                email=user.email,
                full_name=user.full_name,
                token=token,
                otp_code=otp_code,
            )
        else:
            await EmailService.send_verification_email(
                email=user.email, full_name=user.full_name, token=token, otp_code=otp_code
            )

        return SignupResponse(
            user_id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            status=user.status,
            message="Registration successful. A verification link and OTP have been sent to your email.",
        )

    @classmethod
    async def verify_email(cls, session: AsyncSession, token: str) -> VerifyResponse:
        """Validate email verification token, transition account to ACTIVE status, and issue JWT tokens."""
        try:
            payload = decode_email_verification_token(token)
            user_id = uuid.UUID(payload["sub"])
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token",
            ) from exc

        stmt = select(User).options(selectinload(User.role)).where(User.user_id == user_id)
        user = await session.scalar(stmt)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User associated with token not found",
            )

        if user.status == "ACTIVE":
            tokens = cls.issue_tokens(user)
            return VerifyResponse(
                message="Email is already verified. You can now access your account.",
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                token_type="bearer",
                user_id=user.user_id,
                email=user.email,
                full_name=user.full_name,
                role_name=user.role.name if user.role else "Student",
            )

        user.status = "ACTIVE"
        # Invalidate any pending OTPs for this user
        await session.execute(
            update(EmailOTP)
            .where(
                EmailOTP.user_id == user.user_id,
                EmailOTP.purpose == "EMAIL_VERIFICATION",
                EmailOTP.is_used.is_(False),
            )
            .values(is_used=True)
        )
        await session.commit()

        tokens = cls.issue_tokens(user)
        return VerifyResponse(
            message="Email verified successfully. Your account is now active and you are logged in.",
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_type="bearer",
            user_id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            role_name=user.role.name if user.role else "Student",
        )

    @classmethod
    async def verify_otp(cls, session: AsyncSession, email: str, otp: str) -> VerifyResponse:
        """Validate 6-digit OTP code, check attempts & expiry, transition user to ACTIVE status, and issue JWT tokens."""
        clean_email = email.strip().lower()
        clean_otp = otp.strip()

        # Retrieve user with role for token issuance
        stmt = select(User).options(selectinload(User.role)).where(User.email == clean_email)
        user = await session.scalar(stmt)
        if not user:
            # Timing attack mitigation
            await verify_password_async("dummy", DUMMY_BCRYPT_HASH)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email or verification code",
            )

        if user.status == "ACTIVE":
            tokens = cls.issue_tokens(user)
            return VerifyResponse(
                message="Email is already verified. You can now access your account.",
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                token_type="bearer",
                user_id=user.user_id,
                email=user.email,
                full_name=user.full_name,
                role_name=user.role.name if user.role else "Student",
            )

        if user.status != "PENDING_VERIFICATION":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Account cannot be verified in current status: {user.status}",
            )

        # Retrieve latest active OTP for this user
        now = datetime.now(UTC)
        otp_stmt = (
            select(EmailOTP)
            .where(
                EmailOTP.user_id == user.user_id,
                EmailOTP.purpose == "EMAIL_VERIFICATION",
                EmailOTP.is_used.is_(False),
            )
            .order_by(EmailOTP.created_at.desc())
            .limit(1)
        )
        otp_record = await session.scalar(otp_stmt)

        if not otp_record or otp_record.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired or is invalid. Please request a new code.",
            )

        if otp_record.attempts >= otp_record.max_attempts:
            otp_record.is_used = True
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts exceeded. Please request a new OTP code.",
            )

        if not verify_otp_hash(clean_otp, otp_record.otp_hash):
            otp_record.attempts += 1
            remaining = otp_record.max_attempts - otp_record.attempts
            if remaining <= 0:
                otp_record.is_used = True
                await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification code. Maximum attempts exceeded. Please request a new code.",
                )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid verification code. {remaining} attempt(s) remaining.",
            )

        # Mark OTP as used and activate user
        otp_record.is_used = True
        user.status = "ACTIVE"
        await session.commit()

        tokens = cls.issue_tokens(user)
        return VerifyResponse(
            message="Email verified successfully. Your account is now active and you are logged in.",
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_type="bearer",
            user_id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            role_name=user.role.name if user.role else "Student",
        )

    @classmethod
    async def resend_otp(
        cls,
        session: AsyncSession,
        email: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> StandardMessageResponse:
        """Resend a fresh 6-digit OTP code to pending unverified accounts with cooldown rate-limiting."""
        clean_email = email.strip().lower()
        stmt = select(User).where(User.email == clean_email)
        user = await session.scalar(stmt)

        if not user or user.status != "PENDING_VERIFICATION":
            await verify_password_async("dummy", DUMMY_BCRYPT_HASH)
            return StandardMessageResponse(
                message="If your account is pending verification, a new OTP code has been sent to your email."
            )

        now = datetime.now(UTC)

        # Check rate-limit cooldown against most recently created OTP
        last_otp_stmt = (
            select(EmailOTP)
            .where(
                EmailOTP.user_id == user.user_id,
                EmailOTP.purpose == "EMAIL_VERIFICATION",
            )
            .order_by(EmailOTP.created_at.desc())
            .limit(1)
        )
        last_otp = await session.scalar(last_otp_stmt)

        if last_otp and last_otp.created_at:
            elapsed = (now - last_otp.created_at).total_seconds()
            if elapsed < settings.OTP_RESEND_COOLDOWN_SECONDS:
                remaining_cooldown = int(settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Please wait {remaining_cooldown} seconds before requesting a new OTP code.",
                )

        # Invalidate previous unconsumed OTPs for this user & purpose
        await session.execute(
            update(EmailOTP)
            .where(
                EmailOTP.user_id == user.user_id,
                EmailOTP.purpose == "EMAIL_VERIFICATION",
                EmailOTP.is_used.is_(False),
            )
            .values(is_used=True)
        )

        # Generate fresh OTP
        new_otp_code = generate_numeric_otp(6)
        new_otp_record = EmailOTP(
            user_id=user.user_id,
            email=user.email,
            otp_hash=hash_otp(new_otp_code),
            purpose="EMAIL_VERIFICATION",
            expires_at=now + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
            max_attempts=settings.OTP_MAX_ATTEMPTS,
        )
        session.add(new_otp_record)
        await session.commit()

        # Send email with OTP
        if background_tasks is not None:
            background_tasks.add_task(
                EmailService.send_otp_email,
                email=user.email,
                full_name=user.full_name,
                otp_code=new_otp_code,
                expire_minutes=settings.OTP_EXPIRE_MINUTES,
            )
        else:
            await EmailService.send_otp_email(
                email=user.email,
                full_name=user.full_name,
                otp_code=new_otp_code,
                expire_minutes=settings.OTP_EXPIRE_MINUTES,
            )

        return StandardMessageResponse(
            message="If your account is pending verification, a new OTP code has been sent to your email."
        )

    @classmethod
    async def resend_verification(
        cls,
        session: AsyncSession,
        email: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> StandardMessageResponse:
        """Resend activation email if account exists and is pending verification."""
        clean_email = email.strip().lower()
        stmt = select(User).where(User.email == clean_email)
        user = await session.scalar(stmt)

        if user and user.status == "PENDING_VERIFICATION":
            token = create_email_verification_token(user.user_id, user.email)
            otp_code = generate_numeric_otp(6)
            otp_record = EmailOTP(
                user_id=user.user_id,
                email=user.email,
                otp_hash=hash_otp(otp_code),
                purpose="EMAIL_VERIFICATION",
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
                max_attempts=settings.OTP_MAX_ATTEMPTS,
            )
            session.add(otp_record)
            await session.commit()

            if background_tasks is not None:
                background_tasks.add_task(
                    EmailService.send_verification_email,
                    email=user.email,
                    full_name=user.full_name,
                    token=token,
                    otp_code=otp_code,
                )
            else:
                await EmailService.send_verification_email(
                    email=user.email,
                    full_name=user.full_name,
                    token=token,
                    otp_code=otp_code,
                )
        else:
            # Timing mitigation for non-existent or active users
            await verify_password_async("dummy", DUMMY_BCRYPT_HASH)

        return StandardMessageResponse(
            message="If your account is pending verification, a new confirmation link has been sent to your email."
        )

    @classmethod
    async def request_password_reset(
        cls,
        session: AsyncSession,
        email: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> StandardMessageResponse:
        """Generate a 6-digit password reset OTP and dispatch it by email (anti-enumeration safe)."""
        clean_email = email.strip().lower()
        stmt = select(User).where(User.email == clean_email)
        user = await session.scalar(stmt)

        if user and user.status == "ACTIVE":
            now = datetime.now(UTC)
            # Invalidate any previous unconsumed reset codes so only the newest is valid
            await session.execute(
                update(EmailOTP)
                .where(
                    EmailOTP.user_id == user.user_id,
                    EmailOTP.purpose == "PASSWORD_RESET",
                    EmailOTP.is_used.is_(False),
                )
                .values(is_used=True)
            )

            otp_code = generate_numeric_otp(6)
            otp_record = EmailOTP(
                user_id=user.user_id,
                email=user.email,
                otp_hash=hash_otp(otp_code),
                purpose="PASSWORD_RESET",
                expires_at=now + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
                max_attempts=settings.OTP_MAX_ATTEMPTS,
            )
            session.add(otp_record)
            await session.commit()

            if background_tasks is not None:
                background_tasks.add_task(
                    EmailService.send_otp_email,
                    email=user.email,
                    full_name=user.full_name,
                    otp_code=otp_code,
                    expire_minutes=settings.OTP_EXPIRE_MINUTES,
                )
            else:
                await EmailService.send_otp_email(
                    email=user.email,
                    full_name=user.full_name,
                    otp_code=otp_code,
                    expire_minutes=settings.OTP_EXPIRE_MINUTES,
                )
        else:
            # Constant-time mitigation to prevent email enumeration
            await verify_password_async("dummy", DUMMY_BCRYPT_HASH)

        return StandardMessageResponse(
            message="If an account with that email exists, a password reset code has been sent."
        )

    @classmethod
    async def reset_password(
        cls,
        session: AsyncSession,
        email: str,
        otp: str,
        new_password: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> StandardMessageResponse:
        """Verify the password reset OTP and update the user's password hash."""
        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be at least 8 characters long",
            )

        clean_email = email.strip().lower()
        clean_otp = otp.strip()

        stmt = select(User).where(User.email == clean_email)
        user = await session.scalar(stmt)
        if not user or user.status != "ACTIVE":
            # Timing-attack / enumeration mitigation
            await verify_password_async("dummy", DUMMY_BCRYPT_HASH)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email or reset code",
            )

        now = datetime.now(UTC)
        otp_stmt = (
            select(EmailOTP)
            .where(
                EmailOTP.user_id == user.user_id,
                EmailOTP.purpose == "PASSWORD_RESET",
                EmailOTP.is_used.is_(False),
            )
            .order_by(EmailOTP.created_at.desc())
            .limit(1)
        )
        otp_record = await session.scalar(otp_stmt)

        if not otp_record or otp_record.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset code has expired or is invalid. Please request a new code.",
            )

        if otp_record.attempts >= otp_record.max_attempts:
            otp_record.is_used = True
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum reset attempts exceeded. Please request a new code.",
            )

        if not verify_otp_hash(clean_otp, otp_record.otp_hash):
            otp_record.attempts += 1
            remaining = otp_record.max_attempts - otp_record.attempts
            if remaining <= 0:
                otp_record.is_used = True
                await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid reset code. Maximum attempts exceeded. Please request a new code.",
                )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid reset code. {remaining} attempt(s) remaining.",
            )

        otp_record.is_used = True
        user.password_hash = await get_password_hash_async(new_password)
        await session.commit()

        # Send confirmation email
        if background_tasks is not None:
            background_tasks.add_task(
                EmailService.send_password_reset_confirmation,
                email=user.email,
                full_name=user.full_name,
            )
        else:
            await EmailService.send_password_reset_confirmation(
                email=user.email, full_name=user.full_name
            )

        return StandardMessageResponse(
            message="Password has been reset successfully. You can now log in with your new password."
        )
