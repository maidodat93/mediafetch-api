import os
import subprocess
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
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


class RenderRequest(BaseModel):
    url: str
    mode: str = "video"  # "video" or "mp3"
    resolution: str = "2160p"
    codec: str = "h264"
    fps: int = 60
    preset: str = "crf16"
    mp3_bitrate: str = "320"
    normalize: bool = True
    embed_cover: bool = True


@app.post("/api/render")
def render_media(req: RenderRequest):
    job_id = uuid.uuid4().hex[:10]
    out_template = str(DOWNLOAD_DIR / f"{job_id}_%(title)s.%(ext)s")

    if req.mode == "mp3":
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", f"{req.mp3_bitrate}K",
            "-o", out_template,
        ]
        if req.embed_cover:
            cmd.extend(["--embed-thumbnail", "--add-metadata"])
        if req.normalize:
            cmd.extend(["--postprocessor-args", "ffmpeg:-af loudnorm=I=-14:TP=-1.5:LRA=11"])
        cmd.append(req.url)
    else:
        height = int(req.resolution.replace("p", "")) if req.resolution.endswith("p") else 2160
        ext = "webm" if req.codec == "vp9" else "mp4"
        vcodec = {"av1": "libsvtav1", "vp9": "libvpx-vp9"}.get(req.codec, "libx264")
        cmd = [
            "yt-dlp",
            "-f", f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
            "--merge-output-format", ext,
            "-o", out_template,
        ]
        if req.preset != "remux":
            crf = "16" if req.preset == "crf16" else "20"
            cmd.extend([
                "--postprocessor-args",
                f"ffmpeg:-c:v {vcodec} -crf {crf} -r {req.fps} -c:a aac -b:a 256k",
            ])
        cmd.append(req.url)

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail=exc.stderr or "Render failed")

    files = list(DOWNLOAD_DIR.glob(f"{job_id}_*"))
    if not files:
        raise HTTPException(status_code=500, detail="Output file not found")

    target = files[0]
    return FileResponse(path=target, filename=target.name)
