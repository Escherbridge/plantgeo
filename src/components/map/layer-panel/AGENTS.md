# Layer Panel coverage evidence

`describeCoverageEvidence` in `layer-coverage-track.ts` composes the active Layer
Row's coverage caption. `availability` identifies published availability-index
provenance; `census` means coverage was discovered from stored files. An omitted
authority supports neither claim. The index artifacts are checksum-bound, but a
capability can come from a pointer-bound rollup without recomputing the generation
checksum for that response. Captions therefore report provenance without promising
a fresh checksum check or a production-policy verdict.

The source publication ceiling and the newest usable day are separate facts. A
bounded-carry release can remain usable beyond its publication ceiling, so the
caption must never clamp selection or call that carried tail a new publication.
An available edge before the ceiling is stated as an availability shortfall; it
does not prove a writer backlog or missing observations at every map scale. The
ceiling's distance from today uses `serverCurrentDate`, never the browser clock.
This distinguishes source holdback from an additional coverage delay.

Snapshot rows may report provenance and a stated ceiling but do not acquire a
daily lag claim, time axis or per-day controls. Static lookup census is valid
evidence and is not described as a failed or unbootstrapped index. Missing legacy
fields remain unstated; a missing ceiling never defaults to today. The existing
typed availability/status copy and coverage geometry continue to own their
separate claims. The row attaches its visible caption to the layer switch with
`aria-describedby` so its evidence is available to keyboard and screen-reader users.
