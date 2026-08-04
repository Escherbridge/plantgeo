"""Realtime publishing: RESP encoding, URL parsing, and the best-effort contract that never fails a write."""

# ruff: noqa: PLR2004

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from agri_data_service.ingest.realtime import (
    REALTIME_CHANNEL_PATTERN,
    RealtimePublisher,
    encode_redis_command,
    parse_redis_url,
    resolve_redis_url,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FIRE_CHANNEL = "layer:fire-detections"


class FakeRedisServer:
    """A one-connection Redis stand-in that records commands and answers with a fixed reply."""

    def __init__(self, reply: bytes = b":1\r\n") -> None:
        self.reply = reply
        self.commands: list[bytes] = []
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        """Bind an ephemeral loopback port and begin accepting one connection at a time."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close the listening socket."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        """The `redis://` URL a publisher should be pointed at."""
        return f"redis://127.0.0.1:{self.port}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readline()
                if not header:
                    return
                argument_count = int(header[1:].strip())
                command = bytearray(header)
                for _ in range(argument_count):
                    length_line = await reader.readline()
                    command += length_line
                    command += await reader.readexactly(int(length_line[1:].strip()) + 2)
                self.commands.append(bytes(command))
                writer.write(self.reply)
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError, ValueError):
            return
        finally:
            writer.close()


@pytest.fixture
async def redis_server() -> AsyncIterator[FakeRedisServer]:
    """A loopback Redis stand-in, so the publisher is exercised over a real socket."""
    server = FakeRedisServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def test_a_command_encodes_as_a_resp_array_of_bulk_strings() -> None:
    assert encode_redis_command("PUBLISH", "layer:x", "{}") == b"*3\r\n$7\r\nPUBLISH\r\n$7\r\nlayer:x\r\n$2\r\n{}\r\n"


def test_a_redis_url_splits_into_the_facts_a_publish_needs() -> None:
    endpoint = parse_redis_url("rediss://default:s3cr3t@junction.example:44220/2")
    assert endpoint.host == "junction.example"
    assert endpoint.port == 44220
    assert endpoint.username == "default"
    assert endpoint.password == "s3cr3t"
    assert endpoint.database_index == 2
    assert endpoint.use_tls is True


def test_a_bare_redis_url_defaults_its_port_and_database() -> None:
    endpoint = parse_redis_url("redis://localhost")
    assert endpoint.port == 6379
    assert endpoint.database_index == 0
    assert endpoint.use_tls is False
    assert endpoint.password is None


@pytest.mark.parametrize("url", ["http://localhost:6379", "redis://", "redis://localhost/not-a-number"])
def test_a_malformed_redis_url_is_refused_up_front(url: str) -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        parse_redis_url(url)


def test_the_redis_url_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert resolve_redis_url() == "redis://localhost:6379"
    monkeypatch.setenv("REDIS_URL", "redis://elsewhere:6380")
    assert resolve_redis_url() == "redis://elsewhere:6380"


@pytest.mark.parametrize("channel", ["layer:fire-detections", "layer:a", "layer:water-gauges"])
def test_the_governed_channel_shape_is_accepted(channel: str) -> None:
    assert REALTIME_CHANNEL_PATTERN.match(channel) is not None


@pytest.mark.parametrize("channel", ["fire-detections", "layer:", "layer:Fire", "layer:a b", "notlayer:x"])
def test_a_channel_outside_the_governed_shape_is_refused(channel: str) -> None:
    assert REALTIME_CHANNEL_PATTERN.match(channel) is None


async def test_a_publish_reaches_redis_as_one_publish_command(redis_server: FakeRedisServer) -> None:
    message = {"type": "Feature", "id": "row-1", "properties": {"id": "x"}, "geometry": None}
    async with RealtimePublisher(redis_server.url) as publisher:
        await publisher.publish(FIRE_CHANNEL, message)
        await publisher.publish(FIRE_CHANNEL, message)
        assert publisher.delivered == 2
        assert publisher.dropped == 0

    assert len(redis_server.commands) == 2
    assert b"PUBLISH" in redis_server.commands[0]
    assert FIRE_CHANNEL.encode() in redis_server.commands[0]
    assert json.dumps(message).encode() in redis_server.commands[0]


async def test_a_channel_outside_the_contract_is_refused_before_any_connection() -> None:
    publisher = RealtimePublisher("redis://127.0.0.1:1")
    with pytest.raises(ValueError, match="realtime channel"):
        await publisher.publish("fire-detections", {})
    await publisher.aclose()


async def test_an_unreachable_redis_degrades_to_counted_drops_rather_than_failing_the_write() -> None:
    # A Redis outage must never roll back a durable warehouse write; it costs live invalidation only.
    publisher = RealtimePublisher("redis://127.0.0.1:1")
    await publisher.publish(FIRE_CHANNEL, {"type": "Feature"})
    await publisher.publish(FIRE_CHANNEL, {"type": "Feature"})
    assert publisher.delivered == 0
    assert publisher.dropped == 2
    await publisher.aclose()


async def test_an_error_reply_marks_the_publisher_unavailable_for_the_rest_of_the_run() -> None:
    server = FakeRedisServer(reply=b"-NOAUTH Authentication required\r\n")
    await server.start()
    try:
        publisher = RealtimePublisher(server.url)
        await publisher.publish(FIRE_CHANNEL, {"type": "Feature"})
        await publisher.publish(FIRE_CHANNEL, {"type": "Feature"})
        assert publisher.delivered == 0
        assert publisher.dropped == 2
        await publisher.aclose()
    finally:
        await server.stop()


async def test_a_credentialed_url_authenticates_and_selects_its_database() -> None:
    server = FakeRedisServer(reply=b"+OK\r\n")
    await server.start()
    try:
        async with RealtimePublisher(f"redis://default:s3cr3t@127.0.0.1:{server.port}/3") as publisher:
            await publisher.publish(FIRE_CHANNEL, {"type": "Feature"})
        assert b"AUTH" in server.commands[0]
        assert b"default" in server.commands[0]
        assert b"SELECT" in server.commands[1]
        assert b"3" in server.commands[1]
        assert b"PUBLISH" in server.commands[2]
    finally:
        await server.stop()
