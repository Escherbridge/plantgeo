"""Strict daily-value replay around the retained pure NWIS source normalizer."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, time
from typing import Final

from agri_data_service.ingest.usgs_nwis import parse_daily_value_series
from agri_data_service.pipeline.water_gauges_recovery.models import MAX_RECORDS, WaterSourceCapture

MISSING_VALUE: Final = -999999.0
MIDNIGHT: Final = time(0)
DECIMAL_TEXT: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
MAX_SITE_NAME_CHARACTERS: Final = 1024
MAX_SITE_NUMBER_CHARACTERS: Final = 64


def _mapping(value: object, label: str) -> dict[str, object]:
    """Refuse malformed nested WaterML JSON objects."""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"NWIS {label} must be a JSON object")
    return value


def _list(value: object, label: str) -> list[object]:
    """Refuse a missing array instead of treating it as empty coverage."""
    if not isinstance(value, list):
        raise ValueError(f"NWIS {label} must be a JSON array")
    return value


def _number(value: object, label: str) -> float:
    """Require a whole finite numeric value rather than JavaScript prefix parsing."""
    if isinstance(value, bool) or not isinstance(value, str | float | int):
        raise ValueError(f"NWIS {label} must be finite numeric text or a number")
    if isinstance(value, str) and DECIMAL_TEXT.fullmatch(value.strip()) is None:
        raise ValueError(f"NWIS {label} must be a whole ASCII decimal")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"NWIS {label} must be finite")
    return number


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys before a decoder can select the last value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("NWIS response contains duplicate JSON object keys")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> object:
    """Reject nonstandard JSON numeric constants anywhere in a source response."""
    raise ValueError("NWIS response contains a non-finite JSON constant")


def _variable(series: dict[str, object]) -> None:
    """Bind discharge units and daily mean statistical support explicitly."""
    variable = _mapping(series.get("variable"), "variable")
    codes = _list(variable.get("variableCode"), "variableCode")
    if len(codes) != 1 or _mapping(codes[0], "variableCode item").get("value") != "00060":
        raise ValueError("NWIS series does not describe parameter 00060 discharge")
    if _mapping(variable.get("unit"), "unit").get("unitCode") != "ft3/s":
        raise ValueError("NWIS discharge series does not declare ft3/s units")
    options = _list(_mapping(variable.get("options"), "options").get("option"), "option")
    statistics = [_mapping(item, "option item") for item in options]
    statistics = [item for item in statistics if item.get("name") == "Statistic"]
    if len(statistics) != 1 or statistics[0].get("optionCode") != "00003":
        raise ValueError("NWIS series does not declare daily mean statistic 00003")
    if _number(variable.get("noDataValue"), "noDataValue") != MISSING_VALUE:
        raise ValueError("NWIS series uses an unsupported missing-value sentinel")


def _source(series: dict[str, object], tile: str) -> tuple[str, str]:
    """Validate site identity, native coordinates and publisher standard time."""
    source = _mapping(series.get("sourceInfo"), "sourceInfo")
    codes = _list(source.get("siteCode"), "siteCode")
    if len(codes) != 1:
        raise ValueError("NWIS source site identity is ambiguous")
    site = _mapping(codes[0], "siteCode item").get("value")
    if not isinstance(site, str) or re.fullmatch(r"[0-9]+", site) is None or len(site) > MAX_SITE_NUMBER_CHARACTERS:
        raise ValueError("NWIS source site number must be nonempty digit text")
    site_name = source.get("siteName")
    if not isinstance(site_name, str) or not site_name or len(site_name) > MAX_SITE_NAME_CHARACTERS:
        raise ValueError("NWIS source site name is missing or exceeds its text bound")
    location = _mapping(_mapping(source.get("geoLocation"), "geoLocation").get("geogLocation"), "geogLocation")
    if any(
        isinstance(location.get(key), bool) or not isinstance(location.get(key), int | float)
        for key in ("latitude", "longitude")
    ):
        raise ValueError("NWIS source coordinates must be JSON numbers")
    lat, lon = _number(location.get("latitude"), "latitude"), _number(location.get("longitude"), "longitude")
    west, south, east, north = (float(value) for value in tile.split(","))
    if not west <= lon <= east or not south <= lat <= north:
        raise ValueError("NWIS source gauge lies outside its requested tile")
    zone = _mapping(source.get("timeZoneInfo"), "timeZoneInfo")
    offset = _mapping(zone.get("defaultTimeZone"), "defaultTimeZone").get("zoneOffset")
    if not isinstance(offset, str) or re.fullmatch(r"[+-](?:0\d|1[0-3]):[0-5]\d|[+-]14:00", offset) is None:
        raise ValueError("NWIS daily values require an explicit valid source standard-time offset")
    return site, offset


def _reading(value: object, capture: WaterSourceCapture) -> tuple[dict[str, object], date, float]:
    """Require a finite reading on one publisher-named midnight in the request."""
    reading = _mapping(value, "reading")
    timestamp = reading.get("dateTime")
    if not isinstance(timestamp, str) or "T" not in timestamp:
        raise ValueError("NWIS daily value has no source timestamp")
    instant = datetime.fromisoformat(timestamp)
    day = instant.date()
    if instant.tzinfo is not None or instant.time() != MIDNIGHT or timestamp[:10] != day.isoformat():
        raise ValueError("NWIS daily-value timestamps must name a naive publisher midnight")
    if not capture.start_day <= day < capture.end_day_exclusive:
        raise ValueError("NWIS source reading lies outside the requested publisher days")
    raw = reading.get("value")
    if not isinstance(raw, str):
        raise ValueError("NWIS reading value must be numeric source text")
    flow = _number(raw, "reading value")
    qualifiers = _list(reading.get("qualifiers"), "reading qualifiers")
    if any(not isinstance(item, str) or not item for item in qualifiers):
        raise ValueError("NWIS source qualifiers are malformed")
    return reading, day, flow


def normalize_responses(
    bodies: list[tuple[str, bytes]],
    *,
    capture: WaterSourceCapture,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Account for every raw reading and refuse conflicting or undercovered source series."""
    records: dict[tuple[str, date], dict[str, object]] = {}
    seen: dict[tuple[str, date], str] = {}
    raw_count = sentinel_count = duplicates = 0
    for tile, body in bodies:
        payload = _mapping(
            json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_constant), "response"
        )
        value = _mapping(payload.get("value"), "value")
        series_list = _list(value.get("timeSeries"), "timeSeries")
        for item in series_list:
            series = _mapping(item, "timeSeries item")
            _variable(series)
            site, offset = _source(series, tile)
            values = _list(series.get("values"), "values")
            if len(values) != 1:
                raise ValueError("NWIS series has ambiguous value blocks")
            block = _mapping(values[0], "values item")
            readings = _list(block.get("value"), "reading values")
            days: set[date] = set()
            accepted: list[dict[str, object]] = []
            for raw in readings:
                raw_count += 1
                if raw_count > MAX_RECORDS:
                    raise ValueError("NWIS source window exceeds the raw reading cap")
                reading, day, flow = _reading(raw, capture)
                days.add(day)
                grain = site, day
                fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "sourceInfo": series["sourceInfo"],
                            "variable": series["variable"],
                            "reading": reading,
                            "offset": offset,
                        },
                        allow_nan=False,
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                if grain in seen:
                    if seen[grain] != fingerprint:
                        raise ValueError("conflicting NWIS daily values share a site and publisher day")
                    duplicates += 1
                    continue
                seen[grain] = fingerprint
                if flow == MISSING_VALUE:
                    sentinel_count += 1
                else:
                    accepted.append(reading)
            if len(days) != (capture.end_day_exclusive - capture.start_day).days:
                raise ValueError(
                    "NWIS source series undercovers its requested days; explicit absence evidence is required"
                )
            parsed = parse_daily_value_series({**series, "values": [{**block, "value": accepted}]})
            if len(parsed) != len(accepted):
                raise ValueError("NWIS parser rejected part of the non-sentinel source population")
            for record in parsed:
                day = date.fromisoformat(str(record["updatedAt"])[:10])
                records[(site, day)] = record
    return list(records.values()), {
        "raw_readings": raw_count,
        "sentinel_readings": sentinel_count,
        "identical_duplicates": duplicates,
        "normalized_readings": len(records),
    }
