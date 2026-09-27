# Troubleshooting

Start with the status and console output on the **Main** tab. Compare progress with
your [Twitch inventory](https://www.twitch.tv/drops/inventory), since a dashboard
display and Twitch's recorded progress can differ.

## The helper cannot connect or open Chrome

- Run the helper in a local session on your desktop or laptop with a display.
  An SSH terminal connected to your headless server runs it on the server. Follow
  the [headless home server instructions](authentication.md#headless-home-server-or-nas)
  and choose the helper for your desktop's operating system and CPU.
- Use the TDM root address reachable from the helper desktop. `localhost` refers to
  that desktop, not another computer running the miner.
- Enable **Settings → Allow helper connection** before starting the helper. A
  successful login turns it off automatically; turning it off also invalidates
  outstanding helper connections.
- Use a helper matching your TDM version or source revision. Older releases may not
  provide helper assets or support the current flow. Follow the
  [download guidance](authentication.md#download-the-helper).
- Install Google Chrome on the helper desktop. `--chrome` can select its local
  executable if it is not found automatically. `--tdm` only selects the miner's
  address; it does not select a remote Chrome process.
- On Linux, use native Chrome with `google-chrome` or `google-chrome-stable` on
  `PATH`, or pass its executable path with `--chrome`. Flatpak Chrome is not
  currently supported; `--chrome` cannot take a `flatpak run ...` command.
- If you use a reverse proxy, use its configured root URL and follow
  [Dashboard access](dashboard-access.md#reverse-proxy-and-https).

Sign into Twitch only in the Chrome window the helper opens.

## The helper reports an unknown result or cleanup error

The dashboard's top-right **Connected** indicator only confirms the dashboard's
connection to TDM. It does not confirm Twitch login. Check the Twitch login and
session status on the **Main** tab before opening helper access and trying again;
the server may already have accepted the session.

- `SESSION_HELPER_RESULT_UNKNOWN` means the helper could not confirm acceptance.
  Keep the saved data and check the miner's logs from the same attempt. In v2.0.0,
  failure to start the server's Chromium can also produce this code. The v2.0.1 helper
  reports that specific rejection as `SESSION_HELPER_SERVER_BROWSER`; other lost
  acknowledgements still recover through the result endpoint without uploading again.
- `SESSION_HELPER_SERVER_BROWSER` means Chromium could not start on the **miner
  host**. For a source installation, check that `chromium` or `chromium-browser` is
  installed on that process's `PATH`. For Docker, check the image tag and digest,
  available resources, and startup logs; the official image includes Chromium.
  Installing Chrome on the helper desktop does not provide the server browser.
- `SESSION_HELPER_PROFILE_CLEANUP` means the helper could not remove its temporary
  local Chrome profile. It does not establish whether TDM accepted the login.
  The v2.0.1 helper handles read-only files in an owned Windows profile and retries
  when a child file disappears during deletion. Persistent locks or permission
  failures still report an error. Keep remaining temporary profiles private.

The fixes above require the updated helper executable as well as the miner;
the v2.0.0 helper does not contain them. For a remaining problem, report the helper archive name, desktop
OS and Chrome version, miner installation and image tag/digest, exact error code,
and redacted miner logs with a timestamp. The helper uses HTTP requests to
`/api/helper/connect`, `/api/helper/session` and `/api/helper/result`; Socket.IO
connect/disconnect messages alone do not describe the login handoff.

## Startup fails while reading web_auth.json

A traceback from `WebAuth.__init__` ending in `JSONDecodeError` identifies malformed
local dashboard-password state in `data/web_auth.json`. This is separate from
`settings.json`, drop history and Twitch authentication. Startup deliberately stops
to avoid silently disabling password protection.

Stop the miner and restrict access to its dashboard port. Restore a known-good
private backup of `web_auth.json`, or follow the
[local password recovery procedure](dashboard-access.md#recover-a-forgotten-password)
to reset only dashboard protection. Keep any malformed backup private and preserve
`cookies.jar`, `imported-session.json` and all other data. Set a new dashboard
password before restoring remote access. Do not attach the authentication file to
an issue. If it happens again, report whether the file was edited or copied, any
interrupted writes or storage errors, and whether multiple miners share the data
directory.

## TDM needs a new login after an upgrade

Preserve your data directory and `cookies.jar`. Working Android sessions should be
restored without a new login. Smart TV sessions from v1.3.1/v1.3.2 and expired sessions
need the [login helper](authentication.md). Older manual imports also need a helper
login to enable automatic renewal. Deleting all data is not necessary.

## Renewal is retrying or asks for another login

A temporary renewal error is retried automatically. If the dashboard says a new
helper login is required, enable **Allow helper connection** and sign in again.
Revoked Twitch access or credentials that expired during a long outage cannot always
be renewed.

For source installations, check that `chromium` or `chromium-browser` is available
on the miner process's `PATH`. The packaged helper's desktop Chrome is separate.
The current Docker build includes the server browser. Keep the same persistent
data mount across container replacement so TDM retains its renewal state.

You do not need to leave the helper desktop running, leave helper access enabled,
or publish a browser-control port for renewal. See
[Automatic renewal](authentication.md#automatic-renewal).

## No campaigns or no progress

Check the campaign on Twitch and work through these conditions:

1. The Twitch account is linked to the correct game account and is eligible for the
   campaign.
2. The campaign and individual reward are active, and the reward is earned by
   watching rather than by subscribing or making a purchase.
3. The game is in **Games to Watch**, and its reward type is enabled in **Mining Benefits**.
4. Your ignored keywords do not exclude the reward or one of its prerequisites.
5. An eligible participating channel is live. Some campaigns work only on a fixed
   list of channels.
6. You are not watching Twitch manually with the same account at the same time.

Higher-priority games may take precedence. If **Manual Mode** is active, use
**Return to Auto Mode** to follow your normal game priorities. Upcoming or sequential
rewards in the queue may need to wait for their start time or earlier rewards.

For **Special Events** or **IRL**, keep the campaign's category in Games to Watch;
its participating channels may be streaming another category. See
[channel selection](usage.md#special-events-and-irl-campaigns).

Try **Reload** after correcting settings. **Clear All Cache** can rebuild local
campaign and channel data while keeping login, settings, and dashboard protection.
It cannot repair inaccurate campaign information supplied by Twitch.

## Dashboard writes or live updates fail behind a proxy

A page loading successfully does not mean its live connection or settings writes use
the expected origin. Set `PUBLIC_BASE_URL` to the exact browser-facing root URL,
including the correct scheme and any non-default port. Continue using that address.

Configure forwarded headers only for trusted proxy peers when needed. Do not set
`FORWARDED_ALLOW_IPS=*` as a general fix. Follow the full
[proxy instructions](dashboard-access.md#reverse-proxy-and-https).

For a temporary dashboard login request error, retry **Log in**. If rate limited,
wait a minute before retrying. A forgotten dashboard password has a separate
[local recovery procedure](dashboard-access.md#recover-a-forgotten-password);
clearing campaign cache does not reset it.

## Telegram alerts are missing

Use **Test Connection** in Telegram settings and check any error shown. Start a
conversation with the bot before testing, and verify the chat ID. Leave a saved token's
field blank to reuse it. Failed notifications are not retried and do not reverse a
successful drop claim. See [Telegram notifications](notifications.md).

## Logs and reporting a problem

For Docker, inspect console output with:

```bash
docker logs --tail 100 twitch-drops-miner
```

File logs are written to `logs/TDM.log`; the supplied Compose configuration persists
that directory. A custom `docker run` needs a `./logs:/app/logs` mount to retain those
files outside the container.

Before [opening an issue](https://github.com/rangermix/TwitchDropsMiner/issues), search
for an existing report and read [CONTRIBUTING.md](../CONTRIBUTING.md). Include the TDM
version or source revision, installation method, operating system, relevant browser,
steps, expected and actual results, and troubleshooting already attempted. For campaign
issues, include public campaign/channel identifiers and the time with timezone.

Share only relevant, redacted log excerpts. Never share `cookies.jar`,
`imported-session.json`, `web_auth.json`, a whole data directory, passwords, session
cookies, or Telegram bot tokens. Support is best-effort for personal installations on
your own hardware and home network.

[All guides](README.md)
