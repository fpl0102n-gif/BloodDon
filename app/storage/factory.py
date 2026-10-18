"""Storage factory creating storage provider based on application settings.

Breaks circular dependencies by providing a dedicated factory layer above
base abstractions and concrete backends (LocalStorage, FakeStorage).
"""

from app.core.config import Settings, get_settings
from app.storage.base import FakeStorage, Storage
from app.storage.local import LocalStorage


def create_storage(settings: Settings | None = None) -> Storage:
    """Instantiate appropriate storage implementation (LocalStorage or FakeStorage)."""
    cfg = settings or get_settings()
    if cfg.storage_backend == "local":
        return LocalStorage(
            root_path=cfg.storage_local_path,
            public_base_url=cfg.storage_public_base_url,
        )
    return FakeStorage(public_base_url=cfg.storage_public_base_url)
