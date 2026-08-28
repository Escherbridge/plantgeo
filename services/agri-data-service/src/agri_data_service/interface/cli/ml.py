"""Machine-learning command-family wiring."""

import click

from agri_data_service.execution.recommendation_commands import register_recommendation_commands
from agri_data_service.interface.cli import commands
from agri_data_service.interface.cli._registry import register_commands


@click.group()
def ml() -> None:
    """Train and inspect evaluation-only ML artifacts."""


register_commands(
    ml,
    (
        ("strategy-label-map-preflight", commands.strategy_label_map_preflight),
        ("strategy-train", commands.strategy_train),
    ),
)
register_recommendation_commands(ml)
