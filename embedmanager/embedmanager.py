from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel
from core.paginator import EmbedPaginatorSession
from core.utils import human_join

from .core.builder import EmbedBuilderView
from .core.converters import (
    MessageableChannel,
    BotMessage,
    StoredEmbedConverter,
    StringToEmbed,
)
from .core.data import JSON_EXAMPLE


if TYPE_CHECKING:
    from .motor.motor_asyncio import AsyncIOMotorCollection
    from bot import ModmailBot

info_json = Path(__file__).parent.resolve() / "info.json"
with open(info_json, encoding="utf-8") as f:
    __plugin_info__ = json.loads(f.read())

__plugin_name__ = __plugin_info__["name"]
__version__ = __plugin_info__["version"]
__description__ = "\n".join(__plugin_info__["description"]).format(__version__)


# <!-- Developer -->
try:
    from discord.ext.modmail_utils import inline, paginate
except ImportError as exc:
    required = __plugin_info__["cogs_required"][0]
    raise RuntimeError(
        f"`modmail_utils` package is required for {__plugin_name__} plugin to function.\n"
        f"Install {required} plugin to resolve this issue."
    ) from exc


# <!-- ----- -->


JSON_CONVERTER = StringToEmbed()
JSON_CONTENT_CONVERTER = StringToEmbed(content=True)


YES_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
NO_EMOJI = "\N{CROSS MARK}"


class EmbedManager(commands.Cog, name=__plugin_name__):
    __doc__ = __description__

    _id = "config"
    default_config = {"embeds": {}}

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.db: AsyncIOMotorCollection = bot.api.get_plugin_partition(self)

    async def db_config(self) -> Dict:
        # No need to store in cache when initializing the plugin.
        # Only fetch from db when needed.
        config = await self.db.find_one({"_id": self._id})
        if config is None:
            config = {k: v for k, v in self.default_config.items()}
        return config

    async def update_db(self, data: dict):
        await self.db.find_one_and_update(
            {"_id": self._id},
            {"$set": data},
            upsert=True,
        )

    @staticmethod
    async def get_embed_from_message(message: discord.Message, index: int = 0):
        embeds = message.embeds
        if not embeds:
            raise commands.BadArgument("That message has no embeds.")
        index = max(min(index, len(embeds)), 0)
        embed = message.embeds[index]
        if embed.type == "rich":
            return embed
        raise commands.BadArgument("That is not a rich embed.")

    @staticmethod
    async def get_file_from_message(ctx: commands.Context, *, file_types=("json", "txt")) -> str:
        if not ctx.message.attachments:
            raise commands.BadArgument(
                f"Run `{ctx.bot.prefix}{ctx.command.qualified_name}` again, but this time attach an embed file."
            )
        attachment = ctx.message.attachments[0]
        if not any(attachment.filename.endswith("." + ft) for ft in file_types):
            raise commands.BadArgument(
                f"Invalid file type. The file name must end with one of {human_join([inline(ft) for ft in file_types])}."
            )

        content = await attachment.read()
        try:
            data = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise commands.BadArgument("Failed to read embed file contents.") from exc
        return data

    @commands.group(name="embed", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_group(self, ctx: commands.Context):
        """
        Base command for Embed Manager.

        __**Note:**__
        The JSON must be in the format expected by this [Discord documentation](https://discord.com/developers/docs/resources/channel#embed-object).
        - Use command `{prefix}embed example` to see a JSON example.
        """
        await ctx.send_help(ctx.command)

    @embed_group.command(name="example")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_example(self, ctx: commands.Context):
        """
        Show an example of embed in JSON.
        """
        embed = discord.Embed(color=self.bot.main_color, title="JSON Example")
        embed.description = f"```py\n{JSON_EXAMPLE}\n```"
        await ctx.send(embed=embed)

    @embed_group.command(name="build")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_build(self, ctx: commands.Context):
        """
        Build embeds in an interactive mode using buttons and modal view.
        """
        description = "Select the category and press the button below respectively to start creating/editing your embed."
        embed = discord.Embed(
            title="Embed Builder Panel",
            description=description,
            color=self.bot.main_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="This panel will time out after 10 minutes.")
        view = EmbedBuilderView(self, ctx.author)
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.embed:
            await ctx.send(embed=view.embed)

    @embed_group.command(name="simple")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_simple(
        self,
        ctx: commands.Context,
        channel: Optional[MessageableChannel],
        color: Optional[discord.Color],
        title: str,
        *,
        description: str,
    ):
        """
        Post a simple embed.

        Put the title in quotes if it is multiple words.
        """
        channel = channel or ctx.channel
        color = color or self.bot.main_color
        embed = discord.Embed(color=color, title=title, description=description)
        await channel.send(embed=embed)

    @embed_group.command(name="json", aliases=["fromjson", "fromdata"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_json(self, ctx: commands.Context, *, data: JSON_CONTENT_CONVERTER):
        """
        Post an embed from valid JSON.
        """
        embed = data
        await ctx.send(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_group.command(name="fromfile", aliases=["fromjsonfile", "fromdatafile"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_fromfile(self, ctx: commands.Context):
        """
        Post an embed from a valid JSON file.
        """
        data = await self.get_file_from_message(ctx, file_types=("json", "txt"))
        embed = await JSON_CONTENT_CONVERTER.convert(ctx, data)
        await ctx.send(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_group.command(name="message", aliases=["frommsg", "frommessage"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_message(self, ctx: commands.Context, message: discord.Message, index: int = 0):
        """
        Post an embed from a message.

        `message` may be a message ID, message link, or format of `channelid-messageid` of the embed.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(message, index)
        await ctx.send(embed=embed)

    @embed_group.command(name="download")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_download(self, ctx: commands.Context, message: discord.Message, index: int = 0):
        """
        Download a JSON file from a message's embed.

        `message` may be a message ID, message link, or format of `channelid-messageid` of the embed.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(message, index)
        data = embed.to_dict()
        data = json.dumps(data, indent=4)
        fp = io.BytesIO(bytes(data, "utf-8"))
        await ctx.send(file=discord.File(fp, "embed.json"))

    @embed_group.command(name="post", aliases=["view", "drop", "show"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_post(
        self,
        ctx: commands.Context,
        name: StoredEmbedConverter,
        channel: MessageableChannel = None,
    ):
        """
        Post a stored embed.

        `name` must be a name that was used when storing the embed.
        `channel` may be a channel name, ID, or mention.

        Use command `{prefix}embed store list` to get the list of stored embeds.
        """
        channel = channel or ctx.channel
        await channel.send(embed=discord.Embed.from_dict(name["embed"]))

    @embed_group.command(name="info")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_info(self, ctx: commands.Context, name: StoredEmbedConverter):
        """
        Get info about an embed that is stored.

        `name` must be a name that was used when storing the embed.

        Use command `{prefix}embed store list` to get the list of stored embeds.
        """
        embed = discord.Embed(
            title=f"`{name['name']}` Info",
            description=(
                f"Author: <@!{name['author']}>\n" f"Length: {len(discord.Embed.from_dict(name['embed']))}"
            ),
        )
        await ctx.send(embed=embed)

    @embed_group.group(name="edit", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit(self, ctx: commands.Context, message: BotMessage, index: int = 0):
        """
        Edit a message's embed sent by the bot.
        This will initiate the Embed Editor panel with interactive buttons and text inputs session.
        The values for the input fields are pre-defined based on the source embed.

        `message` may be a message ID, message link, or format of `channelid-messageid` of the bot's embed.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        source_embed = await self.get_embed_from_message(message, index)
        view = EmbedBuilderView.from_embed(self, ctx.author, embed=source_embed)
        description = "Select the category and press the button below respectively to start creating/editing your embed."
        embed = discord.Embed(
            title="Embed Editor",
            description=description,
            color=self.bot.main_color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="This panel will time out after 10 minutes.")
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()

        if view.embed:
            await message.edit(embed=view.embed)
            await ctx.message.add_reaction(YES_EMOJI)

    @embed_edit.command(name="json", aliases=["fromjson", "fromdata"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit_json(self, ctx: commands.Context, message: BotMessage, *, data: JSON_CONVERTER):
        """
        Edit a message's embed using valid JSON.

        `message` may be a message ID, message link, or format of `channelid-messageid` of the bot's embed.
        """
        await message.edit(embed=data)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_edit.command(name="fromfile", aliases=["fromjsonfile", "fromdatafile"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit_fromfile(self, ctx: commands.Context, message: BotMessage):
        """
        Edit a message's embed using a valid JSON file.

        `message` may be a message ID, message link, or format of `channelid-messageid` of the bot's embed.
        """
        data = await self.get_file_from_message(ctx, file_types=("json", "txt"))
        embed = await JSON_CONVERTER.convert(ctx, data)
        await message.edit(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_edit.command(name="message", aliases=["frommsg", "frommessage"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_edit_message(
        self,
        ctx: commands.Context,
        source: discord.Message,
        target: BotMessage,
        index: int = 0,
    ):
        """
        Edit a message's embed using another message's embed.

        `source` may be a message ID, message link, or format of `channelid-messageid` of the source embed.
        `target` may be a message ID, message link, or format of `channelid-messageid` of the bot's embed you want to edit.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(source, index)
        await target.edit(embed=embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_group.group(name="store", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store(self, ctx: commands.Context):
        """
        Store commands to store embeds for later use.
        """
        await ctx.send_help(ctx.command)

    @embed_store.command(name="simple")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_simple(
        self,
        ctx: commands.Context,
        name: str,
        color: Optional[discord.Color],
        title: str,
        *,
        description: str,
    ):
        """
        Store a simple embed.

        Put the title in quotes if it has multiple words.
        """
        if not color:
            color = self.bot.main_color
        embed = discord.Embed(color=color, title=title, description=description)
        await ctx.send(embed=embed)
        await self.store_embed(ctx, name, embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="json", aliases=["fromjson", "fromdata"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_json(self, ctx: commands.Context, name: str, *, data: JSON_CONVERTER):
        """
        Store an embed from valid JSON.
        """
        await self.store_embed(ctx, name, data)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="fromfile", aliases=["fromjsonfile", "fromdatafile"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_fromfile(self, ctx: commands.Context, name: str):
        """
        Store an embed from a valid JSON file.
        """
        data = await self.get_file_from_message(ctx, file_types=("json", "txt"))
        embed = await JSON_CONVERTER.convert(ctx, data)
        await self.store_embed(ctx, name, embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="message", aliases=["frommsg", "frommessage"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_message(
        self, ctx: commands.Context, name: str, message: discord.Message, index: int = 0
    ):
        """
        Store an embed from a message.

        `message` may be a message ID, message link, or format of `channelid-messageid` of the embed you want to store.

        __**Note:**__
        If the message has multiple embeds, you can pass a number to `index` to specify which embed.
        """
        embed = await self.get_embed_from_message(message, index)
        await self.store_embed(ctx, name, embed)
        await ctx.message.add_reaction(YES_EMOJI)

    @embed_store.command(name="remove", aliases=["delete", "rm", "del"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_remove(self, ctx: commands.Context, name: str):
        """
        Remove a stored embed.
        """
        db_config = await self.db_config()
        embeds = db_config.get("embeds", {})
        try:
            del embeds[name]
        except KeyError:
            await ctx.send("This is not a stored embed.")
        else:
            await self.update_db(db_config)
            await ctx.send(f"Embed `{name}` is now deleted.")

    @embed_store.command(name="download")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_download(self, ctx: commands.Context, embed: StoredEmbedConverter):
        """
        Download a JSON file from a stored embed.
        """
        data = json.dumps(embed["embed"], indent=4)
        fp = io.BytesIO(bytes(data, "utf-8"))
        await ctx.send(file=discord.File(fp, "embed.json"))

    @embed_store.command(name="list")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def embed_store_list(self, ctx: commands.Context):
        """
        View stored embeds.
        """
        db_config = await self.db_config()
        _embeds = db_config.get("embeds")
        if not _embeds:
            raise commands.BadArgument("There are no stored embeds.")

        description = [f"{index}. `{embed}`" for index, embed in enumerate(_embeds, start=1)]
        description = "\n".join(description)

        color = self.bot.main_color
        em = discord.Embed(color=color, title="Stored Embeds")

        if len(description) > 2048:
            embeds = []
            pages = list(paginate(description, page_length=1024))
            for page in pages:
                embed = em.copy()
                embed.description = page
                embeds.append(embed)
            session = EmbedPaginatorSession(ctx, *embeds)
            await session.run()
        else:
            em.description = description
            await ctx.send(embed=em)

    async def store_embed(self, ctx: commands.Context, name: str, embed: discord.Embed):
        embed = embed.to_dict()
        db_config = await self.db_config()
        embeds = db_config.get("embeds", {})
        embeds[name] = {"author": ctx.author.id, "embed": embed, "name": name}
        await self.update_db(db_config)
        await ctx.send(
            f"Embed stored under the name `{name}`. To post this embed, use command:\n"
            f"`{self.bot.prefix}embed post {name}`"
        )

    async def get_stored_embed(self, ctx: commands.Context, name: str):
        db_config = await self.db_config()
        embeds = db_config.get("embeds")
        try:
            data = embeds[name]
            embed = data["embed"]
        except KeyError:
            await ctx.send("This is not a stored embed.")
            return
        embed = discord.Embed.from_dict(embed)
        return embed, data["author"], data["uses"]


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(EmbedManager(bot))
