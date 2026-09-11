"""Capture or prepare 1984-2017 MTBS recovery evidence locally; never stage or publish."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import threading
from pathlib import Path

from agri_data_service.pipeline.direct.burn_severity.older_capture import capture_older_population
from agri_data_service.pipeline.direct.burn_severity.older_recovery import (
    prepare_older_capture,
    verify_current_preservation,
    write_recovery_packet,
)

CHILD_TIMEOUT_SECONDS = 600


def _run(args: argparse.Namespace) -> None:
    watchdog = threading.Timer(CHILD_TIMEOUT_SECONDS, os._exit, args=(2,))
    watchdog.daemon = True
    watchdog.start()
    if args.capture:
        result = capture_older_population(args.out)
    else:
        preservation = verify_current_preservation(
            args.current_capture, args.current_prepared, args.current_manifest_sha256
        )
        candidate = prepare_older_capture(args.from_capture, args.manifest_sha256, args.out)
        write_recovery_packet(args.out, preservation, captured=args.from_capture)
        result = {
            "status": "candidate_prepared_admission_blocked",
            "manifest_sha256": candidate["manifest"]["sha256"],
            "rows": candidate["descriptor"]["source_row_count"],
            "available_day": candidate["descriptor"]["available_day"],
            "rungs": [
                {"zoom": rung["zoom"], "rows": rung["rows"], "parts": len(rung["parts"])} for rung in candidate["rungs"]
            ],
            "artifact_bytes": candidate["artifact_bytes"],
            "apply_authority": False,
        }
    print(json.dumps(result, sort_keys=True), flush=True)
    watchdog.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true", help="explicit bounded public-source GET capture")
    mode.add_argument("--from-capture", type=Path, help="prepare from preserved local source bytes")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--current-capture", type=Path)
    parser.add_argument("--current-prepared", type=Path)
    parser.add_argument("--current-manifest-sha256")
    parser.add_argument("--out", type=Path, required=True, help="exclusive new local output directory")
    args = parser.parse_args()
    protection = (args.manifest_sha256, args.current_capture, args.current_prepared, args.current_manifest_sha256)
    if args.from_capture and not all(protection):
        parser.error(
            "preparation requires the older manifest digest and all three current-snapshot preservation arguments"
        )
    if args.capture and any(protection):
        parser.error("source/current preservation arguments belong only to --from-capture")
    process = multiprocessing.get_context("spawn").Process(target=_run, args=(args,))
    process.start()
    process.join(timeout=CHILD_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        raise SystemExit("MTBS older recovery exceeded 600 seconds; partial evidence is not apply authority")
    raise SystemExit(process.exitcode)


if __name__ == "__main__":
    main()
