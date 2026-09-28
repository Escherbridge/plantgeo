"""Classify one child's terminal result into the frozen `ExitClass` vocabulary (spec Sec 4.9.3).

Pure and DB-free: no I/O, no subprocess, no session. `classify_exit` is o5a's single call site
(`job_executor_service.py::run_scheduled_command`) for stamping `metrics.exit_class`. The stamp was
observational at GL-3 (FR-33); from G1 the hold ladder reads it back to pick a lane's probe ladder
(`lane_incidents.py::HoldLadders`), and nothing here holds a lane itself.

**Evidence rules (R1-R4, spec Sec 4.9.3, design record Sec 3.1.1).** Applied only to a "legacy"
non-zero exit -- one that is not itself one of the explicit signals (75/70/78/2, a timeout, a lost
fence, or a pre-spawn failure) -- because those already say what they are without evidence:

- **R1 message**: the report's `error` field contains `ingest/http.py::UpstreamHttpError`'s exact
  message (`upstream request failed with status 429|5xx`) or names a transient class
  (`UpstreamTimeoutError`, `UpstreamTransportError`, `OpenMeteoRateLimitError`). Covers vegetation
  (`VegetationSourceError: vegetation <day>: upstream request failed with status 5xx`, from
  `pipeline/direct/vegetation/source.py`) and watersheds (`DirectWatershedsError: watersheds
  snapshot fetch: upstream request failed...`, from `pipeline/direct/watersheds/forward.py`)
  verbatim. Climate's real fetch path (`pipeline/direct/climate/source.py::_fetch_cell_day`) never
  raises `UpstreamHttpError` for a bad status -- it checks `response.ok` itself and phrases the
  failure as `"NASA POWER answered <status> for ..."` -- so `_R1_CLIMATE_STATUS_PATTERN` matches
  that phrasing too; a genuine transport fault (`except UpstreamError as error: ...` at the same
  call site) still carries the literal `UpstreamTimeoutError`/`UpstreamTransportError` name.
- **R2 pair**: the report is the one-JSON-object shape `pipeline/parquet/water_gauges_forward.py`,
  `pipeline/direct/sensors/forward.py` and `pipeline/direct/weather_observations/forward.py` all
  emit from the same `except Exception as error: emit("<lane>_forward_failed", error_type=type
  (error).__name__, detail=str(error))` -- an `error_type` naming a transient class, and, when it
  is `UpstreamHttpError` specifically, a `detail` that itself holds the status message (its
  `__init__` never adds a class prefix the way the R1 shape's `f"{type(error).__name__}: {error}"`
  does).
- **R3 wrapper + meter**: `WRAPPER_EVIDENCE[lane]` matches AND the usage line's `last_send_outcome`
  is one of `{429, 5xx, transport}`. The wrapper text alone never carries a status -- every R3 lane's
  retry helper (`drought/forward.py::_retry_async`, `fire_perimeters/forward.py`,
  `evacuation_zones/forward.py`, `burn_severity/forward.py`, `fire_detections/support.py::
  retry_async`) raises `f"{label} failed after {attempts} attempts"` with the underlying status
  swallowed into `from last_error` -- so the meter is the only place that status survives.
- **R4 infra**: an infra token (`EndpointConnectionError`, `ConnectTimeoutError`, `SlowDown`,
  `ServiceUnavailable`, `InternalError`, `OperationalError`, `ConnectionDoesNotExistError`,
  `CannotConnectNowError`, `ConnectionResetError`, or the phrase `could not connect to server`)
  survives a wrap; an R2/DB census failure is the design's stated case.

**Conflict -> `code`.** Any R1/R2/R3/R4 match is discarded if the SAME evidence text also names an
exception class this module does not recognise (`KeyError:`, say) -- the report's own leading
`f"{type(error).__name__}: {error}"` class name is exempt (every pipeline/direct report starts with
one; see `_split_leading_class`), only a SECOND, unexpected class token downgrades the match. A
wrapper whose `last_send_outcome` reads a successful status (`2xx`/`3xx`/`4xx`) is a conflict too:
the retries stopped for a reason the meter says was not transient.

**With no report** (a killed or telemetry-off child), only the LAST `^[A-Za-z_.]+(Error|Exception):
` line of the bounded stderr tail is read (spec Sec 4.9.3); an earlier matching line is not evidence.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.observability.vocabulary import ExitClass

# --- Explicit exit-code and monitor signals (spec Sec 4.9.3's table; no evidence needed) ---------

#: A modern writer's own "the upstream would not settle" signal.
_EXIT_CODE_UPSTREAM: Final = 75
#: A modern writer's own "this is a bug" signal.
_EXIT_CODE_BUG: Final = 70
#: A modern writer's own "this is misconfigured" signal.
_EXIT_CODE_CONFIG: Final = 78
#: argparse's usage-error convention; grouped with `_EXIT_CODE_CONFIG` per the spec table's "78; 2".
_EXIT_CODE_USAGE: Final = 2

# --- R1/R2 transient tokens (ingest/http.py, ingest/open_meteo.py; UpstreamHttpError is separate,
# since ITS message alone -- not its class name -- is what R1/R2 look for) -----------------------

_TRANSIENT_CLASSES: Final[frozenset[str]] = frozenset(
    {"UpstreamTimeoutError", "UpstreamTransportError", "OpenMeteoRateLimitError"}
)
_UPSTREAM_HTTP_ERROR_CLASS: Final = "UpstreamHttpError"

#: `ingest/http.py::_send_outcome`'s failure-shaped labels; `"429"` is kept distinct from `"4xx"`
#: there, and neither `"2xx"`, `"3xx"` nor plain `"4xx"` counts as transient for R3.
_R3_TRANSIENT_SEND_OUTCOMES: Final[frozenset[str]] = frozenset({"429", "5xx", "transport"})

#: `UpstreamHttpError.__init__`'s exact wording (`ingest/http.py`); `\b` keeps `4290` from matching.
_R1_HTTP_ERROR_PATTERN: Final = re.compile(r"upstream request failed with status (?:429|5\d\d)\b")
#: `pipeline/direct/climate/source.py::_fetch_cell_day`'s home-grown phrasing for the same fault.
_R1_CLIMATE_STATUS_PATTERN: Final = re.compile(r"\banswered (?:429|5\d\d)\b")

# --- R4 infra tokens (design record Sec 3.1.1; botocore and asyncpg/psycopg names) ---------------

_INFRA_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "EndpointConnectionError",
        "ConnectTimeoutError",
        "SlowDown",
        "ServiceUnavailable",
        "InternalError",
        "OperationalError",
        "ConnectionDoesNotExistError",
        "CannotConnectNowError",
        "ConnectionResetError",
    }
)
_INFRA_PHRASE: Final = "could not connect to server"

#: Every class name R1/R2/R4 may legitimately name inside one message without that being a second,
#: conflicting class; a fresh name outside this set (`KeyError`, say) downgrades the match to `code`.
_ALLOWED_EVIDENCE_CLASSES: Final[frozenset[str]] = _TRANSIENT_CLASSES | _INFRA_CLASSES | {_UPSTREAM_HTTP_ERROR_CLASS}

#: Any `Word...Error`/`Word...Exception` token, so a stray `KeyError:` inside an otherwise-matching
#: message is found without hand-listing every exception class this codebase could ever raise.
_CLASS_TOKEN_PATTERN: Final = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b")

#: The report's own leading `f"{type(error).__name__}: {error}"` prefix (every pipeline/direct
#: `main()` prints this shape); stripped before conflict-scanning so naming the report's OWN wrapper
#: class is never itself a conflict.
_LEADING_CLASS_PATTERN: Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):\s?(.*)$", re.DOTALL)
#: The stderr-fallback rule (spec Sec 4.9.3): dotted paths allowed, one line, no report at all.
_STDERR_LAST_EXCEPTION_PATTERN: Final = re.compile(r"^([A-Za-z_.]+(?:Error|Exception)): (.*)$")

#: R3's registered wrapper pattern per cohort-1 lane (spec Sec 4.9.5: these rows retire with
#: `c7-quarantine-1`, together, so each lane keeps its own entry rather than sharing one constant).
#: Every entry matches the same `_retry_async`/`retry_async` phrasing today (`"{label} failed after
#: {attempts} attempts"`, from `drought/forward.py`, `drought/usdm.py`, `fire_perimeters/forward.py`,
#: `fire_perimeters/source.py`, `evacuation_zones/forward.py`, `evacuation_zones/source.py`,
#: `burn_severity/forward.py`, `burn_severity/mtbs.py`, `fire_detections/support.py::retry_async`),
#: but the registry stays per-lane because nothing requires that to keep being true.
WRAPPER_EVIDENCE: Final[dict[str, re.Pattern[str]]] = {
    "drought": re.compile(r"\bfailed after \d+ attempts\b"),
    "fire-perimeters": re.compile(r"\bfailed after \d+ attempts\b"),
    "evacuation-zones": re.compile(r"\bfailed after \d+ attempts\b"),
    "burn-severity": re.compile(r"\bfailed after \d+ attempts\b"),
    "fire-detections": re.compile(r"\bfailed after \d+ attempts\b"),
}


class _Evidence(NamedTuple):
    """One report's classifying class name (if the shape names one directly) and its message text."""

    class_hint: str | None
    text: str


def _split_leading_class(raw: str) -> tuple[str | None, str]:
    """Strip a `"ClassName: message"` prefix, keeping only the short (undotted) class name."""
    match = _LEADING_CLASS_PATTERN.match(raw)
    if match is None:
        return None, raw
    return match.group(1).rsplit(".", 1)[-1], match.group(2)


def _stderr_fallback_evidence(stderr_tail: str) -> _Evidence:
    """Read only the LAST matching exception line of the tail; an earlier one is not evidence."""
    last: re.Match[str] | None = None
    for line in stderr_tail.splitlines():
        match = _STDERR_LAST_EXCEPTION_PATTERN.match(line.strip())
        if match is not None:
            last = match
    if last is None:
        return _Evidence(None, "")
    class_name, text = _split_leading_class(f"{last.group(1)}: {last.group(2)}")
    return _Evidence(class_name, text)


def _report_evidence(report: Mapping[str, object]) -> _Evidence:
    """Read the R1 (`error`) or R2 (`error_type`/`detail`) shape off one parsed terminal report."""
    error_type = report.get("error_type")
    if isinstance(error_type, str) and error_type:
        detail = report.get("detail")
        return _Evidence(error_type, str(detail) if detail is not None else "")
    raw_error = report.get("error")
    if isinstance(raw_error, str) and raw_error:
        return _Evidence(*_split_leading_class(raw_error))
    raw_detail = report.get("detail")
    if isinstance(raw_detail, str) and raw_detail:
        return _Evidence(None, raw_detail)
    return _Evidence(None, "")


def _evidence(report: Mapping[str, object] | None, stderr_tail: str) -> _Evidence:
    """Dispatch to the report shape when there is one, else the stderr-tail fallback (spec Sec 4.9.3)."""
    if report is not None:
        return _report_evidence(report)
    return _stderr_fallback_evidence(stderr_tail)


def _conflicting_class(text: str) -> bool:
    """True when `text` names an exception class outside `_ALLOWED_EVIDENCE_CLASSES` (design Sec 3.1.1)."""
    return any(token not in _ALLOWED_EVIDENCE_CLASSES for token in _CLASS_TOKEN_PATTERN.findall(text))


def _matches_upstream_message(evidence: _Evidence) -> bool:
    """R1 (message) and R2's non-`UpstreamHttpError` half: a transient class or its literal message."""
    if evidence.class_hint == _UPSTREAM_HTTP_ERROR_CLASS:
        return bool(_R1_HTTP_ERROR_PATTERN.search(evidence.text))
    if evidence.class_hint in _TRANSIENT_CLASSES:
        return True
    if _R1_HTTP_ERROR_PATTERN.search(evidence.text) or _R1_CLIMATE_STATUS_PATTERN.search(evidence.text):
        return True
    return any(token in evidence.text for token in _TRANSIENT_CLASSES)


def _matches_infra(evidence: _Evidence) -> bool:
    """R4: an infra token as the report's own class, embedded in its text, or the raw connect phrase."""
    if evidence.class_hint in _INFRA_CLASSES:
        return True
    if any(token in evidence.text for token in _INFRA_CLASSES):
        return True
    return _INFRA_PHRASE in evidence.text.casefold()


def _matches_wrapper(lane_id: str | None, evidence: _Evidence, *, last_send_outcome: str | None) -> bool:
    """R3: the lane's registered wrapper text AND a transient metered `last_send_outcome`."""
    if lane_id is None or last_send_outcome not in _R3_TRANSIENT_SEND_OUTCOMES:
        return False
    pattern = WRAPPER_EVIDENCE.get(lane_id)
    return pattern is not None and bool(pattern.search(evidence.text))


def classify_exit(  # noqa: PLR0911, PLR0913 - one keyword per spec 4.9.3 input, one return per table row
    *,
    return_code: int | None,
    pre_spawn: bool = False,
    timed_out: bool = False,
    fence_lost: bool = False,
    report: Mapping[str, object] | None = None,
    stderr_tail: str = "",
    last_send_outcome: str | None = None,
    lane_id: str | None = None,
    native: bool = False,
) -> ExitClass:
    """Stamp one child's terminal result with its `ExitClass` (spec Sec 4.9.3's table; the G1 ladder acts on it).

    `return_code` is `None` exactly when the command never started (`pre_spawn`) or the monitor
    stopped it before it could exit (`timed_out`, `fence_lost`); those three flags are checked first
    because they say what happened without looking at any report. A `return_code` of 0 with no
    `report` is `report_missing` (design Sec 3.1), never `ok`. 75/70/78/2 are explicit, evidence-free
    signals (spec Sec 4.9.3's table); every other non-zero code is a "legacy" exit and runs the R1-R4
    evidence rules in `_matches_infra` -> `_matches_upstream_message` -> `_matches_wrapper` order, R4
    first because an infra failure is never also upstream evidence in practice. Any match a
    `_conflicting_class` also holds falls through to `code`, and so does no match at all.

    `native` is the config runner's S4 contract (spec S4): it speaks 0/75/70/78 itself, so any other
    non-zero code is `code` and the legacy evidence rules never read its output.
    """
    if pre_spawn:
        return "config"
    if fence_lost:
        return "lease_lost"
    if timed_out:
        return "hang"
    if return_code is None:  # defensive: none of the three flags above fired, yet nothing exited either
        return "code"
    if return_code == 0:
        return "ok" if report is not None else "report_missing"
    if return_code == _EXIT_CODE_UPSTREAM:
        return "upstream"
    if return_code == _EXIT_CODE_BUG:
        return "code"
    if return_code in (_EXIT_CODE_CONFIG, _EXIT_CODE_USAGE):
        return "config"
    if native:
        return "code"

    evidence = _evidence(report, stderr_tail)
    if _matches_infra(evidence) and not _conflicting_class(evidence.text):
        return "infra"
    if _matches_upstream_message(evidence) and not _conflicting_class(evidence.text):
        return "upstream"
    if _matches_wrapper(lane_id, evidence, last_send_outcome=last_send_outcome) and not _conflicting_class(
        evidence.text
    ):
        return "upstream"
    return "code"


__all__ = ["WRAPPER_EVIDENCE", "classify_exit"]
