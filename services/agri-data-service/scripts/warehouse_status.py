"""One-shot health report for the continuous Parquet warehouse load.

Answers the only four questions a monitor actually needs, in one command, so checking on the drain
costs one line instead of a session's worth of ad-hoc probing:

  1. Is the supervisor loop still alive?      (a dead loop means nothing is draining, silently)
  2. Is it making progress?                   (days written/absent, and how recently)
  3. Is the four-rung zoom ladder growing?    (zoom 00/05/09 must climb together, not just 13)
  4. Is anything failing?                     (`raised` days, non-zero verb exits)

Read-only: it lists the bucket and reads the loop's log. It never writes an object, never touches
Postgres, and never restarts anything -- deciding to restart is the operator's call, and the report
says plainly when that is the call.

Credentials come from services/agri-data-service/.env and are never printed.

    uv run python scripts/warehouse_status.py          # from services/agri-data-service
    uv run python scripts/warehouse_status.py --quiet  # verdict line only
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

import boto3  # type: ignore[import-untyped]

SERVICE_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP_LOG = SERVICE_ROOT / "continuous-warehouse-loop.log"
LOOP_SCRIPT_NAME = "continuous-warehouse-loop.sh"

# A drain pass can legitimately spend this long on one cold `signal` day, so silence shorter than
# this is not evidence of a stall. Measured 2026-08-24: ~8 s/day uncontended, ~25 min contended.
QUIET_MINUTES_BEFORE_SUSPICION = 30

DAY_LINE = re.compile(
    r"^(fire-detections|burn-severity|signal|vegetation|drought|water-gauges|sensors)\s+\d{4}-\d{2}-\d{2}\s+(\w+)"
)


def read_environment() -> dict[str, str]:
    """Parse .env into a dict without importing the service's settings machinery."""
    values: dict[str, str] = {}
    env_path = SERVICE_ROOT / ".env"
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        matched = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
        if matched:
            values[matched.group(1)] = matched.group(2).strip().strip('"').strip("'")
    return values


def supervisor_is_alive() -> bool:
    """True when a shell is still running the loop script. A drain with no supervisor never restarts."""
    try:
        listing = subprocess.run(
            ["wmic", "process", "get", "CommandLine"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return LOOP_SCRIPT_NAME in listing


@dataclass(frozen=True, slots=True)
class LoopSummary:
    """What the supervisor log says about the drain, or that there is no log to read.

    A dataclass rather than a `dict[str, object]` because every consumer below does arithmetic or
    numeric formatting on these values, and `object` makes each of those a cast at the call site.
    `present=False` keeps the zero defaults: an absent log has made no progress, by definition.
    """

    present: bool
    written: int = 0
    absent: int = 0
    raised: int = 0
    contended: int = 0
    verb_failures: int = 0
    last_day_line: str = ""
    quiet_minutes: float = 0.0


def summarise_loop_log() -> LoopSummary:
    """Days done, failures, and how long since the log last moved."""
    if not LOOP_LOG.exists():
        return LoopSummary(present=False)
    text = LOOP_LOG.read_text(encoding="utf-8", errors="replace")
    outcomes: Counter[str] = Counter()
    last_day_line = ""
    for line in text.splitlines():
        matched = DAY_LINE.match(line)
        if matched:
            outcomes[matched.group(2)] += 1
            last_day_line = line
    modified = dt.datetime.fromtimestamp(LOOP_LOG.stat().st_mtime, tz=dt.UTC)
    return LoopSummary(
        present=True,
        written=outcomes.get("written", 0),
        absent=outcomes.get("absent", 0),
        raised=outcomes.get("raised", 0),
        contended=outcomes.get("contended", 0),
        verb_failures=text.count(" EXIT "),
        last_day_line=last_day_line[:100],
        quiet_minutes=(dt.datetime.now(dt.UTC) - modified).total_seconds() / 60,
    )


def census_bucket(values: dict[str, str]) -> dict[str, object]:
    """Object counts by zoom rung, plus how recently anything landed."""
    client = boto3.client(
        "s3",
        endpoint_url=values["OBJECT_STORE_ENDPOINT_URL"],
        region_name=values.get("OBJECT_STORE_REGION", "auto"),
        aws_access_key_id=values["OBJECT_STORE_ACCESS_KEY_ID"],
        aws_secret_access_key=values["OBJECT_STORE_SECRET_ACCESS_KEY"],
    )
    markers_by_zoom: Counter[str] = Counter()
    total = markers = absences = 0
    newest = None
    token = None
    while True:
        request = {"Bucket": values["OBJECT_STORE_BUCKET"]}
        if token:
            request["ContinuationToken"] = token
        response = client.list_objects_v2(**request)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            total += 1
            stamp = obj["LastModified"]
            newest = stamp if newest is None or stamp > newest else newest
            if key.endswith("_complete.json"):
                markers += 1
                zoom = next((p.split("=", 1)[1] for p in key.split("/") if p.startswith("zoom=")), "?")
                markers_by_zoom[zoom] += 1
            elif key.endswith("absent.json"):
                absences += 1
        token = response.get("NextContinuationToken")
        if not token:
            break
    return {
        "objects": total,
        "markers": markers,
        "absences": absences,
        "markers_by_zoom": dict(sorted(markers_by_zoom.items())),
        "newest": newest,
    }


def main() -> int:  # noqa: PLR0912 - one read-only report intentionally owns every verdict branch
    """Print the report and return a shell-meaningful code: 0 healthy, 1 needs attention."""
    quiet_only = "--quiet" in sys.argv
    alive = supervisor_is_alive()
    loop = summarise_loop_log()
    values = read_environment()

    bucket: dict[str, object] = {}
    if values.get("OBJECT_STORE_BUCKET"):
        try:
            bucket = census_bucket(values)
        except Exception as error:  # a bucket that will not list is itself the finding
            bucket = {"error": str(error)}

    problems: list[str] = []
    if not alive:
        problems.append("SUPERVISOR IS NOT RUNNING -- nothing is draining; restart the loop")
    if not loop.present:
        problems.append("no loop log found -- the loop has never run from this directory")
    else:
        if loop.raised:
            problems.append(f"{loop.raised} lane-day(s) raised")
        if loop.verb_failures:
            problems.append(f"{loop.verb_failures} verb exit(s) non-zero")
        if alive and loop.quiet_minutes > QUIET_MINUTES_BEFORE_SUSPICION:
            problems.append(
                f"log silent {loop.quiet_minutes:.0f} min -- cross-check the bucket's newest "
                "object before calling it stalled; one cold signal day can take ~25 min"
            )
    if isinstance(bucket.get("error"), str):
        problems.append(f"bucket would not list: {bucket['error']}")

    if not quiet_only:
        print(f"supervisor      : {'alive' if alive else 'NOT RUNNING'}")
        if loop.present:
            print(
                f"days            : {loop.written} written, {loop.absent} absent, "
                f"{loop.raised} raised, {loop.contended} contended"
            )
            print(f"last day        : {loop.last_day_line}")
            print(f"log last moved  : {loop.quiet_minutes:.1f} min ago")
        if bucket and "error" not in bucket:
            print(f"bucket objects  : {bucket['objects']:,}")
            print(f"completion marks: {bucket['markers']:,}  by zoom {bucket['markers_by_zoom']}")
            print(f"governed absent : {bucket['absences']:,}")
            print(f"newest object   : {bucket['newest']}")
        print()

    if problems:
        print("NEEDS ATTENTION:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("HEALTHY: supervisor running, days advancing, no failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
