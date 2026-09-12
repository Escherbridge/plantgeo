"""Operational command-family wiring."""

import click

from agri_data_service.execution.job_executor_service import jobs_executor
from agri_data_service.execution.job_lane_control import jobs_set_lane_enabled
from agri_data_service.execution.job_run_supersession import jobs_supersede_run
from agri_data_service.interface.cli import commands
from agri_data_service.interface.cli._registry import register_commands


@click.group()
def ops() -> None:
    """Inspect and operate the service and its durable jobs."""


register_commands(
    ops,
    (
        ("seed", commands.seed),
        ("db-status", commands.db_status),
        ("db-upgrade", commands.db_upgrade),
        ("job-logs-maintain", commands.job_logs_maintain),
        ("jobs-executor", jobs_executor),
        ("jobs-supersede-run", jobs_supersede_run),
        ("jobs-set-lane-enabled", jobs_set_lane_enabled),
    ),
)
