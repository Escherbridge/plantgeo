"""Root Click group for the hard-cutover `agri-service` binary."""

import click

from agri_data_service.foundation.observability.logging import configure_logging
from agri_data_service.interface.cli.agent import agent
from agri_data_service.interface.cli.data import data
from agri_data_service.interface.cli.ops import ops

# The `agent` group is exempt from the `tool` profile on purpose (design §1.3, GL-1): it belongs to
# another session with its own in-flight edits and its own stdout discipline
# (`interface/cli/agent.py::reserved_stdout`). Reconfiguring structlog underneath it would change
# `mcp_server`'s rendering; that session can opt in later.
_UNCONFIGURED_SUBCOMMAND: str = "agent"


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """PlantGeo agriculture data, forecasting, and operations."""
    if ctx.invoked_subcommand != _UNCONFIGURED_SUBCOMMAND:
        configure_logging("tool")


cli.add_command(data)
cli.add_command(ops)
cli.add_command(agent)

__all__ = ["cli"]
