import asyncio
import copy
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from src.config import State
from src.config.settings import Settings, default_settings
from src.core.client import Twitch
from src.i18n import _
from src.web import app as web_app


def make_twitch() -> Twitch:
    with patch("src.config.settings.json_load", return_value=copy.deepcopy(default_settings)):
        twitch = Twitch(Settings())
    twitch.gui = MagicMock()
    return twitch


class TestPauseGate(unittest.TestCase):
    def test_pause_overrides_always_and_open_window(self):
        twitch = make_twitch()
        for hours in ({"mode": "always"}, {"mode": "range", "start": "00:00", "end": "23:59"}):
            twitch.settings.mining_hours = hours
            twitch._paused = True
            self.assertFalse(twitch.is_mining_time())
            twitch._paused = False
            with patch("src.core.client.window_status", return_value=(True, 60.0)):
                self.assertTrue(twitch.is_mining_time())

    def test_set_paused_is_idempotent_and_broadcasts_changes_only(self):
        twitch = make_twitch()
        twitch.set_paused(True)
        twitch.set_paused(True)
        twitch.set_paused(False)
        self.assertEqual(
            [c.args[0]["paused"] for c in twitch.gui.broadcast_paused.call_args_list], [True, False]
        )
        self.assertEqual(twitch.pause_info(), {
            "paused": False, "resume_at": None, "revision": 2, "instance_id": twitch._pause_instance_id,
        })
        self.assertEqual(
            [c.args[0]["revision"] for c in twitch.gui.broadcast_paused.call_args_list], [1, 2]
        )

    @staticmethod
    def frozen_clock(now: datetime):
        """Freeze both clocks; naive now() is the local wall-clock time."""
        mock = MagicMock(wraps=datetime)
        clock = patch.multiple("src.core.client", datetime=mock, monotonic=lambda: 1000.0)
        clock.start()
        mock.now.side_effect = lambda tz=None: (
            now.astimezone().replace(tzinfo=None) if tz is None else now.astimezone(tz)
        )
        return clock

    def test_durations_set_resume_time(self):
        twitch = make_twitch()
        clock = self.frozen_clock(datetime(2026, 10, 18, 23, 55).astimezone())
        try:
            for duration, expected in (
                ("10m", datetime(2026, 10, 19, 0, 5)),
                ("1h", datetime(2026, 10, 19, 0, 55)),
                ("tomorrow", datetime(2026, 10, 19, 0, 0)),
                (None, None),
            ):
                twitch.set_paused(True, duration)
                resume_at = twitch._resume_at
                local = resume_at.astimezone().replace(tzinfo=None) if resume_at else None
                self.assertEqual(local, expected, duration)
                self.assertTrue(twitch.pause_info()["paused"])
        finally:
            clock.stop()
        # Changing only the duration while paused is still a change.
        self.assertEqual(twitch.gui.broadcast_paused.call_count, 4)
        # Resuming always clears the timer, even if a duration is passed.
        twitch.set_paused(False, "1h")
        self.assertIsNone(twitch._resume_at)

    @unittest.skipUnless(hasattr(time, "tzset"), "needs a settable process timezone")
    def test_timed_pause_lasts_real_time_across_a_dst_change(self):
        """Madrid falls back at 03:00 CEST on 2026-10-25; one hour stays one hour."""
        previous = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Madrid"
        time.tzset()
        twitch = make_twitch()
        start = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Madrid"))  # CEST
        clock = self.frozen_clock(start)
        try:
            twitch.set_paused(True, "1h")
            self.assertEqual(twitch._resume_at - start, timedelta(hours=1))
            # 02:30 CEST + 1 h is 02:30 CET, which the status shows in local time.
            self.assertEqual(twitch._resume_at.astimezone().utcoffset(), timedelta(hours=1))
            twitch._paused = True
            self.assertEqual(
                twitch._closed_status(), _.t["gui"]["status"]["paused_until"].format(time="02:30")
            )
        finally:
            clock.stop()
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_pause_info_reports_resume_time_with_offset(self):
        twitch = make_twitch()
        twitch._paused = True
        twitch._resume_at = datetime(2026, 10, 19, 0, 0).astimezone(timezone.utc)
        resume_at = datetime.fromisoformat(twitch.pause_info()["resume_at"])
        self.assertIsNotNone(resume_at.tzinfo)
        self.assertEqual(resume_at, twitch._resume_at)

    def test_pause_instance_is_random_runtime_only_and_stable_across_updates(self):
        twitch = make_twitch()
        instance = twitch.pause_info()["instance_id"]
        self.assertRegex(instance, r"^[a-f0-9]{32}$")
        self.assertNotEqual(make_twitch().pause_info()["instance_id"], instance)
        self.assertNotIn("instance_id", vars(twitch.settings))
        self.assertNotIn("_pause_instance_id", vars(twitch.settings))
        twitch.set_paused(True, "10m")
        self.assertEqual(twitch.pause_info()["instance_id"], instance)
        self.assertEqual(twitch.gui.broadcast_paused.call_args.args[0]["instance_id"], instance)
        twitch.set_paused(False)
        self.assertEqual(twitch.pause_info()["instance_id"], instance)

    def test_closed_status_names_the_reason(self):
        twitch = make_twitch()
        self.assertEqual(twitch._closed_status(), _.t["gui"]["status"]["outside_hours"])
        twitch._paused = True
        self.assertEqual(twitch._closed_status(), _.t["gui"]["status"]["paused"])
        twitch._resume_at = datetime(2026, 10, 19, 7, 5).astimezone(timezone.utc)
        self.assertEqual(
            twitch._closed_status(), _.t["gui"]["status"]["paused_until"].format(time="07:05")
        )

    def test_console_pause_messages_use_translations(self):
        twitch = make_twitch()
        with (
            patch.dict(_.t["status"], {"mining_resumed": "Translated resumed"}),
            patch.dict(_.t["gui"]["status"], {"paused": "Translated paused", "paused_until": "Translated until {time}"}),
        ):
            twitch.set_paused(True)
            twitch.gui.print.assert_called_with("Translated paused", collapse_key=None)
            twitch.set_paused(True, "10m")
            self.assertTrue(twitch.gui.print.call_args.args[0].startswith("Translated until "))
            twitch.set_paused(False)
            twitch.gui.print.assert_called_with("Translated resumed", collapse_key=None)

    def test_duration_resume_time_tracks_remaining_elapsed_time_after_clock_step(self):
        twitch = make_twitch()
        wall = [datetime(2026, 10, 29, 12, tzinfo=timezone.utc)]
        with (
            patch("src.core.client.datetime", wraps=datetime) as clock,
            patch("src.core.client.monotonic", return_value=1000.0, create=True) as elapsed,
        ):
            clock.now.side_effect = lambda tz=None: wall[0].astimezone(tz) if tz else wall[0].astimezone().replace(tzinfo=None)
            twitch.set_paused(True, "1h")
            wall[0] += timedelta(hours=2)
            elapsed.return_value += 600
            resume_at = datetime.fromisoformat(twitch.pause_info()["resume_at"])
            self.assertEqual(resume_at, wall[0] + timedelta(minutes=50))

    def test_pause_revision_tracks_changes_not_clock_projection_reads(self):
        twitch = make_twitch()
        clock = self.frozen_clock(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
        try:
            twitch.set_paused(True, "1h")
            twitch.pause_info()
            twitch._closed_status()
            twitch.set_paused(True, "1h")
            self.assertEqual(twitch.pause_info()["revision"], 1)
            twitch.set_paused(True, "10m")
            self.assertEqual(twitch.pause_info()["revision"], 2)
            self.assertEqual(twitch.gui.broadcast_paused.call_count, 2)
            self.assertEqual(make_twitch().pause_info()["revision"], 0)
        finally:
            clock.stop()

    def test_new_duration_request_resets_elapsed_deadline_after_clock_correction(self):
        twitch = make_twitch()
        clock = self.frozen_clock(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
        try:
            twitch.set_paused(True, "10m")
            with patch("src.core.client.monotonic", return_value=1100.0):
                twitch.set_paused(True, "10m")
                self.assertEqual(twitch._pause_seconds_remaining(), 600.0)
            self.assertEqual(twitch.pause_info()["revision"], 2)
        finally:
            clock.stop()


class TestPauseSchedule(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await self.twitch._stop_mining_schedule()
        if self.twitch._watching_task is not None:
            self.twitch._watching_task.cancel()
            await asyncio.gather(self.twitch._watching_task, return_exceptions=True)

    async def test_pause_drains_watch_and_resume_restarts_it_with_refresh(self):
        self.twitch = twitch = make_twitch()
        entered, drained = asyncio.Event(), asyncio.Event()

        async def watch():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()

        twitch._watch_service.watch_loop = watch
        twitch._schedule_task = asyncio.create_task(twitch._mining_schedule_loop())
        await asyncio.wait_for(entered.wait(), 1)
        twitch.watching_channel.set(MagicMock())
        twitch._state = State.CHANNEL_SWITCH

        twitch.set_paused(True)
        await asyncio.wait_for(drained.wait(), 1)
        for _i in range(5):
            await asyncio.sleep(0)
        self.assertFalse(twitch.watching_channel.has_value())
        self.assertIsNone(twitch._watching_task)
        self.assertEqual(twitch._state, State.IDLE)
        twitch.gui.status.update.assert_called_with(_.t["gui"]["status"]["paused"])
        # Refreshes requested while paused stay queued instead of running.
        twitch.request_inventory_refresh()
        twitch._activate_pending_inventory_refresh()
        self.assertEqual(twitch._state, State.IDLE)

        entered.clear()
        twitch.set_paused(False)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(twitch._inventory_refresh_pending)
        twitch._activate_pending_inventory_refresh()
        self.assertEqual(twitch._state, State.INVENTORY_FETCH)

    async def test_timed_pause_resumes_by_itself(self):
        self.twitch = twitch = make_twitch()
        entered = asyncio.Event()

        async def watch():
            entered.set()
            await asyncio.Event().wait()

        twitch._watch_service.watch_loop = watch
        twitch._paused, twitch._resume_at = True, datetime.now(timezone.utc)
        twitch._schedule_task = asyncio.create_task(twitch._mining_schedule_loop())
        await asyncio.wait_for(entered.wait(), 1)
        expected = {"paused": False, "resume_at": None, "revision": 1, "instance_id": twitch._pause_instance_id}
        self.assertEqual(twitch.pause_info(), expected)
        twitch.gui.broadcast_paused.assert_called_once_with(expected)

    async def test_schedule_wakes_for_a_pause_ending_before_the_next_sample(self):
        self.twitch = twitch = make_twitch()
        twitch._watch_service.watch_loop = AsyncMock()
        twitch.set_paused(True, "10m")
        waits: list[float] = []

        async def fake_wait_for(awaitable, timeout):
            awaitable.close()
            waits.append(timeout)
            twitch._state = State.EXIT
            raise TimeoutError

        twitch._resume_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        twitch._pause_deadline = time.monotonic() + 5
        with patch("src.core.client.asyncio.wait_for", fake_wait_for):
            await twitch._mining_schedule_loop()
        self.assertLess(waits[0], 6)

    async def test_fixed_pauses_follow_elapsed_time_instead_of_wall_clock_steps(self):
        for duration, seconds in (("10m", 600), ("1h", 3600)):
            for wall_step, elapsed_seconds, expected_paused in (
                (timedelta(hours=2), 0, True),
                (timedelta(hours=-2), seconds, False),
            ):
                with self.subTest(duration=duration, wall_step=wall_step):
                    self.twitch = twitch = make_twitch()
                    twitch._watch_service.watch_loop = AsyncMock()
                    wall = [datetime(2026, 10, 29, 12, tzinfo=timezone.utc)]

                    async def end_tick(awaitable, timeout, paused_client=twitch):
                        awaitable.close()
                        paused_client._state = State.EXIT
                        raise TimeoutError

                    with (
                        patch("src.core.client.datetime", wraps=datetime) as clock,
                        patch("src.core.client.monotonic", return_value=1000.0, create=True) as elapsed,
                        patch("src.core.client.asyncio.wait_for", end_tick),
                    ):
                        clock.now.side_effect = lambda tz=None, wall=wall: wall[0].astimezone(tz) if tz else wall[0].astimezone().replace(tzinfo=None)
                        twitch.set_paused(True, duration)
                        wall[0] += wall_step
                        elapsed.return_value += elapsed_seconds
                        try:
                            await twitch._mining_schedule_loop()
                            self.assertEqual(twitch.pause_info()["paused"], expected_paused)
                        finally:
                            if twitch._watching_task is not None:
                                twitch._watching_task.cancel()
                                await asyncio.gather(twitch._watching_task, return_exceptions=True)

    async def test_expired_pause_outside_mining_hours_keeps_watching_stopped(self):
        self.twitch = twitch = make_twitch()
        twitch.settings.mining_hours = {"mode": "range", "start": "08:00", "end": "09:00"}
        twitch._watch_service.watch_loop = AsyncMock()

        async def end_tick(awaitable, timeout):
            awaitable.close()
            twitch._state = State.EXIT
            raise TimeoutError

        with (
            patch("src.core.client.monotonic", return_value=1000.0, create=True) as elapsed,
            patch("src.core.client.window_status", return_value=(False, 600.0)),
            patch("src.core.client.asyncio.wait_for", end_tick),
        ):
            twitch.set_paused(True, "10m")
            twitch.request_inventory_refresh()
            elapsed.return_value += 600
            await twitch._mining_schedule_loop()
            self.assertFalse(twitch.pause_info()["paused"])
            self.assertTrue(twitch._inventory_refresh_pending)
            self.assertIsNone(twitch._watching_task)
            twitch._watch_service.watch_loop.assert_not_called()
            twitch.gui.status.update.assert_called_with(_.t["gui"]["status"]["outside_hours"])


class TestPauseApi(unittest.IsolatedAsyncioTestCase):
    def test_request_requires_a_real_boolean_and_known_duration(self):
        for bad in ({"paused": "true"}, {"paused": 1}, {"paused": None},
                    {"paused": True, "duration": "5m"}, {"paused": True, "duration": 10}):
            with self.assertRaises(ValidationError):
                web_app.PauseRequest(**bad)
        self.assertIsNone(web_app.PauseRequest(paused=True).duration)

    async def test_endpoint_applies_and_reports_state(self):
        twitch = make_twitch()
        clock = TestPauseGate.frozen_clock(datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
        try:
            with patch.object(web_app, "twitch_client", twitch):
                result = await web_app.update_paused(web_app.PauseRequest(paused=True, duration="1h"))
            self.assertTrue(result["success"] and result["paused"])
            self.assertEqual(result["resume_at"], twitch.pause_info()["resume_at"])
            self.assertEqual(result["instance_id"], twitch.pause_info()["instance_id"])
        finally:
            clock.stop()

    async def test_endpoint_unavailable_before_client_starts(self):
        with (
            patch.object(web_app, "twitch_client", None),
            self.assertRaises(web_app.HTTPException) as caught,
        ):
            await web_app.update_paused(web_app.PauseRequest(paused=True))
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
