import discord, os
from dotenv import load_dotenv
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=",", intents=intents)

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! Latency is `{round(bot.latency*1000, 2)}ms`.")

@bot.command()
async def connect(ctx):
    e = discord.Embed(
        title="Hey there!",
        description="""
Before using SCWN, you need to agree to the following:
Your Last.fm data will be downloaded and kept in a database so you can check some cool stats.

        """
    )
    await ctx.send(embed=e)

load_dotenv()
bot.run(os.getenv("BOT"))