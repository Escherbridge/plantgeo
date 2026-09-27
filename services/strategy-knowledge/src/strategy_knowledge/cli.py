"""`strategy-kb`: sync, append, validate, materialize, index, eval and serve (stdio or HTTP). See AGENTS.md."""

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from strategy_knowledge.config import ObjectStoreNotConfiguredError, Settings, load_settings
from strategy_knowledge.corpus import CorpusStore, InvalidSourceIdError, read_json
from strategy_knowledge.embedding import embedder_for
from strategy_knowledge.evaluation import GOLDEN_FILE, evaluate
from strategy_knowledge.fetch import FetchError
from strategy_knowledge.http_app import serve_http
from strategy_knowledge.index import IncompleteCoverageError, Indexer, IndexMismatchError, IndexMissingError
from strategy_knowledge.ingest import (
    ImportOptions,
    RawFileMissingError,
    add_source,
    bootstrap,
    import_chunked,
    read_known_strategy_ids,
    resolve_slice_id,
    slice_assignments,
    slices_without_output,
)
from strategy_knowledge.knowledge_base import KnowledgeBase
from strategy_knowledge.materialize import materialize_store
from strategy_knowledge.server import ServingRefusedError, serve
from strategy_knowledge.storage import BucketSync, conflict_policy
from strategy_knowledge.streams import reserved_stdout
from strategy_knowledge.validate import summarise, validate_corpus

logger = logging.getLogger("strategy_knowledge")

EXIT_OK: Final = 0
EXIT_PROBLEMS: Final = 1
EXIT_REFUSED: Final = 2
TRANSPORTS: Final = ("stdio", "http")
DEFAULT_HTTP_HOST: Final = "127.0.0.1"
DEFAULT_HTTP_PORT: Final = 8000


def emit_json(payload: Any) -> None:
    """Print one JSON document on the real stdout while everything else is held on stderr."""
    with reserved_stdout() as stream:
        stream.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
        stream.flush()


def command_sync(settings: Settings, arguments: argparse.Namespace) -> int:
    """Pull from or push to the bucket; non-zero when a conflict was left undecided or a remote key refused."""
    sync = BucketSync(settings, CorpusStore(settings.cache_dir))
    transfer = sync.pull if arguments.direction == "pull" else sync.push
    policy = conflict_policy(force_local=arguments.force_local, force_remote=arguments.force_remote)
    report = transfer(with_index=arguments.with_index, on_conflict=policy)
    emit_json({"direction": arguments.direction, **report.as_response()})
    if report.conflicts:
        logger.error(
            "%d key(s) changed on both sides since the last sync; nothing was transferred in this direction. "
            "Re-run with --force-local or --force-remote to choose a side",
            len(report.conflicts),
        )
    return EXIT_PROBLEMS if report.failed else EXIT_OK


def command_add_source(settings: Settings, arguments: argparse.Namespace) -> int:
    """Fetch and register one URL, then print the append runbook for it."""
    outcome = add_source(CorpusStore(settings.cache_dir), arguments.url)
    print(f"{outcome.status}: {outcome.source_id} ({outcome.line_count} lines)")
    if outcome.status == "unchanged":
        print("Nothing to do: the stored raw text is identical.")
    for step in outcome.next_steps:
        print(step)
    return EXIT_OK


def command_import_chunked(settings: Settings, arguments: argparse.Namespace) -> int:
    """Validate a DESIGN.md section 5 document and split it into corpus files."""
    chunked_file = Path(arguments.file)
    document = read_json(chunked_file)
    slice_id = resolve_slice_id(document, chunked_file)
    assignments = None
    missing_slices: list[str] = []
    if arguments.assignments:
        assignments_path = Path(arguments.assignments)
        assignments = slice_assignments(assignments_path, slice_id)
        missing_slices = slices_without_output(assignments_path, chunked_file.parent)
        if assignments is None:
            logger.error("refused: slice %s is not listed in %s", slice_id, assignments_path)
            return EXIT_REFUSED
    known_ids = read_known_strategy_ids(Path(arguments.known_ids)) if arguments.known_ids else set()
    options = ImportOptions(
        assignments=assignments,
        known_strategy_ids=frozenset(known_ids),
        accept_problems=arguments.accept_problems,
    )
    outcome = import_chunked(CorpusStore(settings.cache_dir), document, slice_id, options)
    emit_json(
        {
            "slice_id": slice_id,
            "imported": outcome.imported,
            "rejected": outcome.rejected,
            "slices_without_output": missing_slices,
        },
    )
    return EXIT_PROBLEMS if outcome.rejected else EXIT_OK


def command_bootstrap(settings: Settings, arguments: argparse.Namespace) -> int:
    """Seed the cache from the research directory; non-zero on a rejected block or a slice with no output yet."""
    summary = bootstrap(
        CorpusStore(settings.cache_dir),
        Path(arguments.research_directory),
        accept_problems=arguments.accept_problems,
    )
    emit_json(summary)
    rejected = any(slice_summary["rejected"] for slice_summary in summary["slices"].values())
    return EXIT_PROBLEMS if rejected or summary["slices_without_output"] else EXIT_OK


def command_validate(settings: Settings, arguments: argparse.Namespace) -> int:
    """Validate stored corpus files; non-zero exit on any problem."""
    known_ids = read_known_strategy_ids(Path(arguments.known_ids)) if arguments.known_ids else set()
    report = validate_corpus(CorpusStore(settings.cache_dir), arguments.source, known_ids)
    print(summarise(report))
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def command_materialize(settings: Settings, arguments: argparse.Namespace) -> int:
    """Write passage windows for inspection."""
    emit_json({"windows_per_source": materialize_store(CorpusStore(settings.cache_dir), arguments.source)})
    return EXIT_OK


def command_index(settings: Settings, arguments: argparse.Namespace) -> int:
    """Rebuild every collection (--all) or bring one source / every source up to date.

    Must not run while `serve` has the same cache open (AGENTS.md section "Indexing"). Any planned source left
    out (stale, unregistered, or the `--source` asked for) has its records removed and the run exits non-zero.
    """
    indexer = Indexer(CorpusStore(settings.cache_dir), embedder_for(settings.embedding_model))
    if arguments.all:
        report = indexer.rebuild_all(allow_partial=arguments.allow_partial)
    else:
        report = indexer.update(arguments.source, allow_partial=arguments.allow_partial)
    emit_json(report)
    reasons = report["excluded_sources"].get(arguments.source) if arguments.source else None
    if reasons is not None:
        logger.error(
            "source %s cannot be indexed (%s); its records were removed from the index. Re-chunk it "
            "(add-source printed the steps), then run `strategy-kb index --source %s` again",
            arguments.source,
            "; ".join(reasons),
            arguments.source,
        )
        return EXIT_PROBLEMS
    if report["excluded_sources"]:
        logger.error(
            "%d planned source(s) were left out and their records removed, so the index is NOT stamped full: %s. "
            "Re-chunk them (add-source printed the steps) or restore their raw files, then run `strategy-kb index`",
            len(report["excluded_sources"]),
            "; ".join(f"{source}: {', '.join(why)}" for source, why in sorted(report["excluded_sources"].items())),
        )
        return EXIT_PROBLEMS
    return EXIT_OK


def command_eval(settings: Settings, arguments: argparse.Namespace) -> int:
    """Score the golden queries against the built index."""
    golden = GOLDEN_FILE.validate_json(Path(arguments.golden).read_bytes())
    with reserved_stdout():
        knowledge_base = KnowledgeBase.open(
            CorpusStore(settings.cache_dir),
            embedder_for(settings.embedding_model),
            candidate_pool=settings.candidate_pool,
        )
        report = evaluate(knowledge_base, golden, knowledge_base.aliases)
    emit_json(report)
    return EXIT_OK


def command_status(settings: Settings, _arguments: argparse.Namespace) -> int:
    """Local corpus version, per-source freshness and what the cache holds."""
    store = CorpusStore(settings.cache_dir)
    sources = store.load_sources()
    freshness = {source_id: store.freshness(source_id, entry.sha256) for source_id, entry in sources.items()}
    stale = {source_id: state.problems() for source_id, state in freshness.items() if not state.usable}
    emit_json(
        {
            "cache_dir": str(store.root),
            "corpus_version": store.corpus_version(),
            "sources": len(sources),
            "planned_sources": len(store.plan_source_ids()),
            "stale_sources": stale,
            "registry_strategies": len(store.load_registry()),
            "index_present": store.chroma_path.is_dir(),
            "settings": repr(settings),
        },
    )
    return EXIT_OK


def command_serve(settings: Settings, arguments: argparse.Namespace) -> int:
    """Serve the tools over MCP stdio (the default) or HTTP; see AGENTS.md section "HTTP transport".

    Over stdio, `mcp.server.stdio.stdio_server()` runs the lifespan inside its own anyio task group, so a
    `ServingRefusedError` the lifespan raises (see `server.load_serving_knowledge_base`) arrives here wrapped in a
    `BaseExceptionGroup` instead of bare. Unwrap it so `main()`'s single `except (..., ServingRefusedError)` clause
    still reports it the same way; any other exception in the group is left for `except*` to re-raise untouched.
    """
    if arguments.transport == "http":
        serve_http(settings, host=arguments.host, port=arguments.port, pull_from_bucket=arguments.pull_from_bucket)
    else:
        try:
            serve(settings, pull_from_bucket=arguments.pull_from_bucket)
        except* ServingRefusedError as refusals:
            raise refusals.exceptions[0] from None
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for every subcommand."""
    parser = argparse.ArgumentParser(prog="strategy-kb", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="mirror raw/ and corpus/ with the bucket (three-way, never overwrites)")
    sync.add_argument("direction", choices=("pull", "push"))
    sync.add_argument("--with-index", action="store_true", help="also move the prebuilt index (index/LATEST)")
    side = sync.add_mutually_exclusive_group()
    side.add_argument("--force-local", action="store_true", help="settle keys changed on both sides: local wins")
    side.add_argument("--force-remote", action="store_true", help="settle keys changed on both sides: remote wins")
    sync.set_defaults(handler=command_sync)

    add = commands.add_parser("add-source", help="fetch, hash and register a new source URL")
    add.add_argument("url")
    add.set_defaults(handler=command_add_source)

    imported = commands.add_parser("import-chunked", help="validate a chunked JSON document and store its plans")
    imported.add_argument("file")
    imported.add_argument("--assignments", help="slice_assignments.json giving each source's assigned line ranges")
    imported.add_argument("--known-ids", help="strategy_ids.txt of ids to accept besides the registry's")
    imported.add_argument("--accept-problems", action="store_true", help="store blocks even when validation fails")
    imported.set_defaults(handler=command_import_chunked)

    seeded = commands.add_parser("bootstrap", help="seed the cache from the research directory")
    seeded.add_argument("research_directory")
    seeded.add_argument("--accept-problems", action="store_true")
    seeded.set_defaults(handler=command_bootstrap)

    validate = commands.add_parser("validate", help="check coverage, overlaps, enums and verbatim excerpts")
    validate.add_argument("--source")
    validate.add_argument("--known-ids", help="strategy_ids.txt of ids to accept besides the registry's")
    validate.set_defaults(handler=command_validate)

    materialize = commands.add_parser("materialize", help="write <=180-word passage windows for inspection")
    materialize.add_argument("--source")
    materialize.set_defaults(handler=command_materialize)

    index = commands.add_parser("index", help="build or incrementally update the Chroma collections")
    scope = index.add_mutually_exclusive_group()
    scope.add_argument("--source", help="update only this source (plus the registry); a partial update")
    scope.add_argument("--all", action="store_true", help="drop and rebuild every collection")
    index.add_argument(
        "--allow-partial",
        action="store_true",
        help="index sources whose chunks and skips do not yet cover their whole raw file",
    )
    index.set_defaults(handler=command_index)

    evaluation = commands.add_parser("eval", help="hit@k and MRR over golden queries")
    evaluation.add_argument("--golden", required=True)
    evaluation.set_defaults(handler=command_eval)

    commands.add_parser("status", help="corpus version and source freshness").set_defaults(handler=command_status)

    served = commands.add_parser("serve", help="serve the tools over MCP stdio (default) or HTTP (API + /mcp)")
    served.add_argument("--transport", choices=TRANSPORTS, default="stdio")
    served.add_argument("--host", default=DEFAULT_HTTP_HOST, help="HTTP bind address (ignored for stdio)")
    served.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port (ignored for stdio)")
    served.add_argument(
        "--pull-from-bucket",
        action="store_true",
        help="run `sync pull --with-index` first; refuse to serve unless the pulled index is full and fresh",
    )
    served.set_defaults(handler=command_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the `strategy-kb` console script."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    arguments = build_parser().parse_args(argv)
    handler: Callable[[Settings, argparse.Namespace], int] = arguments.handler
    try:
        return handler(load_settings(), arguments)
    except (
        IndexMismatchError,
        IndexMissingError,
        IncompleteCoverageError,
        ObjectStoreNotConfiguredError,
        RawFileMissingError,
        InvalidSourceIdError,
        ServingRefusedError,
    ) as error:
        logger.error("refused: %s", error)
        return EXIT_REFUSED
    except FetchError as error:
        logger.error("fetch failed: %s", error)
        return EXIT_PROBLEMS
