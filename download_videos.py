#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_videos.py

Download videos using pre-scanned metadata from scan_channels.py.
This avoids triggering metadata rate limits during the download phase.

Usage:
    python download_videos.py --metadata metadata.json
    python download_videos.py --metadata metadata.json --output ./downloads
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Set

import download_channel_videos as downloader


def load_metadata(metadata_path: str) -> Dict:
    """Load metadata cache from JSON file."""

    if not os.path.exists(metadata_path):
        print(f"Error: Metadata file not found: {metadata_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: Failed to parse metadata JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    # Validate structure
    if not isinstance(data, dict):
        print("Error: Invalid metadata format (expected JSON object)", file=sys.stderr)
        sys.exit(1)

    if "channels" not in data:
        print("Error: Invalid metadata format (missing 'channels' key)", file=sys.stderr)
        sys.exit(1)

    return data


def build_video_url_list(metadata: Dict, args: argparse.Namespace) -> List[str]:
    """Build list of video URLs from metadata, respecting filters."""

    video_urls: List[str] = []
    archive_ids: Set[str] = set()

    # Load download archive if available
    if args.archive and os.path.exists(args.archive):
        archive_ids = downloader._load_download_archive(args.archive)

    channels = metadata.get("channels", [])

    for channel in channels:
        if channel.get("error"):
            print(f"[skip] Skipping channel {channel.get('url')} (scan error: {channel.get('error')})")
            continue

        videos = channel.get("videos", [])
        channel_url = channel.get("url", "unknown")

        for video in videos:
            video_id = video.get("video_id")
            if not video_id:
                continue

            # Check if already downloaded
            if video_id in archive_ids:
                continue

            # Build video URL
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            video_urls.append(video_url)

    return video_urls


def download_from_metadata(metadata: Dict, args: argparse.Namespace) -> None:
    """Download videos from metadata cache."""

    print("\n" + "=" * 70)
    print("Loading metadata...")
    print("=" * 70)

    total_channels = metadata.get("total_channels", 0)
    total_videos = metadata.get("total_videos", 0)
    scan_date = metadata.get("scan_date", "unknown")

    print(f"Metadata scan date: {scan_date}")
    print(f"Total channels: {total_channels}")
    print(f"Total videos in cache: {total_videos}")

    # Build video URL list
    video_urls = build_video_url_list(metadata, args)

    print(f"Videos to download (after archive filtering): {len(video_urls)}")
    print("=" * 70)

    if not video_urls:
        print("\nNo videos to download (all videos already in archive).")
        return

    # Download videos
    print("\n[download] Starting download phase...")
    print("[download] Using cached metadata (no additional metadata requests)\n")

    # Create a pseudo-source for downloading
    # We'll use the download_videos_from_urls function
    downloader.download_videos_from_urls(video_urls, args)

    print("\n" + "=" * 70)
    print("Download phase complete!")
    print("=" * 70)


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Download videos using pre-scanned metadata from scan_channels.py."
    )

    # Input
    parser.add_argument(
        "--metadata",
        required=True,
        help="Path to metadata JSON file (from scan_channels.py)",
    )

    # Output
    parser.add_argument(
        "--output",
        default="./downloads",
        help="Directory where videos will be stored (default: ./downloads)",
    )
    parser.add_argument(
        "--archive",
        default=None,
        help="Path to yt-dlp download archive (default: <output>/.download-archive.txt)",
    )

    # Download options
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Maximum videos to download",
    )
    parser.add_argument(
        "--rate-limit",
        default=None,
        help="Limit download rate (passed to yt-dlp)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Concurrent fragment downloads (passed to yt-dlp)",
    )
    parser.add_argument(
        "--skip-subtitles",
        action="store_true",
        help="Disable subtitle downloads",
    )
    parser.add_argument(
        "--skip-thumbs",
        action="store_true",
        help="Disable thumbnail downloads",
    )
    parser.add_argument(
        "--format",
        default=None,
        help="Format selector for yt-dlp",
    )
    parser.add_argument(
        "--merge-output-format",
        default=None,
        help="Container for merged downloads",
    )

    # Authentication
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Reuse cookies from the specified browser",
    )
    parser.add_argument(
        "--allow-restricted",
        action="store_true",
        help="Allow restricted/private videos when authentication permits",
    )

    # Rate limiting
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=2.0,
        help="Seconds to sleep between HTTP requests (default: 2.0)",
    )
    parser.add_argument(
        "--sleep-interval",
        type=float,
        default=3.0,
        help="Minimum randomized sleep between downloads (default: 3.0)",
    )
    parser.add_argument(
        "--max-sleep-interval",
        type=float,
        default=8.0,
        help="Maximum randomized sleep between downloads (default: 8.0)",
    )

    # YouTube client options
    parser.add_argument(
        "--youtube-client",
        choices=downloader.PLAYER_CLIENT_CHOICES,
        default=None,
        help="Force a specific YouTube client",
    )
    parser.add_argument(
        "--youtube-fetch-po-token",
        choices=["auto", "always", "never"],
        default=None,
        help="Control PO token fetching behaviour",
    )
    parser.add_argument(
        "--youtube-po-token",
        action="append",
        default=[],
        metavar="CLIENT.CONTEXT+TOKEN",
        help="Provide pre-generated PO tokens",
    )
    parser.add_argument(
        "--youtube-player-params",
        default=None,
        help="Override Innertube player params",
    )
    parser.add_argument(
        "--bgutil-provider",
        choices=downloader.BGUTIL_PROVIDER_CHOICES,
        default=None,
        help="Select BGUtil PO token provider",
    )
    parser.add_argument(
        "--bgutil-http-base-url",
        default=None,
        help="Override BGUtil HTTP provider base URL",
    )
    parser.add_argument(
        "--bgutil-http-disable-innertube",
        dest="bgutil_http_disable_innertube",
        action="store_true",
        help="Disable Innertube attestation for BGUtil HTTP provider",
    )
    parser.add_argument(
        "--bgutil-http-enable-innertube",
        dest="bgutil_http_disable_innertube",
        action="store_false",
        help="Enable Innertube attestation for BGUtil HTTP provider",
    )
    parser.set_defaults(bgutil_http_disable_innertube=None)
    parser.add_argument(
        "--bgutil-script-path",
        default=None,
        help="Path to the BGUtil script provider",
    )

    # Proxy options
    parser.add_argument(
        "--proxy",
        default=None,
        help="Use a single proxy for all requests (e.g., http://proxy.example.com:8080 or socks5://127.0.0.1:1080)",
    )
    parser.add_argument(
        "--proxy-file",
        default=None,
        help="Path to a file containing proxy URLs (one per line). Proxies will be rotated randomly.",
    )

    # Failure handling
    parser.add_argument(
        "--failure-limit",
        type=downloader.positive_int,
        default=downloader.DEFAULT_FAILURE_LIMIT,
        help="Number of failed downloads allowed per client before switching",
    )

    args = parser.parse_args(argv)

    # Apply authentication defaults
    downloader.apply_authentication_defaults(args)

    # Set default archive if not specified
    if not args.archive:
        args.archive = os.path.join(args.output, ".download-archive.txt")

    # Ensure output directory exists
    os.makedirs(args.output, exist_ok=True)

    # Set defaults for attributes required by build_ydl_options
    args.since = None
    args.until = None
    args.no_shorts = False

    return args


def main(argv=None) -> int:
    """Main entry point."""

    args = parse_args(argv)

    print("=" * 70)
    print("YouTube Video Downloader (Metadata-based)")
    print("=" * 70)
    print(f"Metadata file: {args.metadata}")
    print(f"Output directory: {args.output}")
    print(f"Archive file: {args.archive}")
    print("=" * 70)

    # Load metadata
    metadata = load_metadata(args.metadata)

    # Download videos
    download_from_metadata(metadata, args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
