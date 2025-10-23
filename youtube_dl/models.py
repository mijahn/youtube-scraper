"""Data models, enums, and constants for YouTube downloader."""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

try:
    from yt_dlp.extractor.youtube import YoutubeIE
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
    except ModuleNotFoundError:  # Older yt-dlp releases expose the constant directly.
        from yt_dlp.extractor.youtube import INNERTUBE_CLIENTS
except ImportError:
    raise ImportError("yt-dlp is not installed. Run: pip install -r requirements.txt")


# Constants
DEFAULT_FAILURE_LIMIT = 10  # Conservative: give each client more tolerance before rotating

# User-Agent rotation pool to appear as different browsers
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
]


@dataclass
class DownloadAttempt:
    """Tracks the state of a download attempt."""
    downloaded: int
    video_unavailable_errors: int
    other_errors: int
    detected_video_ids: Set[str] = field(default_factory=set)
    downloaded_video_ids: Set[str] = field(default_factory=set)
    retryable_error_ids: Set[str] = field(default_factory=set)
    stopped_due_to_limit: bool = False
    failure_count: int = 0
    total_failure_count: int = 0
    failure_limit_reached: bool = False
    consecutive_limit_reached: bool = False
    failure_limit: int = DEFAULT_FAILURE_LIMIT
    rate_limit_pauses: int = 0


@dataclass(frozen=True)
class VideoMetadata:
    """Metadata for a single video."""
    video_id: str
    title: Optional[str] = None


@dataclass
class ErrorPattern:
    """Tracks a specific error pattern and its occurrences."""
    error_type: str
    count: int = 0
    video_ids: List[str] = field(default_factory=list)
    sample_messages: List[str] = field(default_factory=list)
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None

    def record(self, video_id: Optional[str], message: str) -> None:
        """Record an occurrence of this error pattern."""
        self.count += 1
        timestamp = time.time()

        if self.first_seen is None:
            self.first_seen = timestamp
        self.last_seen = timestamp

        if video_id and video_id not in self.video_ids:
            self.video_ids.append(video_id)

        # Keep only the first 5 sample messages to avoid memory bloat
        if len(self.sample_messages) < 5 and message not in self.sample_messages:
            self.sample_messages.append(message)


class SourceType(Enum):
    """Type of YouTube source."""
    CHANNEL = "channel"
    PLAYLIST = "playlist"
    VIDEO = "video"


def normalize_url(url: str) -> str:
    """Normalize and validate a URL."""
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("missing URL")

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = "https://" + cleaned.lstrip("/")

    return cleaned.rstrip("/")


@dataclass(frozen=True)
class Source:
    """Represents a YouTube source (channel, playlist, or video)."""
    kind: SourceType
    url: str

    def build_download_urls(self, include_shorts: bool = True) -> List[str]:
        """Build list of URLs to download from this source."""
        normalized = normalize_url(self.url)

        if self.kind is SourceType.CHANNEL:
            urls: List[str]
            base_channel_url = normalized
            trailing_match = re.search(r"/(videos|shorts|streams|live)$", normalized)

            if trailing_match:
                urls = [normalized]
                base_channel_url = normalized[: -len(trailing_match.group(0))]
            else:
                urls = [normalized + "/videos"]

            if include_shorts:
                shorts_url = base_channel_url + "/shorts"
                if shorts_url not in urls:
                    urls.append(shorts_url)
            return urls

        return [normalized]


class BgUtilProviderMode(Enum):
    """BGUtil provider mode."""
    AUTO = "auto"
    HTTP = "http"
    SCRIPT = "script"
    DISABLED = "disabled"


# Environment variable names
ENV_COOKIES_FROM_BROWSER = "YOUTUBE_SCRAPER_COOKIES_FROM_BROWSER"
ENV_PO_TOKENS = "YOUTUBE_SCRAPER_PO_TOKENS"
ENV_FETCH_PO_TOKEN = "YOUTUBE_SCRAPER_FETCH_PO_TOKEN"
ENV_BGUTIL_PROVIDER_MODE = "YOUTUBE_SCRAPER_BGUTIL_PROVIDER"
ENV_BGUTIL_HTTP_BASE_URL = "YOUTUBE_SCRAPER_BGUTIL_HTTP_BASE_URL"
ENV_BGUTIL_HTTP_DISABLE_INNERTUBE = "YOUTUBE_SCRAPER_BGUTIL_HTTP_DISABLE_INNERTUBE"
ENV_BGUTIL_SCRIPT_PATH = "YOUTUBE_SCRAPER_BGUTIL_SCRIPT_PATH"

# Defaults
DEFAULT_FETCH_PO_TOKEN_BEHAVIOR = "always"
DEFAULT_BGUTIL_PROVIDER_MODE = BgUtilProviderMode.AUTO.value
DEFAULT_BGUTIL_HTTP_BASE_URL = "http://127.0.0.1:4416"

# Player client configuration
PLAYER_CLIENT_CHOICES: Tuple[str, ...] = tuple(
    sorted(client for client in INNERTUBE_CLIENTS if not client.startswith("_"))
)

BGUTIL_PROVIDER_CHOICES: Tuple[str, ...] = tuple(mode.value for mode in BgUtilProviderMode)


def _default_player_clients() -> Tuple[str, ...]:
    """Get default player clients for YouTube extraction."""
    # Start with yt-dlp's defaults but extend with additional clients for better rotation
    defaults = getattr(YoutubeIE, "_DEFAULT_CLIENTS", None)
    base_clients = list(defaults) if defaults else []

    # Extended list of clients for better rate limit avoidance and resilience
    # Prioritize mobile and TV clients as they tend to have better success rates
    preferred_order = ("tv", "web_safari", "web", "android", "ios", "mweb", "android_vr", "tv_embedded")

    # Build final list: start with defaults, then add any from preferred_order not already included
    final_clients = []
    seen = set()

    # Add base clients first
    for client in base_clients:
        if client in PLAYER_CLIENT_CHOICES and client not in seen:
            final_clients.append(client)
            seen.add(client)

    # Add additional clients from preferred order
    for client in preferred_order:
        if client in PLAYER_CLIENT_CHOICES and client not in seen:
            final_clients.append(client)
            seen.add(client)

    # If we still don't have enough clients, add any remaining ones
    if len(final_clients) < 5:
        for client in PLAYER_CLIENT_CHOICES:
            if client not in seen and not client.endswith("_embedded") and not client.endswith("_creator"):
                final_clients.append(client)
                seen.add(client)
                if len(final_clients) >= 8:  # Cap at 8 clients for reasonable rotation
                    break

    return tuple(final_clients) if final_clients else tuple(PLAYER_CLIENT_CHOICES[:3])


DEFAULT_PLAYER_CLIENTS: Tuple[str, ...] = _default_player_clients()

MAX_ATTEMPTS_PER_CLIENT = 5


@dataclass
class FormatSelection:
    """Format selection result."""
    requested: Optional[str]
    effective: Optional[str]
    fallback_reason: Optional[str] = None


MUXED_ONLY_CLIENTS: Set[str] = {"ios"}
