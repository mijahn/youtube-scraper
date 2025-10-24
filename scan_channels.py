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
from typing import Dict, List, Optional, Set, Tuple

import youtube_dl as downloader


def _log_with_timestamp(message: str) -> None:
    """Print a log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")
    sys.stdout.flush()  # Force immediate output


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


def load_existing_metadata(output_path: str) -> Optional[MetadataCache]:
    """Load existing metadata cache from JSON file if it exists."""
    if not os.path.exists(output_path):
        return None

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Convert dict data back to ChannelMetadata objects
        channels = []
        for ch_data in data.get("channels", []):
            channels.append(
                ChannelMetadata(
                    url=ch_data["url"],
                    kind=ch_data["kind"],
                    label=ch_data["label"],
                    scan_timestamp=ch_data["scan_timestamp"],
                    videos=ch_data["videos"],
                    total_videos=ch_data["total_videos"],
                    error=ch_data.get("error"),
                )
            )

        return MetadataCache(
            scan_date=data["scan_date"],
            channels=channels,
            total_videos=data["total_videos"],
            total_channels=data["total_channels"],
        )
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        _log_with_timestamp(f"[resume] ⚠ Warning: Could not load existing metadata from {output_path}: {exc}")
        _log_with_timestamp(f"[resume] Will start fresh scan")
        return None


def scan_single_source(
    source: downloader.Source,
    args: argparse.Namespace,
    player_client: Optional[str],
    request_interval: float,
    error_analyzer: Optional[downloader.ErrorAnalyzer] = None,
) -> ChannelMetadata:
    """Scan a single source and return its metadata."""

    _log_with_timestamp(f"[source] ▶ Starting scan of source: {source.url}")
    _log_with_timestamp(f"[source] Source type: {source.kind.value}")

    try:
        _log_with_timestamp(f"[source] Building URLs to scan...")
        urls = source.build_download_urls(include_shorts=not args.no_shorts)
        display_url = downloader.normalize_url(source.url)

        url_list = list(urls)
        _log_with_timestamp(f"[source] Built {len(url_list)} URL(s) to scan:")
        for i, url in enumerate(url_list, 1):
            # Extract the meaningful part (e.g., /videos, /shorts)
            url_suffix = url.split('@')[-1].split('/')[-1] if '/' in url else 'main'
            _log_with_timestamp(f"[source]   {i}. .../{url_suffix}")
    except ValueError as exc:
        _log_with_timestamp(f"[source] ❌ Error: Invalid URL: {exc}")
        if error_analyzer:
            error_analyzer.categorize_and_record(None, str(exc))
        return ChannelMetadata(
            url=source.url,
            kind=source.kind.value,
            label=source.url,
            scan_timestamp=datetime.now().isoformat(),
            videos=[],
            total_videos=0,
            error=str(exc),
        )

    _log_with_timestamp(f"[source] Starting video metadata extraction...")
    _log_with_timestamp(f"[source] Rate limiting: {request_interval}s between requests")

    # Override sleep_requests to use the configured interval
    args.sleep_requests = request_interval

    try:
        video_entries = downloader.collect_all_video_ids(
            urls, args, player_client, error_analyzer=error_analyzer
        )

        # Convert VideoMetadata objects to dicts
        videos = [
            {"video_id": entry.video_id, "title": entry.title}
            for entry in video_entries
        ]

        label = downloader.summarize_source_label(source, display_url)

        _log_with_timestamp(f"[source] ✓ Scan complete!")
        _log_with_timestamp(f"[source] Summary for {display_url}:")
        _log_with_timestamp(f"[source]   • Total videos found: {len(videos)}")
        _log_with_timestamp(f"[source]   • Source label: {label}")

        # Show a sample of video titles if we have any
        if videos:
            sample_size = min(3, len(videos))
            _log_with_timestamp(f"[source]   • Sample videos:")
            for i, video in enumerate(videos[:sample_size], 1):
                title = video['title'] or '(no title)'
                title_short = title[:60] + '...' if len(title) > 60 else title
                _log_with_timestamp(f"[source]     {i}. {title_short}")

        return ChannelMetadata(
            url=display_url,
            kind=source.kind.value,
            label=label,
            scan_timestamp=datetime.now().isoformat(),
            videos=videos,
            total_videos=len(videos),
        )

    except Exception as exc:
        _log_with_timestamp(f"[source] ❌ Error scanning {display_url}: {exc}")
        if error_analyzer:
            error_analyzer.categorize_and_record(None, str(exc))
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
) -> Tuple[MetadataCache, downloader.ErrorAnalyzer]:
    """Scan all channels and return cached metadata with error analysis."""

    # Initialize error analyzer
    error_analyzer = downloader.ErrorAnalyzer()

    # Set up error log path
    output_dir = os.path.dirname(args.output) if os.path.dirname(args.output) else "."
    error_log_path = os.path.join(output_dir, "scan_errors.log")
    error_analyzer.set_error_log_path(error_log_path)

    print(f"Error logging enabled: {error_log_path}")

    # Load existing metadata for resume capability (unless --force is used)
    existing_metadata: Optional[MetadataCache] = None
    existing_urls: Set[str] = set()

    if not args.force:
        _log_with_timestamp(f"[resume] Checking for existing metadata in {args.output}...")
        existing_metadata = load_existing_metadata(args.output)

        if existing_metadata:
            _log_with_timestamp(f"[resume] ✓ Loaded existing metadata:")
            _log_with_timestamp(f"[resume]   • Previously scanned: {existing_metadata.total_channels} channel(s)")
            _log_with_timestamp(f"[resume]   • Total videos in cache: {existing_metadata.total_videos}")
            _log_with_timestamp(f"[resume]   • Last scan date: {existing_metadata.scan_date}")

            # Build set of already-scanned URLs (normalized)
            for ch in existing_metadata.channels:
                # Normalize URL for comparison
                normalized = downloader.normalize_url(ch.url)
                existing_urls.add(normalized)

            _log_with_timestamp(f"[resume] Resume mode: Will skip already-scanned sources")
        else:
            _log_with_timestamp(f"[resume] No existing metadata found - starting fresh scan")
    else:
        _log_with_timestamp(f"[resume] Force mode enabled - rescanning all sources")

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

    # Get player client - only set if explicitly requested by user
    # Otherwise, pass None to allow automatic client rotation
    player_client: Optional[str] = None
    if args.youtube_client:
        player_client = args.youtube_client
        print(f"Using forced player client: {player_client}")
    else:
        # Don't force a specific client - allow rotation through all available clients
        print(f"Using automatic client rotation ({len(downloader.DEFAULT_PLAYER_CLIENTS)} clients available: {', '.join(downloader.DEFAULT_PLAYER_CLIENTS)})")

    # Scan each source
    total_sources = len(sources)
    new_channel_metadata: List[ChannelMetadata] = []
    new_videos = 0
    skipped_count = 0

    for idx, source in enumerate(sources, start=1):
        _log_with_timestamp(f"\n{'='*50}")
        _log_with_timestamp(f"[scan {idx}/{total_sources}] Scanning {source.url}")
        _log_with_timestamp(f"{'='*50}")

        # Check if this source was already scanned
        try:
            normalized_url = downloader.normalize_url(source.url)
        except ValueError:
            # Invalid URL, will be handled by scan_single_source
            normalized_url = source.url

        if normalized_url in existing_urls:
            _log_with_timestamp(f"[resume] ⏭ Skipping - already scanned")
            _log_with_timestamp(f"[resume] (Use --force to rescan all sources)")
            skipped_count += 1
            continue

        scan_start = time.time()
        metadata = scan_single_source(
            source, args, player_client, request_interval, error_analyzer
        )
        scan_duration = time.time() - scan_start
        _log_with_timestamp(f"[scan {idx}/{total_sources}] Completed in {scan_duration:.1f} seconds")

        new_channel_metadata.append(metadata)

        if not metadata.error:
            new_videos += metadata.total_videos
            _log_with_timestamp(f"[scan] New videos from this scan: {new_videos}")

        # Sleep between sources to avoid rate limiting (except after the last one)
        remaining = total_sources - idx - skipped_count
        if remaining > 0:
            _log_with_timestamp(f"[scan] Waiting {request_interval}s before next source...")
            next_start_time = datetime.now().timestamp() + request_interval
            _log_with_timestamp(f"[scan] Next scan will start at approximately {datetime.fromtimestamp(next_start_time).strftime('%H:%M:%S')}")
            time.sleep(request_interval)
            _log_with_timestamp(f"[scan] Wait complete, moving to next source...")

    # Merge with existing metadata if resuming
    if existing_metadata:
        _log_with_timestamp(f"\n[resume] Merging results:")
        _log_with_timestamp(f"[resume]   • Existing channels: {len(existing_metadata.channels)}")
        _log_with_timestamp(f"[resume]   • Newly scanned: {len(new_channel_metadata)}")
        _log_with_timestamp(f"[resume]   • Skipped (already scanned): {skipped_count}")

        # Combine old and new channels
        all_channels = existing_metadata.channels + new_channel_metadata
        combined_total_videos = existing_metadata.total_videos + new_videos

        _log_with_timestamp(f"[resume]   • Total channels in output: {len(all_channels)}")
        _log_with_timestamp(f"[resume]   • Total videos in output: {combined_total_videos}")

        return (
            MetadataCache(
                scan_date=datetime.now().isoformat(),
                channels=all_channels,
                total_videos=combined_total_videos,
                total_channels=len(all_channels),
            ),
            error_analyzer,
        )
    else:
        # Fresh scan - no existing data
        return (
            MetadataCache(
                scan_date=datetime.now().isoformat(),
                channels=new_channel_metadata,
                total_videos=new_videos,
                total_channels=len(new_channel_metadata),
            ),
            error_analyzer,
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

    # Resume control
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rescan of all sources, ignoring existing metadata (disables resume)",
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

    args = parser.parse_args(argv)

    # Validate input
    if not args.channels_file and not args.channels_url:
        parser.error("Either --channels-file or --channels-url must be provided")

    # Apply authentication defaults
    downloader.apply_authentication_defaults(args)

    # Override BGUtil and PO token settings for metadata scanning
    # These features can cause significant delays and are usually not needed for basic metadata extraction
    if not hasattr(args, 'bgutil_provider') or args.bgutil_provider is None:
        args.bgutil_provider = 'disabled'
        args.bgutil_provider_candidates = []
        args.bgutil_provider_resolved = 'disabled'
    if not hasattr(args, 'youtube_fetch_po_token') or args.youtube_fetch_po_token is None:
        args.youtube_fetch_po_token = 'auto'  # Changed from 'always' to 'auto'

    # Set defaults for attributes required by build_ydl_options
    # These aren't used during metadata scanning but are checked by the builder
    args.skip_thumbs = True  # Don't download thumbs during metadata scan
    args.skip_subtitles = True  # Don't download subtitles during metadata scan
    args.allow_restricted = False
    args.sleep_interval = 0.0
    args.max_sleep_interval = 0.0
    args.archive = None
    args.rate_limit = None
    args.concurrency = None
    args.since = None
    args.until = None
    args.merge_output_format = None
    args.format = None

    return args


def main(argv=None) -> int:
    """Main entry point."""

    args = parse_args(argv)

    print("=" * 70)
    print("YouTube Channel Metadata Scanner (Enhanced)")
    print("=" * 70)
    print(f"Request interval: {args.request_interval}s (slow scanning to avoid rate limits)")
    print(f"Output file: {args.output}")
    print(f"Features: Resume capability, retry logic, client rotation, exponential backoff")
    print("=" * 70)

    # Show existing metadata summary if available
    if os.path.exists(args.output):
        _log_with_timestamp(f"\n[metadata] Existing metadata file found: {args.output}")
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                data = json.load(f)

            channels_count = len(data.get("channels", []))
            videos_count = data.get("total_videos", 0)
            scan_date = data.get("scan_date", "unknown")

            # Count successful vs failed channels
            successful = sum(1 for ch in data.get("channels", []) if not ch.get("error"))
            failed = channels_count - successful

            _log_with_timestamp(f"[metadata] Summary of existing data:")
            _log_with_timestamp(f"[metadata]   • Total channels: {channels_count} ({successful} successful, {failed} failed)")
            _log_with_timestamp(f"[metadata]   • Total videos: {videos_count}")
            _log_with_timestamp(f"[metadata]   • Last scan: {scan_date}")

            if args.force:
                _log_with_timestamp(f"[metadata] --force flag set: Will rescan ALL channels")
            else:
                _log_with_timestamp(f"[metadata] Resume mode: Will skip already-scanned channels")
                _log_with_timestamp(f"[metadata] (Use --force to rescan everything)")
        except (json.JSONDecodeError, OSError) as exc:
            _log_with_timestamp(f"[metadata] ⚠ Could not read existing metadata: {exc}")
    else:
        _log_with_timestamp(f"\n[metadata] No existing metadata found - starting fresh scan")

    print("=" * 70)

    # Scan all channels
    cache, error_analyzer = scan_all_channels(args, args.request_interval)

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

    # Print error analysis
    if error_analyzer:
        error_analyzer.print_summary()

    print("\nMetadata cache ready for download_videos.py")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
