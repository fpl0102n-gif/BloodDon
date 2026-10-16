"""Tests for the database seeding CLI command and reference dataset generation.

Implements T-STRUCT-09 per docs/07-test-cases.md §3.2, §7, and docs/12-implementation-plan.md P2.7:
- T-STRUCT-09: seed strictly refuses in production environment (APP_ENV=prod / production).
- Verify baseline dataset generation: Sites A/B/C/Z, Campaigns C1-C5, staff users,
  and reference donors dA1-dB1 in C2.
"""

import pytest
from typer.testing import CliRunner

from app.cli.__main__ import cli_app
from app.cli.seed import assert_not_production, seed_database
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.pledge import Pledge
from app.models.user import User

runner = CliRunner()


@pytest.mark.unit
def test_t_struct_09_seed_refuses_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-STRUCT-09: Seeding must refuse to run when APP_ENV is 'prod' or 'production'."""
    # Test with app_env="prod"
    mock_prod = type("MockSettings", (), {"app_env": "prod"})()
    monkeypatch.setattr("app.cli.seed.get_settings", lambda: mock_prod)

    # 1. Direct function assertion raises Exit(1)
    with pytest.raises(Exception) as exc_info:
        assert_not_production()
    assert getattr(exc_info.value, "exit_code", None) == 1

    # 2. CLI command invocation fails with code 1 and prints refusal message
    result = runner.invoke(cli_app, ["seed"])
    assert result.exit_code == 1
    assert "Refusing to seed database in production environment" in result.output

    # Test with app_env="production"
    mock_production = type("MockSettings", (), {"app_env": "production"})()
    monkeypatch.setattr("app.cli.seed.get_settings", lambda: mock_production)

    result_alt = runner.invoke(cli_app, ["seed"])
    assert result_alt.exit_code == 1
    assert "Refusing to seed database in production environment" in result_alt.output


@pytest.mark.db
def test_seed_database_builds_complete_dataset(isolated_db) -> None:
    """Verify that seed_database builds reference sites, campaigns, users, donors, and pledges."""
    session = isolated_db
    summary = seed_database(session, clean=True)
    assert summary["sites"] == 4
    assert summary["campaigns"] == 5

    # 1. Verify Sites A, B, C, Z (§3.2)
    sites = session.query(Site).all()
    site_names = {s.name: s for s in sites}
    assert "نادي الطب أ" in site_names
    assert "نادي التمريض ب" in site_names
    assert "نادي الصيدلة ج" in site_names
    assert "نادي معطّل ز" in site_names

    assert site_names["نادي الطب أ"].is_active is True
    assert site_names["نادي التمريض ب"].is_active is True
    assert site_names["نادي الصيدلة ج"].is_active is True
    assert site_names["نادي معطّل ز"].is_active is False

    # 2. Verify Campaigns C1-C5 (§3.2)
    campaigns = session.query(Campaign).all()
    camp_map = {c.slug: c for c in campaigns}
    assert "national-blood-drive-2027" in camp_map  # C1: upcoming
    assert "national-blood-drive-2026" in camp_map  # C2: live/current
    assert "national-blood-drive-2025" in camp_map  # C3: ended with final_stats
    assert "national-blood-drive-2024" in camp_map  # C4: ended > 1 year ago
    assert "draft-campaign-2028" in camp_map  # C5: draft

    c1 = camp_map["national-blood-drive-2027"]
    c2 = camp_map["national-blood-drive-2026"]
    c3 = camp_map["national-blood-drive-2025"]
    c5 = camp_map["draft-campaign-2028"]

    assert c1.status == "upcoming" and c1.is_published is True and c1.is_current is False
    assert c2.status == "live" and c2.is_published is True and c2.is_current is True
    assert c3.status == "ended" and c3.is_published is True and c3.final_stats is not None
    assert c5.status == "upcoming" and c5.is_published is False

    # 3. Verify CampaignSites: Sites A and B participate in C1, C2, C3, C4
    c2_sites = session.query(CampaignSite).filter(CampaignSite.campaign_id == c2.id).all()
    c2_site_ids = {cs.site_id for cs in c2_sites}
    site_a = site_names["نادي الطب أ"]
    site_b = site_names["نادي التمريض ب"]
    assert site_a.id in c2_site_ids
    assert site_b.id in c2_site_ids

    # 4. Verify Staff Users (§3.1)
    users = session.query(User).all()
    user_emails = {u.email: u for u in users}
    assert "admin@bloodday.local" in user_emails
    assert "admin2@bloodday.local" in user_emails
    assert "admin_nomfa@bloodday.local" in user_emails
    assert "sup_a@bloodday.local" in user_emails
    assert "sup_a2@bloodday.local" in user_emails
    assert "sup_b@bloodday.local" in user_emails
    assert "sup_disabled@bloodday.local" in user_emails
    assert "sup_mfa@bloodday.local" in user_emails

    admin = user_emails["admin@bloodday.local"]
    assert admin.role == "admin"
    assert admin.is_active is True
    assert admin.totp_enabled is True

    sup_a = user_emails["sup_a@bloodday.local"]
    assert sup_a.role == "supervisor"
    assert sup_a.site_id == site_a.id
    assert sup_a.is_active is True

    sup_dis = user_emails["sup_disabled@bloodday.local"]
    assert sup_dis.is_active is False

    # 5. Verify Reference Donors dA1-dB1 in C2 (§3.2)
    donors = session.query(Donor).filter(Donor.campaign_id == c2.id).all()
    assert len(donors) >= 50

    donor_by_fn = {d.first_name: d for d in donors}

    # dA1: attended, no phone, no consents
    da1 = donor_by_fn["محمد"]
    assert da1.status == "attended"
    assert da1.site_id == site_a.id
    assert da1.phone is None
    assert da1.consent_keep_phone is False
    assert da1.consent_hero_name is False
    assert da1.consent_photo is False
    assert da1.consent_public_phone is False

    # dA2: donated, O+, all consents
    da2 = donor_by_fn["كريم"]
    assert da2.status == "donated"
    assert da2.site_id == site_a.id
    assert da2.blood_type == "O+"
    assert da2.consent_keep_phone is True
    assert da2.consent_hero_name is True
    assert da2.consent_photo is True
    assert da2.consent_public_phone is True
    assert da2.donated_at is not None

    # dA3: donated, A+, hero only, no phone
    da3 = donor_by_fn["سارة"]
    assert da3.status == "donated"
    assert da3.blood_type == "A+"
    assert da3.consent_hero_name is True
    assert da3.phone is None

    # dA4: donated, O+, public phone only
    da4 = donor_by_fn["يوسف"]
    assert da4.status == "donated"
    assert da4.blood_type == "O+"
    assert da4.consent_public_phone is True
    assert da4.phone is not None

    # dA5: attended, B+, keep phone only
    da5 = donor_by_fn["فاطمة"]
    assert da5.status == "attended"
    assert da5.blood_type == "B+"
    assert da5.consent_keep_phone is True
    assert da5.donated_at is None

    # dB1: Site B, donated, O+, all consents
    db1 = donor_by_fn["بلال"]
    assert db1.status == "donated"
    assert db1.site_id == site_b.id
    assert db1.blood_type == "O+"
    assert db1.consent_hero_name is True
    assert db1.consent_photo is True
    assert db1.consent_public_phone is True

    # 6. Verify Pledges and Photos
    pledges = session.query(Pledge).all()
    assert len(pledges) >= 90

    photos = session.query(Photo).all()
    assert len(photos) >= 4
    individual_photos = [p for p in photos if p.kind == "individual"]
    general_photos = [p for p in photos if p.kind == "general"]
    assert len(individual_photos) >= 2
    assert len(general_photos) >= 2
    for ip in individual_photos:
        assert ip.donor_id is not None
    for gp in general_photos:
        assert gp.donor_id is None
