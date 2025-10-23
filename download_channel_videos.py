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
import contextlib
import json
import os
import random
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


DEFAULT_FAILURE_LIMIT = 10  # Conservative: give each client more tolerance before rotating

# User-Agent rotation pool to appear as different browsers
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
]


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
    total_failure_count: int = 0
    failure_limit_reached: bool = False
    consecutive_limit_reached: bool = False
    failure_limit: int = DEFAULT_FAILURE_LIMIT
    rate_limit_pauses: int = 0


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    title: Optional[str] = None


@dataclass
class ErrorPattern:
    """Tracks a specific error pattern and its occurrences."""
    error_type: str
    count: int = 0
    video_ids: List[str] = field(default_factory=list)
    sample_messages: List[str] = field(default_factory=list)
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None

    def record(self, video_id: Optional[str], message: str) -> None:
        """Record an occurrence of this error pattern."""
        self.count += 1
        timestamp = time.time()

        if self.first_seen is None:
            self.first_seen = timestamp
        self.last_seen = timestamp

        if video_id and video_id not in self.video_ids:
            self.video_ids.append(video_id)

        # Keep only the first 5 sample messages to avoid memory bloat
        if len(self.sample_messages) < 5 and message not in self.sample_messages:
            self.sample_messages.append(message)


class ErrorAnalyzer:
    """Analyzes error patterns and suggests remediation strategies."""

    def __init__(self) -> None:
        self.patterns: Dict[str, ErrorPattern] = {
            "geo_restricted": ErrorPattern("geo_restricted"),
            "age_restricted": ErrorPattern("age_restricted"),
            "members_only": ErrorPattern("members_only"),
            "private_deleted": ErrorPattern("private_deleted"),
            "video_unavailable": ErrorPattern("video_unavailable"),
            "rate_limit": ErrorPattern("rate_limit"),
            "po_token": ErrorPattern("po_token"),
            "auth_required": ErrorPattern("auth_required"),
            "unknown": ErrorPattern("unknown"),
        }
        self.total_errors = 0
        self.error_log_path: Optional[str] = None

    def set_error_log_path(self, path: str) -> None:
        """Set the path for the detailed error log file."""
        self.error_log_path = path

    def categorize_and_record(self, video_id: Optional[str], error_message: str) -> str:
        """Categorize an error and record it. Returns the error category."""
        self.total_errors += 1
        lowered = error_message.lower()

        category = "unknown"

        # Categorize the error (order matters - more specific first)
        if any(x in lowered for x in ["not available in your country", "geo", "region"]):
            category = "geo_restricted"
        elif any(x in lowered for x in ["age", "sign in to confirm"]):
            category = "age_restricted"
        elif any(x in lowered for x in ["members only", "member", "subscription", "subscriber"]):
            category = "members_only"
        elif any(x in lowered for x in ["private", "deleted", "removed", "uploader has not made"]):
            category = "private_deleted"
        elif any(x in lowered for x in ["video unavailable", "content isn't available", "content is not available", "this content isn't available"]):
            category = "video_unavailable"
        elif any(x in lowered for x in ["403", "forbidden", "too many requests", "rate limit"]):
            category = "rate_limit"
        elif any(x in lowered for x in ["po token", "po_token"]):
            category = "po_token"
        elif any(x in lowered for x in ["login required", "authentication"]):
            category = "auth_required"

        # Record the error
        pattern = self.patterns[category]
        pattern.record(video_id, error_message)

        # Log to error file if configured
        if self.error_log_path:
            self._append_to_error_log(video_id, category, error_message)

        return category

    def _append_to_error_log(self, video_id: Optional[str], category: str, message: str) -> None:
        """Append error details to the error log file."""
        try:
            timestamp = datetime.now().isoformat()
            video_id_str = video_id or "unknown"
            log_entry = f"[{timestamp}] [{category}] {video_id_str}: {message}\n"

            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            # Don't fail the scan if error logging fails
            print(f"Warning: Failed to write to error log: {e}", file=sys.stderr)

    def get_recommendations(self) -> List[str]:
        """Generate recommendations based on error patterns."""
        recommendations = []

        if self.total_errors == 0:
            return ["No errors detected - scan completed successfully!"]

        # Analyze each pattern and provide specific recommendations
        if self.patterns["geo_restricted"].count > 0:
            recommendations.append(
                f"🌍 Geo-restriction ({self.patterns['geo_restricted'].count} videos): "
                "Use a VPN or proxy from a different region. Try --proxy with a different location."
            )

        if self.patterns["age_restricted"].count > 0:
            recommendations.append(
                f"🔞 Age-restricted ({self.patterns['age_restricted'].count} videos): "
                "Ensure your browser cookies are fresh. Sign in to YouTube in your browser and retry. "
                "Consider using --cookies-from-browser with a recently authenticated browser."
            )

        if self.patterns["members_only"].count > 0:
            recommendations.append(
                f"👥 Members-only ({self.patterns['members_only'].count} videos): "
                "These videos require channel membership. Use --allow-restricted if you have membership "
                "and are authenticated."
            )

        if self.patterns["private_deleted"].count > 0:
            recommendations.append(
                f"🗑️  Private/Deleted ({self.patterns['private_deleted'].count} videos): "
                "These videos are no longer available. This is expected - channels often delete old content."
            )

        if self.patterns["video_unavailable"].count > 0:
            recommendations.append(
                f"⚠️  Video Unavailable ({self.patterns['video_unavailable'].count} videos): "
                "YouTube is blocking access. This may indicate rate limiting or bot detection. "
                "The script now automatically rotates clients and adds delays. "
                "Try: (1) Increase --request-interval to 180-300 seconds, "
                "(2) Use --cookies-from-browser with a recently authenticated browser, "
                "(3) Add --proxy or --proxy-file to use different IP addresses, "
                "(4) Reduce scan frequency and try again later."
            )

        if self.patterns["rate_limit"].count > 0:
            recommendations.append(
                f"⏱️  Rate limiting ({self.patterns['rate_limit'].count} errors): "
                "YouTube is detecting automated access. Increase --request-interval to 180-300 seconds. "
                "Consider using a different proxy or adding more delay between requests."
            )

        if self.patterns["po_token"].count > 0:
            recommendations.append(
                f"🔑 PO Token issues ({self.patterns['po_token'].count} errors): "
                "BGUtil may be failing. Check if BGUtil is running (curl http://127.0.0.1:4416). "
                "Try --bgutil-http-disable-innertube or --bgutil-provider script. "
                "Consider --youtube-fetch-po-token auto instead of always."
            )

        if self.patterns["auth_required"].count > 0:
            recommendations.append(
                f"🔐 Authentication ({self.patterns['auth_required'].count} videos): "
                "These videos require login. Ensure --cookies-from-browser is working correctly. "
                "Sign in to YouTube in your browser and try again."
            )

        if self.patterns["unknown"].count > 0:
            recommendations.append(
                f"❓ Unknown errors ({self.patterns['unknown'].count}): "
                "Check the error log for details. May require manual investigation."
            )

        # Add percentage analysis
        error_rate = (self.total_errors / max(1, self.total_errors)) * 100
        if error_rate > 20:
            recommendations.append(
                f"\n⚠️  High error rate detected! Consider systematic fixes rather than individual retries."
            )

        return recommendations

    def print_summary(self) -> None:
        """Print a formatted summary of error patterns."""
        if self.total_errors == 0:
            print("\n✅ No errors detected during scan!")
            return

        print("\n" + "=" * 70)
        print("Error Pattern Analysis")
        print("=" * 70)
        print(f"Total errors: {self.total_errors}\n")

        # Sort patterns by count
        sorted_patterns = sorted(
            [(name, pattern) for name, pattern in self.patterns.items()],
            key=lambda x: x[1].count,
            reverse=True
        )

        for name, pattern in sorted_patterns:
            if pattern.count > 0:
                print(f"{name.replace('_', ' ').title()}: {pattern.count} occurrences")
                print(f"  Affected videos: {len(pattern.video_ids)}")
                if pattern.sample_messages:
                    print(f"  Sample: {pattern.sample_messages[0][:80]}...")
                print()

        print("=" * 70)
        print("Recommendations")
        print("=" * 70)
        for rec in self.get_recommendations():
            print(f"{rec}\n")
        print("=" * 70)

        if self.error_log_path:
            print(f"\nDetailed error log: {self.error_log_path}")


class DownloadLogger:
    """Custom logger that tracks repeated 'Video unavailable' errors."""

    UNAVAILABLE_FRAGMENTS = (
        "video unavailable",
        "video is unavailable",
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

    YOUTUBE_ID_PATTERN = re.compile(r"\[youtube[^\]]*\]\s+([0-9A-Za-z_-]{11})")

    def __init__(
        self,
        failure_callback: Optional[Callable[[Optional[str]], None]] = None,
        detection_callback: Optional[Callable[[str], None]] = None,
        error_analyzer: Optional[ErrorAnalyzer] = None,
    ) -> None:
        self.video_unavailable_errors = 0
        self.other_errors = 0
        self.current_url: Optional[str] = None
        self.current_client: Optional[str] = None
        self.current_video_id: Optional[str] = None
        self.retryable_error_ids: Set[str] = set()
        self._failure_callback = failure_callback
        self._detection_callback = detection_callback
        self._error_analyzer = error_analyzer
        self._last_reported_failure: Optional[Tuple[Optional[str], str]] = None
        # Track 403 errors for rate limit detection
        self.http_403_count = 0
        self.http_403_timestamps: List[float] = []
        # Track "video unavailable" errors for rate limit detection
        self.unavailable_timestamps: List[float] = []

    def set_failure_callback(
        self, callback: Optional[Callable[[Optional[str]], None]]
    ) -> None:
        self._failure_callback = callback

    def set_detection_callback(
        self, callback: Optional[Callable[[str], None]]
    ) -> None:
        self._detection_callback = callback

    def set_context(
        self, url: Optional[str], client: Optional[str], video_id: Optional[str] = None
    ) -> None:
        self.current_url = url
        self.current_client = client
        self.current_video_id = video_id
        self._last_reported_failure = None

    def check_rate_limit_backoff(self) -> Optional[int]:
        """
        Check if we should pause due to rate limiting based on 403 error count.
        Returns the number of seconds to wait, or None if no pause needed.

        Implements exponential backoff:
        - First 403: Wait 30 seconds
        - Second 403: Wait 60 seconds
        - Third 403: Wait 120 seconds
        - Fourth+ 403: Should trigger client switch (handled by caller)
        """
        if self.http_403_count == 0:
            return None

        # Exponential backoff based on sequential 403 count
        if self.http_403_count == 1:
            return 30  # First 403: 30 seconds
        elif self.http_403_count == 2:
            return 60  # Second 403: 60 seconds
        elif self.http_403_count >= 3:
            return 120  # Third 403: 120 seconds (fourth triggers client switch)

        return None

    def check_unavailable_rate_limiting(self) -> bool:
        """
        Check if we're seeing too many "video unavailable" errors in a short time window.
        Returns True if we should pause for rate limiting (3+ errors in 10 seconds).
        """
        if len(self.unavailable_timestamps) < 3:
            return False

        # Check if we have 3+ unavailable errors in the last 10 seconds
        recent_window = time.time() - 10
        recent_unavailable = sum(1 for ts in self.unavailable_timestamps if ts > recent_window)

        return recent_unavailable >= 3

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

        parsed_video_id: Optional[str] = None
        match = self.YOUTUBE_ID_PATTERN.search(text)
        if match:
            parsed_video_id = match.group(1)

        video_id = self.current_video_id or parsed_video_id

        if self._detection_callback and video_id:
            self._detection_callback(video_id)

        is_retryable = any(
            fragment in lowered for fragment in self.RETRYABLE_FRAGMENTS
        )

        # Track HTTP 403 errors specifically for rate limit detection
        if "http error 403" in lowered or "forbidden" in lowered:
            self.http_403_count += 1
            self.http_403_timestamps.append(time.time())
            # Keep only recent timestamps (last 10 minutes)
            cutoff_time = time.time() - 600
            self.http_403_timestamps = [ts for ts in self.http_403_timestamps if ts > cutoff_time]

        if any(fragment in lowered for fragment in self.UNAVAILABLE_FRAGMENTS):
            self.video_unavailable_errors += 1
            # Track timestamp for rate limiting detection
            self.unavailable_timestamps.append(time.time())
            # Keep only recent timestamps (last 60 seconds)
            cutoff_time = time.time() - 60
            self.unavailable_timestamps = [ts for ts in self.unavailable_timestamps if ts > cutoff_time]

            # Record in error analyzer if available
            if self._error_analyzer:
                self._error_analyzer.categorize_and_record(video_id, text)

            key = (video_id, lowered)
            if key == self._last_reported_failure:
                return
            self._last_reported_failure = key
            if self._failure_callback:
                self._failure_callback(video_id)
            return

        key = (video_id, lowered)
        if key == self._last_reported_failure:
            return

        if is_retryable:
            if video_id:
                self.retryable_error_ids.add(video_id)
            # Record retryable errors in analyzer
            if self._error_analyzer:
                self._error_analyzer.categorize_and_record(video_id, text)
        else:
            self.other_errors += 1
            # Record other errors in analyzer
            if self._error_analyzer:
                self._error_analyzer.categorize_and_record(video_id, text)

        self._last_reported_failure = key
        if self._failure_callback:
            self._failure_callback(video_id)

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
        text = self._ensure_text(message)
        self._print(text, file=sys.stderr)
        self._handle_message(text)

    def error(self, message) -> None:
        text = self._ensure_text(message)
        self._print(text, file=sys.stderr)
        self._handle_message(text)

    def record_exception(self, exc: Exception) -> None:
        text = self._ensure_text(str(exc))
        self._print(text, file=sys.stderr)
        self._last_reported_failure = None
        self._handle_message(text)


def _collect_video_ids_from_info(
    info: object,
    dest: List[VideoMetadata],
    seen: Optional[Set[str]] = None,
) -> None:
    """Recursively extract video identifiers from yt-dlp metadata objects."""

    if seen is None:
        seen = set()

    if info is None:
        return

    if isinstance(info, list):
        for entry in info:
            _collect_video_ids_from_info(entry, dest, seen)
        return

    if not isinstance(info, dict):
        return

    info_type = info.get("_type")

    if info_type in {"playlist", "multi_video", "compat_list"}:
        entries = info.get("entries") or []
        _collect_video_ids_from_info(entries, dest, seen)
        return

    if info_type == "url" and "entries" in info:
        _collect_video_ids_from_info(info.get("entries"), dest, seen)

    video_id = info.get("id")
    if video_id:
        video_id_str = str(video_id)
        if video_id_str not in seen:
            seen.add(video_id_str)
            title = info.get("title")
            title_str = title if isinstance(title, str) else None
            dest.append(VideoMetadata(video_id=video_id_str, title=title_str))


def collect_all_video_ids(
    urls: Iterable[str],
    args,
    player_client: Optional[str],
    error_analyzer: Optional[ErrorAnalyzer] = None,
) -> List[VideoMetadata]:
    """
    Fetch playlist metadata to determine every video entry for the given URLs.
    Enhanced with retry logic, client rotation, and exponential backoff.
    """

    logger = DownloadLogger(error_analyzer=error_analyzer)

    def noop_hook(_):
        return None

    video_metadata: List[VideoMetadata] = []
    seen_ids: Set[str] = set()

    # Get the sleep delay for metadata scanning (use sleep_requests value)
    base_delay = getattr(args, "sleep_requests", 2.0) or 2.0
    current_delay = base_delay

    # Track consecutive failures for exponential backoff
    consecutive_failures = 0
    max_backoff_delay = base_delay * 8  # Cap at 8x the base delay

    # Get available player clients for retry
    available_clients = list(DEFAULT_PLAYER_CLIENTS) if not player_client else [player_client]
    client_idx = 0
    current_client = available_clients[client_idx] if available_clients else player_client

    # Track which client is currently working well
    successful_client: Optional[str] = None
    consecutive_successes_with_client = 0

    # Track consecutive unavailable errors for the CURRENT client
    # This helps detect when a previously successful client starts failing
    consecutive_unavailable_errors = 0
    unavailable_error_threshold = 3  # Rotate client faster after 3 consecutive unavailable errors

    # Track initial unavailable error count to detect new errors
    initial_unavailable_count = logger.video_unavailable_errors

    print(f"[metadata scan] Starting with {len(available_clients)} available client(s): {', '.join(available_clients)}")

    urls_list = list(urls)
    total_urls = len(urls_list)

    try:
        for idx, url in enumerate(urls_list):
            # Add delay between metadata requests to avoid rate limiting
            if idx > 0:
                # Add random jitter (±20%) to avoid predictable patterns
                jitter = random.uniform(0.8, 1.2)
                delay_with_jitter = current_delay * jitter

                if consecutive_failures > 0:
                    print(f"[metadata scan] Exponential backoff: waiting {delay_with_jitter:.1f}s (base: {base_delay}s, consecutive failures: {consecutive_failures})...")
                else:
                    print(f"[metadata scan] Waiting {delay_with_jitter:.1f}s before next request to avoid rate limiting...")
                time.sleep(delay_with_jitter)

            # Try to extract info with retry logic
            # Try ALL available clients until one succeeds (more aggressive rotation)
            max_retries = len(available_clients)
            retry_count = 0
            success = False

            # If we have a known successful client, start with it
            if successful_client and successful_client in available_clients:
                # Start with the successful client
                successful_idx = available_clients.index(successful_client)
                if successful_idx != client_idx:
                    client_idx = successful_idx
                    current_client = available_clients[client_idx]

            while retry_count < max_retries and not success:
                try:
                    # Rotate user agent for each request to appear as different browsers
                    selected_user_agent = random.choice(USER_AGENTS)

                    # Build options for current client
                    ydl_opts = build_ydl_options(args, current_client, logger, noop_hook)
                    ydl_opts["skip_download"] = True
                    # Don't suppress output during metadata scanning - we want to see progress
                    ydl_opts["quiet"] = False
                    ydl_opts["no_warnings"] = False
                    ydl_opts["progress_hooks"] = []
                    ydl_opts["writethumbnail"] = False
                    ydl_opts["writesubtitles"] = False
                    ydl_opts["writeautomaticsub"] = False
                    ydl_opts.pop("download_archive", None)
                    ydl_opts.pop("match_filter", None)

                    # Override user agent for this request
                    if "http_headers" not in ydl_opts:
                        ydl_opts["http_headers"] = {}
                    ydl_opts["http_headers"]["User-Agent"] = selected_user_agent

                    logger.set_context(url, current_client)

                    # Track unavailable errors before the request
                    pre_request_unavailable_count = logger.video_unavailable_errors

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        if retry_count > 0:
                            print(f"[metadata scan] Retry {retry_count}/{max_retries-1} for {url} with client '{current_client}'")

                        # Show that we're starting the extraction (this can take time)
                        print(f"[metadata scan] Extracting channel info from {url}...")

                        info = ydl.extract_info(url, download=False)
                        _collect_video_ids_from_info(info, video_metadata, seen_ids)

                        # Check if unavailable errors occurred during this request
                        post_request_unavailable_count = logger.video_unavailable_errors
                        new_unavailable_errors = post_request_unavailable_count - pre_request_unavailable_count

                        if new_unavailable_errors > 0:
                            consecutive_unavailable_errors += new_unavailable_errors
                            print(f"[metadata scan] Detected {new_unavailable_errors} unavailable video(s) (consecutive: {consecutive_unavailable_errors})")

                            # Check if we're being rate limited (many errors in short time)
                            if logger.check_unavailable_rate_limiting():
                                rate_limit_pause = 60  # Pause for 1 minute
                                print(f"[metadata scan] ⚠️  Detected rapid unavailable errors - possible rate limiting!")
                                print(f"[metadata scan] Pausing for {rate_limit_pause}s to avoid further rate limiting...")
                                time.sleep(rate_limit_pause)
                                # Clear old timestamps after pause
                                logger.unavailable_timestamps = []

                            # Check if we should rotate client due to too many unavailable errors
                            if consecutive_unavailable_errors >= unavailable_error_threshold:
                                if len(available_clients) > 1:
                                    old_client = current_client
                                    client_idx = (client_idx + 1) % len(available_clients)
                                    current_client = available_clients[client_idx]
                                    print(f"[metadata scan] ⚠️  Too many unavailable errors ({consecutive_unavailable_errors}), rotating client: {old_client} → {current_client}")
                                    consecutive_unavailable_errors = 0

                                    # Add extra delay after client rotation
                                    rotation_delay = base_delay * 2
                                    print(f"[metadata scan] Adding {rotation_delay:.1f}s delay after client rotation...")
                                    time.sleep(rotation_delay)
                                else:
                                    print(f"[metadata scan] ⚠️  {consecutive_unavailable_errors} unavailable errors detected, but only one client available")
                        else:
                            # Reset counter on successful request without unavailable errors
                            consecutive_unavailable_errors = 0

                        # Success! Track which client worked
                        if successful_client != current_client:
                            if successful_client is None:
                                print(f"[metadata scan] ✓ Client '{current_client}' succeeded - will continue using it")
                            else:
                                print(f"[metadata scan] ✓ Client switched: {successful_client} → {current_client}")
                            successful_client = current_client
                            consecutive_successes_with_client = 1
                        else:
                            consecutive_successes_with_client += 1

                        # Reset failure tracking
                        consecutive_failures = 0
                        current_delay = base_delay
                        success = True

                except (DownloadError, ExtractorError) as exc:
                    error_msg = str(exc)
                    logger.record_exception(exc)

                    retry_count += 1

                    # Check if this is a retryable error (expanded to include unavailable errors)
                    is_retryable = any(
                        fragment in error_msg.lower()
                        for fragment in ["403", "forbidden", "po token", "login required",
                                        "video unavailable", "content isn't available",
                                        "content is not available", "this content isn't available"]
                    )

                    if is_retryable and retry_count < max_retries:
                        # Rotate to next client immediately
                        old_client = current_client
                        client_idx = (client_idx + 1) % len(available_clients)
                        current_client = available_clients[client_idx]

                        # Mark that the previously successful client is now failing
                        if old_client == successful_client:
                            print(f"[metadata scan] ⚠️ Previously successful client '{old_client}' is now failing")
                            consecutive_successes_with_client = 0
                            # Don't reset successful_client yet - we'll update it when we find a new working one

                        print(f"[metadata scan] Retryable error detected, rotating client: {old_client} → {current_client} (attempt {retry_count}/{max_retries})")

                        # Add a backoff before retry (with jitter to avoid patterns and bans)
                        # Scale delay based on retry count but keep it reasonable for YouTube
                        retry_delay = min(5 + (retry_count * 3), 20) * random.uniform(0.9, 1.1)
                        print(f"[metadata scan] Waiting {retry_delay:.1f}s before retry to avoid triggering rate limits...")
                        time.sleep(retry_delay)
                    else:
                        # Not retryable or out of retries
                        if retry_count >= max_retries:
                            print(f"[metadata scan] Failed to extract info from {url} after trying all {max_retries} available client(s)")
                        else:
                            print(f"[metadata scan] Non-retryable error for {url}: {error_msg[:100]}")
                        break

                except Exception as exc:  # pragma: no cover - defensive
                    logger.record_exception(exc)
                    retry_count += 1

                    if retry_count < max_retries:
                        old_client = current_client
                        client_idx = (client_idx + 1) % len(available_clients)
                        current_client = available_clients[client_idx]
                        print(f"[metadata scan] Unexpected error with client '{old_client}', rotating to '{current_client}' (attempt {retry_count}/{max_retries})")

                        # Add delay with jitter
                        delay = 5 * random.uniform(0.8, 1.2)
                        time.sleep(delay)
                    else:
                        print(f"[metadata scan] Unexpected error after trying all {max_retries} client(s): {str(exc)[:100]}")
                        break

            # Update exponential backoff based on success/failure
            if not success:
                consecutive_failures += 1
                # Exponential backoff: double the delay for each consecutive failure
                current_delay = min(base_delay * (2 ** consecutive_failures), max_backoff_delay)
                print(f"[metadata scan] Failed to process URL {idx+1}/{total_urls}, increasing delay to {current_delay:.1f}s")
            else:
                # Success - reset exponential backoff but maintain base delay
                if consecutive_failures > 0:
                    print(f"[metadata scan] Success after {consecutive_failures} consecutive failures, resetting delay")
                consecutive_failures = 0
                current_delay = base_delay

    except KeyboardInterrupt:
        raise

    return video_metadata


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
    # Start with yt-dlp's defaults but extend with additional clients for better rotation
    defaults = getattr(YoutubeIE, "_DEFAULT_CLIENTS", None)
    base_clients = list(defaults) if defaults else []

    # Extended list of clients for better rate limit avoidance and resilience
    # Prioritize mobile and TV clients as they tend to have better success rates
    preferred_order = ("tv", "web_safari", "web", "android", "ios", "mweb", "android_vr", "tv_embedded")

    # Build final list: start with defaults, then add any from preferred_order not already included
    final_clients = []
    seen = set()

    # Add base clients first
    for client in base_clients:
        if client in PLAYER_CLIENT_CHOICES and client not in seen:
            final_clients.append(client)
            seen.add(client)

    # Add additional clients from preferred order
    for client in preferred_order:
        if client in PLAYER_CLIENT_CHOICES and client not in seen:
            final_clients.append(client)
            seen.add(client)

    # If we still don't have enough clients, add any remaining ones
    if len(final_clients) < 5:
        for client in PLAYER_CLIENT_CHOICES:
            if client not in seen and not client.endswith("_embedded") and not client.endswith("_creator"):
                final_clients.append(client)
                seen.add(client)
                if len(final_clients) >= 8:  # Cap at 8 clients for reasonable rotation
                    break

    return tuple(final_clients) if final_clients else tuple(PLAYER_CLIENT_CHOICES[:3])


DEFAULT_PLAYER_CLIENTS: Tuple[str, ...] = _default_player_clients()

MAX_ATTEMPTS_PER_CLIENT = 5


@dataclass
class FormatSelection:
    requested: Optional[str]
    effective: Optional[str]
    fallback_reason: Optional[str] = None


MUXED_ONLY_CLIENTS: Set[str] = {"ios"}


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


def _load_download_archive(path: Optional[str]) -> Set[str]:
    if not path:
        return set()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            entries = set()
            for raw_line in handle:
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                entries.add(stripped)
            return entries
    except FileNotFoundError:
        return set()
    except OSError as exc:
        print(
            f"Warning: Failed to read download archive {path}: {exc}",
            file=sys.stderr,
        )
        return set()


def _write_download_archive(path: Optional[str], video_ids: Iterable[str]) -> None:
    if not path:
        return

    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            print(
                f"Warning: Failed to create directory for archive {path}: {exc}",
                file=sys.stderr,
            )
            return

    sanitized_ids = sorted(
        {str(video_id).strip() for video_id in video_ids if str(video_id).strip()}
    )
    temp_path = f"{path}.tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            for video_id in sanitized_ids:
                handle.write(f"{video_id}\n")
        os.replace(temp_path, path)
    except OSError as exc:
        print(
            f"Warning: Failed to update download archive {path}: {exc}",
            file=sys.stderr,
        )
        with contextlib.suppress(OSError):
            os.remove(temp_path)


def _append_to_download_archive(path: Optional[str], video_id: Optional[str]) -> None:
    if not path or not video_id:
        return

    sanitized = str(video_id).strip()
    if not sanitized:
        return

    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            print(
                f"Warning: Failed to create directory for archive {path}: {exc}",
                file=sys.stderr,
            )
            return

    data = f"{sanitized}\n".encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND

    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        print(
            f"Warning: Failed to open download archive {path}: {exc}",
            file=sys.stderr,
        )
        return

    try:
        os.write(fd, data)
    except OSError as exc:
        print(
            f"Warning: Failed to append to download archive {path}: {exc}",
            file=sys.stderr,
        )
    finally:
        os.close(fd)


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


def _format_requires_separate_streams(format_selector: str) -> bool:
    normalized = format_selector.lower()
    if "+" in normalized or "/" in normalized:
        return True
    return bool(re.search(r"(?:best|worst)?video", normalized))


def select_format_for_client(args, player_client: Optional[str]) -> FormatSelection:
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


def run_download_attempt(
    urls: List[str],
    args,
    player_client: Optional[str],
    max_total: Optional[int],
    downloaded_ids: Optional[Set[str]],
    target_video_ids: Optional[Set[str]] = None,
    failure_limit: int = DEFAULT_FAILURE_LIMIT,
) -> DownloadAttempt:
    logger = DownloadLogger()
    downloaded = 0
    stopped_due_to_limit = False
    failure_limit_reached = False
    consecutive_limit_reached = False
    total_failures = 0
    consecutive_failures = 0
    failure_limit_reason: Optional[str] = None
    rate_limit_pause_count = 0  # Track how many times we paused for rate limiting
    if failure_limit <= 0:
        failure_limit = DEFAULT_FAILURE_LIMIT
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

    logger.set_detection_callback(record_video_detection)

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
        nonlocal failure_limit_reached
        nonlocal consecutive_limit_reached
        nonlocal total_failures
        nonlocal consecutive_failures
        nonlocal failure_limit_reason
        nonlocal rate_limit_pause_count
        if failure_limit_reached:
            return
        if failed_video_id and failed_video_id in completed_ids:
            return

        # Check for rate limiting and apply exponential backoff
        backoff_seconds = logger.check_rate_limit_backoff()
        if backoff_seconds:
            rate_limit_pause_count += 1
            print(
                f"\n{'='*80}\n"
                f"⚠️  RATE LIMITING DETECTED: HTTP 403 error #{logger.http_403_count} detected.\n"
                f"Pausing for {backoff_seconds} seconds to avoid further blocking...\n"
                f"{'='*80}",
                file=sys.stderr
            )
            time.sleep(backoff_seconds)
            print(f"Resuming downloads after {backoff_seconds}s pause...\n")

        # Check for excessive "video unavailable" errors indicating rate limiting
        if logger.check_unavailable_rate_limiting():
            rate_limit_pause_count += 1
            pause_duration = 300  # 5 minutes
            recent_count = len([ts for ts in logger.unavailable_timestamps if ts > time.time() - 10])
            print(
                f"\n{'='*80}\n"
                f"⚠️  RATE LIMITING DETECTED: {recent_count} 'Video unavailable' errors in 10 seconds.\n"
                f"This may indicate YouTube is rate limiting your requests.\n"
                f"Pausing for {pause_duration} seconds (5 minutes) to avoid further blocking...\n"
                f"{'='*80}",
                file=sys.stderr
            )
            time.sleep(pause_duration)
            print(f"Resuming downloads after {pause_duration}s pause...\n")
            # Clear the timestamps after the pause
            logger.unavailable_timestamps.clear()

        # Check if we should force a client switch after 4 HTTP 403 errors
        if logger.http_403_count >= 4 and not failure_limit_reached:
            print(
                f"\n{'='*80}\n"
                f"⚠️  EXCESSIVE HTTP 403 ERRORS: {logger.http_403_count} errors detected.\n"
                f"Forcing client switch to avoid further blocking...\n"
                f"{'='*80}",
                file=sys.stderr
            )
            failure_limit_reached = True
            consecutive_limit_reached = True
            failure_limit_reason = "http_403"
            if interrupt:
                raise DownloadCancelled(
                    f"Forcing client switch after {logger.http_403_count} HTTP 403 errors"
                )
            return

        total_failures += 1
        consecutive_failures += 1
        if failed_video_id:
            failed_video_ids.add(failed_video_id)

        limit_reason: Optional[str] = None
        if consecutive_failures >= failure_limit:
            consecutive_limit_reached = True
            limit_reason = "consecutive"
        elif total_failures >= failure_limit:
            limit_reason = "total"

        if limit_reason:
            failure_limit_reached = True
            failure_limit_reason = limit_reason
            if interrupt:
                if limit_reason == "consecutive":
                    raise DownloadCancelled(
                        (
                            f"Aborting client {client_label} after "
                            f"{consecutive_failures} consecutive download failures "
                            f"(total failures: {total_failures}, limit={failure_limit})"
                        )
                    )
                raise DownloadCancelled(
                    (
                        f"Aborting client {client_label} after {total_failures} "
                        f"total download failures (limit={failure_limit})"
                    )
                )

    logger.set_failure_callback(
        lambda video_id: register_failure(video_id, interrupt=True)
    )

    def hook(d):
        nonlocal downloaded, stopped_due_to_limit, failure_limit_reached, consecutive_failures
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
                already_seen = info_id in seen_ids
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
                if not already_seen:
                    _append_to_download_archive(args.archive, info_id)
            downloaded += 1
            if max_total and downloaded >= max_total:
                stopped_due_to_limit = True
                raise KeyboardInterrupt
            logger.set_video(None)
            consecutive_failures = 0

    extra_filters: List[Callable[[dict], Optional[str]]] = []

    def match_filter(info_dict: dict) -> Optional[str]:
        video_id = info_dict.get("id") if isinstance(info_dict, dict) else None
        if video_id:
            record_video_detection(video_id)
        if video_id and video_id in seen_ids:
            return "Video already downloaded (tracked in archive or previous attempt)"
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
                before_total_failures = total_failures
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
                    after_total_failures = total_failures
                    after_consecutive = consecutive_failures

                    delta_downloaded = after_downloaded - before_downloaded
                    delta_unavailable = after_unavailable - before_unavailable
                    delta_other = after_other - before_other
                    delta_total_failures = after_total_failures - before_total_failures

                    summary_parts = [f"{delta_downloaded} downloaded"]
                    if delta_unavailable:
                        summary_parts.append(f"{delta_unavailable} unavailable")
                    if delta_other:
                        summary_parts.append(f"{delta_other} other errors")
                    if delta_total_failures:
                        summary_parts.append(
                            f"{delta_total_failures} failures"
                            f" (total={after_total_failures}, consecutive={after_consecutive})"
                        )
                    if (
                        not delta_unavailable
                        and not delta_other
                        and not delta_total_failures
                    ):
                        summary_parts.append("no new errors")
                    if encountered_exception and not (delta_unavailable or delta_other):
                        summary_parts.append("see logs for details")
                    if stopped_due_to_limit:
                        summary_parts.append("stopped due to limit")
                    if (
                        failure_limit_reached
                        and consecutive_limit_reached
                        and failure_limit_reason == "consecutive"
                    ):
                        summary_parts.append("consecutive failure limit reached")
                    elif failure_limit_reached and failure_limit_reason == "total":
                        summary_parts.append("total failure limit reached")

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
        failure_count=consecutive_failures,
        total_failure_count=total_failures,
        failure_limit_reached=failure_limit_reached,
        consecutive_limit_reached=consecutive_limit_reached,
        failure_limit=failure_limit,
        rate_limit_pauses=rate_limit_pause_count,
    )


def format_attempt_summary(attempt: DownloadAttempt) -> str:
    parts = [f"{attempt.downloaded} downloaded"]
    if attempt.video_unavailable_errors:
        parts.append(f"{attempt.video_unavailable_errors} unavailable")
    if attempt.other_errors:
        parts.append(f"{attempt.other_errors} other errors")
    if attempt.retryable_error_ids:
        parts.append(f"{len(attempt.retryable_error_ids)} retryable")
    if attempt.total_failure_count:
        parts.append(f"{attempt.total_failure_count} total failures")
    if attempt.failure_count:
        if attempt.consecutive_limit_reached:
            parts.append(
                f"{attempt.failure_count} consecutive failures (limit reached)"
            )
        else:
            parts.append(f"{attempt.failure_count} consecutive failures")
    if attempt.stopped_due_to_limit:
        parts.append("stopped due to limit")
    if attempt.failure_limit_reached:
        parts.append(
            f"reached failure limit ({attempt.failure_limit})"
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

    archive_path = getattr(args, "archive", None)
    downloaded_ids: Set[str] = set()
    if archive_path:
        previously_downloaded = _load_download_archive(archive_path)
        downloaded_ids.update(previously_downloaded)
        if os.path.exists(archive_path):
            if previously_downloaded:
                print(
                    f"Found {len(previously_downloaded)} previously downloaded video"
                    f"{'s' if len(previously_downloaded) != 1 else ''} in archive {archive_path}."
                )
            else:
                print(f"Download archive {archive_path} is empty; starting fresh.")
        else:
            print(
                f"No existing download archive at {archive_path}; it will be created after downloads."
            )
    metadata_video_entries = collect_all_video_ids(
        urls, args, client_attempts[0] if client_attempts else None
    )
    if metadata_video_entries:
        print(
            "\nMetadata scan detected"
            f" {len(metadata_video_entries)} video"
            f"{'s' if len(metadata_video_entries) != 1 else ''} before downloading."
        )
    detected_ids: Set[str] = {entry.video_id for entry in metadata_video_entries}
    downloaded_in_session: Set[str] = set()
    pending_retry_ids: Optional[Set[str]] = None
    total_downloaded = 0
    total_unavailable = 0
    total_other_errors = 0
    last_result: Optional[DownloadAttempt] = None
    archive_updated = False

    # Session telemetry tracking
    session_http_403_count = 0
    session_client_rotations = 0
    session_pauses_for_rate_limiting = 0

    total_client_attempts = len(client_attempts)

    def print_client_switch_banner(attempt_number: int, client_label: str) -> None:
        border = "=" * 80
        header = (
            f" >>> Attempt {attempt_number}/{total_client_attempts}: Using YouTube client {client_label} <<< "
        )
        print("\n" + border)
        print(header.center(len(border)))
        print(border)

    stop_all_attempts = False

    for idx, client in enumerate(client_attempts):
        if stop_all_attempts:
            break

        client_label = client if client else "default"
        print_client_switch_banner(idx + 1, client_label)

        attempts_for_client = 0
        consecutive_failures = 0

        while (
            attempts_for_client < MAX_ATTEMPTS_PER_CLIENT
            and consecutive_failures < MAX_ATTEMPTS_PER_CLIENT
        ):
            target_ids = pending_retry_ids if pending_retry_ids else None
            result = run_download_attempt(
                urls,
                args,
                client,
                max_total,
                downloaded_ids,
                target_ids,
                failure_limit=getattr(args, "failure_limit", DEFAULT_FAILURE_LIMIT),
            )
            pending_retry_ids = None
            last_result = result

            attempts_for_client += 1

            total_downloaded += result.downloaded
            total_unavailable += result.video_unavailable_errors
            total_other_errors += result.other_errors
            detected_ids.update(result.detected_video_ids)
            downloaded_in_session.update(result.downloaded_video_ids)
            if archive_path and result.downloaded_video_ids:
                _write_download_archive(archive_path, downloaded_ids)
                archive_updated = True

            # Track session telemetry
            session_pauses_for_rate_limiting += result.rate_limit_pauses
            session_http_403_count += len(result.retryable_error_ids)

            print(
                f"Attempt summary using {client_label!r} client: {format_attempt_summary(result)}"
            )

            only_unavailable_failures = (
                result.downloaded == 0
                and result.video_unavailable_errors > 0
                and result.other_errors == 0
            )
            attempt_failed = (
                result.failure_limit_reached
                or result.downloaded == 0
                or only_unavailable_failures
            )

            if attempt_failed:
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            if result.stopped_due_to_limit:
                stop_all_attempts = True
                break

            next_client_available = idx < len(client_attempts) - 1
            should_switch_client = False

            if result.failure_limit_reached:
                limit_label = (
                    "consecutive failed downloads"
                    if result.consecutive_limit_reached
                    else "failed downloads"
                )
                if next_client_available:
                    next_client = client_attempts[idx + 1]
                    print(
                        "\nReached the maximum of"
                        f" {result.failure_limit} {limit_label} with the"
                        f" {client!r} client. Trying {next_client!r} next..."
                    )
                    should_switch_client = True
                else:
                    print(
                        "\nReached the maximum number of"
                        f" {limit_label} (limit={result.failure_limit}) and no additional"
                        f" clients are available after {client!r}."
                    )
                    stop_all_attempts = True
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
                    should_switch_client = True
                else:
                    print(
                        "\nEncountered retryable HTTP 403 errors but no additional"
                        f" clients are available after the {client!r} client."
                    )
                    stop_all_attempts = True
                break

            if not next_client_available:
                stop_all_attempts = True
                break

            if result.other_errors > 0:
                next_client = client_attempts[idx + 1]
                plural = "error" if result.other_errors == 1 else "errors"
                print(
                    "\nEncountered"
                    f" {result.other_errors} download {plural} using the"
                    f" {client!r} client. Trying {next_client!r} next..."
                )
                should_switch_client = True
            elif result.video_unavailable_errors > 0:
                next_client = client_attempts[idx + 1]
                plural = "error" if result.video_unavailable_errors == 1 else "errors"
                print(
                    "\nEncountered"
                    f" {result.video_unavailable_errors} 'Video unavailable' {plural} using the"
                    f" {client!r} client. Retrying with {next_client!r}..."
                )
                should_switch_client = True
            elif result.downloaded == 0:
                if attempts_for_client < MAX_ATTEMPTS_PER_CLIENT:
                    next_attempt = attempts_for_client + 1
                    print(
                        "\nNo videos were downloaded using the"
                        f" {client!r} client. Retrying attempt"
                        f" {next_attempt} of {MAX_ATTEMPTS_PER_CLIENT}..."
                    )
                # stay on current client until the limit is reached
            else:
                stop_all_attempts = True
                break

            threshold_reached = (
                attempts_for_client >= MAX_ATTEMPTS_PER_CLIENT
                or consecutive_failures >= MAX_ATTEMPTS_PER_CLIENT
            )

            if threshold_reached and not should_switch_client:
                if consecutive_failures >= MAX_ATTEMPTS_PER_CLIENT:
                    reason = "5 consecutive failed attempts"
                else:
                    reason = "5 attempts"

                if next_client_available:
                    next_client = client_attempts[idx + 1]
                    print(
                        "\nReached"
                        f" {reason} with the {client!r} client. Trying {next_client!r} next..."
                    )
                    should_switch_client = True
                else:
                    print(
                        "\nReached"
                        f" {reason} with the {client!r} client and no additional"
                        " clients are available."
                    )
                    stop_all_attempts = True

            if stop_all_attempts:
                break

            if should_switch_client:
                session_client_rotations += 1
                break

            # Otherwise, loop again with the same client.

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

    if archive_path and downloaded_in_session and not archive_updated:
        _write_download_archive(archive_path, downloaded_ids)

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
    # Session telemetry
    print(f"{label_color}Session Statistics:{reset}")
    print(f"  {label_color}Videos processed:{reset} {value_color}{total_detected}{reset}")
    print(f"  {label_color}Videos downloaded:{reset} {value_color}{total_downloaded}{reset}")
    if total_unavailable > 0:
        print(f"  {label_color}Video unavailable errors:{reset} {value_color}{total_unavailable}{reset}")
    if session_http_403_count > 0:
        print(f"  {label_color}HTTP 403 errors:{reset} {value_color}{session_http_403_count}{reset}")
    if session_client_rotations > 0:
        print(f"  {label_color}Client rotations:{reset} {value_color}{session_client_rotations}{reset}")
    if session_pauses_for_rate_limiting > 0:
        print(f"  {label_color}Rate limit pauses:{reset} {value_color}{session_pauses_for_rate_limiting}{reset}")
    avg_delay = args.sleep_requests or 2.0
    sleep_range = f"{args.sleep_interval or 3.0}-{args.max_sleep_interval or 8.0}s"
    print(f"  {label_color}Request delay:{reset} {value_color}{avg_delay}s{reset}")
    print(f"  {label_color}Download delay:{reset} {value_color}{sleep_range}{reset}")
    print(border_line)


class RemoteSourceError(Exception):
    """Raised when a remote channels list cannot be retrieved or parsed."""


def download_videos_from_urls(video_urls: List[str], args) -> None:
    """
    Download videos from a list of video URLs.

    This is a simplified version of download_source() for direct video URL downloads.
    Used by download_videos.py and queue_manager.py.
    """

    if not video_urls:
        print("No videos to download.")
        return

    print(f"\n=== Starting downloads for {len(video_urls)} video(s) ===")

    # Determine client rotation strategy
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

    # Load download archive
    archive_path = getattr(args, "archive", None)
    downloaded_ids: Set[str] = set()
    if archive_path:
        previously_downloaded = _load_download_archive(archive_path)
        downloaded_ids.update(previously_downloaded)
        if previously_downloaded:
            print(f"Found {len(previously_downloaded)} previously downloaded videos in archive.")

    # Filter out already downloaded videos
    filtered_urls = []
    for url in video_urls:
        # Extract video ID from URL
        import re
        match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11})', url)
        if match:
            video_id = match.group(1)
            if video_id not in downloaded_ids:
                filtered_urls.append(url)
        else:
            # Include URLs we can't parse (let yt-dlp handle them)
            filtered_urls.append(url)

    if not filtered_urls:
        print("All videos already downloaded (found in archive).")
        return

    print(f"Videos to download (after archive filter): {len(filtered_urls)}")

    # Track session stats
    total_downloaded = 0
    total_failed = 0

    # Download each video
    for idx, url in enumerate(filtered_urls, start=1):
        print(f"\n[{idx}/{len(filtered_urls)}] Downloading {url}")

        # Create pseudo-source for this video
        try:
            video_source = Source(kind=SourceType.VIDEO, url=url)

            # Use download_source to leverage existing download logic
            # We'll create a temporary args copy to avoid max limit issues
            temp_args = argparse.Namespace(**vars(args))
            temp_args.max = 1  # Download only this one video

            download_source(video_source, temp_args)
            total_downloaded += 1

        except Exception as exc:
            print(f"Failed to download {url}: {exc}", file=sys.stderr)
            total_failed += 1

    # Print summary
    print("\n" + "=" * 70)
    print("Download Summary")
    print("=" * 70)
    print(f"Successfully downloaded: {total_downloaded}")
    print(f"Failed: {total_failed}")
    print("=" * 70)


def load_sources_from_url(url: str) -> Tuple[List[Source], List[str]]:
    print(f"\nFetching source list from {url} ...")

    # Retry with exponential backoff
    max_retries = 4
    base_delay = 2.0

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read().decode("utf-8")
            break  # Success, exit retry loop
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)  # Exponential backoff: 2s, 4s, 8s, 16s
                print(f"Failed to fetch (attempt {attempt + 1}/{max_retries}): {exc}. Retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
            else:
                raise RemoteSourceError(f"Failed to fetch source list from {url} after {max_retries} attempts: {exc}") from exc

    sources: List[Source] = []
    raw_lines: List[str] = []
    for idx, line in enumerate(data.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parsed = parse_source_line(stripped)
        except ValueError as exc:
            raise RemoteSourceError(
                f"Failed to parse line {idx} from {url}: {exc}"
            ) from exc
        if parsed:
            sources.append(parsed)
            raw_lines.append(stripped)

    # Randomize the order of sources to avoid predictable patterns
    if sources:
        paired = list(zip(sources, raw_lines))
        random.shuffle(paired)
        sources, raw_lines = zip(*paired)
        sources = list(sources)
        raw_lines = list(raw_lines)

    print(f"Loaded {len(sources)} sources from remote list (order randomized)")
    return sources, raw_lines


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

    # Randomize the order of sources to avoid predictable patterns
    if sources:
        paired = list(zip(sources, raw_lines))
        random.shuffle(paired)
        sources, raw_lines = zip(*paired)
        sources = list(sources)
        raw_lines = list(raw_lines)

    print(f"Loaded {len(sources)} sources from {path} (order randomized)")
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


def run_health_check(args) -> int:
    """Run a health check to test YouTube connectivity and rate limiting."""

    print("=" * 80)
    print("YouTube Downloader Health Check".center(80))
    print("=" * 80)
    print()

    # Test URL - use a popular, stable video that's unlikely to be removed
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    print(f"Testing connectivity with: {test_url}")
    print(f"Using player client: {args.youtube_client or 'default'}")
    print(f"Using cookies: {args.cookies_from_browser or 'none'}")
    print(f"Sleep settings: requests={args.sleep_requests}s, interval={args.sleep_interval}-{args.max_sleep_interval}s")
    print()

    logger = DownloadLogger()

    def noop_hook(_):
        return None

    player_client = args.youtube_client
    ydl_opts = build_ydl_options(args, player_client, logger, noop_hook)
    ydl_opts["skip_download"] = True
    ydl_opts["quiet"] = True
    ydl_opts["no_warnings"] = True

    start_time = time.time()
    success = False
    error_message = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            if info and info.get("id"):
                success = True
                video_title = info.get("title", "Unknown")
                duration = info.get("duration", 0)
                print(f"✓ Successfully retrieved metadata for: {video_title}")
                print(f"✓ Video duration: {duration} seconds")
                print(f"✓ Video ID: {info.get('id')}")
    except (DownloadError, ExtractorError) as exc:
        error_message = str(exc)
        logger.record_exception(exc)
    except Exception as exc:
        error_message = str(exc)
        logger.record_exception(exc)

    elapsed = time.time() - start_time

    print()
    print("=" * 80)
    print("Health Check Results".center(80))
    print("=" * 80)

    if success:
        print(f"✓ Status: HEALTHY")
        print(f"✓ Response time: {elapsed:.2f}s")
        print(f"✓ YouTube API is accessible")
        print(f"✓ No rate limiting detected")

        if args.cookies_from_browser:
            print(f"✓ Browser cookies loaded successfully")
        else:
            print(f"⚠ Not using browser cookies (consider --cookies-from-browser chrome)")

        if args.youtube_client == "web":
            print(f"✓ Using recommended 'web' client")
        else:
            print(f"ℹ Using client: {args.youtube_client or 'default'} (consider --youtube-client web)")

        print()
        print("Your configuration appears healthy. You should be able to download without issues.")
        return 0
    else:
        print(f"✗ Status: UNHEALTHY")
        print(f"✗ Response time: {elapsed:.2f}s")

        if logger.http_403_count > 0:
            print(f"✗ HTTP 403 errors detected: {logger.http_403_count}")
            print(f"✗ Likely cause: Rate limiting or IP block")
            print()
            print("Recommendations:")
            print("  1. Wait 10-30 minutes before trying again")
            print("  2. Use browser cookies: --cookies-from-browser chrome")
            print("  3. Use web client: --youtube-client web")
            print("  4. Check if YouTube is accessible in your web browser")
        elif logger.video_unavailable_errors > 0:
            print(f"✗ Video unavailable errors: {logger.video_unavailable_errors}")
            print(f"⚠ Test video may have been removed or is geo-restricted")
        else:
            print(f"✗ Error: {error_message or 'Unknown error'}")
            print()
            print("Recommendations:")
            print("  1. Check your internet connection")
            print("  2. Verify YouTube is accessible in your browser")
            print("  3. Try using browser cookies: --cookies-from-browser chrome")

        return 1


def main() -> int:
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
            watch_channels_file(args.channels_file, args)
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
