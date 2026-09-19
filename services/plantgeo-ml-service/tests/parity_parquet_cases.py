"""The fixed inputs the phase-2A Parquet parity fixtures are built from, and how to evaluate them.

Shared by `test_parquet_paths_parity.py`, `test_parquet_markers_parity.py`, `test_streams_parity.py`,
`test_lanes_parity.py`, `test_availability_parity.py` and `scripts/regenerate_parity_fixtures.py`,
so the golden fixture and the assertion can never be built from two different input sets. Kept
separate from `parity_cases.py` (the phase-1 foundation helpers) only to keep each file readable.

Each evaluator takes an ADAPTER: a small namespace of callables one side supplies, because the two
services spell the same knowledge across different module boundaries. agri-data-service splits the
object-key grammar across `foundation/parquet/paths.py`, `foundation/parquet/zoom.py` and
`pipeline/parquet/objectstore.py`; this service keeps all of it in `foundation/parquet_paths.py`.
The adapter is what lets one fixed case set judge both layouts. See `tests/AGENTS.md`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final

from parity_cases import REPOSITORY_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import ModuleType

SIBLING_SOURCE_ROOT: Final = REPOSITORY_ROOT / "services" / "agri-data-service" / "src"

# --- The fixed case sets -----------------------------------------------------------------------

#: Layers, kinds, rungs, days and part indices every path builder is exercised over. The days cover
#: a leap day, a single-digit month and day (zero padding), and the deepest history floor in the
#: registry; the part indices cover the first, an interior one, and the ceiling.
PATH_LAYERS: Final[tuple[str, ...]] = ("signal", "fire-detections", "burn-severity")
PATH_KINDS: Final[tuple[str, ...]] = ("observed", "forecast")
PATH_ZOOMS: Final[tuple[int, ...]] = (0, 5, 9, 13)
PATH_DAYS: Final[tuple[date, ...]] = (date(2000, 11, 1), date(2024, 2, 29), date(2026, 9, 19))
PATH_PART_INDICES: Final[tuple[int, ...]] = (0, 7, 9_999)

#: Slugs `validate_layer_slug` must accept, and the ones it must refuse.
LAYER_SLUG_CASES: Final[tuple[str, ...]] = (
    "signal",
    "fire-detections",
    "soil-field-moisture-0-7cm",
    "Signal",
    "fire_detections",
    "fire--detections",
    "-signal",
    "signal-",
    "",
)

#: Keys the three `try_parse_*` functions are walked over: real keys, near misses, and nonsense.
PARSE_CASES: Final[tuple[str, ...]] = (
    "layer=signal/kind=observed/zoom=13/year=2026/month=09/day=19/part-0.parquet",
    "layer=signal/kind=forecast/zoom=00/year=2026/month=09/day=19/part-9999.parquet",
    "layer=fire-detections/kind=observed/zoom=09/year=2024/month=02/day=29/absent.json",
    "layer=fire-detections/kind=observed/zoom=09/year=2024/month=02/day=29/_complete.json",
    "layer=fire-detections/kind=observed/zoom=05/year=2024/month=02/day=29/_complete.empty.json",
    # A rung that is not on the ladder: parses structurally, must still be refused.
    "layer=signal/kind=observed/zoom=07/year=2026/month=09/day=19/part-0.parquet",
    # A calendar day that does not exist.
    "layer=signal/kind=observed/zoom=13/year=2025/month=02/day=30/part-0.parquet",
    # Unpadded month, the mistake zero padding exists to prevent.
    "layer=signal/kind=observed/zoom=13/year=2026/month=9/day=19/part-0.parquet",
    # A kind the layout does not have.
    "layer=signal/kind=hindcast/zoom=13/year=2026/month=09/day=19/part-0.parquet",
    # Windows separators, which every parser normalises before matching.
    "layer=signal\\kind=observed\\zoom=13\\year=2026\\month=09\\day=19\\part-0.parquet",
    "layer=signal/kind=observed/zoom=13/year=2026/month=09/day=19/availability.parquet",
    "not-a-key",
    "",
)

#: Keys the two availability retry parsers are walked over: a live claim, a quarantined one, a day
#: that is not a calendar day, a non-ISO rendering, a key under the wrong segment, and nonsense.
RETRY_PARSE_CASES: Final[tuple[str, ...]] = (
    "layer=signal/kind=forecast/availability/pending/day=2026-09-19.json",
    "layer=signal/kind=forecast/availability/pending/day=2026-09-19.quarantined.json",
    "layer=fire-detections/kind=observed/availability/pending/day=2024-02-29.json",
    "layer=fire-detections/kind=observed/availability/pending/day=2025-02-30.json",
    "layer=signal/kind=forecast/availability/pending/day=2026-9-19.json",
    "layer=signal/kind=forecast/availability/pending/2026-09-19.json",
    "layer=signal/kind=forecast/availability/generation=" + "f" * 64 + "/availability.parquet",
    "layer=signal\\kind=forecast\\availability\\pending\\day=2026-09-19.json",
    "",
)

#: The one stream the cross-service SERIALIZATION case is exercised on: a flat, all-non-null schema,
#: so what the case proves is the conform-and-sort contract rather than a lane's own null rules.
SERIALIZATION_STREAM: Final = "fire-detections"

#: Rows deliberately out of grain order. The sort inside `conform_to_stream_schema` is what makes a
#: partition's bytes its content rather than its caller's row order, and nothing else asserts it
#: ACROSS the two services.
SERIALIZATION_DAY: Final = date(2026, 9, 19)
SERIALIZATION_INSTANT: Final = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

#: Requested zooms `serving_zoom_tier` resolves, including both ends of the web-map scale.
SERVING_ZOOM_CASES: Final[tuple[int, ...]] = (0, 4, 5, 8, 9, 12, 13, 22, 23, -1)

#: The streams both services pin an observed schema for.
PARITY_STREAMS: Final[tuple[str, ...]] = (
    "signal",
    "fire-detections",
    "vegetation",
    "drought",
    "burn-severity",
    "weather-observations",
)

#: The lanes whose contract this service copies out of the sibling's registry.
PARITY_LANES: Final[tuple[str, ...]] = PARITY_STREAMS

#: One fixed completion receipt, one derived-empty receipt and one governed absence, so the marker
#: bodies are compared byte for byte rather than field by field.
MARKER_INSTANT: Final = datetime(2026, 9, 19, 12, 34, 56, 789012, tzinfo=UTC)
MARKER_RUN_ID: Final = "parity-run-0001"

#: The fixed availability input: one lane, one day, the full ladder, one published and one absent.
AVAILABILITY_LANE_ROOT: Final = "layer=signal/kind=forecast"
AVAILABILITY_DAY: Final = date(2026, 9, 18)
AVAILABILITY_CEILING: Final = date(2026, 9, 19)
AVAILABILITY_CREATED_AT: Final = datetime(2026, 9, 19, 0, 0, 0, tzinfo=UTC)
AVAILABILITY_INVENTORY_ROOT: Final = "a" * 64
AVAILABILITY_BOOTSTRAP_SHA: Final = "b" * 64
AVAILABILITY_SOURCE_SHA: Final = "c" * 64
AVAILABILITY_TERMINAL_SHA: Final = "d" * 64
AVAILABILITY_COMPLETION_SHA: Final = "e" * 64
AVAILABILITY_PART_SHA: Final = "f" * 64
AVAILABILITY_PRIOR_SHA: Final = "1" * 64
AVAILABILITY_RUNGS: Final[tuple[int, ...]] = (0, 5, 9, 13)


# --- Adapters ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PathsAdapter:
    """Every path-grammar callable one side supplies, whichever module it actually lives in."""

    validate_layer_slug: Callable[[str], str]
    serving_zoom_tier: Callable[[int], int]
    partition_path: Callable[..., str]
    absence_marker_path: Callable[..., str]
    completion_marker_path: Callable[..., str]
    derived_empty_completion_marker_path: Callable[..., str]
    promotion_receipt_path: Callable[..., str]
    availability_lane_root: Callable[[str, str], str]
    day_prefix: Callable[..., str]
    try_parse_partition_path: Callable[[str], Any]
    try_parse_absence_marker_path: Callable[[str], Any]
    try_parse_completion_marker_path: Callable[[str], Any]
    availability_retry_path: Callable[..., str]
    availability_retry_quarantine_path: Callable[..., str]
    availability_retry_prefix: Callable[[str, str], str]
    try_parse_availability_retry_path: Callable[[str], Any]
    try_parse_availability_retry_quarantine_path: Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class MarkersAdapter:
    """The two marker payload classes one side supplies."""

    partition_completion: Callable[..., Any]
    completed_part: Callable[..., Any]
    governed_absence: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class StreamsAdapter:
    """How one side answers "what is this stream's observed and forecast storage contract"."""

    observed_schema: Callable[[str], Any]
    forecast_schema: Callable[[str], Any]
    base_non_null_columns: Callable[[str], Sequence[str]]
    #: `(table, stream) -> the exact Parquet bytes that side would upload`. The one case that proves
    #: the two services write COMPARABLE FILES rather than merely agreeing on a schema rendering.
    serialize: Callable[[Any, Any], bytes]
    #: `(table, stream) -> the conformed, sorted table`, so a byte difference is diagnosable.
    conform: Callable[[Any, Any], Any]


@dataclass(frozen=True, slots=True)
class LanesAdapter:
    """How one side answers "what is this lane's clock"."""

    contract: Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class AvailabilityAdapter:
    """The availability document constructors and serializers one side supplies."""

    index_schema: Any
    required_rungs: Sequence[int]
    schema_version: str
    evidence_receipt: Callable[..., Any]
    identity: Callable[..., Any]
    config: Callable[..., Any]
    row: Callable[..., Any]
    pointer: Callable[..., Any]
    pointer_key: Callable[[str], str]
    generation_key: Callable[[str, str], str]
    bootstrap_marker_key: Callable[[str], str]
    receipt_sha256: Callable[..., str]
    #: The `availability.*` Parquet metadata key SET. Only the key set crosses the parity boundary:
    #: the sibling's value formatter (`availability_index._metadata`) sits behind a module that
    #: imports SQLAlchemy, which a zero-Postgres service will not take into its lockfile to read one
    #: pure function. The VALUES are pinned by `availability_metadata.json`, a fixture this service
    #: alone produces; `tests/AGENTS.md` records that asymmetry and what it does and does not prove.
    metadata_keys: frozenset[bytes]
    #: Present on this service only; `None` on the sibling side, for the reason above.
    metadata: Callable[..., dict[bytes, bytes]] | None = None


# --- Evaluators ----------------------------------------------------------------------------------


def evaluate_parquet_paths(adapter: PathsAdapter) -> dict[str, list[str]]:
    """Return every path-grammar outcome for the fixed grid, as comparable strings."""
    return {
        "validate_layer_slug": [
            _outcome(lambda case=case: adapter.validate_layer_slug(case)) for case in LAYER_SLUG_CASES
        ],
        "serving_zoom_tier": [
            _outcome(lambda case=case: adapter.serving_zoom_tier(case)) for case in SERVING_ZOOM_CASES
        ],
        "day_prefix": _grid(adapter.day_prefix),
        "partition_path": [
            _outcome(lambda ly=layer, k=kind, z=zoom, d=day, p=part: adapter.partition_path(ly, k, z, d, p))
            for layer in PATH_LAYERS
            for kind in PATH_KINDS
            for zoom in PATH_ZOOMS
            for day in PATH_DAYS
            for part in PATH_PART_INDICES
        ],
        "absence_marker_path": _grid(adapter.absence_marker_path),
        "completion_marker_path": _grid(adapter.completion_marker_path),
        "derived_empty_completion_marker_path": _grid(adapter.derived_empty_completion_marker_path),
        "promotion_receipt_path": [
            _outcome(lambda ly=layer, k=kind, d=day: adapter.promotion_receipt_path(ly, k, d))
            for layer in PATH_LAYERS
            for kind in PATH_KINDS
            for day in PATH_DAYS
        ],
        "availability_lane_root": [
            _outcome(lambda ly=layer, k=kind: adapter.availability_lane_root(ly, k))
            for layer in PATH_LAYERS
            for kind in PATH_KINDS
        ],
        "try_parse_partition_path": [_parsed(adapter.try_parse_partition_path, case) for case in PARSE_CASES],
        "try_parse_absence_marker_path": [_parsed(adapter.try_parse_absence_marker_path, case) for case in PARSE_CASES],
        "try_parse_completion_marker_path": [
            _parsed(adapter.try_parse_completion_marker_path, case) for case in PARSE_CASES
        ],
        # THE ROUND TRIP, not just the parse: a parsed key must rebuild the key it was read from, or
        # the two halves of the grammar have drifted while each half still looks right alone.
        "parse_round_trip": [_round_trip(adapter, case) for case in PARSE_CASES],
        "availability_retry_prefix": [
            _outcome(lambda ly=layer, k=kind: adapter.availability_retry_prefix(ly, k))
            for layer in PATH_LAYERS
            for kind in PATH_KINDS
        ],
        "availability_retry_path": [
            _outcome(lambda ly=layer, k=kind, d=day: adapter.availability_retry_path(ly, k, d))
            for layer in PATH_LAYERS
            for kind in PATH_KINDS
            for day in PATH_DAYS
        ],
        "availability_retry_quarantine_path": [
            _outcome(lambda ly=layer, k=kind, d=day: adapter.availability_retry_quarantine_path(ly, k, d))
            for layer in PATH_LAYERS
            for kind in PATH_KINDS
            for day in PATH_DAYS
        ],
        "try_parse_availability_retry_path": [
            _outcome(lambda case=case: adapter.try_parse_availability_retry_path(case)) for case in RETRY_PARSE_CASES
        ],
        "try_parse_availability_retry_quarantine_path": [
            _outcome(lambda case=case: adapter.try_parse_availability_retry_quarantine_path(case))
            for case in RETRY_PARSE_CASES
        ],
    }


def evaluate_parquet_markers(adapter: MarkersAdapter) -> dict[str, list[str]]:
    """Return the exact serialized bodies of one completion, one derived-empty and one absence marker."""
    part = adapter.completed_part(
        relative_path="layer=signal/kind=forecast/zoom=13/year=2026/month=09/day=19/part-0.parquet",
        row_count=1_234,
        byte_count=56_789,
        sha256=AVAILABILITY_PART_SHA,
    )
    return {
        "completion_v1": [
            adapter.partition_completion(
                part_count=3,
                row_count=900,
                completed_at=MARKER_INSTANT,
                run_id=MARKER_RUN_ID,
            )
            .to_json_bytes()
            .decode("utf-8")
        ],
        "completion_v2": [
            adapter.partition_completion(
                part_count=1,
                row_count=1_234,
                completed_at=MARKER_INSTANT,
                run_id=MARKER_RUN_ID,
                parts=(part,),
            )
            .to_json_bytes()
            .decode("utf-8")
        ],
        "completion_derived_empty": [
            adapter.partition_completion(
                part_count=0,
                row_count=0,
                completed_at=MARKER_INSTANT,
                run_id=MARKER_RUN_ID,
                derived_empty=True,
            )
            .to_json_bytes()
            .decode("utf-8")
        ],
        "governed_absence": [
            adapter.governed_absence(
                reason="source_empty",
                upstream_response="HTTP 200, zero features",
                recorded_at=MARKER_INSTANT,
                run_id=MARKER_RUN_ID,
            )
            .to_json_bytes()
            .decode("utf-8")
        ],
    }


def evaluate_streams(adapter: StreamsAdapter) -> dict[str, list[str]]:
    """Return every pinned stream's observed and forecast contract, and one side's written BYTES."""
    stream = adapter.observed_schema(SERIALIZATION_STREAM)
    unsorted_rows = _serialization_rows(stream)
    conformed = adapter.conform(unsorted_rows, stream)
    return {
        "observed": [_rendered_schema(adapter.observed_schema(name)) for name in PARITY_STREAMS],
        "forecast": [_rendered_schema(adapter.forecast_schema(name)) for name in PARITY_STREAMS],
        "base_non_null_columns": [f"{name}:{','.join(adapter.base_non_null_columns(name))}" for name in PARITY_STREAMS],
        "serialized_row_order": [_rendered_grain(conformed, stream)],
        "serialized_sha256": [_sha256_hex(adapter.serialize(unsorted_rows, stream))],
    }


def evaluate_lanes(adapter: LanesAdapter) -> dict[str, list[str]]:
    """Return each copied lane's clock: floor, lag, cadence, nature and forecaster stem."""
    rendered: list[str] = []
    for slug in PARITY_LANES:
        contract = adapter.contract(slug)
        rendered.append(
            "|".join(
                (
                    slug,
                    contract.history_floor.isoformat(),
                    str(contract.publication_lag_days),
                    str(contract.cadence_days),
                    str(contract.nature),
                    str(contract.forecast_module),
                )
            )
        )
    return {"lane_contracts": rendered}


def evaluate_availability(adapter: AvailabilityAdapter) -> dict[str, list[str]]:
    """Return the generation and pointer documents both services must agree on, for one fixed input."""
    identity = adapter.identity(
        lane_root=AVAILABILITY_LANE_ROOT,
        lane="signal",
        product="forecast",
        nature="daily_series",
        required_rungs=tuple(adapter.required_rungs),
        verified_source_inventory_root=AVAILABILITY_INVENTORY_ROOT,
    )
    bootstrap = adapter.evidence_receipt(
        key=f"{AVAILABILITY_LANE_ROOT}/availability/bootstrap/_BOOTSTRAPPED.json",
        sha256=AVAILABILITY_BOOTSTRAP_SHA,
    )
    config = adapter.config(
        identity=identity,
        source_ceiling=AVAILABILITY_CEILING,
        bootstrap_receipt=bootstrap,
    )
    rows = tuple(_availability_row(adapter, rung) for rung in AVAILABILITY_RUNGS)
    prior_key = f"{AVAILABILITY_LANE_ROOT}/availability/generation={AVAILABILITY_PRIOR_SHA}/availability.parquet"
    receipt = adapter.receipt_sha256(
        config=config,
        rows=rows,
        prior_generation_key=prior_key,
        prior_generation_sha256=AVAILABILITY_PRIOR_SHA,
        created_at=AVAILABILITY_CREATED_AT,
    )
    generation_sha = "2" * 64
    pointer = adapter.pointer(
        schema_version=adapter.schema_version,
        identity=identity,
        required_rungs=tuple(adapter.required_rungs),
        generation_key=adapter.generation_key(AVAILABILITY_LANE_ROOT, generation_sha),
        generation_sha256=generation_sha,
        generation_receipt_sha256=receipt,
        generation_bytes=4_096,
        rows=len(rows),
        earliest_terminal_day=AVAILABILITY_DAY,
        latest_terminal_day=AVAILABILITY_DAY,
        source_ceiling=AVAILABILITY_CEILING,
        prior_generation_key=prior_key,
        prior_generation_sha256=AVAILABILITY_PRIOR_SHA,
        created_at=AVAILABILITY_CREATED_AT,
        bootstrap_receipt=bootstrap,
    )
    return {
        "index_schema": [_rendered_arrow_schema(adapter.index_schema)],
        "required_rungs": [",".join(str(rung) for rung in adapter.required_rungs)],
        "schema_version": [adapter.schema_version],
        "pointer_key": [adapter.pointer_key(AVAILABILITY_LANE_ROOT)],
        "generation_key": [adapter.generation_key(AVAILABILITY_LANE_ROOT, generation_sha)],
        "bootstrap_marker_key": [_outcome(lambda: adapter.bootstrap_marker_key(AVAILABILITY_LANE_ROOT))],
        "rows": [_stable_json(row.to_wire()) for row in rows],
        "generation_receipt_sha256": [receipt],
        "generation_metadata_keys": sorted(key.decode("utf-8") for key in adapter.metadata_keys),
        "pointer": [_stable_json(pointer.to_wire())],
    }


def evaluate_availability_metadata(adapter: AvailabilityAdapter) -> dict[str, list[str]]:
    """Return the rendered `availability.*` metadata values for the fixed input, from one side only."""
    if adapter.metadata is None:
        raise RuntimeError("this adapter does not expose a metadata value formatter")
    identity = adapter.identity(
        lane_root=AVAILABILITY_LANE_ROOT,
        lane="signal",
        product="forecast",
        nature="daily_series",
        required_rungs=tuple(adapter.required_rungs),
        verified_source_inventory_root=AVAILABILITY_INVENTORY_ROOT,
    )
    config = adapter.config(
        identity=identity,
        source_ceiling=AVAILABILITY_CEILING,
        bootstrap_receipt=adapter.evidence_receipt(
            key=f"{AVAILABILITY_LANE_ROOT}/availability/bootstrap/_BOOTSTRAPPED.json",
            sha256=AVAILABILITY_BOOTSTRAP_SHA,
        ),
    )
    prior_key = f"{AVAILABILITY_LANE_ROOT}/availability/generation={AVAILABILITY_PRIOR_SHA}/availability.parquet"
    metadata = adapter.metadata(
        config=config,
        generation_receipt_sha256="3" * 64,
        row_count=len(AVAILABILITY_RUNGS),
        earliest_terminal_day=AVAILABILITY_DAY,
        latest_terminal_day=AVAILABILITY_DAY,
        prior_generation_key=prior_key,
        prior_generation_sha256=AVAILABILITY_PRIOR_SHA,
        created_at=AVAILABILITY_CREATED_AT,
    )
    return {
        "generation_metadata": [
            f"{key.decode('utf-8')}={value.decode('utf-8')}" for key, value in sorted(metadata.items())
        ]
    }


# --- Adapter factories ----------------------------------------------------------------------------


def sibling_module(dotted_name: str) -> ModuleType:
    """Import one agri-data-service module by its real dotted name, with the sibling `src` on the path.

    Its `foundation`, `warehouse` and `pipeline.parquet` modules resolve absolute imports inside
    their own package, so loading a single file by path (the phase-1 approach) cannot work here.
    """
    import importlib  # noqa: PLC0415 - imported here so a missing sibling never costs a module import

    root = str(SIBLING_SOURCE_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(dotted_name)


def sibling_source_is_present() -> bool:
    """Return whether the sibling's source is on disk; it is absent inside the Docker image."""
    return (SIBLING_SOURCE_ROOT / "agri_data_service" / "foundation" / "parquet" / "paths.py").is_file()


def _outcome(call: Any) -> str:
    """Return one call's rendered result, or the name of the exception it raised."""
    try:
        return f"ok:{call()}"
    except Exception as error:  # a refusal is part of the contract, so its TYPE is the golden value
        return f"raised:{type(error).__name__}"


def _grid(build: Callable[[str, str, int, date], str]) -> list[str]:
    """Walk the (layer, kind, zoom, day) grid every prefix builder shares."""
    return [
        _outcome(lambda ly=layer, k=kind, z=zoom, d=day: build(ly, k, z, d))
        for layer in PATH_LAYERS
        for kind in PATH_KINDS
        for zoom in PATH_ZOOMS
        for day in PATH_DAYS
    ]


def _parsed(parse: Callable[[str], Any], case: str) -> str:
    """Render one parse outcome as a comparable string, field by field rather than by repr."""
    try:
        parsed = parse(case)
    except Exception as error:
        return f"raised:{type(error).__name__}"
    if parsed is None:
        return "none"
    fields = ["layer", "kind", "zoom", "day", "part_index", "derived_empty"]
    rendered = [f"{name}={getattr(parsed, name)}" for name in fields if hasattr(parsed, name)]
    return "|".join(rendered)


def _round_trip(adapter: PathsAdapter, case: str) -> str:
    """Rebuild the key a parse produced, so a builder and its inverse cannot drift apart."""
    for parse in (
        adapter.try_parse_partition_path,
        adapter.try_parse_absence_marker_path,
        adapter.try_parse_completion_marker_path,
    ):
        try:
            parsed = parse(case)
        except Exception as error:
            return f"raised:{type(error).__name__}"
        if parsed is not None:
            return _outcome(lambda instance=parsed: instance.key)
    return "none"


def _serialization_rows(stream: Any) -> Any:
    """Build the fixed `fire-detections` case table, deliberately NOT in the stream's grain order."""
    import pyarrow as pa  # type: ignore[import-untyped]  # noqa: PLC0415  # pyarrow ships no stubs; one call site

    ordered = pa.table(
        {
            "cell_longitude": [-120.0, -120.01, -119.99],
            "cell_latitude": [46.0, 46.5, 46.25],
            "observed_day": [SERIALIZATION_DAY, SERIALIZATION_DAY, SERIALIZATION_DAY],
            "detection_count": [3, 1, 2],
            "frp_sum": [12.5, 1.0, 2.0],
            "frp_observation_count": [3, 1, 2],
            "high_confidence_detection_count": [1, 0, 1],
            "newest_observed_at": [SERIALIZATION_INSTANT, SERIALIZATION_INSTANT, SERIALIZATION_INSTANT],
        },
        schema=stream.arrow_schema,
    )
    return ordered.take([2, 0, 1])


def _rendered_grain(table: Any, stream: Any) -> str:
    """Render the conformed table's grain columns in row order, so a sort difference is legible."""
    rows = zip(*(table.column(name).to_pylist() for name in stream.sort_columns), strict=True)
    return ";".join(",".join(str(value) for value in row) for row in rows)


def _sha256_hex(payload: bytes) -> str:
    """Return the lowercase hex digest of exactly these bytes."""
    import hashlib  # noqa: PLC0415 - one call site, kept out of the module import list

    return hashlib.sha256(payload).hexdigest()


def _rendered_schema(schema: Any) -> str:
    """Render one stream contract: its name, codec, grain and every Arrow field."""
    return "|".join(
        (
            str(schema.name),
            str(schema.compression),
            ",".join(schema.sort_columns),
            _rendered_arrow_schema(schema.arrow_schema),
        )
    )


def _rendered_arrow_schema(arrow_schema: Any) -> str:
    """Render an Arrow schema field by field: name, type and nullability, in declared order."""
    return ";".join(f"{field.name}:{field.type!s}:{'null' if field.nullable else 'notnull'}" for field in arrow_schema)


def _stable_json(payload: Any) -> str:
    """Render one wire document with sorted keys, so two dict orderings cannot read as a difference."""
    import json  # noqa: PLC0415 - one call site, kept out of the module import list

    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _availability_row(adapter: AvailabilityAdapter, rung: int) -> Any:
    """Build one published terminal row of the fixed lane-day at one rung."""
    day_prefix = f"{AVAILABILITY_LANE_ROOT}/zoom={rung:02d}/year=2026/month=09/day=18"
    return adapter.row(
        lane="signal",
        product="forecast",
        nature="daily_series",
        day=AVAILABILITY_DAY,
        rung=rung,
        terminal_state="published",
        row_count=1_234,
        source_receipt=adapter.evidence_receipt(
            key=f"{AVAILABILITY_LANE_ROOT}/availability/evidence/source-2026-09-18.json",
            sha256=AVAILABILITY_SOURCE_SHA,
        ),
        terminal_receipt=adapter.evidence_receipt(
            key=f"{AVAILABILITY_LANE_ROOT}/availability/evidence/terminal-2026-09-18-{rung:02d}.json",
            sha256=AVAILABILITY_TERMINAL_SHA,
        ),
        data_receipts=(adapter.evidence_receipt(key=f"{day_prefix}/part-0.parquet", sha256=AVAILABILITY_PART_SHA),),
        completion_receipt=adapter.evidence_receipt(
            key=f"{day_prefix}/_complete.json", sha256=AVAILABILITY_COMPLETION_SHA
        ),
        absence_reason=None,
        source_ceiling=AVAILABILITY_CEILING,
        published_at=AVAILABILITY_CREATED_AT,
    )
