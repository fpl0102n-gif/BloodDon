"""Transactional email delivery and template rendering package."""

from app.mail.base import (
    EmailMessage,
    FakeMailer,
    Mailer,
    get_mailer,
    set_mailer,
)
from app.mail.console import ConsoleMailer
from app.mail.factory import create_mailer
from app.mail.smtp import SmtpMailer
from app.mail.templates import (
    render_invitation_email,
    render_password_reset_email,
)

__all__ = [
    "ConsoleMailer",
    "EmailMessage",
    "FakeMailer",
    "Mailer",
    "SmtpMailer",
    "create_mailer",
    "get_mailer",
    "render_invitation_email",
    "render_password_reset_email",
    "set_mailer",
]
