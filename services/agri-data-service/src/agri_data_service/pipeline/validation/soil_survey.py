"""Reconcile the written `soil-survey` release against USDA Soil Data Access -- the source system.

Layer L2: may import foundation, warehouse, and other `pipeline` modules; may NOT import method,
planes, or interface. Needs the network (httpx), so it cannot live in `method`.

Compares what was WRITTEN (the exported Parquet release, read back with Polars) against what SDA
holds RIGHT NOW -- never the lane's own intermediate state, which would only prove the exporter
agrees with itself. Two checks per `docs/lanes/soil-survey.md` section 6: a per-survey-area
delineation count on `mupolygonkey` (never `mukey`, see the 683-into-98 collapse it documents),
and a vintage-staleness check (`sacatalog.saverest` republishing past what was written) -- this
lane is STATIC, not never-changing, so detecting a republish is the point.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal, Protocol

import polars as pl

from agri_data_service.foundation.parquet.paths import try_parse_partition_path
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    import httpx

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

_KIND: Final[PartitionKind] = "observed"

# One network call per requested area; a validator asking for "every survey area" in one pass
# would be an unbounded scan of the source system, not a bounded reconciliation.
MAX_VALIDATION_AREAS: Final = 50

_SDA_TABULAR_ENDPOINT: Final = "https://SDMDataAccess.nrcs.usda.gov/Tabular/post.rest"
_SDA_REQUEST_TIMEOUT_SECONDS: Final = 30.0

# SSURGO's own areasymbol shape (legend.areasymbol), e.g. "ID001", "OR605" (docs/lanes/soil-survey.md
# section 1). This string is embedded directly into a T-SQL statement SDA's endpoint has no bind
# mechanism for, so the format check IS the first line of injection defense, not a courtesy.
_AREA_SYMBOL_PATTERN: Final = re.compile(r"^[A-Z]{2}[0-9]{3,4}$")

# SDA's `JSON+COLUMNNAME` table is `[header, *data_rows]`; fewer than this many entries means no
# data row was returned at all (a genuinely empty result, not a malformed one).
_MIN_TABLE_ROWS_WITH_DATA: Final = 2

SoilSurveyFindingKind = Literal[
    "no_release_written",
    "area_not_written",
    "area_not_at_source",
    "delineation_count_mismatch",
    "vintage_stale",
    "source_query_failed",
]


class SoilSurveyValidationError(RuntimeError):
    """Raised when soil-survey validation cannot be run as requested."""


class SoilSurveySdaResponseError(SoilSurveyValidationError):
    """Raised when USDA SDA's tabular endpoint returns a body this client cannot parse."""


@dataclass(frozen=True, slots=True)
class SoilSurveyWrittenAreaSummary:
    """One survey area's state as the written Parquet release actually holds it."""

    area_symbol: str
    delineation_count: int
    newest_vintage: datetime | None
    hydric_true_count: int
    hydric_false_count: int
    hydric_unknown_count: int


@dataclass(frozen=True, slots=True)
class SoilSurveyWrittenRelease:
    """The written release this validation run reconciled against; `None` day is an honest gap."""

    release_day: date | None
    areas: Mapping[str, SoilSurveyWrittenAreaSummary]


@dataclass(frozen=True, slots=True)
class SdaSurveyAreaSummary:
    """One survey area's state as USDA Soil Data Access reports it right now."""

    area_symbol: str
    delineation_count: int
    saverest: datetime | None
    raw_response: str


class SoilSurveySdaClient(Protocol):
    """The whole USDA SDA surface this validator needs; implement it to test without a network."""

    async def fetch_survey_area_summary(self, area_symbol: str) -> SdaSurveyAreaSummary: ...


@dataclass(frozen=True, slots=True)
class SoilSurveyValidationFinding:
    """One honest gap or mismatch: names the survey area, the lane, and the source response."""

    lane: str
    area_symbol: str
    kind: SoilSurveyFindingKind
    detail: str
    source_response: str | None


@dataclass(frozen=True, slots=True)
class SoilSurveyValidationReport:
    """This run's whole verdict: which release it checked, which areas, and what it found."""

    release_day: date | None
    checked_areas: tuple[str, ...]
    findings: tuple[SoilSurveyValidationFinding, ...]

    @property
    def passed(self) -> bool:
        """True only when every checked area reconciled cleanly."""
        return not self.findings


def _validated_area_symbol(value: str) -> str:
    """Return `value` if it is a plausible SSURGO areasymbol, else raise.

    This string is about to be embedded in a T-SQL statement SDA's endpoint cannot bind a
    parameter to, so the format check is the primary injection defense.
    """
    if not _AREA_SYMBOL_PATTERN.match(value):
        raise SoilSurveyValidationError(
            f"{value!r} is not a plausible SSURGO areasymbol (expected two letters and 3-4 digits, "
            "e.g. 'ID001'); refusing to embed it in a Soil Data Access query"
        )
    return value


def _resolve_latest_release_day(store: ObjectStore) -> date | None:
    """Find the most recently written release day from the object listing alone."""
    keys = store.list_partition_keys(SOIL_SURVEY_STREAM, _KIND)
    days = {parsed.day for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None}
    return max(days) if days else None


def _relative_paths_for_day(store: ObjectStore, day: date) -> tuple[str, ...]:
    """List one day's part files, sorted by part index."""
    keys = store.list_partition_keys(SOIL_SURVEY_STREAM, _KIND, year=day.year, month=day.month)
    candidates = (try_parse_partition_path(key) for key in keys)
    parts = sorted(
        (parsed for parsed in candidates if parsed is not None and parsed.day == day),
        key=lambda parsed: parsed.part_index,
    )
    return tuple(part.key for part in parts)


def read_written_soil_survey_area_summaries(
    store: ObjectStore,
    *,
    storage_options: Mapping[str, str],
    path_for: Callable[[str], str],
    day: date | None = None,
) -> SoilSurveyWrittenRelease:
    """Read the written release and aggregate it per survey area, on the `mupolygonkey` grain.

    Hydric counts use Polars' Boolean `sum`/`~`, which skip nulls exactly like SQL -- the tri-state
    `hydric_rating` (true/false/unknown) never gets coerced into a binary true/false count.
    """
    resolved_day = _resolve_latest_release_day(store) if day is None else day
    if resolved_day is None:
        return SoilSurveyWrittenRelease(release_day=None, areas={})
    relative_paths = _relative_paths_for_day(store, resolved_day)
    if not relative_paths:
        return SoilSurveyWrittenRelease(release_day=None, areas={})
    paths = [path_for(relative_path) for relative_path in relative_paths]
    options = dict(storage_options) if storage_options else None
    frame = (
        pl.scan_parquet(paths, storage_options=options)
        .select(["survey_area_symbol", "mupolygonkey", "survey_area_vintage", "hydric_rating"])
        .group_by("survey_area_symbol")
        .agg(
            pl.col("mupolygonkey").n_unique().alias("delineation_count"),
            pl.col("survey_area_vintage").max().alias("newest_vintage"),
            pl.col("hydric_rating").sum().alias("hydric_true_count"),
            (~pl.col("hydric_rating")).sum().alias("hydric_false_count"),
            pl.col("hydric_rating").is_null().sum().alias("hydric_unknown_count"),
        )
        .collect()
    )
    areas = {
        str(row["survey_area_symbol"]): SoilSurveyWrittenAreaSummary(
            area_symbol=str(row["survey_area_symbol"]),
            delineation_count=int(row["delineation_count"]),
            newest_vintage=row["newest_vintage"],
            hydric_true_count=int(row["hydric_true_count"] or 0),
            hydric_false_count=int(row["hydric_false_count"] or 0),
            hydric_unknown_count=int(row["hydric_unknown_count"] or 0),
        )
        for row in frame.iter_rows(named=True)
        if row["survey_area_symbol"] is not None
    }
    return SoilSurveyWrittenRelease(release_day=resolved_day, areas=areas)


async def validate_soil_survey_release(  # noqa: PLR0913 - one parameter per required reconciliation input
    store: ObjectStore,
    sda_client: SoilSurveySdaClient,
    *,
    survey_area_symbols: Sequence[str],
    storage_options: Mapping[str, str],
    path_for: Callable[[str], str],
    day: date | None = None,
) -> SoilSurveyValidationReport:
    """Reconcile one bounded batch of survey areas: written release vs. live USDA SDA.

    Isolates one area's SDA failure from the rest, exactly like `gap_fill.py` isolates one lane's
    failure from its siblings -- a network blip on one survey area must not hide every other
    area's real reconciliation result.
    """
    if not survey_area_symbols:
        raise SoilSurveyValidationError(
            "validation requires at least one survey area symbol; an unbounded reconciliation "
            "against every SSURGO survey area is not this function's job"
        )
    checked = tuple(_validated_area_symbol(area) for area in survey_area_symbols)
    if len(checked) > MAX_VALIDATION_AREAS:
        raise SoilSurveyValidationError(
            f"refusing to validate {len(checked)} survey areas in one run; the bound is "
            f"{MAX_VALIDATION_AREAS} network calls per validation pass"
        )
    written = read_written_soil_survey_area_summaries(
        store, storage_options=storage_options, path_for=path_for, day=day
    )
    findings: list[SoilSurveyValidationFinding] = []
    if written.release_day is None:
        day_detail = (
            "no soil-survey release has ever been written"
            if day is None
            else f"release {day.isoformat()} was never written"
        )
        findings.extend(
            SoilSurveyValidationFinding(
                lane=SOIL_SURVEY_STREAM,
                area_symbol=area,
                kind="no_release_written",
                detail=f"survey area {area}: {day_detail}",
                source_response=None,
            )
            for area in checked
        )
        return SoilSurveyValidationReport(release_day=None, checked_areas=checked, findings=tuple(findings))

    for area in checked:
        written_area = written.areas.get(area)
        try:
            source = await sda_client.fetch_survey_area_summary(area)
        except Exception as error:  # per-area isolation: one area's fault must not end the run
            findings.append(
                SoilSurveyValidationFinding(
                    lane=SOIL_SURVEY_STREAM,
                    area_symbol=area,
                    kind="source_query_failed",
                    detail=(f"survey area {area}: could not reach USDA SDA: {type(error).__name__}: {error}"),
                    source_response=None,
                )
            )
            continue
        findings.extend(_reconcile_area(area, written.release_day, written_area, source))
    return SoilSurveyValidationReport(release_day=written.release_day, checked_areas=checked, findings=tuple(findings))


def _reconcile_area(
    area: str,
    release_day: date,
    written_area: SoilSurveyWrittenAreaSummary | None,
    source: SdaSurveyAreaSummary,
) -> list[SoilSurveyValidationFinding]:
    """Compare one area's written state against its live SDA state, naming every mismatch."""
    findings: list[SoilSurveyValidationFinding] = []
    if written_area is None:
        if source.delineation_count > 0:
            findings.append(
                SoilSurveyValidationFinding(
                    lane=SOIL_SURVEY_STREAM,
                    area_symbol=area,
                    kind="area_not_written",
                    detail=(
                        f"survey area {area} has {source.delineation_count} delineation(s) published at "
                        f"USDA SDA but release {release_day.isoformat()} holds none"
                    ),
                    source_response=source.raw_response,
                )
            )
        return findings
    if source.delineation_count == 0:
        findings.append(
            SoilSurveyValidationFinding(
                lane=SOIL_SURVEY_STREAM,
                area_symbol=area,
                kind="area_not_at_source",
                detail=(
                    f"survey area {area} is written with {written_area.delineation_count} delineation(s) "
                    "but USDA SDA currently reports none for it"
                ),
                source_response=source.raw_response,
            )
        )
        return findings
    if written_area.delineation_count != source.delineation_count:
        findings.append(
            SoilSurveyValidationFinding(
                lane=SOIL_SURVEY_STREAM,
                area_symbol=area,
                kind="delineation_count_mismatch",
                detail=(
                    f"survey area {area}: written {written_area.delineation_count} delineation(s) on "
                    f"mupolygonkey, USDA SDA currently holds {source.delineation_count}"
                ),
                source_response=source.raw_response,
            )
        )
    written_vintage_date = None if written_area.newest_vintage is None else written_area.newest_vintage.date()
    if source.saverest is not None and (written_vintage_date is None or source.saverest.date() > written_vintage_date):
        written_vintage = "(none written)" if written_vintage_date is None else written_vintage_date.isoformat()
        findings.append(
            SoilSurveyValidationFinding(
                lane=SOIL_SURVEY_STREAM,
                area_symbol=area,
                kind="vintage_stale",
                detail=(
                    f"survey area {area} republished at USDA SDA on {source.saverest.date().isoformat()}, "
                    f"newer than the written vintage {written_vintage}"
                ),
                source_response=source.raw_response,
            )
        )
    return findings


def _sql_string_literal(value: str) -> str:
    """Quote a T-SQL string literal; defense in depth alongside `_validated_area_symbol`'s format check."""
    return "'" + value.replace("'", "''") + "'"


def _survey_area_summary_query(area_symbol: str) -> str:
    """Build the T-SQL SDA's tabular endpoint has no bind-parameter mechanism to run instead of."""
    literal = _sql_string_literal(_validated_area_symbol(area_symbol))
    return (
        "SELECT COUNT(DISTINCT p.mupolygonkey) AS delineation_count, MAX(sac.saverest) AS saverest "
        "FROM mupolygon p "
        "INNER JOIN mapunit mu ON mu.mukey = p.mukey "
        "INNER JOIN legend lg ON lg.lkey = mu.lkey "
        "LEFT JOIN sacatalog sac ON sac.areasymbol = lg.areasymbol "
        f"WHERE lg.areasymbol = {literal}"
    )


def _parse_survey_area_vintage(raw: str) -> datetime:
    """Parse SDA's US-locale `saverest` text, discarding the clock time it carries no timezone for.

    `docs/lanes/soil-survey.md` section 5, point 5: keeping the time would fabricate a timezone the
    publisher never stated.
    """
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", raw.strip())
    if match is None:
        raise SoilSurveySdaResponseError(f"USDA SDA saverest value {raw!r} is not US-locale date text")
    month, day, year = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError as error:
        raise SoilSurveySdaResponseError(f"USDA SDA saverest value {raw!r} is not a valid calendar date") from error


def _parse_sda_table_response(payload: object) -> Mapping[str, str | None]:
    """Decode SDA's `format: JSON+COLUMNNAME` body: a header row, then at most one data row."""
    if not isinstance(payload, Mapping):
        raise SoilSurveySdaResponseError("USDA SDA response body is not a JSON object")
    table = payload.get("Table")
    if table is None:
        return {}
    if not isinstance(table, list) or not table or not isinstance(table[0], list):
        raise SoilSurveySdaResponseError("USDA SDA response carries no usable result table")
    header = [str(name) for name in table[0]]
    if len(table) < _MIN_TABLE_ROWS_WITH_DATA:
        return dict.fromkeys(header)
    row = table[1]
    if not isinstance(row, list) or len(row) != len(header):
        raise SoilSurveySdaResponseError("USDA SDA response row does not match its own header")
    return {name: (None if value is None else str(value)) for name, value in zip(header, row, strict=True)}


@dataclass(frozen=True, slots=True)
class HttpxSoilSurveySdaClient:
    """Production `SoilSurveySdaClient`: raw T-SQL over USDA SDA's tabular POST endpoint.

    Not exercised against the live endpoint by this lane's own tests -- the reconciliation LOGIC
    above is proven against fakes of this interface instead; only this class's own request/response
    plumbing is proven with a mocked transport.
    """

    client: httpx.AsyncClient

    async def fetch_survey_area_summary(self, area_symbol: str) -> SdaSurveyAreaSummary:
        query = _survey_area_summary_query(area_symbol)
        response = await self.client.post(
            _SDA_TABULAR_ENDPOINT,
            json={"format": "JSON+COLUMNNAME", "query": query},
            timeout=_SDA_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        row = _parse_sda_table_response(response.json())
        raw_count = row.get("delineation_count")
        raw_saverest = row.get("saverest")
        if raw_count is None:
            raise SoilSurveySdaResponseError(f"USDA SDA returned no delineation_count for {area_symbol!r}")
        return SdaSurveyAreaSummary(
            area_symbol=area_symbol,
            delineation_count=int(raw_count),
            saverest=None if raw_saverest is None else _parse_survey_area_vintage(raw_saverest),
            raw_response=json.dumps(row, sort_keys=True),
        )
