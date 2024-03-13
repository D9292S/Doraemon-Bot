import random
import typing
from datetime import datetime

from discord.ext import commands


class Administration(commands.Cog):
    """Commands for bot administration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping", brief="Check the bot's latency.")
    async def ping(self, ctx: commands.Context):
        """
        Retrieves the current latency of the bot.

        This command provides information about the time it takes for the bot to
        respond, measured in milliseconds.
        """
        latency_in_ms = round(self.bot.latency * 1000)
        await ctx.send(f'Pong! Latency: {latency_in_ms}ms')




async def setup(bot: commands.Bot):
    await bot.add_cog(Administration(bot))
