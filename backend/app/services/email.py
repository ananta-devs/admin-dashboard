import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage as PyEmailMessage

from app.core.config import settings
from app.core.logging import logger


@dataclass
class OutgoingEmail:
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def body(self) -> str:
        return self.body_text


class EmailSender(ABC):
    """Abstract interface for transactional email delivery."""

    @abstractmethod
    async def send(self, message: OutgoingEmail) -> bool:
        """Send an outgoing email message. Returns True if successfully sent."""
        pass


class MemoryEmailSender(EmailSender):
    """Development and test in-memory email sink.

    Captures sent emails for inspection and assertions without external network dependencies.
    """

    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []

    async def send(self, message: OutgoingEmail) -> bool:
        self.outbox.append(message)
        logger.info(
            f"[MemoryEmailSender] Delivered email to {message.to_email!r}: subject={message.subject!r}"
        )
        return True

    def get_latest_for(self, email: str) -> OutgoingEmail | None:
        for msg in reversed(self.outbox):
            if msg.to_email.lower() == email.lower():
                return msg
        return None

    def clear(self) -> None:
        self.outbox.clear()

    @classmethod
    def get_sent_messages(cls) -> list[OutgoingEmail]:
        return list(_memory_sender.outbox)

    @classmethod
    def reset(cls) -> None:
        _memory_sender.outbox.clear()

    @classmethod
    def clear_all(cls) -> None:
        _memory_sender.outbox.clear()


class SmtpEmailSender(EmailSender):
    """Configurable production SMTP email sender using standard library smtplib."""

    async def send(self, message: OutgoingEmail) -> bool:
        if not settings.SMTP_HOST or not settings.SMTP_USER:
            logger.warning("SMTP delivery requested but SMTP_HOST or SMTP_USER is not configured.")
            return False

        try:
            msg = PyEmailMessage()
            msg["Subject"] = message.subject
            msg["From"] = f"{settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>"
            msg["To"] = message.to_email
            msg.set_content(message.body_text)

            if message.body_html:
                msg.add_alternative(message.body_html, subtype="html")

            # Execute synchronous smtplib in a worker thread if needed, or directly
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                if settings.SMTP_TLS:
                    server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)

            logger.info(f"[SmtpEmailSender] Email sent successfully to {message.to_email!r}")
            return True
        except Exception as e:
            logger.error(
                f"[SmtpEmailSender] Failed to deliver email to {message.to_email!r}: {e!s}"
            )
            return False


# Singleton sender instance based on settings
_memory_sender = MemoryEmailSender()
_smtp_sender = SmtpEmailSender()


def get_email_sender() -> EmailSender:
    """Return the configured EmailSender instance."""
    if settings.EMAIL_BACKEND == "smtp":
        return _smtp_sender
    return _memory_sender


class EmailService:
    """Service formatting and dispatching user lifecycle emails."""

    def __init__(self, sender: EmailSender | None = None) -> None:
        self.sender = sender or get_email_sender()

    async def send_verification_email(self, to_email: str, display_name: str, token: str) -> bool:
        """Send email verification link to user."""
        subject = "Verify your VIBE Enterprise account"
        verification_link = f"http://localhost:3000/auth/verify-email?token={token}"
        body_text = (
            f"Hello {display_name},\n\n"
            f"Thank you for registering on VIBE. Please verify your email address by visiting:\n"
            f"{verification_link}\n\n"
            f"This link will expire in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_SECONDS // 3600} hours.\n\n"
            f"If you did not register, please disregard this email."
        )
        body_html = (
            f"<p>Hello {display_name},</p>"
            f"<p>Thank you for registering on VIBE. Please verify your email address by clicking below:</p>"
            f'<p><a href="{verification_link}">Verify Email Address</a></p>'
            f"<p>This link will expire in {settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_SECONDS // 3600} hours.</p>"
        )
        msg = OutgoingEmail(
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        return await self.sender.send(msg)

    async def send_password_reset_email(self, to_email: str, display_name: str, token: str) -> bool:
        """Send password reset instructions to user."""
        subject = "Reset your VIBE Enterprise password"
        reset_link = f"http://localhost:3000/auth/reset-password?token={token}"
        body_text = (
            f"Hello {display_name},\n\n"
            f"A password reset request was received for your VIBE account. You may reset your password here:\n"
            f"{reset_link}\n\n"
            f"This link will expire in {settings.PASSWORD_RESET_TOKEN_EXPIRE_SECONDS // 60} minutes.\n\n"
            f"If you did not request a password reset, your account remains secure and you may disregard this notice."
        )
        body_html = (
            f"<p>Hello {display_name},</p>"
            f"<p>A password reset request was received for your VIBE account.</p>"
            f'<p><a href="{reset_link}">Reset Password</a></p>'
            f"<p>This link will expire in {settings.PASSWORD_RESET_TOKEN_EXPIRE_SECONDS // 60} minutes.</p>"
        )
        msg = OutgoingEmail(
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        return await self.sender.send(msg)

    async def send_invitation_email(
        self, to_email: str, org_name: str, role_name: str, token: str
    ) -> bool:
        """Send organization invitation email."""
        subject = f"You've been invited to join {org_name} on VIBE"
        invite_link = f"{settings.FRONTEND_URL}/invitations/accept?token={token}"
        body_text = (
            f"Hello,\n\n"
            f"You have been invited to join {org_name} as a {role_name}.\n"
            f"To accept this invitation, please visit:\n"
            f"{invite_link}\n\n"
            f"If you did not expect this invitation, please disregard this email."
        )
        body_html = (
            f"<p>Hello,</p>"
            f"<p>You have been invited to join <strong>{org_name}</strong> as a <strong>{role_name}</strong>.</p>"
            f'<p><a href="{invite_link}">Accept Invitation</a></p>'
        )
        msg = OutgoingEmail(
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        return await self.sender.send(msg)

    async def send_email(
        self, to_email: str, subject: str, html_content: str, text_content: str | None = None
    ) -> bool:
        """Generic email dispatch helper."""
        msg = OutgoingEmail(
            to_email=to_email,
            subject=subject,
            body_text=text_content or html_content,
            body_html=html_content,
        )
        return await self.sender.send(msg)
