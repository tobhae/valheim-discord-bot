"""Minimal async DatHost API client (read-only).

API reference: https://dathost.com/reference
Endpoints used:
  GET /game-servers/{id}          -> on, booting, players_online, ip, ports, valheim_settings
  GET /game-servers/{id}/metrics  -> players_online: [{name}, ...]
  GET /game-servers/{id}/console  -> {"lines": [...]}  (last N console lines)

Contains the client, the status provider (STATUS_SOURCE=dathost) and the console
log source (EVENT_SOURCE=dathost).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import aiohttp

from ..models import ServerState, ServerStatus

log = logging.getLogger(__name__)

API_BASE = "https://dathost.com/api/0.1"
DEFAULT_RETRY_AFTER = 60.0


class DatHostError(Exception):
    pass


class DatHostClient:
    def __init__(self, email: str, password: str, server_id: str, timeout: float = 10.0) -> None:
        self._auth = aiohttp.BasicAuth(email, password)
        self._server_id = server_id
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        # Shared by every endpoint: a 429 anywhere pauses the whole account.
        self._cooldown_until = 0.0

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            raise DatHostError(f"rate limited, retrying in {remaining:.0f}s")
        if self._session is None:
            self._session = aiohttp.ClientSession(
                auth=self._auth,
                timeout=self._timeout,
                headers={"Accept": "application/json", "User-Agent": "valheim-discord-bot"},
            )
        url = f"{API_BASE}/game-servers/{self._server_id}{path}"
        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status == 429:
                    try:
                        delay = float(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
                    except ValueError:
                        delay = DEFAULT_RETRY_AFTER
                    self._cooldown_until = time.monotonic() + delay
                    raise DatHostError(f"rate limited for {delay:.0f}s")
                if resp.status == 401:
                    raise DatHostError("authentication failed (check DATHOST_EMAIL / DATHOST_PASSWORD)")
                if resp.status == 404:
                    raise DatHostError("server not found (check DATHOST_SERVER_ID)")
                if resp.status != 200:
                    raise DatHostError(f"unexpected HTTP {resp.status}")
                try:
                    return await resp.json(content_type=None)
                except ValueError:  # e.g. an HTML maintenance page
                    raise DatHostError("response wasn't valid JSON") from None
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DatHostError(f"request failed: {exc.__class__.__name__}") from None

    async def get_server(self) -> dict[str, Any]:
        return _object(await self._get(""), "server")

    async def get_metrics(self) -> dict[str, Any]:
        return _object(await self._get("/metrics"), "metrics")

    async def get_console_lines(self, max_lines: int = 200) -> list[str]:
        lines = _object(await self._get("/console", {"max_lines": max_lines}), "console").get("lines")
        if not isinstance(lines, list):
            raise DatHostError("unexpected console response format")
        return [line for line in lines if isinstance(line, str)]


def _object(payload: Any, endpoint: str) -> dict[str, Any]:
    """The response as a JSON object; anything else is reported like a failed request."""
    if not isinstance(payload, dict):
        raise DatHostError(f"unexpected {endpoint} response format")
    return payload


class DatHostStatusProvider:
    def __init__(self, client: DatHostClient, max_players: int | None) -> None:
        self._client = client
        self._max_players = max_players

    async def fetch(self) -> ServerStatus:
        try:
            server = await self._client.get_server()
        except DatHostError as exc:
            log.warning("DatHost status fetch failed: %s", exc)
            return ServerStatus(ServerState.UNKNOWN, max_players=self._max_players)

        if server.get("booting"):
            state = ServerState.STARTING
        elif server.get("on"):
            state = ServerState.ONLINE
        else:
            state = ServerState.OFFLINE

        players = server.get("players_online")
        players = players if isinstance(players, int) and players >= 0 else 0

        names: tuple[str, ...] | None = None
        if state is ServerState.ONLINE:
            try:
                metrics = await self._client.get_metrics()
                online = metrics.get("players_online")
                if isinstance(online, list):
                    names = tuple(p["name"] for p in online if isinstance(p, dict) and p.get("name"))
            except DatHostError as exc:
                log.debug("DatHost metrics fetch failed: %s", exc)
        else:
            players = 0

        # Parts in an unexpected shape are left out instead of failing the whole status.
        settings = server.get("valheim_settings")
        settings = settings if isinstance(settings, dict) else {}
        ports = server.get("ports")
        ports = ports if isinstance(ports, dict) else {}
        host = server.get("custom_domain") or server.get("ip") or server.get("raw_ip")
        port = ports.get("game")
        return ServerStatus(
            state=state,
            players=players,
            max_players=self._max_players,
            player_names=names,
            world=settings.get("world_name"),
            address=f"{host}:{port}" if host and port else host,
        )


def new_lines_since(previous: Sequence[str], current: Sequence[str]) -> list[str]:
    """Return the lines of `current` that come after the overlap with `previous`.

    The console endpoint only returns a sliding window of the last N lines, so
    we find the longest suffix of the previous window that is a prefix of the
    current one. With no overlap, the whole window is considered new.
    """
    max_overlap = min(len(previous), len(current))
    for size in range(max_overlap, 0, -1):
        if list(previous[-size:]) == list(current[:size]):
            return list(current[size:])
    return list(current)


class DatHostConsoleSource:
    def __init__(self, client: DatHostClient, window: int = 200) -> None:
        self._client = client
        self._window = window
        self._previous: list[str] | None = None

    async def _fetch(self) -> list[str] | None:
        try:
            return await self._client.get_console_lines(self._window)
        except DatHostError as exc:
            log.warning("DatHost console fetch failed: %s", exc)
            return None

    async def read_backlog(self) -> list[str]:
        self._previous = await self._fetch()
        return list(self._previous or [])

    async def read_new_lines(self) -> list[str]:
        current = await self._fetch()
        if current is None:
            return []
        if self._previous is None:  # the backlog fetch failed; this is the baseline
            self._previous = current
            return []
        new = new_lines_since(self._previous, current)
        self._previous = current
        return new

    async def close(self) -> None:
        pass  # the DatHostClient is shared with the status provider; the bot closes it
