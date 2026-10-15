"""Rate limiting abstraction, in-memory implementation, and trusted-proxy IP resolver.

Implements P1.6 per docs/05-architecture.md §16, docs/03-auth-permissions.md §15, and decision D-30:
- Resolves genuine client IP addresses through trusted reverse proxies (T-RATE-10-a/b).
- Untrusted peers attempting to spoof X-Forwarded-For have the header ignored (T-RATE-10-a).
- Hashes client IP addresses with salt for rate limiting storage (T-RATE-10-c).
- Provides thread-safe sliding-window InMemoryRateLimiter with injectable Clock for zero-sleep testing.
"""

import ipaddress
import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

from fastapi import Request

from app.core.clock import Clock, get_clock
from app.core.config import get_settings
from app.core.logging import anonymize_ip


def is_ip_in_trusted_proxies(ip_str: str, trusted_proxies: list[str] | str | None) -> bool:
    """Verify whether an IP address matches the trusted reverse proxy whitelist or subnets."""
    if not ip_str or not trusted_proxies:
        return False

    if isinstance(trusted_proxies, str):
        proxy_list = [p.strip() for p in trusted_proxies.split(",") if p.strip()]
    else:
        proxy_list = list(trusted_proxies)

    if ip_str in proxy_list:
        return True

    try:
        target_ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    for proxy in proxy_list:
        try:
            if "/" in proxy:
                if target_ip in ipaddress.ip_network(proxy, strict=False):
                    return True
            else:
                if target_ip == ipaddress.ip_address(proxy):
                    return True
        except ValueError:
            continue
    return False


def resolve_client_ip(
    request: Request,
    trusted_proxies: list[str] | str | None = None,
) -> str:
    """Resolve genuine client IP address based on trusted proxy configuration.

    - T-RATE-10-a: If request peer is NOT a trusted proxy, X-Forwarded-For is strictly ignored.
    - T-RATE-10-b: If request peer IS a trusted proxy, client IP is extracted from X-Forwarded-For.
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"
    proxies = trusted_proxies if trusted_proxies is not None else get_settings().trusted_proxies

    # Peer is untrusted: ignore X-Forwarded-For completely
    if not is_ip_in_trusted_proxies(peer_ip, proxies):
        return peer_ip

    # Peer is trusted proxy: inspect X-Forwarded-For header
    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer_ip

    hops = [ip.strip() for ip in xff.split(",") if ip.strip()]
    if not hops:
        return peer_ip

    # Walk chain from right to left (closest proxy to furthest client)
    for hop_ip in reversed(hops):
        if not is_ip_in_trusted_proxies(hop_ip, proxies):
            return hop_ip

    # If all hops are trusted proxies, fall back to the leftmost IP
    return hops[0]


def resolve_client_ip_hash(
    request: Request,
    salt: str | None = None,
    trusted_proxies: list[str] | str | None = None,
) -> str:
    """Resolve client IP and return its irreversible salted hash (T-RATE-10-c)."""
    client_ip = resolve_client_ip(request, trusted_proxies=trusted_proxies)
    return anonymize_ip(client_ip, salt=salt)


# ==============================================================================
# Rate Limiter Interfaces and Implementations
# ==============================================================================


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Result of a rate limit check or hit operation."""

    allowed: bool
    limit: int
    remaining: int
    reset_after: int
    retry_after: int


class RateLimiter(ABC):
    """Abstract interface for rate limiting backends."""

    @abstractmethod
    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Inspect rate limit state without incrementing counters."""
        ...

    @abstractmethod
    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Increment rate limit counter and return updated result."""
        ...

    @abstractmethod
    def reset(self, key: str) -> None:
        """Clear rate limit records for a specific key."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Clear all rate limit records."""
        ...


class InMemoryRateLimiter(RateLimiter):
    """Thread-safe sliding-window in-memory rate limiter using timestamps."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock
        self._storage: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def clock(self) -> Clock:
        return self._clock or get_clock()

    def _evict_stale(self, queue: deque[float], cutoff: float) -> None:
        while queue and queue[0] <= cutoff:
            queue.popleft()

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = self.clock.now().timestamp()
        cutoff = now - window_seconds

        with self._lock:
            queue = self._storage.get(key)
            if not queue:
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    reset_after=window_seconds,
                    retry_after=0,
                )

            self._evict_stale(queue, cutoff)
            count = len(queue)
            allowed = count < limit
            remaining = max(0, limit - count)
            oldest = queue[0] if queue else now
            reset_after = max(1, int(window_seconds - (now - oldest)))
            retry_after = reset_after if not allowed else 0

            return RateLimitResult(
                allowed=allowed,
                limit=limit,
                remaining=remaining,
                reset_after=reset_after,
                retry_after=retry_after,
            )

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = self.clock.now().timestamp()
        cutoff = now - window_seconds

        with self._lock:
            if key not in self._storage:
                self._storage[key] = deque()

            queue = self._storage[key]
            self._evict_stale(queue, cutoff)

            if len(queue) < limit:
                queue.append(now)
                count = len(queue)
                remaining = max(0, limit - count)
                oldest = queue[0]
                reset_after = max(1, int(window_seconds - (now - oldest)))
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=remaining,
                    reset_after=reset_after,
                    retry_after=0,
                )

            # Limit reached: reject and compute retry_after
            oldest = queue[0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_after=retry_after,
                retry_after=retry_after,
            )

    def reset(self, key: str) -> None:
        with self._lock:
            self._storage.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._storage.clear()


class FakeRateLimiter(RateLimiter):
    """Controllable test double for rate limiting."""

    def __init__(self, allow_all: bool = True) -> None:
        self.allow_all = allow_all
        self.calls: list[tuple[str, int, int]] = []
        self._denied_keys: set[str] = set()

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        self.calls.append((key, limit, window_seconds))
        if not self.allow_all or key in self._denied_keys:
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_after=window_seconds,
                retry_after=window_seconds,
            )
        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=max(0, limit - 1),
            reset_after=window_seconds,
            retry_after=0,
        )

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        allowed = self.allow_all and (key not in self._denied_keys)
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=limit if allowed else 0,
            reset_after=window_seconds,
            retry_after=0 if allowed else window_seconds,
        )

    def peek(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Alias for check."""
        return self.check(key, limit, window_seconds)

    def deny(self, key: str) -> None:
        """Force the specified key to be rejected."""
        self._denied_keys.add(key)

    def reset(self, key: str) -> None:
        self._denied_keys.discard(key)

    def clear(self) -> None:
        self.calls.clear()
        self._denied_keys.clear()
        self.allow_all = True


# Default singleton instance
_default_rate_limiter: RateLimiter = InMemoryRateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Retrieve the application rate limiter instance."""
    return _default_rate_limiter


def set_rate_limiter(limiter: RateLimiter | None = None) -> None:
    """Override the application rate limiter instance (used by test fixtures)."""
    global _default_rate_limiter
    _default_rate_limiter = limiter if limiter is not None else InMemoryRateLimiter()
