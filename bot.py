import discord, os
from dotenv import load_dotenv
from discord.ext import commands
import asyncpg
from pylast import LastFMNetwork

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=",", intents=intents)

@bot.event
async def on_ready():
    bot.pool = await asyncpg.create_pool(
        database="lfm",
        host="localhost",
        password=os.getenv("PGP"),
        port="5432"
    )
    bot.conn = bot.pool.acquire()

@bot.event
async def on_disconnect():
    if bot.conn:
        await bot.pool.release(bot.conn)
    await bot.pool.close()

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! Latency is `{round(bot.latency*1000, 2)}ms`.")

@bot.command()
async def connect(ctx):
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅"] and reaction.message == m
    e = discord.Embed(
        title="Hey there!",
        description="""
Before using SCWN, you need to agree to the following:
Your Last.fm data will be downloaded and kept in a database so you can check some cool stats. (You can delete this anytime)
This bot is better than fmbot.
"""
    )
    m = await ctx.send(embed=e)
    await m.add_reaction("✅")
    c = await bot.wait_for("reaction_add", check=check)
    if c:
        await ctx.author.send("Link")
        await ctx.send("Check your DMs!")

@bot.command()
async def country(ctx):
    lfm = bot.conn.fetch("SELECT LASTFM FROM SESSIONS WHERE DISCORD = $1", ctx.author)
    countries = bot.conn.fetch(f"SELECT COUNTRY, COUNT(*) FROM {lfm} GROUP BY COUNTRY ORDER BY COUNT(*) DESC;")
    s = ""
    for c in countries:
        s += c[0] + " - " + str(c[1]) + "\n"
        # United States - 1000

    e = discord.Embed(
        title="Country list",
        description=s,
    )

    await ctx.send(embed=e)

load_dotenv()
bot.run(os.getenv("BOT"))