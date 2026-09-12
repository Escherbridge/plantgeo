# Martin serving contract

Martin is the relational vector-tile boundary for published interventions. Its catalog is
explicit and contains only `geo.intervention_tiles`; `auto_publish` stays disabled so retired or
new database relations cannot become public accidentally.

Environmental data is served from governed Parquet through the Python data service. Basemap data
comes from PMTiles. Adding a Martin source requires a current relational owner, a matching client
source registration, and deployment validation against the live database schema.
