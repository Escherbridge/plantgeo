"""Capture or prepare an immutable MTBS snapshot locally; never publish it."""

from __future__ import annotations

import argparse
import json
import multiprocessing
from pathlib import Path

from agri_data_service.pipeline.direct.burn_severity.capture import capture_snapshot, prepare_capture


def _run(args: argparse.Namespace) -> None:
    receipt = (
        capture_snapshot(args.out)
        if args.capture
        else prepare_capture(args.from_capture, args.manifest_sha256, args.out)
    )
    print(json.dumps(receipt, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true", help="explicitly fetch the reviewed regional source")
    mode.add_argument("--from-capture", type=Path, help="prepare using existing local immutable evidence only")
    parser.add_argument("--manifest-sha256", help="required reviewed manifest digest for local preparation")
    parser.add_argument("--out", type=Path, required=True, help="exclusive new local output directory")
    args = parser.parse_args()
    if args.from_capture and not args.manifest_sha256:
        parser.error("--from-capture requires --manifest-sha256")
    if args.capture and args.manifest_sha256:
        parser.error("--manifest-sha256 belongs only to preparation")
    process = multiprocessing.get_context("spawn").Process(target=_run, args=(args,))
    process.start()
    process.join(timeout=600)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        raise SystemExit("MTBS child exceeded 600 seconds; partial evidence is not apply authority")
    raise SystemExit(process.exitcode)


if __name__ == "__main__":
    main()
