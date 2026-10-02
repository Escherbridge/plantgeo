"""The lane-TOML contract over the REAL `lanes/` tree, as landed (spec §4.1, plan 1A).

Two checkers carry the contract and each is proven twice: it returns nothing over the landed tree,
and it names a planted offender in a built one. Validity, crons and recheck > lag are the loader's
own invariants (`test_loader.py` plants each); here they surface as "every file loads".

- `lane_contract_violations`: S14 resolution, S12 gap-fill/pruning pins, S6/S19 probe_edge.
- `raw_file_violations`: no footprint literals and no key-shaped values in any TOML under `lanes/`.
"""

from __future__ import annotations

import importlib
import inspect
import re
import sys
import tomllib
from collections.abc import Mapping
from types import ModuleType
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.lane_config import (
    STRATEGY_ATTRIBUTE,
    LaneConfigSet,
    load_lane_configs,
)
from agri_data_service.foundation.region import load_region
from tests.lane_config.builders import (
    REAL_LANES_DIRECTORY,
    provisional_forecast_lane,
    settled_soil_lane,
    to_toml,
    transform_lane,
    write_lane_tree,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.foundation.lane_config import LaneConfig

#: S12: gap-fill crons and provisional pruning ship disabled. Enabling one is an explicit TOML flip
#: inside a named owner gate, and that gate's commit adds the lane id here in the same diff.
GAP_FILL_ENABLED_LANES: Final[frozenset[str]] = frozenset({"water-gauges-daily"})
PRUNING_ENABLED_LANES: Final[frozenset[str]] = frozenset()

_INGEST_METHODS: Final = ("plan_requests", "fetch", "settle", "rows")
_TRANSFORM_METHODS: Final = ("derive",)

# --- footprint and key-shape detection (mirrors `tests/test_region_literal_contract.py`'s markers) --

_FOOTPRINT_KEY: Final = re.compile(
    r"(?i)(^|[_-])(lat|lon|latitude|longitude|bbox|envelope|bounds|extent|west|south|east|north)($|[_-])"
)
_REGION_MARKER: Final = re.compile(r"\bUS-(WA|OR|ID)\b|\b(Pacific Northwest|PNW)\b")
_MAX_DECLARED_STRING_LENGTH: Final = 40
_BBOX_ARITY: Final = 4
_SECRET_KEY_NAME: Final = re.compile(r"(?i)(secret|token|password|passwd|api_?key|credential)")
_ALLOWED_SECRET_KEY_NAMES: Final = frozenset({"api_key_env"})
_API_KEY_ENV_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]*_KEY$")
_MIN_TOKEN_LENGTH: Final = 20
_MIN_HEX_KEY_LENGTH: Final = 32
_KEYED_QUERY: Final = re.compile(r"(?i)[?&](api_?key|apikey|key|token|access_token)=")

#: Review finding 2 (f1-config sweep): the parsed-value scan above never sees a TOML *comment*, and
#: only flags a value with lower+upper+digit all present, so a 20-31 char all-lower-plus-digits or
#: all-upper key slips through. This second pass reads the RAW file text (comments included) and
#: flags any long run of BARE `[A-Za-z0-9]` -- hyphen and underscore end a run, the same way a space
#: does -- that mixes letters and digits, regardless of case. Splitting on `-`/`_` is deliberate: a
#: kebab- or snake_case identifier (a lane id, a stream slug, `soil-era5-land-direct-forward`) is
#: exactly the shape config text is full of, and treating its separators as separators is what keeps
#: those legitimate identifiers under `_MIN_TOKEN_LENGTH` per word while still catching an unbroken
#: random-looking run. A token under `_MIN_TOKEN_LENGTH` is intentionally NOT flagged here: below
#: ~20 characters, ordinary config words are indistinguishable from a short key without a per-token
#: allow/deny list, and that false-positive rate was judged worse than the residual gap.
_RAW_TOKEN: Final = re.compile(r"[A-Za-z0-9]+")


def _looks_like_a_secret(value: str) -> bool:
    if re.fullmatch(rf"[0-9a-fA-F]{{{_MIN_HEX_KEY_LENGTH},}}", value):
        return True
    if len(value) < _MIN_TOKEN_LENGTH or re.search(r"\s", value):
        return False
    return bool(re.search(r"[a-z]", value) and re.search(r"[A-Z]", value) and re.search(r"[0-9]", value))


def _looks_like_a_secret_token(token: str) -> bool:
    """Raw-text counterpart of `_looks_like_a_secret`: letters+digits is enough once a token is long."""
    if re.fullmatch(rf"[0-9a-fA-F]{{{_MIN_HEX_KEY_LENGTH},}}", token):
        return True
    if len(token) < _MIN_TOKEN_LENGTH:
        return False
    return bool(re.search(r"[A-Za-z]", token) and re.search(r"[0-9]", token))


def raw_text_violations(path: Path, text: str) -> list[str]:
    """Every long letter+digit token in the file's raw text, TOML comments included (review finding 2)."""
    violations: list[str] = []
    for match in _RAW_TOKEN.finditer(text):
        token = match.group()
        if _looks_like_a_secret_token(token):
            violations.append(f"{path.name}: raw-text token looks key-shaped: {token!r}")
    return violations


def _walk(node: object, path: str) -> list[tuple[str, str, object]]:
    """Every (dotted path, key, value) in a parsed TOML document, arrays included."""
    found: list[tuple[str, str, object]] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            found.append((child, str(key), value))
            found.extend(_walk(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.append((f"{path}[{index}]", "", value))
            found.extend(_walk(value, f"{path}[{index}]"))
    return found


def raw_file_violations(path: Path) -> list[str]:
    """Footprint literals and key-shaped values in one TOML file: parsed values, then raw text/comments."""
    text = path.read_text(encoding="utf-8")
    violations: list[str] = []
    for where, key, value in _walk(tomllib.loads(text), ""):
        if key and _FOOTPRINT_KEY.search(key):
            violations.append(f"{path.name}:{where}: footprint-shaped key {key!r}")
        if isinstance(value, list) and len(value) == _BBOX_ARITY and all(isinstance(v, (int, float)) for v in value):
            violations.append(f"{path.name}:{where}: a 4-number array reads as a bbox")
        if key and _SECRET_KEY_NAME.search(key) and key not in _ALLOWED_SECRET_KEY_NAMES:
            violations.append(f"{path.name}:{where}: secret-shaped key {key!r}")
        if not isinstance(value, str):
            continue
        if key == "api_key_env" and not _API_KEY_ENV_NAME.match(value):
            violations.append(f"{path.name}:{where}: api_key_env must NAME a *_KEY variable, not hold a value")
        if len(value) <= _MAX_DECLARED_STRING_LENGTH and _REGION_MARKER.search(value):
            violations.append(f"{path.name}:{where}: region footprint marker {value!r}")
        if _looks_like_a_secret(value) or _KEYED_QUERY.search(value):
            violations.append(f"{path.name}:{where}: key-shaped value")
    violations.extend(raw_text_violations(path, text))
    return violations


def _resolve_strategy(lane: LaneConfig) -> object:
    """S14: `<layer>.<source>` -> `agri_data_service.pipeline.lanes.<layer>.<source>.STRATEGY`."""
    return getattr(importlib.import_module(lane.strategy_module), STRATEGY_ATTRIBUTE)


def lane_contract_violations(config: LaneConfigSet) -> list[str]:
    """S14 resolution, the S12 enable pins and the S6/S19 probe_edge rule over every loaded lane."""
    violations: list[str] = []
    for lane_id, lane in config.lanes.items():
        try:
            strategy = _resolve_strategy(lane)
        except (ImportError, AttributeError) as error:
            violations.append(f"{lane_id}: strategy {lane.strategy!r} does not resolve: {error}")
            continue
        required = _INGEST_METHODS if lane.kind == "ingest" else _TRANSFORM_METHODS
        missing = [name for name in required if not callable(getattr(strategy, name, None))]
        if missing:
            violations.append(f"{lane_id}: strategy {lane.strategy!r} lacks {missing}")
        if config.requires_probe_edge(lane) and not inspect.iscoroutinefunction(getattr(strategy, "probe_edge", None)):
            violations.append(f"{lane_id}: a settled weighted lane firing more than daily needs async probe_edge")
    gap_fill_enabled = {lane_id for lane_id, lane in config.lanes.items() if lane.schedule.gap_fill_enabled}
    if gap_fill_enabled != GAP_FILL_ENABLED_LANES:
        violations.append(f"gap-fill enabled on {sorted(gap_fill_enabled)}, pinned {sorted(GAP_FILL_ENABLED_LANES)}")
    pruning_enabled = {lane_id for lane_id, lane in config.lanes.items() if lane.pruning.enabled}
    if pruning_enabled != PRUNING_ENABLED_LANES:
        violations.append(f"pruning enabled on {sorted(pruning_enabled)}, pinned {sorted(PRUNING_ENABLED_LANES)}")
    return violations


@pytest.fixture(scope="module")
def landed() -> LaneConfigSet:
    """The real tree, loaded exactly as the pilot deployment's executor would."""
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))


# --- over the landed tree -------------------------------------------------------------------------


def test_every_lane_and_provider_file_in_the_landed_tree_loads(landed: LaneConfigSet) -> None:
    """Validity, parsed crons, recheck > lag, lattices and coverage: nothing in the real tree is quarantined."""
    lane_files = {path.stem for path in REAL_LANES_DIRECTORY.glob("*.toml")}
    provider_files = {path.stem for path in (REAL_LANES_DIRECTORY / "_providers").glob("*.toml")}

    assert dict(landed.quarantined) == {}
    assert dict(landed.provider_failures) == {}
    assert set(landed.lanes) == lane_files
    assert set(landed.providers) == provider_files
    assert {"open-meteo", "nasa-power"} <= provider_files


def test_every_landed_lane_honours_the_strategy_enable_and_probe_contract(landed: LaneConfigSet) -> None:
    """S14 resolution, S12 pins and the S6/S19 probe_edge rule hold for every lane in the real tree."""
    assert lane_contract_violations(landed) == []


def test_no_landed_toml_carries_a_footprint_literal_or_a_key_shaped_value() -> None:
    """Footprints live in the region manifest; keys live in the environment, named never valued."""
    violations = [v for path in sorted(REAL_LANES_DIRECTORY.rglob("*.toml")) for v in raw_file_violations(path)]

    assert violations == []


# --- the checkers catch what they exist to catch --------------------------------------------------


class _SettledStrategy:
    """A strategy shaped like the spec §4.2 Protocol, without the optional probe."""

    def plan_requests(self, *_: object) -> tuple[()]:
        return ()

    async def fetch(self, *_: object) -> None:
        return None

    def settle(self, *_: object) -> None:
        return None

    def rows(self, *_: object) -> None:
        return None


class _ProbingStrategy(_SettledStrategy):
    async def probe_edge(self, *_: object) -> None:
        return None


def _install_strategy(monkeypatch: pytest.MonkeyPatch, strategy_key: str, strategy: object) -> None:
    module = ModuleType(f"agri_data_service.pipeline.lanes.{strategy_key}")
    setattr(module, STRATEGY_ATTRIBUTE, strategy)
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_the_contract_checker_names_each_planted_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An hourly settled lane without probe_edge, an unresolvable strategy and an unpinned enable all fail."""
    _install_strategy(monkeypatch, "contract_fixture.settled_hourly", _SettledStrategy())
    _install_strategy(monkeypatch, "contract_fixture.settled_probing", _ProbingStrategy())
    _install_strategy(monkeypatch, "contract_fixture.provisional", _SettledStrategy())
    hourly = {"forward_cron": "50 * * * *"}
    lanes = [
        settled_soil_lane("hourly-without-probe", strategy="contract_fixture.settled_hourly", schedule=hourly),
        settled_soil_lane("hourly-with-probe", strategy="contract_fixture.settled_probing", schedule=hourly),
        provisional_forecast_lane("hourly-provisional", strategy="contract_fixture.provisional"),
        settled_soil_lane(
            "gap-fill-unpinned",
            strategy="contract_fixture.settled_probing",
            schedule={"gap_fill_enabled": True, "gap_fill_enabled_at_gate": "G6"},
        ),
        transform_lane("unresolvable-transform", inputs=["hourly-with-probe"], strategy="contract_fixture.absent"),
    ]
    config = load_lane_configs(write_lane_tree(tmp_path, lanes), load_region("pnw"))
    assert dict(config.quarantined) == {}

    violations = " | ".join(lane_contract_violations(config))

    assert "hourly-without-probe: a settled weighted lane" in violations
    assert "hourly-with-probe:" not in violations
    assert "hourly-provisional:" not in violations
    assert "unresolvable-transform: strategy 'contract_fixture.absent' does not resolve" in violations
    assert "gap-fill enabled on ['gap-fill-unpinned']" in violations


@pytest.mark.parametrize(
    ("snippet", "fragment"),
    [
        pytest.param("[coverage]\nwest = -125.0\n", "footprint-shaped key", id="envelope-edge-key"),
        pytest.param("extent = [-125.0, 42.0, -111.0, 49.0]\n", "reads as a bbox", id="bbox-array"),
        pytest.param('admin = "US-OR"\n', "region footprint marker", id="admin-code"),
        pytest.param('api_key_env = "sk_live_9f8e7d6c5b4a"\n', "must NAME", id="key-value-in-env-slot"),
        pytest.param('api_key = "OPEN_METEO_API_KEY"\n', "secret-shaped key", id="secret-named-field"),
        pytest.param('note = "aB3dE5gH7jK9mN1pQ3rS5tU7"\n', "key-shaped value", id="mixed-case-token"),
        pytest.param('url = "https://x.example/v1?apikey=abc"\n', "key-shaped value", id="keyed-query"),
        pytest.param(
            '# customer key: 9fq2xl7pm3vb8hk1ty6z\nid = "x"\n',
            "raw-text token looks key-shaped",
            id="key-shaped-token-in-a-comment",
        ),
        pytest.param(
            'basis = "ABCDEFGHIJKLMNOPQRST5"\n',
            "raw-text token looks key-shaped",
            id="all-upper-plus-digit-value",
        ),
    ],
)
def test_the_raw_file_scan_names_a_planted_literal(tmp_path: Path, snippet: str, fragment: str) -> None:
    """Each marker the landed-tree scan relies on actually fires, so its empty result is evidence."""
    planted = tmp_path / "planted.toml"
    planted.write_text(snippet, encoding="utf-8")

    assert fragment in " | ".join(raw_file_violations(planted))


def test_the_raw_file_scan_passes_a_real_shaped_lane(tmp_path: Path) -> None:
    """Soil's own lane shape (hosts, slugs, strategy keys, a lattice name) trips none of the markers."""
    lane = tmp_path / "soil.toml"
    lane.write_text(to_toml(settled_soil_lane()), encoding="utf-8")

    assert raw_file_violations(lane) == []
