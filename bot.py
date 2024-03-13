import logging

import aiohttp
import discord
import structlog
import typing
from discord.ext import commands
from expiringdict import ExpiringDict
from typing import Any
from pythonjsonlogger import jsonlogger

import helpers

import cogs
import coloredlogs

DEFAULT_DISABLED_MESSAGE = (
    "The bot's currently disabled. It may be refreshing for some quick updates, or down for another reason. "
    "Try again later and check the #bot-outages channel in the official server for more details."
)

async def determine_prefix(bot, message):
    # Default prefix
    default_prefix = 'd!'

    # Mention prefixes
    mention_prefixes = [f"<@{bot.user.id}>", f"<@!{bot.user.id}>"]

    # Allow the bot's assigned role as prefix if possible
    if (guild := message.guild) and (role := guild.self_role):
        mention_prefixes.append(role.mention)

    # Combine default prefix and mention prefixes
    prefixes = [default_prefix] + mention_prefixes

    return commands.when_mentioned_or(*prefixes)(bot, message)


class ClusterBot(commands.AutoShardedBot):
    class BlueEmbed(discord.Embed):
        def __init__(self, **kwargs):
            color = kwargs.pop("color", helpers.constants.BLUE)
            super().__init__(**kwargs, color=color)

    class Embed(discord.Embed):
        def __init__(self, **kwargs):
            color = kwargs.pop("color", helpers.constants.PINK)
            super().__init__(**kwargs, color=color)

    def __init__(self, **kwargs):
        self.cluster_name = kwargs.pop("cluster_name")
        self.cluster_idx = kwargs.pop("cluster_idx")
        self.config = kwargs.pop("config", None)
        if self.config is None:
            self.config = __import__("config")

        self.menus = ExpiringDict(max_len=300, max_age_seconds=300)

        self.http_session = None

        super().__init__(**kwargs, command_prefix=determine_prefix, strip_after_prefix=True)

        self.add_check(
            commands.bot_has_permissions(
                read_messages=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
                add_reactions=True,
                external_emojis=True,
            ).predicate
        )

        self.activity = discord.Game("Poke Legends Help")

        # Run bot

        self.setup_logging()
        self.run(kwargs["token"], log_handler=None)

    def setup_logging(self):
        # Initialize structlog logger
        self.log: structlog.BoundLogger = structlog.get_logger()

        # Custom processor to add cluster name to log entries
        def add_cluster_name(logger, name, event_dict):
            event_dict["cluster"] = self.cluster_name
            return event_dict

        # Configure structlog
        timestamper = structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S")
        shared_processors = [structlog.stdlib.add_log_level, timestamper, add_cluster_name]

        structlog.configure(
            processors=[
                *shared_processors,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # Create a formatter for structlog
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer() if self.config.DEBUG else structlog.processors.JSONRenderer(),
            ],
        )

        # Set up a stream handler for logging
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger = logging.getLogger()

        # Add the handler to the root logger
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

        # Install coloredlogs for additional log styling
        coloredlogs.install(
            level=logging.INFO,  # Set the log level to INFO
            fmt="%(asctime)s - %(levelname)s - %(message)s",  # Customize log format
            field_styles={
                "asctime": {"color": "green"},  # Colorize timestamp in green
                "levelname": {"bold": True, "color": "black", "background": "yellow"},  # Colorize log level
            },
        )


    async def is_owner(self, user):
        """
        Check if a user is the owner of the bot.

        :param user: The user to check for ownership.
        :return: True if the user is an owner, False otherwise.
        """
        if isinstance(user, discord.Member):
            # Check if the user has a role with the specified role ID (600502738572279838)
            owner_role = discord.utils.get(user.roles, id=600502738572279838)
            if owner_role:
                return True

        # If not, defer to the default is_owner method
        return await super().is_owner(user)

    def localized_embed(
        self,
        message_id: str,
        *,
        field_values: dict[str, Any] = {},
        droppable_fields: list[str] = [],
        ignored_fields: list[str] = [],
        field_ordering: list[str] = [],
        block_fields: list[str] | bool = False,
        **kwargs: Any,
    ) -> discord.Embed:

        # Create an error embed for fallback
        error_title = self._("localization-error")
        error_embed = discord.Embed(color=discord.Color.red(), title=error_title)

        # Get the localized message from the language module
        result = self.lang.get_message(message_id)
        if not result:
            self.log.error("no such message id", message_id=message_id)
            return error_embed

        msg, bundle = result
        attributes = msg.attributes

        # Create a base Discord embed
        embed = discord.Embed()

        # Set passthrough fields (fields without attributes)
        PASSTHROUGH_FIELDS = ("title", "description", "url", "footer-text")
        for field in PASSTHROUGH_FIELDS:
            if field in attributes:
                val, errors = bundle.format_pattern(attributes[field], kwargs)
                if errors:
                    self.log.error(
                        "failed to format passthrough field for localized embed",
                        message_id=message_id,
                        field=field,
                        errors=errors,
                    )
                    return error_embed
                if field == "footer-text":
                    embed.set_footer(text=val)
                else:
                    setattr(embed, field, val)

        def format_field_attribute(*, field: str, key: str) -> str | None:
            key = f"field-{field}-{key}"

            try:
                title_message = attributes[key]
            except KeyError:
                return None

            val, errors = bundle.format_pattern(title_message, kwargs)

            if errors:
                return None
            return val


        def extract_field_name(fluent_attribute: str) -> str:
            return fluent_attribute[fluent_attribute.find("-") + 1 : fluent_attribute.rfind("-")]

        discovered_field_names = {
            name
            for key in attributes
            if key.startswith("field-") and (name := extract_field_name(key)) not in ignored_fields
        }
        if field_ordering:
            discovered_field_names = sorted(
                discovered_field_names, key=lambda field_name: field_ordering.index(field_name)
            )

        for field_name in discovered_field_names:
            name = format_field_attribute(field=field_name, key="name")


        # Set fields with attributes
        for key, name_key, value_key in [("field", "name", "value")]:
            field_key = f"{key}-{field_name}-{name_key}"
            value_key = f"{key}-{field_name}-{value_key}"

            field_name = extract_field_name(field_key)
            if field_name in ignored_fields:
                continue

            # Format the name and value attributes
            name = format_field_attribute(field=field_name, key=name_key)
            value = field_values.get(field_name) or format_field_attribute(field=field_name, key=value_key)

            if name and value:
                is_inline = not block_fields if isinstance(block_fields, bool) else field_name not in block_fields
                embed.add_field(name=name, value=value, inline=is_inline)
            elif field_name not in droppable_fields:
                self.log.error(
                    "failed to format field attribute, and it isn't droppable",
                    message_id=message_id,
                    field_name=field_name,
                )
                return error_embed

        return embed



    async def send_dm(self, user, message_content, *args, **kwargs):
        """
        Send a direct message to a user on Discord.

        :param user: Either a user ID (Snowflake) or an instance of discord.abc.Snowflake.
        :param message_content: The content of the direct message.
        :param args: Additional positional arguments to pass to dm.send.
        :param kwargs: Additional keyword arguments to pass to dm.send.
        :return: The message that was sent.
        """
        # Ensure the user parameter is a Snowflake
        if not isinstance(user, discord.abc.Snowflake):
            user = discord.Object(user)

        try:
            # Create a direct message channel with the specified user
            dm = await self.create_dm(user)

            # Send the direct message with the provided content and optional arguments
            sent_message = await dm.send(message_content, *args, **kwargs)

            # Return the sent message
            return sent_message
        except Exception as e:
            # Handle any exceptions that may occur during the DM sending process
            print(f"Failed to send DM to user {user.id}: {e}")
            return None

    async def setup_hook(self):
        # Create a shared aiohttp ClientSession
        self.http_session = aiohttp.ClientSession()

        # Load the 'jishaku' extension for debugging
        await self.load_extension("jishaku")
        self.log.info("Loaded extension: jishaku")

        # Load default cogs
        for cog_name in cogs.default:
            await self.load_extension(f"cogs.{cog_name}")   
            self.log.info(f"Loaded extension: cogs.{cog_name}") 

    async def on_ready(self):
        self.log.info("Bot is ready and running!")

    async def on_disconnect(self):
        # Close the HTTP session on disconnect
        if self.http_session:
            await self.http_session.close()

    async def on_shard_ready(self, shard_id):
        self.log.info(f"Bot initialized with shard_ids={self.shard_ids}, shard_count={self.shard_count}")

    async def on_message(self, message: discord.Message):
        # Replace certain characters in the message content
        replacements = {"—": "--", "'": "′", "‘": "′", "’": "′"}
        for original, replacement in replacements.items():
            message.content = message.content.replace(original, replacement)

        # Continue processing commands after preprocessing
        await self.process_commands(message)

    async def close(self):
        self.log.info("close")
        await super().close()
