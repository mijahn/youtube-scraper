#!/usr/bin/env python3
"""Interactive command-line interface for managing YouTube downloads."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

import download_channel_videos as downloader


@dataclass
class InterfaceConfig:
    """Runtime configuration for the interactive interface."""

    channels_file: str
    args: argparse.Namespace
    state_path: str


@dataclass
class SourceStatus:
    """Summary information about a source after scanning metadata."""

    source: downloader.Source
    label: str
    display_url: str
    total_videos: int
    downloaded_videos: int
    pending_videos: int
    pending_ids: Set[str]


@dataclass
class ScanResult:
    """Outcome of scanning all configured sources."""

    statuses: List[SourceStatus]
    new_sources: List[str]
    raw_lines: List[str]


def state_path_for_channels(channels_path: str) -> str:
    """Return the location used to remember previously seen sources."""

    directory = os.path.dirname(os.path.abspath(channels_path)) or os.getcwd()
    return os.path.join(directory, ".channels_state.json")


def load_known_sources(path: str) -> List[str]:
    """Load previously-seen sources from *path*."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        print(f"Warning: Failed to parse state file {path}: {exc}", file=sys.stderr)
        return []

    entries = data.get("sources", [])
    return [str(item) for item in entries]


def save_known_sources(path: str, sources: Sequence[str]) -> None:
    """Persist the provided *sources* list to *path*."""

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    payload = {"sources": list(sources)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def detect_new_sources(previous: Iterable[str], current: Iterable[str]) -> List[str]:
    """Return the sorted list of entries present in *current* but not *previous*."""

    previous_set = {entry.strip() for entry in previous if entry.strip()}
    current_set = {entry.strip() for entry in current if entry.strip()}
    return sorted(current_set - previous_set)


def build_args_from_options(options: argparse.Namespace) -> argparse.Namespace:
    """Construct an argument namespace compatible with downloader helpers."""

    args = argparse.Namespace(
        url=None,
        channels_file=options.channels_file,
        channels_url=None,
        output=options.output,
        archive=options.archive,
        since=options.since,
        until=options.until,
        max=options.max,
        no_shorts=options.no_shorts,
        rate_limit=options.rate_limit,
        concurrency=options.concurrency,
        skip_subtitles=options.skip_subtitles,
        skip_thumbs=options.skip_thumbs,
        format=options.format,
        merge_output_format=options.merge_output_format,
        cookies_from_browser=options.cookies_from_browser,
        sleep_requests=options.sleep_requests,
        sleep_interval=options.sleep_interval,
        max_sleep_interval=options.max_sleep_interval,
        allow_restricted=options.allow_restricted,
        youtube_client=options.youtube_client,
        youtube_fetch_po_token=options.youtube_fetch_po_token,
        youtube_po_token=list(options.youtube_po_token or []),
        youtube_player_params=options.youtube_player_params,
        bgutil_provider=options.bgutil_provider,
        bgutil_http_base_url=options.bgutil_http_base_url,
        bgutil_http_disable_innertube=options.bgutil_http_disable_innertube,
        bgutil_script_path=options.bgutil_script_path,
        bgutil_provider_candidates=[],
        bgutil_provider_resolved="disabled",
        watch_interval=options.watch_interval,
    )

    downloader.apply_authentication_defaults(args)

    if not args.archive:
        args.archive = os.path.join(args.output, ".download-archive.txt")

    os.makedirs(args.output, exist_ok=True)
    return args


def _first_player_client(args: argparse.Namespace) -> Optional[str]:
    if args.youtube_client:
        return args.youtube_client
    return downloader.DEFAULT_PLAYER_CLIENTS[0] if downloader.DEFAULT_PLAYER_CLIENTS else None


def _scan_single_source(
    source: downloader.Source,
    args: argparse.Namespace,
    archive_ids: Set[str],
    player_client: Optional[str],
) -> Optional[SourceStatus]:
    try:
        urls = source.build_download_urls(include_shorts=not args.no_shorts)
        display_url = downloader.normalize_url(source.url)
    except ValueError as exc:
        print(
            f"Skipping source due to invalid URL ({source.url!r}): {exc}",
            file=sys.stderr,
        )
        return None

    video_ids = downloader.collect_all_video_ids(urls, args, player_client)
    downloaded_ids = archive_ids & video_ids
    pending_ids = video_ids - archive_ids

    label = downloader.summarize_source_label(source, display_url)
    return SourceStatus(
        source=source,
        label=label,
        display_url=display_url,
        total_videos=len(video_ids),
        downloaded_videos=len(downloaded_ids),
        pending_videos=len(pending_ids),
        pending_ids=pending_ids,
    )


def perform_scan(config: InterfaceConfig, *, update_state: bool) -> Optional[ScanResult]:
    """Collect metadata for every configured source."""

    try:
        sources, raw_lines = downloader.load_sources_from_file(config.channels_file)
    except FileNotFoundError:
        print(f"channels file not found: {config.channels_file}")
        return None
    except ValueError as exc:
        print(exc)
        return None

    previous = load_known_sources(config.state_path)
    newly_added = detect_new_sources(previous, raw_lines)

    archive_ids = downloader._load_download_archive(config.args.archive)
    player_client = _first_player_client(config.args)

    statuses: List[SourceStatus] = []
    for source in sources:
        status = _scan_single_source(source, config.args, archive_ids, player_client)
        if status:
            statuses.append(status)

    if update_state:
        save_known_sources(config.state_path, raw_lines)

    return ScanResult(statuses=statuses, new_sources=newly_added, raw_lines=raw_lines)


def print_scan_summary(scan: ScanResult) -> None:
    if scan.new_sources:
        print("\nNew sources detected since last scan:")
        for line in scan.new_sources:
            print(f"  - {line}")
    else:
        print("\nNo newly added sources detected.")

    if not scan.statuses:
        print("No sources to analyze.")
        return

    print("\nSource summary:")
    for idx, status in enumerate(scan.statuses, start=1):
        print(
            f"  {idx}. {status.label} -> total: {status.total_videos}, "
            f"downloaded: {status.downloaded_videos}, pending: {status.pending_videos}"
        )


def handle_option_one(config: InterfaceConfig) -> Optional[ScanResult]:
    scan = perform_scan(config, update_state=True)
    if scan:
        print_scan_summary(scan)
    return scan


def _prompt_choice(limit: int) -> Optional[int]:
    while True:
        raw = input("Select a source number (or 'b' to go back): ").strip().lower()
        if raw in {"b", "back", "q", "quit"}:
            return None
        if raw.isdigit():
            value = int(raw)
            if 1 <= value <= limit:
                return value
        print(f"Please enter a number between 1 and {limit}, or 'b' to go back.")


def handle_option_two(config: InterfaceConfig) -> None:
    scan = perform_scan(config, update_state=False)
    if not scan:
        return

    if not scan.statuses:
        print("No sources configured in channels file.")
        return

    print("\nAvailable sources:")
    for idx, status in enumerate(scan.statuses, start=1):
        print(
            f"  {idx}. {status.label} -> pending videos: {status.pending_videos}"
        )

    choice = _prompt_choice(len(scan.statuses))
    if choice is None:
        return

    selected = scan.statuses[choice - 1]
    if selected.pending_videos == 0:
        print(
            f"All videos for {selected.label} are already downloaded according to the archive."
        )
        return

    print(
        f"\nStarting download for {selected.label}. Pending videos: {selected.pending_videos}"
    )
    downloader.download_source(selected.source, config.args)


def handle_option_three(config: InterfaceConfig) -> None:
    scan = handle_option_one(config)
    if not scan or not scan.statuses:
        return

    pending_sources = [status for status in scan.statuses if status.pending_videos > 0]
    if not pending_sources:
        print("\nAll sources are fully downloaded. Nothing to do.")
        return

    for status in pending_sources:
        print(
            f"\nDownloading pending videos for {status.label} (count: {status.pending_videos})"
        )
        downloader.download_source(status.source, config.args)


def parse_interface_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive helper for youtube-scraper downloads."
    )
    parser.add_argument(
        "--channels-file",
        default="channels.txt",
        help="Path to channels.txt file (default: channels.txt)",
    )
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
    parser.add_argument(
        "--no-shorts",
        action="store_true",
        help="Skip scanning the /shorts tab for channel sources",
    )
    parser.add_argument("--since", default=None, help="Only consider videos uploaded on/after this date")
    parser.add_argument("--until", default=None, help="Only consider videos uploaded on/before this date")
    parser.add_argument("--max", type=int, default=None, help="Maximum videos to download per source")
    parser.add_argument("--rate-limit", default=None, help="Limit download rate (passed to yt-dlp)")
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
    parser.add_argument("--format", default=None, help="Format selector for yt-dlp")
    parser.add_argument(
        "--merge-output-format",
        default=None,
        help="Container for merged downloads",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Reuse cookies from the specified browser",
    )
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=None,
        help="Seconds to sleep between HTTP requests",
    )
    parser.add_argument(
        "--sleep-interval",
        type=float,
        default=None,
        help="Minimum randomized sleep between downloads",
    )
    parser.add_argument(
        "--max-sleep-interval",
        type=float,
        default=None,
        help="Maximum randomized sleep between downloads",
    )
    parser.add_argument(
        "--allow-restricted",
        action="store_true",
        help="Allow restricted/private videos when authentication permits",
    )
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
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=None,
        help="Polling interval used by the non-interactive downloader (accepted for compatibility)",
    )
    return parser.parse_args(argv)


def run_menu(config: InterfaceConfig) -> None:
    while True:
        print(
            "\nPlease choose an option:\n"
            "  1. Check for new videos\n"
            "  2. Download videos from a specific source\n"
            "  3. Download all pending videos\n"
            "  q. Quit\n"
        )
        choice = input("Enter your choice: ").strip().lower()

        if choice in {"1", "one"}:
            handle_option_one(config)
        elif choice in {"2", "two"}:
            handle_option_two(config)
        elif choice in {"3", "three"}:
            handle_option_three(config)
        elif choice in {"q", "quit", "exit"}:
            print("Goodbye!")
            return
        else:
            print("Unrecognized option. Please try again.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    options = parse_interface_args(argv)
    args = build_args_from_options(options)
    state_path = state_path_for_channels(options.channels_file)
    config = InterfaceConfig(
        channels_file=options.channels_file,
        args=args,
        state_path=state_path,
    )
    run_menu(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
