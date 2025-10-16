import json

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
    assert interface.state_path_for_channels(str(channels_path)) == str(expected)


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
