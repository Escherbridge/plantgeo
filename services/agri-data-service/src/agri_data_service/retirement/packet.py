"""Assemble D1's three-part drop packet and judge it: `ready` or `blocked`, with every reason named.

A packet is the artifact `evidence/drop-packets/<relation>.md` holds: a counted parity receipt, a
repository-wide zero-reader proof, and an archive record whose sha256 is owed until a dump actually
runs. Nothing here connects to anything. The build is a pure function of the checkout, the ledger, the
receipts an operator captured and the values they recorded, which is why it can be produced while
production is unreachable and why its result is reproducible by the next reader.

FOUR RECORDED FACTS ARE ENFORCED BY THE CODE BELOW, NOT BY A DOCSTRING:

1. `geo.features` never takes a table drop. `ledger.DropCandidate.require_form` raises
   `DropFormRefusedError` and NO packet is emitted -- a blocked packet would imply the condition could
   clear, and `interventions` living in PostgreSQL permanently is not a condition.
2. A relation with live readers is refused whatever the ledger says. `_reader_blockers` blocks on any
   unexempted consumer hit, and when the inventory classed the relation "drop now" it adds a second,
   louder `INVENTORY_CONTRADICTED` naming the ledger row that was wrong. `agri.spatial_cell` also
   carries a recorded `SurvivalDependency`, so the refusal survives a future narrowing of the scan.
3. An unrewritten Parquet twin is never called under-coverage. `parity.assess_shortfall` checks the
   lane's rewrite epoch before it reads any verdict, so `fire-perimeters` reports
   `twin_not_rewritten`/`twin_rewrite_unproven` -- both blocking, neither a shortfall claim.
4. Dropping `geo.mv_signal_observation_day` owes a redefinition of `geo.v_observation_day_census` in
   the same migration. `_dependent_blockers` demands a named co-migration that mentions the dependent
   object, and refuses one containing `CASCADE`. The generated statement never contains `CASCADE` at
   all; `_drop_statement` raises if it ever does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from agri_data_service.retirement.archive import DEFAULT_DSN_ENVIRONMENT_VARIABLE, ArchiveRecord, build_archive_record
from agri_data_service.retirement.ledger import (
    INVENTORY_RELATIVE_PATH,
    DropCandidate,
    DropForm,
    InventoryClass,
    Ledger,
    LedgerError,
)
from agri_data_service.retirement.parity import (
    UNPROVEN_LANE_WRITE_PROBE,
    LaneWriteProbe,
    ParityAvailability,
    ParitySection,
    ShortfallClass,
    build_parity_section,
)
from agri_data_service.retirement.readers import ReaderScan, SearchTerm, scan_for_readers

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date
    from pathlib import Path

TRACK_SLUG: Final = "environmental_postgres_retirement_20260904"

#: `CASCADE` in any spelling, as a whole word, so `cascaded` in prose does not trip the guard.
_CASCADE_PATTERN: Final = re.compile(r"\bcascade\b", re.IGNORECASE)


class PacketError(RuntimeError):
    """Raised when a packet cannot be assembled at all."""


class BlockerCode(StrEnum):
    """Every reason a packet is `blocked`. One code per distinguishable condition, never a bucket."""

    INVENTORY_CLASS_KEEP = "inventory_class_keep"
    INVENTORY_CLASS_UNRESOLVED = "inventory_class_unresolved"
    RELATION_ALREADY_DROPPED = "relation_already_dropped"
    SURVIVAL_DEPENDENCY = "survival_dependency"
    LIVE_READERS = "live_readers"
    INVENTORY_CONTRADICTED = "inventory_contradicted"
    STALE_READER_EXEMPTION = "stale_reader_exemption"
    PARITY_UNAVAILABLE = "parity_unavailable"
    PARITY_UNDER_COVERAGE = "parity_under_coverage"
    PARITY_BASELINE_EMPTY = "parity_baseline_empty"
    PARITY_ROWS_NOT_COMPARED = "parity_rows_not_compared"
    PARQUET_TWIN_NOT_REWRITTEN = "parquet_twin_not_rewritten"
    PARQUET_TWIN_REWRITE_UNPROVEN = "parquet_twin_rewrite_unproven"
    LAYER_READER_PROOF_OWED = "layer_reader_proof_owed"
    DEPENDENT_REDEFINITION_OWED = "dependent_redefinition_owed"
    CO_MIGRATION_MISSING = "co_migration_missing"
    CASCADE_FORBIDDEN = "cascade_forbidden"
    ARCHIVE_SNAPSHOT_OWED = "archive_snapshot_owed"


@dataclass(frozen=True, slots=True)
class Blocker:
    """One named reason the packet is not `ready`."""

    code: BlockerCode
    detail: str

    def to_json_dict(self) -> dict[str, object]:
        """Render one blocker."""
        return {"code": str(self.code), "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DropPacketRequest:
    """Everything a build consumes. No field of it opens a connection."""

    relation: str
    repository_root: Path
    as_of: date
    form: DropForm | None = None
    #: For a row-delete, the layers whose rows leave. Empty means every environmental layer the
    #: relation holds.
    layers: tuple[str, ...] = ()
    #: Scope -> the JSON a layer's own parity command printed. The scope is the layer name for a
    #: row-delete and the relation for every other form.
    parity_receipts: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    parity_receipt_sources: Mapping[str, str] = field(default_factory=dict)
    probe: LaneWriteProbe = UNPROVEN_LANE_WRITE_PROBE
    archive_sha256: str | None = None
    archive_object_key: str | None = None
    archive_bucket: str | None = None
    dsn_variable: str = DEFAULT_DSN_ENVIRONMENT_VARIABLE
    #: Repository-relative migration files that carry the dependent-object work this drop owes.
    co_migration_paths: tuple[str, ...] = ()
    #: Layer -> the citation an operator offers that the layer's PostgreSQL read path is gone. A grep
    #: cannot establish this for a shared table; see `_layer_reader_blockers`.
    layer_reader_proofs: Mapping[str, str] = field(default_factory=dict)
    sample_limit: int = 12


@dataclass(frozen=True, slots=True)
class DropPacket:
    """The assembled three-part proof and its verdict."""

    relation: str
    track: str
    form: DropForm
    candidate: DropCandidate
    layers: tuple[str, ...]
    relation_scan: ReaderScan
    layer_scans: tuple[tuple[str, ReaderScan], ...]
    parity_sections: tuple[ParitySection, ...]
    archive: ArchiveRecord
    drop_statement: str
    migration_preconditions: tuple[str, ...]
    blockers: tuple[Blocker, ...]
    as_of: date
    sample_limit: int

    @property
    def verdict(self) -> str:
        """`ready` only when nothing at all blocks; otherwise `blocked`."""
        return "blocked" if self.blockers else "ready"

    def to_json_dict(self) -> dict[str, object]:
        """Render the whole packet as the JSON body `--json` prints and a test asserts against."""
        return {
            "track": self.track,
            "relation": self.relation,
            "generated_for": self.as_of.isoformat(),
            "drop_form": str(self.form),
            "object_kind": str(self.candidate.object_kind),
            "object_kind_basis": self.candidate.object_kind_basis,
            "inventory": {
                "class": str(self.candidate.inventory.inventory_class),
                "classification_text": self.candidate.inventory.classification_text,
                "gating_layer": self.candidate.inventory.gating_layer,
                "citation": f"{_inventory_citation()}:{self.candidate.inventory.source_line}",
            },
            "layers": list(self.layers),
            "parity": [section.to_json_dict() for section in self.parity_sections],
            "zero_reader_proof": {
                "relation_scan": self.relation_scan.to_json_dict(sample_limit=self.sample_limit),
                "layer_scans": [
                    {"layer": layer, **scan.to_json_dict(sample_limit=self.sample_limit)}
                    for layer, scan in self.layer_scans
                ],
                "limitation": _SCAN_LIMITATION if self.form is DropForm.ROW_DELETE else None,
            },
            "archive": self.archive.to_json_dict(),
            "migration": {
                "statement": self.drop_statement,
                "preconditions": list(self.migration_preconditions),
                "rehearsal": "rehearse on the disposable agri_sweep database before production (D1)",
            },
            "notes": list(self.candidate.notes),
            "verdict": self.verdict,
            "blockers": [blocker.to_json_dict() for blocker in self.blockers],
        }

    def to_markdown(self) -> str:
        """Render the packet as the evidence file `evidence/drop-packets/<relation>.md` carries."""
        return _render_markdown(self)


def _inventory_citation() -> str:
    """Return the inventory's repository-relative path, so every citation spells it the same way."""
    return INVENTORY_RELATIVE_PATH


#: What a name-level grep can and cannot settle when the relation itself survives the drop.
_SCAN_LIMITATION: Final = (
    "This drop deletes ROWS from a relation that survives, so a reference to the relation is not by "
    "itself a blocker -- the question is whether anything still reads THESE LAYERS' rows from "
    "PostgreSQL. The per-layer scan below searches the SQL string-literal spelling ('<layer>'), which "
    "finds tile-function predicates and read-model filters but cannot distinguish them from the Parquet "
    "lane slug, which is the identical string. That residue is why every layer additionally owes a "
    "named reader proof rather than being cleared by a grep."
)


def _drop_statement(candidate: DropCandidate, form: DropForm, row_filter: str | None) -> str:
    """Render the migration statement this packet proposes. NEVER `CASCADE`.

    `DROP ... CASCADE` on an environmental object is how a proven drop becomes an unproven outage: it
    silently removes whatever depended on it, including views the app still reads. Dependents are
    handled explicitly in the same migration or the packet stays blocked.
    """
    if form is DropForm.ROW_DELETE:
        if not row_filter:
            raise PacketError(f"{candidate.relation} is a row-delete with no recorded predicate")
        statement = f"DELETE FROM {candidate.relation} WHERE {row_filter};"
    elif form is DropForm.MATERIALIZED_VIEW_DROP:
        statement = f"DROP MATERIALIZED VIEW IF EXISTS {candidate.relation};"
    elif form is DropForm.VIEW_DROP:
        statement = f"DROP VIEW IF EXISTS {candidate.relation};"
    else:
        statement = f"DROP TABLE IF EXISTS {candidate.relation};"
    if _CASCADE_PATTERN.search(statement):
        raise PacketError(f"generated statement contains CASCADE, which this tool may never emit: {statement}")
    return statement


def _layer_search_terms(layer_name: str) -> tuple[SearchTerm, ...]:
    """Build the SQL string-literal spelling that discriminates a PostgreSQL read of one layer.

    The single-quoted form is searched rather than the bare slug because the slug is ALSO the Parquet
    lane name: `"fire-perimeters"` appears in every module that publishes the lane, while
    `'fire-perimeters'` is how a tile-function predicate or a read-model filter names the PostgreSQL
    layer. The discrimination is real but partial, which `_SCAN_LIMITATION` states rather than hides.
    """
    return (
        SearchTerm(
            pattern=f"'{layer_name}'",
            why="SQL string literal: how a tile function predicate or a read-model filter names a layer",
        ),
    )


def _inventory_blockers(candidate: DropCandidate) -> list[Blocker]:
    """Refuse a ledger row that does not authorise a drop at all."""
    inventory_class = candidate.inventory.inventory_class
    if inventory_class is InventoryClass.KEEP:
        return [
            Blocker(
                code=BlockerCode.INVENTORY_CLASS_KEEP,
                detail=(
                    f"the A3 inventory classes {candidate.relation} 'keep' "
                    f"({candidate.inventory.classification_text}). "
                    "A keep row is out of this track's scope; reclassify it in the inventory first"
                ),
            )
        ]
    if inventory_class is InventoryClass.UNRESOLVED:
        return [
            Blocker(
                code=BlockerCode.INVENTORY_CLASS_UNRESOLVED,
                detail=(
                    f"the inventory's classification cell for {candidate.relation} reads "
                    f"{candidate.inventory.classification_text!r}, which this tool cannot map onto a class"
                ),
            )
        ]
    if inventory_class is InventoryClass.ALREADY_DROPPED:
        return [
            Blocker(
                code=BlockerCode.RELATION_ALREADY_DROPPED,
                detail=(
                    f"{candidate.relation} is already gone; no migration is owed. This packet exists so the "
                    "drop index has one place naming it, not as a pending action"
                ),
            )
        ]
    return []


def _reader_blockers(candidate: DropCandidate, scan: ReaderScan, *, form: DropForm) -> list[Blocker]:
    """Turn the scan into refusals. THE SCAN OUTRANKS THE LEDGER; a 'drop now' row cannot waive it."""
    blockers: list[Blocker] = []
    if candidate.survival is not None:
        recorded = "; ".join(f"{reader.citation} ({reader.why})" for reader in candidate.survival.readers)
        blockers.append(
            Blocker(
                code=BlockerCode.SURVIVAL_DEPENDENCY,
                detail=f"{candidate.survival.correction}. Recorded readers: {recorded}",
            )
        )
    relation_survives = form is DropForm.ROW_DELETE
    if scan.consumers and not relation_survives:
        paths = ", ".join(scan.consumer_paths()[:10])
        blockers.append(
            Blocker(
                code=BlockerCode.LIVE_READERS,
                detail=(
                    f"{len(scan.consumers)} unexempted reference(s) across {len(scan.consumer_paths())} file(s) "
                    f"remain: {paths}"
                ),
            )
        )
        if candidate.inventory.inventory_class is InventoryClass.DROP_NOW:
            blockers.append(
                Blocker(
                    code=BlockerCode.INVENTORY_CONTRADICTED,
                    detail=(
                        f"the A3 inventory classes {candidate.relation} 'drop now' -- zero readers, no live "
                        f"filler -- and the repository holds {len(scan.consumer_paths())} file(s) that reference "
                        f"it ({_inventory_citation()}:{candidate.inventory.source_line}). The inventory row is "
                        "wrong; correct it before this packet is rebuilt"
                    ),
                )
            )
    if scan.unused_exemptions:
        stale = ", ".join(exemption.path for exemption in scan.unused_exemptions)
        blockers.append(
            Blocker(
                code=BlockerCode.STALE_READER_EXEMPTION,
                detail=(
                    f"exemption(s) matched nothing and are therefore claims about the tree that are no longer "
                    f"true: {stale}. Delete them rather than carrying them"
                ),
            )
        )
    return blockers


def _parity_blockers(section: ParitySection) -> list[Blocker]:
    """Map one parity section onto refusals, keeping the epoch classes distinct from under-coverage."""
    blockers: list[Blocker] = []
    classification = section.assessment.classification
    if classification is ShortfallClass.TWIN_NOT_REWRITTEN:
        return [Blocker(code=BlockerCode.PARQUET_TWIN_NOT_REWRITTEN, detail=section.assessment.detail)]
    if classification is ShortfallClass.TWIN_REWRITE_UNPROVEN:
        return [Blocker(code=BlockerCode.PARQUET_TWIN_REWRITE_UNPROVEN, detail=section.assessment.detail)]
    if classification is ShortfallClass.UNMEASURED or section.availability is ParityAvailability.UNAVAILABLE:
        return [
            Blocker(
                code=BlockerCode.PARITY_UNAVAILABLE,
                detail=f"{section.scope}: {section.unavailable_reason or section.assessment.detail}",
            )
        ]
    if classification is ShortfallClass.GENUINE_UNDER_COVERAGE:
        blockers.append(
            Blocker(
                code=BlockerCode.PARITY_UNDER_COVERAGE,
                detail=f"{section.scope}: {section.assessment.detail}",
            )
        )
    normalized = section.normalized
    if normalized is not None and normalized.baseline_empty:
        blockers.append(
            Blocker(
                code=BlockerCode.PARITY_BASELINE_EMPTY,
                detail=(
                    f"{section.scope}: the comparison read zero PostgreSQL days, so 'covered' is unearned. A "
                    "mistargeted DSN produces exactly this receipt"
                ),
            )
        )
    if normalized is not None and not normalized.rows_compared:
        blockers.append(
            Blocker(
                code=BlockerCode.PARITY_ROWS_NOT_COMPARED,
                detail=(
                    f"{section.scope}: the receipt compared day membership only. D1 asks for every day AND row; "
                    "re-run the layer's parity command with its row comparison enabled"
                ),
            )
        )
    return blockers


def _layer_reader_blockers(*, layers: Sequence[str], proofs: Mapping[str, str]) -> list[Blocker]:
    """Demand a named reader proof per layer, because a grep cannot settle a shared table's rows."""
    return [
        Blocker(
            code=BlockerCode.LAYER_READER_PROOF_OWED,
            detail=(
                f"layer {layer!r}: no citation was supplied proving its PostgreSQL read path is gone. The "
                "relation survives this drop, so the per-layer scan below is evidence and not a proof -- name "
                "the wave-C evidence with --layer-reader-proof"
            ),
        )
        for layer in layers
        if not proofs.get(layer)
    ]


def _dependent_blockers(candidate: DropCandidate, request: DropPacketRequest) -> tuple[list[Blocker], list[str]]:
    """Require the same-migration handling of every dependent object, and forbid `CASCADE`."""
    blockers: list[Blocker] = []
    preconditions: list[str] = []
    if not candidate.dependent_objects:
        return blockers, preconditions
    texts: dict[str, str] = {}
    for relative in request.co_migration_paths:
        path = request.repository_root / relative
        if not path.is_file():
            blockers.append(
                Blocker(
                    code=BlockerCode.CO_MIGRATION_MISSING,
                    detail=f"named co-migration {relative} does not exist in this checkout",
                )
            )
            continue
        texts[relative] = path.read_text(encoding="utf-8", errors="replace")
    for dependent in candidate.dependent_objects:
        preconditions.append(f"{dependent.required_action}: {dependent.name} -- {dependent.why}")
        carriers = [relative for relative, text in texts.items() if dependent.name in text]
        if not carriers:
            blockers.append(
                Blocker(
                    code=BlockerCode.DEPENDENT_REDEFINITION_OWED,
                    detail=(
                        f"{dependent.name} must be {dependent.required_action} in the SAME migration as this "
                        f"drop ({dependent.why}). No co-migration naming it was supplied; pass one with "
                        "--co-migration"
                    ),
                )
            )
            continue
        if dependent.forbid_cascade:
            offenders = [relative for relative in carriers if _CASCADE_PATTERN.search(texts[relative])]
            if offenders:
                blockers.append(
                    Blocker(
                        code=BlockerCode.CASCADE_FORBIDDEN,
                        detail=(
                            f"co-migration {', '.join(offenders)} contains CASCADE while handling "
                            f"{dependent.name}. A CASCADE here removes the dependent object instead of "
                            "redefining it, which is the outage this precondition exists to prevent"
                        ),
                    )
                )
    return blockers, preconditions


def build_packet(request: DropPacketRequest, ledger: Ledger) -> DropPacket:
    """Assemble and judge one packet. Pure over the checkout and the operator's recorded values."""
    candidate = ledger.candidate(request.relation)
    form = candidate.require_form(request.form) if request.form is not None else candidate.default_form()
    layers = _resolve_layers(candidate, request, form=form)
    relation_scan = scan_for_readers(
        relation=candidate.relation,
        terms=candidate.search_terms,
        drop_form=str(form),
        exemptions=candidate.reader_exemptions,
        repository_root=request.repository_root,
    )
    layer_scans = tuple(
        (
            layer,
            scan_for_readers(
                relation=f"{candidate.relation} [{layer}]",
                terms=_layer_search_terms(layer),
                drop_form=str(form),
                repository_root=request.repository_root,
            ),
        )
        for layer in layers
    )
    parity_sections = _build_parity_sections(candidate, request, form=form, layers=layers)
    # The archive and the DELETE share ONE predicate, scoped to the layers this packet covers, so an
    # artifact can never describe a narrower set of rows than the statement removes.
    row_filter = candidate.row_filter(layers) if form is DropForm.ROW_DELETE else None
    archive = build_archive_record(
        relation=candidate.relation,
        object_kind=candidate.object_kind,
        drop_form=form,
        as_of=request.as_of,
        row_filter_sql=row_filter,
        bucket=request.archive_bucket,
        sha256=request.archive_sha256,
        object_key=request.archive_object_key,
        dsn_variable=request.dsn_variable,
    )
    dependent_blockers, preconditions = _dependent_blockers(candidate, request)
    blockers: list[Blocker] = [
        *_inventory_blockers(candidate),
        *_reader_blockers(candidate, relation_scan, form=form),
        *(blocker for scan in layer_scans for blocker in _layer_scan_blockers(candidate, scan)),
        *(blocker for section in parity_sections for blocker in _parity_blockers(section)),
        *_layer_reader_blockers(layers=layers, proofs=request.layer_reader_proofs),
        *dependent_blockers,
    ]
    if not archive.satisfied:
        blockers.append(
            Blocker(
                code=BlockerCode.ARCHIVE_SNAPSHOT_OWED,
                detail=(
                    "no archived snapshot exists yet: this tool never runs pg_dump. Run the commands in the "
                    "archive section, upload the artifacts, and rebuild the packet with --archive-sha256. A "
                    "missing archived snapshot is a hard stop in the track's tripwires"
                ),
            )
        )
    return DropPacket(
        relation=candidate.relation,
        track=TRACK_SLUG,
        form=form,
        candidate=candidate,
        layers=layers,
        relation_scan=relation_scan,
        layer_scans=layer_scans,
        parity_sections=parity_sections,
        archive=archive,
        drop_statement=_drop_statement(candidate, form, row_filter),
        migration_preconditions=tuple(preconditions),
        blockers=tuple(blockers),
        as_of=request.as_of,
        sample_limit=request.sample_limit,
    )


def _layer_scan_blockers(candidate: DropCandidate, entry: tuple[str, ReaderScan]) -> list[Blocker]:
    """Report a layer whose SQL-literal spelling still appears in live code."""
    layer, scan = entry
    if not scan.consumers:
        return []
    return [
        Blocker(
            code=BlockerCode.LIVE_READERS,
            detail=(
                f"layer {layer!r} of {candidate.relation}: {len(scan.consumers)} reference(s) to the SQL literal "
                f"'{layer}' across {len(scan.consumer_paths())} file(s): {', '.join(scan.consumer_paths()[:8])}"
            ),
        )
    ]


def _resolve_layers(candidate: DropCandidate, request: DropPacketRequest, *, form: DropForm) -> tuple[str, ...]:
    """Return the layers this packet covers, refusing a layer the relation does not hold."""
    if form is not DropForm.ROW_DELETE:
        if request.layers:
            raise PacketError(f"--layer is meaningless for a {form}; it names rows inside a surviving relation")
        return ()
    if not request.layers:
        return tuple(scope.layer_name for scope in candidate.layer_scopes)
    for layer in request.layers:
        candidate.layer_scope(layer)
    return request.layers


def _build_parity_sections(
    candidate: DropCandidate, request: DropPacketRequest, *, form: DropForm, layers: Sequence[str]
) -> tuple[ParitySection, ...]:
    """Build one parity section per layer for a row-delete, or one for the relation otherwise."""
    if form is DropForm.ROW_DELETE:
        sections: list[ParitySection] = []
        for layer in layers:
            scope = candidate.layer_scope(layer)
            sections.append(
                build_parity_section(
                    scope=layer,
                    binding=scope.parity,
                    receipt_payload=request.parity_receipts.get(layer),
                    receipt_source=request.parity_receipt_sources.get(layer),
                    epoch=scope.rewrite_epoch,
                    probe=request.probe,
                )
            )
        return tuple(sections)
    return (
        build_parity_section(
            scope=candidate.relation,
            binding=candidate.parity,
            receipt_payload=request.parity_receipts.get(candidate.relation),
            receipt_source=request.parity_receipt_sources.get(candidate.relation),
            epoch=candidate.rewrite_epoch,
            probe=request.probe,
        ),
    )


def _bullet(items: Sequence[str]) -> str:
    """Render a markdown bullet list, or an explicit `none` so an empty section is never ambiguous."""
    return "\n".join(f"- {item}" for item in items) if items else "- none"


def _render_markdown(packet: DropPacket) -> str:
    """Render the evidence file. Verdict first: a reader must not have to scroll to learn the answer."""
    body = packet.to_json_dict()
    lines = [
        "---",
        "type: evidence",
        f"slug: {packet.track}",
        f"relation: {packet.relation}",
        f"date: {packet.as_of.isoformat()}",
        "---",
        "",
        f"# Drop packet -- `{packet.relation}` ({packet.form})",
        "",
        f"**Verdict: {packet.verdict.upper()}**",
        "",
        _bullet([f"`{blocker.code}` -- {blocker.detail}" for blocker in packet.blockers]),
        "",
        "Generated by `scripts/build_drop_packet.py`. Dry run: no database connection, no bucket write, "
        "no `pg_dump`, no migration. Every number below came from a file in this checkout or from a "
        "receipt an operator captured and passed in.",
        "",
        "## Ledger row",
        "",
        f"- class: `{packet.candidate.inventory.inventory_class}` ({packet.candidate.inventory.classification_text})",
        f"- gating layer: {packet.candidate.inventory.gating_layer or 'n/a'}",
        f"- object kind: `{packet.candidate.object_kind}` -- {packet.candidate.object_kind_basis}",
        f"- citation: `{_inventory_citation()}:{packet.candidate.inventory.source_line}`",
        "",
        "## 1. Parity receipt (D1 item 1)",
        "",
    ]
    for section in packet.parity_sections:
        lines.extend(_render_parity_section(section))
    lines.extend(
        [
            "## 2. Zero-reader proof (D1 item 2, c2 removal-packet form)",
            "",
            f"Scanned {packet.relation_scan.files_scanned} files across "
            f"{len(packet.relation_scan.surfaces_scanned)} surfaces: "
            f"{', '.join(packet.relation_scan.surfaces_scanned)}.",
            "",
            f"- consumers: {len(packet.relation_scan.consumers)} "
            f"in {len(packet.relation_scan.consumer_paths())} file(s)",
            f"- exempted: {len(packet.relation_scan.exempt)}",
            f"- schema definitions: {len(packet.relation_scan.schema_definitions)}",
            f"- documentation: {len(packet.relation_scan.documentation)}",
            "",
        ]
    )
    if packet.form is DropForm.ROW_DELETE:
        lines.extend([_SCAN_LIMITATION, ""])
    lines.extend(
        [
            "### Consumer hits",
            "",
            _bullet(
                [
                    f"`{hit.path}:{hit.line}` ({hit.surface}, term `{hit.term}`) -- `{hit.excerpt}`"
                    for hit in packet.relation_scan.consumers[: packet.sample_limit]
                ]
            ),
            "",
            "### Asserted exemptions",
            "",
            _bullet(
                [
                    f"`{hit.path}:{hit.line}` -- {hit.exemption_reason}"
                    for hit in packet.relation_scan.exempt[: packet.sample_limit]
                ]
            ),
            "",
        ]
    )
    for layer, scan in packet.layer_scans:
        lines.extend(
            [
                f"### Layer `{layer}` -- SQL-literal references",
                "",
                _bullet(
                    [
                        f"`{hit.path}:{hit.line}` ({hit.surface}) -- `{hit.excerpt}`"
                        for hit in scan.consumers[: packet.sample_limit]
                    ]
                ),
                "",
            ]
        )
    archive = packet.archive
    lines.extend(
        [
            "## 3. Archived snapshot (D1 item 3)",
            "",
            f"Form: `{archive.form}`. Bucket: `{archive.bucket}`. "
            f"sha256: **{archive.sha256 or 'OWED -- no dump has been taken'}**.",
            "",
            _bullet([f"{command.purpose}:\n  ```\n  {command.command}\n  ```" for command in archive.commands]),
            "",
            "Keys:",
            "",
            _bullet([f"`{key}`" for key in archive.object_keys]),
            "",
            "Upload and digest:",
            "",
            _bullet([f"`{command}`" for command in archive.upload_commands] + [f"`{archive.digest_command}`"]),
            "",
            _bullet(list(archive.notes)),
            "",
            "## Migration this packet proposes",
            "",
            "```sql",
            packet.drop_statement,
            "```",
            "",
            "Preconditions, all of which land in the SAME migration:",
            "",
            _bullet(list(packet.migration_preconditions)),
            "",
            "Rehearse on the disposable `agri_sweep` database before production (D1).",
            "",
            "## Notes carried from the ledger",
            "",
            _bullet(list(packet.candidate.notes)),
            "",
            "## Machine-readable body",
            "",
            "```json",
            json.dumps(body, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _render_parity_section(section: ParitySection) -> list[str]:
    """Render one parity section as markdown lines."""
    lines = [f"### `{section.scope}`", "", f"- availability: `{section.availability}`"]
    if section.unavailable_reason is not None:
        lines.append(f"- reason: {section.unavailable_reason}")
    if section.binding is not None:
        lines.append(f"- module: `{section.binding.module}` (`{section.binding.citation}`)")
        lines.append(f"- command: `{section.binding.command}`")
    if section.normalized is not None:
        normalized = section.normalized
        lines.append(
            f"- counted: {normalized.postgres_days} PostgreSQL day(s), {normalized.postgres_rows} row(s); "
            f"covered={normalized.covered}; rows_compared={normalized.rows_compared}; "
            f"uncovered_days={normalized.under_covered_day_count}"
        )
        lines.extend(f"- note: {note}" for note in normalized.notes)
    lines.extend(
        [
            f"- shortfall: `{section.assessment.classification}` -- {section.assessment.detail}",
            "",
        ]
    )
    return lines


__all__ = [
    "TRACK_SLUG",
    "Blocker",
    "BlockerCode",
    "DropPacket",
    "DropPacketRequest",
    "LedgerError",
    "PacketError",
    "build_packet",
]
