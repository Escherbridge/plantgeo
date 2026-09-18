---
type: evidence
status: active
recorded_on: 2026-09-18
---

# Session 21: The shortwave frontier stall, and the vegetation-type track's first slice

Starts from `e54a265c` (Session 20 deployed and recorded; the frontend rebuilt SUCCESS on that
docs push and readiness returned 200). Two threads run in parallel on `fable` with disjoint
partitions; each is independently reviewed before the single sweep.

## Thread A2b — fall through past an unsettled frontier (climate walk)

The first post-deploy climate turn (13:40Z) refused `shortwave-radiation` day 2026-09-12 as
`source_unsettled`: POWER answered all 397 support cells with fill values and no later settled day
of the product is published, so the writer refused rather than governed the day absent — the
safety design working. `requests_spent 397` of 794; the ten lag-5 siblings were idempotent no-ops,
so the fan-out was shortwave's own. Two facts follow. POWER's solar edge was at least seven days
behind on 2026-09-18, so the measured six-day lag is right on average and too tight on some days:
the edge advances in bursts (four days behind on 2026-09-15, seven or more on 2026-09-18). And the
walk re-selects that same frontier day every hourly turn — `_pending_days` is newest-first and
`CLIMATE_DEFAULT_MAX_DAYS = 1` — so the ~100-day backlog beneath it never drains while the frontier
stays unpublished. The lane is meanwhile withheld as `availability_stale` and therefore excluded
from autonomous repair (`coverage_withheld`), leaving the hourly walk its only path. The change in
authoring: when the selected day returns `source_unsettled`, continue to the next older pending day
within the turn's request budget, bounded by a named skip constant, with the skipped frontier
reported and `_product_outcome` staying honest (`published` when an older day wrote). Verdict,
sweep, receipt and deployment are appended when observed.

## Thread p1a — per-rung key columns (vegetation-type track, Phase 1A)

`GridAggregation.key_columns_by_tier` (default `None`, every existing lane byte-identical — pinned
by golden digests computed before the edit and by a verbatim copy of the pre-change derivation as
an oracle), a public `grid_key_columns(strategy, tier)` resolver, construction-time refusals (every
derived rung named; a `13:` entry must equal `key_columns`; a key dropped at a rung must aggregate
`first` or `null`), per-tier grain in `_derive_grid_tier`, and `MAX_DERIVATION_ROWS` re-documented
as a per-call bound with its value unchanged. Forty tests pass on the slice's files.

**Finding that amends the spec:** measured on all 1,069 rows of the LF2025 legend (fixture
`tests/parquet/fixtures/lf2025-evt-hierarchy.csv`), the vocabulary ladder does **not** nest
functionally. `VALUE → EVT_GP` is functional; `EVT_GP → EVT_PHYS` is violated by 47 of 193
groups, including core PNW groups (645 Western Red-cedar–Western Hemlock, 632 Red Alder, 629
Western Oak Woodland, 609 Pacific Coastal Scrub, 617/618 Grassland and Steppe, 651
Alpine-Subalpine Barrens, 731 Managed Tree Plantation); `EVT_PHYS → EVT_LF` by 4 of 20 (Riparian,
Agricultural, Developed, Exotic Tree-Shrub each span Tree/Shrub/Herb); and `EVT_GP → EVT_GP_N` is
not one-to-one (codes 785 and 826). The spec's `first`-below aggregation would therefore fabricate
labels at z9 and z5. The honest form, expressible with the new field at negligible row cost, is a
joint-key ladder — `{13: (evt_code,), 9: (evt_group_code, evt_phys, evt_lifeform),
5: (evt_phys, evt_lifeform), 0: (evt_lifeform,)}` with finer codes `null` where dropped — and the
guard tests pin the measured non-nesting in both directions so a legend change fails the sweep.
FR-3 and Phase 1C are being amended accordingly before 1C starts; a read-time group-name join must
refuse the ambiguous codes rather than pick one.

## Boundaries

Neither thread promotes a QA case; the 220-case matrix is unchanged. p1b (latitude-band folding)
waits on p1a's independent verdict, per the track's sequential partitions.

**Measurement 2026-09-18T14:03Z** (three PNW cells): POWER's `ALLSKY_SFC_SW_DWN` last real day is
**2026-09-13**, fill only 09-14 through 09-17; T2M last real 09-15. The 13:40Z refusal of 2026-09-12 was
therefore a transient at POWER's daily publication boundary — the day is real twenty minutes later —
and the solar edge has now been observed at 4, ≥7 and 5 days behind within three days: burst
publication, as the fall-through assumes. The 14:40Z turn is expected to write 2026-09-12, advance
shortwave's pointer, clear `availability_stale` and return the layer to the slider; the drain then
proceeds one day per hourly turn. A2b's independent review found the code correct and bounded and
required only that the sufficiency claim be stated honestly: one skip covers an edge up to lag+1;
at lag+2 the drain stalls at two fan-outs per hour because the 794-request budget is exactly two
fan-outs — the cure for deeper jitter is a cross-turn skip, recorded as a follow-up.

**A2b verdict: APPROVE** after one wording round (230 climate + contract tests, mypy and ruff clean on
the touched files). No code defect in either pass: the walk terminates on the finite backlog with at
most `max_days + CLIMATE_UNSETTLED_FRONTIER_SKIPS` fetches; a spent 429 pause series sets
`deferred_refusal` and is never treated as a frontier; report words and executor consumers are
unchanged; the ten lag-5 siblings see no behaviour change. The residual is stated in three places
as structural, not tunable: one skip covers an edge up to lag+1, and at lag+2 the hour asks two
unsettled days and writes nothing because the 794-request budget is exactly two fan-outs; the
14:03Z reading shows the edge sat at lag+2 for under an hour, so the exposure is a transient window
per day. Follow-up recorded, not owed here: a cross-turn frontier skip. p1a's re-review is pending;
the single Python sweep runs once over both when it lands.

**p1a verdict: APPROVE** after one round (212 tests across the parquet suites, mypy clean;
byte-identity re-verified independently on the final tree for all six registered grid lanes). The
round added a chain-safety refusal (a coarser rung's key must be a key of, or `first` through, the
finer derived rung — reproduced defects at both chain links now refused by name), a registration-time
schema check that also closes a pre-existing base-key gap, an immutable hashable ladder, and a
key/aggregate overlap refusal for unladdered lanes. Three LOWs carried: a non-nested `first` cannot
be refused at declaration and its symptom (chained ≠ base, counts migrating between labels) is now
stated in the doc, with the lane-owned proof assigned to 1C's tests; a laddered strategy is not
picklable/deep-copyable (latent, no consumer); one redundant test. The chain-safety rule was added to
spec FR-4. p1b (banding) launched on its disjoint partition; the Python sweep runs over A2b + p1a.

## Receipt custody while an author lane is in flight

The receipt runner certifies one commit by three equalities: the working tree's service digest, the
`git archive`-style export of the named commit, and the digest the Linux container recomputes after
unpacking. With p1b authoring in the main checkout (`objectstore.py`, `derivation.py` modified), the
working-tree digest of `d4bb3491` (`b8ee3b1d…`) no longer equalled the committed export, and the run
refused with `committed export digest mismatch` — correct behaviour, not a defect: a receipt built
from a tree that includes unreviewed edits would certify code that is not in the commit. The route
taken is a detached worktree at `.tmp/receipt-d4bb3491` (HEAD `d4bb3491`, clean, service digest
`fd84ddec… 902`) with the runner's untracked inputs (`runbook-20260914/session6/`, the session-20
`receipt/` scripts) copied in; the attempt directories it produces are copied back as evidence. The
rule that follows: a receipt is built from a clean checkout of the certified commit whenever any
author lane shares the main working tree — the two must never race for the same digest.

**Receipt and push 2026-09-18T14:29–14:33Z.** The clean-worktree receipt for `d4bb3491` passed all
four Linux gates (format, lint, mypy, pytest) with tree digest `sha256:fd84ddec…` over 902 inputs;
the host verifier accepted it in the same clean checkout; evidence retained at
`.omc/research/runbook-20260915-session20/receipt/attempt-2c1ca762…` (stopped container kept). Committed
as `14d7f549` and pushed with `7231afbe` (A2b frontier fall-through), `ae39b7b8`/`434f674e`/`d4bb3491`
(p1a per-rung keys). Python changed, so parquet-api and job-executor rebuild; the frontend Dockerfile
also rebuilds on every push. Deployment outcome and the first post-deploy climate turn are appended
when observed.

## Thread p1b — latitude-band folding (vegetation-type track, Phase 1B)

Authored 2026-09-18 (72 tests pass on the slice's three files; six-file diff, uncommitted pending
review). One deviation from the plan, declared by the author: the band declaration is a registry in
`pipeline/parquet/derivation.py` (`LatitudeBanding(band_height_degrees, base_resolution_degrees)`,
`register_latitude_banding`, `latitude_banding`) rather than a `TierDerivation` field, because
`warehouse/parquet/tiers.py` was p1a's closed partition; default `None` keeps the whole-day path as
the literal old code. Edges are integer multiples of the height, which must be a multiple of every
per-band rung pitch and of the lane's base pitch; one membership rule `floor(lat / height)` serves
rows, part bounds and extent; z0 derives from the whole z5; `mean` and `sha256-lines` are refused at
declaration; part receipts carry latitude bounds and `read_partition_with_receipts` takes a
`latitude_interval` that fails open on unknown bounds; the bounds memo is process-local, so a
cross-process re-derivation without `base_table` reads the day whole (exact, not memory-flat) —
flagged for the reviewer. Independent adversarial review launched; verdict appended when returned.

**Deployment 2026-09-18T14:38Z.** All four Railway deployments for `14d7f549` reached SUCCESS
(parquet-api and job-executor rebuilt on the Python change; frontend and martin on the push) and
`/api/ready` returned 200, two minutes before the 14:40Z climate turn — so the first post-deploy
turn runs the A2b fall-through. Shortwave's state after that turn is appended when read.

**p1b review round 1: CHANGES-REQUIRED** (72 tests and mypy pass; ruff 27 new errors). The reviewer
refuted the author's "one membership rule": rows band through the Polars expression while extent
enumeration, the `interval()` float comparison in `_may_hold_latitudes` and the part-band assertion
use Python `math.floor`, and for non-integer band heights the two disagree at float edges inside the
platform envelope — with the recommended 0.4° band on the 0.0025° base a 24.0–24.4 lattice derives
z9 with 160 rows banded against 163 whole-day (pixel totals 19,242 vs 19,437) and marks every rung
complete. Heights 1.0 and 2.0 are exact globe-wide, which is why every exactness test (all at 1.0)
passed. Further findings: `first` is band-exact only under a one-distinct-value-per-group contract
that nothing enforces (probe: banded z0 `['V9','V4','V3']` vs whole-day `['V2','V4','V0']`); the
byte-identity oracle pinned to `HEAD` becomes a tautology at the landing commit; process-local part
bounds turn a cross-process re-derivation into a silent whole-day read that the cap cannot catch;
NaN latitudes escape as raw Polars errors. The round sent back: integer band index computed by one
engine from the z5 cell index (band = z5_index // cells_per_band) used for rows, receipts, filtering
and extent, with an envelope-wide property test per recommended (height, base) pair or a refusal of
the height; enforce `first`'s constancy for banded lanes; pin the oracle to `d4bb3491`; a typed
refusal when bounds are unknown and the day exceeds the cap; `fill_nan(None)`; ruff to zero.
Follow-ups recorded: durable per-part bounds (Parquet footer statistics or the completion marker)
before gap repair is armed for the lane; `first` constancy enforced platform-wide; the band
declaration onto `TierDerivation` when `tiers.py` reopens.

**First post-deploy climate turn 14:40Z — and a NASA POWER solar regression.** The fall-through ran
as designed: shortwave selected 2026-09-12, POWER answered all 397 cells with fill, the walk stepped
to 2026-09-11 (`unsettled_frontier_days: ["2026-09-12"]`), and 09-11 was also all-fill, so the
product's outcome stayed `source_unsettled` and nothing was written; the second fan-out met POWER
429s, the four-pause series ran out and the turn deferred (`requests_spent 807` against a 794 budget
— the overrun by the 429 re-asks is a small accounting follow-up). Shortwave therefore remains
withheld `availability_stale`. The cause is upstream, measured at 14:47–14:55Z with the lane's own URL
shape (`temporal/daily/point`, community AG, all eleven parameters, and again with two): POWER now
returns `-999` for `ALLSKY_SFC_SW_DWN` on **every day from 2026-07-01 through 2026-09-13** at 47.6/−122.3
and at the turn's own 49/−109 cell, while T2M stays real; the retained 14:03Z probe of the same cell
(`scratchpad/power-edge--122.3-47.6.json`) had real solar through 2026-09-13 with 5.4 on the 13th. The
solar parameter regressed provider-side within forty minutes (response header sources GEOSIT, MERRA2,
SYN1DEG — the CERES SYN1deg solar source). Consequences: the lane is fail-closed — an all-fill day is
refused, not governed absent, and "a later all-fill response never removes published data", so the
published 06-01→09-11 shortwave days stand; the layer stays off the slider until POWER republishes;
every hourly turn will spend two fan-outs and meet 429s for the outage's duration (the cross-turn
frontier skip follow-up would bound this). Two small reporting follow-ups: the refused-day summary
reports `fill_value_cells: 0` (forward.py:496) while the detail string carries the true 397; the
`behind_provider` horizon fields still cannot distinguish "provider revising" from "provider late".
No code change is owed for the outage itself. Raw responses retained in the scratchpad
(`power-full-0901-0913.json`, `power-0601-0819.json`, `power-two-params-0901-0913.json`).

**p1b round 2 authored** (ruff 0, mypy strict clean on both sources, 90 tests on the slice's files).
Band membership is one integer primitive: `z5_cell_index_expression` — `floor(fill_nan(lat) / 0.2)`
as Int64, defined once in Polars — and `LatitudeBanding.z5_cells_per_band: int`, band = index //
cells; `of_height(degrees, base)` converts and refuses non-multiples; receipts carry integer
`z5_cell_min/max` (the float bounds are gone); filtering is an inclusive integer compare; no `math`
import and no float comparison remain on the membership path. The author declined to refuse 0.4° or
0.2°: the residual is not banding's — `floor(lat/0.01)` and `floor(lat/0.2)` inside the tier
derivation are independent IEEE divisions that disagree at a handful of lattice edges (five z9 cells
for (0.4°, 0.0025°), nine for (0.2°, 0.01°) across 24–50 N), so a z9 cell can be opened by two bands —
and instead merges a grain row two bands both produced with aggregates mirroring the tier
derivation's, refusing `first` across a split unless constant. `first` is admitted only under a
constancy contract now enforced per band per rung, before the z0 chain and across splits
(`NonConstantFirstError` names column and group). The byte-identity oracle is pinned to
`d4bb3491` and asserts the oracle has no `latitude_banding`. Unknown bounds on a day whose parts ×
250,000 exceed the cap refuse loudly before any GET. Re-review launched to adjudicate the
merge-versus-refuse design and the associativity of every admitted aggregate.

**p1b review round 2: CHANGES-REQUIRED** (47 new tests in 5.5 s, 90 total, ruff and mypy clean;
noqa audit against `d4bb3491` clean). The merge design was upheld — verified exact for every
band-safe aggregate including all-null sum → null, Kleene `all`/`any`, typed `null`, dtype and the
z9-straddles-at-most-one-edge argument — but the membership primitive beneath it was refuted:
Polars 1.43.2 evaluates `col / 0.2` by a different path on a one-row frame than on a bulk frame
(32.8 / 0.2 → 163.99999999999997 alone, 164.0 in bulk), so 46 envelope lattice latitudes at 0.0025°
change z5 cell with frame length, a one-row part records the wrong band, its band is never
enumerated and the row vanishes with every rung marked complete (reproduced: banded z9 all-null,
whole-day z9 populated). The same length-dependence sits in the platform's `floor_to_resolution`,
a pre-existing defect that banding is the first consumer to turn into data loss. Second blocker:
the store-global bounds memo is keyed by object key and never invalidated, so another process
re-exporting a day leaves the first process enumerating the old bands, filtering every row out and
retracting all three rungs as derived-empty. Round 3 sent: exact integer membership
`round(lat / base) // cells_per_z5` used everywhere, with a one-row-versus-bulk identity test over the
whole envelope and the one-row-part regression; drop the memo and hand bounds in explicitly from the
writer's receipts; state the 250k rows/part estimate assumption and the NaN exception. Adjudicated:
merge over refuse; conftest move deferred; the tripwire re-worded in the track metadata.

**p1b round 3 authored** (ruff 0, mypy strict clean, 102 passed + 1 strict xfail). The `lat / 0.2`
expression is gone: membership is `round(lat / base) // cells_per_z5` with `cells_per_z5 =
round(0.2 / base)`, exact integers on the declared lattice; the author's test evaluates it on a
one-row and on the bulk frame for every envelope latitude at three base pitches with zero
disagreements, and the reviewer's one-row-part reproducer now records `(164, 164)` and loses no row.
The store-global memo is removed outright — `objectstore.py` knows nothing about bands again and
`ParquetWriteReceipt` is byte-identical to HEAD; `write_banded_base_day` returns explicit
`PartCellRange(sha256, z5_cell_min, z5_cell_max)` records that the caller hands to
`derive_and_write_day_tiers(part_cell_ranges=)`, the extent counts as known only when the ranges name
every part the bucket lists today, and a fetched part whose digest differs from its record is refused
by name ("another process re-exported this day in place"). One thing the slice cannot make exact,
stated by the author and pinned as a strict xfail naming the `tiers.py` owner: in the single-row-band
reproducer the z5 rung differs from whole-day because the platform's own `floor_to_resolution`
floors 32.8 to 32.6 on a one-row frame and to 32.8 on a two-row frame — the whole-day derivation is
itself frame-length dependent for such days. Third review launched to adjudicate the xfail, the
centroid-versus-origin question for `round`, and the digest check's placement.

**p1b review round 3: CHANGES-REQUIRED on one point** (59 + 43 tests pass with the strict xfail; mypy,
ruff and the noqa audit against `d4bb3491` clean; `ParquetWriteReceipt` byte-identical to HEAD; the
eight existing callers untouched; unbanded lanes byte-identical to the oracle). Independent probing
upheld the integer membership: zero one-row-versus-bulk disagreements for lattice origins at all
three pitches across 24–50 N, −50..−24 and the 0.0 edge, membership z5 equal to the tier derivation's
z5 everywhere, negatives flooring correctly; same-key/different-bytes refused by name with the bucket
byte-identical before and after; grown day → unknown extent → exact whole read. The surviving HIGH is
the last member of the round-2 class: an all-`None` range set (an all-unlocated first export) enumerates
zero bands, fetches nothing, so the digest check never runs and a located in-place re-export is
retracted as derived-empty. MEDIUM: `round(lat / base)` is unambiguous only for lattice origins —
centroids put 121 of 5,000 rows per pair in the next z5 cell and one true one-row disagreement
survives — so the writer must refuse non-origin rows (the spec's FR-6 already says origins). LOW: the
new test file grew to 20 s. Withdrawn by the reviewer: the `[float-order]` case (fixture error).
Accepted: NaN as a documented exception; the strict xfail (p1c may arm the lane; the defect misplaces
rather than loses one edge cell for a one-row frame and is confined to envelope fringes). Round 4 sent.

**p1b round 4 authored** (ruff 0, mypy strict clean, 105 passed + 1 strict xfail, new file 7.9 s). The
`return None, range(0)` early return is gone: the known-extent branch requires bounds, and every
other case takes the whole read with the fetched parts digest-checked against the ranges in hand
before banding by filter — the reviewer's all-unlocated-then-relocated scenario now refuses by name
with no rung written and no rung retracted, and the grown-day case asserts the same path. Base rows
must be lattice origins: `refuse_non_lattice_origins` (tolerance 1e-6 base-cell units, nulls exempt)
runs in `write_banded_base_day` before the first put and the contract sits in the primitive's
docstring. The one-row-versus-bulk loop runs at z5 edges, the origin below each edge and a stride
sample, asserting more than a hundred edges per pitch. Closing re-review launched on the three deltas.

**15:40Z climate turn:** identical to 14:40Z — shortwave selected 09-12 (all-fill, skipped as the
unsettled frontier), fell through to 09-11 (all-fill), `requests_spent 807`, four 429 pauses, nothing
written, backlog 104. This is the outage-duration cost named above: two fan-outs and the pause series
every hour until POWER republishes solar. The cross-turn frontier skip follow-up would let a turn
that has seen two consecutive unsettled days stand down for the hour.

**p1b verdict: APPROVE** after four rounds (62 + 43 tests with one strict xfail, mypy strict and ruff
clean, noqa audit against `d4bb3491` clean, unbanded lanes byte-identical to the oracle). The
reviewer's round-4 probes: the relocated in-place rewrite refuses by name with the bucket and every
completion marker byte-identical before and after; all-`None` ranges with matching bytes read whole
and retract honestly; all-`None` ranges over the cap refuse with zero GETs; centroids are refused at
write with nothing written and origins with 1e-12 noise pass; the sampled one-row loop still covers
all 46 latitudes from the original disagreement set. One LOW carried into the landing as a
reviewer-specified one-liner: the origins guard also runs on the derive path. Consolidated follow-ups
(recorded in the track metadata): the `tiers.py` frame-length floor defect with its strict xfail;
durable per-part z5 ranges before gap repair re-derives the lane without `base_table`; a byte bound
via `size_of`; the conftest move; `TierDerivation.band_height_degrees` when `tiers.py` reopens; NaN
as a documented exception to reconcile with the tiers fix; a `slow` marker if the suite budget
tightens. The single Python sweep runs next over A2b + p1a + p1b.
