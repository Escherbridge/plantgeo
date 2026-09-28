---
type: Implementation Plan
title: Plant-suitability serving plane — preconditions and grill, rules of record v1, climate normals, region-scale curation, species tables and per-cell lane, serving behind verified access, validation, closure
tags: [plant_suitability_plane_20260927]
resource: ./spec.md
---

# Implementation Plan: Plant-suitability serving plane

## Overview

Eight phases. Phases 1 to 6 map to the brief's items A to F. Phase 7 is the item G closure, and
Phase 0 holds the preconditions and the one grill round.

    P0 preconditions + terms + grill (Q1–Q4)
      → P1 (A) rules of record v1: the seven FROZEN issues closed in an oracle; golden fixture
      → P2 (B) climate normals: regions lane (pulled forward) → hardiness → frost-free bias surface
               → D-PRISM → precipitation
      → P3 (C) curation at region scale: reference schema freeze → v0 port → per-region batches
      → P4 (D) species tables + rule engine + guild pools → plant-reference release
               → plant-suitability build (banded)
      → P5 (E) serving: agri tool behind verified access, TS reader/slider/legend/panel, flags
      → P6 (F) validation: recompute, transcription, watch list, EVT (blocked), community (blocked)
      → P7 (G) closure: risks and assumptions re-checked, supersession record, RUNBOOK, tracks flip

| phase | ships (all dark until its owner gate) | owner gate | adversarial review at the boundary (recorded in `metadata.json` `reviews`) |
|---|---|---|---|
| 0 | the terms record, pins, grill answers, the soil C1 amendment request | PG-0 (grill answers) | `oh-my-claudecode:critic` (opus), prompted to refute the spec |
| 1 | `docs/lanes/plant-suitability.md` (rules of record v1) and the golden sample fixture | none (no production write) | independent recompute reviewer (opus) + evidence/transcription reviewer (opus) |
| 2 | `plant-curation-regions`, `climate-normals-hardiness`, `climate-normals-frost-free`, and `climate-normals-precipitation` once cleared | PG-1, PG-2, PG-3, PG-4 | `/code-review high` per package push; terms review by `oh-my-claudecode:document-specialist`; independent station recompute |
| 3 | the `plant-reference` schema freeze, guide rows, quote check, v0 port, region batches | PG-5 (raw documents to the object store) | evidence/transcription reviewer per batch; `oh-my-claudecode:critic` on the region readings |
| 4 | the species streams, rule engine, guild pools, the `plant-reference` release, the `plant-suitability` lane | PG-6 (reference release), PG-7 (plane publish) | `/code-review high` + independent recompute (0 mismatches) |
| 5 | the agri tool, the access gate, TS serving, flags | PG-8 (reads flag), PG-9 (tool flag + access predicate live) | `/code-review high` + `/security-review` + `oh-my-claudecode:qa-tester` browser evidence + agent-honesty reviewer |
| 6 | validation reports | none | `oh-my-claudecode:scientist` (method) + `oh-my-claudecode:critic` |
| 7 | the closure record | PG-10 (status flips on the superseded tracks, the owner's call) | `oh-my-claudecode:code-reviewer` closure verdict |

### Working rules (binding on every phase)

- **Authors never verify their own work.** An authoring slice runs no tests, lint, builds, probes
  or git. The coordinator (`i-coordinator`) launches one monitor sweep (`i-monitor`) per push and a
  separate reviewer per phase. A phase with no recorded verdict is unreviewed, not done.
- **One sweep at the end of each push**, run once after all edits:
  - Python: `services/agri-data-service/scripts/check.py` (ruff, mypy, pytest) with `PROJ_LIB`,
    `PROJ_DATA` and `GDAL_DATA` unset.
  - Web, when `src/**` changed: `npm test`, typecheck, build, `npm run check:data-boundary`.
  - Then the receipt refresh (`QUALITY_RECEIPT.json`).
- **Push small and often.** One step per push, and a closure review under 500 lines. The push list
  is at the end.
- **Production mutations wait for an explicit owner go.** These are object-store writes, publishes,
  Railway variables and paid upstream spend. The gates are PG-n below.
- **Never run PlantGeo locally.** Builds are operator one-offs. Prototype and oracle re-runs use
  the gitignored `.omc` venv, as v0 did.
- **Shared checkout.** Commit with an explicit pathspec, and only when the service tree's
  uncommitted changes are exactly that commit's files. Never `git add conductor/` wholesale;
  `conductor/RUNBOOK.md` is shared with other sessions.
- **Partitions are hypotheses.** Before any launch, `git rev-parse HEAD` must equal `56467bd4`, or
  every `owns` list is re-grepped.
- **TDD.** Each code task is one Red → Green → Refactor cycle. The author writes the test and the
  code; the monitor sweep runs them.

---

## Phase 0: Preconditions, terms and the grill

Goal: settle the facts the design rests on before any code: terms, pins, the soil lane's state and
the owner's answers.

Slices: `i-coordinator`. Reviewer: `oh-my-claudecode:critic` (opus). Owner gate: **PG-0**.

- [ ] Task: Re-verify HEAD against `computed_at_commit` (`56467bd4`). Re-grep every partition
      `owns` entry and record the drift in `evidence/phase0.md`.
- [ ] Task: Read-only census of the soil precondition.
  - Is `soil-properties` published (its availability pointer, or the soil track's P3 and P4
    records)?
  - Is the SSURGO survey-properties follow-up (soil DESIGN §4, F1) scheduled?
  - Record both with their evidence. This is a read, never a mutation.
- [ ] Task: Terms capture (`oh-my-claudecode:document-specialist`, sonnet), capture-then-scrt into
      `.omc/research/plant-suitability-plane-terms-20260927/`. Each file carries a URL, timestamp
      and intent header.
  - Capture verbatim: USDA PHZM 2023 terms and disclaimer; PRISM normals terms; USDA PLANTS
    characteristics and bulk-download terms; NCEI 1991–2020 normals terms; NWPL 2022 terms.
  - Capture the reuse terms of every v0 guide document with a copyright line (Xerces, OSU, ODFW).
    This seeds the `licence` column.
  - Acceptance: one row per source (source, URL, verbatim clause, verdict, verifier). No verdict
    without a verbatim clause.
- [ ] Task: Pins probe (orchestrator-run, read-only HTTP).
  - PHZM 2023 grid file names, Last-Modified and ETag.
  - The NCEI normals station inventory for WA/OR/ID with `ANN-TMIN-PRBGSL-T32FP50` present, as a
    count.
  - PLANTS characteristics download identity (date, size).
  - Acceptance: the counts and pins go into `evidence/phase0.md`. No number in the spec is taken
    as given where a probe can measure it.
- [ ] Task: One grill round (spec §3.3 Q1–Q4) through the parent session's `AskUserQuestion`,
      with the defaults shown.
  - Record the answers in `metadata.json` `owner_answers`.
  - Anything unasked stays a stated assumption (spec §9).
- [ ] Task: Hand the soil orchestrator a request for a CONTRACT C1 amendment: this track reads the
      `soil-properties` z9 rung (spec S8, §11.3).
  - This is a handoff note in `evidence/phase0.md`. **Never edit `.omc/soil-data-plane-20260927/`.**
- [ ] Verification: `oh-my-claudecode:critic` reads spec + plan + `metadata.json`, prompted to
      refute the grain, row budget, access gate and framing guard. Record the verdict and
      disposition table in `evidence/reviews.md`. [checkpoint marker]

Acceptance: PG-0 answered, terms rows recorded, the precondition census recorded, and the critic
verdict recorded.

## Phase 1 (A): Rules of record v1 — close the seven FROZEN open issues

Goal: the v0 rules, amended for issues 1 to 5 and 7 (issue 6 goes to Phase 2), are frozen as the
rules of record v1. A **v1 oracle** re-run on the three pilots becomes the reference the production
engine must reproduce.

Slices: `a1-rules-of-record` (opus). Reviewers: an independent recompute reviewer (opus, separate
context) and an evidence/transcription reviewer (opus). No owner gate.

The oracle work happens in a **copy**, `.omc/research/plant-suitability-v1-oracle-20260927/`
(gitignored, unpartitioned). The frozen v0 directory is never edited.

- [ ] Task: FR-1 band inheritance. (TDD in the oracle: a test that *Leymus triticoides* on the Boise
      District Appendix A row inherits `saline_alkali_or_poor_drainage`; implement in the oracle's
      `row_applicability.py`; refactor.)
  - Acceptance: the Boise restoration top-3 slots resting only on unbanded in-region rows are
    recounted (the baseline is 7,009 of 7,011).
- [ ] Task: FR-2. Add `origin_scope` to both curated lists. Change `decide_origin` so `range_wide`
      never suppresses "not recorded in <state> (PLANTS)". Delete `RANGE_WIDE_NATIVE_PATTERN`.
      (TDD: *Prunus pumila* at Boise is flagged; implement; refactor.)
  - Acceptance: `not_recorded_unflagged` reads 0.
- [ ] Task: FR-3, the Bend region reading. TN 2A, TN PM-50 and the OR/WA guide go `in_region` for
      Bend; TN PM-50 confidence is recorded per pilot. (TDD on the aligned `in_region` lists;
      implement; refactor.)
  - Acceptance: Bend picks from those documents carry `in-region: yes`.
- [ ] Task: FR-4. Add the `labels_format` and `pick_definition` sentences. Mark *Cotoneaster
      integerrimus* (TN 2A Tables 5 and 6) `nativity_conflict`, settled by the C4 rule. (TDD:
      assertions on the metadata strings; implement.)
- [ ] Task: FR-5 reconciliation. Explain 56 against 70 unscorable names and ~1,000 against 305
      unflagged slots. Write the explanation to `evidence/phase1.md`.
- [ ] Task: Write the rules of record v1 as `docs/lanes/plant-suitability.md` §"Rules of record".
  - Port `GUILD_RULES.md` §0–§8 with the FR-1 to FR-4 amendments.
  - Keep the evidence labels (O-, C-, R-, D-, F-, DI) and the refuted-claims list.
  - Carry the framing text (OD-12) and the label grammar.
- [ ] Task: Golden sample fixture
      `services/agri-data-service/tests/fixtures/plant_suitability_golden.json`.
  - Take a stratified sample from the oracle: 100 cells per pilot, covering every guild status,
    uncertain frost-free picks, neighbour-only picks and a withheld cell.
  - Store the inputs (the site vector plus the region pool ids) and the expected per-guild columns.
    The full 7,500-cell parity is the reviewer's job, not a unit test's.
- [ ] Verification: the independent recompute reviewer recomputes all 7,500 oracle cells from the
      oracle inputs and expects **0 mismatches**.
  - It reports the watch-list table (v0 C11) before and after. The three enforced rules must read
    0; `not_recorded_unflagged` must read 0.
  - The evidence reviewer re-reads every curated row changed by FR-2, FR-3 and FR-4 against its
    source.
  - Verdicts go to `metadata.json` `reviews.phase1`. [checkpoint marker]

Push P1: `docs/lanes/plant-suitability.md` plus the golden fixture. This is documentation and data
only, well under 500 lines of prose.

## Phase 2 (B): Climate normals at a usable grain (and the region lane they need)

Goal: cleared, per-cell hardiness, frost-free and precipitation normals at 0.01°. The frost-free
bias becomes a **regional surface** rather than three stations.

Slices: `c1-curation-regions` (sonnet; the C item, pulled forward because the bias surface needs
the MLRA per cell), `b1-hardiness` (sonnet), `b2-frost-free` (opus), `b3-precipitation` (**no
partition** until D-PRISM), and `d5-registrations` (sonnet, serialized).

Reviewers: `/code-review high` per push; `oh-my-claudecode:document-specialist` for terms; an
independent station recompute.

- [ ] Task: Re-check at phase start that no lane provides normals. Grep
      `phzm|hardiness|prism|frost[-_ ]free|normals` over `services/agri-data-service/src` and
      `src/`. Check the config-driven track's landed state. Record the result.
  - The planning finding: none. `climate-field-*` is a 1° POWER lattice; `meteorology-*` is 0.25°
    and not landed.
- [ ] Task: `plant-curation-regions` lane (c1): cell → `state`, `mlra`, `nwpl_region` (+ basis),
      `curation_region_id` for every 0.01° cell of WA/OR/ID. The boundary sources are pinned and
      their terms recorded in P0.
  - (TDD: v0's pilot MLRA shares from `pilot_mlra_sda.json` are reproduced; implement
    capture/prepare/publish/verify/maintain/retract mirroring `pipeline/direct/soil_properties/`;
    refactor.)
  - Acceptance: FR-9, and the MLRA list count is reported.
- [ ] Task: Register `plant-curation-regions` (d5): a `_REFERENCE_DATA_REGISTRATIONS` entry, the
      `WRITER_MODULES` row, the `gap_repair_contract.py` static excuse, and the
      `EXPECTED_REGISTERED_CENSUS_LANES` bump. (TDD: the registration-shell test mirrors
      `tests/direct/soil_properties/test_registration_shell.py`.)
- [ ] Task: `climate-normals-hardiness` lane (b1): PHZM 2023 30″ pinned capture.
  - The prepare step centre-samples into 0.01° cells (A7), writing `mean_annual_extreme_min_c` and
    `hardiness_zone`.
  - The served metadata carries the attribution and disclaimer text verbatim.
  - (TDD: the three v0 ZIP cells, 83702 7a, 97330 8b and 97701 6b, match; implement; refactor.)
  - Registration via d5.
- [ ] Task: **PG-1**. The owner go to publish `climate-normals-hardiness`. The operator runs
      capture, prepare, publish and verify, and the evidence is recorded.
- [ ] Task: Decision D-FROST applies the Q3 answer: base-field source, box list, the spend figure
      re-measured from the P0 station and area probes, and the month split. Record in
      `evidence/phase2.md`.
- [ ] Task: `climate-normals-frost-free` lane (b2).
  - The base-field capture is bounded, resumable and metered, with the Open-Meteo weight rule (≥ 1.0
    per location per request, config-driven O10) when the source is Open-Meteo.
  - Capture the NCEI station inventory.
  - Per-station bias = base cell − `ANN-TMIN-PRBGSL-T32FP50`.
  - Regional surface: the MLRA median when n ≥ 3, else the state median (A9). Write
    `frost_free_days_base`, `frost_free_bias_days`, `frost_free_bias_basis`.
  - (TDD: USW00024131, USC00351862 and USC00350694 reproduce 15, 35 and 44.5 d on the v0 inputs; a
    region with n < 3 falls back to the state median; implement; refactor.)
  - Registration via d5.
- [ ] Task: **PG-2**. The owner go for the base-field spend and the frost-free publish.
- [ ] Task: Decision **D-PRISM** (FR-8), from the P0 terms record. Serve PRISM or do not.
  - If not cleared, evaluate nClimGrid monthly, Daymet v4 and WorldClim 2.1 (the wrong period)
    against NCEI station normals, as v0's `QUALITY.md` did. Their terms are **not checked** until
    P0 records them.
  - The owner picks, and the pick is recorded.
- [ ] Task: `climate-normals-precipitation` lane (b3; **partitioned only after D-PRISM**):
      `precip_mean_mm` and `precip_p20_mm`. p20 comes from the source's own annual totals, never
      from ERA5 scaling. (TDD: station comparison within the tolerance D-PRISM records; implement;
      refactor.)
- [ ] Task: **PG-3**. The owner go to publish precipitation. **PG-4** covers the regions lane
      publish. It can run any time after its review.
- [ ] Verification: an independent reviewer recomputes the bias for 20 randomly drawn stations and
      the regional medians. `/code-review high` verdicts are recorded per push. The document-
      specialist confirms each served attribution string matches the terms record.
      [checkpoint marker]

Acceptance: FR-6, FR-7, FR-8 and FR-9 met. No cell's fail-soft band comes from a three-station
constant.

## Phase 3 (C): Curation at region scale

Goal: a governed, re-verifiable curation dataset covering every (curation region × guild) in WA, OR
and ID. Gaps are shown, never filled.

Slices: `c2-reference-core` (opus), then `c3-curation-batches` (sonnet, sequential, one batch per
push). Reviewers: the evidence/transcription reviewer (opus) per batch, and
`oh-my-claudecode:critic` on the region readings. Owner gate: **PG-5** (raw documents to the object
store).

- [ ] Task: **Schema freeze** (c2). `warehouse/schemas/plant_reference.py` declares all six
      `plant-reference` streams (spec §4.2), following the `warehouse/schemas/botanical_occurrences.py`
      one-module-one-release-set precedent. (TDD: the schema tests pin every column and its
      nullability; implement.)
  - After this push the schema is frozen for Phases 3 and 4. A change needs a recorded amendment.
- [ ] Task: Guide-rows loader and quote check (c2), ported from v0's `steppe_quote_check.py` and
      the westside rules.
  - A quote passes as exact, or as ordered tokens within a 2 × tokens + 10 span.
  - `quote_kind = table row (cells joined)` is checked for token presence only.
  - The build refuses on any failure and collects every failure in one report.
  - (TDD: a broken quote fixture fails the build; a two-page quote passes; implement; refactor.)
- [ ] Task: Port the 1,458 v0 rows (747 steppe + 711 westside) to one JSONL per document (c2), with
      `origin_scope`, `superseded_by`, `licence` and `redistribution`. Carry the Phase-1 edits.
      Add `plant-guide-applicability` rows per (row, region), with basis and confidence, from each
      document's stated geography.
  - Acceptance: `verify` reports 0 quote failures.
- [ ] Task: **PG-5**. The owner go to upload the raw documents to the object store, content-
      addressed by sha256 (A11). This includes the Internet Archive captures, whose origin URL and
      capture URL are both recorded.
- [ ] Task: The region coverage matrix (FR-10). Compute (curation region × guild) → in-region /
      neighbour / none from the applicability rows. Publish it into the release metadata and
      `evidence/phase3.md`.
  - WA is assessed from the documents' stated geography first (the OR/WA guide covers Washington;
    Xerces "Western Oregon & Washington" and "Maritime Northwest" name WA regions).
- [ ] Task: Curation batches (c3), **one push each**. Each batch covers a bounded source search
      with a logged search, transcription, applicability and a gap statement.
  - Batch order:
    1. Oregon east of the crest, closing the Bend reading at region scale;
    2. Idaho;
    3. Oregon west side;
    4. Washington east;
    5. Washington west.
  - (TDD per batch: the quote check and the applicability tests over the new files.)
  - Acceptance per batch: every new row verified; the matrix updated; no row with a fire-effect
    phrase.
- [ ] Task: West-side greenstrip gap statement (FR-10). Record the searched-source list (v0
      `SOURCES_westside.md` §"Greenstrip for corvallis" plus any new search) as release metadata.
      Assert that no west-side region carries a greenstrip pool.
- [ ] Verification: per batch, the evidence/transcription reviewer re-finds every quote on its
      cited page of the stored bytes and re-reads each applicability basis. At phase end,
      `oh-my-claudecode:critic` refutes the region readings (the neighbour relations and the
      east-of-crest mapping). [checkpoint marker]

Acceptance: FR-10 and FR-11 met. Every region the FR-9 census lists appears in the matrix.

## Phase 4 (D): Species tables, rule engine and the per-cell lane

Goal: the `plant-reference` release published, and the `plant-suitability` lane built and
published. Species tables are static streams, and the per-cell lane is derived.

Slices: `d1-species-tables` (sonnet), `d2-rule-engine` (opus), `d3-guild-pools-and-release`
(opus), `d4-suitability-lane` (opus), `d5-registrations` (sonnet, serialized). Reviewers:
`/code-review high` per push, and the independent recompute agent. Owner gates: **PG-6**, **PG-7**.

- [ ] Task: `plant-species-envelope` (d1): pinned PLANTS characteristics capture, the three-tier
      trait resolution (`GUILD_RULES.md` §3) and the C1 infraspecific merge.
  - `taxon_key = usda-plants:<symbol>`. Genus-only names are never expanded.
  - (TDD: parity with v0 `species_traits.py` for the pilot pool taxa; the tie-fails-build case;
    the inverted-range assertion; implement; refactor.)
- [ ] Task: `plant-wetland-ratings` (d1): NWPL 2022 with the C3 resolutions, `NWPL_CHECKED_ABSENT`
      and the wetland-genus assertion. (TDD: *Cornus sericea* → *C. alba* FACW; an unrated
      *Salix* fails the build unless it is on the checked-absent list.)
- [ ] Task: `plant-exclusions` (d1): OR 2025 (PDF transcription with quotes), plus WA and ID
      editions from the P0 and Phase 3 search, using the v0 `exclusion_match.py` rules. (TDD:
      genus rows, infraspecific listings and hybrid formulas.)
- [ ] Task: The rule engine `warehouse/plant_suitability/` (d2), pure.
  - It covers the 13 axes (pass / fail / unknown), `source_applicability` with FR-1, and the
    frost-free fail-soft band per cell from `frost_free_bias_days`.
  - Ranking: `LEADING_KEYS`, then the per-guild keys, then `robustness_margin`.
  - Labels (C9 + FR-4), with no fire field on `hedgerow_buffer`.
  - The framing guard pattern (FR-20).
  - (TDD: reproduce `tests/fixtures/plant_suitability_golden.json` exactly; implement; refactor.)
- [ ] Task: Guild pools and the release (d3). `plant-guild-pools` derivation: C4 origin with FR-2,
      in-region, carrying rows, the fire-resistance label on greenstrip and restoration only, and
      noxious removal.
  - Then the release-set verbs for the six streams: publish, verify, maintain, retract. One release
    id binds all six.
  - The build asserts that the served-facing streams hold **zero** NC-licensed rows (NFR-4).
  - (TDD: parity with v0 `guild_candidates.parquet` under the v1 rules for the three pilots.)
- [ ] Task: Register the release set and each new stream (d5), serialized.
- [ ] Task: **PG-6**. The owner go to publish the `plant-reference` release.
- [ ] Task: `plant-suitability` schema and rungs (d4): `warehouse/schemas/plant_suitability.py`
      with the §4.3 row and a `GridAggregation` using only band-safe aggregates (S3).
  - (TDD: the **A2 test**, `derive_tier` at a 0.01° base equal to the z9 pitch, is either identity
    or a refusal. On refusal, switch to the 0.005° base per S1's reversal and record it.)
- [ ] Task: `plant-suitability` build (d4). It reads the `soil-properties` z9 rung (S8; the C1
      amendment must be recorded first), the three normals lanes, `plant-curation-regions` and the
      `plant-reference` release, and computes in 0.2° latitude bands (S4).
  - Each band's RSS and evaluation count go into the build report.
  - The metadata goes into the file (A1), or into the companion stream on fallback.
  - `maintain` compares `input_release_ids` and `rules_version`.
  - (TDD: a two-band fixture equals the one-pass result; a changed input release id makes
    `maintain` report `republish`; implement; refactor.)
- [ ] Task: `docs/lanes/plant-suitability.md` §"Lane mechanics" (d4, after a1's rules section):
      grain, rungs, refresh, the declared deviation from standard §8, the input list, and the
      reproduction commands.
- [ ] Task: Precondition check (coordinator) before PG-7.
  - `soil-properties` is published.
  - The Q1 path is satisfied: survey properties published, or the owner chose the interim.
  - Precipitation is published (S12).
  - All three are recorded, or PG-7 is not asked.
- [ ] Task: **PG-7**. The owner go to publish `plant-suitability`, run by the operator.
- [ ] Verification:
  - The independent recompute agent recomputes a stratified sample of production cells (every
    band, region and guild status) from the published inputs, expecting **0 mismatches**.
  - It re-runs the 7,500 pilot cells against the Phase-1 oracle, expecting **0 mismatches**.
  - It computes the rule watch list over the whole plane; the three enforced rules read **0**.
  - `/code-review high` verdicts are recorded. [checkpoint marker]

Acceptance: FR-12 to FR-17 met.

## Phase 5 (E): Serving

Goal: the public map plane and click panel, plus the full-list agent tool behind verified access.
Everything is flag-gated and byte-identical while the flags are off.

Slices: `e1-agri-tool` (opus), `e3-access-gate` (**no partition**: the enforcement point is not
grounded), `e4-ts-tool-declaration` (sonnet), `e2-ts-serving` (opus). Reviewers:
`/code-review high`, `/security-review` (non-negotiable: access, user coordinates, licence filter),
`oh-my-claudecode:qa-tester` for browser evidence, and an agent-honesty reviewer. Owner gates:
**PG-8**, **PG-9**.

- [ ] Task: The agri reader and the `plant_candidates_at_point` tool (e1).
  - Nearest-cell selection uses the CONTRACT C2 algorithm on the 0.01 lattice.
  - Build the site vector, then run a live DuckDB join over the region's pools through
    `warehouse/plant_suitability/`.
  - Refusals use the C5.6 vocabulary. The result carries `distance_m` and `release_day`.
  - When the forwarded access context says the requester is an affiliate, NC rows are excluded.
  - `PLANT_CANDIDATES_TOOL_ENABLED` uses the C2 parse rule.
  - (TDD: kill switch off → the tool answers `reads_disabled`; the affiliate context never returns
    an NC row; the framing guard over every returned string; implement; refactor.)
- [ ] Task: The access gate (e3; partitioned at launch after a trace of the TS → agri tool path).
  - The verified predicate follows the Q4 answer at the entitlement seam
    (`src/lib/server/security/regional-intelligence-access.ts::resolveEntitlementPolicy`), per S9.
  - The tool is withheld from the model's catalogue unless the requester is verified, and the
    affiliate flag is forwarded in `server_context`.
  - (TDD: signed-in-unverified → no tool; verified non-affiliate → tool with NC rows allowed;
    affiliate → tool without NC rows.)
- [ ] Task: The TS tool declaration (e4) in `src/lib/server/services/regional-evidence-tools.ts`
      and its test.
- [ ] Task: TS map serving (e2).
  - A reader modelled on `soilgrids.ts`, a tRPC procedure, the snapshot capability (hand-spelled in
    `parquet-slider-capabilities.test.ts`), the `layer-registry.ts` entry and a `layer-legends.ts`
    legend (count ramp, hatched `no_regional_guide`, grey `site_not_scored`).
  - A `PlantSuitabilityLayer` renderer and a `PlantSuitabilityDetails` click panel (top 3 per guild
    with labels and the guild framing text).
  - `PLANT_SUITABILITY_READS_ENABLED`.
  - (TDD: the capability pinned by a hand-spelled name; the panel renders `no_regional_guide` as a
    gap statement; the legend order is coordinated with the botanical layers.)
- [ ] Task: **PG-8**. The owner go to set `PLANT_SUITABILITY_READS_ENABLED=true` on the web and
      agri services. **PG-9**. The owner go for `PLANT_CANDIDATES_TOOL_ENABLED=true` and the live
      access predicate.
- [ ] Task: Browser evidence with a standalone Playwright config under
      `evidence/browser-phase5/` against the deployed build. **Never** use the repo
      `playwright.config.ts`.
  - Capture a Boise post-fire click, a Corvallis greenstrip gap, a Bend uncertain-frost-free label,
    and z5 counts at low zoom.
  - Measure the tool's p95 (NFR-1).
- [ ] Verification:
  - `/code-review high` and `/security-review` verdicts.
  - The qa-tester browser evidence.
  - The agent-honesty reviewer confirms no fire-effect wording, that distances and `release_day`
    are present, and that an unverified user is told the full list needs verified access.
  - All recorded in `reviews.phase5`. [checkpoint marker]

Acceptance: FR-18, FR-19 and FR-20 met. With both flags off, the web and agri outputs are byte
for byte unchanged.

## Phase 6 (F): Validation

Goal: evidence that the served plane is what the rules say, that its sources say what the labels
claim, and how it compares with independent vegetation and observation data.

Slices: `f1-validation-evidence` (opus, a reviewer lane), `f2-evt-validation` (**no partition**;
the EVT lane is absent at HEAD), `f3-community-validation` (**no partition**; no community
observation layer). Reviewers: `oh-my-claudecode:scientist` (method) and
`oh-my-claudecode:critic`.

- [ ] Task: End-to-end read-back (f1). Served values through the TS reader and the agri tool equal
      the published Parquet for a stratified cell sample. (TDD: a comparison script under
      `evidence/validation/`.)
- [ ] Task: The full evidence pass (f1). Re-verify every curated row across all batches against the
      stored bytes (`verify`), and review a human-model sample of applicability bases per batch.
- [ ] Task: The plane-wide rule watch list (f1): counts per rule, per region and guild, with the top
      taxa per rule. The enforced rules must read 0; the others are reported.
- [ ] Task: Adopt the abstaining validation protocol from
      `botanical_species_recommendation_validation_20260911` as the frame for the EVT and community
      comparisons. They are reports, never filters.
- [ ] Task: LANDFIRE EVT comparison (f2): **blocked** until `vegetation_type_landfire_evt_20260918`
      publishes. Compare guild lifeform with EVT physiognomy per region.
- [ ] Task: Community observations as validation (f3): **blocked** until a community observation
      layer exists (report §5.4).
  - Presence and absence test the plane and never train it.
  - An excluded taxon observed where the plane shows candidates is an early-warning report.
  - The leakage rule (report §3) applies.
- [ ] Verification: the scientist reviews the method; the critic refutes the conclusions. Record in
      `reviews.phase6`. [checkpoint marker]

Acceptance: FR-21 met for the recompute, transcription and watch-list rows. EVT and community stay
open (P2) with their blockers named.

## Phase 7 (G): Closure — non-goals, risks, supersession

Goal: leave an honest record. Every assumption resolved or re-stated, every risk re-scored, and the
supersession recorded.

Slices: `i-coordinator`. Reviewer: `oh-my-claudecode:code-reviewer` (closure verdict). Owner gate:
**PG-10** (status flips on the superseded tracks are the owner's call).

- [ ] Task: Re-score spec §9 (assumptions) and §10 (risks) with outcomes. PRISM, curation cost,
      ERA5 grain and PLANTS coverage are each stated with their measured effect.
- [ ] Task: The supersession record (FR-22).
  - Propose status notes for `pnw_herbaria_source_admission_20260911`,
    `botanical_occurrence_parquet_lane_20260911` and
    `botanical_species_recommendation_validation_20260911`.
  - Edit none of their files without PG-10.
  - Retiring served herbaria or occurrence surfaces is a separate standard §13.1 release, not done
    here.
- [ ] Task: A lean `conductor/RUNBOOK.md` section: open state, standing rules and evidence pointers
      only (memory `plantgeo-runbook-stays-lean`). One heading, pathspec commit.
- [ ] Task: A memory note for durable facts: the soil coarse-rung consumer, the access predicate,
      the normals sources and their terms.
- [ ] Task: Flip this track's line in `conductor/tracks.md` to complete. Append-only discipline;
      other sessions edit that file.
- [ ] Verification: the code-reviewer closure verdict on the final state, recorded in
      `reviews.phase7`. [checkpoint marker]

---

## Owner gates

| gate | what the owner approves | preconditions |
|---|---|---|
| PG-0 | answers to Q1–Q4 | the Phase 0 critic verdict |
| PG-1 | publish `climate-normals-hardiness` | P0 terms row for PHZM; `/code-review high` |
| PG-2 | frost-free base-field spend + publish | D-FROST record with a measured spend |
| PG-3 | publish `climate-normals-precipitation` | the D-PRISM decision |
| PG-4 | publish `plant-curation-regions` | boundary-source terms recorded |
| PG-5 | raw guide documents to the object store | the P0 `licence` rows |
| PG-6 | publish the `plant-reference` release | Phase 3 batch reviews; zero-NC assertion |
| PG-7 | publish `plant-suitability` | soil published, Q1 path satisfied, precipitation published, recompute verdict |
| PG-8 | `PLANT_SUITABILITY_READS_ENABLED=true` (web + agri) | Phase 5 review verdicts |
| PG-9 | `PLANT_CANDIDATES_TOOL_ENABLED=true` + the live access predicate | `/security-review` verdict |
| PG-10 | status flips on the superseded tracks | the closure verdict |

## Pushes (one step each; closure review under 500 lines)

| push | contents | slice |
|---|---|---|
| P1 | rules of record v1 + golden sample | a1 |
| P2 | `plant-curation-regions` package + registration | c1, d5 |
| P3 | `climate-normals-hardiness` package + registration | b1, d5 |
| P4 | `climate-normals-frost-free` package + registration | b2, d5 |
| P5 | `climate-normals-precipitation` package + registration (after D-PRISM) | b3, d5 |
| P6 | `plant-reference` schema freeze | c2 |
| P7 | guide-rows loader + quote check | c2 |
| P8 | v0 row port + applicability rows | c2 |
| P9–P13 | one curation batch each (OR east, ID, OR west, WA east, WA west) | c3 |
| P14 | species envelope | d1 |
| P15 | wetland ratings + exclusions | d1 |
| P16 | rule engine | d2 |
| P17 | guild pools + release verbs + registration | d3, d5 |
| P18 | `plant-suitability` schema + rungs + A2 test | d4 |
| P19 | `plant-suitability` build + maintain + lane docs + registration | d4, d5 |
| P20 | agri tool | e1 |
| P21 | access gate + TS tool declaration | e3, e4 |
| P22 | TS reader + capability + registry + legend | e2 |
| P23 | renderer + click panel | e2 |
| P24 | validation read-back and reports | f1 |

A push over 500 lines of reviewable diff is split before review, never reviewed as one.
