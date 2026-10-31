"""Reads the server log and posts game events (deaths, joins/leaves, raids) to the events channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from ..events import EventParser, PlayerDied, PlayerJoined, PlayerLeft, RaidStarted

if TYPE_CHECKING:
    from ..main import ValheimBot

log = logging.getLogger(__name__)

# Player event -> its switch in config.toml, which is also the name of its [events] text.
PLAYER_EVENTS = {PlayerDied: "death", PlayerJoined: "player_joined", PlayerLeft: "player_left"}


class EventsCog(commands.Cog):
    def __init__(self, bot: ValheimBot) -> None:
        self.bot = bot
        self.parser = EventParser()
        self.poll.change_interval(seconds=bot.config.event_poll_seconds)

    async def cog_load(self) -> None:
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    @tasks.loop(seconds=15)
    async def poll(self) -> None:
        # An error escaping the loop would stop it, so a failed poll is retried at the next one.
        try:
            await self._post_new_events()
        except Exception:
            log.exception("Event poll failed, retrying in %ss", self.bot.config.event_poll_seconds)

    async def _post_new_events(self) -> None:
        server = self.bot.config.server_name
        for line in await self.bot.log_source.read_new_lines():
            event = self.parser.parse(line)
            # Events are still detected (and logged) when their switch is off.
            if switch := PLAYER_EVENTS.get(type(event)):
                log.info("%s: %s", switch, event.name)
                await self.bot.post_event(switch, name=discord.utils.escape_markdown(event.name), server=server)
            elif isinstance(event, RaidStarted):
                log.info("Raid: %s", event.raid)
                if attackers := self.bot.messages.raid_attackers(event.raid):
                    await self.bot.post_event("raid", server=server, attackers=attackers, raid=event.raid)
                else:
                    await self.bot.post_event("raid", "raid_unknown", server=server, raid=event.raid)

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        # Replay the recent log without posting anything, only so the bot knows who
        # is already online (for row 2 and their "left" post).
        try:
            backlog = await self.bot.log_source.read_backlog()
        except Exception:
            # An error here would stop the loop before its first poll.
            log.exception("Could not read earlier log lines; players already online won't be known")
            return
        for line in backlog:
            self.parser.parse(line)
        self.parser.forget_timings()
        log.info("Read %d earlier log lines; online now: %s", len(backlog),
                 ", ".join(self.parser.online_names()) or "nobody")


async def setup(bot: ValheimBot) -> None:
    await bot.add_cog(EventsCog(bot))
