"""Guard: every upstream send goes through `ingest/http.py`'s metered factories, or a pinned, reasoned exception.

design §2.1 (COV2-10): a raw upstream HTTP/FTP client constructed anywhere under `src/` or `scripts/`,
outside `ingest/http.py` itself, bypasses the GL-2 meter entirely -- no counters, no fail-open
telemetry switch, no WQ-5 User-Agent. This guard walks the AST rather than grepping text (`_raw_client_count`
below), so it also catches a `from httpx import AsyncClient` name bound at module scope and a bare
module-level `httpx.get`/`post`/`stream`/`request`/`head` send, not only `httpx.Client(`/
`httpx.AsyncClient(`/`cdsapi.Client(`. Each allow-listed file is pinned to the EXACT count of raw
constructions this AST walk finds in it TODAY, so a new one added to an already-allowed file fails the
guard exactly as a brand new file would; a file that is deleted, or whose count drops, is simply never
visited by `rglob` and so can never fail it either way -- the guard only ever objects to MORE raw
clients than were reasoned about, never fewer. These pinned counts are call-site counts (AST `Call`
nodes), which is a different, more precise measure than the plain substring/regex occurrence count the
design record's own illustrative examples used, so a pinned number here need not match one quoted there.

Reasoned per design §2.1 and plan 0W.2's `o3-ingest-meter` task text, in prose rather than a bulleted
list so ERA001 does not mistake this comment for commented-out code. `agent/**` holds another
session's own LLM tool calls, not upstream data sources, and stays an unbounded PREFIX allow (not a
pinned count) both because it is a different kind of client entirely and because another session edits
that tree concurrently with this one. `execution/publisher.py` and `execution/historical_export.py`
are internal endpoints. `execution/historical_era5.py`, `execution/historical_usdm.py`,
`execution/plan_continuation.py` and `execution/weather_observations/nasa_power.py` have no live
entrypoint today; reviving one means removing its entry here and wiring it through the metered
factory. `execution/geospatial_capture.py`, `ingest/mtbs.py` (which also keeps its own separate
`USER_AGENT`, unaffected by `default_user_agent`) and `scripts/capture_sensor_evidence.py` are
operator-only tools run by hand rather than by a scheduled lane.
`pipeline/direct/botanical_occurrences/fetch.py` runs its own bounded `urllib.request` opener with a
no-redirect handler, a documented exception to the shared bounded-fetch contract (`identity.py`'s own
`AGENTS.md` section covers why). `pipeline/direct/crop_cover/source.py` is SHADOW; `s-crop-cover` (a
different track) moves it. Landed 2026-09-27, the day before this slice (git log `56467bd4`), and not
in the design record: `pipeline/direct/soil_properties/{capture,maintain,verify}.py` and
`pipeline/direct/soil_survey/__main__.py` sit outside this partition's owns/reads-only list
(`o3-ingest-meter`'s note field: "FORBIDDEN: every other ingest/* file and pipeline/** except
burn_severity/capture.py"), so this slice cannot wire them through `upstream_sync_client`/
`upstream_client` without stepping outside its own file boundary; recorded here as a stated residual,
not an oversight, for whoever next owns `pipeline/direct/soil_properties/**`/`soil_survey/**` to move
onto the metered factories or re-pin with its own reasoned count.
`pipeline/direct/burn_severity/capture.py` is DELIBERATELY ABSENT from every list below: this slice's
own edit moved it onto `upstream_sync_client`, so it is now ordinary metered code and any raw client
reappearing there is exactly the regression this guard exists to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

# Relative to `services/agri-data-service/src/agri_data_service/`, mapped to the exact number of raw
# client/sender constructions `_raw_client_count` finds in that file today.
ALLOWED_SRC_FILE_COUNTS: Final[dict[str, int]] = {
    "execution/publisher.py": 1,
    "execution/historical_export.py": 1,
    "execution/historical_era5.py": 1,
    "execution/historical_usdm.py": 1,
    "execution/plan_continuation.py": 2,
    "execution/weather_observations/nasa_power.py": 1,
    "execution/geospatial_capture.py": 1,
    "ingest/mtbs.py": 1,
    "pipeline/direct/botanical_occurrences/fetch.py": 2,
    "pipeline/direct/crop_cover/source.py": 2,
    "pipeline/direct/soil_properties/capture.py": 1,
    "pipeline/direct/soil_properties/maintain.py": 1,
    "pipeline/direct/soil_properties/verify.py": 1,
    # 3: areas, capture and (S2) validate. They stay raw because SEC-2(d) requires follow_redirects=False,
    # which upstream_client does not offer yet; follow-up: add that keyword, then meter all three.
    "pipeline/direct/soil_survey/__main__.py": 3,
}

# Relative to `services/agri-data-service/scripts/`.
ALLOWED_SCRIPT_FILE_COUNTS: Final[dict[str, int]] = {"capture_sensor_evidence.py": 1}

# Whole subtrees allowed without a pinned count; see the module docstring for why `agent/**` is one.
ALLOWED_SRC_PREFIXES: Final[tuple[str, ...]] = ("agent/",)

_METERED_FACTORY_FILE: Final = "http.py"

_HTTPX_CLIENT_ATTRS: Final = frozenset({"Client", "AsyncClient"})
_HTTPX_SENDER_ATTRS: Final = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}
)
_HTTPX_RAW_ATTRS: Final = _HTTPX_CLIENT_ATTRS | _HTTPX_SENDER_ATTRS


class _RawClientVisitor(ast.NodeVisitor):
    """Count raw `httpx`/`cdsapi`/`urllib.request` constructions, resolving module and from-import aliases."""

    def __init__(self) -> None:
        self.count = 0
        self._httpx_aliases: set[str] = set()
        self._cdsapi_aliases: set[str] = set()
        self._imported_raw_names: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            if alias.name == "httpx":
                self._httpx_aliases.add(bound)
            elif alias.name == "cdsapi":
                self._cdsapi_aliases.add(bound)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "httpx":
            for alias in node.names:
                if alias.name in _HTTPX_RAW_ATTRS:
                    self._imported_raw_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_raw_client_call(node.func):
            self.count += 1
        self.generic_visit(node)

    def _is_raw_client_call(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Name):
            return func.id in self._imported_raw_names
        if not isinstance(func, ast.Attribute):
            return False
        root = func.value.id if isinstance(func.value, ast.Name) else None
        if root in self._httpx_aliases and func.attr in _HTTPX_RAW_ATTRS:
            return True
        if root in self._cdsapi_aliases and func.attr == "Client":
            return True
        return (_dotted_name(func) or "").startswith("urllib.request.")


def _dotted_name(node: ast.expr) -> str | None:
    """Reconstruct `a.b.c` from a `Name`/`Attribute` chain, or `None` when it does not bottom out in a `Name`."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _raw_client_count(path: Path) -> int:
    """How many raw client/sender constructions `path` contains, by AST rather than a text pattern."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _RawClientVisitor()
    visitor.visit(tree)
    return visitor.count


def _find_violations(
    roots: Iterable[tuple[Path, str, dict[str, int]]],
    *,
    allowed_prefixes: tuple[str, ...] = ALLOWED_SRC_PREFIXES,
) -> list[str]:
    """Walk each `(root, published_prefix, pinned_counts)` triple, returning one message per file over budget.

    A path `rglob` never finds (deleted, renamed) is never visited, so a stale `pinned_counts` entry
    for it can never produce a violation -- the walk-then-look-up structure makes that failure mode
    architecturally impossible, not merely untested.
    """
    violations: list[str] = []
    for root, published_prefix, pinned_counts in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            bare_relative = path.relative_to(root).as_posix()
            relative = published_prefix + bare_relative
            if not published_prefix and relative == f"ingest/{_METERED_FACTORY_FILE}":
                continue
            if any(relative.startswith(prefix) for prefix in allowed_prefixes):
                continue
            actual = _raw_client_count(path)
            pinned = pinned_counts.get(bare_relative, 0)
            if actual > pinned:
                violations.append(f"{relative}: found {actual} raw client(s), {pinned} pinned")
    return violations


def test_no_raw_http_clients_beyond_the_pinned_allow_list() -> None:
    service_root = Path(__file__).resolve().parents[1]
    src_root = service_root / "src" / "agri_data_service"
    scripts_root = service_root / "scripts"

    violations = _find_violations(
        [
            (src_root, "", ALLOWED_SRC_FILE_COUNTS),
            (scripts_root, "scripts/", ALLOWED_SCRIPT_FILE_COUNTS),
        ]
    )
    assert violations == [], f"raw upstream client(s) beyond the pinned allow-list: {violations}"


def test_a_pinned_file_that_no_longer_exists_is_never_visited(tmp_path: Path) -> None:
    """A stale pinned-count entry for a deleted file can never fail the guard: `rglob` never finds it."""
    empty_src = tmp_path / "src"
    empty_src.mkdir()
    violations = _find_violations([(empty_src, "", {"deleted/module.py": 3})])
    assert violations == []


def test_a_brand_new_raw_client_in_an_unlisted_file_fails_the_guard(tmp_path: Path) -> None:
    root = tmp_path / "src"
    (root / "new_lane").mkdir(parents=True)
    (root / "new_lane" / "fetch.py").write_text("import httpx\n\n\ndef f():\n    httpx.Client()\n")
    violations = _find_violations([(root, "", {})])
    assert violations == ["new_lane/fetch.py: found 1 raw client(s), 0 pinned"]


def test_a_new_raw_client_added_to_an_already_allowed_file_still_fails(tmp_path: Path) -> None:
    """Being allow-listed pins a COUNT, not a blank cheque: one more raw client than reasoned about still fails."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "allowed.py").write_text("import httpx\n\n\ndef f():\n    httpx.Client()\n    httpx.Client()\n")
    violations = _find_violations([(root, "", {"allowed.py": 1})])
    assert violations == ["allowed.py: found 2 raw client(s), 1 pinned"]


def test_raw_client_detector_catches_client_constructions_and_module_level_senders(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import httpx\n"
        "import cdsapi\n"
        "from httpx import AsyncClient\n"
        "import urllib.request\n"
        "\n"
        "def f():\n"
        "    httpx.Client()\n"
        "    httpx.AsyncClient()\n"
        "    cdsapi.Client()\n"
        "    AsyncClient()\n"
        "    httpx.get('https://x')\n"
        "    urllib.request.urlopen('https://x')\n"
    )
    expected_raw_client_calls = 6
    assert _raw_client_count(sample) == expected_raw_client_calls


def test_raw_client_detector_ignores_unrelated_calls_and_annotations(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from __future__ import annotations\n"
        "import httpx\n"
        "\n"
        "\n"
        "def f(client: httpx.Client) -> None:\n"
        "    httpx.Timeout(5.0)\n"
        "    dict(a=1)\n"
    )
    assert _raw_client_count(sample) == 0
