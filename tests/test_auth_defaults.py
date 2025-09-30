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
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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
