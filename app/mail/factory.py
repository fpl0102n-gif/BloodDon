"""Mailer factory creating email backend based on application settings.

Breaks circular dependencies by providing a dedicated factory layer above
base abstractions and concrete backends (ConsoleMailer, SmtpMailer).
"""

from app.core.config import Settings, get_settings
from app.mail.base import Mailer
from app.mail.console import ConsoleMailer
from app.mail.smtp import SmtpMailer


def create_mailer(settings: Settings | None = None) -> Mailer:
    """Instantiate appropriate mailer implementation (SmtpMailer or ConsoleMailer)."""
    cfg = settings or get_settings()
    if cfg.mail_backend == "smtp":
        return SmtpMailer(cfg)
    return ConsoleMailer()
