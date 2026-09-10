"""Capture bounded NWS evidence locally; never publish or certify regional completeness."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import re
import tarfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx

from agri_data_service.ingest.sensors import parse_station_page, resolve_nws_user_agent

API: Final = "https://api.weather.gov"
MAX_WORKERS: Final = 8
MAX_WINDOW: Final = timedelta(days=6)
STATION_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
PAGE_LIMIT: Final = 500
HTTP_OK: Final = 200
BBOX_COORDINATES: Final = 4
POINT_COORDINATES: Final = 2
MIN_LONGITUDE: Final = -180
MAX_LONGITUDE: Final = 180
MIN_LATITUDE: Final = -90
MAX_LATITUDE: Final = 90


@dataclass(frozen=True)
class Limits:
    requests: int = 2000
    pages_per_owner: int = 30
    page_bytes: int = 8 * 1024 * 1024
    total_bytes: int = 128 * 1024 * 1024
    records: int = 200000
    seconds: float = 600
    concurrency: int = 4
    stations: int = 750


@dataclass(frozen=True)
class Scope:
    bbox: str
    states: tuple[str, ...]
    networks: tuple[str, ...]
    start: str
    end: str


class CaptureError(ValueError):
    """Evidence remains incomplete after a failed safety or completeness check."""


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CaptureError("timestamps require an explicit offset")
    return parsed


def validate_url(url: str, initial: str) -> str:
    candidate, expected = urlsplit(url), urlsplit(initial)
    if (
        candidate.scheme != "https"
        or candidate.netloc != "api.weather.gov"
        or candidate.path != expected.path
        or candidate.fragment
    ):
        raise CaptureError("pagination left the exact NWS host or owner path")
    query, original = (
        parse_qs(candidate.query, keep_blank_values=True),
        parse_qs(expected.query, keep_blank_values=True),
    )
    if set(query) - {"cursor", "limit", "state", "start", "end"}:
        raise CaptureError("pagination introduced unknown query parameters")
    if any(len(values) != 1 or not values[0] for values in query.values()):
        raise CaptureError("pagination has blank or duplicated query parameters")
    for key in ("state", "start", "end"):
        if key in query and query[key] != original.get(key):
            raise CaptureError("pagination changed its requested source scope")
    if not query.get("cursor") and any(query.get(key) != value for key, value in original.items()):
        raise CaptureError("pagination lost its scope without a cursor")
    if "limit" in query and (not query["limit"][0].isdigit() or not 1 <= int(query["limit"][0]) <= PAGE_LIMIT):
        raise CaptureError("pagination page limit is outside its bound")
    return url


def page_features(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise CaptureError("response is not a FeatureCollection")
    features = payload.get("features")
    if not isinstance(features, list) or any(not isinstance(item, dict) for item in features):
        raise CaptureError("response features are malformed")
    if len(features) > PAGE_LIMIT:
        raise CaptureError("response exceeds requested feature page limit")
    return features


def next_page(payload: dict[str, object], initial: str) -> str | None:
    pagination = payload.get("pagination")
    if pagination is None:
        return None
    if not isinstance(pagination, dict) or set(pagination) != {"next"}:
        raise CaptureError("pagination is malformed")
    following = pagination["next"]
    if not isinstance(following, str) or not following:
        raise CaptureError("pagination next is malformed")
    return validate_url(following, initial)


def validate_observations(features: list[dict[str, object]], station: str, scope: Scope) -> None:
    expected = f"{API}/stations/{station}"
    for feature in features:
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise CaptureError("observation properties are malformed")
        identities = [properties[key] for key in ("station", "stationId", "stationIdentifier") if key in properties]
        if not identities or any(value not in (station, expected) for value in identities):
            raise CaptureError("observation identity does not bind its requested station")
        observed = properties.get("timestamp")
        if not isinstance(observed, str):
            raise CaptureError("observation timestamp is absent")
        if not timestamp(scope.start) <= timestamp(observed) < timestamp(scope.end):
            raise CaptureError("observation timestamp is outside its requested window")


def validate_roster(features: list[dict[str, object]]) -> None:
    for feature in features:
        properties, geometry = feature.get("properties"), feature.get("geometry")
        if (
            not isinstance(properties, dict)
            or not STATION_ID.fullmatch(str(properties.get("stationIdentifier", "")))
            or not isinstance(geometry, dict)
            or geometry.get("type") != "Point"
        ):
            raise CaptureError("roster feature has invalid station identity or geometry")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < POINT_COORDINATES:
            raise CaptureError("roster feature coordinates are missing")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in coordinates[:2]
        ):
            raise CaptureError("roster feature coordinates are not finite numbers")
        if not (MIN_LONGITUDE <= coordinates[0] <= MAX_LONGITUDE and MIN_LATITUDE <= coordinates[1] <= MAX_LATITUDE):
            raise CaptureError("roster feature coordinates exceed WGS84 bounds")


def add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


@dataclass
class Capture:
    client: httpx.AsyncClient
    archive: tarfile.TarFile
    scope: Scope
    limits: Limits
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    deadline: float = field(init=False)
    requests: list[dict[str, object]] = field(default_factory=list)
    total_bytes: int = 0
    records: int = 0

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.limits.seconds

    async def fetch(self, url: str, kind: str, owner: str) -> tuple[dict[str, object], dict[str, object]]:
        if len(self.requests) >= self.limits.requests or time.monotonic() >= self.deadline:
            raise CaptureError("request or wall-time cap reached")
        sequence = len(self.requests)
        receipt: dict[str, object] = {
            "sequence": sequence,
            "kind": kind,
            "owner": owner,
            "url": url,
            "started_at": datetime.now(UTC).isoformat(),
            "status": None,
            "error": None,
        }
        self.requests.append(receipt)
        body = bytearray()
        try:
            async with asyncio.timeout(min(20.0, self.deadline - time.monotonic())):
                async with self.client.stream("GET", url) as response:
                    receipt["status"] = response.status_code
                    receipt["content_type"] = response.headers.get("content-type")
                    receipt["content_encoding"] = response.headers.get("content-encoding")
                    async for chunk in response.aiter_raw():
                        available = min(self.limits.page_bytes - len(body), self.limits.total_bytes - self.total_bytes)
                        piece = chunk[:available]
                        body.extend(piece)
                        self.total_bytes += len(piece)
                        if len(piece) != len(chunk):
                            raise CaptureError("response or total byte cap reached; body is partial")
                    if response.status_code != HTTP_OK:
                        raise CaptureError(f"HTTP status {response.status_code}")
                    if response.headers.get("content-encoding", "identity") not in ("identity", ""):
                        raise CaptureError("unexpected content encoding; bytes retained without reinterpretation")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise CaptureError("response JSON is not an object")
            return payload, receipt
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            receipt["error"] = f"{type(error).__name__}: {error}"
            raise CaptureError(str(receipt["error"])) from error
        finally:
            encoded = bytes(body)
            name = f"responses/{sequence:06d}.json"
            add_bytes(self.archive, name, encoded)
            receipt.update(
                body_path=name,
                sha256=hashlib.sha256(encoded).hexdigest(),
                byte_count=len(encoded),
                retrieved_at=datetime.now(UTC).isoformat(),
            )

    async def walk(self, initial: str, kind: str, owner: str) -> tuple[list[dict[str, object]], dict[str, object]]:
        url: str | None = initial
        seen: set[str] = set()
        collected: list[dict[str, object]] = []
        report: dict[str, object] = {"owner": owner, "complete": False, "request_sequences": [], "errors": []}
        try:
            for _ in range(self.limits.pages_per_owner):
                if url is None:
                    break
                if url in seen:
                    raise CaptureError("pagination repeated a URL")
                seen.add(url)
                payload, receipt = await self.fetch(validate_url(url, initial), kind, owner)
                features = page_features(payload)
                receipt["feature_count"] = len(features)
                if kind == "observations":
                    validate_observations(features, owner, self.scope)
                    if self.records + len(features) > self.limits.records:
                        raise CaptureError("observation record cap reached")
                    self.records += len(features)
                collected.extend(features)
                url = next_page(payload, initial)
                receipt["next_url"] = url
                if not features and url is not None:
                    raise CaptureError("empty page retains next cursor; transport exhaustion unproven")
            if url is not None:
                raise CaptureError("page cap reached with pagination still owed")
            report["complete"] = True
        except (CaptureError, ValueError) as error:
            report["errors"] = [f"{type(error).__name__}: {error}"]
        report["request_sequences"] = [
            item["sequence"] for item in self.requests if item["kind"] == kind and item["owner"] == owner
        ]
        return collected, report

    async def run(self) -> dict[str, object]:
        stations: dict[str, dict[str, object]] = {}
        rosters: list[dict[str, object]] = []
        for state in self.scope.states:
            url = f"{API}/stations?{urlencode({'state': state, 'limit': PAGE_LIMIT})}"
            features, report = await self.walk(url, "roster", state)
            rosters.append(report)
            try:
                validate_roster(features)
                parsed = parse_station_page({"features": features}, self.scope.bbox, frozenset(self.scope.networks))
                for station in parsed.stations:
                    if not STATION_ID.fullmatch(station.station_identifier):
                        raise CaptureError("roster station identifier cannot form a safe owner path")
                    value = asdict(station)
                    held = stations.setdefault(station.station_identifier, value)
                    if held != value:
                        raise CaptureError("roster repeats a station with conflicting metadata")
            except ValueError as error:
                report.update(complete=False, errors=[str(error)])
        selected = sorted(stations)[: self.limits.stations]
        results: list[dict[str, object]] = []
        for offset in range(0, len(selected), self.limits.concurrency):
            chunk = selected[offset : offset + self.limits.concurrency]
            outcomes = await asyncio.gather(
                *(
                    self.walk(
                        f"{API}/stations/{quote(station, safe='')}/observations?"
                        + urlencode({"start": self.scope.start, "end": self.scope.end, "limit": PAGE_LIMIT}),
                        "observations",
                        station,
                    )
                    for station in chunk
                )
            )
            results.extend(report for _, report in outcomes)
        roster_complete = all(report["complete"] is True for report in rosters)
        cap_applied = len(stations) > len(selected)
        retention_eligible = timestamp(self.scope.start) >= self.started - MAX_WINDOW
        return {
            "schema_version": "sensor-evidence-capture/v1",
            "scope": asdict(self.scope) | {"kind": "configured_selected_station_roster", "not_entire_region": True},
            "limits": asdict(self.limits),
            "started_at": self.started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "roster": {
                "stations": [stations[key] for key in selected],
                "eligible_count": len(stations),
                "selected_count": len(selected),
                "station_cap_applied": cap_applied,
                "complete": roster_complete,
                "state_reports": rosters,
            },
            "stations": results,
            "requests": sorted(self.requests, key=lambda row: int(str(row["sequence"]))),
            "counts": {"requests": len(self.requests), "bytes": self.total_bytes, "observation_features": self.records},
            "retention_eligible": retention_eligible,
            "upstream_population_complete": False,
            "source_complete": bool(selected)
            and roster_complete
            and not cap_applied
            and retention_eligible
            and all(report["complete"] is True for report in results),
            "completeness_basis": (
                "Transport exhausted only for configured selected roster/window; "
                "never proof of regional census or an empty day."
            ),
        }


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--output", type=Path, required=True)
    built.add_argument("--start", required=True)
    built.add_argument("--end", required=True)
    built.add_argument("--bbox", required=True)
    built.add_argument("--states", default="WA,OR,ID,MT")
    built.add_argument("--networks", default="ASOS,ASOS-HFM,RAWS,NonFedAWOS")
    built.add_argument("--user-agent", default=resolve_nws_user_agent())
    built.add_argument("--capture", action="store_true", help="Perform bounded upstream GETs; default is plan only.")
    for name, default in asdict(Limits()).items():
        built.add_argument(
            f"--max-{name.replace('_', '-')}" if name != "concurrency" else "--concurrency",
            type=type(default),
            default=default,
        )
    return built


def validate_limits(limits: Limits) -> None:
    ceilings = Limits(
        requests=10000,
        pages_per_owner=100,
        page_bytes=16 * 1024 * 1024,
        total_bytes=512 * 1024 * 1024,
        records=1000000,
        seconds=3600,
        concurrency=MAX_WORKERS,
        stations=5000,
    )
    for name, value in asdict(limits).items():
        if not math.isfinite(value) or not 0 < value <= asdict(ceilings)[name]:
            raise CaptureError(f"{name} must be positive and at most {asdict(ceilings)[name]}")


def configuration(args: argparse.Namespace) -> tuple[Scope, Limits]:
    limits = Limits(
        **{name: getattr(args, f"max_{name}" if name != "concurrency" else name) for name in asdict(Limits())}
    )
    validate_limits(limits)
    if not timestamp(args.start) < timestamp(args.end) <= datetime.now(UTC):
        raise CaptureError("window must be ordered and end no later than now")
    coordinates = [float(value) for value in args.bbox.split(",")]
    if len(coordinates) != BBOX_COORDINATES or not (
        MIN_LONGITUDE <= coordinates[0] < coordinates[2] <= MAX_LONGITUDE
        and MIN_LATITUDE <= coordinates[1] < coordinates[3] <= MAX_LATITUDE
    ):
        raise CaptureError("bbox must be finite ordered W,S,E,N")
    states = tuple(sorted(set(args.states.upper().split(","))))
    if any(not re.fullmatch(r"[A-Z]{2}", state) for state in states):
        raise CaptureError("states must be two-letter comma-separated codes")
    networks = tuple(sorted({value.strip().upper() for value in args.networks.split(",") if value.strip()}))
    return Scope(args.bbox, states, networks, args.start, args.end), limits


async def execute(args: argparse.Namespace, scope: Scope, limits: Limits) -> dict[str, object]:
    with args.output.open("xb") as output, tarfile.open(fileobj=output, mode="w:gz") as archive:
        async with httpx.AsyncClient(
            headers={"User-Agent": args.user_agent, "Accept": "application/geo+json", "Accept-Encoding": "identity"},
            follow_redirects=False,
        ) as client:
            manifest = await Capture(client, archive, scope, limits).run()
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        add_bytes(archive, "manifest.json", payload)
        add_bytes(archive, "manifest.sha256", hashlib.sha256(payload).hexdigest().encode())
    return manifest


def main() -> int:
    args = parser().parse_args()
    scope, limits = configuration(args)
    if not args.capture:
        print(
            json.dumps(
                {"capture": False, "scope": asdict(scope), "limits": asdict(limits), "output": str(args.output)},
                sort_keys=True,
            )
        )
        return 0
    manifest = asyncio.run(execute(args, scope, limits))
    print(
        json.dumps(
            {"output": str(args.output), "source_complete": manifest["source_complete"], "counts": manifest["counts"]},
            sort_keys=True,
        )
    )
    return 0 if manifest["source_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
