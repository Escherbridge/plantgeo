import os
import json

tracks_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks"
retros_dir = r"c:\Users\atooz\Programming\plantgeo\conductor\retros"
registry_file = r"c:\Users\atooz\Programming\plantgeo\conductor\tracks.md"

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
    "## Active & Planned Tracks",
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

print(f"Successfully registered {len(active_tracks)} active tracks and {len(completed_tracks)} completed retros in conductor/tracks.md!")
