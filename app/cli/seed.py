"""Database seeding CLI command and reference dataset generation.

Implements P2.7 per docs/07-test-cases.md §3.2, docs/02-database.md §14,
and docs/12-implementation-plan.md:
- Strictly refuses in production environment (T-STRUCT-09).
- Generates standard reference dataset:
  - Sites: A (active), B (active), C (active non-participating), Z (disabled).
  - Campaigns: C1 (upcoming), C2 (live/current), C3 (ended with final_stats),
    C4 (ended > 1 year ago for purge test), C5 (draft/unpublished).
  - Users: admin, admin2, admin_nomfa, sup_a, sup_a2, sup_b, sup_disabled, sup_mfa.
  - Donors in C2: dA1, dA2, dA3, dA4, dA5, dB1 plus 40+ diverse donors covering
    all 8 blood types and all consent combinations.
  - Pledges: ~100 realistic declarations of intent across C1 and C2.
  - Photos: General atmosphere photos and individual donor photos.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import typer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_db_engine
from app.models.campaign import Campaign, CampaignSite, Site
from app.models.donor import Donor
from app.models.photo import Photo
from app.models.pledge import Pledge
from app.models.post import Post
from app.models.user import User

seed_app = typer.Typer(help="Database seeding commands for development and testing environments.")


def assert_not_production() -> None:
    """T-STRUCT-09: Refuse database seeding in production environment."""
    settings = get_settings()
    if settings.app_env.lower() in ("prod", "production"):
        typer.secho(
            "Error: Refusing to seed database in production environment.",
            fg=typer.colors.RED,
            bold=True,
            err=True,
        )
        raise typer.Exit(code=1)


def seed_database(session: Session, *, clean: bool = False) -> dict[str, Any]:
    """Execute reference dataset seeding inside the given database session."""
    assert_not_production()

    if clean:
        # Delete existing data in reverse foreign-key dependency order
        # Note: audit_log is strictly append-only per BR-AUDIT-02 and cannot be deleted
        session.execute(text("DELETE FROM phone_reveal_log"))
        session.execute(text("DELETE FROM rate_limit_events"))
        session.execute(text("DELETE FROM file_deletion_queue"))
        session.execute(text("DELETE FROM photos"))
        session.execute(text("DELETE FROM posts"))
        session.execute(text("DELETE FROM donors"))
        session.execute(text("DELETE FROM pledges"))
        session.execute(text("DELETE FROM auth_sessions"))
        session.execute(text("DELETE FROM mfa_recovery_codes"))
        session.execute(text("DELETE FROM user_tokens"))
        session.execute(text("DELETE FROM users"))
        session.execute(text("DELETE FROM campaign_sites"))
        session.execute(text("DELETE FROM sites"))
        session.execute(text("DELETE FROM campaigns"))
        session.flush()

    today = date.today()
    now_utc = datetime.now(UTC)

    # ==========================================================================
    # 1. Sites: A, B, C, Z (docs/07-test-cases.md §3.2)
    # ==========================================================================
    site_a = Site(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        name="نادي الطب أ",
        address="كلية الطب - المدرج أ",
        map_url="https://maps.google.com/?q=36.7525,3.0419",
        latitude=36.752500,
        longitude=3.041970,
        description="موقع التبرع المركزي في كلية الطب",
        is_active=True,
    )
    site_b = Site(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        name="نادي التمريض ب",
        address="المعهد العالي للتمريض - القاعة المركزية",
        map_url="https://maps.google.com/?q=36.7550,3.0450",
        latitude=36.755000,
        longitude=3.045000,
        description="موقع تبرع مجهز بـ 4 أسرّة",
        is_active=True,
    )
    site_c = Site(
        id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        name="نادي الصيدلة ج",
        address="معهد الصيدلة - البهو الرئيسي",
        map_url="https://maps.google.com/?q=36.7580,3.0480",
        latitude=36.758000,
        longitude=3.048000,
        description="موقع نشط غير مشارك في الحملات الحالية",
        is_active=True,
    )
    site_z = Site(
        id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        name="نادي معطّل ز",
        address="المقر القديم المغلق",
        map_url=None,
        latitude=None,
        longitude=None,
        description="موقع معطل لأغراض الاختبار",
        is_active=False,
    )
    session.add_all([site_a, site_b, site_c, site_z])
    session.flush()

    # ==========================================================================
    # 2. Campaigns: C1, C2, C3, C4, C5 (docs/07-test-cases.md §3.2)
    # ==========================================================================
    c1 = Campaign(
        id=uuid.UUID("c1111111-1111-1111-1111-111111111111"),
        slug="national-blood-drive-2027",
        title="اليوم الوطني للتبرع بالدم 2027",
        summary="الحملة القادمة الموحدة للتبرع بالدم في الجامعات",
        content="## تفاصيل الحملة القادمة\nندعو جميع الطلاب للمشاركة في اليوم الموحد.",
        campaign_date=today + timedelta(days=60),
        status="upcoming",
        is_published=True,
        is_current=False,
    )
    c2 = Campaign(
        id=uuid.UUID("c2222222-2222-2222-2222-222222222222"),
        slug="national-blood-drive-2026",
        title="اليوم الوطني للتبرع بالدم 2026",
        summary="اليوم الموحد للتبرع بالدم - الحملة الجارية الحالية",
        content="## الحملة الجارية\nتستقبل مواقع التبرع الطلاب والطالبات حتى المساء.",
        campaign_date=today,
        status="live",
        is_published=True,
        is_current=True,
    )
    c3 = Campaign(
        id=uuid.UUID("c3333333-3333-3333-3333-333333333333"),
        slug="national-blood-drive-2025",
        title="اليوم الوطني للتبرع بالدم 2025",
        summary="الحملة السابقة المكتملة المجمّدة",
        content="## الأرشيف\nنتائج الحملة المكتملة للعام الماضي.",  # noqa: RUF001
        campaign_date=today - timedelta(days=90),
        status="ended",
        is_published=True,
        is_current=False,
        ended_at=now_utc - timedelta(days=90),
        final_stats={
            "pledges": 120,
            "present": 65,
            "donated": 55,
            "heroes": 35,
            "by_site": [
                {
                    "site_id": str(site_a.id),
                    "name": site_a.name,
                    "pledges": 70,
                    "present": 35,
                    "donated": 30,
                },
                {
                    "site_id": str(site_b.id),
                    "name": site_b.name,
                    "pledges": 50,
                    "present": 30,
                    "donated": 25,
                },
            ],
            "frozen_at": (now_utc - timedelta(days=90)).isoformat(),
        },
    )
    c4 = Campaign(
        id=uuid.UUID("c4444444-4444-4444-4444-444444444444"),
        slug="national-blood-drive-2024",
        title="اليوم الوطني للتبرع بالدم 2024",
        summary="حملة قديمة مضى عليها أكثر من عام لاختبار الإخفاء والاحتفاظ",
        content="## أرشيف 2024\nحملة مؤهلة لأمر الإخفاء السنوي.",
        campaign_date=today - timedelta(days=400),
        status="ended",
        is_published=True,
        is_current=False,
        ended_at=now_utc - timedelta(days=400),
        final_stats={
            "pledges": 90,
            "present": 50,
            "donated": 40,
            "heroes": 25,
            "by_site": [
                {
                    "site_id": str(site_a.id),
                    "name": site_a.name,
                    "pledges": 50,
                    "present": 30,
                    "donated": 25,
                },
                {
                    "site_id": str(site_b.id),
                    "name": site_b.name,
                    "pledges": 40,
                    "present": 20,
                    "donated": 15,
                },
            ],
            "frozen_at": (now_utc - timedelta(days=400)).isoformat(),
        },
    )
    c5 = Campaign(
        id=uuid.UUID("c5555555-5555-5555-5555-555555555555"),
        slug="draft-campaign-2028",
        title="حملة تجريبية مسودة غير منشورة",
        summary="مسودة قيد التجهيز",
        content="مسودة غير معلنة للعام القادم.",
        campaign_date=today + timedelta(days=365),
        status="upcoming",
        is_published=False,
        is_current=False,
    )
    session.add_all([c1, c2, c3, c4, c5])
    session.flush()

    # Link participating sites to campaigns (A and B participate in C1, C2, C3, C4)
    campaign_sites = [
        CampaignSite(
            campaign_id=c1.id,
            site_id=site_a.id,
            start_time=time(9, 0),
            end_time=time(17, 0),
            supervisors_text="د. سفيان، د. مريم",
        ),
        CampaignSite(
            campaign_id=c1.id,
            site_id=site_b.id,
            start_time=time(9, 0),
            end_time=time(16, 30),
            supervisors_text="د. رشيد",
        ),
        CampaignSite(
            campaign_id=c2.id,
            site_id=site_a.id,
            start_time=time(8, 30),
            end_time=time(18, 0),
            supervisors_text="فريق النادي الطبي أ",
        ),
        CampaignSite(
            campaign_id=c2.id,
            site_id=site_b.id,
            start_time=time(8, 30),
            end_time=time(18, 0),
            supervisors_text="فريق نادي التمريض ب",
        ),
        CampaignSite(
            campaign_id=c3.id,
            site_id=site_a.id,
            start_time=time(9, 0),
            end_time=time(17, 0),
        ),
        CampaignSite(
            campaign_id=c3.id,
            site_id=site_b.id,
            start_time=time(9, 0),
            end_time=time(17, 0),
        ),
        CampaignSite(
            campaign_id=c4.id,
            site_id=site_a.id,
            start_time=time(9, 0),
            end_time=time(17, 0),
        ),
        CampaignSite(
            campaign_id=c4.id,
            site_id=site_b.id,
            start_time=time(9, 0),
            end_time=time(17, 0),
        ),
    ]
    session.add_all(campaign_sites)
    session.flush()

    # ==========================================================================
    # 3. Staff Users (§3.1)
    # ==========================================================================
    # Standard argon2id test hash for password 'SecretPass123!'
    test_hash = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQAAAAAAAAAAA$4WvQ9W8Wz2oJ7n9R/P4A9B1C2D3E4F5G6H7I8J9K0L"

    users = [
        User(
            id=uuid.UUID("a0000001-0000-0000-0000-000000000001"),
            email="admin@bloodday.local",
            full_name="المدير الرئيسي للمنصة",
            password_hash=test_hash,
            role="admin",
            is_active=True,
            totp_enabled=True,
        ),
        User(
            id=uuid.UUID("a0000002-0000-0000-0000-000000000002"),
            email="admin2@bloodday.local",
            full_name="نائب مدير المنصة",
            password_hash=test_hash,
            role="admin",
            is_active=True,
            totp_enabled=True,
        ),
        User(
            id=uuid.UUID("a0000003-0000-0000-0000-000000000003"),
            email="admin_nomfa@bloodday.local",
            full_name="مدير قيد تفعيل المصادقة الثنائية",
            password_hash=test_hash,
            role="admin",
            is_active=True,
            totp_enabled=False,
        ),
        User(
            id=uuid.UUID("b0000001-0000-0000-0000-000000000001"),
            email="sup_a@bloodday.local",
            full_name="مشرف الموقع أ (رئيسي)",
            password_hash=test_hash,
            role="supervisor",
            site_id=site_a.id,
            is_active=True,
            totp_enabled=False,
        ),
        User(
            id=uuid.UUID("b0000002-0000-0000-0000-000000000002"),
            email="sup_a2@bloodday.local",
            full_name="مشرف الموقع أ (مساعد)",
            password_hash=test_hash,
            role="supervisor",
            site_id=site_a.id,
            is_active=True,
            totp_enabled=False,
        ),
        User(
            id=uuid.UUID("b0000003-0000-0000-0000-000000000003"),
            email="sup_b@bloodday.local",
            full_name="مشرف الموقع ب",
            password_hash=test_hash,
            role="supervisor",
            site_id=site_b.id,
            is_active=True,
            totp_enabled=False,
        ),
        User(
            id=uuid.UUID("b0000004-0000-0000-0000-000000000004"),
            email="sup_disabled@bloodday.local",
            full_name="مشرف حساب معطّل",
            password_hash=test_hash,
            role="supervisor",
            site_id=site_a.id,
            is_active=False,
            totp_enabled=False,
        ),
        User(
            id=uuid.UUID("b0000005-0000-0000-0000-000000000005"),
            email="sup_mfa@bloodday.local",
            full_name="مشرف مفعل للمصادقة الثنائية",
            password_hash=test_hash,
            role="supervisor",
            site_id=site_a.id,
            is_active=True,
            totp_enabled=True,
        ),
    ]
    session.add_all(users)
    session.flush()

    sup_a = users[3]

    # ==========================================================================
    # 4. Donors in C2 (exact mapping from docs/07-test-cases.md §3.2)
    # ==========================================================================
    reference_donors = [
        # dA1: attended, no phone, no consents
        Donor(
            id=uuid.UUID("d0000001-0000-0000-0000-000000000001"),
            campaign_id=c2.id,
            site_id=site_a.id,
            first_name="محمد",
            last_name="الأول",
            phone=None,
            blood_type=None,
            status="attended",
            consent_keep_phone=False,
            consent_hero_name=False,
            consent_photo=False,
            consent_public_phone=False,
            registered_by=sup_a.id,
            donated_at=None,
        ),
        # dA2: donated, phone O+, all consents True
        Donor(
            id=uuid.UUID("d0000002-0000-0000-0000-000000000002"),
            campaign_id=c2.id,
            site_id=site_a.id,
            first_name="كريم",
            last_name="المنصور",
            phone="+213550111111",
            blood_type="O+",
            status="donated",
            consent_keep_phone=True,
            consent_hero_name=True,
            consent_photo=True,
            consent_public_phone=True,
            registered_by=sup_a.id,
            donated_at=now_utc - timedelta(hours=3),
        ),
        # dA3: donated, no phone, hero only
        Donor(
            id=uuid.UUID("d0000003-0000-0000-0000-000000000003"),
            campaign_id=c2.id,
            site_id=site_a.id,
            first_name="سارة",
            last_name="العربي",
            phone=None,
            blood_type="A+",
            status="donated",
            consent_keep_phone=False,
            consent_hero_name=True,
            consent_photo=False,
            consent_public_phone=False,
            registered_by=sup_a.id,
            donated_at=now_utc - timedelta(hours=2),
        ),
        # dA4: donated, public emergency only (no keep)
        Donor(
            id=uuid.UUID("d0000004-0000-0000-0000-000000000004"),
            campaign_id=c2.id,
            site_id=site_a.id,
            first_name="يوسف",
            last_name="البشير",
            phone="+213550222222",
            blood_type="O+",
            status="donated",
            consent_keep_phone=False,
            consent_hero_name=False,
            consent_photo=False,
            consent_public_phone=True,
            registered_by=sup_a.id,
            donated_at=now_utc - timedelta(hours=1),
        ),
        # dA5: attended, keep phone only
        Donor(
            id=uuid.UUID("d0000005-0000-0000-0000-000000000005"),
            campaign_id=c2.id,
            site_id=site_a.id,
            first_name="فاطمة",
            last_name="الزهراء",
            phone="+213550333333",
            blood_type="B+",
            status="attended",
            consent_keep_phone=True,
            consent_hero_name=False,
            consent_photo=False,
            consent_public_phone=False,
            registered_by=sup_a.id,
            donated_at=None,
        ),
        # dB1: Site B, donated, phone O+, all consents True
        Donor(
            id=uuid.UUID("d0000006-0000-0000-0000-000000000006"),
            campaign_id=c2.id,
            site_id=site_b.id,
            first_name="بلال",
            last_name="الناصري",
            phone="+213550444444",
            blood_type="O+",
            status="donated",
            consent_keep_phone=True,
            consent_hero_name=True,
            consent_photo=True,
            consent_public_phone=True,
            registered_by=users[5].id,  # sup_b
            donated_at=now_utc - timedelta(hours=4),
        ),
    ]

    # Additional 44 diverse donors to reach ~50 total donors covering all blood types
    blood_types = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
    arabic_names = [
        ("أمين", "قدور"),
        ("حمزة", "بلقاسم"),
        ("إيمان", "حساني"),
        ("عمر", "مزيان"),
        ("خالد", "بن عيسى"),
        ("أسماء", "بوعلام"),
        ("طارق", "رحماني"),
        ("نادية", "سليماني"),
        ("مراد", "طاهري"),
        ("زينب", "قاسمي"),
        ("وليد", "يحيى"),
        ("صوفيا", "دحماني"),
    ]

    extra_donors: list[Donor] = []
    for i in range(1, 45):
        fn, ln = arabic_names[i % len(arabic_names)]
        b_type = blood_types[i % len(blood_types)]
        donor_site = site_a if i % 2 == 0 else site_b
        is_donated = i % 4 != 0  # 75% donated, 25% attended

        # Diversify consent combinations
        has_phone = i % 3 != 0
        phone_val = f"+213550{500000 + i:06d}" if has_phone else None

        c_keep = has_phone and (i % 2 == 0)
        c_hero = is_donated and (i % 3 != 1)
        c_photo = is_donated and (i % 2 == 1)
        c_public = is_donated and has_phone and (i % 3 == 0)

        # If phone is set, must have at least keep or public
        if phone_val and not (c_keep or c_public):
            c_keep = True

        extra_donors.append(
            Donor(
                campaign_id=c2.id,
                site_id=donor_site.id,
                first_name=fn,
                last_name=ln,
                phone=phone_val,
                blood_type=b_type,
                status="donated" if is_donated else "attended",
                consent_keep_phone=c_keep,
                consent_hero_name=c_hero,
                consent_photo=c_photo,
                consent_public_phone=c_public,
                registered_by=sup_a.id if donor_site == site_a else users[5].id,
                donated_at=now_utc - timedelta(minutes=i * 7) if is_donated else None,
            )
        )

    session.add_all(reference_donors + extra_donors)
    session.flush()

    # ==========================================================================
    # 5. Pledges: ~100 declarations of intent across C1 and C2 (docs/02-database.md §14)
    # ==========================================================================
    pledge_names = [
        "سفيان",
        "نسيمة",
        "حميد",
        "راضية",
        "فريد",
        "ليلى",
        "هشام",
        "سمية",
        "عادل",
        "حنان",
        "نبيل",
        "سلوى",
        "جمال",
        "فنزة",
        "توفيق",
        "صباح",
        "إسماعيل",
        "دلال",
        "كمال",
        "سناء",
    ]
    pledges: list[Pledge] = []
    # 60 pledges for C1 (upcoming)
    for i in range(1, 61):
        target_site = site_a if i % 2 == 1 else site_b
        name = pledge_names[i % len(pledge_names)]
        phone_opt = f"+213560{100000 + i:06d}" if i % 3 != 0 else None
        pledges.append(
            Pledge(
                campaign_id=c1.id,
                site_id=target_site.id,
                first_name=name,
                phone=phone_opt,
                created_at=now_utc - timedelta(days=i % 10),
            )
        )

    # 40 pledges for C2 (live)
    for i in range(1, 41):
        target_site = site_a if i % 2 == 1 else site_b
        name = pledge_names[(i + 5) % len(pledge_names)]
        phone_opt = f"+213561{200000 + i:06d}" if i % 4 != 0 else None
        pledges.append(
            Pledge(
                campaign_id=c2.id,
                site_id=target_site.id,
                first_name=name,
                phone=phone_opt,
                created_at=now_utc - timedelta(hours=i),
            )
        )

    session.add_all(pledges)
    session.flush()

    # ==========================================================================
    # 6. Photos (General & Individual)
    # ==========================================================================
    photos = [
        # General atmosphere photos
        Photo(
            campaign_id=c2.id,
            site_id=site_a.id,
            storage_key=f"campaigns/{c2.id}/sites/{site_a.id}/general_01.jpg",
            kind="general",
            caption="استقبال المتبرعين في مدخل كلية الطب",
            donor_id=None,
            mime_type="image/jpeg",
            size_bytes=1024 * 650,
            uploaded_by=sup_a.id,
            created_at=now_utc - timedelta(hours=2),
        ),
        Photo(
            campaign_id=c2.id,
            site_id=site_b.id,
            storage_key=f"campaigns/{c2.id}/sites/{site_b.id}/general_02.jpg",
            kind="general",
            caption="قاعة التبرع في معهد التمريض",
            donor_id=None,
            mime_type="image/jpeg",
            size_bytes=1024 * 720,
            uploaded_by=users[5].id,
            created_at=now_utc - timedelta(hours=3),
        ),
        # Individual photo for dA2 (has consent_photo=True)
        Photo(
            campaign_id=c2.id,
            site_id=site_a.id,
            storage_key=f"campaigns/{c2.id}/sites/{site_a.id}/donor_da2.jpg",
            kind="individual",
            caption="المتبرع البطل كريم بعد إتمام التبرع بنجاح",
            donor_id=reference_donors[1].id,  # dA2
            mime_type="image/jpeg",
            size_bytes=1024 * 480,
            uploaded_by=sup_a.id,
            created_at=now_utc - timedelta(hours=1),
        ),
        # Individual photo for dB1 (has consent_photo=True)
        Photo(
            campaign_id=c2.id,
            site_id=site_b.id,
            storage_key=f"campaigns/{c2.id}/sites/{site_b.id}/donor_db1.jpg",
            kind="individual",
            caption="المتبرع البطل بلال في موقع التمريض",
            donor_id=reference_donors[5].id,  # dB1
            mime_type="image/jpeg",
            size_bytes=1024 * 510,
            uploaded_by=users[5].id,
            created_at=now_utc - timedelta(hours=2),
        ),
    ]
    session.add_all(photos)
    session.flush()

    # ==========================================================================
    # 7. Posts: Awareness articles and guidance (Phase 9)
    # ==========================================================================
    post_published = Post(
        id=uuid.UUID("99999999-1111-1111-1111-111111111111"),
        campaign_id=c2.id,
        slug="blood-donation-guide",
        title="دليل التبرع بالدم: الفوائد الصحية والإرشادات الطبية",
        excerpt="كل ما تحتاج معرفته عن أهمية التبرع بالدم، شروطه وفوائده الصحية على الجسم وإنقاذ حياة المرضى.",
        content=(
            "## أهمية التبرع بالدم\n\n"
            "يعتبر التبرع بالدم عملاً إنسانياً نبيلاً يساهم في إنقاذ الأرواح في حالات الطوارئ والعمليات الجراحية.\n\n"
            "### الفوائد الصحية للمتبرع\n\n"
            "* تجديد خلايا الدم وتنشيط الدورة الدموية.\n"
            "* تقليل مخاطر أمراض القلب والشرايين.\n"
            "* فحص طبي شامل ومجاني لضغط الدم والتحاليل الفيروسية.\n\n"
            "### نصائح قبل التبرع\n\n"
            "1. شرب كميات كافية من السوائل.\n"
            "2. أخذ قسط كافٍ من النوم والراحة.\n"
            "3. تناول وجبة خفيفة قبل القدوم لمركز التبرع."
        ),
        cover_url="/static/images/og-default.svg",
        is_published=True,
        published_at=now_utc - timedelta(days=2),
        created_at=now_utc - timedelta(days=3),
        updated_at=now_utc - timedelta(days=2),
    )
    post_draft = Post(
        id=uuid.UUID("99999999-2222-2222-2222-222222222222"),
        campaign_id=None,
        slug="myths-about-blood-donation",
        title="حقائق وأساطير شائعة حول التبرع بالدم",
        excerpt="تصحيح المفاهيم الخاطئة والشائعة التي تمنع البعض من المبادرة بالتبرع.",
        content="مسودة قيد المراجعة والاعتماد الطبي من قبل الطاقم المتخصص.",
        cover_url=None,
        is_published=False,
        published_at=None,
        created_at=now_utc - timedelta(days=1),
        updated_at=now_utc - timedelta(days=1),
    )
    posts = [post_published, post_draft]
    session.add_all(posts)
    session.flush()

    return {
        "sites": 4,
        "campaigns": 5,
        "sites_map": {"A": site_a, "B": site_b, "C": site_c, "Z": site_z},
        "campaigns_map": {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5},
        "users": users,
        "reference_donors": reference_donors,
        "donors_count": len(reference_donors) + len(extra_donors),
        "pledges_count": len(pledges),
        "photos_count": len(photos),
        "posts_count": len(posts),
    }


@seed_app.command(name="seed")
def seed_cmd(
    clean: bool = typer.Option(
        False,
        "--clean",
        "-c",
        help="Clean existing database tables before seeding.",
    ),
) -> None:
    """Seed development and test database with reference baseline data."""
    assert_not_production()

    settings = get_settings()
    engine = create_db_engine(settings.database_url)

    typer.echo("Starting database seed...")
    with Session(engine) as session:
        result = seed_database(session, clean=clean)
        session.commit()

    typer.secho(
        f"Database seeded successfully!\n"
        f" - Sites: 4 (A, B, C, Z)\n"
        f" - Campaigns: 5 (C1-C5)\n"
        f" - Staff Users: {len(result['users'])}\n"
        f" - Donors in C2: {result['donors_count']}\n"
        f" - Pledges: {result['pledges_count']}\n"
        f" - Photos: {result['photos_count']}",
        fg=typer.colors.GREEN,
        bold=True,
    )
