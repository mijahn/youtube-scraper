"""Download logger for tracking errors and rate limiting."""

import re
import sys
import time
from typing import Callable, List, Optional, Set, Tuple

from .errors import ErrorAnalyzer


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

    def _is_expected_unavailable_error(self, text: str) -> bool:
        """Check if this is an expected unavailable error that should be suppressed from logs."""
        lowered = text.lower()
        # Check for ignored fragments first (these should never be logged)
        if any(fragment in lowered for fragment in self.IGNORED_FRAGMENTS):
            return True
        # Check for unavailable fragments (members-only, private, age-restricted, etc.)
        return any(fragment in lowered for fragment in self.UNAVAILABLE_FRAGMENTS)

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
        # Check if this is a known unavailable error before printing
        if not self._is_expected_unavailable_error(text):
            self._print(text, file=sys.stderr)
        self._handle_message(text)

    def error(self, message) -> None:
        text = self._ensure_text(message)
        # Check if this is a known unavailable error before printing
        if not self._is_expected_unavailable_error(text):
            self._print(text, file=sys.stderr)
        self._handle_message(text)

    def record_exception(self, exc: Exception) -> None:
        text = self._ensure_text(str(exc))
        # Check if this is a known unavailable error before printing
        if not self._is_expected_unavailable_error(text):
            self._print(text, file=sys.stderr)
        self._last_reported_failure = None
        self._handle_message(text)
