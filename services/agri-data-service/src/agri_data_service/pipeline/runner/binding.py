"""Production bindings for one turn: the bucket, the loader session, the writer, and the provider client.

Imports that open sockets or read settings are made inside the functions that need them, so the
package stays cheap to import (`tests/test_layer_import_contract.py` imports every module) and a
`--compare` turn never opens a database session. See `pipeline/runner/AGENTS.md` "Production binding".
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import (
    HTTP_FORBIDDEN,
    HTTP_SERVER_ERROR_MINIMUM,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_UNAUTHORIZED,
    UpstreamBounds,
    UpstreamHttpError,
    upstream_client,
)
from agri_data_service.pipeline.runner.contract import (
    ProviderConfigurationError,
    ProviderResponse,
    SourceThrottledError,
    SourceUnavailableError,
)
from agri_data_service.pipeline.runner.cooldown import ProviderCooldowns
from agri_data_service.pipeline.runner.exits import TurnConfigurationError
from agri_data_service.pipeline.runner.reader import ObjectStoreLaneReader
from agri_data_service.pipeline.runner.receipts import TurnReceipts
from agri_data_service.pipeline.runner.turn import TurnPorts
from agri_data_service.pipeline.runner.writer import ObjectStoreLaneWriter, WritePermit

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    import httpx

    from agri_data_service.foundation.lane_config.models import ProviderConfig
    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import IngestStrategy, TransformStrategy
    from agri_data_service.pipeline.runner.turn import TurnSpec

#: One logical attempt's ceiling on any provider endpoint: the Open-Meteo archive's own bound.
CONFIG_PROVIDER_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=300.0)


@dataclass(slots=True)
class ConfigProviderClient:
    """The runner's `ProviderClient`: one attempt per call through `ingest/provider_client.py`, never a logged key.

    `provider_endpoint_request` resolves the host and the key (FR-2: a customer host REQUIRES its key, an
    empty key is a named configuration error, never a silent fall back to the free host);
    `send_provider_request` makes the one bounded attempt. Status handling stays with the runner's retry
    ladder (`fetch.py`); `fetch_bounded`'s own transport retries are the only re-send below it.
    """

    provider: ProviderConfig
    http: httpx.AsyncClient
    clock: TurnClock
    bounds: UpstreamBounds = CONFIG_PROVIDER_BOUNDS

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse:  # noqa: ARG002 - the Protocol's probe flag; the runner meters it
        """Send one request and return its body, or raise the typed error the runner's ladder reads."""
        from agri_data_service.ingest.provider_client import (  # noqa: PLC0415 - lazy by design
            ProviderConfigError,
            provider_endpoint_request,
            send_provider_request,
        )

        try:
            request = provider_endpoint_request(self.provider, endpoint, parameters)
        except ProviderConfigError as error:
            raise ProviderConfigurationError(str(error)) from error
        response = await send_provider_request(self.http, request, self.bounds)
        if response.status == HTTP_TOO_MANY_REQUESTS:
            raise SourceThrottledError(
                f"upstream request failed with status {response.status}",
                retry_after_seconds=response.retry_after_seconds,
            )
        if response.status >= HTTP_SERVER_ERROR_MINIMUM:
            raise SourceUnavailableError(f"upstream request failed with status {response.status}")
        if response.status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
            raise ProviderConfigurationError(
                f"{self.provider.id} refused the request with status {response.status}; check its key"
            )
        if not response.ok:
            raise UpstreamHttpError(response.status)
        if response.payload_error is not None:
            raise response.payload_error
        return ProviderResponse(
            body=response.text.encode("utf-8"), request_url=request.request_url, retrieved_at=self.clock.now()
        )


@contextlib.asynccontextmanager
async def production_ports(
    spec: TurnSpec, strategy: IngestStrategy | TransformStrategy, *, clock: TurnClock
) -> AsyncIterator[TurnPorts]:
    """Bind the bucket, receipts, checkpoints, cooldowns, a metered HTTP client and (unless comparing) the writer."""
    from agri_data_service.config import settings  # noqa: PLC0415 - lazy by design
    from agri_data_service.db.engine import local_source_loader_session  # noqa: PLC0415 - lazy by design
    from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage  # noqa: PLC0415
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore  # noqa: PLC0415 - lazy by design
    from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints  # noqa: PLC0415

    try:
        store = ObjectStore.from_settings()
        availability = BotoAvailabilityStorage.from_settings()
        loader_url = None if spec.compare else settings.require_local_source_loader_database_url()
    except ValueError as error:
        raise TurnConfigurationError(f"the runner's storage settings are incomplete: {error}") from error
    receipts = TurnReceipts(availability)
    reader = ObjectStoreLaneReader(store=store, receipts=receipts)
    async with contextlib.AsyncExitStack() as stack:
        client = None
        checkpoint_store = None
        cooldowns = None
        if spec.lane.kind == "ingest" and spec.provider is not None:
            http = await stack.enter_async_context(upstream_client(CONFIG_PROVIDER_BOUNDS))
            client = ConfigProviderClient(provider=spec.provider, http=http, clock=clock)
            checkpoint_store = SourceResponseCheckpoints(availability)
            cooldowns = ProviderCooldowns(availability)
        writer = None
        if loader_url is not None:
            session = await stack.enter_async_context(local_source_loader_session(loader_url))
            writer = ObjectStoreLaneWriter(
                permit=WritePermit.for_turn(compare=spec.compare),
                session=session,
                store=store,
                availability_storage=availability,
                receipts=receipts,
                run_id=spec.run_id,
                clock=clock,
            )
        yield TurnPorts(
            strategy=strategy,
            reader=reader,
            clock=clock,
            writer=writer,
            client=client,
            checkpoint_store=checkpoint_store,
            cooldowns=cooldowns,
        )


__all__ = ["CONFIG_PROVIDER_BOUNDS", "ConfigProviderClient", "production_ports"]
