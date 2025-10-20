#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_channels.py

Standalone metadata scanner for YouTube channels.
Slowly scans channels for video metadata to avoid rate limiting.

Usage:
    python scan_channels.py --channels-file channels.txt --output metadata.json
    python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set

import download_channel_videos as downloader


@dataclass
class ChannelMetadata:
    """Metadata for a single channel/source."""

    url: str
    kind: str
    label: str
    scan_timestamp: str
    videos: List[Dict[str, Optional[str]]]
    total_videos: int
    error: Optional[str] = None


@dataclass
class MetadataCache:
    """Container for all scanned metadata."""

    scan_date: str
    channels: List[ChannelMetadata]
    total_videos: int
    total_channels: int


def scan_single_source(
    source: downloader.Source,
    args: argparse.Namespace,
    player_client: Optional[str],
    request_interval: float,
) -> ChannelMetadata:
    """Scan a single source and return its metadata."""

    try:
        urls = source.build_download_urls(include_shorts=not args.no_shorts)
        display_url = downloader.normalize_url(source.url)
    except ValueError as exc:
        print(f"Error: Invalid URL ({source.url!r}): {exc}", file=sys.stderr)
        return ChannelMetadata(
            url=source.url,
            kind=source.kind.value,
            label=source.url,
            scan_timestamp=datetime.now().isoformat(),
            videos=[],
            total_videos=0,
            error=str(exc),
        )

    print(f"[scan] Fetching metadata for {display_url}")
    print(f"[scan] Request interval: {request_interval}s (to avoid rate limiting)")

    # Override sleep_requests to use the configured interval
    args.sleep_requests = request_interval

    try:
        video_entries = downloader.collect_all_video_ids(urls, args, player_client)

        # Convert VideoMetadata objects to dicts
        videos = [
            {"video_id": entry.video_id, "title": entry.title}
            for entry in video_entries
        ]

        label = downloader.summarize_source_label(source, display_url)

        print(f"[scan] Found {len(videos)} videos in {display_url}")

        return ChannelMetadata(
            url=display_url,
            kind=source.kind.value,
            label=label,
            scan_timestamp=datetime.now().isoformat(),
            videos=videos,
            total_videos=len(videos),
        )

    except Exception as exc:
        print(f"Error scanning {display_url}: {exc}", file=sys.stderr)
        return ChannelMetadata(
            url=display_url,
            kind=source.kind.value,
            label=display_url,
            scan_timestamp=datetime.now().isoformat(),
            videos=[],
            total_videos=0,
            error=str(exc),
        )


def scan_all_channels(
    args: argparse.Namespace,
    request_interval: float,
) -> MetadataCache:
    """Scan all channels and return cached metadata."""

    # Load sources
    sources: List[downloader.Source]
    try:
        if args.channels_url:
            sources, _ = downloader.load_sources_from_url(args.channels_url)
        elif args.channels_file:
            sources, _ = downloader.load_sources_from_file(args.channels_file)
        else:
            print("Error: Either --channels-file or --channels-url must be provided", file=sys.stderr)
            sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Channels file not found: {args.channels_file}", file=sys.stderr)
        sys.exit(1)
    except downloader.RemoteSourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Get player client
    player_client: Optional[str] = None
    if args.youtube_client:
        player_client = args.youtube_client
    elif downloader.DEFAULT_PLAYER_CLIENTS:
        player_client = downloader.DEFAULT_PLAYER_CLIENTS[0]

    # Scan each source
    total_sources = len(sources)
    channel_metadata: List[ChannelMetadata] = []
    total_videos = 0

    for idx, source in enumerate(sources, start=1):
        print(f"\n[scan {idx}/{total_sources}] Scanning {source.url}")

        metadata = scan_single_source(source, args, player_client, request_interval)
        channel_metadata.append(metadata)

        if not metadata.error:
            total_videos += metadata.total_videos

        # Sleep between sources to avoid rate limiting (except after the last one)
        if idx < total_sources:
            print(f"[scan] Waiting {request_interval}s before next source...")
            time.sleep(request_interval)

    return MetadataCache(
        scan_date=datetime.now().isoformat(),
        channels=channel_metadata,
        total_videos=total_videos,
        total_channels=len(channel_metadata),
    )


def save_metadata(cache: MetadataCache, output_path: str) -> None:
    """Save metadata cache to JSON file."""

    # Convert to dict
    data = {
        "scan_date": cache.scan_date,
        "total_channels": cache.total_channels,
        "total_videos": cache.total_videos,
        "channels": [
            {
                "url": ch.url,
                "kind": ch.kind,
                "label": ch.label,
                "scan_timestamp": ch.scan_timestamp,
                "total_videos": ch.total_videos,
                "videos": ch.videos,
                "error": ch.error,
            }
            for ch in cache.channels
        ],
    }

    # Ensure directory exists
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # Write to file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n[scan] Metadata saved to {output_path}")


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Scan YouTube channels for video metadata (slowly to avoid rate limiting)."
    )

    # Input source
    parser.add_argument(
        "--channels-file",
        default=None,
        help="Path to channels.txt file",
    )
    parser.add_argument(
        "--channels-url",
        default=None,
        help="URL to a remote channels.txt file",
    )

    # Output
    parser.add_argument(
        "--output",
        default="metadata.json",
        help="Output path for metadata JSON file (default: metadata.json)",
    )

    # Rate limiting
    parser.add_argument(
        "--request-interval",
        type=float,
        default=60.0,
        help="Seconds to wait between metadata requests (default: 60.0 - one per minute)",
    )

    # YouTube options
    parser.add_argument(
        "--no-shorts",
        action="store_true",
        help="Skip scanning the /shorts tab for channel sources",
    )
    parser.add_argument(
        "--youtube-client",
        choices=downloader.PLAYER_CLIENT_CHOICES,
        default=None,
        help="Force a specific YouTube client",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Reuse cookies from the specified browser",
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

    args = parser.parse_args(argv)

    # Validate input
    if not args.channels_file and not args.channels_url:
        parser.error("Either --channels-file or --channels-url must be provided")

    # Apply authentication defaults
    downloader.apply_authentication_defaults(args)

    return args


def main(argv=None) -> int:
    """Main entry point."""

    args = parse_args(argv)

    print("=" * 70)
    print("YouTube Channel Metadata Scanner")
    print("=" * 70)
    print(f"Request interval: {args.request_interval}s (slow scanning to avoid rate limits)")
    print(f"Output file: {args.output}")
    print("=" * 70)

    # Scan all channels
    cache = scan_all_channels(args, args.request_interval)

    # Save metadata
    save_metadata(cache, args.output)

    # Print summary
    print("\n" + "=" * 70)
    print("Scan Summary")
    print("=" * 70)
    print(f"Total channels scanned: {cache.total_channels}")
    print(f"Total videos found: {cache.total_videos}")

    failed_channels = [ch for ch in cache.channels if ch.error]
    if failed_channels:
        print(f"\nWarning: {len(failed_channels)} channel(s) failed to scan:")
        for ch in failed_channels:
            print(f"  - {ch.url}: {ch.error}")

    print("\nMetadata cache ready for download_videos.py")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
