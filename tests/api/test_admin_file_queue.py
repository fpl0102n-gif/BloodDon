"""API integration tests for admin file deletion queue endpoints (P5.8).

Per:
- docs/04-api.md §20:
  - GET /admin/file-queue (file_queue:manage)
  - POST /admin/file-queue/{id}/mark-deleted (file_queue:manage)
- docs/07-test-cases.md §5.15 (T-FILE-01..03)
- Canonical permission matrix: file_queue:manage requires ACTIVE_ACCOUNT + MFA_VERIFIED.
"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session_reauth,
    generate_csrf_token,
    get_clock,
    get_db,
    get_session_service,
    get_settings,
    get_storage_dep,
    record_session_reauth,
)
from app.core.clock import FakeClock
from app.main import create_app
from app.models.campaign import Site
from app.models.photo import FileDeletionQueue
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from app.storage.base import FakeStorage


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage(public_base_url="/media")


@pytest.fixture
def test_clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC))


@pytest.fixture
def admin_user(db_session: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin_fq_api@blood.local",
        full_name="أدمن طابور الملفات",
        role="admin",
        site_id=None,
        is_active=True,
        password_hash="argon2id$mocked",
        totp_enabled=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def sup_a(db_session: Session) -> User:
    site = Site(
        id=uuid.uuid4(),
        name="موقع المشرف",
        address="شارع عام",
        is_active=True,
    )
    db_session.add(site)
    db_session.flush()

    user = User(
        id=uuid.uuid4(),
        email="sup_fq_api@blood.local",
        full_name="مشرف طابور",
        role="supervisor",
        site_id=site.id,
        is_active=True,
        password_hash="argon2id$mocked",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def file_queue_client(
    db_session: Session,
    test_clock: FakeClock,
    fake_storage: FakeStorage,
) -> TestClient:
    app = create_app()

    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def _override_clock() -> FakeClock:
        return test_clock

    def _override_storage() -> FakeStorage:
        return fake_storage

    def _override_session_service() -> SessionService:
        return SessionService(
            SessionRepository(db_session),
            clock=test_clock,
            settings=get_settings(),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_clock] = _override_clock
    app.dependency_overrides[get_storage_dep] = _override_storage
    app.dependency_overrides[get_session_service] = _override_session_service

    return TestClient(app, base_url="http://testserver")


def auth_headers(
    client: TestClient,
    db: Session,
    user: User,
    clock: FakeClock,
    *,
    mfa_verified: bool = False,
    fresh_reauth: bool = False,
) -> dict[str, str]:
    session_service = SessionService(
        SessionRepository(db),
        clock=clock,
        settings=get_settings(),
    )
    raw_token, session_obj = session_service.create_session(
        user_id=user.id,
        role=user.role,
    )
    session_obj.mfa_verified = mfa_verified
    db.flush()

    if fresh_reauth:
        record_session_reauth(session_obj.id, clock.now())
    else:
        clear_session_reauth(session_obj.id)

    client.cookies.set("session", raw_token)
    csrf_token = generate_csrf_token(raw_token)
    return {
        "X-CSRF-Token": csrf_token,
    }


def test_list_pending_file_queue_empty(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """GET /admin/file-queue returns 200 with empty list when no entries exist."""
    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.get("/api/v1/admin/file-queue", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["limit"] == 25
    assert data["offset"] == 0


def test_list_pending_file_queue_filters_deleted_items(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """GET /admin/file-queue lists only pending items (deleted_at is None)."""
    # Create 2 pending and 1 already deleted
    q1 = FileDeletionQueue(storage_key="photos/camp1/site1/f1.jpg")
    q2 = FileDeletionQueue(storage_key="photos/camp1/site1/f2.jpg")
    q3 = FileDeletionQueue(
        storage_key="photos/camp1/site1/f3.jpg",
        deleted_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
    )
    db_session.add_all([q1, q2, q3])
    db_session.flush()

    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.get("/api/v1/admin/file-queue", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    keys = [item["storage_key"] for item in data["items"]]
    assert "photos/camp1/site1/f1.jpg" in keys
    assert "photos/camp1/site1/f2.jpg" in keys
    assert "photos/camp1/site1/f3.jpg" not in keys


def test_mark_deleted_endpoint_success(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """POST /admin/file-queue/{id}/mark-deleted populates deleted_at timestamp (T-FILE-02-a)."""
    q = FileDeletionQueue(storage_key="photos/camp1/site1/mark_me.jpg")
    db_session.add(q)
    db_session.flush()

    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.post(
        f"/api/v1/admin/file-queue/{q.id}/mark-deleted",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == q.id
    assert data["storage_key"] == "photos/camp1/site1/mark_me.jpg"
    assert data["deleted_at"] is not None

    # Verify listing now excludes this item
    resp_list = file_queue_client.get("/api/v1/admin/file-queue", headers=headers)
    assert resp_list.status_code == 200
    assert resp_list.json()["total"] == 0


def test_mark_deleted_endpoint_not_found(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """POST /admin/file-queue/{id}/mark-deleted returns 404 for nonexistent id."""
    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.post(
        "/api/v1/admin/file-queue/99999/mark-deleted",
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_file_queue_permissions_across_personas(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
    sup_a: User,
) -> None:
    """Canonical permission matrix for file_queue:manage:

    - anon: 401 unauthenticated
    - supervisor: 403 forbidden
    - admin_nomfa: 403 mfa_required
    - admin: 200 OK
    """
    # 1. anon: unauthenticated visitor
    file_queue_client.cookies.clear()
    resp_anon = file_queue_client.get("/api/v1/admin/file-queue")
    assert resp_anon.status_code == 401

    # 2. supervisor: forbidden
    headers_sup = auth_headers(file_queue_client, db_session, sup_a, test_clock)
    resp_sup = file_queue_client.get("/api/v1/admin/file-queue", headers=headers_sup)
    assert resp_sup.status_code == 403

    # 3. admin_nomfa: mfa_required
    headers_nomfa = auth_headers(
        file_queue_client, db_session, admin_user, test_clock, mfa_verified=False
    )
    resp_nomfa = file_queue_client.get("/api/v1/admin/file-queue", headers=headers_nomfa)
    assert resp_nomfa.status_code == 403
    assert resp_nomfa.json()["error"]["code"] == "mfa_required"

    # 4. admin with MFA: 200 OK
    headers_admin = auth_headers(
        file_queue_client, db_session, admin_user, test_clock, mfa_verified=True
    )
    resp_admin = file_queue_client.get("/api/v1/admin/file-queue", headers=headers_admin)
    assert resp_admin.status_code == 200


def test_process_file_queue_endpoint_success(
    file_queue_client: TestClient,
    db_session: Session,
    fake_storage: FakeStorage,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """POST /admin/file-queue/process unlinks physical files and stamps deleted_at."""
    k1 = "photos/api_queue/f1.jpg"
    k2 = "photos/api_queue/f2.jpg"
    fake_storage.save(k1, b"bytes1", content_type="image/jpeg")
    fake_storage.save(k2, b"bytes2", content_type="image/jpeg")

    q1 = FileDeletionQueue(storage_key=k1)
    q2 = FileDeletionQueue(storage_key=k2)
    db_session.add_all([q1, q2])
    db_session.flush()

    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.post(
        "/api/v1/admin/file-queue/process",
        headers=headers,
        json={"batch_size": 10, "dry_run": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed_count"] == 2
    assert data["dry_run"] is False

    # Physical storage verification
    assert not fake_storage.exists(k1)
    assert not fake_storage.exists(k2)

    # Database verification
    db_session.refresh(q1)
    db_session.refresh(q2)
    assert q1.deleted_at == test_clock.now()
    assert q2.deleted_at == test_clock.now()


def test_process_file_queue_endpoint_dry_run(
    file_queue_client: TestClient,
    db_session: Session,
    fake_storage: FakeStorage,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """POST /admin/file-queue/process with dry_run=True does not unlink files or alter database."""
    k = "photos/api_queue/dry_run.jpg"
    fake_storage.save(k, b"dry_run_bytes", content_type="image/jpeg")

    q = FileDeletionQueue(storage_key=k)
    db_session.add(q)
    db_session.flush()

    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.post(
        "/api/v1/admin/file-queue/process",
        headers=headers,
        json={"batch_size": 10, "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed_count"] == 1
    assert data["dry_run"] is True

    # Storage and DB intact
    assert fake_storage.exists(k)
    db_session.refresh(q)
    assert q.deleted_at is None


def test_process_file_queue_permissions_across_personas(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
    sup_a: User,
) -> None:
    """POST /admin/file-queue/process requires FILE_QUEUE_MANAGE with MFA."""
    # 1. anon: 401
    file_queue_client.cookies.clear()
    resp_anon = file_queue_client.post(
        "/api/v1/admin/file-queue/process",
        json={"batch_size": 10, "dry_run": True},
    )
    assert resp_anon.status_code == 401

    # 2. supervisor: 403
    headers_sup = auth_headers(file_queue_client, db_session, sup_a, test_clock)
    resp_sup = file_queue_client.post(
        "/api/v1/admin/file-queue/process",
        headers=headers_sup,
        json={"batch_size": 10, "dry_run": True},
    )
    assert resp_sup.status_code == 403

    # 3. admin_nomfa: 403 mfa_required
    headers_nomfa = auth_headers(
        file_queue_client, db_session, admin_user, test_clock, mfa_verified=False
    )
    resp_nomfa = file_queue_client.post(
        "/api/v1/admin/file-queue/process",
        headers=headers_nomfa,
        json={"batch_size": 10, "dry_run": True},
    )
    assert resp_nomfa.status_code == 403
    assert resp_nomfa.json()["error"]["code"] == "mfa_required"


def test_process_file_queue_invalid_batch_size_422(
    file_queue_client: TestClient,
    db_session: Session,
    test_clock: FakeClock,
    admin_user: User,
) -> None:
    """Payload with invalid batch_size yields 422 validation error."""
    headers = auth_headers(file_queue_client, db_session, admin_user, test_clock, mfa_verified=True)
    resp = file_queue_client.post(
        "/api/v1/admin/file-queue/process",
        headers=headers,
        json={"batch_size": 0},
    )
    assert resp.status_code == 422
