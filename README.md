# Twitch Drops Miner

> Automatically mine timed Twitch Drops without streaming video or audio.

<p align="center">
  <a href="https://github.com/rangermix/TwitchDropsMiner/stargazers"><img src="https://img.shields.io/github/stars/rangermix/TwitchDropsMiner?style=for-the-badge&color=yellow" alt="GitHub stars"></a>
  <a href="https://github.com/rangermix/TwitchDropsMiner/releases"><img src="https://img.shields.io/github/v/release/rangermix/TwitchDropsMiner?style=for-the-badge&color=brightgreen" alt="Latest release"></a>
  <a href="https://hub.docker.com/r/rangermix/twitch-drops-miner"><img src="https://img.shields.io/docker/pulls/rangermix/twitch-drops-miner?style=for-the-badge&color=blue" alt="Docker pulls"></a>
  <a href="https://github.com/rangermix/TwitchDropsMiner/blob/main/LICENSE"><img src="https://img.shields.io/github/license/rangermix/TwitchDropsMiner?style=for-the-badge&color=orange" alt="License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12+-blue?style=for-the-badge&logo=python" alt="Python 3.12 or newer"></a>
</p>

Twitch Drops Miner is a low-bandwidth, headless application that discovers eligible
campaigns, selects an appropriate live channel, and tracks drop progress from a web
dashboard. It sends Twitch watch events without downloading the stream itself.

![Twitch Drops Miner web dashboard showing campaign progress, output, and channels](./screenshot.png)

## Features

- **Low-bandwidth mining** — progresses timed drops without downloading video or audio
- **Automatic campaign discovery** — detects active and upcoming drop campaigns
- **Smart channel selection** — prioritizes eligible channels, preferred games, and viewers
- **Drop-name ignore rules** — excludes unwanted reward names and dependent branches
- **Persistent sessions** — saves OAuth login state between runs
- **Web dashboard** — manages campaigns, channels, inventory, settings, and login status
- **Optional dashboard password** — protects the web UI, API, and live connections with one password
- **Drop history** — records every claimed drop locally (date, game, campaign, rewards)
  with a filterable **History** tab, aggregated stats, and one-click **Export CSV**
- **Telegram notifications** — sends an alert when a drop is claimed, including claims found during startup and inventory refresh
- **Headless deployment** — runs locally, remotely, or in Docker without a desktop GUI
- **Safe rendering** — builds dynamic translated content with DOM APIs instead of raw HTML

## Quick start

### Docker (recommended)

Docker stores persistent application data in `/app/data`. The command below binds that
directory to `./data` on the host:

```bash
docker run -d \
  --name twitch-drops-miner \
  -p 8080:8080 \
  -v "${PWD}/data:/app/data" \
  --restart unless-stopped \
  rangermix/twitch-drops-miner:latest
```

Open <http://localhost:8080>.

### Docker Compose

From the repository root, build and start the included
[`docker-compose.yml`](./docker-compose.yml):

```bash
docker compose up -d --build
```

### From source

Source installations require Python 3.12 or newer and
[`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
uv run main.py
```

Then open <http://localhost:8080>.

## Using the web app

1. Log in with your Twitch account through the OAuth device flow.
2. Wait for the miner to discover available campaigns.
3. Choose the games you want to prioritize. You can also search for a game, select
   **Add Game**, and then select **Reload**.
4. Leave the miner running while it selects eligible channels and tracks drop progress.

In **Games to Watch**, drag games to reorder them or type a priority number to move a
game directly. Priority 1 is highest; out-of-range numbers are clamped to the list ends.
Blank or fractional values leave the order unchanged. Priority controls and remove buttons
use translated labels for screen readers.

**Special Events** and **IRL** campaigns can be mined on their listed participating
channels even when those channels stream another category or lack a drops-enabled flag.
Include the campaign's category in **Games to Watch**. Channels must be live and eligible;
campaigns without an enabled participating-channel list still require a matching category.
Channels streaming categories outside Games to Watch retain the lowest automatic priority.
When the watched channel goes offline or becomes ineligible, another eligible participant
can replace it even at that same fallback priority.

Inventory filters combine **Active**, **Upcoming**, and **Expired** as alternatives.
**Not Linked** narrows that status result, while fully claimed campaigns stay hidden
until **Finished** is selected. Zero-minute subscription rewards are omitted from the
Inventory and Wanted Drops Queue because they cannot be earned by watching. Individually
expired and non-mineable rewards are also omitted from the queue, while upcoming and
sequential rewards remain visible; successful claims refresh the queue immediately. The
channel list matches game names case-insensitively and keeps the actively watched channel
visible while game settings are changing. Campaign totals and claim messages count only
rewards that can be earned by watching. Consecutive identical no-active-campaign console
prompts are collapsed until another console message appears.

**Ignored Drop Keywords** in Settings is empty by default. Enter one literal substring per
line; surrounding whitespace and blank lines are removed, and duplicates are collapsed
case-insensitively while preserving the first spelling. Matching is also case-insensitive.
A matching drop and every unclaimed branch that depends on it are ignored dynamically.
Prerequisite-only branches with no remaining mineable reward are shown as skipped, while a
prerequisite shared by an allowed reward remains mineable. Ignored and skipped drops are
never reported as claimed. This controls what the miner intentionally targets, but Twitch
may still grant simultaneous progress to an ignored reward while another reward advances.

In **Settings**, **Clear All Cache** calls `POST /api/cache/clear` to discard local
campaign, channel, and other derived miner state while preserving your OAuth login and
settings, then reloads the data from Twitch. This is a recovery and diagnostic action;
it cannot correct inaccurate campaign metadata returned by Twitch.

### Dashboard password

Password protection is **off by default**. In **Settings → Dashboard password**, enter
and confirm a password (8–1024 characters), then select **Enable password protection**.
This password is separate from your Twitch account; no username is needed. Enabling it
immediately locks out other browsers. Mining continues while the dashboard is locked.

If the login page shows a temporary request error, you can still enter your password
and select **Log in** to retry without reloading the page.

- Login uses an HttpOnly, SameSite=Strict **session cookie** by default. Select
  **Remember me for 30 days** for a persistent cookie with a fixed 30-day expiry.
  Sessions survive miner restarts, and all sessions have a maximum server lifetime of
  30 days. Browser session-restore features may preserve session cookies; use **Log out**
  to explicitly revoke a session on shared devices.
- **Change password** requires the current password and signs out all other sessions.
  The browser making the change receives a new session cookie.
- **Disable protection and clear password** also requires the current password. It
  deletes the stored password hash and all sessions, making the dashboard public again.
- Passwords are salted and hashed with scrypt; only digests of random session tokens
  are stored. Login and password-setting attempts are rate limited (5 per minute per
  client IP, 30 per minute overall). Auth credentials never enter normal settings or logs.
- The UI, application API, and Socket.IO are protected. `/healthz` stays public and
  returns only a health flag for Docker checks. Login resources and auth status are public.
  API writes require `X-TDM-Request: 1`; browser clients send it automatically. Cross-origin
  writes and Socket.IO connections are rejected.

**Remote access:** use HTTPS through a reverse proxy to encrypt passwords and cookies.
Cookies receive the Secure flag over HTTPS. Preserve the original Host header and configure
Uvicorn to trust forwarded protocol/IP headers **only from your proxy** (for example via
`FORWARDED_ALLOW_IPS`). A proxy that hides client IPs shares the per-IP login limit.
Configure protection on a trusted network before making the dashboard publicly reachable.
Run one miner process per data directory.

**Forgotten password:** stop the miner, restrict network access to its port, delete only
`data/web_auth.json` (Docker: `/app/data/web_auth.json` in the mounted data directory),
then restart and set a new password in Settings before restoring remote access. This
resets dashboard authentication without deleting Twitch cookies or other settings.
Keep the data directory private. A malformed auth file stops startup rather than silently
turning off protection. **Clear All Cache** preserves dashboard authentication.

### Drop history

The **History** tab logs every successfully claimed drop to `data/drop_history.json`.
Filter the table by game name or "claimed on or after" date, view per-game and per-month
stats, or download the current view as a CSV file (UTF-8 BOM so Excel opens it cleanly).
History controls are translated in all supported languages. The date filter starts at
midnight UTC on the selected date; displayed claim times use your browser’s local timezone.
CSV downloads support Unicode game names. Existing Twitch claims are not backfilled.
The **Clear** button deletes all locally recorded history; this does not affect your
Twitch account or already-claimed rewards.

### Telegram notifications

In **Settings → Telegram Notifications**, enter a bot token from
[@BotFather](https://t.me/BotFather) and your chat ID. Start a conversation with your bot
before selecting **Test Connection**. The Help tab contains the setup steps. A successful
test sends a test message and saves the credentials; **Save Settings** saves without sending
a message. A failed test or save displays an error.

The bot token is stored on the server and is never returned to the browser. Leave the token
field blank to reuse it when testing or changing the chat ID. To disable notifications,
clear the chat ID and save. Enter a new chat ID to enable notifications again.

Notifications cover new successful claims from both live events and inventory checks.
Repeated events for an already claimed drop do not send another alert. Telegram delivery
failures do not undo a Twitch claim, and failed notifications are not retried.

> [!NOTE]
> Your Twitch account must be linked to the relevant game accounts. Review your
> [Twitch Drops campaigns](https://www.twitch.tv/drops/campaigns) before mining.

## Important notes

> [!WARNING]
> Avoid watching Twitch manually with the same account while the miner is running.
> Simultaneous viewing can cause drop-progress desynchronization.

- Docker data is stored inside the container at `/app/data`; the examples persist it
  to `./data` on the host.
- Source installations store persistent data in the repository's `data/` directory.
- Logs can be persisted separately by mounting `./logs:/app/logs`.

## Contributors

Contributors are credited automatically when their pull requests are merged into `main`.

<!-- contributors:start -->
| Contributor | Merged pull requests |
| --- | --- |
| [@birdhimself](https://github.com/birdhimself) | [#41](https://github.com/rangermix/TwitchDropsMiner/pull/41) |
| [@capkz](https://github.com/capkz) | [#70](https://github.com/rangermix/TwitchDropsMiner/pull/70) |
| [@EthanBlazkowicz](https://github.com/EthanBlazkowicz) | [#33](https://github.com/rangermix/TwitchDropsMiner/pull/33) |
| [@Klages](https://github.com/Klages) | [#94](https://github.com/rangermix/TwitchDropsMiner/pull/94) · [#95](https://github.com/rangermix/TwitchDropsMiner/pull/95) |
| [@Knight-sys](https://github.com/Knight-sys) | [#3](https://github.com/rangermix/TwitchDropsMiner/pull/3) |
| [@rangermix](https://github.com/rangermix) | [#1](https://github.com/rangermix/TwitchDropsMiner/pull/1) · [#2](https://github.com/rangermix/TwitchDropsMiner/pull/2) · [#7](https://github.com/rangermix/TwitchDropsMiner/pull/7) · [#8](https://github.com/rangermix/TwitchDropsMiner/pull/8) · [#9](https://github.com/rangermix/TwitchDropsMiner/pull/9) · [#13](https://github.com/rangermix/TwitchDropsMiner/pull/13) · [#20](https://github.com/rangermix/TwitchDropsMiner/pull/20) · [#24](https://github.com/rangermix/TwitchDropsMiner/pull/24) · [#29](https://github.com/rangermix/TwitchDropsMiner/pull/29) · [#32](https://github.com/rangermix/TwitchDropsMiner/pull/32) · [#45](https://github.com/rangermix/TwitchDropsMiner/pull/45) · [#74](https://github.com/rangermix/TwitchDropsMiner/pull/74) · [#79](https://github.com/rangermix/TwitchDropsMiner/pull/79) · [#80](https://github.com/rangermix/TwitchDropsMiner/pull/80) · [#84](https://github.com/rangermix/TwitchDropsMiner/pull/84) · [#86](https://github.com/rangermix/TwitchDropsMiner/pull/86) · [#88](https://github.com/rangermix/TwitchDropsMiner/pull/88) · [#93](https://github.com/rangermix/TwitchDropsMiner/pull/93) · [#89](https://github.com/rangermix/TwitchDropsMiner/pull/89) · [#90](https://github.com/rangermix/TwitchDropsMiner/pull/90) · [#91](https://github.com/rangermix/TwitchDropsMiner/pull/91) · [#92](https://github.com/rangermix/TwitchDropsMiner/pull/92) · [#104](https://github.com/rangermix/TwitchDropsMiner/pull/104) |
| [@Sean-Destefano](https://github.com/Sean-Destefano) | [#49](https://github.com/rangermix/TwitchDropsMiner/pull/49) |
| [@SimpliAj](https://github.com/SimpliAj) | [#72](https://github.com/rangermix/TwitchDropsMiner/pull/72) |
| [@Stein-N](https://github.com/Stein-N) | [#71](https://github.com/rangermix/TwitchDropsMiner/pull/71) |
| [@vurmil](https://github.com/vurmil) | [#12](https://github.com/rangermix/TwitchDropsMiner/pull/12) · [#17](https://github.com/rangermix/TwitchDropsMiner/pull/17) · [#18](https://github.com/rangermix/TwitchDropsMiner/pull/18) · [#100](https://github.com/rangermix/TwitchDropsMiner/pull/100) |
<!-- contributors:end -->

## Support

If Twitch Drops Miner saves you time or bandwidth, you can support the project by:

- [starring the repository](https://github.com/rangermix/TwitchDropsMiner)
- [reporting an issue](https://github.com/rangermix/TwitchDropsMiner/issues) or
  [submitting a pull request](https://github.com/rangermix/TwitchDropsMiner/pulls)
- [buying the maintainer a coffee](https://buymeacoffee.com/rangermix)

## Credits

This project is a modern fork of
[DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner), created by
[@DevilXD](https://github.com/DevilXD). You can support the original author through
[Buy Me a Coffee](https://www.buymeacoffee.com/DevilXD) or
[Patreon](https://www.patreon.com/bePatron?u=26937862).

<details>
<summary>Original project and translation credits</summary>

### Original project contributions

- [@guihkx](https://github.com/guihkx) — CI scripts, CI maintenance, and Linux builds
- [@kWAYTV](https://github.com/kWAYTV) — dark mode theme

### Translation credits

- **Arabic** — [@Bamboozul](https://github.com/Bamboozul)
- **Chinese (Simplified)** — [@Suz1e](https://github.com/Suz1e),
  [@wwj010](https://github.com/wwj010), and
  [@zhangminghao1989](https://github.com/zhangminghao1989)
- **Chinese (Traditional)** — [@Ricky103403](https://github.com/Ricky103403) and
  [@LusTerCsI](https://github.com/LusTerCsI)
- **Czech** — [@nwvh](https://github.com/nwvh)
- **Danish** — [@Kjerne](https://github.com/Kjerne)
- **French** — [@roobini-gamer](https://github.com/roobini-gamer) and
  [@Calvineries](https://github.com/Calvineries)
- **German** — [@ThisIsCyreX](https://github.com/ThisIsCyreX)
- **Hungarian** — [@centipederat](https://github.com/centipederat)
- **Indonesian** — [@Eriza-Z](https://github.com/Eriza-Z)
- **Italian** — [@casungo](https://github.com/casungo)
- **Japanese** — [@ShimadaNanaki](https://github.com/ShimadaNanaki)
- **Polish** — [@Patriot99](https://github.com/Patriot99), co-authored with
  [@DevilXD](https://github.com/DevilXD)
- **Portuguese** — [@zarigata](https://github.com/zarigata)
- **Russian** — [@Sergo1217](https://github.com/Sergo1217) and
  [@kilroy98](https://github.com/kilroy98)
- **Spanish** — [@Shofuu](https://github.com/Shofuu)
- **Turkish** — [@alikdb](https://github.com/alikdb)
- **Ukrainian** — [@Nollasko](https://github.com/Nollasko) and
  [@kilroy98](https://github.com/kilroy98)

</details>

## Development disclosure

Repository instructions for all coding agents live in [AGENTS.md](./AGENTS.md).
`CLAUDE.md` and `GEMINI.md` are relative symlinks to that file; edit `AGENTS.md` to
update the shared guidance.

This fork is maintained with AI-assisted development tools. Changes are validated through
automated tests and code-quality checks, but users should still review updates before
deploying them. The validation suite includes GraphQL watch events and batched channel
discovery, alongside settings, full-locale translation schema and placeholder checks,
and frontend safety checks. Use the software
responsibly. Release automation verifies that the runtime, package, and lockfile versions
match before publishing tags and Docker images. Docker validation and release jobs use
the same pinned, Node-24-native Buildx and image-build action releases.
The suite also covers ignored-keyword normalization, dependency branches, the combined
expiry/ignore Wanted Queue guard, watch selection, API persistence, translated placeholder
parity, frontend rendering, and the claimed-drop history store with CSV export and API
endpoints. Any `web/static/app.js` or `web/static/styles.css` change
must go through the release workflow so the application version and browser asset cache key
are bumped before deployment.

Telegram regression coverage includes Help translation rendering, saved-token reuse,
disabling notifications, failed saves, claim deduplication, and mocked Telegram transport
errors. From the activated environment, run:

```bash
python -m pytest tests/test_telegram_frontend.py tests/test_telegram_api.py tests/test_telegram_integration.py
```

No real Telegram messages are sent by these tests.

Games to Watch supports Enter to add an exact or unique partial match. Ambiguous
searches ask for a more specific name. Manual names and Deselect All require a
confirmation; Escape cancels and keyboard focus stays in the dialog. Select All
retains the existing priority order and manual entries, adding missing games only.
