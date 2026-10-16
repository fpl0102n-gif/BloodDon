"""Transactional SMTP email backend for production and staging delivery.

Implements SMTP delivery per decision D-50, docs/08-setup-env.md §4, and Gate G4.
Supports port 465 (SSL/SMTPS) and port 587 (STARTTLS).
"""

import logging
import smtplib
from email.message import EmailMessage as PyEmailMessage

from app.core.config import Settings
from app.mail.base import Mailer

logger = logging.getLogger("app.mail.smtp")


class SmtpMailer(Mailer):
    """SMTP mailer transmitting transactional messages via standard smtplib."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        """Transmit email message through configured SMTP server."""
        host = self.settings.smtp_host or "localhost"
        port = self.settings.smtp_port or 587
        sender = self.settings.mail_from

        msg = PyEmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        # Port 465 enforces direct SSL connection (SMTPS); other ports use STARTTLS if enabled
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10) as server:
                if self.settings.smtp_user and self.settings.smtp_password:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=10) as server:
                if self.settings.smtp_tls:
                    server.starttls()
                if self.settings.smtp_user and self.settings.smtp_password:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)

        logger.info("Sent transactional email to %s (subject: %s)", to, subject)
