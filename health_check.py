#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_check.py

Health check utility for YouTube downloader.
Tests current IP rate limit status and authentication.

Usage:
    python health_check.py
    python health_check.py --test-video dQw4w9WgXcQ
    python health_check.py --cookies-from-browser chrome
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Optional

import download_channel_videos as downloader

try:
    import yt_dlp
    from yt_dlp.utils import DownloadError, ExtractorError
except ImportError:
    print("yt-dlp is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


def test_simple_request(args: argparse.Namespace, test_video_id: str = "dQw4w9WgXcQ") -> dict:
    """
    Perform a simple test request to check YouTube connectivity and rate limiting.

    Returns a dict with test results.
    """

    print("\n" + "=" * 70)
    print("HEALTH CHECK: Simple Request Test")
    print("=" * 70)
    print(f"Test video ID: {test_video_id}")
    print(f"Test URL: https://www.youtube.com/watch?v={test_video_id}")
    print("=" * 70)

    # Create a test logger to track errors
    logger = downloader.DownloadLogger()

    def noop_hook(_):
        return None

    # Get first player client
    player_client: Optional[str] = None
    if args.youtube_client:
        player_client = args.youtube_client
    elif downloader.DEFAULT_PLAYER_CLIENTS:
        player_client = downloader.DEFAULT_PLAYER_CLIENTS[0]

    print(f"\nUsing player client: {player_client or 'default'}")

    # Build yt-dlp options
    ydl_opts = downloader.build_ydl_options(args, player_client, logger, noop_hook)
    ydl_opts["skip_download"] = True
    ydl_opts["quiet"] = False
    ydl_opts["no_warnings"] = False

    # Remove download-specific options
    ydl_opts.pop("download_archive", None)
    ydl_opts.pop("match_filter", None)

    result = {
        "success": False,
        "video_id": test_video_id,
        "player_client": player_client,
        "timestamp": datetime.now().isoformat(),
        "error": None,
        "http_403_detected": False,
        "rate_limited": False,
        "authentication_status": "unknown",
        "video_accessible": False,
        "estimated_wait_time": None,
    }

    # Attempt to extract info
    start_time = time.time()

    try:
        print("\n[test] Attempting to fetch video metadata...")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={test_video_id}",
                download=False
            )

        elapsed_time = time.time() - start_time

        if info:
            result["success"] = True
            result["video_accessible"] = True
            result["authentication_status"] = "working"

            print(f"\n[test] ✓ SUCCESS")
            print(f"  Video title: {info.get('title', 'unknown')}")
            print(f"  Video duration: {info.get('duration', 'unknown')}s")
            print(f"  Response time: {elapsed_time:.2f}s")
            print(f"\n  → Your IP is NOT rate limited")
            print(f"  → Authentication is working")

    except (DownloadError, ExtractorError) as exc:
        elapsed_time = time.time() - start_time
        error_msg = str(exc).lower()

        result["error"] = str(exc)

        print(f"\n[test] ✗ FAILED (after {elapsed_time:.2f}s)")
        print(f"  Error: {exc}")

        # Check for specific error types
        if "403" in error_msg or "forbidden" in error_msg:
            result["http_403_detected"] = True
            result["rate_limited"] = True

            print(f"\n  → HTTP 403 Forbidden detected")
            print(f"  → Your IP is likely RATE LIMITED")

            # Estimate wait time based on common patterns
            if logger.http_403_count > 0:
                estimated_wait = 30 * (2 ** logger.http_403_count)
                result["estimated_wait_time"] = estimated_wait
                print(f"  → Estimated wait time: {estimated_wait} seconds")
            else:
                print(f"  → Recommended wait time: 60-120 seconds")
                result["estimated_wait_time"] = 60

        elif "sign in" in error_msg or "login required" in error_msg:
            result["authentication_status"] = "required"
            print(f"\n  → Authentication required")
            print(f"  → Try using --cookies-from-browser")

        elif "private" in error_msg or "unavailable" in error_msg:
            result["video_accessible"] = False
            print(f"\n  → Test video is unavailable or private")
            print(f"  → This may not indicate a rate limit issue")

        elif "po token" in error_msg:
            result["authentication_status"] = "po_token_needed"
            print(f"\n  → PO Token required")
            print(f"  → YouTube may require additional authentication")

        else:
            print(f"\n  → Unknown error (see error message above)")

    except Exception as exc:
        result["error"] = str(exc)
        print(f"\n[test] ✗ EXCEPTION: {exc}")

    return result


def test_authentication(args: argparse.Namespace) -> dict:
    """Test authentication status."""

    print("\n" + "=" * 70)
    print("HEALTH CHECK: Authentication Test")
    print("=" * 70)

    result = {
        "cookies_configured": bool(args.cookies_from_browser),
        "po_token_configured": bool(args.youtube_po_token),
        "authentication_method": None,
    }

    if args.cookies_from_browser:
        print(f"✓ Cookies configured: {args.cookies_from_browser}")
        result["authentication_method"] = f"cookies ({args.cookies_from_browser})"
    else:
        print(f"✗ No cookies configured")

    if args.youtube_po_token:
        print(f"✓ PO Token configured: {len(args.youtube_po_token)} token(s)")
        result["authentication_method"] = "po_token"
    else:
        print(f"✗ No PO Token configured")

    if args.youtube_fetch_po_token:
        print(f"✓ PO Token fetching: {args.youtube_fetch_po_token}")
    else:
        print(f"  PO Token fetching: default behavior")

    if not result["authentication_method"]:
        result["authentication_method"] = "none (anonymous)"
        print(f"\n→ Operating in ANONYMOUS mode")
        print(f"  Recommendation: Use --cookies-from-browser for better reliability")

    return result


def check_recent_errors(args: argparse.Namespace) -> dict:
    """Check for recent error patterns in logs (if available)."""

    print("\n" + "=" * 70)
    print("HEALTH CHECK: Recent Errors Analysis")
    print("=" * 70)

    # This is a placeholder - in a real implementation, you might:
    # 1. Check log files for recent 403 errors
    # 2. Analyze download archive for failure patterns
    # 3. Check queue manager for failed downloads

    result = {
        "recent_403_count": 0,
        "recent_unavailable_count": 0,
        "recommendation": "No recent errors detected (or logging not configured)",
    }

    print("Note: Error history analysis requires additional logging configuration")
    print("This feature is a placeholder for future implementation")

    return result


def print_recommendations(
    simple_test_result: dict,
    auth_result: dict,
) -> None:
    """Print recommendations based on test results."""

    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)

    if simple_test_result["success"]:
        print("✓ Everything looks good!")
        print("  - Your IP is not rate limited")
        print("  - You can proceed with downloads")

    else:
        print("Issues detected. Recommendations:")

        if simple_test_result["rate_limited"]:
            print("\n1. RATE LIMITING DETECTED")
            print("   - Your IP has been rate limited by YouTube")
            print("   - Wait before resuming downloads")

            if simple_test_result["estimated_wait_time"]:
                wait_minutes = simple_test_result["estimated_wait_time"] / 60
                print(f"   - Recommended wait: {wait_minutes:.1f} minutes")

            print("\n   To avoid future rate limits:")
            print("   - Use scan_channels.py with --request-interval 60 (or higher)")
            print("   - Increase --sleep-requests and --sleep-interval")
            print("   - Download during off-peak hours")
            print("   - Consider using a VPN or different network")

        if auth_result["authentication_method"] == "none (anonymous)":
            print("\n2. NO AUTHENTICATION CONFIGURED")
            print("   - Anonymous mode is less reliable")
            print("   - Recommendation: Use --cookies-from-browser chrome")
            print("   - This improves reliability and reduces rate limiting")

        if simple_test_result.get("authentication_status") == "po_token_needed":
            print("\n3. PO TOKEN REQUIRED")
            print("   - YouTube is requesting additional authentication")
            print("   - Try: --youtube-fetch-po-token always")
            print("   - Or provide tokens manually with --youtube-po-token")

    print("\n" + "=" * 70)


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Health check for YouTube downloader (test rate limits and authentication)."
    )

    parser.add_argument(
        "--test-video",
        default="dQw4w9WgXcQ",
        help="Video ID to use for testing (default: dQw4w9WgXcQ)",
    )

    # Authentication options
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Browser to extract cookies from (chrome, firefox, etc.)",
    )
    parser.add_argument(
        "--youtube-client",
        choices=downloader.PLAYER_CLIENT_CHOICES,
        default=None,
        help="YouTube player client to test",
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

    args = parser.parse_args(argv)

    # Apply authentication defaults
    downloader.apply_authentication_defaults(args)

    # Set defaults for attributes required by build_ydl_options
    # Health check only tests connectivity, not actual downloads
    # These attributes are not in the parser but are required by build_ydl_options
    args.output = '/tmp/health-check'  # Dummy output path
    args.skip_thumbs = True
    args.skip_subtitles = True
    args.allow_restricted = False
    args.sleep_interval = 0.0
    args.max_sleep_interval = 0.0
    args.sleep_requests = 0.0
    args.archive = None
    args.rate_limit = None
    args.concurrency = None
    args.since = None
    args.until = None
    args.merge_output_format = None
    args.format = None
    args.proxy = None
    args.proxy_file = None

    return args


def main(argv=None) -> int:
    """Main entry point."""

    args = parse_args(argv)

    print("=" * 70)
    print("YouTube Downloader Health Check")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 70)

    # Run tests
    simple_test_result = test_simple_request(args, args.test_video)
    auth_result = test_authentication(args)
    # error_analysis = check_recent_errors(args)

    # Print recommendations
    print_recommendations(simple_test_result, auth_result)

    # Return exit code
    if simple_test_result["success"]:
        print("\nHealth check PASSED ✓")
        return 0
    else:
        print("\nHealth check FAILED ✗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
