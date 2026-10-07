# Multi-Site Telegram Downloader Bot

This project is a modified version of the original TikTok Downloader Bot. It keeps the Pyrogram Telegram-bot structure but replaces the hard-coded TikTok RapidAPI downloader with `yt-dlp` + FFmpeg.

## Features

- Send a supported video URL directly to the bot.
- Automatic site detection through yt-dlp.
- Download as MP4 video.
- Extract audio as high-quality MP3.
- Inline Video / MP3 buttons.
- Download progress and Telegram upload status.
- Title, site and duration preview.
- Temporary-file cleanup after upload.
- Optional cookies file for services/accounts you are authorized to access.
- Docker support with FFmpeg and Node.js.

`yt-dlp` supports a large number of extractors, but sites can change and a listed site is not guaranteed to work forever. Test the actual URL when troubleshooting.

## Setup

1. Install Python 3.10+ and FFmpeg. For full YouTube support, yt-dlp also recommends its EJS component and a supported JavaScript runtime; the included Dockerfile installs Node.js.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in:

- `API_ID` / `API_HASH` from Telegram
- `BOT_TOKEN` from BotFather
- `WORKERS`
- `CHANNEL_URL`
- `BOT_URL`

4. Start:

```bash
python main.py
```

## Docker

```bash
docker compose up -d --build
```

## Usage

Send a supported URL. The bot will show:

- 🎬 Video — MP4
- 🎵 MP3 — audio extraction
- ❌ Cancel

Only download media you have permission to download and redistribute. The bot does not bypass private/paywalled access.


## Render deployment

This Docker image can run as a Render Web Service because the bot starts a small health server on the `PORT` environment variable while Pyrogram runs the Telegram bot.

- Build: use the included Dockerfile.
- Start command: leave the Docker command as-is (`python main.py`).
- Render will provide `PORT` automatically.
- Required environment variables: `API_ID`, `API_HASH`, `BOT_TOKEN`.

For a pure background-worker deployment, the included `Procfile` can also be used with `worker: python main.py`.


## Cookies-free mode
This version does not require `cookies.txt` or browser cookies. Public URLs that yt-dlp can access normally will download. If a site (especially YouTube) requires login or bot verification, the bot reports that clearly and does not crash.
