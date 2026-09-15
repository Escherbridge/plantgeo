"""Local storage preserves containment across Windows namespace transitions."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path, PureWindowsPath

import pytest

from agri_data_service.pipeline.direct.botanical_species_profiles.storage import LocalAvailabilityStorage
from agri_data_service.warehouse.botanical_species_profiles.contract import ProfileError


@pytest.mark.skipif(os.name != "nt", reason="characterizes the Windows native realpath implementation")
@pytest.mark.parametrize(
    ("root_text", "extended_root"),
    [
        ("C:\\qa-synthetic\\store", "\\\\?\\C:\\qa-synthetic\\store"),
        ("\\\\qa-synthetic\\share\\store", "\\\\?\\UNC\\qa-synthetic\\share\\store"),
    ],
)
def test_windows_storage_survives_a_disappearing_prefix_validation_probe(
    monkeypatch: pytest.MonkeyPatch, root_text: str, extended_root: str
) -> None:
    ordinary_leaf = str(PureWindowsPath(root_text) / "object")
    extended_leaf = str(PureWindowsPath(extended_root) / "object")
    leaf_calls: list[str] = []

    def native_lookup(path: str) -> str:
        if path in {root_text, extended_root}:
            return extended_root
        assert path in {ordinary_leaf, extended_leaf}
        leaf_calls.append(path)
        if len(leaf_calls) > 1:
            error = FileNotFoundError("synthetic disappearance during prefix validation")
            error.winerror = 2
            raise error
        return extended_leaf

    monkeypatch.setattr(ntpath, "_getfinalpathname", native_lookup)
    monkeypatch.setattr(Path, "stat", lambda _path: os.stat_result((0,) * 10))
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    # Simulated native transition, not a timing reproduction of concurrent CAS.
    legacy_result = ntpath.realpath(ordinary_leaf)
    assert legacy_result == extended_leaf
    assert not PureWindowsPath(legacy_result).is_relative_to(PureWindowsPath(root_text))
    leaf_calls.clear()

    storage = LocalAvailabilityStorage(Path(root_text))
    assert storage._path("object") == Path(extended_leaf)
    assert leaf_calls == [extended_leaf]


def test_local_storage_refuses_a_resolved_target_outside_its_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalAvailabilityStorage(tmp_path / "storage")
    candidate = storage.root / "object"
    outside = storage.root.parent / "outside" / "object"
    original_resolve = Path.resolve

    def redirected_resolve(path: Path, *, strict: bool = False) -> Path:
        return outside if path == candidate else original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)
    with pytest.raises(ProfileError, match="escapes its local root"):
        storage.read("object", max_bytes=1024)


@pytest.mark.parametrize("linked_component", ["nested", "nested/object"])
def test_local_storage_refuses_symbolic_links_even_when_resolved_within_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked_component: str
) -> None:
    storage = LocalAvailabilityStorage(tmp_path)
    linked = storage.root / linked_component
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked)
    with pytest.raises(ProfileError, match="does not follow symbolic links"):
        storage.read("nested/object", max_bytes=1024)
