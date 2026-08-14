import os
import json
import re

tracks_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks"
retros_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\retros"
src_dir = r"c:\Users\atooz\Programming\plantgeo\src"
py_dir = r"c:\Users\atooz\Programming\plantgeo\services\agri-data-service"

# Mapping of tracks to key source code evidence files
track_code_checks = {
    "01-core-map-engine": [
        os.path.join(src_dir, "components", "map", "MapView.tsx"),
        os.path.join(src_dir, "stores", "map-store.ts")
    ],
    "02-vector-tile-pipeline": [
        os.path.join(src_dir, "lib", "server", "services", "tile.ts"),
        r"c:\Users\atooz\Programming\plantgeo\infra\martin\martin.yaml"
    ],
    "03-deck-gl-visualization": [
        os.path.join(src_dir, "components", "map", "LayerManager.tsx")
    ],
    "05-geocoding-search": [
        os.path.join(src_dir, "lib", "server", "services", "geocoding.ts"),
        os.path.join(src_dir, "components", "panels", "layer-panel", "SearchDockSection.tsx")
    ],
    "10-wildfire-prevention": [
        os.path.join(src_dir, "lib", "server", "services", "nasa-firms.ts"),
        os.path.join(src_dir, "components", "map", "layers", "FireLayer.tsx")
    ],
    "11-offline-pwa": [
        os.path.join(src_dir, "lib", "indexeddb.ts"),
        os.path.join(src_dir, "app", "manifest.ts")
    ],
    "15-ui-design-system": [
        os.path.join(src_dir, "components", "ui", "button.tsx"),
        os.path.join(src_dir, "styles", "globals.css")
    ],
    "17-places-poi": [
        os.path.join(src_dir, "lib", "server", "services", "places.ts")
    ],
    "18-railway-deployment": [
        r"c:\Users\atooz\Programming\plantgeo\Dockerfile",
        r"c:\Users\atooz\Programming\plantgeo\railway.json"
    ],
    "19-testing-quality": [
        r"c:\Users\atooz\Programming\plantgeo\vitest.config.ts",
        os.path.join(src_dir, "__tests__", "api", "ingress-bounds.test.ts")
    ],
    "20-embed-api": [
        os.path.join(src_dir, "app", "api", "v1", "embed", "route.ts")
    ],
    "21-wildfire-enhancement": [
        os.path.join(src_dir, "lib", "server", "services", "wfigs-fire-perimeters.ts")
    ],
    "25-community-strategy-requests": [
        os.path.join(src_dir, "lib", "server", "services", "community-activity.ts"),
        os.path.join(src_dir, "app", "community", "CommunityLedger.tsx")
    ],
    "27-team-organization-pages": [
        os.path.join(src_dir, "app", "team", "page.tsx")
    ],
    "29-environmental-alerts": [
        os.path.join(src_dir, "lib", "server", "services", "alert-engine.ts")
    ],
    "31-ai-regional-intelligence": [
        os.path.join(src_dir, "lib", "server", "services", "ai-prompt.ts"),
        os.path.join(src_dir, "components", "panels", "RegionalIntelligencePanel.tsx")
    ],
    "agri_data_service_scaffold_20260324": [
        os.path.join(py_dir, "agri_data_service", "main.py")
    ],
    "data_ingestion_pipeline_20260324": [
        os.path.join(py_dir, "agri_data_service", "jobs", "ledger.py")
    ],
    "dw_materialized_zoom_aggregation_20260814": [
        os.path.join(src_dir, "lib", "server", "services", "zoom-granularity.ts")
    ],
    "inapp_job_runner_admin_20260814": [
        os.path.join(src_dir, "lib", "server", "trpc", "routers", "jobs.ts")
    ],
    "ml_strategy_materialized_rendering_20260814": [
        os.path.join(src_dir, "lib", "map", "layers", "strategy-layer.ts")
    ],
    "swr_indexeddb_dw_reconciliation_20260814": [
        os.path.join(src_dir, "hooks", "useSWRDB.ts")
    ],
    "webworker_webgpu_acceleration_20260814": [
        os.path.join(src_dir, "workers", "gpu-accelerator.ts")
    ]
}

results = []
for track, files in track_code_checks.items():
    existing = [f for f in files if os.path.exists(f)]
    missing = [f for f in files if not os.path.exists(f)]
    status = "completed" if len(missing) == 0 else ("partial" if len(existing) > 0 else "unstarted")
    results.append((track, status, len(existing), len(files)))

print(f"{'Track ID':<45} | {'Code Status':<12} | {'Found Files'}")
print("-" * 75)
for t, st, found, total in results:
    print(f"{t:<45} | {st:<12} | {found}/{total}")
