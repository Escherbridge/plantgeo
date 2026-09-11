"""Prepare saved static SoilGrids bytes or inspect one candidate point; never publish."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from agri_data_service.pipeline.static_soil.point import open_verified_soil_bundle, soil_point
from agri_data_service.pipeline.static_soil.prepare import prepare_saved_soil


def main() -> None:
    """Require explicit artifact pins for preparation and point evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-root", required=True, type=Path)
    prepare.add_argument("--hash-receipt", required=True, type=Path)
    prepare.add_argument("--source-manifest-sha256", required=True)
    prepare.add_argument("--hash-receipt-sha256", required=True)
    prepare.add_argument("--output", required=True, type=Path)
    point = commands.add_parser("point")
    point.add_argument("--candidate", required=True, type=Path)
    point.add_argument("--candidate-sha256", required=True)
    point.add_argument("--longitude", required=True, type=float)
    point.add_argument("--latitude", required=True, type=float)
    point.add_argument("--selected-day", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.command == "prepare":
        candidate = prepare_saved_soil(
            source_root=args.source_root,
            hash_receipt=args.hash_receipt,
            expected_source_sha256=args.source_manifest_sha256,
            expected_receipt_sha256=args.hash_receipt_sha256,
            output=args.output,
        )
        print(candidate.model_dump_json(indent=2))
    else:
        bundle = open_verified_soil_bundle(args.candidate, expected_manifest_sha256=args.candidate_sha256)
        result = soil_point(bundle, longitude=args.longitude, latitude=args.latitude, selected_day=args.selected_day)
        print(json.dumps(asdict(result), default=str, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
