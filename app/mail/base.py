"""Transactional email abstraction interfaces and test doubles.

Implements email delivery contracts per docs/05-architecture.md §13 and decision D-50:
- Mailer: Abstract contract for transactional notifications (invitations, password resets).
- FakeMailer: In-memory test double capturing dispatched emails into an outbox list with failure injection.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Representation of an outgoing transactional email."""

    to: str
    subject: str
    body_text: str
    body_html: str | None = None


class Mailer(ABC):
    """Abstract interface for transactional email delivery."""

    @abstractmethod
    def send(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        """Send a single email message."""
        ...


class FakeMailer(Mailer):
    """In-memory test double recording sent emails into an inspection outbox."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []
        self.inject_failure: bool = False

    def send(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        if self.inject_failure:
            raise RuntimeError("Injected email delivery failure")
        msg = EmailMessage(
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        self.outbox.append(msg)

    def clear(self) -> None:
        """Clear outbox and reset failure flag."""
        self.outbox.clear()
        self.inject_failure = False


# Global singleton mailer provider
_GLOBAL_MAILER: Mailer | None = None


def get_mailer() -> Mailer:
    """Retrieve active mailer provider (defaults to FakeMailer if unconfigured)."""
    global _GLOBAL_MAILER
    if _GLOBAL_MAILER is None:
        _GLOBAL_MAILER = FakeMailer()
    return _GLOBAL_MAILER


def set_mailer(mailer: Mailer | None) -> None:
    """Override active mailer provider (used by test harness and startup wiring)."""
    global _GLOBAL_MAILER
    _GLOBAL_MAILER = mailer
