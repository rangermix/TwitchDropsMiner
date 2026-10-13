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
        with patch("src.config.settings.json_load", return_value=copy.deepcopy(default_settings)):
            self.twitch = Twitch(Settings())
        self.twitch.gui = MagicMock()
        self.twitch.gui.login.stop_avatar = AsyncMock()
        self.twitch.websocket = MagicMock(start=AsyncMock(), stop=AsyncMock())
        self.twitch.get_auth = AsyncMock(return_value=SimpleNamespace(user_id=42))
        self.twitch._ensure_api_clients = MagicMock()
        self.twitch.fetch_inventory = AsyncMock()
        self.open = True
        self.twitch.is_mining_time = MagicMock(side_effect=lambda: self.open)

    async def asyncTearDown(self):
        await self.twitch._stop_mining_schedule()
        for task in (self.twitch._run_task, self.twitch._watching_task):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_closing_boundary_drains_active_watch_and_reopening_refreshes(self):
        entered, drained = asyncio.Event(), asyncio.Event()
        async def watch():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        self.twitch._watch_service.watch_loop = watch
        self.twitch._schedule_task = asyncio.create_task(self.twitch._mining_schedule_loop())
        await asyncio.wait_for(entered.wait(), 1)
        self.twitch.watching_channel.set(MagicMock())
        self.twitch._state = State.CHANNEL_SWITCH
        self.open = False
        self.twitch._schedule_changed.set()
        await asyncio.wait_for(drained.wait(), 1)
        for _ in range(5):
            await asyncio.sleep(0)
        if self.twitch._schedule_task.done():
            self.twitch._schedule_task.result()
        self.assertFalse(self.twitch.watching_channel.has_value())
        self.assertIsNone(self.twitch._watching_task)
        self.assertEqual(self.twitch._state, State.IDLE)
        self.assertFalse(self.twitch._inventory_refresh_pending)
        entered.clear()
        self.open = True
        self.twitch.request_games_update()
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(self.twitch._inventory_refresh_pending)

    async def test_paused_policy_and_cache_requests_stay_deferred_without_spinning(self):
        self.open = False
        self.twitch._inventory_loaded = True
        evaluations = 0
        def clock():
            nonlocal evaluations
            evaluations += 1
            if evaluations > 100:
                raise AssertionError("Paused state loop spun without yielding")
            return False
        self.twitch.is_mining_time.side_effect = clock
        self.twitch._run_task = asyncio.create_task(self.twitch._run())
        for _ in range(5):
            await asyncio.sleep(0)
        self.twitch.request_games_update()
        self.twitch.request_inventory_refresh(clear_cache=True)
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertFalse(self.twitch._run_task.done())
        self.assertLess(evaluations, 100)
        self.assertTrue(self.twitch._games_update_pending)
        self.assertTrue(self.twitch._clear_cache_pending)
        self.assertTrue(self.twitch._inventory_refresh_pending)
        self.twitch.fetch_inventory.assert_not_called()
        self.assertEqual(self.twitch._state, State.IDLE)
        self.twitch.close()
        await asyncio.wait_for(self.twitch._run_task, 1)

    async def test_policy_edit_recomputes_deadline_and_caps_clock_resampling(self):
        delays = []
        async def wait_for(awaitable, timeout):
            awaitable.close()
            delays.append(timeout)
            if len(delays) == 1:
                self.twitch.settings.mining_hours = {"mode": "range", "start": "10:00", "end": "10:01"}
            else:
                self.twitch._state = State.EXIT
        self.twitch._watch_service.watch_loop = AsyncMock()
        with (
            patch("src.core.client.asyncio.wait_for", side_effect=wait_for),
            patch("src.core.client.window_status", return_value=(True, 2.5)),
        ):
            await self.twitch._mining_schedule_loop()
        self.assertEqual(delays, [60.0, 2.5])

    async def test_authentication_replacement_drains_schedule_before_install(self):
        entered, drained = asyncio.Event(), asyncio.Event()
        async def schedule():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        self.twitch._schedule_task = asyncio.create_task(schedule())
        await entered.wait()
        async with self.twitch.authentication_change():
            self.assertTrue(drained.is_set())
            self.assertIsNone(self.twitch._schedule_task)
        self.assertFalse(self.twitch._changing_auth)

    async def test_inflight_inventory_completion_after_close_does_not_start_watch(self):
        fetching, finish, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def fetch():
            fetching.set()
            await finish.wait()
        self.twitch.fetch_inventory = fetch
        self.twitch._run_task = asyncio.create_task(self.twitch._run())
        await asyncio.wait_for(fetching.wait(), 1)
        self.open = False
        self.twitch.gui.status.update.side_effect = lambda value: closed.set() if value == "Outside mining hours" else None
        self.twitch._schedule_changed.set()
        await asyncio.wait_for(closed.wait(), 1)
        finish.set()
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertEqual(self.twitch._state, State.IDLE)
        self.assertFalse(self.twitch.watching_channel.has_value())
        self.twitch.gui.channels.set_watching.assert_not_called()

    async def test_stopping_schedule_during_watch_cleanup_drains_both_tasks(self):
        entered, cleaning, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def watch():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
        self.twitch._watch_service.watch_loop = watch
        self.twitch._schedule_task = asyncio.create_task(self.twitch._mining_schedule_loop())
        await entered.wait()
        watcher = self.twitch._watching_task
        self.open = False
        self.twitch._schedule_changed.set()
        await cleaning.wait()
        await self.twitch._stop_mining_schedule()
        self.assertTrue(watcher.done())
        self.assertTrue(cleaned.is_set())
        self.assertIsNone(self.twitch._schedule_task)

    async def test_watch_guard_rejects_manual_selection_outside_window(self):
        self.open = False
        channel = MagicMock()
        self.twitch.watch(channel)
        self.assertFalse(self.twitch.can_watch(channel))
        self.assertFalse(self.twitch.watching_channel.has_value())


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

    def test_invalid_api_and_saved_schedules_are_rejected(self):
        invalid = [
            {}, {"mode": "range"},
            {"mode": "range", "start": "24:00", "end": "12:00"},
            {"mode": "range", "start": "12:99", "end": "12:00"},
            {"mode": "range", "start": "", "end": "12:00"},
            {"mode": "range", "start": 1200, "end": "12:00"},
            {"mode": "bogus", "start": "12:00", "end": "12:00"},
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SettingsUpdate(mining_hours=value)
                persisted = copy.deepcopy(default_settings)
                persisted["mining_hours"] = value
                with patch("src.config.settings.json_load", return_value=persisted), self.assertRaises(ValueError):
                    Settings()

    async def test_failed_save_keeps_previous_live_hours_and_does_not_wake(self):
        settings = SimpleNamespace(**copy.deepcopy(default_settings))
        settings.save = MagicMock(side_effect=OSError("disk full"))
        callback = MagicMock()
        broadcaster = AsyncMock()
        manager = SettingsManager(broadcaster, settings, MagicMock(), on_change=callback)
        with self.assertRaises(OSError):
            manager.update_settings({"mining_hours": {"mode": "range", "start": "10:00", "end": "18:00"}})
        self.assertEqual(settings.mining_hours, default_settings["mining_hours"])
        callback.assert_not_called()
        broadcaster.emit.assert_not_called()

    async def test_invalid_schedule_does_not_mutate_other_settings(self):
        settings = SimpleNamespace(**copy.deepcopy(default_settings), save=MagicMock())
        manager = SettingsManager(AsyncMock(), settings, MagicMock())
        with self.assertRaises(ValueError):
            manager.update_settings({"dark_mode": True, "mining_hours": {"mode": "range"}})
        self.assertFalse(settings.dark_mode)
        settings.save.assert_not_called()

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

    async def test_settings_manager_logs_mining_hours_cleanly(self):
        mock_broadcaster = AsyncMock()
        mock_settings = SimpleNamespace(**copy.deepcopy(default_settings))
        mock_settings.save = MagicMock()
        console = MagicMock()
        manager = SettingsManager(mock_broadcaster, mock_settings, console)

        manager.update_settings(
            {"mining_hours": {"mode": "range", "start": "11:01", "end": "22:00"}}
        )
        console.print.assert_called_with("Setting changed: mining_hours = 11:01-22:00")

        manager.update_settings(
            {"mining_hours": {"mode": "always", "start": "11:01", "end": "22:00"}}
        )
        console.print.assert_called_with("Setting changed: mining_hours = always")


if __name__ == "__main__":
    unittest.main()
