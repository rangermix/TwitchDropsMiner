# Troubleshooting

Start with the status and console output on the **Main** tab. Compare progress with
your [Twitch inventory](https://www.twitch.tv/drops/inventory), since a dashboard
display and Twitch's recorded progress can differ.

## The helper cannot connect or open Chrome

- Use the TDM root address reachable from the helper desktop. `localhost` refers to
  that desktop, not another computer running the miner.
- Enable **Settings → Allow helper connection** before starting the helper. A
  successful login turns it off automatically; turning it off also invalidates
  outstanding helper connections.
- Use a helper matching your TDM version or source revision. Older releases may not
  provide helper assets or support the current flow. Follow the
  [download guidance](authentication.md#download-the-helper).
- Install Google Chrome on the helper desktop. With the source helper, `--chrome`
  can select its executable if it is not found automatically.
- If you use a reverse proxy, use its configured root URL and follow
  [Dashboard access](dashboard-access.md#reverse-proxy-and-https).

Sign into Twitch only in the Chrome window the helper opens. If a connection problem
leaves the helper reporting an unknown result, check TDM's login status before opening
helper access and trying again. The server may already have accepted the session.

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
