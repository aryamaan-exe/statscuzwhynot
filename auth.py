from flask import Flask, request, redirect, url_for, render_template, jsonify, session, copy_current_request_context
import asyncio, aiohttp
import os
from pylast import LastFMNetwork, SessionKeyGenerator, WSError
from dotenv import load_dotenv
from datetime import datetime
import hashlib, requests, threading
import asyncpg
from cryptography.fernet import Fernet
import pycountry
import logging

logging.basicConfig(level=logging.DEBUG)

def generate_api_sig(params, shared_secret):
    concatenated = ''.join(f"{key}{value}" for key, value in sorted(params.items()))
    concatenated += shared_secret
    return hashlib.md5(concatenated.encode()).hexdigest()

def encrypt_sk(sk):
    load_dotenv()
    fern = Fernet(os.getenv("FERNET").encode())
    return fern.encrypt(sk.encode()).decode()

load_dotenv()
API = os.getenv("API")
LFS = os.getenv("LFS")
REDIRECT_URI = "http://localhost:5000/callback"

lastfm_data = {}
usernames_in_progress = set()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK")

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
    

async def track_to_dict(track):
    playback_date = track.playback_date
    dt_object = datetime.strptime(playback_date, "%d %b %Y, %H:%M")
    sql_timestamp = dt_object.strftime("%Y-%m-%d %H:%M:%S")
    artist = track.track.artist.name
    try:
        genre = track.track.artist.get_top_tags()[0].item.name # Possible that top tag is not genre but rare
    except:
        genre = "Unknown"
    country = await get_country(artist)
    return {
        "title": track.track.title,
        "artist": artist,
        "album": track.album,
        "played_at": sql_timestamp,
        "genre": genre, 
        "country": country
    }

async def fetch_tracks(user, username):
    def get_recent():
        return user.get_recent_tracks(limit=10)
    global usernames_in_progress, lastfm_data
    usernames_in_progress.add(username)
    logging.info("Fetching tracks")
    try:
        tracks = await asyncio.to_thread(get_recent)
        lastfm_data[username] = []
        for track in tracks:
            try:
                track_dict = await track_to_dict(track)
                lastfm_data[username].append(track_dict)
            except Exception as e:
                logging.error(f"Error processing track {track}: {e}")

        logging.info("Done fetching")
    except Exception as e:
        logging.error(f"Error in fetch_tracks: {e}")
    finally:
        usernames_in_progress.discard(username)

async def send_sk(username, sk):
    pool = await asyncpg.create_pool(
        database="lfm",
        host="localhost",
        password=os.getenv("PGP"),
        port="5432"
    )
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO SESSIONS VALUES ($1, $2)", username, encrypt_sk(sk))
    await pool.close()

async def fetch_session_data(api_key, token, api_sig):
    async with aiohttp.ClientSession() as session:
        url = f"https://ws.audioscrobbler.com/2.0/?method=auth.getSession&api_key={api_key}&token={token}&api_sig={api_sig}&format=json"
        async with session.get(url) as response:
            return await response.json()

async def callback(skg, network):
    while True:
        try:
            session_key = await asyncio.to_thread(skg.get_web_auth_session_key, session['auth_url'])
            logging.info("Done")
            break
        except WSError:
            logging.error("Oops")
            await asyncio.sleep(1)
    
    
    network.session_key = session_key
    user = network.get_authenticated_user()
    try:
        tasks = [
            asyncio.create_task(send_sk(user.name, session_key)),
            asyncio.create_task(fetch_tracks(user, user.name))
        ]
        await asyncio.gather(*tasks)

    except Exception as e:
        logging.error(f"Error in callback: {e}")
    return render_template("index.html")

#------------------------------------------------------------------

@app.route('/')
async def index():
    try:
        network = LastFMNetwork(API, LFS, username=request.args.get("username"))
        skg = SessionKeyGenerator(network)
        session['auth_url'] = skg.get_web_auth_url()
        @copy_current_request_context
        def run_callback():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(callback(skg, network))
            loop.close()
        threading.Thread(target=run_callback).start()
        return redirect(session['auth_url'])
    except Exception as e:
        logging.error(f"Error in index: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/status', methods=['GET'])
async def status():
    load_dotenv()
    auth = request.authorization
    if not auth:
        return jsonify({'error': 'Authorization header is missing'}), 401

    if auth.password == os.getenv("LFS"):
        username = auth.username
        if username in usernames_in_progress:
            return jsonify({'message': 'Fetching tracks in progress'}), 402
        elif username in lastfm_data:
            return jsonify(lastfm_data[username]), 200
        else:
            return jsonify({'error': 'No tracks available for this user'}), 403
    else:
        return jsonify({'error': 'Invalid credentials'}), 401

if __name__ == '__main__':
    app.run(debug=True)
