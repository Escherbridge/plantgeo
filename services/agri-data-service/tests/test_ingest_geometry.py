"""Pure-unit coverage of the Type-2 geometry dimension adapter: no database, an in-memory geo.geometry stand-in."""

# ruff: noqa: PLR2004, PLR0911

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.ingest.geometry import (
    NEGATIVE_INFINITY_TIMESTAMP,
    FeatureGeometryLink,
    GeoJsonGeometry,
    GeometryContractError,
    GeometryVersionRequest,
    GridCell,
    MissingGeometryError,
    StoredFeatureGeometry,
    feature_geometry_request,
    geometry_key_for,
    geometry_source_for,
    link_feature_geometry,
    lock_geometry_natural_keys,
    timestamptz_literal,
    upsert_geometry_version,
    upsert_geometry_versions,
)
from agri_data_service.ingest.identity import FeatureIdentity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.ingest.geometry import GeometrySource

DEFAULT_PRODUCER = "firms"


def _identity(local_id: str, observed_at: datetime | None, producer: str = DEFAULT_PRODUCER) -> FeatureIdentity:
    """A real FeatureIdentity, so requests are built against the shipped identity contract."""
    return FeatureIdentity(producer=producer, producer_local_id=local_id, observed_at=observed_at)


def _request(
    natural_key: str,
    observed_at: datetime | None,
    source: GeometrySource,
    producer: str = DEFAULT_PRODUCER,
    grid_cell: GridCell | None = None,
) -> GeometryVersionRequest:
    """One geometry version request, defaulting to the firms producer namespace."""
    return GeometryVersionRequest(
        natural_key=natural_key,
        producer=producer,
        observed_at=observed_at,
        source=source,
        grid_cell=grid_cell,
    )


def _is_after(candidate: str, baseline: str) -> bool:
    """Mirror timestamptz ordering against -infinity without a real database."""
    if candidate == NEGATIVE_INFINITY_TIMESTAMP:
        return False
    if baseline == NEGATIVE_INFINITY_TIMESTAMP:
        return True
    return datetime.fromisoformat(candidate) > datetime.fromisoformat(baseline)


@dataclass
class _StoredVersion:
    """One row of the fake geo.geometry table, holding exactly the columns the adapter reads and writes."""

    geometry_id: str
    natural_key: str
    version_valid_from: str
    geom: str
    producer: str
    grid_name: str | None = None
    cell_key: str | None = None
    resolution_metres: int | None = None
    version_valid_to: str | None = None
    superseded_by: str | None = None
    last_confirmed_at: str | None = None


class FakeGeometryStore:
    """An in-memory model of geo.geometry plus the geo.features geometry it can be resolved against."""

    def __init__(self) -> None:
        """Start with no versions, no seeded feature geometry, and no feature-to-geometry links."""
        self.versions: dict[str, list[_StoredVersion]] = {}
        self.feature_geoms: dict[str, str] = {}
        self.feature_geometry_id: dict[str, str] = {}

    def seed_feature_geom(self, feature_id: str, geom: str) -> None:
        """Register what geo.features.geom would resolve to for a StoredFeatureGeometry source."""
        self.feature_geoms[feature_id] = geom

    def open_version(self, natural_key: str) -> _StoredVersion | None:
        """The one row per place with version_valid_to IS NULL, mirroring uq_geometry_current."""
        for version in self.versions.get(natural_key, []):
            if version.version_valid_to is None:
                return version
        return None

    def resolve_geom(self, feature_id: str, geojson: str) -> str | None:
        """Mirror the classify CTE's CASE: a feature id wins, else the request's own GeoJSON, else missing."""
        if feature_id:
            return self.feature_geoms.get(feature_id)
        if geojson:
            return geojson
        return None


class FakeResult:
    """The one result accessor geometry.py reads: `.all()`."""

    def __init__(self, rows: Sequence[Any]) -> None:
        """Hold the rows this statement is pretending to have returned."""
        self._rows = list(rows)

    def all(self) -> list[Any]:
        """Every row the statement returned."""
        return list(self._rows)


class FakeGeometrySession:
    """An AsyncSession stand-in that answers geometry.py's own SQL against a FakeGeometryStore."""

    def __init__(self, store: FakeGeometryStore | None = None) -> None:
        """Hold the store this session answers against, and record every statement it was asked to run."""
        self.store = store if store is not None else FakeGeometryStore()
        self.executions: list[tuple[str, dict[str, Any]]] = []
        self.locked_keys: list[list[str]] = []

    async def execute(self, statement: object, parameters: dict[str, Any] | None = None) -> FakeResult:
        """Dispatch on the statement's own SQL text, exactly as the writer.py FakeSession does."""
        statement_text = " ".join(str(statement).split())
        arguments = parameters or {}
        self.executions.append((statement_text, dict(arguments)))

        if "pg_advisory_xact_lock" in statement_text:
            self.locked_keys.append(list(arguments["natural_keys"]))
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
            resolved_geom = self.store.resolve_geom(feature_id, geojson)
            open_version = self.store.open_version(natural_key)
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
            producer,
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
            if self.store.open_version(natural_key) is not None:
                continue
            self.store.versions.setdefault(natural_key, []).append(
                _StoredVersion(
                    geometry_id=geometry_id,
                    natural_key=natural_key,
                    version_valid_from=version_valid_from,
                    geom=self.store.resolve_geom(feature_id, geojson) or "",
                    producer=producer,
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
            version = self.store.open_version(natural_key)
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
            version = self.store.open_version(natural_key)
            if version is not None:
                version.last_confirmed_at = run_clock

    def _select_current(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Re-read the open version per place, exactly as the adapter does before naming an outcome."""
        rows = []
        for natural_key in arguments["natural_keys"]:
            version = self.store.open_version(natural_key)
            if version is not None:
                rows.append(SimpleNamespace(natural_key=natural_key, geometry_id=version.geometry_id))
        return rows

    def _link(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Repoint every feature named in the link batch, skipping one already pointing at its target."""
        rows = []
        for feature_id, geometry_id in zip(arguments["feature_ids"], arguments["geometry_ids"], strict=True):
            if self.store.feature_geometry_id.get(feature_id) != geometry_id:
                self.store.feature_geometry_id[feature_id] = geometry_id
                rows.append(SimpleNamespace(id=feature_id))
        return rows


# --- 1: first version ------------------------------------------------------------------------------


async def test_first_version_opens_dated_from_observed_at_with_no_valid_to() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(1 1)")
    session = FakeGeometrySession(store)
    observed_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    outcomes = await upsert_geometry_versions(
        session,
        [_request("firms:alpha", observed_at, StoredFeatureGeometry(feature_id="feature-1"))],
        run_clock=datetime(2026, 8, 4, tzinfo=UTC),
    )

    outcome = outcomes["firms:alpha"]
    assert outcome.action == "opened"
    version = store.open_version("firms:alpha")
    assert version is not None
    assert version.version_valid_from == observed_at.isoformat()
    assert version.version_valid_to is None
    assert version.geometry_id == outcome.geometry_id


async def test_first_version_with_no_observed_at_opens_at_negative_infinity() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(1 1)")
    session = FakeGeometrySession(store)

    outcomes = await upsert_geometry_versions(
        session,
        [_request("wfigs:undated", None, StoredFeatureGeometry(feature_id="feature-1"), producer="wfigs")],
        run_clock=datetime(2026, 8, 4, tzinfo=UTC),
    )

    version = store.open_version("wfigs:undated")
    assert version is not None
    assert version.version_valid_from == NEGATIVE_INFINITY_TIMESTAMP
    assert outcomes["wfigs:undated"].action == "opened"


async def test_upsert_geometry_version_singular_returns_the_one_outcome() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(1 1)")
    session = FakeGeometrySession(store)
    request = _request("firms:solo", datetime(2026, 8, 1, tzinfo=UTC), StoredFeatureGeometry(feature_id="feature-1"))

    outcome = await upsert_geometry_version(session, request, run_clock=datetime(2026, 8, 1, tzinfo=UTC))

    assert outcome.natural_key == "firms:solo"
    assert outcome.action == "opened"


# --- 2: unchanged geometry mints no history --------------------------------------------------------


async def test_unchanged_geometry_mints_no_new_version_and_only_touches_last_confirmed_at() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(1 1)")
    session = FakeGeometrySession(store)
    first_seen = datetime(2026, 8, 1, tzinfo=UTC)
    await upsert_geometry_versions(
        session,
        [_request("firms:alpha", first_seen, StoredFeatureGeometry(feature_id="feature-1"))],
        run_clock=datetime(2026, 8, 1, 1, tzinfo=UTC),
    )
    opened_geometry_id = store.open_version("firms:alpha").geometry_id  # type: ignore[union-attr]

    run_clock = datetime(2026, 8, 2, 1, tzinfo=UTC)
    outcomes = await upsert_geometry_versions(
        session,
        [_request("firms:alpha", datetime(2026, 8, 2, tzinfo=UTC), StoredFeatureGeometry(feature_id="feature-1"))],
        run_clock=run_clock,
    )

    versions = store.versions["firms:alpha"]
    assert len(versions) == 1
    assert versions[0].geometry_id == opened_geometry_id
    assert versions[0].version_valid_from == first_seen.isoformat()
    # A scalar timestamptz bind must reach asyncpg as a datetime; a string raises DataError before
    # the SQL CAST runs. version_valid_from stays text because '-infinity' has no datetime spelling.
    assert versions[0].last_confirmed_at == run_clock
    assert outcomes["firms:alpha"].action == "confirmed"
    assert outcomes["firms:alpha"].geometry_id == opened_geometry_id


async def test_a_grid_cell_reappearing_in_a_later_batch_confirms_rather_than_reopens() -> None:
    """Two batches, same cell: the second sees the identical polygon and only confirms it."""
    store = FakeGeometryStore()
    session = FakeGeometrySession(store)
    cell = GridCell(grid_name="ndvi-250m", cell_key="R003C004", resolution_metres=250, geojson='{"type":"Polygon"}')
    identity = _identity("sample-1", datetime(2026, 8, 1, tzinfo=UTC), producer="ndvi")
    first_request = feature_geometry_request(identity, "feature-1", grid_cell=cell)
    await upsert_geometry_versions(session, [first_request], run_clock=datetime(2026, 8, 1, tzinfo=UTC))

    second_identity = _identity("sample-2", datetime(2026, 8, 2, tzinfo=UTC), producer="ndvi")
    second_request = feature_geometry_request(second_identity, "feature-2", grid_cell=cell)
    outcomes = await upsert_geometry_versions(session, [second_request], run_clock=datetime(2026, 8, 2, tzinfo=UTC))

    assert len(store.versions[first_request.natural_key]) == 1
    assert outcomes[first_request.natural_key].action == "confirmed"


# --- 3: changed geometry closes exactly one version and opens exactly one successor -----------------


async def test_changed_geometry_closes_the_old_version_and_opens_exactly_one_successor() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(1 1)")
    session = FakeGeometrySession(store)
    first_seen = datetime(2026, 8, 1, tzinfo=UTC)
    await upsert_geometry_versions(
        session,
        [_request("firms:alpha", first_seen, StoredFeatureGeometry(feature_id="feature-1"))],
        run_clock=first_seen,
    )
    original = store.open_version("firms:alpha")
    assert original is not None

    store.seed_feature_geom("feature-1", "POINT(2 2)")  # the shape moved
    moved_at = datetime(2026, 8, 2, tzinfo=UTC)
    outcomes = await upsert_geometry_versions(
        session,
        [_request("firms:alpha", moved_at, StoredFeatureGeometry(feature_id="feature-1"))],
        run_clock=moved_at,
    )

    chain = store.versions["firms:alpha"]
    assert len(chain) == 2
    closed, opened = chain
    assert closed.geometry_id == original.geometry_id
    assert closed.version_valid_to == moved_at.isoformat()
    assert closed.superseded_by == opened.geometry_id
    # No gap or overlap: the successor opens at exactly the instant the predecessor closed.
    assert opened.version_valid_from == closed.version_valid_to
    assert opened.version_valid_to is None
    assert outcomes["firms:alpha"].action == "superseded"
    assert outcomes["firms:alpha"].geometry_id == opened.geometry_id


async def test_changed_geometry_with_no_observed_at_is_undatable_and_leaves_the_chain_untouched() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(1 1)")
    session = FakeGeometrySession(store)
    first_seen = datetime(2026, 8, 1, tzinfo=UTC)
    await upsert_geometry_versions(
        session,
        [_request("wfigs:alpha", first_seen, StoredFeatureGeometry(feature_id="feature-1"), producer="wfigs")],
        run_clock=first_seen,
    )
    original = store.open_version("wfigs:alpha")
    assert original is not None

    store.seed_feature_geom("feature-1", "POINT(2 2)")
    outcomes = await upsert_geometry_versions(
        session,
        [_request("wfigs:alpha", None, StoredFeatureGeometry(feature_id="feature-1"), producer="wfigs")],
        run_clock=datetime(2026, 8, 2, tzinfo=UTC),
    )

    chain = store.versions["wfigs:alpha"]
    assert len(chain) == 1
    assert chain[0].geometry_id == original.geometry_id
    assert chain[0].version_valid_to is None
    # last_confirmed_at stays at whatever the opening insert set it to (first_seen): an undatable move
    # must not be reported as "seen unchanged" by advancing it to this run's clock.
    assert chain[0].last_confirmed_at == first_seen
    assert outcomes["wfigs:alpha"].action == "undatable"
    assert outcomes["wfigs:alpha"].geometry_id == original.geometry_id


# --- 4: -infinity, never datetime.min or now() -------------------------------------------------------


def test_timestamptz_literal_spells_none_as_negative_infinity_never_datetime_min_or_a_fixed_epoch() -> None:
    assert timestamptz_literal(None) == NEGATIVE_INFINITY_TIMESTAMP
    assert timestamptz_literal(None) != datetime.min.replace(tzinfo=UTC).isoformat()
    assert timestamptz_literal(None) != datetime(1, 1, 1, tzinfo=UTC).isoformat()

    moment = datetime(2026, 8, 4, tzinfo=UTC)
    assert timestamptz_literal(moment) == moment.isoformat()


# --- 5: namespace separation -------------------------------------------------------------------------


async def test_two_producers_with_the_same_local_id_never_share_a_version_chain() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-firms", "POINT(1 1)")
    store.seed_feature_geom("feature-wfigs", "POINT(1 1)")
    session = FakeGeometrySession(store)
    observed_at = datetime(2026, 8, 1, tzinfo=UTC)

    firms_request = feature_geometry_request(_identity("shared-id", observed_at, producer="firms"), "feature-firms")
    wfigs_request = feature_geometry_request(_identity("shared-id", observed_at, producer="wfigs"), "feature-wfigs")
    assert firms_request.natural_key != wfigs_request.natural_key

    outcomes = await upsert_geometry_versions(session, [firms_request, wfigs_request], run_clock=observed_at)

    assert set(store.versions) == {"firms:shared-id", "wfigs:shared-id"}
    assert outcomes["firms:shared-id"].geometry_id != outcomes["wfigs:shared-id"].geometry_id


def test_geometry_version_request_rejects_a_natural_key_not_namespaced_by_its_producer() -> None:
    with pytest.raises(GeometryContractError):
        GeometryVersionRequest(
            natural_key="other:alpha",
            producer="firms",
            observed_at=None,
            source=GeoJsonGeometry(geojson='{"type":"Point","coordinates":[0,0]}'),
        )


def test_geometry_version_request_rejects_a_naive_observed_at() -> None:
    with pytest.raises(GeometryContractError, match="timezone"):
        GeometryVersionRequest(
            natural_key="firms:alpha",
            producer="firms",
            observed_at=datetime(2026, 8, 1),  # noqa: DTZ001
            source=GeoJsonGeometry(geojson="{}"),
        )


# --- 6: grid-cell shape ------------------------------------------------------------------------------


async def test_grid_cell_shaped_source_versions_by_grid_name_and_cell_key() -> None:
    store = FakeGeometryStore()
    session = FakeGeometrySession(store)
    cell = GridCell(
        grid_name="ndvi-250m",
        cell_key="R003C004",
        resolution_metres=250,
        geojson='{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}',
    )
    identity = _identity("ignored-for-grid", datetime(2026, 8, 1, tzinfo=UTC), producer="ndvi")

    natural_key = geometry_key_for(identity, cell)
    assert natural_key == "ndvi:ndvi-250m:R003C004"

    request = feature_geometry_request(identity, feature_id="", grid_cell=cell)
    assert isinstance(request.source, GeoJsonGeometry)

    outcomes = await upsert_geometry_versions(session, [request], run_clock=datetime(2026, 8, 1, tzinfo=UTC))

    version = store.open_version(natural_key)
    assert version is not None
    assert version.grid_name == "ndvi-250m"
    assert version.cell_key == "R003C004"
    assert version.resolution_metres == 250
    assert outcomes[natural_key].action == "opened"


async def test_many_samples_landing_on_one_grid_cell_share_a_single_version_without_colliding_with_features() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("uncelled-feature", "POINT(9 9)")
    session = FakeGeometrySession(store)
    cell = GridCell(grid_name="soil-250m", cell_key="R010C020", resolution_metres=250, geojson='{"type":"Polygon"}')
    first = feature_geometry_request(
        _identity("sample-1", datetime(2026, 8, 1, tzinfo=UTC), producer="soil"), "feature-1", grid_cell=cell
    )
    second = feature_geometry_request(
        _identity("sample-2", datetime(2026, 8, 1, 1, tzinfo=UTC), producer="soil"), "feature-2", grid_cell=cell
    )
    uncelled = _request(
        "firms:uncelled", datetime(2026, 8, 1, tzinfo=UTC), StoredFeatureGeometry(feature_id="uncelled-feature")
    )
    assert first.natural_key == second.natural_key == "soil:soil-250m:R010C020"

    outcomes = await upsert_geometry_versions(
        session, [first, second, uncelled], run_clock=datetime(2026, 8, 1, tzinfo=UTC)
    )

    assert len(store.versions[first.natural_key]) == 1
    assert len(outcomes) == 2
    assert set(outcomes) == {first.natural_key, "firms:uncelled"}


@pytest.mark.parametrize(
    ("grid_name", "cell_key", "resolution_metres", "geojson"),
    [
        ("", "R1", 250, "{}"),
        ("g" * 101, "R1", 250, "{}"),
        ("grid", "", 250, "{}"),
        ("grid", "c" * 181, 250, "{}"),
        ("grid", "R1", 0, "{}"),
        ("grid", "R1", -1, "{}"),
        ("grid", "R1", 250, "   "),
    ],
)
def test_grid_cell_rejects_fields_outside_ck_geometry_cell_fields(
    grid_name: str, cell_key: str, resolution_metres: int, geojson: str
) -> None:
    with pytest.raises(GeometryContractError):
        GridCell(grid_name=grid_name, cell_key=cell_key, resolution_metres=resolution_metres, geojson=geojson)


def test_geometry_source_for_uses_the_stored_feature_geometry_when_uncelled() -> None:
    source = geometry_source_for("feature-1")
    assert isinstance(source, StoredFeatureGeometry)
    assert source.feature_id == "feature-1"


def test_geometry_source_for_uses_the_cell_polygon_when_celled() -> None:
    cell = GridCell(grid_name="g", cell_key="c", resolution_metres=250, geojson='{"type":"Point","coordinates":[0,0]}')
    source = geometry_source_for("feature-1", cell)
    assert isinstance(source, GeoJsonGeometry)
    assert source.geojson == cell.geojson


# --- Missing geometry, empty batches, and lock/link helpers on their own ----------------------------


async def test_missing_geometry_raises_rather_than_versioning_a_shape_we_lack() -> None:
    session = FakeGeometrySession(FakeGeometryStore())  # no feature geoms seeded
    request = _request(
        "firms:alpha", datetime(2026, 8, 1, tzinfo=UTC), StoredFeatureGeometry(feature_id="missing-feature")
    )

    with pytest.raises(MissingGeometryError):
        await upsert_geometry_versions(session, [request], run_clock=datetime(2026, 8, 1, tzinfo=UTC))


async def test_empty_batch_upserts_nothing_and_touches_no_statement() -> None:
    session = FakeGeometrySession(FakeGeometryStore())

    outcomes = await upsert_geometry_versions(session, [], run_clock=datetime(2026, 8, 1, tzinfo=UTC))

    assert outcomes == {}
    assert session.executions == []


async def test_lock_geometry_natural_keys_locks_sorted_unique_keys() -> None:
    session = FakeGeometrySession(FakeGeometryStore())

    await lock_geometry_natural_keys(session, ["firms:c", "firms:a", "firms:a", "firms:b"])

    assert session.locked_keys == [["firms:a", "firms:b", "firms:c"]]


async def test_lock_geometry_natural_keys_is_a_no_op_for_an_empty_sequence() -> None:
    session = FakeGeometrySession(FakeGeometryStore())

    await lock_geometry_natural_keys(session, [])

    assert session.executions == []


async def test_link_feature_geometry_skips_a_feature_already_pointing_at_the_target() -> None:
    store = FakeGeometryStore()
    store.feature_geometry_id["feature-1"] = "geometry-a"
    session = FakeGeometrySession(store)

    repointed = await link_feature_geometry(
        session,
        [
            FeatureGeometryLink(feature_id="feature-1", geometry_id="geometry-a"),
            FeatureGeometryLink(feature_id="feature-2", geometry_id="geometry-b"),
        ],
    )

    assert repointed == 1
    assert store.feature_geometry_id == {"feature-1": "geometry-a", "feature-2": "geometry-b"}


async def test_link_feature_geometry_is_a_no_op_for_an_empty_sequence() -> None:
    session = FakeGeometrySession(FakeGeometryStore())

    assert await link_feature_geometry(session, []) == 0
    assert session.executions == []


def test_feature_geometry_request_carries_the_identitys_own_observed_at_and_producer() -> None:
    identity = _identity("alpha", datetime(2026, 8, 1, tzinfo=UTC), producer="firms")

    request = feature_geometry_request(identity, "feature-1")

    assert request.natural_key == "firms:alpha"
    assert request.producer == "firms"
    assert request.observed_at == identity.observed_at
    assert isinstance(request.source, StoredFeatureGeometry)


# --- The dimension is keyed by the PLACE, never by the observation ----------------------------------
# `FeatureIdentity.entity_key` exists for exactly this and its docstring says so: "the enduring place
# this observation was taken at, not the observation". Keying the dimension by `natural_key` instead
# makes every chain v1-only for the two producers that embed a reading time in their id, so
# `_CONFIRM_GEOMETRY_VERSIONS` and `_CLOSE_GEOMETRY_VERSIONS` become unreachable for them and
# `uq_geometry_current` answers "the current geometry of this gauge" with one row per reading.


def _gauge_identity(site_number: str, updated_at: str) -> FeatureIdentity:
    """A USGS NWIS-shaped identity: the reading keys the feature, the site keys the place."""
    return FeatureIdentity(
        producer="usgs-nwis",
        producer_local_id=f"{site_number}:{updated_at}",
        observed_at=datetime.fromisoformat(updated_at),
        entity_local_id=site_number,
    )


def test_geometry_key_for_keys_an_uncelled_place_by_its_entity_not_its_observation() -> None:
    identity = _gauge_identity("13081500", "2026-08-04T09:15:00+00:00")

    assert geometry_key_for(identity) == "usgs-nwis:13081500"
    assert geometry_key_for(identity) == identity.entity_key
    assert geometry_key_for(identity) != identity.natural_key


def test_geometry_key_for_is_unchanged_where_the_observation_is_its_own_entity() -> None:
    identity = _identity("N:2026-08-02:1106:47.8380:-113.2649", datetime(2026, 8, 2, 11, 6, tzinfo=UTC))

    assert identity.observation_is_its_own_entity
    assert geometry_key_for(identity) == identity.natural_key == "firms:N:2026-08-02:1106:47.8380:-113.2649"


def test_a_grid_cell_still_outranks_the_entity_key() -> None:
    cell = GridCell(grid_name="ndvi-250m", cell_key="R003C004", resolution_metres=250, geojson='{"type":"Polygon"}')
    identity = _gauge_identity("13081500", "2026-08-04T09:15:00+00:00")

    assert geometry_key_for(identity, cell) == "usgs-nwis:ndvi-250m:R003C004"


async def test_two_readings_of_one_gauge_confirm_one_chain_instead_of_opening_two() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(-116 43)")
    store.seed_feature_geom("feature-2", "POINT(-116 43)")
    session = FakeGeometrySession(store)

    first = feature_geometry_request(_gauge_identity("13081500", "2026-08-04T09:15:00+00:00"), "feature-1")
    await upsert_geometry_versions(session, [first], run_clock=datetime(2026, 8, 4, 10, tzinfo=UTC))

    later = feature_geometry_request(_gauge_identity("13081500", "2026-08-04T10:45:00+00:00"), "feature-2")
    outcomes = await upsert_geometry_versions(session, [later], run_clock=datetime(2026, 8, 4, 11, tzinfo=UTC))

    assert first.natural_key == later.natural_key == "usgs-nwis:13081500"
    assert len(store.versions["usgs-nwis:13081500"]) == 1
    assert outcomes["usgs-nwis:13081500"].action == "confirmed"


async def test_a_gauge_that_genuinely_moves_supersedes_its_own_chain() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(-116 43)")
    store.seed_feature_geom("feature-2", "POINT(-116.5 43.5)")
    session = FakeGeometrySession(store)

    first = feature_geometry_request(_gauge_identity("13081500", "2026-08-04T09:15:00+00:00"), "feature-1")
    await upsert_geometry_versions(session, [first], run_clock=datetime(2026, 8, 4, 10, tzinfo=UTC))

    moved = feature_geometry_request(_gauge_identity("13081500", "2026-08-04T10:45:00+00:00"), "feature-2")
    outcomes = await upsert_geometry_versions(session, [moved], run_clock=datetime(2026, 8, 4, 11, tzinfo=UTC))

    assert outcomes["usgs-nwis:13081500"].action == "superseded"
    assert len(store.versions["usgs-nwis:13081500"]) == 2


async def test_one_batch_holding_many_readings_of_one_place_versions_it_from_the_latest_observation() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(-116 43)")
    store.seed_feature_geom("feature-2", "POINT(-116 43)")
    store.seed_feature_geom("feature-3", "POINT(-116 43)")
    session = FakeGeometrySession(store)

    requests = [
        feature_geometry_request(_gauge_identity("13081500", "2026-08-04T09:15:00+00:00"), "feature-1"),
        feature_geometry_request(_gauge_identity("13081500", "2026-08-04T10:45:00+00:00"), "feature-3"),
        feature_geometry_request(_gauge_identity("13081500", "2026-08-04T10:00:00+00:00"), "feature-2"),
    ]
    outcomes = await upsert_geometry_versions(session, requests, run_clock=datetime(2026, 8, 4, 11, tzinfo=UTC))

    version = store.open_version("usgs-nwis:13081500")
    assert version is not None
    assert version.version_valid_from == datetime(2026, 8, 4, 10, 45, tzinfo=UTC).isoformat()
    assert len(outcomes) == 1


async def test_an_undated_request_never_displaces_a_dated_one_when_a_batch_collapses() -> None:
    store = FakeGeometryStore()
    store.seed_feature_geom("feature-1", "POINT(-116 43)")
    store.seed_feature_geom("feature-2", "POINT(-116 43)")
    session = FakeGeometrySession(store)

    undated = _request("firms:place", None, StoredFeatureGeometry(feature_id="feature-1"))
    dated = _request("firms:place", datetime(2026, 8, 4, tzinfo=UTC), StoredFeatureGeometry(feature_id="feature-2"))

    await upsert_geometry_versions(session, [undated, dated], run_clock=datetime(2026, 8, 4, 11, tzinfo=UTC))

    version = store.open_version("firms:place")
    assert version is not None
    assert version.version_valid_from == datetime(2026, 8, 4, tzinfo=UTC).isoformat()
