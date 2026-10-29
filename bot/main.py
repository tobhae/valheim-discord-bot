from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from .config import Config
from .messages import Messages
from .sources.a2s_query import A2SStatusProvider
from .sources.dathost import DatHostClient, DatHostConsoleSource, DatHostStatusProvider
from .sources.docker_logs import DockerLogSource
from .sources.logs import FileLogSource, NullLogSource
from .state import State

log = logging.getLogger("bot")

EXTENSIONS = ("bot.cogs.status", "bot.cogs.events", "bot.cogs.announce")


class ValheimBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            # Slash commands only, so no privileged intents (message content etc.) are needed.
            intents=discord.Intents.default(),
            # By default discord.py silently waits out rate limits, even for minutes, which
            # freezes the loop that made the call. Longer than this raises discord.RateLimited.
            max_ratelimit_timeout=30.0,
        )
        self.config = config
        self.state = State(config.state_file)
        self.messages = Messages.load(config.messages_file)

        self.dathost: DatHostClient | None = None
        if "dathost" in (config.status_source, config.event_source):
            self.dathost = DatHostClient(config.dathost_email, config.dathost_password, config.dathost_server_id)

        if config.status_source == "dathost":
            self.status_provider = DatHostStatusProvider(self.dathost, config.max_players)
        else:
            self.status_provider = A2SStatusProvider(config.a2s_host, config.a2s_port, config.max_players)

        if config.event_source == "dathost":
            self.log_source = DatHostConsoleSource(self.dathost)
        elif config.event_source == "docker":
            self.log_source = DockerLogSource(config.docker_container, config.docker_socket)
        elif config.event_source == "file":
            self.log_source = FileLogSource(config.event_log_file)
        else:
            self.log_source = NullLogSource()

    async def setup_hook(self) -> None:
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        # Syncing to one guild makes commands show up instantly (global sync can take an hour).
        guild = discord.Object(self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d application commands to guild %s", len(synced), self.config.guild_id)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s)", self.user, self.user.id)

    async def close(self) -> None:
        await self.log_source.close()
        if self.dathost is not None:
            await self.dathost.close()
        await super().close()

    async def post_event(self, switch: str, template: str | None = None, **values: object) -> None:
        """Post events.<template> (default: same name as the switch) if its switch is on."""
        channel_id = self.config.events_channel_id
        if not channel_id or not self.messages.enabled(switch):
            return
        text = self.messages.render(f"events.{template or switch}", **values)
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("EVENTS_CHANNEL_ID %s is not a text channel I can see", channel_id)
            return
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, discord.RateLimited) as exc:
            log.warning("Could not post event: %s", exc)


def run() -> None:
    # Reads .env when running outside Docker; variables already set (e.g. by compose) win.
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    config = Config.from_env()
    ValheimBot(config).run(config.discord_token, log_handler=None)
