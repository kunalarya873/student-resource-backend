import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class StorageQuotaSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    quota_bytes: int
    used_bytes: int
    reserved_bytes: int
    available_bytes: int


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    role_name: str
    department_id: int | None = None
    department_name: str | None = None
    status: str
    quota: StorageQuotaSummary | None = None


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    department_id: int | None = None
    role_name: str = "Student"


class SignupResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    status: str
    message: str


class VerifyResponse(BaseModel):
    message: str
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    user_id: uuid.UUID | None = None
    email: EmailStr | None = None
    full_name: str | None = None
    role_name: str | None = None


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric password reset code",
        examples=["482910"],
    )
    new_password: str = Field(..., min_length=8)


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric verification code",
        examples=["482910"],
    )


class ResendOtpRequest(BaseModel):
    email: EmailStr
