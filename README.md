# YouTube Channel Video Downloader

Download all videos from one or more YouTube channels using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

> **Heads up:** The bundled `requirements.txt` pins `yt-dlp` to the latest release that still supports Python 3.8. If you
> already run Python 3.9+ you can upgrade `yt-dlp` manually, but the pin avoids the "Support for Python version 3.8 has been
> deprecated" warning on older systems.

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

### Multiple channels (local channels.txt)
```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --output "/Volumes/Micha 4TB/youtube downloads" \
  --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt" \
  --cookies-from-browser chrome
```

### Multiple channels (remote GitHub repo)
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
  --cookies-from-browser chrome \
  --sleep-requests 1.5 \
  --sleep-interval 2 \
  --max-sleep-interval 5 \
  --youtube-client web
```

### Avoiding temporary rate limiting
YouTube can temporarily block unauthenticated scraping when too many requests are made in a short period, or when the wrong
player client is used. A few options have been added to help mitigate this:

```bash
python download_channel_videos.py \
  --channels-file channels.txt \
  --sleep-requests 1.5 \   # wait between HTTP requests
  --sleep-interval 2 \     # random sleep between downloads (min)
  --max-sleep-interval 5 \ # random sleep between downloads (max)
  --youtube-client web \   # force the regular web client instead of TV
  --cookies-from-browser chrome
```

The downloader now automatically retries with different YouTube player clients when every download in a batch fails with
`Video unavailable` so you don't have to restart manually. If rate limits persist, try the following adjustments:

- **Use browser cookies** (`--cookies-from-browser chrome`) so requests look like a real logged-in session.
- **Slow down the request rate** with the sleep options shown above.
- **Force the `web` player client** via `--youtube-client web` to avoid the TV client that Google often rate limits.
- If the limits persist, pause the script for a few hours and resume later (using `--archive` avoids re-downloading files).
