"""Capture bounded, allowlisted herbarium metadata; never request archives or images."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAX_BYTES = 1_048_576
MAX_ATTEMPTS = 8
SESSION_SECONDS = 180
REQUEST_SECONDS = 30
URLS = {
    "portal-inventory": "https://www.pnwherbaria.org/data/datasets.php",
    "portal-usage-policy": "https://www.pnwherbaria.org/data/datausagepolicy.php",
    "wtu-provider": "https://www.pnwherbaria.org/data/providermetadata.php?code=WTU",
    "ubc-provider": "https://www.pnwherbaria.org/data/providermetadata.php?code=UBC",
    "ubc-ipt-current": "https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens",
    "ubc-ipt-16.43": "https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens&v=16.43",
    "ubc-eml-16.43": "https://data.canadensys.net/ipt/eml.do?r=ubc-vascular-specimens&v=16.43",
}


def main() -> None:
    started = time.monotonic()
    receipts: list[dict[str, object]] = []
    for attempt, (name, url) in enumerate(URLS.items(), start=1):
        if attempt > MAX_ATTEMPTS or time.monotonic() - started > SESSION_SECONDS:
            break
        receipt: dict[str, object] = {
            "name": name,
            "attempt": attempt,
            "requested_url": url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "method": "GET",
            "limit_bytes": MAX_BYTES,
        }
        request = urllib.request.Request(url, headers={"User-Agent": "PlantGeo-source-admission/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_SECONDS) as response:
                content_type = response.headers.get_content_type()
                receipt.update(
                    status=response.status,
                    final_url=response.url,
                    headers={k: v for k, v in response.headers.items() if k.lower() != "set-cookie"},
                )
                if content_type not in ("text/html", "application/xml", "text/xml"):
                    raise RuntimeError(f"disallowed metadata content type: {content_type}")
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise RuntimeError("metadata byte cap exceeded")
                suffix = "xml" if "eml" in name else "html"
                path = ROOT / f"pnw-{name}-20260912.{suffix}"
                path.write_bytes(body)
                receipt.update(
                    file=path.name,
                    bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest(),
                    outcome="captured",
                )
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            receipt.update(outcome="failed", error=f"{type(exc).__name__}: {exc}")
        receipts.append(receipt)

    receipt_path = ROOT / "pnw-admission-metadata-refresh-http-receipts-20260912.json"
    receipt_path.write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipts, indent=2))


if __name__ == "__main__":
    main()
