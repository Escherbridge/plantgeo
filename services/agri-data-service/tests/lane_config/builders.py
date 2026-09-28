"""Builders for lane TOMLs and `lanes/` trees with production-shaped defaults (today's soil facts).

A built tree always carries the REAL provider files and `AGENTS.md`, copied from the service's own
`lanes/`, so a loader test exercises the providers production loads rather than stand-ins.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

SERVICE_ROOT: Final = Path(__file__).resolve().parents[2]
REAL_LANES_DIRECTORY: Final = SERVICE_ROOT / "lanes"

_BARE_KEY: Final = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(name: str) -> str:
    return name if _BARE_KEY.match(name) else json.dumps(name)


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    message = f"no TOML rendering for {value!r}"
    raise TypeError(message)


def _table_array(value: object) -> list[Mapping[str, object]] | None:
    """The items of an array of tables, or None when `value` is anything else."""
    if isinstance(value, (list, tuple)) and value and all(isinstance(item, Mapping) for item in value):
        return [item for item in value if isinstance(item, Mapping)]
    return None


def _emit_table(lines: list[str], path: tuple[str, ...], table: Mapping[str, object]) -> None:
    if path:
        lines.append(f"[{'.'.join(_key(part) for part in path)}]")
    for name, value in table.items():
        if value is not None and not isinstance(value, Mapping) and _table_array(value) is None:
            lines.append(f"{_key(name)} = {_value(value)}")
    for name, value in table.items():
        items = _table_array(value)
        if isinstance(value, Mapping):
            _emit_table(lines, (*path, name), value)
        elif items is not None:
            for item in items:
                lines.append(f"[[{'.'.join(_key(part) for part in (*path, name))}]]")
                lines.extend(f"{_key(key)} = {_value(entry)}" for key, entry in item.items() if entry is not None)


def to_toml(document: Mapping[str, object]) -> str:
    """Render a nested mapping as TOML; a `None` value omits its key, which is how builders drop a field."""
    lines: list[str] = []
    _emit_table(lines, (), document)
    return "\n".join(lines) + "\n"


def merged(base: Mapping[str, object], overrides: Mapping[str, object]) -> dict[str, object]:
    """Deep-merge `overrides` into `base`: nested tables merge, anything else replaces."""
    result = dict(base)
    for name, value in overrides.items():
        current = result.get(name)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[name] = merged(current, value)
        else:
            result[name] = value
    return result


def settled_soil_lane(lane_id: str = "soil-era5-land-direct-forward", **overrides: object) -> dict[str, object]:
    """Today's soil lane as spec §4.1 illustrates it: settled, on the Open-Meteo archive, 6-hourly."""
    lane: dict[str, object] = {
        "id": lane_id,
        "kind": "ingest",
        "nature": "daily_series",
        "enabled": True,
        "executor": "legacy",
        "strategy": "soil.open_meteo_era5_land",
        "grid": "analysis-0p25",
        "source": {
            "provider": "open-meteo",
            "endpoint": "archive",
            "model": "era5_land",
            "coverage": "global",
            "history": {"capability": "archive", "earliest": date(1950, 1, 1)},
            "lifecycle": "active",
        },
        "schedule": {
            "forward_cron": "50 */6 * * *",
            "gap_fill_cron": "20 3 * * *",
            "gap_fill_enabled": False,
            "catch_up": "coalesce_latest",
        },
        "days": {
            "floor": date(2022, 8, 2),
            "floor_basis": "first day of the reviewed ERA5-Land archive plan",
            "publication_lag_days": 5,
            "absence_recheck_days": 14,
            "partial_day": "refuse",
            "expected_value_units": 1470,
        },
        "budget": {
            "forward_max_weighted_calls": 1600,
            "gap_fill_max_weighted_calls": 1600,
            "max_concurrency": 2,
            "turn_timeout_seconds": 900,
        },
        "streams": [
            {
                "slug": f"{lane_id}-vpd",
                "history_floor": date(2022, 8, 2),
                "floor_basis": "first day of the reviewed ERA5-Land archive plan",
            }
        ],
    }
    return merged(lane, overrides)


def provisional_forecast_lane(
    lane_id: str = "weather-observations-direct-forward", **overrides: object
) -> dict[str, object]:
    """A `write_and_recheck` lane on the Open-Meteo forecast host: exempt from the probe_edge rule."""
    return settled_soil_lane(
        lane_id,
        **merged(
            {
                "strategy": "weather_observations.open_meteo_current",
                "grid": None,
                "source": {"endpoint": "forecast", "model": None, "history": {"capability": "none", "earliest": None}},
                "schedule": {"forward_cron": "5 * * * *", "gap_fill_cron": None},
                "days": {"publication_lag_days": 0, "absence_recheck_days": 2, "partial_day": "write_and_recheck"},
            },
            overrides,
        ),
    )


def nasa_power_lane(lane_id: str = "climate-nasa-power-direct-forward", **overrides: object) -> dict[str, object]:
    """A settled lane on keyless NASA POWER, with no analysis lattice: region-portable by construction."""
    return settled_soil_lane(
        lane_id,
        **merged(
            {
                "strategy": "climate.nasa_power_daily",
                "grid": None,
                "source": {"provider": "nasa-power", "endpoint": "daily-point", "model": None},
                "schedule": {"forward_cron": "15 * * * *"},
            },
            overrides,
        ),
    )


def transform_lane(lane_id: str, inputs: Iterable[str], **overrides: object) -> dict[str, object]:
    """A derived fact lane (D4) over `inputs`: no source, daily, rebuilt when an input publishes."""
    lane = settled_soil_lane(lane_id, **merged({"strategy": "transforms.precedence"}, overrides))
    lane["kind"] = "transform"
    lane["source"] = None
    lane["inputs"] = list(inputs)
    return lane


def write_lane_tree(root: Path, lanes: Iterable[Mapping[str, object]]) -> Path:
    """Write `root/lanes/` with the real provider files and AGENTS.md plus one `<id>.toml` per lane."""
    directory = root / "lanes"
    shutil.copytree(REAL_LANES_DIRECTORY / "_providers", directory / "_providers")
    shutil.copy2(REAL_LANES_DIRECTORY / "AGENTS.md", directory / "AGENTS.md")
    for lane in lanes:
        (directory / f"{lane['id']}.toml").write_text(to_toml(lane), encoding="utf-8")
    return directory
