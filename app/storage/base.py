"""File and image storage abstraction interfaces.

Implements storage layer contracts per docs/05-architecture.md §13 and decision D-06:
- Storage: Abstract contract for storing, retrieving, deleting, and resolving URLs for uploaded media.
- FakeStorage: In-memory test double supporting deterministic testing and failure injection.
"""

from abc import ABC, abstractmethod


class Storage(ABC):
    """Abstract interface for local or object storage providers."""

    @abstractmethod
    def save(self, file_key: str, content: bytes, content_type: str = "image/jpeg") -> str:
        """Save file content bytes under the specified key and return the stored identifier."""
        ...

    @abstractmethod
    def get(self, file_key: str) -> bytes | None:
        """Retrieve stored file bytes by key, or return None if not found."""
        ...

    @abstractmethod
    def delete(self, file_key: str) -> bool:
        """Delete file by key. Returns True if deleted, False if not found."""
        ...

    @abstractmethod
    def exists(self, file_key: str) -> bool:
        """Check whether a file exists under the specified key."""
        ...

    @abstractmethod
    def get_url(self, file_key: str) -> str:
        """Generate public or media URL for the specified storage key."""
        ...


class FakeStorage(Storage):
    """In-memory test double for media storage with failure injection capabilities."""

    def __init__(self, public_base_url: str = "/media") -> None:
        self._files: dict[str, bytes] = {}
        self._content_types: dict[str, str] = {}
        self.public_base_url = public_base_url.rstrip("/")
        self.inject_failure: bool = False

    def save(self, file_key: str, content: bytes, content_type: str = "image/jpeg") -> str:
        if self.inject_failure:
            raise OSError("Injected storage write failure")
        self._files[file_key] = content
        self._content_types[file_key] = content_type
        return file_key

    def get(self, file_key: str) -> bytes | None:
        if self.inject_failure:
            raise OSError("Injected storage read failure")
        return self._files.get(file_key)

    def delete(self, file_key: str) -> bool:
        if self.inject_failure:
            raise OSError("Injected storage delete failure")
        self._content_types.pop(file_key, None)
        return self._files.pop(file_key, None) is not None

    def exists(self, file_key: str) -> bool:
        return file_key in self._files

    def get_url(self, file_key: str) -> str:
        return f"{self.public_base_url}/{file_key.lstrip('/')}"

    def clear(self) -> None:
        """Reset in-memory storage."""
        self._files.clear()
        self._content_types.clear()
        self.inject_failure = False


# Global singleton storage provider
_GLOBAL_STORAGE: Storage | None = None


def get_storage() -> Storage:
    """Retrieve active storage provider (defaults to FakeStorage if unconfigured)."""
    global _GLOBAL_STORAGE
    if _GLOBAL_STORAGE is None:
        _GLOBAL_STORAGE = FakeStorage()
    return _GLOBAL_STORAGE


def set_storage(storage: Storage | None) -> None:
    """Override active storage provider (used by test harness and startup wiring)."""
    global _GLOBAL_STORAGE
    _GLOBAL_STORAGE = storage
