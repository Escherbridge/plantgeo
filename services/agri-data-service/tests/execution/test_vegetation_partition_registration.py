"""The partition register verb's contracts that need no database: identity, refusals, reachability.

The one thing a stubbed test can prove better than a live one is a NEGATIVE: that no statement this
module can execute names the frozen source. `test_vegetation_partition_registration_postgresql.py`
proves the positive against a real planner.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from agri_data_service.execution.vegetation_ndvi_plane import (
    DuplicatePartitionCellError,
    EmptyPartitionRegistrationError,
    NonFinitePartitionValueError,
    PartitionRegistrationError,
    PartitionSourceNotSuppliedError,
    partition_payload_checksum,
    register_governed_forward_plane,
    register_governed_partition_plane,
)
from agri_data_service.execution.vegetation_partition_promotion import day_partition_content_sha256

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _SERVICE_ROOT / "src" / "agri_data_service" / "execution" / "vegetation_ndvi_plane.py"
_SQL_ROOT = _SERVICE_ROOT / "src" / "agri_data_service" / "sql" / "execution"

DAY = date(2026, 9, 12)
CELL_VALUES = (("43.3750:-116.3750", 0.41), ("43.1250:-116.1250", 0.40))
#: Every source the postgres-vegetation lane froze on 2026-09-04. None may be reachable from the
#: scheduled promotion path again; that reachability is what rolled the lane back twice.
FROZEN_SOURCES = ("geo.features", "geo.layers", "agri.vegetation")


class _ExplodingSession:
    """Fails loudly if validation ever lets a statement through; there is nothing to stub."""

    async def execute(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a refused partition must never reach the database")


async def _register(cell_values: tuple[tuple[str, float], ...]) -> None:
    await register_governed_partition_plane(
        _ExplodingSession(),  # type: ignore[arg-type] - every refusal here precedes the first execute
        observed_day=DAY,
        cell_values=cell_values,
    )


def test_release_identity_is_the_promoters_own_partition_content_sha() -> None:
    """The registered payload checksum, the promotion receipt and the index generation are one value."""
    assert partition_payload_checksum(CELL_VALUES) == day_partition_content_sha256(CELL_VALUES)


def test_partition_checksum_ignores_the_order_rows_were_read_in() -> None:
    assert partition_payload_checksum(CELL_VALUES) == partition_payload_checksum(tuple(reversed(CELL_VALUES)))


@pytest.mark.asyncio
async def test_an_empty_partition_is_a_named_refusal_not_a_bare_value_error() -> None:
    with pytest.raises(EmptyPartitionRegistrationError) as caught:
        await _register(())
    assert isinstance(caught.value, PartitionRegistrationError)
    assert DAY.isoformat() in str(caught.value)


@pytest.mark.asyncio
async def test_a_duplicated_cell_is_refused_before_any_statement_runs() -> None:
    with pytest.raises(DuplicatePartitionCellError):
        await _register((*CELL_VALUES, (CELL_VALUES[0][0], 0.9)))


@pytest.mark.asyncio
async def test_a_non_finite_value_is_refused_before_any_statement_runs() -> None:
    with pytest.raises(NonFinitePartitionValueError):
        await _register((("43.1250:-116.1250", math.nan),))


@pytest.mark.asyncio
async def test_the_retired_cell_days_verb_names_its_replacement_instead_of_reviving_postgres() -> None:
    with pytest.raises(PartitionSourceNotSuppliedError) as caught:
        await register_governed_forward_plane(
            None,  # type: ignore[arg-type] - refused before the session is ever touched
            cutoff_day=DAY,
            cell_days=((CELL_VALUES[0][0], DAY),),
        )
    assert "register_governed_partition_plane" in str(caught.value)


def test_no_statement_this_module_loads_names_a_frozen_source() -> None:
    module_source = _MODULE_PATH.read_text(encoding="utf-8")
    loaded_sql_names = sorted(
        line.split('load_query_sql("execution/')[1].split('"')[0]
        for line in module_source.splitlines()
        if 'load_query_sql("execution/' in line
    )
    assert loaded_sql_names, "the module must still load its statements from sql/execution"

    for name in loaded_sql_names:
        body = (_SQL_ROOT / name).read_text(encoding="utf-8")
        statements = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("--"))
        for frozen in FROZEN_SOURCES:
            assert frozen not in statements, f"sql/execution/{name} still reaches the frozen source {frozen}"


def test_the_module_no_longer_binds_a_source_layer_to_any_statement() -> None:
    """Every retired statement took a `layer_name` bind; no live one does, so none can resolve a layer."""
    module_source = _MODULE_PATH.read_text(encoding="utf-8")
    assert '"layer_name"' not in module_source
