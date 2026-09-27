"""Secret redaction for every first-party log line, stdlib bridge record and usage line.

See `foundation/observability/AGENTS.md` "Redaction" for where each function runs in the pipeline
and why the order (exact-value scrub, then truncate, then regex) is pinned. `jobs/lease.py::redact_text`
and `ingest/results.py::redact_secrets` re-export `redact_strict` from here rather than each keeping
their own copy of the ledger pattern (design §1.1, "Kept from logs-first").

Never logged, by construction of the callers that use this module: headers, bodies, URLs, SQL,
parameters, locals. This module itself only ever receives strings and JSON-safe structures -- it
never opens a socket, a database connection or a file other than the process's own `.env` (read,
never written).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

REDACTED_PLACEHOLDER: Final = "[redacted]"
SQL_REDACTED_PLACEHOLDER: Final = "[sql redacted]"
DEPTH_LIMIT_PLACEHOLDER: Final = "[depth-limit]"
TRUNCATION_SUFFIX: Final = "…[truncated]"

# How deep `redact_value` (and the logging processors built on it) will walk a mapping or list
# before giving up and stubbing the remainder. Matches design §1.7's "8-level depth stub".
MAX_REDACTION_DEPTH: Final = 8

# The per-leaf truncation width (design §1.1's "8 KiB" leaf bound). Applied to individual string
# leaves inside the structlog processor chain, never to a whole rendered line (that is the 16 KiB
# line clamp, a separate concern in `logging.py`).
LEAF_TRUNCATE_BYTES: Final = 8 * 1024

# `describe_error`'s clamp width -- the same one `jobs/lease.py::FAILURE_SUMMARY_MAX_LENGTH` uses,
# named here so the test that pins it and this module's own docstring reference one symbol rather
# than a bare literal (ruff PLR2004).
DESCRIBE_ERROR_MAX_LENGTH: Final = 500

# Truncation drops the trailing partial token by cutting at the last separator within this many
# trailing bytes, so a secret whose value straddles the cut point never leaks its prefix (BUI2-10):
# the exact-value scrub below always runs BEFORE truncation, over the whole untruncated string.
_TRUNCATE_TAIL_WINDOW_BYTES: Final = 256
_TRUNCATE_SEPARATOR_BYTES: Final = frozenset(b" \t\r\n/?&=:,;\"'")

# A URL can carry an API key (FIRMS embeds `MAP_KEY` in the path, not the query) and a DSN carries a
# password; either reaching an operator-facing summary publishes a live credential. Each alternative
# substitutes a whole whitespace-delimited token rather than parsing it, because a partial match
# leaves the secret in the half that survived: scheme-shaped, user@host-shaped, and a bare query
# tail for a message that names a key without naming its scheme. This is the ledger pattern
# `jobs/lease.py` and `ingest/results.py` used to each keep a private copy of; both now re-export
# `redact_strict` from here, unchanged, byte for byte.
_SECRET_SHAPED: Final = re.compile(r"[a-z][a-z0-9+.\-]*://\S+|\S+@\S+|\?\S+", re.IGNORECASE)


def redact_strict(text: str) -> str:
    """Substitute every URL-shaped, user@host-shaped and query-shaped token, whole.

    The ledger pattern, verbatim. Used where a whole token must go rather than a value-only scrub,
    e.g. `agri.job_work_item.last_error_summary` and `agri.job_attempt.error_summary`.
    """
    return _SECRET_SHAPED.sub(REDACTED_PLACEHOLDER, text)


# --- Exact-value scrub: collect live secret values, then substitute each one whole ----------------

_SECRET_ENV_NAME_SUFFIXES: Final = (
    "_API_KEY",
    "_KEY",
    "_SECRET",
    "_SECRET_ACCESS_KEY",
    "_ACCESS_KEY_ID",
    "_TOKEN",
    "_PASSWORD",
)
# Suffixes checked separately from the ones above: a value only qualifies through these when it is
# itself DSN-shaped and carries a password (`_dsn_password` below), so a benign public URL (e.g.
# `PLANTCOMMERCE_API_URL`) never gets treated as a secret just for ending in `_URL`.
_DSN_SHAPED_ENV_NAME_SUFFIXES: Final = ("_DATABASE_URL", "_URL")
_SECRET_ENV_EXACT_NAMES: Final = frozenset(
    {
        "DATABASE_URL",
        "DATABASE_URL_SYNC",
        "CDSAPI_KEY",
        "NASA_FIRMS_KEY",
        "OPEN_METEO_API_KEY",
        "USGS_WATER_DATA_API_KEY",
        "R2_SECRET_ACCESS_KEY",
        "LOCAL_SOURCE_LOADER_DATABASE_URL",
        # Railway/production DSN and password forms this service's own Settings fields and Railway's
        # own environment use (security review: these were reachable via a bare DSN echo but matched
        # no suffix and no exact name).
        "RECEIVER_WRITER_DATABASE_URL",
        "PUBLISHED_READER_DATABASE_URL",
        "FORECAST_MV_REFRESH_DATABASE_URL",
        "FORECAST_ITERATION_DATABASE_URL",
        "PGPASSWORD",
        "DATABASE_PUBLIC_URL",
        "REDIS_URL",
    }
)
# `PLANTGEO_*` names are this service's own non-secret tuning switches (profiles, turn context,
# feature flags); never treated as a secret even if a future name happened to end in `_TOKEN`.
_NEVER_SECRET_ENV_PREFIX: Final = "PLANTGEO_"
_MIN_SECRET_VALUE_LENGTH: Final = 8
_DOTENV_FILENAME: Final = ".env"
# A quoted `.env` value needs at least an opening and a closing quote character to strip.
_MIN_QUOTED_VALUE_LENGTH: Final = 2

# `register_secret_values` seam: a caller (a lane that mints a short-lived credential the process
# environment never held) adds values here so they are scrubbed too. Module-level and mutable by
# design; cleared only by process exit, since a secret once minted stays live for the process.
_registered_secret_values: set[str] = set()


def register_secret_values(values: object) -> None:
    """Add one or more secret values to the exact-value scrub, in addition to the environment scan.

    Accepts a `str`, `bytes` (decoded utf-8, errors ignored) or an iterable of either; anything else
    -- `None`, a bare iterable of ints, an unrelated object -- is silently ignored rather than raised
    into the caller, since a fault in registering a secret must never itself become the thing that
    breaks a lane (this module may not import `logging.py`, which imports it, so it cannot log the
    type-name warning a caller-side reviewer might otherwise want; the caller is the one place that
    can safely report a rejected registration). Anything shorter than the minimum secret length is
    ignored, the same rule the environment scan applies.
    """
    raw_candidates: Iterable[object]
    if isinstance(values, (str, bytes)):
        raw_candidates = [values]
    elif isinstance(values, Iterable):
        raw_candidates = values
    else:
        return
    for candidate in raw_candidates:
        text: str | None
        if isinstance(candidate, str):
            text = candidate
        elif isinstance(candidate, bytes):
            text = candidate.decode("utf-8", errors="ignore")
        else:
            text = None
        if text is not None and len(text) >= _MIN_SECRET_VALUE_LENGTH:
            _registered_secret_values.add(text)


def _is_secret_env_name(name: str) -> bool:
    if name.startswith(_NEVER_SECRET_ENV_PREFIX):
        return False
    if name in _SECRET_ENV_EXACT_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _SECRET_ENV_NAME_SUFFIXES)


def _is_dsn_shaped_env_name(name: str) -> bool:
    return not name.startswith(_NEVER_SECRET_ENV_PREFIX) and any(
        name.endswith(suffix) for suffix in _DSN_SHAPED_ENV_NAME_SUFFIXES
    )


class _DotenvCache:
    """A mutable holder for the `.env` cache -- attribute mutation, not module `global`, keeps ruff's
    PLW0603 (discouraged rebinding of a module name) from firing on what is otherwise an ordinary
    memoization.
    """

    __slots__ = ("loaded", "mtime", "pairs")

    def __init__(self) -> None:
        self.mtime: float | None = None
        self.pairs: dict[str, str] = {}
        self.loaded = False


_dotenv_cache = _DotenvCache()


def _dotenv_pairs() -> dict[str, str]:
    """Parse `Path.cwd()/.env` with a minimal, dependency-free reader, cached by mtime.

    Mirrors `Settings`' `env_file=".env"` behavior closely enough for this purpose (a name/value
    scrub list) without importing `config`, which would pull SQLAlchemy/httpx transitively into a
    module `foundation` may not import (BUI2-10; `tests/test_layer_import_contract.py`). Cached by
    the file's mtime (a missing file caches as `None`) so a hot per-line redaction path re-reads the
    file only when it actually changes, not once per log line (security review: measured at 7 reads
    per line before this cache existed).
    """
    path = Path.cwd() / _DOTENV_FILENAME
    try:
        mtime: float | None = path.stat().st_mtime
    except OSError:
        mtime = None
    if _dotenv_cache.loaded and _dotenv_cache.mtime == mtime:
        return _dotenv_cache.pairs
    pairs: dict[str, str] = {}
    if mtime is not None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            name = name.strip().removeprefix("export ").strip()
            value = value.strip()
            # python-dotenv strips an unquoted inline `# comment`; a quoted value keeps everything
            # inside its quotes verbatim, including a literal `#`.
            is_quoted = len(value) >= _MIN_QUOTED_VALUE_LENGTH and value[0] == value[-1] and value[0] in "\"'"
            if not is_quoted:
                value = value.split(" #", 1)[0].rstrip()
            if is_quoted:
                value = value[1:-1]
            if name:
                pairs[name] = value
    _dotenv_cache.mtime = mtime
    _dotenv_cache.pairs = pairs
    _dotenv_cache.loaded = True
    return pairs


def _dsn_password(value: str) -> str | None:
    """Return a DSN-shaped value's password component, or None when it is not DSN-shaped."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.password:
        return parsed.password
    return None


class _SecretCache:
    """Same rationale as `_DotenvCache`: attribute mutation avoids a `global` rebind."""

    __slots__ = ("key", "loaded", "values")

    def __init__(self) -> None:
        self.key: tuple[object, ...] | None = None
        self.values: dict[str, str] = {}
        self.loaded = False


_secret_cache = _SecretCache()


def _collected_secret_values() -> dict[str, str]:
    """Build the name -> value map the exact-value scrub substitutes, memoized until the inputs move.

    The cache key is the (name, value) pairs that actually matter -- the environment's secret-named
    entries, the `.env` pairs (themselves mtime-cached by `_dotenv_pairs`) and the registered set --
    so a test's `monkeypatch.setenv` invalidates it exactly the same call it takes effect on, while a
    chatty steady-state process (dozens of redactions per second) stops re-parsing and re-scanning on
    every single line.
    """
    dotenv_pairs = _dotenv_pairs()
    env_items = tuple(
        sorted(
            (name, value)
            for name, value in os.environ.items()
            if _is_secret_env_name(name) and len(value) >= _MIN_SECRET_VALUE_LENGTH
        )
    )
    dsn_env_items = tuple(
        sorted(
            (name, value)
            for name, value in os.environ.items()
            if _is_dsn_shaped_env_name(name) and _dsn_password(value)
        )
    )
    cache_key = (
        env_items,
        dsn_env_items,
        tuple(sorted(dotenv_pairs.items())),
        tuple(sorted(_registered_secret_values, key=len, reverse=True)),
    )
    if _secret_cache.loaded and _secret_cache.key == cache_key:
        return _secret_cache.values

    collected: dict[str, str] = {}
    for name, value in env_items:
        collected[name] = value
        password = _dsn_password(value)
        if password and len(password) >= _MIN_SECRET_VALUE_LENGTH:
            collected[f"{name}:password"] = password
    for name, value in dsn_env_items:
        password = _dsn_password(value)
        if password and len(password) >= _MIN_SECRET_VALUE_LENGTH:
            collected.setdefault(f"{name}:password", password)
    for name, value in dotenv_pairs.items():
        if _is_secret_env_name(name) and len(value) >= _MIN_SECRET_VALUE_LENGTH:
            collected.setdefault(name, value)
            password = _dsn_password(value)
            if password and len(password) >= _MIN_SECRET_VALUE_LENGTH:
                collected.setdefault(f"{name}:password", password)
    for index, value in enumerate(sorted(_registered_secret_values, key=len, reverse=True)):
        collected[f"registered:{index}"] = value

    _secret_cache.values = collected
    _secret_cache.key = cache_key
    _secret_cache.loaded = True
    return collected


def exact_value_scrub(text: str) -> str:
    """Replace every live secret value found whole in `text` with `[redacted:<NAME>]`.

    Values are substituted longest first so a short value that happens to be a substring of a
    longer one (a password contained in the DSN that carries it, say) never leaves the longer
    secret partially exposed.
    """
    if not text:
        return text
    result = text
    for name, value in sorted(_collected_secret_values().items(), key=lambda item: len(item[1]), reverse=True):
        if value and value in result:
            result = result.replace(value, f"[redacted:{name}]")
    return result


# --- Truncation: drop the trailing partial token, never the secret prefix ------------------------


def truncate_leaf(value: str, limit: int = LEAF_TRUNCATE_BYTES) -> str:
    """Cut a string leaf to at most `limit` UTF-8 bytes, dropping the trailing partial token.

    Callers scrub known secret values BEFORE calling this (the pinned processor order in
    `logging.py`), so a secret that straddles the cut point has already been replaced and cannot
    leak a prefix.
    """
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    window = encoded[:limit]
    tail_start = max(0, limit - _TRUNCATE_TAIL_WINDOW_BYTES)
    cut = None
    for index in range(len(window) - 1, tail_start - 1, -1):
        if window[index] in _TRUNCATE_SEPARATOR_BYTES:
            cut = index + 1
            break
    if cut is not None:
        window = window[:cut]
    return window.decode("utf-8", errors="ignore") + TRUNCATION_SUFFIX


# --- Regex-based scrubbing: SQL blocks, DSN userinfo, secret query params, bearer tokens ----------

_SQL_BLOCK_MARKER: Final = "[SQL: "
# Greedy up to the LAST '@' before the next '/' or whitespace, not the first: a password that itself
# contains an unencoded '@' (a hand-written DSN) previously left everything after its first '@'
# exposed (security review).
_DSN_USERINFO: Final = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s]*@")
# A driver message's bound-parameter fragment, cut alongside the `[SQL: ...]` block it usually
# follows (asyncpg's "query argument $1: '<value>'" and psycopg's "DETAIL: Key (col)=(<value>)").
# `[^\n]*` rather than a lazy dot-star: each fragment is redacted to the end of ITS OWN line, never
# spilling into (or being swallowed by) a neighbouring line.
_QUERY_ARGUMENT_FRAGMENT: Final = re.compile(r"query argument \$\d+:[^\n]*")
_DETAIL_KEY_FRAGMENT: Final = re.compile(r"DETAIL:\s*Key\s*\([^)]*\)=\([^)]*\)")
_SECRET_QUERY_PARAM_NAMES: Final = (
    "apikey",
    "api_key",
    "api-key",
    "x-api-key",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "map_key",
    "password",
    "pwd",
    "passwd",
    "secret",
    "client_secret",
    "sig",
    "signature",
)
# `(?<![A-Za-z0-9])` rather than `\b`: `\b` does not fire after `_`, so `client_secret=`/`refresh_
# token=` previously survived untouched (security review). The optional `(?:[A-Za-z0-9]+[_-])*`
# prefix lets any `*_token`/`*_key`/`*_secret`/`*_password`-shaped name match while keeping the whole
# matched name (prefix included) in the replacement, not only its bare suffix.
_SECRET_QUERY_PARAM: Final = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:"
    + "|".join(_SECRET_QUERY_PARAM_NAMES)
    + r"|x-amz-[a-z0-9]+))=([^&\s]+)"
)
# The quoted `'name': 'value'` / `"name": "value"` shape a dict repr or an echoed JSON body takes --
# `_SECRET_QUERY_PARAM` only ever matches the URL `name=value` shape and never this one.
_SECRET_QUOTED_KV: Final = re.compile(
    r"(?i)(['\"]?)([\w-]*(?:api[_-]?key|token|secret|password|pwd|passwd|authorization|"
    r"private[_-]?token|cookie)[\w-]*)\1\s*:\s*(['\"])(.*?)\3"
)
_BEARER_TOKEN: Final = re.compile(r"(?i)\bBearer\s+\S+")
# Consumes the scheme word AND the credential that follows it (`Basic dXNlcjpz...`, `Token abc...`),
# not only the first token: the original pattern's `\S+` stopped at the space before the credential,
# leaving a Basic/Token credential exposed (security review). `(?:proxy-)?` covers the sibling
# `Proxy-Authorization` header.
_AUTHORIZATION_HEADER: Final = re.compile(r"(?i)\b((?:proxy-)?authorization)\s*[:=]\s*(?:[A-Za-z][\w-]*\s+)?\S+")


def _cut_sql_block(text: str) -> str:
    index = text.find(_SQL_BLOCK_MARKER)
    if index != -1:
        text = text[:index] + SQL_REDACTED_PLACEHOLDER
    text = _QUERY_ARGUMENT_FRAGMENT.sub("query argument [redacted]", text)
    return _DETAIL_KEY_FRAGMENT.sub("DETAIL: [redacted]", text)


def _quoted_kv_replacement(match: re.Match[str]) -> str:
    quote, name, value_quote = match.group(1), match.group(2), match.group(3)
    return f"{quote}{name}{quote}: {value_quote}[redacted]{value_quote}"


def _regex_scrub(text: str) -> str:
    text = _cut_sql_block(text)
    text = _DSN_USERINFO.sub(r"\1[redacted]@", text)
    text = _SECRET_QUERY_PARAM.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _SECRET_QUOTED_KV.sub(_quoted_kv_replacement, text)
    text = _BEARER_TOKEN.sub("Bearer [redacted]", text)
    return _AUTHORIZATION_HEADER.sub(lambda match: f"{match.group(1)}: [redacted]", text)


def redact_for_log(text: str) -> str:
    """The general-purpose text redactor: exact-value scrub, then the regex-based scrubs.

    Used wherever a whole message is redacted outside the per-leaf structlog pipeline: the stdlib
    bridge's formatted record, `describe_error`, and the usage writers' free-text fields. The
    structlog processor chain in `logging.py` calls the lower-level pieces directly instead, so it
    can insert `truncate_leaf` between the scrub and the regex step (design §1.1).
    """
    return _regex_scrub(exact_value_scrub(text))


def redact_leaf(value: str) -> str:
    """The per-leaf pipeline pinned processor order uses: scrub, then truncate, then regex."""
    return _regex_scrub(truncate_leaf(exact_value_scrub(value)))


# --- Structured redaction: walk mappings/lists, redact matching keys and every string leaf --------

# Casefolded key set a value is fully redacted for outright, regardless of its own shape -- kept
# alongside the suffix/substring predicate below rather than replaced by it, so an exact historical
# name never depends on the predicate continuing to cover it. The bare `key` is deliberately
# excluded (burn-severity's `manifest.key` must survive); it stays covered by the query-parameter
# rule above, which only fires inside a URL-shaped string.
_REDACTED_KEY_NAMES: Final = frozenset(
    {
        "apikey",
        "api_key",
        "x-api-key",
        "api-key",
        "token",
        "access_token",
        "map_key",
        "password",
        "secret",
        "client_secret",
        "authorization",
        "dsn",
        "database_url",
    }
)
_BARE_KEY_EXEMPT: Final = frozenset({"key"})
_REDACTED_KEY_SUFFIXES: Final = ("_key", "_token", "_secret", "_password", "_database_url")
_REDACTED_KEY_SUBSTRINGS: Final = (
    "apikey",
    "api_key",
    "api-key",
    "secret",
    "password",
    "token",
    "authorization",
    "cookie",
    "credential",
)


# A key containing any of these is URL/query/DSN-SHAPED data using a key slot, not a short name --
# `redact_value`/`_redact_tree` must redact its own TEXT (the query-parameter regex scrub), never
# treat it as a "key NAME" whose value is wholesale redacted while the name itself (the whole URL,
# secret and all) is left untouched. Without this guard, `{"https://h/?apikey=SECRET": 1}`'s key
# contains the substring "apikey" and `is_redacted_key` matched the WHOLE URL, which kept the key
# (SECRET included) verbatim and only blanked the harmless value `1` (found while adding this rule).
_KEY_NAME_SHAPE_EXCLUDED_CHARS: Final = frozenset("/:?=&")


def is_redacted_key(key: object) -> bool:
    """True when a mapping key NAMES a value that must be redacted outright, never inspected.

    A predicate, not only the historical exact set: realistic names this service actually uses
    (`open_meteo_api_key`, `cdsapi_key`, `receiver_writer_database_url`, `cookie`, `set-cookie`) fell
    through the old exact-match set entirely (security review).
    """
    if not isinstance(key, str):
        return False
    if any(char in key for char in _KEY_NAME_SHAPE_EXCLUDED_CHARS):
        return False
    normalized = key.casefold().replace("-", "_")
    if normalized in _BARE_KEY_EXEMPT:
        return False
    if normalized in _REDACTED_KEY_NAMES:
        return True
    if normalized.endswith(_REDACTED_KEY_SUFFIXES):
        return True
    return any(substring in normalized for substring in _REDACTED_KEY_SUBSTRINGS)


def _dedupe_key(existing: dict[object, object], key: object) -> object:
    """Suffix a redacted-key collision (`#1`, `#2`, ...) rather than let one silently overwrite another."""
    if key not in existing:
        return key
    suffix = 1
    candidate = f"{key}#{suffix}"
    while candidate in existing:
        suffix += 1
        candidate = f"{key}#{suffix}"
    return candidate


def redact_value(obj: object, *, _depth: int = 0, leaf_limit: int | None = None) -> object:
    """Recursively redact a JSON-safe structure: matching keys wholesale, every other string leaf.

    Non-string, non-container leaves (numbers, booleans, ``None``) pass through unchanged; a value
    deeper than `MAX_REDACTION_DEPTH` becomes `"[depth-limit]"` rather than being walked further. A
    string KEY is itself redacted for its own content (not only checked by name) -- a dict keyed by
    a URL that embeds an `apikey=` query parameter previously published that key verbatim (security
    review) -- while a key whose NAME matches `is_redacted_key` keeps its name and has only its value
    replaced, exactly as before. `leaf_limit`, when given, truncates each string leaf (via
    `truncate_leaf`) before the regex scrub, matching the per-leaf structlog pipeline's own 8 KiB
    bound instead of only the whole-line 64 KiB one (`router.py` passes `LEAF_TRUNCATE_BYTES`).
    """
    if _depth > MAX_REDACTION_DEPTH:
        return DEPTH_LIMIT_PLACEHOLDER
    if isinstance(obj, dict):
        result: dict[object, object] = {}
        for key, value in obj.items():
            if is_redacted_key(key):
                result[key] = REDACTED_PLACEHOLDER
                continue
            new_key = _redact_key_text(key, leaf_limit=leaf_limit) if isinstance(key, str) else key
            new_key = _dedupe_key(result, new_key)
            result[new_key] = redact_value(value, _depth=_depth + 1, leaf_limit=leaf_limit)
        return result
    if isinstance(obj, list):
        return [redact_value(item, _depth=_depth + 1, leaf_limit=leaf_limit) for item in obj]
    if isinstance(obj, str):
        return _redact_string_leaf(obj, leaf_limit=leaf_limit)
    return obj


def _redact_key_text(key: str, *, leaf_limit: int | None) -> str:
    return _redact_string_leaf(key, leaf_limit=leaf_limit)


def _redact_string_leaf(value: str, *, leaf_limit: int | None) -> str:
    scrubbed = exact_value_scrub(value)
    if leaf_limit is not None:
        scrubbed = truncate_leaf(scrubbed, limit=leaf_limit)
    return _regex_scrub(scrubbed)


_CLASS_NAME_ONLY_MODULE_NAMES: Final = ("sqlalchemy", "asyncpg", "psycopg", "psycopg2")


def describe_error(error: BaseException) -> str:
    """Describe an exception without ever publishing a statement, a payload, or a keyed URL.

    SQLAlchemy, asyncpg and psycopg/psycopg2 exceptions (recognised by module name, never by
    `isinstance` -- `foundation` may not import any of them, `tests/test_layer_import_contract.py`)
    give only their class name: the message carries the whole statement, its bound parameters, or a
    driver-native `DETAIL: Key (...)=(...)` fragment. Everything else is `redact_for_log(str(error))`,
    clamped to `DESCRIBE_ERROR_MAX_LENGTH` -- the same width `jobs/lease.py::FAILURE_SUMMARY_MAX_LENGTH`
    uses for the same reason.
    """
    module_name = type(error).__module__
    if module_name in _CLASS_NAME_ONLY_MODULE_NAMES or module_name.startswith(
        tuple(f"{name}." for name in _CLASS_NAME_ONLY_MODULE_NAMES)
    ):
        return type(error).__name__
    return redact_for_log(str(error))[:DESCRIBE_ERROR_MAX_LENGTH]


def redacting_fallback(obj: object) -> str:
    """`JSONRenderer(default=...)`'s last-resort hook for a value the renderer cannot serialise.

    Every ordinary leaf is normalised and redacted well before this runs; this exists only so an
    unexpected object never crashes the log call or, worse, reaches the line via `repr()`
    unredacted.
    """
    try:
        rendered = repr(obj)
    except Exception:  # a broken __repr__ must never crash the renderer itself
        rendered = f"<unrepresentable {type(obj).__name__}>"
    return redact_for_log(rendered)
