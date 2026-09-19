"""Prove this service's three copied schemas are the sibling ML service's, against a fixture and the tree.

Three contracts are held as COPIES in two repositories' trees, on purpose: `services/agri-data-service`
and `services/plantgeo-ml-service` deploy independently and neither imports the other, so a shared
package would couple two release cadences to buy nothing but an import. The cost of that decision is
that "identical" was, until this file existed, a claim made in prose in three module headers
(`warehouse/schemas/fire_risk.py`, `warehouse/schemas/weather_forecast.py`,
`warehouse/schemas/expert_labels.py`) and checkable only by a human diffing two files.

What a drift would actually do is the reason this is a test and not a review note: the writer
encodes Parquet against ITS copy and the reader decodes against THIS one, so a single field whose
nullability or position moved produces files that are rejected at read time in the other service,
with an error that names a column rather than the commit that moved it.

TWO CHECKS, AND ONLY ONE OF THEM ALWAYS RUNS
---------------------------------------------
1. **Against the checked-in fixture, always.** `tests/parquet/fixtures/ml_schema_parity/*.json` is a
   rendering of the SIBLING's objects, written by `scripts/regenerate_ml_schema_fixtures.py`. This
   comparison needs no sibling tree, so it runs inside the Docker image and in any checkout.
2. **Against the sibling's live modules, when that tree is on disk.** This is the only check that
   can notice the sibling moving; it skips with a named reason when the tree is absent.

WHY THE FIXTURE EARNS ITS BYTES -- the hole it closes had a measured instance. On 2026-09-19 the ML
service changed `fire-risk`'s `quantile` field, this service's copy did not follow, the suite went
red on `origin/main`, and `QUALITY_RECEIPT.json` kept verifying, because the receipt digests THIS
tree only (`scripts/quality_receipt.py:39`). The fixture is under `tests/`, which is a digest input
by that same line, so the ONLY way to make check 2 pass again is to regenerate a digested byte --
which stales the receipt, which makes `scripts/verify_quality_receipt.py` refuse in the image build
(`Dockerfile:48`) until a green sweep rewrites it (`scripts/check.py:685` will not write one over a
red sweep). A cross-service schema change is a two-tree change by construction after that.

WHAT NEITHER CHECK CATCHES, stated so nobody reads more into them: neither knows whether a human
read the diff. Both establish that a change cannot be SILENT, not that it was reviewed. Check 1
compares against bytes that a person chose to regenerate, so a regeneration run without reading the
sibling's diff still passes -- it is simply no longer invisible, because it moves the receipt and
shows up in the commit. See `tests/AGENTS.md`, "Copied ML schemas are pinned twice".

A FAILURE NAMES THE DECISION, NOT JUST THE DIFF: the check is bidirectional and cannot know which
copy is right, so `_divergence_message` prints both file paths, the document that owns the field
(`CopiedContract.contract_section`) and every column position that moved. The 2026-09-19 `fire-risk`
break was a correct amendment landing on one side only; reading the section answered it, diffing
the two modules would not have.

The sibling tree is not installed -- it is a path on disk in a monorepo checkout and is absent from
the agri Docker image -- so its modules are loaded from their FILE PATHS by
`regenerate_ml_schema_fixtures.load_ml_contracts`, which is the same loader the regeneration script
uses, so the fixture and this comparison can never disagree about what they are looking at.
"""

from __future__ import annotations

from itertools import zip_longest
from typing import TYPE_CHECKING, Any, Final

import pytest

from agri_data_service.warehouse.schemas.expert_labels import (
    EXPERT_LABEL_EXPORT_SCHEMA,
    EXPERT_LABEL_SORT_COLUMNS,
)
from agri_data_service.warehouse.schemas.fire_risk import FIRE_RISK_SCHEMA
from agri_data_service.warehouse.schemas.weather_forecast import WEATHER_FORECAST_SCHEMA
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

#: The single declaration of WHICH contracts are copied, where each half lives, who writes the
#: partitions and which document owns the shape. Held in the regeneration script rather than
#: restated here, so the fixture writer and this reader can never drift apart about the set.
ML_SCHEMA_FIXTURES: Final = load_scripts_module("regenerate_ml_schema_fixtures.py", "regenerate_ml_schema_fixtures")

COPIED_CONTRACTS: Final = ML_SCHEMA_FIXTURES.COPIED_CONTRACTS
CONTRACT_SLUGS: Final[tuple[str, ...]] = tuple(contract.slug for contract in COPIED_CONTRACTS)

#: This service's half of each copied contract, by the same slug the fixture and the script use.
#: `expert-labels` is a bare `pyarrow.Schema`; the other two are `ParquetStreamSchema` objects. The
#: renderer duck-types across that difference rather than branching on the class.
AGRI_CONTRACT_OBJECTS: Final[dict[str, Any]] = {
    "fire-risk": FIRE_RISK_SCHEMA,
    "weather-forecast": WEATHER_FORECAST_SCHEMA,
    "expert-labels": EXPERT_LABEL_EXPORT_SCHEMA,
}


def _agri_shape(slug: str) -> dict[str, Any]:
    """Render this service's copy through the same renderer that wrote the fixture."""
    return ML_SCHEMA_FIXTURES.render_contract(AGRI_CONTRACT_OBJECTS[slug])


def _columns_of(shape: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Render one shape's fields as the (name, type, nullable) triples a divergence is reported in."""
    return [(column["name"], column["type"], column["nullable"]) for column in shape["columns"]]


def _moved_positions(here: list[tuple[str, str, bool]], there: list[tuple[str, str, bool]]) -> list[str]:
    """Name every column POSITION at which two renderings disagree; Parquet decode is positional."""
    return [
        f"  position {index}: {left!r} vs {right!r}"
        for index, (left, right) in enumerate(zip_longest(here, there))
        if left != right
    ]


def _divergence_message(contract: Any, agri_shape: dict[str, Any], ml_shape: dict[str, Any]) -> str:
    """Name the stream, both copies, the document that owns the shape, and every position that moved."""
    moved = _moved_positions(_columns_of(agri_shape), _columns_of(ml_shape))
    return "\n".join(
        [
            f"the two {contract.slug!r} schema copies diverged. {contract.written_by} WRITES these partitions",
            "and the other side READS them, so the copy to change is whichever one disagrees with the",
            "contract -- not simply the older one. Read the section before editing either file:",
            f"  contract: {contract.contract_section}",
            f"  agri: {contract.agri_path}",
            f"  ml  : {contract.ml_path}",
            *(moved or ["  (the columns agree; the divergence is in sort order, codec or name)"]),
        ]
    )


def _fixture_divergence_message(contract: Any, agri_shape: dict[str, Any], fixture_shape: dict[str, Any]) -> str:
    """Say which copy moved, and name the ONE command that may update the fixture."""
    moved = _moved_positions(_columns_of(agri_shape), _columns_of(fixture_shape))
    return "\n".join(
        [
            f"this service's {contract.slug!r} copy no longer matches the checked-in golden fixture of the",
            f"ML service's schema ({ML_SCHEMA_FIXTURES.fixture_path(contract.slug).name}).",
            "Either this copy moved and must be put back, or the ML service moved and this copy must",
            "follow it. The fixture is NEVER hand-edited: regenerate it against the sibling tree with",
            f"  {ML_SCHEMA_FIXTURES.REGENERATION_COMMAND}",
            "and expect that to stale QUALITY_RECEIPT.json, which is the point of it.",
            f"  contract: {contract.contract_section}",
            f"  agri: {contract.agri_path}",
            f"  ml  : {contract.ml_path}",
            *(moved or ["  (the columns agree; the divergence is in sort order, codec or name)"]),
        ]
    )


# ---------------------------------------------------------------------------------------------
# Check 1 -- against the checked-in fixture. No sibling tree required; runs everywhere.
# ---------------------------------------------------------------------------------------------


def test_every_copied_contract_has_a_fixture_and_an_agri_object_with_nothing_orphaned() -> None:
    """A contract with no fixture is unguarded, and a fixture with no contract guards nothing."""
    assert set(AGRI_CONTRACT_OBJECTS) == set(CONTRACT_SLUGS), (
        "every copied contract needs this service's half named here, and nothing else belongs in it"
    )
    on_disk = {path.stem for path in ML_SCHEMA_FIXTURES.FIXTURE_DIRECTORY.glob("*.json")}
    assert on_disk == set(CONTRACT_SLUGS), (
        f"the fixture directory holds {sorted(on_disk)} but the copied contracts are "
        f"{sorted(CONTRACT_SLUGS)}; regenerate with {ML_SCHEMA_FIXTURES.REGENERATION_COMMAND}"
    )


@pytest.mark.parametrize("slug", CONTRACT_SLUGS)
def test_the_agri_copy_is_the_checked_in_fixture_so_a_cross_service_drift_cannot_be_silent(slug: str) -> None:
    """The always-on half: no sibling tree needed, so this runs in the image the receipt certifies."""
    contract = ML_SCHEMA_FIXTURES.contract_by_slug(slug)
    fixture_shape = ML_SCHEMA_FIXTURES.read_fixture(slug)["shape"]
    agri_shape = _agri_shape(slug)

    assert agri_shape == fixture_shape, _fixture_divergence_message(contract, agri_shape, fixture_shape)


def test_the_expert_label_sort_key_is_non_null_in_the_fixture_so_the_exported_order_is_total() -> None:
    """The export sorts by `label_key`; a nullable sort key is not a total order, and the ML reader
    cannot reproduce the order without the column. Asserted against the fixture rather than the
    sibling module, so it holds in a checkout that has no sibling tree.
    """
    assert EXPERT_LABEL_SORT_COLUMNS == ("label_key",)
    pinned = ML_SCHEMA_FIXTURES.read_fixture("expert-labels")["shape"]["columns"]
    columns = {column["name"]: column for column in pinned}
    for name in EXPERT_LABEL_SORT_COLUMNS:
        assert name in columns, f"the ML service's pinned shape has no {name!r} column to sort by"
        assert not columns[name]["nullable"], "a nullable sort key is not a total order"


def test_a_fixture_from_an_older_renderer_is_refused_rather_than_compared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A version the renderer no longer writes describes a different shape; comparing it would lie."""
    monkeypatch.setattr(ML_SCHEMA_FIXTURES, "FIXTURE_DIRECTORY", tmp_path)
    ML_SCHEMA_FIXTURES.write_fixture("fire-risk", {"contract": "fire-risk", "fixture_version": 0, "shape": {}})

    with pytest.raises(ML_SCHEMA_FIXTURES.FixtureError) as refusal:
        ML_SCHEMA_FIXTURES.read_fixture("fire-risk")

    assert "fixture_version" in str(refusal.value)
    assert ML_SCHEMA_FIXTURES.REGENERATION_COMMAND in str(refusal.value), (
        "a refusal that does not name the regeneration command leaves the next person guessing"
    )


def test_the_regeneration_refuses_without_the_sibling_tree_so_no_fixture_is_blessed_from_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The load-bearing refusal: rendering a fixture from this service's own copy would agree with
    itself by construction and bless exactly the drift the fixture exists to catch.
    """
    monkeypatch.setattr(ML_SCHEMA_FIXTURES, "ML_SERVICE_PACKAGE", tmp_path / "absent" / "plantgeo_ml_service")

    with pytest.raises(ML_SCHEMA_FIXTURES.SiblingTreeAbsentError) as refusal:
        ML_SCHEMA_FIXTURES.regenerate()

    assert "no plantgeo-ml-service tree" in str(refusal.value)
    assert ML_SCHEMA_FIXTURES.main([]) == ML_SCHEMA_FIXTURES.SIBLING_ABSENT_EXIT_CODE, (
        "refusing must be an exit code, not just an exception"
    )


# ---------------------------------------------------------------------------------------------
# Check 2 -- against the sibling's live modules. Skips with a named reason when that tree is absent.
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ml_schemas() -> dict[str, Any]:
    """Load the sibling's three contracts from disk, keyed by slug, or skip with the reason.

    Returns rather than yields, so no test body ever executes inside the `except ImportError` below:
    a test that happened to raise `ImportError` would otherwise be converted into a skip. The
    loader's own `finally` unhooks `sys.path` when the comprehension exhausts it
    (`scripts/regenerate_ml_schema_fixtures.py:274-276`), so there is nothing left to tear down.
    """
    absence = ML_SCHEMA_FIXTURES.sibling_tree_absence()
    if absence is not None:
        pytest.skip(
            f"{absence}; schema parity against the live sibling is only checkable in a monorepo "
            "checkout, never inside the agri Docker image. The fixture comparison above still ran."
        )
    try:
        return {contract.slug: obj for contract, obj in ML_SCHEMA_FIXTURES.load_ml_contracts()}
    except ImportError as error:  # pragma: no cover - a dependency skew, not a contract failure
        pytest.skip(f"the plantgeo-ml-service tree is present but does not import here: {error}")


@pytest.mark.parametrize("slug", CONTRACT_SLUGS)
def test_the_fixture_is_the_live_ml_module_so_a_stale_fixture_cannot_pass_as_current(
    ml_schemas: dict[str, Any], slug: str
) -> None:
    """The half that notices the SIBLING moving, and the only one that can. Failing here is the
    signal to regenerate; regenerating then stales the receipt, which is how the change reaches
    this service's image at all.
    """
    contract = ML_SCHEMA_FIXTURES.contract_by_slug(slug)
    fixture_shape = ML_SCHEMA_FIXTURES.read_fixture(slug)["shape"]
    live_shape = ML_SCHEMA_FIXTURES.render_contract(ml_schemas[slug])

    assert live_shape == fixture_shape, "\n".join(
        [
            f"the golden fixture of {slug!r} is stale: the ML service's module has moved since it was",
            "written. Regenerate it, then make this service's copy follow:",
            f"  {ML_SCHEMA_FIXTURES.REGENERATION_COMMAND}",
            f"  contract: {contract.contract_section}",
            f"  ml  : {contract.ml_path}",
            *(
                _moved_positions(_columns_of(live_shape), _columns_of(fixture_shape))
                or ["  (the columns agree; the divergence is in sort order, codec or name)"]
            ),
        ]
    )


def test_the_fire_risk_schema_is_the_ml_services_own_object_field_for_field(ml_schemas: dict[str, Any]) -> None:
    """`fire-risk` is written THERE and read HERE, so a moved field breaks the read, not the write."""
    live = ml_schemas["fire-risk"]
    message = _divergence_message(
        ML_SCHEMA_FIXTURES.contract_by_slug("fire-risk"),
        _agri_shape("fire-risk"),
        ML_SCHEMA_FIXTURES.render_contract(live),
    )
    assert FIRE_RISK_SCHEMA.arrow_schema.equals(live.arrow_schema), message
    assert FIRE_RISK_SCHEMA.sort_columns == live.sort_columns, message
    assert FIRE_RISK_SCHEMA.compression == live.compression, message
    assert FIRE_RISK_SCHEMA.name == live.name, message


def test_the_fire_risk_copies_agree_column_by_column_so_a_failure_names_the_field(
    ml_schemas: dict[str, Any],
) -> None:
    """`Schema.equals` answers one bool; this answers WHICH column moved, which is the actionable fact."""
    here = _agri_shape("fire-risk")
    there = ML_SCHEMA_FIXTURES.render_contract(ml_schemas["fire-risk"])

    assert _columns_of(here) == _columns_of(there), _divergence_message(
        ML_SCHEMA_FIXTURES.contract_by_slug("fire-risk"), here, there
    )


def test_the_weather_forecast_schema_is_the_ml_services_own_nineteen_columns(ml_schemas: dict[str, Any]) -> None:
    """FR-12: the provider run is written by the ML service; agri only registers the slug to serve it."""
    live = ml_schemas["weather-forecast"]
    assert WEATHER_FORECAST_SCHEMA.arrow_schema.equals(live.arrow_schema)
    assert WEATHER_FORECAST_SCHEMA.sort_columns == live.sort_columns
    assert WEATHER_FORECAST_SCHEMA.compression == live.compression
    assert WEATHER_FORECAST_SCHEMA.name == live.name


def test_the_weather_forecast_copies_agree_column_by_column_so_a_failure_names_the_field(
    ml_schemas: dict[str, Any],
) -> None:
    here = _agri_shape("weather-forecast")
    there = ML_SCHEMA_FIXTURES.render_contract(ml_schemas["weather-forecast"])

    assert _columns_of(here) == _columns_of(there), _divergence_message(
        ML_SCHEMA_FIXTURES.contract_by_slug("weather-forecast"), here, there
    )


def test_the_expert_label_export_writes_the_shape_the_ml_service_pins_for_reading(
    ml_schemas: dict[str, Any],
) -> None:
    """The export direction is agri -> ML, and that reader refuses a file whose schema is not this one.

    It compares with `schema.remove_metadata().equals(...)`, so the comparison here is metadata-free
    for the same reason: the pandas metadata pyarrow would otherwise attach carries a library
    version and would make two identical exports differ.
    """
    assert EXPERT_LABEL_EXPORT_SCHEMA.remove_metadata().equals(ml_schemas["expert-labels"].remove_metadata())


def test_the_expert_label_sort_column_exists_on_both_sides_so_the_exported_order_is_readable(
    ml_schemas: dict[str, Any],
) -> None:
    """The export sorts by `label_key`; a reader cannot reproduce that order without the column."""
    live = ml_schemas["expert-labels"]
    assert EXPERT_LABEL_SORT_COLUMNS == ("label_key",)
    for column in EXPERT_LABEL_SORT_COLUMNS:
        assert column in live.names
        assert not live.field(column).nullable, "a nullable sort key is not a total order"


def test_the_parity_check_runs_rather_than_skips_in_a_monorepo_checkout(ml_schemas: dict[str, Any]) -> None:
    """A skip-shaped parity test proves nothing; reaching this line is what makes the others evidence."""
    assert ML_SCHEMA_FIXTURES.ML_SERVICE_PACKAGE.is_dir()
    assert ml_schemas["fire-risk"].name == "fire-risk"
