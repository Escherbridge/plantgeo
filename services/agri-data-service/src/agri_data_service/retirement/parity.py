"""D1 item 1: the counted parity receipt, read from the per-layer modules rather than recomputed.

THERE IS NO FOURTH COMPARISON HERE, ON PURPOSE. Wave B already ships one counted Postgres-vs-Parquet
comparison per layer -- `pipeline/direct/vegetation/parity.py`,
`pipeline/direct/weather_observations/parity.py`, `pipeline/direct/drought/parity.py`. Each encodes
its own layer's exporter grain, its own governance predicate and its own sparse-history rule, and
none of that survives being generalised. This module BINDS to them: it names the command that
produces a receipt, normalises the three different JSON shapes those commands print into one verdict,
and refuses a shape it does not recognise. Where a layer has no such module it emits
`parity: unavailable` and blocks, rather than inventing a number the packet could not stand behind.

Nothing here opens a database or a bucket. The operator runs the layer's own parity command, captures
its JSON, and hands the file to `build_drop_packet.py`. That seam is what lets a packet be assembled
while production is unreachable, and it keeps the drop tool incapable of firing a production action.

THE FIRE-PERIMETERS TRAP, AND WHY AN EPOCH OUTRANKS A RECEIPT. `fire-perimeters` was re-registered
from `daily_series` to `static_lookup` on 2026-09-04 (`pipeline/parquet/lane_registry.py:850-884`).
Its 177 published perimeters were laid across 45 partition days keyed on `observed_day`; under the new
registration the partition day comes from the source watermark instead, so those 45 days are not
readable as the lane's coverage any more. A parity receipt taken before the first fresh snapshot
therefore compares a live PostgreSQL population against an effectively empty lane -- and reads
`under_covered` for a twin that is not short, only not yet rewritten. `assess_shortfall` checks the
epoch BEFORE it looks at the verdict, so a lane whose twin has not been rewritten can never be
reported as under-covered, and a lane whose rewrite cannot be proven is reported as unproven rather
than as either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping


#: How many example days a normalised receipt carries forward into the packet.
_SAMPLE_LIMIT: Final = 20


class ParityReceiptError(ValueError):
    """Raised when a supplied receipt cannot be understood, or reports its own run as failed.

    Fail-closed by construction: an unrecognised shape is never normalised into a permissive default,
    because the one thing a drop must not be gated on is a verdict nobody computed.
    """


class ParityAvailability(StrEnum):
    """Whether this relation has a counted comparison at all."""

    MEASURED = "measured"
    UNAVAILABLE = "unavailable"


class ShortfallClass(StrEnum):
    """What the difference between the two sides means. Everything but `NONE` blocks."""

    NONE = "none"
    #: Parquet is genuinely short of what PostgreSQL holds. D1's "under-coverage is a blocker".
    GENUINE_UNDER_COVERAGE = "genuine_under_coverage"
    #: Nobody counted. Distinct from under-coverage on purpose: calling an unmeasured lane "short"
    #: is the same overclaim in the opposite direction, and it would make a later real shortfall
    #: indistinguishable from the state every lane starts in.
    UNMEASURED = "unmeasured"
    #: The lane's semantics changed and nothing has been written since. Not under-coverage: the
    #: comparison is meaningless until a fresh write exists to compare against.
    TWIN_NOT_REWRITTEN = "twin_not_rewritten"
    #: Same epoch, but no evidence either way about when the twin was last written.
    TWIN_REWRITE_UNPROVEN = "twin_rewrite_unproven"


@dataclass(frozen=True, slots=True)
class ParityBinding:
    """The per-layer parity module a packet cites, and the exact command that produces its receipt."""

    lane_slug: str
    module: str
    command: str
    #: The normaliser key this module's output is recognised as; see `normalize_parity_receipt`.
    shape: str
    citation: str


@dataclass(frozen=True, slots=True)
class RewriteEpoch:
    """A moment at which a lane's partition semantics changed, invalidating everything written before.

    `epoch_at` is compared against the newest completion marker the twin carries, never against a
    partition day: a `static_lookup` lane's partition day is a version stamp from the source
    watermark and may be older than the write that produced it.
    """

    lane_slug: str
    epoch_at: datetime
    reason: str
    citation: str


@dataclass(frozen=True, slots=True)
class NormalizedParity:
    """One receipt from any of the three layer modules, reduced to the facts a verdict needs."""

    shape: str
    postgres_days: int
    postgres_rows: int
    covered: bool
    rows_compared: bool
    under_covered_day_count: int
    under_covered_day_sample: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def baseline_empty(self) -> bool:
        """True when PostgreSQL contributed zero days, making `covered` unearned rather than proven."""
        return self.postgres_days == 0

    def to_json_dict(self) -> dict[str, object]:
        """Render the normalised comparison for the packet body."""
        return {
            "receipt_shape": self.shape,
            "postgres_days": self.postgres_days,
            "postgres_rows": self.postgres_rows,
            "covered": self.covered,
            "rows_compared": self.rows_compared,
            "baseline_empty": self.baseline_empty,
            "under_covered_day_count": self.under_covered_day_count,
            "under_covered_day_sample": list(self.under_covered_day_sample),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ShortfallAssessment:
    """The classification of a difference, and the sentence a packet prints for it."""

    classification: ShortfallClass
    detail: str

    @property
    def blocking(self) -> bool:
        """Every classification but `NONE` blocks a drop."""
        return self.classification is not ShortfallClass.NONE


class LaneWriteProbe(Protocol):
    """Answers when a lane's Parquet twin was last written, or `None` when that is not known.

    A live implementation lists the lane's completion markers; the offline one below is fed values an
    operator captured. The packet builder only ever sees this interface, so it can be assembled with
    no bucket credentials and still refuse honestly.
    """

    def newest_completion_at(self, lane_slug: str) -> datetime | None:
        """Return the newest completion-marker timestamp for one lane, or `None` when unknown."""
        ...


@dataclass(frozen=True, slots=True)
class RecordedLaneWriteProbe:
    """A probe over values an operator recorded, so the packet never needs the bucket to be reachable."""

    recorded: Mapping[str, datetime]

    def newest_completion_at(self, lane_slug: str) -> datetime | None:
        """Return the recorded timestamp for one lane, or `None` when nothing was recorded for it."""
        return self.recorded.get(lane_slug)


#: The one probe used when nothing at all is known: every lane's rewrite state is unproven. This is
#: the DEFAULT, so a packet built with no evidence about the twin says so instead of assuming.
UNPROVEN_LANE_WRITE_PROBE: Final = RecordedLaneWriteProbe(recorded=MappingProxyType({}))

PARITY_BINDINGS: Final[Mapping[str, ParityBinding]] = MappingProxyType(
    {
        "vegetation": ParityBinding(
            lane_slug="vegetation",
            module="agri_data_service.pipeline.direct.vegetation.parity",
            command=(
                "UV_NO_SYNC=1 uv run --no-sync python -m "
                "agri_data_service.pipeline.direct.vegetation.parity --count-rows"
            ),
            shape="vegetation",
            citation="services/agri-data-service/src/agri_data_service/pipeline/direct/vegetation/parity.py:262",
        ),
        "weather-observations": ParityBinding(
            lane_slug="weather-observations",
            module="agri_data_service.pipeline.direct.weather_observations.parity",
            command=(
                "UV_NO_SYNC=1 uv run --no-sync python -m agri_data_service.pipeline.direct.weather_observations.parity"
            ),
            shape="weather_observations",
            citation=(
                "services/agri-data-service/src/agri_data_service/pipeline/direct/weather_observations/parity.py:130"
            ),
        ),
        "drought": ParityBinding(
            lane_slug="drought",
            module="agri_data_service.pipeline.direct.drought.parity",
            command="UV_NO_SYNC=1 uv run --no-sync python -m agri_data_service.pipeline.direct.drought.parity",
            shape="drought",
            citation="services/agri-data-service/src/agri_data_service/pipeline/direct/drought/parity.py:172",
        ),
    }
)

#: Lanes whose partition semantics changed under them. Keyed by lane slug, consulted before any
#: receipt is believed. Adding a lane here is how a future re-registration stops being a silent trap.
PARQUET_REWRITE_EPOCHS: Final[Mapping[str, RewriteEpoch]] = MappingProxyType(
    {
        "fire-perimeters": RewriteEpoch(
            lane_slug="fire-perimeters",
            epoch_at=datetime(2026, 9, 4, tzinfo=UTC),
            reason=(
                "re-registered from daily_series to static_lookup, moving the partition day from "
                "geo.feature_observation_day to the source watermark; the 177 perimeters previously laid "
                "across 45 observed_day partitions are not readable as this lane's coverage under the new "
                "registration, so a receipt taken before the first fresh snapshot compares a live "
                "PostgreSQL population against an empty lane"
            ),
            citation="services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py:850-884",
        )
    }
)

#: The reason string a packet prints for a layer with no counted comparison at all.
NO_BINDING_REASON: Final = (
    "no per-layer parity module exists for this lane; wave B ships one only for vegetation, "
    "weather-observations and drought. A number is not invented here"
)


def parse_epoch_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 instant, refusing a naive one so an epoch comparison is never ambiguous."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ParityReceiptError(f"{value!r} is not an ISO-8601 timestamp: {error}") from error
    if parsed.tzinfo is None:
        raise ParityReceiptError(f"{value!r} carries no timezone; an epoch comparison needs an absolute instant")
    return parsed


def _as_int(payload: Mapping[str, object], key: str, *, shape: str) -> int:
    """Read one integer field, refusing a missing or non-integer value rather than defaulting it."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParityReceiptError(f"{shape} receipt: {key!r} is {value!r}, expected an integer")
    return value


def _normalize_drought(payload: Mapping[str, object]) -> NormalizedParity:
    """Reduce `pipeline/direct/drought/parity.py`'s receipt, which compares days AND rows per day."""
    missing = payload.get("missing_from_parquet")
    mismatches = payload.get("row_count_mismatches")
    incomplete = payload.get("parquet_incomplete_days")
    if not isinstance(missing, list) or not isinstance(mismatches, list) or not isinstance(incomplete, list):
        raise ParityReceiptError("drought receipt: missing_from_parquet/row_count_mismatches must be lists")
    notes: list[str] = []
    if incomplete:
        notes.append(f"{len(incomplete)} written-but-unmarked Parquet day(s); a half-finished export proves nothing")
    if mismatches:
        notes.append(f"{len(mismatches)} day(s) whose row counts differ; drought compares for EQUALITY, not >=")
    return NormalizedParity(
        shape="drought",
        postgres_days=_as_int(payload, "postgres_days", shape="drought"),
        postgres_rows=_as_int(payload, "postgres_rows", shape="drought"),
        covered=payload.get("parity_achieved") is True,
        rows_compared=True,
        under_covered_day_count=len(missing) + len(mismatches),
        under_covered_day_sample=tuple(str(day) for day in missing[:_SAMPLE_LIMIT]),
        notes=tuple(notes),
    )


def _normalize_weather_observations(payload: Mapping[str, object]) -> NormalizedParity:
    """Reduce `pipeline/direct/weather_observations/parity.py`'s receipt (per-day row comparison)."""
    days = payload.get("under_covered_days")
    if not isinstance(days, list):
        raise ParityReceiptError("weather_observations receipt: under_covered_days must be a list")
    sample = tuple(str(entry.get("day")) for entry in days[:_SAMPLE_LIMIT] if isinstance(entry, dict))
    return NormalizedParity(
        shape="weather_observations",
        postgres_days=_as_int(payload, "postgres_days", shape="weather_observations"),
        postgres_rows=_as_int(payload, "postgres_rows", shape="weather_observations"),
        covered=payload.get("verdict") == "parity_matched",
        rows_compared=True,
        under_covered_day_count=_as_int(payload, "under_covered_day_count", shape="weather_observations"),
        under_covered_day_sample=sample,
        notes=(
            "this layer has no upstream archive endpoint, so PostgreSQL IS the only historical record; "
            "its drop is additionally gated on the Postgres-reading adapter having republished history "
            "to completion (plan.md, B2 finding 2026-09-04)",
        ),
    )


def _normalize_vegetation(payload: Mapping[str, object]) -> NormalizedParity:
    """Reduce `pipeline/direct/vegetation/parity.py`'s nested receipt.

    ITS ROW COMPARISON IS OPTIONAL AND THAT IS RECORDED, NOT SMOOTHED OVER. Without `--count-rows`
    the module reports `row_coverage: not_measured`, which satisfies "every day" but not D1's "every
    day AND row". `rows_compared=False` is what makes `packet.py` refuse such a receipt.
    """
    verdict = payload.get("verdict")
    postgres = payload.get("postgres")
    findings = payload.get("findings")
    if not isinstance(verdict, dict) or not isinstance(postgres, dict) or not isinstance(findings, dict):
        raise ParityReceiptError("vegetation receipt: verdict/postgres/findings must all be objects")
    row_coverage = verdict.get("row_coverage")
    sample = findings.get("missing_from_parquet_sample")
    notes: list[str] = []
    if row_coverage == "not_measured":
        notes.append(
            "row_coverage is not_measured: this receipt was produced WITHOUT --count-rows, so it proves "
            "day membership only and cannot discharge D1's 'every day and row'"
        )
    incomplete = findings.get("ladder_incomplete_count")
    if isinstance(incomplete, int) and incomplete:
        notes.append(f"{incomplete} day(s) covered at the base rung but not settled at all four rungs")
    return NormalizedParity(
        shape="vegetation",
        postgres_days=_as_int(postgres, "days", shape="vegetation"),
        postgres_rows=_as_int(postgres, "cell_day_rows", shape="vegetation"),
        covered=verdict.get("parity_achieved") is True,
        rows_compared=row_coverage != "not_measured",
        under_covered_day_count=_as_int(findings, "missing_from_parquet_count", shape="vegetation")
        + _as_int(findings, "ladder_incomplete_count", shape="vegetation"),
        under_covered_day_sample=tuple(str(day) for day in sample[:_SAMPLE_LIMIT]) if isinstance(sample, list) else (),
        notes=tuple(notes),
    )


def normalize_parity_receipt(payload: Mapping[str, object]) -> NormalizedParity:
    """Recognise which layer module printed this receipt and reduce it; refuse anything else.

    Recognition is by structure, not by a caller-declared shape, so a receipt captured from the wrong
    layer's command cannot be filed under the layer whose packet is being built.
    """
    if payload.get("status") == "failed":
        raise ParityReceiptError(f"the parity run itself failed: {payload.get('error')!r}")
    if payload.get("event") == "weather_observations_parity":
        return _normalize_weather_observations(payload)
    verdict = payload.get("verdict")
    if isinstance(verdict, dict) and "day_coverage" in verdict:
        return _normalize_vegetation(payload)
    if "parity_achieved" in payload and "missing_from_parquet" in payload:
        return _normalize_drought(payload)
    raise ParityReceiptError(
        f"unrecognised parity receipt shape; top-level keys were {sorted(payload)}. "
        "A receipt this tool cannot read is never normalised into a permissive default"
    )


def assess_shortfall(
    *,
    normalized: NormalizedParity | None,
    epoch: RewriteEpoch | None,
    probe: LaneWriteProbe,
) -> ShortfallAssessment:
    """Classify the comparison. THE EPOCH IS CHECKED FIRST, BEFORE ANY VERDICT IS BELIEVED.

    A lane whose partition semantics changed has no comparable twin until something is written under
    the new semantics -- so neither `covered` nor `under_covered` from such a receipt means anything,
    and reporting either would be the fire-perimeters trap. Order matters here and is the whole point
    of the function.
    """
    if epoch is not None:
        newest = probe.newest_completion_at(epoch.lane_slug)
        if newest is None:
            return ShortfallAssessment(
                classification=ShortfallClass.TWIN_REWRITE_UNPROVEN,
                detail=(
                    f"lane {epoch.lane_slug!r} carries a rewrite epoch at {epoch.epoch_at.isoformat()} "
                    f"({epoch.reason}; {epoch.citation}) and nothing records when its twin was last "
                    "written, so a shortfall cannot be told apart from an unrewritten twin"
                ),
            )
        if newest < epoch.epoch_at:
            return ShortfallAssessment(
                classification=ShortfallClass.TWIN_NOT_REWRITTEN,
                detail=(
                    f"lane {epoch.lane_slug!r} was last written at {newest.isoformat()}, before its rewrite "
                    f"epoch {epoch.epoch_at.isoformat()} ({epoch.reason}; {epoch.citation}). This is NOT "
                    "under-coverage: the twin has not been rewritten under the new registration yet"
                ),
            )
    if normalized is None:
        return ShortfallAssessment(
            classification=ShortfallClass.UNMEASURED,
            detail="no counted comparison was supplied, so coverage is unproven -- which is not the same as short",
        )
    if not normalized.covered:
        return ShortfallAssessment(
            classification=ShortfallClass.GENUINE_UNDER_COVERAGE,
            detail=(
                f"the counted comparison reports {normalized.under_covered_day_count} uncovered day(s) "
                f"against {normalized.postgres_days} PostgreSQL day(s); sample "
                f"{list(normalized.under_covered_day_sample)}"
            ),
        )
    return ShortfallAssessment(classification=ShortfallClass.NONE, detail="the Parquet twin covers every counted day")


@dataclass(frozen=True, slots=True)
class ParitySection:
    """D1 item 1 for one relation, or for one layer's rows inside a shared relation."""

    scope: str
    availability: ParityAvailability
    binding: ParityBinding | None
    receipt_source: str | None
    normalized: NormalizedParity | None
    assessment: ShortfallAssessment
    unavailable_reason: str | None

    def to_json_dict(self) -> dict[str, object]:
        """Render the parity section exactly as the packet prints it."""
        return {
            "scope": self.scope,
            "parity": str(self.availability),
            "unavailable_reason": self.unavailable_reason,
            "binding": (
                None
                if self.binding is None
                else {
                    "lane": self.binding.lane_slug,
                    "module": self.binding.module,
                    "command": self.binding.command,
                    "citation": self.binding.citation,
                }
            ),
            "receipt_source": self.receipt_source,
            "receipt": None if self.normalized is None else self.normalized.to_json_dict(),
            "shortfall": {
                "classification": str(self.assessment.classification),
                "detail": self.assessment.detail,
                "blocking": self.assessment.blocking,
            },
        }


def build_parity_section(  # noqa: PLR0913 - each argument is one independent coordinate of the section
    *,
    scope: str,
    binding: ParityBinding | None,
    receipt_payload: Mapping[str, object] | None,
    receipt_source: str | None,
    epoch: RewriteEpoch | None,
    probe: LaneWriteProbe = UNPROVEN_LANE_WRITE_PROBE,
) -> ParitySection:
    """Assemble one parity section, emitting `unavailable` rather than a number nobody computed."""
    if binding is None:
        return ParitySection(
            scope=scope,
            availability=ParityAvailability.UNAVAILABLE,
            binding=None,
            receipt_source=None,
            normalized=None,
            assessment=assess_shortfall(normalized=None, epoch=epoch, probe=probe),
            unavailable_reason=NO_BINDING_REASON,
        )
    if receipt_payload is None:
        return ParitySection(
            scope=scope,
            availability=ParityAvailability.UNAVAILABLE,
            binding=binding,
            receipt_source=None,
            normalized=None,
            assessment=assess_shortfall(normalized=None, epoch=epoch, probe=probe),
            unavailable_reason=(
                f"a parity module exists but no receipt was supplied; run `{binding.command}` and pass its "
                "JSON with --parity-receipt"
            ),
        )
    normalized = normalize_parity_receipt(receipt_payload)
    return ParitySection(
        scope=scope,
        availability=ParityAvailability.MEASURED,
        binding=binding,
        receipt_source=receipt_source,
        normalized=normalized,
        assessment=assess_shortfall(normalized=normalized, epoch=epoch, probe=probe),
        unavailable_reason=None,
    )


__all__ = [
    "NO_BINDING_REASON",
    "PARITY_BINDINGS",
    "PARQUET_REWRITE_EPOCHS",
    "UNPROVEN_LANE_WRITE_PROBE",
    "LaneWriteProbe",
    "NormalizedParity",
    "ParityAvailability",
    "ParityBinding",
    "ParityReceiptError",
    "ParitySection",
    "RecordedLaneWriteProbe",
    "RewriteEpoch",
    "ShortfallAssessment",
    "ShortfallClass",
    "assess_shortfall",
    "build_parity_section",
    "normalize_parity_receipt",
    "parse_epoch_timestamp",
]
