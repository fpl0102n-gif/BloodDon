"""Test factories for campaigns, sites, and campaign_sites."""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignSite, Site

_slug_counter = 0


def create_campaign(
    session: Session | None = None,
    *,
    id_: uuid.UUID | None = None,
    slug: str | None = None,
    title: str = "حملة التبرع بالدم التجريبية",
    summary: str | None = "موجز الحملة التجريبية",
    content: str | None = "محتوى الحملة التفصيلي التجريبي",
    cover_url: str | None = None,
    campaign_date: date | None = None,
    status: str = "upcoming",
    is_published: bool = False,
    is_current: bool = False,
    final_stats: dict[str, Any] | None = None,
    ended_at: datetime | None = None,
    personal_data_purged_at: datetime | None = None,
    **kwargs: Any,
) -> Campaign:
    """Create a Campaign model instance with valid constraints."""
    global _slug_counter
    _slug_counter += 1

    if slug is None:
        slug = f"campaign-test-{_slug_counter}-{uuid.uuid4().hex[:6]}"

    if campaign_date is None:
        campaign_date = date.today() + timedelta(days=14)

    campaign = Campaign(
        id=id_ or uuid.uuid4(),
        slug=slug,
        title=title,
        summary=summary,
        content=content,
        cover_url=cover_url,
        campaign_date=campaign_date,
        status=status,
        is_published=is_published,
        is_current=is_current,
        final_stats=final_stats,
        ended_at=ended_at,
        personal_data_purged_at=personal_data_purged_at,
        **kwargs,
    )

    if session is not None:
        session.add(campaign)
        session.flush()

    return campaign


_site_counter = 0


def create_site(
    session: Session | None = None,
    *,
    id_: uuid.UUID | None = None,
    name: str | None = None,
    logo_key: str | None = None,
    address: str = "المقر التجريبي للكلية",
    map_url: str | None = "https://maps.google.com/?q=36.75,3.05",
    latitude: float | None = 36.752500,
    longitude: float | None = 3.041970,
    description: str | None = "موقع تبرع مجهز بـ 6 أسرّة",
    is_active: bool = True,
    **kwargs: Any,
) -> Site:
    """Create a Site model instance with valid constraints."""
    global _site_counter
    _site_counter += 1

    if name is None:
        name = f"نادي طبي تجريبي {_site_counter}"

    site = Site(
        id=id_ or uuid.uuid4(),
        name=name,
        logo_key=logo_key,
        address=address,
        map_url=map_url,
        latitude=latitude,
        longitude=longitude,
        description=description,
        is_active=is_active,
        **kwargs,
    )

    if session is not None:
        session.add(site)
        session.flush()

    return site


def create_campaign_site(
    session: Session | None = None,
    *,
    campaign_id: uuid.UUID,
    site_id: uuid.UUID,
    start_time: time | None = time(8, 30),
    end_time: time | None = time(17, 0),
    supervisors_text: str | None = "د. أحمد، د. سارة",
    notes: str | None = "مدخل رئيسي واسع",
    **kwargs: Any,
) -> CampaignSite:
    """Link a Campaign and Site via CampaignSite."""
    cs = CampaignSite(
        campaign_id=campaign_id,
        site_id=site_id,
        start_time=start_time,
        end_time=end_time,
        supervisors_text=supervisors_text,
        notes=notes,
        **kwargs,
    )

    if session is not None:
        session.add(cs)
        session.flush()

    return cs
