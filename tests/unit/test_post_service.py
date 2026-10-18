"""Unit tests for PostService and safe Markdown rendering (P9.1).

Per:
- docs/06-business-rules.md (BR-POST-01..05)
- docs/07-test-cases.md §5.15 (T-POST-01..05)
- docs/11-decisions.md (D-01, D-70)
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.core.clock import FakeClock
from app.core.errors import (
    NotFoundError,
    SlugTakenError,
)
from app.core.markdown import render_safe_markdown
from app.models.campaign import Campaign
from app.models.post import Post
from app.schemas.post import PostCreate, PostUpdate
from app.services.audit import AuditService
from app.services.posts import PostService


class FakePostRepository:
    """In-memory double for PostRepository."""

    def __init__(self) -> None:
        self.posts_by_id: dict[uuid.UUID, Post] = {}
        self.posts_by_slug: dict[str, Post] = {}

    def get_by_id(self, post_id: uuid.UUID) -> Post | None:
        return self.posts_by_id.get(post_id)

    def get_by_slug(self, slug: str) -> Post | None:
        return self.posts_by_slug.get(slug)

    def create(self, post: Post) -> Post:
        self.posts_by_id[post.id] = post
        self.posts_by_slug[post.slug] = post
        return post

    def update(self, post: Post) -> Post:
        self.posts_by_id[post.id] = post
        self.posts_by_slug[post.slug] = post
        return post

    def delete(self, post: Post) -> None:
        self.posts_by_id.pop(post.id, None)
        self.posts_by_slug.pop(post.slug, None)

    def list_all(
        self,
        *,
        is_published: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Post]:
        items = list(self.posts_by_id.values())
        if is_published is not None:
            items = [p for p in items if p.is_published is is_published]
        return items[offset : offset + limit]

    def list_public(self, *, limit: int = 100, offset: int = 0) -> list[Post]:
        items = [p for p in self.posts_by_id.values() if p.is_published]
        items.sort(
            key=lambda x: x.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return items[offset : offset + limit]


class FakeCampaignRepository:
    """In-memory double for CampaignRepository."""

    def __init__(self) -> None:
        self.campaigns: dict[uuid.UUID, Campaign] = {}

    def get_by_id(self, campaign_id: uuid.UUID) -> Campaign | None:
        return self.campaigns.get(campaign_id)


class FakeAuditService(AuditService):
    """In-memory audit recorder."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_event(self, **kwargs) -> None:  # type: ignore[override]
        self.events.append(kwargs)


@pytest.fixture
def post_service_env() -> tuple[
    PostService, FakePostRepository, FakeCampaignRepository, FakeClock, FakeAuditService
]:
    repo = FakePostRepository()
    camp_repo = FakeCampaignRepository()
    clock = FakeClock(datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC))
    audit = FakeAuditService()
    service = PostService(
        repository=repo,  # type: ignore[arg-type]
        campaign_repository=camp_repo,  # type: ignore[arg-type]
        audit_service=audit,
        clock=clock,
    )
    return service, repo, camp_repo, clock, audit


@pytest.mark.unit
@pytest.mark.br("BR-POST-01")
def test_t_post_01_a_duplicate_slug_raises_conflict(
    post_service_env: tuple[
        PostService, FakePostRepository, FakeCampaignRepository, FakeClock, FakeAuditService
    ],
) -> None:
    """T-POST-01-a: Rejects duplicate slug with SlugTakenError (409) (BR-POST-01)."""
    service, _, _, _, _ = post_service_env

    payload = PostCreate(
        title="First Article",
        slug="first-article",
        content="Some content",
    )
    service.create_post(payload)

    # Attempt duplicate creation
    with pytest.raises(SlugTakenError):
        service.create_post(payload)


@pytest.mark.unit
@pytest.mark.br("BR-POST-02")
def test_t_post_02_a_published_at_set_on_first_publish_only(
    post_service_env: tuple[
        PostService, FakePostRepository, FakeCampaignRepository, FakeClock, FakeAuditService
    ],
) -> None:
    """T-POST-02-a: published_at is populated on first publish only and preserved across unpublish/republish."""
    service, _, _, clock, _ = post_service_env

    # 1. Create as draft
    post = service.create_post(
        PostCreate(
            title="Draft Post",
            slug="draft-post",
            content="Content",
            is_published=False,
        )
    )
    assert post.is_published is False
    assert post.published_at is None

    # 2. Advance clock and publish for the FIRST time
    first_publish_time = datetime(2026, 10, 19, 9, 30, 0, tzinfo=UTC)
    clock.set_time(first_publish_time)

    updated = service.update_post(post.id, PostUpdate(is_published=True))
    assert updated.is_published is True
    assert updated.published_at == first_publish_time

    # 3. Advance clock and unpublish
    unpublish_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    clock.set_time(unpublish_time)

    unpublished = service.update_post(post.id, PostUpdate(is_published=False))
    assert unpublished.is_published is False
    assert unpublished.published_at == first_publish_time  # historical timestamp preserved

    # 4. Advance clock and publish AGAIN
    second_publish_time = datetime(2026, 10, 21, 14, 0, 0, tzinfo=UTC)
    clock.set_time(second_publish_time)

    republished = service.update_post(post.id, PostUpdate(is_published=True))
    assert republished.is_published is True
    # Crucial BR-POST-02 assertion: must NOT overwrite with second_publish_time!
    assert republished.published_at == first_publish_time


@pytest.mark.unit
@pytest.mark.br("BR-POST-03")
def test_t_post_03_a_draft_post_raises_not_found_on_public_query(
    post_service_env: tuple[
        PostService, FakePostRepository, FakeCampaignRepository, FakeClock, FakeAuditService
    ],
) -> None:
    """T-POST-03-a: get_public_post raises NotFoundError (404) for draft posts (BR-POST-03)."""
    service, _, _, _, _ = post_service_env

    # Draft post
    service.create_post(
        PostCreate(
            title="Hidden Draft",
            slug="hidden-draft",
            content="Draft content",
            is_published=False,
        )
    )

    with pytest.raises(NotFoundError):
        service.get_public_post("hidden-draft")


@pytest.mark.unit
@pytest.mark.br("BR-POST-04")
def test_t_post_04_a_content_size_limit_100kb(
    post_service_env: tuple[
        PostService, FakePostRepository, FakeCampaignRepository, FakeClock, FakeAuditService
    ],
) -> None:
    """T-POST-04-a: Markdown content exceeding 100 KB (102,400 bytes) raises ValidationError (422)."""
    service, _, _, _, _ = post_service_env

    # Exactly 100 KB = 102,400 bytes
    valid_content = "a" * 102400
    post = service.create_post(
        PostCreate(
            title="Max Length Post",
            slug="max-length-post",
            content=valid_content,
        )
    )
    assert post.content == valid_content

    # 100 KB + 1 byte = 102,401 bytes
    excessive_content = "a" * 102401
    with pytest.raises(ValueError):  # Schema validator raises ValueError which converts to 422
        PostCreate(
            title="Excessive Post",
            slug="excessive-post",
            content=excessive_content,
        )


@pytest.mark.unit
@pytest.mark.br("BR-POST-04")
@pytest.mark.critical
def test_t_post_04_b_markdown_sanitization_removes_xss() -> None:
    """T-POST-04-b ★: Markdown containing unsafe elements like <iframe>, <script>, or event handlers is sanitized."""
    malicious_markdown = (
        "# حملة التوعية بالتبرع\n\n"
        "مرحبًا بالجميع.\n\n"
        "<iframe src='https://malicious.evil/steal-cookie'></iframe>\n\n"
        "<script>window.location='http://evil.com'</script>\n\n"
        "<img src='valid.png' onerror='alert(1)'>\n\n"
        "**نص عريض آمن**"
    )

    clean_html = render_safe_markdown(malicious_markdown)

    assert "<h1>حملة التوعية بالتبرع</h1>" in clean_html
    assert "<strong>نص عريض آمن</strong>" in clean_html
    assert "<iframe" not in clean_html.lower()
    assert "malicious.evil" not in clean_html
    assert "<script" not in clean_html.lower()
    assert "onerror" not in clean_html.lower()
    assert "alert" not in clean_html


@pytest.mark.unit
@pytest.mark.br("BR-POST-05")
def test_t_post_05_campaign_link_validation(
    post_service_env: tuple[
        PostService, FakePostRepository, FakeCampaignRepository, FakeClock, FakeAuditService
    ],
) -> None:
    """Verify linking to a valid campaign succeeds, while a non-existent campaign raises NotFoundError."""
    service, _, camp_repo, _, _ = post_service_env

    camp_id = uuid.uuid4()
    camp_repo.campaigns[camp_id] = Campaign(
        id=camp_id,
        title="Official Drive",
        slug="official-drive",
    )

    # Valid link
    post = service.create_post(
        PostCreate(
            title="Linked Post",
            slug="linked-post",
            campaign_id=camp_id,
        )
    )
    assert post.campaign_id == camp_id

    # Invalid campaign link raises NotFoundError
    with pytest.raises(NotFoundError):
        service.create_post(
            PostCreate(
                title="Bad Link Post",
                slug="bad-link-post",
                campaign_id=uuid.uuid4(),
            )
        )
