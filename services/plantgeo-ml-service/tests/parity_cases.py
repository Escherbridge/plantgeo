"""The fixed inputs every cross-service parity fixture is built from, and how to evaluate them.

Shared by `test_canonical_parity.py`, `test_contracts_parity.py` and
`scripts/regenerate_parity_fixtures.py`, so the golden fixture and the assertion can never be built
from two different input sets. See `tests/AGENTS.md`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from types import ModuleType

#: The monorepo root, three parents up from this file (tests/ -> service -> services/ -> repo).
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]

SIBLING_PACKAGE_ROOT: Final = REPOSITORY_ROOT / "services" / "agri-data-service" / "src" / "agri_data_service"

FIXTURE_DIRECTORY: Final = Path(__file__).resolve().parent / "fixtures" / "parity"

#: JSON documents whose canonical rendering both services must agree on, byte for byte.
CANONICAL_JSON_CASES: Final[tuple[Any, ...]] = (
    {"b": 1, "a": 2},
    {"nested": {"z": [3, 2, 1], "a": {"deep": True}}, "alpha": None},
    [1, 2.5, "three", False, None],
    {"unicode": "café åäö", "emoji_free": "plain"},
    # A non-ASCII KEY, not just a non-ASCII value: `ensure_ascii` changes the rendering of both, and
    # a digest taken over this document is what caught the divergent local helper in the wind model.
    {"température_moyenne": 12.5, "précipitation": None, "ascii_key": "ascii value"},
    {"empty_object": {}, "empty_list": [], "empty_string": ""},
    {"float_edges": [0.0, -0.0, 1e-9, 1e20]},
    "a bare string",
    42,
)

#: Text digested as UTF-8 by both services.
DIGEST_TEXT_CASES: Final[tuple[str, ...]] = (
    "",
    "a",
    "café",
    '{"a":1,"b":2}',
    '{"température_moyenne":12.5}',  # the canonical rendering of a non-ASCII KEY, digested
    "x" * 1000,
)

#: The same inputs as raw bytes, hex-encoded so the fixture stays JSON.
DIGEST_BYTES_HEX_CASES: Final[tuple[str, ...]] = ("", "00", "ff00ff", "68656c6c6f")

#: Floats `validate_finite` must accept, and the non-finite ones it must refuse.
FINITE_CASES: Final[tuple[float, ...]] = (0.0, -1.5, 1e308, -0.0)
NON_FINITE_CASES: Final[tuple[str, ...]] = ("nan", "inf", "-inf")

#: URLs `reject_credential_url` must accept, and the ones it must refuse.
CREDENTIAL_URL_CASES: Final[tuple[str, ...]] = (
    "https://example.test/path",
    "https://user:secret@example.test/path",
    "https://example.test/path?api_key=abc",
    "https://example.test/path?page=2",
    "https://example.test/path#access_token=abc",
    "token=abc",
    "page=2",
    "",
)


def load_module_by_path(module_name: str, path: Path) -> ModuleType:
    """Import one module from an explicit file path, without putting its package on sys.path."""
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise ImportError(f"cannot load {module_name} from {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _outcome(call: Any) -> str:
    """Return one call's rendered result, or the name of the exception it raised."""
    try:
        return f"ok:{call()}"
    except Exception as error:  # a refusal is part of the contract, so its TYPE is the golden value
        return f"raised:{type(error).__name__}"


def evaluate_canonical(module: ModuleType) -> dict[str, list[str]]:
    """Return every canonical-helper outcome for the fixed case set, as comparable strings."""
    return {
        "canonical_json": [module.canonical_json(case) for case in CANONICAL_JSON_CASES],
        "sha256_digest_text": [module.sha256_digest(case) for case in DIGEST_TEXT_CASES],
        "sha256_digest_bytes": [module.sha256_digest(bytes.fromhex(case)) for case in DIGEST_BYTES_HEX_CASES],
        "validate_finite": [_outcome(lambda case=case: module.validate_finite(case)) for case in FINITE_CASES],
        "validate_finite_refusals": [
            _outcome(lambda case=case: module.validate_finite(float(case))) for case in NON_FINITE_CASES
        ],
    }


def evaluate_contracts(module: ModuleType) -> dict[str, list[str]]:
    """Return every custody-helper outcome for the fixed case set, as comparable strings."""
    return {
        "canonical_json_bytes": [module.canonical_json_bytes(case).hex() for case in CANONICAL_JSON_CASES],
        "reject_credential_url": [
            _outcome(lambda case=case: module.reject_credential_url(case)) for case in CREDENTIAL_URL_CASES
        ],
    }
