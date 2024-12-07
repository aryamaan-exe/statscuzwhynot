from flask import Flask, request, redirect, url_for, render_template, jsonify
import asyncio
import os
from pylast import LastFMNetwork
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
CLIENT_ID = os.getenv("API")
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
        tracks = await asyncio.to_thread(user.get_recent_tracks, limit=5)
        return [track_to_dict(track) for track in tracks]
    finally:
        usernames_in_progress.remove(username)

async def asyncfetch(user, username):
    if username not in usernames_in_progress:
        lastfm_data[username] = await fetch_recent_tracks(user, username)

@app.route('/')
def index():
    auth_url = f"https://www.last.fm/api/auth/?api_key={CLIENT_ID}"
    return redirect(auth_url)

@app.route('/callback')
async def callback():
    load_dotenv()
    access_token = request.args.get('token')
    print("ACCESS TOKEN:", access_token)
    network = LastFMNetwork(api_key=CLIENT_ID, api_secret=os.getenv("LFS"), token=access_token)
    user = network.get_authenticated_user()
    username = user.get_name()
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
