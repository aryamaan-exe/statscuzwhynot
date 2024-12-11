import psycopg2
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import time
from pylast import LastFMNetwork

def e(x):
    cur.execute(x)


load_dotenv()


API_KEY = os.getenv("API")
conn = psycopg2.connect(
    database="lfm",
    host="localhost",
    password=os.environ.get("PGP"),
    port="5432"
)

cur = conn.cursor()
username = u = "aryamaan_exe"  #input("Enter username: ")


auth = HTTPBasicAuth(u, os.getenv("LFS"))

network = LastFMNetwork(api_key=os.getenv("API"), api_secret=os.getenv("LFS"), session_key="4UudyBaiD4P7UbxVtOOJgguoUf3mkCRA")

while True:
    start = time.perf_counter()
    r = requests.get("http://localhost:5000/status", auth=auth)
    if r.status_code == 200:
        print(time.perf_counter() - start)
        break
    time.sleep(0.5)


if r.status_code == 200:
    try:
        tracksinfo = r.json()
        
        
        if not tracksinfo:
            print(f"No tracks found for user '{username}'. Exiting.")
        else:
            
            e(f"DROP TABLE IF EXISTS {u}")
            e(f"CREATE TABLE IF NOT EXISTS {u} (TRACK TEXT, ARTIST TEXT, ALBUM TEXT, AT TIMESTAMP)")
            
            
            args_list = []
            for track in tracksinfo:
                track_data = (
                    track["title"],
                    track["artist"],
                    track["album"] or "Unknown",  
                    track["played_at"],
                    track["genre"],
                    track["country"]
                )
                args_list.append(cur.mogrify("(%s, %s, %s, %s)", track_data).decode("utf-8"))
            
            args_str = ",".join(args_list)
            e(f"INSERT INTO {u} VALUES {args_str}")
            
            conn.commit()
            e(f"SELECT * FROM {u}")
            x = cur.fetchone()
            print(f"You were just listening to {x[0]} by {x[1]}.")
    
    except Exception as ex:
        print(f"An error occurred: {ex}")
else:
    print(f"Failed to fetch tracks. HTTP Status Code: {r.status_code}")

cur.close()
conn.close()
