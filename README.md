# YouTube Channel Video Downloader

This project provides a Python script to download all videos from a YouTube channel using the actively maintained [yt-dlp](https://github.com/yt-dlp/yt-dlp) library.

It supports downloading both **regular videos** and **Shorts**, merges best video+audio into MP4, and avoids duplicates using a download archive.

---

## 📦 Installation

1. Clone or download this repository.
2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Install [ffmpeg](https://ffmpeg.org/) for video/audio merging:
- **macOS (Homebrew):**
  ```bash
  brew install ffmpeg
  ```
- **Ubuntu/Debian:**
  ```bash
  sudo apt-get install -y ffmpeg
  ```
- **Windows (winget):**
  ```powershell
  winget install Gyan.FFmpeg
  ```

---

## ▶️ Usage

Basic example:

```bash
python download_channel_videos.py --url https://www.youtube.com/@PatrickOakleyEllis
```

Recommended (output folder + archive to avoid re-downloading):

```bash
python download_channel_videos.py \
  --url https://www.youtube.com/@PatrickOakleyEllis \
  --output ./downloads \
  --archive ./downloads/downloaded.txt
```

This will:
- Download from the channel’s **/videos** and **/shorts** tabs
- Merge best video+audio into MP4
- Save videos as:  
  `downloads/<ChannelName>/<YYYY-MM-DD> - <Title> [<ID>].<ext>`
- Skip duplicates using the archive file

---

## ⚙️ Options

- `--no-shorts` – exclude Shorts (only long-form videos)  
- `--since 2024-01-01` / `--until 2024-12-31` – download within date range  
- `--max 50` – stop after downloading N videos  
- `--rate-limit 2M` – throttle speed (e.g., `500K`, `2M`)  
- `--concurrency 5` – concurrent fragment downloads for HLS/DASH  
- `--skip-subtitles`, `--skip-thumbs` – skip captions or thumbnails  

---

## 🔐 Authentication (fixing 'Sign in to confirm you're not a bot' errors)

YouTube sometimes blocks downloads unless you are logged in. Use your browser’s cookies:

```bash
python download_channel_videos.py \
  --url https://www.youtube.com/@PatrickOakleyEllis \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome
```

- Supported browsers: `chrome`, `safari`, `firefox`, `edge` (must be logged into YouTube).  
- Alternative: export cookies with an extension (like **Get cookies.txt**) and pass them with yt-dlp directly, but `--cookies-from-browser` is easier.

---

## 📺 Multiple Channels

Run the script repeatedly with different URLs (using the same archive file prevents duplicates):

```bash
python download_channel_videos.py --url https://www.youtube.com/@PatrickOakleyEllis --output ./downloads --archive ./downloads/downloaded.txt
python download_channel_videos.py --url https://www.youtube.com/@SomeOtherCreator     --output ./downloads --archive ./downloads/downloaded.txt
```

---

## ⚠️ Notes

- Works with `@handle` URLs, `/channel/UC…`, and `/c/<name>` formats  
- If you want **only Shorts**, pass a `/shorts` URL or use `--no-shorts` with a `/shorts` link  
- For private/members-only videos you would need authenticated cookies (not included here)  
- Please **only download content you have rights to** (respect YouTube ToS and local laws)  

---

## ✅ Requirements

- Python 3.8+
- yt-dlp (see `requirements.txt`)
- ffmpeg (recommended)
