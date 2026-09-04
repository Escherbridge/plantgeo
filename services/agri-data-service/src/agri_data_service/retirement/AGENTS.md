# Drop-packet engine

Decision D1 of `conductor/tracks/environmental_postgres_retirement_20260904/spec.md` lets an
environmental relation be dropped only once three things hold AND are recorded in
`evidence/drop-packets/<relation>.md`: a counted parity receipt, a repository-wide zero-reader proof,
and a `pg_dump` archived to R2 with its key and sha256. This package assembles that packet and judges
it. `scripts/build_drop_packet.py` is the operator surface; `scripts/AGENTS.md` §"Drop-packet builder"
is the operator-facing note. This file is the module-level "why".

## The one architectural constraint everything else follows from

**Nothing here opens a database, a bucket or a socket.** Not as a phase, as a property. Production was
unreachable when the package was written (`plantgeo-job-executor` pointed at a database with no `agri`
or `geo` schema), and a packet builder that needed production in order to say "blocked" would be
useless in exactly the situation where a blocked answer matters most. Every entry point is a pure
function of the checkout plus values an operator recorded, which also makes every refusal reproducible
by the next reader and makes it structurally impossible for the tool to fire a production action.

That property is load-bearing twice over. It is also what earns the `retirement_tooling` scan surface:
the package names relations in order to reason about them, so without an exemption it would report
itself as a reader of every relation it ledgers. `tests/retirement/test_readers.py::
test_the_retirement_package_cannot_reach_production` asserts the exemption against the imports rather
than trusting this paragraph.

## Module boundaries

`ledger.py` PARSES the A3 inventory rather than transcribing it, so the tool cannot drift away from
the evidence file the track cites; a relation absent from it is refused rather than synthesised.
`readers.py` walks the surfaces D1 names and reports what it finds. `parity.py` BINDS to wave B's
per-layer parity modules and normalises their three different receipt shapes; it computes no
comparison of its own. `archive.py` renders commands and never runs one. `packet.py` assembles and
judges. Dependencies run one way: `packet` → {`ledger`, `parity`, `readers`, `archive`}, `ledger` →
{`parity`, `readers`}, and nothing points back.

## Why there is no fourth parity comparison

`pipeline/direct/vegetation/parity.py`, `.../weather_observations/parity.py` and `.../drought/parity.py`
each encode their own layer's exporter grain, governance predicate and sparse-history rule, and none of
that survives generalisation — vegetation counts `(cell, grid_name, metric_name, metric_unit)` because
its exporter does; drought compares for EQUALITY per day because a direct-fetched release reproduces
every USDM class or none; weather-observations counts a population no upstream archive can reproduce.
A generalised comparison would be wrong in three different ways at once. So this package reads their
receipts and, where no such module exists, emits `parity: unavailable` and blocks. **A number nobody
computed is worse than an admitted absence**, which is the same rule D3's manifest-trusted provenance
class exists to keep.

Receipt recognition is STRUCTURAL, not by a caller-declared name, so a receipt captured from the wrong
layer's command cannot be filed under the layer whose packet is being built. An unrecognised shape
raises; it never normalises into a permissive default.

## The inventory is an input, never an authority

`evidence/retirement-inventory.md` is a hypothesis per row. It classes `agri.spatial_cell` "drop now";
the tree holds 302 references across 88 files, including the vegetation forward writer's lattice, the
exporter's `INNER JOIN` and the vegetation parity baseline itself. So the scan outranks the ledger
everywhere, and when a "drop now" row meets a live consumer the packet raises a second, louder
`inventory_contradicted` blocker naming the row that is wrong rather than quietly out-voting it. The
`SURVIVAL_DEPENDENCIES` registry is belt-and-braces on the same case: it keeps the refusal alive if a
future edit ever narrows a scan surface, and it records the correction so the wrongness is documented
instead of merely overridden.

## Three dispositions, because a grep cannot tell a read from a definition

A hit in `drizzle/0038_tile_low_zoom_routing.sql` is either a tile function SELECTing the relation or
the migration that CREATEd it, and a substring match sees the same characters either way. Rather than
guess, schema hits are their own disposition: they never clear a packet alone and they are surfaced as
objects the drop migration must itself account for. Documentation hits are recorded and never block —
the c2 form's own rule. Consumer hits block. An exemption is ASSERTED with its path, its reason and
the drop forms it applies to, and an exemption matching nothing is reported as stale, because the
wave-C review's complaint about the existing guard test was precisely that it passed by NOT listing
the relation.

Nor can a grep tell a read from a comment ABOUT a read. `readers.py::_match_lines` re-tests every hit
against a line-level, comment-stripped view of the same line (`_code_only_line`): a match that
survives only in the stripped-away portion is DOCUMENTATION regardless of which surface it landed on,
never blocking. This is what let `public.drought_data`'s Drizzle declaration be correctly deleted and
replaced with a `//` comment naming the table without the scan reading that comment as the very
reference it announced the removal of. It is a heuristic, not a parser — it cannot see a match inside
a multi-line string or a block comment that opened on an earlier line, and in both blind spots it
resolves toward "code" rather than guess, so the false is a block, never a clear.

## Order matters in `assess_shortfall`

The rewrite epoch is checked BEFORE any verdict is believed. `fire-perimeters` moved from
`daily_series` to `static_lookup` on 2026-09-04, which moved its partition day from
`geo.feature_observation_day` to the source watermark and made its 45 pre-existing partition days
unreadable as coverage. A receipt taken before the first fresh snapshot therefore compares 177 live
PostgreSQL rows against an effectively empty lane — and reads `under_covered` for a twin that is not
short, only not yet rewritten. Neither `covered` nor `under_covered` from such a receipt means
anything, so the epoch check runs first and returns `twin_not_rewritten` or `twin_rewrite_unproven`.
`unmeasured` is a third class for the case where nobody counted at all: calling an uncounted lane
"short" is the same overclaim pointed the other way, and it would make a later real shortfall
indistinguishable from the state every lane starts in.

## Archive forms exist because `pg_dump` lies by omission about matviews

`pg_dump --table` on a materialized view emits its definition and a `REFRESH`, never its contents —
and this track is deleting the base relations that `REFRESH` would read. An archive that looked
complete would restore nothing. So a matview archives as a definition dump PLUS a `\copy` of its rows,
a plain view claims only its definition (its rows live in base relations with their own packets), and
a row-delete archives the exact predicate it deletes by, since dumping the whole `geo.features` table
would copy live community `interventions` rows into a retirement prefix. The sha256 slot is empty and
marked `owed` until a real 64-character digest is supplied; `TBD`, `pending` and a truncated paste are
all refused, because the track's tripwire is "never emit a digest that was not computed from the
object it describes".

## The limit this package will not paper over

For a row-delete, a name-level grep cannot separate "reads the fire-perimeters rows from PostgreSQL"
from "publishes the fire-perimeters Parquet lane": the layer name and the lane slug are the identical
string. The per-layer scan narrows to the SQL string-literal spelling (`'fire-perimeters'`), which
finds tile-function predicates and read-model filters, and the packet then still demands a named
`--layer-reader-proof` citation per layer. An honest limit that forces a human citation beats a grep
that reads as a proof — and this track has already lost a wave to a proof that read cleaner than it
was.
