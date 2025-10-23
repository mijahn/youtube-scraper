"""Configuration and argument parsing for YouTube downloader."""

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Set

from .models import (
    BGUTIL_PROVIDER_CHOICES,
    DEFAULT_FAILURE_LIMIT,
    ENV_BGUTIL_HTTP_BASE_URL,
    ENV_BGUTIL_HTTP_DISABLE_INNERTUBE,
    ENV_BGUTIL_PROVIDER_MODE,
    ENV_BGUTIL_SCRIPT_PATH,
    ENV_COOKIES_FROM_BROWSER,
    ENV_FETCH_PO_TOKEN,
    ENV_PO_TOKENS,
    PLAYER_CLIENT_CHOICES,
    BgUtilProviderMode,
    DEFAULT_BGUTIL_HTTP_BASE_URL,
    DEFAULT_FETCH_PO_TOKEN_BEHAVIOR,
)


def positive_int(value: str) -> int:
    """Return *value* parsed as a positive integer for argparse."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise argparse.ArgumentTypeError(
            "Expected a positive integer"
        ) from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer")

    return parsed


def load_config_file(config_path: str) -> Dict[str, any]:
    """Load configuration from a JSON file.

    Returns a dictionary with configuration values that can be used as defaults
    for command-line arguments. If the file doesn't exist or is invalid, returns
    an empty dictionary.
    """
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        if not isinstance(config, dict):
            print(f"Warning: Config file {config_path} must contain a JSON object. Ignoring.", file=sys.stderr)
            return {}

        # Validate config keys to prevent typos
        valid_keys = {
            'sleep_requests', 'sleep_interval', 'max_sleep_interval',
            'cookies_from_browser', 'youtube_client', 'failure_limit',
            'output', 'archive', 'rate_limit', 'concurrency',
            'skip_subtitles', 'skip_thumbs', 'format', 'merge_output_format',
            'no_shorts', 'allow_restricted', 'youtube_fetch_po_token',
            'watch_interval', 'since', 'until', 'max', 'proxy'
        }

        invalid_keys = set(config.keys()) - valid_keys
        if invalid_keys:
            print(f"Warning: Unknown config keys ignored: {', '.join(sorted(invalid_keys))}", file=sys.stderr)

        return {k: v for k, v in config.items() if k in valid_keys}

    except json.JSONDecodeError as exc:
        print(f"Warning: Failed to parse config file {config_path}: {exc}. Ignoring.", file=sys.stderr)
        return {}
    except Exception as exc:
        print(f"Warning: Failed to read config file {config_path}: {exc}. Ignoring.", file=sys.stderr)
        return {}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    # First, check for config file
    config_path = "config.json"  # Default config file
    if "--config" in sys.argv:
        try:
            config_idx = sys.argv.index("--config")
            if config_idx + 1 < len(sys.argv):
                config_path = sys.argv[config_idx + 1]
        except (ValueError, IndexError):
            pass

    # Load configuration from file
    config = load_config_file(config_path)
    if config:
        print(f"Loaded configuration from {config_path}")

    parser = argparse.ArgumentParser(
        description="Download videos from YouTube channels, playlists, or single videos using yt-dlp."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to JSON configuration file (default: config.json)",
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
    parser.add_argument("--output", default=config.get("output", "./downloads"), help="Output directory (default: ./downloads)")
    parser.add_argument("--archive", default=config.get("archive"), help="Path to a download archive file to skip already downloaded videos")
    parser.add_argument("--since", default=config.get("since"), help="Only download videos uploaded on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=config.get("until"), help="Only download videos uploaded on/before this date (YYYY-MM-DD)")
    parser.add_argument("--max", type=int, default=config.get("max"), help="Stop after downloading N videos per channel")
    parser.add_argument(
        "--no-shorts",
        action="store_true",
        default=config.get("no_shorts", False),
        help="Exclude /shorts tab when downloading channel sources",
    )
    parser.add_argument("--rate-limit", default=config.get("rate_limit"), help="Limit download speed, e.g., 2M or 500K (passed to yt-dlp)")
    parser.add_argument("--concurrency", type=int, default=config.get("concurrency"), help="Concurrent fragment downloads (HLS/DASH)")
    parser.add_argument("--skip-subtitles", action="store_true", default=config.get("skip_subtitles", False), help="Do not download subtitles/auto-captions")
    parser.add_argument("--skip-thumbs", action="store_true", default=config.get("skip_thumbs", False), help="Do not download thumbnails")
    parser.add_argument(
        "--format",
        default=config.get("format"),
        help=(
            "Format selector passed to yt-dlp. Defaults to yt-dlp's native behaviour when omitted, "
            "which picks the best available combination without forcing a container."
        ),
    )
    parser.add_argument(
        "--merge-output-format",
        default=config.get("merge_output_format"),
        help=(
            "Container format for merged downloads (passed to yt-dlp). "
            "If omitted, yt-dlp will keep the original container when possible."
        ),
    )
    parser.add_argument("--cookies-from-browser", default=config.get("cookies_from_browser"), help="Use cookies from your browser (chrome, safari, firefox, edge, etc.)")
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=config.get("sleep_requests", 2.0),  # Conservative default: 2 seconds between HTTP requests
        help="Seconds to sleep between HTTP requests (default: 2.0, helps avoid rate limiting)",
    )
    parser.add_argument(
        "--sleep-interval",
        type=float,
        default=config.get("sleep_interval", 3.0),  # Conservative default: minimum 3 seconds between downloads
        help="Minimum randomized sleep between video downloads (default: 3.0)",
    )
    parser.add_argument(
        "--max-sleep-interval",
        type=float,
        default=config.get("max_sleep_interval", 8.0),  # Conservative default: maximum 8 seconds between downloads
        help="Maximum randomized sleep between video downloads (default: 8.0)",
    )
    parser.add_argument(
        "--failure-limit",
        type=positive_int,
        default=config.get("failure_limit", DEFAULT_FAILURE_LIMIT),
        help=(
            "Number of failed downloads allowed before switching to the next "
            "YouTube client (default: 10)"
        ),
    )
    parser.add_argument(
        "--allow-restricted",
        action="store_true",
        default=config.get("allow_restricted", False),
        help=(
            "Download restricted videos (subscriber-only, Premium, private, etc.)"
            " when authentication is available"
        ),
    )
    parser.add_argument(
        "--youtube-client",
        choices=PLAYER_CLIENT_CHOICES,
        default=config.get("youtube_client"),
        help=(
            "Override the YouTube player client used by yt-dlp "
            "(default: yt-dlp's recommended clients)"
        ),
    )
    parser.add_argument(
        "--youtube-fetch-po-token",
        choices=["auto", "always", "never"],
        default=config.get("youtube_fetch_po_token"),
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
        default=config.get("watch_interval", 300.0),
        help="When using --channels-file, seconds between checks for updates (default: 300)",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help=(
            "Run a health check to test YouTube connectivity and rate limiting status. "
            "Makes a single test request and reports results without downloading."
        ),
    )
    parser.add_argument(
        "--proxy",
        default=config.get("proxy"),
        help="Use a single proxy for all requests (e.g., http://proxy.example.com:8080 or socks5://127.0.0.1:1080)",
    )
    parser.add_argument(
        "--proxy-file",
        default=None,
        help="Path to a file containing proxy URLs (one per line). Proxies will be rotated randomly.",
    )
    return parser.parse_args()


def _parse_po_token_env(value: str) -> List[str]:
    """Parse PO tokens from environment variable."""
    tokens: List[str] = []
    for part in re.split(r"[\n,]+", value):
        cleaned = part.strip()
        if cleaned:
            tokens.append(cleaned)
    return tokens


def _normalize_env_str(value: Optional[str]) -> Optional[str]:
    """Normalize environment variable string value."""
    if not value:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _env_flag(value: Optional[str]) -> bool:
    """Parse a boolean flag from environment variable."""
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _apply_bgutil_provider_defaults(args, environ: Optional[Dict[str, str]]) -> None:
    """Apply BGUtil provider defaults from environment variables."""
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
