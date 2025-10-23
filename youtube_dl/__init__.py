"""YouTube downloader package."""

# Import main components for easier access
from .config import apply_authentication_defaults, parse_args
from .downloader import download_source
from .errors import ErrorAnalyzer, RemoteSourceError
from .health_check import run_health_check
from .models import Source, SourceType
from .sources import load_sources_from_file, load_sources_from_url, parse_source_line
from .watcher import watch_channels_file

__all__ = [
    "parse_args",
    "apply_authentication_defaults",
    "download_source",
    "run_health_check",
    "watch_channels_file",
    "parse_source_line",
    "load_sources_from_file",
    "load_sources_from_url",
    "Source",
    "SourceType",
    "ErrorAnalyzer",
    "RemoteSourceError",
]
