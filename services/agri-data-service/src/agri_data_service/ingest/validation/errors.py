"""Typed refusals the report raises rather than reasoning about a result it never declared."""

from __future__ import annotations


class ValidationRowError(RuntimeError):
    """Raised when a result column comes back in a shape the report's own SQL does not declare."""


class ObservedDayScanTooLargeError(RuntimeError):
    """Raised when the day series exceeds its row cap; truncating it would invent gaps that are not there."""
