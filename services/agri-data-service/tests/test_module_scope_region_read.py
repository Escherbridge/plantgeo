"""Guard: no `load_region()` call may run while a module under `src/agri_data_service` imports.

`conductor/code_styleguides/federation.md` §1 -- "the manifest is read at ingress once and passed
down ... they do not import a module-level constant that hides the dependency" -- plus the standing
manifest-moves-must-be-lazy rule. A module-scope read snapshots whichever region the process
started with, so a later `PLANTGEO_REGION` selection or a test that swaps the manifest is silently
ignored, and the dependency stops being visible at the call site.

W2 B1 removed the last one from this tree and nothing had been watching since. Its TypeScript twin
is `src/__tests__/region/no-module-scope-region-read.test.ts`, which asks the same question of
`getRegion()`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_SERVICE_ROOT: Final = Path(__file__).resolve().parents[1]
_SRC_ROOT: Final = _SERVICE_ROOT / "src" / "agri_data_service"

#: The manifest reader every module must call from inside a function, never at import.
_REGION_READER: Final = "load_region"


def _module_scope_region_reads(tree: ast.Module) -> list[int]:
    """Line numbers of `load_region(...)` calls whose nearest enclosing scope is the module body.

    A function or lambda body defers the call to whoever calls it, which is precisely the shape the
    rule asks for; a class body does NOT, because it runs at import like the module body does.
    """
    offending_lines: list[int] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == _REGION_READER:
                offending_lines.append(child.lineno)
            walk(child)

    walk(tree)
    return offending_lines


def test_no_module_scope_region_read() -> None:
    """Fail naming every import-time manifest read, so the next one cannot land quietly."""
    offenders: list[str] = []
    for module_path in sorted(_SRC_ROOT.rglob("*.py")):
        source = module_path.read_text(encoding="utf-8")
        if _REGION_READER not in source:
            continue
        offenders.extend(
            f"{module_path.relative_to(_SERVICE_ROOT).as_posix()}:{line}"
            for line in _module_scope_region_reads(ast.parse(source))
        )
    assert offenders == [], (
        "a module-scope load_region() snapshots the region the process started with and hides the "
        "dependency from the call site; move the read inside the function that needs it, or take a "
        "Region parameter (federation.md §1). Offenders: " + ", ".join(offenders)
    )


def test_guard_recognises_the_shape_it_watches_for() -> None:
    """The walk itself is proved, so a silently-broken guard cannot read as a clean tree."""
    module_scope = ast.parse("REGION = load_region()\n")
    inside_function = ast.parse("def region():\n    return load_region()\n")
    inside_class_body = ast.parse("class Holder:\n    region = load_region()\n")
    assert _module_scope_region_reads(module_scope) == [1]
    assert _module_scope_region_reads(inside_function) == []
    assert _module_scope_region_reads(inside_class_body) == [2]
