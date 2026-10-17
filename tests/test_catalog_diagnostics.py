"""Catalog diagnostics expose fixed categories, never response values."""

import json
from unittest.mock import AsyncMock

import pytest

from src.auth.imported_session import SessionTransport, catalog_diagnostic
from src.auth.session_bundle import SessionBundle, SessionError
from src.config import ClientType
from tests.test_imported_session import bundle_data, catalog


@pytest.mark.parametrize("message,category", [
    ("PersistedQueryNotFound", "persisted_query_not_found"),
    ("failed integrity check", "integrity"),
    ("service error", "service"),
    ("secret-response", "other"),
    ("failed integrity check secret-response", "other"),
    ({"secret-response": "private"}, "other"),
])
def test_error_categories_never_echo_values(message, category):
    data = catalog()
    data[1]["errors"] = [{"message": message, "extensions": {"secret-response": "private"}}]
    result = catalog_diagnostic(data)
    assert result["Campaigns"]["errors"] == [category]
    text = json.dumps(result)
    assert "secret-response" not in text and "private" not in text


@pytest.mark.parametrize("value,expected", [(None, "null"), ([], "list"), ({}, "object"), ("secret-response", "other"), (12, "other")])
def test_catalog_shape_only(value, expected):
    data = catalog()
    data[0]["data"]["currentUser"]["inventory"]["gameEventDrops"] = value
    result = catalog_diagnostic(data)
    assert result["Inventory"]["gameEventDrops"] == expected
    assert "secret-response" not in json.dumps(result)


@pytest.mark.parametrize("data", [None, {}, "secret-response", [], [{"errors": "secret-response"}], [None, None]])
def test_malformed_diagnostic_is_bounded_and_redacted(data):
    text = json.dumps(catalog_diagnostic(data))
    assert len(text) < 1000
    assert "secret-response" not in text


@pytest.mark.asyncio
async def test_failed_catalog_logs_categories_but_still_rejects(caplog):
    data = catalog()
    data[1]["errors"] = [{"message": "PersistedQueryNotFound", "private": "secret-response"}]
    transport = SessionTransport()
    transport.request = AsyncMock(side_effect=[
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, data,
    ])
    with pytest.raises(SessionError) as error:
        await transport.validate(SessionBundle.from_dict(bundle_data(), now=1000), None)
    assert error.value.code == "CATALOG"
    assert "persisted_query_not_found" in caplog.text
    for secret in ("secret-response", "test-token", "test-integrity", "test-device"):
        assert secret not in caplog.text


def test_inventory_error_is_classified_independently():
    data = catalog()
    data[0]["errors"] = [{"message": "failed integrity check"}]
    result = catalog_diagnostic(data)
    assert result["Inventory"]["errors"] == ["integrity"]
    assert result["Campaigns"]["errors"] == []


@pytest.mark.asyncio
async def test_valid_catalog_produces_no_warning(caplog):
    transport = SessionTransport()
    transport.request = AsyncMock(side_effect=[
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, catalog(),
    ])
    identity = await transport.validate(SessionBundle.from_dict(bundle_data(), now=1000), None)
    assert identity.user_id == 42
    assert "Catalog validation rejected" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, {}, [], [None, None], catalog() + [{}]])
async def test_malformed_catalog_remains_rejected(data, caplog):
    transport = SessionTransport()
    transport.request = AsyncMock(side_effect=[
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, data,
    ])
    with pytest.raises(SessionError) as error:
        await transport.validate(SessionBundle.from_dict(bundle_data(), now=1000), None)
    assert error.value.code == "CATALOG"
    assert "Catalog validation rejected" in caplog.text
