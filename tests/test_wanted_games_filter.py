import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.models.campaign import DropsCampaign
from src.models.game import Game
from src.services.stream_selector import StreamSelector


class TestWantedGamesFilter(unittest.TestCase):
    def setUp(self):
        # Mock Settings
        self.settings = MagicMock()
        self.settings.games_to_watch = ["Game1", "Game2"]
        self.settings.mining_benefits = {
            "BADGE": True,
            "DIRECT_ENTITLEMENT": True,
        }  # both allowed by default

    def test_filter_wanted_campaigns(self):
        # Setup Campaigns

        # Campaign 1: Game1, Can Earn, Has Wanted Benefits -> Should be selected
        c1 = MagicMock(spec=DropsCampaign)
        c1.game = Game({"id": 1, "name": "Game1"})
        c1.can_earn_within.return_value = True
        c1.eligible = True
        c1.id = "123"
        c1.name = "Test Campaign"
        c1.campaign_url = "http://test.url"
        d1 = MagicMock()
        d1.name = "Test Drop"
        d1.is_claimed = False
        d1.ends_at = datetime.max.replace(tzinfo=timezone.utc)
        d1.get_wanted_unclaimed_benefits.return_value = ["Benefit1"]
        c1.drops = [d1]
        c1.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c1, DropsCampaign)
        )

        # Campaign 2: Game2, Can Earn, NO Wanted Benefits -> still mineable (DevilXD parity)
        c2 = MagicMock(spec=DropsCampaign)
        c2.game = Game({"id": 2, "name": "Game2"})
        c2.can_earn_within.return_value = True
        c2.eligible = True
        d2 = MagicMock()
        d2.is_claimed = False
        d2.ends_at = datetime.max.replace(tzinfo=timezone.utc)
        d2.get_wanted_unclaimed_benefits.return_value = []
        c2.drops = [d2]
        c2.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c2, DropsCampaign)
        )

        # Campaign 3: Game3 (Not in games_to_watch), Can Earn, Has Benefits -> Should NOT be selected
        c3 = MagicMock(spec=DropsCampaign)
        c3.game = Game({"id": 3, "name": "Game3"})
        c3.can_earn_within.return_value = True
        c3.eligible = True
        d3 = MagicMock()
        d3.is_claimed = False
        d3.ends_at = datetime.max.replace(tzinfo=timezone.utc)
        d3.get_wanted_unclaimed_benefits.return_value = ["Benefit3"]
        c3.drops = [d3]
        c3.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c3, DropsCampaign)
        )

        # Campaign 4: Game1 duplicate, claimed benefits only -> Game1 already selected via c1
        c4 = MagicMock(spec=DropsCampaign)
        c4.game = Game({"id": 1, "name": "Game1"})
        c4.can_earn_within.return_value = True
        c4.eligible = True
        c4.id = "123"
        c4.name = "Test Campaign"
        c4.campaign_url = "http://test.url"
        d4 = MagicMock()
        d4.name = "Test Drop"
        d4.is_claimed = True
        d4.ends_at = datetime.max.replace(tzinfo=timezone.utc)
        d4.get_wanted_unclaimed_benefits.return_value = ["Benefit4"]
        c4.drops = [d4]
        c4.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c4, DropsCampaign)
        )

        # Campaign 5: Game1, Can Not Earn -> ignored when selecting Game1 (c1 wins)
        c5 = MagicMock(spec=DropsCampaign)
        c5.game = Game({"id": 1, "name": "Game1"})
        c5.can_earn_within.return_value = False
        c5.eligible = False
        c5.id = "123"
        c5.name = "Test Campaign"
        c5.campaign_url = "http://test.url"
        d5 = MagicMock()
        d5.name = "Test Drop"
        d5.is_claimed = False
        d5.ends_at = datetime.max.replace(tzinfo=timezone.utc)
        d5.get_wanted_unclaimed_benefits.return_value = ["Benefit5"]
        c5.drops = [d5]
        c5.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c5, DropsCampaign)
        )

        inventory = [c1, c2, c3, c4, c5]
        stream_selector = StreamSelector()
        wanted_games = stream_selector.get_wanted_games(self.settings, inventory)

        self.assertEqual(len(wanted_games), 2)
        self.assertEqual(wanted_games[0].name, "Game1")
        self.assertEqual(wanted_games[1].name, "Game2")

    def test_priority_skips_non_earnable_then_selects_next(self):
        """CoD not earnable + SoT earnable → wanted_games is [Sea of Thieves] only."""
        self.settings.games_to_watch = [
            "Call of Duty: Modern Warfare 4",
            "Sea of Thieves",
            "ROBLOX",
        ]

        cod = MagicMock(spec=DropsCampaign)
        cod.game = Game({"id": 10, "name": "Call of Duty: Modern Warfare 4"})
        cod.can_earn_within.return_value = False
        cod.eligible = False
        cod.drops = []

        sot = MagicMock(spec=DropsCampaign)
        sot.game = Game({"id": 20, "name": "Sea of Thieves"})
        sot.can_earn_within.return_value = True
        sot.eligible = True
        sot.drops = []

        roblox = MagicMock(spec=DropsCampaign)
        roblox.game = Game({"id": 30, "name": "ROBLOX"})
        roblox.can_earn_within.return_value = False
        roblox.eligible = True
        roblox.drops = []

        stream_selector = StreamSelector()
        wanted_games = stream_selector.get_wanted_games(
            self.settings, [cod, sot, roblox]
        )

        self.assertEqual([g.name for g in wanted_games], ["Sea of Thieves"])

        reasons = stream_selector.explain_skipped_games(
            self.settings, [cod, sot, roblox]
        )
        self.assertTrue(any("Call of Duty" in r for r in reasons))
        self.assertTrue(any("ROBLOX" in r for r in reasons))
        self.assertFalse(any("Sea of Thieves" in r for r in reasons))

    def test_not_linked_priority_game_is_still_wanted(self):
        """Games to Watch entries mine even when Twitch reports NOT LINKED."""
        self.settings.games_to_watch = ["Sea of Thieves"]

        sot = MagicMock(spec=DropsCampaign)
        sot.game = Game({"id": 20, "name": "Sea of Thieves"})
        sot.linked = False
        sot.eligible = False
        sot.can_earn_within.side_effect = (
            lambda stamp, ignore_link=False: ignore_link
        )
        sot.drops = []

        stream_selector = StreamSelector()
        # Use real can_earn_within behavior via a lightweight stub
        class StubCampaign:
            def __init__(self):
                self.game = Game({"id": 20, "name": "Sea of Thieves"})
                self.linked = False
                self.eligible = False
                self._valid = True
                self.active = True
                from datetime import datetime, timedelta, timezone
                now = datetime.now(timezone.utc)
                self.starts_at = now - timedelta(hours=1)
                self.ends_at = now + timedelta(days=1)
                drop = MagicMock()
                drop._can_earn_within.return_value = True
                self.drops = [drop]

            def can_earn_within(self, stamp, *, ignore_link=False):
                return DropsCampaign.can_earn_within(self, stamp, ignore_link=ignore_link)

        wanted = stream_selector.get_wanted_games(self.settings, [StubCampaign()])
        self.assertEqual([g.name for g in wanted], ["Sea of Thieves"])


if __name__ == "__main__":
    unittest.main()
