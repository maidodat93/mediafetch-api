import os
import subprocess
import uuid
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="MediaFetch Pro Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path("./downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
COOKIES_PATH = Path("/tmp/youtube_cookies.txt")


class RenderRequest(BaseModel):
    url: str
    mode: str = "video"
    resolution: str = "1080p"
    codec: str = "h264"
    fps: int = 60
    preset: str = "remux"
    mp3_bitrate: str = "320"
    normalize: bool = False
    embed_cover: bool = False


def cleanup_file(path: Path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def get_cookie_args():
    cookies_env = os.getenv("YOUTUBE_COOKIES", "").strip()
    if cookies_env:
        COOKIES_PATH.write_text(cookies_env, encoding="utf-8")
        return ["--cookies", str(COOKIES_PATH)]
    return []


@app.get("/")
def health():
    return {
        "status": "ok",
        "cookies_loaded": bool(os.getenv("YOUTUBE_COOKIES", "").strip()),
    }


@app.post("/api/render")
def render_media(req: RenderRequest, background_tasks: BackgroundTasks):
    job_id = uuid.uuid4().hex[:10]
    out_template = str(DOWNLOAD_DIR / f"{job_id}_%(title).60s.%(ext)s")

    base_args = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--extractor-args", "youtube:player_client=android,ios,web",
    ] + get_cookie_args()

    if req.mode == "mp3":
        cmd = base_args + [
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", f"{req.mp3_bitrate}K",
            "-o", out_template,
            req.url,
        ]
    else:
        height = int(req.resolution.replace("p", "")) if req.resolution.endswith("p") else 1080
        ext = "webm" if req.codec == "vp9" else "mp4"
        cmd = base_args + [
            "-f", f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
            "--merge-output-format", ext,
            "-o", out_template,
        ]
        if req.preset != "remux":
            crf = "20" if req.preset == "crf20" else "18"
            cmd.extend([
                "--postprocessor-args",
                f"ffmpeg:-threads 2 -c:v libx264 -preset veryfast -crf {crf} -c:a aac -b:a 192k",
            ])
        cmd.append(req.url)

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=240)
    except subprocess.CalledProcessError as exc:
        err_msg = (exc.stderr or exc.stdout or "Render failed").strip()
        print("YT-DLP ERROR:", err_msg, flush=True)
        raise HTTPException(status_code=400, detail=err_msg[-500:])
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Render timeout")

    files = list(DOWNLOAD_DIR.glob(f"{job_id}_*"))
    if not files:
        raise HTTPException(status_code=500, detail="Output file not found")

    target = files[0]
    background_tasks.add_task(cleanup_file, target)
    return FileResponse(path=target, filename=target.name)
