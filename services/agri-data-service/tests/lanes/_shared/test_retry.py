"""`_shared/retry.py`: the one jittered-backoff loop three lane sources restated byte-for-byte."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import pytest

from agri_data_service.pipeline.lanes._shared.retry import retry_with_backoff


class _UpstreamError(RuntimeError):
    """A stand-in for whatever transport/parse failure an operation raises on a bad attempt."""


class _CallerError(RuntimeError):
    """A stand-in for each lane's own `exhausted` type (`BurnSeverityFetchError`, `DroughtSourceError`, ...)."""


class _RefusalError(RuntimeError):
    """A stand-in for `EvacuationZonesTruncatedError`: a refusal, never retried."""


async def _no_op_sleep(seconds: float) -> None:
    """Skip real wall-clock delay; the formula itself is `jittered_backoff_delay`'s own concern."""
    del seconds


@dataclass
class _ScriptedOperation:
    """Raises `_RefusalError` immediately if `refuse`, else `_UpstreamError` until `succeeds_on` (never, if `None`)."""

    succeeds_on: int | None
    refuse: bool = False
    calls: int = field(default=0, init=False)

    async def __call__(self) -> str:
        self.calls += 1
        if self.refuse:
            raise _RefusalError("Oregon OEM's feed did not deliver every published area")
        if self.succeeds_on is not None and self.calls >= self.succeeds_on:
            return "ok"
        raise _UpstreamError(f"attempt {self.calls} failed")


@dataclass(frozen=True)
class _Case:
    name: str
    succeeds_on: int | None
    refuse: bool
    attempts: int
    never_retry: tuple[type[BaseException], ...]
    expected_calls: int
    outcome: str  # "success" | "exhausted" | "passthrough"


CASES: Final[tuple[_Case, ...]] = (
    _Case(
        name="succeeds on attempt 2 of 3",
        succeeds_on=2,
        refuse=False,
        attempts=3,
        never_retry=(),
        expected_calls=2,
        outcome="success",
    ),
    _Case(
        name="succeeds on the first attempt (no retry needed)",
        succeeds_on=1,
        refuse=False,
        attempts=3,
        never_retry=(),
        expected_calls=1,
        outcome="success",
    ),
    _Case(
        name="exhausts after every attempt, raising the caller's type with __cause__",
        succeeds_on=None,
        refuse=False,
        attempts=3,
        never_retry=(),
        expected_calls=3,
        outcome="exhausted",
    ),
    _Case(
        name="never_retry passes through unretried on the first try",
        succeeds_on=None,
        refuse=True,
        attempts=5,
        never_retry=(_RefusalError,),
        expected_calls=1,
        outcome="passthrough",
    ),
)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_retry_with_backoff(case: _Case) -> None:
    operation = _ScriptedOperation(succeeds_on=case.succeeds_on, refuse=case.refuse)
    kwargs = {
        "attempts": case.attempts,
        "base_seconds": 0.01,
        "max_seconds": 0.02,
        "exhausted": _CallerError,
        "label": "test op",
        "never_retry": case.never_retry,
        "sleep": _no_op_sleep,
    }

    if case.outcome == "success":
        result = await retry_with_backoff(operation, **kwargs)
        assert result == "ok"
    elif case.outcome == "exhausted":
        with pytest.raises(_CallerError, match=f"test op failed after {case.attempts} attempts") as caught:
            await retry_with_backoff(operation, **kwargs)
        assert isinstance(caught.value.__cause__, _UpstreamError)
    else:
        with pytest.raises(_RefusalError):
            await retry_with_backoff(operation, **kwargs)

    assert operation.calls == case.expected_calls
