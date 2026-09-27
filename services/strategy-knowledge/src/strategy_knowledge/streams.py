"""stdout discipline: on a stdio surface nothing but protocol frames or the command's JSON may reach stdout.

See AGENTS.md section "stdout is the transport".
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import TextIO


@contextmanager
def reserved_stdout() -> Iterator[TextIO]:
    """Take the real stdout (forced to UTF-8) for the caller and point `sys.stdout` at stderr meanwhile."""
    real_stdout = sys.stdout
    with suppress(AttributeError, OSError):  # a stream a test substituted; leave its encoding alone
        real_stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    sys.stdout = sys.stderr
    try:
        yield real_stdout
    finally:
        sys.stdout = real_stdout
