import discord, os
from dotenv import load_dotenv
from discord.ext import commands
import asyncpg
from pylast import LastFMNetwork, SessionKeyGenerator, WSError, PERIOD_1MONTH
import asyncio, aiohttp, logging, pycountry, random, time, json
from cryptography.fernet import Fernet
from datetime import datetime
import google.generativeai as genai
from hashlib import sha256
from openai import OpenAI

def load_conversation_history():
    if os.path.exists(conversation_history_file):
        with open(conversation_history_file, "r") as file:
            return json.load(file)
    else:
        return [
            {"role": "system", "content": "You are an experienced programmer willing to help out noobs. You explain your work in simple terms but you never put comments in your code."},
        ]

def save_conversation_history():
    with open(conversation_history_file, "w") as file:
        json.dump(conversation_history, file)

async def send_message_to_chatgpt(user_input):
    conversation_history.append({"role": "user", "content": user_input})
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=conversation_history,
    )
    assistant_reply = response.choices[0].message.content
    conversation_history.append({"role": "assistant", "content": assistant_reply})
    save_conversation_history()
    return assistant_reply

def split_message(message, max_length=2000):
    return [message[i:i + max_length] for i in range(0, len(message), max_length)]

async def get_country(artist):
    try:
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
    except:
        return "Unknown"

async def track_to_tup(track):
    playback_date = track.playback_date
    dt_object = datetime.strptime(playback_date, "%d %b %Y, %H:%M")
    artist = track.track.artist.name
    # try:
    #     genre = track.track.artist.get_top_tags()[0].item.name # Possible that top tag is not genre but rare
    # except:
    #     genre = "Unknown"
    genre = "Unknown"
    country = await get_country(artist)
    id = generate_id(track)
    return (
        track.track.title,
        artist,
        track.album,
        dt_object,
        genre, 
        country,
        id
    )

def generate_id(track):
    # Prevent double counting of scrobbles by generating a unique ID
    # If same title and artist name and playback time then it's a double scrobble
    title = track.track.title
    artist = track.track.artist.name
    playback_date = str(datetime.strptime(track.playback_date, "%d %b %Y, %H:%M").timestamp())
    return sha256((title + artist + playback_date).encode()).hexdigest()

async def fetch_tracks_old(user, ctx):
    def get_recent():
        return user.get_recent_tracks(limit=10)
    logging.info("Fetching tracks")
    try:
        try:
            async with bot.pool.acquire() as conn:
                await conn.execute(f"CREATE TABLE u{ctx.author.id} (TRACK TEXT, ARTIST TEXT, ALBUM TEXT, AT TIMESTAMP, GENRE VARCHAR(255), COUNTRY VARCHAR(255), ID CHAR(64) PRIMARY KEY)")
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


async def fetch_tracks(user, ctx):
    async def get_recent_tracks_in_thread(user, time_from, time_to, limit=500):
        return await asyncio.to_thread(user.get_recent_tracks, limit=limit, time_from=time_from, time_to=time_to)

    async def insert_tracks_in_batches(conn, trackobjs, ctx):
        for i in range(0, len(trackobjs), 1000):
            batch = trackobjs[i:i + 1000]
            await conn.copy_records_to_table(f"u{ctx.author.id}", records=batch)

    logging.info("Fetching tracks")
    try:
        try:
            async with bot.pool.acquire() as conn:
                await conn.execute(f"CREATE TABLE u{ctx.author.id} (TRACK TEXT, ARTIST TEXT, ALBUM TEXT, AT TIMESTAMP, GENRE VARCHAR(255), COUNTRY VARCHAR(255), ID CHAR(64) PRIMARY KEY)")
        except:
            async with bot.pool.acquire() as conn:
                await conn.execute(f"DELETE FROM u{ctx.author.id}")

        account_creation_time = user.get_unixtime_registered()
        current_time = int(time.time())

        interval_start = account_creation_time
        interval_end = current_time
        all_tracks = []

        start = time.perf_counter()
        while interval_start < interval_end:
            try:
                tracks = await get_recent_tracks_in_thread(user, interval_start, interval_end)
                if not tracks:
                    break
                
                all_tracks.extend(tracks)
                interval_end = min(int(tracks[-1].timestamp) - 1, interval_end)
                logging.info(f"Fetched {len(tracks)} tracks up to {interval_end}")
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Error fetching tracks for interval {interval_start}-{interval_end}: {str(e)}")
                mid_point = interval_start + (interval_end - interval_start) // 2
                interval_end = mid_point
        logging.info(f"Time taken: {time.perf_counter() - start}")

        trackobjs = []
        for track in all_tracks:
            try:
                t = await track_to_tup(track)
                trackobjs.append(t)
            except Exception as e:
                logging.error(f"Error processing track {track}: {str(e)}")

        async with bot.pool.acquire() as conn:
            async with conn.transaction():
                await insert_tracks_in_batches(conn, trackobjs, ctx)

        logging.info(f"Done fetching and inserting {len(trackobjs)} tracks")
    except Exception as e:
        logging.error(f"Error in fetch_tracks: {str(e)}")

def encrypt_sk(sk):
    return fern.encrypt(sk.encode()).decode()

def decrypt_sk(sk):
    return fern.decrypt(sk.encode()).decode()

async def send_sk(username, id, sk):
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO SESSIONS VALUES ($1, $2, $3)", username, id, encrypt_sk(sk))
        bot.sessions[id] = [username, sk]

def authenticate(session_data):
    return LastFMNetwork(API, LFS, decrypt_sk(session_data[1]), session_data[0]).get_authenticated_user()

async def background_update(id):
    session_data = bot.sessions[id]
    user = authenticate(session_data)
    async with bot.pool.acquire() as conn:
        latest_time = await conn.fetchval(f"SELECT AT FROM u{id} ORDER BY AT DESC LIMIT 1")
        tracks = user.get_recent_tracks(limit=None, time_from=int(latest_time.timestamp()))
        new_tracks = []
        for track in tracks:
            new_tracks.append(await track_to_tup(track))

        # Cannot use copy_records_to_table as it will fail the entire command if any one track fails
        for track in new_tracks:
            try:
                await conn.execute(f"INSERT INTO u{id} VALUES ($1, $2, $3, $4, $5, $6, $7)", *track)
            except Exception as e:
                pass

#---------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler() 
    ]
)
load_dotenv()
fern = Fernet(os.getenv("FERNET"))
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=",", intents=intents)
API = os.getenv("API")
LFS = os.getenv("LFS")
genai.configure(api_key=os.getenv("GEM"))
model = genai.GenerativeModel("gemini-2.0-flash-exp")
openai = OpenAI()
conversation_history_file = "conversation_history.json"
conversation_history = load_conversation_history()

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
    
    await bot.process_commands(message)
    try:
        await background_update(message.author.id)
    except:
        pass # User does not have account and sends a message starting with ","


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
            asyncio.create_task(fetch_tracks_old(user, ctx))
        ]
        
        await asyncio.gather(*tasks)
        await ctx.author.send("Your tracks have been fetched.")
    except Exception as e:
        logging.error(f"Error in callback: {e}")    

@bot.command()
async def recent(ctx):
    async with bot.pool.acquire() as conn:
        tracks = await conn.fetch(f"SELECT * FROM u{ctx.author.id} ORDER BY AT DESC LIMIT 10")
        if len(tracks) == 0:
            session_data = bot.sessions[ctx.author.id]
            user = authenticate(session_data)
            await fetch_tracks(user, ctx)
            tracks = await conn.fetch(f"SELECT * FROM u{ctx.author.id} ORDER BY AT DESC LIMIT 10")
    s = ""
    for track in tracks:
        # TRACK ARTIST ALBUM AT GENRE COUNTRY ID
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
    start = time.time()
    response = model.generate_content(f"Give me 5 paragraphs roasting the music taste of someone who likes {', '.join(top_artists)}.")
    end = time.time()
    
    e = discord.Embed(
        title="Your Roast",
        description=response.text,
        color=0xe65912,
    )
    e.set_footer(text=f"Response took {round((end - start), 2)} seconds.")

    await message.edit(content="", embed=e)

@bot.command()
async def praise(ctx):
    message = await ctx.send("Generating Praise...")
    session_data = bot.sessions[ctx.author.id]
    user = authenticate(session_data)
    top_artists = random.sample(list(map(lambda x: x.item.name, user.get_top_artists(period=PERIOD_1MONTH, limit=20))), 3)
    start = time.time()
    response = model.generate_content(f"Give me 5 paragraphs praising the music taste of someone who likes {', '.join(top_artists)}.")
    end = time.time()
    
    e = discord.Embed(
        title="Your Praise",
        description=response.text,
        color=0x27d6c2
    )
    e.set_footer(text=f"Response took {round((end - start), 2)} seconds.")
    await message.edit(content="", embed=e)

@bot.command(aliases=["rec"])
async def recommend(ctx, *, picks=None):
    message = await ctx.send("Generating Recommendations...")
    
    if picks == None:
        session_data = bot.sessions[ctx.author.id]
        user = authenticate(session_data)
        picks = ", ".join(random.sample(list(map(lambda x: x.item.title, user.get_top_albums(period=PERIOD_1MONTH, limit=20))), 3))
    
    start = time.time()
    response = model.generate_content(f"Recommend 3 albums (going from less obscure from more obscure, don't mention this fact in your response) to someone who likes {picks}.")
    end = time.time()
    
    e = discord.Embed(
        title="Your Recommendations",
        description=response.text,
        color=0x1cba1c
    )
    e.set_footer(text=f"Response took {round((end - start), 2)} seconds.")
    
    await message.edit(content="", embed=e)


@bot.command()
async def ai(ctx, *, prompt):
    start = time.time()
    response_content = await send_message_to_chatgpt(prompt)
    split_response = split_message(response_content)
    for part in split_response:
        await ctx.send(part)
    end = time.time()
    await ctx.send(f"Response took `{round((end - start), 2)}` seconds.")


bot.run(os.getenv("BOT"))