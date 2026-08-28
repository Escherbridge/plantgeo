"""Operational command-family wiring."""

import click

from agri_data_service.execution.jobs_pulse_command import jobs_pulse
from agri_data_service.ingest.commands import INGEST_COMMANDS
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
        ("local", commands.local_execution),
        ("pipeline-status", commands.pipeline_status),
        *(
            (command.name or "", command)
            for command in INGEST_COMMANDS
            if not (command.name or "").startswith("ingest-")
        ),
        ("jobs-pulse", jobs_pulse),
    ),
)
