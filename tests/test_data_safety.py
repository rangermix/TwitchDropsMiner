"""A damaged settings.json must not stop startup or be silently overwritten."""

import json
from unittest.mock import patch

import pytest

from src.config.settings import Settings, default_settings
from src.utils import json_load


ENTRY = {
    "id": "old", "claimed_at": "2026-01-01T00:00:00+00:00", "game": "Game",
    "campaign": "Camp", "drop_name": "Drop", "benefits": ["Badge"],
    "required_minutes": 30, "campaign_id": "c",
}


def test_json_load_rejects_a_non_object(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        json_load(path, {})


@pytest.mark.parametrize("content", ["{trunc", "[]", b"\xff\xfe", '{"proxy": {"__type": "URL"}}'])
def test_damaged_settings_fall_back_to_defaults_and_are_kept(tmp_path, content):
    path = tmp_path / "settings.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    with patch("src.config.settings.SETTINGS_PATH", path):
        settings = Settings()
        assert settings.games_to_watch == default_settings["games_to_watch"]
        settings.save()
    assert path.with_name("settings.json.bad").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["language"] == default_settings["language"]
