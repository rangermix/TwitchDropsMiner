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
        self.settings.mining_benefits = {"BADGE": True, "DIRECT_ENTITLEMENT": True} # both allowed by default


    def test_filter_wanted_campaigns(self):
        # Setup Campaigns

        # Campaign 1: Game1, Can Earn, Has Wanted Benefits -> Should be selected
        c1 = MagicMock(spec=DropsCampaign)
        c1.game = Game({"id": 1, "name": "Game1"})
        c1.can_earn_within.return_value = True
        c1.has_wanted_unclaimed_benefits.return_value = True

        # Campaign 2: Game2, Can Earn, NO Wanted Benefits -> Should NOT be selected
        c2 = MagicMock(spec=DropsCampaign)
        c2.game = Game({"id": 2, "name": "Game2"})
        c2.can_earn_within.return_value = True
        c2.has_wanted_unclaimed_benefits.return_value = False

        # Campaign 3: Game3 (Not in games_to_watch), Can Earn, Has Benefits -> Should NOT be selected
        c3 = MagicMock(spec=DropsCampaign)
        c3.game = Game({"id": 3, "name": "Game3"})
        c3.can_earn_within.return_value = True
        c3.has_wanted_unclaimed_benefits.return_value = True

        inventory = [c1, c2, c3]
        stream_selector = StreamSelector()
        wanted_games = stream_selector.get_wanted_games(self.settings, inventory)

        self.assertEqual(len(wanted_games), 1)
        self.assertEqual(wanted_games[0].name, "Game1")

        # Verify calls
        c1.has_wanted_unclaimed_benefits.assert_called_with(self.settings.mining_benefits)
        c2.has_wanted_unclaimed_benefits.assert_called_with(self.settings.mining_benefits)

if __name__ == '__main__':
    unittest.main()
