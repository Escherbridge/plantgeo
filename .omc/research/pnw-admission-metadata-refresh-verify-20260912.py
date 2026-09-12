"""Verify the September 12 PNW metadata receipt without network access."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / ".omc" / "research"
TRACK = ROOT / "conductor" / "tracks" / "pnw_herbaria_source_admission_20260911"
RECEIPT = RESEARCH / "pnw-admission-metadata-refresh-http-receipts-20260912.json"
EXPECTED_NAMES = {
    "portal-inventory",
    "portal-usage-policy",
    "wtu-provider",
    "ubc-provider",
    "ubc-ipt-current",
    "ubc-ipt-16.43",
    "ubc-eml-16.43",
}


def check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> None:
    errors: list[str] = []
    receipts = json.loads(RECEIPT.read_text(encoding="utf-8"))
    check(len(receipts) == 7, "expected seven HTTP receipts", errors)
    check({item["name"] for item in receipts} == EXPECTED_NAMES, "unexpected receipt names", errors)
    check(max(item["attempt"] for item in receipts) <= 8, "HTTP attempt ceiling exceeded", errors)
    for item in receipts:
        check(item.get("outcome") == "captured", f"{item['name']}: not captured", errors)
        check(item.get("status") == 200, f"{item['name']}: non-200 status", errors)
        check(item.get("requested_url") == item.get("final_url"), f"{item['name']}: redirect observed", errors)
        check(item.get("bytes", 0) <= item.get("limit_bytes", 0), f"{item['name']}: byte cap exceeded", errors)
        path = RESEARCH / item["file"]
        check(path.is_file(), f"{item['name']}: missing body", errors)
        if path.is_file():
            body = path.read_bytes()
            check(len(body) == item["bytes"], f"{item['name']}: byte count mismatch", errors)
            check(hashlib.sha256(body).hexdigest() == item["sha256"], f"{item['name']}: hash mismatch", errors)
        check("archive.do" not in item["requested_url"], f"{item['name']}: archive requested", errors)

    decisions = json.loads((TRACK / "evidence" / "admission-decisions.json").read_text(encoding="utf-8"))
    metadata = json.loads((TRACK / "metadata.json").read_text(encoding="utf-8"))
    check(decisions["status"] == "blocked", "decision status is not blocked", errors)
    check(decisions["admitted_releases"] == [], "decision admits a release", errors)
    check(decisions["corpus_downloaded"] is False, "decision claims corpus download", errors)
    check(metadata["status"] == "active", "track is not active", errors)
    check(metadata["admission_status"] == "blocked", "metadata admission is not blocked", errors)
    check(metadata["admitted_releases"] == [], "metadata admits a release", errors)

    eml_now = (RESEARCH / "pnw-ubc-eml-16.43-20260912.xml").read_bytes()
    eml_prior = (RESEARCH / "pnw-ubc-eml-16.43-20260911.xml").read_bytes()
    check(eml_now == eml_prior, "UBC EML differs from September 11", errors)

    markdown_files = [TRACK / "spec.md", TRACK / "plan.md", *(TRACK / "evidence").glob("*.md")]
    local_link = re.compile(r"\[[^]]+\]\((?!https?://|#)([^)#]+)(?:#[^)]+)?\)")
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        check(not any(line.endswith((" ", "\t")) for line in text.splitlines()), f"{path}: trailing whitespace", errors)
        for target in local_link.findall(text):
            check((path.parent / target).resolve().exists(), f"{path}: broken link {target}", errors)

    diff_check = subprocess.run(
        ["git", "diff", "--check"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    check(diff_check.returncode == 0, f"git diff --check failed: {diff_check.stdout}{diff_check.stderr}", errors)
    print(json.dumps({"status": "passed" if not errors else "failed", "errors": errors}, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
