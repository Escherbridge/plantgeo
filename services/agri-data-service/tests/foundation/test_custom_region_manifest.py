"""Custom manifest selection, validation and content-aware identity."""

from __future__ import annotations

import json
import traceback
from importlib import resources

import pytest
from pydantic import ValidationError

from agri_data_service.foundation.region import load_region
from agri_data_service.foundation.region.layer_availability import PLATFORM_LAYER_SLUGS
from agri_data_service.foundation.region.manifest import (
    MAX_ANALYSIS_LATTICE_CELLS,
    MAX_REGION_MANIFEST_BYTES,
    REGION_ENV_VAR,
    REGION_MANIFEST_ENV_VAR,
    AnalysisLattice,
)

CUSTOM_SLUG = "custom-colorado"


def _document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "slug": CUSTOM_SLUG,
        "display_name": "Colorado test region",
        "envelope": {"west": -108, "south": 38, "east": -106, "north": 40},
        "default_camera_envelope": {"west": -108, "south": 38, "east": -106, "north": 40},
        "sub_envelopes": {},
        "crs": 4326,
        "lattice_pitch_degrees": 0.01,
        "lattice_origin_rule": "floor_to_cell_origin",
        "timezone": "America/Denver",
        "iso_country_codes": ["US"],
        "admin_codes": ["US-CO"],
        "platform_layers": list(PLATFORM_LAYER_SLUGS),
        "enabled_layers": [{"layer_slug": "water-gauges", "source_slug": "usgs_water_data", "coverage": "regional"}],
        "analysis_lattices": {},
    }
    document.update(updates)
    return document


@pytest.fixture(autouse=True)
def _clean_region_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REGION_ENV_VAR, raising=False)
    monkeypatch.delenv(REGION_MANIFEST_ENV_VAR, raising=False)


def _configure(monkeypatch: pytest.MonkeyPatch, document: dict[str, object]) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, CUSTOM_SLUG)
    monkeypatch.setenv(REGION_MANIFEST_ENV_VAR, json.dumps(document))


def test_custom_manifest_uses_the_canonical_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, _document())
    region = load_region()
    assert region.slug == CUSTOM_SLUG
    assert region.admin_codes == ("US-CO",)
    assert region.timezone == "America/Denver"
    assert region is load_region(CUSTOM_SLUG)
    assert region is load_region()


def test_same_slug_changed_content_never_reuses_old_region(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, _document())
    first = load_region()
    shifted = {"west": -107, "south": 38, "east": -105, "north": 40}
    _configure(
        monkeypatch, _document(display_name="Revised deployment", envelope=shifted, default_camera_envelope=shifted)
    )
    revised = load_region()
    assert revised is not first
    assert revised.display_name == "Revised deployment"
    assert revised.envelope != first.envelope
    assert first.display_name == "Colorado test region"


def test_packaged_defaults_and_explicit_reference_survive_custom_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    pilot = load_region()
    assert pilot.slug == "pnw"
    _configure(monkeypatch, _document())
    assert load_region("pnw") is pilot
    assert load_region("kenya-highlands").slug == "kenya-highlands"
    monkeypatch.setenv(REGION_MANIFEST_ENV_VAR, "malformed")
    assert load_region("pnw") is pilot
    with pytest.raises(ValueError, match="must be valid JSON"):
        load_region()


def test_custom_selection_requires_matching_deployment_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_MANIFEST_ENV_VAR, json.dumps(_document()))
    with pytest.raises(ValueError, match="requires PLANTGEO_REGION"):
        load_region(CUSTOM_SLUG)
    monkeypatch.setenv(REGION_ENV_VAR, "different-region")
    with pytest.raises(ValueError, match="must match"):
        load_region()
    monkeypatch.setenv(REGION_ENV_VAR, CUSTOM_SLUG)
    with pytest.raises(ValueError, match="must match"):
        load_region("different-region")


@pytest.mark.parametrize("slug", ["pnw", "kenya-highlands"])
def test_custom_manifest_cannot_shadow_packaged_region(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    _configure(monkeypatch, _document(slug=slug))
    monkeypatch.setenv(REGION_ENV_VAR, slug)
    with pytest.raises(ValueError, match="must not shadow"):
        load_region()


@pytest.mark.parametrize(
    "payload", [" ", "{", "[]", "null", "42", '{"slug": "one", "slug": "two"}', '{"nested": {"key": 1, "key": 2}}']
)
def test_malformed_or_ambiguous_json_never_falls_back(monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, CUSTOM_SLUG)
    monkeypatch.setenv(REGION_MANIFEST_ENV_VAR, payload)
    with pytest.raises(ValueError, match="region manifest"):
        load_region()


def test_empty_build_argument_preserves_packaged_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_MANIFEST_ENV_VAR, "")
    assert load_region().slug == "pnw"
    monkeypatch.setenv(REGION_ENV_VAR, "kenya-highlands")
    assert load_region().slug == "kenya-highlands"


def test_byte_limit_counts_utf8_not_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REGION_ENV_VAR, CUSTOM_SLUG)
    monkeypatch.setenv(REGION_MANIFEST_ENV_VAR, "\u20ac" * (MAX_REGION_MANIFEST_BYTES // 3 + 1))
    with pytest.raises(ValueError, match="exceeds"):
        load_region()


@pytest.mark.parametrize("slug", ["../outside", "/absolute", "a_b", "UPPER", "a--b", "", "a" * 65])
def test_unsafe_slug_is_rejected(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    _configure(monkeypatch, _document(slug=slug))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


@pytest.mark.parametrize("timezone", ["Invalid/Nowhere", "../UTC", "", "/etc/localtime"])
def test_invalid_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch, timezone: str) -> None:
    _configure(monkeypatch, _document(timezone=timezone))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iso_country_codes", ["US", "US"]),
        ("iso_country_codes", []),
        ("iso_country_codes", ["us"]),
        ("admin_codes", ["US-CO", "US-CO"]),
        ("admin_codes", ["CA-BC"]),
        ("admin_codes", ["US/CO"]),
        ("platform_layers", ["water-gauges", "water-gauges"]),
        ("lattice_pitch_degrees", float("nan")),
        ("lattice_pitch_degrees", float("inf")),
    ],
)
def test_inconsistent_scope_is_rejected(monkeypatch: pytest.MonkeyPatch, field: str, value: object) -> None:
    _configure(monkeypatch, _document(**{field: value}))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


def test_duplicate_layer_binding_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = [
        {"layer_slug": "water-gauges", "source_slug": source, "coverage": "regional"}
        for source in ("usgs_water_data", "another_source")
    ]
    _configure(monkeypatch, _document(enabled_layers=bindings))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


@pytest.mark.parametrize(
    ("ordinate", "value"),
    [
        ("west", -181),
        ("east", 181),
        ("south", -91),
        ("north", 91),
        ("west", float("nan")),
        ("east", float("inf")),
        ("west", -105),
        ("south", 41),
        ("east", "-106"),
        ("north", True),
    ],
)
def test_invalid_coordinates_are_rejected(monkeypatch: pytest.MonkeyPatch, ordinate: str, value: object) -> None:
    envelope: dict[str, object] = {"west": -108, "south": 38, "east": -106, "north": 40, ordinate: value}
    _configure(monkeypatch, _document(envelope=envelope))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


@pytest.mark.parametrize("field", ["default_camera_envelope", "sub_envelopes", "analysis_lattices"])
def test_nested_footprints_cannot_escape_region(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    outside = {"west": -110, "south": 38, "east": -106, "north": 40}
    value: object = outside
    if field == "sub_envelopes":
        value = {"test": outside}
    elif field == "analysis_lattices":
        value = {
            "analysis-test": {
                "pitch_degrees": 0.25,
                "origin_rule": "half_step",
                "envelope": outside,
                "cell_key_prefix": "test:",
            }
        }
    _configure(monkeypatch, _document(**{field: value}))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


@pytest.mark.parametrize("location", ["value", "root-key", "sub-envelope-key", "lattice-key"])
def test_validation_errors_do_not_echo_input_keys_or_values(monkeypatch: pytest.MonkeyPatch, location: str) -> None:
    secret = "accidentally-pasted-secret-credential"
    updates: dict[str, dict[str, object]] = {
        "value": {"timezone": secret},
        "root-key": {secret: "unexpected"},
        "sub-envelope-key": {"sub_envelopes": {secret: {"west": "invalid"}}},
        "lattice-key": {"analysis_lattices": {secret: {"pitch_degrees": "invalid"}}},
    }
    _configure(monkeypatch, _document(**updates[location]))
    with pytest.raises(ValueError, match="invalid custom region manifest") as error:
        load_region()
    assert secret not in str(error.value)
    assert secret not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("crs", ["4326", True, False, 4326.5])
def test_crs_refuses_coercion(monkeypatch: pytest.MonkeyPatch, crs: object) -> None:
    _configure(monkeypatch, _document(crs=crs))
    with pytest.raises(ValueError, match="invalid custom region manifest"):
        load_region()


@pytest.mark.parametrize("crs", [4326, None])
def test_crs_accepts_integer_or_null(monkeypatch: pytest.MonkeyPatch, crs: int | None) -> None:
    _configure(monkeypatch, _document(crs=crs))
    assert load_region().crs == crs


def test_web_and_service_load_the_same_canonical_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json")
    document = json.loads(fixture.read_text(encoding="utf-8"))
    _configure(monkeypatch, document)
    region = load_region()
    assert region.slug == document["slug"]
    assert region.admin_codes == ("US-CO",)
    assert region.sub_envelopes["test_scope"].west == document["sub_envelopes"]["test_scope"]["west"]
    cells = region.analysis_lattices["analysis-0p25"].cells()
    expected_cell_count = 64
    assert len(cells) == expected_cell_count
    assert cells[0].cell_key == "test-region:38.1250:-107.8750"


def test_custom_vocabulary_cannot_hide_unsupported_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, _document(platform_layers=["water-gauges"]))
    with pytest.raises(ValueError, match="platform"):
        load_region()


def _lattice(pitch: float, west: float, south: float, east: float, north: float) -> AnalysisLattice:
    return AnalysisLattice.model_validate(
        {
            "pitch_degrees": pitch,
            "origin_rule": "half_step",
            "envelope": {"west": west, "south": south, "east": east, "north": north},
            "cell_key_prefix": "test:",
        }
    )


@pytest.mark.parametrize(("pitch", "origin", "end"), [(0.0002, 0, 0.0004), (0.0001, 0.00005, 0.00025)])
def test_exact_four_decimal_lattice_keys_are_unique(pitch: float, origin: float, end: float) -> None:
    cells = _lattice(pitch, origin, origin, end, end).cells()
    expected_count = 4
    assert len(cells) == len({cell.cell_key for cell in cells}) == expected_count
    assert cells[0].cell_key == "test:0.0001:0.0001"


@pytest.mark.parametrize(
    ("pitch", "west", "south", "east", "north"),
    [
        (0.00001, 0, 0, 0.0001, 0.0001),
        (0.0001, 0, 0, 0.0002, 0.0002),
        (0.25, 0, 0, 0.3, 0.5),
        (0.0002, 1e-100, 0, 0.0004, 0.0004),
        (1e308, 0, 0, 1, 1),
    ],
)
def test_unsafe_lattice_identity_is_refused(pitch: float, west: float, south: float, east: float, north: float) -> None:
    with pytest.raises(ValidationError):
        _lattice(pitch, west, south, east, north)


def test_lattice_cell_limit_is_checked_before_enumeration(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_enumeration(_self: AnalysisLattice) -> None:
        pytest.fail("validation must not enumerate cells")

    monkeypatch.setattr(AnalysisLattice, "cells", no_enumeration)
    expected_limit = 100_000
    pitch = 0.01
    assert expected_limit == MAX_ANALYSIS_LATTICE_CELLS
    assert _lattice(pitch, 0, 0, 10, 1).pitch_degrees == pitch
    with pytest.raises(ValueError, match="100000"):
        _lattice(0.01, 0, 0, 10.01, 1)


def test_pilot_analysis_cell_identity_is_unchanged() -> None:
    cells = load_region("pnw").analysis_lattices["analysis-0p25"].cells()
    expected_cell_count = 1568
    assert len(cells) == expected_cell_count
    assert cells[0].cell_key == "sentinel2-ndvi-0p25deg:42.1250:-124.8750"
    assert cells[-1].cell_key == "sentinel2-ndvi-0p25deg:48.8750:-111.1250"
