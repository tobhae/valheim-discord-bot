"""Reads a container's log straight from the Docker Engine API (EVENT_SOURCE=docker).

For a Valheim server running in Docker on the same machine as the bot, e.g. the
local test server from compose.valheim.yml. It talks to Docker through its
socket, the same way the `docker logs -f` command does, so no pipe is needed.

API: GET /containers/{name}/logs?follow=1&timestamps=1
https://docs.docker.com/reference/api/engine/latest/#tag/Container/operation/ContainerLogs
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import logging
import struct
import time
from datetime import datetime

import aiohttp

log = logging.getLogger(__name__)

BACKLOG_LINES = 1000
RECONNECT_SECONDS = 5.0


def parse_timestamp(value: str) -> int:
    """Docker's RFC 3339 timestamp ("2026-10-28T17:42:48.12345Z") in nanoseconds."""
    seconds, _, fraction = value.rstrip("Z").partition(".")
    whole = int(datetime.fromisoformat(seconds + "+00:00").timestamp())
    return whole * 1_000_000_000 + int(fraction.ljust(9, "0")[:9] or 0)


def split_timestamp(line: str) -> tuple[int, str] | None:
    stamp, _, text = line.partition(" ")
    try:
        return parse_timestamp(stamp), text
    except ValueError:
        return None


class DockerLogSource:
    def __init__(self, container: str, socket_path: str) -> None:
        self._container = container
        self._socket_path = socket_path
        self._session: aiohttp.ClientSession | None = None
        self._buffer: list[str] = []
        self._last_ns = 0 # Timestamp of the newest line seen (or of the bot's start), to resume without gaps or repeats.
        self._follow_task: asyncio.Task | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=self._socket_path),
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=5),
            )
        return self._session

    async def close(self) -> None:
        if self._follow_task is not None:
            self._follow_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._follow_task
        if self._session is not None:
            await self._session.close()

    async def read_backlog(self) -> list[str]:
        started_ns = time.time_ns()
        lines: list[str] = []
        try:
            async for text in self._stream(follow=False, tail=BACKLOG_LINES):
                lines.append(text)
        except (aiohttp.ClientError, OSError) as exc:
            log.warning("Could not read the log of container %r: %s", self._container, _explain(exc))
        self._last_ns = max(self._last_ns, started_ns)
        self._follow_task = asyncio.create_task(self._follow())
        return lines

    async def read_new_lines(self) -> list[str]:
        # Filled by the live connection that read_backlog() starts.
        lines, self._buffer = self._buffer, []
        return lines

    async def _follow(self) -> None:
        """Keep a live log connection open; reconnect when the container restarts."""
        problem: str | None = None  # last reported problem, so a stopped server isn't logged every 5s
        while True:
            try:
                async for text in self._stream(follow=True):
                    if problem is not None:
                        log.info("Reading the log of container %r again", self._container)
                        problem = None
                    self._buffer.append(text)
                # A stopped container's log ends at once; the loop waits for it to start.
                message = "isn't running, waiting for it to start"
            except (aiohttp.ClientError, OSError) as exc:
                message = f"is unavailable: {_explain(exc)}"
            if message != problem:
                log.warning("Container %r %s", self._container, message)
                problem = message
            await asyncio.sleep(RECONNECT_SECONDS)

    async def _stream(self, follow: bool, tail: int | None = None):
        params = {"stdout": "1", "stderr": "1", "timestamps": "1", "follow": "1" if follow else "0"}
        if tail is not None:
            params["tail"] = str(tail)
        if self._last_ns:
            params["since"] = f"{self._last_ns // 1_000_000_000}.{self._last_ns % 1_000_000_000:09d}"
        url = f"http://docker/containers/{self._container}/logs"
        async with self._http().get(url, params=params) as resp:
            if resp.status == 404:
                raise aiohttp.ClientError(f"no container named {self._container!r} (check DOCKER_CONTAINER)")
            if resp.status != 200:
                raise aiohttp.ClientError(f"Docker API returned HTTP {resp.status}")
            async for line in _lines(resp):
                parsed = split_timestamp(line)
                if parsed is None:
                    continue
                ns, text = parsed
                if ns <= self._last_ns:
                    continue  # already seen before a reconnect
                self._last_ns = ns
                yield text


async def _lines(resp: aiohttp.ClientResponse):
    """Text lines from a logs response."""
    # Keeps a multi-byte character (å, ø...) intact when a chunk ends in the middle of it.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    async for chunk in _chunks(resp):
        pending += decoder.decode(chunk)
        *done, pending = pending.split("\n")
        for line in done:
            yield line.rstrip("\r")
    if pending := pending + decoder.decode(b"", final=True):
        yield pending.rstrip("\r")


async def _chunks(resp: aiohttp.ClientResponse):
    """Raw log bytes, in either of Docker's two formats."""
    if resp.headers.get("Content-Type", "").startswith("application/vnd.docker.raw-stream"):
        # Container with a TTY: plain text.
        async for chunk in resp.content.iter_any():
            yield chunk
        return
    # No TTY (the usual case): frames of [stream type, 0, 0, 0, size (4 bytes)] + payload.
    while True:
        try:
            header = await resp.content.readexactly(8)
        except asyncio.IncompleteReadError:
            return
        _, size = struct.unpack(">BxxxL", header)
        yield await resp.content.readexactly(size)


def _explain(exc: BaseException) -> str:
    if isinstance(exc, PermissionError) or "Permission denied" in str(exc):
        return f"{exc} (the bot's user needs access to the Docker socket, see dev/README.md)"
    if isinstance(exc, FileNotFoundError) or "No such file" in str(exc):
        return f"{exc} (is Docker running? check DOCKER_SOCKET)"
    return str(exc)
