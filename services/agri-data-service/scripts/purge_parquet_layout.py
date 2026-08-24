"""Delete the Parquet warehouse's objects so the bulk drain can rewrite it from a clean bucket.

Run from services/agri-data-service:

    uv run python scripts/purge_parquet_layout.py                 # DRY RUN: count, never delete
    uv run python scripts/purge_parquet_layout.py --json
    uv run python scripts/purge_parquet_layout.py --layer drought # narrow to one stream
    uv run python scripts/purge_parquet_layout.py --confirm       # actually delete

DRY RUN IS THE DEFAULT AND `--confirm` IS THE ONLY WAY PAST IT. This deletes production objects and
nothing here is recoverable; the bucket has no versioning.

WHY THIS EXISTS (owner decision, RUNBOOK 0.35.5). Every day already written under the zoom layout
predates the completion marker, so it has none and classifies `incomplete` on deploy. The rejected
alternative was a backfill verb that stamps completion onto those days -- but nothing verified them,
and asserting completion for an unverified day is the one claim this marker exists to make
trustworthy. RUNBOOK 0.32.4 had already decided these objects are DISCARDED rather than migrated, so
marking objects that are slated for deletion is work spent to weaken a guarantee.

WHAT IT DELETES. By default every object under the store prefix that PARSES as the current layout --
part files, governed-absence markers and completion markers alike. Legacy objects written before the
`zoom=` axis existed do not parse and are left alone unless `--include-unparsable` is passed; they
are equally condemned (RUNBOOK 0.32.4) but deleting an unrecognised key is a different and blunter
act than deleting one this code can name, so it asks separately.

A GOVERNED ABSENCE IS EVIDENCE, AND THIS DESTROYS IT TOO. `absent.json` records that a source was
asked and had nothing -- a claim the drain must re-derive rather than inherit. That is intended
here: the drain rewrites every day it walks, and an absence marker surviving beside freshly drained
parts would be the `conflict` state that only an admin may resolve.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agri_data_service.config import settings
from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore

KIND_PART = "part"
KIND_ABSENCE = "absence-marker"
KIND_COMPLETION = "completion-marker"
KIND_UNPARSABLE = "unparsable-legacy"


def classify(relative_path: str) -> str:
    """Name which of the layout's three object kinds a key is, or mark it as pre-zoom legacy."""
    if try_parse_partition_path(relative_path) is not None:
        return KIND_PART
    if try_parse_absence_marker_path(relative_path) is not None:
        return KIND_ABSENCE
    if try_parse_completion_marker_path(relative_path) is not None:
        return KIND_COMPLETION
    return KIND_UNPARSABLE


def layer_of(relative_path: str) -> str:
    """Return the `layer=` segment of a key, or `<unparsable>` when it has none."""
    head = relative_path.split("/", 1)[0]
    return head[len("layer=") :] if head.startswith("layer=") else "<unparsable>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete. Without it this only counts, which is the default and the safe mode.",
    )
    parser.add_argument(
        "--layer",
        action="append",
        default=None,
        metavar="SLUG",
        help="Restrict to one layer slug; repeatable. Default: every layer in the bucket.",
    )
    parser.add_argument(
        "--include-unparsable",
        action="store_true",
        help="Also delete keys that do not parse as the current layout -- the pre-zoom objects of "
        "RUNBOOK 0.32.4. Off by default: deleting a key this code cannot name is a blunter act.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args()

    store = ObjectStore.from_settings(settings)
    wanted_layers = set(args.layer) if args.layer else None

    by_kind: Counter[str] = Counter()
    by_layer: Counter[str] = Counter()
    doomed: list[str] = []
    for listed in store._backend.list_objects(store.prefix):
        relative_path = store.relative_key(listed.key)
        kind = classify(relative_path)
        layer = layer_of(relative_path)
        if wanted_layers is not None and layer not in wanted_layers:
            continue
        by_kind[kind] += 1
        by_layer[layer] += 1
        if kind == KIND_UNPARSABLE and not args.include_unparsable:
            continue
        doomed.append(listed.key)

    deleted = 0
    failures: list[str] = []
    if args.confirm:
        for key in doomed:
            try:
                store._backend.delete(key)
            except Exception as error:  # one undeletable key must not abandon the rest
                failures.append(f"{key}: {type(error).__name__}: {error}")
                continue
            deleted += 1

    report = {
        "bucket_prefix": store.prefix,
        "dry_run": not args.confirm,
        "seen_by_kind": dict(by_kind),
        "seen_by_layer": dict(sorted(by_layer.items())),
        "selected_for_deletion": len(doomed),
        "deleted": deleted,
        "failures": failures,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        mode = "DRY RUN -- nothing was deleted" if not args.confirm else "DELETED"
        print(f"{mode}. prefix={store.prefix or '<bucket root>'}")
        for kind, count in sorted(by_kind.items()):
            print(f"  {kind:>18}: {count}")
        print(f"  {'selected':>18}: {len(doomed)}")
        if args.confirm:
            print(f"  {'deleted':>18}: {deleted}")
        for failure in failures:
            print(f"  FAILED {failure}")
        if not args.confirm and doomed:
            print("\nRe-run with --confirm to delete. There is no undo and the bucket has no versioning.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
