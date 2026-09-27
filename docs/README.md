# Twitch Drops Miner guides

Twitch Drops Miner discovers eligible timed Twitch Drops, chooses live channels, and
tracks and claims rewards without downloading stream video or audio. Use its web
dashboard to choose games, follow progress, and manage settings.

Start with [Installation](installation.md), then [Twitch login](authentication.md)
and [Using the dashboard](usage.md).

| Guide | What you will find |
| --- | --- |
| [Installation](installation.md) | Docker, Compose, source setup, persistent data, and upgrades |
| [Twitch login](authentication.md) | Login helper downloads, existing sessions, account replacement, and automatic renewal |
| [Using the dashboard](usage.md) | Game priorities, campaigns, inventory, ignored rewards, and drop history |
| [Dashboard access](dashboard-access.md) | Optional password protection, reverse proxies, and password recovery |
| [Telegram notifications](notifications.md) | Bot setup, testing, saved credentials, and disabling alerts |
| [Troubleshooting](troubleshooting.md) | Login, renewal, progress, connection, and cache problems |
| [Credits](credits.md) | Upstream authors, translators, contributors, and project support |

These guides describe the current source tree. Published versions may differ; check
the [release notes and available assets](https://github.com/rangermix/TwitchDropsMiner/releases)
for your version. The [installation guide](installation.md#choose-a-version) explains
how to use an unreleased source revision without mixing incompatible downloads.

## Supported use

This is a hobby project for personal use on your own hardware and home network.
Support is best-effort and limited to that setup. VPS, cloud, other third-party
hosting, and services operated for other users are outside the support scope.
Continued compatibility with Twitch is not guaranteed.

Your Twitch account must be linked to the relevant game account, and the reward must
be available to you and earned by watching. Review your
[Twitch Drops campaigns](https://www.twitch.tv/drops/campaigns) before mining. Avoid
watching Twitch manually with the same account while the miner is running, because
simultaneous viewing can disrupt drop progress.

For bug reports, translations, and other contributions, read
[CONTRIBUTING.md](../CONTRIBUTING.md). Return to the
[project overview](../README.md) for the quick start and current contributors.
