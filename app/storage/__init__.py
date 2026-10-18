"""File and image storage abstraction interfaces and local/cloud storage implementations."""

from app.storage.base import FakeStorage, Storage, get_storage, set_storage
from app.storage.factory import create_storage
from app.storage.images import ProcessedImage, process_uploaded_image
from app.storage.local import LocalStorage

__all__ = [
    "FakeStorage",
    "LocalStorage",
    "ProcessedImage",
    "Storage",
    "create_storage",
    "get_storage",
    "process_uploaded_image",
    "set_storage",
]
