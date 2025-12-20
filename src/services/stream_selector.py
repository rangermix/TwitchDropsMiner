from datetime import datetime, timedelta, timezone

from src.models.campaign import DropsCampaign
from src.models.game import Game
from src.config.settings import Settings


class StreamSelector:

    def _get_wanted_game_tree(self, settings: Settings, campaigns: list[DropsCampaign]) -> list[dict]:
        """
        Get the hierarchical tree of wanted items (Games -> Campaigns -> Drops -> Benefits).
        Ignoring 'can earn within' time constraint.
        """
        wanted_games = []
        games_to_watch = settings.games_to_watch
        mining_benefits = settings.mining_benefits
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)

        for game_name in games_to_watch:
            wanted_campaigns = []
            game_obj = None
            game_name_lower = game_name.lower()

            # Find all campaigns for this game
            for campaign in campaigns:
                if campaign.game.name.lower() != game_name_lower:
                    continue

                if game_obj is None:
                    game_obj = campaign.game

                if not campaign.can_earn_within(next_hour):
                    continue

                wanted_drops = []
                for drop in campaign.drops:
                    if drop.is_claimed:
                        continue

                    filtered_benefits = drop.get_wanted_unclaimed_benefits(mining_benefits)

                    if filtered_benefits:
                        wanted_drops.append({
                            "name": drop.name,
                            "benefits": filtered_benefits
                        })

                if wanted_drops:
                    wanted_campaigns.append({
                        "id": campaign.id,
                        "name": campaign.name,
                        "url": campaign.campaign_url,
                        "drops": wanted_drops
                    })

            if wanted_campaigns:
                wanted_games.append({
                    "game_id": game_obj.id if game_obj else None,
                    "game_name": game_name,
                    "game_icon": game_obj.box_art_url if game_obj else None,
                    "game_obj": game_obj,
                    "campaigns": wanted_campaigns
                })

        return wanted_games

    def get_wanted_game_tree(self, settings: Settings, campaigns: list[DropsCampaign]) -> list[dict]:
        return [{**game, "game_obj": None} for game in self._get_wanted_game_tree(settings, campaigns)]

    def get_wanted_games(self, settings: Settings, campaigns: list[DropsCampaign]) -> list[Game]:
        return [game["game_obj"] for game in self._get_wanted_game_tree(settings, campaigns)]
