"""Optional reward ordering preserves eligibility, selected tiers and saved game order."""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.reward_priority import RewardPriority
from src.config.settings import Settings, default_settings
from src.services.channel_service import ChannelService
from src.services.stream_selector import StreamSelector
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager
from tests.test_priority_fallback import _campaign, _channel, _drop, _settings


ORDER = list(RewardPriority.TYPES)


def settings(*games, **kwargs):
    return _settings(*games, reward_priority=ORDER, mining_benefits=dict.fromkeys(ORDER, True), **kwargs)


@pytest.mark.parametrize('value', [None, {}, 'BADGE', ['BADGE'], ORDER + ['BADGE'], ['BADGE'] * 4,
                                  ['BADGE', 'EMOTE', 'DIRECT_ENTITLEMENT', 1]])
def test_rejects_malformed_priority_before_mutation(value):
    with pytest.raises(ValueError):
        RewardPriority.normalize(value)
    if value is not None:
        with pytest.raises(ValueError):
            SettingsUpdate(reward_priority=value)


def test_default_disabled_and_persisted_round_trip():
    assert default_settings['reward_priority'] == []
    stored = copy.deepcopy(default_settings)
    stored['reward_priority'] = ORDER
    with patch('src.config.settings.json_load', return_value=stored), patch('src.config.settings.json_save') as save:
        value = Settings()
        value.save()
        assert save.call_args.args[1]['reward_priority'] == ORDER
    assert RewardPriority.normalize([]) == []


def test_reward_type_precedes_game_order_and_deadline_breaks_ties():
    value = settings('Items', 'Later badge', 'Soon badge', priority_list_only=True)
    now = datetime.now(timezone.utc)
    campaigns = [
        _campaign('items', 'Items', game_id=1, settings=value, drops=[_drop('item')]),
        _campaign('later', 'Later badge', game_id=2, settings=value,
                  drops=[_drop('badge-later', benefit_type='BADGE', ends_at=(now + timedelta(hours=8)).isoformat())]),
        _campaign('soon', 'Soon badge', game_id=3, settings=value,
                  drops=[_drop('badge-soon', benefit_type='BADGE', ends_at=(now + timedelta(hours=2)).isoformat())]),
    ]
    saved = value.games_to_watch.copy()
    games = StreamSelector().get_wanted_games(value, campaigns)
    assert [game.name for game in games] == ['Soon badge', 'Later badge', 'Items']
    assert value.games_to_watch == saved
    twitch = SimpleNamespace(settings=value, inventory=campaigns, wanted_games=games, gui=MagicMock())
    channels = [_channel(twitch, index, {'id': str(campaign.game.id), 'name': campaign.game.name}) for index, campaign in enumerate(campaigns, 1)]
    service = ChannelService(twitch)
    assert service.get_mining_priority(channels[2]) < service.get_mining_priority(channels[1]) < service.get_mining_priority(channels[0])


@pytest.mark.parametrize('boundary', ['ignored', 'claimed', 'expired', 'disabled'])
def test_high_priority_reward_cannot_bypass_eligibility(boundary):
    value = settings('Item', 'Badge', priority_list_only=True)
    if boundary == 'ignored':
        value.drop_name_blacklist = ['badge']
    if boundary == 'disabled':
        value.mining_benefits['BADGE'] = False
    options = {'benefit_type': 'BADGE'}
    if boundary == 'expired':
        options['ends_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    campaigns = [_campaign('item', 'Item', settings=value),
                 _campaign('badge', 'Badge', game_id=2, settings=value, linked=boundary != 'unlinked',
                           drops=[_drop('badge', **options)])]
    if boundary == 'claimed':
        next(iter(campaigns[1].drops)).is_claimed = True
    assert [game.name for game in StreamSelector().get_wanted_games(value, campaigns)] == ['Item']


def test_fallback_badge_never_preempts_selected_item():
    value = settings('Selected')
    campaigns = [_campaign('item', 'Selected', settings=value),
                 _campaign('badge', 'Fallback', game_id=2, settings=value,
                           drops=[_drop('badge', benefit_type='BADGE')])]
    games = StreamSelector().get_wanted_games(value, campaigns)
    assert [game.name for game in games] == ['Selected', 'Fallback']
    twitch = SimpleNamespace(settings=value, inventory=campaigns, wanted_games=games, gui=MagicMock())
    channels = [_channel(twitch, i, {'id': str(campaign.game.id), 'name': campaign.game.name}) for i, campaign in enumerate(campaigns, 1)]
    service = ChannelService(twitch)
    assert service.get_mining_priority(channels[0]) < service.get_mining_priority(channels[1])


@pytest.mark.asyncio
async def test_priority_save_failure_rolls_back_and_success_requests_refresh():
    stored = SimpleNamespace(**copy.deepcopy(default_settings), save=MagicMock())
    changed = MagicMock()
    manager = SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, MagicMock(), changed)
    stored.save.side_effect = OSError('synthetic failure')
    with pytest.raises(OSError):
        manager.update_settings({'reward_priority': ORDER})
    assert stored.reward_priority == []
    changed.assert_not_called()
    stored.save.side_effect = None
    result = manager.update_settings({'reward_priority': ORDER})
    assert result['reward_priority'] == ORDER
    changed.assert_called_once()


def test_locked_badge_does_not_preempt_earnable_emote_and_queue_keeps_sequence():
    value = settings('Sequence', 'Emote', priority_list_only=True)
    prerequisite = _drop('prerequisite')
    badge = _drop('locked-badge', benefit_type='BADGE', precondition_drop_ids=['prerequisite'])
    campaigns = [_campaign('sequence', 'Sequence', settings=value, drops=[badge, prerequisite]),
                 _campaign('emote', 'Emote', game_id=2, settings=value, drops=[_drop('emote', benefit_type='EMOTE')])]
    tree = StreamSelector().get_wanted_game_tree(value, campaigns)
    assert [game['game_name'] for game in tree] == ['Emote', 'Sequence']
    assert [drop['name'] for drop in tree[1]['campaigns'][0]['drops']] == ['prerequisite', 'locked-badge']
    assert campaigns[0].first_drop.name == 'prerequisite'


def test_same_game_campaign_and_drop_queue_match_type_order():
    value = settings('Game', priority_list_only=True)
    campaigns = [_campaign('item', 'Game', settings=value),
                 _campaign('mixed', 'Game', settings=value, drops=[_drop('item'), _drop('badge', benefit_type='BADGE')])]
    tree = StreamSelector().get_wanted_game_tree(value, campaigns)
    assert [entry['id'] for entry in tree[0]['campaigns']] == ['mixed', 'item']
    assert [entry['name'] for entry in tree[0]['campaigns'][0]['drops']] == ['badge', 'item']
    assert campaigns[1].first_drop.name == 'badge'


def test_selected_special_campaign_precedes_fallback_on_same_channel():
    from src.services.inventory_service import InventoryService
    value = settings('Special Events')
    selected = _campaign('selected', 'Special Events', game_id=509663, settings=value, acl=[1])
    fallback = _campaign('fallback', 'Regular', game_id=2, settings=value,
                         drops=[_drop('badge', benefit_type='BADGE')])
    twitch = SimpleNamespace(settings=value, inventory=[fallback, selected],
                             wanted_games=[selected.game, fallback.game], gui=MagicMock())
    channel = _channel(twitch, 1, {'id': '2', 'name': 'Regular'})
    twitch.watching_channel = SimpleNamespace(get_with_default=lambda fallback: channel)
    assert InventoryService(twitch).get_active_campaign(channel) is selected


def test_mixed_benefit_drop_uses_best_enabled_type_without_reenabling_disabled_type():
    value = settings('Game', priority_list_only=True)
    item = _drop('item')
    item['benefitEdges'] += _drop('badge', benefit_type='BADGE')['benefitEdges']
    campaign = _campaign('mixed', 'Game', settings=value, drops=[item])
    policy = RewardPriority(value)
    assert policy.campaign_key(campaign)[0] == 0
    value.mining_benefits['BADGE'] = False
    assert RewardPriority(value).campaign_key(campaign)[0] == 2


def test_active_campaign_tie_uses_game_order_before_remaining_minutes():
    from src.services.inventory_service import InventoryService
    value = settings('Special Events', 'Regular')
    special = _campaign('special', 'Special Events', game_id=509663, settings=value, acl=[1],
                        drops=[_drop('long', required_minutes=120)])
    regular = _campaign('regular', 'Regular', game_id=2, settings=value,
                        drops=[_drop('short', required_minutes=15)])
    twitch = SimpleNamespace(settings=value, inventory=[regular, special],
                             wanted_games=[special.game, regular.game], gui=MagicMock())
    channel = _channel(twitch, 1, {'id': '2', 'name': 'Regular'})
    twitch.watching_channel = SimpleNamespace(get_with_default=lambda fallback: channel)
    assert InventoryService(twitch).get_active_campaign(channel) is special
