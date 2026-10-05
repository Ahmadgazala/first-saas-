"""Storage abstraction.

All file IO for uploads and generated artifacts goes through a StorageService so the
local filesystem backend can later be swapped for S3 / Cloudflare R2 / Supabase Storage.
Keys are POSIX-style relative paths such as ``generated/<job_id>/laser_sheet.svg``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.errors import ErrorCode, PuzzleError

_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")


def validate_key(key: str) -> str:
    """Reject anything that could escape the storage root (absolute paths, '..', odd chars)."""
    if not key or key.startswith("/") or "\\" in key:
        raise PuzzleError(ErrorCode.STORAGE_ERROR, "Invalid storage key.", f"key={key!r}")
    parts = key.split("/")
    for part in parts:
        if part in ("", ".", "..") or not _KEY_SEGMENT.match(part):
            raise PuzzleError(ErrorCode.STORAGE_ERROR, "Invalid storage key.", f"key={key!r}")
    return key


class StorageService(ABC):
    @abstractmethod
    def save_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def size(self, key: str) -> int: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """Filesystem path for efficient streaming, or None for remote backends."""


class LocalStorageService(StorageService):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise PuzzleError(ErrorCode.STORAGE_ERROR, "Invalid storage key.", f"escape attempt: {key!r}")
        return path

    def save_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise PuzzleError(ErrorCode.FILE_NOT_READY, "The requested file does not exist.", key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def delete_prefix(self, prefix: str) -> None:
        base = self._path(prefix)
        if base.is_dir():
            for child in sorted(base.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            base.rmdir()

    def local_path(self, key: str) -> Path | None:
        return self._path(key)
