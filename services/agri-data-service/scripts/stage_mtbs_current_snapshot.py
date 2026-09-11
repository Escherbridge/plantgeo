"""Verify an existing local MTBS capture; explicitly stage it only with --stage."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from agri_data_service.pipeline.direct.burn_severity.capture import prepare_capture
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import MAX_MANIFEST_BYTES, canonical_bytes, digest
from agri_data_service.pipeline.direct.burn_severity.stage import stage_prepared
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage


def _run(args: argparse.Namespace) -> None:
    result: dict[str, Any]
    watchdog = threading.Timer(600, os._exit, args=(2,))
    watchdog.daemon = True
    watchdog.start()
    if args.stage:
        result = stage_prepared(
            BotoAvailabilityStorage.from_settings(),
            capture=args.capture,
            prepared=args.prepared,
            manifest_sha256=args.manifest_sha256,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="mtbs-adoption-preview-") as temporary:
            result = cast(
                "dict[str, Any]", prepare_capture(args.capture, args.manifest_sha256, Path(temporary) / "prepared")
            )
        with (args.prepared / "preparation.json").open("rb") as handle:
            body = handle.read(MAX_MANIFEST_BYTES + 1)
        if body != canonical_bytes(result):
            raise ValueError("existing preparation differs from independently reproduced source")
        for rung in result["rungs"]:
            path = args.prepared / "blobs" / rung["sha256"]
            if path.is_symlink():
                raise ValueError("prepared candidate must not be a symlink")
            with path.open("rb") as handle:
                payload = handle.read(rung["bytes"] + 1)
            if len(payload) != rung["bytes"] or digest(payload) != rung["sha256"]:
                raise ValueError("prepared candidate bytes differ from reproduced source")
        result = {"status": "verified_only", "manifest_sha256": args.manifest_sha256, "apply_authority": False}
    print(json.dumps(result, sort_keys=True), flush=True)
    watchdog.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--stage", action="store_true", help="archive immutable evidence and enqueue; never publish")
    args = parser.parse_args()
    process = multiprocessing.get_context("spawn").Process(target=_run, args=(args,))
    process.start()
    process.join(timeout=600)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        raise SystemExit("MTBS stage adoption exceeded its deadline; partial immutable evidence stays recoverable")
    raise SystemExit(process.exitcode)


if __name__ == "__main__":
    main()
