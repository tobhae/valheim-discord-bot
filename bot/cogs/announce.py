"""/announce: admins write a message in a pop-up form and the bot posts it.

Only members with "Manage Server" see the command by default. You can change
who may use it under Server Settings → Integrations → <bot> without code.
"""

from __future__ import annotations

import logging
from pathlib import PurePath
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from ..main import ValheimBot

log = logging.getLogger(__name__)

Style = Literal["plain", "embed"]
PLAIN_LIMIT = 2000  # Discord's message limit; embed descriptions allow 4096
IMAGE_TYPES = ("png", "jpg", "gif", "webp")
MB = 1024 * 1024


def image_filename(content_type: str | None, filename: str) -> str | None:
    """The name to post an image under, or None if the file isn't a supported image.

    A fixed ASCII name keeps `attachment://` working whatever the file was called.
    """
    if content_type:
        kind, _, ext = content_type.split(";")[0].strip().lower().partition("/")
        if kind != "image":
            return None
    else:
        ext = PurePath(filename).suffix.lower().lstrip(".")
    ext = "jpg" if ext == "jpeg" else ext
    return f"image.{ext}" if ext in IMAGE_TYPES else None


def image_problem(content_type: str | None, filename: str, size: int, limit: int) -> str | None:
    """Why the bot can't post this image, or None if it can."""
    if image_filename(content_type, filename) is None:
        return "That file isn't an image I can post (PNG, JPG, GIF or WebP)."
    if size > limit:
        return f"That image is {size / MB:.1f} MB, I can only post up to {limit / MB:.0f} MB here."
    return None


def announcement_card(message: discord.Message) -> discord.Embed | None:
    """The card of an embed-style announcement. Link previews in plain posts are embeds too, but not cards."""
    return next((embed for embed in message.embeds if embed.type == "rich"), None)


class AnnouncementModal(discord.ui.Modal, title="New announcement"):
    heading = discord.ui.TextInput(label="Title (optional)", required=False, max_length=256)
    body = discord.ui.TextInput(
        label="Message", style=discord.TextStyle.paragraph, max_length=4000,
        placeholder="Markdown works: **bold**, *italic*, lists, links...",
    )

    def __init__(
        self, channel: discord.TextChannel, mention: discord.Role | None, style: Style,
        image: discord.Attachment | None = None, original: discord.Message | None = None,
    ) -> None:
        super().__init__()
        self.channel = channel
        self.mention = mention
        self.style = style
        self.image = image
        self.original = original
        has_image = image is not None
        if original is not None:
            self.title = "Edit announcement"
            if (card := announcement_card(original)) is not None:
                self.heading.default = card.title
                self.body.default = card.description
                self.style = "embed"
                has_image = bool(card.image.url)
            else:
                # The title is part of the text in plain posts, so edit it there.
                self.body.default = original.content
                self.style = "plain"
                has_image = bool(original.attachments)
        if self.style == "plain":
            self.body.max_length = PLAIN_LIMIT
        if has_image:
            # The image alone is enough to post, so the text becomes a caption you can leave out.
            self.body.required = False
            self.body.placeholder = "Optional with an image. Markdown works: **bold**, *italic*, links..."

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title, body = self.heading.value.strip(), self.body.value

        embed: discord.Embed | None = None
        content: str | None = None
        if self.style == "embed":
            embed = discord.Embed(title=title or None, description=body or None, color=discord.Color.gold())
        else:
            content = (f"## {title}\n{body}".rstrip() if title else body) or None
            if content and len(content) > PLAIN_LIMIT:
                await interaction.response.send_message(
                    f"Plain messages are limited to {PLAIN_LIMIT} characters, use `style: embed` for longer ones.",
                    ephemeral=True,
                )
                return

        # Fetching and re-uploading an image can take longer than the 3 seconds Discord waits for a reply.
        await interaction.response.defer(ephemeral=True, thinking=True)

        if self.original is not None:
            if embed is not None:
                content = self.original.content or None  # keep the ping line; edits never re-ping
                # The new card replaces the old one, so carry its picture over. Attachments stay on edits.
                old_card = announcement_card(self.original)
                if old_card is not None and old_card.image.url:
                    attachments = self.original.attachments
                    embed.set_image(
                        url=f"attachment://{attachments[0].filename}" if attachments else old_card.image.url,
                    )
            await self.original.edit(content=content, embed=embed)
            await interaction.followup.send(f"Updated {self.original.jump_url}", ephemeral=True)
            return

        file: discord.File | None = None
        if self.image is not None:
            # Upload it again: the link to the admin's upload expires.
            name = image_filename(self.image.content_type, self.image.filename) or self.image.filename
            file = await self.image.to_file(filename=name)
            if embed is not None:
                embed.set_image(url=f"attachment://{name}")

        if self.mention is not None:
            ping = "@everyone" if self.mention.is_default() else self.mention.mention
            content = f"{ping}\n{content}" if content else ping
        message = await self.channel.send(
            content=content,
            embed=embed,
            file=file,
            # Only ping what the admin explicitly picked, never mentions in the body.
            allowed_mentions=discord.AllowedMentions(
                everyone=self.mention is not None and self.mention.is_default(),
                roles=[self.mention] if self.mention is not None and not self.mention.is_default() else [],
                users=False,
            ),
        )
        log.info("%s posted an announcement in #%s", interaction.user, self.channel.name)
        await interaction.followup.send(f"Posted {message.jump_url}", ephemeral=True)

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
        image="Image to post with it, shown as the card's picture in embed style",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def announce(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
        mention: discord.Role | None = None, style: Style = "plain", image: discord.Attachment | None = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Pick a text channel to post in.", ephemeral=True)
            return
        if image is not None:
            # Checked before the form opens, so nobody types out a message that can't be posted.
            problem = image_problem(image.content_type, image.filename, image.size, target.guild.filesize_limit)
            if problem is not None:
                await interaction.response.send_message(problem, ephemeral=True)
                return
        perms = target.permissions_for(target.guild.me)
        needed = {"Send Messages": perms.send_messages}
        if style == "embed":
            needed["Embed Links"] = perms.embed_links
        if image is not None:
            needed["Attach Files"] = perms.attach_files
        missing = [name for name, ok in needed.items() if not ok]
        if missing:
            listed = " and ".join(f"*{name}*" for name in missing)
            await interaction.response.send_message(f"I need {listed} in {target.mention}.", ephemeral=True)
            return
        await interaction.response.send_modal(AnnouncementModal(target, mention, style, image))

    async def edit_announcement(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if message.author.id != self.bot.user.id or not isinstance(message.channel, discord.TextChannel):
            await interaction.response.send_message("I can only edit messages I posted.", ephemeral=True)
            return
        # The style is taken from the original message.
        await interaction.response.send_modal(AnnouncementModal(message.channel, None, "plain", original=message))


async def setup(bot: ValheimBot) -> None:
    await bot.add_cog(AnnounceCog(bot))
