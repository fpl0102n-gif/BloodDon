"""CLI command for physical file deletion queue batch processing (P8.6).

Per:
- docs/06-business-rules.md §15 (BR-FILE-01..03: Queue-driven physical storage deletion)
- docs/07-test-cases.md §5.15 (T-FILE-02, T-FILE-03: Batch processing and storage unlinking)
- docs/08-setup-env.md §12.6 (Weekly scheduled file deletion job)
- docs/11-decisions.md (Decision D-54: --dry-run option)
- docs/05-architecture.md §17 (CLI Typer interface)
"""

import typer

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.repositories.audit import AuditRepository
from app.repositories.file_queue import FileQueueRepository
from app.services.audit import AuditService
from app.services.file_queue import FileQueueService
from app.storage.factory import create_storage


def process_file_queue_cmd(
    batch_size: int = typer.Option(
        50,
        "--batch-size",
        "-b",
        help="Maximum number of queued files to process in this run.",
        min=1,
        max=500,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate queue processing without unlinking files or updating the database.",
    ),
) -> None:
    """Process pending file deletion queue entries, physically deleting unlinked files from storage."""
    settings = get_settings()
    clock = SystemClock()
    storage = create_storage(settings)

    with SessionLocal() as session:
        audit_repo = AuditRepository(session)
        audit_service = AuditService(audit_repo, clock=clock)
        file_queue_repo = FileQueueRepository(session)
        file_queue_service = FileQueueService(
            file_queue_repo=file_queue_repo,
            storage=storage,
            clock=clock,
            audit_service=audit_service,
            settings=settings,
        )

        mode_str = "[DRY-RUN] " if dry_run else ""
        typer.secho(
            f"{mode_str}Processing file deletion queue (batch size: {batch_size})...",
            fg=typer.colors.CYAN,
            bold=True,
        )

        try:
            processed_count = file_queue_service.process_queue(
                batch_size=batch_size,
                dry_run=dry_run,
            )

            _, remaining_count = file_queue_service.list_pending(limit=1, offset=0)

            if not dry_run:
                session.commit()
                typer.secho(
                    f"Successfully processed {processed_count} queued file(s). "
                    f"Remaining pending: {remaining_count}.",
                    fg=typer.colors.GREEN,
                    bold=True,
                )
            else:
                typer.secho(
                    f"[DRY-RUN] Would process {processed_count} queued file(s). "
                    f"Remaining pending: {remaining_count}. Zero modifications made.",
                    fg=typer.colors.YELLOW,
                    bold=True,
                )

        except Exception as exc:
            session.rollback()
            typer.secho(f"File queue processing failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
