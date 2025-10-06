from types import SimpleNamespace

import pytest

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
    }
    defaults.update(overrides)
    args = SimpleNamespace(**defaults)
    dc.apply_authentication_defaults(args, environ={})
    return args


def test_run_download_attempt_uses_persistent_archive(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "download-archive.txt"
    archive_path.write_text("known-id\n", encoding="utf-8")

    args = make_args(output=str(tmp_path), archive=str(archive_path))

    existing_ids = dc.load_download_archive(str(archive_path))
    assert existing_ids == {"known-id"}

    archive_state = dc.DownloadArchiveState(path=str(archive_path), known_ids=set(existing_ids))
    downloaded_ids = set(existing_ids)

    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params
            assert params.get("download_archive") == str(archive_path)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            assert urls == ["https://example.com/playlist"]
            match_filter = self.params.get("match_filter")
            assert match_filter is not None
            reason = match_filter({"id": "known-id"})
            assert reason is not None
            assert "already" in reason.lower()
            assert match_filter({"id": "new-id"}) is None
            for hook in self.params.get("progress_hooks", []):
                hook({"status": "finished", "info_dict": {"id": "new-id", "title": "New video"}})

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    attempt = dc.run_download_attempt(
        ["https://example.com/playlist"],
        args,
        player_client=None,
        max_total=None,
        downloaded_ids=downloaded_ids,
        target_video_ids=None,
        download_archive=archive_state,
    )

    assert attempt.downloaded == 1
    assert attempt.downloaded_video_ids == {"new-id"}
    assert downloaded_ids == {"known-id", "new-id"}
    assert archive_state.known_ids == {"known-id", "new-id"}
    assert archive_path.read_text(encoding="utf-8").splitlines() == ["known-id", "new-id"]


def test_load_download_archive_missing_file(tmp_path) -> None:
    archive_path = tmp_path / "missing.txt"
    ids = dc.load_download_archive(str(archive_path))
    assert ids == set()
