"""Shared jittered retry delay for the `pipeline/direct` writers; see pipeline/direct/AGENTS.md §Shared retry."""

from __future__ import annotations

import random


def retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    """Return the bounded jittered retry delay used for source, R2, and write retries."""
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))
