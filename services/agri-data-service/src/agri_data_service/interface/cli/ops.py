"""Operational command-family wiring."""

import click

from agri_data_service.execution.expert_label_export import export_expert_labels
from agri_data_service.execution.gap_repair import jobs_plan_gap_repair
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
        ("jobs-plan-gap-repair", jobs_plan_gap_repair),
        ("jobs-set-lane-enabled", jobs_set_lane_enabled),
        # The one-time export of the 28-row reviewed label plane to the ML service's bucket
        # prefix (track `plantgeo_ml_service_20260918`, FR-7). An operational verb rather than a
        # lane: it has no schedule, no cursor and no gap census, and it runs when an owner says so.
        ("export-expert-labels", export_expert_labels),
    ),
)
