---
type: integration-handoff
track: intervention_boundary_authoring_20260911
---

# Boundary authoring documentation handoff

No MapView or LayerManager mount patch is required. `InterventionSubmitModal`
mounts `InterventionBoundaryEditor` under the existing `MapProvider` inherited
through the map manager's Community section. Its portal is outside the dock DOM.

Append the following sections to the shared files after verifying their base
blobs. This packet deliberately leaves shared AGENTS files unchanged.

## src/lib/map/AGENTS.md

Base blob: `d0b70e5174428db6fb180a77b55a4dc691c4b1c6`

### Intervention boundary authoring

`intervention-boundary.ts` is the browser-safe authoring contract for an explicit
Point or one Polygon exterior ring. Polygon and opposite-corner rectangle tools
produce closed, counterclockwise rings. Validation rejects duplicate vertices,
self-crossing/touching edges, non-finite or out-of-range coordinates, poles,
antimeridian-spanning sites, and spherical areas below one square metre. Area
uses the spherical longitude/sine-latitude formula, not Cartesian degrees.
The UI ceiling is 4,095 drawn vertices plus ring closure, bounded by the server's
4,096 positions per ring and 10,000 total positions; the 900,000 byte geometry
cap leaves room under the 1 MiB request cap for form fields and RPC metadata.
These local authoring restrictions do not redefine the server's existing
Point/Polygon/MultiPolygon contract or imply that a browser validation replaces
server topology checks.

## src/components/map/AGENTS.md

Base blob: `7c8ba0a8a82e751f1f1eec9485db88fe58323b9c`

### Intervention boundary editor

`InterventionBoundaryEditor` owns one temporary interaction through a portal
covering the map canvas. Primary pointer-up handles mouse, pen and touch; arrows
move the next-point marker, Enter adds, Backspace undoes, Ctrl+Enter finishes,
and Escape cancels. Controls retain normal keyboard activation and trap Tab
within the editor. Global map shortcuts are suppressed while it owns focus.
The overlay receives pointer events before the map, preventing the same gesture
from opening agent popups, selecting rendered features, or moving query pins.
Every map gesture handler's prior enabled state is captured and restored; an
already-disabled dragPan handler stays disabled after drawing. The dock's DOM
visibility is temporarily hidden rather than closing/unmounting its form,
so the full mobile canvas is available while draft text and consent survive.

The temporary GeoJSON source renders the first point, next point, edges and fill.
Its `style.load` listener recreates all layers from the current draft after a
basemap replacement. It deliberately does not gate this listener on
`map.isStyleLoaded()`, which also waits on sources unrelated to drawing.
The installed MapLibre source confirms `style.load` follows stylesheet readiness
and the `Style._checkLoaded` add-source guard. Only the initial not-yet-loaded
stylesheet error is deferred to this event. Cleanup removes listeners, layers,
source and temporary interaction changes. The editor does not touch the
environmental serving lane or persist drafts to shared/global storage.

## src/components/panels/AGENTS.md

Base blob: `ecd1fc2634494734476482cc2e2d0c8ba30814ca`

### Recommendation site authoring and revisions

`InterventionSubmitModal` starts with no implicit geometry. A contributor draws
a Polygon or rectangle, chooses a point on the map, or explicitly accepts the
map centre as a Point. Drawing hides only the form surface; returning from the
editor preserves form text, consent and the previously saved geometry on Cancel.
Finish validates and replaces the saved site. Clear removes it and disables
submission until a replacement is chosen. A successful submit or revision uses
the saved GeoJSON, not a substituted map-centre pin. Consent explicitly describes
the public visibility of location and boundary after a reviewer publishes.

Community rows distinguish loading, empty, auth, access, provisioning and service
failures, and poll at 30 seconds for reviewer outcomes. Publication is labelled
Published; historical Approved is labelled Awaiting publication review. Rejected
and revision-requested rows expose Edit and resubmit only when the current user
is the original submitter and has the required workspace role. Revision uses
`reviseIntervention`, which must recheck author and workspace authorization.
Existing MultiPolygon and polygon holes are preserved on resubmission; entering
a new drawing explicitly replaces the whole site, as disclosed in the form.
Cancel retains the complex original. The editor itself only authors one ring.

Focused tests: `intervention-boundary.test.ts`, `InterventionBoundaryEditor.test.tsx`,
`InterventionSubmitModal.test.tsx`, and the existing `CommunityDetails.test.tsx`.
Execution belongs to the integrated final sweep; this packet claims no test pass.
