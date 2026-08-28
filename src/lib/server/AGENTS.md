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
dimension rather than leaving orphans for `agri-service data ingest-geometry-repair` to claim later.

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

Client-side, `WaterDetails`'s watershed tab and `LayerManager` both call
`useWatershedsQuery` — see §proxied-viewport-queries for why neither may build the query
itself.

Both proxied procedures cap viewport **area**, not just WGS84 legality. They are
unauthenticated, they call a third-party API per request, and the Redis key is the
exact bbox — so nothing amortizes a basin-wide ask, and the ceiling is the only
cost boundary. Both go through `cacheGeoJSON`/`getCachedGeoJSON`, which latch off a
dead Redis instead of throwing: the cache is an optimization, and a Redis outage
must not take two map layers down with an `INTERNAL_SERVER_ERROR`.

## §soil-field

`services/environmental-read-model.ts#getPublishedSoilField`,
`drizzle/0014_soil_moisture_field.sql` + `drizzle/0016_soil_field.sql`,
`lib/geo/isobands.ts`, `lib/environmental/soil-field.ts`,
`trpc/routers/environmental.ts#getSoilField`,
`components/map/layers/SoilFieldLayer.tsx`.

**The first layers served out of the model plane.** Everything else on this map reads
`geo.features`. Both ERA5-Land soil fields land in `agri.signal_observation` joined to
`agri.spatial_cell`, on one lattice and one grain:

| Measure | Signals | Unit | Depths |
| --- | --- | --- | --- |
| moisture | `soil_water_content_layer_1/_2/_3` | `m^3/m^3` | 0–7, 7–28, 28–100 cm |
| temperature | `soil_temperature_level_1..4` | `C` | 0–7, 7–28, 28–100, 100–255 cm |

Both carry `support_key = 'era5-land-0.1deg'`, daily at midnight UTC over the 1,568-cell
0.25° `sentinel2-ndvi-0p25deg` PNW lattice for 2022-04-30..2026-04-30, from the
`open-meteo-era5-land-archive` source. Before 2026-08-06 there were **zero** references to
`soil_water_content` or `era5-land` anywhere in `src/`: no reader, no registry entry, no
toggle, no renderer. Temperature was still absent for another day after that.

**One reader, not two.** `getPublishedSoilField(bbox, { measure, depth, date, zoom })` serves
both. They share the lattice, the grain, the source release, the staleness rule, the tier
boundaries and the truncation cap; only the signal name, the unit and the band table differ,
and those come from `soilFieldMeasureDefinition`. The same goes down the stack — one tRPC
procedure carrying `measure`, one `useSoilFieldQuery`, one `SoilFieldLayer` component
instantiated twice, one `SoilFieldSection` in `SoilPanel`. Two registry toggles, though:
they are two measurements of the same ground and a reader may want either, both or neither,
so folding them into one switch would make "off" ambiguous.

**Temperature coverage is partial and must read as partial.** The backfill was mid-flight
when this landed (567 k of the eventual ~6.4 M rows on 2026-08-06, ~15 % of cells at the
PNW-wide coarse tier: 14 lattice nodes against moisture's 96). A viewport can legitimately
hold measured moisture and no temperature at all. That is `reason: "not_published"` with the
panel captioning blank ground as missing coverage — never a drawn value, and never a
disabled switch, because a switch disabled for a gap outlasts the gap and nothing reopens it.

**Ownership boundary.** 0014 created `geo.soil_moisture_observation` (a view) and
`geo.soil_moisture_field` (a set-returning function) in the **serving** plane, reading the
**model** plane. 0016 widens that: the view is replaced by `geo.soil_field_observation`,
which enumerates the (signal, unit) pairs for both measures and derives a `measure` column,
and the function is **renamed** to `geo.soil_field` with `ALTER FUNCTION ... RENAME TO`. The
rename rather than a DROP + CREATE is deliberate — the body was already measure-agnostic
(it takes `target_signal` as a parameter), so re-authoring it would fork the Gaussian-blur
definition into a second copy that could drift. Nothing is created in `agri` and no lock is
taken on a table the ingestion crawl writes. Martin is unaffected: `infra/martin/martin.yaml`
sets `auto_publish: false` and names its function sources explicitly, so a new `geo` function
cannot join a composite and nothing needs restarting.

**Where each step runs, and why.**

| Step | Runs in | Why not elsewhere |
| --- | --- | --- |
| bbox → covered cells | SQL (GiST on `agri.spatial_cell.geometry`) | — |
| resolve the served day | SQL | one round trip answers "which day is drawn" and "what does it look like" together |
| average onto a coarser lattice | SQL (`geo.soil_field`) | repo rule: geospatial aggregation goes through PostGIS, never the client |
| Gaussian blur across that lattice | SQL, as a weighted self-join over neighbours within `blur_radius_cells` | it is a grid convolution, which is a join; moving it out would mean shipping the grid |
| marching squares → isobands | TypeScript, server-side (`lib/geo/isobands.ts`) | **`postgis_raster` is not installed.** Verified on production 2026-08-05: `pg_available_extensions` lists `postgis_raster` 3.6.4 with `installed_version` NULL. Production extensions are now (as of 2026-08-25) exactly: btree_gist, hypopg, pg_buffercache, pgcrypto, plpgsql, postgis, vector. (`pg_stat_statements` is *available* on Railway managed Postgres but is NOT installed -- available is not installed, the same distinction this row turns on for `postgis_raster`.) So `ST_Contour` is unavailable, and installing a raster extension is a far larger change than one layer justifies. The node grid it contours is tens of nodes, so this is cheap; the browser still never sees it. |
| paint | MapLibre `fill` | see §soil-field in `src/components/map/AGENTS.md` for why not deck.gl |

**No new index.** Measured with `EXPLAIN (ANALYZE)` on production 2026-08-06, against a
`agri.signal_observation` already past 2 M rows: a PNW-wide coarse aggregation is 15 ms
and a PNW-wide detail read 27 ms, both on the existing
`ix_signal_observation_cell_time_signal (cell_id, observed_at, signal_name)` — the bbox
resolves the cell list first and the day is then one index search per cell. An index built
here would also lock a table a live crawl is writing to, for no measured gain.

**Three rules every day-windowed reader in this file must follow.** All three were violated by
the original `readSoilFieldCells` and inherited by the climate field; all three are now pinned
by `src/__tests__/services/climate-field-sql-contract.test.ts`, which executes the real
statements against a real PostgreSQL because none of them is visible to a mocked `db.execute`.

1. **Cast every numeric bind.** postgres.js sends a JS number with **no type OID**, so
   PostgreSQL resolves `${day}::date - $n` as `date - date -> integer` and a following
   `::timestamptz` is illegal — SQLSTATE 42846, at PARSE time, on every call. Write
   `${AGE}::integer`. A statement can be dead on arrival while the whole unit suite passes.
2. **Half-open upper bound.** These lanes stamp every row at exactly midnight UTC, so
   `observed_at <= (day + 1)` admits day+1's own reading and `max()` picks it: the map paints
   tomorrow under a caption promising "at or before" today, for every archive day but the
   newest. Use `< ((${day}::date + 1)::timestamp AT TIME ZONE 'UTC')`.
3. **Pin both bounds to UTC.** `date::timestamptz` resolves through the **session** TimeZone
   while both views derive `observed_day` `AT TIME ZONE 'UTC'`. On a -06:00 session
   `'2026-04-30'::date` is 06:00Z and the window disagrees with the day label about where a
   midnight row falls. `(…)::timestamp AT TIME ZONE 'UTC'` is session-independent.

**A day resolver that reads the base table must mirror the view's gates.** `served` and the
`newest*Day` helpers read `agri.signal_observation` directly (rule below: the view may be
referenced only once), so they do not inherit `is_observed`, `quality_flag = 'accepted'`,
`normalized_value IS NOT NULL` or the unit match. Un-mirrored, one rejected or wrong-unit row
on the newest day resolves an instant the view holds nothing at — the whole viewport blanks,
and the panel prints that row's day as "the newest reading for this view", which is false.
Both readers therefore bind the resolved signal's `normalized_unit` alongside its name.

**Two query shapes that look equivalent and are not.** Reading
`geo.soil_field_observation` **twice** in one statement (once to resolve the day, once
to read it) makes PostgreSQL materialize it as a CTE, and the same viewport costs **2.3 s**
instead of 27 ms. `readSoilFieldCells` therefore resolves the day from the base tables
and references the view exactly once. The same trap will catch the next reader of this
view; the comment on that function says so.

**Honest gaps, as everywhere else.** A lattice square with any corner the lane has not
filled is skipped rather than interpolated across, so unfetched ground stays blank. The
archive ends 2026-04-30 while the slider's today is later, so `observedDay` and
`requestedDay` routinely differ and both are published — `SoilPanel` names them. Past
`SOIL_FIELD_MAX_OBSERVATION_AGE_DAYS` (30, matching vegetation) nothing is carried
forward: the answer is `reason: "stale"` plus the `newestAvailableDay` the user should
scrub to. A future day is `not_forecastable`; a reanalysis archive has not run it.

**One unresolved governance question, deliberately surfaced rather than decided.**
`agri.data_source.allowed_client_exposure` is `false` for `open-meteo-era5-land-archive`.
That is the server DEFAULT every generically-ingested source gets
(`source_ingestion.py:248`), not a decision anybody made about this lane: the same row
carries `review_state = 'approved'` and a CC-BY 4.0 licence that expressly permits
redistribution with attribution. Nothing in `src/` has ever read that column. The read
model **publishes** it as `sourceClientExposureApproved` and carries the required
attribution in `attribution`, rather than silently gating the layer off or silently
ignoring the flag. Flipping the column in the warehouse is the owner's call.

## §climate-field

`services/environmental-read-model.ts#getPublishedClimateField`,
`drizzle/0020_climate_field.sql`, `lib/environmental/climate-field.ts`,
`trpc/routers/environmental.ts#getClimateField`,
`components/map/layers/ClimateFieldLayer.tsx`.

The **second** lane served out of the model plane. NASA POWER daily: eight meteorology
signals plus three pilot soil-wetness signals, `support_key = 'surface'`, daily at midnight
UTC over the 397-cell 0.5° `nasa-power-0.5-degree` lattice for 2022-04-30..2026-04-30, from
the `nasa-power-daily` source. It reads like §soil-field above and differs in exactly two
ways, both forced by the data.

**One tier, no isobands, no SQL aggregation function.** The soil field aggregates because a
0.25° lattice ships 1,568 squares PNW-wide; this lattice holds 397 cells in total, which is
smaller than that field's *regional* aggregate. There is nothing to aggregate away, and a
coarser average would only blur the coarsest honest thing the lane holds — so
`getPublishedClimateField` takes no `zoom`, `getClimateField` has no `zoom` input, and
`useClimateFieldQuery` keeps it out of the query key. Adding one later would split one answer
into one cache entry per zoom level for a lane that serves the same cells at every zoom.

**The read dedupes, because the lane has duplicates.** Overlapping archive releases left
~47 k `(cell_id, signal_name, observed_at)` keys carrying two rows apiece. `DISTINCT ON
(cell_id)` with `ORDER BY cell_id, release_retrieved_at DESC, observation_id DESC` picks
exactly one: newest release wins, and the observation id breaks a tie between two releases
retrieved in the same instant. Without it the same viewport paints differently between runs
depending on what the planner emitted first — and neither `retrieved_at` nor the observation
id can be derived downstream, which is why `geo.climate_field_observation` publishes both.

**It obeys the three bind/bound rules and the gate-mirroring rule in §soil-field above**, and
so does the soil field now — the climate reader was written from the soil reader and inherited
all four defects from it, which is why they are documented there rather than here. The four
statements are exported as builders (`climateFieldCellsStatement`,
`climateFieldNewestDayStatement`, and the soil pair) so the contract test can execute the
production SQL rather than a paraphrase of it.

Everything else is the soil field's: the same `resolveRequestedObservationDay`, the same
30-day `MAX_OBSERVATION_AGE_DAYS` with `stale` + `newestAvailableDay` past it,
`not_forecastable` for a future day, `not_published` for uncovered ground, one probe per
covered cell for the newest-day answer, and the view referenced **exactly once** so the 2.3 s
materialized-CTE trap documented above cannot bite the second reader of the same pattern.

**No new index** (`drizzle/0020_climate_field.sql` justifies it): the bbox resolves the cell
list off the GiST `ix_spatial_cell_geometry` with a `grid_name` predicate, and the day is one
search per cell on the existing `ix_signal_observation_cell_time_signal`. That is the access
path measured at 27 ms PNW-wide on a lattice four times this one's size.

**The same unresolved governance question, surfaced the same way.**
`allowed_client_exposure` is `false` for `nasa-power-daily`, which is the server default every
generically-ingested source gets. The reader publishes it as `sourceClientExposureApproved`
rather than gating the layer off or ignoring the flag.

## §proxied-viewport-queries

`src/hooks/useViewportProxiedLayers.ts`, consumed directly by `components/map/LayerManager.tsx`
and, via `components/map/layer-panel/DockDetails.tsx`'s per-region bodies
(`SoilDetailsBody`, `WaterDetailsBody`), by `components/panels/SoilDetails.tsx` and
`components/panels/WaterDetails.tsx`. Applies to the two proxied feeds above and, since
2026-08-06, to the warehouse-backed soil-moisture field as well: what the three share is
not their upstream but the sharing hazard.

**`zoom` is part of the key, and omitting it is not neutral.** `getSoilSurvey` and
`getSoilMoisture` both resolve their render granularity from `zoom` server-side. Until
2026-08-06 *neither* soil-survey caller passed one: `LayerManager` called
`useSoilSurveyQuery(bbox, { enabled })` and the (since-removed) `PanelManager` mounted the
(since-renamed) `SoilPanel` without the prop it already accepted.
`resolveSoilSurveyGranularity(undefined)` falls to `"detail"`, whose 0.02 sq-deg ceiling
`getSoilSurvey`'s `superRefine` then enforces — so at any ordinary zoom the request was
rejected and the region showed its "zoom in" note. The server's whole zoom-adaptive path
existed and was never once exercised. Both call sites now pass `zoom` from the one
`useViewportBounds()` derivation, which is also what keeps them on a single cache entry.

**One query per feed, not one per caller.** A map layer and the details region that
describes it read the same viewport, so they must produce the *same* react-query entry —
not two that look alike. Four things have to agree for that to hold: the bbox derivation,
the placeholder input used when there is no bbox, `staleTime`, and `retry`. Hand-copied
across two files they drift silently, and the failure is invisible on first paint: with
the map on 1 h and a region inheriting `providers.tsx`'s 60 s default, expanding it a
minute later issues a *second* full upstream fetch (~5 MB / ~7 s for HUC12) of data the
map considered fresh for another 59 minutes. `staleTime` is per-observer in TanStack v5
and `refetchOnMount` defaults true, so nothing about a shared key prevents this.

So all four live in `useSoilSurveyQuery` / `useWatershedsQuery`, and the bbox in
`useViewportBounds` — one derivation, called by `LayerManager` and, independently, by each
`DockDetails` body that needs it, which hands the result to its region as a `bbox` prop
exactly as the other viewport-scoped regions already receive one. On the map side callers
supply `enabled`, genuinely per-caller (a map layer is mounted or it is not) and never
part of the key, so it can never split one cache entry into two. On the dock side there is
no `enabled` term at all: `DetailsSection` (`components/map/layer-panel/DetailsSection.tsx`)
mounts `DockDetailsBody` only while its section is expanded, so mounting IS the gate — a
collapsed region issues nothing, an expanded one issues the same query the map already has
open.

The hooks also apply the registry's `permanentlyUnavailableReason` guard to the
*request*, not just to the render, so a details region can never become the sole requester
of a layer governance withholds from the map. `SoilDetails` reads `useLayerVisibility()`
rather than `useLayerToggle()` for the same reason — raw `activeLayers` membership
bypasses that guard.

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
`lib/map/layer-toggle-context.ts`'s `useDebouncedLayerDay(layerId)` on the client — one hook call
per layer row since 2026-08-09, not the single `useDebouncedMapDay()` this section used to name
when one slider served the whole map. The time slider used to report *whether* the warehouse held
a chosen day while every layer kept drawing the live edge; this is what makes the layers draw the
day, and per-layer dates only narrowed WHOSE day each request carries — every warehouse-backed
read still takes at most one optional `date`, and everything below still holds per layer.

**An omitted day and today's date are the same read, deliberately.**
`resolveRequestedObservationDay` collapses both to `{ kind: "live" }`, so first paint runs the
exact query each reader has always run — the one whose cost is measured — and the client sends
no `date` at all when a layer's selection is `capabilities.serverCurrentDate`. That is not
cosmetic: tRPC keys on the input, so passing the date explicitly would mint a second react-query
entry for an identical answer and fetch it twice on load.
`useDebouncedLayerDay(layerId).requestDate` is `undefined` in exactly that case, and also before
capabilities arrive — without them nothing knows which day is today, and reading the browser
clock is the timezone disagreement `serverCurrentDate` exists to prevent. Because most layers now
default to their own `latestObservedDate` rather than to today (see `resolveLayerDate` in
`src/components/map/AGENTS.md`), `requestDate` is set far more often per layer than it was when
one shared slider mostly sat on today.

**Bucket a named day on the publisher's own named day, never in UTC.** In SQL that is
`substring(<iso text>, 1, 10)::date` (`OBSERVATION_DAY`, or `namedDaySql` for one explicitly
named field); in TypeScript it is `publisherNamedDay`, which reads the raw stored string
because `parseZonedObservationTime` has already normalized it to UTC by then. All 16,743
stored USGS gauge readings carry `-07:00`, and 6,279 of them (37.5%) land on the day *after*
the one their own timestamp names under UTC bucketing: `2026-08-03T23:50:00.000-07:00` buckets
to 2026-08-04. The map renders correctly and lies about the day, and a user cross-checking
waterdata.usgs.gov sees the mismatch.

**A now-relative freshness window must not survive into a named day.** The live streamflow and
weather readers gate on `Date.now()` — 6 h and 3 h respectively — and left in place those
windows report *every* historical day as unobserved. Re-anchoring the same duration to
the end of the requested day is just as wrong: six hours before midnight drops every gauge
whose last reading that day was before 18:00. So the window is re-expressed, per reader:

- **streamflow, weather, fire detections** — the day predicate pins the publisher-named day
  exactly, and the window *is* that day. `DISTINCT ON` (site / grid point) then keeps the
  newest reading per entity, which also makes the row cap count entities rather than readings:
  a busy national streamflow day is ~10,900 readings, and paging those by `created_at` would
  have answered with a subset of the country. `created_at` can never date an observation on
  either path — it is a "last touched" column the refresh path rewrites.
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

## §agri-forecasts

The `forecasts` router is a bounded proxy over the agri-data-service's published
forecast serving view (`GET /forecasts/` on `AGRI_DATA_SERVICE_URL`) — the first
HTTP bridge between this app and that service; everything else the two share
arrives through the warehouse. The proxy trims the serving record to what the
band chart draws (quantile points, method, issue identity) but keeps availability
explicit, in the §proxied-viewport-queries idiom: an unset base URL answers
`unavailable`/`forecast_service_not_configured` rather than throwing, and an
empty page stays **published with zero points**, because zero receipts is the
expected production state until a forecast run publishes. The panel words those
two states differently on purpose — one is a deployment without the bridge, the
other is a bridge waiting for data.

One serving page can span several forecast receipts: the publication pointer is
unique per scope, not per series window, so a fresh run arrives interleaved with
the run it superseded. `toPublishedSeries` draws exactly one run — the
latest-issue receipt — and counts what it dropped in
`staleReceiptPointsDropped` rather than interleaving two runs into a saw-tooth
whose caption describes neither. A body that fails the zod contract throws
`ForecastContractError`, which is deliberately NOT transient: the shared
`trpc/upstream-fault.ts` helper (also used by the environmental router) only
relabels timeouts, payload bounds, 429/5xx, and network-level fetch failures as
retryable. The client side pays for this proxy like every other one: one retry,
a ten-minute stale time, and no fetch until the Forecast tab is on screen.

Series keys are derived client-side in `src/lib/forecast/series-key.ts` from the
map viewport's centre cell. That file mirrors the Python naming contract
(`ndvi-daily:sentinel2-ndvi-0p25deg:<lat.toFixed(4)>:<lon.toFixed(4)>`, cells
anchored at multiples of 0.25° — `execution/vegetation_ndvi_plane.py` and
`ingest/vegetation.py` in the service). If the grid, prefix, or coordinate
rendering changes there, that file is the coupling to update; a drifted key does
not error, it just asks for a series that can never exist.

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

## §pre-aggregation

Landed 2026-08-15, under one owner constraint: *"we should not be running analytical
queries for application reasons."* The production timeseries database is capped at 3 GB
of RAM by a Railway cgroup with zero active users. The binding constraint is RESIDENT
WORKING SET, not latency — the owner explicitly accepts up to ~2 s per slider-debounce
pause, because Redis and the client's local-first sync already own caching. Optimise for
a small working set; do not add a DB-side cache to make a read faster.

The rule this pass enforces is not "no aggregates" — `DISTINCT ON` over an index is fine.
It is: **no application request may cause the database to read more rows than it returns
by more than a bounded, documented factor.** Three ways to satisfy it, in descending order
of value: index the predicate; delete the plan pin; pre-aggregate. Only aggregates and
small dictionaries get materialized.

### What each reader now reads

| reader | was | now |
|---|---|---|
| `readObservationWindows` | `GROUP BY` over 4.97M `geo.features` rows | `geo.v_observation_day_census WHERE surface_kind='feature'` (~16,000 rows) |
| `readStreamObservationWindows` | aggregate over ~17M accepted `agri.signal_observation` rows, no usable index | same census, `surface_kind IN ('signal','polygon')` (~19,000 rows) |
| `getMetricAtDate`'s `summary` (no bbox) | `COUNT(*)` over the whole uncapped `candidate` CTE | one `metric_counts` jsonb lookup on `geo.mv_feature_observation_day` |
| `resolveDroughtRelease` | three FILTERed aggregates over an unfiltered `geo.drought_areas` | two index probes on `geo.mv_drought_release_index` |
| `getFeatureCountByLayer` / `getSystemStats` / `layerStats` | `COUNT(*) GROUP BY layer_id` over 4.97M rows, on a publicProcedure | `geo.mv_layer_feature_stats` (11 rows) |
| `getRecentActivity(N)` | `created_at >= now() - N hours` with no index that leads on `created_at` | `SUM(feature_count)` over an indexed range of `geo.mv_layer_hourly_activity` |

The `readStreamObservationWindows` row is the one that mattered most: that query caused a
Cloudflare 524 on 2026-08-15 and took the whole slider system down, streams and
`geo.features` layers alike, because its payload is the only definition of "today" the
client is allowed to read.

`geo.layers` is joined at read time in every analytics reader rather than denormalized into
the matview, and that join is what applies `is_public`. The visibility rule stays in one
place; 11 rows cost nothing.

The axis pipeline in `observationWindowStatement` — five window functions, four GROUP BYs,
two `jsonb_agg`, eight FILTERed aggregates — is UNCHANGED and must stay so. It is the
definition of what the slider draws, and a second hand-written variant would be a second
definition. Only the relation it consumes changed. Over ~35,000 census rows it is a small
sort, not an analytical query in any sense that matters here.

### The plan and index fixes, which matter as much as the rollups

- **`getPublishedWeatherForPoint` lost its `WITH candidates AS MATERIALIZED`.** The comment
  defending it as a plan pin was right about a fat box: it spools the whole weather layer's
  `properties` jsonb plus geometry into a tuplestore before a `LIMIT 8` KNN. It fires on
  first paint of every session and again inside `assembleRegionalContext`. The GiST KNN now
  drives.
- **`readPublishedFirePerimeters` uses `geom && ST_MakeEnvelope(...)`** in place of four
  `ST_XMax`/`ST_XMin`/`ST_YMax`/`ST_YMin` comparisons. Identical predicate; the old form was
  a function OF the indexed column, so `idx_features_geom` could not serve it at all.
- **`readStreamflowGaugesOnDay`, `readWeatherOnDay`, and `getMetricAtDate` each carry a
  redundant-by-data restriction written as
  `geo.feature_observation_day(f.properties)`** — the exact expression
  `ix_features_layer_observation_day (layer_id, geo.feature_observation_day(properties))
  WHERE status = 'published'` is built on. The layer's own day predicate stays the
  authority; the restatement exists so the DISTINCT ON sorts one day instead of a history.
  The comment claiming this index "cannot be created (42P17)" was true of the INLINE
  `namedDaySql` expression and false of the FUNCTION, which is `IMMUTABLE PARALLEL SAFE`
  (drizzle/0015_tile_observation_day.sql). `observation-day-contract.test.ts` is what makes
  the restatement safe — it asserts the two derive the day alike. **Do not delete it.**
- **`readDetailFeatures` sorts on `f.id`**, not `properties->>'id'` — same determinism, no
  per-row jsonb extraction on the sort key of a TOAST-heavy table.
- **`visualization.ts`'s heatmap and flow readers project explicit keys** instead of the
  whole `properties` blob. `getPointData` keeps the blob because it RETURNS it.
- **`tracking.ts` gained bounds.** `getLastPositions` had no time predicate and no LIMIT,
  so it sorted the entire positions table; `getRouteHistory` had an unbounded range. Neither
  is on the 24-surface slider list, so a layer-scoped sweep misses both.
- **`analytics.ts`'s `getFeatureDensity` is deleted** — exported, referenced by no router.
- **`routers/jobs.ts` is unchanged and index-dependent.** `getLanes`, `getRunHistory` and
  `getExhaustedGapWindows` are served by three new `agri` indexes shipped in the alembic
  revision beside drizzle/0029. Fewer relations wins where an index can do the job; the SQL
  must stay servable by them, because `agri.job_run` grows one row per lane per tick forever.

### What was deliberately NOT materialized, and what would have to change first

An honest remaining scan beats a matview that returns a subtly different answer.

- **The four soil/climate field statements** (`soilFieldCellsStatement`,
  `climateFieldCellsStatement`, and both `*NewestDayStatement` LATERALs) still read
  `agri.signal_observation` and its two governed views. They are cell-scoped index probes
  already — the LATERAL shape measured 16 ms PNW-wide against 207 ms for the `GROUP BY` it
  is often "simplified" into — and `src/__tests__/services/climate-field-sql-contract.test.ts`
  is a REAL-DATABASE suite that seeds `agri.signal_observation` directly and executes these
  statements through the driver. Repointing them at `geo.mv_signal_cell_daily` requires that
  fixture to refresh the matview between seed and read. Do the two together or neither.
- **`readAggregatedFeatures` / `readSummaryFeatures`** (`usda-soil.ts`). Both are
  viewport-parameterised in a way a three-tier matview cannot reproduce: the union is scoped
  and simplified to THIS bbox, and `soilSummaryCellDegrees` picks its step off an unbounded
  doubling ladder that is published back to the client as `cellDegrees` and used to place
  every returned point. Pin the tier ladder to a fixed enumerated set first. Both reads are
  bounded today (20,000 and 200,000 input rows, and the union path is gated to ~zoom 10.6+).
- **`readFireDetectionsOnDay`** gets no `geo.feature_observation_day` restriction: that
  function's COALESCE knows `observedAt`/`updatedAt`/`polygonDateTime` and not FIRMS's
  `acqDate`, so adding it would drop exactly the rows the reader's own COALESCE exists to
  keep. Fix by teaching the function `acqDate` or by backfilling `observedAt`, not here.
- **`getMetricAtDate` with a bbox** keeps counting the `candidate` CTE. The census is
  grained `(surface, day)` and carries no geometry, so it cannot answer "N of M observations
  IN THIS VIEWPORT are unlinked". Printing the national figure under a viewport's caption
  would be a false sentence. With the expression index the bboxed count is a bounded index
  range, which is the case measured at 0.85 ms.
- **Martin's eight `ST_AsMVT` tile functions are untouched.** They benefit automatically
  from the new expression index at high zoom. Changing any of them forces a Martin restart,
  and a missing tile function 404s the whole composite — every layer, not one.

### What the pre-aggregation layer DOES change about an answer

Everything else in this section is a claim of equivalence. These four are the exceptions, and
each one is a property of the grain the matview is built on, not of the rollup being wrong.
They were found by an adversarial pass over the equivalence claim on 2026-08-15; anything not
on this list is asserted to be byte-identical to the query it replaced.

1. **`analytics.getRecentActivity` over-counts by up to 59 minutes.** The predicate was
   `created_at >= now() - N hours`, an exact rolling instant; `geo.mv_layer_hourly_activity`
   is grained on the hour, so `hour_bucket >= date_trunc('hour', now()) - N hours` admits the
   whole oldest bucket. Including it is the deliberate choice — excluding it silently drops up
   to an hour of the window the caller asked for, and a "last 24 hours" panel that
   under-reports is the worse failure. The newest bucket can also lag by the refresh interval.
2. **`geo.mv_layer_feature_stats` LEFT JOINs, and the readers filter it back.** The matview
   carries a zero row for a public layer with no published features (`interventions` today),
   deliberately, so an empty layer is reportable as empty rather than absent.
   `getFeatureCountByLayer` and `getLayerFeatureStats` add `published_count > 0` because the
   inner join they replaced emitted no row at all for that case. `layerStats` also takes
   `layer_name` from the LIVE `geo.layers` row, never `stats.layer_name`, so a rename is not
   stale until the next refresh.
3. **Two clock-dependent matviews need a clock component in their watermark, and have one.**
   `geo.mv_drought_observation_day` carries the newest USDM release forward to today, and
   `geo.mv_layer_hourly_activity` materialises a trailing 168-hour window — both advance with
   no ingest at all. Their refresh watermarks carry `(now() AT TIME ZONE 'UTC')::date` and
   `date_trunc('hour', now())` respectively. Without those, the drought census's newest covered
   day sits at yesterday after UTC midnight while `resolveDroughtRelease` serves drought for
   today, and the slider reports a coverage gap for a day the layer is painting. The residual
   window is now one refresh interval, not one backstop interval.
### Deploy ordering, which is not optional

These readers name relations that do not exist until `drizzle/0029_pre_aggregation_layer.sql`
and `scripts/apply-pre-aggregation.mjs` have both run — the matviews are created `WITH NO
DATA` and first-populated by the ops script. There is deliberately **no fallback path** to
the old queries: a fallback silently reintroduces the scan this work exists to remove, and
`agri.mv_forecast_ml_daily_serving` (created, never populated, never scheduled) is the
standing proof that a quiet degradation lasts longer than a loud failure. Deploy the read
paths AFTER the migration.

Every matview is refreshed by the `matview-refresh` pulse lane against a watermark in
`agri.matview_refresh_state`. A reader that needs to know how stale an answer is reads
`refreshed_at` there — a rollup over a stalled ingest lane faithfully materialises the stall,
and staleness must not be mistaken for absence.

## Parquet tRPC cutover

`services/parquet-trpc-readers.ts` is the public boundary over the frozen private Parquet HTTP
contract. It maps `published`, `governed_absence`, `day_not_written`, and
`lane_never_written` to `ready`, `absent`, `not_generated`, and the retained unwritten reason;
recognized bounded transport, payload, and contract failures become a typed
`upstream_unavailable` result. Request-validation, zoom-resolution, and programmer errors still
throw. No reader in this adapter may import a PostgreSQL read model or retry there after a
Parquet failure.

Every viewport procedure supplies the selected publisher day and the live map zoom. The latter
passes through the one `resolveZoomTier` ladder (`z13`, `z9`, `z5`, `z0`). Drought uses the
release route and preserves both requested and served release days; the serving projection has
already converted its warehouse WKB to clipped GeoJSON text. Vegetation keeps its trailing
30-day selection, and fire keeps an exact one-day read whenever the caller names a day. A
vegetation row is public only when `allowed_client_exposure` is literally `true`; one false-gated
row rejects the whole envelope as a contract fault instead of being filtered into a plausible
partial map.

`services/parquet-slider-capabilities.ts` is deliberately fail-closed. The frozen coverage
response is tier-agnostic, and signal coverage has no product axis, so it cannot demonstrate a
lane/day/rung combination or a particular signal product. Migrated PostgreSQL rows are removed
from the public census and no Parquet replacement is advertised until those proof axes land.
The generic signal reader likewise returns a typed contract refusal because post-limit
filtering could silently omit the requested product.

The shared `parquet_ops`/CLI extraction is reconciled at `9553fc8`, on top of the readiness repair
at `fced1e8`. Production activation at `069ef90` binds the server-only
`AGRI_PARQUET_SERVICE_URL` to `http://${{plantgeo-parquet-api.RAILWAY_PRIVATE_DOMAIN}}:8080`;
missing configuration remains a typed fault, never a PostgreSQL fallback. Per-rung and
signal-product coverage are required before the withheld slider rows or generic signal reader can
be enabled.
