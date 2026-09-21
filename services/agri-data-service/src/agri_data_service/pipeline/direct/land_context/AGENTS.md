# BLM current reference snapshots

## Scope and source decisions

This package publishes three independent reference products: `land-context-boundaries` contains
only BLM-managed surface, `land-context-offices` contains administrative field-office jurisdiction,
and `land-context-contacts` contains the official office identifier/name and publisher-listed state
website. Administrative jurisdiction is never substituted for managed surface. No parcel ownership,
legal responsibility, individual, phone, email, or forwarding relationship is inferred.

The reviewed source definitions are in `products.py`:

- OR/WA: [BLM regional Ownership](https://gis.blm.gov/orarcgis/rest/services/Land_Status/BLM_OR_Ownership/MapServer/0),
  `PROPERTY_STATUS = 'BLM'`, keyed by `GLOBALID`. The live source returned 41,190 features on
  2026-09-20. The captured inventory is clipped to the Census OR/WA union; outside-state fragments are excluded from serving, with original responses retained. This is current surface-management context, not a cadastral rights assertion.
- Idaho: [national SMA](https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer/1),
  BLM records intersecting the Idaho bounding rectangle, clipped to the Census Idaho polygon.
  `ADMIN_ST` identifies administrative organization and is deliberately NOT the filter: Oregon's
  administrative code also covers Washington. The national service is limited-scale; its query
  requests `maxAllowableOffset=0.0001` degrees, recorded in immutable evidence. This display
  generalization is not a survey measurement. `OBJECTID` identifies this captured release only;
  `native_feature_version` and the manifest identify its immutable content.
- [Field offices](https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3),
  captured by unique `OBJECTID`, includes Oregon/Washington and Idaho administrative units.
  Multiple source features for one `ADM_UNIT_CD` are legitimate: every piece is retained in an
  office-level union, with a hash of constituent feature versions and the source feature count.
- [Census state masks](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0),
  the three actual state polygons, provide physical-state context. Regional feature state is the
  state containing its representative interior point, an explicitly recorded derived attribute;
  it is not substituted from BLM's administrative state. Source polygons are clipped to their admitted regional mask, retaining the original native key and contributing source count. A polygon spanning the OR/WA boundary remains intact within that union; its state attribute is representative-point context.

The earlier Idaho regional SMA endpoint now answers ArcGIS `Service not found` (404). It is
deferred until a successor endpoint, schema, native identifiers, and complete population can be
admitted. National SMA supplies useful current Idaho context in the meantime. Grazing allotments,
wilderness/designated areas, mineral interests, private parcels, utilities and state-managed lands
are distinct legal/source interests and are not silently appended to this surface product.
BLM's [dataset disclaimer policy](https://www.blm.gov/policy/im-2014-029) and the project's existing
rights-gate evidence admit this federal GIS material with attribution and accuracy qualifications.

## Capture and time

The history floor is first admission, 2026-09-20. These endpoints expose current mutable snapshots;
there is no synthetic daily history, retrospective source date, or forecast. `release_day` is the
UTC capture day. The unknown source-effective timestamp stays null. Source metadata, exact query
parameters, decoded UTF-8 response entities, and all geometry responses are durably archived under
content hashes before any serving object is written. A manifest binds the whole capture interval.

Every product proves a count/ID match, unique source keys, exact requested-versus-returned IDs for
each page, and matching before/after inventories and metadata. Transfer-limit flags, incomplete
pages, caps and malformed geometry refuse the complete capture. Because the source offers no
transaction token, this is an interval-consistent capture, not proof that geometry could not change
without an inventory/metadata change. Limits are 100,000 records per product, 600 requests,
64 MiB per response, 512 MiB total, and a finite deadline. Unchanged content is identified across
all four inputs, including the Census masks; archived source checks are retained without rewriting
unchanged serving data.

## Publication and recovery

`forward.py` owns the bounded command and `WRITER_CONTRACT`. Its package lock is acquired before
capture; the shared publisher also takes each ordinary lane-day lock. Tables are normalized before
a durable pending pointer is installed. Publication uses `fill_one_lane_day`, including its part
pruning, all-rung derivation and last-written base completion marker. Every physical rung's source
manifest is checked, and the base is read back against the reconstructed input. These products are
`static_lookup` lanes: daily availability indexes and bootstrap are not applicable. Immutable
source manifests, completion markers, and the pending/published state retain typed capture evidence.

Reconciliation reads the complete physical part set and compares its row and part counts with
each completion marker before allowing an unchanged result. When the marker carries part receipts,
their paths, SHA-256 digests, row counts and byte counts must also match exactly. Legacy base
markers without those receipts retain count and source-manifest checks; they do not claim byte
verification. Missing or concurrently pruned parts select replay; unrelated storage failures propagate.

The pending pointer remains until all three products have four verified physical rungs and matching
base readback. A later invocation replays the original hash-verified response graph, retaining its
original capture day. It does not backdate a fresh fetch to fill a failed old snapshot. The runtime's command deadline must also bound synchronous DuckDB/object
operations; Python's async timeout principally bounds asynchronous source operations.

- `--mode refresh`: acquire a new source check, publish changed content, or repair pending work.
- `--mode reconcile`: inspect every stored rung and author repair by replaying the admitted capture.
- `--mode backfill`: restore the admitted current snapshot when missing; no historic snapshots are invented.
- `--capture-manifest SHA`: replay an already archived capture, useful for initial operational proof.

Separate scheduled forward, reconciliation and recovery turns provide the three lane obligations.
No pre-floor absence markers are authored. Unexpected empty BLM populations are failures, not
claims that all public land disappeared. Census masks are the source support, so an arbitrary bbox
flag would change the admitted population and is intentionally unavailable.

## Geometry ladder and routes

Base surface serving keys are `serving:OR:<source-key>`, `serving:WA:<source-key>`, or
`serving:ID:<source-key>`. These are explicit serving identities; `source_native_feature_key`
retains the actual source key. The existing hierarchy derivation groups them by the real physical
state prefix at z9/z5/z0, dissolves managed surface, and applies the shared topology-preserving
simplifier. Coarse keys (`serving:OR`, etc.) are aggregate identities; the original native key and
per-feature version become null. Source namespaces remain constant within each state group.
This is a map display estimate with at most three coarse surface rows, not an approximation of
legal parcel boundaries. Source feature counts are summed and provenance remains bound.

Office polygons use geometry simplification without dissolution; contacts use passthrough on
all four required rungs. Contact subjects are `blm-field-offices:<ADM_UNIT_CD>`. A surface selection
must spatially query the separate office product to find geographic candidates, then resolve those
office subjects; there is no fabricated 41,000-row parcel-to-duty assignment. Future/filler agency
dates become null. `ADMU_ST_URL` is a publisher-listed state site and route status stays `unverified`;
it is not represented as an independently verified individual contact or a program-specific duty.

The generic Parquet reader clips and serves `geometry_wkb` as GeoJSON. `geom_wkb` stays null for
compatibility with the original front-end minimum schema; duplicating unsimplified hex WKB would
defeat the selected-rung and viewport geometry contract. No new Python plane is needed.
