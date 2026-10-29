"""/announce: admins write a message in a pop-up form and the bot posts it.

Only members with "Manage Server" see the command by default. You can change
who may use it under Server Settings → Integrations → <bot> without code.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from ..main import ValheimBot

log = logging.getLogger(__name__)

Style = Literal["plain", "embed"]
PLAIN_LIMIT = 2000  # Discord's message limit; embed descriptions allow 4096


class AnnouncementModal(discord.ui.Modal, title="New announcement"):
    heading = discord.ui.TextInput(label="Title (optional)", required=False, max_length=256)
    body = discord.ui.TextInput(
        label="Message", style=discord.TextStyle.paragraph, max_length=4000,
        placeholder="Markdown works: **bold**, *italic*, lists, links...",
    )

    def __init__(
        self, channel: discord.TextChannel, mention: discord.Role | None, style: Style,
        original: discord.Message | None = None,
    ) -> None:
        super().__init__()
        self.channel = channel
        self.mention = mention
        self.style = style
        self.original = original
        if original is not None:
            self.title = "Edit announcement"
            if original.embeds:
                self.heading.default = original.embeds[0].title
                self.body.default = original.embeds[0].description
                self.style = "embed"
            else:
                # The title is part of the text in plain posts, so edit it there.
                self.body.default = original.content
                self.style = "plain"
        if self.style == "plain":
            self.body.max_length = PLAIN_LIMIT

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title, body = self.heading.value.strip(), self.body.value

        embed: discord.Embed | None = None
        content: str | None = None
        if self.style == "embed":
            embed = discord.Embed(title=title or None, description=body, color=discord.Color.gold())
        else:
            content = f"## {title}\n{body}" if title else body
            if len(content) > PLAIN_LIMIT:
                await interaction.response.send_message(
                    f"Plain messages are limited to {PLAIN_LIMIT} characters, use `style: embed` for longer ones.",
                    ephemeral=True,
                )
                return

        if self.original is not None:
            if embed is not None:
                content = self.original.content or None  # keep the ping line; edits never re-ping
            await self.original.edit(content=content, embed=embed)
            await interaction.response.send_message(f"Updated {self.original.jump_url}", ephemeral=True)
            return

        if self.mention is not None:
            ping = "@everyone" if self.mention.is_default() else self.mention.mention
            content = f"{ping}\n{content}" if content else ping
        message = await self.channel.send(
            content=content,
            embed=embed,
            # Only ping what the admin explicitly picked, never mentions in the body.
            allowed_mentions=discord.AllowedMentions(
                everyone=self.mention is not None and self.mention.is_default(),
                roles=[self.mention] if self.mention is not None and not self.mention.is_default() else [],
                users=False,
            ),
        )
        log.info("%s posted an announcement in #%s", interaction.user, self.channel.name)
        await interaction.response.send_message(f"Posted {message.jump_url}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Announcement failed", exc_info=error)
        text = "I don't have permission to post there." if isinstance(error, discord.Forbidden) \
            else "Something went wrong, check the bot logs."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class AnnounceCog(commands.Cog):
    def __init__(self, bot: ValheimBot) -> None:
        self.bot = bot
        self.edit_menu = app_commands.ContextMenu(name="Edit announcement", callback=self.edit_announcement)
        self.edit_menu.default_permissions = discord.Permissions(manage_guild=True)
        self.edit_menu.guild_only = True

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.edit_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.edit_menu.name, type=self.edit_menu.type)

    @app_commands.command(name="announce", description="Post an announcement as the bot")
    @app_commands.describe(
        channel="Where to post (defaults to this channel)",
        mention="Role to ping, pick @everyone to ping everyone",
        style="Plain message (default) or an embed card",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def announce(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
        mention: discord.Role | None = None, style: Style = "plain",
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Pick a text channel to post in.", ephemeral=True)
            return
        perms = target.permissions_for(target.guild.me)
        if not perms.send_messages or (style == "embed" and not perms.embed_links):
            needed = "*Send Messages* and *Embed Links*" if style == "embed" else "*Send Messages*"
            await interaction.response.send_message(f"I need {needed} in {target.mention}.", ephemeral=True)
            return
        await interaction.response.send_modal(AnnouncementModal(target, mention, style))

    async def edit_announcement(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if message.author.id != self.bot.user.id or not isinstance(message.channel, discord.TextChannel):
            await interaction.response.send_message("I can only edit messages I posted.", ephemeral=True)
            return
        # The style is taken from the original message.
        await interaction.response.send_modal(AnnouncementModal(message.channel, None, "plain", original=message))


async def setup(bot: ValheimBot) -> None:
    await bot.add_cog(AnnounceCog(bot))
