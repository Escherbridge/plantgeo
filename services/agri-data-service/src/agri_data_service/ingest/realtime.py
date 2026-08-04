"""Best-effort Redis publish for map invalidation, spoken as RESP over one asyncio connection per run."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote, urlsplit

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

logger = structlog.get_logger()

REDIS_URL_VARIABLE: Final = "REDIS_URL"
DEFAULT_REDIS_URL: Final = "redis://localhost:6379"
DEFAULT_REDIS_PORT: Final = 6379
REDIS_COMMAND_TIMEOUT_SECONDS: Final = 5.0

REALTIME_CHANNEL_PATTERN: Final = re.compile(r"^layer:[a-z0-9-]{1,100}$")

_ERROR_REPLY_PREFIX: Final = b"-"


class RealtimeProtocolError(OSError):
    """Raised when Redis answered a command with an error reply or closed the connection mid-command."""


@dataclass(frozen=True, slots=True)
class RedisEndpoint:
    """The connection facts a PUBLISH needs, split out of a `redis://` or `rediss://` URL."""

    host: str
    port: int
    username: str | None
    password: str | None
    database_index: int
    use_tls: bool


def resolve_redis_url() -> str:
    """Read REDIS_URL at call time so a cron environment change needs no restart."""
    return os.environ.get(REDIS_URL_VARIABLE, "").strip() or DEFAULT_REDIS_URL


def parse_redis_url(url: str) -> RedisEndpoint:
    """Split a `redis://` or `rediss://` URL into host, port, credentials, database index and TLS choice."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError("REDIS_URL must use the redis or rediss scheme")
    if not parsed.hostname:
        raise ValueError("REDIS_URL must name a host")
    database_path = parsed.path.removeprefix("/")
    if database_path and not database_path.isdigit():
        raise ValueError("REDIS_URL path must be a numeric database index")
    return RedisEndpoint(
        host=parsed.hostname,
        port=parsed.port or DEFAULT_REDIS_PORT,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        database_index=int(database_path) if database_path else 0,
        use_tls=parsed.scheme == "rediss",
    )


def encode_redis_command(*arguments: str) -> bytes:
    """Encode one Redis command as a RESP array of bulk strings."""
    parts: list[bytes] = [f"*{len(arguments)}\r\n".encode()]
    for argument in arguments:
        encoded = argument.encode("utf-8")
        parts.append(f"${len(encoded)}\r\n".encode())
        parts.append(encoded)
        parts.append(b"\r\n")
    return b"".join(parts)


class RealtimePublisher:
    """Publishes one map-invalidation message per written row; a Redis failure never rolls back a durable write."""

    def __init__(self, url: str | None = None) -> None:
        """Validate the target URL up front and open nothing; the first publish connects."""
        self._endpoint = parse_redis_url(url if url is not None else resolve_redis_url())
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._unavailable = False
        self.delivered = 0
        self.dropped = 0

    async def __aenter__(self) -> RealtimePublisher:
        """Enter the publisher's lifetime; the connection is still opened lazily."""
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the publisher connection when the ingestion run ends."""
        del exception_type, exception, traceback
        await self.aclose()

    async def publish(self, channel: str, message: Mapping[str, object]) -> None:
        """Publish one message, degrading to a counted drop once Redis has proved unreachable."""
        if REALTIME_CHANNEL_PATTERN.match(channel) is None:
            raise ValueError(f"realtime channel must match {REALTIME_CHANNEL_PATTERN.pattern}")
        payload = json.dumps(message, allow_nan=False)
        if self._unavailable:
            self.dropped += 1
            return
        try:
            await self._send(encode_redis_command("PUBLISH", channel, payload))
        except OSError as error:
            self._unavailable = True
            self.dropped += 1
            logger.warning("realtime_publish_unavailable", channel=channel, error=str(error))
            await self.aclose()
            return
        self.delivered += 1

    async def aclose(self) -> None:
        """Close the connection, tolerating a socket the peer has already dropped."""
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        writer.close()
        with suppress(OSError):
            await asyncio.wait_for(writer.wait_closed(), REDIS_COMMAND_TIMEOUT_SECONDS)

    async def _send(self, command: bytes) -> None:
        """Write one command and consume its single-line reply so the stream stays in sync."""
        reader, writer = await self._connection()
        writer.write(command)
        await asyncio.wait_for(writer.drain(), REDIS_COMMAND_TIMEOUT_SECONDS)
        reply = await asyncio.wait_for(reader.readline(), REDIS_COMMAND_TIMEOUT_SECONDS)
        if not reply:
            raise RealtimeProtocolError("redis closed the connection before replying")
        if reply.startswith(_ERROR_REPLY_PREFIX):
            raise RealtimeProtocolError(reply.decode("utf-8", errors="replace").strip())

    async def _connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open the connection once, authenticating and selecting the database the URL names."""
        if self._reader is not None and self._writer is not None:
            return self._reader, self._writer
        endpoint = self._endpoint
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(endpoint.host, endpoint.port, ssl=endpoint.use_tls or None),
            REDIS_COMMAND_TIMEOUT_SECONDS,
        )
        self._reader = reader
        self._writer = writer
        if endpoint.password is not None:
            credentials = (endpoint.username, endpoint.password) if endpoint.username else (endpoint.password,)
            await self._send(encode_redis_command("AUTH", *credentials))
        if endpoint.database_index:
            await self._send(encode_redis_command("SELECT", str(endpoint.database_index)))
        return reader, writer
