from __future__ import annotations

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import patch

import pytest


web_app = importlib.import_module("src.web.app")


class _FakeHistory:
    def __init__(self) -> None:
        self.total_count = 2

    def get_entries(self, **kwargs):
        return [{"id": "1", "claimed_at": "2026-01-01T00:00:00+00:00", "game": "Valorant"}]

    def to_csv(self, **kwargs):
        return "claimed_at,game\n2026-01-01T00:00:00+00:00,Valorant\n"

    def stats_by_game(self):
        return {"Valorant": 2}

    def stats_by_month(self):
        return {"2026-01": 2}

    def clear(self):
        self.total_count = 0


def _fake_client() -> SimpleNamespace:
    return SimpleNamespace(drop_history=_FakeHistory())


@pytest.mark.asyncio
async def test_history_endpoint_returns_entries():
    with patch.object(web_app, "twitch_client", _fake_client()):
        response = await web_app.get_history(game="Valorant", since="2026-01-01", limit=50)

    assert response["total"] == 2
    assert response["entries"][0]["game"] == "Valorant"


@pytest.mark.asyncio
async def test_history_endpoint_requires_client():
    with patch.object(web_app, "twitch_client", None):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await web_app.get_history()
        assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_export_csv_includes_bom_and_filename():
    with patch.object(web_app, "twitch_client", _fake_client()):
        response = await web_app.export_history_csv(game="Valorant")

    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "filename*=UTF-8''drop_history_Valorant.csv" in response.headers["content-disposition"]
    assert response.body.startswith("\ufeff".encode("utf-8"))
    assert "Valorant" in response.body.decode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("game", ["原神", 'A "game"\r\nInjected: value', "Baldur's Gate 3"])
async def test_export_csv_encodes_untrusted_unicode_filename(game):
    with patch.object(web_app, "twitch_client", _fake_client()):
        response = await web_app.export_history_csv(game=game)
    disposition = response.headers["content-disposition"]
    assert 'filename="drop_history.csv"' in disposition
    assert "filename*=UTF-8''" + quote(f"drop_history_{game}.csv", safe="") in disposition
    assert "\r" not in disposition and "\n" not in disposition
    disposition.encode("ascii")


@pytest.mark.asyncio
async def test_stats_endpoint_aggregates():
    with patch.object(web_app, "twitch_client", _fake_client()):
        response = await web_app.get_history_stats()

    assert response == {"total_drops": 2, "by_game": {"Valorant": 2}, "by_month": {"2026-01": 2}}


@pytest.mark.asyncio
async def test_delete_history_endpoint_clears():
    client = _fake_client()
    with patch.object(web_app, "twitch_client", client):
        response = await web_app.clear_history()

    assert response == {"success": True}
    assert client.drop_history.total_count == 0


def test_parse_history_since_accepts_date_and_datetime():
    assert web_app._parse_history_since("2026-02-03") is not None
    assert web_app._parse_history_since("2026-02-03T12:30:00+00:00") is not None
    assert web_app._parse_history_since(None) is None
    assert web_app._parse_history_since("not-a-date") is None


@pytest.mark.parametrize("value", [
    "2026-02-03T12:30:00+10:00", "2026-02-02T21:30:00-05:00",
    "2026-02-03T02:30:00Z", "2026-02-03 02:30:00", "2026-02-03T02:30:00",
])
def test_history_since_preserves_instant_and_normalizes_to_utc(value):
    assert web_app._parse_history_since(value) == datetime(2026, 2, 3, 2, 30, tzinfo=timezone.utc)
