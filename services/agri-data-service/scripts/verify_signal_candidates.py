"""Verify the saved 222-day signal archive and emit a local-only admission packet; see scripts/AGENTS.md."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agri_data_service.pipeline.parquet.signal_candidate_admission import verify_signal_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.out.exists():
        parser.error("output must be a new local file")
    packet = verify_signal_candidates(arguments.archive)
    with arguments.out.open("x", encoding="utf-8") as target:
        json.dump(packet, target, indent=2, sort_keys=True)
        target.write("\n")
    print(
        json.dumps(
            {key: value for key, value in packet.items() if key not in {"days", "required_before_admission"}},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
