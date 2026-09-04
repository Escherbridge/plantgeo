"""Build D1's three-part drop packet for one environmental relation. DRY RUN ONLY.

    UV_NO_SYNC=1 uv run --no-sync python scripts/build_drop_packet.py --relation geo.mv_soil_survey_grid
    UV_NO_SYNC=1 uv run --no-sync python scripts/build_drop_packet.py --list

Decision D1 of `conductor/tracks/environmental_postgres_retirement_20260904/spec.md` lets a relation
be dropped only once three things hold and are RECORDED: a counted parity receipt, a repository-wide
zero-reader proof, and a `pg_dump` archived to R2 with its key and sha256. Before this script every
packet would have been assembled by hand, which is how a drop gets applied on a proof nobody can
re-run. This produces the packet, judges it, and exits on the judgement.

WHAT IT WILL NOT DO, BY CONSTRUCTION. There is no `--apply`. It opens no database connection, lists
no bucket, runs no `pg_dump`, writes no object and applies no migration. It emits the archive COMMAND
and leaves the sha256 slot explicitly EMPTY and marked owed, because a digest that was not computed
from the object it describes is the one thing this track's tripwires forbid outright. The parity
receipt is likewise READ from a file the operator captured by running the layer's own parity command;
this script never recomputes a comparison, because `pipeline/direct/{vegetation,weather_observations,
drought}/parity.py` already encode three layer-specific ones and a generalised fourth would be worse
than all of them.

EXIT CODE IS THE GATE, matching the parity modules it cites: 0 when the packet is `ready`, 1 when it
is `blocked`, 2 when it could not be built at all (an unledgered relation, a refused drop form, an
unreadable receipt). A CI step or an `&&` chain reads the code, not the prose.

Production was unreachable when this was written (`plantgeo-job-executor` pointed at a database with
no `agri` or `geo` schema), so every path here was proven against fakes. That is not a limitation of
the design: a packet builder that needed production to say "blocked" would be useless in exactly the
situation where a blocked answer matters most.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.retirement.archive import DEFAULT_DSN_ENVIRONMENT_VARIABLE, ArchiveError  # noqa: E402
from agri_data_service.retirement.ledger import (  # noqa: E402
    DropForm,
    DropFormRefusedError,
    InventoryClass,
    Ledger,
    LedgerError,
    load_ledger,
)
from agri_data_service.retirement.packet import (  # noqa: E402
    DropPacket,
    DropPacketRequest,
    PacketError,
    build_packet,
)
from agri_data_service.retirement.parity import (  # noqa: E402
    ParityReceiptError,
    RecordedLaneWriteProbe,
    parse_epoch_timestamp,
)
from agri_data_service.retirement.readers import ReaderScanError, find_repository_root  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Exit codes. `blocked` is not an error -- it is the tool working -- but it must not read as success.
EXIT_READY = 0
EXIT_BLOCKED = 1
EXIT_REFUSED = 2

#: Flags this tool will never grow. Named so the refusal teaches rather than reading as a typo.
_FORBIDDEN_FLAGS = {
    "--apply": (
        "there is no --apply. This script produces evidence; the migration is a separate, "
        "owner-confirmed action rehearsed on agri_sweep first"
    ),
    "--pg-dump": "this script never runs pg_dump; it prints the command for an operator to run",
}


def _split_pair(value: str, *, flag: str) -> tuple[str, str]:
    """Split a `key=value` argument, refusing a shape that would silently drop half the pair."""
    key, separator, remainder = value.partition("=")
    if not separator or not key.strip() or not remainder.strip():
        raise argparse.ArgumentTypeError(f"{flag} expects key=value, got {value!r}")
    return key.strip(), remainder.strip()


def _receipt_pair(value: str) -> tuple[str, str]:
    """Parse `--layer-parity-receipt <layer>=<path>`."""
    return _split_pair(value, flag="--layer-parity-receipt")


def _proof_pair(value: str) -> tuple[str, str]:
    """Parse `--layer-reader-proof <layer>=<citation>`."""
    return _split_pair(value, flag="--layer-reader-proof")


def _written_at_pair(value: str) -> tuple[str, str]:
    """Parse `--twin-newest-completion-at <lane>=<iso8601>`."""
    return _split_pair(value, flag="--twin-newest-completion-at")


def parser() -> argparse.ArgumentParser:
    """Build the read-only drop-packet operator."""
    built = argparse.ArgumentParser(
        prog="build_drop_packet.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    built.add_argument("--relation", help="the schema-qualified relation, e.g. geo.mv_signal_observation_day")
    built.add_argument(
        "--list",
        action="store_true",
        help="print every relation the A3 inventory ledgers, grouped by class, and exit",
    )
    built.add_argument(
        "--form",
        choices=sorted(str(form) for form in DropForm),
        help="the drop form; defaults to the only form the relation permits, and refuses one it does not",
    )
    built.add_argument(
        "--layer",
        action="append",
        default=[],
        help="for a row-delete: one layer whose rows leave. Repeatable; default is every environmental layer",
    )
    built.add_argument(
        "--parity-receipt",
        type=Path,
        help="JSON printed by the relation's own parity command, for a whole-object drop",
    )
    built.add_argument(
        "--layer-parity-receipt",
        action="append",
        default=[],
        type=_receipt_pair,
        metavar="LAYER=PATH",
        help="for a row-delete: one layer's captured parity receipt. Repeatable",
    )
    built.add_argument(
        "--twin-newest-completion-at",
        action="append",
        default=[],
        type=_written_at_pair,
        metavar="LANE=ISO8601",
        help=(
            "when a lane's Parquet twin was last written, as an ISO-8601 instant WITH a timezone. Only "
            "this proves that a lane carrying a rewrite epoch has been rewritten; without it the packet "
            "reports twin_rewrite_unproven rather than guessing"
        ),
    )
    built.add_argument(
        "--archive-sha256",
        help="the sha256 of an archive the operator ALREADY took and uploaded. Omit and the packet says owed",
    )
    built.add_argument("--archive-key", help="override the computed R2 key, when an archive already landed elsewhere")
    built.add_argument("--bucket", help="the R2 bucket name; omitted, the packet prints ${OBJECT_STORE_BUCKET}")
    built.add_argument(
        "--dsn-var",
        default=DEFAULT_DSN_ENVIRONMENT_VARIABLE,
        help="the environment variable the printed archive commands read the DSN from; never expanded here",
    )
    built.add_argument(
        "--co-migration",
        action="append",
        default=[],
        help="repository-relative migration file carrying a dependent object's same-migration redefinition",
    )
    built.add_argument(
        "--layer-reader-proof",
        action="append",
        default=[],
        type=_proof_pair,
        metavar="LAYER=CITATION",
        help="for a row-delete: the citation proving one layer's PostgreSQL read path is gone",
    )
    built.add_argument("--as-of", help="the packet date, YYYY-MM-DD. Defaults to today in UTC")
    built.add_argument("--json", action="store_true", help="print the machine-readable body instead of the markdown")
    built.add_argument("--output", type=Path, help="write the packet to this path instead of stdout")
    built.add_argument(
        "--sample-limit", type=int, default=12, help="how many hits each rendered section quotes (default 12)"
    )
    built.add_argument(
        "--repository-root",
        type=Path,
        help="the checkout to scan; defaults to the one holding this script",
    )
    return built


def _load_receipt(path: Path) -> dict[str, object]:
    """Read one captured parity receipt, refusing anything that is not a JSON object."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ParityReceiptError(f"cannot read parity receipt {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ParityReceiptError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ParityReceiptError(f"{path} is not a JSON object; the parity modules print one object per run")
    return parsed


def _build_request(arguments: argparse.Namespace, repository_root: Path) -> DropPacketRequest:
    """Turn parsed arguments into the pure request object the builder consumes."""
    receipts: dict[str, dict[str, object]] = {}
    sources: dict[str, str] = {}
    if arguments.parity_receipt is not None:
        receipts[arguments.relation] = _load_receipt(arguments.parity_receipt)
        sources[arguments.relation] = str(arguments.parity_receipt)
    for layer, raw_path in arguments.layer_parity_receipt:
        receipts[layer] = _load_receipt(Path(raw_path))
        sources[layer] = raw_path
    probe = RecordedLaneWriteProbe(
        recorded={lane: parse_epoch_timestamp(value) for lane, value in arguments.twin_newest_completion_at}
    )
    as_of = date.fromisoformat(arguments.as_of) if arguments.as_of else datetime.now(tz=UTC).date()
    return DropPacketRequest(
        relation=arguments.relation,
        repository_root=repository_root,
        as_of=as_of,
        form=DropForm(arguments.form) if arguments.form else None,
        layers=tuple(arguments.layer),
        parity_receipts=receipts,
        parity_receipt_sources=sources,
        probe=probe,
        archive_sha256=arguments.archive_sha256,
        archive_object_key=arguments.archive_key,
        archive_bucket=arguments.bucket,
        dsn_variable=arguments.dsn_var,
        co_migration_paths=tuple(arguments.co_migration),
        layer_reader_proofs=dict(arguments.layer_reader_proof),
        sample_limit=arguments.sample_limit,
    )


def _render(packet: DropPacket, *, as_json: bool) -> str:
    """Render the packet in the requested form."""
    if as_json:
        return json.dumps(packet.to_json_dict(), indent=2, sort_keys=True)
    return packet.to_markdown()


def _print_ledger(ledger: Ledger) -> int:
    """Print every ledgered relation grouped by class; this is the index a packet build starts from."""
    payload = {
        str(inventory_class): list(ledger.relations_in_class(inventory_class))
        for inventory_class in InventoryClass
        if ledger.relations_in_class(inventory_class)
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return EXIT_READY


def main(argv: Sequence[str] | None = None) -> int:
    """Build one packet and exit on its verdict. Fires no production action on any path."""
    raw = list(sys.argv[1:] if argv is None else argv)
    for flag, refusal in _FORBIDDEN_FLAGS.items():
        if any(token == flag or token.startswith(f"{flag}=") for token in raw):
            print(f"refused: {refusal}", file=sys.stderr)
            return EXIT_REFUSED
    arguments = parser().parse_args(raw)
    try:
        repository_root = arguments.repository_root or find_repository_root()
        ledger = load_ledger(repository_root)
        if arguments.list:
            return _print_ledger(ledger)
        if not arguments.relation:
            print("refused: --relation is required unless --list is given", file=sys.stderr)
            return EXIT_REFUSED
        packet = build_packet(_build_request(arguments, repository_root), ledger)
    except DropFormRefusedError as error:
        print(f"refused, and no packet was emitted: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (ArchiveError, LedgerError, PacketError, ParityReceiptError, ReaderScanError, ValueError) as error:
        print(f"refused: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_REFUSED
    rendered = _render(packet, as_json=arguments.json)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"{packet.verdict}: wrote {arguments.output}", file=sys.stderr)
    else:
        print(rendered)
    return EXIT_READY if packet.verdict == "ready" else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
