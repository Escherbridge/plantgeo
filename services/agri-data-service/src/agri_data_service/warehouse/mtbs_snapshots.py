"""Validated current MTBS snapshot descriptors; see warehouse/AGENTS.md."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

MTBS_SNAPSHOT_SCHEMA: Final = "mtbs-current-snapshot/v1"
MTBS_SNAPSHOT_SOURCE_URL: Final = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query"
MTBS_SNAPSHOT_LANE_ROOT: Final = "layer=burn-severity/kind=observed"
MTBS_SNAPSHOT_FIRST_DAY: Final = date(2026, 9, 11)
MTBS_SNAPSHOT_MAX_ROWS: Final = 2000
MTBS_SNAPSHOT_MAX_CONSISTENCY_LENGTH: Final = 2000
MTBS_SNAPSHOT_MAX_RESPONSES: Final = 400
MTBS_SNAPSHOT_MAX_RAW_BYTES: Final = 400 * 1024 * 1024
MTBS_SNAPSHOT_FIELDS: Final = frozenset(
    {
        "schema",
        "manifest_sha256",
        "mode",
        "source_url",
        "bbox",
        "crs",
        "covered_years",
        "captured_from",
        "captured_through",
        "available_day",
        "capture_complete",
        "partial_fire_years",
        "source_row_count",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _utc(value: object) -> datetime:
    _require(isinstance(value, str), "snapshot capture time must be text")
    parsed = datetime.fromisoformat(str(value))
    _require(parsed.utcoffset() == timedelta(0), "snapshot capture time must be UTC-aware")
    return parsed


def _digest(value: object) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid snapshot digest")
    return str(value)


@dataclass(frozen=True, slots=True)
class MtbsSnapshotDescriptor:
    """A complete query capture with explicitly incomplete fire-season mapping."""

    manifest_sha256: str
    captured_from: datetime
    captured_through: datetime
    available_day: date
    source_row_count: int

    def __post_init__(self) -> None:
        _digest(self.manifest_sha256)
        _require(self.captured_from.utcoffset() == timedelta(0), "capture start must be UTC-aware")
        _require(self.captured_through.utcoffset() == timedelta(0), "capture end must be UTC-aware")
        _require(
            timedelta(0) <= self.captured_through - self.captured_from <= timedelta(seconds=600),
            "invalid capture interval",
        )
        _require(
            self.available_day == self.captured_through.date() + timedelta(days=1), "snapshot availability must be D+1"
        )
        _require(self.available_day >= MTBS_SNAPSHOT_FIRST_DAY, "snapshot precedes its governed ownership floor")
        _require(
            type(self.source_row_count) is int and 0 <= self.source_row_count <= MTBS_SNAPSHOT_MAX_ROWS,
            "invalid snapshot source row count",
        )

    @property
    def release_identifier(self) -> str:
        return f"mtbs-current-snapshot:{self.manifest_sha256}"

    @property
    def manifest_key(self) -> str:
        return f"{MTBS_SNAPSHOT_LANE_ROOT}/availability/mtbs-snapshots/manifest={self.manifest_sha256}.json"

    def to_wire(self) -> dict[str, object]:
        return {
            "schema": MTBS_SNAPSHOT_SCHEMA,
            "manifest_sha256": self.manifest_sha256,
            "mode": "full_replacement",
            "source_url": MTBS_SNAPSHOT_SOURCE_URL,
            "bbox": [-125.0, 42.0, -111.0, 49.0],
            "crs": "EPSG:4326",
            "covered_years": {"from": 2018, "to": 2026},
            "captured_from": self.captured_from.isoformat(),
            "captured_through": self.captured_through.isoformat(),
            "available_day": self.available_day.isoformat(),
            "capture_complete": True,
            "partial_fire_years": [2023, 2024, 2025, 2026],
            "source_row_count": self.source_row_count,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> MtbsSnapshotDescriptor:
        _require(set(value) == MTBS_SNAPSHOT_FIELDS, "unexpected snapshot descriptor fields")
        _require(isinstance(value["available_day"], str), "snapshot day must be text")
        _require(value["capture_complete"] is True, "snapshot capture must be complete")
        _require(type(value["source_row_count"]) is int, "snapshot row count must be integer")
        result = cls(
            manifest_sha256=_digest(value["manifest_sha256"]),
            captured_from=_utc(value["captured_from"]),
            captured_through=_utc(value["captured_through"]),
            available_day=date.fromisoformat(str(value["available_day"])),
            source_row_count=int(str(value["source_row_count"])),
        )
        _require(dict(value) == result.to_wire(), "snapshot descriptor differs from its governed scope")
        return result


def descriptor_from_manifest(payload: bytes, *, expected_sha256: str) -> MtbsSnapshotDescriptor:
    """Verify canonical manifest identity, bounded receipt declarations and exact captured scope."""
    _require(hashlib.sha256(payload).hexdigest() == _digest(expected_sha256), "snapshot manifest digest mismatch")
    value = json.loads(payload)
    _require(isinstance(value, dict), "snapshot manifest must be an object")
    expected_fields = MTBS_SNAPSHOT_FIELDS - {"manifest_sha256"} | {
        "counts_by_year",
        "responses",
        "consistency",
        "source_content_sha256",
    }
    _require(set(value) == expected_fields, "unexpected snapshot manifest fields")
    _require(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() == payload,
        "snapshot manifest is not canonical",
    )
    descriptor = MtbsSnapshotDescriptor.from_wire(
        {
            **{key: value[key] for key in MTBS_SNAPSHOT_FIELDS - {"manifest_sha256"}},
            "manifest_sha256": expected_sha256,
        }
    )
    _digest(value["source_content_sha256"])
    counts = value["counts_by_year"]
    _require(
        isinstance(counts, dict) and set(counts) == {str(year) for year in range(2018, 2027)},
        "snapshot year counts differ",
    )
    _require(all(type(count) is int and count >= 0 for count in counts.values()), "invalid snapshot year count")
    _require(sum(counts.values()) == descriptor.source_row_count, "snapshot count total differs")
    responses = value["responses"]
    _require(
        isinstance(responses, list) and 0 < len(responses) <= MTBS_SNAPSHOT_MAX_RESPONSES,
        "invalid snapshot response inventory",
    )
    raw_bytes = 0
    for response in responses:
        _require(isinstance(response, dict), "invalid snapshot response receipt")
        _digest(response.get("sha256"))
        size = response.get("bytes")
        _require(type(size) is int and 0 < size <= 20 * 1024 * 1024, "invalid response byte count")
        raw_bytes += size
    _require(raw_bytes <= MTBS_SNAPSHOT_MAX_RAW_BYTES, "snapshot raw-response budget exceeded")
    _require(
        isinstance(value["consistency"], str) and 0 < len(value["consistency"]) <= MTBS_SNAPSHOT_MAX_CONSISTENCY_LENGTH,
        "snapshot consistency limits missing",
    )
    return descriptor
