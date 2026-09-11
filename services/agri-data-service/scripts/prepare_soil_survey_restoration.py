"""Prepare all soil-survey rungs from one explicitly pinned preserved-Parquet input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

from agri_data_service.pipeline.soil_survey_restore.prepare import prepare_soil_survey


def main() -> None:
    """Require an installed spatial extension; perform no acquisition or publication."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with duckdb.connect(
        config={"autoinstall_known_extensions": False, "autoload_known_extensions": False}
    ) as connection:
        connection.execute("LOAD spatial")
        receipt = prepare_soil_survey(
            source_root=args.source_root,
            expected_manifest_sha256=args.source_manifest_sha256,
            output=args.output,
            connection=connection,
        )
    print(json.dumps(receipt, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
