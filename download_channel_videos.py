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
import urllib.error
import urllib.parse
import urllib.request
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError, ExtractorError
except ImportError:
    print("yt-dlp is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


@dataclass
class DownloadAttempt:
    downloaded: int
    video_unavailable_errors: int
    other_errors: int
    stopped_due_to_limit: bool = False


class DownloadLogger:
    """Custom logger that tracks repeated 'Video unavailable' errors."""

    UNAVAILABLE_FRAGMENTS = (
        "video unavailable",
        "content isn't available",
        "content is not available",
        "channel members",
        "members-only",
        "requires purchase",
        "http error 410",
        "sign in to confirm your age",
        "age-restricted",
        "this video is private",
        "the uploader has not made this video available",
        "this video can only be played",
    )

    IGNORED_FRAGMENTS = (
        "does not have a shorts tab",
    )

    def __init__(self) -> None:
        self.video_unavailable_errors = 0
        self.other_errors = 0
        self.current_url: Optional[str] = None
        self.current_client: Optional[str] = None

    def set_context(self, url: Optional[str], client: Optional[str]) -> None:
        self.current_url = url
        self.current_client = client

    def _format_with_context(self, message: str) -> str:
        context_parts = []
        if self.current_client:
            context_parts.append(f"client={self.current_client}")
        if self.current_url:
            context_parts.append(f"url={self.current_url}")
        if context_parts:
            return f"[{' '.join(context_parts)}] {message}"
        return message

    def _print(self, message: str, file=sys.stdout) -> None:
        print(self._format_with_context(message), file=file)

    def _handle_message(self, text: str) -> None:
        lowered = text.lower()
        if any(fragment in lowered for fragment in self.IGNORED_FRAGMENTS):
            return

        if any(fragment in lowered for fragment in self.UNAVAILABLE_FRAGMENTS):
            self.video_unavailable_errors += 1
        else:
            self.other_errors += 1

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
        self._print(text, file=sys.stderr)
        self._handle_message(text)

    def record_exception(self, exc: Exception) -> None:
        text = self._ensure_text(str(exc))
        self._print(text, file=sys.stderr)
        self._handle_message(text)


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


DEFAULT_PLAYER_CLIENTS: Tuple[str, ...] = ("web", "android", "ios", "tv")


def normalize_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("missing URL")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = "https://" + cleaned.lstrip("/")

    return cleaned.rstrip("/")


def infer_source_kind(url: str) -> SourceType:
    """Best-effort inference for the source type based on the URL structure."""

    try:
        normalized = normalize_url(url)
    except ValueError:
        # Invalid/empty URLs will be handled later by callers.
        return SourceType.CHANNEL

    parsed = urllib.parse.urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path or ""
    query = urllib.parse.parse_qs(parsed.query)

    if host.endswith("youtu.be"):
        return SourceType.VIDEO

    if query.get("list"):
        return SourceType.PLAYLIST

    lowered_path = path.lower()

    playlist_indicators = (
        "/playlist",
        "/playlists",
        "/watchlist",
    )
    if any(indicator in lowered_path for indicator in playlist_indicators):
        return SourceType.PLAYLIST

    video_prefixes = (
        "/watch",
        "/shorts/",
        "/live/",
        "/clip/",
        "/v/",
    )
    if any(lowered_path.startswith(prefix) for prefix in video_prefixes):
        return SourceType.VIDEO

    channel_prefixes = (
        "/@",
        "/channel/",
        "/c/",
        "/user/",
    )
    if any(lowered_path.startswith(prefix) for prefix in channel_prefixes):
        return SourceType.CHANNEL

    return SourceType.CHANNEL


def parse_source_line(line: str) -> Optional[Source]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    comment_match = re.search(r"\s#", stripped)
    if comment_match:
        stripped = stripped[: comment_match.start()].rstrip()
        if not stripped:
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

    inferred_kind = infer_source_kind(stripped)
    return Source(inferred_kind, stripped)


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
        "--allow-restricted",
        action="store_true",
        help=(
            "Download restricted videos (subscriber-only, Premium, private, etc.)"
            " when authentication is available"
        ),
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


def _combine_match_filters(
    filters: Iterable[Callable[[dict], Optional[str]]]
) -> Optional[Callable[[dict], Optional[str]]]:
    filters = tuple(f for f in filters if f is not None)
    if not filters:
        return None

    def combined(info_dict: dict) -> Optional[str]:
        for flt in filters:
            result = flt(info_dict)
            if result:
                return result
        return None

    return combined


def build_ydl_options(
    args,
    player_client: Optional[str],
    logger: DownloadLogger,
    hook,
    additional_filters: Optional[Iterable[Callable[[dict], Optional[str]]]] = None,
) -> dict:
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
    filters: List[Callable[[dict], Optional[str]]] = []

    if not args.allow_restricted:

        def restricted_match_filter(info_dict: dict) -> Optional[str]:
            reasons = []

            availability = info_dict.get("availability")
            if availability:
                normalized = str(availability).lower()
                availability_reasons = {
                    "needs_auth": "requires authentication",
                    "subscriber_only": "channel members only",
                    "premium_only": "YouTube Premium only",
                    "members_only": "channel members only",
                    "private": "marked as private",
                    "login_required": "requires authentication",
                }
                reason = availability_reasons.get(normalized)
                if reason:
                    reasons.append(reason)
                elif normalized not in {"public", "unlisted"}:
                    reasons.append(f"availability is '{availability}'")

            if info_dict.get("is_private"):
                reasons.append("marked as private")
            if info_dict.get("requires_subscription"):
                reasons.append("requires channel subscription")
            if info_dict.get("subscriber_only"):
                reasons.append("channel members only")
            if info_dict.get("premium_only"):
                reasons.append("YouTube Premium only")

            if reasons:
                unique_reasons: List[str] = []
                seen = set()
                for reason in reasons:
                    if reason not in seen:
                        unique_reasons.append(reason)
                        seen.add(reason)
                joined_reasons = "; ".join(unique_reasons)
                video_id = info_dict.get("id") or "unknown id"
                return f"{video_id} skipped: {joined_reasons}"

            return None

        filters.append(restricted_match_filter)

    if player_client:
        ydl_opts.setdefault("extractor_args", {})
        ydl_opts["extractor_args"].setdefault("youtube", {})
        ydl_opts["extractor_args"]["youtube"]["player_client"] = [player_client]

    if additional_filters:
        filters.extend(additional_filters)

    combined_filter = _combine_match_filters(filters)
    if combined_filter:
        ydl_opts["match_filter"] = combined_filter

    debug_parts = [f"format={ydl_opts['format']}"]
    if player_client:
        debug_parts.append(f"player_client={player_client}")
    if args.rate_limit:
        debug_parts.append(f"ratelimit={args.rate_limit}")
    if args.concurrency:
        debug_parts.append(f"concurrency={args.concurrency}")
    if args.sleep_requests:
        debug_parts.append(f"sleep_requests={args.sleep_requests}")
    if args.sleep_interval:
        debug_parts.append(f"sleep_interval={args.sleep_interval}")
    if args.max_sleep_interval:
        debug_parts.append(f"max_sleep_interval={args.max_sleep_interval}")
    if args.since or args.until:
        debug_parts.append(
            "date_range="
            + ":".join(
                filter(
                    None,
                    [
                        f"since={ydl_opts.get('dateafter')}" if args.since else None,
                        f"until={ydl_opts.get('datebefore')}" if args.until else None,
                    ],
                )
            )
        )

    print(
        "Constructed yt-dlp options: "
        + ", ".join(debug_parts)
        + f", write_subtitles={not args.skip_subtitles}, write_thumbnails={not args.skip_thumbs}"
    )

    return ydl_opts


def run_download_attempt(
    urls: List[str],
    args,
    player_client: Optional[str],
    max_total: Optional[int],
    downloaded_ids: Optional[Set[str]],
) -> DownloadAttempt:
    logger = DownloadLogger()
    downloaded = 0
    stopped_due_to_limit = False
    seen_ids: Set[str]
    if downloaded_ids is not None:
        seen_ids = downloaded_ids
    else:
        seen_ids = set()

    client_label = player_client if player_client else "default"
    active_url: Optional[str] = None
    format_logged_ids: Set[str] = set()
    format_descriptions: Dict[str, str] = {}
    video_labels: Dict[str, str] = {}

    def describe_format_entry(entry: Optional[dict]) -> str:
        if not isinstance(entry, dict):
            return "unknown format"

        parts: List[str] = []
        format_id = entry.get("format_id")
        if format_id:
            parts.append(str(format_id))

        width = entry.get("width")
        height = entry.get("height")
        if width and height:
            parts.append(f"{width}x{height}")
        elif height:
            parts.append(f"{height}p")

        fps = entry.get("fps")
        if fps:
            parts.append(f"{fps}fps")

        vcodec = entry.get("vcodec")
        if vcodec and vcodec != "none":
            parts.append(vcodec)

        acodec = entry.get("acodec")
        if acodec and acodec != "none":
            parts.append(acodec)

        abr = entry.get("abr")
        if abr:
            parts.append(f"{abr}k")

        ext = entry.get("ext")
        if ext:
            parts.append(ext)

        if not parts:
            return "unknown format"
        return " ".join(str(p) for p in parts if p)

    def describe_formats(info: Optional[dict], payload: dict) -> str:
        info_dict = info if isinstance(info, dict) else {}
        requested = info_dict.get("requested_formats")
        if isinstance(requested, list) and requested:
            return " + ".join(describe_format_entry(fmt) for fmt in requested)

        if info_dict:
            return describe_format_entry(info_dict)
        return describe_format_entry(payload)

    def describe_video(info: Optional[dict]) -> str:
        if not isinstance(info, dict):
            return "unknown video"
        video_id = info.get("id")
        title = info.get("title")
        if video_id and title:
            return f"{title} ({video_id})"
        if title:
            return str(title)
        if video_id:
            return str(video_id)
        return "unknown video"

    def context_prefix() -> str:
        parts = [f"client={client_label}"]
        if active_url:
            parts.append(f"url={active_url}")
        return f"[{' '.join(parts)}] "

    print(
        "Starting download attempt with "
        f"client={client_label}, max_total={'no-limit' if max_total is None else max_total}, "
        f"urls={urls}"
    )

    def hook(d):
        nonlocal downloaded, stopped_due_to_limit
        status = d.get("status")
        info = d.get("info_dict")
        video_id = info.get("id") if isinstance(info, dict) else None

        if status == "downloading":
            if video_id and video_id not in format_logged_ids:
                format_logged_ids.add(video_id)
                video_label = describe_video(info)
                format_text = describe_formats(info, d)
                if video_id:
                    format_descriptions[video_id] = format_text
                    video_labels[video_id] = video_label
                print(
                    f"{context_prefix()}Starting download for {video_label} using {format_text}",
                )
        elif status == "error":
            video_label = video_labels.get(video_id) or describe_video(info)
            format_text = format_descriptions.get(video_id)
            if not format_text:
                format_text = describe_formats(info, d)
            fragment_url = d.get("fragment_url")
            error_message = d.get("error") or d.get("message") or "unknown error"
            details = [f"Download error for {video_label}"]
            if format_text:
                details.append(f"formats: {format_text}")
            if fragment_url:
                details.append(f"fragment: {fragment_url}")
            details.append(f"yt-dlp said: {error_message}")
            logger.error(" | ".join(details))
            if video_id:
                format_logged_ids.discard(video_id)
                format_descriptions.pop(video_id, None)
                video_labels.pop(video_id, None)
        if status == "finished":
            info_id = None
            if isinstance(info, dict):
                info_id = info.get("id")
            if info_id:
                seen_ids.add(info_id)
                video_label = video_labels.get(info_id) or describe_video(info)
                format_text = format_descriptions.get(info_id)
                completion_parts = [f"Completed download for {video_label}"]
                if format_text:
                    completion_parts.append(f"formats: {format_text}")
                print(f"{context_prefix()}" + " | ".join(completion_parts))
                format_logged_ids.discard(info_id)
                format_descriptions.pop(info_id, None)
                video_labels.pop(info_id, None)
            downloaded += 1
            if max_total and downloaded >= max_total:
                stopped_due_to_limit = True
                raise KeyboardInterrupt

    extra_filters: List[Callable[[dict], Optional[str]]] = []

    if args.archive is None:
        # Avoid re-downloading videos that completed successfully during
        # earlier client attempts in this invocation.
        def match_filter(info_dict: dict) -> Optional[str]:
            video_id = info_dict.get("id") if isinstance(info_dict, dict) else None
            if video_id and video_id in seen_ids:
                return "Video already downloaded during previous client attempt"
            return None

        extra_filters.append(match_filter)

    ydl_opts = build_ydl_options(args, player_client, logger, hook, extra_filters)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for u in urls:
                active_url = u
                logger.set_context(active_url, client_label)
                print(f"\n=== Processing with client {client_label}: {u} ===")

                before_downloaded = downloaded
                before_unavailable = logger.video_unavailable_errors
                before_other = logger.other_errors
                encountered_exception = False

                try:
                    ydl.download([u])
                except (DownloadError, ExtractorError) as exc:
                    encountered_exception = True
                    logger.record_exception(exc)
                except Exception as exc:  # pragma: no cover - defensive safety net
                    encountered_exception = True
                    logger.record_exception(exc)
                finally:
                    after_downloaded = downloaded
                    after_unavailable = logger.video_unavailable_errors
                    after_other = logger.other_errors

                    delta_downloaded = after_downloaded - before_downloaded
                    delta_unavailable = after_unavailable - before_unavailable
                    delta_other = after_other - before_other

                    summary_parts = [f"{delta_downloaded} downloaded"]
                    if delta_unavailable:
                        summary_parts.append(f"{delta_unavailable} unavailable")
                    if delta_other:
                        summary_parts.append(f"{delta_other} other errors")
                    if not delta_unavailable and not delta_other:
                        summary_parts.append("no new errors")
                    if encountered_exception and not (delta_unavailable or delta_other):
                        summary_parts.append("see logs for details")
                    if stopped_due_to_limit:
                        summary_parts.append("stopped due to limit")

                    print(
                        f"{context_prefix()}URL summary: {u} -> {', '.join(summary_parts)}"
                    )

                    active_url = None

                if stopped_due_to_limit:
                    break

            logger.set_context(None, None)
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
    )


def format_attempt_summary(attempt: DownloadAttempt) -> str:
    parts = [f"{attempt.downloaded} downloaded"]
    if attempt.video_unavailable_errors:
        parts.append(f"{attempt.video_unavailable_errors} unavailable")
    if attempt.other_errors:
        parts.append(f"{attempt.other_errors} other errors")
    if attempt.stopped_due_to_limit:
        parts.append("stopped due to limit")
    return ", ".join(parts)


def download_source(source: Source, args) -> None:
    try:
        urls = source.build_download_urls(include_shorts=not args.no_shorts)
        display_url = normalize_url(source.url)
    except ValueError as exc:
        print(f"Skipping {source.kind.value} source due to invalid URL: {exc}", file=sys.stderr)
        return

    print(f"\n=== Starting downloads for {source.kind.value}: {display_url} ===")
    print(
        "Resolved download URLs: "
        + ", ".join(urls)
        + (" (shorts excluded)" if args.no_shorts else "")
    )
    max_total = args.max if isinstance(args.max, int) and args.max > 0 else None

    client_attempts: List[Optional[str]]
    if args.youtube_client:
        client_attempts = [args.youtube_client]
    else:
        client_attempts = list(DEFAULT_PLAYER_CLIENTS)

    downloaded_ids: Set[str] = set()
    total_downloaded = 0
    total_unavailable = 0
    total_other_errors = 0
    last_result: Optional[DownloadAttempt] = None

    for idx, client in enumerate(client_attempts):
        result = run_download_attempt(urls, args, client, max_total, downloaded_ids)
        last_result = result

        total_downloaded += result.downloaded
        total_unavailable += result.video_unavailable_errors
        total_other_errors += result.other_errors

        client_label = client if client else "default"
        print(
            f"Attempt summary using {client_label!r} client: {format_attempt_summary(result)}"
        )

        if result.stopped_due_to_limit:
            break

        if args.youtube_client:
            break

        should_retry = (
            result.other_errors == 0
            and result.video_unavailable_errors > 0
            and idx < len(client_attempts) - 1
        )

        if not should_retry:
            break

        next_client = client_attempts[idx + 1]
        print(
            "\nEncountered only 'Video unavailable' errors using the"
            f" {client!r} client. Retrying with {next_client!r}..."
        )

    if (
        not args.allow_restricted
        and total_downloaded == 0
        and total_unavailable > 0
        and total_other_errors == 0
    ):
        print(
            "\nAll attempts resulted in restricted or unavailable videos. "
            "Provide authentication (e.g., --cookies-from-browser) or rerun with "
            "--allow-restricted to skip them.",
            file=sys.stderr,
        )

    if last_result and total_other_errors > 0:
        print(
            f"Encountered {total_other_errors} download errors. See logs above for details.",
            file=sys.stderr,
        )


def load_sources_from_url(url: str) -> List[Source]:
    print(f"\nFetching source list from {url} ...")
    try:
        with urllib.request.urlopen(url) as response:
            data = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"Failed to fetch source list from {url}: {exc}", file=sys.stderr)
        raise SystemExit(1)
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
    print(f"Loaded {len(sources)} sources from remote list")
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
    print(f"Loaded {len(sources)} sources from {path}")
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
