#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_channel_videos.py

Download all videos (and optionally Shorts) from one or more YouTube channels using yt-dlp.
Supports:
- Single channel (--url)
- Local channels.txt (--channels-file)
- Remote channels.txt from GitHub or any URL (--channels-url)

This script has been refactored into a modular package structure.
All core functionality is now in the youtube_dl package.
"""

import os
import sys

from youtube_dl import (
    apply_authentication_defaults,
    download_source,
    load_sources_from_file,
    load_sources_from_url,
    parse_args,
    parse_source_line,
    run_health_check,
    watch_channels_file,
)
from youtube_dl.errors import RemoteSourceError


def main() -> int:
    """Main entry point for the YouTube channel downloader."""
    args = parse_args()
    apply_authentication_defaults(args)

    # Handle health check mode
    if args.health_check:
        return run_health_check(args)

    if not args.archive:
        args.archive = os.path.join(args.output, ".download-archive.txt")
    if args.archive:
        print(f"Using download archive: {args.archive}")

    if not args.url and not args.channels_file and not args.channels_url:
        print("Error: You must provide either --url, --channels-file, or --channels-url", file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)

    if args.channels_file:
        try:
            watch_channels_file(args.channels_file, args, download_source)
        except KeyboardInterrupt:
            print("\nStopping channel watcher.")
            return 0

    elif args.channels_url:
        try:
            sources, _ = load_sources_from_url(args.channels_url)
        except RemoteSourceError as exc:
            print(exc, file=sys.stderr)
            return 1
        for source in sources:
            download_source(source, args)

    else:
        parsed = parse_source_line(args.url.strip()) if args.url else None
        if not parsed:
            print("Error: Provided --url is empty or a comment", file=sys.stderr)
            return 1
        download_source(parsed, args)

    print("\nAll done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
