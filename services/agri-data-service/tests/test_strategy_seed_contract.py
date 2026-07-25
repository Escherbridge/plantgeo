"""Evidence-governance tests for definition-only strategy seeds."""

from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql.base import PGDialect

from agri_data_service.cli import _strategy_seed_statement
from agri_data_service.db.base import Base
from agri_data_service.models.strategy import StrategyReviewState
from agri_data_service.seed.strategies import (
    STRATEGY_SEEDS,
    UNSUPPORTED_PRESCRIPTIVE_FIELDS,
)

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect


def test_strategy_seeds_cover_four_families_without_prescriptive_values() -> None:
    expected = {
        ("agriculture", "USDA-NRCS", "340"),
        ("agroforestry", "USDA-NRCS", "311"),
        ("reforestation", "USDA-NRCS", "612"),
        ("silvopasture", "USDA-NRCS", "381"),
    }
    assert {(seed["category"], seed["authority"], seed["practice_code"]) for seed in STRATEGY_SEEDS} == expected
    for seed in STRATEGY_SEEDS:
        assert all(seed[field] is None for field in UNSUPPORTED_PRESCRIPTIVE_FIELDS)
        assert seed["review_state"] == StrategyReviewState.DRAFT
        assert seed["reviewed_at"] is None
        assert seed["reviewed_by"] is None


def test_strategy_seed_sources_are_bounded_authoritative_drafts() -> None:
    for seed in STRATEGY_SEEDS:
        source = urlparse(seed["evidence_source_url"])
        assert source.scheme == "https"
        assert source.hostname is not None
        assert source.hostname.endswith("nrcs.usda.gov")
        assert seed["evidence_citation"].strip()
        assert seed["jurisdiction"] == "US"
        assert seed["limitations"].strip()


def test_strategy_schema_keeps_unknown_rankings_null_and_approval_strict() -> None:
    table = Base.metadata.tables["agri.strategies"]
    for field in UNSUPPORTED_PRESCRIPTIVE_FIELDS:
        assert table.c[field].nullable
    checks = {str(constraint.sqltext) for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
    approval = next(check for check in checks if "review_state <> 'approved'" in check)
    for required in (
        "reviewed_at IS NOT NULL",
        "reviewed_by IS NOT NULL",
        "evidence_citation IS NOT NULL",
        "evidence_source_url IS NOT NULL",
        "jurisdiction IS NOT NULL",
        "limitations IS NOT NULL",
    ):
        assert required in approval
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("authority", "practice_code") in unique_columns


def test_seed_upsert_resets_review_only_when_governed_content_drifts() -> None:
    statement = _strategy_seed_statement(STRATEGY_SEEDS[0])
    dialect_factory = cast("type[Dialect]", PGDialect)
    sql = str(statement.compile(dialect=dialect_factory()))
    assert "ON CONFLICT" in sql
    assert "DO UPDATE SET" in sql
    assert "review_state" in sql
    assert "reviewed_at" in sql
    assert "reviewed_by" in sql
    assert "IS DISTINCT FROM" in sql
