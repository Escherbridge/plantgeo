"""Build and inspect local botanical releases without network or database access."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from agri_data_service.pipeline.direct.botanical_species_profiles.publication import (
    build_release,
    publish_release,
    read_release,
)
from agri_data_service.pipeline.direct.botanical_species_profiles.source_ingest import (
    DEFAULT_AUTHORING_CENSUS_NOTE,
    read_admitted_source,
    request_from_wcvp,
)
from agri_data_service.pipeline.direct.botanical_species_profiles.storage import LocalAvailabilityStorage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-wcvp", help="Build a reviewed WCVP v16 candidate into a local immutable store")
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--admission", type=Path, required=True)
    build.add_argument("--admission-sha256", required=True)
    build.add_argument("--taxon-id", action="append", required=True)
    build.add_argument("--review-decision-id", required=True)
    build.add_argument(
        "--authoring-census-state",
        choices=("unavailable", "reviewed_snapshot"),
        default="unavailable",
        help="State of the independently checked authoring census attached by the operator",
    )
    build.add_argument(
        "--authoring-census-note",
        default=DEFAULT_AUTHORING_CENSUS_NOTE,
        help="Checked census receipt path, SHA-256 and summary supplied by the operator",
    )
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--expected-pointer-etag")
    inspect = commands.add_parser("inspect", help="Verify every artifact of an explicitly pinned local release")
    inspect.add_argument("--store", type=Path, required=True)
    inspect.add_argument("--release-id", required=True)
    return parser


def main() -> int:
    """Run only explicitly requested local build or pinned inspection."""
    arguments = _parser().parse_args()
    if arguments.command == "inspect":
        release = read_release(LocalAvailabilityStorage(arguments.store), arguments.release_id)
        print(json.dumps(release.manifest.model_dump(mode="json"), indent=2))
        return 0
    source = read_admitted_source(arguments.admission, arguments.admission_sha256)
    request = request_from_wcvp(
        arguments.archive,
        source,
        tuple(arguments.taxon_id),
        review_decision_id=arguments.review_decision_id,
        authoring_census_state=arguments.authoring_census_state,
        authoring_census_note=arguments.authoring_census_note,
    )
    bundle = build_release(request)
    receipt = publish_release(
        LocalAvailabilityStorage(arguments.out),
        bundle,
        expected_pointer_etag=arguments.expected_pointer_etag,
    )
    print(
        json.dumps(
            {
                **asdict(receipt),
                "scope": "local-candidate; independent acceptance and production gates are separate",
                "authoring_census_state": request.authoring_census_state,
                "taxa": len(request.taxa),
                "synonyms": sum(len(taxon.synonyms) for taxon in request.taxa),
                "assertions": len(request.assertions),
                "store": str(arguments.out.resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
