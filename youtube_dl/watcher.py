"""File watching functionality for continuous channel monitoring."""

import os
import time
from typing import List, Optional

from .sources import load_sources_from_file


def watch_channels_file(path: str, args, download_source_func) -> None:
    """Watch a channels file for changes and trigger downloads."""
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
                    download_source_func(source, args)
                last_contents = raw_lines
            else:
                print(f"{os.path.basename(path)} timestamp changed but content is the same; skipping downloads.")

            last_mtime = mtime

        time.sleep(interval)
