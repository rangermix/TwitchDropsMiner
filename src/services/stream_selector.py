from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.models.campaign import DropsCampaign
from src.models.game import Game


class StreamSelector:
    def _get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[dict]:
        """
        Get the hierarchical tree of wanted items (Games -> Campaigns -> Drops -> Benefits).
        Used for Wanted Drops Queue display; applies mining_benefits filtering.
        """
        wanted_games = []
        games_to_watch = settings.games_to_watch
        mining_benefits = settings.mining_benefits
        now = datetime.now(timezone.utc)
        next_hour = now + timedelta(hours=1)

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

                if not campaign.can_earn_within(next_hour, ignore_link=True):
                    continue

                wanted_drops = []
                for drop in campaign.drops:
                    if (
                        not drop.is_watch_drop
                        or drop.is_claimed
                        or drop.ends_at <= now
                        or not drop.is_mineable
                    ):
                        continue

                    filtered_benefits = drop.get_wanted_unclaimed_benefits(mining_benefits)

                    if len(filtered_benefits) > 0:
                        wanted_drops.append({"name": drop.name, "benefits": filtered_benefits})

                if len(wanted_drops) > 0:
                    wanted_campaigns.append(
                        {
                            "id": campaign.id,
                            "name": campaign.name,
                            "url": campaign.campaign_url,
                            "drops": wanted_drops,
                        }
                    )

            if len(wanted_campaigns) > 0:
                wanted_games.append(
                    {
                        "game_id": game_obj.id if game_obj else None,
                        "game_name": game_name,
                        "game_icon": game_obj.box_art_url if game_obj else None,
                        "game_obj": game_obj,
                        "campaigns": wanted_campaigns,
                    }
                )

        return wanted_games

    def get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[dict]:
        return [
            {**game, "game_obj": None} for game in self._get_wanted_game_tree(settings, campaigns)
        ]

    def get_wanted_games(self, settings: Settings, campaigns: list[DropsCampaign]) -> list[Game]:
        """
        Build mining eligibility list in games_to_watch order (DevilXD-aligned).

        A game is wanted if any inventory campaign for that name can progress
        within the next hour. Games on the priority list are mined even when
        Twitch reports the campaign as NOT LINKED. Benefit filters do not block
        mining eligibility.
        """
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
        wanted_games: list[Game] = []
        seen: set[str] = set()

        for game_name in settings.games_to_watch:
            game_name_lower = game_name.lower()
            if game_name_lower in seen:
                continue

            for campaign in campaigns:
                if campaign.game.name.lower() != game_name_lower:
                    continue
                if not campaign.can_earn_within(next_hour, ignore_link=True):
                    continue
                wanted_games.append(campaign.game)
                seen.add(game_name_lower)
                break

        return wanted_games

    def explain_skipped_games(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[str]:
        """
        Short reasons why games_to_watch entries are not in wanted_games.
        Used for status/logging when the miner goes idle.
        """
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
        wanted_names = {game.name.lower() for game in self.get_wanted_games(settings, campaigns)}
        reasons: list[str] = []

        for game_name in settings.games_to_watch:
            if game_name.lower() in wanted_names:
                continue

            matching = [c for c in campaigns if c.game.name.lower() == game_name.lower()]
            if not matching:
                reasons.append(f"{game_name}: no campaign in inventory")
            elif not any(c.can_earn_within(next_hour, ignore_link=True) for c in matching):
                reasons.append(f"{game_name}: no earnable drops within 1h")
            else:
                reasons.append(f"{game_name}: skipped")

        return reasons
