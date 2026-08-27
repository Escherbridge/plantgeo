"""Stable PostgreSQL advisory-lock identities shared across operational layers."""

from typing import Final

VEGETATION_PUBLICATION_BARRIER_KEY: Final = "vegetation-governed-publication-v1"

__all__ = ["VEGETATION_PUBLICATION_BARRIER_KEY"]
