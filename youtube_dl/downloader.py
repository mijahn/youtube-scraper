"""Core download orchestration logic."""

import argparse
import os
import re
import sys
import time
import urllib.parse
from typing import Dict, List, Optional, Set

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from .archive import append_to_download_archive
from .errors import ErrorAnalyzer
from .logger import DownloadLogger
from .metadata import collect_all_video_ids
from .models import (
    DEFAULT_FAILURE_LIMIT,
    DEFAULT_PLAYER_CLIENTS,
    MAX_ATTEMPTS_PER_CLIENT,
    DownloadAttempt,
    Source,
    SourceType,
    VideoMetadata,
    normalize_url,
)
from .ytdlp_options import build_ydl_options


def format_attempt_summary(attempt: DownloadAttempt) -> str:
    """Format a summary of download attempt results."""
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
    """Generate a short label for a source."""
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

