"""Serving faults: the answers that are NOT one of the four warehouse states.

Layer L4. A non-2xx from this plane is a transport or serving fault and never a statement about
warehouse content -- see `AGENTS.md` in this directory, "Why a conflict is not a state".
"""

from __future__ import annotations

from typing import Final

HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503


class ServingRefusalError(Exception):
    """The plane can reach the warehouse and still refuses to state what a day holds."""

    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_wire(self) -> dict[str, object]:
        """Render the refusal body; deliberately outside the frozen 200 contract."""
        return {"error": {"code": self.code, "message": self.message}}


def day_conflict(*, layer: str, day: str) -> ServingRefusalError:
    """A day carrying both a release and a governed absence; serving either half would pick a side."""
    return ServingRefusalError(
        "partition_day_conflict",
        f"{layer} {day} carries both part files and a governed-absence marker; retracting either side is a "
        "manual admin action, and serving one of them would pick a side",
        status=HTTP_CONFLICT,
    )


def day_incomplete(*, layer: str, day: str) -> ServingRefusalError:
    """A day holding parts with no completion marker: half a release, and half a release is not a day."""
    return ServingRefusalError(
        "partition_day_incomplete",
        f"{layer} {day} holds part files but no completion marker, so its export has not finished; serving it "
        "would put a prefix of a release on the map, and calling it unwritten would claim a gap that is not one",
        status=HTTP_SERVICE_UNAVAILABLE,
    )


def bbox_unsupported(*, layer: str, reason: str) -> ServingRefusalError:
    """A viewport was asked of a lane that cannot honestly be narrowed by one."""
    return ServingRefusalError(
        "bbox_unsupported",
        f"{layer} cannot be narrowed by a bbox: {reason}. Answering the whole world to a viewport request would "
        "silently widen the answer and is the read that consumed the host on 2026-08-24",
        status=HTTP_CONFLICT,
    )


def bbox_columns_absent(*, layer: str, columns: tuple[str, ...], key: str) -> ServingRefusalError:
    """The lane's schema promises the position columns and one object in the read does not carry them."""
    return ServingRefusalError(
        "bbox_columns_absent",
        f"{layer} declares {', '.join(columns)} in its registered schema and {key} does not carry them, so this "
        "viewport cannot be applied to every object in the read; answering from the objects that DO carry them "
        "would report the rest as empty, and the whole-world answer is refused too",
        status=HTTP_SERVICE_UNAVAILABLE,
    )


def read_timed_out(*, route: str, timeout_seconds: float) -> ServingRefusalError:
    """The read did not finish inside its budget. A serving fault, and never a claim about content."""
    return ServingRefusalError(
        "read_timed_out",
        f"the {route} read did not finish inside {timeout_seconds:.0f}s; this is a serving fault and "
        "says nothing about what the warehouse holds",
        status=HTTP_SERVICE_UNAVAILABLE,
    )


def read_over_budget(*, route: str) -> ServingRefusalError:
    """The read did not fit the session's memory ceiling. NOT retryable: the same read costs the same."""
    return ServingRefusalError(
        "read_over_budget",
        f"the {route} read does not fit the serving memory budget; narrow the viewport, the window or the zoom "
        "tier. This is a serving limit and says nothing about what the warehouse holds",
        status=HTTP_CONFLICT,
    )


def serving_at_capacity(*, route: str, concurrent_reads: int) -> ServingRefusalError:
    """Every read slot is taken. Refused rather than queued, and NOT retryable: a retry deepens the queue."""
    return ServingRefusalError(
        "serving_at_capacity",
        f"the {route} read found all {concurrent_reads} serving slots busy; each one holds a memory-capped DuckDB "
        "session, so the read is refused rather than queued. This is a serving limit and says nothing about "
        "what the warehouse holds",
        status=HTTP_CONFLICT,
    )


def serving_fault(*, route: str, fault: str) -> ServingRefusalError:
    """An unexpected fault, rendered as a refusal so it can never be read as a claim about content."""
    return ServingRefusalError(
        "serving_fault",
        f"the {route} read failed with an unexpected {fault}; this is a serving fault and says nothing about "
        "what the warehouse holds",
        status=HTTP_CONFLICT,
    )


def object_store_session_unavailable(*, detail: str) -> ServingRefusalError:
    """A session could not be pointed at the bucket. Deliberately detail-free: the cause quotes a secret."""
    return ServingRefusalError(
        "object_store_session_unavailable",
        f"a serving session could not be opened against the object store ({detail}); the underlying message is "
        "withheld because it quotes the statement that carries the credentials",
        status=HTTP_SERVICE_UNAVAILABLE,
    )


def serving_extension_unavailable(*, extension: str, detail: str) -> ServingRefusalError:
    """A DuckDB extension this plane reads through is not installed in the image."""
    return ServingRefusalError(
        "serving_extension_unavailable",
        f"the {extension} DuckDB extension could not be loaded ({detail}); it is not bundled in the wheel and is "
        "never installed on a request path, so this image cannot serve reads until it carries one",
        status=HTTP_SERVICE_UNAVAILABLE,
    )


def census_budget_exhausted(*, listed_keys: int) -> ServingRefusalError:
    """The whole-warehouse census walked more keys than one request may spend, across all its listings."""
    return ServingRefusalError(
        "census_budget_exhausted",
        f"the coverage census exceeded its {listed_keys}-key aggregate listing budget; a partial census would "
        "report lanes it never reached as absent, so nothing is claimed at all",
        status=HTTP_CONFLICT,
    )
