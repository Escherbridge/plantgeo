"""Strict operator inputs for inactive regional bundles."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from agri_data_service.foundation.lane_config.models import LaneBudget, LaneCoverageTarget
from agri_data_service.foundation.region.manifest import RegionEnvelope

SafeName = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
EnvironmentName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=128)]
Coordinate = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class InputModel(BaseModel):
    """Unknown operator policy is refused rather than ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RegionRequest(InputModel):
    slug: SafeName
    display_name: str = Field(min_length=1, max_length=128)
    bbox: tuple[Coordinate, Coordinate, Coordinate, Coordinate]
    timezone: str
    admin_codes: tuple[str, ...]

    def envelope(self) -> RegionEnvelope:
        """Use the canonical WGS84 bounds validator."""
        west, south, east, north = self.bbox
        return RegionEnvelope(west=west, south=south, east=east, north=north)


class CorrectionPolicy(InputModel):
    mode: Literal["coupled_forward", "independent"]
    window_days: int = Field(gt=14, le=20000, strict=True)
    days_per_turn: int = Field(gt=0, le=366, strict=True)
    max_calls: int | None = Field(default=None, ge=0, le=10000, strict=True)


class LayerRequest(InputModel):
    source: SafeName
    temporal_mode: Literal["daily_archive", "current", "release", "static"]
    history: LaneCoverageTarget | None = None
    budget: LaneBudget
    corrections: CorrectionPolicy | None = None
    repair_max_days: int | None = Field(default=None, gt=0, le=366, strict=True)
    credential_env: EnvironmentName | None = None
    require_credential: bool = Field(default=False, strict=True)


class IsolationRequest(InputModel):
    database_name: SafeName
    dedicated_database: Literal[True]


class BootstrapRequest(InputModel):
    schema_version: Literal[1]
    deployment_id: SafeName
    region: RegionRequest
    layers: dict[SafeName, LayerRequest] = Field(min_length=1, max_length=12)
    isolation: IsolationRequest
