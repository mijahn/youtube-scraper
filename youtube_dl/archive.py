"""Download archive management for tracking downloaded videos."""

import contextlib
import os
import sys
from typing import Iterable, Optional, Set


def load_download_archive(path: Optional[str]) -> Set[str]:
    """Load previously downloaded video IDs from archive file."""
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


def write_download_archive(path: Optional[str], video_ids: Iterable[str]) -> None:
    """Write video IDs to the download archive file."""
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


def append_to_download_archive(path: Optional[str], video_id: Optional[str]) -> None:
    """Append a single video ID to the download archive."""
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
