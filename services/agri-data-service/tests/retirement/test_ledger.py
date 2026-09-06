"""The ledger parses the A3 inventory, and the recorded corrections outrank what it says."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agri_data_service.retirement.ledger import (
    GEO_FEATURES_ENVIRONMENTAL_LAYERS,
    MATVIEW_REFRESH_REGISTRY_PATHS,
    MATVIEW_REFRESH_RETIRED_REGION_MARKER,
    MATVIEW_REFRESH_RETIRED_RELATIONS,
    DropForm,
    DropFormRefusedError,
    InventoryClass,
    LedgerError,
    ObjectKind,
    load_ledger,
    parse_inventory,
)
from agri_data_service.retirement.readers import find_repository_root
from tests.retirement import SYNTHETIC_INVENTORY, build_checkout

if TYPE_CHECKING:
    from pathlib import Path


def test_one_cell_naming_two_relations_becomes_two_rows() -> None:
    """`geo.historical_fire_data, geo.historical_water_drought` is two droppable objects, not one."""
    rows = {row.relation: row for row in parse_inventory(SYNTHETIC_INVENTORY)}

    assert rows["geo.historical_a"].inventory_class is InventoryClass.DROP_NOW
    assert rows["geo.historical_b"].inventory_class is InventoryClass.DROP_NOW


def test_a_bare_name_is_qualified_with_its_rows_schema() -> None:
    """The inventory writes `job_run` in the relation cell and `agri` in the schema cell beside it."""
    rows = {row.relation for row in parse_inventory(SYNTHETIC_INVENTORY)}

    assert "agri.job_run" in rows
    assert "job_run" not in rows


def test_a_classification_the_tool_cannot_map_is_unresolved_not_a_guess() -> None:
    """An unreadable class must never default to something that authorises a drop."""
    rows = {row.relation: row for row in parse_inventory(SYNTHETIC_INVENTORY)}

    assert rows["geo.mystery"].inventory_class is InventoryClass.UNRESOLVED


def test_an_inventory_that_parses_to_nothing_is_a_refusal() -> None:
    """A changed table shape must fail loudly rather than ledger zero relations."""
    with pytest.raises(LedgerError, match="zero relations"):
        parse_inventory("# no tables here\n")


def test_an_unledgered_relation_is_refused(tmp_path: Path) -> None:
    """The packet's claim is that the drop was ledgered; an unledgered relation has no packet."""
    ledger = load_ledger(build_checkout(tmp_path))

    with pytest.raises(LedgerError, match="not in"):
        ledger.candidate("geo.invented_relation")


def test_geo_features_permits_only_a_row_delete(tmp_path: Path) -> None:
    """Fact 1: `interventions` lives in geo.features permanently, so the table never drops."""
    candidate = load_ledger(build_checkout(tmp_path)).candidate("geo.features")

    assert candidate.permitted_forms == frozenset({DropForm.ROW_DELETE})
    assert candidate.default_form() is DropForm.ROW_DELETE
    with pytest.raises(DropFormRefusedError, match="interventions"):
        candidate.require_form(DropForm.TABLE_DROP)


def test_the_row_predicate_follows_the_layers_the_packet_covers(tmp_path: Path) -> None:
    """A one-layer packet must not archive or delete the other six layers' rows."""
    candidate = load_ledger(build_checkout(tmp_path)).candidate("geo.features")

    scoped = candidate.row_filter(("fire-perimeters",))

    assert "'fire-perimeters'" in scoped
    assert "'vegetation'" not in scoped
    assert all(f"'{layer}'" in candidate.row_filter() for layer in GEO_FEATURES_ENVIRONMENTAL_LAYERS)


def test_the_row_predicate_refuses_a_layer_the_relation_does_not_hold(tmp_path: Path) -> None:
    """`interventions` is not one of the seven, and must not be reachable through --layer."""
    candidate = load_ledger(build_checkout(tmp_path)).candidate("geo.features")

    with pytest.raises(LedgerError, match="interventions"):
        candidate.row_filter(("interventions",))


def test_interventions_is_not_one_of_the_environmental_layers() -> None:
    """The permanent resident must never appear in the set whose rows the track deletes."""
    assert "interventions" not in GEO_FEATURES_ENVIRONMENTAL_LAYERS
    assert len(GEO_FEATURES_ENVIRONMENTAL_LAYERS) == 7


def test_spatial_cell_carries_a_survival_dependency_against_its_drop_now_row(tmp_path: Path) -> None:
    """Fact 2: the inventory says drop now; the recorded readers say the environment cannot lose it."""
    candidate = load_ledger(build_checkout(tmp_path)).candidate("agri.spatial_cell")

    assert candidate.inventory.inventory_class is InventoryClass.DROP_NOW
    assert candidate.survival is not None
    assert len(candidate.survival.readers) >= 5
    assert any("vegetation_ndvi_plane" in reader.citation for reader in candidate.survival.readers)


def test_signal_observation_day_owes_a_same_migration_redefinition(tmp_path: Path) -> None:
    """Fact 4: the census view must be redefined, never taken by a CASCADE."""
    candidate = load_ledger(build_checkout(tmp_path)).candidate("geo.mv_signal_observation_day")

    dependents = {dependent.name: dependent for dependent in candidate.dependent_objects}

    assert "geo.v_observation_day_census" in dependents
    assert dependents["geo.v_observation_day_census"].forbid_cascade is True


def test_a_matview_without_an_mv_prefix_is_still_a_matview(tmp_path: Path) -> None:
    """`geo.watershed_rollup` archives as a matview, which decides that pg_dump alone is not enough."""
    candidate = load_ledger(build_checkout(tmp_path)).candidate("geo.watershed_rollup")

    assert candidate.object_kind is ObjectKind.MATERIALIZED_VIEW
    assert candidate.permitted_forms == frozenset({DropForm.MATERIALIZED_VIEW_DROP})


def test_kind_is_inferred_from_the_naming_convention_and_the_basis_is_recorded(tmp_path: Path) -> None:
    """A reader must be able to see that the kind was inferred rather than looked up."""
    ledger = load_ledger(build_checkout(tmp_path))

    assert ledger.candidate("geo.mv_orphan").object_kind is ObjectKind.MATERIALIZED_VIEW
    assert ledger.candidate("geo.v_thing").object_kind is ObjectKind.VIEW
    assert "inferred" in ledger.candidate("geo.mv_orphan").object_kind_basis


def test_the_real_inventory_still_parses() -> None:
    """The parser is bound to a live evidence file; a table-shape change must fail here, not in prod."""
    ledger = load_ledger(find_repository_root())

    assert "geo.features" in ledger.relations_in_class(InventoryClass.DROP_AFTER_PARQUET_PROOF)
    assert "agri.spatial_cell" in ledger.relations_in_class(InventoryClass.DROP_NOW)
    assert "agri.job_run" in ledger.relations_in_class(InventoryClass.KEEP)


# --- The refresh lane's retirement registry is not a reader of what it retires -------
#
# These four run against the REAL checkout rather than a synthetic one on purpose: the whole claim is
# about two named files that exist, and a synthetic stand-in would pass by describing a tree nobody
# ships. See `MATVIEW_REFRESH_RETIRED_RELATIONS` in ledger.py for the circular gate they break.


def test_a_retired_matview_exempts_the_refresh_lanes_registry_for_its_drop_form_only() -> None:
    """`jobs/matview_refresh.py` NAMES a retired relation in order to keep reporting it, not to read it."""
    candidate = load_ledger(find_repository_root()).candidate("geo.mv_soil_survey_grid")

    exemptions = candidate.reader_exemptions

    assert [exemption.path for exemption in exemptions] == list(MATVIEW_REFRESH_REGISTRY_PATHS)
    assert all(
        exemption.applies_to_forms == frozenset({str(DropForm.MATERIALIZED_VIEW_DROP)}) for exemption in exemptions
    )
    assert all("_RetiredView" in exemption.reason for exemption in exemptions)


def test_a_relation_still_on_the_refresh_lane_is_not_exempted_in_the_same_files() -> None:
    """The scope that matters: a live `MATVIEW_REFRESH_SPECS` entry sits in the very same module.

    `geo.mv_layer_feature_stats` is refreshed on every tick and read by `analytics.ts`. If the
    exemption were written per FILE rather than per relation, it would clear a matview backing a
    `publicProcedure` on the strength of the refresh lane's own retirement bookkeeping.
    """
    candidate = load_ledger(find_repository_root()).candidate("geo.mv_layer_feature_stats")

    assert candidate.reader_exemptions == ()


def test_the_re_add_guard_relation_keeps_blocking_and_earns_no_exemption() -> None:
    """`geo.mv_feature_observation_day_axis` is ABSENT from production, not retired from the lane.

    What names it is `REMOVED_MATVIEW_NAMES` in tests/test_matview_refresh.py -- the guard that stops
    an absent relation being re-added to a lane it once dead-lettered. That guard is a live safety
    property, and exempting it would clear the relation by deleting what protects it. It has no
    `_RetiredView` entry, so it is not in the registry, so its packet still reports `live_readers`.
    """
    candidate = load_ledger(find_repository_root()).candidate("geo.mv_feature_observation_day_axis")

    assert candidate.reader_exemptions == ()
    assert "geo.mv_feature_observation_day_axis" not in MATVIEW_REFRESH_RETIRED_RELATIONS


def test_every_exempted_relation_is_retired_in_the_real_module_and_specced_nowhere_in_it() -> None:
    """The hand-spelled registry is checked against the module it describes, never trusted.

    `ledger.py` may not import the refresh lane -- that would drag SQLAlchemy into a package whose
    claim is that it cannot reach production -- so the registry is spelled by hand and verified here
    against the module's own text. Restoring one of these three to `MATVIEW_REFRESH_SPECS` without
    deleting its registry entry would leave a live spec exempted from its own reader scan; this fails
    the moment that happens, because a spec entry is written above the retired region and a
    `_RetiredView` entry below it.
    """
    source = (find_repository_root() / MATVIEW_REFRESH_REGISTRY_PATHS[0]).read_text(encoding="utf-8")
    assert source.count(MATVIEW_REFRESH_RETIRED_REGION_MARKER) == 1
    marker = source.index(MATVIEW_REFRESH_RETIRED_REGION_MARKER)
    spec_region, retired_region = source[:marker], source[marker:]

    for relation in MATVIEW_REFRESH_RETIRED_RELATIONS:
        entry = f'qualified_name="{relation}"'
        assert entry in retired_region, f"{relation} has no _RetiredView entry; delete its exemption"
        assert entry not in spec_region, f"{relation} is back in MATVIEW_REFRESH_SPECS; delete its exemption"
