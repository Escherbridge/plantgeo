# `pipeline/lanes/fire_perimeters/` — the fire-perimeters config-lane strategy package

A NEW package (`s-fire-perimeters`, track `config_driven_ingestion_20260926`, Phase 4): `p4-extract`
moved nothing here because fire-perimeters' modules were not shared with any non-lane caller, unlike
`evacuation_zones`/`watersheds`/`burn_severity`/`drought`. Everything below is authored fresh beside
`pipeline/direct/fire_perimeters/**`, which stays the active legacy implementation
(`executor = "legacy"` in the shipped TOML) until the G6 cut-over (plan Phase 5).

| module | what |
|---|---|
| `wfigs.py` | the strategy (`STRATEGY`): probes `editingInfo.lastEditDate`, fetches and conforms the `_Current` walk, settles and builds rows |
| `support.py` | DuckDB geometry repair to WKB — restated from `pipeline/direct/fire_perimeters/support.py`, byte-identical to ITS chain specifically (the pass-through-and-flag variant; `burn_severity/support.py`'s always-repair variant is a DIFFERENT chain — see `support.py`'s own module docstring, corrected after ring review) |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/runner/contract.py` and this package only.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_layer_import_contract.py`'s `test_lanes_do_not_import_each_other`).

## Why this strategy carries no watermark ladder, and where that costs something (H1)

The legacy `pipeline/direct/fire_perimeters/watermark.py` reads the previously published version
back out of the object store, row-count-short-circuits, and content-digests it against the fresh
fetch — all of it hand-built because `resolve_static_lane` and PostgreSQL's `max(updated_at)` gate
are what the generic runner's `static_lookup` handling and S11 rewrite rule do for every OTHER
config lane today. This module leans on both: `probe_edge` answers only "what does WFIGS' own
`editingInfo.lastEditDate` say right now" (`pipeline/runner/AGENTS.md`, "Static lookup"), and
`settle`'s `Written.source_digest` is compared against the last RECEIPT by the runner itself, never
against a re-read partition.

**But S11 is never reached on a forward turn `turn.py::_static_forward`'s day-only gate already
calls current** (corrected after ring review; an earlier revision of this file implied S11 covers
the same ground the legacy ladder did, which overstates it). The gate is `served_day >= watermark_day`
— a bare `date` comparison — so a SECOND genuine WFIGS edit landing on the SAME UTC day
`editingInfo.lastEditDate` already named is masked until the day rolls over, for up to ~24 h on an
hourly cron, with S11 never even consulted. This is the same class of gap
`docs/lanes/watersheds.md` §8 item 1 names for the sibling static-lookup lane in this track; it is
pinned here too (owner register row `static_freshness_contract`) rather than silently inherited.
See `docs/lanes/fire-perimeters.md` §8 and `wfigs.py`'s own module docstring for the full writeup,
and `tests/lanes/fire_perimeters/test_wfigs.py`'s same-UTC-day pinning test for the mechanism made
concrete.

## Known residuals (H1/H2/H3) — stated, not fixed in this slice

None of the three below can be closed from inside this dark, read-only-reviewed slice; each is
pinned here, in `wfigs.py`'s own module docstring, and in `docs/lanes/fire-perimeters.md` §8 so the
Phase 4 adversarial review and the Phase 6 QA round inherit them explicitly rather than discover
them at G6.

- **H1 — same-UTC-day re-edits are masked until rollover.** See the section just above.
- **H2 — compare parity with the legacy writer is NOT achievable as designed, and MUST NOT be
  attempted before a contract fix lands.** An earlier revision of this package's own report claimed
  `--mode forward --compare` would show zero unexplained delta because `feature_id`/`status`/
  `data_available_at` are kept byte-identical to `pipeline/direct/fire_perimeters/rows.py` — that
  claim was REFUTED on re-read and is corrected, not repeated, here. Two problems survive those
  three constants matching: (1) a full-row digest still includes `updated_at`, each writer's OWN
  capture instant, so an identical population still diffs on every row between any two independent
  fetches; (2) the two writers resolve DIFFERENT days for the same version — this strategy's day is
  `editingInfo.lastEditDate` (§7's named watermark), the legacy writer's is
  `source.fetched_at.date()` (whenever ITS turn happened to run) — and those two clocks need not
  agree, especially across a UTC-midnight boundary. The fix needs a volatile-column-aware compare
  AND day alignment, owned by neither writer alone; `--compare` should not be run for this lane
  until it exists.
- **H3 — `editingInfo.lastEditDate` has never been read against the live service, and a wrong
  assumption here is a SILENT failure mode.** `lanes/_providers/nifc-wfigs.toml` already carries a
  `TODO(s-fire-perimeters)` saying so. If the field does not behave as `probe_edge` assumes,
  `probe_edge` answers `invalid`/`blind` every turn, the gate holds the window every time (O-R3-1: a
  probe that cannot answer never fails the run), and the lane exits 0 while publishing nothing,
  forever — indistinguishable in a log from a genuinely quiet WFIGS day. `docs/lanes/
  fire-perimeters.md` §8 carries the G5-blocking live-probe checklist line and a one-paragraph
  content-digest fallback sketch (precedent: `pipeline/lanes/evacuation_zones/watermark.py`) for if
  the field proves unusable.

## Compare-step parity (G6) — REQUIRES the H2 contract fix; do not attempt before it lands

`feature_id` keeps the legacy writer's `direct:<uniqueFireIdentifier>` namespace, `status` keeps
`"published"`, and `data_available_at` stays `None` — all three byte-identical to
`pipeline/direct/fire_perimeters/rows.py`, matching three of the facts a comparison needs. They are
NOT sufficient on their own: see H2 above for the two problems (a volatile `updated_at` column, and
two writers resolving different days for the same version) that a byte-identical set of constants
cannot close.

## What this slice did not port

`pipeline/direct/fire_perimeters/forward.py`'s lane-day locking, four-rung ladder verification,
contention retries and `--republish-current` handling are the generic runner's job for a config lane
(`pipeline/runner/AGENTS.md` "Static lookup", `CA17`) and are not restated here. Geometry repair IS
ported (byte-identical to its own variant — see `support.py`'s module docstring); it is implemented
but exercised in this slice only through `tests/lanes/fire_perimeters/` unit coverage of
`plan_requests` / `probe_edge` / `content_digest` — the real DuckDB round trip over a fetched
population is untested here (Wave O rules: no test runs) and should be a named item in the Phase 4
per-lane adversarial review and the Phase 6 QA round before G6.

## Rationale relocated from `wfigs.py`'s module docstring

The module docstring itself now carries only a one-line summary and a pointer here; the sections
below are its full original text, moved verbatim so the code stays terse per this repo's
directory-doc convention. (The H1/H2/H3 residuals and the two deliberate deviations the docstring
covered are ALSO already stated above in "Why this strategy carries no watermark ladder" and
"Known residuals (H1/H2/H3)"; this section restates them in the docstring's own original words for
completeness, plus the two pieces those sections above do not cover: the "snapshot never
partitions by day" note and the legacy-casting-rules note below, UPDATED to reflect the
`_named_day` parity fix landed alongside this move.)

### Strategy key and DARK status

Strategy key `fire_perimeters.wfigs` (spec S14, §7). DARK: the shipped lane TOML still carries
`executor = "legacy"` (`lanes/fire-perimeters-direct-forward.toml`), so
`pipeline/direct/fire_perimeters/forward.py` is what the executor actually drives today, hourly at
`:10` (`pipeline/direct/AGENTS.md:1728`). This module is authored and reviewed ahead of the G6
cut-over (plan Phase 5); nothing here runs in production until that flip.

### Two deliberate deviations (left for the per-lane adversarial review and Phase 6 QA to weigh before G6)

1. **`fetch` does not send through the `client: ProviderClient` it is handed.** The adaptive,
   byte-bounded, oversized-record-skipping page walk (`ingest.wfigs.fetch_fire_perimeters_walk`) does
   not know its own page count ahead of `plan_requests` time, which is the model every other unit in
   this codebase assumes ("A request unit is the retry grain... one failed unit never discards its
   siblings", `pipeline/runner/contract.py`). `lanes/AGENTS.md` "Extracted source leaves are NOT
   strategies" names this exact tension for the modules `p4-extract` already moved and states "the
   s-* slices decide per lane what migrates onto the provider client" -- this slice's decision is NOT
   YET, for the same reason those extracted leaves stayed on `upstream_client`. The whole walk
   (`ingest.http.upstream_client` + `ingest.wfigs.fetch_fire_perimeters_walk`) runs inside `fetch` on
   its OWN already-adequate retry ladder (`ingest.wfigs.WFIGS_RETRY`, widened 2026-08-10 after the
   production throttle incident); layering the runner's per-unit ladder on top would double the
   backoff rather than improve it. `probe_edge`, by contrast, IS one send and threads the real
   `client` cleanly.
2. **Geometry repair is restated, not imported, from `pipeline/direct/fire_perimeters/support.py`.**
   `pipeline/lanes/fire_perimeters/support.py` carries the identical `ST_MakeValid` /
   `ST_CollectionExtract` chain `evacuation_zones/support.py` already restates (see that module's own
   docstring for why: a strategy may import its own `pipeline/lanes/<layer>/` package and never
   `pipeline/direct/**`, which Phase 7 deletes whole).

### A snapshot never partitions by day

`plan_requests` ignores the day(s) the runner asks about beyond passing them through on the one
unit: a `static_lookup`'s only question is "what does `_Current` look like right now", and
`probe_edge` already decided a newer version is owed before `plan_requests` is ever called
(`pipeline/runner/AGENTS.md`'s probe gate). `rows` stamps every row with whichever day the runner
resolved the turn's `day` to (the probed watermark day), exactly as
`pipeline/direct/fire_perimeters/rows.py::fire_perimeters_table` stamps `snapshot_day` from the
watermark rather than from any row's own timestamp.

### The legacy-casting-rules divergence (L1) — NARROWED by the `_named_day` parity fix

An earlier revision of this docstring described TWO small, deliberate divergences from legacy's
casting rules, both argued unreachable rather than fixed. That is now only ONE: this module has no
casting helpers that raise on an unexpected type, where legacy's `_optional_text`/`_optional_float`
do. This divergence is left as-is because it cannot be exercised: legacy's helpers read
PostgreSQL's untyped-at-rest jsonb document, where a wrong-shaped value is a real possibility worth
stopping for; this module's `perimeter` dicts come from `ingest.wfigs.parse_perimeter_collection`,
which already runs every field through
`ingest.arcgis.optional_text`/`optional_number`/`epoch_milliseconds_to_iso` -- none of which can
pass a wrong type through. Restating legacy's guards here would be dead code defending against a
shape this module's own upstream producer cannot produce; if that producer's contract ever widens,
the right fix is at that producer, not a second guard here.

The SECOND divergence this docstring used to describe -- `_named_day` continuing to the next key
on an empty string, where legacy's `publisher_named_day` STOPS the chain on any string, empty
included -- is CLOSED, not merely narrowed: `_named_day` now stops on any `str` value exactly as
legacy does, and additionally enforces legacy's strict `_ISO_DAY_PATTERN`
(`\d{4}-\d{2}-\d{2}` fullmatch) before calling `date.fromisoformat`, which otherwise also accepts
ISO week dates (`2026-W01-1`) the legacy SQL regex rejects. `tests/lanes/fire_perimeters/
test_named_day_parity.py` table-drives both functions over the same inputs so a future edit
reintroducing either divergence fails loudly here rather than at a G6 compare.

### The repair-audit keys (`GEOMETRY_REPAIRED_KEY`/`GEOMETRY_REPAIRED_AREA_CHANGE_KEY`)

Restored (L3, re-read after ring review): the trigger's `geometry_repaired: true` stamp, named
identically to `pipeline/direct/fire_perimeters/rows.py:71-73`'s `GEOMETRY_REPAIRED_KEY` /
`GEOMETRY_REPAIRED_AREA_CHANGE_KEY`. Carried on `FirePerimeterSnapshot.rows` so the fact is
computed and inspectable (`FirePerimeterSnapshot.repairs`, tested directly), but NOT a schema
column -- `rows()` projects onto `FIRE_PERIMETERS_SCHEMA.column_names` before building the Arrow
table, exactly as the legacy `fire_perimeters_table` does, for the identical reason: the L1 schema
is frozen. Unlike the legacy writer's terminal report and
`fire_perimeters_forward_geometry_repaired` stderr event, nothing in the generic runner's S5
report surfaces this per-incident detail today -- "Known residuals (H1/H2/H3)" and "What this
slice did not port" above name that gap rather than inventing a misleading home for it (e.g.
`Written.dropped_rows`, which means ROWS NEVER WRITTEN, not rows written-but-repaired).

### Why `_OBSERVATION_DAY_KEYS` is narrower than legacy's `OBSERVATION_DAY_KEYS`

The observation-day COALESCE is narrowed to the two keys `ingest.wfigs.parse_perimeter_collection`
ever actually produces. The legacy `rows.py::publisher_named_day` restates all four of
`geo.feature_observation_day`'s keys because it reads the stored PostgreSQL jsonb document, which
could structurally grow `observedAt`/`updatedAt`; this module's input is this package's OWN,
narrower dict shape (`ingest.wfigs.parse_perimeter_collection`'s output), which never carries
those two keys at all, so restating them here would assert a possibility that cannot arise.

### `probe_edge`'s classification table (ONE small GET, never the `_Current` walk)

`probe_edge` reads `editingInfo.lastEditDate` off the cheap layer resource; this is the probe-time
saving `plan_requests`/`fetch` do not need to re-earn. The answers the function returns directly:

- a non-JSON body, a 200 body carrying an ArcGIS error that does NOT read as a busy throttle, a
  non-object body, or an `editingInfo.lastEditDate` that is missing, null or not a finite
  epoch-millisecond number -> `invalid`;
- a 200 body carrying an ArcGIS error whose message matches
  `ingest.upstream_retry.BUSY_MESSAGE_PATTERN` ("too many requests|busy|try again") -> `deferred`:
  ArcGIS reports throttling INSIDE an HTTP 200 body (`lanes/_providers/nifc-wfigs.toml`'s own "busy
  HTTP-200 `error` body" note -- the exact production incident `BUSY_MESSAGE_PATTERN` exists for),
  so this can never raise `SourceThrottledError` and must be classified here instead of folded into
  `invalid`;
- editor tracking disabled (`lastEditDate == 0`, `_NO_TRACKED_EDITS`) -> `blind`: answered, but
  names no edit at all;
- a readable instant -> `ok`, with that instant's UTC date as the one valued day.

A transport failure or an HTTP 429 (`SourceUnavailableError` / `SourceThrottledError`) is NOT
caught here (M1, re-read after ring review): it PROPAGATES to
`pipeline/runner/turn.py::_spend_probe`, which owns the Retry-After cooldown
(`exceeds_ladder`/`hold_stated_wait`) for every probing strategy. Catching either exception in this
function would quietly bypass that ladder and answer `unavailable`/`deferred` without ever
persisting a stated wait.

**The per-incident repair detail (L3, restored after ring review) is computed but not reported.**
`FirePerimeterSnapshot.rows` carries `GEOMETRY_REPAIRED_KEY`/`GEOMETRY_REPAIRED_AREA_CHANGE_KEY` per
row again (`wfigs.py`, matching `pipeline/direct/fire_perimeters/rows.py:319-320`'s legacy keys),
and `FirePerimeterSnapshot.repairs` exposes them exactly as the legacy writer's own `.repairs`
property does. What does NOT exist is anywhere for a per-incident area-change list to surface in
the generic runner's S5 report the way the legacy writer's terminal report and
`fire_perimeters_forward_geometry_repaired` stderr event do — `Written.dropped_rows` means rows
never written, not rows written-but-repaired, so overloading it would be actively misleading, and
this slice has no other report hook to add one to. An aggregate repaired-count surfacing in the S5
report (not just the per-row property this slice restored) is a fair ask for a future revision of
the runner's report contract, not something fixable from inside one lane's strategy.
