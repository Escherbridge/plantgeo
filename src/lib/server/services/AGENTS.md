# Service boundaries

Environmental service modules may call the governed Parquet readers and availability contracts
only. They must not import the relational database client or retry a failed Parquet read against
PostgreSQL.

Relational services are reserved for authentication, teams, community workflows, tracking,
alerts, conversations, layer metadata, raster metadata, and user-authored interventions.

The UI-selected day is part of every environmental answer. Preserve requested and resolved dates,
source provenance, coverage, and neighbour evidence through service and agent responses.
