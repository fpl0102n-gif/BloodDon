"""Phase 4 Exit Gate: End-to-End Campaign Setup Scripted Scenario.

Per docs/09-roadmap.md §3 and docs/12-implementation-plan.md §7 Exit Gate:
"Exit gate: scripted scenario (httpie/pytest) prepares a full campaign:
 sites → campaign → participation → publish → set-current → open → invite supervisors."

Verifies the entire Phase 4 administrative lifecycle:
1. Admin authentication with MFA & step-up reauth (R).
2. Sites creation (Site Central, Site University).
3. Campaign creation in draft (unpublished).
4. Participation configuration (adding participating sites).
5. Campaign publication (revealing to public catalog).
6. Setting campaign as platform current.
7. Opening campaign to 'live' status (one live campaign invariant).
8. Inviting site supervisors assigned to participating sites.
9. Uploading campaign banner via admin media upload pipeline.
10. Public discovery verification from visitor perspective.
11. Complete non-PII audit trail verification across all lifecycle events.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    generate_csrf_token,
    get_clock,
    get_settings,
    record_session_reauth,
)
from app.models.photo import Photo
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.services.sessions import SessionService
from app.storage import FakeStorage
from tests.conftest import SENTINEL_FIRST_NAME, SENTINEL_LAST_NAME, SENTINEL_PHONE
from tests.factories.user import create_user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    """Create a verified administrator user for full campaign setup scenario."""
    return create_user(
        db_session,
        email="superadmin@bloodday.local",
        full_name="الأدمن الرئيسي",
        role="admin",
        is_active=True,
        totp_enabled=True,
    )


def attach_admin_auth(
    test_client: TestClient,
    db_session: Session,
    admin: User,
) -> dict[str, str]:
    """Attach session cookie with valid MFA and fresh step-up reauth (R valid)."""
    settings = get_settings()
    clock = get_clock()
    session_repo = SessionRepository(db_session)
    session_service = SessionService(session_repo, clock=clock, settings=settings)

    raw_token, session = session_service.create_session(
        admin.id,
        admin.role,
        mfa_verified=True,
    )
    # Record fresh reauth timestamp within 10-minute window
    record_session_reauth(session.id, clock.now())

    csrf_token = generate_csrf_token(raw_token, secret_key=settings.secret_key)
    test_client.cookies.set(settings.cookie_name, raw_token)
    return {"X-CSRF-Token": csrf_token}


def make_test_banner() -> bytes:
    """Generate in-memory valid JPEG banner image."""
    img = Image.new("RGB", (800, 400), (180, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.mark.api
@pytest.mark.critical
def test_p4_exit_gate_full_campaign_setup_scenario(
    test_client: TestClient,
    db_session: Session,
    admin_user: User,
    fake_storage: FakeStorage,
) -> None:
    """Execute complete Phase 4 lifecycle scenario satisfying all completion gate criteria."""
    auth_headers = attach_admin_auth(test_client, db_session, admin_user)

    # ==========================================================================
    # Step 1: Create physical donation sites (P4.1)
    # ==========================================================================
    res_site1 = test_client.post(
        "/api/v1/admin/sites",
        json={
            "name": "مستشفى مصطفى باشا الجامعي",
            "address": "ساحة أول ماي، الجزائر العاصمة",
            "description": "المستشفى الجامعي المركزي",
        },
        headers=auth_headers,
    )
    assert res_site1.status_code == 201, f"Failed to create Site 1: {res_site1.text}"
    site1 = res_site1.json()
    site1_id = site1["id"]

    res_site2 = test_client.post(
        "/api/v1/admin/sites",
        json={
            "name": "جامعة باب الزوار - كلية الطب",
            "address": "باب الزوار، الجزائر العاصمة",
            "description": "الحرم الجامعي الطبي",
        },
        headers=auth_headers,
    )
    assert res_site2.status_code == 201, f"Failed to create Site 2: {res_site2.text}"
    site2 = res_site2.json()
    site2_id = site2["id"]

    # ==========================================================================
    # Step 2: Create a draft campaign (P4.2)
    # ==========================================================================
    campaign_slug = f"national-day-{uuid.uuid4().hex[:8]}"
    res_camp = test_client.post(
        "/api/v1/admin/campaigns",
        json={
            "title": "اليوم الوطني الموحد للتبرع بالدم 2026",
            "slug": campaign_slug,
            "summary": "حملة سنوية موحدة للتبرع بالدم في المستشفيات والجامعات",
            "content": "## تفاصيل الحملة\nمرحبًا بكم في فعاليات **يوم التبرع بالدم** الموحد.",  # noqa: RUF001
            "campaign_date": "2026-11-20",
        },
        headers=auth_headers,
    )
    assert res_camp.status_code == 201, f"Failed to create campaign: {res_camp.text}"
    camp = res_camp.json()
    camp_id = camp["id"]
    assert camp["status"] == "upcoming"
    assert camp["is_published"] is False

    # Verify that draft campaign is completely invisible to public catalog (BR-CAMP-03)
    pub_detail = test_client.get(f"/api/v1/public/campaigns/{campaign_slug}")
    assert pub_detail.status_code == 404, "Draft campaign must return 404 to public"

    # ==========================================================================
    # Step 3: Configure campaign-sites participation (P4.3)
    # ==========================================================================
    res_part1 = test_client.put(
        f"/api/v1/admin/campaigns/{camp_id}/sites/{site1_id}",
        json={
            "start_time": "08:00:00",
            "end_time": "17:00:00",
            "supervisors_text": "فريق النادي الطبي - مصطفى باشا",
            "notes": "الخيمة الطبية عند المدخل الرئيسي",
        },
        headers=auth_headers,
    )
    assert res_part1.status_code == 200, f"Failed to add site1 participation: {res_part1.text}"

    res_part2 = test_client.put(
        f"/api/v1/admin/campaigns/{camp_id}/sites/{site2_id}",
        json={
            "start_time": "09:00:00",
            "end_time": "16:00:00",
            "supervisors_text": "فريق النادي الطبي - باب الزوار",
            "notes": "بهو كلية الطب",
        },
        headers=auth_headers,
    )
    assert res_part2.status_code == 200, f"Failed to add site2 participation: {res_part2.text}"

    # ==========================================================================
    # Step 4: Publish the campaign (P4.2)
    # ==========================================================================
    res_pub = test_client.patch(
        f"/api/v1/admin/campaigns/{camp_id}",
        json={"is_published": True},
        headers=auth_headers,
    )
    assert res_pub.status_code == 200, f"Failed to publish campaign: {res_pub.text}"
    assert res_pub.json()["is_published"] is True

    # Public can now discover the published campaign
    pub_camp_res = test_client.get(f"/api/v1/public/campaigns/{campaign_slug}")
    assert pub_camp_res.status_code == 200
    assert pub_camp_res.json()["title"] == "اليوم الوطني الموحد للتبرع بالدم 2026"

    # ==========================================================================
    # Step 5: Mark campaign as current (P4.4)
    # ==========================================================================
    res_curr = test_client.post(
        f"/api/v1/admin/campaigns/{camp_id}/set-current",
        headers=auth_headers,
    )
    assert res_curr.status_code == 200, f"Failed to set current: {res_curr.text}"

    # Homepage /public/campaigns/current now returns this campaign
    res_pub_curr = test_client.get("/api/v1/public/campaigns/current")
    assert res_pub_curr.status_code == 200
    assert res_pub_curr.json()["slug"] == campaign_slug

    # ==========================================================================
    # Step 6: Open the campaign to 'live' status (P4.4)
    # ==========================================================================
    res_open = test_client.post(
        f"/api/v1/admin/campaigns/{camp_id}/open",
        headers=auth_headers,
    )
    assert res_open.status_code == 200, f"Failed to open campaign: {res_open.text}"
    assert res_open.json()["status"] == "live"

    # ==========================================================================
    # Step 7: Invite site supervisors (P4.5)
    # ==========================================================================
    res_sup1 = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": f"supervisor.hosp.{uuid.uuid4().hex[:6]}@bloodday.local",
            "full_name": "د. سمير بن علي",
            "role": "supervisor",
            "site_id": site1_id,
        },
        headers=auth_headers,
    )
    assert res_sup1.status_code == 201, f"Failed to invite supervisor 1: {res_sup1.text}"
    sup1 = res_sup1.json()
    assert sup1["role"] == "supervisor"
    assert sup1["site_id"] == site1_id

    res_sup2 = test_client.post(
        "/api/v1/admin/users",
        json={
            "email": f"supervisor.univ.{uuid.uuid4().hex[:6]}@bloodday.local",
            "full_name": "د. نادية قاسمي",
            "role": "supervisor",
            "site_id": site2_id,
        },
        headers=auth_headers,
    )
    assert res_sup2.status_code == 201, f"Failed to invite supervisor 2: {res_sup2.text}"
    sup2 = res_sup2.json()
    assert sup2["role"] == "supervisor"
    assert sup2["site_id"] == site2_id

    # ==========================================================================
    # Step 8: Upload admin banner image (P4.7)
    # ==========================================================================
    banner_bytes = make_test_banner()
    res_upload = test_client.post(
        "/api/v1/admin/uploads",
        files={"file": ("campaign_cover.jpg", banner_bytes, "image/jpeg")},
        headers=auth_headers,
    )
    assert res_upload.status_code == 201, f"Failed to upload banner: {res_upload.text}"
    upload_body = res_upload.json()
    assert upload_body["key"].startswith("admin/")
    assert fake_storage.exists(upload_body["key"]) is True

    # Verify zero rows in photos table for admin cover/logo upload (BR-PHOTO-14)
    assert db_session.scalar(select(Photo)) is None

    # Attach cover_url to campaign
    res_update_cover = test_client.patch(
        f"/api/v1/admin/campaigns/{camp_id}",
        json={"cover_url": upload_body["url"]},
        headers=auth_headers,
    )
    assert res_update_cover.status_code == 200
    assert res_update_cover.json()["cover_url"] == upload_body["url"]

    # ==========================================================================
    # Step 9: Verify public views & discovery (P4.1, P4.2, P4.3)
    # ==========================================================================
    # Public active sites
    pub_sites = test_client.get("/api/v1/public/sites").json()
    pub_site_ids = {s["id"] for s in pub_sites}
    assert site1_id in pub_site_ids
    assert site2_id in pub_site_ids

    # Public participating sites with times and team names
    pub_camp_sites = test_client.get(f"/api/v1/public/campaigns/{campaign_slug}/sites").json()
    assert len(pub_camp_sites) == 2
    camp_site_names = {cs["name"] for cs in pub_camp_sites}
    assert "مستشفى مصطفى باشا الجامعي" in camp_site_names
    assert "جامعة باب الزوار - كلية الطب" in camp_site_names

    # ==========================================================================
    # Step 10: Verify Audit Trail & Zero PII Leakage (P4.6, BR-AUDIT-01..05)
    # ==========================================================================
    res_audit = test_client.get("/api/v1/admin/audit?limit=50", headers=auth_headers)
    assert res_audit.status_code == 200
    audit_events = res_audit.json()
    logged_actions = {evt["action"] for evt in audit_events}

    expected_lifecycle_actions = {
        "site.create",
        "campaign.create",
        "campaign_site.add",
        "campaign.update",
        "campaign.set_current",
        "campaign.open",
        "user.invite",
        "photo.upload",
    }
    missing_actions = expected_lifecycle_actions - logged_actions
    assert not missing_actions, f"Audit log missing lifecycle actions: {missing_actions}"

    # Sentinel sweep: verify leak-detection sentinels never appear in any audit details
    audit_text = str(audit_events)
    assert SENTINEL_FIRST_NAME not in audit_text
    assert SENTINEL_LAST_NAME not in audit_text
    assert SENTINEL_PHONE not in audit_text
