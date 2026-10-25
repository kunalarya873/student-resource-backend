import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from app.core.config import settings

logger = logging.getLogger("amu_backend.email")


class EmailService:
    @classmethod
    def _send_smtp_sync(
        cls,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str,
    ) -> bool:
        """Synchronous SMTP worker function executed in worker thread."""
        sender_email = settings.MAIL_FROM or settings.EMAIL_HOST_USER
        sender_name = settings.MAIL_FROM_NAME or "AMU Support"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((sender_name, sender_email))
        msg["To"] = to_email

        # Attach text and html alternatives
        part_text = MIMEText(text_content, "plain", "utf-8")
        part_html = MIMEText(html_content, "html", "utf-8")
        msg.attach(part_text)
        msg.attach(part_html)

        # Normalize Gmail app password (remove spaces if any)
        clean_password = (settings.EMAIL_HOST_PASSWORD or "").replace(" ", "")

        try:
            with smtplib.SMTP(
                host=settings.EMAIL_SERVER, port=settings.EMAIL_PORT, timeout=20
            ) as server:
                if settings.EMAIL_USE_TLS:
                    server.starttls()
                if settings.EMAIL_HOST_USER and clean_password:
                    server.login(settings.EMAIL_HOST_USER, clean_password)
                server.sendmail(sender_email, [to_email], msg.as_string())
            logger.info(
                "Successfully dispatched email to %s with subject '%s'",
                to_email,
                subject,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            # Redact password or internal sensitive details from logs
            logger.warning(
                "Failed to send email to %s via %s:%s. Error: %s",
                to_email,
                settings.EMAIL_SERVER,
                settings.EMAIL_PORT,
                type(exc).__name__,
            )
            return False

    @classmethod
    async def send_email_async(
        cls,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
        raise_on_error: bool = False,
    ) -> bool:
        """Non-blocking asynchronous email delivery offloaded to thread pool."""
        plain_text = text_content or html_content
        result = await asyncio.to_thread(
            cls._send_smtp_sync, to_email, subject, html_content, plain_text
        )
        if not result and raise_on_error:
            raise RuntimeError(f"Failed to deliver email to {to_email}")
        return result

    @classmethod
    async def send_verification_email(
        cls, email: str, full_name: str, token: str, otp_code: str | None = None
    ) -> bool:
        """Send account activation email with secure verification link and optional 6-digit OTP."""
        verification_link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        api_verify_link = f"{settings.API_V1_PREFIX}/auth/verify-email?token={token}"

        subject = "Verify Your AMU Account - Student Resource Management System"

        otp_text_block = (
            f"\n\nYour One-Time Verification Code (OTP): {otp_code}\n(Valid for {settings.OTP_EXPIRE_MINUTES} minutes)\n"
            if otp_code
            else ""
        )

        text_content = f"""Hello {full_name},

Thank you for registering with the AMU Student Resource Management System.
{otp_text_block}
Please verify your email address by clicking the link below or copying it into your browser:
{verification_link}

(Alternatively, API direct verification: {api_verify_link})

This verification link will expire in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours.

If you did not register for this account, please ignore this email.

Best regards,
AMU Academic Technology Team
"""

        otp_html_block = (
            f"""
              <!-- OTP Box -->
              <div style="background-color: #f0f7ff; border: 2px dashed #2563eb; border-radius: 8px; padding: 20px; text-align: center; margin: 24px 0;">
                <span style="font-size: 13px; font-weight: 600; text-transform: uppercase; color: #1e40af; letter-spacing: 1px; display: block; margin-bottom: 8px;">
                  Your One-Time Verification Code (OTP)
                </span>
                <span style="font-family: 'Courier New', Courier, monospace; font-size: 36px; font-weight: 700; color: #1e3a8a; letter-spacing: 8px; display: inline-block;">
                  {otp_code}
                </span>
                <p style="color: #64748b; font-size: 13px; margin: 10px 0 0 0;">
                  Valid for <strong>{settings.OTP_EXPIRE_MINUTES} minutes</strong>. Enter this code on the verification screen.
                </p>
              </div>
            """
            if otp_code
            else ""
        )

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <table width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
          <!-- Header -->
          <tr>
            <td style="background-color: #1e3a8a; padding: 24px; text-align: center;">
              <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 600;">AMU Student Resource Management</h1>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 32px 28px;">
              <h2 style="color: #0f172a; margin-top: 0; font-size: 18px;">Welcome, {full_name}!</h2>
              <p style="color: #475569; font-size: 15px; line-height: 1.6;">
                Thank you for creating an account with the AMU Student Resource Management System. To complete your registration and activate your storage quota, please verify your email address using the OTP code below or by clicking the verification button.
              </p>
              {otp_html_block}
              <!-- Call to Action -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                <tr>
                  <td align="center">
                    <a href="{verification_link}" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; font-size: 15px; font-weight: 600; border-radius: 6px; display: inline-block;">
                      Verify Email Address
                    </a>
                  </td>
                </tr>
              </table>
              <p style="color: #64748b; font-size: 13px; line-height: 1.5;">
                Or copy and paste this verification URL into your browser:<br>
                <a href="{verification_link}" style="color: #2563eb; word-break: break-all;">{verification_link}</a>
              </p>
              <div style="background-color: #f1f5f9; border-left: 4px solid #3b82f6; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
                <p style="color: #334155; font-size: 13px; margin: 0;">
                  <strong>Note:</strong> The OTP code is valid for <strong>{settings.OTP_EXPIRE_MINUTES} minutes</strong> and the direct link for <strong>{settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS} hours</strong>.
                </p>
              </div>
              <p style="color: #94a3b8; font-size: 12px; margin-top: 24px;">
                If you did not sign up for this account, you can safely ignore this email.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background-color: #f8fafc; padding: 16px; text-align: center; border-top: 1px solid #e2e8f0;">
              <p style="color: #94a3b8; font-size: 12px; margin: 0;">
                &copy; 2026 Aligarh Muslim University &bull; Academic Technology Division
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        return await cls.send_email_async(email, subject, html_content, text_content)

    @classmethod
    async def send_otp_email(
        cls,
        email: str,
        full_name: str,
        otp_code: str,
        expire_minutes: int | None = None,
    ) -> bool:
        """Send verification email featuring 6-digit OTP code."""
        duration = expire_minutes or settings.OTP_EXPIRE_MINUTES
        subject = f"Your AMU Verification Code: {otp_code}"

        text_content = f"""Hello {full_name},

Your verification OTP code for the AMU Student Resource Management System is:

{otp_code}

This code will expire in {duration} minutes.

For security reasons, never share this code with anyone.

If you did not request this verification code, please ignore this email.

Best regards,
AMU Academic Technology Team
"""

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <table width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
          <!-- Header -->
          <tr>
            <td style="background-color: #1e3a8a; padding: 24px; text-align: center;">
              <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 600;">AMU Student Resource Management</h1>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 32px 28px;">
              <h2 style="color: #0f172a; margin-top: 0; font-size: 18px;">Email Verification Code</h2>
              <p style="color: #475569; font-size: 15px; line-height: 1.6;">
                Hello {full_name}, please use the following One-Time Password (OTP) to verify your AMU account:
              </p>
              <!-- OTP Box -->
              <div style="background-color: #eff6ff; border: 2px dashed #2563eb; border-radius: 8px; padding: 24px; text-align: center; margin: 28px 0;">
                <span style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #1e40af; letter-spacing: 1.5px; display: block; margin-bottom: 8px;">
                  Verification OTP
                </span>
                <span style="font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 700; color: #1e3a8a; letter-spacing: 10px; display: inline-block;">
                  {otp_code}
                </span>
                <p style="color: #64748b; font-size: 13px; margin: 12px 0 0 0;">
                  This code expires in <strong>{duration} minutes</strong>.
                </p>
              </div>
              <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
                <p style="color: #991b1b; font-size: 13px; margin: 0;">
                  <strong>Security Warning:</strong> Never share this code with anyone. AMU staff will never ask for your verification code.
                </p>
              </div>
              <p style="color: #94a3b8; font-size: 12px; margin-top: 24px;">
                If you did not request this OTP code, please disregard this email.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background-color: #f8fafc; padding: 16px; text-align: center; border-top: 1px solid #e2e8f0;">
              <p style="color: #94a3b8; font-size: 12px; margin: 0;">
                &copy; 2026 Aligarh Muslim University &bull; Academic Technology Division
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        return await cls.send_email_async(email, subject, html_content, text_content)

    @classmethod
    async def send_password_reset_email(cls, email: str, full_name: str, token: str) -> bool:
        """Send password reset link with time-bounded expiration."""
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"

        subject = "Reset Your Password - AMU Student Resource Management System"

        text_content = f"""Hello {full_name},

We received a request to reset your password for the AMU Student Resource Management System.

Please click the link below to set a new password:
{reset_link}

This link is valid for {settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes.

If you did not request a password reset, please ignore this email or notify IT support immediately. Your password will remain unchanged.

Best regards,
AMU Academic Technology Team
"""

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <table width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
          <!-- Header -->
          <tr>
            <td style="background-color: #1e3a8a; padding: 24px; text-align: center;">
              <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 600;">AMU Student Resource Management</h1>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 32px 28px;">
              <h2 style="color: #0f172a; margin-top: 0; font-size: 18px;">Password Reset Request</h2>
              <p style="color: #475569; font-size: 15px; line-height: 1.6;">
                Hello {full_name}, we received a request to reset your password. Click the button below to choose a new password:
              </p>
              <!-- Call to Action -->
              <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                <tr>
                  <td align="center">
                    <a href="{reset_link}" style="background-color: #dc2626; color: #ffffff; padding: 12px 28px; text-decoration: none; font-size: 15px; font-weight: 600; border-radius: 6px; display: inline-block;">
                      Reset Password
                    </a>
                  </td>
                </tr>
              </table>
              <p style="color: #64748b; font-size: 13px; line-height: 1.5;">
                Or copy and paste this reset URL into your browser:<br>
                <a href="{reset_link}" style="color: #2563eb; word-break: break-all;">{reset_link}</a>
              </p>
              <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
                <p style="color: #991b1b; font-size: 13px; margin: 0;">
                  <strong>Security Alert:</strong> This link will expire in <strong>{settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes</strong>. If you did not make this request, you can safely ignore this email.
                </p>
              </div>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background-color: #f8fafc; padding: 16px; text-align: center; border-top: 1px solid #e2e8f0;">
              <p style="color: #94a3b8; font-size: 12px; margin: 0;">
                &copy; 2026 Aligarh Muslim University &bull; Academic Technology Division
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        return await cls.send_email_async(email, subject, html_content, text_content)

    @classmethod
    async def send_password_reset_confirmation(cls, email: str, full_name: str) -> bool:
        """Send notification confirming password change."""
        subject = "Your Password Has Been Changed - AMU Student Resource Management System"

        text_content = f"""Hello {full_name},

This is a confirmation that your password for the AMU Student Resource Management System was recently changed.

If you made this change, no further action is required.

If you DID NOT make this change, please contact IT support immediately to secure your account.

Best regards,
AMU Academic Technology Team
"""

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <table width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
          <tr>
            <td style="background-color: #1e3a8a; padding: 24px; text-align: center;">
              <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 600;">AMU Student Resource Management</h1>
            </td>
          </tr>
          <tr>
            <td style="padding: 32px 28px;">
              <h2 style="color: #0f172a; margin-top: 0; font-size: 18px;">Password Updated Successfully</h2>
              <p style="color: #475569; font-size: 15px; line-height: 1.6;">
                Hello {full_name}, the password for your account has been updated successfully.
              </p>
              <div style="background-color: #f0fdf4; border-left: 4px solid #22c55e; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
                <p style="color: #166534; font-size: 13px; margin: 0;">
                  If you performed this action, no further steps are needed.
                </p>
              </div>
              <p style="color: #dc2626; font-size: 13px; line-height: 1.5;">
                <strong>Did not make this change?</strong> Please contact the AMU system administrator or IT helpdesk immediately.
              </p>
            </td>
          </tr>
          <tr>
            <td style="background-color: #f8fafc; padding: 16px; text-align: center; border-top: 1px solid #e2e8f0;">
              <p style="color: #94a3b8; font-size: 12px; margin: 0;">
                &copy; 2026 Aligarh Muslim University &bull; Academic Technology Division
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        return await cls.send_email_async(email, subject, html_content, text_content)

    @classmethod
    async def send_share_notification_email(
        cls,
        email: str,
        grantee_name: str,
        sharer_name: str,
        resource_title: str,
        permission: str,
        resource_id: str | object,
    ) -> bool:
        """Send notification to grantee when an academic resource has been shared with them."""
        resource_url = f"{settings.FRONTEND_URL}/resources/{resource_id}"
        subject = f"{sharer_name} shared '{resource_title}' with you - AMU Resources"

        text_content = f"""Hello {grantee_name},

{sharer_name} has shared the academic resource "{resource_title}" with you on the AMU Student Resource Management System with {permission} permissions.

You can access and view this resource at:
{resource_url}

Best regards,
AMU Academic Technology Team
"""

        html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0">
    <tr>
      <td align="center">
        <table width="600" border="0" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;">
          <tr>
            <td style="background-color: #1e3a8a; padding: 24px; text-align: center;">
              <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 600;">AMU Student Resource Management</h1>
            </td>
          </tr>
          <tr>
            <td style="padding: 32px 28px;">
              <h2 style="color: #0f172a; margin-top: 0; font-size: 18px;">New Shared Resource</h2>
              <p style="color: #475569; font-size: 15px; line-height: 1.6;">
                Hello <strong>{grantee_name}</strong>,<br><br>
                <strong>{sharer_name}</strong> has shared an academic resource with you.
              </p>
              <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <p style="margin: 0 0 8px 0; font-size: 16px; font-weight: 600; color: #1e293b;">
                  📄 {resource_title}
                </p>
                <p style="margin: 0; font-size: 13px; color: #64748b;">
                  Access Level: <strong style="color: #2563eb; text-transform: uppercase;">{permission}</strong>
                </p>
              </div>
              <div style="text-align: center; margin: 28px 0;">
                <a href="{resource_url}" style="background-color: #2563eb; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-weight: 600; font-size: 14px; display: inline-block;">
                  View Shared Resource
                </a>
              </div>
            </td>
          </tr>
          <tr>
            <td style="background-color: #f8fafc; padding: 16px; text-align: center; border-top: 1px solid #e2e8f0;">
              <p style="color: #94a3b8; font-size: 12px; margin: 0;">
                &copy; 2026 Aligarh Muslim University &bull; Academic Technology Division
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
        return await cls.send_email_async(email, subject, html_content, text_content)
