"""Prepare an exact-day FIRMS archive candidate from supplied captured responses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agri_data_service.pipeline.fire_detections_recovery.prepare import prepare_fire_recovery


def main() -> None:
    """Write a new local candidate directory; no capture credentials or apply option."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    candidate = prepare_fire_recovery(
        source_root=args.source_root, expected_manifest_sha256=args.source_manifest_sha256, output=args.output
    )
    print(json.dumps(candidate, allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
