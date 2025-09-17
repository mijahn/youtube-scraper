#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_channel_videos.py

Download all videos (and optionally Shorts) from a YouTube channel using yt-dlp.
"""

import argparse
import os
import sys
import re
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
    parser = argparse.ArgumentParser(description="Download all videos from a YouTube channel using yt-dlp.")
    parser.add_argument("--url", required=True, help="Channel URL (e.g., https://www.youtube.com/@SomeCreator)")
    parser.add_argument("--output", default="./downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("--archive", default=None, help="Path to a download archive file to skip already downloaded videos (e.g., ./downloads/downloaded.txt)")
    parser.add_argument("--since", default=None, help="Only download videos uploaded on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="Only download videos uploaded on/before this date (YYYY-MM-DD)")
    parser.add_argument("--max", type=int, default=None, help="Stop after downloading N videos (across tabs)")
    parser.add_argument("--no-shorts", action="store_true", help="Exclude /shorts tab (download only long-form videos)")
    parser.add_argument("--rate-limit", default=None, help="Limit download speed, e.g., 2M or 500K (passed to yt-dlp)")
    parser.add_argument("--concurrency", type=int, default=None, help="Concurrent fragment downloads (HLS/DASH). E.g., 5")
    parser.add_argument("--skip-subtitles", action="store_true", help="Do not download subtitles/auto-captions")
    parser.add_argument("--skip-thumbs", action="store_true", help="Do not download thumbnails")
    parser.add_argument("--cookies-from-browser", default=None, help="Use cookies from your browser (chrome, safari, firefox, edge, etc.) for authentication")
    return parser.parse_args()


def ytdlp_date(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise SystemExit(f"Invalid date '{s}'. Use YYYY-MM-DD.")


def main() -> int:
    args = parse_args()
    os.makedirs(args.output, exist_ok=True)

    urls = normalize_channel_urls(args.url, include_shorts=not args.no_shorts)

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
                print(f"
=== Processing: {u} ===")
                ydl.download([u])
                if max_total and downloaded_count["n"] >= max_total:
                    break
    except KeyboardInterrupt:
        print("
Reached max download limit; stopping.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print("
All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
