# scripts

Operator surface. `mypy` covers this directory as well as `src`, so a script is held to the same
type discipline as the service.

## Locked quality receipt

`QUALITY_RECEIPT.json` records the sha256 over the CRLF-normalized bytes of `src/`, `tests/`,
`scripts/`, `pyproject.toml`, `uv.lock`, `mypy.ini` and `ruff.toml`, together with the result of the
one sweep that judged them. `check.py --write-receipt` is the only writer, it refuses to write
unless every check passed AND the working tree digests exactly like git's index, and the image build
runs `verify_quality_receipt.py` to refuse a tree whose digest has moved since. Editing source
without re-running the sweep therefore fails the BUILD instead of shipping unjudged code.

Never edit the receipt by hand. A CRLF tree writes a different digest, which is why the digest
normalizes line endings before hashing.

Differences from agri-data-service's copy, both deliberate:

- The digest covers no `alembic/` or `db/` directory, because this service is zero-Postgres
  (decision D5) and there is no migration machinery to ship.
- `check.py` carries no `--changed` / `--batch` test selection. That map is keyed on the sibling's
  lane directories; a selection map naming directories this service does not have would be a rule
  nobody could trust. The sweep here is always the whole suite.

## Parity fixtures

`regenerate_parity_fixtures.py` rewrites `tests/fixtures/parity/*.json` from agri-data-service's own
modules. It needs the sibling's source on disk, so it runs in a repository checkout and never in the
image. Run it only after deciding which side of a drift is correct; see `tests/AGENTS.md`.
