"""Decision D5 is zero-Postgres, and a dependency is how that decision gets undone by accident.

The AST import contract (`test_layer_import_contract.py`) only sees code that was already written.
This file guards the step before: a database driver appearing in `pyproject.toml` at all, which is
what makes writing the import possible and what an inherited sibling requirement file would bring.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Final

import pytest

from plantgeo_ml_service.config import Settings, names_a_database_variable


@pytest.fixture(autouse=True)
def without_inherited_database_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the developer shell's own database variables so each case proves its OWN marker."""
    for name in list(os.environ):
        if names_a_database_variable(name):
            monkeypatch.delenv(name, raising=False)


PYPROJECT_PATH: Final = Path(__file__).resolve().parents[1] / "pyproject.toml"

#: Every distribution that would give this service a way to reach Postgres. Named individually
#: rather than pattern-matched so adding one is a deliberate edit to this list, not a silent pass.
FORBIDDEN_DISTRIBUTIONS: Final[frozenset[str]] = frozenset(
    {
        "sqlalchemy",
        "asyncpg",
        "psycopg",
        "psycopg2",
        "psycopg2-binary",
        "alembic",
        "pgvector",
        "geoalchemy2",
    }
)

#: A PEP 508 requirement string starts with the distribution name; everything after it is a version
#: specifier, an extra, or an environment marker, none of which identify the distribution.
_DISTRIBUTION_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _distribution_name(requirement: str) -> str:
    """Return one requirement string's normalised distribution name."""
    match = _DISTRIBUTION_NAME.match(requirement.strip())
    if match is None:
        raise ValueError(f"cannot read a distribution name from {requirement!r}")
    return match.group(0).lower().replace("_", "-")


def _declared_requirements() -> dict[str, list[str]]:
    """Return every requirement this service declares, grouped by the table that declares it."""
    project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]
    groups: dict[str, list[str]] = {"dependencies": list(project.get("dependencies", ()))}
    for extra, requirements in project.get("optional-dependencies", {}).items():
        groups[f"optional-dependencies.{extra}"] = list(requirements)
    return groups


def test_pyproject_declares_at_least_the_groups_this_file_guards() -> None:
    """A renamed or emptied table would make every assertion below vacuously true."""
    groups = _declared_requirements()

    assert groups["dependencies"], groups
    assert groups["optional-dependencies.dev"], groups


def test_no_database_driver_is_a_declared_dependency() -> None:
    offenders = [
        f"{group}: {requirement}"
        for group, requirements in _declared_requirements().items()
        for requirement in requirements
        if _distribution_name(requirement) in FORBIDDEN_DISTRIBUTIONS
    ]

    assert not offenders, (
        "plantgeo-ml-service is zero-Postgres by owner decision D5 (2026-09-18); remove:\n" + "\n".join(offenders)
    )


def test_the_driver_list_would_actually_catch_one() -> None:
    """Without this, a typo in `_distribution_name` would make the test above pass on anything."""
    assert _distribution_name("psycopg2-binary>=2.9") in FORBIDDEN_DISTRIBUTIONS
    assert _distribution_name("SQLAlchemy[asyncio]>=2.0") in FORBIDDEN_DISTRIBUTIONS
    assert _distribution_name("polars>=1.17,<2") not in FORBIDDEN_DISTRIBUTIONS


@pytest.mark.parametrize(
    "variable_name",
    ["PGHOST", "PGDATABASE", "POSTGRES_DSN", "DATABASE_DSN", "PGHOSTADDR", "AGRI_POSTGRES_DSN"],
)
def test_the_refusal_marker_catches_the_libpq_and_dsn_spellings(
    monkeypatch: pytest.MonkeyPatch, variable_name: str
) -> None:
    """`DATABASE_URL` alone let `PGHOST` through, and libpq needs nothing else to connect."""
    monkeypatch.setenv(variable_name, "localhost")

    with pytest.raises(ValueError, match="zero-Postgres by owner decision D5"):
        Settings(_env_file=None)
