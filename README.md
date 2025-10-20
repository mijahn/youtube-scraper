# YouTube Channel Video Downloader

Download all videos from one or more YouTube channels using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

> **Heads up:** The bundled `requirements.txt` pins `yt-dlp` to the latest release that still supports Python 3.8. If you
> already run Python 3.9+ you can upgrade `yt-dlp` manually, but the pin avoids the "Support for Python version 3.8 has been
> deprecated" warning on older systems.

---

## 📚 Scripts Overview

This project includes multiple scripts designed to work together. Here's what each one does:

### Core Scripts

| Script | Purpose | When to Use | Standalone? |
|--------|---------|-------------|-------------|
| **`download_channel_videos.py`** | All-in-one downloader | Quick downloads, simple workflows | ✅ Yes |
| **`interactive_interface.py`** | Menu-driven interface | Interactive exploration and downloads | ✅ Yes |
| **`scan_channels.py`** | Slow metadata scanner | Large channel lists, avoid rate limits | ✅ Yes (Step 1) |
| **`download_videos.py`** | Metadata-based downloader | After scanning with `scan_channels.py` | ⚠️ Requires metadata.json |
| **`queue_manager.py`** | Persistent queue with retries | Managing failed downloads, resumable workflows | ⚠️ Requires metadata.json |
| **`health_check.py`** | Test YouTube connectivity | Check if you're rate-limited before starting | ✅ Yes |

### How Scripts Work Together

**Architecture:** All scripts follow the **DRY (Don't Repeat Yourself)** principle by importing and calling functions from `download_channel_videos.py`, which serves as the core library. No logic is duplicated between scripts.

```
download_channel_videos.py  ← Core library (all functions live here)
         ↑          ↑          ↑          ↑          ↑
         │          │          │          │          │
         │          │          │          │          └─── health_check.py
         │          │          │          └─── interactive_interface.py
         │          │          └─── download_videos.py
         │          └─── queue_manager.py
         └─── scan_channels.py
```

**Call Graph:**
- `scan_channels.py` → calls `collect_all_video_ids()` from core
- `download_videos.py` → calls `download_videos_from_urls()` from core
- `queue_manager.py` → calls `download_videos_from_urls()` from core
- `interactive_interface.py` → calls `download_source()`, `collect_all_video_ids()`, `run_health_check()` from core

### Choosing Your Workflow

**Simple Workflow (Single Script):**
```bash
# For most users - downloads everything in one go
python download_channel_videos.py --channels-file channels.txt --output ./downloads
```

**Interactive Workflow (Menu-Driven):**
```bash
# Menu interface for exploring and downloading selectively
python interactive_interface.py --channels-file channels.txt --output ./downloads
```

**Advanced Workflow (Separate Scan + Download):**
```bash
# Step 1: Scan slowly overnight (60-120s between requests)
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 120

# Step 2: Download using cached metadata (no additional metadata requests!)
python download_videos.py --metadata metadata.json --output ./downloads
```

**Queue-Based Workflow (With Retry Support):**
```bash
# Step 1: Scan channels (as above)
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 120

# Step 2: Populate persistent queue
python queue_manager.py --populate --metadata metadata.json

# Step 3: Download with automatic retry and exponential backoff
python queue_manager.py --download --workers 1

# Optional: Check queue status anytime
python queue_manager.py --status
```

---

## ⚠️ IMPORTANT: Avoiding YouTube Blocks

**TL;DR - Use these flags to avoid being blocked:**

```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --cookies-from-browser chrome \
  --youtube-client web
```

**The script now applies conservative rate-limiting defaults automatically** (2s between requests, 3-8s between downloads), so you can safely run it without additional flags. For more control or faster downloads, see the detailed settings below.

---

## ⚠️ DETAILED: Rate Limiting & Block Prevention

**This script now includes conservative rate limiting defaults to prevent YouTube from blocking your requests.**

### Default Anti-Blocking Settings (Automatically Applied):
- **2 seconds** delay between HTTP requests during metadata scanning
- **3-8 seconds** randomized delay between video downloads
- **10 consecutive failures** allowed before switching YouTube player clients
- **Exponential backoff** on HTTP 403 errors (pauses 30s to 10 minutes automatically)
- **Reduced retry attempts** to avoid aggressive patterns

### ✅ Best Practices for Long-Running Unattended Downloads:

1. **Always use browser cookies** for authenticated requests:
   ```bash
   --cookies-from-browser chrome
   ```

2. **Use the web client** instead of TV (TV client is more aggressively rate-limited):
   ```bash
   --youtube-client web
   ```

3. **Enable PO token support** for integrity verification:
   ```bash
   --youtube-fetch-po-token always
   ```

4. **Monitor the session statistics** shown at the end of downloads to see if rate limiting occurred

5. **If you need faster downloads**, you can reduce delays, but **expect more blocking**:
   ```bash
   --sleep-requests 1.0 --sleep-interval 1.0 --max-sleep-interval 3.0
   ```

### What Happens When YouTube Blocks You?

The script will **automatically detect** HTTP 403 errors and:
- Pause for 30 seconds on first detection
- Pause for 2 minutes after 3-6 errors
- Pause for 5 minutes after 7-10 errors
- Pause for 10 minutes after 10+ errors

This exponential backoff prevents harder blocks and usually resolves temporary rate limiting.

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

### Multiple sources (local channels.txt)
```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome
```

### Multiple sources (remote GitHub repo)
If your `channels.txt` is in a public GitHub repo, copy the **raw** link, e.g.:

```
https://raw.githubusercontent.com/<username>/<repo>/main/channels.txt
```

Run:
```bash
python3 download_channel_videos.py \
  --channels-url https://raw.githubusercontent.com/mijahn/youtube-scraper/main/channels.txt \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome

```

### Ready-to-copy remote command with delays
Need everything in one line-ready block? Copy the following command to pull the repo-hosted channel list while applying
every available delay and client mitigation option:

```bash
python3 download_channel_videos.py \
  --channels-url https://raw.githubusercontent.com/mijahn/youtube-scraper/main/channels.txt \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --youtube-fetch-po-token always \
  --cookies-from-browser chrome \
  --youtube-client web \
  --sleep-requests 1.5 \
  --sleep-interval 2 \
  --max-sleep-interval 5
```

### Advanced: Customizing Rate Limiting Behavior

The script now includes **conservative defaults** designed for long-running unattended operation. These defaults are automatically applied, but you can customize them if needed:

**Current defaults** (automatically applied):
```bash
--sleep-requests 2.0        # 2 seconds between HTTP requests
--sleep-interval 3.0        # minimum 3 seconds between downloads
--max-sleep-interval 8.0    # maximum 8 seconds between downloads
--failure-limit 10          # allow 10 failures before switching clients
```

**To speed up downloads** (higher risk of blocking):
```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --sleep-requests 1.0 \
  --sleep-interval 1.0 \
  --max-sleep-interval 3.0 \
  --youtube-client web \
  --cookies-from-browser chrome
```

**To slow down even more** (lowest risk, for very large batch downloads):
```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --sleep-requests 3.0 \
  --sleep-interval 5.0 \
  --max-sleep-interval 15.0 \
  --failure-limit 15 \
  --youtube-client web \
  --cookies-from-browser chrome
```

### Automatic Rate Limit Detection

The downloader automatically:
- Retries with different YouTube player clients when downloads fail
- Detects HTTP 403 patterns and pauses with exponential backoff
- Switches clients after reaching the failure limit
- Displays session statistics showing client rotations and rate limit pauses

If temporary rate limits persist despite automatic backoff:
- The script will show a warning message with pause duration
- After pausing, downloads resume automatically
- Use the archive file (`--archive`) to avoid re-downloading on restarts

### Configuration File Support

**Tired of remembering all the command-line flags?** Create a `config.json` file to store your preferred settings:

```json
{
  "sleep_requests": 2.0,
  "sleep_interval": 3.0,
  "max_sleep_interval": 8.0,
  "cookies_from_browser": "chrome",
  "youtube_client": "web",
  "failure_limit": 10,
  "output": "./downloads",
  "archive": "./downloads/.download-archive.txt"
}
```

Place this file in the same directory where you run the script, and all these settings will be applied automatically. **Command-line arguments always override config file values**, so you can still customize individual runs.

**Supported config keys:**
- Rate limiting: `sleep_requests`, `sleep_interval`, `max_sleep_interval`, `failure_limit`
- Authentication: `cookies_from_browser`, `youtube_client`, `youtube_fetch_po_token`
- Output: `output`, `archive`, `format`, `merge_output_format`
- Filters: `since`, `until`, `max`, `no_shorts`, `allow_restricted`
- Performance: `rate_limit`, `concurrency`
- Downloads: `skip_subtitles`, `skip_thumbs`
- Advanced: `watch_interval`, `proxy`

To use a different config file location:
```bash
python download_channel_videos.py --config /path/to/my-config.json
```

### Interactive menu for scanning & downloads

Prefer a guided workflow instead of calling `download_channel_videos.py` directly? The `interactive_interface.py` helper
wraps the downloader and exposes three menu options:

1. **Check for new videos** – scans every entry in your `channels.txt`, highlights sources you recently added, and shows how
   many videos are already archived versus still pending.
2. **Download videos from a specific source** – lists each source with its pending count, then triggers downloads for the one
   you pick using the same flags you would pass to `download_channel_videos.py`.
3. **Download all pending videos** – combines the previous two options by scanning first and then downloading everything that
   has not been archived yet.

Run the interface with the same arguments you normally pass to the downloader, for example:

```bash
python interactive_interface.py \
  --channels-file channels.txt \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome
```

All command-line flags accepted by `download_channel_videos.py` are also supported here, so existing workflows continue to
work while adding a quick way to inspect channel state or focus on a single source without remembering the specific yt-dlp
commands.

### Preserving original formats

The downloader now relies on yt-dlp's native format selection so videos are saved in whatever container YouTube provides.
If you need to override the selection you can pass a custom `--format` expression or request a specific merged container via
`--merge-output-format`, both of which are forwarded directly to yt-dlp.

### Automatic authentication defaults

If you routinely run the script with the same authentication details you can configure them once via environment
variables:

| Environment variable | Purpose |
| --- | --- |
| `YOUTUBE_SCRAPER_COOKIES_FROM_BROWSER` | Browser profile to pull cookies from (e.g. `chrome`, `firefox`). |
| `YOUTUBE_SCRAPER_PO_TOKENS` | Comma or newline separated list of PO tokens in `CLIENT.CONTEXT+TOKEN` format. |
| `YOUTUBE_SCRAPER_FETCH_PO_TOKEN` | Overrides yt-dlp's PO token fetch behavior (`auto`, `always`, or `never`). |

When these variables are present the script automatically applies them to every invocation, so the required PO token and
cookies are always provided even if you omit the corresponding command-line options. By default the downloader now also
requests PO tokens proactively (`--youtube-fetch-po-token always`) to avoid integrity challenges on the first client
attempt.

## channels.txt format tips

- Lines beginning with `#` are ignored, which lets you organize related sources into sections.
- Each non-comment line can optionally start with a prefix to specify the source type:
  - `channel: https://www.youtube.com/@SomeCreator`
  - `playlist: https://www.youtube.com/playlist?list=...`
  - `video: https://www.youtube.com/watch?v=...`
- If you omit the prefix the script assumes the entry is a channel and automatically fetches both the `/videos` and `/shorts`
  tabs (unless you pass `--no-shorts`).

Example:

```
# channels
channel: https://www.youtube.com/@EricWTech

# curated playlists
playlist: https://www.youtube.com/playlist?list=PL01Ur3GaFSxyFumM3ywYF6MiJiOPpRdcA

# favorite talks
video: https://www.youtube.com/watch?v=Dif1hwBejCk
```

---

## 🏗️ Script Details & Advanced Features

### `scan_channels.py` - Metadata Scanner

**Purpose:** Separate metadata collection from downloads to avoid rate limiting.

**Key Features:**
- Configurable request interval (recommended: 60-120 seconds for large channel lists)
- Outputs metadata.json for use with `download_videos.py` or `queue_manager.py`
- Imports and uses `collect_all_video_ids()` from `download_channel_videos.py`
- No code duplication - pure orchestration layer

**Example:**
```bash
# Scan slowly overnight (recommended for 50+ channels)
python scan_channels.py \
  --channels-file channels.txt \
  --output metadata.json \
  --request-interval 120 \
  --cookies-from-browser chrome
```

**When to use:**
- Large channel lists (50+ sources)
- When you've been rate-limited before
- When you want to retry downloads without re-scanning

---

### `download_videos.py` - Metadata-Based Downloader

**Purpose:** Download videos using pre-scanned metadata (no additional metadata requests).

**Key Features:**
- Uses cached metadata from `scan_channels.py`
- Respects archive file (skips already-downloaded videos)
- Imports and uses `download_videos_from_urls()` from `download_channel_videos.py`
- Fast downloads without metadata rate limits

**Example:**
```bash
# Download using cached metadata
python download_videos.py \
  --metadata metadata.json \
  --output ./downloads \
  --archive ./downloads/.download-archive.txt
```

**When to use:**
- After running `scan_channels.py`
- When you need to retry downloads without re-scanning
- For the fastest possible download speed (no metadata delays)

---

### `queue_manager.py` - Persistent Queue Manager

**Purpose:** Manage downloads with persistent queue, automatic retry, and exponential backoff.

**Key Features:**
- Persistent JSON-based queue (survives restarts)
- Exponential retry with configurable max attempts (default: 5)
- Backoff formula: `min(60 * (2 ^ attempts), 3600)` seconds
- Track download state (pending/downloading/completed/failed)
- Imports and uses `download_videos_from_urls()` from `download_channel_videos.py`

**Example:**
```bash
# Populate queue from metadata
python queue_manager.py --populate --metadata metadata.json --archive ./downloads/.download-archive.txt

# Start downloading (handles retries automatically)
python queue_manager.py --download --output ./downloads --workers 1

# Check queue status
python queue_manager.py --status

# Clear queue (if needed)
python queue_manager.py --clear
```

**When to use:**
- When you expect many download failures
- For long-running unattended downloads
- When you need resumable downloads across restarts
- For tracking which videos failed and why

---

### `interactive_interface.py` - Menu-Driven Interface

**Purpose:** Explore channels and manage downloads through an interactive menu.

**Key Features:**
- Menu options: check for new videos, download specific source, download all pending, health check
- Tracks new sources since last scan (using `.channels_state.json`)
- Shows pending vs downloaded counts for each source
- Imports and calls `download_source()`, `collect_all_video_ids()`, `run_health_check()` from core

**Example:**
```bash
# Launch interactive menu
python interactive_interface.py \
  --channels-file channels.txt \
  --output ./downloads \
  --cookies-from-browser chrome
```

**When to use:**
- When you want to explore channels before downloading
- For selective downloads (one channel at a time)
- When you prefer guided workflows over command-line flags

---

### `health_check.py` - Connection Tester

**Purpose:** Test if you're currently rate-limited by YouTube.

**Key Features:**
- Single test request (minimal impact)
- Tests authentication status
- Provides recommendations if rate-limited
- Can test specific YouTube clients (web, tv, etc.)

**Example:**
```bash
# Basic health check
python health_check.py

# With authentication
python health_check.py --cookies-from-browser chrome --youtube-client web
```

**When to use:**
- Before starting large download jobs
- After being rate-limited (to check if block has lifted)
- To test authentication setup

---

### 📚 Complete Documentation

For in-depth architecture details, see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## 🚀 Quick Reference: Which Script Should I Use?

| I want to... | Use this script | Example |
|-------------|----------------|---------|
| **Download everything quickly** | `download_channel_videos.py` | `python download_channel_videos.py --channels-file channels.txt --output ./downloads` |
| **Use a menu interface** | `interactive_interface.py` | `python interactive_interface.py --channels-file channels.txt` |
| **Avoid rate limits (large lists)** | `scan_channels.py` + `download_videos.py` | See "Advanced Workflow" above |
| **Retry failed downloads automatically** | `queue_manager.py` | See "Queue-Based Workflow" above |
| **Check if I'm rate-limited** | `health_check.py` | `python health_check.py --cookies-from-browser chrome` |
| **Download one channel at a time** | `interactive_interface.py` | Choose option 2 from menu |
| **Resume after a crash/restart** | `queue_manager.py` | `python queue_manager.py --download` |
| **See which videos are new** | `interactive_interface.py` | Choose option 1 from menu |

### Common Scenarios

**Scenario: First time user, small channel list (< 20 channels)**
```bash
python download_channel_videos.py --channels-file channels.txt --output ./downloads --cookies-from-browser chrome
```

**Scenario: Large channel list (50+ channels), want to be safe**
```bash
# Day 1: Scan overnight
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 120

# Day 2: Download
python download_videos.py --metadata metadata.json --output ./downloads
```

**Scenario: Keep getting rate-limited or many failures**
```bash
# Step 1: Check connectivity
python health_check.py --cookies-from-browser chrome

# Step 2: Scan slowly
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 180

# Step 3: Use queue manager for automatic retries
python queue_manager.py --populate --metadata metadata.json
python queue_manager.py --download --output ./downloads
```

**Scenario: Want to explore channels interactively before downloading**
```bash
python interactive_interface.py --channels-file channels.txt --output ./downloads --cookies-from-browser chrome
```

---
