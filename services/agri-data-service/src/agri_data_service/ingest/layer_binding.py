"""One producer's layer binding: the variable that renames its layer, the default name, and the realtime channel."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LayerBinding:
    """A producer's target layer: its override variable, its default name, and the channel a written row invalidates."""

    variable: str
    default: str
    channel: str

    def resolve(self) -> str:
        """Read the variable AT CALL TIME, never at import; see ingest/AGENTS.md "layer_binding.py"."""
        return os.environ.get(self.variable, "").strip() or self.default
