# Land-context reference plane

## Published families and honest availability

`land-context-boundaries` publishes admitted BLM surface-management records. Its census is
publication evidence for the BLM switch only. It does not establish that county parcels,
electric utility territories or state-managed land sources are available. The dock lists those
families as unavailable without switches. An unavailable family must not capture map clicks.

The PNW manifest binds BLM; the serving census still decides whether a release can be read.
Other regions return `source_unbound_for_region` before any query. Region binding alone never
enables a family switch. Publication failures remain failures, not absence claims.

## Geometry, identity and bounded resolution

The native tier preserves a namespaced stable serving identity plus `source_native_feature_key`.
Coarse BLM rungs dissolve by source/state and clear the source-native key; these are aggregated
management areas, never parcels. The API retains `aggregation_basis` and a nullable source-native
identity. Generated serving identifiers are not represented as original agency identifiers.

The Parquet plane projects `geometry_wkb` into clipped GeoJSON. The reader validates Polygon or
MultiPolygon coordinates and uses that geometry for exact point containment and rectangle
intersection, preserving holes, disjoint parts and boundary touches. Legacy hex WKB remains
supported. `attachDecodedGeometry` transmits geometry once alongside the source reference.
The planar tests describe the published geometry, not survey precision or legal title.

Viewport reads pass the map zoom tier. The server selects the finest published tier no finer
than that request whose area budget admits the view. Area is capped at 1,600 square degrees;
responses remain bounded to 200 boundary features and 2 MB. Coarse geometry is how a regional
view fits those budgets. There is no silent row-count truncation. Partial results retain a
separate coverage notice, and data-phase transport/schema failures retain a typed failure.
A successful read without matches does not prove geographic coverage.

## Public office routes

`land-context-offices` carries published office jurisdiction polygons; `land-context-contacts`
contains documented public routes keyed by `blm-field-offices:<ADM_UNIT_CD>`. Surface ownership
identifiers are not office identifiers. A point or selected area first intersects office
geometry, then one contacts GET supplies all matching office subjects. Geographic overlap does
not establish responsibility for a particular program, legal authority or permission.

The panel retains the selected boundary's source evidence separately from office matches.
No source-native parcel key is invented for a dissolved aggregate. Contact routes keep their
source verification date and documented route meaning. No contact messages are sent.
Parcel-key indexing and county-level coverage products remain unavailable.

## Annual estimated crop cover

`crop-cover` is a separate USDA CDL derived product, never an administrative boundary family.
Its cells summarize classified imagery; they do not identify parcels, title or utility service.
The observed year, source release day, source and analysis resolution, aggregation size,
estimation method, per-class areas and exact source-manifest digest remain attached to each cell.
Crop fraction divides classified cultivated area by the full cell area. Classified fraction
states the supported share; neither fraction is a confidence score.

The dock uses an annual publication edition selector as an explicit exception to daily sliders.
Only singleton published census ranges and the latest published release are offered. The chosen
release day is passed as `asOfDay`; the reader rejects a response newer than that day. Observed
year is displayed from rows, never inferred from the publication year. UI and read-only agent
tools use the same reader, with a 2,000-cell and 2 MB response ceiling. NoData or unreleased years
are not fabricated. Transports and schema failures render as failed reads.

Publication switches require the same release on all four rungs. Crop editions are discrete annual
partitions, selected from their common published days. Crop bbox ceilings are 0.5/8/128/64800
square degrees at z13/z9/z5/z0, keeping ordinary regional views on the coarse classified-area grid.
