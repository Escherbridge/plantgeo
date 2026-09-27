"""`arm_from_environment()`: the one guarded call the package root makes at import time.

See `foundation/observability/AGENTS.md` "Arming" for the three branches and "The bootstrap <->
usage.py contract" for the lazy, by-name import into GL-1b's `usage.py` (not yet written when this
module was; every call into it is guarded and fails open). Every branch here fails open: an
exception anywhere in this module must never prevent `agri_data_service` from importing.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib
import json
import os
import sys
import time
from pathlib import PurePosixPath
from typing import Final

from agri_data_service.foundation.observability import events
from agri_data_service.foundation.observability.logging import (
    configure_logging,
    get_logger,
    is_long_running_configured,
)

logger = get_logger(__name__)

# The safe-switch-parsing rule every Wave O toggle follows (design §3.2, "Switches and the
# environment rule", SOF2-10): synonyms are case-insensitive and trimmed; a garbled or unrecognised
# value resolves to the DEFAULT (the legacy/current disposition), never to a guess, and a numeric
# tunable that fails to parse (or is non-positive) falls back to its default plus one
# `plantgeo_job_executor_config_fallback` warning naming the variable. Only `PLANTGEO_LOG_LEVEL`
# (logging.py) is exempt from the warning half of this rule -- it is a verbosity knob, not a
# safety-relevant tunable, and silently defaulting to `info` is the documented behaviour for it.
_SWITCH_ON_VALUES: Final = frozenset({"1", "true", "yes", "on", "enabled"})
_SWITCH_OFF_VALUES: Final = frozenset({"0", "false", "no", "off", "disabled", "none"})


def parse_switch(name: str, *, default: bool) -> bool:
    """Parse an on/off environment switch; a garbled or unrecognised value resolves to `default`."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in _SWITCH_ON_VALUES:
        return True
    if normalized in _SWITCH_OFF_VALUES:
        return False
    return default


def parse_positive_number(name: str, *, default: float) -> float:
    """Parse a positive numeric tunable; an invalid or non-positive value falls back with one warning."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or value <= 0:
        logger.warning(events.EVENT_CONFIG_FALLBACK, variable=name, value=raw, fallback=default)
        return default
    return value


# Deleted by the autouse test fixture in `tests/conftest.py` between tests, so one test's arming
# environment never leaks into the next (`test_arming_happens_only_with_a_turn_id` and siblings).
TURN_CONTEXT_ENV_VARS: Final[tuple[str, ...]] = (
    "PLANTGEO_TURN_ID",
    "PLANTGEO_LANE_ID",
    "PLANTGEO_TURN_MODE",
    "PLANTGEO_ATTEMPT",
    "PLANTGEO_TURN_BUCKET",
    "PLANTGEO_TURN_PROBE",
)

_TURN_ID_ENV_VAR: Final = "PLANTGEO_TURN_ID"

# GL-1b's contract (agreed name, documented so the second GL-1 author and this one do not drift):
# `foundation.observability.usage.write_usage_line(pid=..., opening=...)`. Imported lazily, by name,
# because `usage.py` does not exist yet when this file is authored -- the module may be absent for
# one commit within the same push, and forever absent under `PLANTGEO_UPSTREAM_TELEMETRY=off`-style
# misconfiguration. Either way, the call degrades to a no-op rather than raising.
_USAGE_WRITER_MODULE: Final = "agri_data_service.foundation.observability.usage"
_USAGE_WRITER_FUNCTION: Final = "write_usage_line"

_SCRIPT_EXCLUDED_MODULES: Final = frozenset({"pytest", "_pytest", "sanic", "alembic"})


def _write_usage_line(*, pid: int, opening: bool) -> None:
    """Best-effort call into GL-1b's usage writer; an absent module or function is a silent no-op."""
    try:
        module = importlib.import_module(_USAGE_WRITER_MODULE)
        writer = getattr(module, _USAGE_WRITER_FUNCTION, None)
        if writer is None:
            return
        writer(pid=pid, opening=opening)
    except Exception:  # fail-open by design; a usage-line fault must never break the caller
        return


def _raw_write_stdout(payload: dict[str, object]) -> None:
    """Flush both streams, then raw-write one JSON line to stdout with a leading blank line.

    The leading newline keeps a report whose own last newline is still buffered from fusing with
    this line into one malformed JSON line (BUI2-01). Uses `os.write` directly, not `print`, so no
    buffering layer can defer it past the process's own exit.
    """
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    with contextlib.suppress(Exception):
        sys.stderr.flush()
    line = json.dumps(payload, default=repr).encode("utf-8")
    os.write(1, b"\n" + line + b"\n")


def _is_turn_child() -> bool:
    return bool(os.environ.get(_TURN_ID_ENV_VAR))


def _is_tool_module_invocation(orig_argv: list[str]) -> bool:
    """`python -m agri_data_service.*`, matched on the MODULE NAME alone (see `_is_tool_invocation`)."""
    module_index = orig_argv.index("-m") + 1
    if module_index >= len(orig_argv):
        return False
    module_name = orig_argv[module_index]
    if module_name.split(".", 1)[0] in _SCRIPT_EXCLUDED_MODULES:
        return False
    return module_name.startswith("agri_data_service")


def _is_tool_script_invocation(orig_argv: list[str]) -> bool:
    """A `.py` entry point under this service's `scripts/`, matched on the FILENAME stem alone."""
    entry = orig_argv[1] if len(orig_argv) > 1 else None
    if entry is None or not entry.endswith(".py"):
        return False
    normalized = PurePosixPath(entry.replace("\\", "/"))
    if normalized.stem in _SCRIPT_EXCLUDED_MODULES:
        return False
    return "scripts" in normalized.parts


def _is_tool_invocation() -> bool:
    """`sys.orig_argv` names a `-m agri_data_service.*` run or a `.py` under this service's `scripts/`.

    Read with `sys.orig_argv` (not `__main__.__spec__`, which is still `None` under `runpy` at the
    point this module is imported -- BUI2-07). Exclusions are matched on the `-m` MODULE NAME or the
    script's own filename stem -- never on whether the JOINED argv happens to contain one of those
    words as a substring (security review: the old substring check false-excluded any invocation
    whose ARGUMENTS merely contained "sanic"/"alembic"/"pytest", e.g. a path component, and the
    `/scripts/` substring check missed the common relative form `python scripts/foo.py` run from the
    service root, since that path never contains a `/` before `scripts`).
    """
    orig_argv = getattr(sys, "orig_argv", None)
    if not orig_argv:
        return False
    if "-m" in orig_argv:
        return _is_tool_module_invocation(orig_argv)
    return _is_tool_script_invocation(orig_argv)


def _no_hosts_metered() -> bool:
    """True when this process has metered nothing so far -- best-effort, by name, matching
    `_write_usage_line`'s own lazy-import contract with `usage.py` (may not exist yet, or ever, under
    a misconfiguration; either way this fails open to "nothing metered").
    """
    try:
        module = importlib.import_module(_USAGE_WRITER_MODULE)
        host_counters = getattr(module, "_host_counters", None)
    except Exception:
        return True
    return not host_counters


def _register_operator_summary_at_exit() -> None:
    """One `plantgeo_source_usage` line at exit for an unarmed process that metered a host.

    Skipped when `configure_logging(long_running=True)` ran (a long-running service prints its own
    lines throughout, never one summary at exit), under pytest, or when nothing was metered this
    process. Runs independently of whether `_is_tool_invocation()` also configured the `tool` profile
    for this same process -- the two questions ("what profile logs this process?" and "does an
    unarmed process still owe one exit line?") are orthogonal (design §1.3, point 3: "No turn id (any
    process)"). The zero-hosts skip lives HERE, at the decision point, not inside
    `usage.py::_write_operator_line` -- that function always writes when called directly (design's
    "a zero-host line is still written" rule is pinned to the TURN line, not this one; security
    review corrected an earlier version of this docstring that claimed the writer itself skipped
    zero-host operator lines, which it never did).
    """
    if "pytest" in sys.modules:
        return
    if is_long_running_configured():
        return

    def _on_exit() -> None:
        if _no_hosts_metered():
            return
        _write_usage_line(pid=os.getpid(), opening=False)

    atexit.register(_on_exit)


def _arm_executor_child() -> None:
    configure_logging("service", long_running=True)
    pid = os.getpid()
    turn_id = os.environ.get(_TURN_ID_ENV_VAR, "")
    _raw_write_stdout(
        {
            "event": events.EVENT_TURN_USAGE_OPEN,
            "level": "debug",
            "pid": pid,
            "turn_id": turn_id,
            "opened_at": time.time(),
        }
    )

    def _on_exit() -> None:
        _write_usage_line(pid=pid, opening=False)

    atexit.register(_on_exit)
    # No signal handler is installed here, deliberately (SOF2-06): SIGTERM keeps Python's default
    # immediate-termination disposition, exactly as at HEAD. A holding pattern that traps SIGTERM
    # to "clean up" would turn Railway's ordinary redeploy signal into a hang.


def arm_from_environment() -> None:
    """Decide this process's logging profile and usage-line responsibility, and configure it.

    Called exactly once, from the package root (`src/agri_data_service/__init__.py`), itself
    guarded there too (belt and suspenders): every branch below already fails open, but the call
    site never trusts that alone.
    """
    try:
        if _is_turn_child():
            _arm_executor_child()
            return
        if _is_tool_invocation():
            configure_logging("tool")
        _register_operator_summary_at_exit()
    except Exception:  # arming must never prevent the package from importing
        with contextlib.suppress(Exception):
            logger.warning("plantgeo_observability_bootstrap_failed")
