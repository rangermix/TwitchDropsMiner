"""Discord API, credential persistence and transport boundaries; no real sends."""

import asyncio
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings, default_settings
from src.diagnostics import Diagnostics
from src.services.discord_service import DiscordNotifier, _escape_embed
from src.web.managers.settings import DISCORD_WEBHOOK_MASK, SettingsManager
from tests.test_web_auth import client  # noqa: F401 -- shared isolated production HTTP fixture


web = importlib.import_module("src.web.app")
URL = "https://discord.com/api/webhooks/111/dummy-secret"
INVALID = [
    "http://discord.com/api/webhooks/111/secret",
    "https://discord.com.evil.test/api/webhooks/111/secret",
    "https://discord.com@127.0.0.1/api/webhooks/111/secret",
    "https://127.0.0.1/api/webhooks/111/secret",
    "https://discord.com:444/api/webhooks/111/secret",
    URL + "?wait=true", URL + "#fragment", URL + "/extra", URL + "\n",
    "https://discord.com/api/webhooks/111/%2e%2e",
    "https://discord.com/api/webhooks/111/../secret",
    "https://discord.com/api/webhooks/not-a-number/secret",
]


@pytest.fixture
def manager():
    settings = SimpleNamespace(**copy.deepcopy(default_settings))
    settings.discord_webhook_url = URL
    settings.save = MagicMock()
    return SettingsManager(AsyncMock(), settings, MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", DISCORD_WEBHOOK_MASK, URL])
async def test_connection_reuses_saved_secret_without_saving(manager, value, monkeypatch):
    monkeypatch.setattr(web, "gui_manager", SimpleNamespace(settings=manager))
    with patch.object(DiscordNotifier, "test_connection", autospec=True, return_value=True) as send:
        result = await web.test_discord(web.DiscordTestRequest(discord_webhook_url=value))
    assert send.call_args.args[0].webhook_url == URL
    assert result == {"success": True}
    manager._settings.save.assert_not_called()


@pytest.mark.asyncio
async def test_remove_disables_and_masks_settings_broadcast(manager):
    result = manager.update_settings({"discord_remove_webhook": True, "discord_webhook_url": URL})
    await asyncio.sleep(0)
    assert manager._settings.discord_webhook_url == ""
    assert result["discord_configured"] is False
    assert result["discord_webhook_url"] == ""
    manager._settings.save.assert_called_once()
    manager._broadcaster.emit.assert_awaited_once_with("settings_updated", result)
    assert URL not in str(manager._console.print.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [{"discord_remove_webhook": True}, {"discord_webhook_url": URL + "2"}])
async def test_failed_save_restores_previous_webhook_without_broadcast(manager, update):
    manager._settings.save.side_effect = OSError("cannot save " + URL)
    with pytest.raises(OSError):
        manager.update_settings(update)
    assert manager._settings.discord_webhook_url == URL
    await asyncio.sleep(0)
    manager._broadcaster.emit.assert_not_awaited()
    manager._console.print.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", INVALID)
async def test_invalid_destinations_never_send_or_mutate_settings(manager, value, monkeypatch):
    monkeypatch.setattr(web, "gui_manager", SimpleNamespace(settings=manager))
    # Boundary whitespace is intentionally stripped by the settings form/API.
    if value.endswith("\n"):
        assert not DiscordNotifier.is_valid_webhook(value)
        return
    with patch.object(DiscordNotifier, "test_connection", new_callable=AsyncMock) as send:
        result = await web.test_discord(web.DiscordTestRequest(discord_webhook_url=value))
    assert result["success"] is False
    send.assert_not_awaited()
    with pytest.raises(ValueError, match="invalid_discord_webhook"):
        manager.update_settings({"dark_mode": True, "discord_webhook_url": value})
    assert manager._settings.discord_webhook_url == URL
    assert manager._settings.dark_mode is False
    manager._settings.save.assert_not_called()


@pytest.mark.asyncio
async def test_saved_webhook_round_trip_and_removal(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    settings = Settings()
    assert settings.discord_webhook_url == ""
    manager = SettingsManager(AsyncMock(), settings, MagicMock())
    manager.update_settings({"discord_webhook_url": URL})
    assert Settings().discord_webhook_url == URL
    manager.update_settings({"discord_remove_webhook": True})
    assert Settings().discord_webhook_url == ""
    await asyncio.sleep(0)


@pytest.mark.parametrize("path,payload", [
    ("/api/settings", {"discord_webhook_url": {"secret": URL}}),
    ("/api/settings", {"discord_remove_webhook": URL}),
    ("/api/settings", {"discord_webhook_url": URL, "dark_mode": URL}),
    ("/api/settings/test-discord", {"discord_webhook_url": {"secret": URL}}),
])
def test_http_validation_never_echoes_secret(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert "dummy-secret" not in response.text


def test_http_failures_redacted_and_foreign_origins_rejected(client, manager, monkeypatch):
    monkeypatch.setattr(web, "gui_manager", SimpleNamespace(settings=manager))
    manager._settings.save.side_effect = OSError(URL)
    result = client.post("/api/settings", json={"discord_remove_webhook": True})
    assert result.status_code == 500
    assert result.json() == {"detail": "settings_save_failed"}
    assert manager._settings.discord_webhook_url == URL
    with patch.object(DiscordNotifier, "test_connection", new_callable=AsyncMock) as send:
        result = client.post("/api/settings/test-discord", json={}, headers={"Origin": "https://evil.test"})
    assert result.status_code == 403
    send.assert_not_awaited()


def test_test_endpoint_exception_is_redacted(client, manager, monkeypatch):
    monkeypatch.setattr(web, "gui_manager", SimpleNamespace(settings=manager))
    with patch.object(DiscordNotifier, "test_connection", new=AsyncMock(side_effect=RuntimeError(URL))):
        result = client.post("/api/settings/test-discord", json={})
    assert result.json() == {"success": False, "error": "discord_connection_failed"}
    assert URL not in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 204, 301, 302, 307, 400, 429, 500])
async def test_transport_has_no_redirects_mentions_or_secret_capture(status, tmp_path, caplog):
    diagnostics = Diagnostics(tmp_path)
    with patch("src.services.discord_service.aiohttp.ClientSession") as cls:
        session = MagicMock()
        cls.return_value.__aenter__.return_value = session
        response = session.post.return_value.__aenter__.return_value
        response.status = status
        response.text = AsyncMock(return_value=URL)
        result = await DiscordNotifier(URL, diagnostics=diagnostics).test_connection()
    assert result is (status in {200, 204})
    kwargs = session.post.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"].total == 10
    assert kwargs["json"]["allowed_mentions"] == {"parse": []}
    response.text.assert_not_awaited()
    cls.return_value.__aexit__.assert_awaited_once()
    assert diagnostics.apis["discord"]["samples"][-1]["status"] == status
    assert URL not in caplog.text + str(diagnostics.apis)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(URL), OSError(URL), RuntimeError(URL)])
async def test_transport_errors_are_redacted(error, tmp_path, caplog):
    diagnostics = Diagnostics(tmp_path)
    with patch("src.services.discord_service.aiohttp.ClientSession") as cls:
        session = MagicMock()
        cls.return_value.__aenter__.return_value = session
        session.post.return_value.__aenter__.side_effect = error
        assert await DiscordNotifier(URL, diagnostics=diagnostics).test_connection() is False
    cls.return_value.__aexit__.assert_awaited_once()
    assert URL not in caplog.text + str(diagnostics.apis)


@pytest.mark.asyncio
async def test_transport_cancellation_propagates():
    with patch("src.services.discord_service.aiohttp.ClientSession") as cls:
        session = MagicMock()
        cls.return_value.__aenter__.return_value = session
        session.post.return_value.__aenter__.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await DiscordNotifier(URL).test_connection()
    cls.return_value.__aexit__.assert_awaited_once()


def test_dynamic_names_escape_masked_links_and_mentions():
    result = _escape_embed({"title": "T", "fields": [{"name": "Reward", "value": "[free](https://evil.test) <@123> @everyone"}]})
    value = result["fields"][0]["value"]
    assert r"\[free\]\(https://evil.test\)" in value
    assert "<@123>" not in value
    assert "@everyone" not in value
