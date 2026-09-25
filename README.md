# Twitch Drops Miner

> Automatically mine timed Twitch Drops without streaming video or audio.

> **Warning: new Twitch device-code login is broken; browser recovery is experimental.** Preserve existing `data/cookies.jar`
> files and backups. New login and missing-campaign recovery are tracked in
> [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).

Twitch rejects new device-code authorization for the Android app client. This source
revision restores `ANDROID_APP` as the default so still-valid Android sessions can be
reused without forced reauthorization. It cannot recover credentials already deleted,
replaced, or expired. Releases v1.3.1 and v1.3.2 use the Smart TV client, which can log in
but may show only campaigns already in progress; successful login or a healthy container
does not establish complete campaign discovery. Clearing data, reinstalling, or changing
Games to Watch does not repair this upstream restriction. Native Chrome login and
local-session export/import with a browser-assisted renewal helper have passed live
checks on one home network. An experimental [server renewal helper](docs/server-renewal.md)
now uses a one-time SDK cookie export and headless Chromium on the server, allowing the
local browser to close. Two consecutive scheduled cycles and protected requests after
each previous integrity token expired have passed in Alpine. Renewal also passed after
the original exported SDK cookie expired, and the miner and helper resumed from saved
server state after restart. Initial export and subsequent server consumption passed with
Chrome closed before the server ran. Independent inventory, campaign, stream-lookup and
current-drop checks passed; see the [timestamped evidence and limits](docs/notes/2026-09-25-sdk-cookie-renewal.md).
Fresh browser login inside Docker remains rejected.
These are experimental source features in this branch, not a released fix. The tracking
issue records implementation, live verification, and release status.

`DEVICE_AUTH_400` means Twitch rejected new device authorization. `CLIENT_MISMATCH`
means a saved token belongs to another client; it is preserved, but cannot be used as
an Android token. Neither error is fixed by deleting the data directory.

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

> [!IMPORTANT]
> **This is a hobby project for personal use on your own hardware and home network.**
> Support is limited to that setup. VPS, cloud, and other third-party hosting environments,
> as well as services operated for other users, are outside the project's support scope.
> Maintenance and support are provided on a best-effort basis; continued compatibility
> with Twitch is not guaranteed.

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
- **Headless deployment** — runs on your own home hardware, including Docker, without a desktop GUI
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

### Experimental browser login (#118)

The optional browser setup runs **Google Chrome** in a separate container with a
virtual display and an interactive viewer. TDM controls its persistent session through
WebDriver. You enter your Twitch credentials and any verification directly on Twitch's
page. TDM continues only after checking the web token's identity and obtaining both
inventory and campaign responses. Existing valid Android sessions take priority and do
not start a browser session.

**Live status (24 September 2026):** Twitch rejected login in both Debian Chromium 152
and official Google Chrome 153 in Docker with “Your browser is not currently supported.”
The Chrome WebDriver attempt returned HTTP 400 with Twitch error code `5025`. The same
Chrome version also failed when launched without ChromeDriver in a separate profile,
with `navigator.webdriver` false. Firefox 156 controlled through WebDriver BiDi in a
separate Docker profile was also rejected. Removing ChromeDriver or changing browser
engine therefore did not resolve the rejection. A separate native Chrome 153 profile on
macOS did accept fresh login under DevTools control. TDM's browser service then attached
through ChromeDriver and validated the account, inventory, and 125 campaigns without
GraphQL errors after waiting for Twitch's complete request context. With the full miner
running, a separate live Twitch inventory query confirmed the test campaign advancing
from 0 to 4 watched minutes. This supports the desktop attachment option below; Docker
login remains unresolved. Restarting both the dedicated Chrome instance and the miner
restored login and resumed watching without another sign-in. See
[#118](https://github.com/rangermix/TwitchDropsMiner/issues/118) for current results.

Further fresh-login tests in Docker also failed with the same unsupported-browser message:
Camoufox 152.0.4 beta.28 (`docker-stealthy-auto-browse`), Chrome 153 with Puppeteer-Stealth,
and Browserless 2.56.7 Chrome 153 with its built-in `stealth` launch option. Browserless
ran under AMD64 emulation; the other two used ARM64 containers. All three reported
`navigator.webdriver` false and produced no authenticated cookie. These results cover
the tested configurations on one home network, not every browser or fingerprint setup.

A separate session-transfer experiment copied Twitch cookies from the working desktop
profile into Docker Chrome. Identity and inventory succeeded, but campaign access still
failed Twitch's integrity check. Reusing the desktop browser's complete matching request
context instead returned inventory and 126 campaigns through both Docker Chrome and a
plain Python HTTP client inside Docker. The manual import path below now implements
that approach and supports a local automatic renewal helper. Operation on another
network remains unverified. Later server-renewal tests observed Twitch-side account
progress, with other-device activity uncontrolled; see the evidence note below. A fresh authenticated GraphQL
integrity response advertised about **one hour** of validity; the login cookie's much
longer lifetime does not extend that context. The issued token matched a successful
authenticated request returning 126 campaigns. An export needs renewal; the imported provider enforces its observed expiry, and the
local helper refreshes it before expiry. Twitch's login page returns
`X-Frame-Options: SAMEORIGIN`, and browser origin isolation prevents a TDM page from
reading Twitch cookies or storage. Automatic export would need a local helper or an
explicitly permitted browser extension, rather than a login iframe. See the
[session portability investigation](docs/notes/2026-09-24-browser-session-portability.md).
The direct browser integration still requires its browser to remain running. Manual
import can operate without that browser until the imported context expires.

#### Manual local-browser export and import (experimental)

This source-only option lets a home-hosted TDM instance use a session from a working
local Chrome browser. The TDM server does not need a browser. It is not in a release.

1. Enable `TDM_SESSION_IMPORT=1` on TDM. For Compose, add
   `- TDM_SESSION_IMPORT=1` under the miner service's `environment` list. Do not configure
   `TDM_BROWSER_URL`, `TDM_BROWSER_VIEWER_URL`, or `TDM_BROWSER_DEBUGGER_ADDRESS` with this
   mode. Still-valid Android credentials retain priority and are never overwritten.
2. Enable dashboard password protection in **Settings**, then log into the dashboard.
   Import requires authenticated dashboard access, even on an otherwise unprotected
   instance. Use HTTPS or a local tunnel when accessing the dashboard remotely.
3. On your local computer, open a dedicated Chrome profile with loopback DevTools and
   sign into Twitch there. For example, on macOS:

   ```bash
   open -na "Google Chrome" --args \
     --user-data-dir="$HOME/.tdm-login-profile" \
     --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
     https://www.twitch.tv/drops/campaigns
   ```

4. From a local source checkout with its Python environment installed, export:

   ```bash
   source env/bin/activate
   python -m src.auth.session_helper export \
     --browser http://127.0.0.1:9222 --output "$HOME/tdm-session.json"
   ```

5. On TDM's **Main** tab, choose that file under **Import browser session** and click
   **Import session**. TDM verifies identity, inventory and campaign access before
   accepting it. The panel shows the accepted expiry. Keep the exported file private;
   it contains credentials, and must not be posted to issues or committed to Git.

The helper opens and closes its own tab without closing Chrome. It exports only a
matching Twitch request context whose integrity token has passed a live campaign query,
not every browser cookie. Export and server state files use owner-only permissions.
TDM saves accepted state separately in `data/imported-session.json`, revalidates it after
restart, rejects account changes and stale replacements, and waits for a fresh import
after expiry. Failed validation preserves the previous accepted context.

For the experimental server-renewal work, add `--server-seed "$HOME/tdm-server-seed.json"`
to the export command. This also saves the SDK cookie for `k.twitchcdn.net` alongside
the matching context in a separate private seed file. Import `tdm-session.json` into
the dashboard as before, then follow the [server helper setup](docs/server-renewal.md).
Both files contain credentials. If the local SDK cookie is absent or expired, the helper
obtains a new one in an empty temporary browser context, validates the same Twitch
account and catalog, and disposes that context before export. Your signed-in profile is
preserved. The server helper is experimental; normal renewal, original SDK-cookie expiry,
restart persistence and the initial-export handoff have passed on the tested home setup.

**Live manual-path check (25 September 2026):** the actual dashboard accepted an export
from the dedicated native Chrome profile into a fresh browser-free Docker TDM instance.
Identity, Inventory and Campaigns passed; a subsequent read-only query through the
imported provider returned 129 campaigns. The renewal helper subsequently delivered three distinct new integrity contexts,
and authenticated campaign access passed inside Docker. That initial check did not
establish Twitch-side mining progress with imported state; the later server-renewal
evidence distinguishes account progress from exclusive mining attribution.
See the [import and renewal evidence](docs/notes/2026-09-25-session-import-renewal.md).

#### Automatic renewal from the local browser (experimental)

**This is browser-assisted renewal, not autonomous server renewal.** It requires the
user's computer, signed-in browser and helper to remain available. It does not satisfy
the intended unattended deployment: export once, then turn off the user's computer.
With the exporting browser stopped, direct Alpine HTTP issuance returned new one-hour
tokens, but Twitch rejected their campaign queries. Replaying captured browser issuance
headers, including the browser SDK proof headers, also failed; the same test's imported
token successfully returned 152 campaigns. See the [server-only renewal investigation](docs/notes/2026-09-25-server-only-renewal.md).

A later [SDK cookie experiment](docs/notes/2026-09-25-sdk-cookie-renewal.md) has enabled
accepted renewal inside a headless server browser after one export. The separate
[server helper](docs/server-renewal.md) passed scheduled renewal and expiry/restart tests.
Use it when the local computer must be able to turn off. Multi-day reliability and other
desktop platforms remain unverified.

After a successful manual import, click **Download renewal connection** in the login
panel. Store `tdm-connection.json` privately on the computer running the dedicated Chrome
profile. Each download replaces the previous helper credential. It allows only session
renewal for the already accepted Twitch account, and does not grant dashboard access.

Run the helper from this source checkout on that local computer:

```bash
source env/bin/activate
chmod 600 "$HOME/Downloads/tdm-connection.json"
python -m src.auth.session_helper renew \
  --browser http://127.0.0.1:9222 \
  --connection "$HOME/Downloads/tdm-connection.json"
```

The helper captures and sends a fresh verified context immediately, then normally renews
five minutes before its observed expiry. Keep Chrome and this command running. It retries
transient browser/network failures with bounded delay. If the context expires or Twitch
rejects it, TDM waits for a new accepted context. If Twitch signs out the local profile,
sign in there again; the helper does not collect your password. A wrong-account session,
revoked credential, or redirect stops the helper with a fixed diagnostic code.

The connection file's destination must use HTTPS, except literal loopback HTTP for a
local instance or tunnel. For a reverse proxy set `PUBLIC_BASE_URL` to the exact public
HTTPS origin before downloading. No redirects are followed. Protect connection files as
credentials. **Disconnect helper** revokes future renewal uploads; the already accepted
Twitch context remains usable until expiry. Disabling dashboard protection also blocks
renewal. Enabling it again does not revoke the saved helper credential; disconnect or
replace the connection if you want to invalidate it. If the helper exited with
`SESSION_PAIRING` while protection was disabled, restart the command after reenabling.

**Live renewal check (25 September 2026):** the helper loop automatically advanced the
browser-free Docker provider through three replacements with distinct integrity tokens.
The destination validated each replacement's identity, inventory and campaigns; an
independent provider query using the first automatic replacement returned 129 campaigns.
The test used `--renew-before 3550` to observe successive renewals about 51 seconds apart.
That local-browser test did not exercise a complete default cycle. The separate server
helper later passed its normal cycle and protected requests after expiry, as recorded
above. Cross-network behavior and browser sign-out recovery remain unverified, and
account progress is not exclusive mining attribution. These source features are unreleased.

#### Desktop Chrome attachment (experimental)

On a machine with a desktop, TDM can attach to a dedicated, normally launched Chrome
profile. The browser must remain running on that machine while TDM uses it. This has
been checked on macOS ARM64; other desktop platforms and long-running renewal through
this direct ChromeDriver path remain unverified.
Install a [ChromeDriver matching your Chrome build](https://developer.chrome.com/docs/chromedriver/downloads/version-selection).
Keep both debugging and driver ports on loopback. Use a dedicated profile, not your
everyday Chrome profile. Do not expose either control port to other machines.

For example, on macOS, launch the browser from the repository directory:

```bash
mkdir -p data/native-browser
chmod 700 data/native-browser
open -na 'Google Chrome' --args \
  --user-data-dir="$PWD/data/native-browser" \
  --remote-debugging-port=9222 --no-first-run \
  https://www.twitch.tv/drops/campaigns
```

Run ChromeDriver in another terminal, with its executable on `PATH`:

```bash
chromedriver --port=9515 --allowed-ips=127.0.0.1 --log-level=OFF
```

Then run TDM from its activated source environment:

```bash
source env/bin/activate
unset TDM_BROWSER_VIEWER_URL
TDM_BROWSER_URL=http://127.0.0.1:9515 \
TDM_BROWSER_DEBUGGER_ADDRESS=127.0.0.1:9222 python main.py
```

Complete login and any verification in the **dedicated TDM Chrome window**. A login in
another Chrome window does not authenticate this profile. The dashboard shows a desktop
login prompt without a viewer link. Chrome retains the session in `data/native-browser`;
TDM keeps Android cookies separate and waits for Twitch's integrity-bearing request
context before validating campaign access. On restart, launch the same profile and
ChromeDriver again if they have stopped. Close TDM before closing its browser. A remote
dashboard does not provide remote control of this desktop window.

#### Docker browser experiment (login currently rejected)

To reproduce the Docker experiment, create a private, ignored `.env` file in the repository
root containing a unique viewer password (VNC uses only its first eight characters):

```dotenv
TDM_BROWSER_VNC_PASSWORD=replace-with-a-unique-password
```

```bash
chmod 600 .env
docker compose -f docker-compose.yml -f docker-compose.browser.yml up -d --build
```

Open the TDM dashboard, then **Open Twitch login browser**, or visit
<http://localhost:7900/vnc.html>. Connect with your viewer password and complete Twitch
login. The viewer is separate from dashboard authentication and is bound to loopback;
WebDriver is accessible only on the Compose network. On a remote Docker host, forward
the viewer with `ssh -L 7900:127.0.0.1:7900 user@docker-host` and use the local URL.
Do not publish ports 4444 or 5900, or expose the browser viewer directly to the internet.
A viewer can access your signed-in Twitch account.

The `browser-profile` volume holds sensitive Twitch session data, separately from
`data/cookies.jar`. Preserve both when restarting or upgrading; `docker compose down -v`
deletes the browser profile. One miner owns one browser/profile. TDM closes its browser
session on normal shutdown and reuses the profile next time; after an interrupted miner
process it can reconnect using `data/browser-session.json`. If the browser itself crashes,
restart the browser and miner. Do not delete a profile simply because login failed.

For a source-run miner using a browser with a remote viewer, point `TDM_BROWSER_URL` at
a private WebDriver endpoint and set `TDM_BROWSER_VIEWER_URL` to its HTTP(S) viewer URL.
Desktop attachment instead uses the debugger-address configuration above without a viewer.
The Python service currently supports Chrome's network-event API; Firefox is not yet an
implemented backend. Browser requests use the browser's own network connection, so TDM's
HTTP proxy setting does not configure Chrome; configure the browser network separately.

`BROWSER_DRIVER` means the driver could not start/respond; `BROWSER_REQUEST` means Twitch
did not return usable JSON over the browser transport; `BROWSER_CATALOG` means login
could not establish inventory and campaign access. `BROWSER_SESSION_CHANGED` stops
requests if the interactive browser logs out or switches accounts; restart TDM after
restoring the intended account. `BROWSER_ACCOUNT_MISMATCH` means the browser account
differs from the account identified by a still-valid saved token; sign into that account
in the browser. These errors do not imply that a campaign has ended or
that an account is linked. Browser login times out after 15 minutes; restart TDM to retry.
Close/SIGTERM interrupts pending login and closes the owned session. If a browser is
still being allocated, cleanup can wait up to 65 seconds for the driver to return its ID.

### From source

Source installations require Python 3.12 or newer and
[`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
uv run main.py
```

Then open <http://localhost:8080>.

## Using the web app

1. Existing valid Android sessions are restored automatically. Fresh login is currently
   affected by the [Twitch login outage](https://github.com/rangermix/TwitchDropsMiner/issues/118).
2. Wait for the miner to discover available campaigns.
3. Choose the games you want to prioritize. You can also search for a game, select
   **Add Game**, and then select **Reload**.
4. Leave the miner running while it selects eligible channels and tracks drop progress.

The Smart TV device-flow workaround in v1.3.1/v1.3.2 did not restore full campaign
discovery. Do not discard a working Android session to repeat that authorization.
See [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118) for the current
login status. Channel pages still use the public Twitch website to discover the
watch-event endpoint.

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

**Remote access to your home-hosted instance:** use HTTPS through a reverse proxy to encrypt passwords and cookies.
Set the miner's `PUBLIC_BASE_URL` environment variable to the exact address you open in
your browser, for example `PUBLIC_BASE_URL=https://drops.example.com`. The included
Compose file has a commented example; uncomment it, replace the hostname, and recreate
the container with `docker compose up -d --build` after updating the source.

The setting accepts one absolute `http://` or `https://` root URL with an optional port
and trailing slash. Credentials, subpaths, query strings, fragments, wildcard hosts, and
multiple URLs are rejected at startup. Use a hostname, dotted-decimal IPv4 address, or
bracketed IPv6 address; legacy short/octal/hexadecimal IPv4 forms are rejected. The setting
controls the allowed origin for API writes and Socket.IO connections. HTTPS public URLs
give session cookies the Secure flag even
when the proxy connects to the miner over HTTP or rewrites Host. Continue opening the
dashboard at that configured URL; browser writes/connections from another address are
rejected. It does not provide TLS or add support for hosting under a subpath.

Leaving `PUBLIC_BASE_URL` unset or empty keeps request-derived origin and cookie behavior.
For that setup, preserve the original Host header and configure Uvicorn to trust forwarded
protocol/IP headers **only from your proxy**, for example with `FORWARDED_ALLOW_IPS` set to
its exact IP or dedicated proxy subnet. Do not use `*` as a default. `PUBLIC_BASE_URL` does
not trust forwarded headers or restore client IPs: a proxy that hides them shares the
per-IP login limit unless client-IP forwarding is separately configured with trusted peers.
Keep `X-TDM-Request: 1` on API writes; Socket.IO does not require that marker.
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

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for issue reporting, development setup,
pull requests, required unit and regression checks, and independent adversarial review.
Coding agents must follow the mandatory workflow in [AGENTS.md](./AGENTS.md), also
available through the `CLAUDE.md` and `GEMINI.md` symlinks. The pull request template
records validation and review evidence.

Dashboard session-expiry tests use a controlled clock and scheduled callbacks to check
idle socket disconnection without depending on short wall-clock sleeps.

## Contributors

Contributors are credited automatically when their pull requests are merged into `main`.

<!-- contributors:start -->
| Contributor | Merged pull requests |
| --- | --- |
| [@3lb0z0](https://github.com/3lb0z0) | [#110](https://github.com/rangermix/TwitchDropsMiner/pull/110) |
| [@birdhimself](https://github.com/birdhimself) | [#41](https://github.com/rangermix/TwitchDropsMiner/pull/41) |
| [@capkz](https://github.com/capkz) | [#70](https://github.com/rangermix/TwitchDropsMiner/pull/70) |
| [@EthanBlazkowicz](https://github.com/EthanBlazkowicz) | [#33](https://github.com/rangermix/TwitchDropsMiner/pull/33) |
| [@Klages](https://github.com/Klages) | [#94](https://github.com/rangermix/TwitchDropsMiner/pull/94) · [#95](https://github.com/rangermix/TwitchDropsMiner/pull/95) |
| [@Knight-sys](https://github.com/Knight-sys) | [#3](https://github.com/rangermix/TwitchDropsMiner/pull/3) |
| [@rangermix](https://github.com/rangermix) | [#1](https://github.com/rangermix/TwitchDropsMiner/pull/1) · [#2](https://github.com/rangermix/TwitchDropsMiner/pull/2) · [#7](https://github.com/rangermix/TwitchDropsMiner/pull/7) · [#8](https://github.com/rangermix/TwitchDropsMiner/pull/8) · [#9](https://github.com/rangermix/TwitchDropsMiner/pull/9) · [#13](https://github.com/rangermix/TwitchDropsMiner/pull/13) · [#20](https://github.com/rangermix/TwitchDropsMiner/pull/20) · [#24](https://github.com/rangermix/TwitchDropsMiner/pull/24) · [#29](https://github.com/rangermix/TwitchDropsMiner/pull/29) · [#32](https://github.com/rangermix/TwitchDropsMiner/pull/32) · [#45](https://github.com/rangermix/TwitchDropsMiner/pull/45) · [#74](https://github.com/rangermix/TwitchDropsMiner/pull/74) · [#79](https://github.com/rangermix/TwitchDropsMiner/pull/79) · [#80](https://github.com/rangermix/TwitchDropsMiner/pull/80) · [#84](https://github.com/rangermix/TwitchDropsMiner/pull/84) · [#86](https://github.com/rangermix/TwitchDropsMiner/pull/86) · [#88](https://github.com/rangermix/TwitchDropsMiner/pull/88) · [#93](https://github.com/rangermix/TwitchDropsMiner/pull/93) · [#89](https://github.com/rangermix/TwitchDropsMiner/pull/89) · [#90](https://github.com/rangermix/TwitchDropsMiner/pull/90) · [#91](https://github.com/rangermix/TwitchDropsMiner/pull/91) · [#92](https://github.com/rangermix/TwitchDropsMiner/pull/92) · [#104](https://github.com/rangermix/TwitchDropsMiner/pull/104) · [#105](https://github.com/rangermix/TwitchDropsMiner/pull/105) · [#116](https://github.com/rangermix/TwitchDropsMiner/pull/116) · [#119](https://github.com/rangermix/TwitchDropsMiner/pull/119) · [#120](https://github.com/rangermix/TwitchDropsMiner/pull/120) |
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
