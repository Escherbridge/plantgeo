"""`classify_exit` against every §4.9.3 lane's REAL `main()` failure print, and the R1-R4 edge rules.

Each fixture below is not invented JSON: it is the exact shape and wrapping chain the named lane's
own source prints today, read directly out of the repository (cited per case). `pipeline/direct/*`
lanes all share one `main()` idiom (`except Exception as error: print(json.dumps({"status": "failed",
"error": f"{type(error).__name__}: {error}"}))`); the R2 lanes (`water_gauges_forward.py`,
`sensors/forward.py`, `weather_observations/forward.py`) share another
(`emit("<lane>_forward_failed", error_type=type(error).__name__, detail=str(error))`). No child
process runs here -- these are the JSON payloads those idioms produce, held as Python dicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.exit_classes import WRAPPER_EVIDENCE, classify_exit

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

#: Every real lane's `except Exception` branch in `main()` returns exactly this for an unhandled fault.
_LEGACY_NON_ZERO_EXIT = 1


def _direct_report(wrapper_class: str, message: str) -> Mapping[str, object]:
    """The `{"status": "failed", "error": ...}` shape every `pipeline/direct/*/forward.py::main()` prints."""
    return {"status": "failed", "error": f"{wrapper_class}: {message}"}


def _forward_failed_report(event: str, error_type: str, detail: str) -> Mapping[str, object]:
    """The one-JSON-object shape the R2 lanes' own `except Exception` branch emits on stdout."""
    return {"event": event, "error_type": error_type, "detail": detail}


# --- R1 lanes: climate, vegetation, watersheds (design record Sec 3.1.1) -------------------------
#
# vegetation and watersheds route a genuine `UpstreamHttpError` straight to `main()`'s broad except
# (`vegetation/source.py:289`'s `except UpstreamError as error: raise VegetationSourceError(f"vegetation
# {day}: {error}") from error`; watersheds never even catches it -- `ingest/watersheds.py::fetch_watersheds`
# uses `fetch_bounded_json`, which raises `UpstreamHttpError` directly, and `forward.py:279` only
# catches the unrelated `WatershedsSourceError`). Climate's own fetch (`climate/source.py::_fetch_cell_day`)
# checks `response.ok` itself rather than calling `fetch_bounded_json`/`_text`, so its real message
# reads `"NASA POWER answered <status> ..."`, and the module docstring's `_R1_CLIMATE_STATUS_PATTERN`
# exists to still recognise it; 429 there is `ClimateProviderDeferredError` re-raised bare (`adapter.py`
# lines 108-110), never wrapped in `DirectClimateFieldError`.

_R1_LANE_CASES: tuple[tuple[str, Mapping[str, object], Mapping[str, object], Mapping[str, object]], ...] = (
    (
        "climate",
        _direct_report(
            "ClimateProviderDeferredError",
            "NASA POWER answered 429 for support-cell-1892 2026-09-15; deferred until a later turn",
        ),
        _direct_report(
            "DirectClimateFieldError",
            "nasa-power-precipitation 2026-09-15: NASA POWER answered 503 for support-cell-1892 2026-09-15",
        ),
        _direct_report("KeyError", "'nasa-power-precipitation'"),
    ),
    (
        "vegetation",
        _direct_report("VegetationSourceError", "vegetation 2026-09-15: upstream request failed with status 429"),
        _direct_report("VegetationSourceError", "vegetation 2026-09-15: upstream request failed with status 503"),
        _direct_report("KeyError", "'sentinel2_ndvi'"),
    ),
    (
        "watersheds",
        _direct_report("UpstreamHttpError", "upstream request failed with status 429"),
        _direct_report("UpstreamHttpError", "upstream request failed with status 503"),
        _direct_report("KeyError", "'huc12'"),
    ),
)

# --- R2 lanes: water gauges, sensors, weather observations (their shared `_forward_failed` shape) -

_R2_LANE_CASES: tuple[tuple[str, Mapping[str, object], Mapping[str, object], Mapping[str, object]], ...] = (
    (
        "water-gauges",
        _forward_failed_report(
            "water_gauges_forward_failed", "UpstreamHttpError", "upstream request failed with status 429"
        ),
        _forward_failed_report(
            "water_gauges_forward_failed", "UpstreamHttpError", "upstream request failed with status 503"
        ),
        _forward_failed_report("water_gauges_forward_failed", "KeyError", "'site_no'"),
    ),
    (
        "sensors",
        _forward_failed_report(
            "sensors_forward_failed", "UpstreamHttpError", "upstream request failed with status 429"
        ),
        _forward_failed_report(
            "sensors_forward_failed", "UpstreamHttpError", "upstream request failed with status 503"
        ),
        _forward_failed_report("sensors_forward_failed", "KeyError", "'station_id'"),
    ),
    (
        "weather-observations",
        _forward_failed_report(
            "weather_observations_forward_failed",
            "UpstreamHttpError",
            "upstream request failed with status 429",
        ),
        _forward_failed_report(
            "weather_observations_forward_failed",
            "UpstreamHttpError",
            "upstream request failed with status 503",
        ),
        _forward_failed_report("weather_observations_forward_failed", "KeyError", "'usaf'"),
    ),
)

# --- R3 lanes: drought, fire-perimeters, evacuation-zones, burn-severity, fire-detections ---------
#
# Every retry helper (`drought/forward.py::_retry_async`, `drought/usdm.py`, `fire_perimeters/forward.py`,
# `fire_perimeters/source.py`, `evacuation_zones/forward.py`, `evacuation_zones/source.py`,
# `burn_severity/forward.py`, `burn_severity/mtbs.py`, `fire_detections/support.py::retry_async`)
# raises the identical `f"{label} failed after {attempts} attempts"` with the underlying status
# swallowed into `from last_error` -- the wrapper text alone never carries "429"/"503", which is
# exactly why R3 also requires a transient `last_send_outcome` from the meter.

_R3_LANE_CASES: tuple[tuple[str, str, Mapping[str, object], Mapping[str, object], Mapping[str, object]], ...] = (
    (
        "drought",
        "drought",
        _direct_report("DirectDroughtError", "usdm weekly census fetch failed after 3 attempts"),
        _direct_report("DirectDroughtError", "usdm weekly census fetch failed after 3 attempts"),
        _direct_report("KeyError", "'fips'"),
    ),
    (
        "fire-perimeters",
        "fire-perimeters",
        _direct_report("DirectFirePerimetersError", "nifc perimeters fetch failed after 3 attempts"),
        _direct_report("DirectFirePerimetersError", "nifc perimeters fetch failed after 3 attempts"),
        _direct_report("KeyError", "'perimeter_id'"),
    ),
    (
        "evacuation-zones",
        "evacuation-zones",
        _direct_report("DirectEvacuationZonesError", "evacuation zones census fetch failed after 3 attempts"),
        _direct_report("DirectEvacuationZonesError", "evacuation zones census fetch failed after 3 attempts"),
        _direct_report("KeyError", "'zone_id'"),
    ),
    (
        "burn-severity",
        "burn-severity",
        _direct_report("DirectBurnSeverityError", "mtbs snapshot fetch failed after 3 attempts"),
        _direct_report("DirectBurnSeverityError", "mtbs snapshot fetch failed after 3 attempts"),
        _direct_report("KeyError", "'event_id'"),
    ),
    (
        "fire-detections",
        "fire-detections",
        _direct_report("PipelineOperationError", "firms detections fetch failed after 3 attempts"),
        _direct_report("PipelineOperationError", "firms detections fetch failed after 3 attempts"),
        _direct_report("KeyError", "'latitude'"),
    ),
)


def _legacy_lane_sweep_params() -> list[Any]:  # ParameterSet is pytest-private; Any is the honest type
    """Flatten every R1/R2/R3 lane's (429, 503, KeyError) triple into one parametrised table."""
    params: list[Any] = []
    for label, report_429, report_503, report_key_error in (*_R1_LANE_CASES, *_R2_LANE_CASES):
        params.append(pytest.param(label, None, report_429, None, "upstream", id=f"{label}-429"))
        params.append(pytest.param(label, None, report_503, None, "upstream", id=f"{label}-503"))
        params.append(pytest.param(label, None, report_key_error, None, "code", id=f"{label}-KeyError"))
    for label, lane_id, report_429, report_503, report_key_error in _R3_LANE_CASES:
        params.append(pytest.param(label, lane_id, report_429, "429", "upstream", id=f"{label}-429"))
        params.append(pytest.param(label, lane_id, report_503, "5xx", "upstream", id=f"{label}-503"))
        params.append(pytest.param(label, lane_id, report_key_error, None, "code", id=f"{label}-KeyError"))
    return params


@pytest.mark.parametrize(("label", "lane_id", "report", "last_send_outcome", "expected"), _legacy_lane_sweep_params())
def test_each_legacy_lane_real_failure_print_classifies(
    label: str,
    lane_id: str | None,
    report: Mapping[str, object],
    last_send_outcome: str | None,
    expected: str,
) -> None:
    """Every §4.9.3 lane's real `main()` print, at 429 and 503, classifies `upstream`; a bug is `code`."""
    del label  # carried only for the parametrize id
    assert (
        classify_exit(
            return_code=_LEGACY_NON_ZERO_EXIT,
            report=report,
            last_send_outcome=last_send_outcome,
            lane_id=lane_id,
        )
        == expected
    )


# --- R3's own two edge rules: the wrapper alone is never enough ----------------------------------


def test_wrapper_needs_a_failed_last_send() -> None:
    """The wrapper text matches, but there is no usage line (telemetry off, or the child was killed first)."""
    report = _direct_report("DirectDroughtError", "usdm weekly census fetch failed after 3 attempts")
    assert (
        classify_exit(return_code=_LEGACY_NON_ZERO_EXIT, report=report, last_send_outcome=None, lane_id="drought")
        == "code"
    )


@pytest.mark.parametrize("last_send_outcome", ["2xx", "3xx", "4xx"])
def test_wrapper_after_successful_last_send_is_code(last_send_outcome: str) -> None:
    """The wrapper matches, but the meter's last send was not transient: the retries stopped for a reason
    the meter says is not upstream, so a conflicting bug is more likely than a fault the ladder should probe."""
    report = _direct_report("DirectFirePerimetersError", "nifc perimeters fetch failed after 3 attempts")
    assert (
        classify_exit(
            return_code=_LEGACY_NON_ZERO_EXIT,
            report=report,
            last_send_outcome=last_send_outcome,
            lane_id="fire-perimeters",
        )
        == "code"
    )


# --- R4: infra tokens that survive a wrap ---------------------------------------------------------


@pytest.mark.parametrize(
    "report",
    [
        pytest.param(
            _forward_failed_report(
                "sensors_forward_failed", "OperationalError", "could not connect to server: Connection refused"
            ),
            id="operational-error-detail-phrase",
        ),
        pytest.param(
            _forward_failed_report(
                "sensors_forward_failed", "EndpointConnectionError", "Could not connect to the endpoint URL"
            ),
            id="endpoint-connection-error-as-error-type",
        ),
        pytest.param(
            _direct_report(
                "DirectWatershedsError", "watersheds snapshot fetch: ConnectionResetError: connection reset by peer"
            ),
            id="connection-reset-embedded-in-a-direct-wrap",
        ),
    ],
)
def test_infra_tokens(report: Mapping[str, object]) -> None:
    """An R2/DB census failure whose infra token (botocore or the DB driver) survives the wrap is `infra`."""
    assert classify_exit(return_code=_LEGACY_NON_ZERO_EXIT, report=report) == "infra"


# --- Conflict: a second, unrecognised exception class downgrades a real match to `code` ----------


@pytest.mark.parametrize(
    "report",
    [
        pytest.param(
            _direct_report(
                "VegetationSourceError",
                "vegetation 2026-09-15: upstream request failed with status 503 while indexing "
                "support cell KeyError: 'cell_042'",
            ),
            id="r1-message-plus-embedded-key-error",
        ),
        pytest.param(
            _forward_failed_report(
                "water_gauges_forward_failed",
                "UpstreamHttpError",
                "upstream request failed with status 503; TypeError: 'NoneType' object is not subscriptable",
            ),
            id="r2-detail-plus-embedded-type-error",
        ),
    ],
)
def test_conflict_is_code(report: Mapping[str, object]) -> None:
    """A field that names a real evidence match AND a non-allow-listed exception class is `code` (design Sec 3.1.1:
    "a conflict is ... an R1-R4 field that also names a non-allow-listed exception class, for example KeyError:")."""
    assert classify_exit(return_code=_LEGACY_NON_ZERO_EXIT, report=report) == "code"


# --- No report at all: only the LAST exception-shaped stderr line is evidence ---------------------


def test_stderr_fallback_reads_last_exception_line_only() -> None:
    """A killed or telemetry-off child leaves no report; an earlier exception line must never win over the last."""
    upstream_last = (
        "plantgeo_lane_turn_retry event=drought_forward_retry attempt=1\n"
        "ValueError: an earlier, superseded traceback line\n"
        "UpstreamHttpError: upstream request failed with status 503\n"
    )
    assert classify_exit(return_code=_LEGACY_NON_ZERO_EXIT, report=None, stderr_tail=upstream_last) == "upstream"

    code_last = (
        "UpstreamHttpError: upstream request failed with status 503\n"
        "some interleaved non-exception log line\n"
        "KeyError: 'cell_042'\n"
    )
    assert classify_exit(return_code=_LEGACY_NON_ZERO_EXIT, report=None, stderr_tail=code_last) == "code"


# --- The three signals that never need evidence at all --------------------------------------------


def test_timeout_is_hang() -> None:
    """The monitor killed the child on its command budget; `timed_out` wins even over a report that would
    otherwise read as upstream evidence, because the kill is what actually happened to this attempt."""
    report = _direct_report("UpstreamHttpError", "upstream request failed with status 503")
    assert classify_exit(return_code=None, timed_out=True, report=report) == "hang"


def test_pre_spawn_is_config() -> None:
    """A `run_scheduled_command` failure before `create_subprocess_exec` (an unknown lane, a bad repair
    request, ...) never produced a return code or a report at all; `pre_spawn` wins over everything."""
    assert classify_exit(return_code=None, pre_spawn=True) == "config"


# --- A report with no usable field, or a class this module does not recognise ---------------------


@pytest.mark.parametrize(
    "report",
    [
        pytest.param({"status": "failed"}, id="no-error-field-at-all"),
        pytest.param({"status": "failed", "error": ""}, id="blank-error-field"),
        pytest.param(
            _direct_report("ValueError", "the product config was internally inconsistent"), id="unknown-class"
        ),
    ],
)
def test_missing_class_is_code(report: Mapping[str, object]) -> None:
    """No usable evidence field, or a class outside every R1/R2/R4 allow-list, is `code` -- the safe default."""
    assert classify_exit(return_code=_LEGACY_NON_ZERO_EXIT, report=report) == "code"


def test_wrapper_evidence_is_registered_per_cohort_1_lane() -> None:
    """`WRAPPER_EVIDENCE` names exactly the five cohort-1 lanes design record Sec 3.1.1 lists for R3."""
    assert set(WRAPPER_EVIDENCE) == {
        "drought",
        "fire-perimeters",
        "evacuation-zones",
        "burn-severity",
        "fire-detections",
    }
