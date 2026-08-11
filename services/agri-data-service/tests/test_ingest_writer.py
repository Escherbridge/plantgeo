"""Writer contract: bounded identity guards, the asymmetric refresh diff, batching, and one publish per written row."""

# ruff: noqa: PLR2004, PLR0911, PLR0912, PLR0913

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.ingest import writer as writer_module
from agri_data_service.ingest.geometry import GridCell
from agri_data_service.ingest.identity import FeatureIdentity
from agri_data_service.ingest.writer import (
    INSERT_BATCH_SIZE,
    FeatureWrite,
    IngestContractError,
    MissingIngestionLayerError,
    bind_feature_writer,
    encode_stored_properties,
    ingest_features,
    realtime_message,
    resolve_layer_id,
    validate_feature_write,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

LAYER_UUID = "3f7a1c2e-5b6d-4e8f-9a0b-1c2d3e4f5a6b"
FIRE_CHANNEL = "layer:fire-detections"


def _body_only(statement: str) -> str:
    """The statement's SQL body, on one line: full-line ``--`` comments dropped, whitespace collapsed.

    Runtime SQL now lives in documented ``.sql`` files whose headers name the very tables and clauses
    the body touches -- ``refresh_features.sql`` quotes its own ``UPDATE geo.features AS feature``, and
    several headers mention ``geo.layers``. Matching the whole text would therefore count a statement
    that merely *documents* a table as one that queries it, and would reach the ``geo.layers`` catch-all
    below for a statement whose body never touches that table.
    """
    body = "\n".join("" if line.lstrip().startswith("--") else line for line in statement.splitlines())
    return " ".join(body.split())


def build_identity(producer_local_id: str, producer: str = "firms") -> FeatureIdentity:
    """A real FeatureIdentity, so the writer is exercised against the shipped identity contract."""
    return FeatureIdentity(producer=producer, producer_local_id=producer_local_id, observed_at=None)


def build_write(
    producer_local_id: str,
    properties: Mapping[str, object] | None = None,
    layer_reference: str = "fire-detections",
    channel: str = FIRE_CHANNEL,
    producer: str = "firms",
    grid_cell: GridCell | None = None,
) -> FeatureWrite:
    """One feature write with a real identity and an inert default payload."""
    return FeatureWrite(
        layer_reference=layer_reference,
        identity=build_identity(producer_local_id, producer),
        properties={"brightness": 312.4} if properties is None else properties,
        channel=channel,
        grid_cell=grid_cell,
    )


class FakeResult:
    """The two result accessors the writer uses."""

    def __init__(self, rows: Sequence[Any]) -> None:
        """Hold the rows this statement is pretending to have returned."""
        self._rows = list(rows)

    def first(self) -> Any | None:
        """The first row, or None when the statement matched nothing."""
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        """Every row the statement returned."""
        return list(self._rows)


@dataclass
class _StoredGeometryVersion:
    """One row of the fake geo.geometry table, holding exactly the columns _maintain_batch_geometry touches."""

    geometry_id: str
    natural_key: str
    version_valid_from: str
    geom: str
    version_valid_to: str | None = None
    superseded_by: str | None = None
    last_confirmed_at: str | None = None


class FakeSession:
    """An AsyncSession stand-in that dispatches on the writer's own SQL, so no database is involved."""

    def __init__(
        self,
        layer_id: str | None = LAYER_UUID,
        existing: Sequence[str] = (),
        changed: Sequence[str] | None = None,
        fail_on: str | None = None,
    ) -> None:
        """Configure which layer resolves, which external ids already exist, and which refreshes report a change."""
        self.layer_id = layer_id
        self.existing = set(existing)
        self.changed = None if changed is None else set(changed)
        self.fail_on = fail_on
        self.executions: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0
        # geo.geometry stand-in for _maintain_batch_geometry, which now runs on every write in every batch.
        self.geometry_versions: dict[str, list[_StoredGeometryVersion]] = {}
        self.feature_geometry_id: dict[str, str] = {}

    async def execute(self, statement: object, parameters: dict[str, Any] | None = None) -> FakeResult:
        """Answer one statement the way Postgres would, recording it for assertions."""
        statement_text = _body_only(str(statement))
        arguments = parameters or {}
        self.executions.append((statement_text, dict(arguments)))
        if self.fail_on is not None and self.fail_on in statement_text:
            raise RuntimeError("database refused the statement")

        if "pg_advisory_xact_lock" in statement_text:
            return FakeResult([])
        # The geometry branches must be tested before the geo.features branches below, because the classify
        # statement joins geo.features. The refresh and the geometry link are both `UPDATE geo.features AS
        # feature ...` and are told apart only by the column each one SETs, so neither prefix may be shortened.
        if "AS geometry_unchanged" in statement_text:
            return FakeResult(self._classify_geometry(arguments))
        if "INSERT INTO geo.geometry" in statement_text:
            return FakeResult(self._insert_geometry(arguments))
        if "UPDATE geo.geometry AS closing" in statement_text:
            return FakeResult(self._close_geometry(arguments))
        if "UPDATE geo.geometry" in statement_text:
            self._confirm_geometry(arguments)
            return FakeResult([])
        if "SELECT natural_key, geometry_id FROM geo.geometry" in statement_text:
            return FakeResult(self._select_current_geometry(arguments))
        if "UPDATE geo.features AS feature SET geometry_id" in statement_text:
            return FakeResult(self._link_feature_geometry(arguments))
        if "UPDATE geo.features AS feature SET properties" in statement_text:
            return FakeResult(self._refresh_features(arguments))
        if "INSERT INTO geo.features" in statement_text:
            rows = []
            for payload in arguments["payloads"]:
                external_id = json.loads(payload)["id"]
                rows.append(SimpleNamespace(id=f"row-{external_id}", external_id=external_id))
            return FakeResult(rows)
        if "AS external_id" in statement_text:
            matched = [
                SimpleNamespace(id=f"row-{external_id}", external_id=external_id)
                for external_id in arguments["external_ids"]
                if external_id in self.existing
            ]
            return FakeResult(matched)
        if "geo.layers" in statement_text:
            return FakeResult([SimpleNamespace(id=self.layer_id)] if self.layer_id else [])
        raise AssertionError(f"unexpected statement: {statement_text}")

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1

    def statements_matching(self, fragment: str) -> list[tuple[str, dict[str, Any]]]:
        """Every recorded execution whose statement body contains the fragment; headers are already stripped."""
        return [execution for execution in self.executions if fragment in execution[0]]

    def _open_geometry_version(self, natural_key: str) -> _StoredGeometryVersion | None:
        """The one row per place with version_valid_to IS NULL, mirroring uq_geometry_current."""
        for version in self.geometry_versions.get(natural_key, []):
            if version.version_valid_to is None:
                return version
        return None

    @staticmethod
    def _resolve_geom(feature_id: str, geojson: str) -> str | None:
        """A feature id is always resolvable here: every id classify sees was just inserted or refreshed."""
        if feature_id:
            return feature_id
        if geojson:
            return geojson
        return None

    def _classify_geometry(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Answer the classify CTE against the fake geo.geometry table."""
        rows = []
        for natural_key, feature_id, geojson, observed_at in zip(
            arguments["natural_keys"],
            arguments["feature_ids"],
            arguments["geojsons"],
            arguments["observed_ats"],
            strict=True,
        ):
            resolved_geom = self._resolve_geom(feature_id, geojson)
            open_version = self._open_geometry_version(natural_key)
            rows.append(
                SimpleNamespace(
                    natural_key=natural_key,
                    geometry_missing=resolved_geom is None,
                    open_geometry_id=None if open_version is None else open_version.geometry_id,
                    geometry_unchanged=(
                        open_version is not None and resolved_geom is not None and open_version.geom == resolved_geom
                    ),
                    successor_is_datable=(
                        open_version is not None
                        and observed_at != "-infinity"
                        and (
                            open_version.version_valid_from == "-infinity"
                            or observed_at > open_version.version_valid_from
                        )
                    ),
                )
            )
        return rows

    def _insert_geometry(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Insert every planned open version, honouring ON CONFLICT (natural_key) WHERE version_valid_to IS NULL."""
        rows = []
        for geometry_id, natural_key, version_valid_from, feature_id, geojson in zip(
            arguments["geometry_ids"],
            arguments["natural_keys"],
            arguments["version_valid_froms"],
            arguments["feature_ids"],
            arguments["geojsons"],
            strict=True,
        ):
            if self._open_geometry_version(natural_key) is not None:
                continue
            self.geometry_versions.setdefault(natural_key, []).append(
                _StoredGeometryVersion(
                    geometry_id=geometry_id,
                    natural_key=natural_key,
                    version_valid_from=version_valid_from,
                    geom=self._resolve_geom(feature_id, geojson) or "",
                    last_confirmed_at=arguments.get("run_clock"),
                )
            )
            rows.append(SimpleNamespace(geometry_id=geometry_id, natural_key=natural_key))
        return rows

    def _close_geometry(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Close every open version named in the supersession batch, in the same call as its successor id."""
        rows = []
        for natural_key, closed_at, successor_id in zip(
            arguments["natural_keys"], arguments["closed_ats"], arguments["successor_ids"], strict=True
        ):
            version = self._open_geometry_version(natural_key)
            if version is None:
                continue
            version.version_valid_to = closed_at
            version.superseded_by = successor_id
            rows.append(SimpleNamespace(geometry_id=version.geometry_id))
        return rows

    def _confirm_geometry(self, arguments: Mapping[str, Any]) -> None:
        """Touch last_confirmed_at on every open version named in the confirm batch, nothing else."""
        run_clock = arguments["run_clock"]
        for natural_key in arguments["natural_keys"]:
            version = self._open_geometry_version(natural_key)
            if version is not None:
                version.last_confirmed_at = run_clock

    def _select_current_geometry(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Re-read the open version per place, exactly as the adapter does before naming an outcome."""
        rows = []
        for natural_key in arguments["natural_keys"]:
            version = self._open_geometry_version(natural_key)
            if version is not None:
                rows.append(SimpleNamespace(natural_key=natural_key, geometry_id=version.geometry_id))
        return rows

    def _refresh_features(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Return one row per external id the refresh diff accepted, as the batched UPDATE ... RETURNING does."""
        return [
            SimpleNamespace(id=f"row-{external_id}", external_id=external_id)
            for external_id, _ in zip(arguments["external_ids"], arguments["next_properties"], strict=True)
            if self.changed is None or external_id in self.changed
        ]

    def _link_feature_geometry(self, arguments: Mapping[str, Any]) -> list[SimpleNamespace]:
        """Repoint every feature named in the link batch, skipping one already pointing at its target."""
        rows = []
        for feature_id, geometry_id in zip(arguments["feature_ids"], arguments["geometry_ids"], strict=True):
            if self.feature_geometry_id.get(feature_id) != geometry_id:
                self.feature_geometry_id[feature_id] = geometry_id
                rows.append(SimpleNamespace(id=feature_id))
        return rows


class RecordingPublisher:
    """Captures realtime publishes so the one-per-written-row contract can be asserted."""

    def __init__(self) -> None:
        """Start with an empty log."""
        self.messages: list[tuple[str, Mapping[str, object]]] = []

    async def publish(self, channel: str, message: Mapping[str, object]) -> None:
        """Record one publish."""
        self.messages.append((channel, message))


# --- T1: the refresh diff ignores geometry, and its asymmetry is load-bearing ---------------------


def test_refresh_predicate_strips_geometry_repaired_on_the_stored_side_only() -> None:
    """The stored side strips both geometry keys; the candidate side strips only 'geometry'."""
    statement = " ".join(str(writer_module._REFRESH_FEATURES).split())
    assert "(feature.properties - 'geometry' - 'geometry_repaired')" in statement
    assert "IS DISTINCT FROM (CAST(pending.next_properties AS jsonb) - 'geometry')" in statement


def test_refresh_predicate_is_not_symmetric() -> None:
    """Tidying the candidate side into a symmetric strip breaks the day a producer emits geometry_repaired."""
    statement = " ".join(str(writer_module._REFRESH_FEATURES).split())
    assert "CAST(pending.next_properties AS jsonb) - 'geometry' - 'geometry_repaired'" not in statement


def test_refresh_is_one_set_based_statement_per_batch() -> None:
    """The refresh unnests its arrays like the insert beside it: one round trip per batch, not per row."""
    statement = " ".join(str(writer_module._REFRESH_FEATURES).split())
    assert "FROM unnest(CAST(:external_ids AS text[]), CAST(:next_properties AS text[]))" in statement
    assert "RETURNING feature.id, pending.external_id AS external_id" in statement


async def test_unchanged_rows_are_not_rewritten_and_publish_nothing() -> None:
    """An unchanged upstream must not churn rows or replay realtime invalidations."""
    session = FakeSession(existing=["alpha", "beta"], changed=[])
    publisher = RecordingPublisher()

    written = await ingest_features(session, [build_write("alpha"), build_write("beta")], publisher)

    assert written == 0
    assert publisher.messages == []
    # `AS feature SET properties` is the refresh statement; the geometry link statement is also an
    # `UPDATE geo.features AS feature ...` but sets `geometry_id`, so it is asserted separately below
    # rather than folded into this count. Two unchanged rows now cost one statement, not two.
    refreshes = session.statements_matching("UPDATE geo.features AS feature SET properties")
    assert len(refreshes) == 1
    assert refreshes[0][1]["external_ids"] == ["alpha", "beta"]
    assert session.statements_matching("INSERT INTO geo.features") == []


async def test_only_genuinely_changed_rows_count_as_written() -> None:
    """A refresh that the diff rejects is not a write, so the count reflects real changes only."""
    session = FakeSession(existing=["alpha", "beta"], changed=["beta"])
    publisher = RecordingPublisher()

    written = await ingest_features(session, [build_write("alpha"), build_write("beta")], publisher)

    assert written == 1
    assert [channel for channel, _ in publisher.messages] == [FIRE_CHANNEL]
    assert publisher.messages[0][1]["id"] == "row-beta"


# --- stored payload shape ------------------------------------------------------------------------


def test_stored_properties_pin_the_identity_over_an_upstream_id() -> None:
    """The producer-local id always wins, so an upstream 'id' field can never re-key a feature."""
    write = build_write("alpha", properties={"id": "upstream-supplied", "brightness": 1.0})
    assert write.stored_properties()["id"] == "alpha"


def test_stored_properties_carry_the_external_id_not_the_natural_key() -> None:
    """properties->>'id' stays byte-identical to the TypeScript featureId; the namespace lives in natural_key."""
    write = build_write("N:2026-08-03:0142:47.8380:-113.2649")
    assert write.external_id == "N:2026-08-03:0142:47.8380:-113.2649"
    assert write.natural_key == "firms:N:2026-08-03:0142:47.8380:-113.2649"
    assert write.stored_properties()["id"] == write.external_id


def test_encode_stored_properties_rejects_non_finite_numbers() -> None:
    """jsonb cannot hold NaN or Infinity, so the writer refuses them rather than emitting invalid JSON."""
    write = build_write("alpha", properties={"brightness": float("nan")})
    with pytest.raises(ValueError, match="Out of range float"):
        encode_stored_properties(write)


def test_encode_stored_properties_round_trips_to_the_stored_payload() -> None:
    """The encoded text is exactly the payload the database receives."""
    write = build_write("alpha", properties={"brightness": 312.4, "geometry": {"type": "Point"}})
    assert json.loads(encode_stored_properties(write)) == write.stored_properties()


def test_feature_write_exposes_the_identitys_data_available_at_for_future_sql_wiring() -> None:
    """Plumbing only: the writer surfaces whatever a producer supplied, defaulting to None."""
    assert build_write("alpha").data_available_at is None

    known = FeatureWrite(
        layer_reference="fire-detections",
        identity=FeatureIdentity(
            producer="firms",
            producer_local_id="alpha",
            observed_at=None,
            data_available_at=datetime(2026, 8, 3, tzinfo=UTC),
        ),
        properties={"brightness": 1.0},
        channel=FIRE_CHANNEL,
    )
    assert known.data_available_at == datetime(2026, 8, 3, tzinfo=UTC)


# --- realtime message ----------------------------------------------------------------------------


def test_realtime_message_uses_the_geojson_feature_shape() -> None:
    """The map consumes a GeoJSON Feature, matching what the TypeScript publisher emitted."""
    geometry = {"type": "Point", "coordinates": [-113.2649, 47.838]}
    write = build_write("alpha", properties={"geometry": geometry, "brightness": 312.4})

    message = realtime_message("row-alpha", write)

    assert message["type"] == "Feature"
    assert message["id"] == "row-alpha"
    assert message["geometry"] == geometry
    assert message["properties"] == write.stored_properties()


def test_realtime_message_geometry_is_none_when_the_payload_has_none() -> None:
    """A payload without geometry publishes an explicit null rather than omitting the key."""
    message = realtime_message("row-alpha", build_write("alpha", properties={"brightness": 1.0}))
    assert message["geometry"] is None


# --- bounded contract guards ---------------------------------------------------------------------


def test_validate_feature_write_accepts_a_conforming_write() -> None:
    """The happy path raises nothing."""
    validate_feature_write(build_write("alpha"))


@pytest.mark.parametrize(
    "channel",
    [
        "fire-detections",
        "layer:Fire-Detections",
        "layer:fire_detections",
        "layer:",
        "layer:fire-detections ",
        f"layer:{'a' * 101}",
    ],
)
def test_validate_feature_write_rejects_a_channel_outside_the_pattern(channel: str) -> None:
    """The channel must match ^layer:[a-z0-9-]{1,100}$, exactly as ingest.ts:145 enforces."""
    with pytest.raises(IngestContractError, match="channel"):
        validate_feature_write(build_write("alpha", channel=channel))


def test_validate_feature_write_rejects_a_whitespace_only_identity() -> None:
    """A blank identity would collapse every record in the layer onto one row."""
    with pytest.raises(IngestContractError, match="identity"):
        validate_feature_write(build_write("   "))


def test_validate_feature_write_rejects_an_oversized_identity() -> None:
    """The 500-character ceiling is defence in depth beyond FeatureIdentity's own 255-character natural_key cap."""
    oversized = SimpleNamespace(producer_local_id="x" * 501, natural_key="firms:x")
    write = FeatureWrite(
        layer_reference="fire-detections",
        identity=oversized,  # type: ignore[arg-type]
        properties={},
        channel=FIRE_CHANNEL,
    )
    with pytest.raises(IngestContractError, match="identity"):
        validate_feature_write(write)


@pytest.mark.parametrize("layer_reference", ["", "l" * 101])
def test_validate_feature_write_rejects_a_layer_reference_outside_the_bounds(layer_reference: str) -> None:
    """The layer reference is bounded at 1..100 characters."""
    with pytest.raises(IngestContractError, match="layer"):
        validate_feature_write(build_write("alpha", layer_reference=layer_reference))


async def test_ingest_features_validates_before_touching_the_database() -> None:
    """A bad channel must be refused before any statement runs, not after a partial write."""
    session = FakeSession()
    with pytest.raises(IngestContractError):
        await ingest_features(session, [build_write("alpha", channel="not-a-layer-channel")])
    assert session.executions == []


# --- layer resolution ----------------------------------------------------------------------------


async def test_resolve_layer_id_matches_a_uuid_reference_on_the_id_column() -> None:
    """A UUID reference resolves by primary key without weakening the foreign key."""
    session = FakeSession()
    assert await resolve_layer_id(session, LAYER_UUID) == LAYER_UUID
    assert "WHERE id = CAST(:layer_reference AS uuid)" in session.executions[0][0]


async def test_resolve_layer_id_matches_an_operator_friendly_name() -> None:
    """A non-UUID reference resolves by layer name."""
    session = FakeSession()
    assert await resolve_layer_id(session, "fire-detections") == LAYER_UUID
    assert "WHERE name = :layer_reference" in session.executions[0][0]


async def test_resolve_layer_id_raises_when_the_layer_is_absent() -> None:
    """A misconfigured layer is a hard failure, never a silent no-op write."""
    session = FakeSession(layer_id=None)
    with pytest.raises(MissingIngestionLayerError, match="fire-detections"):
        await resolve_layer_id(session, "fire-detections")


# --- insert, lock, batch, isolation ---------------------------------------------------------------


async def test_new_features_are_inserted_and_published_once_each() -> None:
    """Every inserted row yields exactly one realtime invalidation."""
    session = FakeSession()
    publisher = RecordingPublisher()

    written = await ingest_features(session, [build_write("alpha"), build_write("beta")], publisher)

    assert written == 2
    assert len(publisher.messages) == 2
    assert {message["id"] for _, message in publisher.messages} == {"row-alpha", "row-beta"}
    assert session.commits == 1


async def test_event_keys_are_locked_in_sorted_order() -> None:
    """Sorted lock acquisition is what stops two concurrent runs deadlocking on one layer."""
    session = FakeSession()

    await ingest_features(session, [build_write("gamma"), build_write("alpha"), build_write("beta")])

    _, arguments = session.statements_matching("pg_advisory_xact_lock")[0]
    assert arguments["event_keys"] == [
        f"{LAYER_UUID}:alpha",
        f"{LAYER_UUID}:beta",
        f"{LAYER_UUID}:gamma",
    ]


async def test_a_repeated_external_id_is_written_once() -> None:
    """Deduplication happens before the insert, mirroring the TypeScript's inputById map."""
    session = FakeSession()
    duplicate = [build_write("alpha", properties={"brightness": 1.0}), build_write("alpha", properties={"b": 2.0})]

    written = await ingest_features(session, duplicate)

    assert written == 1
    _, arguments = session.statements_matching("INSERT INTO geo.features")[0]
    assert len(arguments["payloads"]) == 1


async def test_writes_are_batched_at_the_insert_batch_size() -> None:
    """Batching bounds the statement size regardless of how many records an upstream returns."""
    session = FakeSession()
    writes = [build_write(f"key-{index:04d}") for index in range(INSERT_BATCH_SIZE + 1)]

    written = await ingest_features(session, writes)

    assert written == INSERT_BATCH_SIZE + 1
    inserts = session.statements_matching("INSERT INTO geo.features")
    assert [len(arguments["payloads"]) for _, arguments in inserts] == [INSERT_BATCH_SIZE, 1]
    assert session.commits == 2


async def test_refreshes_cost_one_statement_per_batch_not_one_per_row() -> None:
    """A re-walked window is the hot path: its refreshes collapse into one statement per batch."""
    external_ids = [f"key-{index:04d}" for index in range(INSERT_BATCH_SIZE + 1)]
    session = FakeSession(existing=external_ids)
    publisher = RecordingPublisher()

    written = await ingest_features(session, [build_write(external_id) for external_id in external_ids], publisher)

    assert written == INSERT_BATCH_SIZE + 1
    refreshes = session.statements_matching("UPDATE geo.features AS feature SET properties")
    assert [len(arguments["external_ids"]) for _, arguments in refreshes] == [INSERT_BATCH_SIZE, 1]
    # Every accepted row is still counted and published exactly once, in the order the batch held it.
    assert [message["id"] for _, message in publisher.messages] == [f"row-{key}" for key in external_ids]


async def test_each_layer_is_resolved_before_its_own_writes() -> None:
    """Writes are grouped by layer so one resolve serves every record in that layer."""
    session = FakeSession()
    writes = [
        build_write("alpha"),
        build_write("gauge", layer_reference="water-gauges", channel="layer:water-gauges"),
    ]

    written = await ingest_features(session, writes)

    assert written == 2
    assert len(session.statements_matching("geo.layers")) == 2


async def test_a_failed_statement_rolls_back_and_propagates() -> None:
    """A write failure must surface so the job records a failure rather than reporting a partial success."""
    session = FakeSession(fail_on="INSERT INTO geo.features")

    with pytest.raises(RuntimeError, match="database refused"):
        await ingest_features(session, [build_write("alpha")])

    assert session.rollbacks == 1
    assert session.commits == 0


async def test_the_writer_publishes_nothing_when_no_publisher_is_bound() -> None:
    """Realtime is optional; the durable write path does not depend on it."""
    session = FakeSession()
    assert await ingest_features(session, [build_write("alpha")], None) == 1


async def test_bind_feature_writer_produces_a_single_argument_writer() -> None:
    """Jobs receive a one-argument seam so a job test needs neither a session nor a publisher."""
    session = FakeSession()
    publisher = RecordingPublisher()

    write_features = bind_feature_writer(session, publisher)
    written = await write_features([build_write("alpha")])

    assert written == 1
    assert len(publisher.messages) == 1


# --- the geometry seam: maintained inside the same transaction as the insert/refresh loops -----------


async def test_a_newly_inserted_feature_is_versioned_and_linked_to_its_geometry() -> None:
    """The dimension opens a first version for a brand-new feature and repoints geo.features at it."""
    session = FakeSession()

    written = await ingest_features(session, [build_write("alpha")])

    assert written == 1
    version = session._open_geometry_version("firms:alpha")
    assert version is not None
    assert session.feature_geometry_id.get("row-alpha") == version.geometry_id


async def test_re_ingesting_the_same_feature_confirms_rather_than_reopens_its_geometry_version() -> None:
    """A second run of the identical shape must not mint a second version for the same place."""
    natural_key = "firms:alpha"
    session = FakeSession(existing=["alpha"], changed=["alpha"])

    await ingest_features(session, [build_write("alpha")])
    first_version = session._open_geometry_version(natural_key)
    assert first_version is not None

    await ingest_features(session, [build_write("alpha", properties={"brightness": 400.0})])

    assert len(session.geometry_versions[natural_key]) == 1
    second_version = session._open_geometry_version(natural_key)
    assert second_version is not None
    assert second_version.geometry_id == first_version.geometry_id


async def test_geometry_is_maintained_even_for_a_refresh_the_diff_rejected() -> None:
    """An unchanged refresh writes no row, but the place was still seen and still gets its geometry linked."""
    session = FakeSession(existing=["alpha"], changed=[])

    written = await ingest_features(session, [build_write("alpha")])

    assert written == 0
    assert session.feature_geometry_id.get("row-alpha") is not None
    assert session._open_geometry_version("firms:alpha") is not None


async def test_a_grid_cell_write_versions_by_grid_name_and_cell_key_not_by_its_own_feature_id() -> None:
    """A raster-shaped write is versioned on the cell it lands in, so many samples can share one geometry row."""
    cell = GridCell(grid_name="ndvi-250m", cell_key="R003C004", resolution_metres=250, geojson='{"type":"Polygon"}')
    session = FakeSession()

    await ingest_features(
        session,
        [build_write("sample-1", producer="ndvi", layer_reference="ndvi", channel="layer:ndvi", grid_cell=cell)],
    )

    assert "ndvi:ndvi-250m:R003C004" in session.geometry_versions
    assert "ndvi:sample-1" not in session.geometry_versions


# --- The forward path reports what the dimension did, instead of discarding it ----------------------
# `upsert_geometry_versions` names an action per place and `run_geometry_repair` already folds those
# into its job result. The forward path read only `geometry_id` and returned nothing, so an
# `undatable` place -- a shape that visibly moved with no instant to date the move -- was re-detected
# on every tick, relinked to the same stale version, and reported as a clean run.


class RecordingLogger:
    """A structlog stand-in that records the events the writer emits, so an operator signal is assertable."""

    def __init__(self) -> None:
        """Start with no recorded events."""
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def warning(self, event: str, **fields: Any) -> None:
        """Record one warning."""
        self.events.append(("warning", event, dict(fields)))

    def info(self, event: str, **fields: Any) -> None:
        """Record one info event."""
        self.events.append(("info", event, dict(fields)))


def _seed_open_version(session: FakeSession, natural_key: str, geom: str, version_valid_from: str) -> None:
    """Put one open geo.geometry row in the fake table, as an earlier tick would have left it."""
    session.geometry_versions[natural_key] = [
        _StoredGeometryVersion(
            geometry_id=f"geometry-{natural_key}",
            natural_key=natural_key,
            version_valid_from=version_valid_from,
            geom=geom,
        )
    ]


async def test_an_undatable_geometry_drift_is_named_in_a_warning_rather_than_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    # An open version whose shape differs from what geo.features now holds, dated later than the
    # incoming observation (the write is undated, so it binds '-infinity' and can never be later).
    _seed_open_version(session, "firms:alpha", "a-different-shape", "2026-08-04T00:00:00+00:00")
    recorder = RecordingLogger()
    monkeypatch.setattr(writer_module, "logger", recorder)

    written = await ingest_features(session, [build_write("alpha")])

    assert written == 1
    warnings = [fields for level, event, fields in recorder.events if event == "geometry_version_undatable"]
    assert warnings == [{"count": 1, "natural_keys": ["firms:alpha"]}]
    # The chain is left exactly as it was: no invented boundary, no confirmation of a shape that moved.
    assert len(session.geometry_versions["firms:alpha"]) == 1
    assert session.geometry_versions["firms:alpha"][0].version_valid_to is None
    assert session.geometry_versions["firms:alpha"][0].last_confirmed_at is None


async def test_a_run_reports_the_version_actions_it_took(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    _seed_open_version(session, "firms:beta", "row-beta", "2026-08-04T00:00:00+00:00")
    recorder = RecordingLogger()
    monkeypatch.setattr(writer_module, "logger", recorder)

    await ingest_features(session, [build_write("alpha"), build_write("beta")])

    tallies = [fields for level, event, fields in recorder.events if event == "geometry_versions_maintained"]
    assert tallies == [{"opened": 1, "confirmed": 1, "superseded": 0, "undatable": 0}]


async def test_a_run_that_versioned_nothing_reports_no_tally(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    recorder = RecordingLogger()
    monkeypatch.setattr(writer_module, "logger", recorder)

    await ingest_features(session, [])

    assert recorder.events == []
