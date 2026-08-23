"""Per-lane reconciliation of written Parquet against the SOURCE SYSTEM (L3 `pipeline`).

One module per stream, named by slug. Validation lives here rather than in `method` because it
needs the network: comparing a lane against its own intermediate state only proves the code
agrees with itself. A lane never imports another lane -- enforced by
`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`.
See `conductor/code_styleguides/layer-lanes.md` §4.

EVERY MODULE HERE PINS ITS ZOOM TIER AND ACCEPTS NO `zoom` ARGUMENT. Each declares
`WRITTEN_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]` -- the base rung, the one nothing generalised,
derived from the ladder rather than spelled as a literal so a fifth rung added above cannot leave a
validator silently checking a tier the writer abandoned.

This is the deliberate opposite of `planes/AGENTS.md`, where every public function takes a
`requested_zoom` and resolves it. The two directories ask different questions. A plane answers a
viewport, which genuinely has a zoom. A validator asks "did the writer transcribe the source
faithfully", and the writer writes one rung, so there is nothing to parameterise -- while a `zoom`
argument would let a caller aim the question at a rung no export targets, where every day lists
`missing` and the report reads as a total ingest outage rather than as derivation lag. That is a
different defect with a different owner, and misattributing it is worse than not checking at all.

Coarser rungs are produced by the DERIVATION step, downstream of these lanes. Whatever validates
that a coarse rung faithfully generalises its base tier is derivation's own reconciliation to write,
and it belongs beside derivation rather than folded in here: its source system is this warehouse,
not the internet, which is the exact distinction §4 draws.
"""
