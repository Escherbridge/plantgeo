"""The one `configure_logging`, ported from `app.py::create_app`'s structlog chain and extended.

See `foundation/observability/AGENTS.md` "Logging contract" for the pinned processor order, the
`service`/`tool` profiles, and the Railway facts this module's defaults were chosen against. Every
first-party line this configures is one flat, redacted JSON object with `event`, `level`,
`timestamp` and `service` (design §1.2); `dict_tracebacks`, `ExceptionDictTransformer` and
`show_locals=True` are never configured (this is the one place that promise is enforced).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal

import structlog
import structlog.stdlib
import structlog.typing

from agri_data_service.foundation.observability import redaction

if TYPE_CHECKING:
    from collections.abc import MutableMapping

Profile = Literal["service", "tool"]

# --- Railway level mapping (AGENTS.md "Railway logging facts") -----------------------------------
# Railway fuzzy-matches a `level` field to the nearest of debug/info/warn/error, case-insensitive;
# `critical` and `exception` are not among its four, so both fold to `error` here rather than
# relying on that fuzzy match to land where this service intends.
_RAILWAY_LEVEL_RENAME: Final[dict[str, str]] = {
    "warning": "warn",
    "critical": "error",
    "exception": "error",
    "fatal": "error",
}
_STDOUT_LEVELS: Final = frozenset({"debug", "info", "warn"})

_ENVELOPE_ALWAYS_KEYS: Final = ("event", "level", "timestamp", "service", "deploy")
_LINE_CLAMP_BYTES: Final = 16 * 1024

# `PLANTGEO_LOG_LEVEL` applies to first-party (structlog and `agri_data_service.*`) loggers only;
# an unset or unrecognised value resolves to `info` silently, same as the switch table's row for it
# (design §3.2, "Switches and the environment rule").
_LOG_LEVEL_ENV_VAR: Final = "PLANTGEO_LOG_LEVEL"
_DEFAULT_LOG_LEVEL_NAME: Final = "info"
_LOG_LEVEL_NUMBERS: Final[dict[str, int]] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# Third-party loggers whose WARNING floor is pinned regardless of `PLANTGEO_LOG_LEVEL` -- raising
# the first-party level to `debug` must never turn on SQLAlchemy's or httpx's own debug chatter
# (`test_debug_level_never_enables_third_party_debug`, `test_engine_execute_under_debug_emits_no_sql`).
_STDLIB_WARNING_FLOOR_LOGGERS: Final = (
    "httpx",
    "httpcore",
    "sqlalchemy",
    "asyncpg",
    "botocore",
    "boto3",
    "s3transfer",
    "urllib3",
    "rasterio",
    "asyncio",
    "sanic",
)
_FIRST_PARTY_LOGGER_NAME: Final = "agri_data_service"

# `PLANTGEO_TURN_*` plus `PLANTGEO_LANE_ID` and `PLANTGEO_ATTEMPT`: how turn context reaches a
# child process (design §1.2). `bootstrap.py` sets these on a spawned child; this processor reads
# them fresh on every call rather than binding once, so a value changed mid-process (a test using
# `monkeypatch.setenv`, principally) is honoured immediately.
_TURN_ENV_TO_FIELD: Final[dict[str, str]] = {
    "PLANTGEO_TURN_ID": "turn_id",
    "PLANTGEO_LANE_ID": "lane",
    "PLANTGEO_TURN_MODE": "mode",
    "PLANTGEO_ATTEMPT": "attempt",
    "PLANTGEO_TURN_BUCKET": "shard_key",
    "PLANTGEO_TURN_PROBE": "probe",
}


def _resolve_min_level() -> int:
    raw = os.environ.get(_LOG_LEVEL_ENV_VAR, _DEFAULT_LOG_LEVEL_NAME).strip().lower()
    return _LOG_LEVEL_NUMBERS.get(raw, _LOG_LEVEL_NUMBERS[_DEFAULT_LOG_LEVEL_NAME])


def _service_name() -> str:
    return os.environ.get("RAILWAY_SERVICE_NAME", "agri-data-service")


def _deploy_id() -> str | None:
    return os.environ.get("RAILWAY_DEPLOYMENT_ID")


# --- Pinned processors ----------------------------------------------------------------------------


def _railway_level_rename(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    level = event_dict.get("level")
    if isinstance(level, str):
        event_dict["level"] = _RAILWAY_LEVEL_RENAME.get(level, level)
    return event_dict


def _turn_context_processor(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    for env_name, field in _TURN_ENV_TO_FIELD.items():
        if field in event_dict:
            continue
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        event_dict[field] = value == "1" if field == "probe" else value
    return event_dict


def _envelope_processor(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    """Stamp `service` and `deploy`, present on every line (design §1.2)."""
    event_dict.setdefault("service", _service_name())
    deploy = _deploy_id()
    if deploy is not None:
        event_dict.setdefault("deploy", deploy)
    return event_dict


def _normalize_key(key: object) -> object:
    """Coerce a dict key to something `json.dumps` can serialise as a key (str/int/float/bool/None).

    `json.dumps`'s `default=` hook is never consulted for KEYS, only values -- a tuple-keyed or
    otherwise exotic-keyed dict previously raised `TypeError: keys must be str, int, float, bool or
    None, ...` straight out of the log call (security review; verified: a lane logging a
    `(lane, day)`-keyed counter aborted its own turn on the log line meant to report it).
    """
    if key is None or isinstance(key, (str, int, float, bool)):
        return key
    return str(key)


_Container = dict[object, object] | Mapping[object, object] | list[object] | tuple[object, ...]


def _normalize_container(value: _Container, *, depth: int) -> object:
    if isinstance(value, dict):
        return {_normalize_key(key): _normalize_leaf(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, Mapping):
        # A non-dict Mapping (httpx.Headers, MappingProxyType, ...) previously fell straight to the
        # `repr()` fallback below, which skips both key normalisation and the key-based redaction
        # rule `_redact_tree` applies to `dict` -- a header mapping's `x-api-key` entry was never
        # even inspected (security review).
        return {_normalize_key(key): _normalize_leaf(item, depth=depth + 1) for key, item in dict(value).items()}
    return [_normalize_leaf(item, depth=depth + 1) for item in value]


def _normalize_fallback(value: object, *, depth: int) -> object:
    dunder = getattr(value, "__structlog__", None)
    if callable(dunder):
        try:
            return _normalize_leaf(dunder(), depth=depth + 1)
        except Exception:  # a broken __structlog__() must never break the log line itself
            pass
    try:
        return repr(value)
    except Exception:  # a broken __repr__ must never raise out of the log call (security review)
        return f"<unrepresentable {type(value).__name__}>"


def _normalize_leaf(value: object, *, depth: int = 0) -> object:
    if depth > redaction.MAX_REDACTION_DEPTH:
        return redaction.DEPTH_LIMIT_PLACEHOLDER
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dict, Mapping, list, tuple)):
        return _normalize_container(value, depth=depth)
    return _normalize_fallback(value, depth=depth)


def _leaf_normalizer(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    """Every value that is not str/int/float/bool/None/list/dict becomes `__structlog__()` or `repr()`."""
    for key, value in list(event_dict.items()):
        event_dict[key] = _normalize_leaf(value)
    return event_dict


def _dedupe_redacted_key(existing: dict[object, object], key: object) -> object:
    """Suffix a redacted-key collision (`#1`, `#2`, ...) -- duplicated from `redaction.py` rather than
    imported: a same-package import would pass the layer-import test, but reaching into a sibling
    module's private helper is a worse coupling than four duplicate lines (see this module's
    AGENTS.md entry, the same rule `router.py` already documents for its own duplicated constant).
    """
    if key not in existing:
        return key
    suffix = 1
    candidate = f"{key}#{suffix}"
    while candidate in existing:
        suffix += 1
        candidate = f"{key}#{suffix}"
    return candidate


def _redact_tree(value: object, *, depth: int = 0) -> object:
    if depth > redaction.MAX_REDACTION_DEPTH:
        return redaction.DEPTH_LIMIT_PLACEHOLDER
    if isinstance(value, str):
        return redaction.redact_leaf(value)
    if isinstance(value, dict):
        result: dict[object, object] = {}
        for key, item in value.items():
            if redaction.is_redacted_key(key):
                result[key] = redaction.REDACTED_PLACEHOLDER
                continue
            # The key's own TEXT is redacted too, not only checked by name: a dict keyed by a URL
            # that embeds `apikey=...` (a natural shape for a per-host counter) published that key
            # verbatim otherwise (security review).
            new_key = redaction.redact_leaf(key) if isinstance(key, str) else key
            new_key = _dedupe_redacted_key(result, new_key)
            result[new_key] = _redact_tree(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_redact_tree(item, depth=depth + 1) for item in value]
    return value


def _redaction_processor(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    """Per leaf: exact-value scrub, then truncate to 8 KiB dropping the trailing partial token, then regex.

    A top-level kwarg named for a secret (`logger.info("x", password=...)`) is redacted wholesale by
    key, the same rule `_redact_tree` applies to a nested mapping's keys -- the event dict itself is
    just the outermost mapping in the tree, and must not be exempted from its own rule.
    """
    for key, value in list(event_dict.items()):
        event_dict[key] = redaction.REDACTED_PLACEHOLDER if redaction.is_redacted_key(key) else _redact_tree(value)
    return event_dict


def _line_clamp(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    """Keep the serialised line at or under 16 KiB, clamping the largest free-text leaf first."""
    try:
        serialized = json.dumps(event_dict, default=str)
    except (TypeError, ValueError):
        return event_dict
    if len(serialized.encode("utf-8")) <= _LINE_CLAMP_BYTES:
        return event_dict
    clampable_keys = [
        key for key, value in event_dict.items() if key not in _ENVELOPE_ALWAYS_KEYS and isinstance(value, str)
    ]
    clampable_keys.sort(key=lambda key: len(event_dict[key]), reverse=True)  # type: ignore[arg-type]
    for key in clampable_keys:
        event_dict[key] = redaction.truncate_leaf(event_dict[key], limit=max(1, _LINE_CLAMP_BYTES // 4))  # type: ignore[arg-type]
        try:
            serialized = json.dumps(event_dict, default=str)
        except (TypeError, ValueError):
            break
        if len(serialized.encode("utf-8")) <= _LINE_CLAMP_BYTES:
            return event_dict
    # Still too big (or nothing clampable): stub the line rather than emit an oversized one.
    return {
        "event": "plantgeo_line_clamped",
        "level": event_dict.get("level", "warn"),
        "timestamp": event_dict.get("timestamp"),
        "service": event_dict.get("service", _service_name()),
        "original_event": _normalize_leaf(event_dict.get("event")),
        "original_bytes": len(serialized.encode("utf-8")),
    }


# --- Sinks that resolve sys.stdout/sys.stderr at write time, never at configure time (BUI2-08) -----


class _RoutingPrintLogger:
    """A structlog "logger" whose stream choice is made per call, from (profile, level).

    `service`: debug/info/warn to stdout, error to stderr. `tool`: every level to stderr, so stdout
    stays free as the data channel a CLI command may still print to. The stream is looked up again
    on every write (`sys.stdout`/`sys.stderr`, not a captured reference), so a context manager that
    swaps either stream mid-process -- Click's `CliRunner.isolation`, principally -- is honoured for
    calls made while it is active and stops being honoured the moment it exits
    (`test_configure_inside_clirunner_does_not_capture_later_output`).
    """

    def __init__(self, *, profile: Profile) -> None:
        self._profile = profile

    def _write(self, *, stream_name: Literal["stdout", "stderr"], message: str) -> None:
        stream = sys.stderr if stream_name == "stderr" else sys.stdout
        stream.write(message + "\n")
        stream.flush()

    def _route(self, level: str, message: str) -> None:
        if self._profile == "tool":
            self._write(stream_name="stderr", message=message)
            return
        self._write(stream_name="stdout" if level in _STDOUT_LEVELS else "stderr", message=message)

    def debug(self, message: str) -> None:
        self._route("debug", message)

    def info(self, message: str) -> None:
        self._route("info", message)

    def warning(self, message: str) -> None:
        self._route("warn", message)

    warn = warning

    def error(self, message: str) -> None:
        self._route("error", message)

    def critical(self, message: str) -> None:
        self._route("error", message)

    def exception(self, message: str) -> None:
        self._route("error", message)

    def fatal(self, message: str) -> None:
        self._route("error", message)

    # `structlog.PrintLogger`-style generic alias some rendering paths call directly.
    msg = info


class StreamSink:
    """The `logger_factory` this module configures: builds a `_RoutingPrintLogger` per profile."""

    def __init__(self, *, profile: Profile) -> None:
        self._profile = profile

    def __call__(self, *_args: object) -> _RoutingPrintLogger:
        return _RoutingPrintLogger(profile=self._profile)


# --- Fail-open processor wrapper (security review: a single processor fault must never surface at
# the `logger.info(...)` call site -- structlog does not catch a processor's own exception) --------


def _fail_open(processor: structlog.typing.Processor) -> structlog.typing.Processor:
    """Wrap a pinned processor so any exception it raises becomes a minimal stub line instead.

    Every processor below already tries hard not to raise (each leaf is normalised and redacted
    before this point), but a fault in the small amount of logic each one still has left -- an
    unexpected event-dict shape, an environment read that misbehaves -- must degrade to a stub
    line carrying only the envelope fields FR-30 requires, never propagate into the caller's turn.
    """

    def _wrapped(logger: object, method_name: str, event_dict: MutableMapping[str, object]) -> Any:
        try:
            return processor(logger, method_name, event_dict)
        except Exception as exc:  # a single processor fault must never break the log call
            return {
                "event": event_dict.get("event", "plantgeo_log_processor_failed"),
                "level": event_dict.get("level", "error"),
                "timestamp": event_dict.get("timestamp"),
                "service": event_dict.get("service", _service_name()),
                "logging_error": type(exc).__name__,
            }

    return _wrapped


# --- stdlib logging bridge -------------------------------------------------------------------------


class _RailwayLevelStreamHandler(logging.Handler):
    """A stdlib `Handler` that writes a `ProcessorFormatter`-rendered, already-redacted line.

    Redaction and the envelope now happen INSIDE `self.format(record)` (the formatter set in
    `_configure_stdlib_bridge` is a `structlog.stdlib.ProcessorFormatter` running the same pinned
    chain `configure_logging` uses for structlog's own lines), so `emit` itself does no redaction of
    its own -- doing it twice risked mangling an already-substituted `[redacted:NAME]` token and,
    more importantly, left every stdlib/third-party record with no `event`/`timestamp`/`service`
    envelope at all (security review: a plain-text `sanic.error`/`py.warnings` line carried no
    `level` field Railway could read, and rendered a raw traceback -- including any keyed URL in it
    -- with no redaction pass over it whatsoever).
    """

    def __init__(self, *, profile: Profile) -> None:
        super().__init__()
        self._profile = profile

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # a formatting fault must never crash the caller's real work
            self.handleError(record)
            return
        level = "error" if record.levelno >= logging.ERROR else "warn"
        stream = sys.stdout if (self._profile == "service" and level != "error") else sys.stderr
        stream.write(message + "\n")
        stream.flush()

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - overrides `logging.Handler.handleError`
        """Never let the stdlib default `handleError` print `record.msg`/`record.args` verbatim.

        The default implementation writes `"Message: %r\\nArguments: %s" % (record.msg, record.args)`
        straight to `sys.stderr`, bypassing every redaction path this module has -- a third-party
        logger calling `.warning("fetch %s", keyed_url)` with a `%`-arg mismatch (or an argument
        whose `__str__` raises) published the raw argument, keyed URL included (security review).
        This override writes one fixed, redacted-by-construction JSON line naming only the failing
        logger and the exception's class, and touches neither `record.msg` nor `record.args`.
        """
        exc_type = sys.exc_info()[0]
        try:
            payload = {
                "event": "plantgeo_log_format_failed",
                "level": "error",
                "logger": record.name,
                "logging_error": exc_type.__name__ if exc_type is not None else "unknown",
            }
            sys.stderr.write(json.dumps(payload) + "\n")
            sys.stderr.flush()
        except Exception:  # the absolute last resort: swallow it rather than risk a second fault
            pass


def _stdlib_foreign_pre_chain() -> list[structlog.typing.Processor]:
    """The same pinned chain `configure_logging` builds, minus the sinks structlog itself owns.

    Run by `structlog.stdlib.ProcessorFormatter` over every record a THIRD-PARTY or stdlib logger
    emits (Sanic's own loggers, `py.warnings`, an unconfigured library) before it reaches
    `_RailwayLevelStreamHandler`, so those lines get the identical envelope, redaction, leaf
    normalisation and 16 KiB clamp a first-party `structlog` line does -- not the bare
    `"%(name)s: %(message)s"` text the stdlib bridge rendered before (security/FR-30).
    """
    return [
        structlog.processors.add_log_level,
        _railway_level_rename,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _turn_context_processor,
        _envelope_processor,
        structlog.processors.format_exc_info,
        _leaf_normalizer,
        _redaction_processor,
        _line_clamp,
    ]


def _strip_foreign_logger_handlers() -> None:
    """Force every OTHER logger's records through the one redacting bridge, not a handler of its own.

    Sanic 25.12.1's `Sanic(...)` constructor runs `logging.config.dictConfig(...)` before
    `configure_logging` ever gets to run, attaching its own unredacted `StreamHandler`s to
    `sanic.root`/`sanic.error`/`sanic.access`/`sanic.server` -- every Sanic error traceback (which
    can carry a keyed URL) was written raw to stderr through Sanic's own handler, then a SECOND time
    through this bridge, redacted (security review). Any logger, not only `sanic.*`, that has
    attached its own handler by the time this runs is walked and stripped the same way, so a future
    third-party library that does the same thing is covered without a per-library allowlist.
    """
    manager = logging.Logger.manager
    for name in list(manager.loggerDict):
        candidate = manager.loggerDict.get(name)
        if not isinstance(candidate, logging.Logger) or not candidate.handlers:
            continue
        for existing_handler in list(candidate.handlers):
            candidate.removeHandler(existing_handler)
        candidate.propagate = True


def _configure_stdlib_bridge(*, profile: Profile, min_level: int) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(default=redaction.redacting_fallback),
        ],
        foreign_pre_chain=_stdlib_foreign_pre_chain(),
    )
    handler = _RailwayLevelStreamHandler(profile=profile)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    for name in _STDLIB_WARNING_FLOOR_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger(_FIRST_PARTY_LOGGER_NAME).setLevel(min_level)
    logging.captureWarnings(True)
    _strip_foreign_logger_handlers()


# --- configure_logging -------------------------------------------------------------------------------


class _ConfigurationState:
    """Holds `long_running` as an attribute rather than a bare module `global`, so recording it does
    not trip ruff's PLW0603 (discouraged global rebind) the way a plain module-level variable would.
    """

    __slots__ = ("long_running",)

    def __init__(self) -> None:
        self.long_running = False


_configuration_state = _ConfigurationState()


def is_long_running_configured() -> bool:
    """True once the most recent `configure_logging(..., long_running=True)` call ran.

    `bootstrap.py::_register_operator_summary_at_exit` reads this to skip the operator exit line for
    a long-running service (it prints its own lines throughout and never owes one summary at exit).
    Previously `configure_logging` did `del long_running` and never recorded the flag at all, so that
    skip could never actually fire (security review).
    """
    return _configuration_state.long_running


def configure_logging(profile: Profile, *, long_running: bool = False, console: bool = False) -> None:
    """Configure structlog and the stdlib bridge for this process. Safe to call more than once.

    `profile="service"`: the executor (`long_running=True`), its armed children, and the Sanic app.
    `profile="tool"`: the CLI root callback for every group except `agent`, and manual
    `python -m agri_data_service.*`/`scripts/*.py` runs. `console=True` renders human-readable text
    (structlog's `ConsoleRenderer`) instead of JSON, for a local TTY; redaction still runs first
    either way. `long_running` is recorded (`is_long_running_configured`) for
    `bootstrap.py`'s exit-line decision; it does not change the processor chain itself.
    """
    _configuration_state.long_running = long_running
    min_level = _resolve_min_level()
    final_renderer = (
        structlog.dev.ConsoleRenderer()
        if console
        else structlog.processors.JSONRenderer(default=redaction.redacting_fallback)
    )
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        _fail_open(structlog.processors.add_log_level),
        _fail_open(_railway_level_rename),
        _fail_open(structlog.processors.TimeStamper(fmt="iso", utc=True)),
        _fail_open(_turn_context_processor),
        _fail_open(_envelope_processor),
        _fail_open(structlog.processors.format_exc_info),
        _fail_open(_leaf_normalizer),
        _fail_open(_redaction_processor),
    ]
    if not console:
        processors.append(_fail_open(_line_clamp))
    processors.append(final_renderer)
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        context_class=dict,
        logger_factory=StreamSink(profile=profile),
        cache_logger_on_first_use=False,
    )
    _configure_stdlib_bridge(profile=profile, min_level=min_level)


# --- Deferred get_logger: resolves to structlog once configured, else a plain stderr writer --------


def _fallback_timestamp() -> str:
    return datetime.now(UTC).isoformat()


class _FallbackStderrLogger:
    """What `get_logger(name)` delegates to before `configure_logging` has ever run.

    Mirrors `jobs/worker.py`'s original pre-Wave-O behaviour (a plain stderr line, never stdout, so
    a cron container's stdout stays a pure JSON-lines report stream) without needing structlog to be
    configured first. Builds the payload through `redaction.redact_value` (the same key-aware rule
    the configured pipeline uses) BEFORE serialising, rather than running a value-only regex scrub
    over the already-dumped JSON text: a keyed field like `password=...` or `headers={"x-api-key":
    ...}` previously reached stderr verbatim, because the query-parameter regex needs a `name=value`
    shape a JSON `"password": "..."` never takes (security review). Also stamps `timestamp`/`service`
    so a pre-configure line carries the same envelope FR-30 requires everywhere else, and accepts
    `bind(**context)` so a caller that binds fields before `configure_logging` has run still gets them
    on every subsequent line from the bound logger.
    """

    def __init__(self, name: str | None, *, context: Mapping[str, object] | None = None) -> None:
        self._name = name
        self._context: dict[str, object] = dict(context) if context else {}

    def bind(self, **new_values: object) -> _FallbackStderrLogger:
        return _FallbackStderrLogger(self._name, context={**self._context, **new_values})

    def _write(self, _level: str, _event: str, **kwargs: object) -> None:
        # `_level`/`_event` (not `level`/`event`) so a caller's own `level=`/`event=` kwarg -- itself
        # a real bug worth reporting, not one worth crashing over -- lands in the payload rather than
        # colliding with this method's own named parameters and raising `TypeError` (security review).
        merged = {**self._context, **kwargs}
        payload: dict[str, object] = {
            "event": _event,
            "level": _level,
            "logger": self._name,
            "timestamp": _fallback_timestamp(),
            "service": _service_name(),
            **merged,
        }
        redacted = redaction.redact_value(payload)
        if not isinstance(redacted, dict):  # pragma: no cover - redact_value(dict) always returns dict
            redacted = payload
        try:
            line = json.dumps(redacted, default=repr)
        except Exception:  # never let a bad value break the fallback path itself
            line = json.dumps({"event": _event, "level": _level, "logging_error": "unserializable_payload"})
        sys.stderr.write(line + "\n")
        sys.stderr.flush()

    def debug(self, event: str, **kwargs: object) -> None:
        self._write("debug", event, **kwargs)

    def info(self, event: str, **kwargs: object) -> None:
        self._write("info", event, **kwargs)

    def warning(self, event: str, **kwargs: object) -> None:
        self._write("warn", event, **kwargs)

    warn = warning

    def error(self, event: str, **kwargs: object) -> None:
        self._write("error", event, **kwargs)

    def exception(self, event: str, **kwargs: object) -> None:
        self._write("error", event, **kwargs)

    def critical(self, event: str, **kwargs: object) -> None:
        self._write("error", event, **kwargs)


class _DeferredLogger:
    """Decides, on every call, whether structlog is configured yet -- never freezing that decision."""

    def __init__(self, name: str | None) -> None:
        self._name = name

    def _resolve(self) -> object:
        if structlog.is_configured():
            return structlog.get_logger(self._name)
        return _FallbackStderrLogger(self._name)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._resolve(), item)


def get_logger(name: str | None = None) -> _DeferredLogger:
    """A logger that decides, at every call, whether to delegate to structlog or a stderr fallback.

    Import order therefore never freezes a branch (design §1.1, BUI2-06): a module that does
    `logger = get_logger(__name__)` at import time gets a working logger whether or not
    `configure_logging` has run yet by the time that import happens.
    """
    return _DeferredLogger(name)
