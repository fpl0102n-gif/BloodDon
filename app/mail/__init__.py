"""Transactional email delivery and template rendering package."""

from app.mail.base import EmailMessage, FakeMailer, Mailer, get_mailer, set_mailer

__all__ = [
    "EmailMessage",
    "FakeMailer",
    "Mailer",
    "get_mailer",
    "set_mailer",
]
