"""A provider file's `path_template` is refused at load, so a malformed template never becomes a sent URL.

Each case edits one shipped provider file inside a built `lanes/` tree and loads it as the executor
does: the broken provider fails to load (and takes only its own lanes with it), the rest stay healthy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from tests.lane_config.builders import settled_soil_lane, write_lane_tree

if TYPE_CHECKING:
    from pathlib import Path

USDM_TEMPLATE: Final = 'path_template = "/data/json/usdm_{release}.json"'
FIRMS_OPT_IN: Final = 'customer_host = "firms.modaps.eosdis.nasa.gov"\n'


def _usdm(template: str, reason_fragment: str, case: str) -> object:
    """One case that swaps the shipped usdm `release` template for `template`."""
    return pytest.param("usdm", USDM_TEMPLATE, f'path_template = "{template}"', reason_fragment, id=case)


def _firms(edited: str, reason_fragment: str, case: str) -> object:
    """One case that swaps nasa-firms' first `customer_host` opt-in line for `edited`."""
    return pytest.param("nasa-firms", FIRMS_OPT_IN, edited, reason_fragment, id=case)


@pytest.mark.parametrize(
    ("provider_id", "shipped", "edited", "reason_fragment"),
    [
        _usdm("/data/json/usdm_{release.json", "unclosed or stray brace", "unclosed-brace"),
        _usdm("/data/json/usdm_release}.json", "unclosed or stray brace", "stray-brace"),
        _usdm("/data/json/{Release}.json", "unclosed or stray brace", "placeholder-not-an-identifier"),
        _usdm("/data/json/../{release}.json", "dot segment", "dot-segment"),
        _usdm("/data/json/usdm.json", "names no placeholder", "no-placeholder"),
        _usdm("/data/json/{release}/{release}", "twice", "duplicate-placeholder"),
        _usdm("/elsewhere/{release}.json", "must extend", "outside-the-fixed-path"),
        _usdm("/data/json/{api_key}/{release}.json", "needs api_key_transport", "key-segment-on-a-keyless-provider"),
        _firms("", "fills only under a customer_host", "key-segment-without-opt-in"),
        _firms("optional_api_key = true\n", "optional key cannot fill", "optional-key-cannot-fill-a-required-segment"),
    ],
)
def test_a_malformed_path_template_fails_its_provider_file_at_load(
    tmp_path: Path, provider_id: str, shipped: str, edited: str, reason_fragment: str
) -> None:
    directory = write_lane_tree(tmp_path, [settled_soil_lane()])
    provider_file = directory / "_providers" / f"{provider_id}.toml"
    text = provider_file.read_text(encoding="utf-8")
    assert shipped in text, "the shipped provider file changed shape; update this fixture"
    provider_file.write_text(text.replace(shipped, edited, 1), encoding="utf-8")

    config = load_lane_configs(directory, load_region("pnw"))

    assert set(config.provider_failures) == {provider_id}
    assert reason_fragment in " | ".join(config.provider_failures[provider_id].reasons)
    assert provider_id not in config.providers
    assert set(config.lanes) == {settled_soil_lane()["id"]}
