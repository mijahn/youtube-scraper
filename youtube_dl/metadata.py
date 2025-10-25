"""Video metadata collection from YouTube channels and playlists."""

import random
import sys
import time
from datetime import datetime
from typing import Iterable, List, Optional, Set

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from .errors import ErrorAnalyzer
from .logger import DownloadLogger
from .models import DEFAULT_PLAYER_CLIENTS, USER_AGENTS, VideoMetadata


def _log_with_timestamp(message: str) -> None:
    """Print a log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")
    sys.stdout.flush()  # Force immediate output


def _collect_video_ids_from_info(
    info: object,
    dest: List[VideoMetadata],
    seen: Optional[Set[str]] = None,
    log_progress: bool = False,
) -> int:
    """
    Recursively extract video identifiers from yt-dlp metadata objects.
    Returns the number of new videos found in this call.
    """

    if seen is None:
        seen = set()

    if info is None:
        return 0

    if isinstance(info, list):
        count = 0
        for entry in info:
            count += _collect_video_ids_from_info(entry, dest, seen, log_progress)
        return count

    if not isinstance(info, dict):
        return 0

    info_type = info.get("_type")

    if info_type in {"playlist", "multi_video", "compat_list"}:
        entries = info.get("entries") or []
        return _collect_video_ids_from_info(entries, dest, seen, log_progress)

    if info_type == "url" and "entries" in info:
        return _collect_video_ids_from_info(info.get("entries"), dest, seen, log_progress)

    video_id = info.get("id")
    if video_id:
        video_id_str = str(video_id)
        if video_id_str not in seen:
            seen.add(video_id_str)
            title = info.get("title")
            title_str = title if isinstance(title, str) else None
            dest.append(VideoMetadata(video_id=video_id_str, title=title_str))

            # Enhanced per-video progress logging
            if log_progress:
                video_count = len(dest)

                # Log every 10 videos for more granular progress tracking
                if video_count % 10 == 0:
                    # Show title preview for every 10th video
                    title_preview = (title_str[:50] + '...') if title_str and len(title_str) > 50 else (title_str or '(no title)')
                    _log_with_timestamp(f"[video] 📹 Collected {video_count} videos | Latest: {title_preview}")

                # Show milestone markers for larger numbers
                elif video_count in [25, 75, 125, 175, 250, 500, 750, 1000, 2000, 5000]:
                    _log_with_timestamp(f"[video] ✓ Progress milestone: {video_count} videos collected")

            return 1
        else:
            # Video already seen (duplicate)
            if log_progress:
                _log_with_timestamp(f"[video] ⏭ Skipping duplicate: {video_id_str}")
            return 0

    return 0


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
    # Import here to avoid circular dependency
    from .ytdlp_options import build_ydl_options

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

    _log_with_timestamp(f"[metadata scan] Starting with {len(available_clients)} available client(s): {', '.join(available_clients)}")

    urls_list = list(urls)
    total_urls = len(urls_list)
    _log_with_timestamp(f"[url-scan] Will process {total_urls} URL(s) from this source")

    try:
        for idx, url in enumerate(urls_list):
            _log_with_timestamp(f"[url-scan] " + "─" * 60)
            _log_with_timestamp(f"[url-scan] ▶ Processing URL {idx+1}/{total_urls}")

            # Extract meaningful part of URL for display
            url_parts = url.split('/')
            url_suffix = url_parts[-1] if url_parts[-1] else (url_parts[-2] if len(url_parts) > 1 else 'main')
            _log_with_timestamp(f"[url-scan] URL type: {url_suffix}")
            _log_with_timestamp(f"[url-scan] Full URL: {url}")

            # Add delay between metadata requests to avoid rate limiting
            if idx > 0:
                # Add random jitter (±20%) to avoid predictable patterns
                jitter = random.uniform(0.8, 1.2)
                delay_with_jitter = current_delay * jitter

                if consecutive_failures > 0:
                    _log_with_timestamp(f"[url-scan] ⏸ Exponential backoff: waiting {delay_with_jitter:.1f}s (base: {base_delay}s, consecutive failures: {consecutive_failures})...")
                else:
                    _log_with_timestamp(f"[url-scan] ⏸ Waiting {delay_with_jitter:.1f}s before next request (rate limiting)...")

                time.sleep(delay_with_jitter)
                _log_with_timestamp(f"[url-scan] ▶ Resuming...")

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
                    user_agent_short = selected_user_agent.split('(')[0].strip() if '(' in selected_user_agent else selected_user_agent[:50]

                    # Build options for current client
                    _log_with_timestamp(f"[extract] Building yt-dlp options...")
                    ydl_opts = build_ydl_options(args, current_client, logger, noop_hook)
                    ydl_opts["skip_download"] = True
                    # Enable VERBOSE mode for maximum visibility
                    ydl_opts["verbose"] = True
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

                    _log_with_timestamp(f"[extract] Using User-Agent: {user_agent_short}")
                    _log_with_timestamp(f"[extract] Using player client: {current_client}")

                    logger.set_context(url, current_client)

                    # Track unavailable errors before the request
                    pre_request_unavailable_count = logger.video_unavailable_errors
                    videos_before = len(video_metadata)

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        if retry_count > 0:
                            _log_with_timestamp(f"[extract] ⟳ Retry {retry_count}/{max_retries-1} with client '{current_client}'")

                        # Show that we're starting the extraction (this can take time)
                        _log_with_timestamp(f"[extract] ═══════════════════════════════════════════════")
                        _log_with_timestamp(f"[extract] ▶ Starting metadata extraction from YouTube")
                        _log_with_timestamp(f"[extract] This operation may take several minutes...")
                        _log_with_timestamp(f"[extract] yt-dlp is fetching channel/playlist data...")
                        _log_with_timestamp(f"[extract] ═══════════════════════════════════════════════")
                        sys.stdout.flush()

                        extraction_start = time.time()
                        info = ydl.extract_info(url, download=False)
                        extraction_duration = time.time() - extraction_start

                        _log_with_timestamp(f"[extract] ✓ Metadata extraction completed in {extraction_duration:.1f}s")
                        _log_with_timestamp(f"[extract] Parsing video metadata structure...")

                        new_videos = _collect_video_ids_from_info(info, video_metadata, seen_ids, log_progress=True)
                        videos_after = len(video_metadata)
                        duplicates_found = new_videos - (videos_after - videos_before) if new_videos > 0 else 0

                        _log_with_timestamp(f"[extract] ✓ Found {new_videos} video(s) from this URL")
                        if duplicates_found > 0:
                            _log_with_timestamp(f"[extract] ℹ {duplicates_found} duplicate(s) skipped")
                        _log_with_timestamp(f"[extract] Running total: {videos_after} unique video(s)")

                        # Check if unavailable errors occurred during this request
                        post_request_unavailable_count = logger.video_unavailable_errors
                        new_unavailable_errors = post_request_unavailable_count - pre_request_unavailable_count

                        if new_unavailable_errors > 0:
                            consecutive_unavailable_errors += new_unavailable_errors
                            _log_with_timestamp(f"[extract] ⚠ Detected {new_unavailable_errors} unavailable video(s) (consecutive: {consecutive_unavailable_errors})")

                            # Check if we're being rate limited (many errors in short time)
                            if logger.check_unavailable_rate_limiting():
                                rate_limit_pause = 60  # Pause for 1 minute
                                _log_with_timestamp(f"[extract] ⚠ Detected rapid unavailable errors - possible rate limiting!")
                                _log_with_timestamp(f"[extract] ⏸ Pausing for {rate_limit_pause}s to avoid further rate limiting...")
                                time.sleep(rate_limit_pause)
                                # Clear old timestamps after pause
                                logger.unavailable_timestamps = []

                            # Check if we should rotate client due to too many unavailable errors
                            if consecutive_unavailable_errors >= unavailable_error_threshold:
                                if len(available_clients) > 1:
                                    old_client = current_client
                                    client_idx = (client_idx + 1) % len(available_clients)
                                    current_client = available_clients[client_idx]
                                    _log_with_timestamp(f"[extract] ⚠ Too many unavailable errors ({consecutive_unavailable_errors}), rotating client: {old_client} → {current_client}")
                                    consecutive_unavailable_errors = 0

                                    # Add extra delay after client rotation
                                    rotation_delay = base_delay * 2
                                    _log_with_timestamp(f"[extract] ⏸ Adding {rotation_delay:.1f}s delay after client rotation...")
                                    time.sleep(rotation_delay)
                                else:
                                    _log_with_timestamp(f"[extract] ⚠ {consecutive_unavailable_errors} unavailable errors detected, but only one client available")
                        else:
                            # Reset counter on successful request without unavailable errors
                            consecutive_unavailable_errors = 0

                        # Success! Track which client worked
                        if successful_client != current_client:
                            if successful_client is None:
                                _log_with_timestamp(f"[client] ✓ Client '{current_client}' succeeded - will continue using it")
                            else:
                                _log_with_timestamp(f"[client] ✓ Switched: {successful_client} → {current_client}")
                            successful_client = current_client
                            consecutive_successes_with_client = 1
                        else:
                            consecutive_successes_with_client += 1

                        # Reset failure tracking
                        consecutive_failures = 0
                        current_delay = base_delay
                        success = True
                        _log_with_timestamp(f"[url-scan] ✓ Successfully completed URL {idx+1}/{total_urls}")

                except (DownloadError, ExtractorError) as exc:
                    error_msg = str(exc)
                    logger.record_exception(exc)
                    _log_with_timestamp(f"[extract] ❌ Error: {error_msg[:150]}")

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
                            _log_with_timestamp(f"[client] ⚠ Previously successful client '{old_client}' is now failing")
                            consecutive_successes_with_client = 0
                            # Don't reset successful_client yet - we'll update it when we find a new working one

                        _log_with_timestamp(f"[client] ⟳ Rotating: {old_client} → {current_client} (attempt {retry_count}/{max_retries})")

                        # Add a backoff before retry (with jitter to avoid patterns and bans)
                        # Scale delay based on retry count but keep it reasonable for YouTube
                        retry_delay = min(5 + (retry_count * 3), 20) * random.uniform(0.9, 1.1)
                        _log_with_timestamp(f"[extract] ⏸ Waiting {retry_delay:.1f}s before retry...")
                        time.sleep(retry_delay)
                    else:
                        # Not retryable or out of retries
                        if retry_count >= max_retries:
                            _log_with_timestamp(f"[extract] ❌ Failed after trying all {max_retries} available client(s)")
                        else:
                            _log_with_timestamp(f"[extract] ❌ Non-retryable error: {error_msg[:100]}")
                        break

                except Exception as exc:  # pragma: no cover - defensive
                    logger.record_exception(exc)
                    retry_count += 1
                    _log_with_timestamp(f"[extract] ❌ Unexpected exception: {str(exc)[:150]}")

                    if retry_count < max_retries:
                        old_client = current_client
                        client_idx = (client_idx + 1) % len(available_clients)
                        current_client = available_clients[client_idx]
                        _log_with_timestamp(f"[client] ⟳ Rotating due to error: {old_client} → {current_client} (attempt {retry_count}/{max_retries})")

                        # Add delay with jitter
                        delay = 5 * random.uniform(0.8, 1.2)
                        time.sleep(delay)
                    else:
                        _log_with_timestamp(f"[extract] ❌ Failed after trying all {max_retries} client(s)")
                        break

            # Update exponential backoff based on success/failure
            if not success:
                consecutive_failures += 1
                # Exponential backoff: double the delay for each consecutive failure
                current_delay = min(base_delay * (2 ** consecutive_failures), max_backoff_delay)
                _log_with_timestamp(f"[url-scan] ❌ Failed URL {idx+1}/{total_urls}, next delay increased to {current_delay:.1f}s")
            else:
                # Success - reset exponential backoff but maintain base delay
                if consecutive_failures > 0:
                    _log_with_timestamp(f"[url-scan] ✓ Success after {consecutive_failures} consecutive failure(s), resetting delay")
                consecutive_failures = 0
                current_delay = base_delay

    except KeyboardInterrupt:
        raise

    return video_metadata
