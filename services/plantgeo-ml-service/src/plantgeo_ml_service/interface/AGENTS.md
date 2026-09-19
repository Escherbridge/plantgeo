# interface (L5)

The `plantgeo-ml` click adapter. Outermost layer: it may import anything below it and nothing
imports it.

Adapters only. A command parses arguments, calls one domain function and renders the result; it
does not own orchestration, a retry policy or a durability boundary. `predict-daily` is a stub
that exits 2 until phase 2 -- a bounded turn still exits with a named code rather than hanging.
