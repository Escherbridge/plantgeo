"""Origin decided once per (region, taxon) across every guide row; see AGENTS.md §Origin here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict

import polars as pl

if TYPE_CHECKING:
    from agri_data_service.warehouse.plant_suitability.config import RuleConfig

# Stands in for the guild's introduced-flag wording, which each guild renders from its RuleConfig.
INTRODUCED_PLACEHOLDER = "<introduced flag>"
NATIVE_AND_INTRODUCED_LABEL = "native and introduced populations (PLANTS L48 I|N)"
RANGE_WIDE_SCOPE = "range_wide"
ORIGIN_SCOPES = frozenset({"regional", RANGE_WIDE_SCOPE})
PLANTS_NATIVE_CODES = frozenset({"N", "N?"})
PLANTS_INTRODUCED_CODES = frozenset({"I", "I?", "W"})


class OriginRecord(TypedDict):
    """One taxon's decided origin, as a row of the origins table."""

    plant_id: int
    origin_category: str
    origin_label: str
    introduced_flag: bool


@dataclass
class OriginDecision:
    """One (region, taxon) origin: category, label, introduced flag and the notes appended to the label."""

    category: str
    label: str
    introduced_flag: bool
    notes: list[str] = field(default_factory=list)


def plants_codes(l48_status: str | None) -> set[str]:
    """The PLANTS lower-48 status codes, e.g. {'I', 'N'} for 'I|N'."""
    return set((l48_status or "").split("|")) - {""}


def document_statements(matched_all: pl.DataFrame, config: RuleConfig) -> pl.DataFrame:
    """Per (document, binomial): what the document says about origin anywhere in it (any region, any row)."""
    native = pl.col("origin_per_source") == "native"
    introduced = pl.col("origin_per_source") == "introduced"
    scoped_range_wide = pl.col("origin_scope") == RANGE_WIDE_SCOPE
    range_wide = scoped_range_wide if config.range_wide_origin_is_not_state_claim else pl.lit(value=False)
    return matched_all.group_by("source_short_name", "plant_binomial").agg(
        (native & ~range_wide).any().alias("says_native_regionally"),
        (native & range_wide).any().alias("says_native_range_wide"),
        introduced.any().alias("says_introduced"),
        (introduced & pl.col("nativity_conflict")).any().alias("introduced_marked_conflict"),
    )


def document_verdict(
    document: dict[str, Any], *, present: bool, l48: str | None, state: str
) -> tuple[str | None, str | None]:
    """(verdict, note): native if the document says native anywhere; a curator-marked conflict falls to PLANTS."""
    name = document["source_short_name"]
    if document["says_native_regionally"] or document["says_native_range_wide"]:
        note = f"{name} prints it both native and introduced, counted native" if document["says_introduced"] else None
        return "native", note
    if not document["says_introduced"]:
        return None, None
    if not document["introduced_marked_conflict"]:
        return "introduced", None
    if present and plants_codes(l48) & PLANTS_NATIVE_CODES:
        return "native", f"origin disputed: {name} says introduced, PLANTS L48 {l48} and recorded in {state}"
    return "introduced", f"origin disputed: {name} says introduced, PLANTS L48 {l48}, not recorded in {state}"


def plants_fallback(l48: str | None, *, present: bool, state: str) -> OriginDecision:
    """Origin from PLANTS lower-48 status when every document is silent."""
    codes = plants_codes(l48)
    has_native, has_introduced = bool(codes & PLANTS_NATIVE_CODES), bool(codes & PLANTS_INTRODUCED_CODES)
    if has_native and has_introduced:
        return OriginDecision("native_and_introduced", NATIVE_AND_INTRODUCED_LABEL, introduced_flag=False)
    if has_introduced:
        label = f"{INTRODUCED_PLACEHOLDER} (PLANTS L48 {l48}; sources silent)"
        return OriginDecision("introduced", label, introduced_flag=True)
    if has_native:
        recorded = f"recorded in {state}" if present else f"not recorded in {state}"
        return OriginDecision("native", f"native in L48, {recorded} (PLANTS; sources silent)", introduced_flag=False)
    return OriginDecision("unknown", "origin unknown (sources silent; no PLANTS L48 status)", introduced_flag=False)


def document_vote(verdicts: list[dict[str, Any]], *, present: bool, l48: str | None, state: str) -> OriginDecision:
    """In-region documents vote first (one vote each); a tie is broken by PLANTS state presence."""
    in_region_votes = [verdict for verdict in verdicts if verdict["verdict"] and verdict["in_region"]]
    tier = in_region_votes or [verdict for verdict in verdicts if verdict["verdict"] and not verdict["in_region"]]
    tier_label = "in-region" if in_region_votes else "neighbouring"
    native_by = sorted({verdict["source_short_name"] for verdict in tier if verdict["verdict"] == "native"})
    introduced_by = sorted({verdict["source_short_name"] for verdict in tier if verdict["verdict"] == "introduced"})
    if not (native_by or introduced_by):
        return plants_fallback(l48, present=present, state=state)
    notes: list[str] = []
    tie = len(native_by) == len(introduced_by)
    if tie:
        native_wins = present and bool(plants_codes(l48) & PLANTS_NATIVE_CODES)
        presence = "recorded" if present else "not recorded"
        notes.append(f"tie between {tier_label} sources broken by PLANTS ({presence} in {state}, L48 {l48})")
    else:
        native_wins = len(native_by) > len(introduced_by)
    other_side = "tie" if tie else "outvoted"
    if native_wins:
        others = f" / introduced per {', '.join(introduced_by)}, {other_side}" if introduced_by else ""
        label = f"native (per {', '.join(native_by)}{others})"
        return OriginDecision("native", label, introduced_flag=False, notes=notes)
    others = f" / native per {', '.join(native_by)}, {other_side}" if native_by else ""
    label = f"{INTRODUCED_PLACEHOLDER} (per {', '.join(introduced_by)}{others})"
    return OriginDecision("introduced", label, introduced_flag=True, notes=notes)


def state_check(
    decision: OriginDecision, verdicts: list[dict[str, Any]], *, l48: str | None, state: str
) -> OriginDecision:
    """Flag a taxon PLANTS does not record in the state unless an in-region, state-scoped source calls it native."""
    if decision.category == "introduced":
        decision.label += f" (not recorded in {state}, PLANTS)"
        return decision
    state_claims = [
        verdict["source_short_name"]
        for verdict in verdicts
        if verdict["in_region"] and verdict["verdict"] == "native" and verdict["says_native_regionally"]
    ]
    if state_claims:
        decision.notes.append(f"PLANTS does not record it in {state}; native per in-region {', '.join(state_claims)}")
        return decision
    natives = [verdict for verdict in verdicts if verdict["verdict"] == "native"]
    range_wide = sorted({verdict["source_short_name"] for verdict in natives if not verdict["says_native_regionally"]})
    regional = [verdict for verdict in natives if verdict["says_native_regionally"] and not verdict["in_region"]]
    neighbours = sorted({verdict["source_short_name"] for verdict in regional})
    basis: list[str] = []
    if range_wide:
        basis.append(f"native per {', '.join(range_wide)} is a range-wide statement")
    if neighbours:
        basis.append(f"native only per neighbouring {', '.join(neighbours)}")
    if not basis:
        basis.append(f"no in-region source calls it native, PLANTS L48 {l48}")
    label = f"not recorded in {state} (PLANTS) ({' / '.join(basis)})"
    return OriginDecision("not_recorded_in_state", label, introduced_flag=True, notes=decision.notes)


def decide_origin(taxon: dict[str, Any], documents: list[dict[str, Any]], state: str) -> OriginRecord:
    """Origin of one (region, taxon) from one verdict per document naming its binomial."""
    present, l48 = bool(taxon["present_in_state"]), taxon["native_status_l48"]
    verdicts: list[dict[str, Any]] = []
    for document in documents:
        verdict, note = document_verdict(document, present=present, l48=l48, state=state)
        verdicts.append({**document, "verdict": verdict, "note": note})
    decision = document_vote(verdicts, present=present, l48=l48, state=state)
    decision.notes = [verdict["note"] for verdict in verdicts if verdict["note"]] + decision.notes
    if not present:
        decision = state_check(decision, verdicts, l48=l48, state=state)
    label = decision.label
    if decision.notes:
        label += " — " + " / ".join(dict.fromkeys(decision.notes))
    return {
        "plant_id": taxon["plant_id"],
        "origin_category": decision.category,
        "origin_label": label,
        "introduced_flag": decision.introduced_flag,
    }


def decide_origins(
    kept_taxa: pl.DataFrame, region_matched: pl.DataFrame, documents: pl.DataFrame, state: str
) -> pl.DataFrame:
    """Origin per kept taxon of one region (every guild), from every region row naming the taxon's binomial."""
    rows = (
        region_matched.group_by("plant_binomial", "source_short_name")
        .agg(pl.col("in_region").any())
        .join(documents, on=["source_short_name", "plant_binomial"], how="left")
        .sort("source_short_name")
    )
    by_binomial: dict[str, list[dict[str, Any]]] = {}
    for row in rows.iter_rows(named=True):
        by_binomial.setdefault(row["plant_binomial"], []).append(row)
    records = [
        decide_origin(taxon, by_binomial.get(taxon["plant_binomial"], []), state)
        for taxon in kept_taxa.unique(subset="plant_id", keep="first", maintain_order=True).iter_rows(named=True)
    ]
    schema = {
        "plant_id": pl.Int64,
        "origin_category": pl.String,
        "origin_label": pl.String,
        "introduced_flag": pl.Boolean,
    }
    return pl.DataFrame(records, schema=schema)
