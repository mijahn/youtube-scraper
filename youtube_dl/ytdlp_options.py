"""yt-dlp options builder and format selection logic."""

import os
import random
import re
import sys
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional

from .logger import DownloadLogger
from .models import MUXED_ONLY_CLIENTS, USER_AGENTS, FormatSelection


def ytdlp_date(s: str) -> str:
    """Convert date string from YYYY-MM-DD to yt-dlp format."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise SystemExit(f"Invalid date '{s}'. Use YYYY-MM-DD.")


def _combine_match_filters(
    filters: Iterable[Callable[[dict], Optional[str]]]
) -> Optional[Callable[[dict], Optional[str]]]:
    """Combine multiple match filters into a single filter function."""
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


def _format_requires_separate_streams(format_selector: str) -> bool:
    """Check if format selector requires separate audio/video streams."""
    normalized = format_selector.lower()
    if "+" in normalized or "/" in normalized:
        return True
    return bool(re.search(r"(?:best|worst)?video", normalized))


def select_format_for_client(args, player_client: Optional[str]) -> FormatSelection:
    """Select appropriate format based on player client capabilities."""
    requested = getattr(args, "format", None)
    if not requested:
        return FormatSelection(requested=None, effective=None)

    if not player_client:
        return FormatSelection(requested=requested, effective=requested)

    if player_client in MUXED_ONLY_CLIENTS and _format_requires_separate_streams(requested):
        fallback = "best"
        reason = (
            f"Requested format '{requested}' requires separate audio/video streams, "
            f"but client '{player_client}' only provides muxed formats. Falling back to '{fallback}'."
        )
        return FormatSelection(requested=requested, effective=fallback, fallback_reason=reason)

    return FormatSelection(requested=requested, effective=requested)


def select_random_user_agent() -> str:
    """Select a random User-Agent from the pool to rotate through different browsers."""
    return random.choice(USER_AGENTS)


def load_proxies_from_file(proxy_file: str) -> List[str]:
    """Load proxy URLs from a file, one per line."""
    proxies: List[str] = []
    try:
        with open(proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                # Skip empty lines and comments
                if stripped and not stripped.startswith("#"):
                    proxies.append(stripped)
        if proxies:
            print(f"Loaded {len(proxies)} proxies from {proxy_file}")
        else:
            print(f"Warning: No proxies found in {proxy_file}", file=sys.stderr)
        return proxies
    except FileNotFoundError:
        print(f"Error: Proxy file not found: {proxy_file}", file=sys.stderr)
        return []
    except OSError as exc:
        print(f"Error reading proxy file {proxy_file}: {exc}", file=sys.stderr)
        return []


def select_proxy(args) -> Optional[str]:
    """
    Select a proxy based on args.
    Returns a single proxy URL, or None if no proxy is configured.
    """
    if args.proxy:
        return args.proxy

    proxy_file = getattr(args, "proxy_file", None)
    if proxy_file:
        # Load proxies and store in args to avoid re-reading the file
        if not hasattr(args, "_proxy_pool"):
            args._proxy_pool = load_proxies_from_file(proxy_file)

        if args._proxy_pool:
            return random.choice(args._proxy_pool)

    return None


def build_ydl_options(
    args,
    player_client: Optional[str],
    logger: DownloadLogger,
    hook,
    additional_filters: Optional[Iterable[Callable[[dict], Optional[str]]]] = None,
) -> dict:
    """Build yt-dlp options dictionary based on arguments."""
    outtmpl = os.path.join(
        args.output,
        "%(channel)s/%(upload_date>%Y-%m-%d)s - %(title).200B [%(id)s].%(ext)s",
    )

    # Rotate User-Agent to appear as different browsers
    user_agent = select_random_user_agent()

    # Select proxy if configured
    proxy = select_proxy(args)

    ydl_opts = {
        "continuedl": True,
        "ignoreerrors": "only_download",
        "noprogress": False,
        "retries": 5,  # Reduced from 10 to 5 for more conservative behavior
        "fragment_retries": 3,  # Reduced from 10 to 3 to avoid appearing aggressive
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
        "http_headers": {
            "User-Agent": user_agent,
        },
    }

    # Add proxy if configured
    if proxy:
        ydl_opts["proxy"] = proxy

    format_selection = select_format_for_client(args, player_client)
    format_selector = format_selection.effective
    requested_format = format_selection.requested
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
        debug_format = f"format={format_selector}"
        if requested_format and requested_format != format_selector:
            debug_format += f" (requested {requested_format})"
        debug_parts = [debug_format]
    else:
        debug_parts = ["format=yt-dlp-default"]
    if format_selection.fallback_reason:
        debug_parts.append(
            f"format_fallback={format_selection.fallback_reason}"
        )
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

    # Add user agent info to debug output
    user_agent_short = user_agent.split('(')[0].strip() if '(' in user_agent else user_agent[:50]
    debug_parts.append(f"user_agent={user_agent_short}")

    # Add proxy info to debug output
    if proxy:
        debug_parts.append(f"proxy={proxy}")

    print(
        "Constructed yt-dlp options: "
        + ", ".join(debug_parts)
        + f", write_subtitles={not args.skip_subtitles}, write_thumbnails={not args.skip_thumbs}"
    )

    return ydl_opts
