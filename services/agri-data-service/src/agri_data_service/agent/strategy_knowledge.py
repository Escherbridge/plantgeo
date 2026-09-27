"""Client, typed refusals and bounded projection for the private strategy-knowledge (literature) service.

The three model-facing tools live in `agent/tools.py`, beside the ledger they record into; this module
owns everything else and imports nothing from `tools.py`. See agent/AGENTS.md, "Strategy knowledge
(literature) tools".
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

import httpx
from anthropic import beta_async_tool
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, ValidationError

from agri_data_service.agent.report import LITERATURE_EVIDENCE_ORIGIN, STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE
from agri_data_service.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence

    from anthropic.lib.tools import BetaAsyncFunctionTool

__all__ = [
    "LITERATURE_EVIDENCE_DOMAIN",
    "LITERATURE_EVIDENCE_ORIGIN",
    "NOT_CONFIGURED",
    "REJECTED_ARGUMENTS",
    "STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE",
    "UNAVAILABLE",
    "RawSiteProfile",
    "SanitizedSiteProfile",
    "StrategyAnswer",
    "StrategySiteProfile",
    "ask",
    "literature_tool",
    "portable_schema",
    "sanitize_site_profile",
    "service_arguments",
    "use_transport",
]

# --- Provenance and bounds ---------------------------------------------------------

# LITERATURE_EVIDENCE_ORIGIN / STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE (C4 of the frozen contract) are defined
# once in agent/report.py, whose validators enforce the pairing, and re-exported here.
#: Ledger domain; the sufficiency gate never counts it as a measured surface.
LITERATURE_EVIDENCE_DOMAIN: Final = "literature_reference"

#: Under the HTTP tool bridge's 12 s deadline (`routes/agent_tools.py::TOOL_TIMEOUT_SECONDS`).
REQUEST_TIMEOUT_SECONDS: Final = 8.0
#: Counted on the wire: the request asks for `identity`, so no decompression happens before the cap.
MAX_RESPONSE_BYTES: Final = 1_048_576
_REQUEST_HEADERS: Final = {"Accept-Encoding": "identity"}
_IDENTITY_ENCODINGS: Final = frozenset({"", "identity"})
DEFAULT_RESULTS: Final = 5
MAX_RESULTS: Final = 10
MAX_STRATEGY_IDS: Final = 5
MAX_FILTER_VALUES: Final = 6
MAX_QUERY_CHARACTERS: Final = 500
MAX_ID_CHARACTERS: Final = 120

_MAX_TEXT_CHARACTERS: Final = 1_200
_MAX_LIST_ITEMS: Final = 8
_MAX_MAPPING_ITEMS: Final = 16
_MAX_CITATIONS: Final = 8
_MAX_DETAIL_CHARACTERS: Final = 300

#: `site_profile` is advisory (boost-only, capped) in the strategy service, so it is sanitized field by
#: field rather than rejected whole; bounds on what a rejected field reports back.
MAX_SITE_PROFILE_IGNORED: Final = 20
_MAX_SITE_PROFILE_REASON_CHARACTERS: Final = 200
_MAX_SITE_PROFILE_FIELD_NAME_CHARACTERS: Final = 60

# --- Typed refusals ----------------------------------------------------------------

NOT_CONFIGURED: Final = "strategy_knowledge_not_configured"
UNAVAILABLE: Final = "strategy_knowledge_unavailable"
REJECTED_ARGUMENTS: Final = "strategy_knowledge_rejected_arguments"

_REJECTED_STATUSES: Final = frozenset({httpx.codes.BAD_REQUEST, httpx.codes.REQUEST_ENTITY_TOO_LARGE})

_REFUSAL_NOTES: Final[dict[str, str]] = {
    NOT_CONFIGURED: (
        "This is a REFUSAL, not an absence. The strategy-knowledge literature service is not configured "
        "on this deployment (STRATEGY_KNOWLEDGE_URL is unset), so no literature was searched. Nothing "
        "follows about whether strategies or findings exist for this need; say the literature lookup is "
        "unavailable and label any strategy reasoning model_inference."
    ),
    UNAVAILABLE: (
        "This is a REFUSAL, not an absence. The strategy-knowledge literature service could not answer "
        "(timeout, connection failure, server error, not ready, or an over-budget or malformed response). "
        "It is a fact about the service, not about the literature: do not report that no strategy or "
        "finding exists, and label any strategy reasoning model_inference."
    ),
    REJECTED_ARGUMENTS: (
        "This is a REFUSAL, not an absence. The strategy-knowledge service rejected these arguments, so "
        "nothing was searched. Correct them (enum values exactly as published, strategy_id values copied "
        "from a result) or drop the offending filter, and never read the rejection as an empty result."
    ),
}

LITERATURE_NOTE: Final = (
    "Literature-grounded strategy knowledge (claim_tier literature_grounded): what cited sources report, "
    "NOT a measurement, observation or prediction at this location. The service never saw the coordinate; "
    "site_profile only boosted or filtered the ranking. Cite with evidenceOrigin 'literature' and "
    "evidenceSource 'strategy-knowledge', never with evidenceReadIds. Quote rates and magnitudes only as "
    "reported, with their conditions, and never extrapolate them to this site. An empty result means no "
    "matching record in this corpus_version, not that no strategy exists."
)

# --- Vocabulary, copied verbatim from strategy-knowledge `vocabulary.py` -----------
#
# Literal enums so the published schema tells the model every accepted value; a drift test parses the
# service's own source and compares.

Goal = Literal[
    "soil_health",
    "water_management",
    "carbon_sequestration",
    "erosion_control",
    "wildfire_resilience",
    "biodiversity_habitat",
    "nutrient_cycling",
    "contaminant_remediation",
    "biomass_circularity",
    "drought_climate_adaptation",
    "productivity_yield",
    "air_quality",
]
FirePhase = Literal["pre_fire", "during_fire", "post_fire_emergency", "post_fire_recovery", "long_term_resilience"]
LandUse = Literal[
    "cropland",
    "rangeland",
    "pasture",
    "forest",
    "woodland",
    "orchard_vineyard",
    "garden_residential",
    "wildland_urban_interface",
    "riparian",
    "mine_or_degraded_land",
    "general",
]
Region = Literal[
    "pnw_westside",
    "pnw_inland",
    "northern_rockies",
    "great_basin_high_desert",
    "southern_rockies",
    "california",
    "southwest",
    "great_plains_texas",
    "us_midwest",
    "us_southeast",
    "us_northeast",
    "alaska",
    "canada",
    "latin_america",
    "europe",
    "asia",
    "africa",
    "oceania",
    "north_america_general",
    "global",
    "general",
]
EvidenceStrength = Literal[
    "ai_synthesis_only",
    "anecdotal_or_news",
    "expert_guidance",
    "field_trial_or_case_study",
    "peer_reviewed_experiment",
    "review_or_meta_analysis",
]
ServiceTool = Literal["search_strategies", "get_strategy", "search_findings"]

# --- Model-facing argument types ---------------------------------------------------

StrategyQuery = Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)]
GoalFilter = Annotated[list[Goal], Field(max_length=MAX_FILTER_VALUES)] | None
LandUseFilter = Annotated[list[LandUse], Field(max_length=MAX_FILTER_VALUES)] | None
FirePhaseFilter = Annotated[list[FirePhase], Field(max_length=MAX_FILTER_VALUES)] | None


def _normalize_region_filter(value: Any) -> Any:
    """Wrap a lone Region string in a one-element list; anything else (a real list, None) passes through.

    A live eval (google/gemini-2.5-flash-lite) sent `region="pnw_westside"` rather than
    `region=["pnw_westside"]` on every call. This runs BEFORE `list[Region]` validation, so an invalid
    region string still fails that validation normally -- it is just as strict as a supplied list, one
    element earlier.
    """
    return [value] if isinstance(value, str) else value


RegionFilter = (
    Annotated[list[Region], BeforeValidator(_normalize_region_filter), Field(max_length=MAX_FILTER_VALUES)] | None
)
MinimumEvidence = EvidenceStrength | None
ResultLimit = Annotated[int, Field(ge=1, le=MAX_RESULTS)]
StrategyId = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARACTERS)]
StrategyIds = Annotated[list[StrategyId], Field(min_length=1, max_length=MAX_STRATEGY_IDS)]
OptionalStrategyId = StrategyId | None

# Published as strings (numbers are coerced), which the service's `str | int` fields accept, so the schema
# needs no union; the service parses a digit string as an MTBS class or an NLCD code.
BurnSeverity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
LandCover = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class StrategySiteProfile(BaseModel):
    """Measured site facts only, never a day, coordinate, range or surface name; mirrors `SiteProfile`."""

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    slope_pct: Annotated[float, Field(ge=0, le=1_000)] | None = Field(
        default=None, description="Slope in percent; >= 30 boosts steep_slope."
    )
    soil_ph: Annotated[float, Field(ge=0, le=14)] | None = Field(
        default=None, description="Soil pH; < 5.5 acidic, > 7.8 alkaline."
    )
    sand_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(
        default=None, description="Sand fraction in percent; >= 70 sandy_coarse."
    )
    clay_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(
        default=None, description="Clay fraction in percent; >= 40 clay_heavy."
    )
    soil_organic_carbon_pct: Annotated[float, Field(ge=0, le=100)] | None = Field(
        default=None, description="Soil organic carbon in percent; < 1.0 low_organic_matter."
    )
    electrical_conductivity_ds_m: Annotated[float, Field(ge=0, le=1_000)] | None = Field(
        default=None, description="Electrical conductivity in dS/m; >= 4 saline_sodic."
    )
    burn_severity: BurnSeverity | None = Field(
        default=None, description="'high', 'moderate', 'low' (or MTBS class '2'-'4'); high also boosts hydrophobic."
    )
    days_since_fire: Annotated[int, Field(ge=0, le=36_500)] | None = Field(
        default=None, description="Days since the fire; <= 60 post_fire_emergency, <= 1095 post_fire_recovery."
    )
    annual_precip_mm: Annotated[float, Field(ge=0, le=20_000)] | None = Field(
        default=None, description="Annual precipitation in mm; < 350 droughty."
    )
    land_cover: LandCover | None = Field(
        default=None, description="NLCD class name ('Cultivated Crops') or code ('82'); mapped to land_use filters."
    )
    region: Region | None = Field(default=None, description="Region enum value, passed through as a filter.")


#: What a tool function actually declares for `site_profile`. Loose ON PURPOSE: `pydantic.validate_call`,
#: which `BaseFunctionTool.__init__` always builds from a tool's real parameter annotation and NEVER
#: from an `input_schema=` override, is what runs before the function body does -- so a strict
#: `StrategySiteProfile | None` here would reject a whole call over one bad key before
#: `sanitize_site_profile` ever saw it. The published schema still advertises the strict shape; see
#: `literature_tool` and `_patch_site_profile_schema`.
RawSiteProfile = dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class SanitizedSiteProfile:
    """A raw `site_profile` hint split into what the service will see and what was dropped, and why."""

    profile: StrategySiteProfile | None
    ignored: tuple[dict[str, str], ...]


def sanitize_site_profile(raw: RawSiteProfile) -> SanitizedSiteProfile:
    """Keep every `site_profile` key that validates on its OWN against `StrategySiteProfile`; drop the rest.

    site_profile only boosts or filters the literature ranking -- it is advisory, never a hard gate --
    so one bad hint (an unknown key a model invented, or a value outside a field's range) must not sink
    an otherwise-good call the way validating the object as a single strict model would. Each key is
    re-checked in ISOLATION with the model's own rules, so a bad key can never mask a good one: an
    unknown key surfaces as pydantic's own "extra_forbidden" on that key alone, because `StrategySiteProfile`
    forbids extras. FILTERS (goals, land_use, fire_phase, min_evidence, strategy_id, ids, limit, query)
    are never touched here and stay strict -- this function only ever sees the site_profile object.
    """
    if not isinstance(raw, dict) or not raw:
        return SanitizedSiteProfile(None, ())
    valid: dict[str, Any] = {}
    ignored: list[dict[str, str]] = []
    for key, value in raw.items():
        field_name = str(key)[:_MAX_SITE_PROFILE_FIELD_NAME_CHARACTERS]
        try:
            validated = StrategySiteProfile.model_validate({field_name: value})
        except ValidationError as error:
            if len(ignored) < MAX_SITE_PROFILE_IGNORED:
                ignored.append({"field": field_name, "reason": _site_profile_field_reason(error)})
            continue
        dumped = validated.model_dump(mode="json", exclude_none=True)
        if field_name in dumped:
            valid[field_name] = dumped[field_name]
    profile = StrategySiteProfile.model_validate(valid) if valid else None
    return SanitizedSiteProfile(profile, tuple(ignored))


def _site_profile_field_reason(error: ValidationError) -> str:
    """The first issue's own message, bounded; the rejected value is never included (`include_input=False`)."""
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    message = issues[0]["msg"] if issues else "invalid value"
    if len(message) <= _MAX_SITE_PROFILE_REASON_CHARACTERS:
        return message
    return f"{message[: _MAX_SITE_PROFILE_REASON_CHARACTERS - 3]}..."


# --- Portable published schema -----------------------------------------------------


def portable_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The same schema with `$ref`s inlined and optional `anyOf [X, null]` collapsed to X.

    Pydantic publishes both for a nested model and an optional parameter; the live map agent drives
    Gemini through OpenRouter, and no other bridge tool publishes either. See agent/AGENTS.md.
    """
    definitions: Mapping[str, Any] = schema.get("$defs", {})

    def rewrite(node: Any) -> Any:
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if not isinstance(node, dict):
            return node
        body = {key: value for key, value in node.items() if key != "$defs"}
        reference = body.pop("$ref", None)
        if isinstance(reference, str):
            return rewrite({**definitions[reference.rsplit("/", 1)[-1]], **body})
        options = body.pop("anyOf", None)
        if isinstance(options, list):
            concrete = [option for option in options if option != {"type": "null"}]
            if len(concrete) == 1:
                if "default" in body and body["default"] is None:
                    del body["default"]
                return rewrite({**concrete[0], **body})
            body["anyOf"] = options
        return {key: rewrite(value) for key, value in body.items()}

    rewritten: dict[str, Any] = rewrite(dict(schema))
    return rewritten


def _patch_site_profile_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the strict, descriptive `StrategySiteProfile` shape for a top-level `site_profile` property.

    A tool function types `site_profile` as `RawSiteProfile` (a loose dict) so `pydantic.validate_call`
    accepts anything and lets `sanitize_site_profile` filter it field by field instead of rejecting the
    whole call. Inferring the schema straight from that loose annotation would publish `site_profile` as
    a bare `{"type": "object"}` with no properties, which tells the model nothing about which keys are
    meaningful. This substitutes the strict model schema back in, keeping only the docstring-derived
    `description` the loose annotation still produced, so the PUBLISHED schema is unaffected by the
    looser runtime type.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "site_profile" not in properties:
        return dict(schema)
    current = properties["site_profile"]
    description = current.get("description") if isinstance(current, dict) else None
    strict = StrategySiteProfile.model_json_schema()
    patched = {**strict, "description": description} if description else strict
    return {**schema, "properties": {**properties, "site_profile": patched}}


def literature_tool(function: Callable[..., Coroutine[Any, Any, str]]) -> BetaAsyncFunctionTool[Any]:
    """`beta_async_tool` publishing `portable_schema`; arguments are still validated against the signature."""
    inferred = beta_async_tool(function)
    published = portable_schema(_patch_site_profile_schema(inferred.input_schema))
    return beta_async_tool(function, input_schema=published)


# --- The call ----------------------------------------------------------------------

#: Tests answer requests in-process through this; production leaves it None and httpx opens sockets.
_transport: ContextVar[httpx.AsyncBaseTransport | None] = ContextVar("strategy_knowledge_transport", default=None)


@contextmanager
def use_transport(transport: httpx.AsyncBaseTransport) -> Iterator[None]:
    """Answer every strategy-knowledge request through `transport` for one block (tests only)."""
    token = _transport.set(transport)
    try:
        yield
    finally:
        _transport.reset(token)


@dataclass(frozen=True, slots=True)
class StrategyAnswer:
    """One tool outcome: the model-facing payload and the ledger detail recorded beside it."""

    payload: dict[str, Any]
    ledger_detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Refused:
    """Why one exchange cannot be answered."""

    code: str
    detail: str


class _ResponseOverBudgetError(Exception):
    """The service answered more than MAX_RESPONSE_BYTES."""


class _UnrequestedEncodingError(Exception):
    """The service compressed a body we asked for as `identity`, so its wire size is not its real size."""


def service_arguments(**arguments: Any) -> dict[str, Any]:
    """Drop unset filters and flatten the site profile into the JSON object the service validates."""
    body: dict[str, Any] = {}
    for name, value in arguments.items():
        if isinstance(value, StrategySiteProfile):
            profile = value.model_dump(mode="json", exclude_none=True)
            if profile:
                body[name] = profile
        elif value is not None and value != []:
            body[name] = value
    return body


async def ask(tool_name: str, service_tool: ServiceTool, arguments: Mapping[str, Any]) -> StrategyAnswer:
    """Call one strategy-knowledge tool and project its answer; every failure is a typed refusal."""
    origin = settings.strategy_knowledge_url
    if not origin:
        return refusal(tool_name, NOT_CONFIGURED, "STRATEGY_KNOWLEDGE_URL is not set on this service")
    decoded = await _exchange(f"{origin}/v1/tools/{service_tool}", arguments)
    if isinstance(decoded, _Refused):
        return refusal(tool_name, decoded.code, decoded.detail)
    projected = _PROJECTIONS[service_tool](decoded)
    result_count = len(projected.get("results", projected.get("strategies", [])))
    return StrategyAnswer(
        payload={
            "tool": tool_name,
            "evidence_domain": LITERATURE_EVIDENCE_DOMAIN,
            "cite_as": {
                "evidenceOrigin": LITERATURE_EVIDENCE_ORIGIN,
                "evidenceSource": STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE,
            },
            "claim_tier": decoded.get("claim_tier"),
            "corpus_version": decoded.get("corpus_version"),
            "index_is_stale": decoded.get("index_is_stale"),
            **projected,
            "result_count": result_count,
            "note": LITERATURE_NOTE,
        },
        ledger_detail={
            "evidence_domain": LITERATURE_EVIDENCE_DOMAIN,
            "state": "answered",
            "result_count": result_count,
            "corpus_version": decoded.get("corpus_version"),
        },
    )


def refusal(tool_name: str, code: str, detail: str) -> StrategyAnswer:
    """A typed refusal payload; never an exception, never an empty success."""
    return StrategyAnswer(
        payload={
            "tool": tool_name,
            "error": code,
            "refusal_detail": detail[:_MAX_DETAIL_CHARACTERS],
            "evidence_domain": LITERATURE_EVIDENCE_DOMAIN,
            "note": _REFUSAL_NOTES[code],
        },
        ledger_detail={"evidence_domain": LITERATURE_EVIDENCE_DOMAIN, "state": "refused", "error": code},
    )


async def _exchange(  # noqa: PLR0911 - one return per refusal class the response is sorted into.
    url: str, arguments: Mapping[str, Any]
) -> dict[str, Any] | _Refused:
    """One bounded request: the decoded 200 body, or the refusal that classifies why there is none."""
    try:
        status, body = await _post(url, arguments)
    except _ResponseOverBudgetError:
        return _Refused(UNAVAILABLE, f"the response exceeded its {MAX_RESPONSE_BYTES}-byte budget")
    except _UnrequestedEncodingError:
        return _Refused(UNAVAILABLE, "the service compressed a response requested as identity")
    except (httpx.HTTPError, TimeoutError) as error:
        return _Refused(UNAVAILABLE, f"the request failed ({type(error).__name__})")
    decoded = _decode(body)
    if status in _REJECTED_STATUSES:
        return _Refused(REJECTED_ARGUMENTS, _service_detail(decoded, status))
    if status != httpx.codes.OK:
        return _Refused(UNAVAILABLE, _service_detail(decoded, status))
    if decoded is None:
        return _Refused(UNAVAILABLE, "the service answered with a body that is not a JSON object")
    return decoded


async def _post(url: str, arguments: Mapping[str, Any]) -> tuple[int, bytes]:
    """POST the arguments and read at most MAX_RESPONSE_BYTES of the answer inside one wall-clock deadline."""
    async with (
        asyncio.timeout(REQUEST_TIMEOUT_SECONDS),
        httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False, transport=_transport.get()
        ) as client,
        client.stream("POST", url, json=dict(arguments), headers=_REQUEST_HEADERS) as response,
    ):
        if response.headers.get("content-encoding", "").strip().lower() not in _IDENTITY_ENCODINGS:
            raise _UnrequestedEncodingError
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise _ResponseOverBudgetError
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise _ResponseOverBudgetError
        return response.status_code, bytes(body)


def _decode(body: bytes) -> dict[str, Any] | None:
    """The body as a JSON object, or None when it is anything else."""
    try:
        decoded = json.loads(body)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _service_detail(decoded: Mapping[str, Any] | None, status: int) -> str:
    """The status plus the service's own error code and detail, bounded."""
    parts = [f"HTTP {status}"]
    for key in ("error", "detail"):
        value = decoded.get(key) if decoded else None
        if isinstance(value, str) and value:
            parts.append(value)
    return ": ".join(parts)[:_MAX_DETAIL_CHARACTERS]


# --- Bounded projection ------------------------------------------------------------
#
# Keeps identity, summaries, goals, evidence strength, actions and citation title/URL; drops facet
# texts, snippets, scores and other bulk the model does not need to choose or cite.

_STRATEGY_HIT_KEYS: Final = (
    "rank",
    "strategy_id",
    "name",
    "summary",
    "family_id",
    "category",
    "goals",
    "fire_phase",
    "evidence_strength",
    "citation_count",
    "review_state",
    "matched_facet",
    "boosted_by",
)
_STRATEGY_RECORD_KEYS: Final = (
    "strategy_id",
    "resolved_from",
    "name",
    "summary",
    "family_id",
    "category",
    "goals",
    "fire_phase",
    "land_use",
    "region",
    "actions",
    "application_rate",
    "timing",
    "slope_guidance",
    "soil_conditions",
    "scale",
    "cost_level",
    "labor_intensity",
    "time_to_effect",
    "benefits",
    "risks_limitations",
    "nrcs_practice_code",
    "evidence_strength",
    "review_state",
)
_FINDING_KEYS: Final = (
    "rank",
    "finding_id",
    "claim",
    "conditions",
    "direction",
    "magnitude",
    "variables",
    "study_type",
    "evidence_strength",
    "linked_strategy_ids",
    "excerpt",
    "review_state",
)
_CITATION_KEYS: Final = ("title", "url", "publisher", "year")
_SEARCH_ECHO_KEYS: Final = ("query", "applied_filters", "site_profile", "ranked_candidates", "not_found")


def _bounded(value: Any) -> Any:
    """Cap strings, lists and mappings so one oversized record cannot flood the model's context."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT_CHARACTERS else f"{value[:_MAX_TEXT_CHARACTERS]} [truncated]"
    if isinstance(value, list):
        return [_bounded(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {str(key): _bounded(item) for key, item in list(value.items())[:_MAX_MAPPING_ITEMS]}
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _pick(entry: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """The named keys that carry a value, bounded."""
    return {key: _bounded(entry[key]) for key in keys if entry.get(key) not in (None, "", [], {})}


def _citations(value: Any) -> list[dict[str, Any]]:
    """Title/URL cards, one per distinct source."""
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for citation in _mappings(value):
        card = _pick(citation, _CITATION_KEYS)
        identity = str(card.get("url") or card.get("title") or "")
        if identity and identity not in seen:
            seen.add(identity)
            cards.append(card)
        if len(cards) >= _MAX_CITATIONS:
            break
    return cards


def _project_strategy_search(decoded: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_pick(decoded, _SEARCH_ECHO_KEYS),
        "results": [_pick(hit, _STRATEGY_HIT_KEYS) for hit in _mappings(decoded.get("results"))[:MAX_RESULTS]],
    }


def _project_strategy_records(decoded: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "strategies": [
            {**_pick(record, _STRATEGY_RECORD_KEYS), "citations": _citations(record.get("citations"))}
            for record in _mappings(decoded.get("strategies"))[:MAX_STRATEGY_IDS]
        ],
        "not_found": _bounded(decoded.get("not_found") or []),
    }


def _project_finding_search(decoded: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_pick(decoded, _SEARCH_ECHO_KEYS),
        "results": [
            {**_pick(finding, _FINDING_KEYS), "source": _pick(_mapping(finding.get("source")), _CITATION_KEYS)}
            for finding in _mappings(decoded.get("results"))[:MAX_RESULTS]
        ],
    }


_PROJECTIONS: Final[dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    "search_strategies": _project_strategy_search,
    "get_strategy": _project_strategy_records,
    "search_findings": _project_finding_search,
}
