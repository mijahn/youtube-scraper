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
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Set, Tuple

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


@dataclass
class DownloadAttempt:
    downloaded: int
    video_unavailable_errors: int
    other_errors: int
    stopped_due_to_limit: bool = False
    downloaded_ids: Set[str] = field(default_factory=set)


class DownloadLogger:
    """Custom logger that tracks repeated 'Video unavailable' errors."""

    def __init__(self) -> None:
        self.video_unavailable_errors = 0
        self.other_errors = 0

    def _print(self, message: str, file=sys.stdout) -> None:
        print(message, file=file)

    @staticmethod
    def _ensure_text(message) -> str:
        if isinstance(message, bytes):
            return message.decode("utf-8", "ignore")
        return str(message)

    def debug(self, message) -> None:  # yt-dlp calls this
        pass

    def info(self, message) -> None:
        self._print(self._ensure_text(message))

    def warning(self, message) -> None:
        self._print(self._ensure_text(message), file=sys.stderr)

    def error(self, message) -> None:
        text = self._ensure_text(message)
        lowered = text.lower()
        if "video unavailable" in lowered or "content isn't available" in lowered or "content is not available" in lowered:
            self.video_unavailable_errors += 1
        else:
            self.other_errors += 1
        self._print(text, file=sys.stderr)


class SourceType(Enum):
    CHANNEL = "channel"
    PLAYLIST = "playlist"
    VIDEO = "video"


@dataclass(frozen=True)
class Source:
    kind: SourceType
    url: str

    def build_download_urls(self, include_shorts: bool = True) -> List[str]:
        normalized = normalize_url(self.url)

        if self.kind is SourceType.CHANNEL:
            urls: List[str]
            if re.search(r"/(videos|shorts|streams|live)$", normalized):
                urls = [normalized]
            else:
                urls = [normalized + "/videos"]

            if include_shorts:
                shorts_url = normalized + "/shorts"
                if shorts_url not in urls:
                    urls.append(shorts_url)
            return urls

        return [normalized]


def normalize_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("missing URL")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = "https://" + cleaned.lstrip("/")

    return cleaned.rstrip("/")


def parse_source_line(line: str) -> Optional[Source]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    prefix_map = {
        "channel": SourceType.CHANNEL,
        "channels": SourceType.CHANNEL,
        "ch": SourceType.CHANNEL,
        "playlist": SourceType.PLAYLIST,
        "list": SourceType.PLAYLIST,
        "video": SourceType.VIDEO,
        "vid": SourceType.VIDEO,
    }

    if ":" in stripped:
        prefix, rest = stripped.split(":", 1)
        kind_key = prefix.strip().lower()
        if kind_key in prefix_map:
            url = rest.strip()
            if not url:
                raise ValueError("missing URL after prefix")
            return Source(prefix_map[kind_key], url)

    # Default to channel when no known prefix is supplied.
    return Source(SourceType.CHANNEL, stripped)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download videos from YouTube channels, playlists, or single videos using yt-dlp."
    )
    parser.add_argument(
        "--url",
        help=(
            "Single source URL. Prefix with 'channel:', 'playlist:', or 'video:' to override autodetection"
            " (default assumes channel)."
        ),
    )
    parser.add_argument(
        "--channels-file",
        help="Path to a local text file with one source per line (supports optional 'channel:', 'playlist:', 'video:' prefixes)",
    )
    parser.add_argument(
        "--channels-url",
        help=(
            "URL to a remote channels.txt file (e.g., GitHub raw link). Each non-comment line"
            " can optionally start with 'channel:', 'playlist:', or 'video:'."
        ),
    )
    parser.add_argument("--output", default="./downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("--archive", default=None, help="Path to a download archive file to skip already downloaded videos")
    parser.add_argument("--since", default=None, help="Only download videos uploaded on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="Only download videos uploaded on/before this date (YYYY-MM-DD)")
    parser.add_argument("--max", type=int, default=None, help="Stop after downloading N videos per channel")
    parser.add_argument(
        "--no-shorts",
        action="store_true",
        help="Exclude /shorts tab when downloading channel sources",
    )
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
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=300.0,
        help="When using --channels-file, seconds between checks for updates (default: 300)",
    )
    return parser.parse_args()


def ytdlp_date(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise SystemExit(f"Invalid date '{s}'. Use YYYY-MM-DD.")


def build_ydl_options(args, player_client: Optional[str], logger: DownloadLogger, hook) -> dict:
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
        "logger": logger,
        "progress_hooks": [hook],
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
    if player_client:
        ydl_opts.setdefault("extractor_args", {})
        ydl_opts["extractor_args"].setdefault("youtube", {})
        ydl_opts["extractor_args"]["youtube"]["player_client"] = [player_client]

    return ydl_opts


def run_download_attempt(
    urls: List[str],
    args,
    player_client: Optional[str],
    max_total: Optional[int],
    skip_ids: Optional[Set[str]] = None,
) -> DownloadAttempt:
    logger = DownloadLogger()
    downloaded = 0
    stopped_due_to_limit = False
    downloaded_ids: Set[str] = set()

    skip_ids = set(skip_ids or [])

    def hook(d):
        nonlocal downloaded, stopped_due_to_limit
        if d.get("status") == "finished":
            downloaded += 1
            info = d.get("info_dict") or {}
            video_id = info.get("id")
            if video_id:
                downloaded_ids.add(video_id)
                skip_ids.add(video_id)
            if max_total and downloaded >= max_total:
                stopped_due_to_limit = True
                raise KeyboardInterrupt

    ydl_opts = build_ydl_options(args, player_client, logger, hook)

    if skip_ids:
        def match_filter(info_dict, *, incomplete):
            video_id = info_dict.get("id")
            if video_id and video_id in skip_ids:
                return "already-downloaded"
            return None

        ydl_opts["match_filter"] = match_filter

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for u in urls:
                print(f"\n=== Processing: {u} ===")
                ydl.download([u])
                if stopped_due_to_limit:
                    break
    except KeyboardInterrupt:
        if stopped_due_to_limit:
            print("\nReached max download limit for this source; stopping.")
        else:
            raise

    return DownloadAttempt(
        downloaded=downloaded,
        video_unavailable_errors=logger.video_unavailable_errors,
        other_errors=logger.other_errors,
        stopped_due_to_limit=stopped_due_to_limit,
        downloaded_ids=downloaded_ids,
    )


def download_source(source: Source, args) -> None:
    try:
        urls = source.build_download_urls(include_shorts=not args.no_shorts)
        display_url = normalize_url(source.url)
    except ValueError as exc:
        print(f"Skipping {source.kind.value} source due to invalid URL: {exc}", file=sys.stderr)
        return

    print(f"\n=== Starting downloads for {source.kind.value}: {display_url} ===")
    max_total = args.max if isinstance(args.max, int) and args.max > 0 else None

    client_attempts: List[Optional[str]]
    if args.youtube_client:
        client_attempts = [args.youtube_client]
    else:
        client_attempts = ["web", "android", "ios", "tv"]

    downloaded_ids: Set[str] = set()

    for idx, client in enumerate(client_attempts):
        result = run_download_attempt(urls, args, client, max_total, skip_ids=downloaded_ids)
        downloaded_ids.update(result.downloaded_ids)

        if result.stopped_due_to_limit:
            break

        if args.youtube_client:
            break

        if result.other_errors > 0 or result.video_unavailable_errors == 0:
            break

        if idx < len(client_attempts) - 1:
            next_client = client_attempts[idx + 1]
            print(
                "\nEncountered only 'Video unavailable' errors using the"
                f" {client!r} client. Retrying with {next_client!r}..."
            )


def load_sources_from_url(url: str) -> List[Source]:
    print(f"\nFetching source list from {url} ...")
    with urllib.request.urlopen(url) as response:
        data = response.read().decode("utf-8")
    sources: List[Source] = []
    for idx, line in enumerate(data.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parsed = parse_source_line(stripped)
        except ValueError as exc:
            raise SystemExit(f"Failed to parse line {idx} from {url}: {exc}")
        if parsed:
            sources.append(parsed)
    return sources


def load_sources_from_file(path: str) -> Tuple[List[Source], List[str]]:
    sources: List[Source] = []
    raw_lines: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                parsed = parse_source_line(stripped)
            except ValueError as exc:
                raise ValueError(f"{path}:{idx}: {exc}") from exc
            if parsed:
                sources.append(parsed)
                raw_lines.append(stripped)
    return sources, raw_lines


def watch_channels_file(path: str, args) -> None:
    interval = args.watch_interval if args.watch_interval and args.watch_interval > 0 else 300.0
    last_mtime = None
    last_contents: Optional[List[str]] = None

    print(f"Watching {path} for updates (checking every {interval} seconds)...")

    while True:
        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            print(f"channels file not found: {path}. Waiting for it to appear...")
            time.sleep(interval)
            continue

        if last_mtime is None or mtime != last_mtime:
            try:
                sources, raw_lines = load_sources_from_file(path)
            except OSError as exc:
                print(f"Failed to read {path}: {exc}")
                time.sleep(interval)
                continue
            except ValueError as exc:
                print(exc)
                time.sleep(interval)
                continue

            if not sources:
                print(f"No sources found in {path}.")
            elif raw_lines != last_contents:
                if last_contents is None:
                    print("Initial channel list loaded. Starting downloads...")
                else:
                    print("Detected update to channel list. Re-running downloads...")
                for source in sources:
                    download_source(source, args)
                last_contents = raw_lines
            else:
                print(f"{os.path.basename(path)} timestamp changed but content is the same; skipping downloads.")

            last_mtime = mtime

        time.sleep(interval)


def main() -> int:
    args = parse_args()

    if not args.url and not args.channels_file and not args.channels_url:
        print("Error: You must provide either --url, --channels-file, or --channels-url", file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)

    if args.channels_file:
        try:
            watch_channels_file(args.channels_file, args)
        except KeyboardInterrupt:
            print("\nStopping channel watcher.")
            return 0

    elif args.channels_url:
        sources = load_sources_from_url(args.channels_url)
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
