# Layer L0: Foundation

## Responsibility
Pure mechanism helpers with no domain meaning and no I/O (canonical JSON serialization, SHA-256 digests, UTC/ISO-prefix date handling, unit/range guards).

## Admission Test
A candidate belongs in `foundation` only if **all four** hold:
1. It imports no first-party module.
2. It imports no `sqlalchemy`, `httpx`, `asyncpg`, or `click`.
3. It is used by two or more layers.
4. Its name describes a mechanism, not a domain noun.

## Invariants
- `foundation` may not grow in the same commit as the caller that needs it.
- Strict 0-first-party-import rule.
