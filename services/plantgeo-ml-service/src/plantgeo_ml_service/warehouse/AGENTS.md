# warehouse (L2)

The pinned schemas of the observed Parquet streams this service consumes and the forecast
streams it writes, including the six forecast provenance columns.

Import rules: may import `foundation` and `method`; may NOT import `pipeline`, `planes` or
`interface`.

Every schema here is a COPY of agri-data-service's, held honest by a parity test rather than an
import (spec section 4). Empty until phase 2 (plan.md, 2A).
