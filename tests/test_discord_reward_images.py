"""Discord reward artwork is public metadata, never an extra miner download."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.discord_service import DiscordNotifier, _escape_embed


IMAGE = 'https://static-cdn.jtvnw.net/twitch-drops/reward.png'


def drop(*images):
    return SimpleNamespace(name='Reward bundle', campaign=SimpleNamespace(name='Campaign', game=SimpleNamespace(name='Game')),
        benefits=[SimpleNamespace(name=f'Reward {index}', image_url=url) for index, url in enumerate(images)])


@pytest.mark.parametrize('url', [None, '', 'http://static-cdn.jtvnw.net/x.png', 'https://localhost/x.png',
    'https://static-cdn.jtvnw.net.evil.test/x.png', 'https://user:password@static-cdn.jtvnw.net/x.png',
    IMAGE+'?token=private', IMAGE+'#private', IMAGE+'\x7f', IMAGE+'\x85', 'file:///tmp/private', 'data:image/png;base64,abcd',
    'https://static-cdn.jtvnw.net:444/x.png', 'https://static-cdn.jtvnw.net:invalid/x.png', IMAGE+'\n',
    'https://static-cdn.jtvnw.net/'+'a'*2100])
def test_untrusted_or_sensitive_artwork_is_not_forwarded(url):
    assert DiscordNotifier.reward_image(drop(url)) is None


def test_first_usable_image_skips_missing_and_untrusted_entries():
    assert DiscordNotifier.reward_image(drop(None, 'https://example.test/x.png', IMAGE, IMAGE.replace('reward', 'second'))) == IMAGE
    assert DiscordNotifier.reward_image(drop()) is None
    assert DiscordNotifier.reward_image(drop('https://static-cdn.jtvnw.net:443/x.png')) == 'https://static-cdn.jtvnw.net:443/x.png'


@pytest.mark.asyncio
@pytest.mark.parametrize('images', [(IMAGE,), (None,), ('https://example.test/private',), (None, IMAGE)])
async def test_claim_embed_includes_artwork_or_text_fallback_without_extra_request(images):
    notifier = DiscordNotifier('https://discord.com/api/webhooks/123/test')
    notifier._send_embed = AsyncMock(return_value=True)
    assert await notifier.notify_drop_claimed(drop(*images))
    embed = notifier._send_embed.await_args.args[0]
    assert embed['fields'][-1]['value'] == ', '.join(f'Reward {index}' for index in range(len(images)))
    if IMAGE in images:
        assert embed['image'] == {'url': IMAGE}
        assert _escape_embed(embed)['image'] == {'url': IMAGE}
    else:
        assert 'image' not in embed
