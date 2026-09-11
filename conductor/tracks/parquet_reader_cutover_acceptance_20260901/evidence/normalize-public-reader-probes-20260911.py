"""Consolidate bounded public reader observations without TLS or cookie dumps."""

from __future__ import annotations

import hashlib
import gzip
import json
import argparse
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input-directory', type=Path, default=Path(__file__).resolve().parent)
args = parser.parse_args()
ROOT = args.input_directory.resolve()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def metrics(raw):
    return {key: raw.get(key) for key in (
        "url_effective", "http_code", "time_starttransfer", "time_total", "size_download", "exitcode", "errormsg"
    )}


output = ROOT / "public-reader-observations-20260911.json"
previous = read_json(output) if output.exists() else {}
payloads = previous.get("response_bodies_by_sha256", {})
compressed_payloads = previous.get("compressed_response_body_files_by_sha256", {})
records = previous.get("row_observations", [])
cleanup = []
metric_files = [ROOT / name for name in (
    "public-reader-metrics-20260911.json", "public-reader-supplemental-metrics-20260911.json"
) if (ROOT / name).exists()]
pending_records = [row for path in metric_files for row in read_json(path)]
cleanup.extend(metric_files)
for row in pending_records:
    body_path = ROOT / row.pop("response_file")
    body = body_path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    payloads.setdefault(digest, body.decode("utf-8"))
    row["response_sha256"] = digest
    row["curl_metrics"] = metrics(row["curl_metrics"])
    header_path = body_path.with_suffix(".headers.txt")
    row["response_headers"] = [line for line in header_path.read_text(encoding="utf-8").splitlines()
                               if line.lower().startswith(("http/", "date:", "content-type:", "cache-control:", "x-nextjs-cache:"))]
    records.append(row)
    cleanup.extend((body_path, header_path))

capabilities = previous.get("capability_observations", [])
for sample in ("first", "repeat"):
    body_path = ROOT / f"capabilities-observed-{sample}-20260911.json"
    if not body_path.exists():
        continue
    body = body_path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    payloads.setdefault(digest, body.decode("utf-8"))
    metric_path = ROOT / f"capabilities-observed-{sample}-metrics-20260911.json"
    row = read_json(metric_path)
    row["curl_metrics"] = metrics(row["curl_metrics"])
    row["response_sha256"] = digest
    capabilities.append(row)
    cleanup.extend((body_path, metric_path))

ready_path = ROOT / "public-ready-20260911.json"
ready = previous.get("readiness_observation")
if ready_path.exists():
    ready_bytes = ready_path.read_bytes()
    ready_digest = hashlib.sha256(ready_bytes).hexdigest()
    payloads.setdefault(ready_digest, ready_bytes.decode("utf-8"))
    ready_metric_path = ROOT / "public-ready-metrics-20260911.json"
    ready = {"response_sha256": ready_digest, "curl_metrics": metrics(read_json(ready_metric_path))}
    cleanup.extend((ready_path, ready_metric_path))

for digest, body in list(payloads.items()):
    if len(body.encode("utf-8")) > 100_000:
        filename = f"response-{digest}.json.gz"
        (ROOT / filename).write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))
        compressed_payloads[digest] = filename
        del payloads[digest]

packet = {
    "schema": "plantgeo-public-reader-observations/v1",
    "source_revision_local": previous.get("source_revision_local") or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    "deployed_revision": previous.get("deployed_revision"),
    "deployment_identity_evidence": previous.get("deployment_identity_evidence"),
    "cache_state": "first and repeated observations; no cache reset or operation instrumentation",
    "capability_observations": capabilities,
    "readiness_observation": ready,
    "row_observations": records,
    "response_bodies_by_sha256": payloads,
    "compressed_response_body_files_by_sha256": compressed_payloads,
}
write_json(output, packet)
saved = read_json(output)
assert len(saved["row_observations"]) in (12, 72, 84, 85)
for digest, body in saved["response_bodies_by_sha256"].items():
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == digest
for digest, filename in saved["compressed_response_body_files_by_sha256"].items():
    assert hashlib.sha256(gzip.decompress((ROOT / filename).read_bytes())).hexdigest() == digest
for path in cleanup:
    if path.resolve().parent != ROOT:
        raise ValueError(f"Refusing cleanup outside evidence directory: {path.name}")
    path.unlink()
print(json.dumps({"observations": len(records), "unique_response_bodies": len(payloads), "packet_bytes": output.stat().st_size}))
