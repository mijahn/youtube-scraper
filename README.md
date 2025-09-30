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
- **Leverage yt-dlp's PO Token support** with `--youtube-fetch-po-token always` so the new 2025.9.26 release proactively
  requests integrity tokens for clients that need them, or pass your own tokens via `--youtube-po-token
  CLIENT.CONTEXT+TOKEN` when integrating an external provider.
- If the limits persist, pause the script for a few hours and resume later (using `--archive` avoids re-downloading files).

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
