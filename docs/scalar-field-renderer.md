# Scalar field renderer rollout

The first frontend slice supports measured vegetation only. Set the build-time allowlist
`NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS=vegetation` to opt into its WebGL2 value surface.
Unset or empty retains native rendering. Other family names do not enable unfinished paths.
Changing this public variable requires restarting the local build/dev process.

The surface colors each served cell with its own NDVI value using the shared legend ramp.
It does not smooth, resample, fill holes, infer support for points, or classify water as missing.
Negative NDVI is valid. Different observation days remain independent cells. Numeric labels
and cell boundaries become visible as the served supports grow on screen; hover and touch
inspection report the exact served value and actual cell observation day.

The native polygon source remains authoritative for inspection and runtime fallback.
Globe, terrain, pitched views, unsupported WebGL2, invalid geometry, and GPU/style failures retain native
cells. Returning to a supported view may restore the optional surface; an empty collection
clears both representations. Satellite imagery retains its existing independent lifecycle.
Empty or refused replacements immediately withdraw the previous cells, labels and inspection,
including any open caption, even when a pending label relayout delays clearing the native
source. Paint and inspection stay suppressed until a later accepted populated source is ready.

The first profile accepts a uniform rectangular grid with compatible day, unit and declared
support dimensions. Mixed-day collections use the native cells, preserving each cell's day.
The handoff uses 64 CSS pixels of projected support spacing. Both paths paint the same
piecewise constant values and opacity, so they switch directly without stacking two
translucent fills. This differs from the archived proposal's 32–48 pixel blend between a
smooth surface and points: the current contract permits exact cells at every zoom.

This option does not enable weather or climate surfaces. Weather keeps its temperature and
wind symbol ordering, and precipitation/soil-wetness keep their existing discontinuous
rendering. Scientific support contracts take precedence over the older scalar-dot audit.

Run the standalone synthetic fixture with `node e2e/scalar-field-fixture/run.mjs` after
installing the repository dependencies and a Playwright Chromium runtime. It makes no
production requests. Its output is local evidence, not a production release gate.

The multiscale Conductor track retains the open published-day, dense-basemap, conservation,
hardware, and performance checks. Keep the flag unset for general rollout until those gates
are reviewed.
