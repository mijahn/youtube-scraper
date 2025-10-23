"""Health check functionality to test YouTube connectivity."""

import time

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from .logger import DownloadLogger
from .ytdlp_options import build_ydl_options


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
