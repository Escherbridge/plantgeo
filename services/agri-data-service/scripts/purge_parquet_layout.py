"""Inventory the historical Parquet layout without mutating object storage.

Run from services/agri-data-service:

    uv run python scripts/purge_parquet_layout.py                 # DRY RUN: count, never delete
    uv run python scripts/purge_parquet_layout.py --json
    uv run python scripts/purge_parquet_layout.py --layer drought # narrow to one stream

Mutation mode is retired. ``--confirm`` is retained only as a fail-closed compatibility guard and
exits before object-store construction or listing. ``--include-unparsable`` broadens only the
read-only inventory.

WHY THIS REMAINS. The script originally supported a destructive RUNBOOK 0.35.5 cleanup. That
mutation path is retired; the classifier remains useful for read-only inventory of incomplete,
current-layout, and legacy objects without making a completion or deletion claim.

The inventory classifies current part, completion-marker, governed-absence, and legacy/unparsable
keys. It does not imply authorization to remove any of them.
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
        help="Retired mutation flag; always fails before object-store access.",
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
        help="Also include legacy keys that do not parse as the current layout in the read-only inventory.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args()
    if args.confirm:
        parser.error("--confirm mutation mode is retired; this command is permanently read-only")

    store = ObjectStore.from_settings(settings)
    wanted_layers = set(args.layer) if args.layer else None

    by_kind: Counter[str] = Counter()
    by_layer: Counter[str] = Counter()
    selected: list[str] = []
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
        selected.append(listed.key)

    report = {
        "bucket_prefix": store.prefix,
        "dry_run": True,
        "mutation_mode": "retired",
        "seen_by_kind": dict(by_kind),
        "seen_by_layer": dict(sorted(by_layer.items())),
        "selected_for_inventory": len(selected),
        "deleted": 0,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"READ-ONLY INVENTORY -- mutation mode retired. prefix={store.prefix or '<bucket root>'}")
        for kind, count in sorted(by_kind.items()):
            print(f"  {kind:>18}: {count}")
        print(f"  {'selected':>18}: {len(selected)}")
        print(f"  {'deleted':>18}: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
