from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import download_channel_videos as dc


def make_args(**overrides):
    defaults = {
        "cookies_from_browser": None,
        "youtube_po_token": [],
        "youtube_fetch_po_token": None,
        "bgutil_provider": None,
        "bgutil_http_base_url": None,
        "bgutil_http_disable_innertube": None,
        "bgutil_script_path": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_bgutil_defaults_populate_without_env():
    args = make_args()
    dc.apply_authentication_defaults(args, environ={})

    assert args.bgutil_provider == dc.DEFAULT_BGUTIL_PROVIDER_MODE
    assert args.bgutil_http_base_url == dc.DEFAULT_BGUTIL_HTTP_BASE_URL
    assert args.bgutil_provider_candidates == ["http"]
    assert args.bgutil_provider_resolved == "http"
    assert args.bgutil_http_disable_innertube is False


def test_env_defaults_populate_missing_fields():
    args = make_args()
    env = {
        dc.ENV_COOKIES_FROM_BROWSER: "chrome",
        dc.ENV_PO_TOKENS: "web.web+abc123,ios.ios+def456\nweb.web+abc123",
        dc.ENV_FETCH_PO_TOKEN: "never",
    }

    dc.apply_authentication_defaults(args, environ=env)

    assert args.cookies_from_browser == "chrome"
    assert args.youtube_po_token == ["web.web+abc123", "ios.ios+def456"]
    assert args.youtube_fetch_po_token == "never"
    assert args.bgutil_provider == dc.DEFAULT_BGUTIL_PROVIDER_MODE
    assert args.bgutil_provider_candidates == ["http"]


def test_cli_values_take_precedence_over_env():
    args = make_args(
        cookies_from_browser="firefox",
        youtube_po_token=["android.context+123"],
        youtube_fetch_po_token="auto",
    )
    env = {
        dc.ENV_COOKIES_FROM_BROWSER: "chrome",
        dc.ENV_PO_TOKENS: "ios.context+456",
        dc.ENV_FETCH_PO_TOKEN: "never",
    }

    dc.apply_authentication_defaults(args, environ=env)

    assert args.cookies_from_browser == "firefox"
    assert args.youtube_po_token == ["android.context+123", "ios.context+456"]
    assert args.youtube_fetch_po_token == "auto"


def test_bgutil_env_overrides(tmp_path):
    script_path = tmp_path / "generate_once.js"
    script_path.write_text("console.log('bgutil');")

    args = make_args()
    env = {
        dc.ENV_BGUTIL_PROVIDER_MODE: "script",
        dc.ENV_BGUTIL_SCRIPT_PATH: str(script_path),
    }

    dc.apply_authentication_defaults(args, environ=env)

    assert args.bgutil_provider == "script"
    assert args.bgutil_script_path == str(script_path)
    assert args.bgutil_provider_candidates == ["script"]
    assert args.bgutil_provider_resolved == "script"
