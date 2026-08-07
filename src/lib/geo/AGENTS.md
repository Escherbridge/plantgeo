# `src/lib/geo` — module notes

Pure geometry. No network, no database, no React. Everything here is a total function over
plain numbers, so it can run on the server (where it currently does) or in a worker without
changing.

## §isobands

`isobands.ts` turns a regular lattice of scalar samples into dissolved, closed isobands.
Built for the ERA5-Land soil-moisture field; nothing in it is soil-specific.

**Why it is here and not in PostGIS.** `ST_Contour` needs the `postgis_raster` extension.
Verified against production 2026-08-05: `pg_available_extensions` lists `postgis_raster`
3.6.4 with `installed_version` NULL, and `pg_extension` holds only postgis, timescaledb,
timescaledb_toolkit, vector, pgcrypto and plpgsql. Installing a raster extension to contour
tens of grid nodes is not proportionate, so the contouring runs here — over a grid the
database has *already* aggregated and smoothed, so this is not client-side aggregation
sneaking back in. See `src/lib/server/AGENTS.md` §soil-moisture for the full split.

**Why it is not in the browser.** It could be. It is not, because the whole point of
`geo.soil_field` is that the grid never crosses the wire.

**Marching triangles, not the 81-case isoband table.** Each lattice square is split into two
triangles on a consistent diagonal, and each triangle is clipped against the band interval
by two Sutherland–Hodgman passes in value space. That is the same result as the textbook
case table with a fraction of the case analysis, and it degrades gracefully: a square with a
missing corner is simply not emitted, which is what makes absent coverage stay blank instead
of being interpolated across.

**The dissolve is the load-bearing part.** Without it a regional viewport is ~500 clipped
triangles — *more* geometry than the discrete cells the aggregation exists to replace. The
union is done by directed-edge cancellation: every piece contributes its boundary
counter-clockwise, an edge traversed once in each direction is interior and cancels, and
what survives is the outline. That is exact, not approximate, and it is exact **only**
because the pieces are vertex-conforming.

Vertex conformance is the one invariant to preserve if you touch this file:

- A crossing point is always computed from the **original triangle edge**, with its two
  endpoints in a canonical order (lower lattice index first). Two triangles sharing an edge
  therefore compute bit-identical coordinates for the crossing on it, and the edges cancel.
- After the first clip pass, a polygon edge may no longer be an original triangle edge.
  `segmentOriginalEdge` resolves it back to one before the second pass interpolates.
  Interpolating between two already-clipped vertices would be mathematically equal and
  bitwise different, which silently breaks cancellation and leaves the interior edges in.
- Segments that lie along a clip line are interior to a triangle and shared with nobody, so
  they are exempt (and cannot be crossed again anyway: on the low clip line every value
  equals the low threshold).
- Coordinate keys are therefore exact, never rounded. Rounding would merge genuinely
  distinct points.

**Ring tracing.** At an ordinary vertex exactly one boundary edge leaves and the walk is
unambiguous. At a pinch — where a band touches itself at a single point — several do, and
the next edge is the first one clockwise from the way we arrived, which is standard planar
face traversal and the only choice that cannot produce a self-crossing ring.

**Failure is bounded, never silent.** If a ring does not close, `chainRings` returns null
and that band alone falls back to its undissolved pieces. The map stays truthful and the
payload grows; dropping the band would lose data. A hole that cannot be nested becomes its
own ring for the same reason.

**Collinear vertices are removed exactly**, by a zero cross product, never by a tolerance.
A dissolved band runs along lattice nodes wherever its edge is straight, so a plain trace
carries a vertex every lattice step down an edge that needs two. Exact removal cannot move
the boundary by an ulp; a tolerance-based simplifier could.
