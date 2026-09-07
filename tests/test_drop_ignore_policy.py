from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.models.campaign import DropsCampaign
from src.services.stream_selector import StreamSelector
from src.utils import DropIgnorePolicy


def _drop(
    drop_id: str,
    name: str,
    *,
    preconditions: tuple[str, ...] = (),
    has_benefit: bool = True,
    claimed: bool = False,
    required_minutes: int = 15,
    ends_at: str = "2099-01-01T00:00:00Z",
) -> dict:
    data = {
        "id": drop_id,
        "name": name,
        "benefitEdges": (
            [
                {
                    "benefit": {
                        "id": f"benefit-{drop_id}",
                        "name": f"Reward {name}",
                        "distributionType": "DIRECT_ENTITLEMENT",
                        "imageAssetURL": f"https://example.test/{drop_id}.png",
                    }
                }
            ]
            if has_benefit
            else []
        ),
        "startAt": "2026-01-01T00:00:00Z",
        "endAt": ends_at,
        "preconditionDrops": [{"id": drop_id} for drop_id in preconditions],
        "requiredMinutesWatched": required_minutes,
    }
    if claimed:
        data["self"] = {
            "dropInstanceID": f"claim-{drop_id}",
            "isClaimed": True,
            "currentMinutesWatched": required_minutes,
        }
    return data


def _campaign(
    drops: list[dict],
    *,
    blacklist: list[str] | None = None,
    magic_mock_settings: bool = False,
) -> DropsCampaign:
    twitch = MagicMock()
    if magic_mock_settings:
        twitch.settings = MagicMock()
    else:
        twitch.settings = SimpleNamespace(drop_name_blacklist=blacklist or [])
    return DropsCampaign(
        twitch,
        {
            "id": "campaign-1",
            "name": "Campaign 1",
            "game": {
                "id": "1",
                "name": "Test Game",
                "displayName": "Test Game",
                "boxArtURL": "https://example.test/game-{width}x{height}.jpg",
            },
            "self": {"isAccountConnected": True},
            "accountLinkURL": "https://example.test/link",
            "startAt": "2026-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
            "status": "ACTIVE",
            "allow": {"channels": [], "isEnabled": True},
            "timeBasedDrops": drops,
        },
        {},
    )


def test_policy_normalizes_keywords_and_returns_first_case_insensitive_match():
    keywords = DropIgnorePolicy.normalize_keywords(
        ["  Gold Mask  ", "gold mask", "", None, 42, "Badge", "BADGE"]
    )

    assert keywords == ["Gold Mask", "Badge"]
    policy = DropIgnorePolicy(keywords)
    assert policy.matching_keyword("A GOLD MASK and badge bundle") == "Gold Mask"
    assert policy.matching_keyword("Unrelated reward") is None
    assert DropIgnorePolicy.normalize_keywords("  Single keyword  ") == ["Single keyword"]
    assert DropIgnorePolicy.normalize_keywords({"not": "a sequence"}) == []


def test_policy_uses_unicode_casefold_for_deduplication_and_matching():
    keywords = DropIgnorePolicy.normalize_keywords(["Straße", "STRASSE"])

    assert keywords == ["Straße"]
    assert DropIgnorePolicy(keywords).matching_keyword("STRASSE reward") == "Straße"


def test_direct_ignore_propagates_through_unclaimed_preconditions():
    campaign = _campaign(
        [
            _drop("ignored", "Skip This Reward"),
            _drop("blocked", "Final Reward", preconditions=("ignored",)),
            _drop("descendant", "Last Reward", preconditions=("blocked",)),
            _drop("independent", "Independent Reward"),
        ],
        blacklist=["skip this"],
    )
    ignored = campaign.timed_drops["ignored"]
    blocked = campaign.timed_drops["blocked"]
    descendant = campaign.timed_drops["descendant"]
    independent = campaign.timed_drops["independent"]

    assert ignored.is_directly_ignored is True
    assert ignored.is_ignored is True
    assert ignored.ignore_reason is not None
    assert ignored.ignore_reason.kind == "keyword"
    assert ignored.ignore_reason.detail == "skip this"
    assert blocked.is_directly_ignored is False
    assert blocked.is_ignored is True
    assert blocked.ignore_reason is not None
    assert blocked.ignore_reason.kind == "precondition"
    assert blocked.ignore_reason.detail == "Skip This Reward"
    assert descendant.is_ignored is True
    assert descendant.ignore_reason is not None
    assert descendant.ignore_reason.kind == "precondition"
    assert descendant.ignore_reason.detail == "Final Reward"
    assert independent.is_ignored is False
    assert independent.is_mineable is True
    assert campaign.mineable_drop_ids == frozenset({"independent"})
    assert campaign.ignored_drops == 3
    assert campaign.skipped_drops == 0
    assert campaign.mining_finished is False


def test_claimed_keyword_match_no_longer_blocks_a_dependent_reward():
    campaign = _campaign(
        [
            _drop("claimed", "Skip This Reward", claimed=True),
            _drop("dependent", "Final Reward", preconditions=("claimed",)),
        ],
        blacklist=["skip"],
    )
    claimed = campaign.timed_drops["claimed"]
    dependent = campaign.timed_drops["dependent"]

    assert claimed.ignore_reason is None
    assert claimed.is_ignored is False
    assert claimed.is_mineable is False
    assert dependent.is_ignored is False
    assert dependent.is_mineable is True
    assert campaign.mineable_drop_ids == frozenset({"dependent"})


def test_unused_prerequisite_is_skipped_without_changing_actual_claim_counts():
    campaign = _campaign(
        [
            _drop("starter", "Starter", has_benefit=False),
            _drop("ignored", "Mask Reward", preconditions=("starter",)),
        ],
        blacklist=["mask"],
    )
    starter = campaign.timed_drops["starter"]
    ignored = campaign.timed_drops["ignored"]

    assert starter.is_ignored is False
    assert starter.is_mineable is False
    assert ignored.is_directly_ignored is True
    assert campaign.mineable_drop_ids == frozenset()
    assert campaign.ignored_drops == 1
    assert campaign.skipped_drops == 1
    assert campaign.mining_finished is True
    assert campaign.finished is False
    assert campaign.claimed_drops == 0
    assert campaign.remaining_drops == 2
    assert campaign.can_earn_within(datetime.now(timezone.utc) + timedelta(hours=1)) is False


def test_shared_prerequisite_remains_mineable_for_a_nonignored_branch():
    campaign = _campaign(
        [
            _drop("starter", "Starter", has_benefit=False),
            _drop("ignored", "Mask Reward", preconditions=("starter",)),
            _drop("wanted", "Wanted Reward", preconditions=("starter",)),
        ],
        blacklist=["mask"],
    )

    assert campaign.mineable_drop_ids == frozenset({"starter", "wanted"})
    assert [drop.id for drop in campaign.mineable_watch_drops] == [
        "starter",
        "wanted",
    ]
    assert campaign.preconditions_chain() == {"starter"}
    assert campaign.timed_drops["starter"].is_mineable is True
    assert campaign.timed_drops["ignored"].is_ignored is True
    assert campaign.timed_drops["wanted"].is_mineable is True
    assert campaign.skipped_drops == 0

    assert campaign.first_drop is not None
    assert campaign.first_drop.id == "starter"
    settings = SimpleNamespace(
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True},
    )
    wanted_tree = StreamSelector().get_wanted_game_tree(settings, [campaign])
    assert [
        drop["name"]
        for game in wanted_tree
        for campaign_data in game["campaigns"]
        for drop in campaign_data["drops"]
    ] == ["Wanted Reward"]


def test_wanted_tree_rejects_expired_and_non_mineable_drops_together():
    now = datetime.now(timezone.utc)
    campaign = _campaign(
        [
            _drop(
                "expired",
                "Expired Reward",
                ends_at=(now - timedelta(minutes=1)).isoformat(),
            ),
            _drop("ignored", "Mask Reward"),
            _drop("wanted", "Wanted Reward"),
        ],
        blacklist=["mask"],
    )

    # Expiry and ignore policy are independent: neither predicate subsumes the other.
    assert campaign.timed_drops["expired"].is_mineable is True
    assert campaign.timed_drops["ignored"].is_mineable is False

    settings = SimpleNamespace(
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True},
    )
    wanted_tree = StreamSelector().get_wanted_game_tree(settings, [campaign])

    assert [
        drop["name"]
        for game in wanted_tree
        for campaign_data in game["campaigns"]
        for drop in campaign_data["drops"]
    ] == ["Wanted Reward"]


def test_policy_evaluator_rejects_missing_and_cyclic_dependency_branches():
    missing = _campaign([_drop("reward", "Reward", preconditions=("missing",))])
    cyclic = _campaign(
        [
            _drop("a", "Reward A", preconditions=("b",)),
            _drop("b", "Reward B", preconditions=("a",)),
        ]
    )

    missing_result = DropIgnorePolicy().evaluate(tuple(missing.drops))
    cyclic_result = DropIgnorePolicy().evaluate(tuple(cyclic.drops))

    assert missing_result.reasons == {}
    assert missing_result.mineable_ids == frozenset()
    assert cyclic_result.reasons == {}
    assert cyclic_result.mineable_ids == frozenset()

    missing_reward = missing.timed_drops["reward"]
    assert missing_reward.preconditions_met is False
    assert missing_reward.can_earn() is False
    assert missing.can_earn_within(
        datetime.now(timezone.utc) + timedelta(hours=1)
    ) is False


def test_claimed_cycle_is_mineable_and_time_metrics_are_cycle_safe():
    campaign = _campaign(
        [
            _drop("claimed", "Claimed Reward", preconditions=("reward",), claimed=True),
            _drop("reward", "Final Reward", preconditions=("claimed",)),
        ]
    )

    assert campaign.mineable_drop_ids == frozenset({"reward"})
    assert campaign.can_earn_within(
        datetime.now(timezone.utc) + timedelta(hours=1)
    ) is True
    assert campaign.required_minutes == 30
    assert campaign.remaining_minutes == 15


def test_magic_mock_settings_without_a_concrete_blacklist_default_to_no_ignores():
    campaign = _campaign(
        [_drop("reward", "Ordinary Reward")],
        magic_mock_settings=True,
    )

    assert campaign.mineable_drop_ids == frozenset({"reward"})
    assert campaign.timed_drops["reward"].is_ignored is False
