"""Rewrite the golden fixtures of the three schemas this service copies from the sibling ML service.

Run from `services/agri-data-service`, in a monorepo checkout that holds the sibling tree:

    uv run --no-sync python scripts/regenerate_ml_schema_fixtures.py

WHY A FIXTURE EXISTS AT ALL, AND WHAT IT BUYS THAT THE PARITY TEST DID NOT
--------------------------------------------------------------------------
`tests/parquet/test_ml_schema_parity.py` compares this service's copies against the sibling's live
modules, and skips the whole comparison when the sibling tree is absent -- which it is inside this
service's Docker image. That left a hole with a measured instance: on 2026-09-19 the ML service
changed `fire-risk`'s `quantile` field, this service's copy did not follow, its suite went red on
`origin/main`, and `QUALITY_RECEIPT.json` kept verifying, because the receipt digests THIS tree only
(`scripts/quality_receipt.py:39` lists the digested directories) and the ML module is outside it.

The fixture closes that by construction rather than by policy. The digest covers `tests`
(`scripts/quality_receipt.py:39`) and excludes only build artifacts by name
(`scripts/quality_receipt.py:44-47`), so these JSON files are digested bytes. Regenerating one
therefore changes the tree digest, which makes the committed receipt stale, which makes
`python scripts/verify_quality_receipt.py` refuse in the image build (`Dockerfile:48`) until a green
sweep rewrites it (`scripts/check.py:685` refuses to write one over a red sweep). A cross-service
schema change is thus a two-tree change: the ML edit alone cannot reach a shipped image.

THIS SCRIPT REFUSES TO RUN WITHOUT THE SIBLING TREE, and that refusal is the point, not a
convenience check. The fixtures are rendered from the ML modules -- never from this service's own
copies -- because a fixture regenerated from the reader would agree with the reader by construction
and would bless any drift it was meant to catch. With no sibling tree there is nothing to render
from, so the only safe answer is to stop.

WHAT IS RENDERED, AND WHAT IS DELIBERATELY NOT
-----------------------------------------------
`render_contract` keeps each field's name, Arrow type and nullability, in order, plus the stream
name, sort columns and compression codec where the contract object carries them. Schema-level
metadata is not rendered: pyarrow attaches a pandas block carrying a library version, so two
identical exports would differ by the version that wrote them -- the same reason the expert-label
comparison uses `remove_metadata()` (`tests/parquet/test_ml_schema_parity.py`, the expert-label
export test). Field ORDER is kept because Parquet decode is positional.

Rationale and the limits of what these two guards catch: `tests/AGENTS.md`, section
"Copied ML schemas are pinned twice".
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

SERVICE_ROOT: Final = Path(__file__).resolve().parent.parent
MONOREPO_ROOT: Final = SERVICE_ROOT.parent.parent
ML_SERVICE_SRC: Final = MONOREPO_ROOT / "services" / "plantgeo-ml-service" / "src"
ML_SERVICE_PACKAGE: Final = ML_SERVICE_SRC / "plantgeo_ml_service"

#: Where the golden fixtures live. Under `tests/`, so they are digest inputs
#: (`scripts/quality_receipt.py:39`); that is what makes a regeneration stale the receipt.
FIXTURE_DIRECTORY: Final = SERVICE_ROOT / "tests" / "parquet" / "fixtures" / "ml_schema_parity"

#: Quoted in this script's refusals and in the parity test's fixture-mismatch message, so the next
#: person is told how to update a fixture deliberately instead of guessing at it.
REGENERATION_COMMAND: Final = "uv run --no-sync python scripts/regenerate_ml_schema_fixtures.py"

#: Bumped when `render_contract` changes what it renders, so a fixture written by an older shape is
#: refused outright rather than compared field by field against a shape it never described.
FIXTURE_VERSION: Final = 1


class SiblingTreeAbsentError(RuntimeError):
    """There is no plantgeo-ml-service tree to render a fixture from, so there is nothing to render."""


class FixtureError(RuntimeError):
    """A golden fixture is absent, malformed, or describes a shape this renderer no longer writes."""


@dataclass(frozen=True, slots=True)
class CopiedContract:
    """One schema held as a copy in both service trees, and where each half of it lives."""

    #: Names the fixture file and the test id. Two of the three are lane slugs; `expert-labels` is
    #: not a lane (`src/agri_data_service/warehouse/schemas/expert_labels.py` says so in its header).
    slug: str
    #: Dotted name of the sibling module, which doubles as its path under the ML package root.
    ml_module: str
    #: The contract object inside that module.
    ml_symbol: str
    #: Both halves by repository-relative path, so a divergence message names the two files to open.
    agri_path: str
    ml_path: str
    #: Which service WRITES these partitions, by directory name. The copy to change on a divergence
    #: is whichever one disagrees with `contract_section` -- not simply the older one -- but knowing
    #: who writes is what tells a reader whether a drift breaks reads or breaks writes.
    written_by: str
    #: The document that OWNS this shape, so a divergence points at the decision rather than at two
    #: files. Per contract, not shared: only `fire-risk` is governed by the lane styleguide's
    #: forecast-provenance section; the other two were settled in the ML service track's spec.
    contract_section: str


COPIED_CONTRACTS: Final[tuple[CopiedContract, ...]] = (
    CopiedContract(
        slug="fire-risk",
        ml_module="plantgeo_ml_service.warehouse.streams",
        ml_symbol="FIRE_RISK_SCHEMA",
        agri_path="services/agri-data-service/src/agri_data_service/warehouse/schemas/fire_risk.py",
        ml_path="services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/streams.py",
        written_by="services/plantgeo-ml-service",
        contract_section="conductor/code_styleguides/layer-lanes.md section 3 (forecast provenance)",
    ),
    CopiedContract(
        slug="weather-forecast",
        ml_module="plantgeo_ml_service.warehouse.weather_forecast",
        ml_symbol="WEATHER_FORECAST_SCHEMA",
        agri_path="services/agri-data-service/src/agri_data_service/warehouse/schemas/weather_forecast.py",
        ml_path="services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/weather_forecast.py",
        written_by="services/plantgeo-ml-service",
        contract_section="conductor/tracks/plantgeo_ml_service_20260918/spec.md FR-12 (the provider NWP lane)",
    ),
    CopiedContract(
        slug="expert-labels",
        ml_module="plantgeo_ml_service.pipeline.expert_labels",
        ml_symbol="EXPERT_LABEL_SCHEMA",
        agri_path="services/agri-data-service/src/agri_data_service/warehouse/schemas/expert_labels.py",
        ml_path="services/plantgeo-ml-service/src/plantgeo_ml_service/pipeline/expert_labels.py",
        written_by="services/agri-data-service",
        contract_section="conductor/tracks/plantgeo_ml_service_20260918/spec.md FR-7 (the exported label plane)",
    ),
)


def contract_by_slug(slug: str) -> CopiedContract:
    """Return the one copied contract this slug names, refusing an unknown slug by listing the known."""
    for contract in COPIED_CONTRACTS:
        if contract.slug == slug:
            return contract
    known = ", ".join(sorted(candidate.slug for candidate in COPIED_CONTRACTS))
    raise KeyError(f"no copied contract named {slug!r}; the copied contracts are: {known}")


def fixture_path(slug: str) -> Path:
    """Return the file holding one contract's golden fixture."""
    return FIXTURE_DIRECTORY / f"{slug}.json"


def render_contract(contract_object: Any) -> dict[str, Any]:
    """Render one schema object as the comparable shape: ordered fields, plus grain and codec if any.

    Duck-typed rather than branched on a class, because the two sides are defined in trees that
    never import each other: `fire-risk` and `weather-forecast` are `ParquetStreamSchema` objects
    carrying an `arrow_schema`, while `expert-labels` is a bare `pyarrow.Schema` with no stream
    name, no sort columns and no codec of its own -- those three render as `null` for it.
    """
    arrow_schema = getattr(contract_object, "arrow_schema", contract_object)
    sort_columns = getattr(contract_object, "sort_columns", None)
    return {
        "columns": [
            {"name": field.name, "type": str(field.type), "nullable": bool(field.nullable)} for field in arrow_schema
        ],
        "compression": getattr(contract_object, "compression", None),
        "sort_columns": None if sort_columns is None else list(sort_columns),
        "stream_name": getattr(contract_object, "name", None),
    }


def build_fixture(contract: CopiedContract, contract_object: Any) -> dict[str, Any]:
    """Wrap one rendered shape in the provenance a reader needs to know what it is looking at."""
    return {
        "agri_module": contract.agri_path,
        "contract": contract.slug,
        "contract_section": contract.contract_section,
        "fixture_version": FIXTURE_VERSION,
        "ml_module": contract.ml_path,
        "ml_symbol": f"{contract.ml_module}.{contract.ml_symbol}",
        "regenerate_with": REGENERATION_COMMAND,
        "shape": render_contract(contract_object),
        "written_by": contract.written_by,
    }


def read_fixture(slug: str) -> dict[str, Any]:
    """Read one golden fixture, refusing anything that is not a current-version object for this slug.

    Every refusal names the regeneration command, because a fixture is never hand-editable: it
    describes bytes in another tree, and typing the shape you wish were there is exactly the silent
    blessing this file exists to prevent.
    """
    path = fixture_path(slug)
    if not path.is_file():
        raise FixtureError(
            f"no golden fixture at {path}; every copied contract must have one. Regenerate in a "
            f"monorepo checkout: {REGENERATION_COMMAND}"
        )
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise FixtureError(f"{path} is not a JSON object; regenerate it: {REGENERATION_COMMAND}")
    version = parsed.get("fixture_version")
    if version != FIXTURE_VERSION:
        raise FixtureError(
            f"{path} declares fixture_version {version!r}, this renderer writes {FIXTURE_VERSION}; the two "
            f"describe different shapes and are not comparable. Regenerate it: {REGENERATION_COMMAND}"
        )
    if parsed.get("contract") != slug:
        raise FixtureError(
            f"{path} carries contract {parsed.get('contract')!r}, not {slug!r}; regenerate it: {REGENERATION_COMMAND}"
        )
    return parsed


def render_fixture_bytes(fixture: dict[str, Any]) -> str:
    """Render one fixture as sorted, newline-terminated JSON, written LF-only by `write_fixture`."""
    return json.dumps(fixture, indent=2, sort_keys=True) + "\n"


def write_fixture(slug: str, fixture: dict[str, Any]) -> bool:
    """Write one fixture with LF endings; return whether the bytes on disk actually changed.

    LF explicitly, because the receipt's digest normalizes CRLF before hashing
    (`scripts/quality_receipt.py:54-56`) while git's checkout on Windows does not, and a fixture
    that round-trips differently in two checkouts would make the staleness signal look like noise.
    """
    path = fixture_path(slug)
    rendered = render_fixture_bytes(fixture)
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return previous != rendered


def _ml_module_path(dotted: str) -> Path:
    """Return the file one dotted ML module name lives in, under the sibling package root."""
    return ML_SERVICE_PACKAGE.joinpath(*dotted.split(".")[1:]).with_suffix(".py")


def sibling_tree_absence() -> str | None:
    """Return why the sibling tree cannot be read, or ``None`` when every copied module is present."""
    if not ML_SERVICE_PACKAGE.is_dir():
        return (
            f"no plantgeo-ml-service tree at {ML_SERVICE_PACKAGE}. The fixtures are rendered from the "
            "sibling's own modules and never from this service's copies, so with no sibling there is "
            "nothing to render from"
        )
    module_paths = [_ml_module_path(contract.ml_module) for contract in COPIED_CONTRACTS]
    absent = sorted(str(path) for path in module_paths if not path.is_file())
    if absent:
        return f"the plantgeo-ml-service tree is present but ships none of {absent}"
    return None


def load_ml_contracts() -> Iterator[tuple[CopiedContract, Any]]:
    """Yield each copied contract paired with the sibling's live object, refusing without that tree.

    The sibling is a path on disk in a monorepo checkout, not an installed dependency, so its
    package ROOT goes on `sys.path` for the duration and comes off again afterwards: the ML modules
    import each other by absolute dotted name, which `spec_from_file_location` alone cannot satisfy.
    The same construction the parity test uses.
    """
    absence = sibling_tree_absence()
    if absence is not None:
        raise SiblingTreeAbsentError(absence)
    source_root = str(ML_SERVICE_SRC)
    added = source_root not in sys.path
    if added:
        sys.path.insert(0, source_root)
    try:
        for contract in COPIED_CONTRACTS:
            module = importlib.import_module(contract.ml_module)
            yield contract, getattr(module, contract.ml_symbol)
    finally:
        if added and source_root in sys.path:
            sys.path.remove(source_root)


def regenerate() -> list[str]:
    """Rewrite every fixture from the sibling's live modules; return the slugs whose bytes moved."""
    rewritten: list[str] = []
    for contract, contract_object in load_ml_contracts():
        if write_fixture(contract.slug, build_fixture(contract, contract_object)):
            rewritten.append(contract.slug)
    return rewritten


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate every golden fixture, or refuse and say why."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    try:
        rewritten = regenerate()
    except SiblingTreeAbsentError as error:
        print(f"Refusing to regenerate the ML schema fixtures: {error}.")
        print("A fixture regenerated from nothing would bless whatever drift it was meant to catch.")
        return 2
    for contract in COPIED_CONTRACTS:
        state = "rewritten" if contract.slug in rewritten else "unchanged"
        print(f"{contract.slug:<18} {state:<10} {fixture_path(contract.slug).relative_to(SERVICE_ROOT).as_posix()}")
    if rewritten:
        print(
            "\nThese are digest inputs, so the committed QUALITY_RECEIPT.json is now stale. "
            "Update this service's copies to match, then re-run a green sweep with "
            "`uv run --no-sync python scripts/check.py --write-receipt`."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
