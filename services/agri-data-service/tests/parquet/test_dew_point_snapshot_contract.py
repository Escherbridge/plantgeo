"""Contract tests for the dew-point snapshot lane."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from agri_data_service.warehouse.parquet.schema import (
    SIGNAL_PLANE_SCHEMA,
    SIGNAL_PLANE_TIER_DERIVATION,
    get_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import tier_derivation, validate_derivation_against_schema

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dew_point_snapshot_breakdown.py"
STREAM = "climate-field-dew-point"
SNAPSHOT_ID = "prod-20260826-full-signal-v1"
MANIFEST_SHA256 = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"


def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=SCRIPT.name)


def _assignment(name: str) -> ast.expr:
    for node in _tree().body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            assert node.value is not None
            return node.value
    raise AssertionError(f"missing top-level assignment {name}")


def test_dew_point_schema_is_the_frozen_signal_product_contract() -> None:
    schema = get_stream_schema(STREAM)

    assert schema == replace(SIGNAL_PLANE_SCHEMA, name=STREAM)
    assert schema.arrow_schema is SIGNAL_PLANE_SCHEMA.arrow_schema
    assert tier_derivation(STREAM) == replace(SIGNAL_PLANE_TIER_DERIVATION, stream=STREAM)
    assert validate_derivation_against_schema(STREAM) == ()


def test_dew_point_builder_pins_source_and_product_contract() -> None:
    values = {
        node.target.id: ast.literal_eval(node.value)
        for node in _tree().body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    product_calls = [
        node
        for node in ast.walk(_assignment("PRODUCTS"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ProductContract"
    ]

    assert values["SNAPSHOT_ID"] == SNAPSHOT_ID
    assert values["REQUIRED_MANIFEST_SHA256"] == MANIFEST_SHA256
    assert len(product_calls) == 1
    assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in product_calls[0].keywords} == {
        "product_id": "t2mdew",
        "stream": STREAM,
        "source_parameter": "T2MDEW",
        "signal_name": "dew_point_temperature",
    }


def test_dew_point_completion_marker_is_the_final_write() -> None:
    build_product = next(
        node for node in _tree().body if isinstance(node, ast.FunctionDef) and node.name == "_build_product"
    )
    writes = sorted(
        (
            node
            for node in ast.walk(build_product)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_put_immutable"
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    targets = [next(keyword.value for keyword in call.keywords if keyword.arg == "relative_key") for call in writes]

    assert [ast.unparse(target) for target in targets[-2:]] == ["manifest_key", "complete_key"]


def test_dew_point_output_is_confined_to_its_snapshot_root() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'return f"layer={product.stream}/snapshot={SNAPSHOT_ID}/"' in source
    assert "layer=signal" not in source
    assert "postgres" not in source.lower()


def test_dew_point_verifier_requires_the_exact_bounded_output_inventory() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "MAX_OUTPUT_OBJECT_COUNT: Final = 1_000" in source
    assert "store._backend.list_objects(store.key_for(root))" in source
    assert "if actual_keys != expected_keys:" in source
