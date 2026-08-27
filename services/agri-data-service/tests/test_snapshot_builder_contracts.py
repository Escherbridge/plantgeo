from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SNAPSHOT_ID = "prod-20260826-full-signal-v1"
MANIFEST_SHA256 = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"


def _tree(script: str) -> ast.Module:
    return ast.parse((SCRIPTS / script).read_text(encoding="utf-8"), filename=script)


def _assignment_value(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    raise AssertionError(f"missing top-level assignment {name}")


def _string_constants(script: str) -> dict[str, str]:
    tree = _tree(script)
    values: dict[str, str] = {}

    def resolve(node: ast.expr) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return resolve(node.left) + resolve(node.right)
        if isinstance(node, ast.JoinedStr):
            chunks: list[str] = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    chunks.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    chunks.append(resolve(value.value))
                else:
                    raise KeyError
            return "".join(chunks)
        raise KeyError

    for node in tree.body:
        target: ast.Name | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target
            value = node.value
        if target is None or value is None:
            continue
        try:
            values[target.id] = resolve(value)
        except KeyError:
            continue
    return values


@pytest.mark.parametrize(
    ("script", "constant", "expected"),
    [
        ("canonical_signal_snapshot.py", "DEFAULT_RAW_PREFIX", "raw-canonical/signal-observation"),
        (
            "vpd_snapshot_breakdown.py",
            "SNAPSHOT_PREFIX",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}/",
        ),
        (
            "air_temperature_snapshot_breakdown.py",
            "SNAPSHOT_PREFIX",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}/",
        ),
        (
            "build_relative_humidity_from_canonical_snapshot.py",
            "SOURCE_ROOT",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}",
        ),
        (
            "build_shortwave_radiation_from_canonical_snapshot.py",
            "SOURCE_ROOT",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}",
        ),
        (
            "build_soil_moisture_from_canonical_snapshot.py",
            "SOURCE_ROOT",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}",
        ),
        (
            "census_signal_snapshot.py",
            "SNAPSHOT_ROOT",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}",
        ),
        (
            "soil_temperature_snapshot_breakdown.py",
            "DEFAULT_INPUT_PREFIX",
            "raw-canonical/signal-observation",
        ),
        (
            "breakdown_wind_speed_snapshot.py",
            "SOURCE_PREFIX",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}",
        ),
        (
            "build_precipitation_from_canonical_snapshot.py",
            "SOURCE_ROOT",
            f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}",
        ),
    ],
)
def test_source_prefixes_are_exactly_contained_in_raw_canonical(
    script: str,
    constant: str,
    expected: str,
) -> None:
    prefix = _string_constants(script)[constant]
    assert prefix == expected
    assert not prefix.startswith("/")
    assert ".." not in prefix.split("/")
    assert "layer=" not in prefix


@pytest.mark.parametrize(
    ("script", "constant"),
    [
        ("vpd_snapshot_breakdown.py", "REQUIRED_MANIFEST_SHA256"),
        ("air_temperature_snapshot_breakdown.py", "REQUIRED_MANIFEST_SHA256"),
        ("build_relative_humidity_from_canonical_snapshot.py", "SOURCE_MANIFEST_SHA256"),
        ("build_shortwave_radiation_from_canonical_snapshot.py", "SOURCE_MANIFEST_SHA256"),
        ("build_soil_moisture_from_canonical_snapshot.py", "SOURCE_MANIFEST_SHA256"),
        ("census_signal_snapshot.py", "MANIFEST_SHA256"),
        ("soil_temperature_snapshot_breakdown.py", "DEFAULT_INPUT_MANIFEST_SHA256"),
        ("breakdown_wind_speed_snapshot.py", "SOURCE_MANIFEST_SHA256"),
        ("build_precipitation_from_canonical_snapshot.py", "SOURCE_MANIFEST_SHA256"),
    ],
)
def test_breakdowns_pin_the_completed_source_manifest(script: str, constant: str) -> None:
    assert _string_constants(script)[constant] == MANIFEST_SHA256


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def _put_target(call: ast.Call) -> str | None:
    function_name: str | None = None
    if isinstance(call.func, ast.Name):
        function_name = call.func.id
    elif isinstance(call.func, ast.Attribute):
        function_name = call.func.attr
    if function_name not in {"_put_immutable", "put_immutable"}:
        return None
    for keyword in call.keywords:
        if keyword.arg == "relative_key":
            return ast.unparse(keyword.value)
    return ast.unparse(call.args[0]) if call.args else None


@pytest.mark.parametrize(
    ("script", "function_name", "manifest_target", "completion_target"),
    [
        ("vpd_snapshot_breakdown.py", "_build_product", "manifest_key", "complete_key"),
        ("air_temperature_snapshot_breakdown.py", "_build_product", "manifest_key", "complete_key"),
        (
            "build_relative_humidity_from_canonical_snapshot.py",
            "_finalize",
            "DESTINATION_MANIFEST_KEY",
            "DESTINATION_COMPLETE_KEY",
        ),
        (
            "build_shortwave_radiation_from_canonical_snapshot.py",
            "_finalize",
            "DESTINATION_MANIFEST_KEY",
            "DESTINATION_COMPLETE_KEY",
        ),
        (
            "build_soil_moisture_from_canonical_snapshot.py",
            "_finalize",
            "DESTINATION_MANIFEST_KEY",
            "DESTINATION_COMPLETE_KEY",
        ),
        ("soil_temperature_snapshot_breakdown.py", "finalize_lane", "manifest_key", "completion_key"),
        ("soil_temperature_snapshot_breakdown.py", "finalize_bundle", "manifest_key", "completion_key"),
        ("breakdown_wind_speed_snapshot.py", "finalize", "manifest_key", "complete_key"),
    ],
)
def test_completion_marker_is_the_last_immutable_write(
    script: str,
    function_name: str,
    manifest_target: str,
    completion_target: str,
) -> None:
    function = _function(_tree(script), function_name)
    calls = sorted(
        (node for node in ast.walk(function) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    targets = [target for call in calls if (target := _put_target(call)) is not None]
    assert targets[-2:] == [manifest_target, completion_target]


def test_precipitation_audit_lands_before_the_final_completion_write() -> None:
    function = _function(_tree("build_precipitation_from_canonical_snapshot.py"), "_finalize")
    calls = sorted(
        (node for node in ast.walk(function) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    write_events: list[str] = []
    for call in calls:
        if target := _put_target(call):
            write_events.append(target)
        elif isinstance(call.func, ast.Name) and call.func.id == "_publish_source_audit":
            write_events.append("_publish_source_audit")

    assert write_events[-3:] == [
        "DESTINATION_MANIFEST_KEY",
        "_publish_source_audit",
        "DESTINATION_COMPLETE_KEY",
    ]


def _constructor_records(script: str, constructor: str) -> list[dict[str, Any]]:
    value = _assignment_value(_tree(script), "PRODUCTS")
    records: list[dict[str, Any]] = []
    for node in ast.walk(value):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != constructor:
            continue
        record: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            try:
                record[keyword.arg] = ast.literal_eval(keyword.value)
            except ValueError:
                continue
        if node.args:
            record["args"] = tuple(ast.literal_eval(argument) for argument in node.args)
        records.append(record)
    return records


def test_lane_family_metadata_is_frozen() -> None:
    assert _constructor_records("vpd_snapshot_breakdown.py", "ProductContract") == [
        {
            "product_id": "vpd",
            "stream": "soil-field-vpd",
            "source_parameter": "vapour_pressure_deficit_max",
            "signal_name": "vapor_pressure_deficit",
        }
    ]
    assert _constructor_records("air_temperature_snapshot_breakdown.py", "ProductContract") == [
        {
            "product_id": "t2m-mean",
            "stream": "climate-field-air-temperature-mean",
            "source_parameter": "T2M",
            "signal_name": "air_temperature_mean",
        },
        {
            "product_id": "t2m-maximum",
            "stream": "climate-field-air-temperature-max",
            "source_parameter": "T2M_MAX",
            "signal_name": "air_temperature_max",
        },
        {
            "product_id": "t2m-minimum",
            "stream": "climate-field-air-temperature-min",
            "source_parameter": "T2M_MIN",
            "signal_name": "air_temperature_min",
        },
    ]
    moisture = _constructor_records("build_soil_moisture_from_canonical_snapshot.py", "ProductSpec")
    assert [(item["product"], item["signal_name"], item["depth_band"]) for item in moisture] == [
        ("soil_moisture_0_to_7cm_mean", "soil_water_content_layer_1", "0-7cm"),
        ("soil_moisture_7_to_28cm_mean", "soil_water_content_layer_2", "7-28cm"),
        ("soil_moisture_28_to_100cm_mean", "soil_water_content_layer_3", "28-100cm"),
    ]
    assert [item["args"] for item in _constructor_records("soil_temperature_snapshot_breakdown.py", "Product")] == [
        (
            "soil_temperature_0_to_7cm_mean",
            "soil_temperature_0_to_7cm_mean",
            "soil-temperature-0-to-7cm",
            "soil_temperature_level_1",
        ),
        (
            "soil_temperature_7_to_28cm_mean",
            "soil_temperature_7_to_28cm_mean",
            "soil-temperature-7-to-28cm",
            "soil_temperature_level_2",
        ),
        (
            "soil_temperature_28_to_100cm_mean",
            "soil_temperature_28_to_100cm_mean",
            "soil-temperature-28-to-100cm",
            "soil_temperature_level_3",
        ),
        (
            "soil_temperature_100_to_255cm_mean",
            "soil_temperature_100_to_255cm_mean",
            "soil-temperature-100-to-255cm",
            "soil_temperature_level_4",
        ),
    ]


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (
            "build_relative_humidity_from_canonical_snapshot.py",
            {
                "CONTRACT_VERSION": "climate-field-relative-humidity.snapshot-breakdown.v1",
                "SOURCE_KEY": "nasa-power-daily",
                "SOURCE_PARAMETER": "RH2M",
                "SIGNAL_NAME": "relative_humidity",
                "NORMALIZED_UNIT": "%",
            },
        ),
        (
            "build_shortwave_radiation_from_canonical_snapshot.py",
            {
                "CONTRACT_VERSION": "climate-field-shortwave-radiation.snapshot-breakdown.v1",
                "SOURCE_KEY": "nasa-power-daily",
                "SOURCE_PARAMETER": "ALLSKY_SFC_SW_DWN",
                "SIGNAL_NAME": "surface_shortwave_radiation",
                "NORMALIZED_UNIT": "MJ/m^2/day",
            },
        ),
        (
            "breakdown_wind_speed_snapshot.py",
            {
                "CONTRACT_VERSION": "plantgeo.climate-field-wind-speed.snapshot.v1",
                "SOURCE_PART_PREFIX": "source=nasa-power-daily/product=WS2M/support=surface/",
            },
        ),
        (
            "build_precipitation_from_canonical_snapshot.py",
            {
                "CONTRACT_VERSION": "climate-field-precipitation.snapshot-breakdown.v1",
                "SOURCE_KEY": "nasa-power-daily",
                "SOURCE_PARAMETER": "PRECTOTCORR",
                "SIGNAL_NAME": "precipitation",
                "NORMALIZED_UNIT": "mm/day",
            },
        ),
    ],
)
def test_scalar_lane_metadata_is_frozen(script: str, expected: dict[str, str]) -> None:
    values = _string_constants(script)
    assert {name: values[name] for name in expected} == expected
