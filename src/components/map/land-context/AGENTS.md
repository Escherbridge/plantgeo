# Land-context map interaction

Spec: `conductor/tracks/pnw_land_contact_experience_20260911/spec.md` ("Selection and persistent
browsing"). Parent rules (listener order, once-per-map handlers) live in `src/components/map/AGENTS.md`.

## The click model (2026-09-15)

Features draw only for a selection, and until this date the only writer of `setSelection` on the map was a
listener delegated to the land-context fill layers -- so a selection required a drawn feature and a drawn
feature required a selection. Unenterable even with perfect data.

`LandContextLayer` now owns ONE bare `map.on("click")` per map, `[map]`-keyed, reading the store
imperatively at click time (`useLandContextStore.getState()`), never re-registered by toggles or results.
It resolves two meanings in one place so a single click can never fire both:

1. **On a drawn land-context feature** (`pickRenderedLandContextFeatureId`, which queries only fill layers
   that exist -- a missing id makes MapLibre fire `error`, which `MapView` reports as a basemap fault):
   focus that candidate and pin the panel. The selection is **not** touched. The old handler called
   `setSelection` first, which reset `results` to `[]` under the index it then set and collapsed an area
   selection to a point -- the spec forbids losing the selected project area while browsing.
2. **On bare canvas, with at least one group on**: a new `point` selection at the click. Never the viewport
   centre (the store has no `viewport` mode on purpose). The panel is not pinned: `candidateIndex: null`
   means "no candidate yet", and choosing the first overlap arbitrarily is forbidden. The drawn features and
   `LandContextAccessibleFeatureList` are the candidate pickers.

Meaning 1 is tried first, so a drawn land-context feature always wins. Only then does meaning 2 apply
`isClickOwnedByAnotherSurface` (`click-ownership.ts`): the soil query-point capture flag, a rendered
intervention feature (unconditionally), or a rendered feature of a layer that answers the click on the
CURRENT pointer and is not inspection-suppressed. "Answers the click" mirrors the surfaces that actually
register one: `DEDICATED_CLICK_LAYER_IDS` (the six fire/water popup layers from `hover-fields.ts` plus the
two GBIF occurrence layers, which register their own `map.on("click", id)` and are deliberately absent from
`HOVERABLE_LAYER_IDS`) own it on every pointer; `TOOLTIP_TAP_LAYER_IDS` (drought, watersheds, weather --
most of the PNW when on) own it only on a coarse pointer, exactly as `HoverTooltip.handleClick` gates
itself, because on a mouse they do nothing with a click and owning it would be a dead zone.
`CLICK_HANDLER_IDS_OUTSIDE_THE_SIX` (GBIF and the three botanical point layers) are dedicated owners
although `hover-fields.ts` does not list them so; their proper home is
`LAYER_IDS_WITH_A_DEDICATED_CLICK_POPUP` there (out of this partition -- and until they move,
`HoverTooltip` double-owns the botanical points on touch: tap-pin AND the layer's own select).
**Membership first, suppression second**: `isScalarFieldInspectionAllowed` is only "not suppressed",
true for the basemap's `earth`/`water` fills under every land pixel, so applied to any rendered feature it
would own every click and reinstate the deadlock. That predicate is the same one `MapView`'s agent-popup
handler applies, hoisted so the two cannot drift; `MapView` in turn yields to land context while a group is
on (`landContextOwnsClick`).

**MapView behaviour change, for the record (2026-09-18).** At HEAD the agent-popup guard swallowed a left
click whenever ANY rendered, never-suppressed feature was under it. On the two VECTOR basemaps the
unfiltered `earth`/`water` fills are under every pixel of the PNW extract, so the popup fired only beyond
the extract; on the SATELLITE style (raster layers plus one `places-label` symbol) it worked everywhere
except under a label. After this change it opens over vector ground unless a genuine owner has the click.

**Residual gap.** The drawing tools (`ServiceAreaDrawTool`, terra-draw in `drawing.ts`) publish no
"I am capturing clicks" flag, so with a group on every polygon vertex click still replaces the point
selection and fires one `resolveBoundaryAtPoint` per vertex. Closing it needs a store flag set by the
drawing tool (outside this directory) that `isClickOwnedByAnotherSurface` then reads -- one line here.

## Geometry

The frozen contract carries hex WKB (`BoundaryVersionRef.geometryWkb`). Browser code may not import the
decoder (`scripts/check-client-server-imports.mjs`), so the router decodes once per result
(`attachDecodedGeometry`, `src/lib/server/services/land-context/geometry/`) and ships `geometry` beside the
contract fields. `useLandContextQuery` declares that shape structurally and maps `null` to an EMPTY
`GeometryCollection`: listable and selectable, draws nothing, fabricates nothing. Malformed WKB is nulled
and stated in `unresolvedGaps`, never swallowed and never a 500 for the whole response. The hex is stripped
once decoded (`geometryWkb: null` on the wire) so the payload stays within the bytes the reader budgeted
against `MAX_RESPONSE_BYTES`; `geometry`, not the hex, says whether a shape existed.

## The notice

`LandContextStatusNotice` is the only surface that repeats the reader's own gap strings. It derives, per
family, "click a point first" / "no admitted source answered (stated gap: ...)" / "no features here" /
"lookup failed" from `selection`, `queryStatus`, `results` and `resultMeta.coverageNotices`. Same pill
classes and `{layerId, tone, message}` shape as `LayerManager`'s `parquetLayerFaults` so the array can be
spliced into that stack; anchored bottom-centre only so two stacks never paint over each other at `top-12`.
