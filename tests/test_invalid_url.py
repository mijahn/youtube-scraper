"""Simple regression test for handling invalid channel list URLs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "download_channel_videos.py"

    invalid_url = "http://127.0.0.1:9/does-not-exist.txt"

    result = subprocess.run(
        [sys.executable, str(script), "--channels-url", invalid_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode != 0, "Expected non-zero exit code for invalid URL"
    assert invalid_url in result.stderr, "Error message should include the failing URL"
    assert "Failed to fetch source list" in result.stderr


if __name__ == "__main__":
    main()
