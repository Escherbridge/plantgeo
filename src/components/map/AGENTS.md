# Map interaction boundary

Location selection is a privacy boundary. `AgentInteraction` requires an explicit user choice before analysis begins and defaults to an approximate (two-decimal) location; exact coordinates are opt-in. Regional analysis remains informational only: it cannot take external actions, and unavailable data must remain visibly unavailable rather than producing substitute recommendations.

## Popups and hover labels

MapLibre's stock popup CSS hard-codes a white background but inherits its text color from the app, which is near-white under the default dark theme — popups rendered as blank cards until `globals.css` bound `.maplibregl-popup-content` to the `--card` tokens. Popup markup must therefore never hard-code text colors; use the `.map-popup-meta` class for secondary lines so both themes stay readable.

Event layers show the event's own time, not just its ingestion time: a fire carries a discovery date (and, for perimeters, a separate "perimeter updated" time), a detection carries its observation time, and a gauge reading carries when it was measured. Freshness is shown as a relative suffix on the absolute timestamp, never as a replacement for it — "3h ago" alone cannot distinguish a fire that started this morning from a decade-old incident whose record was just refreshed. `src/lib/map/time-format.ts` owns that resolution and formatting; upstream feeds disagree on encoding (WFIGS sends epoch milliseconds, USGS sends ISO, tile properties stringify both), and missing values must render as an omitted line rather than a partial label.

The action-network layer owns viewport cancellation through `useActionNetworkFeatures`. It may display only the bounded, worker-processed response and provides a retry affordance only for retryable failures. Freshness/revision labels are metadata, not a claim that a forecast or recommendation exists.
