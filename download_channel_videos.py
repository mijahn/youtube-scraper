#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_channel_videos.py

Download all videos (and optionally Shorts) from one or more YouTube channels using yt-dlp.
Supports:
- Single channel (--url)
- Local channels.txt (--channels-file)
- Remote channels.txt from GitHub or any URL (--channels-url)
"""

import argparse
import os
import sys
import re
import urllib.request
from datetime import datetime
from typing import List

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


def normalize_channel_urls(base_url: str, include_shorts: bool = True) -> List[str]:
    url = base_url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/")

    if re.search(r"/(videos|shorts|streams|live)$", url):
        urls = [url]
    else:
        urls = [url + "/videos"]

    if include_shorts:
        shorts_url = url + "/shorts"
        if shorts_url not in urls:
            urls.append(shorts_url)

    return urls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download all videos from YouTube channels using yt-dlp.")
    parser.add_argument("--url", help="Single channel URL (e.g., https://www.youtube.com/@SomeCreator)")
    parser.add_argument("--channels-file", help="Path to a local text file with one channel URL per line")
    parser.add_argument("--channels-url", help="URL to a remote channels.txt file (e.g., GitHub raw link)")
    parser.add_argument("--output", default="./downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("--archive", default=None, help="Path to a download archive file to skip already downloaded videos")
    parser.add_argument("--since", default=None, help="Only download videos uploaded on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="Only download videos uploaded on/before this date (YYYY-MM-DD)")
    parser.add_argument("--max", type=int, default=None, help="Stop after downloading N videos per channel")
    parser.add_argument("--no-shorts", action="store_true", help="Exclude /shorts tab (download only long-form videos)")
    parser.add_argument("--rate-limit", default=None, help="Limit download speed, e.g., 2M or 500K (passed to yt-dlp)")
    parser.add_argument("--concurrency", type=int, default=None, help="Concurrent fragment downloads (HLS/DASH)")
    parser.add_argument("--skip-subtitles", action="store_true", help="Do not download subtitles/auto-captions")
    parser.add_argument("--skip-thumbs", action="store_true", help="Do not download thumbnails")
    parser.add_argument("--cookies-from-browser", default=None, help="Use cookies from your browser (chrome, safari, firefox, edge, etc.)")
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=None,
        help="Seconds to sleep between HTTP requests (helps avoid rate limiting)",
    )
    parser.add_argument(
        "--sleep-interval",
        type=float,
        default=None,
        help="Minimum randomized sleep between video downloads",
    )
    parser.add_argument(
        "--max-sleep-interval",
        type=float,
        default=None,
        help="Maximum randomized sleep between video downloads",
    )
    parser.add_argument(
        "--youtube-client",
        choices=["web", "android", "ios", "tv"],
        default=None,
        help="Override the YouTube player client used by yt-dlp (default: yt-dlp decides)",
    )
    return parser.parse_args()


def ytdlp_date(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise SystemExit(f"Invalid date '{s}'. Use YYYY-MM-DD.")


def download_channel(url: str, args) -> None:
    urls = normalize_channel_urls(url, include_shorts=not args.no_shorts)

    outtmpl = os.path.join(
        args.output,
        "%(channel)s/%(upload_date>%Y-%m-%d)s - %(title).200B [%(id)s].%(ext)s",
    )

    ydl_opts = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "continuedl": True,
        "ignoreerrors": "only_download",
        "noprogress": False,
        "retries": 10,
        "fragment_retries": 10,
        "outtmpl": outtmpl,
        "restrictfilenames": True,
        "windowsfilenames": False,
        "writethumbnail": not args.skip_thumbs,
        "writesubtitles": not args.skip_subtitles,
        "writeautomaticsub": not args.skip_subtitles,
        "subtitleslangs": ["en.*,de.*,.*"],
        "download_archive": args.archive,
        "quiet": False,
        "no_warnings": False,
    }

    if args.rate_limit:
        ydl_opts["ratelimit"] = args.rate_limit
    if args.concurrency and args.concurrency > 0:
        ydl_opts["concurrent_fragment_downloads"] = args.concurrency
    if args.since:
        ydl_opts["dateafter"] = ytdlp_date(args.since)
    if args.until:
        ydl_opts["datebefore"] = ytdlp_date(args.until)
    if args.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (args.cookies_from_browser,)
    if args.sleep_requests:
        ydl_opts["sleep_interval_requests"] = args.sleep_requests
    if args.sleep_interval:
        ydl_opts["sleep_interval"] = args.sleep_interval
    if args.max_sleep_interval:
        ydl_opts["max_sleep_interval"] = args.max_sleep_interval
    if args.youtube_client:
        ydl_opts.setdefault("extractor_args", {})
        ydl_opts["extractor_args"].setdefault("youtube", {})
        ydl_opts["extractor_args"]["youtube"]["player_client"] = [args.youtube_client]

    downloaded_count = {"n": 0}
    max_total = args.max if isinstance(args.max, int) and args.max > 0 else None

    def hook(d):
        if d.get("status") == "finished":
            downloaded_count["n"] += 1
            if max_total and downloaded_count["n"] >= max_total:
                raise KeyboardInterrupt

    ydl_opts["progress_hooks"] = [hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for u in urls:
                print(f"\n=== Processing: {u} ===")
                ydl.download([u])
                if max_total and downloaded_count["n"] >= max_total:
                    break
    except KeyboardInterrupt:
        print("\nReached max download limit for this channel; stopping.")


def load_channels_from_url(url: str) -> List[str]:
    print(f"\nFetching channel list from {url} ...")
    with urllib.request.urlopen(url) as response:
        data = response.read().decode("utf-8")
    return [line.strip() for line in data.splitlines() if line.strip() and not line.strip().startswith("#")]


def main() -> int:
    args = parse_args()

    if not args.url and not args.channels_file and not args.channels_url:
        print("Error: You must provide either --url, --channels-file, or --channels-url", file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)

    if args.channels_file:
        if not os.path.exists(args.channels_file):
            print(f"Error: channels file not found: {args.channels_file}", file=sys.stderr)
            return 1
        with open(args.channels_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                download_channel(line, args)

    elif args.channels_url:
        urls = load_channels_from_url(args.channels_url)
        for line in urls:
            download_channel(line, args)

    else:
        download_channel(args.url, args)

    print("\nAll done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
