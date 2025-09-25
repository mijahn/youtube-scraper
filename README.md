# YouTube Channel Video Downloader

Download all videos from one or more YouTube channels using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

---

## ▶️ Usage

### Single channel
```bash
python download_channel_videos.py   --url https://www.youtube.com/@PatrickOakleyEllis   --output "/Volumes/Micha 4TB/youtube downloads"   --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt"   --cookies-from-browser chrome
```

### Multiple channels (local channels.txt)
```bash
python download_channel_videos.py   --channels-file channels.txt   --output "/Volumes/Micha 4TB/youtube downloads"   --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt"   --cookies-from-browser chrome
```

### Multiple channels (remote GitHub repo)
If your `channels.txt` is in a public GitHub repo, copy the **raw** link, e.g.:

```
https://raw.githubusercontent.com/<username>/<repo>/main/channels.txt
```

Run:
```bash
python download_channel_videos.py   --channels-url https://raw.githubusercontent.com/<username>/<repo>/main/channels.txt   --output "/Volumes/Micha 4TB/youtube downloads"   --archive "/Volumes/Micha 4TB/youtube downloads/.downloaded.txt"   --cookies-from-browser chrome
```
