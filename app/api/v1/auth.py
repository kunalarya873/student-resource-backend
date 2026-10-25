from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    StorageQuotaSummary,
    TokenResponse,
    UserProfileResponse,
    VerifyEmailRequest,
    VerifyOtpRequest,
    VerifyResponse,
)
from app.schemas.common import StandardMessageResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new student or teacher account and send verification email",
)
async def signup(
    payload: SignupRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> SignupResponse:
    return await AuthService.register_user(session, payload, background_tasks=background_tasks)


@router.post(
    "/verify-otp",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify user email address using a 6-digit numeric OTP code and issue tokens",
)
async def verify_otp(
    payload: VerifyOtpRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> VerifyResponse:
    return await AuthService.verify_otp(session, payload.email, payload.otp)


@router.post(
    "/resend-otp",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend a fresh 6-digit OTP code to pending unverified accounts with cooldown",
)
async def resend_otp(
    payload: ResendOtpRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> StandardMessageResponse:
    return await AuthService.resend_otp(session, payload.email, background_tasks=background_tasks)


@router.post(
    "/verify-email",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify user email address using token body and issue tokens",
)
async def verify_email_post(
    payload: VerifyEmailRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> VerifyResponse:
    return await AuthService.verify_email(session, payload.token)


@router.get(
    "/verify-email",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify email address via direct browser/email link and issue tokens",
)
async def verify_email_get(
    token: Annotated[str, Query(description="Email verification token")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> VerifyResponse:
    return await AuthService.verify_email(session, token)


@router.post(
    "/resend-verification",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend activation email to pending unverified accounts",
)
async def resend_verification(
    payload: ResendVerificationRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> StandardMessageResponse:
    return await AuthService.resend_verification(
        session, payload.email, background_tasks=background_tasks
    )


@router.post(
    "/forgot-password",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password reset email",
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> StandardMessageResponse:
    return await AuthService.request_password_reset(
        session, payload.email, background_tasks=background_tasks
    )


@router.post(
    "/reset-password",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset password using the 6-digit OTP code sent to the user's email",
)
async def reset_password(
    payload: ResetPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> StandardMessageResponse:
    return await AuthService.reset_password(
        session,
        payload.email,
        payload.otp,
        payload.new_password,
        background_tasks=background_tasks,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and issue JWT tokens",
)
async def login(
    payload: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    user = await AuthService.authenticate_user(session, payload.email, payload.password)
    return AuthService.issue_tokens(user)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Issue new access token using a valid refresh token",
)
async def refresh_token(
    payload: RefreshTokenRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    return await AuthService.rotate_refresh_token(session, payload.refresh_token)


@router.post(
    "/logout",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Logout user and invalidate session",
)
async def logout(
    current_user: Annotated[User, Depends(get_current_user)],
) -> StandardMessageResponse:
    return StandardMessageResponse(message="Logged out successfully")


@router.get(
    "/me",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile, role, and storage quota",
)
async def get_my_profile(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserProfileResponse:
    quota_summary = None
    if current_user.storage_quota:
        q = current_user.storage_quota
        avail = max(0, q.quota_bytes - (q.used_bytes + q.reserved_bytes))
        quota_summary = StorageQuotaSummary(
            quota_bytes=q.quota_bytes,
            used_bytes=q.used_bytes,
            reserved_bytes=q.reserved_bytes,
            available_bytes=avail,
        )

    return UserProfileResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        full_name=current_user.full_name,
        role_name=current_user.role.name,
        department_id=current_user.department_id,
        department_name=current_user.department.name if current_user.department else None,
        status=current_user.status,
        quota=quota_summary,
    )
