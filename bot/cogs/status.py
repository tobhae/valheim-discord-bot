"""Server status in the channel list, bot presence and the /status command.

The "stats rows" in the sidebar are voice channels that nobody can join; the
bot simply renames them. Discord allows 2 renames per channel every 10
minutes, so the bot renames at most every channel_rename_seconds (300) and the
names can lag behind by that much. The presence and /status are updated on
every poll. What is shown at all is controlled by [switches] in config.toml.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..messages import Messages
from ..models import ServerState, ServerStatus

if TYPE_CHECKING:
    from ..main import ValheimBot

log = logging.getLogger(__name__)

# Only used by the /status card; sidebar and presence texts live in config.toml.
STATE_ICONS = {
    ServerState.ONLINE: "🟢",
    ServerState.STARTING: "🟡",
    ServerState.OFFLINE: "🔴",
    ServerState.UNKNOWN: "⚪",
}
CHANNEL_NAME_LIMIT = 100


def player_count(status: ServerStatus) -> str:
    if status.max_players:
        return f"{status.players}/{status.max_players}"
    return str(status.players)


def format_uptime(seconds: float) -> str:
    """"42m" under an hour, "3h 42m" under a day, then "1d 3h" (minutes dropped)."""
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def template_values(server_name: str, status: ServerStatus, uptime: float | None) -> dict[str, object]:
    """Everything a status/presence template in config.toml may use."""
    return {
        "server": server_name,
        "state": status.state.value,
        "players": status.players,
        "max_players": status.max_players if status.max_players else "?",
        "uptime": format_uptime(uptime) if uptime is not None else "?",
        "world": status.world or "?",
    }


def _truncate(name: str) -> str:
    return name if len(name) <= CHANNEL_NAME_LIMIT else name[: CHANNEL_NAME_LIMIT - 1] + "…"


def status_channel_name(messages: Messages, values: dict[str, object], status: ServerStatus) -> str:
    return _truncate(messages.render(f"status_channel.{status.state.value}", **values))


def players_channel_name(
    messages: Messages, values: dict[str, object], status: ServerStatus, show_names: bool = True
) -> str:
    if status.state is not ServerState.ONLINE or status.players == 0:
        key, extra = "players_channel.empty", {}
    elif not status.player_names or not show_names:
        key, extra = "players_channel.count", {}
    else:
        key, extra = "players_channel.names", {"names": ", ".join(status.player_names)}
    fields = {k: values[k] for k in ("server", "players", "max_players")}
    return _truncate(messages.render(key, **fields, **extra))


@dataclass(frozen=True)
class Row:
    switch: str  # name in [switches]
    state_key: str  # where the bot remembers the channel it created
    configured_id: int | None  # a channel you picked yourself in .env
    position: int


class StatusCog(commands.Cog):
    def __init__(self, bot: ValheimBot) -> None:
        self.bot = bot
        self.config = bot.config
        self.status: ServerStatus | None = None
        self._last_known: ServerStatus | None = None
        self._presence: tuple[discord.Status, str | None] | None = None
        # Wall-clock time the server was first seen online; kept across bot restarts.
        self._online_since: float | None = bot.state.get("online_since")
        self.rows = (
            Row("status_row", "status_channel_id", self.config.status_channel_id, 0),
            Row("player_row", "players_channel_id", self.config.players_channel_id, 1),
        )
        self.poll.change_interval(seconds=self.config.status_poll_seconds)

    async def cog_load(self) -> None:
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    # --- polling --------------------------------------------------------------

    @tasks.loop(seconds=30)
    async def poll(self) -> None:
        # An error escaping the loop would stop it, so a failed poll is retried at the next one.
        try:
            await self._update()
        except Exception:
            log.exception("Status poll failed, retrying in %ss", self.config.status_poll_seconds)

    async def _update(self) -> None:
        status = self._with_names_from_log(await self.bot.status_provider.fetch())
        self.status = status
        self._track_uptime(status)
        values = self._values(status)
        await self._update_presence(status, values)
        # Compare against the last known state so one failed API call doesn't
        # produce a fake "went offline / is online" pair.
        if status.state is not ServerState.UNKNOWN:
            await self._announce_changes(self._last_known, status)
            self._last_known = status

        guild = self.bot.get_guild(self.config.guild_id)
        if guild is None:
            log.error("Bot is not in guild %s, check DISCORD_GUILD_ID", self.config.guild_id)
            return

        messages = self.bot.messages
        names = {
            "status_row": status_channel_name(messages, values, status),
            "player_row": players_channel_name(messages, values, status, messages.enabled("player_names")),
        }
        try:
            for row in self.rows:
                if messages.enabled("category_bar") and messages.enabled(row.switch):
                    if channel := await self._ensure_channel(guild, row, names[row.switch]):
                        await self._rename(channel, names[row.switch])
                else:
                    await self._remove_channel(guild, row)
            if not await self._remove_empty_category(guild):
                await self._sync_category_name(guild)
        except discord.Forbidden:
            log.error("Missing 'Manage Channels' permission for the status channels")
        except (discord.HTTPException, discord.RateLimited) as exc:
            log.warning("Updating status channels failed, retrying next poll: %s", exc)

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    # --- channels -------------------------------------------------------------

    def _category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        """The bot's category: remembered by id, so changing [display] category_name renames it."""
        category = guild.get_channel(self.bot.state.get("status_category_id") or 0)
        if isinstance(category, discord.CategoryChannel):
            return category
        # Not remembered yet (or deleted): the category a row the bot created is in, else by name.
        for row in self.rows:
            channel = guild.get_channel(self.bot.state.get(row.state_key) or 0)
            if not row.configured_id and channel is not None and channel.category is not None:
                category = channel.category
                break
        else:
            category = discord.utils.get(guild.categories, name=self.config.status_category_name)
        self.bot.state.set("status_category_id", category.id if category else None)
        return category

    async def _ensure_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        category = self._category(guild)
        if category is None:
            category = await guild.create_category(self.config.status_category_name, position=0)
            self.bot.state.set("status_category_id", category.id)
            log.info("Created category %r", category.name)
        return category

    async def _sync_category_name(self, guild: discord.Guild) -> None:
        # Through _rename, so it's limited like the rows if Discord stores the name differently.
        if (category := self._category(guild)) is not None:
            await self._rename(category, self.config.status_category_name)

    async def _ensure_channel(self, guild: discord.Guild, row: Row, initial_name: str) -> discord.VoiceChannel | None:
        channel_id = row.configured_id or self.bot.state.get(row.state_key)
        if channel_id:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.VoiceChannel):
                return channel
            if row.configured_id:
                log.error("%s=%s is not a voice channel in this server", row.state_key.upper(), row.configured_id)
                return None

        # Discord limits how many channels a server can create in a while. When it says
        # wait, wait (saved across restarts) instead of asking again on every poll.
        if time.time() < (create_after := self.bot.state.get("create_after", 0.0)):
            return None
        overwrites = {
            # Visible to everyone, joinable by no one.
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
            # The bot may only set overwrites for permissions it was invited with.
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
        }
        try:
            category = await self._ensure_category(guild)
            channel = await guild.create_voice_channel(
                initial_name, category=category, overwrites=overwrites, position=row.position,
                reason="Valheim status display",
            )
        except discord.RateLimited as exc:
            create_after = time.time() + exc.retry_after
            self.bot.state.set("create_after", create_after)
            log.warning(
                "Discord won't let me create channels until %s (too many created recently). "
                "To get the row now, create a voice channel yourself and put its ID in %s.",
                time.strftime("%H:%M", time.localtime(create_after)), row.state_key.upper(),
            )
            return None
        self.bot.state.set(row.state_key, channel.id)
        # Creating counts as setting the name, so the next rename has to wait like any other.
        self._set_rename_after(channel.id, time.time() + self.config.channel_rename_seconds)
        log.info("Created status channel %r (id %s)", channel.name, channel.id)
        return channel

    async def _remove_channel(self, guild: discord.Guild, row: Row) -> None:
        """Delete a row the bot created. Channels you configured yourself are never deleted."""
        if row.configured_id or not (channel_id := self.bot.state.get(row.state_key)):
            return
        if channel := guild.get_channel(channel_id):
            await channel.delete(reason=f"{row.switch} switched off in config.toml")
            log.info("Removed status channel %r (%s is off)", channel.name, row.switch)
        self.bot.state.set(row.state_key, None)

    async def _remove_empty_category(self, guild: discord.Guild) -> bool:
        """Delete the category once every row is switched off (not while a row is waiting to be created)."""
        messages = self.bot.messages
        if messages.enabled("category_bar") and any(messages.enabled(row.switch) for row in self.rows):
            return False
        category = self._category(guild)
        if category is None or category.channels:
            return False
        await category.delete(reason="All status rows switched off in config.toml")
        self.bot.state.set("status_category_id", None)
        log.info("Removed empty category %r", category.name)
        return True

    def _rename_after(self, channel_id: int) -> float:
        return self.bot.state.get("rename_after", {}).get(str(channel_id), 0.0)

    def _set_rename_after(self, channel_id: int, when: float) -> None:
        # Saved in state.json so a bot restart doesn't forget a recent rename.
        rename_after = {**self.bot.state.get("rename_after", {}), str(channel_id): when}
        self.bot.state.set("rename_after", rename_after)

    async def _rename(self, channel: discord.VoiceChannel | discord.CategoryChannel, name: str) -> None:
        if channel.name == name or time.time() < self._rename_after(channel.id):
            return  # unchanged, or too soon: picked up again on a later poll
        try:
            await channel.edit(name=name, reason="Valheim status update")
        except discord.RateLimited as exc:
            # Discord's limit was hit anyway (e.g. someone renamed it by hand). Skip
            # instead of waiting, so the name isn't stale when it finally goes through.
            self._set_rename_after(channel.id, time.time() + exc.retry_after + 1)
            log.warning("Rename of %r rate limited, next try in %.0fs", channel.name, exc.retry_after)
            return
        self._set_rename_after(channel.id, time.time() + self.config.channel_rename_seconds)
        log.info("Renamed channel to %r", name)

    # --- player names, uptime, presence, posts ---------------------------------

    def _with_names_from_log(self, status: ServerStatus) -> ServerStatus:
        """Fill in names from the server log when the status source has none (local Steam queries)."""
        events = self.bot.get_cog("EventsCog")
        if status.player_names is not None or not status.is_online or events is None:
            return status
        names = events.parser.online_names()
        # Only trust the log if it accounts for everyone the server counts; players who
        # were online before the bot started are missing from it.
        return replace(status, player_names=names) if len(names) == status.players else status

    def _track_uptime(self, status: ServerStatus) -> None:
        if status.state is ServerState.UNKNOWN:
            return  # a failed poll says nothing about whether the server restarted
        # If the bot was down while the server restarted, the saved time is from before
        # the restart; there is no API to ask, so the uptime is then too high.
        since = (self._online_since or time.time()) if status.is_online else None
        if since != self._online_since:
            self._online_since = since
            self.bot.state.set("online_since", since)

    def _uptime(self) -> float | None:
        return time.time() - self._online_since if self._online_since is not None else None

    def _values(self, status: ServerStatus) -> dict[str, object]:
        return template_values(self.config.server_name, status, self._uptime())

    async def _update_presence(self, status: ServerStatus, values: dict[str, object]) -> None:
        online = discord.Status.online if status.is_online else discord.Status.idle
        text = None
        if self.bot.messages.enabled("presence"):
            text = self.bot.messages.render("presence.online" if status.is_online else "presence.offline", **values)
        if (online, text) == self._presence:
            return  # unchanged, don't send it again
        self._presence = (online, text)
        await self.bot.change_presence(status=online, activity=discord.Game(text) if text else None)

    async def _announce_changes(self, previous: ServerStatus | None, current: ServerStatus) -> None:
        if previous is None:
            return  # first poll after startup is the baseline
        post, server = self.bot.post_event, self.config.server_name
        if previous.state is not ServerState.ONLINE and current.is_online:
            await post("server_online", server=server)
        elif previous.is_online and current.state is ServerState.OFFLINE:
            await post("server_offline", server=server)

        # Joins/leaves normally come from the server log (cogs/events.py). Comparing the
        # player lists is the fallback without a log, and only works when the status
        # source reports names (DatHost does, local Steam queries don't).
        if self.config.event_source == "none" and previous.is_online and current.is_online \
                and previous.player_names is not None and current.player_names is not None:
            before, after = set(previous.player_names), set(current.player_names)
            for name in sorted(after - before):
                await post("player_joined", name=discord.utils.escape_markdown(name), server=server)
            for name in sorted(before - after):
                await post("player_left", name=discord.utils.escape_markdown(name), server=server)

    # --- /status --------------------------------------------------------------

    @app_commands.command(name="status", description="Show the Valheim server status")
    async def status_command(self, interaction: discord.Interaction) -> None:
        status = self.status or await self.bot.status_provider.fetch()
        icon = STATE_ICONS[status.state]
        color = {
            ServerState.ONLINE: discord.Color.green(),
            ServerState.STARTING: discord.Color.gold(),
        }.get(status.state, discord.Color.red())
        embed = discord.Embed(title=f"{icon} {self.config.server_name}", color=color)
        embed.add_field(name="State", value=status.state.value.capitalize())
        embed.add_field(name="Players", value=player_count(status))
        if status.is_online and (uptime := self._uptime()) is not None:
            embed.add_field(name="Uptime", value=format_uptime(uptime))
        if status.world:
            embed.add_field(name="World", value=status.world)
        if status.address:
            embed.add_field(name="Address", value=f"`{status.address}`", inline=False)
        if status.player_names:
            embed.add_field(name="Online now", value="\n".join(status.player_names), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: ValheimBot) -> None:
    await bot.add_cog(StatusCog(bot))
