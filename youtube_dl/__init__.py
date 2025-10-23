"""YouTube downloader package."""

# Import main components for easier access
from .archive import load_download_archive as _load_download_archive
from .config import apply_authentication_defaults, parse_args, positive_int
from .downloader import (
    download_source,
    download_videos_from_urls,
    summarize_source_label,
)
from .errors import ErrorAnalyzer, RemoteSourceError
from .health_check import run_health_check
from .logger import DownloadLogger
from .metadata import collect_all_video_ids
from .models import (
    BGUTIL_PROVIDER_CHOICES,
    DEFAULT_FAILURE_LIMIT,
    DEFAULT_PLAYER_CLIENTS,
    PLAYER_CLIENT_CHOICES,
    Source,
    SourceType,
    VideoMetadata,
    normalize_url,
)
from .sources import load_sources_from_file, load_sources_from_url, parse_source_line
from .watcher import watch_channels_file
from .ytdlp_options import build_ydl_options

__all__ = [
    # Main entry points
    "parse_args",
    "apply_authentication_defaults",
    "download_source",
    "run_health_check",
    "watch_channels_file",
    # Source handling
    "parse_source_line",
    "load_sources_from_file",
    "load_sources_from_url",
    "normalize_url",
    # Models and data structures
    "Source",
    "SourceType",
    "VideoMetadata",
    "ErrorAnalyzer",
    "RemoteSourceError",
    "DownloadLogger",
    # Download functions
    "collect_all_video_ids",
    "download_videos_from_urls",
    "summarize_source_label",
    "build_ydl_options",
    # Configuration
    "positive_int",
    # Constants
    "DEFAULT_PLAYER_CLIENTS",
    "PLAYER_CLIENT_CHOICES",
    "BGUTIL_PROVIDER_CHOICES",
    "DEFAULT_FAILURE_LIMIT",
    # Private (but used by other scripts)
    "_load_download_archive",
]
