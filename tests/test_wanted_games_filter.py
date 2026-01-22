import unittest
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
        c1.id = "123"
        c1.name = "Test Campaign"
        c1.campaign_url = "http://test.url"
        d1 = MagicMock()
        d1.name = "Test Drop"
        d1.is_claimed = False
        d1.get_wanted_unclaimed_benefits.return_value = ["Benefit1"]
        c1.drops = [d1]
        c1.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c1, DropsCampaign)
        )

        # Campaign 2: Game2, Can Earn, NO Wanted Benefits -> Should NOT be selected
        c2 = MagicMock(spec=DropsCampaign)
        c2.game = Game({"id": 2, "name": "Game2"})
        c2.can_earn_within.return_value = True
        d2 = MagicMock()
        d2.is_claimed = False
        d2.get_wanted_unclaimed_benefits.return_value = []
        c2.drops = [d2]
        c2.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c2, DropsCampaign)
        )

        # Campaign 3: Game3 (Not in games_to_watch), Can Earn, Has Benefits -> Should NOT be selected
        c3 = MagicMock(spec=DropsCampaign)
        c3.game = Game({"id": 3, "name": "Game3"})
        c3.can_earn_within.return_value = True
        d3 = MagicMock()
        d3.is_claimed = False
        d3.get_wanted_unclaimed_benefits.return_value = ["Benefit3"]
        c3.drops = [d3]
        c3.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c3, DropsCampaign)
        )

        # Campaign 4: Game1, Can Earn, Has Claimed Wanted Benefits -> Should NOT be selected
        c4 = MagicMock(spec=DropsCampaign)
        c4.game = Game({"id": 1, "name": "Game1"})
        c4.can_earn_within.return_value = True
        c4.id = "123"
        c4.name = "Test Campaign"
        c4.campaign_url = "http://test.url"
        d4 = MagicMock()
        d4.name = "Test Drop"
        d4.is_claimed = True
        d4.get_wanted_unclaimed_benefits.return_value = ["Benefit4"]
        c4.drops = [d4]
        c4.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c4, DropsCampaign)
        )

        # Campaign 5: Game1, Can Not Earn, Has Wanted Benefits -> Should NOT be selected
        c5 = MagicMock(spec=DropsCampaign)
        c5.game = Game({"id": 1, "name": "Game1"})
        c5.can_earn_within.return_value = False
        c5.id = "123"
        c5.name = "Test Campaign"
        c5.campaign_url = "http://test.url"
        d5 = MagicMock()
        d5.name = "Test Drop"
        d5.is_claimed = False
        d5.get_wanted_unclaimed_benefits.return_value = ["Benefit5"]
        c5.drops = [d5]
        c5.has_wanted_unclaimed_benefits.side_effect = (
            DropsCampaign.has_wanted_unclaimed_benefits.__get__(c5, DropsCampaign)
        )

        inventory = [c1, c2, c3, c4, c5]
        stream_selector = StreamSelector()
        wanted_games = stream_selector.get_wanted_games(self.settings, inventory)

        self.assertEqual(len(wanted_games), 1)
        self.assertEqual(wanted_games[0].name, "Game1")


if __name__ == "__main__":
    unittest.main()
