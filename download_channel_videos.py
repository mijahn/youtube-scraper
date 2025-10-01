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
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError
    from yt_dlp.extractor.youtube import YoutubeIE
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
    except ModuleNotFoundError:  # Older yt-dlp releases expose the constant directly.
        from yt_dlp.extractor.youtube import INNERTUBE_CLIENTS
except ImportError:
    print("yt-dlp is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


@dataclass
class DownloadAttempt:
    downloaded: int
    video_unavailable_errors: int
    other_errors: int
    detected_video_ids: Set[str] = field(default_factory=set)
    downloaded_video_ids: Set[str] = field(default_factory=set)
    retryable_error_ids: Set[str] = field(default_factory=set)
    stopped_due_to_limit: bool = False
    failure_count: int = 0
    failure_limit_reached: bool = False


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

    RETRYABLE_FRAGMENTS = (
        "http error 403",
        "forbidden",
        "po token",
        "login required",
    )

    IGNORED_FRAGMENTS = (
        "does not have a shorts tab",
    )

    def __init__(
        self, failure_callback: Optional[Callable[[Optional[str]], None]] = None
    ) -> None:
        self.video_unavailable_errors = 0
        self.other_errors = 0
        self.current_url: Optional[str] = None
        self.current_client: Optional[str] = None
        self.current_video_id: Optional[str] = None
        self.retryable_error_ids: Set[str] = set()
        self._failure_callback = failure_callback
        self._last_reported_failure: Optional[Tuple[Optional[str], str]] = None

    def set_failure_callback(
        self, callback: Optional[Callable[[Optional[str]], None]]
    ) -> None:
        self._failure_callback = callback

    def set_context(
        self, url: Optional[str], client: Optional[str], video_id: Optional[str] = None
    ) -> None:
        self.current_url = url
        self.current_client = client
        self.current_video_id = video_id
        self._last_reported_failure = None

    def set_video(self, video_id: Optional[str]) -> None:
        if video_id != self.current_video_id:
            self._last_reported_failure = None
        self.current_video_id = video_id

    def _format_with_context(self, message: str) -> str:
        context_parts = []
        if self.current_client:
            context_parts.append(f"client={self.current_client}")
        if self.current_url:
            context_parts.append(f"url={self.current_url}")
        if self.current_video_id:
            context_parts.append(f"video_id={self.current_video_id}")
        if context_parts:
            return f"[{' '.join(context_parts)}] {message}"
        return message

    def _print(self, message: str, file=sys.stdout) -> None:
        print(self._format_with_context(message), file=file)

    def _handle_message(self, text: str) -> None:
        lowered = text.lower()
        if any(fragment in lowered for fragment in self.IGNORED_FRAGMENTS):
            return

        is_retryable = any(
            fragment in lowered for fragment in self.RETRYABLE_FRAGMENTS
        )
        if any(fragment in lowered for fragment in self.UNAVAILABLE_FRAGMENTS):
            self.video_unavailable_errors += 1
            self._last_reported_failure = None
            return

        key = (self.current_video_id, lowered)
        if key == self._last_reported_failure:
            return

        if is_retryable:
            if self.current_video_id:
                self.retryable_error_ids.add(self.current_video_id)
        else:
            self.other_errors += 1

        self._last_reported_failure = key
        if self._failure_callback:
            self._failure_callback(self.current_video_id)

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


def _collect_video_ids_from_info(info: object, dest: Set[str]) -> None:
    """Recursively extract video identifiers from yt-dlp metadata objects."""

    if info is None:
        return

    if isinstance(info, list):
        for entry in info:
            _collect_video_ids_from_info(entry, dest)
        return

    if not isinstance(info, dict):
        return

    info_type = info.get("_type")

    if info_type in {"playlist", "multi_video", "compat_list"}:
        entries = info.get("entries") or []
        _collect_video_ids_from_info(entries, dest)
        return

    if info_type == "url" and "entries" in info:
        _collect_video_ids_from_info(info.get("entries"), dest)

    video_id = info.get("id")
    if video_id:
        dest.add(str(video_id))


def collect_all_video_ids(
    urls: Iterable[str], args, player_client: Optional[str]
) -> Set[str]:
    """Fetch playlist metadata to determine every video ID for the given URLs."""

    logger = DownloadLogger()

    def noop_hook(_):
        return None

    ydl_opts = build_ydl_options(args, player_client, logger, noop_hook)

    ydl_opts["skip_download"] = True
    ydl_opts["quiet"] = True
    ydl_opts["no_warnings"] = True
    ydl_opts["progress_hooks"] = []
    ydl_opts["writethumbnail"] = False
    ydl_opts["writesubtitles"] = False
    ydl_opts["writeautomaticsub"] = False
    ydl_opts.pop("download_archive", None)
    ydl_opts.pop("match_filter", None)

    video_ids: Set[str] = set()

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for url in urls:
                try:
                    info = ydl.extract_info(url, download=False)
                except (DownloadError, ExtractorError) as exc:
                    logger.record_exception(exc)
                    continue
                except Exception as exc:  # pragma: no cover - defensive
                    logger.record_exception(exc)
                    continue
                _collect_video_ids_from_info(info, video_ids)
    except KeyboardInterrupt:
        raise

    return video_ids


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
            base_channel_url = normalized
            trailing_match = re.search(r"/(videos|shorts|streams|live)$", normalized)

            if trailing_match:
                urls = [normalized]
                base_channel_url = normalized[: -len(trailing_match.group(0))]
            else:
                urls = [normalized + "/videos"]

            if include_shorts:
                shorts_url = base_channel_url + "/shorts"
                if shorts_url not in urls:
                    urls.append(shorts_url)
            return urls

        return [normalized]


PLAYER_CLIENT_CHOICES: Tuple[str, ...] = tuple(
    sorted(client for client in INNERTUBE_CLIENTS if not client.startswith("_"))
)


class BgUtilProviderMode(Enum):
    AUTO = "auto"
    HTTP = "http"
    SCRIPT = "script"
    DISABLED = "disabled"


ENV_COOKIES_FROM_BROWSER = "YOUTUBE_SCRAPER_COOKIES_FROM_BROWSER"
ENV_PO_TOKENS = "YOUTUBE_SCRAPER_PO_TOKENS"
ENV_FETCH_PO_TOKEN = "YOUTUBE_SCRAPER_FETCH_PO_TOKEN"
ENV_BGUTIL_PROVIDER_MODE = "YOUTUBE_SCRAPER_BGUTIL_PROVIDER"
ENV_BGUTIL_HTTP_BASE_URL = "YOUTUBE_SCRAPER_BGUTIL_HTTP_BASE_URL"
ENV_BGUTIL_HTTP_DISABLE_INNERTUBE = "YOUTUBE_SCRAPER_BGUTIL_HTTP_DISABLE_INNERTUBE"
ENV_BGUTIL_SCRIPT_PATH = "YOUTUBE_SCRAPER_BGUTIL_SCRIPT_PATH"
DEFAULT_FETCH_PO_TOKEN_BEHAVIOR = "always"
DEFAULT_BGUTIL_PROVIDER_MODE = BgUtilProviderMode.AUTO.value
DEFAULT_BGUTIL_HTTP_BASE_URL = "http://127.0.0.1:4416"
BGUTIL_PROVIDER_CHOICES: Tuple[str, ...] = tuple(mode.value for mode in BgUtilProviderMode)


def _default_player_clients() -> Tuple[str, ...]:
    defaults = getattr(YoutubeIE, "_DEFAULT_CLIENTS", None)
    if defaults:
        return tuple(defaults)
    # Fallback to a sensible order if yt-dlp changes internals unexpectedly.
    preferred_order = ("tv", "web_safari", "web", "android", "ios")
    ordered_defaults = [client for client in preferred_order if client in PLAYER_CLIENT_CHOICES]
    if ordered_defaults:
        return tuple(ordered_defaults)
    return tuple(PLAYER_CLIENT_CHOICES[:3])


DEFAULT_PLAYER_CLIENTS: Tuple[str, ...] = _default_player_clients()

MAX_FAILURES_PER_CLIENT = 5


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
    parser.add_argument(
        "--format",
        default=None,
        help=(
            "Format selector passed to yt-dlp. Defaults to yt-dlp's native behaviour when omitted, "
            "which picks the best available combination without forcing a container."
        ),
    )
    parser.add_argument(
        "--merge-output-format",
        default=None,
        help=(
            "Container format for merged downloads (passed to yt-dlp). "
            "If omitted, yt-dlp will keep the original container when possible."
        ),
    )
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
        choices=PLAYER_CLIENT_CHOICES,
        default=None,
        help=(
            "Override the YouTube player client used by yt-dlp "
            "(default: yt-dlp's recommended clients)"
        ),
    )
    parser.add_argument(
        "--youtube-fetch-po-token",
        choices=["auto", "always", "never"],
        default=None,
        help=(
            "Control how yt-dlp fetches YouTube PO Tokens when required. "
            "Set to 'always' to proactively request tokens or 'never' to skip. "
            "(default: yt-dlp decides)."
        ),
    )
    parser.add_argument(
        "--youtube-po-token",
        action="append",
        default=[],
        metavar="CLIENT.CONTEXT+TOKEN",
        help=(
            "Provide a pre-generated PO Token to yt-dlp. May be passed multiple times. "
            "Useful when tokens are fetched externally."
        ),
    )
    parser.add_argument(
        "--youtube-player-params",
        default=None,
        help=(
            "Override the Innertube player params used by yt-dlp when requesting streams. "
            "Advanced option for troubleshooting format availability."
        ),
    )
    parser.add_argument(
        "--bgutil-provider",
        choices=BGUTIL_PROVIDER_CHOICES,
        default=None,
        help=(
            "Control how BGUtil PO Token providers are used. "
            "'auto' tries the local HTTP server first, 'http' forces the HTTP provider, "
            "'script' forces the Node.js script provider, and 'disabled' turns the integration off."
        ),
    )
    parser.add_argument(
        "--bgutil-http-base-url",
        default=None,
        help=(
            "Override the base URL for the BGUtil HTTP provider. "
            "Defaults to http://127.0.0.1:4416."
        ),
    )
    parser.add_argument(
        "--bgutil-http-disable-innertube",
        dest="bgutil_http_disable_innertube",
        action="store_true",
        help="Disable Innertube attestation when requesting PO tokens from the BGUtil HTTP provider.",
    )
    parser.add_argument(
        "--bgutil-http-enable-innertube",
        dest="bgutil_http_disable_innertube",
        action="store_false",
        help="Explicitly allow Innertube attestation when requesting PO tokens via the BGUtil HTTP provider.",
    )
    parser.set_defaults(bgutil_http_disable_innertube=None)
    parser.add_argument(
        "--bgutil-script-path",
        default=None,
        help=(
            "Path to the BGUtil generate_once.js script when using the script provider. "
            "Only used when the script provider is enabled."
        ),
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=300.0,
        help="When using --channels-file, seconds between checks for updates (default: 300)",
    )
    return parser.parse_args()


def _parse_po_token_env(value: str) -> List[str]:
    tokens: List[str] = []
    for part in re.split(r"[\n,]+", value):
        cleaned = part.strip()
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _normalize_env_str(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _env_flag(value: Optional[str]) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _apply_bgutil_provider_defaults(args, environ: Optional[Dict[str, str]]) -> None:
    if environ is None:
        environ = os.environ

    provider_value = getattr(args, "bgutil_provider", None)
    valid_modes = set(BGUTIL_PROVIDER_CHOICES)
    if provider_value is None:
        provider_env = _normalize_env_str(environ.get(ENV_BGUTIL_PROVIDER_MODE))
        if provider_env and provider_env in valid_modes:
            provider_mode = BgUtilProviderMode(provider_env)
        else:
            provider_mode = BgUtilProviderMode.AUTO
    else:
        provider_str = str(provider_value).strip().lower()
        if provider_str in valid_modes:
            provider_mode = BgUtilProviderMode(provider_str)
        else:
            provider_mode = BgUtilProviderMode.AUTO
    setattr(args, "bgutil_provider", provider_mode.value)

    base_url_value = getattr(args, "bgutil_http_base_url", None)
    if base_url_value is None:
        base_url_env = _normalize_env_str(environ.get(ENV_BGUTIL_HTTP_BASE_URL))
        base_url = base_url_env or DEFAULT_BGUTIL_HTTP_BASE_URL
    else:
        base_url_str = str(base_url_value).strip()
        base_url = base_url_str or DEFAULT_BGUTIL_HTTP_BASE_URL
    setattr(args, "bgutil_http_base_url", base_url)

    disable_innertube_value = getattr(args, "bgutil_http_disable_innertube", None)
    if disable_innertube_value is None:
        disable_flag = _env_flag(environ.get(ENV_BGUTIL_HTTP_DISABLE_INNERTUBE))
    else:
        disable_flag = bool(disable_innertube_value)
    setattr(args, "bgutil_http_disable_innertube", disable_flag)

    script_path_value = getattr(args, "bgutil_script_path", None)
    if script_path_value is None:
        script_env = _normalize_env_str(environ.get(ENV_BGUTIL_SCRIPT_PATH))
        script_path = os.path.expanduser(script_env) if script_env else None
    else:
        script_path = os.path.expanduser(str(script_path_value)) if script_path_value else None
    setattr(args, "bgutil_script_path", script_path)

    provider_candidates: List[str] = []
    if provider_mode == BgUtilProviderMode.AUTO:
        provider_candidates.append("http")
        if script_path and os.path.isfile(script_path):
            provider_candidates.append("script")
    elif provider_mode == BgUtilProviderMode.HTTP:
        provider_candidates.append("http")
    elif provider_mode == BgUtilProviderMode.SCRIPT:
        if script_path and os.path.isfile(script_path):
            provider_candidates.append("script")
        else:
            warning = "Configured BGUtil script provider path not found; disabling PO Token script provider."
            if script_path:
                warning += f" Missing file: {script_path}"
            print(warning, file=sys.stderr)
    else:  # DISABLED
        provider_candidates = []

    setattr(args, "bgutil_provider_candidates", provider_candidates)
    resolved = provider_candidates[0] if provider_candidates else "disabled"
    setattr(args, "bgutil_provider_resolved", resolved)


def apply_authentication_defaults(args, environ: Optional[Dict[str, str]] = None) -> None:
    """Populate authentication-related args from the environment when missing."""

    if environ is None:
        environ = os.environ

    if not getattr(args, "cookies_from_browser", None):
        env_cookie = environ.get(ENV_COOKIES_FROM_BROWSER, "").strip()
        if env_cookie:
            args.cookies_from_browser = env_cookie

    env_tokens_raw = environ.get(ENV_PO_TOKENS)
    parsed_tokens = _parse_po_token_env(env_tokens_raw) if env_tokens_raw else []

    existing_tokens = list(getattr(args, "youtube_po_token", []) or [])
    if parsed_tokens:
        merged_tokens = existing_tokens + parsed_tokens
        seen: Set[str] = set()
        unique_tokens: List[str] = []
        for token in merged_tokens:
            if token not in seen:
                seen.add(token)
                unique_tokens.append(token)
        args.youtube_po_token = unique_tokens
    elif not existing_tokens:
        args.youtube_po_token = existing_tokens

    fetch_choice = getattr(args, "youtube_fetch_po_token", None)
    if not fetch_choice:
        env_fetch = environ.get(ENV_FETCH_PO_TOKEN, "").strip().lower()
        if env_fetch in {"auto", "always", "never"}:
            fetch_choice = env_fetch
        else:
            fetch_choice = DEFAULT_FETCH_PO_TOKEN_BEHAVIOR
        args.youtube_fetch_po_token = fetch_choice

    _apply_bgutil_provider_defaults(args, environ)


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

    format_selector = getattr(args, "format", None)
    merge_format = getattr(args, "merge_output_format", None)

    if format_selector:
        ydl_opts["format"] = format_selector
    if merge_format:
        ydl_opts["merge_output_format"] = merge_format
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

    extractor_args: Dict[str, Dict[str, List[str]]] = {}
    youtube_extractor_args: Dict[str, List[str]] = {}

    if player_client:
        youtube_extractor_args["player_client"] = [player_client]
    if args.youtube_fetch_po_token:
        youtube_extractor_args["fetch_po_token"] = [args.youtube_fetch_po_token]
    if args.youtube_po_token:
        youtube_extractor_args["po_token"] = list(args.youtube_po_token)
    if args.youtube_player_params:
        youtube_extractor_args["player_params"] = [args.youtube_player_params]

    if youtube_extractor_args:
        extractor_args["youtube"] = youtube_extractor_args

    provider_candidates = list(getattr(args, "bgutil_provider_candidates", []))
    if provider_candidates:
        if "http" in provider_candidates:
            http_args: Dict[str, List[str]] = {}
            base_url = getattr(args, "bgutil_http_base_url", None)
            if base_url:
                http_args["base_url"] = [base_url]
            if getattr(args, "bgutil_http_disable_innertube", False):
                http_args["disable_innertube"] = ["1"]
            extractor_args["youtubepot-bgutilhttp"] = http_args
        if "script" in provider_candidates:
            script_args: Dict[str, List[str]] = {}
            script_path = getattr(args, "bgutil_script_path", None)
            if script_path:
                script_args["script_path"] = [script_path]
            extractor_args["youtubepot-bgutilscript"] = script_args

    if extractor_args:
        ydl_opts["extractor_args"] = extractor_args

    if additional_filters:
        filters.extend(additional_filters)

    combined_filter = _combine_match_filters(filters)
    if combined_filter:
        ydl_opts["match_filter"] = combined_filter

    if format_selector:
        debug_parts = [f"format={format_selector}"]
    else:
        debug_parts = ["format=yt-dlp-default"]
    if merge_format:
        debug_parts.append(f"merge_output_format={merge_format}")
    if player_client:
        debug_parts.append(f"player_client={player_client}")
    if args.youtube_fetch_po_token:
        debug_parts.append(f"fetch_po_token={args.youtube_fetch_po_token}")
    if args.youtube_po_token:
        debug_parts.append(f"po_tokens={len(args.youtube_po_token)} provided")
    if args.youtube_player_params:
        debug_parts.append("player_params=custom")
    if provider_candidates:
        debug_parts.append("bgutil_providers=" + "+".join(provider_candidates))
        if getattr(args, "bgutil_http_base_url", None):
            debug_parts.append(f"bgutil_http={getattr(args, 'bgutil_http_base_url')}")
        if getattr(args, "bgutil_http_disable_innertube", False):
            debug_parts.append("bgutil_http_disable_innertube=1")
        if "script" in provider_candidates:
            debug_parts.append("bgutil_script=enabled")
    else:
        debug_parts.append("bgutil_providers=disabled")
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
    target_video_ids: Optional[Set[str]] = None,
) -> DownloadAttempt:
    logger = DownloadLogger()
    downloaded = 0
    stopped_due_to_limit = False
    failure_limit_reached = False
    failure_events = 0
    seen_ids: Set[str]
    if downloaded_ids is not None:
        seen_ids = downloaded_ids
    else:
        seen_ids = set()
    detected_ids: Set[str] = set()
    completed_ids: Set[str] = set()
    failed_video_ids: Set[str] = set()

    client_label = player_client if player_client else "default"
    active_url: Optional[str] = None
    format_logged_ids: Set[str] = set()
    format_descriptions: Dict[str, str] = {}
    video_labels: Dict[str, str] = {}

    def record_video_detection(video_id: Optional[str]) -> None:
        if not video_id:
            return
        detected_ids.add(video_id)

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
        f"[client={client_label}] Starting download attempt "
        f"with max_total={'no-limit' if max_total is None else max_total}, urls={urls}"
    )

    def register_failure(
        failed_video_id: Optional[str], *, interrupt: bool
    ) -> None:
        nonlocal failure_limit_reached, failure_events
        if failure_limit_reached:
            return
        if failed_video_id and failed_video_id in completed_ids:
            return

        failure_events += 1
        if failed_video_id:
            failed_video_ids.add(failed_video_id)

        failure_total = failure_events
        if failure_total >= MAX_FAILURES_PER_CLIENT:
            failure_limit_reached = True
            if interrupt:
                raise DownloadCancelled(
                    f"Aborting client {client_label} after {failure_total} download failures"
                )

    logger.set_failure_callback(
        lambda video_id: register_failure(video_id, interrupt=True)
    )

    def hook(d):
        nonlocal downloaded, stopped_due_to_limit, failure_limit_reached
        status = d.get("status")
        info = d.get("info_dict")
        video_id = info.get("id") if isinstance(info, dict) else None

        if video_id:
            record_video_detection(video_id)

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
            logger.set_video(video_id)
            try:
                logger.error(" | ".join(details))
            finally:
                logger.set_video(None)
                if video_id:
                    format_logged_ids.discard(video_id)
                    format_descriptions.pop(video_id, None)
                    video_labels.pop(video_id, None)
        if status == "finished":
            info_id = None
            if isinstance(info, dict):
                info_id = info.get("id")
            if info_id:
                record_video_detection(info_id)
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
                completed_ids.add(info_id)
            downloaded += 1
            if max_total and downloaded >= max_total:
                stopped_due_to_limit = True
                raise KeyboardInterrupt
            logger.set_video(None)

    extra_filters: List[Callable[[dict], Optional[str]]] = []

    if args.archive is None:
        # Avoid re-downloading videos that completed successfully during
        # earlier client attempts in this invocation.
        def match_filter(info_dict: dict) -> Optional[str]:
            video_id = info_dict.get("id") if isinstance(info_dict, dict) else None
            if video_id:
                record_video_detection(video_id)
            if video_id and video_id in seen_ids:
                return "Video already downloaded during previous client attempt"
            return None

        extra_filters.append(match_filter)

    if target_video_ids:

        def retry_allowlist(info_dict: dict) -> Optional[str]:
            video_id = info_dict.get("id") if isinstance(info_dict, dict) else None
            if video_id:
                record_video_detection(video_id)
            if video_id and video_id not in target_video_ids:
                return "Video not selected for retry"
            return None

        extra_filters.append(retry_allowlist)

    ydl_opts = build_ydl_options(args, player_client, logger, hook, extra_filters)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for u in urls:
                active_url = u
                logger.set_context(active_url, client_label)
                print(
                    f"\n{context_prefix()}--- Starting downloads for URL: {u} ---"
                )

                before_downloaded = downloaded
                before_unavailable = logger.video_unavailable_errors
                before_other = logger.other_errors
                before_failures = failure_events
                encountered_exception = False

                try:
                    ydl.download([u])
                except DownloadCancelled:
                    encountered_exception = True
                    register_failure(None, interrupt=False)
                    # Failure threshold reached; no need to log extra noise.
                except (DownloadError, ExtractorError) as exc:
                    encountered_exception = True
                    register_failure(None, interrupt=False)
                    logger.record_exception(exc)
                except Exception as exc:  # pragma: no cover - defensive safety net
                    encountered_exception = True
                    register_failure(None, interrupt=False)
                    logger.record_exception(exc)
                finally:
                    after_downloaded = downloaded
                    after_unavailable = logger.video_unavailable_errors
                    after_other = logger.other_errors
                    after_failures = failure_events

                    delta_downloaded = after_downloaded - before_downloaded
                    delta_unavailable = after_unavailable - before_unavailable
                    delta_other = after_other - before_other
                    delta_failures = after_failures - before_failures

                    summary_parts = [f"{delta_downloaded} downloaded"]
                    if delta_unavailable:
                        summary_parts.append(f"{delta_unavailable} unavailable")
                    if delta_other:
                        summary_parts.append(f"{delta_other} other errors")
                    if delta_failures:
                        summary_parts.append(f"{delta_failures} failures")
                    if not delta_unavailable and not delta_other and not delta_failures:
                        summary_parts.append("no new errors")
                    if encountered_exception and not (delta_unavailable or delta_other):
                        summary_parts.append("see logs for details")
                    if stopped_due_to_limit:
                        summary_parts.append("stopped due to limit")

                    print(
                        f"{context_prefix()}URL summary: {u} -> {', '.join(summary_parts)}"
                    )

                    active_url = None
                    logger.set_video(None)

                if stopped_due_to_limit:
                    break
                if failure_limit_reached:
                    break

            logger.set_context(None, None)
            logger.set_video(None)
    except KeyboardInterrupt:
        if stopped_due_to_limit:
            print("\nReached max download limit for this source; stopping.")
        else:
            raise
    except DownloadCancelled:
        failure_limit_reached = True

    return DownloadAttempt(
        downloaded=downloaded,
        video_unavailable_errors=logger.video_unavailable_errors,
        other_errors=logger.other_errors,
        detected_video_ids=set(detected_ids),
        downloaded_video_ids=set(completed_ids),
        retryable_error_ids=set(logger.retryable_error_ids),
        stopped_due_to_limit=stopped_due_to_limit,
        failure_count=failure_events,
        failure_limit_reached=failure_limit_reached,
    )


def format_attempt_summary(attempt: DownloadAttempt) -> str:
    parts = [f"{attempt.downloaded} downloaded"]
    if attempt.video_unavailable_errors:
        parts.append(f"{attempt.video_unavailable_errors} unavailable")
    if attempt.other_errors:
        parts.append(f"{attempt.other_errors} other errors")
    if attempt.retryable_error_ids:
        parts.append(f"{len(attempt.retryable_error_ids)} retryable")
    if attempt.failure_count:
        parts.append(f"{attempt.failure_count} failures")
    if attempt.stopped_due_to_limit:
        parts.append("stopped due to limit")
    if attempt.failure_limit_reached:
        parts.append(
            f"reached failure limit ({MAX_FAILURES_PER_CLIENT})"
        )
    return ", ".join(parts)


def summarize_source_label(source: Source, display_url: str) -> str:
    if source.kind is SourceType.CHANNEL:
        handle_match = re.search(r"/(@[^/]+)", display_url)
        if handle_match:
            return handle_match.group(1)
        channel_match = re.search(r"/channel/([^/?]+)", display_url)
        if channel_match:
            return f"channel {channel_match.group(1)}"
    if source.kind is SourceType.PLAYLIST:
        parsed = urllib.parse.urlparse(display_url)
        if parsed.query:
            return f"playlist {parsed.query}"
    if source.kind is SourceType.VIDEO:
        parsed = urllib.parse.urlparse(display_url)
        if parsed.query:
            return f"video {parsed.query}"
    return display_url


def download_source(source: Source, args) -> None:
    try:
        urls = source.build_download_urls(include_shorts=not args.no_shorts)
        display_url = normalize_url(source.url)
    except ValueError as exc:
        print(f"Skipping {source.kind.value} source due to invalid URL: {exc}", file=sys.stderr)
        return

    summary_label = summarize_source_label(source, display_url)

    print(f"\n=== Starting downloads for {source.kind.value}: {display_url} ===")
    print(
        "Resolved download URLs: "
        + ", ".join(urls)
        + (" (shorts excluded)" if args.no_shorts else "")
    )
    max_total = args.max if isinstance(args.max, int) and args.max > 0 else None

    client_attempts: List[Optional[str]]
    available_clients = list(PLAYER_CLIENT_CHOICES)
    if args.youtube_client:
        preferred = args.youtube_client
        remaining = [client for client in available_clients if client != preferred]
        client_attempts = [preferred] + remaining
    else:
        default_sequence = list(DEFAULT_PLAYER_CLIENTS)
        additional = [client for client in available_clients if client not in default_sequence]
        client_attempts = default_sequence + additional

    downloaded_ids: Set[str] = set()
    metadata_video_ids = collect_all_video_ids(urls, args, client_attempts[0] if client_attempts else None)
    if metadata_video_ids:
        print(
            "\nMetadata scan detected"
            f" {len(metadata_video_ids)} video"
            f"{'s' if len(metadata_video_ids) != 1 else ''} before downloading."
        )
    detected_ids: Set[str] = set(metadata_video_ids)
    downloaded_in_session: Set[str] = set()
    pending_retry_ids: Optional[Set[str]] = None
    total_downloaded = 0
    total_unavailable = 0
    total_other_errors = 0
    last_result: Optional[DownloadAttempt] = None

    total_client_attempts = len(client_attempts)

    def print_client_switch_banner(attempt_number: int, client_label: str) -> None:
        border = "=" * 80
        header = (
            f" >>> Attempt {attempt_number}/{total_client_attempts}: Using YouTube client {client_label} <<< "
        )
        print("\n" + border)
        print(header.center(len(border)))
        print(border)

    for idx, client in enumerate(client_attempts):
        target_ids = pending_retry_ids if pending_retry_ids else None
        client_label = client if client else "default"
        print_client_switch_banner(idx + 1, client_label)
        result = run_download_attempt(
            urls,
            args,
            client,
            max_total,
            downloaded_ids,
            target_ids,
        )
        last_result = result

        total_downloaded += result.downloaded
        total_unavailable += result.video_unavailable_errors
        total_other_errors += result.other_errors
        detected_ids.update(result.detected_video_ids)
        downloaded_in_session.update(result.downloaded_video_ids)

        print(
            f"Attempt summary using {client_label!r} client: {format_attempt_summary(result)}"
        )

        if result.stopped_due_to_limit:
            pending_retry_ids = None
            break

        next_client_available = idx < len(client_attempts) - 1

        if result.failure_limit_reached:
            pending_retry_ids = None
            if next_client_available:
                next_client = client_attempts[idx + 1]
                print(
                    "\nReached the maximum of"
                    f" {MAX_FAILURES_PER_CLIENT} failed downloads with the"
                    f" {client!r} client. Trying {next_client!r} next..."
                )
                continue
            print(
                "\nReached the maximum number of failed downloads and no"
                f" additional clients are available after {client!r}."
            )
            break

        if result.retryable_error_ids:
            if next_client_available:
                pending_retry_ids = set(result.retryable_error_ids)
                next_client = client_attempts[idx + 1]
                retry_count = len(pending_retry_ids)
                plural = "video" if retry_count == 1 else "videos"
                print(
                    "\nEncountered retryable HTTP 403 errors using the"
                    f" {client!r} client. Retrying {retry_count} {plural} with"
                    f" {next_client!r}..."
                )
                continue
            pending_retry_ids = None
            print(
                "\nEncountered retryable HTTP 403 errors but no additional"
                f" clients are available after the {client!r} client."
            )
            break

        if not next_client_available:
            pending_retry_ids = None
            break

        pending_retry_ids = None

        if result.other_errors > 0:
            next_client = client_attempts[idx + 1]
            plural = "error" if result.other_errors == 1 else "errors"
            print(
                "\nEncountered"
                f" {result.other_errors} download {plural} using the"
                f" {client!r} client. Trying {next_client!r} next..."
            )
            continue

        if result.video_unavailable_errors > 0:
            next_client = client_attempts[idx + 1]
            plural = "error" if result.video_unavailable_errors == 1 else "errors"
            print(
                "\nEncountered"
                f" {result.video_unavailable_errors} 'Video unavailable' {plural} using the"
                f" {client!r} client. Retrying with {next_client!r}..."
            )
            continue

        if result.downloaded == 0:
            next_client = client_attempts[idx + 1]
            print(
                "\nNo videos were downloaded using the"
                f" {client!r} client. Trying {next_client!r} next..."
            )
            continue

        break

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

    total_detected = len(detected_ids)
    total_downloaded_now = len(downloaded_in_session)
    total_pending = max(total_detected - total_downloaded_now, 0)

    border_width = max(len(f" Summary for {summary_label} "), 36)
    border_color = "\033[95m"
    header_color = "\033[1;45;97m"
    label_color = "\033[1;36m"
    value_color = "\033[1;33m"
    reset = "\033[0m"

    border_line = f"{border_color}{'=' * border_width}{reset}"
    header_text = f" Summary for {summary_label} "

    print("\n" + border_line)
    print(f"{header_color}{header_text.center(border_width)}{reset}")
    print(border_line)
    print(f"{label_color}Total videos detected:{reset} {value_color}{total_detected}{reset}")
    print(
        f"{label_color}Total videos downloaded:{reset} "
        f"{value_color}{total_downloaded_now}{reset}"
    )
    print(
        f"{label_color}Total videos not downloaded:{reset} "
        f"{value_color}{total_pending}{reset}"
    )
    print(border_line)


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
    apply_authentication_defaults(args)

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
