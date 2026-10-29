"""Turns raw Valheim server log lines into game events."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# When a player spawns the server logs their character ZDOID: <session id>:<object id>.
# The session id is new for every connection and stays the same when they respawn.
# A ZDOID of 0:0 means the character no longer exists, which is what you see when someone dies:
#   "10/28/2026 17:10:47: Got character ZDOID from Ragnar : 2971767933:1"   (joined)
#   "10/27/2026 23:29:02: Got character ZDOID from Ragnar : 0:0"            (died)
#   "10/27/2026 23:29:03: Got character ZDOID from Ragnar : 2971767933:146" (respawned)
_CHARACTER = re.compile(r"Got character ZDOID from (?P<name>.+?) : (?P<owner>-?\d+):(?P<id>\d+)\s*$")

# When a player disconnects, the server destroys the objects of their session:
#   "10/28/2026 17:12:20: Destroying abandoned non persistent zdo 2971767933:1 owner 2971767933"
_ABANDONED = re.compile(r"Destroying abandoned non persistent zdo -?\d+:\d+ owner (?P<owner>-?\d+)\s*$")

# A server shutdown disconnects everyone without the lines above. A (re)start
# means nobody can be online yet, which also covers a crash with no shutdown line:
#   "10/28/2026 16:41:00: ZNet Shutdown"
#   "10/28/2026 16:17:42: Valheim version: l-1.0.20 (network version 41)"
_SHUTDOWN = re.compile(r"ZNet Shutdown\s*$|Valheim version: ")

# Logged by the server when a raid starts. There is no matching line when it ends.
#   "10/28/2026 17:47:52: Random event set:army_eikthyr"
_RAID = re.compile(r"Random event set:\s*(?P<raid>\w+)\s*$")


@dataclass(frozen=True)
class PlayerDied:
    name: str


@dataclass(frozen=True)
class PlayerJoined:
    name: str


@dataclass(frozen=True)
class PlayerLeft:
    name: str


@dataclass(frozen=True)
class RaidStarted:
    raid: str  # Valheim's internal event name, e.g. "army_eikthyr"


GameEvent = PlayerDied | PlayerJoined | PlayerLeft | RaidStarted


class EventParser:
    # Valheim sometimes logs the same line twice in a row.
    DUPLICATE_WINDOW = 10.0
    # Raids are at least ~45 minutes apart, so a repeat within a minute is the same raid.
    RAID_DUPLICATE_WINDOW = 60.0
    # A spawn this soon after a death is a respawn, even for a session we haven't seen
    # join (the player was already online when the bot started).
    RESPAWN_WINDOW = 120.0

    def __init__(self) -> None:
        self._last_seen: dict[tuple[str, str], float] = {}
        self._sessions: dict[str, str] = {}  # session id -> character name, for players online

    def forget_timings(self) -> None:
        """Drop duplicate/respawn timers after replaying old lines, whose times are meaningless."""
        self._last_seen.clear()

    def online_names(self) -> tuple[str, ...]:
        """Players seen joining and not leaving. Misses anyone online before the bot started."""
        return tuple(sorted(self._sessions.values(), key=str.casefold))

    def parse(self, line: str, now: float | None = None) -> GameEvent | None:
        now = time.monotonic() if now is None else now

        if match := _CHARACTER.search(line):
            name, session = match["name"].strip(), match["owner"]
            if (session, match["id"]) == ("0", "0"):
                if self._is_duplicate(("death", name), now, self.DUPLICATE_WINDOW):
                    return None
                return PlayerDied(name)
            if session in self._sessions:
                return None  # respawn after a death
            self._sessions[session] = name
            died = self._last_seen.get(("death", name))
            if died is not None and now - died < self.RESPAWN_WINDOW:
                return None  # respawn of someone who joined before the bot started
            return PlayerJoined(name)

        if match := _ABANDONED.search(line):
            # One line per object of that session; only the first one counts.
            if name := self._sessions.pop(match["owner"], None):
                # So a quick rejoin after dying isn't mistaken for a respawn.
                self._last_seen.pop(("death", name), None)
                return PlayerLeft(name)
            return None

        if _SHUTDOWN.search(line):
            # Everyone is gone; the "server went offline" post already says so.
            self._sessions.clear()
            return None

        if match := _RAID.search(line):
            raid = match["raid"]
            if self._is_duplicate(("raid", raid), now, self.RAID_DUPLICATE_WINDOW):
                return None
            return RaidStarted(raid)

        return None

    def _is_duplicate(self, key: tuple[str, str], now: float, window: float) -> bool:
        if now - self._last_seen.get(key, float("-inf")) < window:
            return True
        self._last_seen[key] = now
        return False
