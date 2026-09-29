"""Provider files against the meter and the owner's budget decision (spec §4.9.2, WQ-4; §6.3, O10).

Every host a provider file declares must meter to a provider and a pool label, or the usage report
shows `provider None` for it; the paid quota must be charged only through keyed customer hosts; and
the provider's weight rule must price a request exactly as the meter does.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.execution import usage_report
from agri_data_service.foundation.lane_config import ProviderConfig, load_lane_configs
from agri_data_service.foundation.observability import usage
from agri_data_service.foundation.observability.vocabulary import POOL_LABELS
from agri_data_service.foundation.region import load_region
from tests.lane_config.builders import REAL_LANES_DIRECTORY, nasa_power_lane, write_lane_tree

if TYPE_CHECKING:
    from pathlib import Path

#: WQ-4: the paid monthly Open-Meteo cap and its two lines, as the owner settled them.
PAID_MONTHLY_WEIGHTED_CALLS: Final = 5_000_000
GAP_FILL_ADMISSION_LINE: Final = 3_000_000
FORWARD_STOP_LINE: Final = 4_750_000


@pytest.fixture(scope="module")
def providers() -> dict[str, ProviderConfig]:
    """The real provider files, loaded as the executor loads them."""
    return dict(load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).providers)


def test_every_host_in_every_provider_file_meters_to_a_provider_and_pool(
    providers: dict[str, ProviderConfig],
) -> None:
    """A provider file host the meter cannot resolve would report `provider None` in the usage report."""
    known_pools = POOL_LABELS | usage.METERING_ONLY_POOL_LABELS
    unresolved = [
        (provider.id, host)
        for provider in providers.values()
        for host in provider.hosts()
        if (resolution := usage.provider_for_host(host)) is None or resolution.pool not in known_pools
    ]

    assert providers, "no provider files loaded"
    assert unresolved == []


def test_the_paid_quota_is_charged_only_through_keyed_customer_hosts(providers: dict[str, ProviderConfig]) -> None:
    """WQ-4: spend on a customer host lands in the charged pool; a free host's never does."""
    budgeted = [provider for provider in providers.values() if provider.budget is not None]
    assert [provider.id for provider in budgeted] == ["open-meteo"]

    for provider in budgeted:
        assert provider.budget is not None
        customer_hosts = set(provider.customer_hosts())
        for host in provider.hosts():
            resolution = usage.provider_for_host(host)
            assert resolution is not None
            assert resolution.provider == provider.id
            assert (resolution.pool == provider.budget.charged_pool) is (host in customer_hosts), host


def test_open_meteo_declares_only_the_paid_monthly_cap_over_keyed_archive_forecast_and_history(
    providers: dict[str, ProviderConfig],
) -> None:
    """WQ-4 and plan 1A: 5,000,000 a month, gap-fill admitted under 60 %, forward stopped at 95 %."""
    open_meteo = providers["open-meteo"]
    budget = open_meteo.budget
    assert budget is not None

    assert (budget.period, budget.weighted_calls) == ("month", PAID_MONTHLY_WEIGHTED_CALLS)
    assert budget.gap_fill_ceiling_calls == GAP_FILL_ADMISSION_LINE
    assert budget.forward_stop_calls == FORWARD_STOP_LINE
    assert open_meteo.api_key_env == "OPEN_METEO_API_KEY"
    for endpoint in ("archive", "forecast", "historical-forecast"):
        assert open_meteo.endpoints[endpoint].customer_host is not None, endpoint


def test_the_toml_budget_agrees_with_the_hand_copied_usage_report_constants(
    providers: dict[str, ProviderConfig],
) -> None:
    """WQ-4 is stated twice (the TOML and `execution/usage_report.py`'s report constants, by design --
    that module's own comment says its `WEIGHTED_POOLS` is a deliberate hand-copy, not a shared
    import). Nothing re-checks the two agree, so a TOML-only cap change would silently stale the
    report's budget lines (review finding 6, f1-config sweep)."""
    budget = providers["open-meteo"].budget
    assert budget is not None

    assert budget.weighted_calls == usage_report.PAID_MONTHLY_BUDGET
    assert budget.ceiling_fraction == usage_report.GAP_FILL_CEILING_FRACTION
    assert budget.stop_fraction == usage_report.FORWARD_STOP_FRACTION


@pytest.mark.parametrize(
    ("locations", "days", "variables"),
    [
        pytest.param(48, 14, 8, id="one-soil-chunk"),
        pytest.param(1, 1, 1, id="one-probe-location"),
        pytest.param(2, 14, 1, id="the-g0-probe"),
        pytest.param(112, 90, 24, id="a-revision-sweep"),
    ],
)
def test_the_provider_weight_rule_prices_a_request_like_the_meter(
    providers: dict[str, ProviderConfig], locations: int, days: int, variables: int
) -> None:
    """A lane budgets with the provider file's rule; the meter charges with `usage`'s. They must agree."""
    open_meteo = providers["open-meteo"]
    rule = open_meteo.weight
    assert rule is not None
    archive = open_meteo.endpoints["archive"]
    start = date(2026, 1, 1)
    end = start + timedelta(days=days - 1)
    latitudes = ",".join(["45.125"] * locations)
    longitudes = ",".join(["-120.125"] * locations)
    daily = ",".join(f"variable_{index}" for index in range(variables))
    url = (
        f"https://{archive.customer_host}{archive.path}?latitude={latitudes}&longitude={longitudes}"
        f"&start_date={start.isoformat()}&end_date={end.isoformat()}&daily={daily}"
    )

    budgeted = rule.weight(locations=locations, days=days, variables=variables)

    assert budgeted == usage.open_meteo_request_weight(locations=locations, days=days, variables=variables)
    assert budgeted == usage.open_meteo_weight_for_url(url)
    # `open_meteo_request_weight` itself takes no `models` parameter (pinned to G0's soil request
    # builder, which never sends one); `open_meteo_weight_for_url` instead multiplies by the URL's
    # own `models=` item count, so a real customer-host URL carrying `models=a,b` now prices exactly
    # like the rule's own `models=2` (review finding 3, RESOLVED: `lanes/AGENTS.md` "Provider files"
    # no longer carries the "not yet exercised end to end" caveat).
    doubled_models_url = f"{url}&models=era5_land,era5"
    assert rule.weight(locations=locations, days=days, variables=variables, models=2) == 2 * budgeted
    assert usage.open_meteo_weight_for_url(doubled_models_url) == 2 * budgeted


def test_nasa_power_is_keyless_uncharged_and_asks_for_utc_days(providers: dict[str, ProviderConfig]) -> None:
    """POWER daily values must be UTC days; an LST day shifts every value and reads as a regression."""
    nasa_power = providers["nasa-power"]

    assert nasa_power.time_standard == "UTC"
    assert (nasa_power.weighted, nasa_power.api_key_env, nasa_power.budget) == (False, None, None)


@pytest.mark.parametrize(
    ("replace", "replacement", "reason_fragment"),
    [
        pytest.param('time_standard = "UTC"', 'time_standard = "LST"', "time_standard", id="local-solar-time"),
        pytest.param("weighted = false", 'weighted = false\napi_key_env = "abc123"', "api_key_env", id="key-value"),
        pytest.param(
            'path = "/api/temporal/daily/point"',
            'path = "/api/temporal/daily/point"\ncustomer_host = "customer-power.larc.nasa.gov"',
            "needs api_key_env",
            id="customer-host-without-key",
        ),
    ],
)
def test_a_provider_file_breaking_its_contract_fails_to_load_and_quarantines_its_lanes(
    tmp_path: Path, replace: str, replacement: str, reason_fragment: str
) -> None:
    """Table-driven over the provider rules a real edit could break; the failure is scoped to that provider."""
    directory = write_lane_tree(tmp_path, [nasa_power_lane()])
    provider_file = directory / "_providers" / "nasa-power.toml"
    provider_file.write_text(provider_file.read_text(encoding="utf-8").replace(replace, replacement), encoding="utf-8")

    config = load_lane_configs(directory, load_region("pnw"))

    assert set(config.providers) == {"open-meteo", "usgs-water-data"}
    assert reason_fragment in " | ".join(config.provider_failures["nasa-power"].reasons)
    assert set(config.quarantined) == {"climate-nasa-power-direct-forward"}
