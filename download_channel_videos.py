#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_channel_videos.py

Download all videos (and optionally Shorts) from a YouTube channel using yt-dlp.

Usage (basic):
    python download_channel_videos.py --url https://www.youtube.com/@PatrickOakleyEllis

Recommended (create an output folder and keep a download archive to avoid duplicates):
    python download_channel_videos.py \
        --url https://www.youtube.com/@PatrickOakleyEllis \
        --output ./downloads \
        --archive ./downloads/downloaded.txt

Install dependencies first:
    pip install -r requirements.txt
    # For best results, also install ffmpeg (on macOS with Homebrew: brew install ffmpeg)

Notes:
- Uses yt-dlp (actively maintained fork of youtube-dl).
- Works with channel "handle" URLs (e.g. https://www.youtube.com/@SomeCreator) and classic channel IDs.
- Adds "/videos" (and optionally "/shorts") automatically to fetch all uploads.
- Uses a download archive to skip previously downloaded videos cleanly.
- Merges best video+audio into MP4 when possible.

Legal reminder: Only download content you have rights to download according to YouTube's ToS and local laws.
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
    """
    Given a YouTube channel URL (handle or channel ID), return a list of tab URLs to download.
    We include the /videos tab and (optionally) the /shorts tab to capture Shorts as well.
    """
    url = base_url.strip()
    # Ensure it looks like a YouTube channel/home URL.
    # Accepts URLs like:
    #   https://www.youtube.com/@SomeHandle
    #   https://www.youtube.com/channel/UCxxxxxxxxxxxx
    #   https://www.youtube.com/c/SomeCustomName
    # Also allow if the user already pasted a /videos or /shorts link.
    if not url.startswith("http"):
        url = "https://" + url

    # Strip trailing slashes for clean joining
    url = url.rstrip("/")

    # If it's already a specific tab, keep it
    if re.search(r"/(videos|shorts|streams|live)$", url):
        urls = [url]
    else:
        urls = [url + "/videos"]

    if include_shorts:
        shorts_url = url + "/shorts"
        # Avoid duplicates if user provided /shorts already
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
    parser.add_argument("--no-shorts", action="bool", nargs="?", const=True, default=False, help="Exclude /shorts tab (download only long-form videos)")
    parser.add_argument("--rate-limit", default=None, help="Limit download speed, e.g., 2M or 500K (passed to yt-dlp)")
    parser.add_argument("--concurrency", type=int, default=None, help="Concurrent fragment downloads (HLS/DASH). E.g., 5")
    parser.add_argument("--skip-subtitles", action="store_true", help="Do not download subtitles/auto-captions")
    parser.add_argument("--skip-thumbs", action="store_true", help="Do not download thumbnails")
    return parser.parse_args()


def ytdlp_date(s: str) -> str:
    """Convert YYYY-MM-DD -> YYYYMMDD for yt-dlp date filters."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise SystemExit(f"Invalid date '{s}'. Use YYYY-MM-DD.")


def main() -> int:
    args = parse_args()

    os.makedirs(args.output, exist_ok=True)

    urls = normalize_channel_urls(args.url, include_shorts=not args.no_shorts)

    # Build yt-dlp options
    ydl_opts = {
        # Best available video+audio merged, fallback to best single file
        "format": "bv*+ba/b",
        # Merge to mp4 when possible so most players handle it well
        "merge_output_format": "mp4",
        # Continue partially downloaded files
        "continuedl": True,
        # Don't stop on individual errors
        "ignoreerrors": "only_download",  # skip unavailable entries but continue
        # Nice, informative progress
        "noprogress": False,
        # Retries
        "retries": 10,
        "fragment_retries": 10,
        # Output template: Uploader/DATE-Title [id].ext
        "outtmpl": os.path.join(
            args.output,
            "%(uploader)s/%(upload_date>%Y-%m-%d)s - %(title).200B [%(id)s].%(ext)s",
        ),
        # Keep things tidy
        "restrictfilenames": True,
        "windowsfilenames": False,
        # Thumbnails & subtitles
        "writethumbnail": not args.skip_thumbs,
        "writesubtitles": not args.skip_subtitles,
        "writeautomaticsub": not args.skip_subtitles,
        "subtitleslangs": ["en.*,de.*,.*"],  # try English, German, else any available
        # Archive file to avoid re-downloading
        "download_archive": args.archive,
        # Quiet summary but still show progress bar
        "quiet": False,
        "no_warnings": False,
    }

    if args.rate_limit:
        ydl_opts["ratelimit"] = args.rate_limit

    if args.concurrency and args.concurrency > 0:
        # Control concurrent fragment downloads for DASH/HLS
        ydl_opts["concurrent_fragment_downloads"] = args.concurrency

    if args.since:
        ydl_opts["dateafter"] = ytdlp_date(args.since)
    if args.until:
        ydl_opts["datebefore"] = ytdlp_date(args.until)

    # Limit number of downloads across tabs, if requested
    # We'll keep a simple counter using the "progress_hooks"
    downloaded_count = {"n": 0}
    max_total = args.max if isinstance(args.max, int) and args.max > 0 else None

    def hook(d):
        if d.get("status") == "finished":
            downloaded_count["n"] += 1
            if max_total and downloaded_count["n"] >= max_total:
                # Abort by raising an exception the outer loop will catch
                raise KeyboardInterrupt

    ydl_opts["progress_hooks"] = [hook]

    # Run yt-dlp for each tab URL
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for u in urls:
                print(f"\n=== Processing: {u} ===")
                ydl.download([u])
                if max_total and downloaded_count["n"] >= max_total:
                    break
    except KeyboardInterrupt:
        print("\nReached max download limit; stopping.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print("\nAll done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
