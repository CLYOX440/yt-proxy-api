import os
import re
import base64
from urllib.parse import urlparse
from flask import Flask, request, jsonify
from flask_cors import CORS
from yt_dlp import YoutubeDL

# ─────────────────────────────────────────────────────────────────────────────
# Config via environment variables
# ─────────────────────────────────────────────────────────────────────────────
API_KEY = os.getenv("API_KEY", "")                 # Optional: require x-api-key header
COOKIES_B64 = os.getenv("COOKIES_B64", "")         # Optional: base64 Netscape cookies.txt
YTDLP_PROXY = os.getenv("YTDLP_PROXY", "")         # Optional: http(s)://user:pass@host:port
PLAYER_CLIENT = os.getenv("PLAYER_CLIENT", "android")  # youtube client: android|web|tv|ios

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

YT_HOST_RE = re.compile(r"(youtube\.com|youtu\.be)")

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

def write_cookiefile_from_b64(b64: str) -> str | None:
    """Write base64 cookies to /tmp/cookies.txt; return path or None."""
    if not b64:
        return None
    path = "/tmp/cookies.txt"
    try:
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        return path
    except Exception as e:
        print("Failed to write cookies:", e)
        return None

def normalize_youtube_url(url: str) -> str:
    """Convert Shorts URLs to standard watch URLs; pass-through others."""
    if not url:
        return url
    # Clean trailing slash and query fragments for shorts
    if "youtube.com/shorts/" in url:
        try:
            vid = url.rstrip("/").split("/")[-1]
            vid = vid.split("?")[0]
            return f"https://www.youtube.com/watch?v={vid}"
        except Exception:
            pass
    return url

def pick_best_progressive_mp4(formats: list[dict]) -> str | None:
    """
    Choose a progressive MP4 (video+audio) URL with the highest quality.
    Fallback to any MP4, then any best URL if needed.
    """
    if not formats:
        return None

    prog_mp4 = [
        f for f in formats
        if f.get("ext") == "mp4"
        and (f.get("vcodec") not in (None, "none"))
        and (f.get("acodec") not in (None, "none"))
        and f.get("url")
    ]
    prog_mp4.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    if prog_mp4:
        return prog_mp4[0]["url"]

    mp4_any = [f for f in formats if f.get("ext") == "mp4" and f.get("url")]
    mp4_any.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    if mp4_any:
        return mp4_any[0]["url"]

    everything = [f for f in formats if f.get("url")]
    everything.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    return everything[0]["url"] if everything else None

def make_ydl_opts(cookiefile: str | None) -> dict:
    """Build yt-dlp options tuned to avoid bot checks and run on Render."""
    opts = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
        # Present as mobile to reduce anti-bot friction
        "http_headers": {"User-Agent": ANDROID_UA, "Accept-Language": "en-US,en;q=0.9"},
        "extractor_args": {"youtube": {"player_client": [PLAYER_CLIENT]}},
        # Some YouTube throttling paths are faster with this
        "concurrent_fragment_downloads": 3,
        "retries": 5,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    return opts

# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/info")
def info():
    """Return basic info for debugging (no secrets)."""
    return jsonify({
        "ok": True,
        "player_client": PLAYER_CLIENT,
        "has_api_key": bool(API_KEY),
        "has_cookies": bool(COOKIES_B64),
        "proxy_set": bool(YTDLP_PROXY),
    })

@app.route("/download")
def download():
    # Auth (optional)
    if API_KEY:
        given = request.headers.get("x-api-key", "")
        if given != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401

    url = (request.args.get("url") or "").strip()
    if not url or not YT_HOST_RE.search(url):
        return jsonify({"error": "Provide a valid YouTube URL via ?url="}), 400

    url = normalize_youtube_url(url)

    cookiefile = write_cookiefile_from_b64(COOKIES_B64)
    ydl_opts = make_ydl_opts(cookiefile)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        # Typical causes: anti-bot wall without cookies, geo, age-gate, or invalid URL
        return jsonify({"error": "yt-dlp failed", "detail": str(e)}), 502

    # If user pasted a playlist link by mistake, use first entry
    if isinstance(info, dict) and info.get("_type") == "playlist":
        if info.get("entries"):
            info = info["entries"][0]
        else:
            return jsonify({"error": "Playlist contained no entries"}), 404

    title = info.get("title")
    duration = info.get("duration")
    formats = info.get("formats") or []

    mp4_url = pick_best_progressive_mp4(formats)
    if not mp4_url:
        return jsonify({"error": "No downloadable MP4 URL was found"}), 502

    return jsonify({
        "title": title,
        "duration": duration,
        "source_url": url,
        "mp4_url": mp4_url
    })

# ─────────────────────────────────────────────────────────────────────────────
# Local dev entrypoint (Render will use Gunicorn)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
