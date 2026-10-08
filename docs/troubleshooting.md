# Troubleshooting

Start with the status and console output on the **Main** tab. Compare progress with
your [Twitch inventory](https://www.twitch.tv/drops/inventory), since a dashboard
display and Twitch's recorded progress can differ.

## The login browser does not open or connect

- Use the current published Docker image or source build with the integrated browser. Check container resources
  and use the supplied init process, 30-second stop grace period and shared memory size.
- Set `TZ` to the timezone of your home internet connection. A mismatch can produce
  Twitch’s “browser not supported” message. Check the host clock as well.
- Complete login and any email/2FA verification before selecting **Finish sign in**.
  Selecting it too early produces `LOGIN_MISSING`; use **Retry** for a fresh attempt.
- A closed viewer can be reconnected with **Retry**. Reverse proxies must forward
  WebSocket upgrades for `/api/session/vnc`. Unlock a password-protected dashboard first.
- `BROWSER_UNAVAILABLE` means required Linux browser/display tools are missing.
  Use Docker or install the dependencies in the [login guide](authentication.md).
- `BROWSER_ISOLATION` means the browser user cannot be separated from the miner or
  the data/log mount ignores private permissions. Use the unmodified Docker image
  with a Linux filesystem or Docker named volumes.
- `BROWSER_START`, `BROWSER_FAILED` or `BROWSER_STOP` indicates a browser/display or
  cleanup failure. Check memory, process limits, permissions and timestamped logs.
- `LOGIN_TIMEOUT` means the 15-minute interactive step expired. Start a fresh attempt.
- `AUTH`, `IDENTITY`, `CATALOG` or `ACCOUNT_MISMATCH` means Twitch validation failed.
  Keep the same account throughout the attempt, check Twitch Drops access and retry.
- `CAPTURE_TIMEOUT`, `SDK_TIMEOUT` and other `SDK_*` errors can require a new login or
  recovery from a Twitch/network outage. Do not edit saved credential files.

The dashboard’s connection indicator confirms access to TDM, not Twitch login.
A cleanup error can occur after a session was saved; check the current login before
retrying. Report the fixed code, image/source version and redacted logs, never the
browser profile, session file, password, verification code or OAuth headers.

## Twitch says the browser is not supported

If the message appears inside a working embedded browser, Twitch is rejecting that
browser's sign-in. Changing the browser used to view the dashboard does not change
the Chromium browser running inside the container.

After checking the timezone and host clock, you can select **Use desktop helper**
on the sign-in screen and use a native browser on your own computer. Update the miner
and download its matching Windows, Linux, or macOS helper. Follow the
[desktop helper steps](authentication.md#desktop-helper-fallback); the helper needs
only the dashboard URL. Access opens for ten minutes and accepts the first helper.
Select **Return to embedded browser** to cancel it or start over after expiry.

The helper is an alternative sign-in path, not a guarantee that Twitch will accept
the login. If it cannot connect to TDM, check that its URL is reachable from the
helper computer and that the access window is still open. `localhost` points to the
helper computer itself. If the helper reports an unknown result, check the dashboard
before trying again. Keep saved credentials and browser profiles private.

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

Preserve the data directory and `cookies.jar`. Valid Android and browser sessions
are restored. Smart TV sessions from v1.3.1/v1.3.2 and expired sessions require the
[dashboard browser](authentication.md). Deleting all data is unnecessary.

## Renewal is retrying or asks for another login

Temporary renewal failures are retried automatically. If TDM shows the sign in
browser, complete login and verification again. Revoked access or credentials that
expired during a long outage cannot always be renewed. Keep the persistent data mount
and check Chromium availability, timezone and network connectivity.

The interactive browser closes after verification. Only the dashboard port is
needed for login and renewal. See [Automatic renewal](authentication.md#automatic-renewal).

## No campaigns or no progress

If TDM 2.2.0 says it is watching but Twitch inventory does not advance, update to
2.2.1 or newer while preserving your data directory. The patch restores segment watch
requests without downloading stream audio or video. A Watching label or accepted
telemetry alone does not establish progress; compare with Twitch inventory.

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
