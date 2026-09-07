import asyncio
import copy
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import State
from src.config.settings import Settings, default_settings
from src.core.client import Twitch
from src.utils.schedule import parse_minutes, window_status
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager


class TestWindowStatus(unittest.TestCase):
    def test_same_day_window(self):
        # 08:00 -> 22:00
        self.assertEqual(
            window_status("08:00", "22:00", datetime(2026, 9, 7, 9, 0)),
            (True, 13 * 3600),
        )
        self.assertEqual(window_status("08:00", "22:00", datetime(2026, 9, 7, 7, 59)), (False, 60))
        self.assertEqual(window_status("08:00", "22:00", datetime(2026, 9, 7, 22, 0)), (False, 10 * 3600))

    def test_overnight_window_wraps_midnight(self):
        # 22:00 -> 06:00
        self.assertEqual(window_status("22:00", "06:00", datetime(2026, 9, 7, 5, 30)), (True, 1800))
        self.assertEqual(window_status("22:00", "06:00", datetime(2026, 9, 7, 23, 0)), (True, 7 * 3600))
        self.assertEqual(window_status("22:00", "06:00", datetime(2026, 9, 7, 12, 0)), (False, 10 * 3600))
        self.assertEqual(window_status("22:00", "06:00", datetime(2026, 9, 7, 6, 0)), (False, 16 * 3600))

    def test_exclusive_end_edge(self):
        self.assertEqual(window_status("08:00", "22:00", datetime(2026, 9, 7, 22, 0, 0)), (False, 10 * 3600))

    def test_zero_length_range_is_always_active(self):
        active, seconds = window_status("22:00", "22:00", datetime(2026, 9, 7, 12, 0))
        self.assertTrue(active)
        self.assertEqual(seconds, 3600)

    def test_malformed_values_fall_back_to_midnight(self):
        # both unparseable -> start == end == 00:00 -> always active
        active, seconds = window_status("", None, datetime(2026, 9, 7, 12, 0))
        self.assertTrue(active)
        self.assertEqual(seconds, 3600)

    def test_parse_minutes(self):
        self.assertEqual(parse_minutes("08:30"), 510)
        self.assertEqual(parse_minutes("23:59"), 1439)
        self.assertEqual(parse_minutes("00:00"), 0)
        self.assertEqual(parse_minutes("25:00"), 1439)  # clamped into the day
        self.assertEqual(parse_minutes("garbage", default=42), 42)


class TestIsMiningTime(unittest.TestCase):
    def setUp(self) -> None:
        persisted = copy.deepcopy(default_settings)
        with (
            patch("src.config.settings.json_load", return_value=persisted),
            patch("src.config.settings.json_save"),
        ):
            settings = Settings()
        self.twitch = Twitch(settings)

    def test_always_mode_ignores_window(self):
        self.twitch.settings.mining_hours = {"mode": "always", "start": "08:00", "end": "22:00"}
        with patch("src.core.client.window_status", return_value=(False, 100.0)) as mock_window:
            self.assertTrue(self.twitch.is_mining_time())
        mock_window.assert_not_called()

    def test_range_mode_delegates_to_window_status(self):
        self.twitch.settings.mining_hours = {"mode": "range", "start": "08:00", "end": "22:00"}
        with patch("src.core.client.window_status", return_value=(True, 100.0)) as mock_window:
            self.assertTrue(self.twitch.is_mining_time())
        mock_window.assert_called_once()
        with patch("src.core.client.window_status", return_value=(False, 100.0)):
            self.assertFalse(self.twitch.is_mining_time())

    def test_missing_or_foreign_setting_is_safe(self):
        self.twitch.settings.mining_hours = {"mode": "unknown"}
        self.assertTrue(self.twitch.is_mining_time())
        self.twitch.settings.mining_hours = None
        self.assertTrue(self.twitch.is_mining_time())


class TestMiningScheduleLoop(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        persisted = copy.deepcopy(default_settings)
        with (
            patch("src.config.settings.json_load", return_value=persisted),
            patch("src.config.settings.json_save"),
        ):
            settings = Settings()
        self.twitch = Twitch(settings)
        self.twitch._state = State.IDLE

    async def test_non_range_mode_polls_without_refreshing(self):
        self.twitch.settings.mining_hours = {"mode": "always", "start": "08:00", "end": "22:00"}

        async def exit_on_sleep(_seconds):
            self.twitch._state = State.EXIT

        with (
            patch("src.core.client.asyncio.sleep", side_effect=exit_on_sleep),
            patch.object(self.twitch, "request_inventory_refresh") as mock_refresh,
        ):
            await self.twitch._mining_schedule_loop()
        mock_refresh.assert_not_called()

    async def test_range_loop_requests_refresh_when_window_opens(self):
        self.twitch.settings.mining_hours = {"mode": "range", "start": "08:00", "end": "22:00"}

        def window_open():
            self.twitch._state = State.EXIT
            return True

        with (
            patch("src.core.client.window_status", return_value=(True, 0.01)),
            patch.object(self.twitch, "is_mining_time", side_effect=window_open),
            patch.object(self.twitch, "request_inventory_refresh") as mock_refresh,
        ):
            await self.twitch._mining_schedule_loop()
        mock_refresh.assert_called_once()

    async def test_range_loop_skips_refresh_when_boundary_was_closing_edge(self):
        self.twitch.settings.mining_hours = {"mode": "range", "start": "08:00", "end": "22:00"}

        def window_closed():
            self.twitch._state = State.EXIT
            return False

        with (
            patch("src.core.client.window_status", return_value=(True, 0.01)),
            patch.object(self.twitch, "is_mining_time", side_effect=window_closed),
            patch.object(self.twitch, "request_inventory_refresh") as mock_refresh,
        ):
            await self.twitch._mining_schedule_loop()
        mock_refresh.assert_not_called()


class TestMiningHoursSettings(unittest.IsolatedAsyncioTestCase):
    def test_old_settings_merge_adds_mining_hours_default(self):
        from src.utils import merge_json

        old_settings = copy.deepcopy(default_settings)
        old_settings.pop("mining_hours")
        merge_json(old_settings, default_settings)
        self.assertEqual(old_settings["mining_hours"], default_settings["mining_hours"])

    def test_settings_save_persists_mining_hours(self):
        with (
            patch("src.config.settings.json_load", return_value=copy.deepcopy(default_settings)),
            patch("src.config.settings.json_save") as json_save,
        ):
            settings = Settings()
            settings.mining_hours = {"mode": "range", "start": "10:00", "end": "18:00"}
            settings.save()
        saved_settings = json_save.call_args.args[1]
        self.assertEqual(
            saved_settings["mining_hours"], {"mode": "range", "start": "10:00", "end": "18:00"}
        )

    async def test_settings_update_model_accepts_mining_hours(self):
        model = SettingsUpdate(mining_hours={"mode": "range", "start": "10:00", "end": "18:00"})
        self.assertEqual(model.mining_hours["mode"], "range")
        self.assertEqual(model.mining_hours["start"], "10:00")

    async def test_settings_manager_persists_and_triggers_policy_refresh(self):
        mock_broadcaster = AsyncMock()
        mock_settings = SimpleNamespace(**copy.deepcopy(default_settings))
        mock_settings.save = MagicMock()
        callback = MagicMock()
        manager = SettingsManager(mock_broadcaster, mock_settings, MagicMock(), on_change=callback)

        manager.update_settings(
            {"mining_hours": {"mode": "range", "start": "10:00", "end": "18:00"}}
        )
        await asyncio.sleep(0)

        self.assertEqual(
            mock_settings.mining_hours, {"mode": "range", "start": "10:00", "end": "18:00"}
        )
        callback.assert_called_once()
        mock_settings.save.assert_called_once()

    async def test_settings_manager_skips_unchanged_mining_hours(self):
        mock_broadcaster = AsyncMock()
        mock_settings = SimpleNamespace(**copy.deepcopy(default_settings))
        mock_settings.save = MagicMock()
        callback = MagicMock()
        manager = SettingsManager(mock_broadcaster, mock_settings, MagicMock(), on_change=callback)

        manager.update_settings({"mining_hours": default_settings["mining_hours"]})
        await asyncio.sleep(0)

        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
