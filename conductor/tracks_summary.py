import os
import re

# Resolved from this file's own location, so the script runs from any clone and any working
# directory. It previously hard-coded one machine's absolute path and crashed everywhere else.
tracks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracks")
summary = []

for item in os.listdir(tracks_dir):
    item_path = os.path.join(tracks_dir, item)
    if os.path.isdir(item_path):
        plan_path = os.path.join(item_path, "plan.md")
        if os.path.exists(plan_path):
            with open(plan_path, "r", encoding="utf-8") as f:
                content = f.read()
                completed = len(re.findall(r"-\s*\[x\]", content, re.IGNORECASE))
                pending = len(re.findall(r"-\s*\[\s*\]", content))
                total = completed + pending
                percent = (completed / total * 100) if total > 0 else 0
                summary.append({
                    "track": item,
                    "completed": completed,
                    "pending": pending,
                    "total": total,
                    "percent": percent
                })

summary.sort(key=lambda x: x["percent"], reverse=True)

print(f"{'Track':<40} | {'Done':<5} | {'Pending':<7} | {'%':<5}")
print("-" * 65)
for s in summary:
    if s["total"] > 0:
        print(f"{s['track']:<40} | {s['completed']:<5} | {s['pending']:<7} | {s['percent']:>5.1f}%")
