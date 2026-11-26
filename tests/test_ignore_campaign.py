"""Inventory "Ignore campaign": stop mining one campaign without touching its game."""

import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.config.settings import IgnoredCampaigns, Settings, default_settings
from src.models.campaign import DropsCampaign
from src.web.managers.settings import SettingsManager
from tests.javascript_helpers import NODE
from tests.test_telegram_frontend import run_javascript


web_app = importlib.import_module("src.web.app")


def _twitch(*ignored):
    return SimpleNamespace(
        settings=SimpleNamespace(
            allow_unlinked_campaigns=False, drop_name_blacklist=[], ignored_campaigns=list(ignored),
        ),
        gui=MagicMock(), request_games_update=MagicMock(),
    )


def _drop(drop_id, *, minutes=0, instance=None):
    return {
        "id": drop_id, "name": drop_id,
        "benefitEdges": [{"benefit": {
            "id": f"{drop_id}-b", "name": f"Reward {drop_id}",
            "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": "https://example.test/r.png",
        }}],
        "startAt": "2000-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z",
        "preconditionDrops": [], "requiredMinutesWatched": 60,
        "self": {"dropInstanceID": instance, "isClaimed": False, "currentMinutesWatched": minutes},
    }


def _campaign(twitch, *drops):
    return DropsCampaign(twitch, {
        "id": "camp", "name": "Campaign", "game": {"id": "1", "name": "Game"},
        "self": {"isAccountConnected": True}, "accountLinkURL": "https://example.test/link",
        "startAt": "2000-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z", "status": "ACTIVE",
        "allow": {"channels": [], "isEnabled": True}, "timeBasedDrops": list(drops),
    }, {})


def test_ignored_campaign_is_not_mined_but_keeps_claimable_rewards():
    twitch = _twitch("camp")
    campaign = _campaign(twitch, _drop("open", minutes=10), _drop("done", minutes=60, instance="i1"))
    open_drop, done = campaign.timed_drops["open"], campaign.timed_drops["done"]
    assert campaign.ignored and campaign.mining_finished
    assert not campaign.can_earn() and not campaign.can_earn_within(campaign.ends_at)
    assert open_drop.ignore_reason.kind == "campaign" and not open_drop.can_earn()
    assert done.can_claim, "an already earned reward is still claimed"
    twitch.settings.ignored_campaigns = []
    assert not campaign.ignored and open_drop.can_earn(), "the cached policy follows the setting"


def test_other_campaigns_of_the_same_game_are_unaffected():
    campaign = _campaign(_twitch("another-campaign"), _drop("open", minutes=10))
    assert not campaign.ignored and campaign.can_earn()


@pytest.mark.parametrize("value", [None, "camp", ["ok", 5], ["bad id"], ["x" * 101],
                                   ["a"] * (IgnoredCampaigns.MAX_ENTRIES + 1)])
def test_invalid_ignored_campaigns_are_rejected(value):
    with pytest.raises(ValueError):
        IgnoredCampaigns.normalize(value)


def test_saved_list_is_sanitized_on_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ignored_campaigns": ["a-1", 5, "a-1", "bad id", "b_2"]}),
                    encoding="utf-8")
    with patch("src.config.settings.SETTINGS_PATH", path):
        assert Settings().ignored_campaigns == ["a-1", "b_2"]
        assert default_settings["ignored_campaigns"] == []


def _manager(*ignored):
    stored = SimpleNamespace(**copy.deepcopy(default_settings), save=MagicMock())
    stored.ignored_campaigns = list(ignored)
    changed = MagicMock()
    return SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, MagicMock(), changed), stored, changed


@pytest.mark.asyncio
async def test_set_campaign_ignored_adds_and_removes_one_id():
    manager, stored, changed = _manager("other")
    assert manager.set_campaign_ignored("camp", True)["ignored_campaigns"] == ["other", "camp"]
    assert manager.set_campaign_ignored("camp", True)["ignored_campaigns"] == ["other", "camp"]
    assert manager.set_campaign_ignored("camp", False)["ignored_campaigns"] == ["other"]
    assert changed.call_count == 2, "each real change refreshes mining selection"


@pytest.mark.asyncio
async def test_a_full_list_drops_the_oldest_ids():
    full = [f"old{i}" for i in range(IgnoredCampaigns.MAX_ENTRIES)]
    manager, stored, _ = _manager(*full)
    result = manager.set_campaign_ignored("new", True)["ignored_campaigns"]
    assert len(result) == IgnoredCampaigns.MAX_ENTRIES
    assert result[0] == "old1" and result[-1] == "new"


def test_set_campaign_ignored_rejects_an_invalid_id_before_saving():
    manager, stored, _ = _manager()
    with pytest.raises(ValueError):
        manager.set_campaign_ignored("../x", True)
    stored.save.assert_not_called()


@pytest.mark.asyncio
async def test_endpoint_reports_fixed_errors():
    request = web_app.IgnoreCampaignRequest(campaign_id="camp", ignored=True)
    manager = SimpleNamespace(settings=SimpleNamespace(set_campaign_ignored=MagicMock(
        side_effect=[{"ignored_campaigns": ["camp"]}, ValueError("x"), OSError("disk")])))
    with patch.object(web_app, "gui_manager", manager):
        assert await web_app.ignore_campaign(request) == {"success": True, "ignored_campaigns": ["camp"]}
        for detail in ("invalid_campaign", "settings_save_failed"):
            with pytest.raises(HTTPException) as error:
                await web_app.ignore_campaign(request)
            assert error.value.detail == detail


def test_request_model_is_strict():
    from pydantic import ValidationError

    for payload in ({"campaign_id": 5, "ignored": True}, {"campaign_id": "c", "ignored": "yes"}):
        with pytest.raises(ValidationError):
            web_app.IgnoreCampaignRequest(**payload)


FRONTEND = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
SETUP = r"""
const assert = require('node:assert/strict');
const state = {campaigns: {camp: {id: 'camp', ignored: false}},
               translations: {gui: {inventory: {ignore_campaign_error: '<b>failed</b>'}}}};
const toasts = [];
let renders = 0;
function showToast(message, type) { toasts.push([message, type]); }
function renderInventory() { renders++; }
const document = {activeElement: null, getElementById() { return null; }};
const button = {disabled: false, setAttribute() {}};
"""


@FRONTEND
def test_toggle_sends_one_request_and_updates_the_campaign():
    run_javascript(["toggleCampaignIgnored"], SETUP + r"""
(async () => {
    const bodies = [];
    let finish;
    global.fetch = (url, options) => {
        assert.equal(url, '/api/campaigns/ignore');
        bodies.push(JSON.parse(options.body));
        return new Promise(resolve => { finish = resolve; });
    };
    const pending = toggleCampaignIgnored('camp', button);
    assert.equal(button.disabled, true);
    await toggleCampaignIgnored('camp', button);  // ignored while the first is pending
    finish({ok: true, json: async () => ({success: true, ignored_campaigns: ['camp']})});
    await pending;
    assert.deepEqual(bodies, [{campaign_id: 'camp', ignored: true}]);
    assert.equal(state.campaigns.camp.ignored, true);
    assert.deepEqual(toasts, []);
    global.fetch = async () => ({ok: true, json: async () => ({success: true, ignored_campaigns: ['camp']})});
    await toggleCampaignIgnored('camp', button);  // server did not remove it
    assert.equal(state.campaigns.camp.ignored, true);
    assert.deepEqual(toasts, [['<b>failed</b>', 'error']]);
    assert.ok(renders >= 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_translations_exist():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "lang").glob("*.json"):
        inventory = json.loads(path.read_text(encoding="utf-8"))["gui"]["inventory"]
        for key in ("ignored_campaign_reason", "ignore_campaign", "unignore_campaign",
                    "ignore_campaign_error"):
            assert inventory[key].strip(), (path.name, key)
