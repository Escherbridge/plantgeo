"""Bounded local immutable storage with locked atomic pointer updates."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from filelock import FileLock

from agri_data_service.pipeline.parquet.availability_index import StoredAvailabilityObject
from agri_data_service.warehouse.botanical_species_profiles.contract import (
    MAX_OBJECT_BYTES,
    ProfileConflictError,
    ProfileError,
    content_sha256,
)

FIRST_PRINTABLE_CODEPOINT = 32


class LocalAvailabilityStorage:
    """Implement AvailabilityStorage over an isolated local publication directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        parts = PurePosixPath(key)
        if (
            not key
            or key != str(parts)
            or parts.is_absolute()
            or "\\" in key
            or any(part in {".", ".."} for part in parts.parts)
            or any(ord(char) < FIRST_PRINTABLE_CODEPOINT for char in key)
            or ":" in key
        ):
            raise ProfileError("publication storage keys must be canonical relative object paths")
        path = self.root.joinpath(*parts.parts)
        if not path.resolve().is_relative_to(self.root):
            raise ProfileError("publication storage key escapes its local root")
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != self.root.parent):
            raise ProfileError("local publication does not follow symbolic links")
        return path

    def _lock(self, key: str) -> FileLock:
        path = self._path(f".locks/{content_sha256(key.encode())}.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(path, timeout=10)

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Read one atomically visible bounded object and its exact-content ETag."""
        if isinstance(max_bytes, bool) or not 0 < max_bytes <= MAX_OBJECT_BYTES:
            raise ProfileError("local reads require a positive bounded byte ceiling")
        path = self._path(key)
        try:
            with path.open("rb") as handle:
                if os.fstat(handle.fileno()).st_size > max_bytes:
                    raise ProfileError("local publication object exceeds its read byte ceiling")
                payload = handle.read(max_bytes + 1)
        except FileNotFoundError:
            return None
        if len(payload) > max_bytes:
            raise ProfileError("local publication object grew beyond its read byte ceiling")
        return StoredAvailabilityObject(payload=payload, etag=content_sha256(payload))

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Create one immutable file or adopt only an identical byte-for-byte replay."""
        self._validate_payload(payload, content_type)
        with self._lock(key):
            existing = self.read(key, max_bytes=MAX_OBJECT_BYTES)
            if existing is not None:
                if existing.payload != payload:
                    raise ProfileConflictError("immutable botanical publication object already has different bytes")
                return
            self._replace(key, payload)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        """Conditionally replace one pointer under an interprocess file lock."""
        self._validate_payload(payload, content_type)
        with self._lock(key):
            existing = self.read(key, max_bytes=MAX_OBJECT_BYTES)
            if (existing.etag if existing else None) != expected_etag:
                return False
            self._replace(key, payload)
            return True

    @staticmethod
    def _validate_payload(payload: bytes, content_type: str) -> None:
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_OBJECT_BYTES or not content_type:
            raise ProfileError("publication payload requires bounded nonempty bytes and a content type")

    def _replace(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".publish-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
