"""Disk-only SHA-256 rehash of the two frozen inputs pinned by the
model-delivery public-evaluation track. No database, no network. See
`conductor/tracks/model_delivery_public_evaluation_20260726/spec.md`
("Immutable inputs") for the bound paths/digests this module re-verifies,
and `decision-record-2026-08-14.md` in the same directory for the receipt
this module produced and the lane decisions it fed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agri_data_service.foundation.canonical import sha256_digest, utc_now

# Digests bound in spec.md's "Immutable inputs" table. A mismatch here means the
# pinned evaluation input has drifted from what was reviewed, and per spec.md
# "A checksum, source/release, support, unit, or availability-clock mismatch
# stops the affected lane" -- this module exists to make that check real rather
# than asserted.
GHISACONUS_CSV_EXPECTED_SHA256 = "e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5"
FORECAST_MANIFEST_EXPECTED_SHA256 = "1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09"

DEFAULT_GHISACONUS_CSV_PATH = Path(r"C:\tmp\plantgeo-kaggle-ghisaconus-v1\GHISACONUS_2008_001_speclib.csv")
DEFAULT_FORECAST_MANIFEST_PATH = Path(r"C:\tmp\plantgeo-frozen-forecast-20260726\manifest.json")


class FrozenInputRehash(BaseModel):
    """Re-hash outcome for one frozen input, read from disk and compared to its pinned digest."""

    model_config = ConfigDict(frozen=True)

    label: str
    path: str
    expected_sha256: str
    actual_sha256: str
    byte_count: int
    matches: bool
    checked_at: str


class RehashReceipt(BaseModel):
    """Combined rehash outcome for both frozen inputs bound to the public-evaluation track."""

    model_config = ConfigDict(frozen=True)

    inputs: tuple[FrozenInputRehash, ...]
    all_match: bool


def rehash_frozen_input(path: Path, expected_sha256: str, label: str) -> FrozenInputRehash:
    """Read `path` fully from disk, SHA-256 it, and compare against `expected_sha256`.

    Raises `FileNotFoundError` if `path` is not a file; never fabricates a digest
    for a missing input.
    """
    if not path.is_file():
        raise FileNotFoundError(f"{label}: frozen input not found at {path}")
    content = path.read_bytes()
    actual = sha256_digest(content)
    expected = expected_sha256.lower()
    return FrozenInputRehash(
        label=label,
        path=str(path),
        expected_sha256=expected,
        actual_sha256=actual,
        byte_count=len(content),
        matches=actual == expected,
        checked_at=utc_now().isoformat(),
    )


def rehash_public_evaluation_frozen_inputs(
    ghisaconus_csv_path: Path = DEFAULT_GHISACONUS_CSV_PATH,
    forecast_manifest_path: Path = DEFAULT_FORECAST_MANIFEST_PATH,
) -> RehashReceipt:
    """Rehash both frozen inputs bound in spec.md's "Immutable inputs" table.

    Neither lane may proceed past Phase 0 if either input fails to rehash; the
    caller reports `all_match` loudly rather than proceeding on a partial pass.
    """
    inputs = (
        rehash_frozen_input(ghisaconus_csv_path, GHISACONUS_CSV_EXPECTED_SHA256, "ghisaconus_csv_v1"),
        rehash_frozen_input(forecast_manifest_path, FORECAST_MANIFEST_EXPECTED_SHA256, "frozen_forecast_manifest_v1"),
    )
    return RehashReceipt(inputs=inputs, all_match=all(item.matches for item in inputs))


def main() -> None:
    """Rehash both frozen public-evaluation inputs and print a JSON receipt; exits 1 on any mismatch."""
    parser = argparse.ArgumentParser(
        description="Re-hash the two frozen inputs pinned by model_delivery_public_evaluation_20260726."
    )
    parser.add_argument("--ghisaconus-csv", type=Path, default=DEFAULT_GHISACONUS_CSV_PATH)
    parser.add_argument("--forecast-manifest", type=Path, default=DEFAULT_FORECAST_MANIFEST_PATH)
    args = parser.parse_args()
    receipt = rehash_public_evaluation_frozen_inputs(args.ghisaconus_csv, args.forecast_manifest)
    print(json.dumps(receipt.model_dump(), sort_keys=True, indent=2))
    if not receipt.all_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
