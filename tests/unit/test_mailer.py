"""Unit tests for transactional email backends, templates, and factory.

Tests:
- FakeMailer: in-memory recording, failure injection, clear.
- ConsoleMailer: logging output.
- SmtpMailer: port 465 (SMTPS / SSL) vs port 587 (STARTTLS) dispatch.
- create_mailer: factory instantiation based on Settings.mail_backend.
- Arabic templates: render_invitation_email and render_password_reset_email formatting.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.mail.base import FakeMailer
from app.mail.console import ConsoleMailer
from app.mail.factory import create_mailer
from app.mail.smtp import SmtpMailer
from app.mail.templates import (
    render_invitation_email,
    render_password_reset_email,
)


@pytest.mark.unit
def test_fake_mailer_recording_and_failure_injection() -> None:
    """FakeMailer records messages in order and allows failure injection."""
    mailer = FakeMailer()
    assert len(mailer.outbox) == 0

    mailer.send(
        to="user1@example.com",
        subject="دعوة تجريبية",
        body_text="نص تجريبي",
    )
    assert len(mailer.outbox) == 1
    assert mailer.outbox[0].to == "user1@example.com"
    assert mailer.outbox[0].subject == "دعوة تجريبية"

    # Test failure injection
    mailer.inject_failure = True
    with pytest.raises(RuntimeError, match="Injected email delivery failure"):
        mailer.send(to="user2@example.com", subject="fail", body_text="fail")

    # Test clear
    mailer.clear()
    assert len(mailer.outbox) == 0
    assert mailer.inject_failure is False


@pytest.mark.unit
def test_console_mailer_logging() -> None:
    """ConsoleMailer outputs message details to the application logger."""
    mailer = ConsoleMailer()
    with patch("app.mail.console.logger.info") as mock_info:
        mailer.send(
            to="test@bloodday.local",
            subject="Test Subject",
            body_text="Test Body",
        )
        assert mock_info.called
        log_call = mock_info.call_args[0]
        assert "test@bloodday.local" in log_call
        assert "Test Subject" in log_call


@pytest.mark.unit
def test_smtp_mailer_port_465_ssl() -> None:
    """SmtpMailer on port 465 connects via SMTP_SSL and authenticates."""
    settings = Settings(
        app_env="test",
        mail_backend="smtp",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_user="test_user",
        smtp_password="test_password_123",  # pragma: allowlist secret
        smtp_tls=True,
        mail_from="noreply@bloodday.dz",
    )
    mailer = SmtpMailer(settings)

    mock_server = MagicMock()
    with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
        mock_smtp_ssl.return_value.__enter__.return_value = mock_server

        mailer.send(
            to="supervisor@hospital.dz",
            subject="دعوة المشرف",
            body_text="نص الدعوة",
            body_html="<p>نص الدعوة</p>",
        )

        mock_smtp_ssl.assert_called_once_with("smtp.example.com", 465, timeout=10)
        mock_server.login.assert_called_once_with(
            "test_user", "test_password_123"
        )  # pragma: allowlist secret
        mock_server.send_message.assert_called_once()
        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["To"] == "supervisor@hospital.dz"
        assert sent_msg["From"] == "noreply@bloodday.dz"
        assert sent_msg["Subject"] == "دعوة المشرف"


@pytest.mark.unit
def test_smtp_mailer_port_587_starttls() -> None:
    """SmtpMailer on port 587 connects via standard SMTP and executes starttls."""
    settings = Settings(
        app_env="test",
        mail_backend="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user587",
        smtp_password="secret_pass_587",  # pragma: allowlist secret
        smtp_tls=True,
        mail_from="noreply@bloodday.dz",
    )
    mailer = SmtpMailer(settings)

    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server

        mailer.send(
            to="doctor@bloodday.dz",
            subject="إعادة تعيين",
            body_text="رابط إعادة التعيين",
        )

        mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=10)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with(
            "user587", "secret_pass_587"
        )  # pragma: allowlist secret
        mock_server.send_message.assert_called_once()


@pytest.mark.unit
def test_create_mailer_factory() -> None:
    """create_mailer instantiates the correct backend according to settings."""
    console_settings = Settings(app_env="test", mail_backend="console")
    mailer_console = create_mailer(console_settings)
    assert isinstance(mailer_console, ConsoleMailer)

    smtp_settings = Settings(
        app_env="test", mail_backend="smtp", smtp_host="smtp.local", smtp_port=587
    )
    mailer_smtp = create_mailer(smtp_settings)
    assert isinstance(mailer_smtp, SmtpMailer)


@pytest.mark.unit
def test_invitation_email_template_rendering() -> None:
    """render_invitation_email produces proper RTL Arabic copy with URL and TTL notice."""
    invite_url = "https://bloodday.dz/auth/accept-invite?token=sample_token_xyz"
    subject, body_text, body_html = render_invitation_email(
        invite_url=invite_url,
        role="supervisor",
        site_name="مركز الأوراس",
    )

    assert "دعوة للانضمام" in subject
    assert "مشرف موقع" in body_text
    assert "مركز الأوراس" in body_text
    assert invite_url in body_text
    assert "72 ساعة" in body_text

    assert "dir='rtl'" in body_html
    assert invite_url in body_html
    assert "مركز الأوراس" in body_html


@pytest.mark.unit
def test_password_reset_email_template_rendering() -> None:
    """render_password_reset_email produces proper RTL Arabic copy with URL and 1-hour TTL notice."""
    reset_url = "https://bloodday.dz/auth/reset-password?token=sample_reset_token_xyz"
    subject, body_text, body_html = render_password_reset_email(reset_url=reset_url)

    assert "إعادة تعيين كلمة المرور" in subject
    assert reset_url in body_text
    assert "ساعة واحدة" in body_text
    assert "إذا لم تطلب" in body_text

    assert "dir='rtl'" in body_html
    assert reset_url in body_html
