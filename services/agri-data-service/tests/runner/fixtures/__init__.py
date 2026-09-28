"""Conformance strategies for the five lane natures (FR-3), resolvable by S14 under package `tests.runner`.

`fixtures.grid_refuse` (a settled grid lane with `probe_edge`), `fixtures.point_recheck` (a point
`write_and_recheck` lane), `fixtures.release_series` (release days, 404 is unsettled),
`fixtures.static_watermark` (a static lookup read at its watermark); the fifth nature, the
precedence transform, is the real `transforms.precedence`.
"""
