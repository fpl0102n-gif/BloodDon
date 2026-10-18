"""Local disk file storage implementation.

Per docs/05-architecture.md §13 and decision D-06:
- LocalStorage: Implements Storage interface writing to server filesystem.
- Enforces strict path normalization and traversal prevention.
"""

from pathlib import Path

from app.storage.base import Storage


class LocalStorage(Storage):
    """Local filesystem storage provider for uploads and media assets."""

    def __init__(
        self,
        root_path: str | Path = "./var/uploads",
        public_base_url: str = "/media",
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.public_base_url = str(public_base_url).rstrip("/")
        self.root_path.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, file_key: str) -> Path:
        """Resolve file key under root_path, preventing path traversal attacks."""
        clean_key = file_key.lstrip("/\\")
        target_path = (self.root_path / clean_key).resolve()
        try:
            target_path.relative_to(self.root_path)
        except ValueError as exc:
            raise ValueError(f"Path traversal detected in storage key '{file_key}'") from exc
        return target_path

    def save(self, file_key: str, content: bytes, content_type: str = "image/jpeg") -> str:
        """Save file bytes to local disk under file_key."""
        del content_type  # Unused in local filesystem storage
        target_path = self._resolve_path(file_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)
        return file_key

    def get(self, file_key: str) -> bytes | None:
        """Read file bytes from local disk."""
        target_path = self._resolve_path(file_key)
        if not target_path.is_file():
            return None
        return target_path.read_bytes()

    def delete(self, file_key: str) -> bool:
        """Delete local file. Returns True if deleted, False if not found."""
        target_path = self._resolve_path(file_key)
        if target_path.is_file():
            target_path.unlink()
            return True
        return False

    def exists(self, file_key: str) -> bool:
        """Check if local file exists on disk."""
        target_path = self._resolve_path(file_key)
        return target_path.is_file()

    def get_url(self, file_key: str) -> str:
        """Resolve public media URL for file key."""
        clean_key = file_key.lstrip("/")
        return f"{self.public_base_url}/{clean_key}"
