"""Score two renderings of the same `agri` DDL: identical, a known reparse, or a real difference.

WHY THIS EXISTS. A database built by the 26-revision chain and one built by the greenfield baseline
hold the same schema but do not `pg_dump` to the same bytes. The baseline executes text the chain's
own `pg_dump` emitted, and PostgreSQL does not re-emit a re-parsed expression the way it emitted the
original: `= ANY ((ARRAY[...])::text[])` comes back as `= ANY (ARRAY[(...)::text])`, the same for
`<> ALL`, and `((A AND B) AND C)` flattens to `(A AND B AND C)`. Measured 2026-08-25: 74 lines
across 41 files, every one of them of that class, zero semantic differences.

So a byte-exact comparison of the two can never pass, and any tool that gates on one -- the
pre-stamp verifier especially -- would refuse a target that is perfectly stampable. This module is
the scoring rule that makes the comparison usable, stated once so the verifier and
`tests/test_alembic_archive_replay_parity.py` cannot drift apart.

WHAT THE RULE ADMITS, AND WHAT IT THEREFORE MISSES. Two renderings are equivalent when their quoted
string literals are identical **in order** and their identifiers are the same **set**. That accepts
all three measured reparse classes -- each re-arranges casts and parentheses around literals it
never changes. It is deliberately weaker than byte equality: an identifier repeated a different
number of times, or reordered, inside an otherwise identical expression would score as equivalent.
The rule is stated here rather than buried so nobody mistakes it for exactness. Every caller pairs
it with an exact leg (catalogue inventories in the test, an exact-lines count in the verifier); this
rule is only ever the fallback for lines that already failed exact comparison.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

_STRING_LITERAL = re.compile(r"'((?:[^']|'')*)'")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def reparse_equivalent(left: str, right: str) -> bool:
    """True when two DDL lines differ only as PostgreSQL's own reparse would make them differ."""
    if left == right:
        return True
    if _STRING_LITERAL.findall(left) != _STRING_LITERAL.findall(right):
        return False
    return set(_IDENTIFIER.findall(left)) == set(_IDENTIFIER.findall(right))


@dataclass
class DdlComparison:
    """The verdict, plus enough evidence for an operator to decide without re-running anything."""

    identical_lines: int = 0
    reparse_lines: int = 0
    unexplained: list[tuple[str, str]] = field(default_factory=list)

    @property
    def equivalent(self) -> bool:
        return not self.unexplained

    def render(self, limit: int = 40) -> str:
        if self.equivalent:
            return (
                f"{self.identical_lines} identical DDL line(s), "
                f"{self.reparse_lines} differing only by the reviewed PostgreSQL reparse"
            )
        lines = [f"{len(self.unexplained)} unexplained DDL difference(s):"]
        for reference, target in self.unexplained[:limit]:
            lines.append(f"  - reference: {reference.rstrip() or '(absent)'}")
            lines.append(f"    target:    {target.rstrip() or '(absent)'}")
        if len(self.unexplained) > limit:
            lines.append(f"  ... {len(self.unexplained) - limit} more")
        return "\n".join(lines)


def compare_ddl(reference: str, target: str) -> DdlComparison:
    """Compare two `pg_dump` renderings line by line, scoring each difference against the rule."""
    reference_lines = reference.splitlines()
    target_lines = target.splitlines()
    comparison = DdlComparison()
    matcher = difflib.SequenceMatcher(a=reference_lines, b=target_lines, autojunk=False)

    for tag, alo, ahi, blo, bhi in matcher.get_opcodes():
        if tag == "equal":
            comparison.identical_lines += ahi - alo
            continue
        if tag == "replace" and (ahi - alo) == (bhi - blo):
            for offset in range(ahi - alo):
                left = reference_lines[alo + offset]
                right = target_lines[blo + offset]
                if reparse_equivalent(left, right):
                    comparison.reparse_lines += 1
                else:
                    comparison.unexplained.append((left, right))
            continue
        # An insert, a delete, or a replace of unequal length: something exists on one side only.
        comparison.unexplained.extend((reference_lines[index], "") for index in range(alo, ahi))
        comparison.unexplained.extend(("", target_lines[index]) for index in range(blo, bhi))

    return comparison
