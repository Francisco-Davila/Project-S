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
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, error
import requests
from mutagen.mp3 import MP3
import io
from pathlib import Path

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

def tag_mp3_basic(mp3_path, title=None, artist=None, album=None):
    try:
        audio = MP3(mp3_path, ID3=ID3)
        try:
            audio.add_tags()
        except error:
            pass
        easy = EasyID3(mp3_path)
    except Exception:
        try:
            easy = EasyID3()
        except Exception:
            return False

    if title:
        easy["title"] = title
    if artist:
        easy["artist"] = artist
    if album:
        easy["album"] = album

    try:
        easy.save(mp3_path)
        return True
    except Exception as e:
        print("Error saving basic tags:", e)
        return False

# helper: fügt Cover-Art (APIC) hinzu (jpeg/png)
def add_cover_art(mp3_path, image_url):
    try:
        resp = requests.get(image_url, timeout=10)
        resp.raise_for_status()
        img_data = resp.content

        audio = MP3(mp3_path, ID3=ID3)
        try:
            audio.add_tags()
        except error:
            pass

        id3 = ID3(mp3_path)
        id3.delall("APIC")
        mime = "image/jpeg"  # Spotify liefert meist jpeg
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=img_data))
        id3.save(mp3_path)
        return True
    except Exception as e:
        print("Cover embedding failed:", e)
        return False

def fetch_and_embed_cover(mp3_path: str, image_url: str, save_image_to: str | None = None, timeout: int = 10):
    """
    Downloads image_url, optionally saves it to save_image_to, and embeds it into mp3_path as APIC.
    Returns (embedded_boolean, saved_image_path_or_None).
    """
    try:
        r = requests.get(image_url, timeout=timeout)
        r.raise_for_status()
        img_bytes = r.content

        # quick sanity check on size
        if len(img_bytes) < 500:  # too small to be a real cover
            print("Cover too small, ignoring")
            return False, None

        # detect type: prefer Content-Type header, fallback to imghdr
        content_type = r.headers.get("Content-Type", "").lower()
        if "jpeg" in content_type or "jpg" in content_type:
            mime = "image/jpeg"
            ext = "jpg"
        elif "png" in content_type:
            mime = "image/png"
            ext = "png"
        else:
            # fallback
            detected = imghdr.what(None, h=img_bytes)
            if detected == "jpeg" or detected == "jpg":
                mime = "image/jpeg"; ext = "jpg"
            elif detected == "png":
                mime = "image/png"; ext = "png"
            else:
                print("Unknown image type:", content_type, detected)
                return False, None

        # optionally save to disk
        saved_path = None
        if save_image_to:
            try:
                p = Path(save_image_to)
                p.parent.mkdir(parents=True, exist_ok=True)
                # ensure extension matches detected ext
                if not p.suffix:
                    p = p.with_suffix(f".{ext}")
                p.write_bytes(img_bytes)
                saved_path = str(p)
            except Exception as e:
                print("Failed to save cover to disk:", e)
                saved_path = None

        # embed into mp3 using mutagen
        try:
            audio = MP3(mp3_path, ID3=ID3)
            try:
                audio.add_tags()
            except error:
                pass
            id3 = ID3(mp3_path)
            id3.delall("APIC")
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=img_bytes))
            id3.save(mp3_path)
            return True, saved_path
        except Exception as e:
            print("Failed to embed cover into mp3:", e)
            return False, saved_path

    except Exception as e:
        print("Failed to download cover image:", e)
        return False, None



## -------------------API CALLS-------------------------


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
    author: str | None = None
    album: str | None = None


@app.post("/youtube/download-audio")
async def download_audio(req: DownloadRequest):
    filename = re.sub(r'[^\w\s-]', '', req.filename).strip().replace(" ", "_")
    mp3_path = f"{filename}.mp3"

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
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

        if not os.path.exists(mp3_path):
            return {"error": "MP3 file not found after download."}

        # grundlegende Tags aus Anfrage
        title_tag = req.filename.replace("_", " ")
        artist_tag = getattr(req, "author", None) or "unknown artist"
        album_tag = getattr(req, "album", None) or "downloaded single"

        # Versuch: Spotify-Abgleich, falls wir token haben
        try:
            if access_token:
                sp = Spotify(auth=access_token)
                # präzisere Suche: track:"..." artist:"..."
                q = f'track:"{title_tag}" artist:"{artist_tag}"'
                search = sp.search(q, type="track", limit=1)
                tracks = search.get("tracks", {}).get("items", [])
                if tracks:
                    track_info = tracks[0]
                    spotify_album = track_info.get("album", {}).get("name")
                    images = track_info.get("album", {}).get("images", [])
                    if spotify_album:
                        album_tag = spotify_album
                    if images:
                        cover_url = images[0].get("url")  # größtes Bild
                        # cover speichern (optional fehlschlagen lassen)
                        try:
                            added = add_cover_art(mp3_path, cover_url)
                            if not added:
                                print("Cover konnte nicht eingebettet werden.")
                        except Exception as e:
                            print("Cover embedding exception:", e)
        except Exception as e:
            print("Spotify lookup failed (ignored):", e)

        # write basic tags (Titel/Artist/Album)
        try:
            tag_ok = tag_mp3_basic(mp3_path, title=title_tag, artist=artist_tag, album=album_tag)
            if not tag_ok:
                print("Basic tagging returned False")
        except Exception as e:
            print("Tagging failed:", e)

        return FileResponse(mp3_path, media_type="audio/mpeg", filename=mp3_path)

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

        # Fetch all track objects from Spotify
        all_items = get_all_playlist_tracks(sp, playlist_id)
        total = len(all_items)

        print(f"🌀 Starting to download {total} tracks from '{playlist_name}'")

        for index, item in enumerate(all_items, start=1):
            # item is the original Spotify playlist item
            track_obj = item.get("track") or {}
            song_name = track_obj.get("name") or "unknown"
            artists = track_obj.get("artists") or []
            artist = artists[0].get("name") if artists else "unknown artist"

            # build filename safe for filesystem
            filename = f"{song_name} by {artist}"
            cleaned_filename = re.sub(r'[<>:\"/\\|?*\']', '', filename).strip()
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
                search_query = f"{artist} {song_name} oficial lyrics"
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
                continue

            # After successful download, write tags
            try:
                # album from Spotify track object when available
                album_obj = track_obj.get("album", {}) if track_obj else {}
                album_name = album_obj.get("name") if album_obj else None

                # write title, artist, album
                try:
                    tag_mp3_basic(mp3_path, title=song_name, artist=artist, album=album_name)
                except Exception as e:
                    print("Basic tagging failed for", cleaned_filename, e)

                # try to embed spotify album cover if available
                try:
                    images = album_obj.get("images", []) if album_obj else []
                    if images:
                        cover_url = images[0].get("url")  # usually the largest
                        if cover_url:
                            try:
                                add_cover_art(mp3_path, cover_url)
                            except Exception as e:
                                print("Cover embedding failed for", cleaned_filename, e)
                except Exception as e:
                    print("Cover handling error for", cleaned_filename, e)

            except Exception as e:
                print("Tagging block exception for", cleaned_filename, e)

            # send update to client
            yield f"data: {json.dumps(current_data)}\n\n"
            time.sleep(0.2)  # slight delay for smoother frontend handling

        print("✅ All tracks processed")
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
