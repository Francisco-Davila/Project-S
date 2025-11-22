from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from spotipy import Spotify, SpotifyOAuth
from dotenv import load_dotenv
from youtubesearchpython import VideosSearch
from pydantic import BaseModel
import os
from fastapi.responses import FileResponse
import yt_dlp
import re
import time
from fastapi.responses import StreamingResponse
import json



# Load environment variables
load_dotenv()

app = FastAPI()

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],  # frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Spotify OAuth setup
sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope="playlist-read-private playlist-read-collaborative",
    cache_path=".cache"  # Optional: store tokens
)

def get_all_playlist_tracks(sp, playlist_id):
    all_tracks = []
    limit = 100
    offset = 0

    while True:
        results = sp.playlist_tracks(playlist_id, limit=limit, offset=offset)
        items = results.get("items", [])
        all_tracks.extend(items)

        if len(items) < limit:
            break  # No more pages

        offset += limit

    return all_tracks

# In-memory token store (simple for now)
access_token = None

@app.get("/login")
def login():
    auth_url = sp_oauth.get_authorize_url()
    return RedirectResponse(auth_url)

@app.get("/callback")
def callback(request: Request):
    global access_token
    code = request.query_params.get("code")
    if code:
        try:
            token_info = sp_oauth.get_access_token(code)
            access_token = token_info["access_token"]
            return RedirectResponse("http://localhost:4200/home?login=success")
        except Exception as e:
            print(f"Spotify auth failed: {e}")
            return RedirectResponse("http://localhost:4200/home?login=fail")
    else:
        return RedirectResponse("http://localhost:4200/home?login=fail")

@app.get("/playlists")
def get_playlists():
    global access_token
    if not access_token:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    sp = Spotify(auth=access_token)
    playlists = sp.current_user_playlists()
    result = [{"id": p["id"], "name": p["name"]} for p in playlists["items"]]
    return result


@app.get("/playlists/{playlist_id}/tracks")
def get_playlist_tracks(playlist_id: str):
    global access_token
    if not access_token:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    sp = Spotify(auth=access_token)
    playlist = sp.playlist(playlist_id)
    playlist_name = playlist["name"]
    folder = os.path.join("music", playlist_name)

    # ✅ Fetch all tracks, not just the first 100
    all_items = get_all_playlist_tracks(sp, playlist_id)

    tracks = []
    for t in all_items:
        name = t["track"]["name"]
        artist = t["track"]["artists"][0]["name"]
        filename = f"{name} by {artist}"
        cleaned = re.sub(r'[<>:"/\\|?*\']', '', filename).strip()
        path = os.path.join(folder, f"{cleaned}.mp3")
        already_downloaded = os.path.exists(path)

        tracks.append({
            "name": name,
            "artist": artist,
            "downloaded": already_downloaded
        })

    return tracks


# 📺 YouTube Search Endpoint
class SearchResponse(BaseModel):
    title: str
    url: str

@app.get("/youtube/search", response_model=SearchResponse)
def search_youtube(
    query: str = Query(..., description="Video title or keywords"),
    author: str = Query(None, description="Optional author or channel name")
):
    full_query = f"{query} {author}" if author else query
    search = VideosSearch(full_query, limit=1)
    results = search.result()

    if not results['result']:
        return SearchResponse(title="Not Found", url="No video found")

    video = results['result'][0]
    return SearchResponse(title=video['title'], url=video['link'])




class DownloadRequest(BaseModel):
    url: str
    filename: str

@app.post("/youtube/download-audio")
async def download_audio(req: DownloadRequest):
    # Clean filename: remove invalid characters and spaces
    filename = re.sub(r'[^\w\s-]', '', req.filename).strip().replace(" ", "_")
    mp3_path = f"{filename}.mp3"

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,  # 🔒 Prevent playlist-related issues
        'ffmpeg_location': r'C:\HTL\Project-S\ffmpeg-7.1.1-essentials_build\bin',
        'outtmpl': f'{filename}.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        if os.path.exists(mp3_path):
            return FileResponse(mp3_path, media_type="audio/mpeg", filename=mp3_path)
        else:
            return {"error": "MP3 file not found after download."}

    except Exception as e:
        return {"error": str(e)}
    

@app.get("/playlists/{playlist_id}/download-all-stream")
def download_playlist_stream(playlist_id: str):
    def generate():
        global access_token
        if not access_token:
            yield f"data: {json.dumps({'error': 'Not authenticated'})}\n\n"
            return

        sp = Spotify(auth=access_token)
        playlist = sp.playlist(playlist_id)
        playlist_name = playlist["name"]
        playlist_folder = os.path.join("music", playlist_name)
        os.makedirs(playlist_folder, exist_ok=True)

        # Fetch all tracks
        all_items = get_all_playlist_tracks(sp, playlist_id)
        tracks = [{"name": t["track"]["name"], "artist": t["track"]["artists"][0]["name"]} for t in all_items]
        total = len(tracks)

        print(f"🌀 Starting to download {total} tracks from '{playlist_name}'")

        for index, track in enumerate(tracks, start=1):
            song_name = track["name"]
            artist = track["artist"]
            filename = f"{song_name} by {artist}"
            cleaned_filename = re.sub(r'[<>:"/\\|?*\']', '', filename).strip()
            mp3_path = os.path.join(playlist_folder, f"{cleaned_filename}.mp3")

            current_data = {
                "index": index,
                "total": total,
                "song": filename,
                "status": "",
                "duration": 0
            }

            if os.path.exists(mp3_path):
                current_data["status"] = "skipped"
                yield f"data: {json.dumps(current_data)}\n\n"
                continue

            # Search YouTube
            try:
                search_query = f"{artist} {song_name} oficial"
                search = VideosSearch(search_query, limit=1)
                yt_results = search.result()
            except Exception as e:
                current_data["status"] = f"youtube search error: {str(e)}"
                yield f"data: {json.dumps(current_data)}\n\n"
                continue

            if not yt_results.get("result"):
                current_data["status"] = "not found"
                yield f"data: {json.dumps(current_data)}\n\n"
                continue

            video_url = yt_results["result"][0]["link"]
            ydl_opts = {
                'format': 'bestaudio/best',
                'noplaylist': True,
                'ffmpeg_location': r'C:\HTL\Project-S\ffmpeg-7.1.1-essentials_build\bin',
                'outtmpl': os.path.join(playlist_folder, f"{cleaned_filename}.%(ext)s"),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'retries': 1,
                'socket_timeout': 10,
            }

            try:
                start = time.time()
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
                end = time.time()
                current_data["duration"] = round(end - start, 2)
                current_data["status"] = "downloaded"
            except Exception as e:
                current_data["status"] = f"error: {str(e)}"

            yield f"data: {json.dumps(current_data)}\n\n"
            time.sleep(0.2)  # slight delay for smoother frontend handling

        print("✅ All tracks processed")
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
