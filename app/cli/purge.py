"""CLI command for automated retention purge and periodic log cleanup (P8.3).

Implements P8.3 per:
- docs/06-business-rules.md §12 (BR-PRIV-01..08, BR-PRIV-11, BR-PRIV-12, BR-PRIV-15)
- docs/07-test-cases.md §5.10 (T-PRIV-01..08, T-PRIV-11, T-CONC-08)
- docs/11-decisions.md (Decision D-54: --dry-run option)
- docs/05-architecture.md §17 (CLI Typer interface)
- docs/08-setup-env.md §7 (Periodic execution via cron or orchestrator)
"""

import typer

from app.core.clock import SystemClock
from app.core.database import SessionLocal
from app.repositories.audit import AuditRepository
from app.services.audit import AuditService
from app.services.retention import RetentionService


def purge_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate the retention purge without modifying the database.",
    ),
) -> None:
    """Execute annual retention purge for qualifying campaigns and periodic log cleanup."""
    with SessionLocal() as session:
        clock = SystemClock()
        audit_repo = AuditRepository(session)
        audit_service = AuditService(audit_repo, clock=clock)
        retention_service = RetentionService(
            session=session,
            clock=clock,
            audit_service=audit_service,
        )

        mode_str = "[DRY-RUN] " if dry_run else ""
        typer.secho(
            f"{mode_str}Starting blood donation platform retention purge...",
            fg=typer.colors.CYAN,
            bold=True,
        )

        try:
            result = retention_service.purge(dry_run=dry_run)

            typer.echo(f"  • Campaigns evaluated: {result.campaigns_evaluated}")
            typer.echo(f"  • Campaigns purged: {result.campaigns_purged}")
            if result.campaigns_failed > 0:
                typer.secho(
                    f"  • Campaigns failed: {result.campaigns_failed}",
                    fg=typer.colors.YELLOW,
                )

            for cr in result.campaign_results:
                status_color = (
                    typer.colors.GREEN if cr.status in ("purged", "dry_run") else typer.colors.RED
                )
                typer.secho(
                    f"    - Campaign '{cr.slug}' [{cr.status}]: "
                    f"donors anonymized={cr.donors_anonymized} (heroes={cr.heroes_retained}), "
                    f"pledges deleted={cr.pledges_deleted}, "
                    f"photos queued={cr.photos_enqueued}, "
                    f"final_stats generated={cr.final_stats_generated}"
                    + (f" (error: {cr.error})" if cr.error else ""),
                    fg=status_color,
                )

            typer.echo("  • Periodic cleanup:")
            typer.echo(f"    - Phone reveal logs deleted (>7 days): {result.phone_reveals_deleted}")
            typer.echo(f"    - Expired user tokens deleted: {result.tokens_deleted}")
            typer.echo(f"    - Expired sessions deleted: {result.sessions_deleted}")

            if not dry_run:
                session.commit()
                typer.secho(
                    "Retention purge and periodic cleanup successfully committed.",
                    fg=typer.colors.GREEN,
                    bold=True,
                )
            else:
                typer.secho(
                    "[DRY-RUN] Simulation complete. Zero database modifications made.",
                    fg=typer.colors.YELLOW,
                    bold=True,
                )

        except Exception as exc:
            session.rollback()
            typer.secho(f"Retention purge failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
