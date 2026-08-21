"""Export the stored Sentinel-2 NDVI grid as line-delimited GeoJSON, ready for tippecanoe to cut display tiles.

Read-only against the warehouse. It never writes a row and never invents a cell: whatever the ingester
measured is what this exports, so an unfilled cell simply has no feature and no tile geometry.

Run it from the repository root with the service's own environment, e.g.

    uv run --project services/agri-data-service python scripts/export-ndvi-grid-tiles.py \
        --output data/ndvi-grid.geojsonl

then cut tiles with the command it prints. The raster display layer stays on the existing NASA GIBS
proxy route (src/app/api/tiles/vegetation/ndvi/...); these vector tiles carry the quantitative grid the
warehouse actually stores, which the GIBS PNG tiles cannot express.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg2

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SERVICE_ENVIRONMENT_FILE = REPOSITORY_ROOT / "services" / "agri-data-service" / ".env"
DATABASE_URL_VARIABLE = "DATABASE_URL_SYNC"

DEFAULT_LAYER_NAME = "vegetation"
DEFAULT_GRID_NAME = "sentinel2-ndvi-0p25deg"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "ndvi-grid.geojsonl"
DEFAULT_TILE_LAYER = "vegetation-ndvi"
MIN_TILE_ZOOM = 0
MAX_TILE_ZOOM = 8

ENVIRONMENT_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$")

# One row per cell: the newest observation the warehouse holds for it, with the cell polygon PostGIS
# already normalised. Strictly SELECT; this script holds no write privilege of its own.
#
# `feature.layer_id = (SELECT id FROM geo.layers WHERE name = ...)` rather than a join: geo.features
# is partitioned by layer_id, and a join filtered on layer.name never propagates into a
# partition-pruning constant, so the DISTINCT ON below would sort every partition instead of just
# vegetation's. The scalar subquery resolves the name once and lets the planner prune to one
# partition before the sort ever runs.
LATEST_CELL_QUERY = """
SELECT DISTINCT ON (feature.properties ->> 'cellKey')
       feature.properties AS properties,
       ST_AsGeoJSON(feature.geom) AS geometry
FROM geo.features AS feature
WHERE feature.layer_id = (SELECT id FROM geo.layers WHERE name = %(layer_name)s)
  AND feature.properties ->> 'gridName' = %(grid_name)s
ORDER BY feature.properties ->> 'cellKey',
         feature.properties ->> 'observedAt' DESC
"""


def read_database_url(environment_file: Path) -> str:
    """Read the sync DSN out of the service's .env, never echoing it back to the operator."""
    if not environment_file.is_file():
        raise SystemExit(f"no environment file at {environment_file}")
    for line in environment_file.read_text(encoding="utf-8").splitlines():
        match = ENVIRONMENT_LINE.match(line)
        if match is None or match.group(1) != DATABASE_URL_VARIABLE:
            continue
        value = match.group(2).strip().strip('"').strip("'")
        if value:
            return value
    raise SystemExit(f"{DATABASE_URL_VARIABLE} is not set in {environment_file}")


def export_cells(database_url: str, layer_name: str, grid_name: str, output: Path) -> int:
    """Stream one GeoJSON feature per grid cell into a line-delimited file, returning how many were written."""
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with psycopg2.connect(database_url) as connection, connection.cursor() as cursor, output.open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(LATEST_CELL_QUERY, {"layer_name": layer_name, "grid_name": grid_name})
        for properties, geometry in cursor:
            if geometry is None:
                continue
            feature = {
                "type": "Feature",
                "geometry": json.loads(geometry),
                "properties": {key: value for key, value in properties.items() if key != "geometry"},
            }
            handle.write(json.dumps(feature, separators=(",", ":"), sort_keys=True))
            handle.write("\n")
            written += 1
    return written


def parse_arguments() -> argparse.Namespace:
    """Read the layer, grid and output path this export should cover."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--layer-name", default=DEFAULT_LAYER_NAME, help="geo.layers name holding the NDVI cells.")
    parser.add_argument("--grid-name", default=DEFAULT_GRID_NAME, help="Grid definition to export.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Line-delimited GeoJSON to write.")
    parser.add_argument("--tile-layer", default=DEFAULT_TILE_LAYER, help="Vector tile layer name to suggest.")
    return parser.parse_args()


def main() -> int:
    """Export the grid and print the tippecanoe command that turns it into a PMTiles archive."""
    arguments = parse_arguments()
    database_url = read_database_url(SERVICE_ENVIRONMENT_FILE)
    written = export_cells(database_url, arguments.layer_name, arguments.grid_name, arguments.output)
    if written == 0:
        print(
            f"no stored cells for grid {arguments.grid_name} in layer {arguments.layer_name}; "
            "nothing was exported and nothing was invented",
            file=sys.stderr,
        )
        return 1
    print(f"exported {written} cells to {arguments.output}")
    print(
        "tippecanoe -o "
        f"{arguments.output.with_suffix('.pmtiles')} -l {arguments.tile_layer} "
        f"-Z{MIN_TILE_ZOOM} -z{MAX_TILE_ZOOM} --drop-densest-as-needed --force {arguments.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
