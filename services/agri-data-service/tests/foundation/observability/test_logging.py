"""`foundation/observability/logging.py::configure_logging` and the deferred `get_logger`.

See `foundation/observability/AGENTS.md` "Logging contract" for the pinned processor order and the
`service`/`tool` profile split this exercises. `tests/conftest.py`'s autouse fixture resets
structlog and the stdlib root logger after every test in this module.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import warnings
from typing import TYPE_CHECKING

import click
import httpx
import structlog
from click.testing import CliRunner
from sanic import Sanic
from sqlalchemy.exc import OperationalError

from agri_data_service.foundation.observability.logging import configure_logging, get_logger
from agri_data_service.interface.cli.root import cli
from agri_data_service.jobs import worker

if TYPE_CHECKING:
    import pytest


def _lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_service_profile_routes_debug_info_warn_to_stdout_and_error_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLANTGEO_LOG_LEVEL", "debug")
    configure_logging("service")
    logger = structlog.get_logger()
    logger.debug("d_event")
    logger.info("i_event")
    logger.warning("w_event")
    logger.error("e_event")
    captured = capsys.readouterr()
    out_events = [line["event"] for line in _lines(captured.out)]
    err_events = [line["event"] for line in _lines(captured.err)]
    assert out_events == ["d_event", "i_event", "w_event"]
    assert err_events == ["e_event"]


def test_tool_profile_renders_json_with_level_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLANTGEO_LOG_LEVEL", "debug")
    configure_logging("tool")
    logger = structlog.get_logger()
    logger.debug("d")
    logger.info("i")
    logger.warning("w")
    logger.error("e")
    captured = capsys.readouterr()
    assert captured.out == ""
    levels = [line["level"] for line in _lines(captured.err)]
    assert levels == ["debug", "info", "warn", "error"]


def test_every_line_is_one_flat_json_object_with_event_level_timestamp_service(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("service")
    structlog.get_logger().info("an_event")
    lines = _lines(capsys.readouterr().out)
    assert len(lines) == 1
    payload = lines[0]
    assert payload["event"] == "an_event"
    assert {"event", "level", "timestamp", "service"} <= payload.keys()
    assert payload["level"] == "info"
    for value in payload.values():
        assert isinstance(value, (str, int, float, bool)) or value is None


def test_warning_renders_as_railway_warn(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("service")
    structlog.get_logger().warning("w_event")
    payload = _lines(capsys.readouterr().out)[0]
    assert payload["level"] == "warn"


def test_configure_is_idempotent_and_reaches_module_level_loggers(capsys: pytest.CaptureFixture[str]) -> None:
    logger = get_logger("module.level")
    configure_logging("service")
    configure_logging("service")
    logger.info("still_works")
    payload = _lines(capsys.readouterr().out)[0]
    assert payload["event"] == "still_works"


def test_exception_locals_never_reach_the_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("service")
    canary_local_value = "should-never-appear-in-any-log-line"  # noqa: F841
    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().error("failure", exc_info=True)
    combined = capsys.readouterr()
    assert "should-never-appear-in-any-log-line" not in combined.out + combined.err


def test_dict_tracebacks_and_show_locals_are_never_configured() -> None:
    configure_logging("service")
    processors = structlog.get_config()["processors"]
    for processor in processors:
        assert not isinstance(processor, structlog.tracebacks.ExceptionDictTransformer)
        renderer = getattr(processor, "func", processor)
        assert getattr(renderer, "show_locals", False) is False


def test_debug_level_never_enables_third_party_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_LOG_LEVEL", "debug")
    configure_logging("service")
    for name in ("httpx", "httpcore", "sqlalchemy", "asyncpg", "urllib3", "sanic"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_engine_execute_under_debug_emits_no_sql(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLANTGEO_LOG_LEVEL", "debug")
    configure_logging("service")
    logging.getLogger("sqlalchemy.engine").debug("SELECT * FROM secrets")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_stdlib_records_are_redacted(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "supersecretvalue123")
    configure_logging("service")
    logging.getLogger("httpx").warning("token=supersecretvalue123 leaked")
    combined = "".join(capsys.readouterr())
    assert "supersecretvalue123" not in combined


def test_turn_context_from_environment_is_bound(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLANTGEO_TURN_ID", "turn-123")
    monkeypatch.setenv("PLANTGEO_LANE_ID", "soil")
    monkeypatch.setenv("PLANTGEO_ATTEMPT", "2")
    configure_logging("service")
    structlog.get_logger().info("turn_event")
    payload = _lines(capsys.readouterr().out)[0]
    assert payload["turn_id"] == "turn-123"
    assert payload["lane"] == "soil"
    assert payload["attempt"] == "2"


def test_sinks_resolve_streams_at_write_time() -> None:
    configure_logging("service")
    logger = structlog.get_logger()
    buffer = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer  # type: ignore[assignment]
    try:
        logger.info("swapped_destination")
    finally:
        sys.stdout = original_stdout
    assert "swapped_destination" in buffer.getvalue()


def test_configure_inside_clirunner_does_not_capture_later_output(capsys: pytest.CaptureFixture[str]) -> None:
    @click.command()
    def _probe() -> None:
        configure_logging("tool")
        structlog.get_logger().error("inside_runner")

    runner = CliRunner()
    result = runner.invoke(_probe)
    assert "inside_runner" in result.output

    structlog.get_logger().error("after_runner")
    captured = capsys.readouterr()
    assert "after_runner" in captured.err


def test_worker_logger_follows_configuration_after_import(capsys: pytest.CaptureFixture[str]) -> None:
    worker.logger.warning("before_configure")
    configure_logging("service")
    worker.logger.error("after_configure")
    captured = capsys.readouterr()
    lines = _lines(captured.err)
    assert lines, "expected the post-configure line to render as JSON"
    payload = lines[-1]
    # Not merely "does the canary text appear somewhere" (the pre-configure fallback also writes to
    # stderr, so that alone passes even when frozen on the fallback branch) -- the post-configure
    # line must carry the full envelope the fallback intentionally omits before configuration.
    assert payload["event"] == "after_configure"
    assert {"timestamp", "service"} <= payload.keys()


def test_non_string_leaf_repr_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("service")
    request = httpx.Request("GET", "https://example.com/path?apikey=CANARY")
    structlog.get_logger().info("request_event", request=request)
    combined = "".join(capsys.readouterr())
    assert "CANARY" not in combined


def test_sqlalchemy_traceback_drops_sql_and_parameters(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("service")
    error = OperationalError("SELECT * FROM secrets WHERE token = :token", {"token": "abc123456789"}, Exception("boom"))
    try:
        raise error
    except OperationalError:
        structlog.get_logger().error("query_failed", exc_info=True)
    combined = "".join(capsys.readouterr())
    assert "SELECT * FROM secrets" not in combined
    assert "abc123456789" not in combined


def test_python_warnings_route_as_warn(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("service")
    warnings.warn("plantgeo_test_deprecation_canary", DeprecationWarning, stacklevel=2)
    combined_out, combined_err = capsys.readouterr()
    # Not merely "the canary text appears somewhere" -- the line must actually parse as the flat
    # JSON envelope FR-30 requires, with a `level` Railway can read (a plain-text `py.warnings` line
    # had no `level` field at all before this fix). `warn` routes to STDOUT under the `service`
    # profile, same as any other non-error line -- design's "service: debug/info/warn to stdout,
    # error to stderr" makes no `py.warnings`-specific exception (security review).
    matching = [
        line
        for stream in (combined_out, combined_err)
        for line in _lines(stream)
        if "plantgeo_test_deprecation_canary" in json.dumps(line)
    ]
    assert matching, "expected the warning to render as a parseable JSON line"
    payload = matching[0]
    assert payload["level"] == "warn"
    assert {"event", "level", "timestamp", "service"} <= payload.keys()


def test_agent_group_is_not_reconfigured() -> None:
    structlog.reset_defaults()
    runner = CliRunner()
    runner.invoke(cli, ["agent"])
    assert not structlog.is_configured()


def test_unrepresentable_leaf_and_non_string_keys_never_raise(capsys: pytest.CaptureFixture[str]) -> None:
    """A leaf whose `repr()` raises, and a non-string dict key, must never raise out of the log call.

    Security review: `structlog.get_logger().info("evt", obj=Bad())` (a `__repr__` that raises) and
    `.info("evt", obj={("a", "b"): 1})` (a tuple-keyed dict) both propagated their own exception out
    of the call site before this fix -- exactly the "a run stops because of logging" failure mode the
    owner ruled out.
    """

    class _BrokenRepr:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    configure_logging("service")
    structlog.get_logger().info("unrepr_event", obj=_BrokenRepr())
    structlog.get_logger().info("tuple_key_event", counts={("a", "b"): 1})
    lines = _lines(capsys.readouterr().out)
    events = {line["event"] for line in lines}
    assert {"unrepr_event", "tuple_key_event"} <= events


def test_sanic_own_handlers_are_stripped_by_the_redacting_bridge() -> None:
    """Sanic's own `dictConfig` handlers must not survive `configure_logging` (security review).

    `Sanic(...)` attaches its own unredacted `StreamHandler`s to `sanic.root`/`sanic.error`/
    `sanic.access`/`sanic.server` before `app.py::create_app` ever calls `configure_logging` --
    every Sanic error traceback (which can carry a keyed URL) was written raw to stderr through
    Sanic's own handler, in addition to a second, redacted copy through this bridge.
    """
    Sanic._app_registry.clear()
    Sanic("logging_test_probe")
    configure_logging("service")
    for name in ("sanic.root", "sanic.error", "sanic.access", "sanic.server"):
        logger = logging.getLogger(name)
        assert not logger.handlers, f"{name} still has its own handler after configure_logging"
        assert logger.propagate is True
