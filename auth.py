from flask import Flask, request, redirect, url_for, render_template, jsonify
import asyncio, aiohttp
import os
from pylast import LastFMNetwork, PyLastError
from dotenv import load_dotenv
from datetime import datetime
import hashlib, requests
import asyncpg
from cryptography.fernet import Fernet
import traceback

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
REDIRECT_URI = "http://localhost:5000/callback"

lastfm_data = {}
usernames_in_progress = set()
app = Flask(__name__)

def track_to_dict(track):
    playback_date = track.playback_date
    dt_object = datetime.strptime(playback_date, "%d %b %Y, %H:%M")
    sql_timestamp = dt_object.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "title": track.track.title,
        "artist": track.track.artist.name,        
        "album": track.album,
        "played_at": sql_timestamp,
    }

async def fetch_recent_tracks(user, username):
    global usernames_in_progress
    usernames_in_progress.add(username)
    try:
        tracks = await asyncio.to_thread(user.get_recent_tracks, limit=1)
        print("After fetching tracks:", tracks)
        if not tracks:
            print(f"No tracks found for {username}.")
            return []
        return [track_to_dict(track) for track in tracks]
    except Exception as e:
        print(f"Error fetching tracks for {username}: {e}")
        print(traceback.format_exc())
        return []
    finally:
        usernames_in_progress.remove(username)


async def asyncfetch(user, username):
    if username not in usernames_in_progress:
        lastfm_data[username] = await fetch_recent_tracks(user, username)

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

@app.route('/')
def index():
    auth_url = f"https://www.last.fm/api/auth/?api_key={API}"
    return redirect(auth_url)

@app.route('/callback')
async def callback():
    load_dotenv()
    API = os.getenv("API")
    LFS = os.getenv("LFS")
    access_token = request.args.get('token')
    params = {
        "api_key": API,
        "method": "auth.getSession",
        "token": access_token
    }
    api_sig = generate_api_sig(params, LFS)
    session_data = await fetch_session_data(API, access_token, api_sig)
    session = session_data["session"]
    sk = session["key"]
    username = session["name"]
    network = LastFMNetwork(api_key=API, api_secret=LFS, session_key=sk)
    user = network.get_authenticated_user()
    await send_sk(username, sk)
    await asyncfetch(user, username)
    return render_template("index.html")

@app.route('/status', methods=['GET'])
def status():
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
