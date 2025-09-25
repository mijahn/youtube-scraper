# YouTube Channel Video Downloader

Download all videos from one or more YouTube channels using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

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

Try the following when you begin to see `Video unavailable` messages or 429 HTTP errors:

- **Use browser cookies** (`--cookies-from-browser chrome`) so requests look like a real logged-in session.
- **Slow down the request rate** with the sleep options shown above.
- **Force the `web` player client** via `--youtube-client web` to avoid the TV client that Google often rate limits.
- If the limits persist, pause the script for a few hours and resume later (using `--archive` avoids re-downloading files).
