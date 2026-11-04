"""/start, /stop and /restart: admins control the DatHost server from Discord.

Only members with "Manage Server" see the commands by default. You can change
who may use each one under Server Settings → Integrations → <bot> without code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..sources.dathost import DatHostClient, DatHostError, is_running

if TYPE_CHECKING:
    from ..main import ValheimBot

log = logging.getLogger(__name__)

CONFIRM_SECONDS = 60.0


class ConfirmView(discord.ui.View):
    """Buttons under the question /stop and /restart ask, since both kick everyone off the server."""

    def __init__(
        self, interaction: discord.Interaction, label: str, working: str, action: Callable[[], Awaitable[str]],
    ) -> None:
        super().__init__(timeout=CONFIRM_SECONDS)
        self.interaction = interaction
        self.working = working
        self.action = action
        self.confirm.label = label

    @discord.ui.button(style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content=self.working, view=None)
        await interaction.edit_original_response(content=await self.action())

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)

    async def on_timeout(self) -> None:
        # The message is gone if the admin dismissed it, which is fine.
        with contextlib.suppress(discord.HTTPException):
            await self.interaction.edit_original_response(content="Cancelled, nobody confirmed.", view=None)


class ServerCog(commands.Cog):
    def __init__(self, client: DatHostClient) -> None:
        self.client = client
        self.busy = asyncio.Lock()  # one at a time, so a /stop can't land in the middle of a /restart

    async def _run(self, command: str, user: object, action: Callable[[], Awaitable[str]]) -> str:
        """Run a server action and return the reply for the admin."""
        if self.busy.locked():
            return "Another server command is still running, try again when it's done."
        async with self.busy:
            log.info("%s ran /%s", user, command)
            try:
                return await action()
            except DatHostError as exc:
                log.warning("/%s failed: %s", command, exc)
                return f"/{command} failed: {exc}. Check `/status` before trying again."

    async def _start(self) -> str:
        if is_running(await self.client.get_server()):
            return "The server is already running."
        await self.client.start_server()
        return "Starting the server, this can take a few minutes."

    async def _stop(self) -> str:
        if not is_running(await self.client.get_server()):
            return "The server is already stopped."
        await self.client.stop_server()
        return "Stopping the server."

    async def _restart(self) -> str:
        await self.client.restart_server()
        return "The server has stopped and is starting again, this can take a few minutes."

    @app_commands.command(name="start", description="Start the game server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def start(self, interaction: discord.Interaction) -> None:
        # DatHost can take longer than the 3 seconds Discord waits for a reply.
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await self._run("start", interaction.user, self._start)
        await interaction.followup.send(reply, ephemeral=True)

    @app_commands.command(name="stop", description="Stop the game server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        view = ConfirmView(
            interaction, "Stop server", "Stopping...", lambda: self._run("stop", interaction.user, self._stop),
        )
        await interaction.response.send_message(
            "Stop the server? Everyone online gets kicked.", view=view, ephemeral=True,
        )

    @app_commands.command(name="restart", description="Restart the game server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def restart(self, interaction: discord.Interaction) -> None:
        view = ConfirmView(
            interaction, "Restart server", "Restarting, waiting for the server to stop...",
            lambda: self._run("restart", interaction.user, self._restart),
        )
        await interaction.response.send_message(
            "Restart the server? Everyone online gets kicked.", view=view, ephemeral=True,
        )


async def setup(bot: ValheimBot) -> None:
    # Without DatHost there's nothing to control, so the commands don't show up at all.
    if bot.dathost is not None:
        await bot.add_cog(ServerCog(bot.dathost))
