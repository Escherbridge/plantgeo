# foundation (L0)

Canonical JSON, digests, finiteness guards and credential-custody helpers. Stdlib only.

Import rules: may import nothing first-party except `plantgeo_ml_service.foundation` itself, and no
third-party package at all. The AST contract in `tests/test_layer_import_contract.py` enforces it.

`canonical.py` and `contracts.py` are deliberate COPIES of agri-data-service helpers, not imports:
the two services deploy independently (spec FR-3, section 4). `tests/test_canonical_parity.py` loads
the sibling's module by file path and asserts identical output, so a drift fails this service's sweep
rather than corrupting a checksum silently.
