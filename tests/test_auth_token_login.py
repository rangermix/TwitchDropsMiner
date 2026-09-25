"""Checks for the dashboard auth-token login used by ClientType.WEB."""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth import integrity
from src.web.managers.login import LoginFormManager


web_app = importlib.import_module("src.web.app")


def _manager() -> LoginFormManager:
    broadcaster = MagicMock()
    broadcaster.emit = AsyncMock()
    return LoginFormManager(broadcaster, MagicMock())


def test_ask_auth_token_waits_for_submission():
    async def scenario():
        manager = _manager()
        waiting = asyncio.create_task(manager.ask_auth_token())
        await asyncio.sleep(0)
        # late-connecting clients learn about the prompt from the status
        assert manager.get_status().get("auth_token_pending") is True

        manager.submit_auth_token("tok")
        assert await waiting == "tok"
        assert "auth_token_pending" not in manager.get_status()
        # the secret is not kept around once handed over
        assert manager._auth_token == ""

    asyncio.run(scenario())


def _submit(token: str, *, pending: bool, details: dict | None):
    manager = _manager()
    manager._auth_token_pending = pending
    manager.submit_auth_token = MagicMock()  # type: ignore[method-assign]
    gui = MagicMock()
    gui.login = manager
    with (
        patch.object(web_app, "gui_manager", gui),
        patch.object(web_app, "validate_auth_token", AsyncMock(return_value=details)),
    ):
        result = asyncio.run(web_app.submit_auth_token(web_app.AuthTokenRequest(auth_token=token)))
    return result, manager.submit_auth_token


def _status(**kwargs) -> int:
    with pytest.raises(HTTPException) as err:
        _submit(**kwargs)
    return err.value.status_code


def test_rejects_when_no_login_is_pending():
    # never swap the session of a running miner
    assert _status(token="tok", pending=False, details={"client_id": integrity.CLIENT_ID}) == 409


def test_rejects_token_twitch_does_not_accept():
    assert _status(token="typo", pending=True, details=None) == 400


def test_rejects_token_from_another_client():
    # a TV/app token logs in but never sees a campaign
    assert _status(token="tok", pending=True, details={"client_id": "ue6666qo983tsx6so1t0vnawi233wa"}) == 400


def test_accepts_valid_browser_token():
    result, submitted = _submit(
        "  tok  ", pending=True, details={"client_id": integrity.CLIENT_ID, "login": "someone"}
    )
    assert result == {"success": True, "login": "someone"}
    submitted.assert_called_once_with("tok")  # whitespace from the paste is stripped
