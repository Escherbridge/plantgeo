"""Small registration helpers shared by the four CLI families."""

from collections.abc import Iterable

import click


def register_commands(group: click.Group, commands: Iterable[tuple[str, click.Command]]) -> None:
    """Register each leaf once and reject accidental name collisions."""
    for name, command in commands:
        if name in group.commands:
            raise RuntimeError(f"duplicate CLI leaf {group.name} {name}")
        group.add_command(command, name=name)
