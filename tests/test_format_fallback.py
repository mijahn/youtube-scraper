import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import download_channel_videos as dc


def make_args(**overrides):
    defaults = {
        "output": "downloads",
        "skip_thumbs": False,
        "skip_subtitles": False,
        "archive": None,
        "rate_limit": None,
        "concurrency": None,
        "since": None,
        "until": None,
        "cookies_from_browser": None,
        "sleep_requests": None,
        "sleep_interval": None,
        "max_sleep_interval": None,
        "allow_restricted": False,
        "youtube_client": None,
        "youtube_fetch_po_token": None,
        "youtube_po_token": [],
        "youtube_player_params": None,
        "no_shorts": False,
        "max": None,
        "format": None,
    }
    defaults.update(overrides)
    args = SimpleNamespace(**defaults)
    dc.apply_authentication_defaults(args, environ={})
    return args


def test_ios_client_uses_format_fallback(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args(format="bestvideo+bestaudio/best", no_shorts=True)

    monkeypatch.setattr(dc, "DEFAULT_PLAYER_CLIENTS", ("ios", "web"))
    monkeypatch.setattr(dc, "PLAYER_CLIENT_CHOICES", ("ios", "web"))
    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: [])

    class FakeYoutubeDL:
        calls = []

        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def _detect_client(self) -> Optional[str]:
            youtube_args = self.params.get("extractor_args", {}).get("youtube", {})
            clients = youtube_args.get("player_client") or []
            return clients[0] if clients else None

        def download(self, urls):
            client = self._detect_client()
            FakeYoutubeDL.calls.append(
                {"client": client, "format": self.params.get("format")}
            )
            if client == "ios":
                raise dc.DownloadError("simulated ios failure")

            for hook in self.params.get("progress_hooks", []):
                hook({
                    "status": "downloading",
                    "info_dict": {"id": "abc123", "title": "Sample"},
                })
                hook({
                    "status": "finished",
                    "info_dict": {"id": "abc123", "title": "Sample"},
                })
            return 0

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    FakeYoutubeDL.calls = []

    dc.download_source(source, args)

    out, err = capsys.readouterr()

    clients_seen = [call["client"] for call in FakeYoutubeDL.calls]
    assert "ios" in clients_seen and "web" in clients_seen
    assert clients_seen.index("ios") < clients_seen.index("web")

    ios_calls = [call for call in FakeYoutubeDL.calls if call["client"] == "ios"]
    web_calls = [call for call in FakeYoutubeDL.calls if call["client"] == "web"]

    assert ios_calls and web_calls
    assert ios_calls[0]["format"] == "best"
    assert web_calls[0]["format"] == "bestvideo+bestaudio/best"

    assert "format=best (requested bestvideo+bestaudio/best)" in out
    assert "format_fallback=Requested format 'bestvideo+bestaudio/best'" in out
