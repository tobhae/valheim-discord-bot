from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ServerState(StrEnum):
    ONLINE = "online"
    STARTING = "starting"
    OFFLINE = "offline"
    UNKNOWN = "unknown"  # we could not reach the API / server at all


@dataclass(frozen=True)
class ServerStatus:
    state: ServerState
    players: int = 0
    max_players: int | None = None
    # None means "the source cannot tell us names", not "nobody online".
    player_names: tuple[str, ...] | None = None
    world: str | None = None
    address: str | None = None

    @property
    def is_online(self) -> bool:
        return self.state is ServerState.ONLINE


class StatusProvider(Protocol):
    """Where the server status comes from (STATUS_SOURCE): DatHost or a Steam query."""

    async def fetch(self) -> ServerStatus:
        """The current status. Failures are reported as UNKNOWN or OFFLINE, never raised."""
        ...
