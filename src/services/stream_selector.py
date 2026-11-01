from datetime import datetime, timedelta, timezone

from src.config.reward_priority import RewardPriority
from src.config.settings import Settings
from src.models.campaign import DropsCampaign
from src.models.game import Game
from src.services.game_order import TemporaryGameOrder


class StreamSelector:
    def _get_wanted_drops(
        self, campaign: DropsCampaign, mining_benefits: dict[str, bool], now: datetime,
        policy: RewardPriority | None = None
    ) -> list[dict]:
        wanted_drops = []
        drops = list(campaign.drops)
        if policy is not None and policy.enabled:
            drops.sort(key=policy.eligible_drop_key)
        for drop in drops:
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
        return wanted_drops

    def _append_unlisted_games(
        self,
        wanted_games: list[dict],
        settings: Settings,
        campaigns: list[DropsCampaign],
        now: datetime,
        stamp: datetime,
    ) -> None:
        if getattr(settings, "priority_list_only", True) is not False:
            return

        selected_names = {game_name.lower() for game_name in settings.games_to_watch}
        queued_ids = {
            game["game_obj"].id
            for game in wanted_games
            if game.get("game_obj") is not None
        }

        unlisted_campaigns: dict[int, dict] = {}
        for campaign in campaigns:
            campaign_game = campaign.game
            if campaign_game.name.lower() in selected_names or campaign_game.id in queued_ids:
                continue
            if not campaign.can_earn_within(stamp):
                continue
            if not self._get_wanted_drops(campaign, settings.mining_benefits, now, RewardPriority(settings)):
                continue
            if campaign_game.id not in unlisted_campaigns:
                unlisted_campaigns[campaign_game.id] = {
                    "game_id": campaign_game.id,
                    "game_name": campaign_game.name,
                    "game_icon": campaign_game.box_art_url,
                    "game_obj": campaign_game,
                    "campaigns": [],
                }
            unlisted_campaigns[campaign_game.id]["campaigns"].append(campaign)

        fallback_games = sorted(
            unlisted_campaigns.values(),
            key=lambda game: (
                min(campaign.ends_at for campaign in game["campaigns"]),
                game["game_name"].lower(),
            ),
        )
        for fallback_game in fallback_games:
            fallback_game["campaigns"] = [
                {
                    "id": campaign.id,
                    "name": campaign.name,
                    "url": campaign.campaign_url,
                    "drops": self._get_wanted_drops(campaign, settings.mining_benefits, now, RewardPriority(settings)),
                }
                for campaign in sorted(fallback_game["campaigns"], key=lambda campaign: campaign.ends_at)
            ]
            wanted_games.append(fallback_game)

    def _get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign],
        order: TemporaryGameOrder | None = None,
    ) -> list[dict]:
        """
        Get the hierarchical tree of wanted items (Games -> Campaigns -> Drops -> Benefits).
        Include campaigns available within an hour, with selected games first. A
        temporary dashboard order, when set, takes precedence over every other rule.
        """
        wanted_games: list[dict] = []
        queued_ids: set[int] = set()
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

                if not campaign.can_earn_within(next_hour):
                    continue

                wanted_drops = self._get_wanted_drops(campaign, mining_benefits, now, RewardPriority(settings))

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
                if game_obj is not None:
                    if game_obj.id in queued_ids:
                        continue
                    queued_ids.add(game_obj.id)
                wanted_games.append(
                    {
                        "game_id": game_obj.id if game_obj else None,
                        "game_name": game_name,
                        "game_icon": game_obj.box_art_url if game_obj else None,
                        "game_obj": game_obj,
                        "campaigns": wanted_campaigns,
                    }
                )

        self._append_unlisted_games(wanted_games, settings, campaigns, now, next_hour)
        policy = RewardPriority(settings)
        if policy.enabled:
            selected = {name.lower() for name in settings.games_to_watch}
            def key(game):
                eligible = [campaign for campaign in campaigns
                            if campaign.game == game['game_obj'] and campaign.can_earn_within(next_hour)]
                return (0 if game['game_name'].lower() in selected else 1,
                        min((policy.campaign_key(campaign) for campaign in eligible),
                            default=(len(policy.TYPES), float('inf'))))
            wanted_games.sort(key=key)
            by_id = {campaign.id: campaign for campaign in campaigns}
            for game in wanted_games:
                game["campaigns"].sort(key=lambda entry: policy.campaign_key(by_id[entry["id"]]))
        if isinstance(order, TemporaryGameOrder) and order.active:
            # Stable: games outside the temporary order keep their default sequence.
            wanted_games.sort(key=lambda game: order.rank(game["game_name"]))
        return wanted_games

    def get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign],
        order: TemporaryGameOrder | None = None,
    ) -> list[dict]:
        return [
            {**game, "game_obj": None}
            for game in self._get_wanted_game_tree(settings, campaigns, order)
        ]

    def get_wanted_games(
        self, settings: Settings, campaigns: list[DropsCampaign],
        order: TemporaryGameOrder | None = None,
    ) -> list[Game]:
        return [game["game_obj"] for game in self._get_wanted_game_tree(settings, campaigns, order)]
