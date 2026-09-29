"""The lane loader over built `lanes/` trees: what loads, what is quarantined, and why (spec §4.1, S8).

Every tree carries the real provider files and is checked against the real region manifests, so
these flows exercise exactly what the executor will load at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.lane_config import (
    LANES_DIRECTORY_ENV_VAR,
    LaneDirectoryError,
    default_lanes_directory,
    load_lane_configs,
)
from agri_data_service.foundation.region import load_region
from tests.lane_config.builders import (
    REAL_LANES_DIRECTORY,
    merged,
    nasa_power_lane,
    provisional_forecast_lane,
    settled_soil_lane,
    to_toml,
    transform_lane,
    write_lane_tree,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.foundation.lane_config import LaneConfigSet

SOIL = "soil-era5-land-direct-forward"
WEATHER = "weather-observations-direct-forward"
CLIMATE = "climate-nasa-power-direct-forward"


def _load(root: Path, lanes: list[dict[str, object]], region_slug: str = "pnw") -> LaneConfigSet:
    return load_lane_configs(write_lane_tree(root, lanes), load_region(region_slug))


def _reasons(config: LaneConfigSet, lane_id: str) -> str:
    return " | ".join(config.quarantined[lane_id].reasons)


def test_a_healthy_tree_loads_every_lane_against_its_provider_and_lattice(tmp_path: Path) -> None:
    """Startup over a tree of an ingest lane, a provisional lane and a transform chained on the first."""
    config = _load(
        tmp_path,
        [settled_soil_lane(), provisional_forecast_lane(), transform_lane("soil-vpd-precedence", inputs=[SOIL])],
    )

    assert dict(config.quarantined) == {}
    assert dict(config.provider_failures) == {}
    assert set(config.lanes) == {SOIL, WEATHER, "soil-vpd-precedence"}
    assert set(config.providers) == {"open-meteo", "nasa-power", "usgs-water-data"}
    soil = config.lanes[SOIL]
    provider = config.provider_of(soil)
    assert provider is not None
    assert provider.id == "open-meteo"
    assert soil.grid in load_region("pnw").analysis_lattices
    assert soil.strategy_module == "agri_data_service.pipeline.lanes.soil.open_meteo_era5_land"
    assert config.provider_of(config.lanes["soil-vpd-precedence"]) is None


def test_one_broken_lane_is_quarantined_and_never_stops_its_siblings(tmp_path: Path) -> None:
    """S8: a bad cron and an unparseable file each quarantine their own lane; the process loads the rest."""
    directory = write_lane_tree(
        tmp_path,
        [settled_soil_lane(), nasa_power_lane(schedule={"forward_cron": "61 * * * *"})],
    )
    (directory / "half-written-lane.toml").write_text('id = "half-written-lane"\nkind = ', encoding="utf-8")

    config = load_lane_configs(directory, load_region("pnw"))

    assert set(config.lanes) == {SOIL}
    assert set(config.quarantined) == {CLIMATE, "half-written-lane"}
    assert "schedule.forward_cron" in _reasons(config, CLIMATE)
    assert "not readable TOML" in _reasons(config, "half-written-lane")
    assert config.quarantined[CLIMATE].path == directory / f"{CLIMATE}.toml"


@pytest.mark.parametrize(
    ("overrides", "reason_fragment"),
    [
        pytest.param({"days": {"absence_recheck_days": 5}}, "absence_recheck_days", id="recheck-not-after-lag"),
        pytest.param({"days": {"revision_window_days": 14}}, "revision_window_days", id="revision-inside-recheck"),
        pytest.param(
            {"schedule": {"gap_fill_enabled": True}}, "gap_fill_enabled_at_gate", id="gap-fill-enabled-without-gate"
        ),
        pytest.param({"grid": "analysis-0p10"}, "not an analysis lattice", id="grid-the-region-lacks"),
        pytest.param({"source": {"endpoint": "ensemble"}}, "not an endpoint of provider", id="unknown-endpoint"),
        pytest.param({"source": {"provider": "cams"}}, "has no provider file", id="unknown-provider"),
        pytest.param(
            {"source": {"coverage": "regional", "iso_country_codes": ["CA"]}},
            "does not contain region",
            id="coverage-misses-the-region",
        ),
        pytest.param({"api_key": "not-a-field"}, "api_key", id="undeclared-field"),
        pytest.param({"strategy": "soil/open_meteo"}, "strategy", id="malformed-strategy-key"),
        pytest.param({"pruning": {"enabled": True, "enabled_at_gate": "G6"}}, "never prunes", id="ingest-pruning"),
        pytest.param({"inputs": ["weather-observations-direct-forward"]}, "has no inputs", id="ingest-with-inputs"),
    ],
)
def test_each_lane_invariant_quarantines_only_the_offending_lane(
    tmp_path: Path, overrides: dict[str, object], reason_fragment: str
) -> None:
    """Table-driven over the §4.1 invariants: the offender is quarantined with a reason, the sibling loads."""
    config = _load(tmp_path, [provisional_forecast_lane(), settled_soil_lane(**overrides)])

    assert set(config.lanes) == {WEATHER}
    assert reason_fragment in _reasons(config, SOIL)


def test_the_same_tree_in_a_region_without_the_lattice_quarantines_only_the_gridded_lane(tmp_path: Path) -> None:
    """C2/S8: a second region lacking `analysis-0p25` cannot serve soil, but still serves a grid-free lane."""
    lanes = [settled_soil_lane(), nasa_power_lane()]

    pilot = _load(tmp_path / "pilot", lanes, region_slug="pnw")
    second = _load(tmp_path / "second", lanes, region_slug="kenya-highlands")

    assert set(pilot.lanes) == {SOIL, CLIMATE}
    assert set(second.lanes) == {CLIMATE}
    assert "not an analysis lattice of region 'kenya-highlands'" in _reasons(second, SOIL)


def test_transform_inputs_must_exist_resolve_and_form_a_dag(tmp_path: Path) -> None:
    """D4 chains load; a missing input, a quarantined input and a cycle each quarantine the transform."""
    config = _load(
        tmp_path,
        [
            settled_soil_lane(),
            transform_lane("soil-vpd-precedence", inputs=[SOIL]),
            transform_lane("soil-vpd-weekly", inputs=["soil-vpd-precedence"]),
            transform_lane("orphan-transform", inputs=["never-declared-lane"]),
            nasa_power_lane(days={"absence_recheck_days": 1}),
            transform_lane("climate-precedence", inputs=[CLIMATE]),
            transform_lane("cycle-left", inputs=["cycle-right"]),
            transform_lane("cycle-right", inputs=["cycle-left"]),
        ],
    )

    assert set(config.lanes) == {SOIL, "soil-vpd-precedence", "soil-vpd-weekly"}
    assert "missing or quarantined" in _reasons(config, "orphan-transform")
    assert "missing or quarantined" in _reasons(config, "climate-precedence")
    assert "never resolve" in _reasons(config, "cycle-left")
    assert "never resolve" in _reasons(config, "cycle-right")


def test_conflicts_with_must_be_declared_on_both_sides(tmp_path: Path) -> None:
    """Two lanes that must not both run name each other; a one-sided claim quarantines only its declarer.

    S8: one bad file never takes a healthy lane down with it, and quarantining the declarer alone
    already stops the pair from running together.
    """
    symmetric = _load(
        tmp_path / "symmetric",
        [settled_soil_lane(conflicts_with=[CLIMATE]), nasa_power_lane(conflicts_with=[SOIL])],
    )
    one_sided = _load(tmp_path / "one-sided", [settled_soil_lane(conflicts_with=[CLIMATE]), nasa_power_lane()])

    assert set(symmetric.lanes) == {SOIL, CLIMATE}
    assert set(one_sided.lanes) == {CLIMATE}
    assert "does not list" in _reasons(one_sided, SOIL)


def test_a_stream_slug_claimed_by_two_lanes_quarantines_both(tmp_path: Path) -> None:
    """S18: each `[[streams]]` slug has exactly one owning lane."""
    shared_stream = [{"slug": "soil-field-vpd", "history_floor": None, "floor_basis": "reviewed archive plan"}]
    config = _load(
        tmp_path,
        [settled_soil_lane(streams=shared_stream), nasa_power_lane(streams=shared_stream), provisional_forecast_lane()],
    )

    assert set(config.lanes) == {WEATHER}
    assert "soil-field-vpd" in _reasons(config, SOIL)
    assert "soil-field-vpd" in _reasons(config, CLIMATE)


def test_a_file_named_for_another_lane_is_quarantined_under_its_own_name(tmp_path: Path) -> None:
    """The file stem is the identity, so a mis-named copy of soil never knocks the real soil lane out."""
    directory = write_lane_tree(tmp_path, [settled_soil_lane()])
    (directory / "soil-copy.toml").write_text(to_toml(settled_soil_lane()), encoding="utf-8")

    config = load_lane_configs(directory, load_region("pnw"))

    assert set(config.lanes) == {SOIL}
    assert "must equal the file name stem" in _reasons(config, "soil-copy")


@pytest.mark.parametrize(
    ("provider_edit", "reason_fragment"),
    [
        pytest.param('\n[pools.open-meteo-free]\nwindow = "day"\n', "pools", id="windowed-pools-declined-wq4"),
        pytest.param("\nreserve_fraction = 0.10\n", "reserve_fraction", id="budget-reserve-declined-wq4"),
    ],
)
def test_a_provider_file_outside_the_schema_quarantines_every_lane_on_it(
    tmp_path: Path, provider_edit: str, reason_fragment: str
) -> None:
    """WQ-4: a provider file growing pools or reserves fails to load, and takes only its own lanes with it."""
    directory = write_lane_tree(tmp_path, [settled_soil_lane(), nasa_power_lane()])
    provider_file = directory / "_providers" / "open-meteo.toml"
    provider_file.write_text(provider_file.read_text(encoding="utf-8") + provider_edit, encoding="utf-8")

    config = load_lane_configs(directory, load_region("pnw"))

    assert set(config.provider_failures) == {"open-meteo"}
    assert reason_fragment in " | ".join(config.provider_failures["open-meteo"].reasons)
    assert set(config.lanes) == {CLIMATE}
    assert "provider 'open-meteo' failed to load" in _reasons(config, SOIL)


@pytest.mark.parametrize(
    ("lane", "requires_probe_edge"),
    [
        pytest.param(settled_soil_lane(schedule={"forward_cron": "50 * * * *"}), True, id="settled-weighted-hourly"),
        pytest.param(settled_soil_lane(), True, id="settled-weighted-six-hourly"),
        pytest.param(settled_soil_lane(schedule={"forward_cron": "20 3 * * *"}), False, id="settled-weighted-daily"),
        pytest.param(provisional_forecast_lane(), False, id="write-and-recheck-exempt"),
        pytest.param(nasa_power_lane(), False, id="settled-unweighted-hourly"),
    ],
)
def test_probe_edge_is_required_only_of_settled_weighted_lanes_firing_more_than_daily(
    tmp_path: Path, lane: dict[str, object], requires_probe_edge: bool
) -> None:
    """S6/S19: the loader states which lanes must probe before fanning out; the contract test enforces it."""
    config = _load(tmp_path, [lane])
    (loaded,) = config.lanes.values()

    assert config.requires_probe_edge(loaded) is requires_probe_edge


def test_an_absent_lanes_directory_is_a_packaging_fault_not_an_empty_catalogue(tmp_path: Path) -> None:
    """CA4: an image that forgot `COPY lanes/` must fail loudly rather than dispatch zero config lanes."""
    with pytest.raises(LaneDirectoryError, match=LANES_DIRECTORY_ENV_VAR):
        load_lane_configs(tmp_path / "lanes", load_region("pnw"))


def test_the_lanes_directory_defaults_to_the_service_tree_and_honours_its_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One setting; its default is `lanes/` beside `src/`, the same relative place both images COPY it to."""
    monkeypatch.delenv(LANES_DIRECTORY_ENV_VAR, raising=False)
    assert default_lanes_directory() == REAL_LANES_DIRECTORY

    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(tmp_path))
    assert default_lanes_directory() == tmp_path


def test_a_disabled_gap_fill_needs_no_gate_and_an_enabled_one_names_it(tmp_path: Path) -> None:
    """S12: a rollback flip is one field, and a gap-fill enable carries the owner gate it happened in."""
    enabled = settled_soil_lane(schedule={"gap_fill_enabled": True, "gap_fill_enabled_at_gate": "G6"})
    rolled_back = merged(enabled, {"schedule": {"gap_fill_enabled": False}})

    enabled_config = _load(tmp_path / "enabled", [enabled])
    rolled_back_config = _load(tmp_path / "rolled-back", [rolled_back])

    assert enabled_config.lanes[SOIL].schedule.gap_fill_enabled_at_gate == "G6"
    assert rolled_back_config.lanes[SOIL].schedule.gap_fill_enabled is False
