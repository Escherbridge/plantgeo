"""The lane's operator CLI: `fetch`, `inspect`, `publish`, `compare`. Every command prints a receipt.

A command that printed nothing would leave an operator with no evidence of what it did, and this lane
is one whose whole contract is evidence. Every subcommand writes exactly one JSON object to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from agri_data_service.foundation.botanical_occurrences.limits import ADMITTED_LIMITS
from agri_data_service.pipeline.direct.botanical_occurrences.fetch import encode_receipt, fetch_archive
from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    parse_args,
    parser,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.identity import compare_releases
from agri_data_service.pipeline.direct.botanical_occurrences.quarantine import inspect_archive

if TYPE_CHECKING:
    from collections.abc import Sequence


def _inspect_command(argv: Sequence[str]) -> int:
    built = argparse.ArgumentParser(prog="botanical-occurrences inspect")
    built.add_argument("archive", type=Path)
    parsed = built.parse_args(argv)
    receipt = inspect_archive(parsed.archive, ADMITTED_LIMITS)
    print(json.dumps(asdict(receipt), sort_keys=True, default=str))
    return 0 if receipt.accepted else 1


def _fetch_command(argv: Sequence[str]) -> int:
    built = argparse.ArgumentParser(prog="botanical-occurrences fetch")
    built.add_argument("url")
    built.add_argument("destination", type=Path)
    built.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Admission manifest JSON; the transfer refuses unless it grants THIS exact URL.",
    )
    parsed = built.parse_args(argv)
    manifest = json.loads(parsed.manifest.read_text(encoding="utf-8"))
    print(encode_receipt(fetch_archive(parsed.url, parsed.destination, manifest=manifest)))
    return 0


def _publish_command(argv: Sequence[str]) -> int:
    report = run_botanical_occurrences_forward(parse_args(argv))
    print(json.dumps(report, sort_keys=True, default=str))
    return 0


def _compare_command(argv: Sequence[str]) -> int:
    built = argparse.ArgumentParser(prog="botanical-occurrences compare")
    built.add_argument("older", type=Path, help="JSON lines of {source_record_key, row_sha256} for the older release.")
    built.add_argument("newer", type=Path)
    built.add_argument("--older-release-key", default="older")
    built.add_argument("--newer-release-key", default="newer")
    built.add_argument("--older-outcome", default="complete")
    built.add_argument("--newer-outcome", default="complete")
    parsed = built.parse_args(argv)
    report = compare_releases(
        json.loads(parsed.older.read_text(encoding="utf-8")),
        json.loads(parsed.newer.read_text(encoding="utf-8")),
        older_release_key=parsed.older_release_key,
        newer_release_key=parsed.newer_release_key,
        older_outcome=parsed.older_outcome,
        newer_outcome=parsed.newer_outcome,
    )
    print(report.as_json())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one subcommand, or print the publish turn's own usage when none is named."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "fetch": _fetch_command,
        "inspect": _inspect_command,
        "publish": _publish_command,
        "compare": _compare_command,
    }
    if not arguments or arguments[0] not in commands:
        parser().print_usage()
        print(json.dumps({"error": "unknown_command", "known_commands": sorted(commands)}, sort_keys=True))
        return 2
    return commands[arguments[0]](arguments[1:])


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
