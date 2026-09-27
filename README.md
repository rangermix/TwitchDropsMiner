# Twitch Drops Miner

<!-- Temporary upgrade notice: remove after the v2.1.0 release. -->
> [!WARNING]
> **All users must upgrade to [TDM 2.0 or newer](https://github.com/rangermix/TwitchDropsMiner/releases/latest).**
> Twitch has changed its authentication, and the old sign-in method no longer works.
>
> - **v1.3.1–v1.3.2 users:** sign in again with the [login helper](docs/authentication.md#sign-in) after upgrading.
> - **Other users:** your existing valid login is preserved; you do not need to sign in again. Keep your `data` directory, including `cookies.jar`.
<!-- End temporary upgrade notice. -->

Automatically earn timed Twitch Drops without downloading stream video or audio.

<p align="center">
  <a href="https://github.com/rangermix/TwitchDropsMiner/stargazers"><img src="https://img.shields.io/github/stars/rangermix/TwitchDropsMiner?style=for-the-badge&color=yellow" alt="GitHub stars"></a>
  <a href="https://github.com/rangermix/TwitchDropsMiner/releases"><img src="https://img.shields.io/github/v/release/rangermix/TwitchDropsMiner?style=for-the-badge&color=brightgreen" alt="Latest release"></a>
  <a href="https://hub.docker.com/r/rangermix/twitch-drops-miner"><img src="https://img.shields.io/docker/pulls/rangermix/twitch-drops-miner?style=for-the-badge&color=blue" alt="Docker pulls"></a>
  <a href="https://github.com/rangermix/TwitchDropsMiner/blob/main/LICENSE"><img src="https://img.shields.io/github/license/rangermix/TwitchDropsMiner?style=for-the-badge&color=orange" alt="License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12+-blue?style=for-the-badge&logo=python" alt="Python 3.12 or newer"></a>
</p>

TDM discovers eligible campaigns, selects live channels, and tracks and claims rewards
from a web dashboard. Run it on your own computer or home server. This hobby project
supports personal use on your own hardware and home network; third-party hosting and
services operated for other users are outside its support scope.

![Twitch Drops Miner web dashboard](./screenshot.png)

- Choose and prioritize games, or let TDM discover available campaigns.
- Track campaigns and rewards, and ignore drops you do not want.
- Sign in through the local helper; TDM saves and renews your session automatically.
- Review claimed-drop history, export it to CSV, and receive Telegram notifications.
- Manage everything from a web dashboard with optional password protection.

## Quick start

With Docker installed, start TDM from the directory where you want to keep its data:

```bash
docker run -d \
  --name twitch-drops-miner --init --stop-timeout 30 --shm-size 256m \
  -p 8080:8080 \
  -v "${PWD}/data:/app/data" \
  --restart unless-stopped \
  rangermix/twitch-drops-miner:2.0.1
```

Open <http://localhost:8080>, or `http://YOUR-SERVER:8080` from another device on your
home network. Keep the `data` directory when updating or recreating the container.
For Docker Compose, installation without Docker, and updates, see the
[installation guide](docs/installation.md).

## Sign in

**Run the login helper on your desktop or laptop**, even when TDM runs on a headless
home server or NAS. The server does not need a desktop or display.

1. Get the [login helper](docs/authentication.md#download-the-helper) for your desktop,
   matching your TDM release or source revision. Chrome must be installed on that desktop.
2. Enable **Settings → Allow helper connection** in TDM. Run the helper on your desktop
   and enter the server's dashboard URL, such as `http://192.168.1.10:8080`.
3. Sign into Twitch in the Chrome window it opens and wait for the helper's success
   message. Then choose your games in TDM.

The helper uploads the session automatically and closes its temporary Chrome window.
TDM handles renewal on your server, so your desktop and browser can be turned off.
See [authentication](docs/authentication.md#headless-home-server-or-nas) for which
machine runs each part, platform downloads, and login recovery.
For Chrome discovery, an unknown helper result, or a startup error involving
`web_auth.json`, see [troubleshooting](docs/troubleshooting.md).

## Upgrading to v2.0

Keep your existing `data` directory, including `cookies.jar`, and the same Docker volume.
A working Android session is restored automatically. If you used the Smart TV login in
v1.3.1/v1.3.2, your session has expired, or you are signed out, use the login helper
matching your updated TDM version. Follow the [v2.0 migration steps](https://github.com/rangermix/TwitchDropsMiner/releases/tag/v2.0.0)
when updating your container or source installation.

## User guides

Browse the [GitHub wiki](https://github.com/rangermix/TwitchDropsMiner/wiki) or read the
same guides in [docs](docs/README.md):

- [Installation and updates](docs/installation.md)
- [Login, migration, and recovery](docs/authentication.md)
- [Games, campaigns, inventory, and history](docs/usage.md)
- [Dashboard password and reverse proxies](docs/dashboard-access.md)
- [Telegram notifications](docs/notifications.md)
- [Troubleshooting](docs/troubleshooting.md)

## Help and contributions

Search [existing issues](https://github.com/rangermix/TwitchDropsMiner/issues) before
reporting a problem. See [CONTRIBUTING.md](CONTRIBUTING.md) for bug reports, translations,
and development. This fork uses AI-assisted development with automated checks and
independent review.

Based on [DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner).
See [credits](docs/credits.md) for the original project and translation contributors.
You can support this fork by [starring it](https://github.com/rangermix/TwitchDropsMiner)
or [buying the maintainer a coffee](https://buymeacoffee.com/rangermix).

## Contributors

<details>
<summary>People who contributed merged pull requests</summary>

<!-- contributors:start -->
| Contributor | Merged pull requests |
| --- | --- |
| [@3lb0z0](https://github.com/3lb0z0) | [#110](https://github.com/rangermix/TwitchDropsMiner/pull/110) |
| [@birdhimself](https://github.com/birdhimself) | [#41](https://github.com/rangermix/TwitchDropsMiner/pull/41) |
| [@capkz](https://github.com/capkz) | [#70](https://github.com/rangermix/TwitchDropsMiner/pull/70) |
| [@EthanBlazkowicz](https://github.com/EthanBlazkowicz) | [#33](https://github.com/rangermix/TwitchDropsMiner/pull/33) |
| [@Klages](https://github.com/Klages) | [#94](https://github.com/rangermix/TwitchDropsMiner/pull/94) · [#95](https://github.com/rangermix/TwitchDropsMiner/pull/95) |
| [@Knight-sys](https://github.com/Knight-sys) | [#3](https://github.com/rangermix/TwitchDropsMiner/pull/3) |
| [@rangermix](https://github.com/rangermix) | [#1](https://github.com/rangermix/TwitchDropsMiner/pull/1) · [#2](https://github.com/rangermix/TwitchDropsMiner/pull/2) · [#7](https://github.com/rangermix/TwitchDropsMiner/pull/7) · [#8](https://github.com/rangermix/TwitchDropsMiner/pull/8) · [#9](https://github.com/rangermix/TwitchDropsMiner/pull/9) · [#13](https://github.com/rangermix/TwitchDropsMiner/pull/13) · [#20](https://github.com/rangermix/TwitchDropsMiner/pull/20) · [#24](https://github.com/rangermix/TwitchDropsMiner/pull/24) · [#29](https://github.com/rangermix/TwitchDropsMiner/pull/29) · [#32](https://github.com/rangermix/TwitchDropsMiner/pull/32) · [#45](https://github.com/rangermix/TwitchDropsMiner/pull/45) · [#74](https://github.com/rangermix/TwitchDropsMiner/pull/74) · [#79](https://github.com/rangermix/TwitchDropsMiner/pull/79) · [#80](https://github.com/rangermix/TwitchDropsMiner/pull/80) · [#84](https://github.com/rangermix/TwitchDropsMiner/pull/84) · [#86](https://github.com/rangermix/TwitchDropsMiner/pull/86) · [#88](https://github.com/rangermix/TwitchDropsMiner/pull/88) · [#93](https://github.com/rangermix/TwitchDropsMiner/pull/93) · [#89](https://github.com/rangermix/TwitchDropsMiner/pull/89) · [#90](https://github.com/rangermix/TwitchDropsMiner/pull/90) · [#91](https://github.com/rangermix/TwitchDropsMiner/pull/91) · [#92](https://github.com/rangermix/TwitchDropsMiner/pull/92) · [#104](https://github.com/rangermix/TwitchDropsMiner/pull/104) · [#105](https://github.com/rangermix/TwitchDropsMiner/pull/105) · [#116](https://github.com/rangermix/TwitchDropsMiner/pull/116) · [#119](https://github.com/rangermix/TwitchDropsMiner/pull/119) · [#120](https://github.com/rangermix/TwitchDropsMiner/pull/120) · [#124](https://github.com/rangermix/TwitchDropsMiner/pull/124) · [#125](https://github.com/rangermix/TwitchDropsMiner/pull/125) · [#126](https://github.com/rangermix/TwitchDropsMiner/pull/126) · [#127](https://github.com/rangermix/TwitchDropsMiner/pull/127) · [#131](https://github.com/rangermix/TwitchDropsMiner/pull/131) · [#133](https://github.com/rangermix/TwitchDropsMiner/pull/133) |
| [@Sean-Destefano](https://github.com/Sean-Destefano) | [#49](https://github.com/rangermix/TwitchDropsMiner/pull/49) |
| [@SimpliAj](https://github.com/SimpliAj) | [#72](https://github.com/rangermix/TwitchDropsMiner/pull/72) |
| [@Stein-N](https://github.com/Stein-N) | [#71](https://github.com/rangermix/TwitchDropsMiner/pull/71) |
| [@vurmil](https://github.com/vurmil) | [#12](https://github.com/rangermix/TwitchDropsMiner/pull/12) · [#17](https://github.com/rangermix/TwitchDropsMiner/pull/17) · [#18](https://github.com/rangermix/TwitchDropsMiner/pull/18) · [#100](https://github.com/rangermix/TwitchDropsMiner/pull/100) |
<!-- contributors:end -->

</details>
