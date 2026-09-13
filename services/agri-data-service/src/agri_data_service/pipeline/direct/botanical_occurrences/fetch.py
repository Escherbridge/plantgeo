"""Bounded, allowlisted, permission-gated transfer of one archive into quarantine.

NOTHING CALLS THIS AUTOMATICALLY. There is no scheduled caller, no import from the adapter and no
default in the CLI: a transfer happens when an operator runs `python -m ... fetch` with an admission
manifest whose verdict for that exact URL is `granted`. The governance audit's position is that
"this evidence-only task authorizes no acquisition or provider contact", so the permission check is
the first thing this module does and its default answer is no.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from agri_data_service.foundation.botanical_occurrences.limits import ADMITTED_LIMITS, AcquisitionLimitError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from agri_data_service.foundation.botanical_occurrences.limits import AcquisitionLimits

#: The only hosts an archive may be fetched from. Extending this list is an admission decision, not
#: a code change: a host outside it has never been through a custody or rights review.
ALLOWED_HOSTS: Final[frozenset[str]] = frozenset(
    {"ipt.pnwherbaria.org", "data.canadensys.net", "www.pnwherbaria.org"}
)

#: Headers never written into a receipt. A receipt is evidence that gets read and copied around; a
#: session cookie in one is a credential in a place nobody expects to find one.
_REDACTED_HEADERS: Final[frozenset[str]] = frozenset({"set-cookie", "authorization", "proxy-authorization"})

_CHUNK_BYTES: Final = 262_144
_USER_AGENT: Final = "plantgeo-agri-data-service/botanical-occurrences (contact: repository owner)"


class TransferRefusedError(RuntimeError):
    """Raised before any socket opens: no granted permission, or a host outside the allowlist."""


class RedirectRefusedError(RuntimeError):
    """Raised when the server answers with a redirect.

    A redirect is refused rather than followed because the allowlist is checked against the URL an
    operator's permission verdict names, and a 302 replaces exactly that. The audit requires "each
    redirect validated before following it"; this lane validates by not following, and an operator
    who wants the new location re-runs with it named in the manifest.
    """


class _RefusingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into a refusal, at the handler, before a second request is built."""

    def redirect_request(  # noqa: PLR0913 - the urllib handler signature, which cannot be narrowed
        self,
        req: urllib.request.Request,
        fp: Any,  # noqa: ANN401 - urllib passes its own file object type here
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        raise RedirectRefusedError(f"refused redirect {code} from {req.full_url} to {newurl}")


@dataclass(frozen=True, slots=True)
class TransferReceipt:
    """What one transfer actually did, in the terms the immutable-manifest procedure asks for."""

    requested_url: str
    final_url: str
    status: int
    headers: Mapping[str, str]
    bytes_written: int
    sha256: str
    retrieved_at: str
    destination: str


def permission_granted(manifest: Mapping[str, Any], url: str) -> bool:
    """Report whether the manifest grants THIS exact URL, with no prefix or host-level inference.

    Exact-match only. A manifest granting a collection's landing page does not grant an archive URL
    under it, and a verdict for version 16.42 is not a verdict for 16.43 -- the whole point of the
    identity rules is that a version change is a different object.
    """
    if manifest.get("permission_verdict") == "granted" and manifest.get("url") == url:
        return True
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("url") == url and entry.get("permission_verdict") == "granted"
        for entry in entries
    )


def _safe_headers(raw: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in raw.items() if key.casefold() not in _REDACTED_HEADERS}


def fetch_archive(  # noqa: PLR0913 - one argument per governance input; none may carry a default verdict
    url: str,
    destination: Path,
    *,
    manifest: Mapping[str, Any],
    limits: AcquisitionLimits = ADMITTED_LIMITS,
    attempts: int | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> TransferReceipt:
    """Fetch one archive into the quarantine directory, or refuse before opening a socket.

    The order is the control: permission, then allowlist, then scheme, then transfer. Bytes are
    written to a `.partial` file and renamed only after the cap, the clock and the hash all agree, so
    an interrupted transfer can never be mistaken for a complete archive by a later `inspect` run.
    """
    if not permission_granted(manifest, url):
        raise TransferRefusedError(
            f"no granted permission verdict for {url}; this lane does not fetch on an inferred or "
            "collection-level grant"
        )
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise TransferRefusedError(f"refused non-HTTPS transfer scheme {parsed.scheme!r}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise TransferRefusedError(f"host {parsed.hostname!r} is not on the transfer allowlist")

    resolved_attempts = min(attempts or limits.http_attempts, limits.http_attempts)
    director = opener or urllib.request.build_opener(_RefusingRedirectHandler)
    deadline = time.monotonic() + limits.wall_seconds
    partial = destination.with_suffix(destination.suffix + ".partial")
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for attempt in range(resolved_attempts):
        if time.monotonic() > deadline:
            raise AcquisitionLimitError(f"wall budget of {limits.wall_seconds}s exhausted after {attempt} attempts")
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")  # noqa: S310 - scheme and host checked above
        try:
            with director.open(request, timeout=limits.request_seconds) as response:
                digest = hashlib.sha256()
                written = 0
                with partial.open("wb") as handle:
                    while chunk := response.read(_CHUNK_BYTES):
                        written += len(chunk)
                        if written > limits.max_archive_bytes:
                            partial.unlink(missing_ok=True)
                            raise AcquisitionLimitError(
                                f"{url} exceeded the {limits.max_archive_bytes}-byte per-archive cap mid-transfer"
                            )
                        if time.monotonic() > deadline:
                            partial.unlink(missing_ok=True)
                            raise AcquisitionLimitError(f"wall budget exhausted mid-transfer of {url}")
                        digest.update(chunk)
                        handle.write(chunk)
                receipt = TransferReceipt(
                    requested_url=url,
                    final_url=response.geturl(),
                    status=response.status,
                    headers=_safe_headers(dict(response.headers.items())),
                    bytes_written=written,
                    sha256=digest.hexdigest(),
                    retrieved_at=datetime.now(UTC).isoformat(),
                    destination=str(destination),
                )
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            continue
        partial.replace(destination)
        return receipt

    raise AcquisitionLimitError(f"{url} did not transfer within {resolved_attempts} attempts: {last_error}")


def encode_receipt(receipt: TransferReceipt) -> str:
    """Render a transfer receipt as the JSON line the CLI prints."""
    return json.dumps(
        {
            "requested_url": receipt.requested_url,
            "final_url": receipt.final_url,
            "status": receipt.status,
            "headers": dict(receipt.headers),
            "bytes": receipt.bytes_written,
            "sha256": receipt.sha256,
            "retrieved_at": receipt.retrieved_at,
            "destination": receipt.destination,
        },
        sort_keys=True,
    )


__all__ = [
    "ALLOWED_HOSTS",
    "RedirectRefusedError",
    "TransferReceipt",
    "TransferRefusedError",
    "encode_receipt",
    "fetch_archive",
    "permission_granted",
]
