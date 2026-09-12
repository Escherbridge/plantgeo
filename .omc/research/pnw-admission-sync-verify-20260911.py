"""Verify botanical baseline inheritance and preserved admission evidence offline."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
TRACK = "conductor/tracks/pnw_herbaria_source_admission_20260911/"
BASELINE = "b9b7bf4fcc0c58556d10fc58522edced8962b869"
PACKET = "f19678c9609c49ad517b5e03554d1cd17bf4ed4e"
OUTPUT = TRACK + "evidence/synchronization-verification.json"
RECIPE = ".omc/research/pnw-admission-sync-verify-20260911.py"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def names(*args):
    return git(*args).decode().splitlines()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise SystemExit(message)


require(not names("diff", "--name-only", "--diff-filter=U"), "Unresolved merge conflicts")
require(git("rev-parse", "MERGE_HEAD").decode().strip() == BASELINE, "Wrong merge baseline")
require(git("rev-parse", "HEAD").decode().strip() == PACKET, "Wrong packet parent")
baseline_files = names("diff", "--name-only", "a7e6b22", BASELINE)
inherited = [name for name in baseline_files if not name.startswith(TRACK)]
for name in inherited:
    require(git("show", ":" + name) == git("show", BASELINE + ":" + name), "Baseline changed: " + name)

historical = [name for name in names("ls-tree", "-r", "--name-only", PACKET) if name.startswith(TRACK + "evidence/") or name.startswith(".omc/research/pnw-") or name == ".omc/research/.gitattributes"]
for name in historical:
    original = git("show", PACKET + ":" + name)
    require((ROOT / name).read_bytes() == original, "Historical working bytes changed: " + name)
    require(git("show", ":" + name) == original, "Historical staged bytes changed: " + name)

resolution = names("diff", "--cached", "--name-only", PACKET)
allowed = set(baseline_files) | {RECIPE, OUTPUT, TRACK + "evidence/baseline-synchronization.md", TRACK + "evidence/synchronization-review.md"}
require(set(resolution) <= allowed, "Out-of-scope resolution files: " + repr(set(resolution) - allowed))
docs = sorted({name for name in baseline_files if name.endswith(".md")} | {str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / TRACK).rglob("*.md")})
json_files = sorted({name for name in baseline_files if name.endswith(".json")} | {str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / TRACK).rglob("*.json") if str(p.relative_to(ROOT)).replace("\\", "/") != OUTPUT})
inputs = set(historical) | set(docs) | set(json_files) | {RECIPE}
local_links = 0
external_urls = set()
for name in docs:
    body = (ROOT / name).read_text(encoding="utf-8-sig")
    require(re.match(r"\A---\n(?:(?!---).)*?type: [^\n]+\n", body, re.S), "Missing OKF type: " + name)
    require(not re.search(r"^(<<<<<<<|=======|>>>>>>>)", body, re.M), "Conflict marker: " + name)
    require(all(line == line.rstrip() for line in body.splitlines()), "Trailing whitespace: " + name)
    for target in re.findall(r"\]\(([^)]+)\)", body):
        target = target.strip("<>")
        if target.startswith(("https://", "http://")):
            require(bool(urlparse(target).netloc), "Malformed URL: " + target)
            external_urls.add(target)
        elif not target.startswith("#"):
            linked = ((ROOT / name).parent / unquote(target.split("#", 1)[0])).resolve()
            require(linked == ROOT / OUTPUT or linked.is_file(), "Broken link in " + name + ": " + target)
            local_links += 1
for name in json_files:
    json.loads((ROOT / name).read_text(encoding="utf-8-sig"))
meta = json.loads((ROOT / TRACK / "metadata.json").read_text())
require(meta["status"] == "active" and meta["partitions"]["confidence"] == "active", "Source track must stay active")
require(meta["admission_status"] == "blocked" and meta["admitted_releases"] == [], "Source admissions must stay blocked")
for name in ("spec.md", "plan.md"):
    require("status: active\n" in (ROOT / TRACK / name).read_text().split("---", 2)[1], "Inactive source document: " + name)
decisions = json.loads((ROOT / TRACK / "evidence/admission-decisions.json").read_text())
require(decisions["admitted_releases"] == [] and all(c["decision"] == "blocked" and c["archive_sha256"] is None for c in decisions["collections"]), "Changed collection admission")
profile = "botanical_species_profile_lookup_20260911"
require(profile in meta["related_tracks"] and profile not in meta["depends_on"], "Profile incorrectly blocks source admission")
profile_meta = json.loads((ROOT / "conductor/tracks" / profile / "metadata.json").read_text())
require(profile_meta["serving_contract"]["database_fallback"] == "none" and profile_meta["serving_contract"]["serving_source"] == "release-pinned Parquet", "Profile serving pivot changed")
direct_bytes = 0
for filename in ("pnw-admission-http-receipts-20260911.json", "pnw-admission-eml-http-receipt-20260911.json"):
    for capture in json.loads((ROOT / ".omc/research" / filename).read_text()):
        data = (ROOT / ".omc/research" / capture["file"]).read_bytes()
        require(len(data) == capture["bytes"] and sha(data) == capture["sha256"], "Direct capture mismatch")
        direct_bytes += len(data)
git("diff", "--cached", "--check")
for name in inputs:
    require((ROOT / name).read_bytes() == git("show", ":" + name), "Working/staged mismatch: " + name)
receipt = {
    "schema_version": 1,
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "status": "passed",
    "baseline_commit": BASELINE,
    "packet_commit": PACKET,
    "track_status": "active",
    "admission_status": "blocked",
    "admitted_releases": [],
    "scope": "Offline documentation and evidence integrity only; no network, acquisition, application tests or runtime certification",
    "checks": ["Resolved merge and exact parent references", "Baseline files outside source track unchanged", "Historical packet evidence and captures byte-identical in working tree and Git index", "Bounded resolution paths", "OKF/JSON/conflict-marker/whitespace checks", "Local links and external URL syntax only", "Active track and blocked collection decisions", "Profile relationship and Parquet-only serving", "Four captured metadata lengths/SHA256", "All receipt inputs equal staged Git bytes"],
    "historical_files_preserved": len(historical),
    "baseline_files_inherited_unchanged": len(inherited),
    "markdown_files_checked": len(docs),
    "json_files_checked": len(json_files),
    "local_links_checked": local_links,
    "external_urls_syntax_checked": len(external_urls),
    "direct_metadata_bytes": direct_bytes,
    "inputs": [{"path": name, "bytes": len((ROOT / name).read_bytes()), "sha256": sha((ROOT / name).read_bytes())} for name in sorted(inputs)],
}
(ROOT / OUTPUT).write_bytes((json.dumps(receipt, indent=2) + "\n").encode())
print(json.dumps({k: v for k, v in receipt.items() if k != "inputs"}, indent=2))
