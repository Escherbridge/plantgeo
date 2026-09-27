"""`bootstrap.py::arm_from_environment` and the safe-switch-parsing helpers it exposes.

Every test here monkeypatches `sys.orig_argv`/`os.environ`/`atexit.register` rather than actually
spawning a child process or letting a real `atexit` hook fire at interpreter exit -- the behaviour
under test is a decision (which branch runs, what gets registered), not the eventual output of a
usage line GL-1b's `usage.py` (not yet written) will supply the content for.
"""

from __future__ import annotations

import signal
import sys

import pytest
import structlog

from agri_data_service.foundation.observability import bootstrap, events
from agri_data_service.foundation.observability.logging import configure_logging

# `default=6.0` above named so both the call site and this assertion read the same symbol
# (ruff PLR2004 "magic value").
_PROBE_HOURS_DEFAULT = 6.0


def test_arming_happens_only_with_a_turn_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    structlog.reset_defaults()
    bootstrap.arm_from_environment()
    assert not structlog.is_configured()

    monkeypatch.setenv("PLANTGEO_TURN_ID", "turn-abc")
    monkeypatch.setattr(bootstrap, "_raw_write_stdout", lambda _payload: None)
    monkeypatch.setattr(bootstrap.atexit, "register", lambda _fn: None)
    bootstrap.arm_from_environment()
    assert structlog.is_configured()


@pytest.mark.parametrize("payload_bytes", [20 * 1024, 7 * 1024 + 2 * 1024])
def test_usage_line_survives_an_unflushed_report_under_python_m(
    monkeypatch: pytest.MonkeyPatch, payload_bytes: int
) -> None:
    calls: list[object] = []

    class _FakeStream:
        def flush(self) -> None:
            calls.append("flush")

    monkeypatch.setattr(bootstrap.sys, "stdout", _FakeStream())
    monkeypatch.setattr(bootstrap.sys, "stderr", _FakeStream())
    monkeypatch.setattr(bootstrap.os, "write", lambda _fd, data: calls.append(("os.write", data)))

    bootstrap._raw_write_stdout({"event": "plantgeo_turn_usage", "filler": "a" * payload_bytes})

    assert calls[0] == "flush"
    assert calls[1] == "flush"
    write_call = calls[-1]
    assert isinstance(write_call, tuple)
    written = write_call[1]
    # The leading newline separates this line from a report whose own trailing newline was still
    # buffered (BUI2-01); the payload survives whole regardless of size.
    assert written.startswith(b"\n")
    assert written.endswith(b"\n")
    assert b"plantgeo_turn_usage" in written


def test_manual_python_m_run_emits_one_operator_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "agri_data_service.interface.cli", "data"])
    configured: list[tuple[object, ...]] = []
    monkeypatch.setattr(bootstrap, "configure_logging", lambda *args, **kwargs: configured.append((args, kwargs)))
    registered: list[object] = []
    monkeypatch.setattr(bootstrap.atexit, "register", registered.append)
    # `_register_operator_summary_at_exit` skips while under pytest; simulate a non-pytest process.
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    try:
        bootstrap.arm_from_environment()
    finally:
        sys.modules["pytest"] = pytest
    assert configured == [(("tool",), {})]
    assert len(registered) == 1


def test_script_file_main_is_operator_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "services/agri-data-service/scripts/build_soil_cogs.py"])
    assert bootstrap._is_tool_invocation() is True


def test_relative_scripts_invocation_from_the_service_root_is_operator_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python scripts/foo.py` (no leading directory) previously missed the `/scripts/` substring
    check entirely -- the common way an operator actually runs a script from the service root
    (security review).
    """
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "scripts/build_soil_cogs.py"])
    assert bootstrap._is_tool_invocation() is True


def test_argument_containing_an_excluded_word_is_not_falsely_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An argument that merely CONTAINS "sanic"/"alembic"/"pytest" must not suppress arming -- only
    the `-m` module name or the script's own filename stem does (security review: the old substring
    check over the whole joined argv false-excluded this).
    """
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "scripts/sanic_helper.py"])
    assert bootstrap._is_tool_invocation() is True


def test_executor_and_pytest_register_no_operator_line(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[object] = []
    monkeypatch.setattr(bootstrap.atexit, "register", registered.append)
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "manual_script.py"])
    # Still under pytest here (the real, ordinary case): no operator line is registered.
    bootstrap.arm_from_environment()
    assert registered == []

    # An executor child registers its OWN close-out usage hook, never the operator-summary one.
    registered.clear()
    monkeypatch.setenv("PLANTGEO_TURN_ID", "turn-xyz")
    monkeypatch.setattr(bootstrap, "_raw_write_stdout", lambda _payload: None)
    bootstrap.arm_from_environment()
    assert len(registered) == 1


def test_operator_line_skipped_for_zero_hosts_and_long_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exit-line DECISION (not `usage.py::_write_operator_line`, which always writes when called
    directly) is what must skip a long-running process and a process that metered nothing.

    Security review: `configure_logging` used to `del long_running` and never record it, so the
    `long_running` skip this test names could never fire; separately, `usage.py::_write_operator_line`
    always wrote even with zero hosts, which the OLD version of this module's docstring wrongly
    described as the writer's own job to skip.
    """
    monkeypatch.delenv("PLANTGEO_TURN_ID", raising=False)
    monkeypatch.setattr(sys, "orig_argv", ["python", "manual_script.py"])
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    written: list[object] = []
    monkeypatch.setattr(bootstrap, "_write_usage_line", lambda **kwargs: written.append(kwargs))
    try:
        # Case 1: long_running=True must skip the registration entirely.
        configure_logging("service", long_running=True)
        registered: list[object] = []
        monkeypatch.setattr(bootstrap.atexit, "register", registered.append)
        bootstrap._register_operator_summary_at_exit()
        assert registered == []

        # Case 2: not long-running, but nothing was metered -- the hook registers, but a call to it
        # must skip the actual write.
        configure_logging("service", long_running=False)
        registered.clear()
        monkeypatch.setattr(bootstrap, "_no_hosts_metered", lambda: True)
        bootstrap._register_operator_summary_at_exit()
        assert len(registered) == 1
        registered[0]()
        assert written == []

        # Case 3: not long-running, and something WAS metered -- the write actually happens.
        monkeypatch.setattr(bootstrap, "_no_hosts_metered", lambda: False)
        registered[0]()
        assert len(written) == 1
    finally:
        sys.modules["pytest"] = pytest


def test_sigterm_keeps_the_default_disposition(monkeypatch: pytest.MonkeyPatch) -> None:
    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setenv("PLANTGEO_TURN_ID", "turn-signal")
    monkeypatch.setattr(bootstrap, "_raw_write_stdout", lambda _payload: None)
    monkeypatch.setattr(bootstrap.atexit, "register", lambda _fn: None)
    bootstrap.arm_from_environment()
    assert signal.getsignal(signal.SIGTERM) == before


def test_garbled_switch_resolves_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE", "sorta-kinda")
    assert bootstrap.parse_switch("PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE", default=True) is True
    assert bootstrap.parse_switch("PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE", default=False) is False


def test_invalid_number_falls_back_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS", "not-a-number")
    configure_logging("service")
    value = bootstrap.parse_positive_number("PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS", default=_PROBE_HOURS_DEFAULT)
    assert value == _PROBE_HOURS_DEFAULT
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert combined.count(events.EVENT_CONFIG_FALLBACK) == 1
