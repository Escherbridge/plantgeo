"""`python -m agri_data_service.pipeline.runner --lane <id> --mode forward|gap-fill|transform [--compare]`.

One turn; the S5 report is the last stdout line, written from `finally` whatever happened; the exit
code is S4's. See `pipeline/runner/AGENTS.md` "The command".
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import TYPE_CHECKING, Final, NoReturn, Protocol

from agri_data_service.foundation.observability.logging import configure_logging
from agri_data_service.foundation.observability.redaction import describe_error
from agri_data_service.pipeline.runner.contract import TURN_MODES
from agri_data_service.pipeline.runner.exits import EXIT_INTERNAL_ERROR, TurnConfigurationError, exit_code_for
from agri_data_service.pipeline.runner.report import (
    ReportIncompleteError,
    TurnLog,
    TurnReportBuilder,
    write_report_line,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager

    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import IngestStrategy, TransformStrategy
    from agri_data_service.pipeline.runner.turn import TurnPorts, TurnSpec

RUN_ID_PREFIX: Final = "lane-runner:"
#: Set by the executor for every child (`foundation/observability/bootstrap.py::TURN_CONTEXT_ENV_VARS`).
TURN_ID_ENV_VAR: Final = "PLANTGEO_TURN_ID"
_HELP_FLAGS: Final = frozenset({"-h", "--help"})


class PortBinder(Protocol):
    """Binds a turn's collaborators: `binding.py::production_ports` in production, fakes in the command tests."""

    def __call__(
        self, spec: TurnSpec, strategy: IngestStrategy | TransformStrategy, *, clock: TurnClock
    ) -> AbstractAsyncContextManager[TurnPorts]: ...


class _RunnerArgumentParser(argparse.ArgumentParser):
    """An argument error is a configuration error (exit 78) with a report, never argparse's bare exit 2."""

    def error(self, message: str) -> NoReturn:
        raise TurnConfigurationError(f"invalid runner arguments: {message}")


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The runner's command line: the lane, the mode, and the operator options."""
    parser = _RunnerArgumentParser(
        prog="python -m agri_data_service.pipeline.runner",
        description="Run one config-lane turn and print its S5 report as the last stdout line.",
    )
    parser.add_argument("--lane", required=True, help="the lane id: lanes/<id>.toml")
    parser.add_argument("--mode", required=True, choices=TURN_MODES)
    parser.add_argument(
        "--compare", action="store_true", help="build rows and diff them against published partitions; never writes"
    )
    parser.add_argument(
        "--republish-current",
        action="store_true",
        help="static_lookup forward only (CA17): refetch the served snapshot and rewrite it when its digest is "
        "unchanged; refuses on a mismatch. Each production use needs an owner go (CQ-8).",
    )
    parser.add_argument(
        "--weighted-budget",
        type=_non_negative_int,
        default=None,
        help="lower this turn's weighted-call cap (admission or an operator); never raises it",
    )
    parser.add_argument("--run-id", default=None, help="the run id receipts and markers carry")
    return parser


def _argument_value(argv: Sequence[str], flag: str) -> str | None:
    """A flag's raw value from `argv`, read before parsing so even a refused invocation's report names its lane."""
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


async def _run(
    arguments: argparse.Namespace, report: TurnReportBuilder, log: TurnLog, *, bind: PortBinder | None
) -> int:
    """Load the lane for the active region, resolve its strategy (S14), bind the ports, run the turn."""
    from agri_data_service.foundation.lane_config.loader import (  # noqa: PLC0415 - lazy by design
        default_lanes_directory,
        load_lane_configs,
    )
    from agri_data_service.foundation.region import load_region  # noqa: PLC0415 - lazy by design
    from agri_data_service.pipeline.runner.binding import production_ports  # noqa: PLC0415 - lazy by design
    from agri_data_service.pipeline.runner.clock import SystemClock  # noqa: PLC0415 - lazy by design
    from agri_data_service.pipeline.runner.resolve import resolve_strategy  # noqa: PLC0415 - lazy by design
    from agri_data_service.pipeline.runner.turn import TurnSpec, run_turn  # noqa: PLC0415 - lazy by design

    try:
        region = load_region()
    except ValueError as error:
        raise TurnConfigurationError(str(error)) from error
    configs = load_lane_configs(default_lanes_directory(), region)
    lane = configs.lanes.get(arguments.lane)
    if lane is None:
        quarantine = configs.quarantined.get(arguments.lane)
        if quarantine is not None:
            raise TurnConfigurationError(f"lane {arguments.lane!r} is quarantined: {'; '.join(quarantine.reasons)}")
        raise TurnConfigurationError(f"no lane {arguments.lane!r} in {configs.directory}")
    report.strategy = lane.strategy
    strategy = resolve_strategy(lane, requires_probe_edge=configs.requires_probe_edge(lane))
    spec = TurnSpec(
        lane=lane,
        provider=configs.provider_of(lane),
        region=region,
        mode=arguments.mode,
        run_id=report.run_id,
        input_lanes={input_id: configs.lanes[input_id] for input_id in lane.inputs if input_id in configs.lanes},
        compare=arguments.compare,
        republish_current=arguments.republish_current,
        weighted_budget=arguments.weighted_budget,
    )
    binder: PortBinder = production_ports if bind is None else bind
    async with binder(spec, strategy, clock=SystemClock()) as ports:
        return await run_turn(spec, ports, report, log)


def _write_report(report: TurnReportBuilder, log: TurnLog, exit_code: int) -> None:
    """The report always prints; a turn that never stated its `unwritten` list says so (`unwritten_known = false`)."""
    try:
        payload = report.to_payload(exit_code=exit_code, log=log)
    except ReportIncompleteError:
        report.mark_unwritten_unknown()
        payload = report.to_payload(exit_code=exit_code, log=log)
    write_report_line(payload)


def main(argv: Sequence[str] | None = None, *, bind: PortBinder | None = None) -> int:
    """Run one turn and return its S4 exit code; the report line is written on every path."""
    arguments_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if _HELP_FLAGS & set(arguments_list):
        parser.parse_args(arguments_list)  # prints help and exits 0; no turn, no report
    configure_logging("service")
    lane_id = _argument_value(arguments_list, "--lane") or "unknown"
    mode = _argument_value(arguments_list, "--mode") or "unknown"
    report = TurnReportBuilder(
        lane=lane_id,
        mode=mode,
        run_id=_argument_value(arguments_list, "--run-id") or f"{RUN_ID_PREFIX}{uuid.uuid4()}",
        turn_id=os.environ.get(TURN_ID_ENV_VAR) or None,
        compare="--compare" in arguments_list,
        republish_current="--republish-current" in arguments_list,
    )
    log = TurnLog(lane=lane_id, mode=mode)
    exit_code = EXIT_INTERNAL_ERROR
    try:
        arguments = parser.parse_args(arguments_list)
        exit_code = asyncio.run(_run(arguments, report, log, bind=bind))
    except (Exception, KeyboardInterrupt) as error:
        exit_code = exit_code_for(error)
        report.set(error_type=type(error).__name__, error=describe_error(error))
        log.error("plantgeo_lane_turn_failed", error_type=type(error).__name__, error=describe_error(error))
    finally:
        _write_report(report, log, exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
