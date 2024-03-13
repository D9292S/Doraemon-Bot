import os
import re
from collections import namedtuple
import discord
import discord.gateway
import discord.http
import yarl
import bot

from dotenv import load_dotenv

load_dotenv()

# Define a named tuple for configuration settings
Config = namedtuple(
    "Config",
    [
        "DEBUG",
        "BOT_TOKEN",
        "DBL_TOKEN",
        "SERVER_URL",
        "EXT_SERVER_URL",
        "ASSETS_BASE_URL",
    ],
)

def patch_with_gateway(env_gateway):
    # Patching Discord.py classes to customize gateway behavior
    class ProductionHTTPClient(discord.http.HTTPClient):
        @staticmethod
        async def get_gateway(**_):
            return f"{env_gateway}?encoding=json&v=9"

        async def get_bot_gateway(self, **_):
            try:
                data = await self.request(discord.http.Route("GET", "/gateway/bot"))
            except discord.HTTPException as exc:
                raise discord.GatewayNotFound() from exc
            return data["shards"], f"{env_gateway}?encoding=json&v=9"

    class ProductionDiscordWebSocket(discord.gateway.DiscordWebSocket):
        DEFAULT_GATEWAY = yarl.URL(env_gateway)

        @staticmethod
        def is_ratelimited():
            return False

    class ProductionBot(bot.ClusterBot):
        async def before_identify_hook(self, shard_id, *, initial):
            pass

        @staticmethod
        def is_ws_ratelimited():
            return False

    class ProductionReconnectWebSocket(Exception):
        def __init__(self, shard_id, *, resume=False):
            self.shard_id = shard_id
            self.resume = False
            self.op = "IDENTIFY"

    # Applying the patches
    discord.http.HTTPClient.get_gateway = ProductionHTTPClient.get_gateway
    discord.http.HTTPClient.get_bot_gateway = ProductionHTTPClient.get_bot_gateway
    discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY = ProductionDiscordWebSocket.DEFAULT_GATEWAY
    discord.gateway.DiscordWebSocket.is_ratelimited = ProductionDiscordWebSocket.is_ratelimited
    discord.gateway.ReconnectWebSocket.__init__ = ProductionReconnectWebSocket.__init__
    bot.ClusterBot = ProductionBot

if __name__ == "__main__":
    # Configuring the Discord.py HTTP Route base URL if provided
    if os.getenv("API_BASE") is not None:
        discord.http.Route.BASE = os.getenv("API_BASE")

    # Applying custom gateway if provided
    if os.getenv("API_GATEWAY") is not None:
        patch_with_gateway(os.getenv("API_GATEWAY"))

    # Loading configuration from environment variables
    config = Config(
        DEBUG=os.getenv("DEBUG") in ("1", "True", "true"),
        BOT_TOKEN=os.environ["BOT_TOKEN"],
        DBL_TOKEN=os.getenv("DBL_TOKEN"),
        SERVER_URL=os.environ["SERVER_URL"],
        EXT_SERVER_URL=os.getenv("EXT_SERVER_URL", os.environ["SERVER_URL"]),
        ASSETS_BASE_URL=os.getenv("ASSETS_BASE_URL"),
    )

    # Parsing cluster-related environment variables
    num_shards = int(os.getenv("NUM_SHARDS", 1))
    num_clusters = int(os.getenv("NUM_CLUSTERS", 1))
    cluster_name = os.getenv("CLUSTER_NAME", str(os.getenv("CLUSTER_IDX", 0)))
    cluster_idx = int(re.search(r"\d+", cluster_name).group(0))

    # Generating shard IDs for the current cluster
    shard_ids = list(range(cluster_idx, num_shards, num_clusters))

    # Configuring Discord.py intents
    intents = discord.Intents.all()


    # Creating and starting the bot instance
    bot.ClusterBot(
        token=config.BOT_TOKEN,
        shard_ids=shard_ids,
        shard_count=num_shards,
        cluster_name=str(cluster_idx),
        cluster_idx=cluster_idx,
        case_insensitive=True,
        member_cache_flags=discord.MemberCacheFlags.none(),
        allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        intents=intents,
        config=config,
    )
