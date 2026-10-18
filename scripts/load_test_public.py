"""Light Load Testing Script for Pledge and Public Pages (P6.5).

Per:
- docs/09-roadmap.md §4 Phase 6: "اختبار حمل خفيف على التعهد والصفحات العامة"
- docs/10-testing-security-deployment.md §9:
  "مجمّع اتصالات القاعدة: DB_POOL_SIZE + OVERFLOW أقل من حد الخطة المُدارة، وعدم نفاد الاتصالات."
- docs/12-implementation-plan.md §9 Task P6.5:
  "Light load test on pledge and public pages | scripts | — | No pool exhaustion"

Usage:
    uv run python scripts/load_test_public.py
    uv run python scripts/load_test_public.py --workers 10 --requests-per-worker 10
"""

import concurrent.futures
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import typer
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_rate_limiter_dep
from app.core.config import get_settings
from app.core.database import check_database_connection, create_db_engine, get_db
from app.core.ratelimit import FakeRateLimiter
from app.main import create_app
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.pledge import Pledge
from tests.factories.campaign import create_campaign, create_campaign_site, create_site
from tests.factories.donor import create_donor
from tests.factories.photo import create_photo

cli_app = typer.Typer(help="Execute light load testing on pledge and public endpoints.")


@dataclass
class LoadTestResult:
    """Summary metrics of the load test execution."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    status_counts: dict[int, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    pool_exhausted: bool = False
    error_messages: list[str] = field(default_factory=list)
    initial_checkedout: int = 0
    final_checkedout: int = 0
    pool_size: int = 0
    max_overflow: int = 0

    @property
    def min_latency(self) -> float:
        return min(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def avg_latency(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_latency(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_latencies = sorted(self.latencies_ms)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]

    @property
    def max_latency(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0


def provision_load_test_campaign(session: Session) -> tuple[Campaign, Site]:
    """Seed a representative live campaign and participating site for public load testing."""
    # Enforce BR-CAMP-04: clear any existing is_current flag before creating a current campaign
    session.execute(update(Campaign).where(Campaign.is_current.is_(True)).values(is_current=False))
    session.flush()

    site = create_site(
        session,
        name=f"موقع اختبار الحمل {uuid.uuid4().hex[:6]}",
        address="شارع الشهداء، الجزائر",
        is_active=True,
    )
    camp = create_campaign(
        session,
        slug=f"load-test-campaign-{uuid.uuid4().hex[:6]}",
        title="حملة اختبار الحمل الخفيف",
        summary="حملة تجريبية لفحص مجمّع اتصالات قاعدة البيانات",
        content="# تعليمات\nمحتوى تجريبي للحملة.",
        status="live",
        is_published=True,
        is_current=True,
    )
    create_campaign_site(
        session,
        campaign_id=camp.id,
        site_id=site.id,
        supervisors_text="د. رشيد",
    )
    # Add a hero donor and photo so heroes and photos endpoints return real data
    donor = create_donor(
        session,
        campaign_id=camp.id,
        site_id=site.id,
        first_name="أحمد",
        status="donated",
        consent_hero_name=True,
        consent_photo=True,
        donated_at=datetime.now(UTC),
    )
    create_photo(
        session,
        campaign_id=camp.id,
        site_id=site.id,
        kind="individual",
        donor_id=donor.id,
    )
    session.flush()
    return camp, site


def run_public_load_test(
    workers: int = 10,
    requests_per_worker: int = 10,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_timeout: int = 5,
    target_engine: Engine | None = None,
) -> LoadTestResult:
    """Execute concurrent requests across pledge and public pages and verify connection pool stability."""
    settings = get_settings()
    url = settings.test_database_url or settings.database_url

    # Dedicated engine with strict pool size to test concurrency under pressure
    engine = target_engine or create_db_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
    )

    if not check_database_connection(engine):
        raise RuntimeError("Database is unreachable for load testing.")

    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Provision campaign data
    init_session = session_factory()
    try:
        camp, site = provision_load_test_campaign(init_session)
        init_session.commit()
        camp_id = camp.id
        camp_slug = camp.slug
        site_id = site.id
        site_id_str = str(site.id)
    finally:
        init_session.close()

    # Create test app with session per request (real production dependency behavior)
    app = create_app()

    def get_pooled_db() -> Any:
        db = session_factory()
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

    # Endpoints to cycle through
    read_paths = [
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

    result = LoadTestResult(
        initial_checkedout=getattr(engine.pool, "checkedout", lambda: 0)(),
        pool_size=pool_size,
        max_overflow=max_overflow,
    )

    def worker_task(worker_id: int) -> list[tuple[int, float, str | None]]:
        worker_results: list[tuple[int, float, str | None]] = []
        # Unique IP per worker so pledge rate limit (20/hour/IP) doesn't reject different workers
        headers = {"X-Forwarded-For": f"10.0.1.{worker_id + 1}"}

        for req_idx in range(requests_per_worker):
            t0 = time.perf_counter()
            try:
                # Interleave reads and pledge writes
                if req_idx % 3 == 0:
                    # POST pledge
                    res = client.post(
                        f"/api/v1/public/campaigns/{camp_slug}/pledges",
                        json={
                            "site_id": site_id_str,
                            "first_name": f"متعهد {worker_id}_{req_idx}",
                            "phone": f"0550{worker_id:02d}{req_idx:04d}",
                        },
                        headers=headers,
                    )
                else:
                    # GET public page
                    path = read_paths[(worker_id + req_idx) % len(read_paths)]
                    res = client.get(path, headers=headers)

                duration_ms = (time.perf_counter() - t0) * 1000
                worker_results.append((res.status_code, duration_ms, None))
            except Exception as ex:
                duration_ms = (time.perf_counter() - t0) * 1000
                worker_results.append((500, duration_ms, str(ex)))

        return worker_results

    # Execute concurrent worker pool
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker_task, w_id) for w_id in range(workers)]
            for fut in concurrent.futures.as_completed(futures):
                worker_metrics = fut.result()
                for status_code, lat_ms, err_msg in worker_metrics:
                    result.total_requests += 1
                    result.latencies_ms.append(lat_ms)
                    result.status_counts[status_code] = result.status_counts.get(status_code, 0) + 1

                    if 200 <= status_code < 400 or status_code == 429:
                        # 200/204 or expected rate limit considered successful HTTP handling
                        result.successful_requests += 1
                    else:
                        result.failed_requests += 1

                    if err_msg:
                        result.error_messages.append(err_msg)
                        if "QueuePool" in err_msg or "timeout" in err_msg.lower():
                            result.pool_exhausted = True

        # Check pool state after all requests completed
        result.final_checkedout = getattr(engine.pool, "checkedout", lambda: 0)()
        if result.final_checkedout > 0:
            result.pool_exhausted = True
            result.error_messages.append(
                f"Connection leak detected! Pool has {result.final_checkedout} unreturned connections."
            )
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
        if target_engine is None:
            engine.dispose()

    return result


@cli_app.command()
def main(
    workers: int = typer.Option(10, "--workers", "-w", help="Number of concurrent worker threads"),
    requests_per_worker: int = typer.Option(
        10, "--requests-per-worker", "-r", help="Requests per worker"
    ),
    pool_size: int = typer.Option(5, "--pool-size", "-p", help="Database connection pool size"),
    max_overflow: int = typer.Option(5, "--max-overflow", "-o", help="Max overflow connections"),
    pool_timeout: int = typer.Option(5, "--pool-timeout", "-t", help="Pool timeout in seconds"),
) -> None:
    """Execute light load test on public and pledge endpoints."""
    typer.echo("")
    typer.secho(
        "=== [Blood Donation Platform] Light Load Test (P6.5) ===",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(
        f"Concurrency: {workers} workers | Requests/worker: {requests_per_worker} | "
        f"Pool: {pool_size} (overflow: {max_overflow}, timeout: {pool_timeout}s)"
    )

    t_start = time.perf_counter()
    result = run_public_load_test(
        workers=workers,
        requests_per_worker=requests_per_worker,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
    )
    total_time = time.perf_counter() - t_start

    typer.echo("")
    typer.secho("--- Results ---", fg=typer.colors.YELLOW, bold=True)
    typer.echo(f"Total Requests:      {result.total_requests}")
    typer.echo(f"Successful Requests: {result.successful_requests}")
    typer.echo(f"Failed Requests:     {result.failed_requests}")
    typer.echo(f"Throughput:          {result.total_requests / total_time:.1f} req/s")
    typer.echo(f"Status Breakdown:    {result.status_counts}")
    typer.echo(f"Latency (Min/Avg):   {result.min_latency:.1f}ms / {result.avg_latency:.1f}ms")
    typer.echo(f"Latency (P95/Max):   {result.p95_latency:.1f}ms / {result.max_latency:.1f}ms")
    typer.echo(
        f"Pool Connections:    Checked Out After Test = {result.final_checkedout} "
        f"(Initial = {result.initial_checkedout})"
    )

    if result.error_messages:
        typer.secho("\nErrors encountered:", fg=typer.colors.RED, bold=True)
        for err in result.error_messages[:5]:
            typer.secho(f"  - {err}", fg=typer.colors.RED)

    typer.echo("")
    if not result.pool_exhausted and result.failed_requests == 0:
        typer.secho(
            "==> [PASS] No Pool Exhaustion! All concurrent requests handled cleanly.",
            fg=typer.colors.GREEN,
            bold=True,
        )
    else:
        typer.secho(
            "==> [FAIL] Load test failed or pool exhaustion detected!",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    cli_app()
