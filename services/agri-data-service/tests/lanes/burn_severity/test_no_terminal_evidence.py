"""DO NOT DELETE: no module in `pipeline/lanes/burn_severity/` ever constructs `TerminalEvidence`.

Same guard as `tests/direct/test_evacuation_zones_direct_adapter.py::
test_no_module_in_this_package_ever_constructs_terminal_evidence`, scoped to this lanes package only: the
legacy `pipeline/direct/burn_severity/publish_snapshot.py` builds terminal evidence legitimately (it is the
current-snapshot publisher), so that package stays unguarded.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agri_data_service.pipeline.lanes import burn_severity as lanes_burn_severity


def test_no_module_in_this_package_ever_constructs_terminal_evidence() -> None:
    """Receipts and digests come from the runner's writer; this package has no reason to name the type at all."""
    banned = {"TerminalEvidence", "provenance"}
    modules = sorted(Path(lanes_burn_severity.__file__).parent.glob("*.py"))
    assert modules
    for module in modules:
        # Parsed rather than grepped: docstrings may describe the trap in prose.
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        keywords = {node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)}
        assert not (banned & (names | attributes | keywords)), module.name
