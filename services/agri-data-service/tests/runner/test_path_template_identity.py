"""A templated endpoint through the runner's own wrappers: the turn budget, and the checkpoint identity.

Path values ride `SourceRequest.parameters`, so the budget's logical-request key, the replay client and
`credential_free_url` all see them with no new seam. The real `ConfigProviderClient` sends to an
`httpx.MockTransport` edge; the provider files are the shipped ones.
"""

from __future__ import annotations

import secrets
from datetime import date
from typing import Final

import httpx
import pytest

from agri_data_service.ingest.provider_client import provider_endpoint_request
from agri_data_service.ingest.usdm import usdm_source_url
from agri_data_service.pipeline.runner.binding import ConfigProviderClient
from agri_data_service.pipeline.runner.budget import BudgetedClient, TurnBudget
from agri_data_service.pipeline.runner.checkpoints import TurnCheckpoints
from agri_data_service.pipeline.runner.contract import SourceRequest, TurnBudgetExhaustedError
from tests.runner.fakes import ManualClock, providers

RELEASES: Final = (date(2026, 1, 6), date(2026, 1, 13), date(2026, 1, 20))


def _release(day: date) -> SourceRequest:
    return SourceRequest(
        unit=f"release:{day.isoformat()}",
        endpoint="release",
        days=(day,),
        parameters=(("release", day.strftime("%Y%m%d")),),
    )


async def test_each_release_is_its_own_logical_request_and_a_resend_is_only_an_attempt() -> None:
    usdm = providers()["usdm"]
    sent: list[str] = []

    def edge(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, text="{}")

    budget = TurnBudget(cap=2, basis="requests", provider=usdm)
    first, second, third = (_release(day) for day in RELEASES)
    async with httpx.AsyncClient(transport=httpx.MockTransport(edge)) as http:
        client = BudgetedClient(ConfigProviderClient(provider=usdm, http=http, clock=ManualClock()), budget)
        for request in (first, second, first):
            await client.get(request.endpoint, request.query())
        with pytest.raises(TurnBudgetExhaustedError):
            await client.get(third.endpoint, third.query())

    assert sent == [usdm_source_url(day.isoformat()) for day in (RELEASES[0], RELEASES[1], RELEASES[0])]
    assert (budget.ledger.requests, budget.ledger.fetch_attempts) == (2, 3)


def test_each_release_checkpoints_under_its_own_exact_upstream_url() -> None:
    checkpoints = TurnCheckpoints(store=None, lane_id="drought-direct-forward", provider=providers()["usdm"])

    identities = [checkpoints.identity(_release(day)).request_url for day in RELEASES]

    assert identities == [usdm_source_url(day.isoformat()) for day in RELEASES]


def test_a_firms_checkpoint_identity_is_the_reported_url_with_the_key_marker() -> None:
    """The identity and the response's `request_url` are built by one rule, so a replay matches its send."""
    firms = providers()["nasa-firms"]
    request = SourceRequest(
        unit="viirs:2026-10-01",
        endpoint="area-csv",
        days=(date(2026, 10, 1),),
        parameters=(("source", "VIIRS_SNPP_NRT"), ("area", "-125,41,-116,49"), ("day_range", "1")),
    )
    key = f"FAKE-firms-{secrets.token_hex(12)}"
    reported = provider_endpoint_request(firms, request.endpoint, request.query(), environment={"NASA_FIRMS_KEY": key})

    identity = TurnCheckpoints(store=None, lane_id="fire-detections-forward", provider=firms).identity(request)

    assert (
        identity.request_url
        == reported.request_url
        == ("https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/VIIRS_SNPP_NRT/-125,41,-116,49/1")
    )
    assert key in reported.send_url.reveal()
    assert key not in repr(identity)
