"""Pure-unit coverage of the backfill driver and the geometry repair pass: no database, fakes for both seams."""

# ruff: noqa: PLR2004, PLR0911, PLR0913, ARG002

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.ingest import backfill as backfill_module
from agri_data_service.ingest.backfill import (
    DEFAULT_HISTORY_CHUNK,
    DEFAULT_HISTORY_YEARS,
    BackfillContractError,
    BackfillPlan,
    default_backfill_plan,
    default_history_window,
    history_chunks,
    merge_backfill_results,
    repair_identity,
    run_geometry_repair,
    run_source_backfill,
    run_source_job,
    subtract_years,
)
from agri_data_service.ingest.geometry import NEGATIVE_INFINITY_TIMESTAMP, GridCell
from agri_data_service.ingest.identity import (
    FIRMS_PRODUCER,
    OPEN_METEO_PRODUCER,
    USGS_NWIS_PRODUCER,
    WFIGS_PRODUCER,
    FeatureIdentity,
)
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX, UNCONFIGURED_BBOX_REASON
from agri_data_service.ingest.results import IngestionJobResult
from agri_data_service.ingest.source import (
    FetchRequest,
    FreshnessRule,
    HistoryCapability,
    HistoryUnavailableError,
    HistoryWindow,
    SourceContractError,
    accepted_writes,
    grid_cell_of,
    select_writes,
)
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.ingest.source import SourceShape


@pytest.fixture(autouse=True)
def _clear_ingest_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every bbox/record-cap read is a call-time environment read; each test starts from an unset environment."""
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS"):
        monkeypatch.delenv(variable, raising=False)


# --- deterministic chunking and the default two-year window -----------------------------------------


def test_history_chunks_cuts_a_window_into_fixed_steps_anchored_at_its_start() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 22, tzinfo=UTC))

    chunks = history_chunks(window, chunk=timedelta(days=7))

    assert [(chunk.start, chunk.end) for chunk in chunks] == [
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC)),
        (datetime(2026, 1, 8, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC)),
        (datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 1, 22, tzinfo=UTC)),
    ]


def test_history_chunks_is_deterministic_and_clips_the_last_chunk_to_the_window_end() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 10, tzinfo=UTC))

    first_run = history_chunks(window, chunk=timedelta(days=7))
    second_run = history_chunks(window, chunk=timedelta(days=7))

    assert first_run == second_run
    assert first_run[-1].end == window.end
    assert first_run[-1].end - first_run[-1].start == timedelta(days=2)


def test_history_chunks_rejects_a_non_positive_chunk() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 2, tzinfo=UTC))

    with pytest.raises(BackfillContractError):
        history_chunks(window, chunk=timedelta(0))


def test_subtract_years_steps_a_leap_day_back_to_the_28th_rather_than_raising() -> None:
    leap_day = datetime(2024, 2, 29, tzinfo=UTC)

    assert subtract_years(leap_day, 1) == datetime(2023, 2, 28, tzinfo=UTC)


def test_default_history_window_spans_two_whole_years_ending_at_the_run_date() -> None:
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)

    window = default_history_window(now)

    assert DEFAULT_HISTORY_YEARS == 2
    assert window.end == now
    assert window.start == datetime(2024, 8, 4, 12, 0, tzinfo=UTC)


def test_default_backfill_plan_uses_the_default_window_and_chunk() -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)

    plan = default_backfill_plan(now=now)

    assert plan.window == default_history_window(now)
    assert plan.chunk == DEFAULT_HISTORY_CHUNK
    assert plan.now == now


def test_backfill_plan_rejects_a_non_positive_chunk() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 2, tzinfo=UTC))

    with pytest.raises(BackfillContractError):
        BackfillPlan(window=window, chunk=timedelta(0))


# --- run_source_backfill: the fetch/write walk, bbox skip, and a typed history refusal --------------


class FakeSource:
    """A minimal IngestionSource stand-in, structurally satisfying the Protocol with canned responses."""

    def __init__(
        self,
        source_name: str = "fake-source",
        producer: str = "firms",
        channel: str = "layer:fire-detections",
        shape: SourceShape = "feature",
        history: HistoryCapability | None = None,
        records_by_chunk: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
    ) -> None:
        """Configure the canned history capability and the records each chunk window should answer with."""
        self.source_name = source_name
        self.producer = producer
        self.channel = channel
        self.shape = shape
        self.freshness = FreshnessRule(accepts_undated_records=True)
        self._history = history if history is not None else HistoryCapability(supported=True)
        self._records_by_chunk = dict(records_by_chunk or {})
        self.fetch_calls: list[HistoryWindow] = []
        self.current_records: list[Mapping[str, object]] = []
        self.current_fetches = 0

    def layer_reference(self) -> str:
        """A fixed layer name; call-time environment resolution is policy.py's concern, not this fake's."""
        return "fire-detections"

    def history_capability(self) -> HistoryCapability:
        """Report the canned capability, exactly as a real source's own typed refusal would."""
        return self._history

    async def fetch_current(self, request: FetchRequest) -> Sequence[Mapping[str, object]]:
        """Answer the canned current window, which run_source_job (and never run_source_backfill) asks for."""
        self.current_fetches += 1
        return list(self.current_records)

    async def fetch_history(self, request: FetchRequest, window: HistoryWindow) -> Sequence[Mapping[str, object]]:
        """Refuse exactly as a real source would, then answer the canned records for this window."""
        self._history.require(self.source_name, window)
        self.fetch_calls.append(window)
        return self._records_by_chunk.get((window.start.isoformat(), window.end.isoformat()), [])

    def build_write(self, record: Mapping[str, object], request: FetchRequest) -> FeatureWrite | None:
        """Map a canned record to a FeatureWrite carrying a real identity, undated so freshness never rejects it."""
        return FeatureWrite(
            layer_reference=self.layer_reference(),
            identity=FeatureIdentity(producer=self.producer, producer_local_id=str(record["id"]), observed_at=None),
            properties=dict(record),
            channel=self.channel,
        )


class RecordingWriter:
    """A FeatureWriter stand-in that records every batch it was handed and reports it all written."""

    def __init__(self) -> None:
        """Start with no recorded batches."""
        self.batches: list[list[FeatureWrite]] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        """Record the batch and report every write as newly written."""
        self.batches.append(list(writes))
        return len(writes)


class IdempotentRecordingWriter:
    """Mimics ingest_features' own idempotence: a producer-local id already seen is not written again."""

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.seen_external_ids: set[str] = set()

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        """Count only the writes whose external id this writer has never seen before."""
        written = 0
        for write in writes:
            if write.external_id not in self.seen_external_ids:
                self.seen_external_ids.add(write.external_id)
                written += 1
        return written


async def test_run_source_backfill_writes_one_result_per_chunk_through_the_forward_write_path() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 15, tzinfo=UTC))
    chunk_one = HistoryWindow(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC))
    chunk_two = HistoryWindow(datetime(2026, 1, 8, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC))
    source = FakeSource(
        records_by_chunk={
            (chunk_one.start.isoformat(), chunk_one.end.isoformat()): [{"id": "a"}, {"id": "b"}],
            (chunk_two.start.isoformat(), chunk_two.end.isoformat()): [{"id": "c"}],
        }
    )
    writer = RecordingWriter()
    plan = BackfillPlan(window=window, chunk=timedelta(days=7), bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX)

    results = await run_source_backfill(source, writer, plan)

    assert [result.records_seen for result in results] == [2, 1]
    assert [result.records_written for result in results] == [2, 1]
    assert all(result.status == "ingested" for result in results)
    assert source.fetch_calls == [chunk_one, chunk_two]
    assert [len(batch) for batch in writer.batches] == [2, 1]


async def test_run_source_backfill_skips_when_the_bbox_is_unconfigured() -> None:
    source = FakeSource()
    writer = RecordingWriter()
    plan = BackfillPlan(window=default_history_window(datetime(2026, 8, 4, tzinfo=UTC)), bbox=None)

    results = await run_source_backfill(source, writer, plan)

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert results[0].reason == UNCONFIGURED_BBOX_REASON
    assert source.fetch_calls == []
    assert writer.batches == []


async def test_run_source_backfill_surfaces_a_typed_history_refusal_as_a_skip_and_stops_walking() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 15, tzinfo=UTC))
    source = FakeSource(history=HistoryCapability(supported=False, reason="no history API"))
    writer = RecordingWriter()
    plan = BackfillPlan(window=window, chunk=timedelta(days=7), bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX)

    results = await run_source_backfill(source, writer, plan)

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert results[0].reason is not None
    assert "no history API" in results[0].reason
    assert writer.batches == []


async def test_re_running_a_completed_window_writes_nothing_new() -> None:
    window = HistoryWindow(start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 8, tzinfo=UTC))
    window_key = (window.start.isoformat(), window.end.isoformat())
    source = FakeSource(records_by_chunk={window_key: [{"id": "a"}, {"id": "b"}]})
    writer = IdempotentRecordingWriter()
    plan = BackfillPlan(window=window, chunk=timedelta(days=7), bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX)

    first = await run_source_backfill(source, writer, plan)
    second = await run_source_backfill(source, writer, plan)

    assert sum(result.records_written for result in first) == 2
    assert sum(result.records_written for result in second) == 0
    # The driver still walks and fetches the same deterministic chunk on the re-run.
    assert sum(result.records_seen for result in second) == 2
    assert source.fetch_calls == [window, window]


def test_merge_backfill_results_sums_seen_and_written_and_folds_the_rejected_count() -> None:
    results = [
        IngestionJobResult(source="s", status="ingested", records_seen=2, records_written=2, details={"rejected": 1}),
        IngestionJobResult(source="s", status="ingested", records_seen=1, records_written=1, details={"rejected": 0}),
    ]

    merged = merge_backfill_results("s", results)

    assert merged.status == "ingested"
    assert merged.records_seen == 3
    assert merged.records_written == 3
    assert merged.details == {"chunks": 2, "rejected": 1, "dropped": 0}


def test_merge_backfill_results_reports_the_first_failures_reason() -> None:
    results = [
        IngestionJobResult(source="s", status="ingested", records_seen=1, records_written=1),
        IngestionJobResult(source="s", status="failed", records_seen=0, records_written=0, reason="boom"),
    ]

    merged = merge_backfill_results("s", results)

    assert merged.status == "failed"
    assert merged.reason == "boom"


def test_merge_backfill_results_of_an_empty_sequence_is_a_skip() -> None:
    assert merge_backfill_results("s", []).status == "skipped"


# --- repair_identity: rebuild through identity.py, never invent a date -------------------------------


FIRMS_REPAIR_PROPERTIES = {
    "id": "N:2026-08-03:0142:45.1563:-119.1563",
    "satellite": "N",
    "acqDate": "2026-08-03",
    "acqTime": "0142",
    "geometry": {"type": "Point", "coordinates": [-119.15625, 45.15625]},
}
USGS_NWIS_REPAIR_PROPERTIES = {
    "id": "14105700:2026-08-03T09:15:00.000-07:00",
    "siteNo": "14105700",
    "updatedAt": "2026-08-03T09:15:00.000-07:00",
}
OPEN_METEO_REPAIR_PROPERTIES = {
    "id": "0.1563:-0.1563:2024-08-02T00:00:00.000Z",
    "observedAt": "2024-08-02T00:00:00.000Z",
    "geometry": {"type": "Point", "coordinates": [-0.15625, 0.15625]},
}
WFIGS_REPAIR_PROPERTIES = {
    "id": "2026-ORTNF-000123",
    "uniqueFireIdentifier": "2026-ORTNF-000123",
    "polygonDateTime": "2026-08-02T18:05:00.000Z",
}


@pytest.mark.parametrize(
    ("producer", "properties", "expected_observed_at"),
    [
        (FIRMS_PRODUCER, FIRMS_REPAIR_PROPERTIES, datetime(2026, 8, 3, 1, 42, tzinfo=UTC)),
        (USGS_NWIS_PRODUCER, USGS_NWIS_REPAIR_PROPERTIES, datetime(2026, 8, 3, 16, 15, tzinfo=UTC)),
        (OPEN_METEO_PRODUCER, OPEN_METEO_REPAIR_PROPERTIES, datetime(2024, 8, 2, tzinfo=UTC)),
        (WFIGS_PRODUCER, WFIGS_REPAIR_PROPERTIES, datetime(2026, 8, 2, 18, 5, tzinfo=UTC)),
    ],
)
def test_repair_identity_rebuilds_each_producer_through_identity_py_and_dates_from_its_own_fields(
    producer: str, properties: dict[str, object], expected_observed_at: datetime
) -> None:
    identity = repair_identity(producer, properties)

    assert identity is not None
    assert identity.producer_local_id == properties["id"]
    assert identity.observed_at == expected_observed_at


def test_repair_identity_falls_back_to_the_stored_id_dated_negative_infinity_on_a_key_mismatch() -> None:
    drifted = {**FIRMS_REPAIR_PROPERTIES, "id": "a-completely-different-id"}

    identity = repair_identity(FIRMS_PRODUCER, drifted)

    assert identity is not None
    assert identity.producer_local_id == "a-completely-different-id"
    assert identity.observed_at is None


def test_repair_identity_falls_back_to_the_stored_id_when_the_rebuild_itself_raises() -> None:
    broken = {"id": "kept-verbatim"}  # no satellite/acqDate/acqTime -> MissingNativeKeyError inside the rebuild

    identity = repair_identity(FIRMS_PRODUCER, broken)

    assert identity is not None
    assert identity.producer_local_id == "kept-verbatim"
    assert identity.observed_at is None


def test_repair_identity_never_uses_now_it_only_ever_dates_from_the_rebuilt_field_or_negative_infinity() -> None:
    undated_perimeter = {
        "id": "2026-CAANF-000456",
        "uniqueFireIdentifier": "2026-CAANF-000456",
        "polygonDateTime": None,
    }

    identity = repair_identity(WFIGS_PRODUCER, undated_perimeter)

    assert identity is not None
    assert identity.producer_local_id == "2026-CAANF-000456"
    assert identity.observed_at is None


def test_repair_identity_returns_none_for_a_row_with_no_usable_stored_id() -> None:
    assert repair_identity(FIRMS_PRODUCER, {"satellite": "N"}) is None
    assert repair_identity(FIRMS_PRODUCER, {"id": "   "}) is None
    assert repair_identity(FIRMS_PRODUCER, {"id": 12345}) is None


def test_select_unversioned_features_never_reads_created_at_or_orders_by_it() -> None:
    """The repair cursor is a keyset on feature.id; the query must not lean on a write-clock column."""
    statement_text = " ".join(str(backfill_module._SELECT_UNVERSIONED_FEATURES).split())

    assert "created_at" not in statement_text
    assert "geometry_id IS NULL" in statement_text
    assert "geom IS NOT NULL" in statement_text


# --- run_geometry_repair: link an orphan, then be a no-op, never reading created_at or now() ---------


@dataclass
class _StoredVersion:
    """One row of the fake geo.geometry table; mirrors test_ingest_geometry.py's fake at a smaller scope."""

    geometry_id: str
    natural_key: str
    version_valid_from: str
    geom: str
    grid_name: str | None = None
    cell_key: str | None = None
    resolution_metres: int | None = None
    version_valid_to: str | None = None
    superseded_by: str | None = None
    last_confirmed_at: str | None = None


@dataclass
class _UnversionedFeatureRow:
    """One geo.features row the fake will surface through the paged unversioned-feature select."""

    feature_id: str
    layer_name: str
    properties: dict[str, object]
    # The repair needs the layer id too, because the feature-side advisory lock is keyed
    # `layer_id:external_id` -- the same key writer.py takes, in the same order, so a repair
    # running beside `ingest-all` queues behind it instead of deadlocking on 40P01.
    layer_id: str = "00000000-0000-4000-8000-00000000000a"


class FakeResult:
    """The one result accessor geometry.py's statements read: `.all()`."""

    def __init__(self, rows: Sequence[Any]) -> None:
        """Hold the rows this statement is pretending to have returned."""
        self._rows = list(rows)

    def all(self) -> list[Any]:
        """Every row the statement returned."""
        return list(self._rows)


class FakeMappingsResult:
    """Answers `.mappings()`, the one accessor run_geometry_repair calls on the paged feature select."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        """Hold the mapping-shaped rows this page is pretending to have returned."""
        self._rows = list(rows)

    def mappings(self) -> list[Mapping[str, object]]:
        """Every row as a mapping, exactly what `[dict(row) for row in rows]` expects to iterate."""
        return list(self._rows)


class FakeRepairSession:
    """An AsyncSession stand-in answering both backfill's own paging SQL and geometry.py's, for repair."""

    def __init__(self) -> None:
        """Start with an empty geo.geometry model and an empty page of unversioned features."""
        self.versions: dict[str, list[_StoredVersion]] = {}
        self.feature_geoms: dict[str, str] = {}
        self.feature_geometry_id: dict[str, str] = {}
        self.unversioned_features: list[_UnversionedFeatureRow] = []
        self.executions: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0

    def seed_feature_geom(self, feature_id: str, geom: str) -> None:
        """Register what geo.features.geom would resolve to for a StoredFeatureGeometry source."""
        self.feature_geoms[feature_id] = geom

    def add_unversioned_feature(
        self,
        feature_id: str,
        layer_name: str,
        properties: Mapping[str, object],
        layer_id: str = "00000000-0000-4000-8000-00000000000a",
    ) -> None:
        """Seed one geo.features row the repair pass should find via geometry_id IS NULL."""
        self.unversioned_features.append(_UnversionedFeatureRow(feature_id, layer_name, dict(properties), layer_id))

    def open_version(self, natural_key: str) -> _StoredVersion | None:
        """The one row per place with version_valid_to IS NULL, mirroring uq_geometry_current."""
        for version in self.versions.get(natural_key, []):
            if version.version_valid_to is None:
                return version
        return None

    def _resolve_geom(self, feature_id: str, geojson: str) -> str | None:
        """Mirror the classify CTE's CASE: a feature id wins, else the request's own GeoJSON, else missing."""
        if feature_id:
            return self.feature_geoms.get(feature_id)
        if geojson:
            return geojson
        return None

    async def execute(self, statement: object, parameters: dict[str, Any] | None = None) -> Any:
        """Dispatch on the statement's own SQL text, exactly as test_ingest_geometry.py's fake does."""
        statement_text = " ".join(str(statement).split())
        arguments = parameters or {}
        self.executions.append((statement_text, dict(arguments)))

        if "AS properties_json" in statement_text:
            return FakeMappingsResult(self._select_unversioned(arguments))
        if "pg_advisory_xact_lock" in statement_text:
            return FakeResult([])
        if "AS geometry_unchanged" in statement_text:
            return FakeResult(self._classify(arguments))
        if "INSERT INTO geo.geometry" in statement_text:
            return FakeResult(self._insert(arguments))
        if "UPDATE geo.geometry AS closing" in statement_text:
            return FakeResult(self._close(arguments))
        if "UPDATE geo.geometry" in statement_text:
            self._confirm(arguments)
            return FakeResult([])
        if "SELECT natural_key, geometry_id FROM geo.geometry" in statement_text:
            return FakeResult(self._select_current(arguments))
        if "UPDATE geo.features AS feature" in statement_text:
            return FakeResult(self._link(arguments))
        raise AssertionError(f"unexpected statement: {statement_text}")

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1

    def _select_unversioned(self, arguments: Mapping[str, Any]) -> list[dict[str, object]]:
        """Page geo.features WHERE geometry_id IS NULL on an id > cursor keyset, ordered by feature id."""
        cursor = arguments["cursor"]
        batch_size = arguments["batch_size"]
        candidates = sorted(
            (
                row
                for row in self.unversioned_features
                if row.feature_id > cursor and row.feature_id not in self.feature_geometry_id
            ),
            key=lambda row: row.feature_id,
        )
        page = candidates[:batch_size]
        return [
            {
                "feature_id": row.feature_id,
                "layer_id": row.layer_id,
                "layer_name": row.layer_name,
                "properties_json": json.dumps(row.properties),
            }
            for row in page
        ]

    def _classify(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Answer the classify CTE: missing / unchanged / datable-successor, per requested place."""
        rows = []
        for natural_key, feature_id, geojson, observed_at in zip(
            arguments["natural_keys"],
            arguments["feature_ids"],
            arguments["geojsons"],
            arguments["observed_ats"],
            strict=True,
        ):
            resolved_geom = self._resolve_geom(feature_id, geojson)
            open_version = self.open_version(natural_key)
            rows.append(
                SimpleNamespace(
                    natural_key=natural_key,
                    geometry_missing=resolved_geom is None,
                    open_geometry_id=None if open_version is None else open_version.geometry_id,
                    geometry_unchanged=(
                        open_version is not None and resolved_geom is not None and open_version.geom == resolved_geom
                    ),
                    successor_is_datable=(
                        open_version is not None and _is_after(observed_at, open_version.version_valid_from)
                    ),
                )
            )
        return rows

    def _insert(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Insert every planned open version, honouring ON CONFLICT (natural_key) WHERE version_valid_to IS NULL."""
        rows = []
        for (
            geometry_id,
            natural_key,
            _producer,
            version_valid_from,
            feature_id,
            geojson,
            grid_name,
            cell_key,
            resolution_metres,
        ) in zip(
            arguments["geometry_ids"],
            arguments["natural_keys"],
            arguments["producers"],
            arguments["version_valid_froms"],
            arguments["feature_ids"],
            arguments["geojsons"],
            arguments["grid_names"],
            arguments["cell_keys"],
            arguments["resolution_metres"],
            strict=True,
        ):
            if self.open_version(natural_key) is not None:
                continue
            self.versions.setdefault(natural_key, []).append(
                _StoredVersion(
                    geometry_id=geometry_id,
                    natural_key=natural_key,
                    version_valid_from=version_valid_from,
                    geom=self._resolve_geom(feature_id, geojson) or "",
                    grid_name=grid_name or None,
                    cell_key=cell_key or None,
                    resolution_metres=int(resolution_metres) if resolution_metres else None,
                    last_confirmed_at=arguments.get("run_clock"),
                )
            )
            rows.append(SimpleNamespace(geometry_id=geometry_id, natural_key=natural_key))
        return rows

    def _close(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Close every open version named in the supersession batch, in the same call as its successor id."""
        rows = []
        for natural_key, closed_at, successor_id in zip(
            arguments["natural_keys"], arguments["closed_ats"], arguments["successor_ids"], strict=True
        ):
            version = self.open_version(natural_key)
            if version is None:
                continue
            version.version_valid_to = closed_at
            version.superseded_by = successor_id
            rows.append(SimpleNamespace(geometry_id=version.geometry_id))
        return rows

    def _confirm(self, arguments: Mapping[str, Any]) -> None:
        """Touch last_confirmed_at on every open version named in the confirm batch, nothing else."""
        run_clock = arguments["run_clock"]
        for natural_key in arguments["natural_keys"]:
            version = self.open_version(natural_key)
            if version is not None:
                version.last_confirmed_at = run_clock

    def _select_current(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Re-read the open version per place, exactly as the adapter does before naming an outcome."""
        rows = []
        for natural_key in arguments["natural_keys"]:
            version = self.open_version(natural_key)
            if version is not None:
                rows.append(SimpleNamespace(natural_key=natural_key, geometry_id=version.geometry_id))
        return rows

    def _link(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Repoint every feature named in the link batch, skipping one already pointing at its target."""
        rows = []
        for feature_id, geometry_id in zip(arguments["feature_ids"], arguments["geometry_ids"], strict=True):
            if self.feature_geometry_id.get(feature_id) != geometry_id:
                self.feature_geometry_id[feature_id] = geometry_id
                rows.append(SimpleNamespace(id=feature_id))
        return rows


def _is_after(candidate: str, baseline: str) -> bool:
    """Mirror timestamptz ordering against -infinity without a real database."""
    if candidate == NEGATIVE_INFINITY_TIMESTAMP:
        return False
    if baseline == NEGATIVE_INFINITY_TIMESTAMP:
        return True
    return datetime.fromisoformat(candidate) > datetime.fromisoformat(baseline)


def _wfigs_properties(fire_id: str, polygon_date_time: str | None = "2026-08-01T00:00:00.000Z") -> dict[str, object]:
    """A minimal WFIGS payload whose repaired identity round-trips through repair_identity untouched."""
    return {"id": fire_id, "uniqueFireIdentifier": fire_id, "polygonDateTime": polygon_date_time}


async def test_run_geometry_repair_versions_and_links_an_orphan_feature() -> None:
    session = FakeRepairSession()
    session.seed_feature_geom("feature-0001", "POLYGON-A")
    session.add_unversioned_feature("feature-0001", "fire-perimeters", _wfigs_properties("2026-OR-000001"))

    result = await run_geometry_repair(session, run_clock=datetime(2026, 8, 4, tzinfo=UTC))

    assert result.records_seen == 1
    assert result.records_written == 1
    assert session.feature_geometry_id["feature-0001"] is not None
    version = session.open_version("wfigs:2026-OR-000001")
    assert version is not None
    assert version.version_valid_from == datetime(2026, 8, 1, tzinfo=UTC).isoformat()


async def test_run_geometry_repair_is_idempotent_on_a_second_run() -> None:
    session = FakeRepairSession()
    session.seed_feature_geom("feature-0001", "POLYGON-A")
    session.add_unversioned_feature("feature-0001", "fire-perimeters", _wfigs_properties("2026-OR-000002"))

    first = await run_geometry_repair(session, run_clock=datetime(2026, 8, 4, tzinfo=UTC))
    second = await run_geometry_repair(session, run_clock=datetime(2026, 8, 5, tzinfo=UTC))

    assert first.records_written == 1
    assert second.records_seen == 0
    assert second.records_written == 0
    assert len(session.versions["wfigs:2026-OR-000002"]) == 1


async def test_run_geometry_repair_never_dates_a_version_from_created_at_or_now() -> None:
    """The version opens at polygonDateTime (2026-08-01), never at the run_clock passed in (2026-08-04)."""
    session = FakeRepairSession()
    session.seed_feature_geom("feature-0001", "POLYGON-A")
    session.add_unversioned_feature("feature-0001", "fire-perimeters", _wfigs_properties("2026-OR-000003"))
    run_clock = datetime(2026, 8, 4, tzinfo=UTC)

    await run_geometry_repair(session, run_clock=run_clock)

    version = session.open_version("wfigs:2026-OR-000003")
    assert version is not None
    assert version.version_valid_from != run_clock.isoformat()
    assert version.version_valid_from == datetime(2026, 8, 1, tzinfo=UTC).isoformat()


async def test_run_geometry_repair_counts_a_feature_whose_layer_has_no_producer_as_unrepairable() -> None:
    session = FakeRepairSession()
    session.add_unversioned_feature("feature-0001", "unmapped-layer", {"id": "whatever"})

    result = await run_geometry_repair(session, run_clock=datetime(2026, 8, 4, tzinfo=UTC))

    assert result.records_seen == 1
    assert result.records_written == 0
    assert result.details["unrepairable"] == 1


async def test_run_geometry_repair_stops_at_max_features_across_several_pages() -> None:
    session = FakeRepairSession()
    for index in range(3):
        feature_id = f"feature-{index:04d}"
        session.seed_feature_geom(feature_id, f"POLYGON-{index}")
        session.add_unversioned_feature(feature_id, "fire-perimeters", _wfigs_properties(f"2026-OR-{index:06d}"))

    result = await run_geometry_repair(
        session, run_clock=datetime(2026, 8, 4, tzinfo=UTC), batch_size=1, max_features=2
    )

    assert result.records_seen == 2
    assert result.records_written == 2


# --- source.py contract: freshness, typed history refusal, and the feature/grid-cell shape guard -----


def test_freshness_rule_accepts_an_undated_record_only_with_explicit_consent() -> None:
    assert FreshnessRule().accepts(None) is False
    assert FreshnessRule(accepts_undated_records=True).accepts(None) is True


def test_freshness_rule_with_no_max_age_accepts_any_dated_record() -> None:
    assert FreshnessRule().accepts(datetime(2000, 1, 1, tzinfo=UTC)) is True


def test_freshness_rule_rejects_an_observation_older_than_its_max_age() -> None:
    rule = FreshnessRule(max_observation_age=timedelta(hours=1))
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)

    assert rule.accepts(now - timedelta(minutes=30), now) is True
    assert rule.accepts(now - timedelta(hours=2), now) is False


def test_history_capability_requires_a_reason_when_unsupported() -> None:
    with pytest.raises(SourceContractError):
        HistoryCapability(supported=False)


def test_history_capability_refuses_a_window_before_its_earliest_supported_instant() -> None:
    capability = HistoryCapability(supported=True, earliest=datetime(2025, 1, 1, tzinfo=UTC))
    window = HistoryWindow(start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2024, 2, 1, tzinfo=UTC))

    with pytest.raises(HistoryUnavailableError):
        capability.require("fake-source", window)


def test_history_window_rejects_a_naive_or_inverted_range() -> None:
    with pytest.raises(ValueError, match="timezone"):
        HistoryWindow(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2, tzinfo=UTC))  # noqa: DTZ001
    with pytest.raises(ValueError, match="precede"):
        HistoryWindow(start=datetime(2026, 1, 2, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC))


def test_grid_cell_of_refuses_a_shape_mismatch_in_either_direction() -> None:
    grid_cell = GridCell(grid_name="g", cell_key="c", resolution_metres=250, geojson="{}")
    celled_write = FeatureWrite(
        layer_reference="ndvi",
        identity=FeatureIdentity(producer="ndvi", producer_local_id="x", observed_at=None),
        properties={},
        channel="layer:ndvi",
        grid_cell=grid_cell,
    )
    uncelled_write = FeatureWrite(
        layer_reference="fire-detections",
        identity=FeatureIdentity(producer="firms", producer_local_id="x", observed_at=None),
        properties={},
        channel="layer:fire-detections",
    )
    grid_source = FakeSource(shape="grid_cell")
    feature_source = FakeSource(shape="feature")

    assert grid_cell_of(grid_source, celled_write) is grid_cell
    assert grid_cell_of(feature_source, uncelled_write) is None
    with pytest.raises(SourceContractError):
        grid_cell_of(feature_source, celled_write)
    with pytest.raises(SourceContractError):
        grid_cell_of(grid_source, uncelled_write)


def test_accepted_writes_applies_the_sources_freshness_rule() -> None:
    source = FakeSource()
    request = FetchRequest(bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, max_records=10)

    accepted, rejected = accepted_writes(source, [{"id": "a"}, {"id": "b"}], request)

    assert [write.external_id for write in accepted] == ["a", "b"]
    assert rejected == 0


def test_accepted_writes_counts_a_record_the_source_declines_to_build() -> None:
    class RejectingSource(FakeSource):
        """A source whose builder rejects every record, as a real one does for a malformed payload."""

        def build_write(self, record: Mapping[str, object], request: FetchRequest) -> FeatureWrite | None:
            """Reject unconditionally."""
            return None

    request = FetchRequest(bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, max_records=10)

    accepted, rejected = accepted_writes(RejectingSource(), [{"id": "a"}], request)

    assert accepted == []
    assert rejected == 1


# --- run_source_job: the forward half of the one standard pipeline ----------------------------------
# `IngestionSource.fetch_current` had no driver at all: `run_source_backfill` only ever calls
# `fetch_history`, so the current-window half of the declared contract was unreachable and every
# adopting module hand-rolled fetch + accept + truncate instead.


class DatedSource(FakeSource):
    """A FakeSource whose records carry their own observation time, so truncation order is observable."""

    def build_write(self, record: Mapping[str, object], request: FetchRequest) -> FeatureWrite | None:
        """Map a canned record to a write dated by its own `observedAt`, or undated when it carries none."""
        observed_at_text = record.get("observedAt")
        observed_at = datetime.fromisoformat(str(observed_at_text)) if isinstance(observed_at_text, str) else None
        return FeatureWrite(
            layer_reference=self.layer_reference(),
            identity=FeatureIdentity(
                producer=self.producer,
                producer_local_id=str(record["id"]),
                observed_at=observed_at,
            ),
            properties=dict(record),
            channel=self.channel,
        )


def _dated_record(record_id: str, observed_at: datetime | None = None) -> dict[str, object]:
    """One canned upstream record, optionally carrying its own observation instant."""
    if observed_at is None:
        return {"id": record_id}
    return {"id": record_id, "observedAt": observed_at.isoformat()}


def _capped_request(max_records: int) -> FetchRequest:
    """A fetch request with a cap small enough to make truncation observable in a unit test."""
    return FetchRequest(bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX, max_records=max_records)


async def test_run_source_job_fetches_the_current_window_and_writes_it_through_the_forward_path() -> None:
    source = FakeSource()
    source.current_records = [{"id": "a"}, {"id": "b"}]
    writer = RecordingWriter()

    result = await run_source_job(source, writer, bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX)

    assert source.current_fetches == 1
    assert source.fetch_calls == []
    assert result.status == "ingested"
    assert result.records_seen == 2
    assert result.records_written == 2
    assert not result.truncated
    assert [write.external_id for write in writer.batches[0]] == ["a", "b"]


async def test_run_source_job_skips_when_the_bbox_is_unconfigured() -> None:
    source = FakeSource()
    source.current_records = [{"id": "a"}]
    writer = RecordingWriter()

    result = await run_source_job(source, writer)

    assert result.status == "skipped"
    assert result.reason == UNCONFIGURED_BBOX_REASON
    assert source.current_fetches == 0
    assert writer.batches == []


# --- select_writes: a bitten cap drops the OLDEST records, never an arrival slice -------------------


def test_truncation_keeps_the_newest_observations_rather_than_the_first_to_arrive() -> None:
    source = DatedSource()
    records = [
        _dated_record("oldest", datetime(2026, 8, 1, tzinfo=UTC)),
        _dated_record("middle", datetime(2026, 8, 2, tzinfo=UTC)),
        _dated_record("newest", datetime(2026, 8, 3, tzinfo=UTC)),
    ]

    selection = select_writes(source, records, _capped_request(2))

    assert [write.external_id for write in selection.writes] == ["middle", "newest"]
    assert selection.truncated


def test_truncation_survivors_keep_their_arrival_order() -> None:
    source = DatedSource()
    records = [
        _dated_record("newest", datetime(2026, 8, 3, tzinfo=UTC)),
        _dated_record("oldest", datetime(2026, 8, 1, tzinfo=UTC)),
        _dated_record("middle", datetime(2026, 8, 2, tzinfo=UTC)),
    ]

    selection = select_writes(source, records, _capped_request(2))

    assert [write.external_id for write in selection.writes] == ["newest", "middle"]


def test_truncation_drops_an_undated_record_before_a_dated_one() -> None:
    source = DatedSource()
    records = [_dated_record("undated"), _dated_record("dated", datetime(2026, 8, 1, tzinfo=UTC))]

    selection = select_writes(source, records, _capped_request(1))

    assert [write.external_id for write in selection.writes] == ["dated"]


def test_an_uncapped_window_is_neither_reordered_nor_reported_as_truncated() -> None:
    source = DatedSource()
    records = [
        _dated_record("newest", datetime(2026, 8, 3, tzinfo=UTC)),
        _dated_record("oldest", datetime(2026, 8, 1, tzinfo=UTC)),
    ]

    selection = select_writes(source, records, _capped_request(10))

    assert [write.external_id for write in selection.writes] == ["newest", "oldest"]
    assert not selection.truncated
    assert selection.rejected == 0


def test_select_writes_still_counts_what_the_sources_freshness_rule_rejected() -> None:
    source = DatedSource()
    source.freshness = FreshnessRule(accepts_undated_records=False)
    records = [_dated_record("undated"), _dated_record("dated", datetime(2026, 8, 1, tzinfo=UTC))]

    selection = select_writes(source, records, _capped_request(10))

    assert [write.external_id for write in selection.writes] == ["dated"]
    assert selection.rejected == 1
    assert not selection.truncated


def _truncating_backfill(
    monkeypatch: pytest.MonkeyPatch,
    cap: int,
    record_count: int,
    chunk_days: int,
) -> tuple[DatedSource, RecordingWriter, BackfillPlan]:
    """A one-chunk walk whose accepted records exceed the cap, oldest record first."""
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", str(cap))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    chunk = HistoryWindow(start, start + timedelta(days=chunk_days))
    oldest_first = [
        _dated_record(f"record-{index:04d}", start + timedelta(minutes=index)) for index in range(record_count)
    ]
    source = DatedSource(records_by_chunk={(chunk.start.isoformat(), chunk.end.isoformat()): oldest_first})
    plan = BackfillPlan(
        window=HistoryWindow(start=chunk.start, end=chunk.end),
        chunk=timedelta(days=chunk_days),
        bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
    )
    return source, RecordingWriter(), plan


async def test_a_backfill_chunk_that_hit_the_record_cap_fails_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cap that bit deleted the chunk's OLDEST days whole; reporting that as `ingested` is the bug.

    Measured on production 2026-08-05: the FIRMS week 2022-09-04..09-10 holds 60,779 published
    detections, and a `--chunk-days 7` walk under the default 10,000-record cap writes 10,000 of them
    and drops 50,779 -- the oldest first, because `_truncation_rank` keeps the newest. The chunk
    reported `status="ingested"` with `details.rejected: 0`, because truncation is not a rejection.
    """
    source, writer, plan = _truncating_backfill(monkeypatch, cap=1000, record_count=1200, chunk_days=7)

    results = await run_source_backfill(source, writer, plan)

    assert results[0].status == "failed"
    assert results[0].truncated
    # Nothing written: the days that fitted would otherwise look complete and nobody would re-walk.
    assert results[0].records_written == 0
    assert writer.batches == []
    assert results[0].details["dropped"] == 200
    assert results[0].details["rejected"] == 0


async def test_a_truncated_chunk_names_the_narrower_chunk_days_that_would_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, writer, plan = _truncating_backfill(monkeypatch, cap=1000, record_count=1200, chunk_days=7)

    results = await run_source_backfill(source, writer, plan)
    reason = results[0].reason or ""

    assert "1200 records against a 1000-record cap" in reason
    assert "200 were dropped" in reason
    # 7 days * 1000/1200 = 5 whole days, which is both under the cap and strictly narrower than 7.
    assert "--chunk-days 5" in reason
    assert "Nothing was written for this chunk" in reason


async def test_a_truncated_chunk_always_advises_a_strictly_narrower_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-record overshoot must not advise the same chunk size back, which would loop forever."""
    source, writer, plan = _truncating_backfill(monkeypatch, cap=1000, record_count=1001, chunk_days=2)

    results = await run_source_backfill(source, writer, plan)

    assert "--chunk-days 1" in (results[0].reason or "")


async def test_a_truncated_chunk_fails_the_fold_without_erasing_the_chunks_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", "1000")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    over_cap = HistoryWindow(start, start + timedelta(days=1))
    under_cap = HistoryWindow(start + timedelta(days=1), start + timedelta(days=2))
    source = DatedSource(
        records_by_chunk={
            (over_cap.start.isoformat(), over_cap.end.isoformat()): [
                _dated_record(f"big-{index:04d}", start + timedelta(minutes=index)) for index in range(1200)
            ],
            (under_cap.start.isoformat(), under_cap.end.isoformat()): [_dated_record("small", start)],
        }
    )
    writer = RecordingWriter()
    plan = BackfillPlan(
        window=HistoryWindow(start=over_cap.start, end=under_cap.end),
        chunk=timedelta(days=1),
        bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX,
    )

    results = await run_source_backfill(source, writer, plan)
    merged = merge_backfill_results(source.source_name, results)

    assert [result.status for result in results] == ["failed", "ingested"]
    assert [write.external_id for write in writer.batches[0]] == ["small"]
    assert merged.status == "failed"
    assert merged.details["dropped"] == 200


async def test_the_forward_window_reports_a_bitten_cap_rather_than_failing_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No narrower window exists for "now", so the forward path surfaces the loss instead of going red."""
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", "1000")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    source = DatedSource()
    source.current_records = [
        _dated_record(f"record-{index:04d}", start + timedelta(minutes=index)) for index in range(1200)
    ]
    writer = RecordingWriter()

    result = await run_source_job(source, writer, bbox=PACIFIC_NORTHWEST_COVERAGE_BBOX)

    assert result.status == "ingested"
    assert result.truncated
    assert result.records_written == 1000
    assert result.details["dropped"] == 200
    # The 200 dropped records are the 200 oldest, not the 200 that happened to arrive last.
    written_ids = [write.external_id for write in writer.batches[0]]
    assert written_ids[0] == "record-0200"
    assert written_ids[-1] == "record-1199"
