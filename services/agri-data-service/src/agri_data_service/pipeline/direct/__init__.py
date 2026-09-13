"""Source-direct Parquet producers that use PostgreSQL only for publication locks.

THE CROSS-WRITER CONTRACT LIVES HERE, and only here. Eleven writers were built over months by
different passes; the words they report, the knobs their CLIs expose and what each does with a
malformed upstream record all drifted. Nothing was wrong with most of the differences -- a lattice
lane genuinely has no bbox to take and a single-product lane genuinely has no `--product` to offer
-- but a difference nobody wrote down is indistinguishable from an accident, and a twelfth writer
copying an arbitrary sibling inherits whichever it happened to copy.

So every writer declares a `WRITER_CONTRACT` beside its `parser()`, `tests/direct/test_direct_writer_contract.py`
reads all eleven as one table, and a policy this module does not name cannot be adopted silently.

IT SITS IN THE PACKAGE `__init__` FOR THE SAME REASON `pipeline/lanes/__init__.py::LANE_BASE_ZOOM_TIER`
does: `tests/test_layer_import_contract.py::_lane_names` counts every flat `*.py` under
`pipeline/direct/` as its own LANE, and `test_lanes_do_not_import_each_other` refuses a lane that
imports a sibling. A `pipeline/direct/contract.py` would therefore be a twelfth lane that the other
eleven are forbidden to import. The package `__init__` is the one module here every writer may reach.

NOTHING HEAVY IS IMPORTED HERE. This module is executed on the way in to every writer, every
adapter and every test that touches one. It holds strings, frozensets and one dataclass.

## The outcome vocabulary is four layers, three of which already existed

1. `LaneDayOutcome` (`pipeline/parquet/gap_fill.py:216`) -- what ONE lane-day publication did.
2. `LaneFillOutcome` (`gap_fill.py:215`) -- what a whole gap-fill WALK did.
3. `StaticLaneState` (`foundation/parquet/lane_contract.py:61`) -- where a version-stamped lane
   stands against its source watermark. The two `static_lookup` writers here report it under a
   `state` key rather than an `outcome` key, which is correct: "stale" is not something a turn did.
4. The DIRECT-WRITER TURN vocabulary -- the words a bounded turn reports for a day it chose not to
   publish. That layer had no declaration anywhere, which is why nine spellings were observable in
   production logs and no monitor could enumerate them. `DIRECT_TURN_OUTCOMES` below is it.

`LANE_DAY_OUTCOMES` RESTATES layer 1 rather than importing it, and that is deliberate rather than
lazy: `gap_fill` imports `lane_registry`, which imports writer packages, which import this module,
so the edge would close a cycle. `pipeline/lanes/__init__.py` documents the identical restatement
for `GAP_FILL_ZOOM_TIER` and for the identical reason. The anti-drift proof is a test
(`test_lane_day_outcomes_match_the_shared_literal`), not an import.

## Two failure policies on a bad upstream record, and the axis that separates them

The two policies read as a contradiction only until you notice they answer questions about
DIFFERENT DEFECTS. Every one of the eleven splits a malformed record the same way:

- IDENTITY DEFECT -- the record cannot be keyed at all (no `properties`/`geometry` object, an
  unparseable natural key, a missing GlobalID). Some writers count it and publish the rest; some
  refuse the release.
- GEOMETRY DEFECT -- the record keys fine and its shape is present, but repairs or converts to
  nothing. EVERY geometry-bearing writer here refuses the whole release, and every one of them
  cites the same basis: the PostgreSQL write path it replaced ALSO refused, because
  `geo_features_sync_geom` raises SQLSTATE 22023 and aborts the INSERT that carried the shape. A
  `MULTIPOLYGON EMPTY` row is a fabricated "this thing exists and covers nothing" claim.

The comparison that looks like drift -- fire-perimeters refusing a snapshot while watersheds
publishes beside `rejected_basins: 0` -- is a comparison across that axis, not along it.
`fire_perimeters/rows.py:242` counts an identity defect and publishes, exactly as watersheds does;
`watersheds/support.py:130` refuses the whole population for an empty geometry, exactly as
fire-perimeters does. Their declared contracts below are IDENTICAL. What differed was which defect
their live data happened to contain.

The one real outlier on this axis is `fire-detections`, which routes an IDENTITY defect
("unkeyable or invalid records for {day}; refusing a partial day", `fire_detections.py:245`) into
the refuse-the-release bucket where its three peers count and continue. That is declared below as
what it is, and left alone: changing a live lane's failure policy is an owner decision, not a
uniformity edit.

## Unset `INGEST_BBOX` has three answers, and the nature of the lane picks which

- REFUSE: the turn cannot honestly proceed. A `static_lookup` writer stamps a VERSION, and the only
  two moves at a version boundary are "publish this population" and "publish nothing"; publishing a
  population fetched over an unstated extent would stamp a version whose coverage nobody can cite
  (`fire_perimeters/source.py:86`).
- SKIP_TURN: the turn is a no-op and the next tick retries. Correct where the layer is bounded
  TWICE -- by the publisher's own footprint AND by `INGEST_BBOX` -- so an unset envelope would
  WIDEN the query past what every downstream coverage contract claims
  (`evacuation_zones/products.py:64`, `watersheds/forward.py:190`).
- NOT_BBOX_BOUNDED: the writer has no spatial argument to take. Its support is a PINNED LATTICE
  read from the historical spatial dimension, or a national release. A `--bbox` on those lanes
  would not narrow a query, it would silently change the SUPPORT a day is written against, and a
  day written against a different support is not comparable with the history it extends.

The two answers differ because refusing and skipping are different claims about the same fact, and
both are right for their lane: fire-perimeters CANNOT skip without leaving the previous version
serving under a coverage claim it never re-proved, and evacuation-zones CANNOT refuse without
failing a life-safety lane's cron on a missing environment variable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

# --------------------------------------------------------------------------------------------
# Outcome vocabulary
# --------------------------------------------------------------------------------------------

#: RESTATES `pipeline/parquet/gap_fill.py::LaneDayOutcome`; see the module docstring for why this is
#: a copy and not an import. A test pins the two equal.
LANE_DAY_OUTCOMES: Final[frozenset[str]] = frozenset({"written", "absent", "raised", "blocked", "contended"})

#: The turn ran out of wall clock before reaching this day. NOT a failure: the day is untouched and
#: the next tick starts where this one stopped. Nine of eleven writers report it; the two that do
#: not publish at most one day per turn, so they have no walk for a clock to interrupt.
TIME_BUDGET_EXHAUSTED: Final = "time_budget_exhausted"

#: The turn hit its per-turn upstream-request ceiling before reaching this day. Distinct from
#: `TIME_BUDGET_EXHAUSTED` because it bounds a QUOTA rather than a clock: a fan-out writer can burn
#: its request allowance long before its seconds, and an operator raising the wrong knob would wait
#: for a limit that was never the binding one.
REQUEST_BUDGET_EXHAUSTED: Final = "request_budget_exhausted"

#: The day is past its publication lag, the writer asked, and the source answered all-null/all-fill
#: with nothing proving the mirror has moved past the day. A REFUSAL, retried next turn -- never a
#: governed absence, because "the mirror has not reached this day" and "the source published nothing
#: for this day" are opposite claims and only the second is permanent.
SOURCE_UNSETTLED: Final = "source_unsettled"

#: The day is not yet past its publication lag, so the writer never asked. Deliberately a DIFFERENT
#: word from `SOURCE_UNSETTLED`, which they read as near-synonyms and are not: this one is "we have
#: not waited long enough yet" (no request was made), that one is "we waited, asked, and the mirror
#: is behind" (a request was made and answered). Confusing them sends an operator to the wrong knob.
NOT_YET_SETTLED: Final = "not_yet_settled"

#: `INGEST_BBOX` is unset and this lane's declared answer is to skip the turn rather than widen its
#: query. See `UNCONFIGURED_BBOX_POLICIES` for why that is not every lane's answer.
BBOX_UNCONFIGURED: Final = "bbox_unconfigured"

#: The source population is byte-for-byte the version already published, so no new version day is
#: stamped. A `static_lookup` word: only a lane whose clock IS a content comparison can report it.
UNCHANGED: Final = "unchanged"

#: Every day the turn considered was already complete, so the turn published nothing and that is the
#: correct result. Distinct from `UNCHANGED`, which is about one snapshot's CONTENT matching; this
#: is about the day-selection walk finding an empty backlog.
IDEMPOTENT_NOOP: Final = "idempotent_noop"

#: The turn published at least one day. The run-level partner of `IDEMPOTENT_NOOP`.
PUBLISHED: Final = "published"

#: Every day the turn touched settled. The run-level partner of `INCOMPLETE`.
COMPLETE: Final = "complete"

#: At least one day the turn touched did not settle. Says nothing about WHICH -- the per-day
#: outcomes ride alongside in the same record, and this word exists so a monitor can alert without
#: parsing them.
INCOMPLETE: Final = "incomplete"

#: The poll returned nothing writable for any day in the window. NOT a governed absence and NOT a
#: failure: a rolling-window source with no fresh readings is the ordinary quiet case.
NO_WRITABLE_OBSERVATIONS: Final = "no_writable_observations"

#: Another run holds this lane-day's advisory lock, so this turn performed no fetch and no write.
#: SPELLED DIFFERENTLY FROM `LaneDayOutcome`'s own `contended`, and that divergence is real drift,
#: not a distinction -- see `KNOWN_SYNONYMS`. It is declared rather than renamed because renaming it
#: changes an observable field on a live lane, which is an owner call.
LOCK_CONTENDED: Final = "lock_contended"

#: The turn stopped because too many days in a row failed to resolve, rather than because a clock or
#: a quota ran out. A distinct word so a monitor can tell "we ran out of time" from "the source kept
#: giving us days we could not settle", which need opposite responses.
UNRESOLVED_DAY_BUDGET_EXHAUSTED: Final = "unresolved_day_budget_exhausted"

#: The turn had no candidate day to consider at all -- the governance table names no release day, or
#: the source watermark could not be read, so there is no window to walk. BORROWED VERBATIM from
#: `LaneFillOutcome` (`gap_fill.py:215`), which already means exactly this for the gap-fill walk;
#: minting a second word for a state the warehouse had already named is how the nine spellings
#: happened. Distinct from `IDEMPOTENT_NOOP`, which means the candidate days existed and were all
#: already complete -- "nothing to do" and "nothing to do it to" send an operator different places.
NO_WINDOW: Final = "no_window"

#: The base rung was written but the day's ladder did not finish. A day in this state holds
#: indefinitely and must NOT be treated as durable
#: (`vegetation/backfill.py::VEGETATION_BACKFILL_DURABLE_OUTCOMES`).
INCOMPLETE_AFTER_WRITE: Final = "incomplete_after_write"

#: One named input (an archive) a turn read passed every archive-safety control. NOT a turn-level
#: word: `botanical_occurrences` is the first writer whose turn can process several independently
#: admitted inputs in one pass, each with its own accept/reject verdict, rather than one calendar
#: day -- so this and its three siblings below name a per-input state that rides alongside whatever
#: the turn as a whole reports (`BLOCKED_BY_QUARANTINE`, `PUBLISHED`, ...), not instead of it.
RELEASE_ACCEPTED: Final = "release_accepted"

#: One named input a turn read failed at least one archive-safety control (path traversal, a
#: missing required member, an oversized member, ...) and was never opened for its rows. Distinct
#: from `RELEASE_QUARANTINED` below: this is the archive-safety verdict alone, before any row is read.
RELEASE_REJECTED: Final = "release_rejected"

#: One named input a turn read was `RELEASE_REJECTED` (or could not resolve its required members),
#: so nothing from it was read into the published generation. Spelled differently from `blocked`
#: (`BLOCKED_BY_QUARANTINE`, reused from `LANE_DAY_OUTCOMES` for the TURN-level state where every
#: input this turn read was refused) because a turn can quarantine one input and still publish the
#: others; the turn-level and per-input words must be able to disagree.
RELEASE_QUARANTINED: Final = "release_quarantined"

#: One accepted input's row cap (core or extension) truncated its population before every row was
#: read. The generation still publishes from what was read; this word is how an operator learns the
#: published population is a bounded PREFIX of the source's, not its whole population.
RELEASE_PARTIAL: Final = "release_partial"

#: One accepted input's every row was read before any cap or clock stopped it. The per-input partner
#: of `COMPLETE`, at a different grain: `COMPLETE` is RUN-level ("every day this turn touched
#: settled"); this is per-input ("this one archive's population was read in full").
RELEASE_COMPLETE: Final = "release_complete"

#: Words a bounded direct-writer turn may report ON TOP OF the five lane-day words.
DIRECT_ONLY_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        TIME_BUDGET_EXHAUSTED,
        REQUEST_BUDGET_EXHAUSTED,
        SOURCE_UNSETTLED,
        NOT_YET_SETTLED,
        BBOX_UNCONFIGURED,
        UNCHANGED,
        IDEMPOTENT_NOOP,
        PUBLISHED,
        COMPLETE,
        INCOMPLETE,
        NO_WRITABLE_OBSERVATIONS,
        NO_WINDOW,
        LOCK_CONTENDED,
        UNRESOLVED_DAY_BUDGET_EXHAUSTED,
        INCOMPLETE_AFTER_WRITE,
        RELEASE_ACCEPTED,
        RELEASE_REJECTED,
        RELEASE_QUARANTINED,
        RELEASE_PARTIAL,
        RELEASE_COMPLETE,
    }
)

#: THE declared set. A writer reporting a word outside this fails
#: `test_every_declared_outcome_is_in_the_shared_vocabulary`, which is the whole point: one monitor
#: has to be able to read all eleven writers, and it can only do that against an enumerable set.
DIRECT_TURN_OUTCOMES: Final[frozenset[str]] = LANE_DAY_OUTCOMES | DIRECT_ONLY_OUTCOMES

#: Pairs that name the SAME state in two words. Not a rename list -- a rename changes an observable
#: field on a running lane and is an owner call -- but a declaration, so a monitor written against
#: one spelling knows to accept the other, and so a twelfth writer knows the pair exists and picks
#: the first element. Kept deliberately small: two words are synonyms only when substituting either
#: leaves every claim in the record true.
KNOWN_SYNONYMS: Final[Mapping[str, str]] = {
    LOCK_CONTENDED: "contended",
}

# --------------------------------------------------------------------------------------------
# Failure policy on a malformed upstream record
# --------------------------------------------------------------------------------------------

BadRecordPolicy = Literal["refuse_whole_release", "skip_and_count", "no_such_defect"]

#: One bad record refuses the entire release/snapshot/day. Nothing is published and the previously
#: published version keeps serving.
REFUSE_WHOLE_RELEASE: Final[BadRecordPolicy] = "refuse_whole_release"

#: One bad record is dropped, counted on the turn's report, and the rest of the population
#: publishes. The COUNT is the contract: a drop nobody counted is a silent loss.
SKIP_AND_COUNT: Final[BadRecordPolicy] = "skip_and_count"

#: This writer's records cannot carry this defect at all -- most often because it publishes
#: cell-keyed values with no geometry column to be malformed. Distinct from either policy, because
#: "we refuse this" and "this cannot happen here" are different facts and only one of them needs
#: revisiting when the schema grows a geometry.
NO_SUCH_DEFECT: Final[BadRecordPolicy] = "no_such_defect"

BAD_RECORD_POLICIES: Final[frozenset[str]] = frozenset({REFUSE_WHOLE_RELEASE, SKIP_AND_COUNT, NO_SUCH_DEFECT})

# --------------------------------------------------------------------------------------------
# Unset INGEST_BBOX
# --------------------------------------------------------------------------------------------

UnconfiguredBboxPolicy = Literal["refuse", "skip_turn", "usage_error", "not_bbox_bounded"]

#: Raise. The turn fails and an operator sees it. For a lane that cannot skip without leaving a
#: version serving under a coverage claim it never re-proved.
REFUSE_UNCONFIGURED_BBOX: Final[UnconfiguredBboxPolicy] = "refuse"

#: Report a `bbox_unconfigured` no-op and exit zero. For a lane bounded twice, where an unset
#: envelope would WIDEN the query rather than leave it unbounded-but-honest.
SKIP_TURN_ON_UNCONFIGURED_BBOX: Final[UnconfiguredBboxPolicy] = "skip_turn"

#: `argparse.ArgumentParser.error`, i.e. exit 2 with a usage message before the turn starts. A THIRD
#: answer, held by exactly one writer. It is the earliest and loudest of the three, but it is also
#: the only one that cannot be told apart from an operator typo by an exit code, which is why no
#: other writer adopted it.
USAGE_ERROR_ON_UNCONFIGURED_BBOX: Final[UnconfiguredBboxPolicy] = "usage_error"

#: The writer takes no bbox: its support is a pinned lattice from the spatial dimension, or a
#: national release. See the module docstring.
NOT_BBOX_BOUNDED: Final[UnconfiguredBboxPolicy] = "not_bbox_bounded"

UNCONFIGURED_BBOX_POLICIES: Final[frozenset[str]] = frozenset(
    {
        REFUSE_UNCONFIGURED_BBOX,
        SKIP_TURN_ON_UNCONFIGURED_BBOX,
        USAGE_ERROR_ON_UNCONFIGURED_BBOX,
        NOT_BBOX_BOUNDED,
    }
)

# --------------------------------------------------------------------------------------------
# CLI surface
# --------------------------------------------------------------------------------------------

#: Every forward writer exposes these, with no exemption available. `--max-days` is how a turn is
#: bounded at all and `--run-id` is how its records are correlated across eleven emitters; a writer
#: missing either is not operable by the same runbook as its siblings.
REQUIRED_FLAGS: Final[frozenset[str]] = frozenset({"--max-days", "--run-id"})

#: Knobs whose presence depends on the lane's nature. Each is either exposed or named in the
#: writer's own `flags_absent_on_purpose` WITH A REASON -- default-deny, the same convention
#: `tests/direct/test_direct_package_registration.py::PENDING_REGISTRATION` uses, so a knob dropped
#: by accident fails a test on the day it is dropped instead of being discovered by a crash.
#:
#: `--max-records` and `--max-records-per-day` are BOTH listed although no writer has both: they are
#: two spellings of a record ceiling with genuinely different scopes (a whole-roster poll versus one
#: exact upstream day), so each writer that has one must say why it does not have the other. Listing
#: only one spelling would have made the naming difference invisible, which is how it drifted.
NATURE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "--bbox",
        "--product",
        "--time-budget-seconds",
        "--retry-attempts",
        "--retry-base-seconds",
        "--retry-max-seconds",
        "--contention-timeout-seconds",
        "--max-records",
        "--max-records-per-day",
    }
)

CONTRACT_FLAGS: Final[frozenset[str]] = REQUIRED_FLAGS | NATURE_FLAGS


class DirectWriterContractError(ValueError):
    """Raised when a writer declares a contract this package does not define."""


@dataclass(frozen=True, slots=True)
class DirectWriterContract:
    """What one `pipeline/direct` writer declares about itself, so eleven can be read as one table.

    Declared beside the writer's own `parser()` rather than in a central registry HERE, so that a
    policy change and its declaration are one diff and cannot land apart. The table is assembled by
    walking the packages (`tests/direct/test_direct_writer_contract.py`), default-deny: a writer
    that declares nothing fails rather than being silently omitted.

    `policy_basis` is not decoration. Every field above it is a choice with a defensible reason, and
    a reason that is not written down decays into "that is how the sibling I copied did it" within
    one pass. An entry whose basis could be pasted onto any other writer has stopped saying anything.
    """

    slug: str
    identity_defect: BadRecordPolicy
    geometry_defect: BadRecordPolicy
    unconfigured_bbox: UnconfiguredBboxPolicy
    turn_outcomes: frozenset[str]
    flags_absent_on_purpose: Mapping[str, str]
    policy_basis: str

    def __post_init__(self) -> None:
        """Refuse a contract naming a policy, outcome or flag this module does not define.

        Checked at import, not at test time, so a writer that mistypes its own declaration fails the
        moment anything imports it -- including production -- rather than only under pytest.
        """
        if self.identity_defect not in BAD_RECORD_POLICIES:
            raise DirectWriterContractError(f"{self.slug}: unknown identity-defect policy {self.identity_defect!r}")
        if self.geometry_defect not in BAD_RECORD_POLICIES:
            raise DirectWriterContractError(f"{self.slug}: unknown geometry-defect policy {self.geometry_defect!r}")
        if self.unconfigured_bbox not in UNCONFIGURED_BBOX_POLICIES:
            raise DirectWriterContractError(f"{self.slug}: unknown unset-bbox policy {self.unconfigured_bbox!r}")
        undeclared = sorted(self.turn_outcomes - DIRECT_TURN_OUTCOMES)
        if undeclared:
            raise DirectWriterContractError(
                f"{self.slug} declares outcome(s) {undeclared} that are not in DIRECT_TURN_OUTCOMES; add the word "
                "to pipeline/direct/__init__.py with what it means, or use the existing word that means it"
            )
        unknown_flags = sorted(set(self.flags_absent_on_purpose) - CONTRACT_FLAGS)
        if unknown_flags:
            raise DirectWriterContractError(
                f"{self.slug} excuses flag(s) {unknown_flags} that no writer is expected to have; an exemption "
                "from a requirement that does not exist reads as a justified gap and is not one"
            )
        blank = sorted(flag for flag, reason in self.flags_absent_on_purpose.items() if not reason.strip())
        if blank:
            raise DirectWriterContractError(f"{self.slug} excuses flag(s) {blank} with no reason")
        required_but_excused = sorted(set(self.flags_absent_on_purpose) & REQUIRED_FLAGS)
        if required_but_excused:
            raise DirectWriterContractError(
                f"{self.slug} excuses required flag(s) {required_but_excused}; REQUIRED_FLAGS has no exemption"
            )
        if not self.policy_basis.strip():
            raise DirectWriterContractError(f"{self.slug} declares no policy basis")


__all__ = [
    "BAD_RECORD_POLICIES",
    "BBOX_UNCONFIGURED",
    "COMPLETE",
    "CONTRACT_FLAGS",
    "DIRECT_ONLY_OUTCOMES",
    "DIRECT_TURN_OUTCOMES",
    "IDEMPOTENT_NOOP",
    "INCOMPLETE",
    "INCOMPLETE_AFTER_WRITE",
    "KNOWN_SYNONYMS",
    "LANE_DAY_OUTCOMES",
    "LOCK_CONTENDED",
    "NATURE_FLAGS",
    "NOT_BBOX_BOUNDED",
    "NOT_YET_SETTLED",
    "NO_SUCH_DEFECT",
    "NO_WINDOW",
    "NO_WRITABLE_OBSERVATIONS",
    "PUBLISHED",
    "REFUSE_UNCONFIGURED_BBOX",
    "REFUSE_WHOLE_RELEASE",
    "RELEASE_ACCEPTED",
    "RELEASE_COMPLETE",
    "RELEASE_PARTIAL",
    "RELEASE_QUARANTINED",
    "RELEASE_REJECTED",
    "REQUEST_BUDGET_EXHAUSTED",
    "REQUIRED_FLAGS",
    "SKIP_AND_COUNT",
    "SKIP_TURN_ON_UNCONFIGURED_BBOX",
    "SOURCE_UNSETTLED",
    "TIME_BUDGET_EXHAUSTED",
    "UNCHANGED",
    "UNCONFIGURED_BBOX_POLICIES",
    "UNRESOLVED_DAY_BUDGET_EXHAUSTED",
    "USAGE_ERROR_ON_UNCONFIGURED_BBOX",
    "BadRecordPolicy",
    "DirectWriterContract",
    "DirectWriterContractError",
    "UnconfiguredBboxPolicy",
]
