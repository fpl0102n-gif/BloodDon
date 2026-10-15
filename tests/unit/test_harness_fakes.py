"""Verification tests for the test harness fakes, doubles, and role skeletons.

Traces to:
- P1.10 (docs/12-implementation-plan.md §4)
- docs/05-architecture.md §13 (Storage, Mailer, Clock, RateLimiter test doubles)
- docs/07-test-cases.md §2 & §3 (test environment, roles, sentinels)
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import FakeClock, get_clock
from app.core.ratelimit import FakeRateLimiter, get_rate_limiter
from app.mail import FakeMailer, get_mailer
from app.storage import FakeStorage, get_storage
from tests.conftest import (
    SENTINEL_FIRST_NAME,
    SENTINEL_LAST_NAME,
    SENTINEL_PHONE,
    MockUser,
)

# ==============================================================================
# 1. FakeClock Verification
# ==============================================================================


@pytest.mark.unit
def test_fake_clock_fixture(fake_clock: FakeClock) -> None:
    """Verify fake_clock fixture installs itself globally and supports time manipulation."""
    assert get_clock() is fake_clock

    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    fake_clock.set_time(t0)
    assert fake_clock.now() == t0

    # Advance time by 2 hours
    fake_clock.advance(timedelta(hours=2))
    assert fake_clock.now() == t0 + timedelta(hours=2)


# ==============================================================================
# 2. FakeStorage Verification
# ==============================================================================


@pytest.mark.unit
def test_fake_storage_fixture(fake_storage: FakeStorage) -> None:
    """Verify fake_storage stores bytes in memory, returns URLs, and handles failure injection."""
    assert get_storage() is fake_storage

    content = b"fake-image-bytes-data"
    key = "photos/donor1.jpg"

    # Save and retrieve
    saved_key = fake_storage.save(key, content)
    assert saved_key == key
    assert fake_storage.exists(key) is True
    assert fake_storage.get(key) == content
    assert fake_storage.get_url(key) == "/media/photos/donor1.jpg"

    # Failure injection
    fake_storage.inject_failure = True
    with pytest.raises(OSError, match="Injected storage write failure"):
        fake_storage.save("photos/donor2.jpg", content)

    fake_storage.inject_failure = False
    assert fake_storage.delete(key) is True
    assert fake_storage.exists(key) is False


# ==============================================================================
# 3. FakeMailer Verification
# ==============================================================================


@pytest.mark.unit
def test_fake_mailer_fixture(fake_mailer: FakeMailer) -> None:
    """Verify fake_mailer records messages to outbox and supports failure injection."""
    assert get_mailer() is fake_mailer

    fake_mailer.send(
        to="donor@example.com",
        subject="Donation Reminder",
        body_text="Thank you for participating.",
    )

    assert len(fake_mailer.outbox) == 1
    msg = fake_mailer.outbox[0]
    assert msg.to == "donor@example.com"
    assert msg.subject == "Donation Reminder"
    assert msg.body_text == "Thank you for participating."

    # Failure injection
    fake_mailer.inject_failure = True
    with pytest.raises(RuntimeError, match="Injected email delivery failure"):
        fake_mailer.send("other@example.com", "Test", "Body")


# ==============================================================================
# 4. FakeRateLimiter Verification
# ==============================================================================


@pytest.mark.unit
def test_fake_rate_limiter_fixture(fake_rate_limiter: FakeRateLimiter) -> None:
    """Verify fake_rate_limiter records calls and allows targeted denial."""
    assert get_rate_limiter() is fake_rate_limiter

    # Default allow
    res = fake_rate_limiter.hit("key-1", limit=10, window_seconds=60)
    assert res.allowed is True
    assert len(fake_rate_limiter.calls) == 1

    # Force denial on specific key
    fake_rate_limiter.deny("key-blocked")
    blocked_res = fake_rate_limiter.hit("key-blocked", limit=5, window_seconds=60)
    assert blocked_res.allowed is False


# ==============================================================================
# 5. Role Fixtures Verification
# ==============================================================================


@pytest.mark.unit
def test_role_fixtures_skeletons(
    role_anon: None,
    role_sup_a: MockUser,
    role_sup_b: MockUser,
    role_sup_disabled: MockUser,
    role_admin: MockUser,
    role_admin_fresh: MockUser,
) -> None:
    """Verify role fixtures produce expected mock contexts for permission testing."""
    assert role_anon is None

    assert role_sup_a.role == "supervisor"
    assert role_sup_a.site_id == "site-a-001"
    assert role_sup_a.is_active is True

    assert role_sup_b.role == "supervisor"
    assert role_sup_b.site_id == "site-b-002"

    assert role_sup_disabled.is_active is False

    assert role_admin.role == "admin"
    assert role_admin.mfa_verified is True
    assert role_admin.reauth_valid is False

    assert role_admin_fresh.role == "admin"
    assert role_admin_fresh.reauth_valid is True


# ==============================================================================
# 6. Sentinel Constants Verification
# ==============================================================================


@pytest.mark.unit
def test_sentinel_constants() -> None:
    """Verify sentinels match definitions in docs/07-test-cases.md §3.3."""
    assert SENTINEL_FIRST_NAME == "ZZFIRST"
    assert SENTINEL_LAST_NAME == "ZZLAST"
    assert SENTINEL_PHONE == "0550123456"
