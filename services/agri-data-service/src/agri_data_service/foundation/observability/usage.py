"""Host -> provider/pool resolution, the Open-Meteo weight table, and the turn/operator usage line.

See `foundation/observability/AGENTS.md` "Usage line" for the envelope this writes, the
bootstrap <-> usage contract `bootstrap.py` already documents, and the GL-2 hook point
(`_host_counters`) `ingest/http.py`'s metering hooks populate once `o3-ingest-meter` lands. Nothing
here calls into any first-party module outside `agri_data_service.foundation`
(`tests/test_layer_import_contract.py::LAYER_FORBIDDEN_IMPORTS["foundation"]`), so the Open-Meteo
weight formula is a pinned duplicate of G0's real one rather than a shared import -- see
`open_meteo_request_weight` below.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from datetime import date
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, get_args
from urllib.parse import parse_qs, urlsplit

from agri_data_service.foundation.observability import events, redaction

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.observability.vocabulary import PoolLabel

try:
    import resource
except ImportError:  # Windows has no `resource` module -- POSIX-only stdlib (design §6.2 Portability)
    resource = None  # type: ignore[assignment]

_TURN_ID_ENV_VAR: Final = "PLANTGEO_TURN_ID"
_USAGE_VERSION: Final = 1
_MAX_HOSTS_REPORTED: Final = 16

# Pools an operator-exit summary treats as "a weighted pool was touched" (design §1.4's usage-line
# level rule). Soil is the only Wave-O lane on a weighted provider today, and it only ever reaches
# Open-Meteo -- so both Open-Meteo pools are the complete set until a second weighted provider lands.
_WEIGHTED_POOLS: Final[frozenset[PoolLabel]] = frozenset({"open-meteo-paid", "open-meteo-free"})

# --- Host -> provider/pool resolution (design §2.3) ----------------------------------------------


#: Metering-and-report labels for keyless, unbudgeted providers. Deliberately NOT `vocabulary.PoolLabel`:
#: that set is what the usage report's month-to-date section walks, and WQ-4 enforces no budget on
#: these, so they label hosts in the per-lane x host section only (and stop it reading `provider None`).
MeteringOnlyPoolLabel = Literal["nasa-power", "nws", "arcgis-online"]
METERING_ONLY_POOL_LABELS: Final[frozenset[MeteringOnlyPoolLabel]] = frozenset(get_args(MeteringOnlyPoolLabel))


class HostResolution(NamedTuple):
    """One host's provider name and metering pool, as `usage.provider_for_host` resolves it."""

    provider: str
    pool: PoolLabel | MeteringOnlyPoolLabel


# Order matters: the `customer-` (paid) rule must be checked before the general Open-Meteo rule, or
# every paid host would also match the free one. Hosts are matched case-insensitively, stripped.
# `tests/lane_config/test_provider_hosts.py` fails when a `lanes/_providers/*.toml` host matches none.
_HOST_RULES: Final[tuple[tuple[re.Pattern[str], str, PoolLabel | MeteringOnlyPoolLabel], ...]] = (
    (re.compile(r"^customer-[^.]*\.open-meteo\.com$"), "open-meteo", "open-meteo-paid"),
    (re.compile(r"(^|\.)open-meteo\.com$"), "open-meteo", "open-meteo-free"),
    (re.compile(r"^firms\.modaps\.eosdis\.nasa\.gov$"), "firms", "firms"),
    (re.compile(r"^(waterservices|api\.waterdata)\.usgs\.gov$"), "usgs-water-data", "usgs-water-data"),
    (re.compile(r"^power\.larc\.nasa\.gov$"), "nasa-power", "nasa-power"),
    (re.compile(r"^api\.weather\.gov$"), "nws", "nws"),
    (re.compile(r"^services[0-9]*\.arcgis\.com$"), "arcgis-online", "arcgis-online"),
)


def provider_for_host(host: str) -> HostResolution | None:
    """Resolve a bare host to its provider name and metering pool; `None` for an unrecognised host."""
    normalized = host.strip().casefold()
    for pattern, provider, pool in _HOST_RULES:
        if pattern.search(normalized):
            return HostResolution(provider=provider, pool=pool)
    return None


# --- Open-Meteo request weight (design §2.1) ------------------------------------------------------

_DEFAULT_FORECAST_DAYS_ON_FORECAST_ENDPOINT: Final = 7
_FORECAST_ENDPOINT_SUFFIX: Final = "/v1/forecast"


def open_meteo_request_weight(locations: int, days: int, variables: int) -> float:
    """One request's Open-Meteo quota weight.

    Byte-identical to `pipeline/direct/soil/source.py::open_meteo_request_weight` -- deliberately a
    duplicate, not a shared import (`foundation` may not import outside itself); pinned equal by
    `test_weight_equals_g0_on_soil_request_builder`. No `models` factor: G0's real soil request
    builder this is pinned against never applies one (the design record's general weight-rule prose
    mentions `models` for a different lane's URL shape, not this signature -- see this module's
    AGENTS.md entry for the discrepancy).
    """
    return locations * max(1.0, days / 14) * max(1.0, variables / 10)


def _comma_item_count(value: str | None) -> int:
    if not value:
        return 0
    return len([item for item in value.split(",") if item.strip()])


class _DateParams(NamedTuple):
    """The raw query values `_resolve_days` needs, bundled to keep its own signature to two params."""

    start_date: str | None
    end_date: str | None
    past_days_raw: str | None
    forecast_days_raw: str | None
    current_raw: str | None


def _resolve_days(params: _DateParams, *, path: str) -> int:
    if params.start_date and params.end_date:
        return (date.fromisoformat(params.end_date) - date.fromisoformat(params.start_date)).days + 1
    if params.past_days_raw is None and params.forecast_days_raw is None and params.current_raw is not None:
        return 1
    past_days = int(params.past_days_raw) if params.past_days_raw is not None else 0
    if params.forecast_days_raw is not None:
        forecast_days = int(params.forecast_days_raw)
    elif path.endswith(_FORECAST_ENDPOINT_SUFFIX):
        forecast_days = _DEFAULT_FORECAST_DAYS_ON_FORECAST_ENDPOINT
    else:
        forecast_days = 0
    return past_days + forecast_days


def open_meteo_weight_for_url(url: str) -> float:
    """Parse one Open-Meteo request URL and return its quota weight (design §2.1's shape table)."""
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)

    def _first(name: str) -> str | None:
        values = params.get(name)
        return values[0] if values else None

    locations = _comma_item_count(_first("latitude")) or 1
    date_params = _DateParams(
        start_date=_first("start_date"),
        end_date=_first("end_date"),
        past_days_raw=_first("past_days"),
        forecast_days_raw=_first("forecast_days"),
        current_raw=_first("current"),
    )
    days = _resolve_days(date_params, path=parsed.path)
    variables = sum(_comma_item_count(_first(name)) for name in ("hourly", "daily", "current", "minutely_15"))
    return open_meteo_request_weight(locations=locations, days=days, variables=variables)


# --- Per-process host counters (GL-2's hook point; empty until o3-ingest-meter wires it) ----------
#
# `ingest/http.py`'s request/response hooks populate this once GL-2 lands. At GL-1 nothing writes to
# it, so every usage line legitimately has zero hosts -- `write_usage_line` still writes one
# (`test_zero_host_usage_line_is_still_written`), because "no sends this process" is itself the
# audit answer, not a reason to skip the line.
_host_counters: dict[str, dict[str, object]] = {}

# --- Foundation-level meter-error counter (o5a's granted extension; GL-2 review MEDIUM #2) --------
#
# `ingest/http.py::_note_meter_error` keeps its OWN per-process counter for its `meter_error_count()`
# test seam, and ALSO calls `record_meter_error()` here -- the usage line's top-level `meter_errors`
# is written by THIS module, so hardcoding it to `0` meant a fail-open metering fault never reached
# the one audit line an operator actually reads. Two counters, one per module, is deliberate: neither
# module reaches into the other's private state (the same rule `_host_counters` itself follows).
_meter_errors: int = 0


def record_meter_error() -> None:
    """Count one fail-open metering fault so the usage line's `meter_errors` is never hardcoded."""
    global _meter_errors  # noqa: PLW0603 - the documented per-process fault counter
    with contextlib.suppress(Exception):
        _meter_errors += 1


def _bounded_hosts() -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    items = list(_host_counters.items())
    kept = dict(items[:_MAX_HOSTS_REPORTED])
    overflow = items[_MAX_HOSTS_REPORTED:]
    other: dict[str, object] = {"folded_host_count": len(overflow)} if overflow else {}
    return kept, other


def _weighted_pool_touched(hosts: Mapping[str, dict[str, object]]) -> bool:
    return any(entry.get("pool") in _WEIGHTED_POOLS for entry in hosts.values())


def _latest_send_outcome(hosts: Mapping[str, dict[str, object]]) -> tuple[object, float | None]:
    """The most recent `last_send_outcome`/`last_send_at` pair across every metered host this process.

    Mirrors `router.py::ChildLogRouter.usage_summary`'s own "latest by `last_send_at` wins" rule
    (design §1.6 point 2), so a turn's OWN process-local usage line agrees with what the router
    later folds from a child's -- the same tie-break, applied to the same shape, in two places
    because `foundation` may not import a helper the other reaches for.
    """
    winning_outcome: object = None
    winning_at: float | None = None
    for entry in hosts.values():
        outcome = entry.get("last_send_outcome")
        sent_at = entry.get("last_send_at")
        sent_at_value = sent_at if isinstance(sent_at, (int, float)) else None
        if outcome is None or sent_at_value is None:
            continue
        if winning_at is None or sent_at_value >= winning_at:
            winning_outcome = outcome
            winning_at = sent_at_value
    return winning_outcome, winning_at


def _rss_peak_kib() -> int | None:
    """The process's peak resident set size, or `None` where `resource` does not exist (Windows).

    Reached through `getattr` rather than `resource.getrusage`/`resource.RUSAGE_SELF` directly: mypy
    resolves the `resource` stub for the platform it runs on, so a straight attribute access reports
    `Module has no attribute "getrusage"`/`"RUSAGE_SELF"` when type-checked on a platform that lacks
    the module (this repo's own CI/dev machine is Windows) even though the `resource is None` guard
    above already makes the access unreachable there at runtime.
    """
    if resource is None:
        return None
    getrusage = getattr(resource, "getrusage", None)
    rusage_self = getattr(resource, "RUSAGE_SELF", None)
    if getrusage is None or rusage_self is None:
        return None
    try:
        usage = getrusage(rusage_self)
    except (AttributeError, OSError):
        return None
    return int(usage.ru_maxrss)


def _cpu_seconds() -> float:
    """Process CPU time via the stdlib's cross-platform clock, not `resource` (which Windows lacks)."""
    return round(time.process_time(), 3)


# --- Raw, unbuffered writes (mirrors `bootstrap.py::_raw_write_stdout`'s flush-first shape) --------


def _raw_write(fd: int, payload: dict[str, object]) -> None:
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    with contextlib.suppress(Exception):
        sys.stderr.flush()
    # Redacted here, not only downstream: GL-2 (`o3-ingest-meter`) will fill `_host_counters` and
    # `last_send_outcome` from real request/response data, and neither this channel nor
    # `router.py::_consume_usage_line` redacted it before now (security review) -- a keyed URL in an
    # error description would otherwise flow, unredacted, straight into the parent's usage line.
    redacted = redaction.redact_value(payload)
    if not isinstance(redacted, dict):  # pragma: no cover - redact_value(dict) always returns dict
        redacted = payload
    line = json.dumps(redacted, default=str).encode("utf-8")
    os.write(fd, b"\n" + line + b"\n")


def _operator_entry() -> str:
    """The `-m` module or script path this operator process was invoked as (redacted before use)."""
    orig_argv = getattr(sys, "orig_argv", None) or []
    if "-m" in orig_argv:
        index = orig_argv.index("-m") + 1
        if index < len(orig_argv):
            return redaction.redact_for_log(orig_argv[index])
    for arg in orig_argv:
        if arg.endswith(".py"):
            return redaction.redact_for_log(arg)
    return "unknown"


# --- write_usage_line: the bootstrap <-> usage contract --------------------------------------------


def write_usage_line(*, pid: int, opening: bool) -> None:
    """Write this process's usage line: the internal turn line, or the `plantgeo_source_usage` audit line.

    Called by `bootstrap.py` exactly as `bootstrap.py::_write_usage_line` describes: lazily, by name,
    and always guarded there too. This function guards itself again (belt and suspenders, matching
    every other fail-open branch in this package) -- a usage-reporting fault must never raise into
    the caller's `atexit` hook or its raw stdout write.

    `opening=True` is accepted for symmetry with `bootstrap.py`'s own inline
    `plantgeo_turn_usage_open` write (see this module's AGENTS.md entry for why bootstrap does not
    actually call this branch today); it only ever does something for a turn child, since only a
    turn has an "open" marker to write.
    """
    try:
        _write_usage_line_unguarded(pid=pid, opening=opening)
    except Exception:  # usage reporting must never break the process it instruments
        return


def _write_usage_line_unguarded(*, pid: int, opening: bool) -> None:
    turn_id = os.environ.get(_TURN_ID_ENV_VAR, "")
    if turn_id:
        _write_turn_line(pid=pid, turn_id=turn_id, opening=opening)
        return
    if opening:
        return  # only a turn child ever writes an "opening" marker
    _write_operator_line(pid=pid)


def _write_turn_line(*, pid: int, turn_id: str, opening: bool) -> None:
    if opening:
        _raw_write(
            1,
            {
                "event": events.EVENT_TURN_USAGE_OPEN,
                "level": "debug",
                "pid": pid,
                "turn_id": turn_id,
                "opened_at": time.time(),
            },
        )
        return
    hosts, other = _bounded_hosts()
    outcome, sent_at = _latest_send_outcome(hosts)
    _raw_write(
        1,
        {
            "event": events.EVENT_TURN_USAGE,
            "level": "debug",
            "usage_version": _USAGE_VERSION,
            "pid": pid,
            "turn_id": turn_id,
            "hosts": hosts,
            "other": other,
            "meter_errors": _meter_errors,
            "last_send_outcome": outcome,
            "last_send_at": sent_at,
            "rss_peak_kib": _rss_peak_kib(),
            "cpu_seconds": _cpu_seconds(),
            "closed_at": time.time(),
        },
    )


def _write_operator_line(*, pid: int) -> None:
    hosts, other = _bounded_hosts()
    level = "warn" if _weighted_pool_touched(hosts) else "info"
    _raw_write(
        2,
        {
            "event": events.EVENT_SOURCE_USAGE,
            "level": level,
            "usage_version": _USAGE_VERSION,
            "pid": pid,
            "run_origin": "operator",
            "entry": _operator_entry(),
            "hosts": hosts,
            "other": other,
            "meter_errors": _meter_errors,
            "rss_peak_kib": _rss_peak_kib(),
            "cpu_seconds": _cpu_seconds(),
        },
    )
