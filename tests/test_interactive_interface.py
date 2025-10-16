import hashlib
import json
import os
import urllib.parse

import pytest

import interactive_interface as interface


def test_detect_new_sources_filters_and_sorts():
    previous = ["channel:https://example.com", "  playlist:https://foo"]
    current = [
        "channel:https://example.com",
        "playlist:https://foo",
        "video:https://bar",
        "  ",
    ]

    assert interface.detect_new_sources(previous, current) == ["video:https://bar"]


def test_state_path_for_channels(tmp_path):
    channels_path = tmp_path / "configs" / "channels.txt"
    channels_path.parent.mkdir()
    expected = channels_path.parent / ".channels_state.json"
    assert (
        interface.state_path_for_channels(str(channels_path), None) == str(expected)
    )


def test_state_path_for_remote_channels():
    url = "https://example.com/path/to/channels.txt"
    parsed = urllib.parse.urlparse(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    joined = "_".join(part for part in (parsed.netloc, parsed.path.strip("/")) if part)
    safe = joined.replace("/", "_").replace(":", "_") or "remote"
    expected = os.path.join(os.getcwd(), f".channels_state_{safe}_{digest}.json")

    assert interface.state_path_for_channels(None, url) == expected


def test_known_sources_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    sources = ["channel:https://example.com", "playlist:https://foo"]

    interface.save_known_sources(str(state_file), sources)

    with open(state_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["sources"] == sources

    loaded = interface.load_known_sources(str(state_file))
    assert loaded == sources


def test_load_known_sources_missing_file(tmp_path):
    missing_path = tmp_path / "missing.json"
    assert interface.load_known_sources(str(missing_path)) == []


def test_parse_interface_args_includes_channels_url():
    url = "https://example.com/list.txt"
    args = interface.parse_interface_args(["--channels-url", url])

    assert args.channels_url == url
    assert args.channels_file == "channels.txt"


def test_perform_scan_remote_sources(monkeypatch, tmp_path):
    url = "https://example.com/list.txt"
    source_line = "channel:https://youtube.com/@example"
    source = interface.downloader.parse_source_line(source_line)

    def fake_load_sources_from_url(target_url):
        assert target_url == url
        return [source], [source_line]

    def fake_collect_all_video_ids(urls, args, player_client):
        assert urls
        return {"abc123"}

    monkeypatch.setattr(
        interface.downloader, "load_sources_from_url", fake_load_sources_from_url
    )
    monkeypatch.setattr(
        interface.downloader, "_load_download_archive", lambda path: set()
    )
    monkeypatch.setattr(
        interface.downloader, "collect_all_video_ids", fake_collect_all_video_ids
    )
    monkeypatch.setattr(interface.downloader, "normalize_url", lambda value: value)
    monkeypatch.setattr(
        interface.downloader,
        "summarize_source_label",
        lambda source, display_url: "Example Channel",
    )

    args = interface.parse_interface_args(
        ["--channels-url", url, "--output", str(tmp_path / "out")]
    )
    built_args = interface.build_args_from_options(args)
    config = interface.InterfaceConfig(
        channels_file=None,
        channels_url=url,
        args=built_args,
        state_path=str(tmp_path / "state.json"),
    )

    scan = interface.perform_scan(config, update_state=True)

    assert scan is not None
    assert scan.raw_lines == [source_line]
    assert scan.new_sources == [source_line]
    assert scan.statuses[0].pending_videos == 1

    with open(config.state_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["sources"] == [source_line]
