from fastapi.responses import FileResponse
from pathlib import Path
import yt_dlp
import re
import os

class DownloadRequest(BaseModel):
    url: str
    filename: str
    foldername: str

@app.post("/youtube/download-audio")
async def download_audio(req: DownloadRequest):
    # Clean folder and filename
    filename = re.sub(r'[^\w\s-]', '', req.filename).strip().replace(" ", "_")
    foldername = re.sub(r'[^\w\s-]', '', req.foldername).strip().replace(" ", "_")

    # Build full path: /music/foldername/filename.mp3
    base_dir = Path("music") / foldername
    base_dir.mkdir(parents=True, exist_ok=True)  # Create both /music and subfolder if needed

    mp3_path = base_dir / f"{filename}.mp3"

    # ✅ Skip download if the file already exists
    if mp3_path.exists():
        return FileResponse(mp3_path, media_type="audio/mpeg", filename=mp3_path.name)

    # yt-dlp options
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'ffmpeg_location': r'C:\HTL\Project-S\ffmpeg-7.1.1-essentials_build\bin',
        'outtmpl': str(base_dir / f"{filename}.%(ext)s"),
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

        if mp3_path.exists():
            return FileResponse(mp3_path, media_type="audio/mpeg", filename=mp3_path.name)
        else:
            return {"error": "MP3 file not found after download."}

    except Exception as e:
        return {"error": str(e)}
