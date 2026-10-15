"""Unit tests for RateLimiter, sliding-window algorithm, and trusted-proxy IP resolver.

Traces to:
- P1.6 (docs/12-implementation-plan.md §4)
- docs/05-architecture.md §16 (trusted-proxy IP resolver)
- docs/03-auth-permissions.md §15 & decision D-30 (in-memory rate limiter)
- T-RATE-10-a (untrusted peer -> ignore X-Forwarded-For)
- T-RATE-10-b (trusted proxy -> use X-Forwarded-For)
- T-RATE-10-c (rate limit storage uses salted hash, never raw IP)
"""

from unittest.mock import MagicMock

import pytest
from starlette.datastructures import Headers

from app.core.clock import FakeClock
from app.core.ratelimit import (
    InMemoryRateLimiter,
    get_rate_limiter,
    is_ip_in_trusted_proxies,
    resolve_client_ip,
    resolve_client_ip_hash,
    set_rate_limiter,
)


def make_dummy_request(
    client_ip: str,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock Request object with configured client host and headers."""
    req = MagicMock()
    req.client.host = client_ip
    req.headers = Headers(headers or {})
    return req


@pytest.mark.unit
def test_is_ip_in_trusted_proxies() -> None:
    """Verify trusted proxy matching for exact IP addresses and CIDR subnets."""
    proxies = ["127.0.0.1", "::1", "10.0.0.0/16", "172.16.0.1"]

    assert is_ip_in_trusted_proxies("127.0.0.1", proxies) is True
    assert is_ip_in_trusted_proxies("::1", proxies) is True
    assert is_ip_in_trusted_proxies("10.0.5.25", proxies) is True
    assert is_ip_in_trusted_proxies("172.16.0.1", proxies) is True

    # Out of range / untrusted
    assert is_ip_in_trusted_proxies("10.1.0.1", proxies) is False
    assert is_ip_in_trusted_proxies("198.51.100.1", proxies) is False
    assert is_ip_in_trusted_proxies("not_an_ip", proxies) is False
    assert is_ip_in_trusted_proxies("", proxies) is False
    assert is_ip_in_trusted_proxies("127.0.0.1", []) is False


@pytest.mark.unit
def test_t_rate_10_a_untrusted_peer_ignores_forwarded_for() -> None:
    """T-RATE-10-a: Verify X-Forwarded-For is strictly ignored when peer is untrusted."""
    trusted_proxies = ["127.0.0.1", "10.0.0.0/8"]

    # Direct connection from untrusted public IP: 198.51.100.5
    req = make_dummy_request(
        client_ip="198.51.100.5",
        headers={"x-forwarded-for": "203.0.113.195, 10.0.0.2"},
    )

    resolved = resolve_client_ip(req, trusted_proxies=trusted_proxies)
    # Must ignore the header and use the direct connection IP
    assert resolved == "198.51.100.5"


@pytest.mark.unit
def test_t_rate_10_b_trusted_proxy_resolves_forwarded_for() -> None:
    """T-RATE-10-b: Verify client IP is extracted from X-Forwarded-For when peer is a trusted proxy."""
    trusted_proxies = ["127.0.0.1", "10.0.0.0/8"]

    # Request forwarded through local reverse proxy (127.0.0.1)
    req = make_dummy_request(
        client_ip="127.0.0.1",
        headers={"x-forwarded-for": "203.0.113.195"},
    )

    resolved = resolve_client_ip(req, trusted_proxies=trusted_proxies)
    assert resolved == "203.0.113.195"


@pytest.mark.unit
def test_t_rate_10_b_multi_hop_proxy_chain() -> None:
    """T-RATE-10-b: Verify right-to-left traversal through multiple trusted proxy hops."""
    trusted_proxies = ["127.0.0.1", "10.0.0.0/8"]

    # Chain: Client (203.0.113.50) -> Cloud/CDN (10.0.1.1) -> Caddy (127.0.0.1)
    req = make_dummy_request(
        client_ip="127.0.0.1",
        headers={"x-forwarded-for": "203.0.113.50, 10.0.1.1"},
    )

    resolved = resolve_client_ip(req, trusted_proxies=trusted_proxies)
    assert resolved == "203.0.113.50"


@pytest.mark.unit
def test_t_rate_10_c_ip_hash_storage() -> None:
    """T-RATE-10-c: Verify rate limit storage uses salted hash and never raw client IP."""
    trusted_proxies = ["127.0.0.1"]
    salt = "b" * 32

    req = make_dummy_request(
        client_ip="127.0.0.1",
        headers={"x-forwarded-for": "203.0.113.50"},
    )

    ip_hash = resolve_client_ip_hash(req, salt=salt, trusted_proxies=trusted_proxies)
    assert ip_hash.startswith("ip_hash_")
    assert "203.0.113.50" not in ip_hash


@pytest.mark.unit
def test_in_memory_rate_limiter_sliding_window_with_fake_clock() -> None:
    """Verify InMemoryRateLimiter sliding window enforcement and reset with FakeClock."""
    fake_clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=fake_clock)
    key = "user_rate_key_01"

    # Limit: 3 hits per 60 seconds
    res1 = limiter.hit(key, limit=3, window_seconds=60)
    assert res1.allowed is True
    assert res1.remaining == 2
    assert res1.retry_after == 0

    res2 = limiter.hit(key, limit=3, window_seconds=60)
    assert res2.allowed is True
    assert res2.remaining == 1

    res3 = limiter.hit(key, limit=3, window_seconds=60)
    assert res3.allowed is True
    assert res3.remaining == 0

    # 4th hit: limit exceeded
    res4 = limiter.hit(key, limit=3, window_seconds=60)
    assert res4.allowed is False
    assert res4.remaining == 0
    assert res4.retry_after > 0

    # check() reports allowed=False without altering state
    check_res = limiter.check(key, limit=3, window_seconds=60)
    assert check_res.allowed is False
    assert check_res.remaining == 0

    # Advance time by 61 seconds (window expires)
    fake_clock.advance(61)

    # Now allowed again!
    res5 = limiter.hit(key, limit=3, window_seconds=60)
    assert res5.allowed is True
    assert res5.remaining == 2


@pytest.mark.unit
def test_rate_limiter_reset_and_clear() -> None:
    """Verify reset and clear operations remove rate limit keys."""
    limiter = InMemoryRateLimiter()

    limiter.hit("key_a", limit=1, window_seconds=60)
    limiter.hit("key_b", limit=1, window_seconds=60)

    assert limiter.check("key_a", limit=1, window_seconds=60).allowed is False
    assert limiter.check("key_b", limit=1, window_seconds=60).allowed is False

    # Reset single key
    limiter.reset("key_a")
    assert limiter.check("key_a", limit=1, window_seconds=60).allowed is True
    assert limiter.check("key_b", limit=1, window_seconds=60).allowed is False

    # Clear all keys
    limiter.clear()
    assert limiter.check("key_b", limit=1, window_seconds=60).allowed is True


@pytest.mark.unit
def test_global_rate_limiter_accessors() -> None:
    """Verify get_rate_limiter and set_rate_limiter allow overriding singleton."""
    original = get_rate_limiter()
    custom = InMemoryRateLimiter()

    set_rate_limiter(custom)
    assert get_rate_limiter() is custom

    set_rate_limiter(original)
    assert get_rate_limiter() is original
