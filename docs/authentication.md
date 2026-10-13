# Twitch login

TDM shows an interactive Chromium browser inside its dashboard by default when no valid
Twitch session is available. It runs in the miner’s Docker container. An optional
[desktop helper](#desktop-helper-fallback) lets you sign in on your own computer if
Twitch rejects the embedded browser. Both paths use the dashboard port and the same
server-side session verification and automatic renewal.

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

Use the published `rangermix/twitch-drops-miner:2.1.1` image or build the current source.
The image includes Chromium, Xvfb, a window manager and noVNC. It does not need a
display on the home server or NAS. Only the dashboard port is published; VNC and
browser control remain on container loopback addresses.

Set `TZ` to match the timezone of your home internet connection, for example
`TZ=Australia/Sydney`. A mismatched timezone can cause Twitch to reject the browser.
Keep the host clock correct. If the miner uses a proxy, the interactive browser uses
the container’s network; use consistent network routing for login and mining.

## Desktop helper fallback

The optional desktop helper is available in v2.1.1. It opens a temporary browser on
your computer for Twitch sign-in, then sends the verified session to your miner.
The embedded browser remains the default, and TDM still verifies the account and
renewal on the miner server. Twitch can reject either browser; this fallback does
not guarantee successful sign-in.

Download the helper matching your **miner version** from the sign-in screen or its
[GitHub release](https://github.com/rangermix/TwitchDropsMiner/releases/tag/v2.1.1).
Choose the platform of the computer where you will run the helper, which may differ
from your miner server:

| Computer | v2.1.1 archive |
| --- | --- |
| Windows x64 | `tdm-login-helper-2.1.1-windows-x64.tar.gz` |
| Linux x64 | `tdm-login-helper-2.1.1-linux-x64.tar.gz` |
| macOS Apple silicon (arm64) | `tdm-login-helper-2.1.1-macos-arm64.tar.gz` |
| macOS Intel (x64) | `tdm-login-helper-2.1.1-macos-x64.tar.gz` |

Extract the archive to a folder before running it. Install a native Chrome, Chromium,
or Firefox 143+ browser on that computer; the helper does not include a browser.

1. On the sign-in screen, choose **Use desktop helper**. This closes the embedded
   browser and opens a ten-minute window for one desktop helper to connect.
2. Within ten minutes, run `tdm-login-helper.exe` on Windows or `./tdm-login-helper`
   from the extracted folder in a Linux/macOS terminal. Enter the dashboard address
   shown by TDM, such as `http://192.168.1.10:8080`. The helper asks only for this URL;
   there is no pairing code or dashboard-password prompt. `localhost` works only
   when the helper and miner run on the same computer.
3. Complete Twitch sign-in and verification in the helper-opened browser. Close all
   windows of that browser instance normally after Twitch confirms the login; on
   macOS, quit that instance. Keep the helper running while it reopens its temporary
   profile, verifies the session, and sends it directly to the selected miner.
4. Wait for the helper's success message and the normal dashboard. The helper and
   desktop computer are no longer needed for automatic renewal.

Only enable helper access when you are ready to connect from a trusted home network.
The first helper that reaches the miner during that window is admitted; later
connections are rejected. The connection expires after ten minutes and is revoked
by cancellation, logout, or restarting TDM. **Return to embedded browser** revokes
desktop access. If the helper fails before completing login, return to the embedded
browser and select **Use desktop helper** again to open a new window.
If the helper cannot determine whether its upload succeeded, check
the dashboard before starting over; it does not repeat a credential upload automatically.

For a source checkout, activate the project environment and run:

```bash
python login_helper.py --tdm http://192.168.1.10:8080
```

The helper connects using the dashboard address. `--browser chrome`,
`--browser chromium`, or `--browser firefox` selects a browser; automatic selection
tries them in that order. `--chrome`, `--chromium`, and `--firefox` accept an installed
native executable path. Flatpak and Snap browser launchers are not supported. The source
environment setup is in [CONTRIBUTING.md](../CONTRIBUTING.md#development-setup).

The release's `SHA256SUMS` file lists checksums for all four helper archives. Use the
same dashboard address you normally trust, on your home network or over HTTPS with
a valid certificate. Your everyday browser profiles are untouched; temporary helper
profiles are removed when the helper finishes. Keep any profile left after a crash
private, and never copy cookies or authentication files into the dashboard.

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

A source installation using the embedded browser additionally needs Linux, a root miner process with a separate
`tdm-browser` system user, `chromium`, `Xvfb`, `openbox`, `x11vnc` and `xdotool`, noVNC
at `/usr/share/novnc`, and timezone data. The desktop helper is an alternative for
interactive sign-in, but the miner host still needs Chromium for server-side
verification and renewal. See [source installation](installation.md#run-from-source).

[All guides](README.md) · [Next: Games and drops](usage.md)
