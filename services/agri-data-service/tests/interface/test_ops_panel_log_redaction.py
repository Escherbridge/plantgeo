"""`routes/ops.py`'s three former `error=str(error)` sites now log only the exception's class name.

A static source check, in the style of `tests/test_no_raw_http_clients.py`: the panel read paths
this covers (`_load_snapshot`'s outer except, `_optional_rows`'s per-panel savepoint except, and the
dropped-client stream close) each run inside a live Sanic request against a real database session,
which this suite does not stand up; the guarantee that matters -- no site ever logs `str(error)`
again -- is exactly as checkable, and far cheaper to check, from the source itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agri_data_service.routes import ops

# The three sites the partition names: the stream-close, snapshot-read and panel-read failures.
_EXPECTED_DESCRIBE_ERROR_SITES = 3


def _source() -> str:
    return Path(ops.__file__).read_text(encoding="utf-8")


def test_every_ops_error_site_logs_class_name_only() -> None:
    source = _source()
    assert "error=str(error)" not in source, "an ops.py site still logs the raw exception text"

    tree = ast.parse(source)
    describe_error_error_kwargs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword)
        and node.arg == "error"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "describe_error"
    ]
    assert len(describe_error_error_kwargs) == _EXPECTED_DESCRIBE_ERROR_SITES
