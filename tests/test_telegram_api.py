"""Telegram API tests use stored credentials without exposing or sending them."""

import asyncio
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import default_settings
from src.services.telegram_service import TelegramNotifier
from src.web.app import SettingsUpdate, TelegramTestRequest
from src.web.managers.settings import TELEGRAM_TOKEN_MASK, SettingsManager


web_app = importlib.import_module("src.web.app")


@pytest.fixture
def configured_gui():
    settings = SimpleNamespace(**copy.deepcopy(default_settings))
    settings.telegram_bot_token = "dummy-stored-token"
    settings.telegram_chat_id = "42"
    settings.save = MagicMock()
    return SimpleNamespace(settings=SettingsManager(AsyncMock(), settings, MagicMock()))


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", TELEGRAM_TOKEN_MASK])
async def test_connection_api_uses_stored_token_and_requested_chat(configured_gui, token):
    with (
        patch.object(web_app, "gui_manager", configured_gui),
        patch.object(TelegramNotifier, "test_connection", autospec=True, return_value=True) as send,
    ):
        response = await web_app.test_telegram(
            TelegramTestRequest(telegram_bot_token=token, telegram_chat_id=" 7 ")
        )
    notifier = send.call_args.args[0]
    assert notifier.bot_token == "dummy-stored-token"
    assert notifier.chat_id == "7"
    assert response["success"] is True
    assert "dummy-stored-token" not in str(response)
    configured_gui.settings._settings.save.assert_not_called()
    assert configured_gui.settings._settings.telegram_chat_id == "42"


@pytest.mark.asyncio
async def test_connection_api_tests_replacement_without_persisting_it(configured_gui):
    with (
        patch.object(web_app, "gui_manager", configured_gui),
        patch.object(TelegramNotifier, "test_connection", autospec=True, return_value=False) as send,
    ):
        response = await web_app.test_telegram(
            TelegramTestRequest(telegram_bot_token=" dummy-replacement ", telegram_chat_id="42")
        )
    assert send.call_args.args[0].bot_token == "dummy-replacement"
    assert response["success"] is False
    assert configured_gui.settings._settings.telegram_bot_token == "dummy-stored-token"
    configured_gui.settings._settings.save.assert_not_called()


@pytest.mark.asyncio
async def test_connection_api_does_not_send_without_credentials(configured_gui):
    configured_gui.settings._settings.telegram_bot_token = ""
    with (
        patch.object(web_app, "gui_manager", configured_gui),
        patch.object(TelegramNotifier, "test_connection", new_callable=AsyncMock) as send,
    ):
        response = await web_app.test_telegram(
            TelegramTestRequest(telegram_bot_token="", telegram_chat_id="42")
        )
    assert response["success"] is False
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", ["7", ""])
async def test_settings_api_saves_or_disables_chat_without_replacing_token(configured_gui, chat_id):
    with patch.object(web_app, "gui_manager", configured_gui):
        response = await web_app.update_settings(
            SettingsUpdate(telegram_bot_token="", telegram_chat_id=chat_id)
        )
    await asyncio.sleep(0)
    stored = configured_gui.settings._settings
    assert stored.telegram_bot_token == "dummy-stored-token"
    assert stored.telegram_chat_id == chat_id
    stored.save.assert_called_once_with()
    assert response["settings"]["telegram_bot_token"] == TELEGRAM_TOKEN_MASK
    assert response["settings"]["telegram_configured"] is True
    assert "dummy-stored-token" not in str(response)
    configured_gui.settings._broadcaster.emit.assert_awaited_once_with(
        "settings_updated", response["settings"]
    )
