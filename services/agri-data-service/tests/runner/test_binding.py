"""The production binding (`pipeline/runner/binding.py`): the paid-key provider client and the port binder.

`httpx.MockTransport` is the provider edge and the provider file is the real `lanes/_providers/open-meteo.toml`.
The command flows run `main` over a built `lanes/` tree with the real `ConfigProviderClient`; only the bucket
is the memory store. The key must reach the send and nothing else: not the report, stderr, `request_url`, or
a checkpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import httpx
import pytest

from agri_data_service.config import Settings
from agri_data_service.foundation.lane_config.loader import LANES_DIRECTORY_ENV_VAR
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from agri_data_service.pipeline.runner import resolve
from agri_data_service.pipeline.runner.__main__ import main
from agri_data_service.pipeline.runner.binding import ConfigProviderClient, production_ports
from agri_data_service.pipeline.runner.contract import (
    ProviderConfigurationError,
    SourceThrottledError,
    SourceUnavailableError,
)
from agri_data_service.pipeline.runner.exits import EXIT_COMPLETED, EXIT_CONFIGURATION_ERROR, TurnConfigurationError
from tests.lane_config.builders import settled_soil_lane, write_lane_tree
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import (
    FIXTURE_STRATEGY_PACKAGE,
    TODAY,
    ManualClock,
    MemoryLaneStore,
    grid_lane,
    ports_for,
    providers,
    spec_for,
)
from tests.runner.fixtures.grid_refuse import GRID_STREAM, STRATEGY, GridWorld

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from agri_data_service.pipeline.runner.__main__ import PortBinder
    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import IngestStrategy, TransformStrategy
    from agri_data_service.pipeline.runner.turn import TurnPorts, TurnSpec

KEY_VARIABLE: Final = "OPEN_METEO_API_KEY"
KEY: Final = "om-fixture-secret-7Q2x"
LANE: Final = "fixture-grid-settled"
FREE_HOST: Final = "archive-api.open-meteo.com"
CUSTOMER_HOST: Final = "customer-archive-api.open-meteo.com"
QUERY: Final = {"latitude": "45.0", "longitude": "-120.0", "daily": "value_mean"}


def _responding(status: int, *, body: bytes = b"{}", headers: dict[str, str] | None = None):  # noqa: ANN202 - a handler
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, content=body, headers={"content-type": "application/json", **(headers or {})})

    return handler, sent


async def _get(handler: Callable[[httpx.Request], httpx.Response]) -> object:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ConfigProviderClient(provider=providers()["open-meteo"], http=http, clock=ManualClock())
        return await client.get("archive", QUERY)


# --- ConfigProviderClient -----------------------------------------------------------------------


async def test_a_keyed_endpoint_sends_the_key_to_the_customer_host_and_reports_the_free_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-2: the customer host carries `apikey`; the response's `request_url` is the credential-free free-host URL."""
    monkeypatch.setenv(KEY_VARIABLE, KEY)
    handler, sent = _responding(200, body=b'{"ok": true}')

    response = await _get(handler)

    assert [(request.url.host, request.url.params.get("apikey")) for request in sent] == [(CUSTOMER_HOST, KEY)]
    assert response.body == b'{"ok": true}'  # type: ignore[attr-defined]
    assert httpx.URL(response.request_url).host == FREE_HOST  # type: ignore[attr-defined]
    assert KEY not in response.request_url  # type: ignore[attr-defined]


async def test_an_empty_required_key_is_a_configuration_error_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-2: never a silent fall-back to the free host."""
    monkeypatch.setenv(KEY_VARIABLE, "  ")
    handler, sent = _responding(200)

    with pytest.raises(ProviderConfigurationError, match=KEY_VARIABLE):
        await _get(handler)

    assert sent == []


@pytest.mark.parametrize(
    ("status", "headers", "raised"),
    [
        (429, {"retry-after": "30"}, SourceThrottledError),
        (503, {}, SourceUnavailableError),
        (401, {}, ProviderConfigurationError),
        (403, {}, ProviderConfigurationError),
    ],
    ids=["throttled", "server", "unauthorized", "forbidden"],
)
async def test_a_status_becomes_the_typed_error_the_runner_s_ladder_reads(
    monkeypatch: pytest.MonkeyPatch, status: int, headers: dict[str, str], raised: type[Exception]
) -> None:
    """ONE attempt per call (the ladder in `fetch.py` owns retries); no error text carries the key."""
    monkeypatch.setenv(KEY_VARIABLE, KEY)
    handler, sent = _responding(status, headers=headers)

    with pytest.raises(raised) as error:
        await _get(handler)

    assert len(sent) == 1
    assert KEY not in str(error.value)
    if raised is SourceThrottledError:
        assert error.value.retry_after_seconds == 30  # type: ignore[attr-defined]  # noqa: PLR2004


# --- production_ports ---------------------------------------------------------------------------


def _unbound_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bucket settings resolve to memory stand-ins; any loader DSN request fails loudly."""

    def no_loader(_settings: object) -> str:
        raise ValueError("LOCAL_SOURCE_LOADER_DATABASE_URL is not set")

    monkeypatch.setattr(ObjectStore, "from_settings", classmethod(lambda _cls: object()))
    monkeypatch.setattr(BotoAvailabilityStorage, "from_settings", classmethod(lambda _cls: MemoryAvailabilityStorage()))
    monkeypatch.setattr(Settings, "require_local_source_loader_database_url", no_loader)


async def test_a_compare_turn_binds_no_writer_and_needs_no_loader_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    _unbound_storage(monkeypatch)

    async with production_ports(spec_for(grid_lane(), compare=True), STRATEGY, clock=ManualClock()) as ports:
        assert ports.writer is None
        assert isinstance(ports.client, ConfigProviderClient)
        assert ports.checkpoint_store is not None


async def test_a_writing_turn_without_a_loader_dsn_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _unbound_storage(monkeypatch)

    with pytest.raises(TurnConfigurationError, match="LOCAL_SOURCE_LOADER_DATABASE_URL"):
        async with production_ports(spec_for(grid_lane()), STRATEGY, clock=ManualClock()):
            pass  # pragma: no cover - the binding refuses before yielding


# --- the command over the real client -----------------------------------------------------------


@pytest.fixture
def lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One config grid lane on the Open-Meteo archive, its fixture strategy resolvable by `main`."""
    lane = settled_soil_lane(
        LANE,
        executor="config",
        strategy="fixtures.grid_refuse",
        streams=[{"slug": GRID_STREAM, "floor_basis": "binding fixture"}],
    )
    directory = write_lane_tree(tmp_path, [lane])
    monkeypatch.setenv(LANES_DIRECTORY_ENV_VAR, str(directory))
    monkeypatch.delenv("PLANTGEO_REGION", raising=False)
    monkeypatch.setattr(
        resolve, "resolve_strategy", functools.partial(resolve.resolve_strategy, package=FIXTURE_STRATEGY_PACKAGE)
    )
    return directory


def _world_handler(world: GridWorld) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        parameters = {name: value for name, value in request.url.params.items() if name != "apikey"}
        body = world.answer("archive", parameters, probe=False)
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return handler, sent


def _real_client_binder(
    store: MemoryLaneStore, handler: Callable[[httpx.Request], httpx.Response], checkpoints: SourceResponseCheckpoints
) -> PortBinder:
    @contextlib.asynccontextmanager
    async def bind(
        spec: TurnSpec, strategy: IngestStrategy | TransformStrategy, *, clock: TurnClock
    ) -> AsyncIterator[TurnPorts]:
        del clock
        assert spec.provider is not None
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            ports = ports_for(strategy, store, checkpoint_store=checkpoints)
            ports.client = ConfigProviderClient(provider=spec.provider, http=http, clock=ManualClock())
            yield ports

    return bind


def _stored_text(storage: MemoryAvailabilityStorage) -> str:
    return "\n".join(f"{key} {stored.payload.decode('utf-8', 'replace')}" for key, stored in storage.objects.items())


@pytest.mark.usefixtures("lanes")
async def test_a_keyed_turn_writes_and_the_key_reaches_only_the_customer_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole turn over the real client: every send is keyed, and stdout, stderr and checkpoints never are."""
    monkeypatch.setenv(KEY_VARIABLE, KEY)
    world = GridWorld()
    world.publish([TODAY - timedelta(days=offset) for offset in range(5, 19)])
    handler, sent = _world_handler(world)
    store, storage = MemoryLaneStore(), MemoryAvailabilityStorage()

    exit_code = await _run_main(store, handler, SourceResponseCheckpoints(storage))

    captured = capsys.readouterr()
    report = json.loads(captured.out.strip().splitlines()[-1])
    assert exit_code == EXIT_COMPLETED
    assert report["days_written"] > 0
    assert sent
    assert all(request.url.host == CUSTOMER_HOST and request.url.params["apikey"] == KEY for request in sent)
    assert storage.objects, "the fully valued answers were checkpointed"
    assert KEY not in captured.out
    assert KEY not in captured.err
    assert KEY not in _stored_text(storage)


@pytest.mark.usefixtures("lanes")
@pytest.mark.parametrize(("key", "status"), [("", 200), (KEY, 401)], ids=["empty-key", "rejected-key"])
async def test_a_missing_or_rejected_key_exits_78_and_never_prints_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], key: str, status: int
) -> None:
    """FR-2 through `main`: an empty key sends nothing; a 401 ends the turn at its first send."""
    monkeypatch.setenv(KEY_VARIABLE, key)
    handler, sent = _responding(status)
    store = MemoryLaneStore()

    exit_code = await _run_main(store, handler, SourceResponseCheckpoints(MemoryAvailabilityStorage()))

    captured = capsys.readouterr()
    report = json.loads(captured.out.strip().splitlines()[-1])
    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert report["outcome"] == "config_error"
    assert len(sent) == (0 if not key else 1)
    assert store.writes() == []
    assert KEY not in captured.out
    assert KEY not in captured.err


async def _run_main(
    store: MemoryLaneStore, handler: Callable[[httpx.Request], httpx.Response], checkpoints: SourceResponseCheckpoints
) -> int:
    """`main` runs its own event loop, so the async test hands it a worker thread."""
    binder = _real_client_binder(store, handler, checkpoints)
    return await asyncio.to_thread(main, ["--lane", LANE, "--mode", "forward"], bind=binder)
