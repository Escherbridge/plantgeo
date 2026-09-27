"""URL -> verbatim text (HTML via trafilatura, PDF via pypdf) and the DESIGN.md section 2 source_id.

Ported from the research directory's `fetch_sources.py`; see AGENTS.md section "Append pipeline".
"""

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Final
from urllib.parse import urlparse

import httpx
import trafilatura
from pypdf import PdfReader

#: Byte ceiling enforced while the body streams in (after content decoding, so a compressed bomb stops too).
MAXIMUM_BYTES: Final = 80 * 1024 * 1024
#: Per-operation httpx timeout (connect, each read); the whole download also has a wall-clock deadline.
REQUEST_TIMEOUT_SECONDS: Final = 30
TOTAL_TIMEOUT_SECONDS: Final = 180
DEFAULT_CHARSET: Final = "utf-8"
MAXIMUM_SLUG_CHARACTERS: Final = 60
#: A host label is at most 63 characters; capping it keeps `<yyyymmdd>-<host>-<slug>-<digest>` within 121.
MAXIMUM_HOST_CHARACTERS: Final = 40
URL_DIGEST_CHARACTERS: Final = 6
HTTP_ERROR_STATUS: Final = 400
BROWSER_HEADERS: Final = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
DOCUMENT_SUFFIXES: Final = re.compile(r"\.(pdf|html?|aspx?|php)$", re.IGNORECASE)
HEADER_PREFIX: Final = "# "


class FetchError(RuntimeError):
    """Raised when a URL cannot be fetched or yields no text."""


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """Extracted text and the facts the raw-file header records."""

    url: str
    final_url: str
    kind: str
    title: str | None
    text: str
    fetched_at: str


def slugify(text: str) -> str:
    """Lower-case kebab-case of the alphanumeric runs."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def url_host(url: str) -> str:
    """First host label after `www.`, as the existing 25 source ids use (`nrcs.usda.gov` -> `nrcs`)."""
    host = slugify(urlparse(url).netloc.lower().removeprefix("www.").split(".")[0])[:MAXIMUM_HOST_CHARACTERS]
    return host.strip("-") or "source"


def url_slug(url: str, title: str | None = None) -> str:
    """Slug of the last meaningful path segment, falling back to the title, capped at a hyphen boundary."""
    segments = [DOCUMENT_SUFFIXES.sub("", segment) for segment in urlparse(url).path.split("/") if segment]
    candidates = [slugify(segment) for segment in reversed(segments)]
    slug = next((candidate for candidate in candidates if candidate and not candidate.isdigit()), "")
    if not slug and title:
        slug = slugify(title)
    slug = slug or "page"
    if len(slug) > MAXIMUM_SLUG_CHARACTERS:
        slug = slug[:MAXIMUM_SLUG_CHARACTERS].rsplit("-", 1)[0] or slug[:MAXIMUM_SLUG_CHARACTERS]
    return slug


def build_source_id(url: str, fetched_on: datetime, title: str | None = None) -> str:
    """DESIGN.md section 2: `<yyyymmdd>-<host>-<slug>`, never a sequence number."""
    return f"{fetched_on:%Y%m%d}-{url_host(url)}-{url_slug(url, title)}"


def disambiguate(source_id: str, url: str) -> str:
    """Suffix a short URL digest when another URL already holds this source_id (still no sequence number)."""
    return f"{source_id}-{hashlib.sha256(url.encode()).hexdigest()[:URL_DIGEST_CHARACTERS]}"


def extract_pdf_text(payload: bytes) -> tuple[str, str | None]:
    """Page texts joined by blank lines, and the document title if the PDF declares one."""
    reader = PdfReader(BytesIO(payload))
    pages = [page.extract_text() or "" for page in reader.pages]
    title = reader.metadata.title if reader.metadata else None
    return "\n\n".join(pages), title


def extract_html_text(html: str, url: str) -> tuple[str, str | None]:
    """Main-content text (tables kept, recall favoured) and the page title."""
    text = trafilatura.extract(html, url=url, include_tables=True, favor_recall=True) or ""
    metadata = trafilatura.extract_metadata(html, default_url=url)
    return text, metadata.title if metadata else None


@dataclass(frozen=True, slots=True)
class DownloadedBody:
    """A response body read under the byte ceiling and deadline, with what extraction needs from the headers."""

    payload: bytes
    content_type: str
    charset: str | None
    final_url: str


def download(
    url: str,
    http: httpx.Client,
    *,
    maximum_bytes: int = MAXIMUM_BYTES,
    total_timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
) -> DownloadedBody:
    """Stream a GET, stopping with `FetchError` past `maximum_bytes` or the wall-clock deadline.

    The deadline is checked between received pieces, so a single stalled read can overrun it by at most the
    per-read timeout of the client.
    """
    deadline = time.monotonic() + total_timeout_seconds
    with http.stream("GET", url) as response:
        if response.status_code >= HTTP_ERROR_STATUS:
            raise FetchError(f"HTTP {response.status_code} for {url}")
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > maximum_bytes:
            raise FetchError(f"{url} declares {declared} bytes, over the {maximum_bytes}-byte ceiling")
        received = bytearray()
        for piece in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise FetchError(f"{url} did not finish downloading within {total_timeout_seconds} s")
            received.extend(piece)
            if len(received) > maximum_bytes:
                raise FetchError(f"{url} is larger than the {maximum_bytes}-byte ceiling")
        return DownloadedBody(
            payload=bytes(received),
            content_type=response.headers.get("content-type", ""),
            charset=response.charset_encoding,
            final_url=str(response.url),
        )


def extract_text(body: DownloadedBody, url: str) -> tuple[str, str | None, str]:
    """(text, title, kind) from a downloaded body; any extractor failure becomes a one-line `FetchError`."""
    is_pdf = "pdf" in body.content_type or body.payload[:5] == b"%PDF-"
    kind = "pdf" if is_pdf else "html"
    html = ""
    if not is_pdf:
        try:
            html = body.payload.decode(body.charset or DEFAULT_CHARSET, errors="replace")
        except LookupError as error:  # an unknown charset label in the Content-Type header
            raise FetchError(f"{url}: cannot decode the body as {body.charset!r}") from error
    try:
        text, title = extract_pdf_text(body.payload) if is_pdf else extract_html_text(html, url)
    except Exception as error:  # pypdf raises PyPdfError subclasses and, on malformed files, plain errors
        raise FetchError(f"{url}: {kind} text extraction failed ({type(error).__name__}: {error})") from error
    return text, title, kind


def fetch_document(
    url: str,
    client: httpx.Client | None = None,
    *,
    maximum_bytes: int = MAXIMUM_BYTES,
    total_timeout_seconds: float = TOTAL_TIMEOUT_SECONDS,
) -> FetchedDocument:
    """GET a URL (bounded in bytes and time) and extract its text; every failure is a `FetchError`."""
    owned = client is None
    http = client or httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        body = download(url, http, maximum_bytes=maximum_bytes, total_timeout_seconds=total_timeout_seconds)
    except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError) as error:
        raise FetchError(f"{url}: {type(error).__name__}: {error}") from error
    finally:
        if owned:
            http.close()
    text, title, kind = extract_text(body, url)
    if not text.strip():
        raise FetchError(f"no text could be extracted from {url} ({kind})")
    return FetchedDocument(
        url=url,
        final_url=body.final_url,
        kind=kind,
        title=title,
        text=text,
        fetched_at=datetime.now(UTC).isoformat(),
    )


def raw_file_content(document: FetchedDocument) -> str:
    """The stored raw file: the fetch header, a blank line, then the verbatim text (LF, no trailing newline)."""
    header = (
        f"# source_url: {document.url}\n# final_url: {document.final_url}\n# fetched_at: {document.fetched_at}\n"
        f"# kind: {document.kind}\n# title: {document.title}\n\n"
    )
    return header + document.text.replace("\r\n", "\n").rstrip("\n")


def raw_body(content: str) -> str:
    """The raw file without its `# key: value` header, for comparing a re-fetch with what is stored."""
    lines = content.split("\n")
    index = 0
    while index < len(lines) and lines[index].startswith(HEADER_PREFIX):
        index += 1
    return "\n".join(lines[index:]).lstrip("\n")


def parse_header(content: str) -> dict[str, str]:
    """The `# key: value` header lines of a raw file."""
    header: dict[str, str] = {}
    for line in content.split("\n"):
        if not line.startswith(HEADER_PREFIX):
            break
        key, _, value = line.removeprefix(HEADER_PREFIX).partition(":")
        header[key.strip()] = value.strip()
    return header
