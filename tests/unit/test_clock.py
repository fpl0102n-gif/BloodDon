"""Unit tests for Clock abstraction, SystemClock, and FakeClock.

Traces to:
- P1.6 (docs/12-implementation-plan.md §4)
- docs/05-architecture.md §5 (Clock abstraction, no direct datetime.now())
- docs/06-business-rules.md §2 (BR-GEN-04: UTC storage, APP_TIMEZONE calculations)
- decision D-14 (testable time injection)
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.clock import (
    FakeClock,
    SystemClock,
    get_clock,
    set_clock,
)


@pytest.mark.unit
def test_system_clock_now_is_utc_aware() -> None:
    """Verify SystemClock.now() returns timezone-aware UTC datetime."""
    clock = SystemClock()
    now_dt = clock.now()

    assert now_dt.tzinfo is not None
    assert now_dt.tzinfo == UTC


@pytest.mark.unit
def test_system_clock_now_local_uses_configured_timezone() -> None:
    """Verify SystemClock.now_local() converts to Africa/Algiers timezone."""
    clock = SystemClock()
    local_dt = clock.now_local("Africa/Algiers")

    assert local_dt.tzinfo == ZoneInfo("Africa/Algiers")
    # Africa/Algiers is standard UTC+1 (3600 seconds offset)
    assert local_dt.utcoffset() == timedelta(hours=1)


@pytest.mark.unit
def test_system_clock_days_since_calculation() -> None:
    """Verify days_since correctly counts calendar days in operational timezone."""
    clock = SystemClock()
    today_date = clock.today()
    past_date = today_date - timedelta(days=15)

    assert clock.days_since(past_date) == 15
    assert clock.days_since(today_date) == 0


@pytest.mark.unit
def test_system_clock_is_past() -> None:
    """Verify is_past correctly assesses datetime ordering against UTC clock."""
    clock = SystemClock()
    now = clock.now()

    past_dt = now - timedelta(minutes=5)
    future_dt = now + timedelta(minutes=5)

    assert clock.is_past(past_dt) is True
    assert clock.is_past(future_dt) is False


@pytest.mark.unit
def test_fake_clock_freezes_and_advances() -> None:
    """Verify FakeClock remains frozen until explicitly advanced."""
    fixed_start = datetime(2026, 10, 15, 8, 30, 0, tzinfo=UTC)
    clock = FakeClock(fixed_start)

    assert clock.now() == fixed_start
    assert clock.today("Africa/Algiers") == date(2026, 10, 15)

    # Advance by 2 hours
    clock.advance(timedelta(hours=2))
    assert clock.now() == datetime(2026, 10, 15, 10, 30, 0, tzinfo=UTC)

    # Advance by float seconds (1800s = 30min)
    clock.advance(1800)
    assert clock.now() == datetime(2026, 10, 15, 11, 0, 0, tzinfo=UTC)

    # Move across local midnight (23:30 UTC -> 00:30 Algiers next day)
    clock.set_time(datetime(2026, 10, 15, 23, 30, 0, tzinfo=UTC))
    assert clock.now().date() == date(2026, 10, 15)
    # Algiers is UTC+1, so it is already Oct 16 locally
    assert clock.today("Africa/Algiers") == date(2026, 10, 16)


@pytest.mark.unit
def test_global_clock_get_and_set() -> None:
    """Verify get_clock and set_clock allow overriding the application clock in tests."""
    original = get_clock()
    fake = FakeClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))

    set_clock(fake)
    assert get_clock() is fake
    assert get_clock().now() == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    # Restore original clock
    set_clock(original)
    assert get_clock() is original
