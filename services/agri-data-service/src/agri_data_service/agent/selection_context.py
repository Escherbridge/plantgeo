"""Bind graph retrieval to the caller's active map selection."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves calendar annotations at runtime.
from typing import TYPE_CHECKING, Any, Literal, Self

from anthropic import beta_async_tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agri_data_service.agent import tools as warehouse_tools

if TYPE_CHECKING:
    from collections.abc import Sequence


class TemporalSelection(BaseModel):
    """A selected day and the exact inclusive comparison window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    range_start: date
    range_end: date
    time_scale: Literal["day", "week", "month", "year", "all"] = "day"

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        """Require the selected day to lie inside the requested window."""
        if not self.range_start <= self.day <= self.range_end:
            raise ValueError("comparison window must contain the selected day")
        return self


class LayerSelection(TemporalSelection):
    """The independent temporal selection of one map layer."""

    surface_name: str = Field(min_length=1, max_length=100)


class MapSelection(TemporalSelection):
    """The active map scale and all explicitly selected layer windows."""

    zoom: float = Field(default=13, ge=0, le=24, allow_inf_nan=False)
    layers: tuple[LayerSelection, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_layers(self) -> Self:
        """Reject ambiguous duplicate layer selections."""
        names = [layer.surface_name for layer in self.layers]
        if len(names) != len(set(names)):
            raise ValueError("map selection repeats a layer")
        return self


def bind_selection_tools(
    tool_list: Sequence[Any], *, longitude: float, latitude: float, selection: MapSelection
) -> list[Any]:
    """Replace the generic reader with a tool whose scope is server-bound."""

    @beta_async_tool
    async def surface_evidence_for_selection(surface_name: str, page_start: int = 0) -> str:
        """Read any map layer at the active selection, with its exact day and comparison window.

        Coordinates, dates and zoom are bound by the caller, including hidden layers. Use
        list_environmental_layers to discover names. Preserve returned availability, actual dates,
        spatial support and history sampling bounds; continue with the returned page_start when needed.
        """
        window = next((layer for layer in selection.layers if layer.surface_name == surface_name), selection)
        return await warehouse_tools.query_surface_evidence_for_selection(
            surface_name=surface_name,
            longitude=longitude,
            latitude=latitude,
            day=window.day.isoformat(),
            range_start=window.range_start.isoformat(),
            range_end=window.range_end.isoformat(),
            time_scale=window.time_scale,
            zoom=selection.zoom,
            page_start=page_start,
        )

    return [
        surface_evidence_for_selection if tool.name == "surface_evidence_for_selection" else tool for tool in tool_list
    ]
