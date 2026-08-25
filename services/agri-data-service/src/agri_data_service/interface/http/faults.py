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


def bbox_columns_absent(*, layer: str, columns: tuple[str, ...]) -> ServingRefusalError:
    """The lane's schema promises the position columns and the written objects do not carry them."""
    return ServingRefusalError(
        "bbox_columns_absent",
        f"{layer} declares {', '.join(columns)} in its registered schema but the objects being read do not carry "
        "them, so this viewport cannot be applied; the whole-world answer it would otherwise return is refused",
        status=HTTP_SERVICE_UNAVAILABLE,
    )
