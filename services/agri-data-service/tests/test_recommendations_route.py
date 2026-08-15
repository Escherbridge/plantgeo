"""The serving surface validates its input, pins its artifact, and never guesses the clock."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agri_data_service.routes import recommendations
from agri_data_service.routes.recommendations import (
    _DEFAULT_RESULTS,
    _bounded_float,
    _bounded_int,
    _insufficient,
    _parse_query,
)


class _Args:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _request(**values: str) -> Any:
    return SimpleNamespace(args=_Args(values))


_CELL = "8615ec4b-3a83-486e-adfa-0b3b4865d799"
_AS_OF = "2026-08-10T00:00:00Z"


def test_a_valid_request_parses_into_a_frozen_query() -> None:
    query = _parse_query(_request(cell_id=_CELL, as_of=_AS_OF))

    assert query.cell_id == _CELL
    assert query.as_of_time == datetime(2026, 8, 10, tzinfo=UTC)
    assert query.artifact_checksum is None
    assert query.limit == _DEFAULT_RESULTS


def test_as_of_is_required_so_the_wall_clock_is_never_substituted() -> None:
    with pytest.raises(ValueError, match="as_of is required"):
        _parse_query(_request(cell_id=_CELL))


def test_a_naive_as_of_is_refused() -> None:
    with pytest.raises(ValueError, match="must include a timezone"):
        _parse_query(_request(cell_id=_CELL, as_of="2026-08-10T00:00:00"))


def test_a_missing_cell_is_refused() -> None:
    with pytest.raises(ValueError, match="cell_id must contain"):
        _parse_query(_request(as_of=_AS_OF))


@pytest.mark.parametrize("checksum", ["deadbeef", "z" * 64, "A" * 63])
def test_a_malformed_artifact_pin_is_refused(checksum: str) -> None:
    with pytest.raises(ValueError, match="artifact_checksum"):
        _parse_query(_request(cell_id=_CELL, as_of=_AS_OF, artifact_checksum=checksum))


def test_a_well_formed_artifact_pin_is_normalized() -> None:
    query = _parse_query(_request(cell_id=_CELL, as_of=_AS_OF, artifact_checksum="AB" * 32))

    assert query.artifact_checksum == "ab" * 32


@pytest.mark.parametrize("weight", ["-0.1", "1.5", "not-a-number"])
def test_objective_weights_are_bounded(weight: str) -> None:
    with pytest.raises(ValueError, match="wildfire_weight"):
        _bounded_float(weight, "wildfire_weight")


def test_limit_is_bounded_at_both_ends() -> None:
    default_limit = 10
    assert _bounded_int(None, default_limit, 1, 50, "limit") == default_limit
    with pytest.raises(ValueError, match="limit"):
        _bounded_int("0", default_limit, 1, 50, "limit")
    with pytest.raises(ValueError, match="limit"):
        _bounded_int("51", default_limit, 1, 50, "limit")


def test_the_insufficient_shape_carries_a_reason_and_no_results() -> None:
    payload = _insufficient(model_kind="species_fit", reason="no agent-reviewed labels")

    assert payload["status"] == "insufficient_labels"
    assert payload["results"] == []
    assert payload["claim_tier"] == recommendations.CLAIM_TIER
    assert "no agent-reviewed labels" in str(payload["reason"])


def test_the_module_never_reads_the_wall_clock_for_an_as_of() -> None:
    source = inspect.getsource(recommendations)

    assert "date.today()" not in source
    assert "datetime.now(" not in source
    assert "utcnow" not in source


def test_the_module_issues_no_write_statement() -> None:
    source = inspect.getsource(recommendations).upper()

    for statement in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert statement not in source


def test_both_model_kinds_are_reachable_and_typed() -> None:
    assert set(recommendations._MODEL_KIND_BY_PATH.values()) == {"species_fit", "strategy_selection"}
    assert recommendations._LABEL_KIND_BY_MODEL_KIND["strategy_selection"] == "strategy_outcome"


def test_the_blueprint_is_mounted_under_recommendations() -> None:
    assert recommendations.recommendations_bp.url_prefix == "/recommendations"
