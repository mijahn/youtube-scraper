#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue_manager.py

Queue-based download manager with persistent queue and retry support.

Features:
- Metadata scanner populates queue
- Downloader pulls from queue with configurable concurrency
- Failed downloads go back to queue with exponential retry
- Persistent queue survives restarts
- Thread-safe operations

Usage:
    # Populate queue from scan
    python queue_manager.py --populate --metadata metadata.json

    # Start downloading from queue
    python queue_manager.py --download --workers 2

    # View queue status
    python queue_manager.py --status

    # Clear the queue
    python queue_manager.py --clear
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set

import download_channel_videos as downloader


class VideoStatus(Enum):
    """Status of a video in the queue."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class QueuedVideo:
    """Represents a video in the download queue."""

    video_id: str
    video_url: str
    title: Optional[str]
    channel_url: str
    status: VideoStatus
    attempts: int = 0
    max_attempts: int = 5
    last_error: Optional[str] = None
    last_attempt_time: Optional[str] = None
    added_time: str = ""
    completed_time: Optional[str] = None

    def __post_init__(self):
        if not self.added_time:
            self.added_time = datetime.now().isoformat()


@dataclass
class DownloadQueue:
    """Persistent download queue."""

    videos: List[QueuedVideo]
    queue_file: str = "download_queue.json"
    lock: threading.Lock = threading.Lock()

    def __init__(self, queue_file: str = "download_queue.json"):
        self.queue_file = queue_file
        self.videos = []
        self.lock = threading.Lock()
        self.load()

    def load(self) -> None:
        """Load queue from disk."""

        if not os.path.exists(self.queue_file):
            return

        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.videos = []
            for item in data.get("videos", []):
                video = QueuedVideo(
                    video_id=item["video_id"],
                    video_url=item["video_url"],
                    title=item.get("title"),
                    channel_url=item["channel_url"],
                    status=VideoStatus(item["status"]),
                    attempts=item.get("attempts", 0),
                    max_attempts=item.get("max_attempts", 5),
                    last_error=item.get("last_error"),
                    last_attempt_time=item.get("last_attempt_time"),
                    added_time=item.get("added_time", datetime.now().isoformat()),
                    completed_time=item.get("completed_time"),
                )
                self.videos.append(video)

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"Warning: Failed to load queue from {self.queue_file}: {exc}", file=sys.stderr)
            print("Starting with empty queue.", file=sys.stderr)
            self.videos = []

    def save(self) -> None:
        """Save queue to disk."""

        data = {
            "last_updated": datetime.now().isoformat(),
            "total_videos": len(self.videos),
            "videos": [
                {
                    "video_id": v.video_id,
                    "video_url": v.video_url,
                    "title": v.title,
                    "channel_url": v.channel_url,
                    "status": v.status.value,
                    "attempts": v.attempts,
                    "max_attempts": v.max_attempts,
                    "last_error": v.last_error,
                    "last_attempt_time": v.last_attempt_time,
                    "added_time": v.added_time,
                    "completed_time": v.completed_time,
                }
                for v in self.videos
            ],
        }

        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_video(self, video: QueuedVideo) -> None:
        """Add a video to the queue."""

        with self.lock:
            # Check if video already exists
            existing = self.get_by_id(video.video_id)
            if existing:
                print(f"[queue] Video {video.video_id} already in queue (status: {existing.status.value})")
                return

            self.videos.append(video)
            self.save()

    def get_by_id(self, video_id: str) -> Optional[QueuedVideo]:
        """Get a video by ID."""

        for video in self.videos:
            if video.video_id == video_id:
                return video
        return None

    def get_next_pending(self) -> Optional[QueuedVideo]:
        """Get the next pending video to download."""

        with self.lock:
            for video in self.videos:
                if video.status == VideoStatus.PENDING:
                    return video
            return None

    def get_next_retryable(self) -> Optional[QueuedVideo]:
        """Get the next video that can be retried (failed with attempts remaining)."""

        with self.lock:
            for video in self.videos:
                if video.status == VideoStatus.FAILED and video.attempts < video.max_attempts:
                    return video
            return None

    def mark_downloading(self, video_id: str) -> None:
        """Mark a video as currently downloading."""

        with self.lock:
            video = self.get_by_id(video_id)
            if video:
                video.status = VideoStatus.DOWNLOADING
                video.last_attempt_time = datetime.now().isoformat()
                self.save()

    def mark_completed(self, video_id: str) -> None:
        """Mark a video as successfully downloaded."""

        with self.lock:
            video = self.get_by_id(video_id)
            if video:
                video.status = VideoStatus.COMPLETED
                video.completed_time = datetime.now().isoformat()
                self.save()

    def mark_failed(self, video_id: str, error: str) -> None:
        """Mark a video as failed and increment retry counter."""

        with self.lock:
            video = self.get_by_id(video_id)
            if video:
                video.attempts += 1
                video.last_error = error
                video.status = VideoStatus.FAILED
                self.save()

    def get_stats(self) -> Dict[str, int]:
        """Get queue statistics."""

        with self.lock:
            stats = {
                "total": len(self.videos),
                "pending": sum(1 for v in self.videos if v.status == VideoStatus.PENDING),
                "downloading": sum(1 for v in self.videos if v.status == VideoStatus.DOWNLOADING),
                "completed": sum(1 for v in self.videos if v.status == VideoStatus.COMPLETED),
                "failed": sum(1 for v in self.videos if v.status == VideoStatus.FAILED),
                "retryable": sum(1 for v in self.videos if v.status == VideoStatus.FAILED and v.attempts < v.max_attempts),
            }
            return stats

    def clear(self) -> None:
        """Clear all videos from the queue."""

        with self.lock:
            self.videos = []
            self.save()


def populate_queue_from_metadata(metadata_path: str, queue: DownloadQueue, archive_path: Optional[str] = None) -> None:
    """Populate queue from metadata file."""

    # Load metadata
    if not os.path.exists(metadata_path):
        print(f"Error: Metadata file not found: {metadata_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: Failed to parse metadata: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load download archive
    archive_ids: Set[str] = set()
    if archive_path and os.path.exists(archive_path):
        archive_ids = downloader._load_download_archive(archive_path)

    # Add videos to queue
    added_count = 0
    skipped_count = 0

    for channel in metadata.get("channels", []):
        if channel.get("error"):
            continue

        channel_url = channel.get("url", "unknown")
        videos = channel.get("videos", [])

        for video in videos:
            video_id = video.get("video_id")
            if not video_id:
                continue

            # Skip if already in archive
            if video_id in archive_ids:
                skipped_count += 1
                continue

            # Create queued video
            queued_video = QueuedVideo(
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                title=video.get("title"),
                channel_url=channel_url,
                status=VideoStatus.PENDING,
            )

            queue.add_video(queued_video)
            added_count += 1

    print(f"\n[queue] Populated queue from metadata:")
    print(f"  Added: {added_count} videos")
    print(f"  Skipped (in archive): {skipped_count} videos")


def download_from_queue(queue: DownloadQueue, args: argparse.Namespace, max_workers: int = 1) -> None:
    """Download videos from the queue."""

    print("\n" + "=" * 70)
    print("Starting queue-based download")
    print("=" * 70)
    print(f"Workers: {max_workers}")
    print("=" * 70)

    # For now, use single-threaded download (can be extended to multi-threaded)
    if max_workers > 1:
        print("Note: Multi-threaded downloads not yet implemented. Using single worker.")

    while True:
        # Get next video
        video = queue.get_next_pending()

        if not video:
            # Try to find retryable videos
            video = queue.get_next_retryable()

        if not video:
            print("\n[queue] No more videos to download.")
            break

        # Calculate exponential backoff for retries
        if video.attempts > 0:
            backoff_seconds = min(60 * (2 ** video.attempts), 3600)  # Max 1 hour
            print(f"\n[queue] Retry attempt {video.attempts + 1}/{video.max_attempts} for {video.video_id}")
            print(f"[queue] Waiting {backoff_seconds}s before retry (exponential backoff)...")
            time.sleep(backoff_seconds)

        # Mark as downloading
        queue.mark_downloading(video.video_id)

        print(f"\n[download] Downloading {video.video_id}: {video.title or '(no title)'}")

        # Download using yt-dlp
        try:
            downloader.download_videos_from_urls([video.video_url], args)
            queue.mark_completed(video.video_id)
            print(f"[queue] ✓ Completed {video.video_id}")

        except Exception as exc:
            error_msg = str(exc)
            queue.mark_failed(video.video_id, error_msg)
            print(f"[queue] ✗ Failed {video.video_id}: {error_msg}", file=sys.stderr)

            if video.attempts >= video.max_attempts:
                print(f"[queue] Max attempts reached for {video.video_id}, will not retry.", file=sys.stderr)

    # Print final stats
    stats = queue.get_stats()
    print("\n" + "=" * 70)
    print("Queue Download Summary")
    print("=" * 70)
    print(f"Total videos: {stats['total']}")
    print(f"Completed: {stats['completed']}")
    print(f"Failed (max attempts): {stats['failed'] - stats['retryable']}")
    print(f"Failed (retryable): {stats['retryable']}")
    print(f"Pending: {stats['pending']}")
    print("=" * 70)


def show_queue_status(queue: DownloadQueue) -> None:
    """Display queue status."""

    stats = queue.get_stats()

    print("\n" + "=" * 70)
    print("Download Queue Status")
    print("=" * 70)
    print(f"Total videos: {stats['total']}")
    print(f"Pending: {stats['pending']}")
    print(f"Downloading: {stats['downloading']}")
    print(f"Completed: {stats['completed']}")
    print(f"Failed (retryable): {stats['retryable']}")
    print(f"Failed (permanent): {stats['failed'] - stats['retryable']}")
    print("=" * 70)

    # Show sample of each status
    for status in VideoStatus:
        videos = [v for v in queue.videos if v.status == status]
        if videos:
            print(f"\n{status.value.upper()} ({len(videos)}):")
            for video in videos[:5]:  # Show first 5
                print(f"  - {video.video_id}: {video.title or '(no title)'}")
                if video.last_error:
                    print(f"    Error: {video.last_error}")
            if len(videos) > 5:
                print(f"  ... and {len(videos) - 5} more")


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Queue-based download manager for YouTube videos."
    )

    # Actions
    parser.add_argument(
        "--populate",
        action="store_true",
        help="Populate queue from metadata file",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Start downloading from queue",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show queue status",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the entire queue",
    )

    # Input
    parser.add_argument(
        "--metadata",
        default=None,
        help="Path to metadata JSON file (for --populate)",
    )
    parser.add_argument(
        "--queue-file",
        default="download_queue.json",
        help="Path to queue file (default: download_queue.json)",
    )

    # Download options
    parser.add_argument(
        "--output",
        default="./downloads",
        help="Directory where videos will be stored (default: ./downloads)",
    )
    parser.add_argument(
        "--archive",
        default=None,
        help="Path to yt-dlp download archive",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent download workers (default: 1)",
    )
    parser.add_argument(
        "--rate-limit",
        default=None,
        help="Limit download rate (passed to yt-dlp)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Concurrent fragment downloads (passed to yt-dlp)",
    )
    parser.add_argument(
        "--format",
        default=None,
        help="Format selector for yt-dlp",
    )
    parser.add_argument(
        "--skip-subtitles",
        action="store_true",
        help="Disable subtitle downloads",
    )
    parser.add_argument(
        "--skip-thumbs",
        action="store_true",
        help="Disable thumbnail downloads",
    )
    parser.add_argument(
        "--merge-output-format",
        default=None,
        help="Container for merged downloads",
    )

    # Authentication
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Reuse cookies from the specified browser",
    )
    parser.add_argument(
        "--allow-restricted",
        action="store_true",
        help="Allow restricted/private videos",
    )

    # Rate limiting
    parser.add_argument(
        "--sleep-requests",
        type=float,
        default=2.0,
        help="Seconds to sleep between HTTP requests (default: 2.0)",
    )
    parser.add_argument(
        "--sleep-interval",
        type=float,
        default=3.0,
        help="Minimum randomized sleep between downloads (default: 3.0)",
    )
    parser.add_argument(
        "--max-sleep-interval",
        type=float,
        default=8.0,
        help="Maximum randomized sleep between downloads (default: 8.0)",
    )

    # YouTube options
    parser.add_argument(
        "--youtube-client",
        choices=downloader.PLAYER_CLIENT_CHOICES,
        default=None,
        help="Force a specific YouTube client",
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

    # Failure handling
    parser.add_argument(
        "--failure-limit",
        type=downloader.positive_int,
        default=downloader.DEFAULT_FAILURE_LIMIT,
        help="Number of failed downloads per client before switching",
    )

    args = parser.parse_args(argv)

    # Validate actions
    actions = [args.populate, args.download, args.status, args.clear]
    if sum(actions) == 0:
        parser.error("One of --populate, --download, --status, or --clear must be specified")
    if sum(actions) > 1:
        parser.error("Only one action can be specified at a time")

    # Validate metadata for populate
    if args.populate and not args.metadata:
        parser.error("--metadata is required with --populate")

    # Apply authentication defaults
    downloader.apply_authentication_defaults(args)

    # Set default archive
    if not args.archive:
        args.archive = os.path.join(args.output, ".download-archive.txt")

    # Ensure output directory exists
    os.makedirs(args.output, exist_ok=True)

    # Set defaults for attributes required by build_ydl_options
    if not hasattr(args, 'since'):
        args.since = None
    if not hasattr(args, 'until'):
        args.until = None
    if not hasattr(args, 'no_shorts'):
        args.no_shorts = False

    return args


def main(argv=None) -> int:
    """Main entry point."""

    args = parse_args(argv)

    # Create queue
    queue = DownloadQueue(queue_file=args.queue_file)

    if args.populate:
        print("=" * 70)
        print("Populating Queue from Metadata")
        print("=" * 70)
        populate_queue_from_metadata(args.metadata, queue, args.archive)
        show_queue_status(queue)

    elif args.download:
        print("=" * 70)
        print("Queue-Based Download Manager")
        print("=" * 70)
        download_from_queue(queue, args, max_workers=args.workers)

    elif args.status:
        show_queue_status(queue)

    elif args.clear:
        print("Clearing queue...")
        queue.clear()
        print("Queue cleared.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
