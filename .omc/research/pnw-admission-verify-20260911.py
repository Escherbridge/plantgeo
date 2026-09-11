"""Verify the bounded admission documentation and retained metadata evidence."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
TRACK = ROOT / "conductor/tracks/pnw_herbaria_source_admission_20260911"
OUTPUT = TRACK / "evidence/verification.json"
RESEARCH = ROOT / ".omc/research"
errors = []
links_checked = 0
external_links = set()
inputs = set(TRACK.rglob("*.md")) | set(TRACK.rglob("*.json"))
inputs.discard(OUTPUT)
for doc in sorted(TRACK.rglob("*.md")):
    body = doc.read_text(encoding="utf-8-sig")
    if not re.match(r"\A---\n(?:(?!---).)*?type: [^\n]+\n", body, re.S):
        errors.append(f"Missing OKF type: {doc}")
    if any(line.rstrip() != line for line in body.splitlines()):
        errors.append(f"Authored trailing whitespace: {doc}")
    for target in re.findall(r"\]\(([^)]+)\)", body):
        target = target.strip("<>")
        if target.startswith(("https://", "http://")):
            parsed = urlparse(target)
            if not parsed.netloc:
                errors.append(f"Malformed external URL: {target}")
            external_links.add(target)
            continue
        if target.startswith("#"):
            continue
        file_target = unquote(target.split("#", 1)[0])
        resolved = (doc.parent / file_target).resolve()
        if resolved != OUTPUT and not resolved.is_file():
            errors.append(f"Missing local link in {doc.name}: {target}")
        if resolved.is_file() and resolved.is_relative_to(RESEARCH):
            inputs.add(resolved)
        links_checked += 1

for path in sorted(inputs):
    if path.suffix == ".json":
        json.loads(path.read_text(encoding="utf-8-sig"))
metadata = json.loads((TRACK / "metadata.json").read_text(encoding="utf-8-sig"))
decisions = json.loads((TRACK / "evidence/admission-decisions.json").read_text(encoding="utf-8"))
if metadata["status"] != "blocked" or metadata["admitted_releases"] != []:
    errors.append("Track must remain blocked without admitted releases")
if any(c["decision"] != "blocked" or c["archive_sha256"] is not None for c in decisions["collections"]):
    errors.append("Unsupported occurrence admission/hash")

direct_bytes = 0
for name in ("pnw-admission-http-receipts-20260911.json", "pnw-admission-eml-http-receipt-20260911.json"):
    receipt_path = RESEARCH / name
    inputs.add(receipt_path)
    for receipt in json.loads(receipt_path.read_text(encoding="utf-8-sig")):
        path = RESEARCH / receipt["file"]
        data = path.read_bytes()
        inputs.add(path)
        if receipt["outcome"] != "captured" or len(data) != receipt["bytes"] or hashlib.sha256(data).hexdigest() != receipt["sha256"]:
            errors.append(f"Capture mismatch: {path.name}")
        if "Set-Cookie" in receipt["headers"]:
            errors.append("Ephemeral cookie retained in receipt")
        direct_bytes += len(data)

eml = ET.parse(RESEARCH / "pnw-ubc-eml-16.43-20260911.xml").getroot()
if eml.attrib.get("packageId") != "07fd0d79-4883-435f-bba1-58fef110cd13/v16.43":
    errors.append("EML package mismatch")
rights = " ".join(eml.find("./dataset/intellectualRights").itertext())
if "CC0 1.0" not in rights or "commercial" not in rights:
    errors.append("EML rights mismatch")

staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
for name in staged:
    if name != ".omc/research/.gitattributes" and not name.startswith(("conductor/tracks/pnw_herbaria_source_admission_20260911/", ".omc/research/pnw-")):
        errors.append(f"Out-of-scope staged file: {name}")
whitespace = subprocess.run(["git", "diff", "--cached", "--check", "--", str(TRACK.relative_to(ROOT)), ".omc/research/pnw-admission-fetch-20260911.py", ".omc/research/pnw-admission-verify-20260911.py"], cwd=ROOT, text=True, capture_output=True)
if whitespace.returncode:
    errors.append(whitespace.stdout + whitespace.stderr)
if errors:
    raise SystemExit("\n".join(errors))

for receipt_name in ("pnw-admission-http-receipts-20260911.json", "pnw-admission-eml-http-receipt-20260911.json"):
    for capture in json.loads((RESEARCH / receipt_name).read_text(encoding="utf-8")):
        index_path = ".omc/research/" + capture["file"]
        index_bytes = subprocess.run(["git", "show", ":" + index_path], cwd=ROOT, capture_output=True, check=True).stdout
        if hashlib.sha256(index_bytes).hexdigest() != capture["sha256"]:
            raise SystemExit(f"Staged bytes differ from source capture: {index_path}")

plan_path = TRACK / "plan.md"
plan = plan_path.read_text(encoding="utf-8")
plan = plan.replace("- [ ] One final documentation/JSON/local-link/hash/whitespace verification sweep.", "- [x] One final documentation/JSON/local-link/hash/whitespace verification sweep; see evidence/verification.json.")
plan_path.write_bytes(plan.encode("utf-8"))
inputs.add(Path(__file__).resolve())
receipt = {
    "schema_version": 1,
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "status": "passed",
    "scope": "Documentation and metadata only; no application test suite, corpus/schema profile, archive-safety execution or production certification",
    "checks": ["Track Markdown OKF type", "Authored Markdown whitespace", "Linked JSON parsing", "Local file links", "External URL syntax only; archive URLs deliberately not probed", "Four direct capture lengths and SHA256 in working tree AND staged Git blobs", "UBC EML well-formedness/package/CC0 evidence (not full XSD validation)", "Blocked admission consistency", "Bounded staged paths", "git diff --cached --check for authored track and research scripts"],
    "local_links_checked": links_checked,
    "external_urls_syntax_checked": len(external_links),
    "direct_metadata_bytes": direct_bytes,
    "raw_capture_whitespace": "Preserved verbatim; excluded from authored whitespace check to retain source hashes",
    "inputs": [{"path": str(p.relative_to(ROOT)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(inputs)],
}
OUTPUT.write_bytes((json.dumps(receipt, indent=2) + "\n").encode("utf-8"))
print(json.dumps({key: value for key, value in receipt.items() if key != "inputs"}, indent=2))
