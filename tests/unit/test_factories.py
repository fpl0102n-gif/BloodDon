"""Unit tests for test model factories."""

import uuid
from datetime import date, time

import pytest

from tests.factories import (
    create_audit_log,
    create_auth_session,
    create_campaign,
    create_campaign_site,
    create_donor,
    create_file_deletion_queue,
    create_mfa_recovery_code,
    create_phone_reveal_log,
    create_photo,
    create_pledge,
    create_rate_limit_event,
    create_site,
    create_user,
    create_user_token,
)


@pytest.mark.unit
def test_campaign_factories() -> None:
    """Verify campaign, site, and campaign_site factories generate valid instances."""
    camp = create_campaign(title="حملة الربيع", status="upcoming", is_published=True)
    assert camp.title == "حملة الربيع"
    assert camp.status == "upcoming"
    assert camp.is_published is True
    assert isinstance(camp.id, uuid.UUID)
    assert isinstance(camp.campaign_date, date)

    site = create_site(name="نادي العلوم", is_active=True)
    assert site.name == "نادي العلوم"
    assert site.is_active is True
    assert isinstance(site.id, uuid.UUID)

    cs = create_campaign_site(
        campaign_id=camp.id,
        site_id=site.id,
        start_time=time(9, 0),
        end_time=time(16, 0),
    )
    assert cs.campaign_id == camp.id
    assert cs.site_id == site.id
    assert cs.start_time is not None
    assert cs.end_time is not None
    assert cs.start_time < cs.end_time


@pytest.mark.unit
def test_user_factories() -> None:
    """Verify user, token, and session factories generate valid instances."""
    # Admin does not require site_id
    admin = create_user(role="admin", email="admin.test@example.com")
    assert admin.role == "admin"
    assert admin.email == "admin.test@example.com"
    assert admin.site_id is None

    # Supervisor requires and automatically receives site_id if omitted
    sup = create_user(role="supervisor", email="sup.test@example.com")
    assert sup.role == "supervisor"
    assert sup.site_id is not None

    token = create_user_token(user_id=admin.id, purpose="invite")
    assert token.user_id == admin.id
    assert token.purpose == "invite"
    assert len(token.token_hash) == 64

    code = create_mfa_recovery_code(user_id=admin.id)
    assert code.user_id == admin.id
    assert len(code.code_hash) == 64

    sess = create_auth_session(user_id=admin.id, mfa_verified=True)
    assert sess.user_id == admin.id
    assert sess.mfa_verified is True


@pytest.mark.unit
def test_donor_and_pledge_factories() -> None:
    """Verify donor and pledge factories generate valid model instances respecting CHECKs."""
    cid = uuid.uuid4()
    sid = uuid.uuid4()

    pledge = create_pledge(campaign_id=cid, site_id=sid, first_name="علي")
    assert pledge.first_name == "علي"
    assert pledge.campaign_id == cid
    assert pledge.site_id == sid

    # Donor 1: attended (donated_at must be None)
    d1 = create_donor(campaign_id=cid, site_id=sid, status="attended")
    assert d1.status == "attended"
    assert d1.donated_at is None

    # Donor 2: donated (donated_at auto-populated)
    d2 = create_donor(campaign_id=cid, site_id=sid, status="donated")
    assert d2.status == "donated"
    assert d2.donated_at is not None

    # Donor 3: with phone (consent_keep_phone auto-enabled to prevent check violation)
    d3 = create_donor(campaign_id=cid, site_id=sid, phone="+213550123456")
    assert d3.phone == "+213550123456"
    assert d3.consent_keep_phone is True

    # Donor 4: public phone (auto-populates phone and blood_type to satisfy public_phone_needs_data)
    d4 = create_donor(campaign_id=cid, site_id=sid, consent_public_phone=True)
    assert d4.consent_public_phone is True
    assert d4.phone is not None
    assert d4.blood_type is not None


@pytest.mark.unit
def test_photo_and_audit_factories() -> None:
    """Verify photo, deletion queue, audit, and log factories generate valid instances."""
    cid = uuid.uuid4()
    sid = uuid.uuid4()

    # General photo has no donor_id
    p_gen = create_photo(campaign_id=cid, site_id=sid, kind="general")
    assert p_gen.kind == "general"
    assert p_gen.donor_id is None

    # Individual photo requires and auto-populates donor_id
    p_ind = create_photo(campaign_id=cid, site_id=sid, kind="individual")
    assert p_ind.kind == "individual"
    assert p_ind.donor_id is not None

    queue_entry = create_file_deletion_queue(storage_key="test_key.jpg")
    assert queue_entry.storage_key == "test_key.jpg"

    audit = create_audit_log(action="campaign.start", entity_type="campaign")
    assert audit.action == "campaign.start"
    assert audit.details is not None
    assert "old_status" in audit.details

    reveal = create_phone_reveal_log(ip_hash="test_ip_hash")
    assert reveal.ip_hash == "test_ip_hash"
    assert isinstance(reveal.donor_id, uuid.UUID)

    rate = create_rate_limit_event(key_hash="test_key_hash", action="login")
    assert rate.action == "login"
