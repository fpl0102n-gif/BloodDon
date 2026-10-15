"""File and image storage abstraction interfaces and local/cloud storage implementations."""

from app.storage.base import FakeStorage, Storage, get_storage, set_storage

__all__ = [
    "FakeStorage",
    "Storage",
    "get_storage",
    "set_storage",
]
