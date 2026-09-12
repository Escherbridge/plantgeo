"""Capture allowlisted public metadata without archive or image requests."""
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URLS = {
    "ubc-ipt": "https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens",
    "wtu-provider": "https://www.pnwherbaria.org/data/providermetadata.php?code=WTU",
    "portal-inventory": "https://www.pnwherbaria.org/data/datasets.php",
}
if "--eml" in sys.argv:
    URLS = {"ubc-eml-16.43": "https://data.canadensys.net/ipt/eml.do?r=ubc-vascular-specimens&v=16.43"}
receipts = []
start = time.monotonic()
for name, url in URLS.items():
    receipt = {"name": name, "requested_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "method": "GET", "limit_bytes": 1048576}
    try:
        if time.monotonic() - start > 120:
            raise RuntimeError("metadata session deadline")
        with urllib.request.urlopen(url, timeout=25) as response:
            receipt.update(status=response.status, final_url=response.url, headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"})
            if response.headers.get_content_type() not in ("text/html", "application/xml", "text/xml"):
                raise RuntimeError("not allowlisted metadata content type")
            body = response.read(1048577)
            if len(body) > 1048576:
                raise RuntimeError("metadata byte cap exceeded")
            suffix = "xml" if name.startswith("ubc-eml") else "html"
            path = ROOT / f"pnw-{name}-20260911.{suffix}"
            path.write_bytes(body)
            receipt.update(file=path.name, bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), outcome="captured")
    except Exception as exc:
        receipt.update(outcome="failed", error=str(exc))
    receipts.append(receipt)
(ROOT / ("pnw-admission-eml-http-receipt-20260911.json" if "--eml" in sys.argv else "pnw-admission-http-receipts-20260911.json")).write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
print(json.dumps([{k: r[k] for k in ("name", "outcome", "bytes", "error") if k in r} for r in receipts]))
