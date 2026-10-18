"""Unit and integration tests for the file deletion queue CLI command (P8.6).

Per:
- docs/06-business-rules.md §15 (BR-FILE-01..03: Queue-driven physical storage deletion)
- docs/07-test-cases.md §5.15 (T-FILE-02, T-FILE-03: Batch processing and storage unlinking)
- docs/08-setup-env.md §12.6 (Weekly scheduled file deletion job)
- docs/11-decisions.md (Decision D-54: --dry-run option)
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.models.photo import FileDeletionQueue
from app.storage.base import FakeStorage


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage(public_base_url="/media")


@contextmanager
def mock_session_local(session: Session):
    """Context manager yielding the test db_session without closing or rolling back the outer test transaction."""
    yield session


def test_cli_process_file_queue_help() -> None:
    """Invoking process-file-queue --help succeeds with usage instructions."""
    runner = CliRunner()
    result = runner.invoke(cli_app, ["process-file-queue", "--help"])
    assert result.exit_code == 0
    assert "Process pending file deletion queue entries" in result.stdout
    assert "--batch-size" in result.stdout
    assert "--dry-run" in result.stdout


def test_cli_process_file_queue_dry_run(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    """CLI process-file-queue --dry-run reports candidate count without modifying DB or storage."""
    # Setup test file in storage and queue
    key = "photos/cli_test/dry_run_candidate.jpg"
    fake_storage.save(key, b"candidate_content", content_type="image/jpeg")

    q = FileDeletionQueue(storage_key=key)
    db_session.add(q)
    db_session.flush()

    runner = CliRunner()
    with (
        patch("app.cli.file_queue.create_storage", return_value=fake_storage),
        patch(
            "app.cli.file_queue.SessionLocal", side_effect=lambda: mock_session_local(db_session)
        ),
    ):
        result = runner.invoke(cli_app, ["process-file-queue", "--dry-run", "--batch-size", "10"])

    assert result.exit_code == 0
    assert "[DRY-RUN]" in result.stdout
    assert "Zero modifications made" in result.stdout

    # Physical file still exists
    assert fake_storage.exists(key)

    # Queue item still pending
    db_session.expire_all()
    refreshed = db_session.get(FileDeletionQueue, q.id)
    assert refreshed is not None
    assert refreshed.deleted_at is None


def test_cli_process_file_queue_live_execution(
    db_session: Session,
    fake_storage: FakeStorage,
) -> None:
    """CLI process-file-queue physically unlinks files and stamps deleted_at in database."""
    k1 = "photos/cli_test/file1.jpg"
    k2 = "photos/cli_test/file2.jpg"
    fake_storage.save(k1, b"bytes1", content_type="image/jpeg")
    fake_storage.save(k2, b"bytes2", content_type="image/jpeg")

    q1 = FileDeletionQueue(storage_key=k1)
    q2 = FileDeletionQueue(storage_key=k2)
    db_session.add_all([q1, q2])
    db_session.flush()

    runner = CliRunner()
    with (
        patch("app.cli.file_queue.create_storage", return_value=fake_storage),
        patch(
            "app.cli.file_queue.SessionLocal", side_effect=lambda: mock_session_local(db_session)
        ),
    ):
        result = runner.invoke(cli_app, ["process-file-queue", "-b", "25"])

    assert result.exit_code == 0
    assert "Successfully processed" in result.stdout

    # Physical files unlinked
    assert not fake_storage.exists(k1)
    assert not fake_storage.exists(k2)

    # Queue items marked deleted
    db_session.expire_all()
    refreshed_q1 = db_session.get(FileDeletionQueue, q1.id)
    refreshed_q2 = db_session.get(FileDeletionQueue, q2.id)
    assert refreshed_q1 is not None and refreshed_q1.deleted_at is not None
    assert refreshed_q2 is not None and refreshed_q2.deleted_at is not None


def test_cli_process_file_queue_empty(db_session: Session, fake_storage: FakeStorage) -> None:
    """CLI process-file-queue with empty queue succeeds reporting 0 processed."""
    # Ensure no pending items exist
    pending = db_session.scalars(
        select(FileDeletionQueue).where(FileDeletionQueue.deleted_at.is_(None))
    ).all()
    for item in pending:
        item.deleted_at = db_session.bind  # type: ignore
    db_session.flush()

    runner = CliRunner()
    with (
        patch("app.cli.file_queue.create_storage", return_value=fake_storage),
        patch(
            "app.cli.file_queue.SessionLocal", side_effect=lambda: mock_session_local(db_session)
        ),
    ):
        result = runner.invoke(cli_app, ["process-file-queue"])

    assert result.exit_code == 0
    assert "Successfully processed 0 queued file(s)" in result.stdout


def test_cli_process_file_queue_invalid_batch_size() -> None:
    """Batch size of 0 is rejected by Typer constraint."""
    runner = CliRunner()
    result = runner.invoke(cli_app, ["process-file-queue", "--batch-size", "0"])
    assert result.exit_code != 0
