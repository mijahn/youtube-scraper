# YouTube Channel Video Downloader

This project provides a Python script to download all videos from one or more YouTube channels using the actively maintained [yt-dlp](https://github.com/yt-dlp/yt-dlp) library.

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

### Single channel

```bash
python download_channel_videos.py \
  --url https://www.youtube.com/@PatrickOakleyEllis \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome
```

### Multiple channels via channels.txt

Instead of running multiple commands, keep your channel list in a file called `channels.txt`:

**channels.txt**
```
https://www.youtube.com/@EricWTech
https://www.youtube.com/@indydevdan
https://www.youtube.com/@buildnpublic
https://www.youtube.com/@PeterYangYT
https://www.youtube.com/@PatrickOakleyEllis
```

Run:
```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome
```

The script will go through each channel listed in `channels.txt` one by one.

---

## ⚙️ Options

- `--no-shorts` – exclude Shorts (only long-form videos)  
- `--since 2024-01-01` / `--until 2024-12-31` – download within date range  
- `--max 50` – stop after downloading N videos per channel  
- `--rate-limit 2M` – throttle speed (e.g., `500K`, `2M`)  
- `--concurrency 5` – concurrent fragment downloads for HLS/DASH  
- `--skip-subtitles`, `--skip-thumbs` – skip captions or thumbnails  
- `--cookies-from-browser chrome` – use cookies from your logged-in browser  

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
