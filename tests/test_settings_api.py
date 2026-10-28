import asyncio
import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from src.config.settings import Settings, default_settings
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager


web_app = importlib.import_module("src.web.app")


class TestSettingsAPI(unittest.IsolatedAsyncioTestCase):
    def test_settings_update_model(self):
        # Verify model accepts new fields
        update_data = {
            "inventory_filters": {"show_upcoming": True},
            "mining_benefits": {"BADGE": True},
            "drop_name_blacklist": ["Mask", "Badge"],
            "priority_list_only": False,
        }
        model = SettingsUpdate(**update_data)
        self.assertEqual(model.inventory_filters, update_data["inventory_filters"])
        self.assertEqual(model.mining_benefits, update_data["mining_benefits"])
        self.assertEqual(model.drop_name_blacklist, update_data["drop_name_blacklist"])
        self.assertIs(model.priority_list_only, False)

    def test_priority_list_defaults_migration_and_persistence_round_trip(self):
        self.assertIs(default_settings["priority_list_only"], True)
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            with patch("src.config.settings.SETTINGS_PATH", settings_path):
                self.assertIs(Settings().priority_list_only, True)
                settings_path.write_text(
                    json.dumps({"games_to_watch": ["Keep selected game"]}),
                    encoding="utf-8",
                )
                settings = Settings()
                self.assertIs(settings.priority_list_only, True)
                for strict in (False, True):
                    with self.subTest(priority_list_only=strict):
                        settings.priority_list_only = strict
                        settings.save()
                        reloaded = Settings()
                        self.assertIs(reloaded.priority_list_only, strict)
                        self.assertEqual(reloaded.games_to_watch, ["Keep selected game"])

    async def test_priority_toggle_persists_and_refreshes_once_in_both_directions(self):
        settings = SimpleNamespace(**copy.deepcopy(default_settings))
        settings.games_to_watch = ["Keep selected game"]
        settings.save = MagicMock()
        broadcaster = AsyncMock()
        callback = MagicMock()
        manager = SettingsManager(broadcaster, settings, MagicMock(), on_change=callback)

        for strict in (False, True):
            with self.subTest(priority_list_only=strict):
                callback.reset_mock()
                settings.save.reset_mock()
                broadcaster.emit.reset_mock()
                with patch.object(web_app, "gui_manager", SimpleNamespace(settings=manager)):
                    response = await web_app.update_settings(
                        SettingsUpdate(priority_list_only=strict)
                    )
                    current_settings = await web_app.get_settings()
                await asyncio.sleep(0)

                self.assertTrue(response["success"])
                self.assertIs(response["settings"]["priority_list_only"], strict)
                self.assertIs(current_settings["priority_list_only"], strict)
                self.assertIs(manager.get_settings()["priority_list_only"], strict)
                self.assertEqual(settings.games_to_watch, ["Keep selected game"])
                settings.save.assert_called_once_with()
                callback.assert_called_once_with()
                broadcaster.emit.assert_awaited_once_with(
                    "settings_updated", response["settings"]
                )

        callback.reset_mock()
        manager.update_settings({"priority_list_only": True})
        await asyncio.sleep(0)
        callback.assert_not_called()

    async def test_failed_priority_toggle_save_retains_previous_live_policy(self):
        settings = SimpleNamespace(**copy.deepcopy(default_settings))
        settings.save = MagicMock(side_effect=OSError("write rejected"))
        broadcaster = AsyncMock()
        console = MagicMock()
        callback = MagicMock()
        manager = SettingsManager(broadcaster, settings, console, on_change=callback)

        with (
            patch.object(web_app, "gui_manager", SimpleNamespace(settings=manager)),
            self.assertRaises(HTTPException) as raised,
        ):
            await web_app.update_settings(SettingsUpdate(priority_list_only=False))
        await asyncio.sleep(0)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "settings_save_failed")
        self.assertIs(settings.priority_list_only, True)
        self.assertIs(manager.get_settings()["priority_list_only"], True)
        settings.save.assert_called_once_with()
        callback.assert_not_called()
        broadcaster.emit.assert_not_awaited()
        console.print.assert_not_called()

    def test_settings_load_and_save_normalize_persisted_blacklist(self):
        persisted = copy.deepcopy(default_settings)
        persisted["drop_name_blacklist"] = [
            "  Gold Mask ",
            "gold mask",
            "",
            42,
            "Badge",
        ]

        with (
            patch("src.config.settings.json_load", return_value=persisted),
            patch("src.config.settings.json_save") as json_save,
        ):
            settings = Settings()
            self.assertEqual(settings.drop_name_blacklist, ["Gold Mask", "Badge"])

            settings.drop_name_blacklist.extend([" badge ", "Token"])
            settings.save()

        self.assertEqual(settings.drop_name_blacklist, ["Gold Mask", "Badge", "Token"])
        saved_settings = json_save.call_args.args[1]
        self.assertEqual(
            saved_settings["drop_name_blacklist"], ["Gold Mask", "Badge", "Token"]
        )

    async def test_settings_endpoint_returns_request_scoped_compatibility_response(self):
        expected = {
            "inventory_filters": {
                "show_only_not_linked": False,
                "show_not_linked": True,
            }
        }
        mock_gui = MagicMock()
        mock_gui.settings.update_settings.return_value = expected

        with patch.object(web_app, "gui_manager", mock_gui):
            response = await web_app.update_settings(
                SettingsUpdate(inventory_filters={"show_not_linked": True})
            )

        assert response == {"success": True, "settings": expected}
        mock_gui.settings.update_settings.assert_called_once_with(
            {"inventory_filters": {"show_not_linked": True}}
        )

    async def test_blacklist_endpoint_normalizes_and_triggers_one_policy_refresh(self):
        settings_values = copy.deepcopy(default_settings)
        settings = SimpleNamespace(**settings_values)
        settings.save = MagicMock()
        broadcaster = AsyncMock()
        callback = MagicMock()
        manager = SettingsManager(
            broadcaster,
            settings,
            MagicMock(),
            on_change=callback,
        )
        mock_gui = SimpleNamespace(settings=manager)

        with patch.object(web_app, "gui_manager", mock_gui):
            response = await web_app.update_settings(
                SettingsUpdate(
                    drop_name_blacklist=[
                        "  Gold Mask ",
                        "gold mask",
                        "",
                        "Badge",
                    ]
                )
            )
        await asyncio.sleep(0)

        self.assertTrue(response["success"])
        self.assertEqual(
            response["settings"]["drop_name_blacklist"], ["Gold Mask", "Badge"]
        )
        self.assertEqual(settings.drop_name_blacklist, ["Gold Mask", "Badge"])
        callback.assert_called_once_with()
        settings.save.assert_called_once_with()
        broadcaster.emit.assert_awaited_once()

        callback.reset_mock()
        manager.update_settings(
            {"drop_name_blacklist": [" Gold Mask ", "Badge", "gold mask"]}
        )
        await asyncio.sleep(0)
        callback.assert_not_called()

    async def test_legacy_filter_request_is_echoed_without_changing_current_semantics(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.inventory_filters = {
            "game_name_search": ["Game A"],
            "show_active": False,
            "show_benefit_badge": True,
            "show_benefit_emote": True,
            "show_benefit_item": True,
            "show_benefit_other": True,
            "show_expired": False,
            "show_finished": False,
            "show_not_linked": True,
            "show_upcoming": True,
        }
        manager = SettingsManager(mock_broadcaster, mock_settings, MagicMock())

        response_settings = manager.update_settings(
            {"inventory_filters": {"show_active": True, "show_not_linked": True}}
        )
        await asyncio.sleep(0)

        filters = mock_settings.inventory_filters
        self.assertTrue(filters["show_active"])
        self.assertEqual(filters["game_name_search"], ["Game A"])
        self.assertEqual(filters["link_status"], "all")
        self.assertNotIn("show_not_linked", filters)
        self.assertTrue(response_settings["inventory_filters"]["show_not_linked"])
        self.assertEqual(response_settings["inventory_filters"]["link_status"], "all")
        self.assertNotIn("show_not_linked", manager.get_settings()["inventory_filters"])
        mock_broadcaster.emit.assert_awaited_once_with(
            "settings_updated", response_settings
        )
        mock_settings.save.assert_called_once()

    async def test_persisted_legacy_filter_is_removed_without_becoming_only_not_linked(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.inventory_filters = {
            "show_not_linked": True,
        }
        manager = SettingsManager(mock_broadcaster, mock_settings, MagicMock())

        response_settings = manager.update_settings(
            {"inventory_filters": {"show_active": True}}
        )
        await asyncio.sleep(0)

        filters = mock_settings.inventory_filters
        self.assertTrue(filters["show_active"])
        self.assertEqual(filters["link_status"], "all")
        self.assertNotIn("show_not_linked", filters)
        self.assertNotIn("show_not_linked", response_settings["inventory_filters"])

    async def test_settings_manager_networking(self):
        # Mock dependencies
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        # Initialize mock attributes with default values for comparison
        mock_settings.inventory_filters = {}
        mock_settings.mining_benefits = {}
        mock_settings.games_to_watch = []

        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # 1. Update Inventory Filters (does NOT trigger callback per implementation)
        inv_filters = {"show_upcoming": False}
        manager.update_settings({"inventory_filters": inv_filters})
        mock_callback.assert_not_called()  # inventory_filters has should_trigger_update=False
        self.assertFalse(mock_settings.inventory_filters["show_upcoming"])
        self.assertEqual(
            set(mock_settings.inventory_filters),
            set(default_settings["inventory_filters"]),
        )
        self.assertIn("Setting changed: inventory_filters", mock_console.print.call_args.args[0])

        # 2. Update Mining Benefits (SHOULD trigger callback)
        benefits = {"BADGE": False}
        manager.update_settings({"mining_benefits": benefits})
        mock_callback.assert_called_once()
        self.assertEqual(mock_settings.mining_benefits, benefits)
        mock_console.print.assert_called_with("Setting changed: mining_benefits = {'BADGE': False}")
        mock_callback.reset_mock()

        # 3. Update Games to Watch (SHOULD trigger callback)
        games = ["Game 1"]
        manager.update_settings({"games_to_watch": games})
        mock_callback.assert_called_once()


    async def test_rejected_update_writes_prints_and_saves_nothing(self):
        valid_fields = {
            "games_to_watch": ["Changed game"],
            "reward_priority": ["BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"],
            "drop_name_blacklist": ["Changed"],
            "dark_mode": True,
            "language": "Deutsch",
            "connection_quality": 6,
            "proxy": "http://127.0.0.1:3128",
            "telegram_bot_token": "changed-token",
            "telegram_chat_id": "42",
            "inventory_list_view": True,
            "mining_benefits": {"BADGE": False},
            "allow_unlinked_campaigns": True,
            "priority_list_only": False,
        }
        invalid_fields = (
            ("reward_priority", ["BADGE"]),
            ("reward_priority", ["BADGE", "BADGE", "DIRECT_ENTITLEMENT", "UNKNOWN"]),
            ("inventory_filters", ["not", "an", "object"]),
            ("inventory_filters", {"link_status": "invalid"}),
            ("mining_hours", {"mode": "range", "start": "25:00", "end": "08:00"}),
            ("discord_webhook_url", "https://example.com/not-discord"),
            ("language", "Klingon"),
        )
        for key, value in invalid_fields:
            with self.subTest(invalid=key, value=value):
                settings = SimpleNamespace(**copy.deepcopy(default_settings))
                settings.save = MagicMock()
                before = copy.deepcopy({k: v for k, v in vars(settings).items() if k != "save"})
                broadcaster = AsyncMock()
                console = MagicMock()
                callback = MagicMock()
                manager = SettingsManager(broadcaster, settings, console, on_change=callback)

                with (
                    patch("src.web.managers.settings._.set_language") as set_language,
                    self.assertRaises(ValueError),
                ):
                    manager.update_settings({**valid_fields, key: value})
                await asyncio.sleep(0)

                after = {k: v for k, v in vars(settings).items() if k != "save"}
                self.assertEqual(after, before)
                console.print.assert_not_called()
                settings.save.assert_not_called()
                set_language.assert_not_called()
                callback.assert_not_called()
                broadcaster.emit.assert_not_called()

    async def test_failed_save_restores_every_field_and_defers_effects(self):
        settings = SimpleNamespace(**copy.deepcopy(default_settings))
        settings.save = MagicMock(side_effect=OSError("disk full"))
        before = copy.deepcopy({k: v for k, v in vars(settings).items() if k != "save"})
        broadcaster = AsyncMock()
        console = MagicMock()
        callback = MagicMock()
        manager = SettingsManager(broadcaster, settings, console, on_change=callback)
        language = manager.get_languages()["current"]
        other_language = next(
            name for name in manager.get_languages()["available"] if name != language
        )

        with self.assertRaises(OSError):
            manager.update_settings({
                "games_to_watch": ["Changed game"],
                "reward_priority": ["BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"],
                "drop_name_blacklist": ["Changed"],
                "dark_mode": True,
                "language": other_language,
                "connection_quality": 6,
                "proxy": "http://127.0.0.1:3128",
                "minimum_refresh_interval_minutes": 45,
                "telegram_bot_token": "changed-token",
                "telegram_chat_id": "42",
                "inventory_filters": {"link_status": "linked"},
                "inventory_list_view": True,
                "mining_benefits": {"BADGE": False},
                "mining_hours": {"mode": "range", "start": "08:00", "end": "17:00"},
                "discord_webhook_url": "https://discord.com/api/webhooks/1/token",
                "allow_unlinked_campaigns": True,
                "priority_list_only": False,
            })
        await asyncio.sleep(0)

        settings.save.assert_called_once()
        after = {k: v for k, v in vars(settings).items() if k != "save"}
        self.assertEqual(after, before)
        self.assertEqual(manager.get_languages()["current"], language)
        console.print.assert_not_called()
        callback.assert_not_called()
        broadcaster.emit.assert_not_called()

        # A later unrelated save must persist none of the rejected changes.
        settings.save = MagicMock()
        manager.update_settings({"dark_mode": False})
        self.assertEqual(
            {k: v for k, v in vars(settings).items() if k != "save"}, before
        )

        # Deferred effects still run once a save succeeds.
        with patch("src.web.managers.settings._.set_language") as set_language:
            manager.update_settings({"language": other_language})
        await asyncio.sleep(0)
        set_language.assert_called_once_with(other_language)
        broadcaster.emit.assert_any_call("language_changed", {"language": other_language})
        console.print.assert_called_once_with(f"Setting changed: language = {other_language}")


if __name__ == "__main__":
    unittest.main()
