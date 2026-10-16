"""Console transactional email backend for development and testing.

Logs email messages to the configured application logger.
"""

import logging

from app.mail.base import Mailer

logger = logging.getLogger("app.mail.console")


class ConsoleMailer(Mailer):
    """Development mailer that outputs emails to the application logger."""

    def send(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        """Log outgoing email payload to application logger."""
        html_snippet = f"\nBody (HTML):\n{body_html}" if body_html else ""
        logger.info(
            "=== [ConsoleMailer] Outgoing Transactional Email ===\n"
            "To: %s\n"
            "Subject: %s\n"
            "Body Text:\n%s%s\n"
            "====================================================",
            to,
            subject,
            body_text,
            html_snippet,
        )
