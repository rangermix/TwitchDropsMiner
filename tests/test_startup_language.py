"""The application can start with a saved language that is no longer available."""

import json
import logging
import runpy
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import truststore

from src import i18n
from src.config import DEFAULT_LANG
from src.config import settings as settings_module
from src.core import client as client_module
from src.web import app as webapp
from src.web import gui_manager


@pytest.mark.parametrize(("saved", "initial", "expected"), [
    ("removed-locale\nuntrusted text", "Deutsch", DEFAULT_LANG),
    ("", DEFAULT_LANG, DEFAULT_LANG),
    ("Deutsch", DEFAULT_LANG, "Deutsch"),
])
def test_startup_uses_available_saved_language(tmp_path, monkeypatch, caplog, saved, initial, expected):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"language": saved}), encoding="utf-8")
    monkeypatch.setattr(settings_module, "SETTINGS_PATH", settings_path)
    monkeypatch.chdir(tmp_path)
    translator = i18n.Translator()
    translator.set_language(initial)
    monkeypatch.setattr(i18n, "_", translator)

    client = SimpleNamespace(run=AsyncMock(), shutdown=AsyncMock(), print=Mock())
    create_client = Mock(return_value=client)
    monkeypatch.setattr(client_module, "Twitch", create_client)
    monkeypatch.setattr(gui_manager, "WebGUIManager", Mock())
    monkeypatch.setattr(webapp, "set_managers", Mock())
    monkeypatch.setattr(webapp, "run_server", AsyncMock())
    monkeypatch.setattr(webapp, "shutdown_server", AsyncMock())
    monkeypatch.setattr(truststore, "inject_into_ssl", Mock())

    logger = logging.getLogger("TwitchDrops")
    caplog.set_level(logging.INFO, logger=logger.name)
    # Leave pytest's log capture attached and close the entry point's temporary handlers.
    monkeypatch.setattr(logger, "addHandler", lambda handler: handler.close())

    with warnings.catch_warnings(), pytest.raises(SystemExit) as exit_info:
        runpy.run_module("src", run_name="__main__")

    assert exit_info.value.code == 0
    client.run.assert_awaited_once()
    webapp.run_server.assert_awaited_once()
    assert translator.current_language == expected
    assert create_client.call_args.args[0].language == expected
    assert json.loads(settings_path.read_text(encoding="utf-8"))["language"] == expected
    missing_language = "Saved language is unavailable. Using the default language."
    assert (missing_language in caplog.messages) == (saved != expected)
    assert "removed-locale" not in caplog.text and "untrusted text" not in caplog.text
