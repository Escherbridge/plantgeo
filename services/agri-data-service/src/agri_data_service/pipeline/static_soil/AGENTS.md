# Static SoilGrids saved-asset admission preparation

This package owns local preparation and pixel-level evidence for the six saved ISRIC SoilGrids
v2.0 0–5 cm mean COG/PMTiles pairs. It is separate from the daily ERA5 soil writer and USDA
soil-survey polygons. It has no database, HTTP client, publisher, active pointer or scheduler.
`scripts/prepare_static_soil.py prepare` requires exact source-manifest and independent full-file
receipt pins, verifies all twelve complete objects, and writes a new exclusive directory. The last
candidate manifest binds full hashes, lengths, immutable proposed keys, exact affine support,
nodata, units/divisors, source/license tags and the PMTiles header's exact ramp metadata.
Preparation is not bucket admission, active-manifest publication or production serving.

## Bounds and support

The September 10 proposal supplies these explicit ceilings, now enforced for local preparation:
twelve source objects, 64 MiB per object, 256 MiB combined, 64 MiB of numeric COGs and 64 KiB of
metadata. Limits are checked against pinned receipts before the objects are read. Full-object reads
stop at the pinned length plus one byte. A reader opens six fully rehashed COG byte buffers once;
each point decodes at most one base-resolution block per selected property, with each int16 block
capped at 1 MiB. It performs no network range reads and has no distributed cache refresh behavior.
The preparer is bounded by bytes/rows, not a claimed hard wall-clock deadline. A future remote
admission worker must enforce the separately proposed 600-second process deadline and two-stream cap.

Rasterio reads the raw stored int16 value from a 1×1 window without `out_shape`, overview selection,
resampling or palette decoding. Nodata is masked before dividing exactly once, even though the COG
also declares GDAL's band scale. Point support is the saved bilinearly reprojected EPSG:4326 pixel,
not the original ISRIC projected 250 m cell. Pixel row/column, center and spherical center distance
are returned. Indexing uses the north/west-inclusive, east/south-exclusive affine raster domain.
Out-of-extent and nodata remain separate outcomes; malformed grids or hashes fail the request.
The radius 6,371,008.8 m is the IUGG mean-Earth radius used for the disclosed spherical distance.

`selected_day` is echoed, with `observed_day=null` and `temporal_distance_days=null`. The static
model estimates have no daily observation dates, nearest temporal samples or manufactured absences.
`latest` in the recorded upstream URL and `v2.0` do not establish an immutable upstream revision or
retrieval timestamp. Both unknown fields remain explicit nulls. Every result continues to say
`local_verified_candidate`; wiring a production endpoint or silently relabeling it as admitted is
outside this preparation module's authority. On Windows, callers must select rasterio's bundled
PROJ data before importing it if the host points PROJ at PostgreSQL's incompatible database.

## Admission handoff

The generic owner must accept an exact reviewed candidate digest; obtain a fresh static-soil owner
fence and record the previous active pointer or its verified absence; create all candidate objects
under their immutable keys with full write/readback receipts; publish the immutable manifest; then
CAS the active pointer last under the publication barrier. A failure keeps the old pointer active.
An existing public object's ETag or matched first/last ranges cannot satisfy a full SHA256 receipt.
Rollback CAS-restores the recorded previous pointer while retaining both immutable generations.
Neither the retired raster PostgreSQL publisher nor the old SoilGrids cache warmer may be called.
The static catalogue must advertise replacement snapshots, not daily gaps. Production reader and
agent endpoint integration require a separate owner-reviewed patch after that admission contract.

Primary documentation reviewed for this boundary: Rasterio windowed reads
https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html, PMTiles v3
https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md and the saved ISRIC guidance
https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html. A 1×1 raster window can decode a
whole block; the decoded-block cap is therefore part of the contract, not only the result size.
