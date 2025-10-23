"""Source URL parsing and loading functionality."""

import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

from .errors import RemoteSourceError
from .models import Source, SourceType, normalize_url


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
    """Parse a line from a channels file into a Source object."""
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


def load_sources_from_url(url: str) -> Tuple[List[Source], List[str]]:
    """Load sources from a remote URL with retry logic."""
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
    print(f"Loaded {len(sources)} sources from remote list")
    return sources, raw_lines


def load_sources_from_file(path: str) -> Tuple[List[Source], List[str]]:
    """Load sources from a local file."""
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
    print(f"Loaded {len(sources)} sources from {path}")
    return sources, raw_lines
