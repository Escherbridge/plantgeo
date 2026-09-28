"""Source checkpoints for config lanes: a fully valued unit answer is kept and replayed through the strategy's `fetch`.

Built on `pipeline/parquet/source_checkpoint.py` (bounded, checksum-verified, 7-day). A replay
keeps the ORIGINAL retrieval instant, and only an answer whose every requested parameter carried
values is retained (per-parameter eligibility). See `pipeline/runner/AGENTS.md` "Checkpoints".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.pipeline.parquet.source_checkpoint import SourceCheckpoint, SourceCheckpointIdentity
from agri_data_service.pipeline.runner.contract import ProviderResponse, SourceFetchError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from agri_data_service.foundation.lane_config.models import ProviderConfig
    from agri_data_service.pipeline.runner.contract import IngestStrategy, SourceRequest, SourceResponse

#: Versions the runner's checkpoint namespace apart from the legacy writers' identities.
RUNNER_CHECKPOINT_PROVIDER_PREFIX: Final = "lane-runner-v1"


class CheckpointStore(Protocol):
    """`SourceResponseCheckpoints` satisfies this; both calls fail open (a bad object reads as `None`)."""

    def read(self, identity: SourceCheckpointIdentity, *, now: datetime) -> SourceCheckpoint | None: ...

    def write(
        self, identity: SourceCheckpointIdentity, checkpoint: SourceCheckpoint, *, response_sha256: str
    ) -> None: ...


class _CheckpointNotReplayableError(SourceFetchError):
    """The replay client was asked for something other than the one held answer."""


@dataclass(frozen=True, slots=True)
class _ReplayClient:
    """A provider client that answers exactly one request, from a held checkpoint, and sends nothing."""

    held: ProviderResponse
    endpoint: str
    parameters: Mapping[str, str]

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse:
        """Return the held answer for the one request it holds."""
        if probe or endpoint != self.endpoint or dict(parameters) != dict(self.parameters):
            raise _CheckpointNotReplayableError("a checkpoint replays only the request it was taken for")
        return self.held


@dataclass(slots=True)
class TurnCheckpoints:
    """This turn's checkpoint reads and writes, counted for the report."""

    store: CheckpointStore | None
    lane_id: str
    provider: ProviderConfig
    #: Compare mode reads checkpoints but never writes one.
    writable: bool = True
    restores: int = 0
    retained: int = 0

    def identity(self, request: SourceRequest) -> SourceCheckpointIdentity:
        """Bind a checkpoint to the lane, provider, support, the unit's days and its credential-free URL."""
        return SourceCheckpointIdentity(
            provider=f"{RUNNER_CHECKPOINT_PROVIDER_PREFIX}:{self.lane_id}:{self.provider.id}",
            support_sha256=request.support_digest or sha256_digest(self.lane_id),
            day=",".join(day.isoformat() for day in request.days),
            request_url=request.credential_free_url(self.provider),
        )

    async def restore(
        self, request: SourceRequest, strategy: IngestStrategy, *, now: datetime
    ) -> SourceResponse | None:
        """Replay a held answer through `strategy.fetch`, or `None` when nothing eligible is held."""
        if self.store is None:
            return None
        identity = self.identity(request)
        held = await asyncio.to_thread(self.store.read, identity, now=now)
        if held is None:
            return None
        replay = _ReplayClient(
            held=ProviderResponse(body=held.body, request_url=identity.request_url, retrieved_at=held.retrieved_at),
            endpoint=request.endpoint,
            parameters=request.query(),
        )
        try:
            response = await strategy.fetch(request, replay)
        except Exception:  # a held answer the parser now refuses is a miss, never a failure
            return None
        if not response.checkpoint_eligible:
            return None
        self.restores += 1
        return replace(response, restored=True, retrieved_at=held.retrieved_at)

    async def retain(self, response: SourceResponse) -> None:
        """Keep a fully valued network answer for later turns; never a replayed or partly null one."""
        if self.store is None or not self.writable or response.restored or not response.checkpoint_eligible:
            return
        identity = self.identity(response.request)
        await asyncio.to_thread(
            self.store.write,
            identity,
            SourceCheckpoint(body=response.body, retrieved_at=response.retrieved_at),
            response_sha256=sha256_digest(response.body),
        )
        self.retained += 1


__all__ = ["RUNNER_CHECKPOINT_PROVIDER_PREFIX", "CheckpointStore", "TurnCheckpoints"]
