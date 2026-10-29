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
3. The browser closes automatically after Twitch sets its login cookie, whether scoped
   to `www.twitch.tv` or `.twitch.tv`. If it stays
   open after you finish verification, select **Finish sign in** or close its X button.
4. Wait while TDM verifies the account, campaign access and renewable session. The
   normal dashboard appears automatically after verification and browser cleanup.

An attempt lasts up to 15 minutes. **Retry** starts a new browser after failure, or
reconnects a closed viewer. Do not close the browser before Twitch verification is done.
Sessions and integrity proofs are checked locally against Twitch before acceptance.

The temporary browser has password saving disabled. Open **Copy and paste** to paste
text into its focused field, or open the panel before copying text inside Twitch and
then copy the resulting text from the panel. This works on home-network HTTP dashboards
without requiring browser clipboard permission. Clipboard text is cleared after sending,
when the panel closes, and when the viewer disconnects; it is never saved by TDM.

`CATALOG` means Twitch rejected inventory or campaign validation. Safe container-log
entries beginning with `SESSION_DIAGNOSTIC` distinguish the validation stage, operation
and cause. For example, `issued_validation` with `Campaigns` and `integrity` means the
server-issued proof was rejected after browser login. Retrying the Finish button cannot
repair that proof. Keep the container clock/timezone and network routing consistent;
report only the fixed diagnostic labels, never session files or complete logs.

The Docker image runs the server verification/renewal browser on its own private
virtual display, separate from the sign-in viewer. Older headless renewal builds can
fail campaign integrity validation even after a successful browser login. Updating to
a build containing this correction can recover an existing renewable session without
another sign-in; TDM still verifies the account and campaign access before accepting it.

A Linux source installation without `xvfb-run` still uses the headless fallback. The
miner logs a warning to install `xvfb` and `xauth` each time it starts that browser,
and reports an integrity rejection of its proof as reason `headless_fallback` instead
of `integrity`. Install both packages, or use Docker. Automatic renewal retries pick up
the change; a failed sign-in needs a new attempt.

## Docker and timezone

Use the `rangermix/twitch-drops-miner:2.4.0` image or build the current source.
The image includes Chromium, Xvfb, a window manager and noVNC. It does not need a
display on the home server or NAS. Only the dashboard port is published; VNC and
login-browser control remain on container loopback addresses. The 2.4.0 Docker image
and Linux/macOS source builds control the session-renewal browser over a private
pipe. Windows source builds use loopback; the older 2.3.2 image also uses a loopback
control port.

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
[GitHub release](https://github.com/rangermix/TwitchDropsMiner/releases/tag/v2.4.0).
Choose the platform of the computer where you will run the helper, which may differ
from your miner server:

| Computer | v2.4.0 archive |
| --- | --- |
| Windows x64 | `tdm-login-helper-2.4.0-windows-x64.tar.gz` |
| Linux x64 | `tdm-login-helper-2.4.0-linux-x64.tar.gz` |
| macOS Apple silicon (arm64) | `tdm-login-helper-2.4.0-macos-arm64.tar.gz` |
| macOS Intel (x64) | `tdm-login-helper-2.4.0-macos-x64.tar.gz` |

Extract the archive to a folder before running it. Install a native Chrome, Chromium,
or Firefox 143+ browser on that computer; the helper does not include a browser.

1. On the sign-in screen, choose **Use desktop helper**. This closes the embedded
   browser and opens a ten-minute window for one desktop helper to connect.
2. Within ten minutes, run `tdm-login-helper.exe` on Windows or `./tdm-login-helper`
   from the extracted folder in a Linux/macOS terminal. Enter the dashboard address
   shown by TDM, such as `http://192.168.1.10:8080`. The helper asks only for this URL;
   there is no pairing code or dashboard-password prompt. `localhost` works only
   when the helper and miner run on the same computer. A dotted host name such as
   `nas.lan` is rejected unless it is an
   [allowed host name](dashboard-access.md#allowed-host-names); use the IP address
   or add the name to `ALLOWED_HOSTS`.
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

### Helper HTTPS certificates

The helper verifies HTTPS certificates and hostnames by default using its computer's
trust store. For a self-signed reverse proxy, give the certificate to the helper with
`--ca-file`, or trust the certificate or its issuing CA on the computer running the
helper. Use a hostname covered by that certificate. Adding a CA only to the miner
container does not change the desktop helper's trust.

#### Trust one certificate with `--ca-file`

**Unreleased:** source helpers support `--ca-file`; the v2.4.0 downloads do not. Use
it instead of trusting the certificate system-wide:

```bash
python login_helper.py --tdm https://192.168.1.10:8443 --ca-file tdm-proxy.pem
```

A matching packaged helper takes the same options (`tdm-login-helper.exe` on Windows,
`./tdm-login-helper` on Linux or macOS). The helper then trusts your operating
system's certificates plus that file, for its connection to TDM only. It still checks
the certificate chain, its validity dates, and that the certificate names the
dashboard host (Subject Alternative Name). The file must be PEM and contain a CA
certificate (`CA:TRUE`): the self-signed proxy certificate itself, or the root CA that
signed it. Never pass a private key. Convert a DER `.cer` file with
`openssl x509 -inform der -in proxy.cer -out tdm-proxy.pem`. A server-only
certificate (`CA:FALSE`) fails with `SESSION_HELPER_CA_FILE`, because Windows and macOS
ignore it. The helper prints only that a custom certificate file is in use, not its
path or contents.

macOS also applies Apple's rules for TLS server certificates: the `serverAuth`
extended key usage, an RSA key of at least 2048 bits (or an EC key) and a validity of
at most 825 days. A plain `openssl req -x509` certificate lacks `serverAuth`. This
command creates a proxy certificate that works on all helper platforms; replace the
IP address with your dashboard host (`DNS:nas.home.arpa` for a name), then configure
the proxy with `tdm-proxy.pem` and `tdm-proxy.key`. It needs OpenSSL 1.1.1 or newer,
for example on the proxy host:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj /CN=tdm \
  -addext subjectAltName=IP:192.168.1.10 -addext extendedKeyUsage=serverAuth \
  -addext basicConstraints=critical,CA:TRUE -keyout tdm-proxy.key -out tdm-proxy.pem
```

#### Skip verification with `--insecure`

Prefer `--ca-file`; the two options cannot be combined.

**Unreleased:** source helpers support `--insecure` as an explicit opt-out. The v2.4.0
downloads do not support this option; packaged commands below require a later release
that includes it, matching your miner version. From an activated source environment:

```bash
python login_helper.py --tdm https://tdm.example.home.arpa --insecure
```

For a matching packaged helper, run from the extracted folder:

```powershell
# Windows PowerShell
.\tdm-login-helper.exe --tdm https://tdm.example.home.arpa --insecure
```

```bash
# Linux or macOS
./tdm-login-helper --tdm https://tdm.example.home.arpa --insecure
```

**Warning:** `--insecure` disables certificate and hostname verification for the
helper's connection to TDM. An attacker between the helper and miner could impersonate
the miner and steal your Twitch session, even on a LAN or VPN. Use it only when you
accept that risk on a trusted network; a trusted, valid certificate is preferable.
The helper displays a warning before connecting whenever this option is enabled.

The option lasts for that run only and is never enabled automatically after a TLS
error. Omit it on the next run to restore verification. It does not change HTTPS
verification in the browser, Twitch requests, or the miner, and it does not follow
redirects or bypass helper admission or server-side session verification.

`--insecure` is limited to dashboards on your own network. It works only with an
`https://` address whose host name resolves only to private or loopback addresses:
`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `::1`, `fc00::/7` or
`fe80::/10`. Otherwise the helper stops with `SESSION_HELPER_INSECURE_TLS` before
connecting. It then connects only to the addresses it checked, so a DNS change during
login cannot send the connection elsewhere. A host name that also has a public IPv6
address is refused too; use the dashboard's private IP address instead.

Carrier-grade NAT addresses (`100.64.0.0/10`) are not accepted, because other
customers of the same internet provider share that range. This also refuses Tailscale
`100.x` addresses and MagicDNS names, which resolve to both a `100.x` and an IPv6
address. Tailscale already encrypts traffic between your devices; use `--ca-file` or
a certificate from `tailscale cert` instead.

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

In **Settings → Account & Maintenance**, select **Log out of Twitch** and confirm. TDM drains
authenticated work, clears its saved session and legacy cookies, and opens a fresh
Twitch sign in page. Games, drop history and other settings are preserved.
This logs out this miner; it does not sign you out of your everyday Twitch browser.
Logout persists across restarts, including when deleting an old cookie file fails.
If logout reports a storage error, check the current session before retrying.

## Automatic renewal

The miner renews the accepted context using a separate temporary Chromium browser,
with a private virtual display when available (`xvfb-run`) and a headless fallback
otherwise. Twitch can reject proofs issued by the headless fallback.
It validates the same account before saving. Mining requests remain in Python;
the interactive browser does not remain running after login.
Transient failures are retried. Expired or revoked access may require a new sign in,
which appears automatically when TDM can no longer use the saved session.

The session and renewal state in `data/imported-session.json` are credentials. Keep
the data mount private and back it up only while the miner is stopped. Run one miner
per data directory. Graceful shutdown closes owned browser processes and removes
their temporary profiles; an abrupt host crash cannot guarantee profile cleanup.

## Dashboard access and source installations

Expand **Dashboard password** on the sign in screen to configure protection before
entering Twitch credentials. The same controls remain in **Settings → Account &
Maintenance** after sign in.

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

### Clipboard and existing-session sign in

The embedded viewer includes the native noVNC sidebar. Open its Clipboard panel,
paste text, return to the Twitch field, and press Ctrl+V. Clipboard content is
limited to 65,536 characters and cleared after sending, closing, or disconnecting.

For an existing login on your own computer, expand **Use an existing Twitch session**.
In your browser's developer tools, open Storage/Application → Cookies → Twitch and
copy only the `auth-token` value. Paste it into the password-style field and select
**Sign in with token**. This is an advanced alternative: treat that value as a
password and never send it to anyone. TDM validates the WEB account, creates fresh
server proof, and validates its account and campaign catalog before saving. Rejected
or expired tokens cannot sign in. The old device-code flow used a different Twitch
client and is not restored by this option.

After downtime, an expired SDK seed can be recreated using a still-valid saved OAuth
identity. Account and catalog validation remain mandatory. Network errors or Twitch
rejection can still require retrying or signing in again.
