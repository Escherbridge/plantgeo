"""Read-only `AvailabilityStorage` over a local capture root, so `validate` runs before `stage` (F8)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.soil_survey.receipts import ROOT, SoilSurveyError, digest
from agri_data_service.pipeline.parquet.availability_storage import StoredAvailabilityObject

if TYPE_CHECKING:
    from pathlib import Path

_OBJECT_KEY: Final = re.compile(rf"{re.escape(ROOT)}/objects/([0-9a-f]{{64}})")
_MANIFEST_KEY: Final = re.compile(rf"{re.escape(ROOT)}/manifests/([0-9a-f]{{64}})\.json")


@dataclass(frozen=True, slots=True)
class LocalCandidateStorage:
    """Map bucket keys onto `root/objects/<sha>` and `root/candidate-<sha>.json`; refuse every write."""

    root: Path

    def path_for(self, key: str) -> Path | None:
        """Local file a bucket key would stage from, or None for a key outside the candidate namespace."""
        if (match := _OBJECT_KEY.fullmatch(key)) is not None:
            return self.root / "objects" / match.group(1)
        if (match := _MANIFEST_KEY.fullmatch(key)) is not None:
            return self.root / f"candidate-{match.group(1)}.json"
        return None

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        path = self.path_for(key)
        if path is None or not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            raise SoilSurveyError(f"local object {key} exceeds its {max_bytes}-byte read ceiling")
        payload = path.read_bytes()
        return StoredAvailabilityObject(payload=payload, etag=digest(payload))

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        raise SoilSurveyError(f"local candidate storage is read-only: {key} ({len(payload)} B, {content_type})")

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        raise SoilSurveyError(
            f"local candidate storage is read-only: {key} ({len(payload)} B, {expected_etag}, {content_type})"
        )


__all__ = ["LocalCandidateStorage"]
