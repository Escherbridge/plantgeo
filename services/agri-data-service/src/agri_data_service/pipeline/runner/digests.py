"""Content digests the S11 rewrite rules, compare mode and CA17 republish compare: one table, one answer.

See `pipeline/runner/AGENTS.md` "Digests".
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agri_data_service.pipeline.runner.contract import SourceResponse


def table_digest(table: pa.Table) -> str:
    """SHA-256 of the table's Arrow IPC stream after `combine_chunks`, so chunking never changes the answer."""
    sink = pa.BufferOutputStream()
    combined = table.combine_chunks()
    with pa.ipc.new_stream(sink, combined.schema) as writer:
        writer.write_table(combined)
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def responses_digest(responses: Iterable[SourceResponse]) -> str:
    """One day's source digest from its unit answers, order-free: the digest an absence receipt cites."""
    parts = sorted(f"{response.request.unit}={response.digest}" for response in responses)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


__all__ = ["responses_digest", "table_digest"]
