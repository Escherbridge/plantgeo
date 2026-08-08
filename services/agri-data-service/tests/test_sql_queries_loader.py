"""Cover ``load_query_sql`` and prove the ``sql/`` tree reaches the wheel.

The loader tests are cheap and run everywhere. The packaging test is the one
that matters operationally: ``sql/**/*.sql`` is a non-``.py`` file inside
``src/agri_data_service``, and nothing else proves hatchling ships it. If it
does not, every extracted query raises ``FileNotFoundError`` at import time in
the installed ``agri-cli``, and only in the installed one -- a dev checkout
reads the files straight off disk and looks fine.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from agri_data_service.db.sql_queries import SQL_ROOT, load_query_sql

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_RELATIVE = "db/_loader_smoke.sql"


def test_loads_packaged_query_verbatim() -> None:
    """The loader returns the file's exact text."""
    expected = (SQL_ROOT / "db" / "_loader_smoke.sql").read_text(encoding="utf-8")
    assert load_query_sql(_SMOKE_RELATIVE) == expected
    assert "SELECT 1 AS loader_smoke" in expected


def test_escaping_path_is_rejected() -> None:
    """A relative path climbing out of the query root is a ValueError, not a read."""
    with pytest.raises(ValueError, match="escapes the runtime query root"):
        load_query_sql("../x.sql")


def test_missing_query_is_loud() -> None:
    """A missing file fails at load time with the resolved path in the message."""
    with pytest.raises(FileNotFoundError, match="no runtime query at"):
        load_query_sql("db/does_not_exist.sql")


def test_wheel_contains_sql_tree(tmp_path: Path) -> None:
    """``uv build --wheel`` ships ``sql/db/_loader_smoke.sql`` inside the package."""
    try:
        build = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
            cwd=_SERVICE_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"uv unavailable or too slow to build a wheel: {exc}")
    if build.returncode != 0:
        pytest.skip(f"uv build --wheel failed: {build.stderr.strip()[-500:]}")

    wheels = sorted(tmp_path.glob("*.whl"))
    assert wheels, f"uv build produced no wheel in {tmp_path}"
    with zipfile.ZipFile(wheels[-1]) as wheel:
        names = set(wheel.namelist())
    assert "agri_data_service/sql/db/_loader_smoke.sql" in names
