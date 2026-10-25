# `pipeline/lanes/burn_severity/` — the burn-severity layer package

Moved verbatim from `pipeline/direct/burn_severity/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Burn severity".
The `s-burn-severity` swarm slice builds the config-lane strategy beside these modules.

| module | what |
|---|---|
| `source_protocol.py` | `BurnSeveritySource` and its payload Protocols; `pipeline/source_bindings.py` binds the implementation |
| `mtbs.py` | `MTBS_BURN_SEVERITY_SOURCE`: the MTBS implementation of that protocol |
| `products.py` | the release calendar and `burn_severity_lane_registration()` |
| `mtbs_releases.py` | `STRATEGY` for `lanes/burn-severity-direct-forward.toml` (`burn_severity.mtbs_releases`): historical releases only |
| `release_rows.py` | one release day's base-rung table and its DuckDB polygon repair (the config-lane successor of legacy `rows.py` + `support.py`) |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package; `mtbs_releases.py`
also imports `execution.contracts` (`MAX_SOURCE_GEOJSON_FEATURES`) and `pipeline/runner/contract.py`,
both of which the layer contract permits (`pipeline` forbids only `planes` and `interface`; S14).
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

**`products.py` imports `LANE_REGISTRY`**, so `pipeline/parquet/lane_registry.py` must never import
it (or `mtbs.py`, which imports it): that edge closes a cycle through a half-initialised registry.
The same holds for `mtbs_releases.py`, which imports `products.py`.

## The config-lane strategy (`s-burn-severity`, Phase 4 swarm)

`lanes/burn-severity-direct-forward.toml` ships **dark**: `executor = "legacy"`, so the legacy daily
command (`execution/lane_specs.py`, the same lane id, `--current-snapshots`) keeps running both halves
of the lane. `mtbs_releases.py` is the historical half only. `mtbs.py` keeps its name because it is the
source module the region binding resolves (`pipeline/source_bindings.py`); a strategy leaf there would
collide with it, hence `mtbs_releases.py`.

- **A `release_series` owing only governed release days.** `release_days(first, last)` is
  `products.governed_release_days()` clipped to the window, so the runner never owes a non-release day
  and never writes the ~2,000 absence markers a daily cadence would. The set grows only by a code
  change to `MTBS_ANNUAL_RELEASE_DATES`, never with time.
- **Forward is cheap by design, history is gap-fill's.** Lag 7 (it must equal the literal
  registration's, S18) and a 14-day recheck put every governed day (2020-11-24 .. 2024-08-22) behind
  the forward window: the daily forward fire costs one census and no send. Once gap-fill is enabled at
  a gate, its window `[2020-11-24, today - 21]` owes the governed days that are missing.
- **One unit per release day, every cohort inside it.** A release day may close more than one fire
  year; the union is all-or-nothing (the legacy `fetch_burn_severity_release_day` rule). The unit's
  declared parameters are its first send (the first cohort's `returnCountOnly`), so the budget's
  reservation key is a real send.
- **Fetched only through the provider client** (Wave O, FR-38): `client.get("burned-area-query", ...)`
  on `lanes/_providers/mtbs-edw.toml`. The legacy `ingest/mtbs.py::fetch_release_features` takes a raw
  `httpx.AsyncClient`, so the paging is restated here over `ProviderClient`, with the same three
  truncation signals (the service count, `exceededTransferLimit`, the short page), the same duplicate
  Fire_ID refusal, the same caps (`MAX_RELEASE_PAGES`, `MAX_SOURCE_GEOJSON_FEATURES`) and legacy's stop
  rule (a short page with no service flag). A page ending exactly on the count therefore asks one more,
  empty page: that page is what catches a fire added between the count and the paging with the
  truncation flag off (`test_a_fire_added_between_the_count_and_the_paging_fails_loud`). A cheaper
  "stop at the count" rule was tried and dropped in review because it published that case silently.
- **Only an oversized-page signal halves the window** (50 -> 25 -> 12 -> 6 -> 5) inside the unit: an
  ArcGIS error document with a 5xx `code` (`OversizedPageError`) or the client's byte cap
  (`ingest/http.py::UpstreamPayloadTooLargeError`). At 5 rows the document case re-raises as
  `SourceUnavailableError` and the runner's ladder takes over (3 attempts, then `upstream_unavailable`).
  A 5xx STATUS or a transport failure is an outage and goes to the runner's ladder at once, never
  narrowing a page. Any other error document is a strategy refusal, never an empty cohort.
  **Watch before cut-over:** `ingest/mtbs.py` records that the live host refuses an oversized window
  with HTTP 500 status, which `ProviderClient` reports as the same `SourceUnavailableError` as a real
  outage (it carries no status). The legacy walk halves on that; this strategy cannot tell it apart, so
  a cohort heavier than the measured 50-row safe page (10.7 MB on the 2020 cohort) would read
  `upstream_unavailable` here. A status on `SourceUnavailableError` is a contract change, not this slice's.
- **An honest zero is a governed absence**: `returnCountOnly` = 0 for every cohort of the day settles
  `Absent` with that proof (the legacy adapter's rule: a cohort with no fire inside the envelope is a
  permanent fact, never "come back later"). No page is requested for a zero count.
- **Never checkpointed.** A unit is many sends, and a checkpoint replays ONE body through `fetch`;
  `requested_parameters` is empty, so `SourceResponse.checkpoint_eligible` is false by construction.
  A failed day is re-asked whole next fire; it is a handful of sends.
- **The source digest is the canonical answer** (`sort_keys`, the features as served), not the
  retrieval instant, so a re-ask of unchanged MTBS content never reads as a revision (S11).
- **The envelope is the region's**: `region.sub_envelopes["burn_severity"]`, read in `plan_requests`
  and carried on every unit (`federation.md` §1).

### Release rows

`release_rows.py` restates legacy `pipeline/direct/burn_severity/rows.py` and `support.py` (same
columns, same MakeValid -> CollectionExtract(3) -> Multi chain, same DuckDB caps with spilling
disabled, same whole-day refusal on an empty repair or a Fire_ID repeated across cohorts) because no
`pipeline/lanes/**` module may import `pipeline/direct/**`. `allowed_client_exposure` stays a hard `False`.
Nothing structural ties the two copies, so `tests/lanes/burn_severity/test_release_rows_parity.py` pins
them: the SQL, the three DuckDB caps and the feature-id prefix are equal, and both builders produce an
equal table for the same records. **Cleanup item (7S):** move the legacy `rows.py` + `support.py` here
and leave re-export shims (CA14), which retires the parity test; not done mid-swarm because it crosses
the `pipeline/direct/**` boundary this slice only reads. No module here constructs `TerminalEvidence`
(`tests/lanes/burn_severity/test_no_terminal_evidence.py`); the legacy `publish_snapshot.py` does, legitimately.

### Budget and cadence arithmetic

Unweighted provider: the caps count logical requests. Per release day: per cohort, 1 count +
floor(n/page) + 1 pages (the short or empty page proves the end), plus one send per halving. Measured
basis: the 2026-08-05 load held 478 rows over four cohorts (`docs/lanes/burn-severity.md` §5), about 120
a cohort, so a typical day is 1 + 3 = 4 sends. Worst case, a 134-fire cohort paged at 5 rows after four
halvings: 1 + 4 + 27 = 32. A runner retry re-sends already-charged requests at no budget cost. Forward
cap 60: one newly governed day at the worst case with margin; it breaches when one day needs more than
60 sends (a cohort above about 275 fires at 5-row pages, or 2,950 at 50-row pages). Gap-fill cap 160 =
the five governed days at the worst case. Today's spend: forward 0 a day (no governed day in its
window); a full gap-fill re-pull of all five days ~20 sends, once.

### Known limits, recorded rather than hidden

- **A governed release dated on or after `MTBS_SNAPSHOT_FIRST_DAY` (2026-09-11) is refused before any
  send.** `parquet_ops/serving.py::_snapshot_scope` admits a burn-severity observed day from that date
  only with verified current-snapshot evidence (`parquet_ops/mtbs_snapshot_catalog.py::_source`); a
  release written through the runner (or the legacy forward, which shares the same generic source
  origin) would be refused at read. The day is reported `strategy_error` behind the edge every fire
  until an owner decision says how a post-2026-09-11 completed cohort is served. Fire years 2023 and
  2024 (MTBS targets end of FY2026) will land exactly here.
- **MTBS's own `User-Agent`/`Accept` headers are not sent.** `ProviderClient.get` takes no headers;
  the request carries the WQ-5 service User-Agent from `ingest/http.py::upstream_client`. The provider
  file says MTBS "keeps its own" format; whether the EDW host requires it is the live probe's question.
- **The per-unit fetch cap is 330 s** (`pipeline/runner/fetch.py::PER_UNIT_FETCH_CAP_SECONDS`). A
  release day is one unit of several ~10 MB pages; a cohort slow enough to pass it reads
  `upstream_unavailable` after three attempts. The cheap remedy is a smaller `DEFAULT_PAGE_SIZE` here,
  not a contract change.

## CA7: no capture successor

**Verdict: no successor; routed to the owner as CQ-9** (spec §4.8.9 CA7). The weekly current capture
(`pipeline/direct/burn_severity/{capture,current_snapshot,stage,publish_snapshot,daily}.py`) has no
sanctioned port in the frozen runner contract: carrying it needs a receipt or descriptor change, which
CA7 forbids. The surfaces that refuse it, in order of severity:

1. **The served source binding.** Serving admits a burn-severity day on or after 2026-09-11 only when
   its base source export has `origin = "mtbs-current-snapshot"` and a `mtbs-current-snapshot-binding/v1`
   detail naming the archived manifest (`parquet_ops/serving.py::_exact_snapshot` ->
   `parquet_ops/mtbs_snapshot_catalog.py::_source`). The runner's writer stamps every day through
   `pipeline/parquet/gap_fill_day.py` with the generic `POSTGRES_DAY_EXPORT_ORIGIN` and a fixed detail;
   `Written`/`rows` carry no origin or detail. Every config-written snapshot would be refused at read.
2. **No sanctioned port for evidence.** The capture must durably archive every decoded response entity
   (`mtbs-staging/blobs/<sha>`), the source manifest the catalogue re-reads (`mtbs-snapshots/manifest=`),
   the CAS stage queue (`_STAGED.json`) and check records. The contract hands a strategy only a
   `ProviderClient`. A strategy could physically reach the bucket by importing `pipeline/parquet`
   (`ObjectStore`, `BotoAvailabilityStorage`), but those writes would bypass the runner's write permit,
   `--compare` (which must never write) and checkpoint discipline, so it is not a port this slice may use.
3. **Day semantics.** A snapshot is dated the UTC day AFTER capture closes, and serving requires its rows'
   `observed_day` and `data_available_at` to equal that day (`serving.py::_snapshot_rows`). The runner
   settles days at or before its edge from answers fetched in the same turn, so it could only date a
   capture on the day it ran: a backdating the legacy contract refuses outright. The legacy two-turn
   path (stage on D, publish on D+1 after a hash-verified replay) needs surface 2.
4. **A mutable source with no version token.** A missed capture day is unrecoverable. The runner's
   recheck and gap-fill would re-ask a past day and label today's MTBS state with it; the contract's only
   other word, `Absent`, writes a governed absence that serving reads as a complete zero-fire snapshot.
5. **One consistency proof, one unit, 330 s.** The capture's before/after attribute re-inventory spans
   the whole request graph (9 fire years x count + attributes, twice, plus 25-row geometry pages; up to
   400 sends inside a 600 s wall budget). Splitting it into units breaks the all-or-nothing proof; one
   unit exceeds `PER_UNIT_FETCH_CAP_SECONDS`.

**What the CQ-9 carve-out means operationally:** this lane stays `executor = "legacy"` through G6 and
moves to a later cohort (spec §4.8 cohort note). Flipping this TOML to `config` would hand the lane id
to the runner and take the legacy daily command, and with it the capture and the daily stage
publication, off the executor. The G6 `mtbs-forward` brake does not fire (it is conditional on a named
successor). The legacy `capture.py` metered-client swap (GL-2) stays live with it. A real successor needs
a contract extension (a writer hook for the source origin and binding, plus an immutable evidence port
for strategies): that is a receipt/descriptor change, so a later track item, not this slice.
