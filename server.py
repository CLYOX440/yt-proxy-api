import os, re
from flask import Flask, request, jsonify
from yt_dlp import YoutubeDL
from urllib.parse import urlparse
from flask_cors import CORS

API_KEY = os.getenv("API_KEY", "")  # optional protection

app = Flask(__name__)
CORS(app)

YT_REGEX = re.compile(r"(youtube\.com|youtu\.be)")

def pick_best_mp4(formats):
    """
    Prefer a progressive MP4 (has both audio+video) up to 1080p.
    Fallback to best available format URL.
    """
    prog_mp4 = [
        f for f in formats
        if f.get("ext") == "mp4"
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    # Sort by height then tbr
    prog_mp4.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    if prog_mp4:
        return prog_mp4[0]["url"]

    # Fallback: any mp4 (may be video-only)
    mp4_any = [f for f in formats if f.get("ext") == "mp4"]
    mp4_any.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    if mp4_any:
        return mp4_any[0]["url"]

    # Last resort: best format's URL
    best = sorted(formats, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    return best[0]["url"] if best else None

@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/download")
def download():
    # Optional simple API key gate
    if API_KEY:
        key = request.headers.get("x-api-key", "")
        if key != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401

    url = request.args.get("url", "").strip()
    if not url or not YT_REGEX.search(url):
        return jsonify({"error": "Provide a valid YouTube URL in ?url="}), 400

    # Normalize shorts URLs -> watch URLs (yt-dlp can handle both; normalization helps)
    if "youtube.com/shorts/" in url:
        parts = urlparse(url)
        video_id = url.rstrip("/").split("/")[-1].split("?")[0]
        url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "nocheckcertificate": True,
        "noplaylist": True,
        "geo_bypass": True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify({"error": "yt-dlp failed", "detail": str(e)}), 500

    # When the input is a playlist accidentally, pick first entry
    if info.get("_type") == "playlist":
        info = info["entries"][0]

    title = info.get("title")
    duration = info.get("duration")
    formats = info.get("formats") or []
    mp4_url = pick_best_mp4(formats)

    if not mp4_url:
        return jsonify({"error": "No downloadable format found"}), 502

    # NOTE: these URLs are signed and expire (typically minutes to hours) — fetch promptly.
    return jsonify({
        "title": title,
        "duration": duration,
        "source_url": url,
        "mp4_url": mp4_url
    })
