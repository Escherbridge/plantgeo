"""Native-identity stability across two complete equivalent releases of one collection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: A release is comparable only when it is `complete`. The governance audit is explicit that
#: "partial exports, timeouts, schema failures and changed rights filters must not generate deletion
#: tombstones", so a partial release on either side makes the comparison `inconclusive` rather than
#: producing a missing-key list that looks like a withdrawal.
COMPARABLE_OUTCOME = "complete"


@dataclass(frozen=True, slots=True)
class IdentityStabilityReport:
    """What two releases say about each other's native keys. A verdict, never a deletion instruction."""

    older_release_key: str
    newer_release_key: str
    continued: tuple[str, ...]
    added: tuple[str, ...]
    missing: tuple[str, ...]
    #: Keys present in both whose row content hash changed: a correction, a re-determination, or a
    #: reused key. Which of those it is cannot be decided here, only reported.
    reused_with_changed_content: tuple[str, ...]
    duplicate_keys_in_older: tuple[str, ...]
    duplicate_keys_in_newer: tuple[str, ...]
    verdict: str

    def as_json(self) -> str:
        """Render the report as the JSON line the `compare` command prints."""
        return json.dumps(
            {
                "older_release_key": self.older_release_key,
                "newer_release_key": self.newer_release_key,
                "verdict": self.verdict,
                "counts": {
                    "continued": len(self.continued),
                    "added": len(self.added),
                    "missing": len(self.missing),
                    "reused_with_changed_content": len(self.reused_with_changed_content),
                    "duplicate_keys_in_older": len(self.duplicate_keys_in_older),
                    "duplicate_keys_in_newer": len(self.duplicate_keys_in_newer),
                },
            },
            sort_keys=True,
        )


def _index(rows: Sequence[Mapping[str, str]]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Map native key -> content hash, and list every key the release used more than once."""
    by_key: dict[str, str] = {}
    duplicates: list[str] = []
    for row in rows:
        key = row["source_record_key"]
        if key in by_key:
            duplicates.append(key)
            continue
        by_key[key] = row["row_sha256"]
    return by_key, tuple(sorted(set(duplicates)))


def compare_releases(  # noqa: PLR0913 - two releases, their keys and their outcomes are five inputs
    older_rows: Sequence[Mapping[str, str]],
    newer_rows: Sequence[Mapping[str, str]],
    *,
    older_release_key: str,
    newer_release_key: str,
    older_outcome: str = COMPARABLE_OUTCOME,
    newer_outcome: str = COMPARABLE_OUTCOME,
) -> IdentityStabilityReport:
    """Compare two releases on native keys and report whether identity held.

    `missing` is a LIST OF KEYS, not a withdrawal. A shrinking export is only a candidate withdrawal
    after two complete equivalent exports or an explicit publisher correction, and this function
    supplies exactly one half of that evidence; nothing here writes a tombstone.

    The verdict is `stable` when nothing is missing, nothing reused a key for different content and
    neither release repeated a key; `unstable` when any of those hold; `inconclusive` when either
    release is not `complete`, because you cannot tell a withdrawn record from an untransferred one.
    """
    older_index, older_duplicates = _index(older_rows)
    newer_index, newer_duplicates = _index(newer_rows)
    older_keys, newer_keys = set(older_index), set(newer_index)
    continued = tuple(sorted(older_keys & newer_keys))
    changed = tuple(sorted(key for key in continued if older_index[key] != newer_index[key]))
    missing = tuple(sorted(older_keys - newer_keys))
    added = tuple(sorted(newer_keys - older_keys))

    if older_outcome != COMPARABLE_OUTCOME or newer_outcome != COMPARABLE_OUTCOME:
        verdict = "inconclusive"
    elif missing or changed or older_duplicates or newer_duplicates:
        verdict = "unstable"
    else:
        verdict = "stable"

    return IdentityStabilityReport(
        older_release_key=older_release_key,
        newer_release_key=newer_release_key,
        continued=continued,
        added=added,
        missing=missing,
        reused_with_changed_content=changed,
        duplicate_keys_in_older=older_duplicates,
        duplicate_keys_in_newer=newer_duplicates,
        verdict=verdict,
    )


__all__ = ["COMPARABLE_OUTCOME", "IdentityStabilityReport", "compare_releases"]
