import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

load_dotenv()

API_ID = int(os.getenv("API_ID", os.getenv("APP_KEY", "0")))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WORKERS = int(os.getenv("WORKERS", "4"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/")
BOT_URL = os.getenv("BOT_URL", "")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
DOWNLOAD_DIR.mkdir(exist_ok=True)

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise RuntimeError("API_ID, API_HASH and BOT_TOKEN must be configured")

app = Client("MultiDownloaderBot", api_id=API_ID, api_hash=API_HASH,
             bot_token=BOT_TOKEN, workers=WORKERS)

# user_id -> {"url": str, "title": str, "message_id": int}
pending = {}

URL_RE = re.compile(r"https?://[^\s<>]+", re.I)


def clean_url(url: str) -> str:
    return url.strip().rstrip(")]>.,!\"'")


def human_bytes(value):
    if value is None:
        return "Unknown"
    value = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024


def safe_name(name: str, fallback: str = "download") -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip(" .")
    return name[:180] or fallback


def yt_base_args():
    # Intentionally run without browser cookies. Some public URLs will work
    # normally; URLs that require YouTube verification/login will fail cleanly.
    return ["yt-dlp", "--no-warnings", "--no-playlist", "--restrict-filenames"]


def extract_info(url: str):
    cmd = yt_base_args() + ["--dump-single-json", "--skip-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1500:] or "Unable to read this URL")
    import json
    return json.loads(result.stdout)


def progress_hook_factory(message, state):
    last_edit = [0.0]

    def hook(data):
        if data.get("status") != "downloading":
            return
        now = time.time()
        if now - last_edit[0] < 2 and data.get("downloaded_bytes") != data.get("total_bytes"):
            return
        last_edit[0] = now
        downloaded = data.get("downloaded_bytes", 0)
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        percent = (downloaded * 100 / total) if total else 0
        speed = data.get("speed") or 0
        eta = data.get("eta")
        eta_text = f"{eta}s" if eta is not None else "--"
        state["last"] = (percent, downloaded, total, speed, eta_text)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(message.edit_text(
                f"⏬ **Downloading**\n\n"
                f"`{percent:.1f}%`  {human_bytes(downloaded)} / {human_bytes(total)}\n"
                f"⚡ {human_bytes(speed)}/s   ⏱ {eta_text}"
            ))
        except RuntimeError:
            pass
    return hook


def download_media(url, mode, message, state):
    workdir = Path(tempfile.mkdtemp(prefix="media_", dir=DOWNLOAD_DIR))
    hook = progress_hook_factory(message, state)
    if mode == "audio":
        format_selector = "bestaudio/best"
        output = str(workdir / "%(title)s.%(ext)s")
        cmd = yt_base_args() + [
            "-f", format_selector,
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "--embed-thumbnail", "--add-metadata",
            "-o", output,
            "--progress", url,
        ]
    else:
        # Prefer MP4 when available, otherwise let yt-dlp choose a compatible format.
        format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        output = str(workdir / "%(title)s.%(ext)s")
        cmd = yt_base_args() + [
            "-f", format_selector,
            "--merge-output-format", "mp4",
            "-o", output,
            "--progress", url,
        ]

    # Python API progress hooks are more reliable than parsing CLI output.
    cmd.remove("--progress")
    cmd += ["--print", "after_move:filepath"]

    import yt_dlp
    options = {
        "format": format_selector,
        "outtmpl": output,
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4" if mode == "video" else None,
    }
    if mode == "audio":
        options.update({
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ]
        })
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    files = [p for p in workdir.rglob("*") if p.is_file()]
    if not files:
        raise RuntimeError("Download completed but no output file was found")
    # Select the largest media file; ignore temporary metadata files.
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    return workdir, files[0]


async def do_download(message, url, mode):
    status = await message.reply_text("⏳ **Preparing download...**")
    workdir = None
    try:
        state = {}
        await status.edit_text("🔎 **Reading media information...**")
        info = await asyncio.to_thread(extract_info, url)
        title = info.get("title") or "Download"
        duration = info.get("duration")
        size = info.get("filesize") or info.get("filesize_approx")
        meta = f"**{safe_name(title)}**"
        if duration:
            meta += f"\n⏱ {int(duration)//60}:{int(duration)%60:02d}"
        if size:
            meta += f"\n📦 {human_bytes(size)}"
        await status.edit_text(f"{meta}\n\n📥 **Starting {mode} download...**")

        workdir, filepath = await asyncio.to_thread(download_media, url, mode, status, state)
        file_size = filepath.stat().st_size
        if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise RuntimeError(
                f"The downloaded file is {human_bytes(file_size)}, above the configured limit of {MAX_FILE_SIZE_MB} MB."
            )

        await status.edit_text("📤 **Uploading to Telegram...**")
        caption = f"🎬 **{safe_name(title)}**\n📦 {human_bytes(file_size)}"
        if BOT_URL:
            caption += f"\n\n🤖 @{BOT_URL}"
        await message.reply_document(
            document=str(filepath),
            caption=caption,
            file_name=filepath.name,
        )
        await status.delete()
    except Exception as exc:
        error = str(exc).replace("`", "")
        if len(error) > 1200:
            error = error[-1200:]
        # Give a friendly message for the common YouTube verification case.
        low = error.lower()
        if ("sign in to confirm" in low or "not a bot" in low or
                "cookies-from-browser" in low or "login_required" in low):
            friendly = (
                "❌ **YouTube verification required**\n\n"
                "This video is currently protected by YouTube and cannot be "
                "downloaded by this bot without authentication.\n\n"
                "Try another public video. Other supported websites may still work."
            )
        else:
            friendly = f"❌ **Download failed**\n\n`{error}`"
        try:
            await status.edit_text(friendly)
        except Exception:
            pass
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


@app.on_message(filters.command("start"))
async def start(_, message):
    buttons = [[
        InlineKeyboardButton("📢 Channel", url=CHANNEL_URL),
        InlineKeyboardButton("💻 Source", url="https://github.com/TerminalWarlord/TikTok-Downloader-Bot")
    ]]
    await message.reply_text(
        "👋 **Multi-Site Downloader Bot**\n\n"
        "Send me a supported video/audio URL. I can download it and send the file back to Telegram.\n\n"
        "🎬 Video • 🎵 MP3 Audio • ⬇️ Progress",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text(
        "**How to use**\n\n"
        "1. Send a video URL.\n"
        "2. Choose **Video** or **MP3 Audio**.\n"
        "3. Wait for download and Telegram upload.\n\n"
        "The downloader uses yt-dlp, so supported sites can change over time."
    )


@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def url_handler(_, message):
    match = URL_RE.search(message.text or "")
    if not match:
        return
    url = clean_url(match.group(0))
    if not url.lower().startswith(("http://", "https://")):
        return

    wait = await message.reply_text("🔎 **Checking link...**")
    try:
        info = await asyncio.to_thread(extract_info, url)
        title = info.get("title") or "Media"
        duration = info.get("duration")
        site = info.get("extractor_key") or info.get("extractor") or "Unknown site"
        text = f"🎬 **{safe_name(title)}**\n🌐 `{site}`"
        if duration:
            text += f"\n⏱ {int(duration)//60}:{int(duration)%60:02d}"
        text += "\n\nChoose what you want to download:"
        pending[message.from_user.id] = {"url": url, "title": title}
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Video", callback_data="dl:video"),
             InlineKeyboardButton("🎵 MP3", callback_data="dl:audio")],
            [InlineKeyboardButton("❌ Cancel", callback_data="dl:cancel")]
        ])
        await wait.edit_text(text, reply_markup=keyboard)
    except Exception as exc:
        error = str(exc).replace("`", "")
        low = error.lower()
        if ("sign in to confirm" in low or "not a bot" in low or
                "cookies-from-browser" in low or "login_required" in low):
            text = (
                "❌ **YouTube verification required**\n\n"
                "YouTube is asking for verification for this video. "
                "No cookies are configured in this bot, so try another public video."
            )
        else:
            text = f"❌ **Unsupported or unavailable URL**\n\n`{error[:900]}`"
        await wait.edit_text(text)


@app.on_callback_query(filters.regex(r"^dl:(video|audio|cancel)$"))
async def callbacks(_, query):
    user_id = query.from_user.id
    item = pending.get(user_id)
    action = query.data.split(":", 1)[1]
    if action == "cancel":
        pending.pop(user_id, None)
        await query.message.edit_text("❌ Cancelled.")
        await query.answer()
        return
    if not item:
        await query.answer("This download request expired. Send the link again.", show_alert=True)
        return
    pending.pop(user_id, None)
    await query.answer()
    await query.message.edit_reply_markup(None)
    await do_download(query.message, item["url"], action)


def start_health_server():
    """Keep Render Web Service health checks satisfied while the Telegram bot runs."""
    port = int(os.getenv("PORT", "10000"))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/health", "/healthz"):
                body = b"OK"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Health server listening on 0.0.0.0:{port}")


if __name__ == "__main__":
    start_health_server()
    app.run()
