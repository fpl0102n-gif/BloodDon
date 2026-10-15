"""Clock abstraction for deterministic time retrieval and testability.

Implements P1.6 per docs/05-architecture.md §5, docs/06-business-rules.md §2 (BR-GEN-04),
and decision D-14:
- Direct calls to datetime.now() are forbidden across business logic and services.
- All domain, auth, session, and retention logic injects or calls Clock.
- All stored datetimes are UTC (TIMESTAMPTZ).
- Calendar day calculations (e.g. days_since_donation) use the operational APP_TIMEZONE (Africa/Algiers).
- Provides SystemClock for production and FakeClock / FrozenClock for zero-sleep deterministic testing.
"""

from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings


class Clock(ABC):
    """Abstract clock interface providing standardized UTC and operational timezone dates."""

    @abstractmethod
    def now(self) -> datetime:
        """Return current time in UTC with timezone awareness."""
        ...

    @abstractmethod
    def now_local(self, tz_name: str | None = None) -> datetime:
        """Return current time converted to the specified or configured operational timezone."""
        ...

    def today(self, tz_name: str | None = None) -> date:
        """Return current date in the operational timezone (BR-GEN-04)."""
        return self.now_local(tz_name).date()

    def days_since(self, target_date: date, tz_name: str | None = None) -> int:
        """Calculate full elapsed days between target_date and today in operational timezone."""
        current_date = self.today(tz_name)
        return (current_date - target_date).days

    def is_past(self, dt: datetime) -> bool:
        """Check if given timezone-aware datetime has elapsed."""
        utc_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return self.now() > utc_dt


class SystemClock(Clock):
    """Production clock implementation backed by system wall-clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def now_local(self, tz_name: str | None = None) -> datetime:
        target_tz_str = tz_name or get_settings().app_timezone
        target_tz = ZoneInfo(target_tz_str)
        return datetime.now(target_tz)


class FakeClock(Clock):
    """Test clock that can be frozen, inspected, and advanced without sleeping."""

    def __init__(self, initial_time: datetime | None = None) -> None:
        if initial_time is None:
            self._current_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
        elif initial_time.tzinfo is None:
            self._current_time = initial_time.replace(tzinfo=UTC)
        else:
            self._current_time = initial_time.astimezone(UTC)

    def now(self) -> datetime:
        return self._current_time

    def now_local(self, tz_name: str | None = None) -> datetime:
        target_tz_str = tz_name or get_settings().app_timezone
        target_tz = ZoneInfo(target_tz_str)
        return self._current_time.astimezone(target_tz)

    def set_time(self, new_time: datetime) -> None:
        """Manually move clock to a specific point in time."""
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=UTC)
        else:
            self._current_time = new_time.astimezone(UTC)

    def advance(self, delta: timedelta | float) -> None:
        """Advance current time by timedelta or number of seconds."""
        if isinstance(delta, (int, float)):
            self._current_time += timedelta(seconds=delta)
        else:
            self._current_time += delta


# Application-level default clock instance
_default_clock: Clock = SystemClock()


def get_clock() -> Clock:
    """Retrieve the application clock instance (can be overridden in tests)."""
    return _default_clock


def set_clock(clock: Clock) -> None:
    """Set the application clock instance (used by test fixtures)."""
    global _default_clock
    _default_clock = clock
