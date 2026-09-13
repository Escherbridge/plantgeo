"""One bounded turn: read admitted archives already in quarantine and publish one generation.

NEVER FETCHES. The turn's input is a path to an archive an operator already transferred under a
granted permission verdict (`fetch.py`), so a scheduled turn cannot become an unreviewed download.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agri_data_service.foundation.botanical_occurrences.coordinates import DECLARED_ENVELOPE
from agri_data_service.foundation.botanical_occurrences.limits import (
    ADMITTED_LIMITS,
    PARSER_VERSION,
    QC_POLICY_VERSION,
    SUPPORT_VERSION,
    TAXONOMY_RECIPE_VERSION,
)
from agri_data_service.foundation.botanical_occurrences.release_identity import (
    build_release_set_identity,
    release_key,
)
from agri_data_service.pipeline.direct import (
    IDEMPOTENT_NOOP,
    LANE_DAY_OUTCOMES,
    NOT_BBOX_BOUNDED,
    NO_WINDOW,
    PUBLISHED,
    REFUSE_WHOLE_RELEASE,
    SKIP_AND_COUNT,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.botanical_occurrences.archive_descriptor import (
    parse_eml_facts,
    parse_meta_descriptor,
)
from agri_data_service.pipeline.direct.botanical_occurrences.normalize import normalize_rows
from agri_data_service.pipeline.direct.botanical_occurrences.publish import (
    GenerationContents,
    publication_target,
    publish_generation,
)
from agri_data_service.pipeline.direct.botanical_occurrences.quarantine import (
    inspect_archive,
    read_member_bytes,
    resolve_member_name,
)
from agri_data_service.pipeline.direct.botanical_occurrences.rows import read_member
from agri_data_service.pipeline.direct.botanical_occurrences.support import (
    SUPPORT_DEGREES,
    associate_record,
    evaluate_support,
    summarise_cell_taxa,
    support_for,
)
from agri_data_service.warehouse.schemas.botanical_occurrences import PROMOTED_OCCURRENCE_TERMS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.direct.botanical_occurrences.normalize import NormalizedOccurrence
    from agri_data_service.pipeline.direct.botanical_occurrences.publish import PublicationTarget
    from agri_data_service.pipeline.direct.botanical_occurrences.rows import SourceRow

#: Every archive this turn read was refused by a safety control, so nothing was published. Spelled
#: with the shared `blocked` word rather than a lane-private one: a monitor reading eleven writers
#: can only do that against the enumerable vocabulary in `pipeline/direct/__init__.py`.
BLOCKED_BY_QUARANTINE: Final = "blocked"
assert BLOCKED_BY_QUARANTINE in LANE_DAY_OUTCOMES  # noqa: S101 - anti-drift pin on a borrowed word

BOTANICAL_RUN_ID_PREFIX: Final = "botanical-occurrences-forward:"
#: One turn publishes at most one generation; `--max-days` bounds how many ARCHIVES it reads into it.
DEFAULT_MAX_ARCHIVES: Final = 1
DEFAULT_TIME_BUDGET_SECONDS: Final = 600.0

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table every writer is read as.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="botanical-occurrences",
    identity_defect=SKIP_AND_COUNT,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=NOT_BBOX_BOUNDED,
    turn_outcomes=frozenset({PUBLISHED, IDEMPOTENT_NOOP, NO_WINDOW, TIME_BUDGET_EXHAUSTED, BLOCKED_BY_QUARANTINE}),
    flags_absent_on_purpose={
        "--bbox": "an admitted archive is a WHOLE institutional collection export, and the envelope "
        "this lane bounds is a publication-time support decision rather than a query filter. Clipping "
        "the export to a run's bbox would publish a release whose population is this run's envelope "
        "rather than the collection's, which no reconciliation against the publisher could then match.",
        "--product": "one release set is published as one generation with a fixed set of artifacts; "
        "there is no family of products to select between, and the support rungs are all built "
        "together because a generation missing a rung is not a publishable generation.",
        "--retry-attempts": "this turn opens no socket at all. Its input is an archive already in "
        "quarantine, so there is no upstream request for a retry series to bound; `fetch.py` holds "
        "the eight-attempt ceiling for the one command that does transfer.",
        "--retry-base-seconds": "same reason as `--retry-attempts`: no request is issued by this turn, "
        "so there is no backoff series to configure and a knob here would configure nothing.",
        "--retry-max-seconds": "same reason as `--retry-attempts`: no request is issued by this turn, "
        "so a maximum backoff would bound a wait this writer never performs.",
        "--contention-timeout-seconds": "publication is addressed by release-set identity and is "
        "idempotent on replay, so two concurrent turns for the same inputs converge on the same "
        "directory rather than racing; there is no lane-day advisory lock here to wait on.",
        "--max-records": "spelled `--max-core-rows` and `--max-extension-rows` here, because this "
        "lane's two row ceilings are separately admitted numbers (600,000 and 2,000,000) and one "
        "shared spelling would let an operator set the wrong one without noticing.",
        "--max-records-per-day": "this lane has no day. Its unit is an immutable release set, so a "
        "per-day ceiling would name a period the lane does not publish against.",
    },
    policy_basis="An unkeyable core row is COUNTED and published under a locator-derived key flagged "
    "`no_native_record_key`, because a keyless row is still a documented specimen and dropping it "
    "would silently shrink the population a publisher reconciliation is supposed to match. A geometry "
    "defect refuses the whole release on the archive-safety basis rather than the PostGIS one: a "
    "coordinate this lane cannot classify means the quarantine controls did not hold, and a release "
    "whose controls did not hold is quarantined entire rather than published in part.",
)


class BotanicalForwardError(RuntimeError):
    """Raised when a turn is asked for a shape it cannot publish honestly."""


@dataclass(frozen=True, slots=True)
class ArchiveRequest:
    """One admitted archive to read: which file, and which collection it is admitted as."""

    path: Path
    collection_key: str
    distributor: str = ""
    source_version: str = ""


@dataclass(frozen=True, slots=True)
class BotanicalForwardConfig:
    """Bound every archive, row count, support rung and second of one turn."""

    archives: tuple[ArchiveRequest, ...] = ()
    root: str | None = None
    run_id: str | None = None
    max_archives: int = DEFAULT_MAX_ARCHIVES
    max_core_rows: int = ADMITTED_LIMITS.core_rows
    max_extension_rows: int = ADMITTED_LIMITS.extension_rows
    supports: tuple[str, ...] = tuple(sorted(SUPPORT_DEGREES))
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE
    advance_pointer: bool = True
    target: PublicationTarget | None = field(default=None, compare=False)

    def resolved_run_id(self) -> str:
        """Return the operator's run id, or generate one so a turn is always correlatable."""
        return self.run_id or f"{BOTANICAL_RUN_ID_PREFIX}{uuid.uuid4()}"


def _raw_row(row: SourceRow, *, collection_key: str, release_key_value: str, native_key: str) -> dict[str, Any]:
    """Shape one verbatim core row for the raw stream, with every promoted term present."""
    shaped: dict[str, Any] = {
        "collection_key": collection_key,
        "release_key": release_key_value,
        "source_record_key": native_key,
        "member_name": row.member_name,
        "row_number": row.row_number,
        "row_sha256": row.row_sha256,
        "verbatim": row.verbatim_json(),
    }
    shaped.update({term: row.values.get(term) for term in PROMOTED_OCCURRENCE_TERMS})
    return shaped


def _identification_row(row: SourceRow, *, collection_key: str, release_key_value: str) -> dict[str, Any]:
    return {
        "collection_key": collection_key,
        "release_key": release_key_value,
        "core_id": row.record_id,
        "member_name": row.member_name,
        "row_number": row.row_number,
        "row_sha256": row.row_sha256,
        "verbatim": row.verbatim_json(),
        "identifiedBy": row.values.get("identifiedBy"),
        "dateIdentified": row.values.get("dateIdentified"),
        "scientificName": row.values.get("scientificName"),
        "taxonRank": row.values.get("taxonRank"),
        "identificationRemarks": row.values.get("identificationRemarks"),
    }


def _normalized_row(record: NormalizedOccurrence) -> dict[str, Any]:
    return {
        "occurrence_id": record.occurrence_id,
        "collection_key": record.collection_key,
        "release_key": record.release_key,
        "source_record_key": record.source_record_key,
        "taxon_concept_id": record.taxon_concept_id,
        "taxonomy_recipe_version": record.taxonomy_recipe_version,
        "resolution_state": record.resolution_state,
        "scientific_name": record.scientific_name,
        "family": record.family,
        "event_start": record.event.start,
        "event_end": record.event.end,
        "event_precision": record.event.precision,
        "longitude": record.longitude,
        "latitude": record.latitude,
        "coordinate_uncertainty_m": record.coordinate_uncertainty_m,
        "spatial_class": record.spatial_class,
        "qc_policy_version": record.qc_policy_version,
        "qc_reasons": list(record.qc_reasons),
        "within_envelope": record.within_envelope,
        "geom": record.geom,
        "catalog_number": record.catalog_number,
        "recorded_by": record.recorded_by,
        "basis_of_record": record.basis_of_record,
        "rights_uri": record.rights_uri,
        "attribution_text": record.attribution_text,
    }


@dataclass(frozen=True, slots=True)
class ReadRelease:
    """One archive read into memory: its receipt row, its raw rows and its normalized records."""

    release_key: str
    release_row: dict[str, Any]
    raw_rows: tuple[dict[str, Any], ...]
    identification_rows: tuple[dict[str, Any], ...]
    records: tuple[NormalizedOccurrence, ...]
    outcome: str
    reasons: tuple[str, ...]


def read_release(request: ArchiveRequest, config: BotanicalForwardConfig) -> ReadRelease:
    """Inspect, descriptor-parse, read and normalize ONE archive, or return it quarantined.

    A rejected archive still produces a release row. That is deliberate: a refusal that leaves no
    record is a refusal nobody can audit, and the row's `quarantined` outcome plus its reasons list
    is what an operator reads to learn which control fired.
    """
    receipt = inspect_archive(request.path, ADMITTED_LIMITS)
    key = release_key(request.collection_key, request.source_version, receipt.archive_sha256)
    retrieved_at = datetime.now(UTC)
    base_row: dict[str, Any] = {
        "collection_key": request.collection_key,
        "release_key": key,
        "distributor": request.distributor,
        "source_version": request.source_version,
        "package_id": None,
        "doi": None,
        "archive_sha256": receipt.archive_sha256,
        "archive_bytes": receipt.archive_bytes,
        "eml_sha256": receipt.eml_sha256,
        "meta_sha256": receipt.meta_sha256,
        "rights_uri": None,
        "attribution_text": None,
        "coordinate_policy_scope": None,
        "retrieved_at": retrieved_at,
        "publisher_pub_date": None,
        "core_row_count": 0,
        "extension_row_counts": "{}",
        "parser_version": PARSER_VERSION,
        "outcome": "quarantined",
        "reasons": list(receipt.reasons),
    }
    if not receipt.accepted:
        return ReadRelease(key, base_row, (), (), (), "quarantined", receipt.reasons)

    meta_name = resolve_member_name(receipt, "meta.xml")
    eml_name = resolve_member_name(receipt, "eml.xml")
    if meta_name is None or eml_name is None:  # pragma: no cover - `inspect_archive` already refused this
        return ReadRelease(key, base_row, (), (), (), "quarantined", ("missing_required_member",))
    descriptor = parse_meta_descriptor(read_member_bytes(request.path, meta_name))
    facts = parse_eml_facts(read_member_bytes(request.path, eml_name))

    core_rows, core_result = read_member(request.path, descriptor.core, max_rows=config.max_core_rows)
    records = normalize_rows(
        core_rows,
        collection_key=request.collection_key,
        release_key=key,
        rights_uri=facts.rights_uri,
        attribution_text=facts.title,
        envelope=config.envelope,
    )
    raw_rows = tuple(
        _raw_row(
            row,
            collection_key=request.collection_key,
            release_key_value=key,
            native_key=record.source_record_key,
        )
        for row, record in zip(core_rows, records, strict=True)
    )

    identification_rows: list[dict[str, Any]] = []
    extension_counts: dict[str, int] = {}
    truncated = core_result.truncated
    for extension in descriptor.extensions:
        rows, result = read_member(request.path, extension, max_rows=config.max_extension_rows)
        truncated = truncated or result.truncated
        extension_counts[extension.member_name] = result.rows_read
        identification_rows.extend(
            _identification_row(row, collection_key=request.collection_key, release_key_value=key) for row in rows
        )

    reasons = tuple(
        f"unread_extension_row_type:{row_type}" for row_type in descriptor.unread_row_types
    ) + (("row_cap_truncated_population",) if truncated else ())
    release_row = {
        **base_row,
        "package_id": facts.package_id,
        "rights_uri": facts.rights_uri,
        "attribution_text": facts.title,
        "coordinate_policy_scope": facts.coordinate_policy_scope,
        "publisher_pub_date": facts.pub_date,
        "core_row_count": core_result.rows_read,
        "extension_row_counts": json.dumps(extension_counts, sort_keys=True),
        "outcome": "partial" if truncated else "complete",
        "reasons": list(reasons),
    }
    return ReadRelease(
        release_key=key,
        release_row=release_row,
        raw_rows=raw_rows,
        identification_rows=tuple(identification_rows),
        records=records,
        outcome=release_row["outcome"],
        reasons=reasons,
    )


def build_generation_contents(
    releases: Sequence[ReadRelease],
    *,
    release_set_id: str,
    supports: Sequence[str],
    envelope: tuple[float, float, float, float],
) -> GenerationContents:
    """Assemble every artifact of one generation from already-read releases."""
    records = tuple(record for release in releases for record in release.records)
    spatial = [record for record in records if not record.excluded_by_qc]
    nonspatial = [record for record in records if record.excluded_by_qc]

    association_rows: list[dict[str, Any]] = []
    support_cells: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, list[dict[str, Any]]] = {}
    for support_id in supports:
        support = support_for(support_id)
        associations = tuple(
            association for record in spatial for association in associate_record(record, support)
        )
        association_rows.extend(
            {
                "occurrence_id": association.occurrence_id,
                "support_id": association.support_id,
                "cell_id": association.cell_id,
                "membership": association.membership,
                "distance_semantics": association.distance_semantics,
            }
            for association in associations
        )
        support_cells[support_id] = [
            {
                "release_set_id": evaluation.release_set_id,
                "support_id": evaluation.support_id,
                "cell_id": evaluation.cell_id,
                "evaluation": evaluation.evaluation,
                "record_count": evaluation.record_count,
                "documented_taxa": evaluation.documented_taxa,
                "event_estimate": evaluation.event_estimate,
                "collection_count": evaluation.collection_count,
                "excluded_by_qc": evaluation.excluded_by_qc,
                "possible_only_records": evaluation.possible_only_records,
                "geom": evaluation.geom,
            }
            for evaluation in evaluate_support(
                records, associations, support, release_set_id=release_set_id, envelope=envelope
            )
        ]
        summaries[support_id] = [
            {
                "release_set_id": summary.release_set_id,
                "support_id": summary.support_id,
                "cell_id": summary.cell_id,
                "taxon_concept_id": summary.taxon_concept_id,
                "record_count": summary.record_count,
                "event_estimate": summary.event_estimate,
                "collection_count": summary.collection_count,
                "earliest_event": summary.earliest_event,
                "latest_event": summary.latest_event,
            }
            for summary in summarise_cell_taxa(records, associations, support, release_set_id=release_set_id)
        ]

    return GenerationContents(
        releases=[release.release_row for release in releases],
        raw_occurrences=[row for release in releases for row in release.raw_rows],
        identifications=[row for release in releases for row in release.identification_rows],
        occurrences=[_normalized_row(record) for record in spatial],
        nonspatial_occurrences=[_normalized_row(record) for record in nonspatial],
        associations=association_rows,
        support_cells=support_cells,
        cell_taxon_summaries=summaries,
    )


def run_botanical_occurrences_forward(config: BotanicalForwardConfig) -> dict[str, Any]:
    """Run one bounded turn and return the single JSON report a caller parses.

    A turn with no archive is `no_window`, not a failure: there is nothing to publish and nothing
    went wrong. A turn whose every archive was quarantined publishes NOTHING -- a generation built
    from refused archives would serve records whose safety controls did not hold.
    """
    run_id = config.resolved_run_id()
    started = time.monotonic()
    if not config.archives:
        return {
            "outcome": NO_WINDOW,
            "run_id": run_id,
            "release_set_id": None,
            "releases": [],
            "detail": "no admitted archive was named for this turn, so there is no release set to publish",
        }

    releases: list[ReadRelease] = []
    for request in config.archives[: config.max_archives]:
        if time.monotonic() - started > config.time_budget_seconds:
            return {
                "outcome": TIME_BUDGET_EXHAUSTED,
                "run_id": run_id,
                "release_set_id": None,
                "releases": [release.release_key for release in releases],
                "detail": f"turn exceeded its {config.time_budget_seconds}s budget before reading every archive",
            }
        releases.append(read_release(request, config))

    publishable = [release for release in releases if release.outcome != "quarantined"]
    if not publishable:
        return {
            "outcome": BLOCKED_BY_QUARANTINE,
            "run_id": run_id,
            "release_set_id": None,
            "releases": [
                {"release_key": release.release_key, "reasons": list(release.reasons)} for release in releases
            ],
            "detail": "every archive this turn read was refused by an archive-safety control",
        }

    identity = build_release_set_identity(
        (release.release_key for release in publishable),
        taxonomy_recipe_version=TAXONOMY_RECIPE_VERSION,
        qc_policy_version=QC_POLICY_VERSION,
        support_version=SUPPORT_VERSION,
    )
    contents = build_generation_contents(
        publishable,
        release_set_id=identity.release_set_id,
        supports=config.supports,
        envelope=config.envelope,
    )
    target = config.target or publication_target(config.root)
    receipt = publish_generation(target, identity, contents, advance_pointer=config.advance_pointer)
    return {
        "outcome": receipt.outcome,
        "run_id": run_id,
        "release_set_id": identity.release_set_id,
        "releases": [
            {"release_key": release.release_key, "outcome": release.outcome, "reasons": list(release.reasons)}
            for release in releases
        ],
        "objects_written": list(receipt.objects_written),
        "pointer_advanced": receipt.pointer_advanced,
        "supports": list(config.supports),
    }


def parser() -> argparse.ArgumentParser:
    """Build the turn's CLI surface, so anything outside this writer can enumerate its knobs."""
    built = argparse.ArgumentParser(
        prog="python -m agri_data_service.pipeline.direct.botanical_occurrences",
        description="Publish one governed botanical occurrence generation from quarantined archives.",
    )
    built.add_argument(
        "--archive",
        action="append",
        default=[],
        metavar="PATH::COLLECTION_KEY[::DISTRIBUTOR[::VERSION]]",
        help="An archive already in quarantine, with the collection key it is admitted as.",
    )
    built.add_argument("--root", default=None, help="Publication root; a local path, or the bucket when omitted.")
    built.add_argument("--run-id", default=None, help="Correlate this turn's records with the job that launched it.")
    built.add_argument(
        "--max-days",
        type=int,
        default=DEFAULT_MAX_ARCHIVES,
        help=(
            "How many admitted ARCHIVES this turn reads into one generation. Named for the shared "
            "runbook flag every direct writer carries; this lane's unit is a release set, not a day."
        ),
    )
    built.add_argument("--max-core-rows", type=int, default=ADMITTED_LIMITS.core_rows)
    built.add_argument("--max-extension-rows", type=int, default=ADMITTED_LIMITS.extension_rows)
    built.add_argument("--support", action="append", default=[], choices=sorted(SUPPORT_DEGREES))
    built.add_argument("--time-budget-seconds", type=float, default=DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument(
        "--hold-pointer",
        action="store_true",
        help="Write the generation durably but leave `current.json` naming the previous one.",
    )
    return built


def parse_archive_argument(raw: str) -> ArchiveRequest:
    """Parse `PATH::COLLECTION_KEY[::DISTRIBUTOR[::VERSION]]` into one archive request."""
    parts = raw.split("::")
    if len(parts) < 2 or not parts[0] or not parts[1]:  # noqa: PLR2004 - a path and a collection key
        raise BotanicalForwardError(f"--archive {raw!r} must be PATH::COLLECTION_KEY[::DISTRIBUTOR[::VERSION]]")
    return ArchiveRequest(
        path=Path(parts[0]),
        collection_key=parts[1],
        distributor=parts[2] if len(parts) > 2 else "",  # noqa: PLR2004 - optional third field
        source_version=parts[3] if len(parts) > 3 else "",  # noqa: PLR2004 - optional fourth field
    )


def parse_args(argv: Sequence[str] | None = None) -> BotanicalForwardConfig:
    """Turn argv into the frozen config one turn runs against."""
    parsed = parser().parse_args(argv)
    return BotanicalForwardConfig(
        archives=tuple(parse_archive_argument(entry) for entry in parsed.archive),
        root=parsed.root,
        run_id=parsed.run_id,
        max_archives=parsed.max_days,
        max_core_rows=parsed.max_core_rows,
        max_extension_rows=parsed.max_extension_rows,
        supports=tuple(parsed.support) if parsed.support else tuple(sorted(SUPPORT_DEGREES)),
        time_budget_seconds=parsed.time_budget_seconds,
        advance_pointer=not parsed.hold_pointer,
    )


__all__ = [
    "WRITER_CONTRACT",
    "ArchiveRequest",
    "BotanicalForwardConfig",
    "BotanicalForwardError",
    "ReadRelease",
    "build_generation_contents",
    "parse_archive_argument",
    "parse_args",
    "parser",
    "read_release",
    "run_botanical_occurrences_forward",
]
