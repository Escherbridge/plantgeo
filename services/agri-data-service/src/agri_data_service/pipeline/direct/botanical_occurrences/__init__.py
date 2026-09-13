"""The governed botanical occurrence lane: quarantine, normalize, support, publish.

Deliberately empty of re-exports. `tests/test_layer_import_contract.py` treats this directory as one
lane and `pipeline/direct/__init__.py` is the only shared module a lane may reach, so importing
siblings here would only make the import graph wider without making anything easier to find. Each
module says what it does at the top of itself; `AGENTS.md` beside this file says why.
"""

from __future__ import annotations
