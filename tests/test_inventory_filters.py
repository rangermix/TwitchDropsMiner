import asyncio
import json
import re
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import default_settings
from src.utils import merge_json
from src.web.managers.inventory import InventoryManager
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "web" / "index.html"


def test_inventory_filter_defaults_hide_finished_without_restricting_link_state():
    filters = default_settings["inventory_filters"]

    assert filters["show_finished"] is False
    assert filters["show_only_not_linked"] is False
    assert "show_not_linked" not in filters

    html = INDEX_HTML.read_text(encoding="utf-8")
    input_tag = re.search(r'<input\b[^>]*\bid="filter-not-linked"[^>]*>', html)
    assert input_tag is not None
    assert re.search(r"\bchecked\b", input_tag.group(0)) is None


def test_legacy_not_linked_setting_migrates_to_neutral_restriction():
    legacy_filters = {
        "show_not_linked": True,
    }

    merge_json(legacy_filters, default_settings["inventory_filters"])

    assert "show_not_linked" not in legacy_filters
    assert legacy_filters["show_only_not_linked"] is False


class TestInventoryDropUpdates(unittest.IsolatedAsyncioTestCase):
    async def test_final_claim_refreshes_campaign_counts_in_event_and_cache(self):
        broadcaster = MagicMock()
        broadcaster.emit = AsyncMock()
        manager = InventoryManager(broadcaster, MagicMock())
        manager._campaigns["campaign-1"] = {
            "claimed_drops": 0,
            "total_drops": 1,
            "drops": [
                {
                    "id": "drop-1",
                    "current_minutes": 29,
                    "required_minutes": 30,
                    "progress": 0.97,
                    "is_claimed": False,
                    "can_claim": False,
                }
            ],
        }
        campaign = SimpleNamespace(id="campaign-1", claimed_drops=1, total_drops=1)
        drop = SimpleNamespace(
            id="drop-1",
            campaign=campaign,
            current_minutes=30,
            required_minutes=30,
            progress=1.0,
            is_claimed=True,
            can_claim=False,
        )

        manager.update_drop(drop)
        await asyncio.sleep(0)

        campaign_data = manager._campaigns["campaign-1"]
        assert campaign_data["claimed_drops"] == 1
        assert campaign_data["total_drops"] == 1
        broadcaster.emit.assert_awaited_once_with(
            "drop_update",
            {
                "campaign_id": "campaign-1",
                "campaign": {"claimed_drops": 1, "total_drops": 1},
                "drop": campaign_data["drops"][0],
            },
        )


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_campaign_filter_behavior_matrix():
    function_source = extract_javascript_function(
        APP_JS.read_text(encoding="utf-8"), "campaignMatchesFilters"
    )
    base_filters = {
        "show_active": False,
        "show_only_not_linked": False,
        "show_upcoming": False,
        "show_expired": False,
        "show_finished": False,
        "game_name_search": [],
        "show_benefit_item": True,
        "show_benefit_badge": True,
        "show_benefit_emote": True,
        "show_benefit_other": True,
    }
    base_campaign = {
        "active": False,
        "upcoming": False,
        "expired": False,
        "linked": True,
        "game_name": "Game A",
        "total_drops": 2,
        "claimed_drops": 0,
        "drops": [{"benefits": [{"type": "DIRECT_ENTITLEMENT"}]}],
    }

    def case(
        *,
        expected: bool,
        filter_changes: dict[str, object] | None = None,
        campaign_changes: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "filters": base_filters | (filter_changes or {}),
            "campaign": base_campaign | (campaign_changes or {}),
            "expected": expected,
        }

    cases = [
        case(expected=True),
        case(expected=True, campaign_changes={"linked": False}),
        case(
            expected=False,
            campaign_changes={"active": True, "claimed_drops": 2},
            filter_changes={"show_active": True},
        ),
        case(
            expected=True,
            campaign_changes={"active": True, "claimed_drops": 2},
            filter_changes={"show_active": True, "show_finished": True},
        ),
        case(
            expected=False,
            campaign_changes={"active": True},
            filter_changes={"show_active": True, "show_only_not_linked": True},
        ),
        case(
            expected=True,
            campaign_changes={"active": True, "linked": False},
            filter_changes={"show_active": True, "show_only_not_linked": True},
        ),
        case(
            expected=False,
            campaign_changes={"expired": True, "linked": False},
            filter_changes={"show_active": True, "show_only_not_linked": True},
        ),
        case(
            expected=True,
            campaign_changes={"upcoming": True},
            filter_changes={"show_active": True, "show_upcoming": True},
        ),
        case(expected=False, filter_changes={"game_name_search": ["Game B"]}),
        case(
            expected=False,
            filter_changes={"show_benefit_item": False, "show_benefit_badge": True},
        ),
    ]

    script = f"""
{function_source}
const cases = {json.dumps(cases)};
const results = cases.map(testCase => ({{
    actual: campaignMatchesFilters(testCase.campaign, testCase.filters),
    expected: testCase.expected,
}}));
process.stdout.write(JSON.stringify(results));
    """
    completed = subprocess.run(
        [NODE, "-"],
        check=True,
        capture_output=True,
        input=script,
        text=True,
    )

    assert json.loads(completed.stdout) == [
        {"actual": test_case["expected"], "expected": test_case["expected"]}
        for test_case in cases
    ]
