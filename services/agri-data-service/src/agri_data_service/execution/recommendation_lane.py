"""Session-bound half of the recommendation lane: governed reads, label loading, receipts.

`method/ml/` holds the pure half -- the harvest contract, the checksums, the envelope verdict
rules, the design matrix, the fit and the artifact -- and may not import SQLAlchemy (the layer
import contract enforces that). Everything needing an `AsyncSession` lives here, in the same
layer as `covariate_wind_persist.py`, whose insert-or-resolve receipt shape this module reuses.

Rationale and the measured coverage of each feature block: `AGENTS.md` (this directory).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution import forecast_receipt_writer as _receipt_writer
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.method.ml.covariates_v2 import (
    AS_OF_MODE_BY_SCHEMA_VERSION,
    CLIMATE_MIN_COMPLETE_DAYS,
    CLIMATE_WINDOW_DAYS,
    SCHEMA_VERSION_V1,
    CovariateReadError,
    CovariateVector,
    CoverageSummary,
    FeatureDefinition,
    SiteClimateTerms,
    classify_aridity,
    hargreaves_reference_evapotranspiration_mm,
    require_supported_schema_version,
)
from agri_data_service.method.ml.expert_label_plane import (
    AGENT_REVIEWER_IDENTITY,
    CONFIDENCE_WEIGHTS,
    DEFAULT_LICENSE_POSTURE,
    ENVELOPE_TERM_SUPPORT,
    NUMERIC_TERM_TOLERANCE,
    REVIEW_TIER_AGENT_REVIEWED,
    ExpertLabelPlaneError,
    HarvestDocument,
    LabelKind,
    LabelLoadReport,
    MappingReport,
    bounded_issue_days,
    envelope_checksum_for,
    evaluate_envelope,
    label_checksum_for,
    label_key_for,
    loader_code_checksum,
    normalize_subject,
    release_checksum_for,
    release_key_for,
    source_checksum_for,
    source_key_for,
    summarize_slices,
)
from agri_data_service.method.ml.recommendation_models import (
    ARTIFACT_URI_PREFIX,
    CLAIM_TIER,
    DEFAULT_REGULARIZATION_STRENGTH,
    EVALUATION_LABEL,
    LABEL_KIND_BY_MODEL_KIND,
    MODEL_NAME_BY_KIND,
    TRAINING_DEFINITION_NAME,
    TRAINING_DEFINITION_VERSION,
    TRAINING_HANDLER_TOKEN,
    TRAINING_LEASE_SECONDS,
    TRAINING_MAX_ATTEMPTS,
    TRAINING_QUEUE_NAME,
    TRAINING_TIME_BUDGET_SECONDS,
    ModelKind,
    RecommendationTrainingError,
    TrainingReport,
    assemble_training_matrix,
    build_artifact,
    evaluate_leave_one_source_out,
    fit_logistic,
    fit_standardization,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

_MAX_LABELS_PER_RELEASE: Final = 5_000
_MAX_INSTANCES_PER_LABEL: Final = 400
_MAX_TRAINING_ROWS: Final = 200_000
_MAX_SIGNAL_ROWS: Final = 200_000
_MAX_FEATURE_ROWS: Final = 500_000
_MIN_LABELS_FOR_FIT: Final = 6
_IDENTITY_KEY_MAX_LENGTH: Final = 255
_DIGEST_KEY_LENGTH: Final = 16

_CLIMATE_SIGNAL_NAMES: Final = (
    "air_temperature_max",
    "air_temperature_mean",
    "air_temperature_min",
    "precipitation",
)

_SELECT_DAILY_SIGNAL_SERIES = text(load_query_sql("execution/select_daily_signal_series.sql"))
_SELECT_COVARIATE_VECTORS = text(load_query_sql("execution/select_covariate_vectors.sql"))
_SELECT_DECLARED_GAPS = text(
    "SELECT stream_key, gap_kind, gap_reason FROM agri.covariate_declared_gap(:schema_version)"
)
_SELECT_FEATURE_SCHEMA = text(
    "SELECT feature_index, feature_name, feature_kind, stream_key, lag_days, window_days "
    "FROM agri.covariate_feature_schema(:schema_version) ORDER BY feature_index"
)
_SELECT_CELL_LATITUDE = text(
    "SELECT ST_Y(ST_Centroid(geometry))::double precision AS latitude_degrees "
    "FROM agri.spatial_cell WHERE id = CAST(:cell_id AS uuid)"
)

_INSERT_SOURCE = text(load_query_sql("execution/insert_expert_label_source.sql"))
_INSERT_RELEASE = text(load_query_sql("execution/insert_expert_label_release.sql"))
_INSERT_LABEL = text(load_query_sql("execution/insert_expert_label.sql"))
_ADVANCE_REVIEW = text(load_query_sql("execution/advance_expert_label_review.sql"))
_SELECT_TRAINABLE_LABELS = text(load_query_sql("execution/select_trainable_expert_labels.sql"))
_INSERT_TRAINING_INSTANCE = text(load_query_sql("execution/insert_expert_label_training_instance.sql"))
_SELECT_MATCHED_INSTANCES = text(load_query_sql("execution/select_matched_training_instances.sql"))
_INSERT_RECEIPT = text(load_query_sql("execution/insert_recommendation_training_receipt.sql"))
# The four statements below are the SAME governed files `covariate_wind_persist.py` and
# `analog_ensemble_persist.py` load; bound here from `forecast_receipt_writer.py` (the one
# `load_query_sql` call site per file) rather than loaded a third time -- see
# tests/test_sql_tree_conventions.py::test_loaded_exactly_once.
_INSERT_JOB_DEFINITION = _receipt_writer.INSERT_JOB_DEFINITION
_INSERT_JOB_RUN = _receipt_writer.INSERT_JOB_RUN
_INSERT_MODEL_ARTIFACT = _receipt_writer.INSERT_MODEL_ARTIFACT
_INSERT_JOB_OUTPUT = _receipt_writer.INSERT_JOB_OUTPUT

_LOOKUP_SOURCE = text("SELECT id FROM agri.expert_label_source WHERE source_key = :source_key")
_LOOKUP_RELEASE = text("SELECT id FROM agri.expert_label_release WHERE release_key = :release_key")
_LOOKUP_LABEL = text("SELECT id FROM agri.expert_label WHERE label_key = :label_key")
_RELEASE_SUMMARY = text(
    "SELECT harvest_slice, label_kind, review_state, outcome, label_count, source_count, "
    "subject_count, refuted_count, mean_confidence, review_tier "
    "FROM agri.expert_label_release_summary(:release_key)"
)
_LOOKUP_JOB_DEFINITION = text("SELECT id FROM agri.job_definition WHERE name = :name AND version = :version")
_LOOKUP_JOB_RUN = text("SELECT id FROM agri.job_run WHERE logical_run_key = :logical_run_key")
_LOOKUP_ARTIFACT = text("SELECT id FROM agri.artifact WHERE uri = :uri AND checksum_sha256 = :checksum_sha256")
_LOOKUP_JOB_OUTPUT = text("SELECT id FROM agri.job_output WHERE job_run_id = :job_run_id AND output_key = :output_key")
_LOOKUP_RECEIPT = text("SELECT id FROM agri.recommendation_training_receipt WHERE training_key = :training_key")
_LOOKUP_LABEL_RELEASE = text(
    "SELECT id, review_tier, release_checksum FROM agri.expert_label_release WHERE release_key = :release_key"
)


async def load_feature_schema(session: AsyncSession, *, schema_version: str) -> tuple[FeatureDefinition, ...]:
    """Read the pinned feature registry for a schema version, in index order."""
    require_supported_schema_version(schema_version)
    result = await session.execute(_SELECT_FEATURE_SCHEMA, {"schema_version": schema_version})
    return tuple(
        FeatureDefinition(
            feature_index=int(row["feature_index"]),
            feature_name=str(row["feature_name"]),
            feature_kind=str(row["feature_kind"]),
            stream_key=str(row["stream_key"]),
            lag_days=int(row["lag_days"]),
            window_days=int(row["window_days"]),
        )
        for row in result.mappings().all()
    )


async def load_declared_gaps(session: AsyncSession, *, schema_version: str) -> tuple[dict[str, str], ...]:
    """Read the streams this schema version declares as gaps rather than emitting empty."""
    require_supported_schema_version(schema_version)
    result = await session.execute(_SELECT_DECLARED_GAPS, {"schema_version": schema_version})
    return tuple(
        {
            "stream_key": str(row["stream_key"]),
            "gap_kind": str(row["gap_kind"]),
            "gap_reason": str(row["gap_reason"]),
        }
        for row in result.mappings().all()
    )


async def load_covariate_vectors(  # noqa: PLR0913 - one parameter per pinned read boundary
    session: AsyncSession,
    *,
    cell_id: str,
    window_start: date,
    window_end: date,
    as_of_time: datetime,
    schema_version: str,
    wanted_days: Sequence[date] | None = None,
) -> tuple[tuple[CovariateVector, ...], CoverageSummary]:
    """Read the covariate plane for a cell and pivot it into ordered vectors plus a coverage summary.

    `wanted_days` filters the emitted vectors after the read; the read itself is bounded by the
    window and a row cap, so a caller can never ask for an unbounded scan.
    """
    require_supported_schema_version(schema_version)
    if window_end < window_start:
        raise CovariateReadError("window_end must not precede window_start")
    if as_of_time.utcoffset() is None:
        raise CovariateReadError("as_of_time must include a timezone")

    definitions = await load_feature_schema(session, schema_version=schema_version)
    if not definitions:
        raise CovariateReadError(f"{schema_version} declares no features")
    names = tuple(definition.feature_name for definition in definitions)

    result = await session.execute(
        _SELECT_COVARIATE_VECTORS,
        {
            "cell_id": cell_id,
            "window_start": datetime(window_start.year, window_start.month, window_start.day, tzinfo=UTC),
            "window_end": datetime(window_end.year, window_end.month, window_end.day, tzinfo=UTC),
            "as_of_time": as_of_time.astimezone(UTC),
            "schema_version": schema_version,
            "row_limit": _MAX_FEATURE_ROWS,
        },
    )
    rows = result.mappings().all()
    if len(rows) >= _MAX_FEATURE_ROWS:
        raise CovariateReadError(
            f"covariate read hit its {_MAX_FEATURE_ROWS} row cap; narrow the window rather than raising the cap"
        )

    per_day: dict[date, list[float | None]] = {}
    availability: dict[date, datetime | None] = {}
    emitted_by_kind: dict[str, int] = {}
    present_by_kind: dict[str, int] = {}
    for row in rows:
        observed_date = row["observed_date"]
        position = int(row["feature_index"]) - 1
        if position < 0 or position >= len(names):
            raise CovariateReadError(f"feature index {row['feature_index']} is outside the {schema_version} vector")
        values = per_day.setdefault(observed_date, [None] * len(names))
        raw_value = row["feature_value"]
        values[position] = None if raw_value is None else float(raw_value)
        kind = str(row["feature_kind"])
        emitted_by_kind[kind] = emitted_by_kind.get(kind, 0) + 1
        if raw_value is not None:
            present_by_kind[kind] = present_by_kind.get(kind, 0) + 1
        available_at = row["data_available_at"]
        if available_at is not None:
            current = availability.get(observed_date)
            if current is None or available_at > current:
                availability[observed_date] = available_at

    wanted = None if wanted_days is None else set(wanted_days)
    vectors = tuple(
        CovariateVector(
            cell_id=cell_id,
            observed_date=observed_date,
            as_of_time=as_of_time.astimezone(UTC),
            schema_version=schema_version,
            feature_names=names,
            feature_values=tuple(values),
            present_count=sum(1 for value in values if value is not None),
            max_data_available_at=availability.get(observed_date),
        )
        for observed_date, values in sorted(per_day.items())
        if wanted is None or observed_date in wanted
    )
    coverage = CoverageSummary(
        schema_version=schema_version,
        as_of_mode=AS_OF_MODE_BY_SCHEMA_VERSION[schema_version],
        day_count=len(per_day),
        complete_day_count=sum(1 for values in per_day.values() if all(value is not None for value in values)),
        emitted_row_count=len(rows),
        present_row_count=sum(present_by_kind.values()),
        present_by_feature_kind={
            kind: (present_by_kind.get(kind, 0), emitted) for kind, emitted in emitted_by_kind.items()
        },
    )
    return vectors, coverage


async def load_site_climate_terms(  # noqa: PLR0912, PLR0915 - one flat pass per trailing-year term, kept linear
    session: AsyncSession,
    *,
    cell_id: str,
    issue_days: Sequence[date],
    as_of_time: datetime,
) -> dict[date, SiteClimateTerms]:
    """Derive the envelope-comparable climate terms for each issue day from governed observations.

    Every term reads only the trailing `CLIMATE_WINDOW_DAYS` **before** the issue day, so a
    day-D term can never contain a day-D observation, and every row is availability-gated on
    `data_available_at <= as_of_time`.
    """
    if not issue_days:
        return {}
    if as_of_time.utcoffset() is None:
        raise CovariateReadError("as_of_time must include a timezone")

    earliest = min(issue_days)
    latest = max(issue_days)
    window_start = date.fromordinal(earliest.toordinal() - CLIMATE_WINDOW_DAYS)
    latitude_row = (await session.execute(_SELECT_CELL_LATITUDE, {"cell_id": cell_id})).mappings().first()
    if latitude_row is None:
        raise CovariateReadError(f"no agri.spatial_cell with id {cell_id}")
    latitude_degrees = float(latitude_row["latitude_degrees"])

    result = await session.execute(
        _SELECT_DAILY_SIGNAL_SERIES,
        {
            "cell_id": cell_id,
            "signal_names": list(_CLIMATE_SIGNAL_NAMES),
            "window_start": window_start,
            "window_end": latest,
            "as_of_time": as_of_time.astimezone(UTC),
            "row_limit": _MAX_SIGNAL_ROWS,
        },
    )
    rows = result.mappings().all()
    if len(rows) >= _MAX_SIGNAL_ROWS:
        raise CovariateReadError(f"site-climate read hit its {_MAX_SIGNAL_ROWS} row cap; narrow the issue-day range")

    by_day: dict[date, dict[str, float]] = {}
    for row in rows:
        value = row["normalized_value"]
        if value is None:
            continue
        by_day.setdefault(row["observed_date"], {})[str(row["signal_name"])] = float(value)

    terms: dict[date, SiteClimateTerms] = {}
    for issue_day in sorted(set(issue_days)):
        window_days = [
            date.fromordinal(ordinal)
            for ordinal in range(issue_day.toordinal() - CLIMATE_WINDOW_DAYS, issue_day.toordinal())
        ]
        precipitation: list[float] = []
        mean_temperature: list[float] = []
        frost_free = 0
        frost_observed = 0
        evapotranspiration = 0.0
        evapotranspiration_days = 0
        for window_day in window_days:
            observations = by_day.get(window_day)
            if observations is None:
                continue
            if "precipitation" in observations:
                precipitation.append(observations["precipitation"])
            if "air_temperature_mean" in observations:
                mean_temperature.append(observations["air_temperature_mean"])
            minimum = observations.get("air_temperature_min")
            maximum = observations.get("air_temperature_max")
            if minimum is not None:
                frost_observed += 1
                if minimum > 0.0:
                    frost_free += 1
            if minimum is not None and maximum is not None and maximum >= minimum:
                evapotranspiration += hargreaves_reference_evapotranspiration_mm(
                    latitude_degrees=latitude_degrees,
                    day_of_year=window_day.timetuple().tm_yday,
                    temperature_min_c=minimum,
                    temperature_max_c=maximum,
                )
                evapotranspiration_days += 1

        complete = len(precipitation) >= CLIMATE_MIN_COMPLETE_DAYS
        annual_precipitation = sum(precipitation) if complete else None
        annual_temperature = (
            sum(mean_temperature) / len(mean_temperature)
            if len(mean_temperature) >= CLIMATE_MIN_COMPLETE_DAYS
            else None
        )
        aridity_index: float | None = None
        aridity: str | None = None
        if (
            annual_precipitation is not None
            and evapotranspiration_days >= CLIMATE_MIN_COMPLETE_DAYS
            and evapotranspiration > 0.0
        ):
            aridity_index = annual_precipitation / evapotranspiration
            aridity = classify_aridity(aridity_index)
        terms[issue_day] = SiteClimateTerms(
            observed_date=issue_day,
            mean_annual_precipitation_mm=annual_precipitation,
            mean_annual_temperature_c=annual_temperature,
            growing_season_frost_free_days=(frost_free if frost_observed >= CLIMATE_MIN_COMPLETE_DAYS else None),
            aridity=aridity,
            aridity_index=aridity_index,
            contributing_day_count=len(precipitation),
        )
    return terms


async def _scalar_uuid(
    session: AsyncSession, statement: TextClause, parameters: Mapping[str, object]
) -> uuid.UUID | None:
    value = (await session.execute(statement, dict(parameters))).scalar_one_or_none()
    return None if value is None else uuid.UUID(str(value))


async def _insert_or_resolve(  # noqa: PLR0913 - one parameter per statement/parameter pair, plus the label
    session: AsyncSession,
    *,
    insert_statement: TextClause,
    insert_parameters: Mapping[str, object],
    lookup_statement: TextClause,
    lookup_parameters: Mapping[str, object],
    description: str,
) -> uuid.UUID:
    """Insert an identity-keyed row, or resolve the one a previous run already wrote."""
    inserted = await _scalar_uuid(session, insert_statement, insert_parameters)
    if inserted is not None:
        return inserted
    existing = await _scalar_uuid(session, lookup_statement, lookup_parameters)
    if existing is None:
        raise ExpertLabelPlaneError(f"{description} was neither inserted nor found")
    return existing


async def load_labels(
    session: AsyncSession,
    document: HarvestDocument,
    *,
    harvest_document_checksum: str,
    harvest_document_uri: str,
    persist: bool = False,
) -> LabelLoadReport:
    """Load a harvest as `draft`, then advance every non-refuted label to `agent_reviewed`.

    Refuted labels are loaded too, and rejected: a refuted claim that is merely dropped leaves
    no record that a verifier caught it. Nothing here reaches `approved`; the caller owns the
    commit.
    """
    # A label with no stated condition envelope cannot be intersected with any governed stream,
    # which is the one thing this plane exists to do. It is reported with its reason rather than
    # stored envelope-less or silently dropped.
    unloadable = tuple(
        {
            "subject": label.subject,
            "label_kind": label.label_kind,
            "harvest_slice": label.harvest_slice,
            "doi": label.source.doi or "",
            "reason": "the harvest states no condition envelope, so no governed stream can be intersected with it",
        }
        for label in (*document.kept, *document.rejected)
        if not label.condition_envelope
    )
    loadable_kept = [label for label in document.kept if label.condition_envelope]
    loadable_rejected = [label for label in document.rejected if label.condition_envelope]

    kept_keys = [label_key_for(label) for label in loadable_kept]
    refuted_keys = [label_key_for(label) for label in loadable_rejected]
    release_key = release_key_for(
        harvested_at=document.harvested_at, harvest_document_checksum=harvest_document_checksum
    )
    slice_summary = summarize_slices(loadable_kept, loadable_rejected)
    non_refuted = [label for label in loadable_kept if not label.citation_check.refuted]
    refuted_in_kept = [label for label in loadable_kept if label.citation_check.refuted]
    agent_reviewed_count = len(non_refuted)
    rejected_count = len(loadable_rejected) + len(refuted_in_kept)
    label_count = len(loadable_kept) + len(loadable_rejected)
    release_checksum = release_checksum_for(
        harvest_document_checksum=harvest_document_checksum,
        label_keys=[*kept_keys, *refuted_keys],
        review_tier=REVIEW_TIER_AGENT_REVIEWED,
    )
    source_count = len({source_key_for(label.source) for label in (*loadable_kept, *loadable_rejected)})

    report = LabelLoadReport(
        release_key=release_key,
        release_id=None,
        harvest_document_checksum=harvest_document_checksum,
        release_checksum=release_checksum,
        review_tier=REVIEW_TIER_AGENT_REVIEWED,
        source_count=source_count,
        label_count=label_count,
        draft_count=0,
        agent_reviewed_count=agent_reviewed_count,
        rejected_count=rejected_count,
        approved_count=0,
        slice_summary=slice_summary,
        unloadable=unloadable,
        persisted=False,
    )
    if not persist:
        return report

    release_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_RELEASE,
        insert_parameters={
            "release_key": release_key,
            "harvest_document_uri": harvest_document_uri,
            "harvest_document_checksum": harvest_document_checksum,
            "harvested_at": _harvested_at_instant(document.harvested_at),
            "label_count": label_count,
            # The release row records the states the load LANDS in, which is what a reader of
            # the row needs; the draft pass below is a step inside this one transaction.
            "draft_count": 0,
            "agent_reviewed_count": agent_reviewed_count,
            "approved_count": 0,
            "rejected_count": rejected_count,
            "slice_summary": canonical_json(slice_summary),
            "review_tier": REVIEW_TIER_AGENT_REVIEWED,
            "release_checksum": release_checksum,
            "loader_code_checksum": loader_code_checksum(),
        },
        lookup_statement=_LOOKUP_RELEASE,
        lookup_parameters={"release_key": release_key},
        description="expert label release",
    )

    for label in (*loadable_kept, *loadable_rejected):
        source_id = await _insert_or_resolve(
            session,
            insert_statement=_INSERT_SOURCE,
            insert_parameters={
                "source_key": source_key_for(label.source),
                "doi": label.source.doi,
                "source_url": label.source.url or (f"https://doi.org/{label.source.doi}" if label.source.doi else None),
                "title": label.source.title,
                "publication_year": label.source.year,
                "journal_or_publisher": label.source.journal or "unstated",
                "edition_or_version": None,
                "license_posture": DEFAULT_LICENSE_POSTURE,
                "source_checksum": source_checksum_for(label.source),
            },
            lookup_statement=_LOOKUP_SOURCE,
            lookup_parameters={"source_key": source_key_for(label.source)},
            description=f"expert label source for {label.source.title!r}",
        )
        await _insert_or_resolve(
            session,
            insert_statement=_INSERT_LABEL,
            insert_parameters={
                "label_key": label_key_for(label),
                "release_id": release_id,
                "source_id": source_id,
                "label_kind": label.label_kind,
                "subject": label.subject,
                "subject_normalized": normalize_subject(label.subject),
                "outcome": label.outcome,
                "condition_envelope": canonical_json(label.condition_envelope),
                "envelope_checksum": envelope_checksum_for(label),
                "rationale": label.rationale,
                "supporting_quote": label.resolved_quote(),
                "confidence": label.confidence,
                "confidence_weight": CONFIDENCE_WEIGHTS[label.confidence],
                "harvest_slice": label.harvest_slice,
                "citation_check_refuted": label.citation_check.refuted,
                "citation_check_doi_resolves": label.citation_check.doi_resolves,
                "citation_check_reason": label.citation_check.reason,
                "label_checksum": label_checksum_for(label),
            },
            lookup_statement=_LOOKUP_LABEL,
            lookup_parameters={"label_key": label_key_for(label)},
            description=f"expert label for {label.subject!r}",
        )

    advanced = (
        (
            await session.execute(
                _ADVANCE_REVIEW,
                {
                    "release_id": release_id,
                    "reviewed_by": AGENT_REVIEWER_IDENTITY,
                    "reviewed_at": datetime.now(tz=UTC),
                },
            )
        )
        .mappings()
        .all()
    )
    advanced_states = [str(row["review_state"]) for row in advanced]
    return LabelLoadReport(
        release_key=release_key,
        release_id=release_id,
        harvest_document_checksum=harvest_document_checksum,
        release_checksum=release_checksum,
        review_tier=REVIEW_TIER_AGENT_REVIEWED,
        source_count=source_count,
        label_count=label_count,
        draft_count=sum(1 for state in advanced_states if state == "draft"),
        agent_reviewed_count=sum(1 for state in advanced_states if state == "agent_reviewed"),
        rejected_count=sum(1 for state in advanced_states if state == "rejected"),
        approved_count=0,
        slice_summary=slice_summary,
        unloadable=unloadable,
        persisted=True,
    )


def _harvested_at_instant(harvested_at: str) -> datetime:
    """Read the harvest's date (or timestamp) as a UTC instant."""
    try:
        parsed = datetime.fromisoformat(harvested_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExpertLabelPlaneError(f"harvested_at {harvested_at!r} is not ISO-8601") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def release_summary(session: AsyncSession, *, release_key: str) -> tuple[dict[str, object], ...]:
    """Read the canonical per-slice/state/outcome accounting of a label release."""
    result = await session.execute(_RELEASE_SUMMARY, {"release_key": release_key})
    return tuple(dict(row) for row in result.mappings().all())


async def map_labels_to_training_instances(  # noqa: PLR0913 - one parameter per pinned input
    session: AsyncSession,
    *,
    release_key: str,
    label_kind: LabelKind,
    cell_id: str,
    window_start: date,
    window_end: date,
    as_of_time: datetime,
    schema_version: str = SCHEMA_VERSION_V1,
    persist: bool = False,
) -> MappingReport:
    """Intersect each trainable label's envelope with the governed streams and emit instances.

    One instance per (label, evaluation day): the day's covariate vector, the per-term verdicts,
    and the envelope terms no governed stream can express. Excluded and unexpressible instances
    are recorded too -- the count of days an envelope excluded is the evidence it was evaluated.
    """
    require_supported_schema_version(schema_version)
    if as_of_time.utcoffset() is None:
        raise ExpertLabelPlaneError("as_of_time must include a timezone")

    labels = (
        (
            await session.execute(
                _SELECT_TRAINABLE_LABELS,
                {"release_key": release_key, "label_kind": label_kind, "row_limit": _MAX_LABELS_PER_RELEASE},
            )
        )
        .mappings()
        .all()
    )
    if not labels:
        raise ExpertLabelPlaneError(f"release {release_key!r} holds no agent-reviewed {label_kind} labels to map")
    release_id = labels[0]["release_id"]

    issue_days = bounded_issue_days(window_start, window_end)
    if len(issue_days) > _MAX_INSTANCES_PER_LABEL:
        raise ExpertLabelPlaneError(
            f"the evaluation grid holds {len(issue_days)} days, above the {_MAX_INSTANCES_PER_LABEL} cap"
        )
    climate = await load_site_climate_terms(session, cell_id=cell_id, issue_days=issue_days, as_of_time=as_of_time)
    vectors, _coverage = await load_covariate_vectors(
        session,
        cell_id=cell_id,
        window_start=min(issue_days),
        window_end=max(issue_days),
        as_of_time=as_of_time,
        schema_version=schema_version,
        wanted_days=issue_days,
    )
    vector_by_day: dict[date, CovariateVector] = {vector.observed_date: vector for vector in vectors}

    matched = excluded = unexpressible_instances = 0
    unexpressible_terms: dict[str, int] = {}
    written = 0
    for label in labels:
        envelope = dict(label["condition_envelope"])
        for issue_day in issue_days:
            day_climate = climate.get(issue_day)
            vector = vector_by_day.get(issue_day)
            if day_climate is None or vector is None:
                continue
            verdicts, unexpressible, match_state = evaluate_envelope(envelope, day_climate)
            for term in unexpressible:
                unexpressible_terms[term] = unexpressible_terms.get(term, 0) + 1
            if match_state == "matched":
                matched += 1
            elif match_state == "excluded":
                excluded += 1
            else:
                unexpressible_instances += 1

            envelope_match = {
                "site_climate": day_climate.to_payload(),
                "terms": {term: verdict.to_payload() for term, verdict in verdicts.items()},
                "tolerances": {
                    term: NUMERIC_TERM_TOLERANCE[term] for term in verdicts if term in NUMERIC_TERM_TOLERANCE
                },
            }
            instance_checksum = sha256_digest(
                canonical_json(
                    {
                        "digest_version": "agri_expert_label_training_instance_v1",
                        "label_checksum": label["label_checksum"],
                        "cell_id": cell_id,
                        "observed_date": issue_day.isoformat(),
                        "feature_schema_version": schema_version,
                        "feature_checksum": vector.checksum,
                        "match_state": match_state,
                        "envelope_match": envelope_match,
                        "unexpressible_terms": list(unexpressible),
                    }
                )
            )
            written += 1
            if not persist:
                continue
            await session.execute(
                _INSERT_TRAINING_INSTANCE,
                {
                    "label_id": label["label_id"],
                    "release_id": release_id,
                    "spatial_cell_id": cell_id,
                    "observed_date": issue_day,
                    "as_of_time": as_of_time.astimezone(UTC),
                    "feature_schema_version": schema_version,
                    "feature_values": canonical_json(vector.to_payload()),
                    "feature_checksum": vector.checksum,
                    "envelope_match": canonical_json(envelope_match),
                    "unexpressible_terms": canonical_json(list(unexpressible)),
                    "match_state": match_state,
                    "instance_checksum": instance_checksum,
                },
            )

    return MappingReport(
        release_key=release_key,
        cell_id=cell_id,
        schema_version=schema_version,
        as_of_time=as_of_time.astimezone(UTC),
        label_count=len(labels),
        issue_day_count=len(issue_days),
        instance_count=written,
        matched_count=matched,
        excluded_count=excluded,
        unexpressible_count=unexpressible_instances,
        unexpressible_terms=unexpressible_terms,
        term_support=ENVELOPE_TERM_SUPPORT,
        climate_day_count=sum(1 for terms in climate.values() if terms.mean_annual_precipitation_mm is not None),
        persisted=persist,
    )


async def train_recommendation_model(  # noqa: PLR0913 - one parameter per pinned input
    session: AsyncSession,
    *,
    model_kind: ModelKind,
    release_key: str,
    feature_schema_version: str = SCHEMA_VERSION_V1,
    regularization_strength: float = DEFAULT_REGULARIZATION_STRENGTH,
    persist: bool = False,
) -> TrainingReport:
    """Read the matched instances, fit, evaluate, and (when asked) write the receipt chain."""
    require_supported_schema_version(feature_schema_version)
    started_at = datetime.now(tz=UTC)
    release = (await session.execute(_LOOKUP_LABEL_RELEASE, {"release_key": release_key})).mappings().first()
    if release is None:
        raise RecommendationTrainingError(f"no agri.expert_label_release with release_key {release_key!r}")

    rows = (
        (
            await session.execute(
                _SELECT_MATCHED_INSTANCES,
                {
                    "release_key": release_key,
                    "label_kind": LABEL_KIND_BY_MODEL_KIND[model_kind],
                    "feature_schema_version": feature_schema_version,
                    "row_limit": _MAX_TRAINING_ROWS,
                },
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        raise RecommendationTrainingError(
            f"release {release_key!r} holds no matched {feature_schema_version} training instances for "
            f"{model_kind}; run the envelope mapper first"
        )

    matrix = assemble_training_matrix([dict(row) for row in rows], model_kind=model_kind)
    if matrix.label_count < _MIN_LABELS_FOR_FIT:
        raise RecommendationTrainingError(
            f"{matrix.label_count} agent-reviewed labels is below the {_MIN_LABELS_FOR_FIT}-label floor "
            "for a fit; harvest more evidence rather than training on fewer"
        )
    standardization = fit_standardization(matrix.features)
    model = fit_logistic(
        standardization.apply(matrix.features),
        matrix.targets,
        matrix.weights,
        regularization_strength=regularization_strength,
    )
    artifact = build_artifact(
        matrix,
        model,
        standardization,
        model_kind=model_kind,
        feature_schema_version=feature_schema_version,
        label_release_key=release_key,
        label_release_checksum=str(release["release_checksum"]),
        label_review_tier=str(release["review_tier"]),
        regularization_strength=regularization_strength,
    )
    metrics = evaluate_leave_one_source_out(
        matrix, model_kind=model_kind, regularization_strength=regularization_strength
    )
    report = TrainingReport(
        model_kind=model_kind,
        model_name=MODEL_NAME_BY_KIND[model_kind],
        release_key=release_key,
        release_checksum=str(release["release_checksum"]),
        review_tier=str(release["review_tier"]),
        feature_schema_version=feature_schema_version,
        artifact=artifact,
        metrics=metrics,
        matrix_row_count=matrix.row_count,
        label_count=matrix.label_count,
        source_count=matrix.source_count,
        skipped_incomplete_rows=matrix.skipped_incomplete_rows,
    )
    if not persist:
        return report
    return await persist_training_receipt(
        session,
        report,
        label_release_id=uuid.UUID(str(release["id"])),
        started_at=started_at,
        completed_at=datetime.now(tz=UTC),
    )


async def persist_training_receipt(
    session: AsyncSession,
    report: TrainingReport,
    *,
    label_release_id: uuid.UUID,
    started_at: datetime,
    completed_at: datetime,
) -> TrainingReport:
    """Write the artifact, the job-ledger rows, and the training receipt in one transaction.

    Deliberately absent: any write to `agri.strategy_selection_receipt`,
    `agri.strategy_selection_candidate`, `agri.forecast_publication` or a publication pointer.
    The receipt table's own CHECKs pin `evaluation_only` true and `publication_authorized` false.
    """
    if report.review_tier == REVIEW_TIER_AGENT_REVIEWED and report.label_count == 0:
        raise RecommendationTrainingError("refusing to persist a receipt for a fit with no labels")

    document = report.artifact.to_document()
    artifact_checksum = report.artifact.checksum
    training_key = (
        f"recommendation:{report.model_kind}:{report.release_checksum[:_DIGEST_KEY_LENGTH]}:"
        f"{artifact_checksum[:_DIGEST_KEY_LENGTH]}"
    )[:_IDENTITY_KEY_MAX_LENGTH]

    definition_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_DEFINITION,
        insert_parameters={
            "name": TRAINING_DEFINITION_NAME,
            "version": TRAINING_DEFINITION_VERSION,
            "handler": TRAINING_HANDLER_TOKEN,
            "queue_name": TRAINING_QUEUE_NAME,
            "max_attempts": TRAINING_MAX_ATTEMPTS,
            "lease_seconds": TRAINING_LEASE_SECONDS,
            "time_budget_seconds": TRAINING_TIME_BUDGET_SECONDS,
            "parameters": canonical_json(
                {
                    "lane": "recommendation_train",
                    "label": EVALUATION_LABEL,
                    "publication_authorized": False,
                    "claim_tier": CLAIM_TIER,
                }
            ),
        },
        lookup_statement=_LOOKUP_JOB_DEFINITION,
        lookup_parameters={"name": TRAINING_DEFINITION_NAME, "version": TRAINING_DEFINITION_VERSION},
        description="recommendation training job definition",
    )
    logical_run_key = f"recommendation-training:{training_key}"[:_IDENTITY_KEY_MAX_LENGTH]
    job_run_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_RUN,
        insert_parameters={
            "job_definition_id": definition_id,
            # No governed release set is pinned: this lane's inputs are literature labels and the
            # covariate plane, not a forecast release set, and inventing one would misstate lineage.
            "release_set_id": None,
            "logical_run_key": logical_run_key,
            "scheduled_for": started_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "requested_by": f"agri-service ml recommendation-train --model {report.model_kind}",
        },
        lookup_statement=_LOOKUP_JOB_RUN,
        lookup_parameters={"logical_run_key": logical_run_key},
        description="recommendation training job run",
    )
    artifact_uri = f"{ARTIFACT_URI_PREFIX}/{report.model_kind}/{artifact_checksum}"
    artifact_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_MODEL_ARTIFACT,
        insert_parameters={
            "uri": artifact_uri,
            "checksum_sha256": artifact_checksum,
            "metadata_json": canonical_json(
                {
                    "label": EVALUATION_LABEL,
                    "claim_tier": CLAIM_TIER,
                    "label_review_tier": report.review_tier,
                    "label_release_key": report.release_key,
                    "feature_schema_version": report.feature_schema_version,
                    "training_code_checksum": report.artifact.training_code_checksum,
                }
            ),
            "model_document": canonical_json(document),
        },
        lookup_statement=_LOOKUP_ARTIFACT,
        lookup_parameters={"uri": artifact_uri, "checksum_sha256": artifact_checksum},
        description="recommendation model artifact",
    )
    output_key = f"recommendation-model:{report.model_kind}:{artifact_checksum[:_DIGEST_KEY_LENGTH]}"
    job_output_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_OUTPUT,
        insert_parameters={
            "job_run_id": job_run_id,
            "artifact_id": artifact_id,
            "output_key": output_key,
            "kind": "recommendation_model_training",
            "checksum_sha256": artifact_checksum,
            "row_count": 1,
            "metadata_json": canonical_json({"evaluation_checksum": report.evaluation_checksum}),
            "validated_at": completed_at,
        },
        lookup_statement=_LOOKUP_JOB_OUTPUT,
        lookup_parameters={"job_run_id": job_run_id, "output_key": output_key},
        description="recommendation model training output",
    )
    receipt_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_RECEIPT,
        insert_parameters={
            "training_key": training_key,
            "model_name": report.model_name,
            "model_kind": report.model_kind,
            "label_release_id": label_release_id,
            "label_review_tier": report.review_tier,
            "artifact_id": artifact_id,
            "job_run_id": job_run_id,
            "job_output_id": job_output_id,
            "feature_schema_version": report.feature_schema_version,
            "label_count": report.label_count,
            "training_instance_count": report.matrix_row_count,
            "source_count": report.source_count,
            "artifact_checksum": artifact_checksum,
            "evaluation_metrics": canonical_json(report.evaluation_payload()),
            "evaluation_checksum": report.evaluation_checksum,
            "parameter_checksum": report.parameter_checksum,
            "training_code_checksum": report.artifact.training_code_checksum,
            "started_at": started_at,
            "completed_at": completed_at,
        },
        lookup_statement=_LOOKUP_RECEIPT,
        lookup_parameters={"training_key": training_key},
        description="recommendation training receipt",
    )
    return TrainingReport(
        model_kind=report.model_kind,
        model_name=report.model_name,
        release_key=report.release_key,
        release_checksum=report.release_checksum,
        review_tier=report.review_tier,
        feature_schema_version=report.feature_schema_version,
        artifact=report.artifact,
        metrics=report.metrics,
        matrix_row_count=report.matrix_row_count,
        label_count=report.label_count,
        source_count=report.source_count,
        skipped_incomplete_rows=report.skipped_incomplete_rows,
        receipt_id=receipt_id,
        training_key=training_key,
        persisted=True,
    )
