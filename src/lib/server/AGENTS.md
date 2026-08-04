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
