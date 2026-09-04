"""The drop ledger: what the A3 inventory says about a relation, and the places it is wrong.

`conductor/tracks/environmental_postgres_retirement_20260904/evidence/retirement-inventory.md` is the
ledger every wave-D packet indexes into. It is PARSED here rather than transcribed, so the tool cannot
drift away from the evidence file the track actually cites, and a relation that is not in it is a
refusal rather than a guess.

THE INVENTORY IS AN INPUT, NEVER AN AUTHORITY. It classes `agri.spatial_cell` "drop now"; the tree
holds five live readers of it (the vegetation forward writer's lattice, `_fill_vegetation`, the
exporter's INNER JOIN, the vegetation parity baseline, and the signal export's own join). So the
overrides below carry the corrections the track has recorded since A3 was written, and `packet.py`
raises a distinct, loud `INVENTORY_CONTRADICTED` blocker whenever a "drop now" row meets a live
consumer in the scan. A ledger row is a hypothesis; the scan is the measurement.

WHAT A DROP FORM IS, AND WHY `geo.features` HAS ONLY ONE. `sql/agent/feature_value_near_point.sql`
keeps a live PostgreSQL read of `geo.features` for `interventions`, a community layer RUNBOOK 0.26.1
keeps in PostgreSQL permanently. There is therefore no future in which that table drops, and the wave
of work that assumed there was would have been wasted. The relation's only permitted form here is
`row_delete` -- "drop these rows for these seven layers" -- and asking for a table drop raises
`DropFormRefusedError` and emits no packet at all, rather than emitting a blocked one that a later reader
might mistake for something an argument could unblock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.retirement.parity import PARITY_BINDINGS, PARQUET_REWRITE_EPOCHS, ParityBinding, RewriteEpoch
from agri_data_service.retirement.readers import ReaderExemption, SearchTerm, default_search_terms

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

#: Where the A3 inventory lives, relative to the repository root.
INVENTORY_RELATIVE_PATH: Final = (
    "conductor/tracks/environmental_postgres_retirement_20260904/evidence/retirement-inventory.md"
)

_BACKTICKED: Final = re.compile(r"`([^`]+)`")

#: A backticked token that is prose, a glob, a function call or a slash-collapsed family rather than
#: one relation name. Rejecting these is what keeps `geo.strategy_recommendations_tiles()` and
#: `agri.forecast_*` out of the parsed ledger instead of becoming relations nothing can resolve.
_NOT_A_RELATION: Final = re.compile(r"[\s*()/\[\]<>:,;=]")


class LedgerError(RuntimeError):
    """Raised when the ledger cannot be read, or names something it cannot classify."""


class DropFormRefusedError(LedgerError):
    """Raised when a caller asks for a drop form this relation may never take.

    Distinct from a blocked packet on purpose: a blocker describes a condition that could later
    clear, and `geo.features` being a permanent PostgreSQL resident is not one of those.
    """


class DropForm(StrEnum):
    """How a relation leaves the environmental schema."""

    TABLE_DROP = "table_drop"
    MATERIALIZED_VIEW_DROP = "materialized_view_drop"
    VIEW_DROP = "view_drop"
    #: The rows of some layers go; the relation stays because something else lives in it.
    ROW_DELETE = "row_delete"


class ObjectKind(StrEnum):
    """What the object is, which decides how it can be archived at all."""

    TABLE = "table"
    MATERIALIZED_VIEW = "materialized_view"
    VIEW = "view"


class InventoryClass(StrEnum):
    """The A3 inventory's own classification of a row."""

    DROP_NOW = "drop_now"
    DROP_AFTER_PARQUET_PROOF = "drop_after_parquet_proof"
    ALREADY_DROPPED = "already_dropped"
    KEEP = "keep"
    UNRESOLVED = "unresolved"


#: The drop form each object kind takes when the whole object goes.
_WHOLE_OBJECT_FORM: Final[Mapping[ObjectKind, DropForm]] = MappingProxyType(
    {
        ObjectKind.TABLE: DropForm.TABLE_DROP,
        ObjectKind.MATERIALIZED_VIEW: DropForm.MATERIALIZED_VIEW_DROP,
        ObjectKind.VIEW: DropForm.VIEW_DROP,
    }
)


@dataclass(frozen=True, slots=True)
class InventoryRow:
    """One relation as the A3 inventory rendered it."""

    relation: str
    schema: str
    inventory_class: InventoryClass
    classification_text: str
    gating_layer: str
    source_line: int


@dataclass(frozen=True, slots=True)
class RecordedReader:
    """A reader established by an earlier pass, cited so a refusal never rests on a grep alone."""

    citation: str
    why: str


@dataclass(frozen=True, slots=True)
class SurvivalDependency:
    """A relation the environment cannot lose, whatever the inventory classed it.

    Belt and braces on purpose. The repository scan finds these readers today; this registry keeps the
    refusal alive even if a later edit narrows a scan surface, and it names the correction so the
    inventory row's own wrongness is recorded rather than merely overridden.
    """

    readers: tuple[RecordedReader, ...]
    correction: str


@dataclass(frozen=True, slots=True)
class DependentObject:
    """An object that must be handled IN THE SAME MIGRATION, never left to `CASCADE`."""

    name: str
    required_action: str
    why: str
    #: `DROP ... CASCADE` would take this object with it. For a view the app still reads, that turns
    #: one proven drop into an unproven outage.
    forbid_cascade: bool = True


@dataclass(frozen=True, slots=True)
class LayerScope:
    """One layer's rows inside a shared polymorphic relation, with its own parity and epoch."""

    layer_name: str
    lane_slug: str
    parity: ParityBinding | None
    rewrite_epoch: RewriteEpoch | None


@dataclass(frozen=True, slots=True)
class DropCandidate:
    """Everything the packet builder needs about one relation, ledger row plus recorded corrections."""

    relation: str
    object_kind: ObjectKind
    object_kind_basis: str
    permitted_forms: frozenset[DropForm]
    inventory: InventoryRow
    search_terms: tuple[SearchTerm, ...]
    parity: ParityBinding | None = None
    rewrite_epoch: RewriteEpoch | None = None
    layer_scopes: tuple[LayerScope, ...] = ()
    reader_exemptions: tuple[ReaderExemption, ...] = ()
    dependent_objects: tuple[DependentObject, ...] = ()
    survival: SurvivalDependency | None = None
    #: The predicate a row-delete archives and deletes by, with a `{layers}` slot for the layers the
    #: packet actually covers. Required for `ROW_DELETE`; `archive.py` refuses a row-delete without
    #: one, and `row_filter` refuses to widen it past the layers asked for.
    row_filter_template: str | None = None
    form_refusal: str = ""
    notes: tuple[str, ...] = ()

    def row_filter(self, layers: Sequence[str] = ()) -> str:
        """Render the row predicate for exactly these layers, defaulting to every layer scope.

        THE PREDICATE FOLLOWS THE PACKET'S SCOPE. A packet built for one layer that archived and
        deleted all seven would destroy six layers on one layer's proof -- and it would do it while
        printing a receipt that only ever mentioned the one.
        """
        if self.row_filter_template is None:
            raise LedgerError(f"{self.relation} has no row-delete predicate; it is not a row-delete candidate")
        selected = tuple(layers) or tuple(scope.layer_name for scope in self.layer_scopes)
        known = {scope.layer_name for scope in self.layer_scopes}
        unknown = [layer for layer in selected if layer not in known]
        if unknown:
            raise LedgerError(f"{self.relation} holds no layer(s) named {unknown}")
        return self.row_filter_template.format(layers=", ".join(f"'{layer}'" for layer in selected))

    def require_form(self, form: DropForm) -> DropForm:
        """Return `form` if this relation may take it, else refuse with the recorded reason."""
        if form in self.permitted_forms:
            return form
        permitted = ", ".join(sorted(str(value) for value in self.permitted_forms))
        raise DropFormRefusedError(
            f"{self.relation} may not be dropped as {form}; permitted form(s): {permitted}. {self.form_refusal}".strip()
        )

    def default_form(self) -> DropForm:
        """Return the only permitted form, refusing when a relation genuinely has a choice."""
        if len(self.permitted_forms) == 1:
            return next(iter(self.permitted_forms))
        raise DropFormRefusedError(
            f"{self.relation} permits more than one drop form; name one with --form: "
            + ", ".join(sorted(str(value) for value in self.permitted_forms))
        )

    def layer_scope(self, layer_name: str) -> LayerScope:
        """Return one named layer scope, refusing a layer this relation does not hold."""
        for scope in self.layer_scopes:
            if scope.layer_name == layer_name:
                return scope
        known = ", ".join(scope.layer_name for scope in self.layer_scopes)
        raise LedgerError(f"{self.relation} holds no layer named {layer_name!r}; known layers: {known}")


# --- The A3 inventory, parsed ------------------------------------------------------


def _classify(text: str) -> InventoryClass:
    """Map an inventory classification cell onto the enum, refusing to guess at an unknown one."""
    plain = re.sub(r"[*_`]", "", text).strip().lower()
    if plain.startswith("drop now"):
        return InventoryClass.DROP_NOW
    if plain.startswith("drop after"):
        return InventoryClass.DROP_AFTER_PARQUET_PROOF
    if plain.startswith("already dropped"):
        return InventoryClass.ALREADY_DROPPED
    if plain.startswith("keep"):
        return InventoryClass.KEEP
    return InventoryClass.UNRESOLVED


def _relations_in(cell: str, schema: str) -> Iterator[str]:
    """Yield each relation named in one table cell, qualifying a bare name with the row's schema."""
    for token in _BACKTICKED.findall(cell):
        name = token.strip()
        if not name or _NOT_A_RELATION.search(name):
            continue
        yield name if "." in name else f"{schema}.{name}"


def parse_inventory(markdown: str) -> tuple[InventoryRow, ...]:
    """Parse every relation row out of the inventory's three markdown tables.

    A cell may name several relations (`geo.geometry` "(+ its view `geo.geometry_current`)", the three
    first-generation historical tables, the eleven-table `job_*` family); each becomes its own row
    carrying the classification of the row it appeared in. The FIRST classification wins, so a
    relation mentioned again in a later grouped row cannot silently reclassify itself.
    """
    rows: dict[str, InventoryRow] = {}
    for number, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        minimum_cells = 6
        if len(cells) < minimum_cells or set(cells[0]) <= set("-: "):
            continue
        schema = _BACKTICKED.findall(cells[1])
        if not schema:
            continue
        classification = _classify(cells[4])
        for relation in _relations_in(cells[0], schema[0].strip()):
            rows.setdefault(
                relation,
                InventoryRow(
                    relation=relation,
                    schema=schema[0].strip(),
                    inventory_class=classification,
                    classification_text=cells[4],
                    gating_layer=cells[5],
                    source_line=number,
                ),
            )
    if not rows:
        raise LedgerError("the retirement inventory parsed to zero relations; the table shape must have changed")
    return tuple(rows.values())


def load_inventory(repository_root: Path) -> tuple[InventoryRow, ...]:
    """Read and parse the inventory from a checkout, refusing a missing evidence file."""
    path = repository_root / INVENTORY_RELATIVE_PATH
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as error:
        raise LedgerError(f"cannot read the retirement inventory at {path}: {error}") from error
    return parse_inventory(markdown)


# --- Recorded corrections to the inventory -----------------------------------------

#: The seven environmental layers whose ROWS leave `geo.features`. `interventions` is deliberately
#: absent: it is the permanent resident that makes the table itself undroppable.
GEO_FEATURES_ENVIRONMENTAL_LAYERS: Final[tuple[str, ...]] = (
    "weather-observations",
    "fire-perimeters",
    "vegetation",
    "sensors",
    "evacuation-zones",
    "watersheds",
    "burn-severity",
)

#: Object kinds a name cannot reveal. `geo.watershed_rollup` is a materialized view with no `mv_`
#: prefix, and pg_dump does not dump a materialized view's CONTENTS -- so getting this wrong would
#: produce an archive command that silently preserves a definition and loses every row.
OBJECT_KIND_OVERRIDES: Final[Mapping[str, tuple[ObjectKind, str]]] = MappingProxyType(
    {
        "geo.watershed_rollup": (
            ObjectKind.MATERIALIZED_VIEW,
            "drizzle/0023_watershed_zoom_generalization.sql:110-130 refreshes it via geo.refresh_watershed_rollup()",
        ),
        "geo.geometry_current": (
            ObjectKind.VIEW,
            "retirement-inventory.md: 'geo.geometry (+ its view geo.geometry_current)'",
        ),
        "geo.published_raster": (
            ObjectKind.VIEW,
            "drizzle/0024_soil_raster_release.sql:90 declares the view over geo.raster_release",
        ),
        "geo.v_strategy_recommendation_cells": (ObjectKind.VIEW, "named as a view in the inventory's keep table"),
    }
)

SURVIVAL_DEPENDENCIES: Final[Mapping[str, SurvivalDependency]] = MappingProxyType(
    {
        "agri.spatial_cell": SurvivalDependency(
            correction=(
                "the A3 inventory classes this 'drop now' on the strength of an unverified report that the "
                "relation is already absent from production. It is a SURVIVAL dependency: five live readers "
                "sit on it, and dropping it would take the vegetation lane's lattice, its cell_id on every "
                "row, its exporter and its own parity baseline with it"
            ),
            readers=(
                RecordedReader(
                    citation="src/agri_data_service/execution/vegetation_ndvi_plane.py:522-526,694",
                    why="the vegetation forward writer's lattice; every published row's cell_id comes from here",
                ),
                RecordedReader(
                    citation="src/agri_data_service/pipeline/parquet/lane_registry.py:106",
                    why="_fill_vegetation loads sql/pipeline/lane_registry_spatial_cell_ids.sql against it",
                ),
                RecordedReader(
                    citation="src/agri_data_service/sql/pipeline/vegetation_day_export.sql",
                    why="the exporter's INNER JOIN; without the cell dimension the export writes nothing",
                ),
                RecordedReader(
                    citation="src/agri_data_service/pipeline/direct/vegetation/parity.py:75",
                    why="INNER JOIN agri.spatial_cell -- the vegetation parity baseline itself reads it",
                ),
                RecordedReader(
                    citation="src/agri_data_service/sql/execution/coverage_grid_cells.sql",
                    why="the signal plane's export and coverage census join the same cell dimension",
                ),
            ),
        )
    }
)

DEPENDENT_OBJECTS: Final[Mapping[str, tuple[DependentObject, ...]]] = MappingProxyType(
    {
        "geo.mv_signal_observation_day": (
            DependentObject(
                name="geo.v_observation_day_census",
                required_action="redefine in the same migration, dropping the signal leg from the union",
                why=(
                    "wave C deleted both of this matview's readers, so it is genuinely zero-reader now -- but "
                    "geo.v_observation_day_census unions it with the drought and feature legs, and the app "
                    "still reads that view's FEATURE leg. DROP ... CASCADE would take the view and the live "
                    "leg with it; the migration must CREATE OR REPLACE the view without the signal leg in the "
                    "same transaction as the DROP"
                ),
            ),
        )
    }
)


def _geo_features_layer_scopes() -> tuple[LayerScope, ...]:
    """Build the seven environmental layer scopes, each carrying its own parity module and epoch."""
    return tuple(
        LayerScope(
            layer_name=layer,
            lane_slug=layer,
            parity=PARITY_BINDINGS.get(layer),
            rewrite_epoch=PARQUET_REWRITE_EPOCHS.get(layer),
        )
        for layer in GEO_FEATURES_ENVIRONMENTAL_LAYERS
    )


#: The predicate that names the covered layers' rows, by layer NAME rather than by a hard-coded uuid:
#: `geo.layers.name` is the stable identifier the tile functions and the CLI both key on, and a uuid
#: transcribed into a packet is a uuid that will be wrong in the next environment.
_GEO_FEATURES_ROW_FILTER_TEMPLATE: Final = "layer_id IN (SELECT id FROM geo.layers WHERE name IN ({layers}))"


def _geo_features_candidate(inventory: InventoryRow) -> DropCandidate:
    """`geo.features`: a row-delete for seven layers, never a table drop. See the module docstring."""
    return DropCandidate(
        relation="geo.features",
        object_kind=ObjectKind.TABLE,
        object_kind_basis="drizzle/0000_narrow_tony_stark.sql:104 declares the table",
        permitted_forms=frozenset({DropForm.ROW_DELETE}),
        inventory=inventory,
        form_refusal=(
            "sql/agent/feature_value_near_point.sql keeps a live read of geo.features for `interventions`, "
            "a community layer RUNBOOK 0.26.1 keeps in PostgreSQL permanently, so the table has no drop. "
            "The seven environmental layers leave as rows. Moving `interventions` to its own table first is "
            "the only route to a table drop and is an owner call, not a packet"
        ),
        search_terms=(
            *default_search_terms("geo.features"),
            SearchTerm("fire_risk_tiles", "Martin tile function over geo.features WHERE l.name = 'fire-perimeters'"),
            SearchTerm("sensor_tiles", "Martin tile function over geo.features WHERE l.name = 'sensors'"),
            SearchTerm("burn_severity_tiles", "Martin tile function over geo.features WHERE l.name = 'burn-severity'"),
            SearchTerm(
                "evacuation_zone_tiles", "Martin tile function over geo.features WHERE l.name = 'evacuation-zones'"
            ),
            SearchTerm("watershed_tiles", "Martin tile function; its z>=10 branch reads geo.features directly"),
        ),
        layer_scopes=_geo_features_layer_scopes(),
        reader_exemptions=(
            ReaderExemption(
                path="services/agri-data-service/src/agri_data_service/sql/agent/feature_value_near_point.sql",
                reason=(
                    "reads geo.features for `interventions` only, which RUNBOOK 0.26.1 keeps in PostgreSQL "
                    "permanently; the caller checks the surface against the catalogue, and none of the seven "
                    "environmental layers routes here any more (the statement's own header, 2026-09-04). This "
                    "exemption is ASSERTED rather than left absent -- the wave-C review's complaint about the "
                    "guard test was exactly that it passed by not listing the relation"
                ),
                applies_to_forms=frozenset({str(DropForm.ROW_DELETE)}),
            ),
        ),
        row_filter_template=_GEO_FEATURES_ROW_FILTER_TEMPLATE,
        notes=(
            "geo.geometry is gated transitively through geo.features.geometry_id and does not clear with "
            "these rows; the seven layers' geometry rows need their own packet",
            "criterion 4 is not discharged by deleting rows: the seven ingest commands, their lane specs and "
            "their tests still exist and are their own removal packets (plan.md, D-fills)",
        ),
    )


def _synthesised_candidate(inventory: InventoryRow) -> DropCandidate:
    """Build the ordinary candidate for a relation with no recorded correction.

    Kind is inferred from the `mv_`/`v_` naming convention the geo schema follows, and the basis is
    recorded in the packet so a reader can see the inference rather than trust it.
    """
    bare = inventory.relation.rpartition(".")[2]
    override = OBJECT_KIND_OVERRIDES.get(inventory.relation)
    if override is not None:
        kind, basis = override
    elif bare.startswith("mv_"):
        kind, basis = ObjectKind.MATERIALIZED_VIEW, "inferred from the `mv_` prefix the geo schema uses"
    elif bare.startswith("v_"):
        kind, basis = ObjectKind.VIEW, "inferred from the `v_` prefix the geo schema uses"
    else:
        kind, basis = ObjectKind.TABLE, "inferred: no `mv_`/`v_` prefix and no recorded override"
    return DropCandidate(
        relation=inventory.relation,
        object_kind=kind,
        object_kind_basis=basis,
        permitted_forms=frozenset({_WHOLE_OBJECT_FORM[kind]}),
        inventory=inventory,
        search_terms=default_search_terms(inventory.relation),
        parity=PARITY_BINDINGS.get(_LANE_FOR_RELATION.get(inventory.relation, "")),
        rewrite_epoch=PARQUET_REWRITE_EPOCHS.get(_LANE_FOR_RELATION.get(inventory.relation, "")),
        dependent_objects=DEPENDENT_OBJECTS.get(inventory.relation, ()),
        survival=SURVIVAL_DEPENDENCIES.get(inventory.relation),
    )


#: Which Parquet lane is the twin of a dedicated relation. Only drought owns its own table; the other
#: seven layers share `geo.features` and are bound per layer scope instead.
_LANE_FOR_RELATION: Final[Mapping[str, str]] = MappingProxyType({"geo.drought_areas": "drought"})


def resolve_candidate(relation: str, inventory: Sequence[InventoryRow]) -> DropCandidate:
    """Look one relation up in the parsed ledger and apply every recorded correction to it.

    A relation absent from the inventory is refused rather than synthesised: the packet's whole claim
    is that the drop was ledgered, proven and archived, and an unledgered relation has not been
    through A3's reader census at all.
    """
    matches = [row for row in inventory if row.relation == relation]
    if not matches:
        raise LedgerError(
            f"{relation!r} is not in {INVENTORY_RELATIVE_PATH}. Add it to the A3 inventory with its filler, "
            "its readers and its classification before asking for a drop packet"
        )
    row = matches[0]
    if relation == "geo.features":
        return _geo_features_candidate(row)
    return _synthesised_candidate(row)


@dataclass(frozen=True, slots=True)
class Ledger:
    """The parsed inventory plus the lookup a packet build needs."""

    rows: tuple[InventoryRow, ...] = field(default_factory=tuple)

    def candidate(self, relation: str) -> DropCandidate:
        """Resolve one relation into a corrected drop candidate."""
        return resolve_candidate(relation, self.rows)

    def relations_in_class(self, inventory_class: InventoryClass) -> tuple[str, ...]:
        """Return every ledgered relation in one class, in inventory order."""
        return tuple(row.relation for row in self.rows if row.inventory_class is inventory_class)


def load_ledger(repository_root: Path) -> Ledger:
    """Read the A3 inventory from a checkout and return the queryable ledger."""
    return Ledger(rows=load_inventory(repository_root))


__all__ = [
    "DEPENDENT_OBJECTS",
    "GEO_FEATURES_ENVIRONMENTAL_LAYERS",
    "INVENTORY_RELATIVE_PATH",
    "OBJECT_KIND_OVERRIDES",
    "SURVIVAL_DEPENDENCIES",
    "DependentObject",
    "DropCandidate",
    "DropForm",
    "DropFormRefusedError",
    "InventoryClass",
    "InventoryRow",
    "LayerScope",
    "Ledger",
    "LedgerError",
    "ObjectKind",
    "RecordedReader",
    "SurvivalDependency",
    "load_inventory",
    "load_ledger",
    "parse_inventory",
    "resolve_candidate",
]
