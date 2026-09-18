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

## Ruled exception: `foundation/region/`

`region` fails criterion 4 (`region` is the platform's central domain noun, not a mechanism) and, at
the time it was placed, criterion 3 (no consumer read `load_region()` yet). It sits here anyway
because `conductor/code_styleguides/federation.md` §1 and `python.md` "Readability and region
portability" both name `foundation/region/` as the one place a deployment's typed footprint lives,
overriding the general test for this one package. It also does I/O (`load_region()` reads
`PLANTGEO_REGION` and, lazily, `<slug>.json`), against this directory's own "no I/O" responsibility
line above -- same ruling. `foundation/region/__init__.py`'s docstring carries the matching note
rather than asserting conformance it does not have.
