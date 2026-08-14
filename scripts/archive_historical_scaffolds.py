import os
import json
import shutil

tracks_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks"
retros_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\retros"
registry_file = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks.md"

deprecated_scaffolds = [
    "02-vector-tile-pipeline",
    "05-geocoding-search",
    "20-embed-api",
    "27-team-organization-pages",
    "agri_data_service_scaffold_20260324",
    "data_ingestion_pipeline_20260324",
    "map_layer_data_viz_20260324",
    "rag_recommendation_engine_20260324",
]

moved = []
for name in deprecated_scaffolds:
    src = os.path.join(tracks_dir, name)
    dst = os.path.join(retros_dir, name)
    if os.path.exists(src) and not os.path.exists(dst):
        meta_file = os.path.join(src, "metadata.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["status"] = "historical"
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
        shutil.move(src, dst)
        moved.append(name)

# Rebuild tracks.md
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
        status_str = "completed"
        type_str = "feature"
        desc = item
        if os.path.exists(meta_p):
            try:
                with open(meta_p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    status_str = d.get("status", "completed")
                    type_str = d.get("type", "feature")
                    desc = d.get("description", desc)
            except Exception:
                pass
        completed_tracks.append((item, status_str, type_str, desc))

lines = [
    "---",
    "type: work-registry",
    "---",
    "",
    "# Master Work Registry & Active Track Pillars",
    "",
    "Per [`README.md`](./README.md), this file is the sole master work registry.",
    "Material under `retros/` contains completed tracks and historical scaffolds.",
    "",
    "Registry updated 2026-08-14.",
    "",
    "## Active & Primary Work Tracks",
    "",
    "| Track | Status | Type | Summary |",
    "|-------|--------|------|---------|",
]

for name, status, ttype, desc in active_tracks:
    lines.append(f"| [{name}](tracks/{name}/) | {status} | {ttype} | {desc} |")

lines.extend(
    [
        "",
        "## Completed Tracks & Historical Retrospectives",
        "",
        "| Track | Status | Type | Summary |",
        "|-------|--------|------|---------|",
    ]
)

for name, status, ttype, desc in completed_tracks:
    lines.append(f"| [{name}](retros/{name}/) | {status} | {ttype} | {desc} |")

with open(registry_file, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"Archived {len(moved)} historical/deprecated scaffolds to retros/.")
print(f"Final Count: {len(active_tracks)} focused active tracks, {len(completed_tracks)} archived retros!")
