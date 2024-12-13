import discord, os
from dotenv import load_dotenv
from discord.ext import commands
import asyncpg
from pylast import LastFMNetwork, SessionKeyGenerator, WSError, PERIOD_1MONTH
import asyncio, aiohttp, logging, pycountry, random
from cryptography.fernet import Fernet
from datetime import datetime
import google.generativeai as genai

async def get_country(artist):
    url = f"https://musicbrainz.org/ws/2/artist?query={artist}&fmt=json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            res = (await r.json())["artists"][0]["area"]  # Possible misidentifying an artist here
            if res["type"] == "Country":
                return res["name"]
            # Annoying part starts (if not country)
            area_id = res["id"]
            url2 = f"https://musicbrainz.org/ws/2/area/{area_id}?fmt=json"
            async with aiohttp.ClientSession() as session2:
                async with session2.get(url2) as r2:
                    ccode = (await r2.json())["iso-3166-2-codes"][0][:2]  # KR-11 -> KR
                    country = pycountry.countries.get(alpha_2=ccode)
                    return country.common_name  # South Korea

async def track_to_tup(track):
    playback_date = track.playback_date
    dt_object = datetime.strptime(playback_date, "%d %b %Y, %H:%M")
    artist = track.track.artist.name
    try:
        genre = track.track.artist.get_top_tags()[0].item.name # Possible that top tag is not genre but rare
    except:
        genre = "Unknown"
    country = await get_country(artist)
    return (
        track.track.title,
        artist,
        track.album,
        dt_object,
        genre, 
        country
    )

async def fetch_tracks(user, ctx):
    def get_recent():
        return user.get_recent_tracks(limit=10)
    logging.info("Fetching tracks")
    try:
        try:
            async with bot.pool.acquire() as conn:
                await conn.execute(f"CREATE TABLE u{ctx.author.id} (TRACK TEXT, ARTIST TEXT, ALBUM TEXT, AT TIMESTAMP, GENRE VARCHAR(255), COUNTRY VARCHAR(255))")
        except:
            async with bot.pool.acquire() as conn:
                await conn.execute(f"DELETE FROM u{ctx.author.id}")
        tracks = await asyncio.to_thread(get_recent)
        trackobjs = []
        for track in tracks:
            try:
                t = await track_to_tup(track)
                trackobjs.append(t)    
            except Exception as e:
                logging.error(f"Error processing track {track}: {str(e)}")
            
        async with bot.pool.acquire() as conn:
            await conn.copy_records_to_table("u"+str(ctx.author.id), records=trackobjs)

        logging.info("Done fetching")
    except Exception as e:
        logging.error(f"Error in fetch_tracks: {e}")

def encrypt_sk(sk):
    load_dotenv()
    fern = Fernet(os.getenv("FERNET").encode())
    return fern.encrypt(sk.encode()).decode()

def decrypt_sk(sk):
    load_dotenv()
    fern = Fernet(os.getenv("FERNET").encode())
    return fern.decrypt(sk.encode()).decode()

async def send_sk(username, id, sk):
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO SESSIONS VALUES ($1, $2, $3)", username, id, encrypt_sk(sk))
        bot.sessions[id] = [username, sk]

def authenticate(session_data):
    return LastFMNetwork(API, LFS, decrypt_sk(session_data[1]), session_data[0]).get_authenticated_user()

async def background_update(id):
    # Fetch the last 5 tracks and add them to table, if date matches, finish, else if all 5 are new, grab 10 more
    # Possible optimization: Is there a way to get middle 10 tracks instead of first 15? Or get by date?
    finished = False
    i = 5
    while not finished:
        session_data = bot.sessions[id]
        user = authenticate(session_data)
        tracks = user.get_recent_tracks(time_from=) # NOTE: fetch the latest time from u{id} db (sort by AT because it's gonna be random order now) and use that in time_from
        async with bot.pool.acquire() as conn:
            recent = await conn.fetchone(f"SELECT * FROM u{id}")
            new_tracks = []

            for track in tracks:
                if track.playback_date == recent[3]:
                    finished = True
                else:
                    new_tracks.append(track_to_tup(track))

            await conn.copy_records_to_table("u"+str(id), records=new_tracks)
            i += 10
        
            
    
    

#---------------------------------------------------------------------

load_dotenv()
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=",", intents=intents)
API = os.getenv("API")
LFS = os.getenv("LFS")
genai.configure(api_key=os.getenv("GEM"))
model = genai.GenerativeModel("gemini-2.0-flash-exp")

@bot.event
async def on_ready():
    bot.sessions = {}
    bot.pool = await asyncpg.create_pool(
        database="lfm",
        host="localhost",
        password=os.getenv("PGP"),
        port="5432"
    )
    async with bot.pool.acquire() as conn:
        sessions = await conn.fetch("SELECT * FROM SESSIONS;")
        for session in sessions:
            bot.sessions[session[1]] = [session[0], session[2]] # sessions[userid] = [lastfm username, their session key]

@bot.event
async def on_disconnect():
    if bot.conn:
        await bot.pool.release(bot.conn)
    await bot.pool.close()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content[0] != ",":
        return
    
    try:
        await background_update(message.author.id)
    except:
        pass # User does not have account and sends a message starting with ","

    await bot.process_commands()

#---------------------------------------------------------------------------

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! Latency is `{round(bot.latency*1000, 2)}ms`.")

@bot.command()
async def countries(ctx):
    async with bot.pool.acquire() as conn:
        countries = await conn.fetch(f"SELECT COUNTRY, COUNT(*) FROM u{ctx.author.id} GROUP BY COUNTRY ORDER BY COUNT(*) DESC")

    s = ""
    for c in countries:
        s += c[0] + " - " + str(c[1]) + "\n"
        # United States - 1000

    e = discord.Embed(
        title="Country list",
        description=s,
    )

    await ctx.send(embed=e)

@bot.command()
async def genres(ctx):
    async with bot.pool.acquire() as conn:
        genres = await conn.fetch(f"SELECT GENRE, COUNT(*) FROM u{ctx.author.id} GROUP BY GENRE ORDER BY COUNT(*) DESC")

    s = ""
    for c in genres:
        s += c[0] + " - " + str(c[1]) + "\n"
        # Rock - 1000

    e = discord.Embed(
        title="Genre List",
        description=s,
    )

    await ctx.send(embed=e)

@bot.command()
async def connect(ctx, username=None):
    if not username:
        await ctx.send("Enter your last.fm username: `,connect <username>`")
        return
    
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅"] and reaction.message == m
    e = discord.Embed(
        title="Hey there!",
        description="""
Before using SCWN, you need to agree to the following:

1. Your Last.fm data will be downloaded and kept in a database so you can check some cool stats. (You can delete this anytime)
2.This bot is better than fmbot.
"""
    )
    m = await ctx.send(embed=e)
    await m.add_reaction("✅")
    c = await bot.wait_for("reaction_add", check=check)
    
    network = LastFMNetwork(API, LFS, username=username)
    skg = SessionKeyGenerator(network)
    auth_url = skg.get_web_auth_url()

    if c:
        await ctx.author.send(f"[Click this link]({auth_url}) to connect your Last.fm account to SCWN.")
        await ctx.send("Check your DMs!")

    while True:
        try:
            session_key = await asyncio.to_thread(skg.get_web_auth_session_key, auth_url)
            break
        except WSError:
            await asyncio.sleep(1)
    
    await ctx.author.send("You have been authenticated. We're fetching your tracks right now. It will take a while, but we'll message you when it's done.")
    network.session_key = session_key
    user = network.get_authenticated_user()

    try:
        tasks = [
            asyncio.create_task(send_sk(user.name, ctx.author.id, session_key)),
            asyncio.create_task(fetch_tracks(user, ctx))
        ]
        
        await asyncio.gather(*tasks)
        await ctx.author.send("Your tracks have been fetched.")
    except Exception as e:
        logging.error(f"Error in callback: {e}")    

@bot.command()
async def recent(ctx):
    async with bot.pool.acquire() as conn:
        tracks = await conn.fetch(f"SELECT * FROM u{ctx.author.id}")
    s = ""
    for track in tracks:
        # TRACK ARTIST ALBUM AT GENRE COUNTRY
        s += track[0] + " - " + track[1] + "\n"
    e = discord.Embed(
        title="Your Recent Tracks",
        description=s
    )
    await ctx.send(embed=e)

@bot.command()
async def roast(ctx):
    message = await ctx.send("Generating Roast...")
    session_data = bot.sessions[ctx.author.id]
    user = authenticate(session_data)
    top_artists = random.sample(list(map(lambda x: x.item.name, user.get_top_artists(period=PERIOD_1MONTH, limit=20))), 3)
    response = model.generate_content(f"Give me 5 paragraphs roasting the music taste of someone who likes {', '.join(top_artists)}.")
    e = discord.Embed(
        title="Your Roast",
        description=response.text
    )
    await message.edit(content="", embed=e)

@bot.command()
async def praise(ctx):
    message = await ctx.send("Generating Praise...")
    session_data = bot.sessions[ctx.author.id]
    user = authenticate(session_data)
    top_artists = random.sample(list(map(lambda x: x.item.name, user.get_top_artists(period=PERIOD_1MONTH, limit=20))), 3)
    response = model.generate_content(f"Give me 5 paragraphs praising the music taste of someone who likes {', '.join(top_artists)}.")
    e = discord.Embed(
        title="Your Praise",
        description=response.text
    )
    await message.edit(content="", embed=e)


bot.run(os.getenv("BOT"))