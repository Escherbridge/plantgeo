"""The daily run: the FR-5 publication gate, the four rungs, the provenance columns and determinism.

The lane fixtures come from `test_fire_risk_features.py` so one seeded warehouse serves both suites.
The backtest RECEIPT is built here rather than stored as a fixture: FR-5's gate is the object, and
the artifact binds its digest, so a fixture with a hand-written digest could never be verified.
"""

from __future__ import annotations

import io
import json
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pyarrow.parquet as pq
import pytest
from test_fire_risk_features import ISSUED_ON, MOMENT, seeded_reader

from plantgeo_ml_service.foundation.canonical import canonical_json
from plantgeo_ml_service.foundation.parquet_paths import (
    ZOOM_TIERS,
    availability_bootstrap_marker_key,
    availability_pointer_path,
)
from plantgeo_ml_service.method.ml.fire_risk_model import (
    ARTIFACT_MISSING,
    BACKTEST_LIFT_NOT_CLEARED,
    OUT_OF_STRATUM,
    FireRiskArtifact,
    artifact_from_mapping,
)
from plantgeo_ml_service.pipeline.availability_publisher import InMemoryPointerStore
from plantgeo_ml_service.pipeline.fire_risk_daily import (
    ENSEMBLE_SIZE,
    MEDIAN_QUANTILE,
    NO_ARTIFACT_SENTINEL,
    FireRiskDailyError,
    FireRiskDailyReceipt,
    FireRiskPublicationGateError,
    forecast_run_id,
    run_fire_risk_daily,
)
from plantgeo_ml_service.pipeline.fire_risk_features import binding_frontier
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    UNFORECAST_DAY_REASON,
    read_lane_bootstrap_receipt,
)
from plantgeo_ml_service.pipeline.object_store import (
    InMemoryObjectStoreBackend,
    ObjectStore,
    sha256_of,
)
from plantgeo_ml_service.warehouse.availability import AvailabilityConfig, AvailabilityIdentity, EvidenceReceipt
from plantgeo_ml_service.warehouse.streams import FIRE_RISK_STREAM

if TYPE_CHECKING:
    import pyarrow as pa

SCRATCH_PREFIX = "ml/scratch/2026-09-19/"
HORIZONS = (1, 2)
FIXTURES = Path(__file__).parent / "fixtures" / "fire_risk"

PROVENANCE_COLUMNS = ("forecast_run_id", "random_seed", "ensemble_size", "horizon_days", "issued_on", "quantile")

#: The day horizons are counted from: the newest day EVERY input lane has settled through (M4).
FRONTIER: Final = binding_frontier(ISSUED_ON)

#: Where the gate's evidence object lives. The artifact binds this key and its digest.
BACKTEST_KEY: Final = "ml/receipts/fire-risk/backtest/2026-09-19.json"


def backtest_receipt_bytes(cleared: tuple[str, ...]) -> bytes:
    """Render a minimal walk-forward receipt: the verdict list is what the gate actually reads."""
    return canonical_json(
        {
            "cleared_strata": list(cleared),
            "feature_set_version": "fire-risk-features-v1",
            "generated_at": "2026-09-19T00:00:00+00:00",
            "schema_version": 1,
        }
    ).encode("utf-8")


def artifact(name: str, *, cleared: tuple[str, ...] | None = None) -> FireRiskArtifact:
    """Load one stored artifact fixture, binding it to a receipt whose digest is really computed.

    The fixture's `backtest.sha256` is a placeholder; M3 makes the run FETCH and verify that object,
    so the binding has to be real. `sha256` is dropped because re-binding changes the document.
    """
    document: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    document.pop("sha256", None)
    if document.get("backtest") is not None:
        strata = tuple(document["backtest"]["cleared_strata"]) if cleared is None else cleared
        document["backtest"] = {
            "cleared_strata": list(strata),
            "key": BACKTEST_KEY,
            "sha256": sha256_of(backtest_receipt_bytes(strata)),
        }
    return artifact_from_mapping(document)


def store(*, model: FireRiskArtifact | None = None) -> ObjectStore:
    """Return an in-memory warehouse holding the backtest receipt `model` binds, when it binds one."""
    target = ObjectStore(backend=InMemoryObjectStoreBackend())
    if model is not None and model.backtest is not None:
        target.put_immutable(
            model.backtest.key,
            backtest_receipt_bytes(model.backtest.cleared_strata),
            content_type="application/json",
        )
    return target


def availability_config(*, ceiling: date | None = None) -> AvailabilityConfig:
    """Return the lane's availability identity, with the whole rung ladder required."""
    return AvailabilityConfig(
        identity=AvailabilityIdentity(
            lane_root=f"layer={FIRE_RISK_STREAM}/kind=forecast",
            lane=FIRE_RISK_STREAM,
            product=FIRE_RISK_STREAM,
            nature="daily_series",
            required_rungs=ZOOM_TIERS,
            verified_source_inventory_root="d" * 64,
        ),
        source_ceiling=ceiling if ceiling is not None else FRONTIER + timedelta(days=max(HORIZONS)),
        bootstrap_receipt=EvidenceReceipt(
            key=f"layer={FIRE_RISK_STREAM}/kind=forecast/availability/bootstrap/_BOOTSTRAPPED.json",
            sha256="e" * 64,
        ),
    )


def run(
    *,
    target: ObjectStore,
    model: FireRiskArtifact | None,
    dry_run_prefix: str | None = SCRATCH_PREFIX,
    availability: AvailabilityConfig | None = None,
    pointers: InMemoryPointerStore | None = None,
) -> FireRiskDailyReceipt:
    """Run one daily pass over the seeded lanes, defaulting to a scratch prefix."""
    return run_fire_risk_daily(
        seeded_reader(),
        target,
        model,
        run_date=ISSUED_ON,
        horizons=HORIZONS,
        dry_run_prefix=dry_run_prefix,
        completed_at=MOMENT,
        availability=availability,
        pointers=pointers,
    )


def test_a_real_prefix_without_a_backtest_receipt_is_refused() -> None:
    """FR-5: no fire-risk partition reaches a real prefix until a walk-forward receipt exists."""
    model = artifact("artifact-ungated.json")
    with pytest.raises(FireRiskPublicationGateError):
        run(target=store(model=model), model=model, dry_run_prefix=None)


def test_a_real_prefix_with_no_artifact_at_all_is_refused() -> None:
    """B2: the gate used to be OPEN when the artifact was None, so a lane of refusals got published."""
    target = store()

    with pytest.raises(FireRiskPublicationGateError, match="no artifact"):
        run(target=target, model=None, dry_run_prefix=None)

    assert not target.backend.objects


def test_a_dry_run_prefix_that_is_not_scratch_is_refused() -> None:
    """A dry run that writes outside the scratch root is a production write wearing a flag."""
    model = artifact("artifact-cleared.json")
    with pytest.raises(FireRiskDailyError):
        run(target=store(model=model), model=model, dry_run_prefix="layer=fire-risk/")


def test_a_dry_run_writes_every_rung_of_every_valid_day() -> None:
    """A day is selectable only at a resolution somebody wrote, so all four rungs are written."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)

    receipt = run(target=target, model=model)

    written = {(partition.day, partition.zoom) for partition in receipt.partitions}
    expected_days = {FRONTIER + timedelta(days=horizon) for horizon in HORIZONS}
    assert written == {(day, zoom) for day in expected_days for zoom in ZOOM_TIERS}
    assert receipt.scratch_run is True
    assert receipt.prefix == SCRATCH_PREFIX
    assert receipt.issued_on == FRONTIER
    assert receipt.run_date == ISSUED_ON


def test_every_written_row_carries_the_six_provenance_columns() -> None:
    model = artifact("artifact-cleared.json")
    target = store(model=model)
    receipt = run(target=target, model=model)

    day = FRONTIER + timedelta(days=HORIZONS[0])
    reading = ObjectStore(backend=target.backend, prefix=SCRATCH_PREFIX)
    table = reading.read_partition(FIRE_RISK_STREAM, "forecast", ZOOM_TIERS[-1], day).table

    for column in PROVENANCE_COLUMNS:
        assert table.column(column).null_count == 0
    assert table.column("forecast_run_id").to_pylist() == [receipt.forecast_run_id] * table.num_rows
    assert table.column("ensemble_size").to_pylist() == [ENSEMBLE_SIZE] * table.num_rows
    assert table.column("quantile").to_pylist() == [MEDIAN_QUANTILE] * table.num_rows
    assert table.column("issued_on").to_pylist() == [FRONTIER] * table.num_rows
    assert table.column("stratum").null_count == 0
    assert table.column("model_artifact_sha256").null_count == 0


def test_the_run_identity_names_the_artifact_the_frontier_the_run_date_and_the_seed() -> None:
    model = artifact("artifact-cleared.json")

    receipt = run(target=store(model=model), model=model)

    assert receipt.forecast_run_id == forecast_run_id(
        artifact_sha256=model.sha256,
        issued_on=FRONTIER,
        run_date=ISSUED_ON,
        random_seed=0,
    )


def test_two_artifactless_runs_of_different_days_never_collide() -> None:
    """B2: an artifact-less run records a DECLARED sentinel, and the run date still separates runs."""
    first = forecast_run_id(artifact_sha256=NO_ARTIFACT_SENTINEL, issued_on=FRONTIER, run_date=ISSUED_ON, random_seed=0)
    later = forecast_run_id(
        artifact_sha256=NO_ARTIFACT_SENTINEL,
        issued_on=FRONTIER + timedelta(days=1),
        run_date=ISSUED_ON + timedelta(days=1),
        random_seed=0,
    )
    reseeded = forecast_run_id(
        artifact_sha256=NO_ARTIFACT_SENTINEL, issued_on=FRONTIER, run_date=ISSUED_ON, random_seed=1
    )

    assert NO_ARTIFACT_SENTINEL
    assert len({first, later, reseeded}) == len((first, later, reseeded))

    receipt = run(target=store(), model=None)
    assert receipt.artifact_sha256 == NO_ARTIFACT_SENTINEL
    assert receipt.forecast_run_id == first


def test_the_same_inputs_write_byte_identical_partitions() -> None:
    """NFR 1: same artifact, same inputs, same seed, same bytes. The digests are the evidence."""
    model = artifact("artifact-cleared.json")
    first = run(target=store(model=model), model=model)
    second = run(target=store(model=model), model=model)

    assert [partition.sha256 for partition in first.partitions] == [partition.sha256 for partition in second.partitions]


def test_a_missing_artifact_refuses_every_row_and_writes_no_score() -> None:
    target = store()

    receipt = run(target=target, model=None)

    table = _base_table(target)
    assert receipt.scored_row_count == 0
    assert receipt.refusal_counts == {ARTIFACT_MISSING: table.num_rows * len(HORIZONS)}
    assert table.column("probability").null_count == table.num_rows
    assert table.column("risk_score").null_count == table.num_rows
    assert table.column("stratum").null_count == 0


def test_a_stratum_the_backtest_did_not_clear_publishes_refusals_not_scores() -> None:
    """A stratum without measured out-of-sample lift publishes a named refusal on a real prefix."""
    model = artifact("artifact-withheld.json")
    target = store(model=model)

    receipt = run(target=target, model=model, dry_run_prefix=None)

    assert receipt.scored_row_count == 0
    assert set(receipt.refusal_counts) == {BACKTEST_LIFT_NOT_CLEARED}
    assert receipt.withheld_strata == ("open_canopy",)
    assert _base_table(target, prefix="").column("probability").null_count > 0


def test_a_cell_without_a_stratum_is_refused_out_of_stratum() -> None:
    model = artifact("artifact-cleared.json")
    target = store(model=model)
    reader = seeded_reader()
    reader.frames["vegetation"] = reader.frames["vegetation"].head(0)

    receipt = run_fire_risk_daily(
        reader,
        target,
        model,
        run_date=ISSUED_ON,
        horizons=HORIZONS,
        dry_run_prefix=SCRATCH_PREFIX,
        completed_at=MOMENT,
    )

    assert set(receipt.refusal_counts) == {OUT_OF_STRATUM}
    assert receipt.scored_row_count == 0


def test_a_cleared_stratum_is_scored_on_a_real_prefix() -> None:
    model = artifact("artifact-cleared.json")
    target = store(model=model)

    receipt = run(target=target, model=model, dry_run_prefix=None)

    assert receipt.scratch_run is False
    assert receipt.cleared_strata == ("open_canopy",)
    assert receipt.scored_row_count > 0
    assert _base_table(target, prefix="").column("risk_score").null_count == 0


# --- M3: the backtest receipt is fetched and verified, never trusted from the artifact -----------


def test_the_cleared_strata_are_read_from_the_receipt_not_from_the_artifact() -> None:
    """The artifact's list is a CLAIM about an object; the object is what gates publication."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)

    receipt = run(target=target, model=model, dry_run_prefix=None)

    stored = target.read_object(BACKTEST_KEY)
    assert stored is not None
    assert receipt.cleared_strata == tuple(json.loads(stored)["cleared_strata"])


def test_a_backtest_receipt_the_store_does_not_hold_refuses_the_run() -> None:
    model = artifact("artifact-cleared.json")
    target = store()  # the receipt object is deliberately absent

    with pytest.raises(FireRiskPublicationGateError, match="does not hold"):
        run(target=target, model=model, dry_run_prefix=None)


def test_a_backtest_receipt_whose_bytes_disagree_with_the_binding_refuses_the_run() -> None:
    """A receipt swapped under its key digests differently, and the artifact bound the digest."""
    model = artifact("artifact-cleared.json")
    target = ObjectStore(backend=InMemoryObjectStoreBackend())
    target.put_immutable(
        BACKTEST_KEY,
        backtest_receipt_bytes(("closed_forest", "open_canopy")),
        content_type="application/json",
    )

    with pytest.raises(FireRiskPublicationGateError, match="digests to"):
        run(target=target, model=model, dry_run_prefix=None)


def test_an_artifact_that_disagrees_with_its_own_receipt_refuses_the_run() -> None:
    """An artifact edited to widen its cleared list would otherwise publish unmeasured strata."""
    honest = artifact("artifact-cleared.json")
    target = store(model=honest)
    widened = artifact("artifact-cleared.json", cleared=("closed_forest", "open_canopy"))
    # The widened artifact binds a DIFFERENT receipt digest, so it is refused on the digest first;
    # re-point it at the honest object to reach the disagreement itself.
    document = json.loads((FIXTURES / "artifact-cleared.json").read_text(encoding="utf-8"))
    document.pop("sha256", None)
    document["backtest"] = {
        "cleared_strata": ["closed_forest", "open_canopy"],
        "key": BACKTEST_KEY,
        "sha256": sha256_of(backtest_receipt_bytes(honest.backtest.cleared_strata)),  # type: ignore[union-attr]
    }
    lying = artifact_from_mapping(document)

    assert widened.backtest is not None
    with pytest.raises(FireRiskPublicationGateError, match="disagrees with its own evidence"):
        run(target=target, model=lying, dry_run_prefix=None)


# --- FR-4a: publication, the bootstrap, and the day ladder ---------------------------------------


def test_the_availability_pointer_advances_after_the_rungs_are_written() -> None:
    """Writing a partition does not publish it; the generation and the pointer do (FR-4a)."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)
    pointers = InMemoryPointerStore()

    receipt = run(
        target=target,
        model=model,
        availability=availability_config(),
        pointers=pointers,
    )

    scratch_pointer_key = f"{SCRATCH_PREFIX}{availability_pointer_path(FIRE_RISK_STREAM, 'forecast')}"
    stored = pointers.read_pointer(scratch_pointer_key)
    assert receipt.availability_outcome == "advanced"
    assert stored is not None
    # A dry run must never advance the PUBLISHED lane pointer, only the one under its own root.
    assert pointers.read_pointer(availability_pointer_path(FIRE_RISK_STREAM, "forecast")) is None


def test_the_lane_root_is_bootstrapped_through_the_shared_path() -> None:
    """M6: the fire-risk lane root used to publish a generation with no bootstrap marker at all."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)
    writing = ObjectStore(backend=target.backend, prefix=SCRATCH_PREFIX)

    run(target=target, model=model, availability=availability_config(), pointers=InMemoryPointerStore())

    lane_root = f"layer={FIRE_RISK_STREAM}/kind=forecast"
    marker = writing.read_object(availability_bootstrap_marker_key(lane_root))
    assert marker is not None
    document = json.loads(marker)
    assert set(document) == {
        "bootstrap_receipt_key",
        "bootstrap_receipt_sha256",
        "lane_root",
        "schema_version",
    }
    assert document["schema_version"] == BOOTSTRAP_MARKER_SCHEMA_VERSION
    receipt = read_lane_bootstrap_receipt(writing, lane_root=lane_root)
    assert receipt is not None
    body = writing.read_object(receipt.key)
    assert body is not None
    assert sha256_of(body) == receipt.sha256


def test_no_availability_row_cites_a_source_receipt_with_an_empty_digest() -> None:
    """B2: the source receipt used to be the artifact key, which had no digest when there was none."""
    target = store()
    pointers = InMemoryPointerStore()

    receipt = run(target=target, model=None, availability=availability_config(), pointers=pointers)

    rows = _generation_rows(target, receipt)
    assert rows
    for row in rows:
        assert len(str(row["source_receipt_sha256"])) == len("0" * 64)
        assert str(row["source_receipt_key"]).endswith(".json")


def test_a_horizon_day_with_no_rows_is_published_as_a_governed_absence() -> None:
    """M2: the ceiling reaches a day this run never forecast, and that hole is INDEXED."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)
    unforecast_day = FRONTIER + timedelta(days=max(HORIZONS) + 1)

    receipt = run(
        target=target,
        model=model,
        availability=availability_config(ceiling=unforecast_day),
        pointers=InMemoryPointerStore(),
    )

    assert receipt.absent_days == (unforecast_day,)
    rows = _generation_rows(target, receipt)
    absent = [row for row in rows if row["day"] == unforecast_day]
    assert {row["rung"] for row in absent} == set(ZOOM_TIERS)
    assert {row["terminal_state"] for row in absent} == {"governed_absence"}
    assert {row["absence_reason"] for row in absent} == {UNFORECAST_DAY_REASON}
    published = [row for row in rows if row["day"] != unforecast_day]
    assert {row["terminal_state"] for row in published} == {"published"}


def test_a_run_without_an_availability_config_publishes_nothing() -> None:
    model = artifact("artifact-cleared.json")
    receipt = run(target=store(model=model), model=model)

    assert receipt.availability_outcome is None


def test_the_prediction_receipt_is_written_and_a_replay_adopts_it() -> None:
    """The receipt carries no wall clock, so a replay of one issue day is byte-identical."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)
    reading = ObjectStore(backend=target.backend, prefix=SCRATCH_PREFIX)

    first = run(target=target, model=model)
    stored = reading.read_object(first.relative_path)
    second = run(target=target, model=model)

    assert stored is not None
    assert first.relative_path.startswith(f"ml/receipts/fire-risk/{ISSUED_ON.isoformat()}/")
    assert reading.read_object(second.relative_path) == stored


# --- M7: the coarse rung is a row SELECT, declared in the shared vocabulary ------------------------


def test_a_coarse_rung_never_holds_more_rows_than_the_base_rung() -> None:
    """A rung-select collapses cells; it may not invent one."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)

    receipt = run(target=target, model=model)

    day = FRONTIER + timedelta(days=HORIZONS[0])
    base = next(
        partition for partition in receipt.partitions if partition.day == day and partition.zoom == ZOOM_TIERS[-1]
    )
    for zoom in ZOOM_TIERS[:-1]:
        coarse = next(partition for partition in receipt.partitions if partition.day == day and partition.zoom == zoom)
        assert coarse.row_count <= base.row_count


def test_a_coarse_fire_risk_row_is_a_whole_row_the_model_produced() -> None:
    """The merge rule is a property of the field: a probability surface has no honest mean."""
    model = artifact("artifact-cleared.json")
    target = store(model=model)

    run(target=target, model=model)

    day = FRONTIER + timedelta(days=HORIZONS[0])
    reading = ObjectStore(backend=target.backend, prefix=SCRATCH_PREFIX)
    base = reading.read_partition(FIRE_RISK_STREAM, "forecast", ZOOM_TIERS[-1], day).table.to_pylist()
    coarse = reading.read_partition(FIRE_RISK_STREAM, "forecast", 0, day).table.to_pylist()

    produced = {(row["risk_score"], row["probability"], row["stratum"], row["refused_reason"]) for row in base}
    assert coarse
    for row in coarse:
        # Every field of a coarse row came from ONE base row, not from four independent merges.
        assert (row["risk_score"], row["probability"], row["stratum"], row["refused_reason"]) in produced
    # And it is the WORST of them, which is the honest summary of a risk surface at a coarse zoom.
    worst = max(row["risk_score"] for row in base if row["risk_score"] is not None)
    assert max(row["risk_score"] for row in coarse if row["risk_score"] is not None) == worst


def _base_table(target: ObjectStore, *, prefix: str = SCRATCH_PREFIX) -> pa.Table:
    """Read the base rung of the first written valid day back out of the bucket."""
    day: date = FRONTIER + timedelta(days=HORIZONS[0])
    reading = ObjectStore(backend=target.backend, prefix=prefix)
    return reading.read_partition(FIRE_RISK_STREAM, "forecast", 13, day).table


def _generation_rows(target: ObjectStore, receipt: FireRiskDailyReceipt) -> list[dict[str, Any]]:
    """Read the published availability generation back out of the bucket the run wrote to."""
    reading = ObjectStore(backend=target.backend, prefix=receipt.prefix)
    payload = reading.read_object(_generation_key(reading))
    assert payload is not None
    return pq.read_table(io.BytesIO(payload)).to_pylist()


def _generation_key(reading: ObjectStore) -> str:
    """Return the one availability generation this run wrote."""
    keys = [key for key in reading.list_relative_paths("layer=") if "/availability/generation=" in key]
    assert len(keys) == 1, keys
    return keys[0]
