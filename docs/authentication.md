# Twitch login

TDM shows an interactive Chromium browser inside its dashboard when no valid Twitch
session is available. The browser runs in the miner’s Docker container. No desktop
helper, local browser installation, additional port, or session upload is needed.

## Sign in

1. Open the TDM dashboard. If dashboard password protection is enabled, unlock it first.
2. Sign in to Twitch in the browser shown there and complete email verification or
   two-factor authentication as requested by Twitch.
3. After Twitch confirms you are signed in, select **Finish sign in**. Closing the
   Chromium window with its X button also finishes the interactive step.
4. Wait while TDM verifies the account, campaign access and renewable session. The
   normal dashboard appears automatically after verification and browser cleanup.

An attempt lasts up to 15 minutes. **Retry** starts a new browser after failure, or
reconnects a closed viewer. Do not close the browser before Twitch verification is done.
Sessions and integrity proofs are checked locally against Twitch before acceptance.

## Docker and timezone

Use the published `rangermix/twitch-drops-miner:2.1.0` image or build the current source.
The image includes Chromium, Xvfb, a window manager and noVNC. It does not need a
display on the home server or NAS. Only the dashboard port is published; VNC and
browser control remain on container loopback addresses.

Set `TZ` to match the timezone of your home internet connection, for example
`TZ=Australia/Sydney`. A mismatched timezone can cause Twitch to reject the browser.
Keep the host clock correct. If the miner uses a proxy, the interactive browser uses
the container’s network; use consistent network routing for login and mining.

## Existing installations

Keep the same private `data` directory across updates. Valid saved Android logins and
accepted browser sessions are reused. The existing `imported-session.json` formats
are read automatically; an accepted session or logout is saved in the new format.
Expired or rejected credentials show the integrated sign in browser.
Smart TV logins from v1.3.1/v1.3.2 require a new login.

Older imported sessions without renewal state remain usable until a new login is
required. Sign in again to enable automatic renewal. Never delete the whole data
directory to recover authentication.

## Log out and change account

At the bottom of **Settings**, select **Log out of Twitch** and confirm. TDM drains
authenticated work, clears its saved session and legacy cookies, and opens a fresh
Twitch sign in page. Games, drop history and other settings are preserved.
This logs out this miner; it does not sign you out of your everyday Twitch browser.
Logout persists across restarts, including when deleting an old cookie file fails.
If logout reports a storage error, check the current session before retrying.

## Automatic renewal

The miner renews the accepted context using a separate temporary headless Chromium
browser and validates the same account before saving. Mining requests remain in
Python; the interactive browser does not remain running after login.
Transient failures are retried. Expired or revoked access may require a new sign in,
which appears automatically when TDM can no longer use the saved session.

The session and renewal state in `data/imported-session.json` are credentials. Keep
the data mount private and back it up only while the miner is stopped. Run one miner
per data directory. Graceful shutdown closes owned browser processes and removes
their temporary profiles; an abrupt host crash cannot guarantee profile cleanup.

## Dashboard access and source installations

Expand **Dashboard password** on the sign in screen to configure protection before
entering Twitch credentials. The same controls remain in Settings after sign in.

The viewer and login actions use the dashboard’s access controls, including optional
password protection, same-origin checks and session expiry. Protect access to the
dashboard on shared networks. A reverse proxy must support WebSocket upgrades for
`/api/session/vnc` as well as Socket.IO. See [Dashboard access](dashboard-access.md).

Docker is recommended for new login. The container runs the interactive desktop as
a separate unprivileged user, protects the miner’s data and logs with private directory
permissions, and omits miner secrets from browser environment variables. If a bind
mount cannot enforce private permissions, login fails with `BROWSER_ISOLATION`; use
a Linux filesystem or a Docker named volume for data/logs.

A source installation additionally needs Linux, a root miner process with a separate
`tdm-browser` system user, `chromium`, `Xvfb`, `openbox`, `x11vnc` and `xdotool`, noVNC
at `/usr/share/novnc`, and timezone data. Ordinary source processes can restore saved
sessions, but use Docker for new interactive login.

[All guides](README.md) · [Next: Games and drops](usage.md)
