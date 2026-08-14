import os
import json
import shutil

tracks_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks"
retros_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\retros"
registry_file = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks.md"

os.makedirs(retros_dir, exist_ok=True)

# List of tracks verified as completed in the codebase
verified_completed = [
    "01-core-map-engine",
    "03-deck-gl-visualization",
    "10-wildfire-prevention",
    "15-ui-design-system",
    "17-places-poi",
    "18-railway-deployment",
    "21-wildfire-enhancement",
    "25-community-strategy-requests",
    "29-environmental-alerts",
    "31-ai-regional-intelligence",
    "dw_materialized_zoom_aggregation_20260814",
    # Verified complete in the 2026-08-14 completion session (per-item evidence
    # in each track's plan.md annotations and track-dir decision records):
    "11-offline-pwa",
    "19-testing-quality",
    "forecasting_predeploy_20260722",
    "ingestion_warehouse_consolidation_20260803",
    "ml_recommendation_models_20260808",
    "model_delivery_public_evaluation_20260726",
    "north_america_intervention_data_20260723",
    "seasonal_forecast_feedback_20260726",
    "ml_strategy_materialized_rendering_20260814",
    "inapp_job_runner_admin_20260814",
    "ai_regional_agent_expansion_20260814",
    "community_intervention_lifecycle_20260814",
]

newly_moved = []
for name in verified_completed:
    src = os.path.join(tracks_dir, name)
    dst = os.path.join(retros_dir, name)
    if os.path.exists(src) and not os.path.exists(dst):
        meta_file = os.path.join(src, "metadata.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["status"] = "completed"
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
        shutil.move(src, dst)
        newly_moved.append(name)

# Now rebuild conductor/tracks.md
active_tracks = []
completed_tracks = []

for item in sorted(os.listdir(tracks_dir)):
    p = os.path.join(tracks_dir, item)
    if os.path.isdir(p):
        meta_p = os.path.join(p, "metadata.json")
        status = "active"
        type_str = "feature"
        desc = item
        if os.path.exists(meta_p):
            try:
                with open(meta_p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    status = d.get("status", "active")
                    type_str = d.get("type", "feature")
                    desc = d.get("description", desc)
            except Exception:
                pass
        active_tracks.append((item, status, type_str, desc))

for item in sorted(os.listdir(retros_dir)):
    p = os.path.join(retros_dir, item)
    if os.path.isdir(p):
        meta_p = os.path.join(p, "metadata.json")
        type_str = "feature"
        desc = item
        if os.path.exists(meta_p):
            try:
                with open(meta_p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    type_str = d.get("type", "feature")
                    desc = d.get("description", desc)
            except Exception:
                pass
        completed_tracks.append((item, "completed", type_str, desc))

lines = [
    "---",
    "type: work-registry",
    "---",
    "",
    "# Current Work Registry",
    "",
    "Per [`README.md`](./README.md), this file is the sole current work registry.",
    "Material under `retros/` contains completed tracks and retrospectives. Status vocabulary: active, planned, blocked, complete, historical.",
    "",
    "Registry updated 2026-08-14.",
    "",
    "## Active & Incomplete Tracks",
    "",
    "| Track | Status | Type | Summary |",
    "|-------|--------|------|---------|",
]

for name, status, ttype, desc in active_tracks:
    lines.append(f"| [{name}](tracks/{name}/) | {status} | {ttype} | {desc} |")

lines.extend(
    [
        "",
        "## Completed Tracks & Retrospectives",
        "",
        "| Track | Status | Type | Summary |",
        "|-------|--------|------|---------|",
    ]
)

for name, status, ttype, desc in completed_tracks:
    lines.append(f"| [{name}](retros/{name}/) | {status} | {ttype} | {desc} |")

with open(registry_file, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"Moved {len(newly_moved)} newly verified completed tracks to retros/.")
print(f"Updated tracks.md: {len(active_tracks)} remaining active/incomplete tracks, {len(completed_tracks)} completed retros!")
