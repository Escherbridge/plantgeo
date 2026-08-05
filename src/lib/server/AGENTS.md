# `src/lib/server` — module notes

Rationale and constraints that the code's one-line doc comments deliberately omit.
Add a section per module as it grows; sections are independent.

## §regional-intelligence

The AI advisor that answers "what should be done about this place?" for a map
point. Files: `services/ai-prompt.ts` (agent loop), `services/regional-context.ts`
(evidence assembly), `services/web-evidence.ts` (search), `services/ai-conversations.ts`
(multi-turn persistence), `security/regional-intelligence-access.ts` (quota),
`app/api/ai/regional-intelligence/route.ts` (SSE transport),
`lib/regional-intelligence.ts` (shared client/server contract).

### The feature recommends; it does not certify

The agent's primary job is to suggest remediation strategies. That output is
**AI-generated advisory content, not a validated model release.** Three things
enforce that, and none of them are cosmetic:

- Every report carries a literal `aiGenerated: true`. A renderer cannot
  accidentally present it as a warehouse product.
- Every claim carries an `evidenceOrigin` of `warehouse`, `web`, or
  `model_inference`. Most remediation reasoning is legitimately inference; the
  contract makes the model say so instead of dressing inference up as data.
- `professionalConsultation` and per-item `consultProfessionals` are **required**
  by the tool schema. The model cannot return a plan without naming who should
  vet it.

This replaces an earlier design in which the route rejected any response citing
a source that was not freshly published. That gate made the feature
unshippable — the strategy and carbon evidence planes it depended on are
permanent `unavailable` stubs, so no recommendation could ever pass. The gate
was removed deliberately (2026-08-02). The cost is real and should be understood:
**model-asserted facts now render alongside warehouse-sourced ones**, so the
data-freshness footer describes what was *observed*, not what the model *said*.
Origin badges, not the footer, are what tell a reader where a claim came from.

### Evidence assembly never blocks the answer

`assembleRegionalContext` used to throw when nothing was published, which
turned a thin data day into a 503. It now returns whatever resolved plus
`contextIsEmpty`, and the prompt instructs the model to state what it could not
see. A source marked `unavailable` means *unmeasured*, not *absent* — the
prompt says this explicitly because the distinction is easy for a model to blur
and consequential for a land manager.

`REGIONAL_EVIDENCE_SOURCES` is the set of layers the platform can date-stamp,
not a ceiling on what the agent may discuss. Fire detections, perimeters, and
weather were added to it because they are the layers actually populated; soil,
MTBS, strategy scores, and carbon remain declared-but-unpublished so the
contract is ready when those planes land.

### Quota is the cost boundary, and it fails closed

`reserveRegionalIntelligenceUsage` reserves a slot in a Redis ZSET **before**
context assembly or any model call, so a rejected caller costs neither an
upstream query nor a token. The reservation is one Lua script because a
read-then-write pair lets two replicas both pass at the limit.

In production an unreachable Redis denies the request. An unmetered agent is an
uncapped bill, and that is the worse failure. Development allows it through so a
local run without Redis is not blocked.

`resolveEntitlementPolicy` is the billing seam: it currently maps every
signed-in account to the `signed_in` tier. Replacing its body with a real
subscription/partner lookup is the entire integration — the reservation logic
below it does not change.

### Conversation history is server-side on purpose

Multi-turn state lives in `ai_conversations` / `ai_messages`, keyed by
`conversationId`. The client sends only that id. An earlier version accepted
history in the request body and had to discard assistant turns as untrusted,
which meant the model could never see its own prior answers — follow-up
questions were not really follow-ups. Reading history from the database fixes
that and removes the forgery surface at the same time.

### The agent loop is bounded in two dimensions

`MAX_TOOL_ROUNDS` bounds wall-clock and token spend; `MAX_SEARCHES_PER_REQUEST`
bounds vendor cost separately, because one runaway round could otherwise burn
the whole search budget. The final round forces `tool_choice` to the report
tool, so a model that keeps wanting to search still returns something usable.
Search failures come back as `is_error` tool results rather than aborting the
turn — degraded advice beats no advice.

Prompt-injection surface: the user's question is fenced in `<user_question>`
tags and the system prompt states that content inside them is untrusted input.
Web search results are model-visible text from arbitrary pages and are treated
the same way — they inform the report, they do not redirect the agent.

### Search provider is swappable by design

`WebEvidenceProvider` exists so Jina can be replaced with Brave, Tavily, or Exa
by editing one file. Jina is configured because search and clean markdown
extraction come from one vendor. Search requests send
`X-Respond-With: no-content` so a query returns snippets; a full page body is
fetched only when the agent asks to read one. Snippets are hard-truncated —
token cost, not result quality, is the binding constraint on this call.

No `JINA_API_KEY` is a supported state, not a broken one: the provider resolves
to `null` and the system prompt tells the model it is working offline.

## §fire-detections

NASA FIRMS active-fire points. Files: `services/nasa-firms.ts` (upstream client),
`services/ingestion-jobs.ts` (`runFireIngestionJob`),
`services/environmental-time.ts` (day range + freshness),
`services/environmental-read-model.ts` (`getPublishedFireDetections`).

### One satellite is not a feed

The job queries the whole VIIRS constellation — SNPP, NOAA-20, NOAA-21 — and
unions the result. It used to query `VIIRS_SNPP_NRT` alone, and on 2026-08-02
that one product stopped publishing over the ingest bbox while its siblings kept
producing normally. FIRMS answered `200` with a header-only CSV, so the job
parsed zero points, wrote zero rows, and reported `status: "ingested"`. Nothing
threw and nothing went red; the layer simply stopped growing and would have gone
empty once the existing rows aged past the freshness window.

A single dead product must therefore never zero the layer. `Promise.allSettled`
keeps the healthy products' rows, names the unavailable ones in `reason`, and
only rethrows when every product fails. The FIRMS `satellite` column (`N`,
`N20`, `N21`) already namespaces the observation id, so the union cannot
collide. The record cap applies to the merged, newest-first set so truncation
drops the oldest detections rather than whichever satellite resolved last.

### The CSV schema is per-instrument

VIIRS publishes `bright_ti4`/`bright_ti5`; only MODIS publishes `brightness`.
The parser was written against the MODIS header, so against VIIRS the lookup
missed and every detection was stored with `brightness: 0` — which the map reads
as the bottom of its colour ramp. The parser now accepts either column name.
Rows written before 2026-08-04 still carry the zero and are not backfilled; they
age out of the freshness window on their own.

### Staleness is not masked

`FIRMS_DAY_RANGE` (default 2) bounds both the upstream request and the read
model's freshness filter. Widening it to keep a dark panel populated would hide
exactly the upstream outage described above, so it stays narrow and an empty
window is allowed to read as empty.

## §drought-ingestion

The US Drought Monitor D0–D4 layer. Files: `services/usdm-drought.ts` (upstream
client), `services/drought-ingestion.ts` (writer + retention),
`services/environmental-read-model.ts` (`getPublishedDroughtClassification`,
`getDroughtCategoryAtPoint`), `app/api/ingest/drought/route.ts`, migration
`drizzle/0007_governed_environmental_ingestion.sql`.

### The date is the request, never the payload

USDM's published GeoJSON carries no date field — `usdm_current.json` is an
undated 19 MB blob. So it is never used. The client fetches
`usdm_<YYYYMMDD>.json` for an explicit Tuesday and treats a 200 as USDM's own
confirmation that it published that release; an unpublished date 404s. That
makes `valid_date` a fact about the request rather than an inference, which is
what lets the read model gate on freshness honestly. The ingestion job walks
back a bounded number of Tuesdays and takes the newest that exists.

### Geometry lives in PostGIS, not in a JSON column

A release is ~19 MB of full-resolution rings across five MultiPolygons. The
legacy `public.drought_data` blob table could only ever be returned whole, which
is unservable to a browser and unusable for a point query. `geo.drought_areas`
stores one real `geometry(MULTIPOLYGON,4326)` row per class per week, so reads
clip to a bbox and simplify in the database, and `checkDroughtAlerts` answers
containment with `ST_Intersects` instead of loading the nation into Node.
`drought_data` is left in place but is no longer read or written.

Rings are repaired on write (`ST_MakeValid` then `ST_CollectionExtract(…, 3)`)
because USDM ships self-intersecting rings that make `ST_Intersects` unreliable.
That fixes topology only — no class is invented, and a class absent from a
release stays absent rather than being stored as an empty geometry.

Simplification generalizes boundaries to roughly one screen pixel for the
requested viewport; it never reclassifies. A class whose clipped geometry comes
back empty is omitted from the collection rather than emitted as a null feature.

Retention is bounded by `DROUGHT_RETAINED_RELEASES` (default 8 weeks) because
each release is ~19 MB. Re-ingesting a stored week is a no-op unless `force=1`,
which exists for the rare case where USDM corrects a release.

## §soil-evidence

`services/soilgrids.ts` + `public.soil_grid_cache` + `app/api/ingest/soil/route.ts`.

SoilGrids returns integers scaled by a per-property `d_factor`, which is read
out of the response rather than hardcoded, so a units change upstream cannot
silently rescale stored values.

`SoilProperties` is all-or-nothing. SoilGrids reports `mean: null` outside its
coverage, and every consumer (USLE K-factor, carbon potential) treats the six
topsoil properties as jointly measured — so a partial profile is refused rather
than back-filled.

Three upstream readings are distinguished, because only two of them are claims
SoilGrids actually made. Every property `null` is a verified coverage gap: it is
cached as `complete = false` and re-raised as `SoilEvidenceUnavailableError`
(`PRECONDITION_FAILED`), so a no-data cell is not re-queried forever. A *mixed*
response is not — the six properties share one raster extent, so a partial
profile is an upstream anomaly, and it is neither cached nor served. A timeout,
429, 5xx or unreadable body is a `SoilUpstreamUnavailableError`
(`SERVICE_UNAVAILABLE`) and is never cached — recording a transport fault as "no
data here" would poison the cache with a claim the upstream never made.

The six properties travel in **one** request carrying six `property` parameters,
not six requests. Measured 2026-08-04 against `rest.isric.org`: a one-property
and a six-property query for the same point are indistinguishable — both hung
past 40 s, and both drew a 0.6 s nginx `503` while `/docs` answered in 0.76 s.
Latency is upstream queueing, not payload size, so splitting the batch buys
nothing and costs six slots against ISRIC's ~5 req/min limit. That limit is also
why the warm route (`app/api/ingest/soil/route.ts`) is serial and paced, and why
concurrent readers of one cell are collapsed into a single in-flight request.

`REQUEST_TIMEOUT_MS` is 12 s rather than 30 s for the same reason: ISRIC answers
in about a second or does not answer at all, so a longer bound only lengthens
the spinner before an identical failure. It also stays under the warm route's
13 s pacing, so a request cannot outlive its own slot. Because v2.0 is a frozen
release, an expired cache row is the same measurement as a fresh one and is
served in preference to failing while ISRIC sheds load.

Cache keys quantize to a 0.001° cell, finer than SoilGrids' own 250 m pixel, and
the quantized cell is what gets queried — so the cached value corresponds
exactly to the coordinate returned, with no interpolation across cells.

## §soil-survey

`services/usda-soil.ts` + `trpc/routers/environmental.ts#getSoilSurvey`. Distinct
from §soil-evidence above: that is SoilGrids' modelled raster at a point, this is
USDA's surveyed SSURGO map units over a viewport.

> **Partly superseded 2026-08-05 by §soil-survey-persistence below.** The provider
> findings, the tri-state `hydric` rule, the density measurements and the honest-gap
> reasoning all still hold. The *acquisition* half does not: map units are persisted, the
> Redis day-cache is gone, and three specific claims in this section are now falsified —
> §soil-survey-persistence lists them.

**The SSURGO spatial WFS cannot serve this feature, and the tabular endpoint can.**
Probed live 2026-08-04:

- `Spatial/SDM.wfs` — the URL this module originally used — does not exist. Every
  request answers `400 <ServiceException>Requested WFS Service does not exist
  'SDM.wfs'</ServiceException>`.
- `Spatial/SDMWGS84Geographic.wfs` does exist, but rejects
  `outputFormat=application/json` with `parameter "outputformat" requires a value
  from the list ("GML2","GML3","XMLMukeyList")`. There is no JSON from that service.
- Its `MapunitPoly` type carries only `areasymbol`, `spatialversion`, `musym`,
  `nationalmusym`, `mukey`, `mupolygonkey`, `muareaacres`. **No `muname`, `compname`,
  `drainagecl` or `hydricrating` at all** — those live in the tabular database. A URL
  fix alone would therefore have painted every polygon in the country
  `muname: "Unknown"`, `drainageClass: "unknown"`, `hydric: false`.

So both halves come from `Tabular/post.rest` in one round trip: SDA's own
`~DeclareGeometry~`/`~GetClippedMapunits~` preprocessor macros clip the map-unit
polygons to the viewport and return them as WKT, and ordinary T-SQL joins `mapunit`
and the dominant `component` for the ratings. `mupolygon.STIntersects(...)` written
out longhand instead of the macro **times out past 90 s** and is not an alternative.

`hydric` is tri-state. SSURGO's `hydricrating` is `"Yes"`, `"No"`, `"Unranked"`, or
null; only the first two are ratings. `Boolean(rating)` is `true` for the string
`"No"` and `true` for `"Unranked"`, and `false` for a null — it both inverts real
ratings and manufactures a "Hydric: No" verdict for unrated units. Every other
rating is `string | null` for the same reason: `hover-fields.ts` omits a line it
cannot fill, which is honest, whereas `"Unknown"` reads as a survey finding.

Payload is linear in viewport **area**, but not in geography: SSURGO map-unit density
varies by an order of magnitude across CONUS, so unlike HUC12 below no single site
generalizes. Measured over **Boise** (arid rangeland, coarse map units): 0.0025 sq deg
→ 0.20 MB / 3.9 s; **0.02 sq deg → 1.32 MB / 5.0 s / 380 polygons**; 0.0625 sq deg →
7.25 MB / 27 s / 2129 polygons. Measured over **Des Moines** (Corn Belt farmland, the
densest surveying in the country) at the *identical* 0.02 sq deg: **2.94 MB / 14.0 s /
1001 map units** — 2.6× the polygons and 2.8× the time for the same area, and past
`MAX_SOIL_POLYGONS`, so the Corn Belt truncates at the sanctioned zoom rather than in
some exotic edge case. Treat the Boise row as the best case and the Des Moines row as
the CONUS ceiling.

`MAX_SOIL_BBOX_SQUARE_DEGREES` is the middle Boise figure, kept because it is also
roughly the zoom at which a 1:24,000 survey is worth drawing, and because payload is
linear in area: halving it would halve the Corn Belt worst case, which is the lever to
reach for if 14 s proves too long a wait, rather than shortening the timeout.

`REQUEST_TIMEOUT_MS` is 30 s, tuned against Des Moines and not Boise. At the old 20 s
the densest viewport in CONUS had 1.43× headroom, so any upstream variance above 43%
turned a slow-but-correct answer into a timeout — and a timeout here is not a cheap
failure: cache keys are per-exact-bbox, nothing is written on failure, so every retry
re-pays in full while the panel reports the layer unavailable. 30 s is ~2.1× the
measured ceiling and stays inside the house band (`usdm-drought` 60 s, `wfigs` 20 s).
This is the opposite call from §soil-evidence's 12 s deliberately: ISRIC answers in a
second or not at all, so a longer bound there only lengthens the spinner before an
identical failure, whereas SDA does answer the dense viewports — just slowly.
`MAX_RESPONSE_BYTES` (8 MB) is unchanged and remains the payload bound; Des Moines'
2.94 MB leaves 2.7× of room.

`SELECT TOP MAX_SOIL_POLYGONS + 1 … ORDER BY g.id` bounds the rows independently and
makes truncation detectable; the extra row is dropped and `truncated` is set. Reducing
geometry in SQL (`geom.Reduce(0.00001)`) was measured and rejected — it saves 2.6%,
because SSURGO vertices are already sparse relative to a metre.

`{}` with no `Table` key is SDA's answer where it holds no map units (verified over
open ocean). That is a coverage gap and is served as an empty published collection.
A `Table` that is present but unreadable is a `SoilSurveyResponseError`, reported as
`soil_survey_upstream_returned_no_table`, and is never cached.

**A dropped row is a gap, not an absence.** `toSoilSurveyCollection` drops any map unit
whose WKT `parseWktPolygon` will not read — an SRID prefix, a geometry type it does not
handle, an unclosed ring — because the ratings alone cannot be placed on the map. Those
drops are counted into `unreadableGeometries` and carried all the way to the client on
`ProxiedFeatureCollection`, for the same reason `truncated` is: without it, a viewport
whose every row failed to parse is byte-identical to open ocean (`features: []`,
`truncated: false`, `availability: "published"`) and `SoilPanel` would caption a reader
gap as "USDA reports no surveyed SSURGO map units in this view" — a coverage claim USDA
never made — and `getSoilSurvey` would then cache that claim for 24 h under this bbox.
The closure check above makes the parser strictly *more* rejecting, so the count is the
thing that keeps that strictness honest. `isSoilSurveyCollection` requires the field, so
a Redis entry written before it existed is re-fetched rather than served: it cannot say
how many rows it dropped, and defaulting to zero would assert a completeness nobody
measured. `getWatersheds` reports `0` because hydrosheds rejects a payload whole rather
than dropping features from it.

Rings are rejected unless they close (RFC 7946 3.1.6, first position repeated last).
SDA's `STAsText()` always closes them, so this is latent — but `parseWktPolygon`'s
contract is "never a partial or repaired geometry", and an unclosed ring handed to a
renderer is auto-closed into a polygon the survey never recorded.

**Where truncation and unavailability are consumed.** `truncated`, `availability` and
`reason` reach the client on `ProxiedFeatureCollection` and are rendered by
`components/panels/SoilPanel.tsx` beneath the `soil-survey` toggle: a truncated view is
captioned as a subset, an `unavailable` one as a provider fault, and only a published
empty one as ground the survey found nothing on. The map itself cannot express the
difference — all three arrive as polygons that stop. The layer registry's
`unavailableReason` channel is not the carrier: `soil-survey` is upstream-proxied with
`warehouseLayerName: null`, so `useLayerRenderState` has no capability to evaluate and
always reads "published", and a reason routed through `LayerToggle` would also disable
the switch while the layer is still drawing. A non-zero `unreadableGeometries` is
captioned there too, beside `truncated`, and suppresses the empty-coverage sentence.
The area ceiling is server-side only, so an over-wide viewport surfaces to the client as
a rejected request, not as a pre-emptive client-side check that could drift from the
constant.

## §soil-survey-persistence

Supersedes the acquisition half of §soil-survey above as of 2026-08-05: SSURGO map units
are **persisted in the warehouse**, and a viewport read is a local PostGIS query. Files:
`services/usda-soil.ts`, `drizzle/0013_soil_survey_persistence.sql`,
`db/schema.ts#soilSurveyCoverage`. Read §soil-survey for the tabular-vs-WFS finding, the
tri-state `hydric` rule and the density measurements, which all still hold.

**Three statements in §soil-survey are now falsified. Do not act on them.**

1. *"`mupolygon.STIntersects(...)` written out longhand instead of the macro times out
   past 90 s and is not an alternative."* Measured 2026-08-05 against the live endpoint:
   the longhand form over a 0.02 sq deg Boise AOI answers in **4.3–6.7 s** and returns
   649 delineations in 2.78 MB. It is what this module now uses, and it is not slow.
2. *"`MAX_RESPONSE_BYTES` (8 MB) is unchanged."* It is 24 MB, because whole delineations
   are ~4.4 KB of WKT each and the per-cell ingest ceiling is 4,000 of them.
3. `toSoilSurveyCollection` and `isSoilSurveyCollection` no longer exist, and neither does
   the Redis day-cache. The names to look for are `parseSoilSurveyRows` (pure, exported)
   and `geo.soil_survey_coverage`.

**Why the macro had to go, and it is not performance.** `~GetClippedMapunits~` returns
geometry *clipped to the AOI that asked*, keyed on `mukey`. Both properties are fatal to
persistence. A clipped shape depends on the viewport that fetched it, so storing one
records an arbitrary slice of a real boundary and makes blank ground appear on the next
pan. And `mukey` is not one shape: measured over a single Boise cell the macro returned
**683 rows across 98 distinct mukeys**, so keying identity on `mukey` would have collapsed
683 delineations into 98. `mupolygon.mupolygonkey` is SSURGO's own per-delineation primary
key and is what `geo.geometry.natural_key` is built from —
`'usda-sda:<mupolygonkey>'`, producer `usda-sda`, `geom_kind` `'polygon'`.

**The vintage is the survey area's, and there is no observation date.** SSURGO is a static
survey product with per-survey-area vintages, not a time series, so `observedAt` stays
`null` on the collection exactly as it was — there is no single release timestamp to put
there and inventing one would be a fabricated observation. What does exist is
`sacatalog.saverest`, the survey area's own export vintage, joined through
`mapunit.lkey → legend.lkey → legend.areasymbol`. Verified over the PNW envelope: all
**220** intersecting survey areas carry one, spanning 2025-08-26 to 2026-03-19. It lands
in two places — `properties.surveyAreaVintage` (ISO date, per feature, alongside
`areaSymbol`) and `geo.geometry.version_valid_from`. SDA serves it as US-locale text with
**no timezone** ("8/27/2025 8:27:08 PM"); `parseSurveyAreaVintage` keeps the date at UTC
midnight and discards the clock, because keeping the time would look more faithful while
asserting a timezone the publisher never stated. A row whose vintage will not parse is
**unstorable** (`version_valid_from` is `NOT NULL`) and is counted, not dropped silently.

`saverest` is also the Type-2 revision signal, which is what §geometry-dimension's change
detection rule demands (*"version on the producer's own revision signal where one
exists"*) and rules out comparing geometry floats. A re-fetch of unchanged ground only
advances `last_confirmed_at`; a survey area republished with a **strictly newer** vintage
closes the old version and opens a new one, in the one legal statement order. Strictly
newer, because `ck_geometry_version_order` requires `version_valid_to > version_valid_from`
— a regressed upstream vintage is an anomaly to fail loudly on, not to absorb.

**The coverage ledger exists because persistence adds a second dishonest-empty case.**
Proxying had exactly one: SDA answering with no map units, which really does mean
unsurveyed. Reading a warehouse adds *ground nobody has fetched*, and it paints
identically — polygons that stop. `geo.soil_survey_coverage` is what separates them: one
row per grid cell, written only after a successful fetch, so a cell **with** a row is
authoritative (`polygon_count = 0` genuinely means unsurveyed) and a cell **without** one
has never been asked. `coverage.covered < coverage.cells` rides all the way to
`SoilPanel`, which captions it as missing coverage on our side. A transport fault leaves
the cell unrecorded on purpose — recording it would convert an outage into a claim about
the ground.

`polygon_count` is counted back out of `geo.features` *after* the writes, and
`unreadable_count` is served-minus-stored. So a row lost anywhere in the path — including
inside PostGIS, where TypeScript cannot see it — still surfaces to the reader instead of
quietly shrinking the map.

**The grid is 1/8 degree and that number is load-bearing.** `SOIL_SURVEY_CELL_DEGREES =
0.125` is exactly representable in binary floating point, so `floor(lon / 0.125)` never
drifts and the same ground always resolves to the same `cell_key`; a decimal such as 0.1
would eventually mint near-duplicate ledger rows for one cell. Cell area is 0.015625 sq
deg, just inside `MAX_SOIL_BBOX_SQUARE_DEGREES` — the largest area one SDA round trip is
measured safe over — so no fetch, read-through or backfill, ever asks for more than that.
`MAX_SOIL_AGGREGATION_CELLS_PER_SIDE` (3) keeps its name and changes its job: it used to
bound the SDA fan-out an averaged *read* made, and now bounds the cells one request may
warm. A viewport needing more is answered from what the ledger covers, with the gap
reported.

**Measured cost, and the envelope projection.** All figures from the verified slice run
against production on 2026-08-05, from a Windows laptop over Railway's **public TCP proxy**
— which is the number that matters, because it is not where a real backfill should run.

| what | measured |
| --- | --- |
| SDA fetch, one 1/8° cell (probed alone) | 4.3–6.7 s, 2.8 MB, 649 delineations |
| whole ingest, one 541-delineation cell | **52 s** (so ~47 s is the write, not SDA) |
| detail read, warm, 23 delineations | 0.8–3.5 s |
| averaged read over 4 cells (2,134 units) | **1.2–1.5 s** — was a measured 30–40 s |
| storage, `geo.features` payload | 4,469 B/delineation |
| storage, `geo.geometry` payload | 1,875 B/delineation |

The PNW envelope `-125,42,-111,49` is 112 × 56 = **6,272 cells** and holds **1,507,623
delineations across 44,332 mukeys and 220 survey areas** (counted directly at SDA in 23 s).
So: **~91 h single-stream / ~30 h at concurrency 3 from a laptop**, and **~9.5 GB** of row
payload (~13–15 GB with indexes). The laptop figure is dominated by round-trip latency to
Railway, not by SDA or by PostGIS — SDA is only ~10% of it. **Run the backfill from inside
Railway's private network**, where SDA becomes the floor at roughly 3 h at concurrency 3.
`backfillSoilSurvey` is bounded by `maxCells` and resumable with no cursor of its own — the
ledger *is* the cursor — so an interrupted run is safe to restart. Do not run the whole
envelope unattended in one pass.

Two measurement traps worth knowing. `WITH … AS MATERIALIZED` in the geometry insert is
worth ~2× on the write path: without it the CTE is inlined and `ST_GeomFromGeoJSON` is
re-evaluated for every column referencing it (the first slice averaged 116 s/cell, the same
code with the CTE materialized 52 s). And **the ledger's `polygon_count` does not sum to the
feature count**: a delineation straddling a cell boundary is fetched by both cells and
counted in both ledger rows while being stored once (measured: 5 cells, ledger sum 2,675,
distinct features 2,525). `unreadable_count` is unaffected, because the stored-count probe
matches on the payload's keys regardless of which cell first inserted them.

Verified on the slice: 2,525 features, 2,525 dimension rows, **0 features with a null
`geometry_id`**, 0 invalid or empty geometries, 0 non-areal geometries, 0 unreadable rows,
and every `natural_key` namespaced. The forward path therefore maintains the geometry
dimension rather than leaving orphans for `agri-cli ingest-geometry-repair` to claim later.

**The averaged tiers got much cheaper and that is the point.** They used to tile the
viewport into up to nine SDA calls, a measured 30–40 s. They are now one local query that
simplifies each delineation *before* the union (the union is the expensive step; feeding it
generalized input is what makes a multi-degree viewport answerable) and bounds its input at
`MAX_SOIL_AGGREGATE_INPUT_ROWS`. The aggregate shape is unchanged and still structurally
unable to pass as a surveyed unit: `aggregated: true`, `mapUnitCount`, `hydricFraction`,
and never a `mukey`. `hydricFraction` is divided in TypeScript rather than SQL because its
two operands are `COUNT(*)` bigints and a fractional parameter placed next to one gets
bound as bigint by postgres-js.

**No Martin restart, no tile function.** `soil-survey` is drawn by
`components/map/layers/SoilSurveyLayer.tsx` from a GeoJSON source fed by the tRPC
procedure, not by a `geo.*_tiles` function — unlike `drizzle/0009` and `0012`, which did
add function sources and did need one. Detail-tier reads serve **whole** delineations now
rather than viewport-clipped ones; the renderer clips, and every other polygon layer here
already serves whole shapes.

## §watershed-boundaries

`services/hydrosheds.ts` + `trpc/routers/environmental.ts#getWatersheds`.

WBDHU12 reports its own truncation. At basin scale the layer answers with a
top-level `exceededTransferLimit: true` beside exactly `resultRecordCount`
features (measured: 500 features, 21.6 MB over a 30 sq deg envelope). Reading the
flag is the only way to tell a complete viewport from an arbitrary slice, so it is
surfaced as `truncated` on `ProxiedFeatureCollection` and rendered as a
partial-coverage note; without it the API asserted `availability: "published"` over
a subset. Paging past it is not available: `resultOffset=500` returns **HTTP 500**,
with or without `orderByFields`.

`geometryPrecision=6` rounds to ~0.1 m — far finer than 1:24,000 boundaries were
digitized at — and cuts a 1 sq deg response from 8.63 MB to 5.07 MB over the same
131 polygons. Without it that viewport exceeds `MAX_RESPONSE_BYTES` and fails as an
oversized payload. HUC12 density is near-uniform by design (~40 sq mi per unit), so
`MAX_WATERSHED_BBOX_SQUARE_DEGREES = 1` is ~130 polygons / ~5 MB / ~7 s anywhere in
CONUS.

Validation happens inside `getWatersheds`, before the cache write, because ArcGIS
answers some faults with HTTP 200 and an `error` object: caching one persisted the
fault for the full hour, and the panel's "pan or zoom to retry" advice could not
work for the same viewport. A rejected payload raises `WatershedResponseError`,
which the router reports as `watershed_upstream_returned_no_features` rather than
as an empty viewport the provider never claimed.

Client-side, `WaterPanel`'s watershed tab and `LayerManager` both call
`useWatershedsQuery` — see §proxied-viewport-queries for why neither may build the query
itself.

Both proxied procedures cap viewport **area**, not just WGS84 legality. They are
unauthenticated, they call a third-party API per request, and the Redis key is the
exact bbox — so nothing amortizes a basin-wide ask, and the ceiling is the only
cost boundary. Both go through `cacheGeoJSON`/`getCachedGeoJSON`, which latch off a
dead Redis instead of throwing: the cache is an optimization, and a Redis outage
must not take two map layers down with an `INTERNAL_SERVER_ERROR`.

## §proxied-viewport-queries

`src/hooks/useViewportProxiedLayers.ts`, consumed by `components/map/LayerManager.tsx`,
`components/map/PanelManager.tsx`, `components/panels/SoilPanel.tsx` and
`components/panels/WaterPanel.tsx`. Applies to both feeds above.

**One query per feed, not one per caller.** A map layer and the panel that describes it
read the same viewport, so they must produce the *same* react-query entry — not two that
look alike. Four things have to agree for that to hold: the bbox derivation, the
placeholder input used when there is no bbox, `staleTime`, and `retry`. Hand-copied
across two files they drift silently, and the failure is invisible on first paint: with
the map on 1 h and a panel inheriting `providers.tsx`'s 60 s default, opening the panel a
minute later issues a *second* full upstream fetch (~5 MB / ~7 s for HUC12) of data the
map considered fresh for another 59 minutes. `staleTime` is per-observer in TanStack v5
and `refetchOnMount` defaults true, so nothing about a shared key prevents this.

So all four live in `useSoilSurveyQuery` / `useWatershedsQuery`, and the bbox in
`useViewportBounds` — one derivation, called by `LayerManager` and by `PanelManager`,
which hands it to the panels as a `bbox` prop exactly as the other viewport-scoped panels
already receive one. Callers supply only `enabled`, which is genuinely per-caller (a map
layer is mounted, a panel is open) and can never split one cache entry into two, since it
is not part of the key.

The hooks also apply the registry's `permanentlyUnavailableReason` guard to the
*request*, not just to the render, so a panel can never become the sole requester of a
layer governance withholds from the map. `SoilPanel` reads `useLayerVisibility()` rather
than `useLayerToggle()` for the same reason — raw `activeLayers` membership bypasses that
guard.

`retry: 1`, not react-query's default 3. Cache keys are per-exact-bbox and nothing is
written on failure, so every attempt re-pays in full: at `REQUEST_TIMEOUT_MS` a dense
Corn Belt viewport that times out costs 4 × 30 s = 120 s of SDA work under the default,
against 60 s at one retry. Not 0, because the observed failure mode is a transport blip
rather than a deterministic fault, and with nothing cached the user's only recourse would
be to pan away and back. A bbox the router rejects for area is a Zod failure that never
reaches the provider, so retrying one costs nothing measurable.

`src/__tests__/components/viewport-proxied-query-sharing.test.tsx` pins this by rendering
the real `LayerManager` and the real `PanelManager` over a recording tRPC link, and
asserting one cache entry, two observers, one set of options and one request per feed. It
deliberately does not mock `useQuery`: a mock would let the two callers diverge and still
report green, which is exactly the state this section exists to prevent.

Known gap, deliberately unfixed here: `abortOnUnmount` is unset (tRPC default `false`)
and `fetchBoundedJson` does not forward an inbound `AbortSignal`, so panning away
cancels nothing server-side. Both are transport-layer concerns in `lib/trpc/client.ts`
and `http/bounded-upstream.ts` respectively, not per-query settings.

## §slider-day

`services/environmental-read-model.ts`, the `date` inputs on
`trpc/routers/environmental.ts` and `trpc/routers/wildfire.ts`, and
`lib/map/layer-toggle-context.ts`'s `useDebouncedMapDay` on the client. The time slider used
to report *whether* the warehouse held a chosen day while every layer kept drawing the live
edge; this is what makes the layers draw the day.

**An omitted day and today's date are the same read, deliberately.**
`resolveRequestedObservationDay` collapses both to `{ kind: "live" }`, so first paint runs the
exact query each reader has always run — the one whose cost is measured — and the client sends
no `date` at all when the selection is `capabilities.serverCurrentDate`. That is not cosmetic:
tRPC keys on the input, so passing the date explicitly would mint a second react-query entry
for an identical answer and fetch it twice on load. `useDebouncedMapDay().requestDate` is
`undefined` in exactly that case, and also before capabilities arrive — without them nothing
knows which day is today, and reading the browser clock is the timezone disagreement
`serverCurrentDate` exists to prevent.

**Bucket a named day on the publisher's own named day, never in UTC.** In SQL that is
`substring(<iso text>, 1, 10)::date` (`OBSERVATION_DAY`, or `namedDaySql` for one explicitly
named field); in TypeScript it is `publisherNamedDay`, which reads the raw stored string
because `parseZonedObservationTime` has already normalized it to UTC by then. All 16,743
stored USGS gauge readings carry `-07:00`, and 6,279 of them (37.5%) land on the day *after*
the one their own timestamp names under UTC bucketing: `2026-08-03T23:50:00.000-07:00` buckets
to 2026-08-04. The map renders correctly and lies about the day, and a user cross-checking
waterdata.usgs.gov sees the mismatch.

**A now-relative freshness window must not survive into a named day.** Every live reader gates
on `Date.now()` — 6 h for streamflow, 3 h for weather, 30 d for vegetation — and left in place
those windows report *every* historical day as unobserved. Re-anchoring the same duration to
the end of the requested day is just as wrong: six hours before midnight drops every gauge
whose last reading that day was before 18:00. So the window is re-expressed, per reader:

- **streamflow, weather, fire detections** — the day predicate pins the publisher-named day
  exactly, and the window *is* that day. `DISTINCT ON` (site / grid point) then keeps the
  newest reading per entity, which also makes the row cap count entities rather than readings:
  a busy national streamflow day is ~10,900 readings, and paging those by `created_at` would
  have answered with a subset of the country. `created_at` can never date an observation on
  either path — it is a "last touched" column the refresh path rewrites.
- **vegetation** — the 30-day per-cell window slides to *end* at the requested day, i.e.
  `(date − VEGETATION_MAX_OBSERVATION_AGE_DAYS, date]`. A reading after the requested day is
  dropped too, so a past day can never borrow a later cloud-free scene. The
  `stale` / `not_published` probe is bounded to the same day, so a viewport the grid had not
  yet sampled reads `not_published` rather than being captioned merely stale.
- **drought** — weekly, so `resolveDroughtRelease` owns it and both
  `getPublishedDroughtClassification` and `getDroughtMetricAtDate` share it: the newest release
  valid on or before the day, bounded. When a later release is stored, the preceding release's
  coverage ended at its own week and a request past that renders EMPTY
  (`reason: "release_week_not_published"`) — `usdm_history.ingest_release_week` recorded that
  week as `is_gap`, and filling it would paint classes USDM never published. Only at the live
  edge may the newest release carry forward, bounded by `DROUGHT_MAX_CARRY_FORWARD_DAYS`.
  `carryForwardDays` is reported so a carried-forward map cannot read as a same-day measurement.

**A future day is refused, never answered.** `FORECAST_HORIZON_DAYS` is 0 and no capability
publishes a forecast variant, so the only thing a future day could be answered with is the
newest observation wearing a date it does not describe. Each reader returns its own empty
shape with a reason (`not_forecastable` where the payload has a slot for one; a bare `[]` where
it does not, in which case the layer's slider capability supplies the caption). Nothing is
interpolated, smoothed or back-filled on any path — a day with no observation comes back empty
with a reason, never a neighbouring day's value wearing the requested date.

**Cast fractional parameters `::numeric`.** postgres-js binds every non-bigint JS number as an
untyped parameter (OID 0), so one bound next to a `COUNT(*)` resolves as bigint and throws
`invalid input syntax for type bigint` at runtime — TypeScript and the `renderSqlText` test
helper both miss it. Exempt only when the value lands directly in a PostGIS argument whose
signature already declares the type (`ST_MakeEnvelope`, `ST_SimplifyPreserveTopology`).
`expectNoBareFractionalParameter` in the read-model test guards each new statement.

Known gap: `wildfire.getFireDetections` and `getPublishedFireDetections` accept a `date`, but
`useFireData` calls `/api/fires` — a route handler that takes no parameters — so the map's fire
layer is still dateless. Forwarding `?date=` from that route to `getPublishedFireDetections` is
all that is missing; the client must not send one before then, because the route would ignore
it and draw today's detections under the selected day.

## §vegetation-tiles

`lib/vegetation.ts` is client-safe and holds no secrets.

NDVI comes from NASA GIBS (`MODIS_Terra_NDVI_8Day`, EPSG:3857,
`GoogleMapsCompatible_Level9`, max zoom 9), **proxied** through
`app/api/tiles/vegetation/ndvi/[time]/[z]/[y]/[x]`. The browser never calls GIBS
directly.

Proxied rather than direct even though GIBS is keyless and CORS-open, because
`scripts/check-client-provider-urls.mjs` is the binding constraint: an
environmental *fact* must reach the client through a PlantGeo API. The two
standing exceptions in that allowlist (ArcGIS World Imagery, AWS terrain tiles)
are cartographic context, not measurements — NDVI is a measurement, so it takes
the API seam instead of a new exception. The proxy is also where attribution
headers, cache lifetimes, and the upstream identity stay under our control.

The gate skips `/api/` and `src/lib/server/`, which is precisely the seam: the
GIBS hostname is allowed to appear in the route handler and nowhere client-reachable.

Cache lifetimes split on mutability: a dated composite is immutable (7 days),
`default` tracks GIBS' newest and expires in 6 hours. GIBS orders its path
`{TileMatrix}/{TileRow}/{TileCol}`, which is MapLibre's `{z}/{y}/{x}`, so the
proxy passes coordinates straight through after bounds-checking them against the
product's max zoom.

Tiles are 8-day rolling composites, so a month maps to a representative date and
the API returns `compositeDate`/`compositeWindowDays` for honest labelling —
the tile is not "that month's NDVI". Out-of-extent dates 404 at GIBS rather than
returning a blank tile, so a month outside coverage resolves to no URL at all
instead of a dead one, and the proxy relays a genuine 404 verbatim rather than
substituting imagery from a neighbouring date.

Three products remain genuinely unavailable and return empty strings:

- **NDWI** — GIBS publishes no NDWI or any water-index raster. A land/water mask
  or a reflectance-band composite would report something never measured.
- **NDVI anomaly** — needs a climatological baseline this platform has not published.
- **NBR and NLCD land cover** — first-party paths; `ENVIRONMENTAL_TILES_CONFIGURED`
  stays `false` until an immutable publication catalog exists.

`getVegetationSources` therefore reports availability **per product**. A single
collapsed flag would either hide the working NDVI layer or overstate the three
missing ones.

## §community-activity

`services/community-activity.ts` holds the two aggregates that replaced the
governance gates installed in `e38b1fa`. Consumers: `app/api/v1/action-network`,
`trpc/routers/analytics.getDemandDensity`, `trpc/routers/community.getPriorityZones`,
`trpc/routers/teams.getTeamDashboard`.

### Why these gates opened (2026-08-03)

Six surfaces threw `PRECONDITION_FAILED`/503 rather than answering, on the theory
that publishing any community aggregate would leak the locations the ledger exists
to protect. The owner decision reversed that: aggregation **is** the privacy
mechanism, and a dark panel that throws is worse than a dark panel that says
"nothing here yet" — the second one starts working by itself.

So the rule is now: return a correctly-shaped, possibly-empty result. Never
fabricate rows to fill it.

### What the aggregation actually guarantees

`aggregateActivityGrid` snaps `strategy_requests` to a zoom-derived cell and emits
only cell centres with `featureCount` and `voteCount`. No id, author, workspace,
title, or exact coordinate is selected at all, so there is nothing to leak
downstream from a single response.

Two rules together — not the cell size on its own — are what stop a *sequence* of
responses resolving a submitted point:

1. **The cell floor.** A cell never goes finer than `MINIMUM_CELL_DEGREES` (0.01°,
   ~1.1 km) no matter how far the caller zooms in.
2. **Whole-cell membership.** The caller's bbox selects which cells come back and
   never which rows compose one: it is applied as `floor(lon / cellDegrees) BETWEEN
   …`, the identical expression the `GROUP BY` uses, so a bbox takes a whole cell or
   none of it. `MINIMUM_CELL_MEMBERS` (3) is a `HAVING` over the grouped rows, so
   without this it filtered a set the caller had already trimmed.

Rule 2 is load-bearing and was missing until 2026-08-04. With the raw bbox pushed
into the `WHERE`, `?zoom=22&bbox=<one 0.01° cell>` returned that cell's exact count,
and querying the same cell minus a thin strip returned the count without it — about
40 requests per axis resolved one contributor's submitted lat/lon to full float
precision. Do not "optimise" the index-space predicate back into a plain coordinate
range: a snapped coordinate literal and `floor()` can disagree at a cell boundary,
which is the same hole in a smaller costume. The coordinate comparisons that remain
are a deliberately over-wide sargable pre-filter, one whole cell beyond the index
bounds on every side.

`minimumVotes`/`minimumFeatureCount` are safe under the same rule: they can only
include or exclude a whole cell, and both of that cell's totals already ride in its
own properties. The route additionally applies `enforcePublicProviderRateLimit`; it
is unauthenticated and every call is a full grouped scan.

`summarizeStrategyActivity` carries no geometry whatsoever and additionally scopes
to rows the caller could already read directly (own personal requests, plus the
workspaces they are a member of).

### What stayed closed, and why it is not a policy gate

- `analytics.getRegionalRiskSummary` throws. Every field of `RegionalRiskSummary`
  is a non-nullable number or a trend enum, so "no data" can only be expressed as
  `fireRiskAvg: 0` / `riskTrend: "stable"` — a confident wrong answer. See
  `db/analytics.ts`, which removed exactly that bug once already. Opening this
  needs the type widened to nullable and the dashboard taught to render unknown.
- Opportunity waypoints. `agri.opportunity_candidate`, `agri.opportunity_waypoint`
  and `agri.waypoint_access_review` exist in no schema — the message now names
  those tables instead of implying a review is pending.
- `services/priority-zones.ts` stays a no-op. `public.priority_zones` holds 0 rows,
  nothing reads it, and the serving contract forbids reusing it for waypoints. It
  should be deleted, not revived; the aggregates above deliberately compute from
  `strategy_requests` instead.
