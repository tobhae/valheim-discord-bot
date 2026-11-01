"""Status via the Steam server query protocol (A2S).

Works against any Valheim server whose query port is reachable, which makes it
the easy option for a local test server. Valheim listens on game port + 1.
"""

from __future__ import annotations

import logging

import a2s

from ..models import ServerState, ServerStatus, StatusProvider

log = logging.getLogger(__name__)


class A2SStatusProvider(StatusProvider):
    # 1 miss = Status Unknown, 2 misses = Offline
    MISSES_FOR_OFFLINE = 2

    def __init__(self, host: str, port: int, max_players: int | None, timeout: float = 3.0) -> None:
        self._address = (host, port)
        self._max_players = max_players
        self._timeout = timeout
        self._misses = 0

    async def fetch(self) -> ServerStatus:
        try:
            info = await a2s.ainfo(self._address, timeout=self._timeout)
        except (TimeoutError, OSError, a2s.BrokenMessageError) as exc:
            self._misses += 1
            log.debug("A2S query failed (%d in a row): %s", self._misses, exc)
            state = ServerState.OFFLINE if self._misses >= self.MISSES_FOR_OFFLINE else ServerState.UNKNOWN
            return ServerStatus(state, max_players=self._max_players)
        self._misses = 0

        names: tuple[str, ...] | None = None
        try:
            players = await a2s.aplayers(self._address, timeout=self._timeout)
            named = tuple(p.name for p in players if p.name)
            # Valheim often reports empty names; only trust a complete list.
            if len(named) == info.player_count:
                names = named
        except (TimeoutError, OSError, a2s.BrokenMessageError):
            pass

        return ServerStatus(
            state=ServerState.ONLINE,
            players=info.player_count,
            max_players=self._max_players or info.max_players,
            player_names=names,
            world=info.map_name or None,
            address=f"{self._address[0]}:{self._address[1] - 1}",
        )
