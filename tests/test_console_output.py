from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.web.managers.console import ConsoleOutputManager


@pytest.mark.asyncio
async def test_identical_keyed_messages_are_collapsed():
    broadcaster = AsyncMock()
    output = ConsoleOutputManager(broadcaster)

    with patch("src.web.managers.console.logger") as logger:
        output.print("Waiting", collapse_key="status.no_campaign")
        output.print("Waiting", collapse_key="status.no_campaign")
        await asyncio.sleep(0)

    assert len(output.get_history()) == 1
    broadcaster.emit.assert_awaited_once()
    logger.info.assert_called_once_with("Waiting")


@pytest.mark.asyncio
async def test_intervening_message_resets_collapsing():
    broadcaster = AsyncMock()
    output = ConsoleOutputManager(broadcaster)

    output.print("Waiting", collapse_key="status.no_campaign")
    output.print("Campaign refresh started")
    output.print("Waiting", collapse_key="status.no_campaign")
    await asyncio.sleep(0)

    assert len(output.get_history()) == 3
    assert broadcaster.emit.await_count == 3


@pytest.mark.asyncio
async def test_changed_text_with_same_key_is_emitted():
    broadcaster = AsyncMock()
    output = ConsoleOutputManager(broadcaster)

    output.print("Waiting", collapse_key="status.no_campaign")
    output.print("En attente", collapse_key="status.no_campaign")
    await asyncio.sleep(0)

    assert len(output.get_history()) == 2
    assert broadcaster.emit.await_count == 2


@pytest.mark.asyncio
async def test_unkeyed_duplicates_keep_existing_behavior():
    broadcaster = AsyncMock()
    output = ConsoleOutputManager(broadcaster)

    output.print("Ordinary message")
    output.print("Ordinary message")
    await asyncio.sleep(0)

    assert len(output.get_history()) == 2
    assert broadcaster.emit.await_count == 2
