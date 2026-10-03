"""Frozen models for one lane TOML (`lanes/<id>.toml`) and one provider TOML (`lanes/_providers/<id>.toml`).

Spec §4.1 (D1, S3, S12, S14, S18, S19). Config holds DATA; strategies hold BEHAVIOUR. See
`foundation/lane_config/AGENTS.md` for every field's rationale and `lanes/AGENTS.md` for authoring.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 - pydantic resolves this at runtime
from datetime import date  # noqa: TC003 - pydantic resolves this at runtime
from types import MappingProxyType
from typing import Annotated, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from agri_data_service.foundation.lane_config.cron import CronExpression, parse_cron
from agri_data_service.foundation.parquet.lane_contract import LaneNature  # noqa: TC001 - pydantic field type
from agri_data_service.foundation.parquet.paths import LAYER_SLUG_PATTERN
from agri_data_service.foundation.region.manifest import SourceCoverage  # noqa: TC001 - pydantic field type
from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim

#: S14: strategy key `<layer>.<source>` resolves to this package + key, attribute `STRATEGY`.
STRATEGY_PACKAGE: Final = "agri_data_service.pipeline.lanes"
STRATEGY_ATTRIBUTE: Final = "STRATEGY"

#: Suffix every API-key environment variable NAME carries; a value-shaped string never has it.
_API_KEY_ENVIRONMENT_SUFFIX: Final = "_KEY"


def _validate_cron(text: str) -> str:
    parse_cron(text)
    return text


def _validate_stream_slug(slug: str) -> str:
    if not LAYER_SLUG_PATTERN.match(slug):
        raise ValueError(f"stream slug {slug!r} must be lowercase alphanumerics joined by single hyphens")
    return slug


def _validate_api_key_environment_name(name: str) -> str:
    if not name.endswith(_API_KEY_ENVIRONMENT_SUFFIX):
        raise ValueError(
            f"api_key_env {name!r} must NAME an environment variable ending in {_API_KEY_ENVIRONMENT_SUFFIX!r}; "
            "a key value never belongs in a TOML"
        )
    return name


KebabIdentifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
StrategyKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
ApiKeyEnvironmentName = Annotated[
    str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$"), AfterValidator(_validate_api_key_environment_name)
]
HostName = Annotated[str, StringConstraints(pattern=r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")]
UrlPath = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9/_.-]*$")]
#: An owner gate id as the plan names them: `G6`, `G9Q-1a`, `G9.0`.
GateName = Annotated[str, StringConstraints(pattern=r"^G[0-9][0-9A-Za-z.-]*$")]
CronText = Annotated[str, AfterValidator(_validate_cron)]
StreamSlug = Annotated[str, AfterValidator(_validate_stream_slug)]

LaneKind = Literal["ingest", "transform"]
LaneExecutor = Literal["legacy", "config"]
PartialDayPolicy = Literal["refuse", "write_and_recheck"]
CatchUpPolicy = Literal["coalesce_latest", "replay_oldest"]
SourceLifecycle = Literal["active", "discontinued"]
HistoryCapability = Literal["archive", "endpoint", "none"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Provider file ------------------------------------------------------------------------------


class ProviderEndpoint(_FrozenModel):
    """One endpoint: its free host, its keyed customer host when the provider sells one, and its path."""

    host: HostName
    customer_host: HostName | None = None
    path: UrlPath
    optional_api_key: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def _optional_key_uses_the_public_host(self) -> ProviderEndpoint:
        if self.optional_api_key and self.customer_host is not None:
            raise ValueError("optional_api_key cannot weaken a customer_host's required key")
        return self


class ProviderWeightRule(_FrozenModel):
    """`weight = locations x models x max(1, days / days_per_call) x max(1, variables / variables_per_call)`.

    Spec §6.3 (O10, conservative). `counts_models = false` drops the models factor.
    """

    days_per_call: int = Field(gt=0)
    variables_per_call: int = Field(gt=0)
    counts_models: bool
    basis: str = Field(min_length=1)

    def weight(self, *, locations: int, days: int, variables: int, models: int = 1) -> float:
        """One request's weighted-call cost under this rule."""
        model_factor = models if self.counts_models else 1
        return (
            locations
            * model_factor
            * max(1.0, days / self.days_per_call)
            * max(1.0, variables / self.variables_per_call)
        )


class ProviderBudget(_FrozenModel):
    """The one enforced cap (WQ-4): a monthly weighted-call quota with a gap-fill line and a forward stop."""

    period: Literal["month"]
    weighted_calls: int = Field(gt=0)
    #: Gap-fill is admitted only while charged + suspect + the turn cap stays at or under this share.
    ceiling_fraction: float = Field(gt=0, lt=1)
    #: Forward stops once charged spend reaches this share.
    stop_fraction: float = Field(gt=0, le=1)
    #: The metering pool the quota is charged against (`foundation/observability/usage.py`).
    charged_pool: KebabIdentifier

    @model_validator(mode="after")
    def _gap_fill_line_sits_below_the_forward_stop(self) -> ProviderBudget:
        if self.ceiling_fraction >= self.stop_fraction:
            raise ValueError(
                f"ceiling_fraction ({self.ceiling_fraction}) must be below stop_fraction ({self.stop_fraction}); "
                "gap-fill must yield before forward does"
            )
        return self

    @property
    def gap_fill_ceiling_calls(self) -> int:
        """The weighted-call line gap-fill admission may not cross this period."""
        return round(self.weighted_calls * self.ceiling_fraction)

    @property
    def forward_stop_calls(self) -> int:
        """The charged spend at which forward turns stop this period."""
        return round(self.weighted_calls * self.stop_fraction)


class ProviderConfig(_FrozenModel):
    """One upstream provider's shared facts (spec S3): hosts, key env var NAME, weight rule, budget."""

    id: KebabIdentifier
    display_name: str = Field(min_length=1)
    weighted: bool
    api_key_env: ApiKeyEnvironmentName | None = None
    #: Where the key travels: the query (`api_key_parameter`) or a request header (`api_key_header`).
    api_key_transport: Literal["query", "header"] = "query"
    api_key_parameter: Literal["apikey", "api_key"] | None = None
    api_key_header: Literal["X-Api-Key"] | None = None
    #: NASA POWER's `time-standard`; only UTC is accepted (daily values must be UTC days).
    time_standard: Literal["UTC"] | None = None
    endpoints: Mapping[KebabIdentifier, ProviderEndpoint]
    weight: ProviderWeightRule | None = None
    budget: ProviderBudget | None = None

    @model_validator(mode="after")
    def _weighting_budget_and_keys_agree(self) -> ProviderConfig:
        if not self.endpoints:
            raise ValueError("a provider declares at least one [endpoints.<name>] table")
        if self.weighted != (self.weight is not None):
            raise ValueError("a weighted provider declares [weight], and only a weighted provider does")
        if self.budget is not None and not self.weighted:
            raise ValueError("[budget] caps weighted calls; an unweighted provider has none to cap")
        keyed = sorted(name for name, endpoint in self.endpoints.items() if endpoint.customer_host is not None)
        if keyed and self.api_key_env is None:
            raise ValueError(f"endpoints {keyed} declare a customer_host, which needs api_key_env")
        header = self.api_key_transport == "header"
        if header != (self.api_key_header is not None):
            raise ValueError('api_key_header is declared exactly when api_key_transport = "header"')
        if header and self.api_key_parameter is not None:
            raise ValueError('api_key_transport = "header" sends no query parameter; drop api_key_parameter')
        key_name = self.api_key_header if header else self.api_key_parameter
        optional = any(endpoint.optional_api_key for endpoint in self.endpoints.values())
        if optional and (self.api_key_env is None or key_name is None):
            raise ValueError("optional_api_key needs api_key_env and the key's api_key_parameter or api_key_header")
        if (self.api_key_parameter is not None or header) and self.api_key_env is None:
            raise ValueError("api_key_parameter and api_key_transport need api_key_env")
        return self

    @field_validator("endpoints", mode="after")
    @classmethod
    def _endpoints_are_immutable(cls, value: Mapping[str, ProviderEndpoint]) -> Mapping[str, ProviderEndpoint]:
        return MappingProxyType(dict(value))

    def hosts(self) -> tuple[str, ...]:
        """Every host this provider file declares, free and customer, sorted and de-duplicated."""
        declared = {endpoint.host for endpoint in self.endpoints.values()}
        declared |= {endpoint.customer_host for endpoint in self.endpoints.values() if endpoint.customer_host}
        return tuple(sorted(declared))

    def customer_hosts(self) -> tuple[str, ...]:
        """The keyed customer hosts only: the ones a paid quota is charged through."""
        return tuple(sorted({e.customer_host for e in self.endpoints.values() if e.customer_host is not None}))


# --- Lane file ----------------------------------------------------------------------------------


class SourceHistory(_FrozenModel):
    """Where a source's history comes from (D3): its archive, a named history endpoint, or nowhere."""

    capability: HistoryCapability
    earliest: date | None = None
    #: The provider endpoint that serves history, for `capability = "endpoint"` only.
    endpoint: KebabIdentifier | None = None

    @model_validator(mode="after")
    def _capability_names_what_it_needs(self) -> SourceHistory:
        if self.capability == "none" and (self.earliest is not None or self.endpoint is not None):
            raise ValueError("capability 'none' declares neither earliest nor endpoint")
        if self.capability != "none" and self.earliest is None:
            raise ValueError(f"capability {self.capability!r} must declare the earliest day it serves")
        if (self.capability == "endpoint") != (self.endpoint is not None):
            raise ValueError("endpoint is required for capability 'endpoint' and forbidden otherwise")
        return self


class LaneSource(_FrozenModel):
    """An ingest lane's upstream: provider file id, endpoint, model, coverage claim, history, lifecycle."""

    provider: KebabIdentifier
    endpoint: KebabIdentifier
    model: str | None = None
    coverage: SourceCoverage
    #: ISO 3166-1 alpha-2 codes a regional source serves; a source's reach, never a footprint.
    iso_country_codes: tuple[str, ...] = ()
    history: SourceHistory
    lifecycle: SourceLifecycle = "active"

    @model_validator(mode="after")
    def _coverage_is_a_valid_claim(self) -> LaneSource:
        try:
            self.coverage_claim()
        except ValidationError as error:
            raise ValueError(error.errors()[0]["msg"]) from error
        return self

    def coverage_claim(self) -> SourceCoverageClaim:
        """This source's coverage as the region package's own claim type."""
        return SourceCoverageClaim(coverage=self.coverage, iso_country_codes=self.iso_country_codes)


class LaneSchedule(_FrozenModel):
    """Parsed 5-field UTC crons (D6, S9). Gap-fill ships off; enabling names its owner gate (S12)."""

    forward_cron: CronText
    gap_fill_cron: CronText | None = None
    gap_fill_enabled: bool = False
    gap_fill_enabled_at_gate: GateName | None = None
    catch_up: CatchUpPolicy = "coalesce_latest"

    @model_validator(mode="after")
    def _an_enabled_gap_fill_names_its_cron_and_gate(self) -> LaneSchedule:
        if self.gap_fill_enabled and self.gap_fill_cron is None:
            raise ValueError("gap_fill_enabled needs a gap_fill_cron to fire on")
        if self.gap_fill_enabled and self.gap_fill_enabled_at_gate is None:
            raise ValueError(
                "gap_fill_enabled is an explicit flip inside a named owner gate (S12): set gap_fill_enabled_at_gate"
            )
        return self

    def forward_expression(self) -> CronExpression:
        """The parsed forward cron."""
        return parse_cron(self.forward_cron)


class LaneDays(_FrozenModel):
    """The day axis: floor, lag fallback, absence recheck, partial-day policy, expected units."""

    floor: date | None = None
    floor_basis: str | None = None
    #: The edge fallback when the strategy has no `probe_edge` (S6).
    publication_lag_days: int = Field(ge=0)
    absence_recheck_days: int = Field(ge=0)
    partial_day: PartialDayPolicy
    expected_value_units: int | None = Field(default=None, gt=0)
    #: S11 rolling revision: each forward turn also re-asks one block of published days up to this many
    #: days behind the edge (late approvals); `None` re-asks the forward window only.
    revision_window_days: int | None = Field(default=None, gt=0)
    #: The block one forward turn re-asks: one request unit's span on the lane's source.
    revision_days_per_turn: int = Field(default=31, gt=0)

    @model_validator(mode="after")
    def _recheck_outlasts_the_lag(self) -> LaneDays:
        if self.absence_recheck_days <= self.publication_lag_days:
            raise ValueError(
                f"absence_recheck_days ({self.absence_recheck_days}) must exceed publication_lag_days "
                f"({self.publication_lag_days}), or a day is judged absent before it could have published"
            )
        if self.revision_window_days is not None and self.revision_window_days <= self.absence_recheck_days:
            raise ValueError(
                f"revision_window_days ({self.revision_window_days}) must exceed absence_recheck_days "
                f"({self.absence_recheck_days}): the forward window already re-asks those days"
            )
        return self


class LaneBudget(_FrozenModel):
    """Per-turn, per-mode weighted-call caps (S19) and turn bounds; a turn never exceeds its mode's cap."""

    forward_max_weighted_calls: int = Field(ge=0)
    gap_fill_max_weighted_calls: int = Field(ge=0)
    max_concurrency: int = Field(default=1, ge=1)
    turn_timeout_seconds: int = Field(gt=0)


class LanePruning(_FrozenModel):
    """Provisional prune-after-supersession for a transform (§4.6); ships off, enabled at a gate (S12)."""

    enabled: bool = False
    enabled_at_gate: GateName | None = None

    @model_validator(mode="after")
    def _enabled_pruning_names_its_gate(self) -> LanePruning:
        if self.enabled and self.enabled_at_gate is None:
            raise ValueError("pruning is an explicit flip inside a named owner gate (S12): set enabled_at_gate")
        return self


class LaneStream(_FrozenModel):
    """One Parquet stream the lane writes (S18); the registration mirror is checked against this."""

    slug: StreamSlug
    history_floor: date | None = None
    complete_history_floor: date | None = None
    floor_basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def _complete_floor_is_not_before_the_floor(self) -> LaneStream:
        floor, complete = self.history_floor, self.complete_history_floor
        if floor is not None and complete is not None and complete < floor:
            raise ValueError(f"complete_history_floor {complete} precedes history_floor {floor}")
        return self


class LaneConfig(_FrozenModel):
    """One lane TOML (spec §4.1). Loaded only through `loader.load_lane_configs`, which adds the cross-file rules."""

    id: KebabIdentifier
    kind: LaneKind
    nature: LaneNature
    enabled: bool
    #: The cut-over switch: `legacy` keeps today's path; `config` hands the lane to the runner.
    executor: LaneExecutor
    strategy: StrategyKey
    #: A named analysis lattice in the active region manifest (C2), never a literal.
    grid: KebabIdentifier | None = None
    inputs: tuple[KebabIdentifier, ...] = ()
    conflicts_with: tuple[KebabIdentifier, ...] = ()
    source: LaneSource | None = None
    schedule: LaneSchedule
    days: LaneDays | None = None
    budget: LaneBudget
    pruning: LanePruning = Field(default_factory=LanePruning)
    streams: tuple[LaneStream, ...] = ()

    @model_validator(mode="after")
    def _kind_decides_source_inputs_and_pruning(self) -> LaneConfig:
        if self.kind == "ingest":
            if self.source is None:
                raise ValueError("an ingest lane declares [source]")
            if self.inputs:
                raise ValueError("an ingest lane has no inputs; only a transform derives from other lanes")
            if self.pruning.enabled:
                raise ValueError("pruning is a transform's; an ingest lane never prunes")
        else:
            if self.source is not None:
                raise ValueError("a transform reads its inputs' partitions, never a [source]")
            if not self.inputs:
                raise ValueError("a transform names at least one input lane")
        if self.nature != "static_lookup" and self.days is None:
            raise ValueError(f"a {self.nature} lane declares [days]")
        return self

    @model_validator(mode="after")
    def _references_are_distinct_and_never_self(self) -> LaneConfig:
        for field_name, references in (("inputs", self.inputs), ("conflicts_with", self.conflicts_with)):
            if self.id in references:
                raise ValueError(f"{field_name} names this lane itself")
            if len(set(references)) != len(references):
                raise ValueError(f"{field_name} repeats a lane id")
        slugs = [stream.slug for stream in self.streams]
        if len(set(slugs)) != len(slugs):
            raise ValueError("[[streams]] repeats a slug")
        return self

    @property
    def strategy_module(self) -> str:
        """The module S14 resolves this lane's strategy from; its `STRATEGY` attribute is the strategy."""
        return f"{STRATEGY_PACKAGE}.{self.strategy}"


def lane_requires_probe_edge(lane: LaneConfig, provider: ProviderConfig | None) -> bool:
    """S6/S19: a settled (`refuse`) ingest lane on a weighted provider firing forward more than once a day."""
    if lane.kind != "ingest" or lane.days is None or lane.days.partial_day != "refuse":
        return False
    if provider is None or not provider.weighted:
        return False
    return lane.schedule.forward_expression().max_fires_per_utc_day > 1


__all__ = [
    "STRATEGY_ATTRIBUTE",
    "STRATEGY_PACKAGE",
    "LaneBudget",
    "LaneConfig",
    "LaneDays",
    "LanePruning",
    "LaneSchedule",
    "LaneSource",
    "LaneStream",
    "ProviderBudget",
    "ProviderConfig",
    "ProviderEndpoint",
    "ProviderWeightRule",
    "SourceHistory",
    "lane_requires_probe_edge",
]
