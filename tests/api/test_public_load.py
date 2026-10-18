"""Light Load and Concurrency Tests for Public API Endpoints (P6.5).

Per:
- docs/09-roadmap.md §4 Phase 6: "اختبار حمل خفيف على التعهد والصفحات العامة"
- docs/10-testing-security-deployment.md §9:
  "مجمّع اتصالات القاعدة: DB_POOL_SIZE + OVERFLOW أقل من حد الخطة المُدارة، وعدم نفاد الاتصالات."
- docs/12-implementation-plan.md §9 Task P6.5:
  "Light load test on pledge and public pages | scripts | — | No pool exhaustion"
"""

import concurrent.futures
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_rate_limiter_dep
from app.core.config import get_settings
from app.core.database import create_db_engine, get_db
from app.core.ratelimit import FakeRateLimiter
from app.main import create_app
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.pledge import Pledge
from scripts.load_test_public import run_public_load_test
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo


@pytest.fixture
def pooled_test_setup(
    db_session: Session,
) -> Generator[tuple[TestClient, Engine, str, str], None, None]:
    """Provide a TestClient wired to a real QueuePool engine with session-per-request lifecycle."""
    settings = get_settings()
    url = settings.test_database_url or settings.database_url

    # Create dedicated engine with explicit pool configuration
    pooled_engine = create_db_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
    )
    pooled_session_factory = sessionmaker(bind=pooled_engine, autocommit=False, autoflush=False)

    # Seed campaign, site, donor, and photo using setup session
    setup_session = pooled_session_factory()
    try:
        # Enforce BR-CAMP-04: clear any existing current campaign
        setup_session.execute(
            update(Campaign).where(Campaign.is_current.is_(True)).values(is_current=False)
        )
        setup_session.flush()

        site = create_site(
            setup_session,
            name="موقع اختبار التحميل",
            address="الجزائر العاصمة",
            is_active=True,
        )
        camp = create_campaign(
            setup_session,
            slug=f"test-public-load-{uuid.uuid4().hex[:8]}",
            title="حملة اختبار التحميل المتزامن",
            summary="ملخص الحملة",
            content="# تفاصيل الحملة",
            status="live",
            is_published=True,
            is_current=True,
        )
        create_campaign_site(
            setup_session,
            campaign_id=camp.id,
            site_id=site.id,
            supervisors_text="د. إبراهيم",
        )
        donor = create_donor(
            setup_session,
            campaign_id=camp.id,
            site_id=site.id,
            first_name="يوسف",
            status="donated",
            consent_hero_name=True,
            consent_photo=True,
            donated_at=datetime.now(UTC),
        )
        create_photo(
            setup_session,
            campaign_id=camp.id,
            site_id=site.id,
            kind="individual",
            donor_id=donor.id,
        )
        setup_session.commit()
        camp_id = camp.id
        camp_slug = camp.slug
        site_id = site.id
        site_id_str = str(site.id)
    finally:
        setup_session.close()

    # Wire FastAPI to create and close a session per request from pooled_engine
    app = create_app()

    def get_pooled_db() -> Generator[Session, None, None]:
        db = pooled_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_pooled_db
    app.dependency_overrides[get_rate_limiter_dep] = lambda: FakeRateLimiter(allow_all=True)
    client = TestClient(app)

    try:
        yield client, pooled_engine, camp_slug, site_id_str
    finally:
        cleanup_session = pooled_session_factory()
        try:
            cleanup_session.execute(delete(Pledge).where(Pledge.campaign_id == camp_id))
            cleanup_session.execute(delete(Photo).where(Photo.campaign_id == camp_id))
            cleanup_session.execute(delete(Donor).where(Donor.campaign_id == camp_id))
            cleanup_session.execute(delete(CampaignSite).where(CampaignSite.campaign_id == camp_id))
            cleanup_session.execute(delete(Campaign).where(Campaign.id == camp_id))
            cleanup_session.execute(delete(Site).where(Site.id == site_id))
            cleanup_session.commit()
        except Exception:
            cleanup_session.rollback()
        finally:
            cleanup_session.close()
        pooled_engine.dispose()


@pytest.mark.api
def test_concurrent_public_reads_no_pool_exhaustion(
    pooled_test_setup: tuple[TestClient, Engine, str, str],
) -> None:
    """P6.5: Multiple concurrent worker threads querying all public endpoints simultaneously do not exhaust connection pool."""
    client, engine, camp_slug, _site_id = pooled_test_setup

    public_endpoints = [
        "/api/v1/public/campaigns/current",
        "/api/v1/public/campaigns",
        f"/api/v1/public/campaigns/{camp_slug}",
        f"/api/v1/public/campaigns/{camp_slug}/sites",
        f"/api/v1/public/campaigns/{camp_slug}/photos",
        f"/api/v1/public/campaigns/{camp_slug}/counters",
        f"/api/v1/public/campaigns/{camp_slug}/heroes",
        f"/api/v1/public/campaigns/{camp_slug}/ranking",
        "/api/v1/public/sites",
    ]

    workers = 12
    requests_per_worker = 8

    def worker_task(worker_id: int) -> list[int]:
        statuses: list[int] = []
        for i in range(requests_per_worker):
            path = public_endpoints[(worker_id + i) % len(public_endpoints)]
            res = client.get(path)
            statuses.append(res.status_code)
        return statuses

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker_task, w) for w in range(workers)]
        all_statuses: list[int] = []
        for fut in concurrent.futures.as_completed(futures):
            all_statuses.extend(fut.result())

    # All requests should return 200 OK
    assert len(all_statuses) == workers * requests_per_worker
    assert all(code == 200 for code in all_statuses), f"Unexpected status codes: {all_statuses}"

    # Pool must not leak any checked out connections
    checked_out = getattr(engine.pool, "checkedout", lambda: 0)()
    assert checked_out == 0, (
        f"Connection leak detected! Pool has {checked_out} checked out connections."
    )


@pytest.mark.api
def test_concurrent_pledges_no_pool_exhaustion(
    pooled_test_setup: tuple[TestClient, Engine, str, str],
) -> None:
    """P6.5: Concurrent pledge submissions do not cause database connection starvation or deadlock."""
    client, engine, camp_slug, site_id = pooled_test_setup

    workers = 10
    requests_per_worker = 5

    def pledge_worker(worker_id: int) -> list[int]:
        statuses: list[int] = []
        for req_idx in range(requests_per_worker):
            headers = {"X-Forwarded-For": f"10.1.{worker_id}.{req_idx + 1}"}
            payload = {
                "site_id": site_id,
                "first_name": f"متعهد {worker_id}_{req_idx}",
                "phone": f"0550{worker_id:02d}{req_idx:04d}",
            }
            res = client.post(
                f"/api/v1/public/campaigns/{camp_slug}/pledges",
                json=payload,
                headers=headers,
            )
            statuses.append(res.status_code)
        return statuses

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(pledge_worker, w) for w in range(workers)]
        all_statuses: list[int] = []
        for fut in concurrent.futures.as_completed(futures):
            all_statuses.extend(fut.result())

    assert len(all_statuses) == workers * requests_per_worker
    assert all(code == 204 for code in all_statuses), f"Unexpected status codes: {all_statuses}"

    checked_out = getattr(engine.pool, "checkedout", lambda: 0)()
    assert checked_out == 0, f"Connection leak detected after pledges! Checked out: {checked_out}"


@pytest.mark.api
def test_mixed_traffic_burst_under_constrained_pool() -> None:
    """P6.5: Mixed read and write traffic under a constrained QueuePool (pool=2, overflow=2) queues gracefully without deadlock."""
    settings = get_settings()
    url = settings.test_database_url or settings.database_url

    # Highly constrained pool to simulate peak saturation
    constrained_engine = create_db_engine(
        url,
        pool_size=2,
        max_overflow=2,
        pool_timeout=5,
    )
    session_factory = sessionmaker(bind=constrained_engine, autocommit=False, autoflush=False)

    setup_session = session_factory()
    try:
        # Enforce BR-CAMP-04
        setup_session.execute(
            update(Campaign).where(Campaign.is_current.is_(True)).values(is_current=False)
        )
        setup_session.flush()

        site = create_site(setup_session, name="موقع حمل مضغوط", is_active=True)
        camp = create_campaign(
            setup_session,
            slug=f"constrained-load-{uuid.uuid4().hex[:8]}",
            status="live",
            is_published=True,
            is_current=True,
        )
        create_campaign_site(setup_session, campaign_id=camp.id, site_id=site.id)
        setup_session.commit()
        camp_id = camp.id
        camp_slug = camp.slug
        site_id = site.id
        site_id_str = str(site.id)
    finally:
        setup_session.close()

    app = create_app()

    def get_constrained_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_constrained_db
    app.dependency_overrides[get_rate_limiter_dep] = lambda: FakeRateLimiter(allow_all=True)
    client = TestClient(app)

    workers = 8
    requests_per_worker = 6

    def mixed_worker(worker_id: int) -> list[int]:
        statuses: list[int] = []
        for req_idx in range(requests_per_worker):
            headers = {"X-Forwarded-For": f"192.168.{worker_id}.{req_idx + 1}"}
            if req_idx % 2 == 0:
                res = client.get(
                    f"/api/v1/public/campaigns/{camp_slug}/counters",
                    headers=headers,
                )
            else:
                res = client.post(
                    f"/api/v1/public/campaigns/{camp_slug}/pledges",
                    json={
                        "site_id": site_id_str,
                        "first_name": f"حمل {worker_id}",
                    },
                    headers=headers,
                )
            statuses.append(res.status_code)
        return statuses

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(mixed_worker, w) for w in range(workers)]
            all_statuses: list[int] = []
            for fut in concurrent.futures.as_completed(futures):
                all_statuses.extend(fut.result())

        # All requests should either succeed (200/204) or gracefully rate-limit (429), NEVER 500 or timeout
        assert len(all_statuses) == workers * requests_per_worker
        for code in all_statuses:
            assert code in (200, 204, 429), f"Unexpected status code {code} under constrained pool"

        checked_out = getattr(constrained_engine.pool, "checkedout", lambda: 0)()
        assert checked_out == 0, f"Constrained pool connection leak! Checked out: {checked_out}"
    finally:
        clean_sess = session_factory()
        try:
            clean_sess.execute(delete(Pledge).where(Pledge.campaign_id == camp_id))
            clean_sess.execute(delete(Photo).where(Photo.campaign_id == camp_id))
            clean_sess.execute(delete(Donor).where(Donor.campaign_id == camp_id))
            clean_sess.execute(delete(CampaignSite).where(CampaignSite.campaign_id == camp_id))
            clean_sess.execute(delete(Campaign).where(Campaign.id == camp_id))
            clean_sess.execute(delete(Site).where(Site.id == site_id))
            clean_sess.commit()
        except Exception:
            clean_sess.rollback()
        finally:
            clean_sess.close()
        constrained_engine.dispose()


@pytest.mark.api
def test_cli_load_test_script_runs_successfully() -> None:
    """P6.5: Verify standalone scripts.load_test_public executes and reports zero pool exhaustion."""
    result = run_public_load_test(
        workers=4,
        requests_per_worker=5,
        pool_size=3,
        max_overflow=3,
        pool_timeout=5,
    )

    assert result.total_requests == 20
    assert result.failed_requests == 0
    assert result.pool_exhausted is False
    assert result.final_checkedout == 0
    assert not result.error_messages
