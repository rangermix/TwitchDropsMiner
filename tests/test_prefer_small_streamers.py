"""Settings > Mining > Prefer small streamers: channel order, validation and the checkbox."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings, default_settings
from src.services.channel_service import ChannelService
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager
from tests.javascript_helpers import NODE
from tests.test_account_link_frontend import SETUP
from tests.test_telegram_frontend import run_javascript


def _channels(*viewers):
    return [SimpleNamespace(name=f"c{i}", viewers=v) for i, v in enumerate(viewers)]


@pytest.mark.parametrize("prefer_small,expected", [
    (False, ["c1", "c0", "c3", "c2"]),
    (True, ["c3", "c0", "c1", "c2"]),
])
def test_viewer_order_follows_the_setting_and_keeps_offline_last(prefer_small, expected):
    twitch = SimpleNamespace(settings=SimpleNamespace(prefer_small_streamers=prefer_small))
    ordered = ChannelService(twitch).sort_by_viewers(_channels(50, 900, None, 5))
    assert [channel.name for channel in ordered] == expected


def test_default_is_off_and_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    with patch("src.config.settings.SETTINGS_PATH", path):
        settings = Settings()
        assert settings.prefer_small_streamers is False
        settings.prefer_small_streamers = True
        settings.save()
        assert Settings().prefer_small_streamers is True


@pytest.mark.parametrize("value", ["true", 1, 0, None.__class__])
def test_api_rejects_non_boolean(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SettingsUpdate(prefer_small_streamers=value)


def _manager():
    stored = SimpleNamespace(**copy.deepcopy(default_settings), save=MagicMock())
    changed = MagicMock()
    return SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, MagicMock(), changed), stored, changed


def test_manager_rejects_non_boolean_before_any_change():
    manager, stored, changed = _manager()
    with pytest.raises(ValueError):
        manager.update_settings({"prefer_small_streamers": "yes", "dark_mode": True})
    assert stored.dark_mode is default_settings["dark_mode"]
    stored.save.assert_not_called()


@pytest.mark.asyncio
async def test_change_refreshes_selection_and_bumps_the_opt_in_revision():
    manager, stored, changed = _manager()
    before = getattr(stored, "mining_opt_ins_revision", 0)
    result = manager.update_settings({"prefer_small_streamers": True})
    assert result["prefer_small_streamers"] is True
    assert result["mining_opt_ins_revision"] == before + 1
    changed.assert_called_once()


FRONTEND = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
FUNCTIONS = ["onPreferSmallChange", "saveBooleanSetting", "updateSettingsUI", "toggleMiningHoursRange"]
SMALL_SETUP = SETUP + r"""
state.translations.gui.settings.prefer_small_streamers_save_error = '<b>Small save failed</b>';
const small = document.getElementById('prefer-small-streamers');
const smallError = document.getElementById('prefer-small-error');
"""


@FRONTEND
def test_checkbox_saves_only_its_field_and_reverts_on_failure():
    run_javascript(FUNCTIONS, SMALL_SETUP + r"""
(async () => {
    updateSettingsUI({});
    assert.equal(small.checked, false, 'off unless the server says true');
    const optIns = {allow_unlinked_campaigns: false, prefer_followed_channels: false};
    updateSettingsUI({...optIns, prefer_small_streamers: false, mining_opt_ins_revision: 0}, true);
    small.checked = true;
    global.fetch = async (url, options) => {
        assert.deepEqual(JSON.parse(options.body), {prefer_small_streamers: true});
        return {ok: true, json: async () => ({success: true,
            settings: {...optIns, prefer_small_streamers: true, mining_opt_ins_revision: 1}})};
    };
    await onPreferSmallChange();
    assert.equal(smallError.hidden, true, 'saved without an error');
    assert.equal(state.settings.prefer_small_streamers, true, 'confirmed in state');
    assert.equal(small.checked, true, 'confirmed in the checkbox');
    small.checked = false;
    global.fetch = async () => ({ok: false, json: async () => ({})});
    await onPreferSmallChange();
    assert.equal(small.checked, true, 'a failed save restores the confirmed value');
    assert.equal(smallError.hidden, false);
    assert.equal(smallError.textContent, '<b>Small save failed</b>', 'rendered as text');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


@FRONTEND
def test_markup_and_translations_exist():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="prefer-small-streamers"' in html and 'aria-describedby="prefer-small-help"' in html
    for path in (root / "lang").glob("*.json"):
        settings = json.loads(path.read_text(encoding="utf-8"))["gui"]["settings"]
        for key in ("prefer_small_streamers", "prefer_small_streamers_help",
                    "prefer_small_streamers_save_error"):
            assert settings[key].strip(), (path.name, key)
