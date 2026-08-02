"""Proves the conftest ``pytest_sessionfinish`` no-silent-skip gate actually flips the exit code.

Every other test in this suite exercises the ``agri_db`` gate indirectly (by
being an ``agri_db``-marked test that runs or legitimately skips). None of them
proves the gate itself -- the ``pytest_sessionfinish`` hook in ``conftest.py``
-- fails the run when an ``agri_db``-marked item skips while
``AGRI_TEST_DATABASE_URL`` is set. This test runs an isolated, injected pytest
session (via the ``pytester`` fixture) that imports the real hook functions
from this suite's own ``conftest.py`` and proves exactly that, plus the
symmetric case where the same skip is a tolerated notice.
"""

import importlib.util
from pathlib import Path

import pytest

_REAL_CONFTEST_PATH = Path(__file__).with_name("conftest.py")

_INJECTED_CONFTEST = f"""
import importlib.util

import pytest

_spec = importlib.util.spec_from_file_location(
    "agri_conftest_under_test", {str(_REAL_CONFTEST_PATH)!r}
)
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)

# Re-export exactly the real hooks under test; this pytester run's own dummy
# ``agri_db_connection`` fixture below never touches a real database, so the
# scenario is deterministic regardless of what AGRI_TEST_DATABASE_URL points to.
pytest_configure = _real.pytest_configure
pytest_collection_modifyitems = _real.pytest_collection_modifyitems
pytest_sessionfinish = _real.pytest_sessionfinish


@pytest.fixture
def agri_db_connection():
    return object()
"""

_INJECTED_SKIPPING_TEST = """
def test_uses_agri_db_but_skips_for_an_unrelated_reason(agri_db_connection):
    import pytest

    pytest.skip("simulated skip unrelated to the DSN gate")
"""


def _agri_test_database_url_env() -> str:
    spec = importlib.util.spec_from_file_location("agri_conftest_under_test", _REAL_CONFTEST_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AGRI_TEST_DATABASE_URL_ENV


def test_no_silent_skip_gate_fails_the_run_when_agri_db_url_is_set(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped ``agri_db``-marked item with the env var set must flip ``session.exitstatus``."""
    monkeypatch.setenv(_agri_test_database_url_env(), "postgresql://unused/only-for-this-pytester-run")
    pytester.makeconftest(_INJECTED_CONFTEST)
    pytester.makepyfile(_INJECTED_SKIPPING_TEST)

    result = pytester.runpytest_subprocess()

    assert result.ret != 0
    result.stdout.fnmatch_lines(["*AGRI_DB SWEEP FAILURE*"])


def test_no_silent_skip_gate_allows_the_skip_when_the_env_var_is_unset(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same skip, with the env var unset, is a structural notice -- not a failure."""
    monkeypatch.delenv(_agri_test_database_url_env(), raising=False)
    pytester.makeconftest(_INJECTED_CONFTEST)
    pytester.makepyfile(_INJECTED_SKIPPING_TEST)

    result = pytester.runpytest_subprocess()

    assert result.ret == 0
    result.stdout.fnmatch_lines(["*AGRI_DB SWEEP NOTICE*"])
