# Selected and served dates

## Intervention data drafts

The intervention draft keeps lane, collection method, observation day and evidence link beside
its other fields. Mode switches and navigation retain them; successful submission, explicit
discard and a genuinely new location reset them. Empty data fields do not count as unfinished
work simply because their object reference changed. Nonempty fields protect the original
location even before any geometry is drawn. The community modal uses independent local state.

## Layer date state

`useViewedLayerDays` and `resolveLayerDate` describe selected request context. Daily agents
must retain an explicitly selected missing day so tools can answer that day without silently
falling back to today. Publication availability must not rewrite or remove that selection.

`drawnDayFlagsFromQuery` separately reads a typed answer's publication date: `servedDay` for
ready Parquet results and `observedDay` for published field collections. Explicit nonpublication
or an invalid/missing typed date yields null. Untyped legacy responses retain the existing
request/placeholder bookkeeping through an undefined `servedDate`; this does not certify
their publication provenance. A typed placeholder carries the old served day while the new
request remains pending. No request date is substituted for a typed refusal.

The drawn-day store is transient and leaves the selected-day stores unchanged. Consumers
must preserve explicit null dates. A static request may have no selected date yet return a
real release date; watersheds therefore show the returned release, while unpublished SSURGO
has no drawn date. These signals describe publication dates, not a guarantee of nonempty
pixels: a dated published-empty result remains distinct from unavailable publication.

Typed manager and climate readers opt into publicationMode typed, so absent data on cold
load or error has no served date. Legacy untyped adapters retain request bookkeeping. The
combined streamflow/groundwater water row remains legacy in this bounded change; aggregate
publication-date semantics require separate work and are not certified here.

`useMetricAtDate` retains a loading frame with TanStack's `placeholderData` callback. The
callback attaches the previous query key's date to an observer-only copy, so chained pending
or failed requests cannot relabel the last successful collection. Fetch results, prefetches,
and externally seeded query-cache entries keep their existing raw GeoJSON shape. This date
comes from the query that owns the data rather than render-time ref bookkeeping. A disabled
or unavailable query returns a fresh refusal labelled with the current debounced request day,
even if TanStack still holds a placeholder internally.

<a id="layer-window"></a>
## Per-layer history windows (owner decisions 2026-10-04)

`layer-window-store` holds ONE preset per dated layer -- 7, 30, 90 or 365 days -- keyed by toggle
id and SPARSE: an absent key is the 30-day default, and writing the default deletes the key. It
is persisted like layer opacity (`plantgeo-layer-window`, localStorage, sanitised on merge). The
window itself is never stored: `layerWindowFor`/`useLayerWindow` derive `{rangeStart, rangeEnd}`
from the layer's own resolved day as an inclusive trailing span whose end is capped at the
server's UTC today (`capabilities.serverCurrentDate`), so a forecast-day selection never asks for
days that have not happened and a stored window can never go stale against a moved slider.

Kept out of `time-slider-store` on purpose: that store is polled every five minutes and every row
subscribes to it, while a preset changes only on a click. "Dated" is `hasSelectableDay` -- the same
single rule that gives a row its scrubber -- so static layers (soil survey, SoilGrids, snapshots)
get no chip and no window.

The chip on each dated `LayerRow` is the only window control. The agent panel's former global
"± N days/months/years around each selected map date" control is gone; the analysis request now
posts the global default (`DEFAULT_ANALYSIS_WINDOW`) plus `analysisSelection.layerWindows` for
every visible dated layer, and the server prefers the layer's own window
(services/AGENTS.md §regional-analysis-window). The same window drives the tooltip's one-line
distribution (map/AGENTS.md §window-distribution).
