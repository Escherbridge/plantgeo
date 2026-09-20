# Selected and served dates

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

The regional-intelligence store holds an analysis history scale and number of calendar units on
each side, independently of sparse per-layer map dates. These transient controls survive a new
chat so the chosen analysis scope stays active; they are read afresh when a question is sent.
