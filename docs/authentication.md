# Twitch login

New Twitch logins use the local TDM login helper. It opens Google Chrome on your
desktop, lets you sign into Twitch, and sends the session directly to your chosen TDM
instance. TDM saves and renews it automatically. Your desktop can be shut down after
the helper confirms success; the miner itself must remain running.

## Headless home server or NAS

**Headless home servers are supported.** Run the miner and login helper on the
machines below:

| Machine | What runs there |
| --- | --- |
| Your home server or NAS | TDM mines drops and renews the session with temporary headless Chromium. No desktop or display is needed; the official Docker image includes Chromium. |
| Your desktop or laptop | The login helper opens installed Google Chrome for you to sign in, then sends the session to the server's TDM address. |

Choose the helper archive for your **desktop's operating system and CPU**, even if
the server uses a different platform. For example, use the Windows helper on your
Windows PC when TDM runs in Docker on a Linux NAS.

The helper finds and launches Chrome on the computer where you run it. `--tdm`
selects the destination miner; it does not move Chrome to that server. Use a local
terminal on your desktop: commands entered in an SSH session to the server run on
the server instead.

## Existing installations

Keep your existing data directory, including `cookies.jar`, when upgrading.

- **Working Android session:** TDM restores it automatically; no fresh login is needed.
- **Smart TV session from v1.3.1/v1.3.2, expired session, or signed out:** update TDM and
  use a matching login helper.
- **Older manually imported session:** use the helper when TDM asks for a new login
  to enable automatic renewal.

Manual JSON upload, device-code login, and the old exported session and pairing files
are no longer used for fresh login. An accepted helper login takes precedence over
preserved Android credentials, including after restart.

## Download the helper

Check your TDM version in the dashboard. For TDM 2.0.0 and later, choose the matching
native helper archive under **Assets** on its
[GitHub release](https://github.com/rangermix/TwitchDropsMiner/releases).

| Your desktop | Archive suffix |
| --- | --- |
| Windows x64 | `windows-x64.tar.gz` |
| macOS, Apple Silicon | `macos-arm64.tar.gz` |
| macOS, Intel | `macos-x64.tar.gz` |
| Linux x64, glibc (built on Ubuntu 22.04) | `linux-x64.tar.gz` |

Extract `tdm-login-helper-<version>-<platform>.tar.gz`. The archive contains the
executable and its license. The release's `SHA256SUMS` file lists checksums for the
archives. Google Chrome must be installed on this desktop; the packaged helper does
not require Python. The native binaries are unsigned.

On Linux, automatic desktop discovery checks `google-chrome` and
`google-chrome-stable` on `PATH`. Use a native Chrome installation; Flatpak Chrome
is not currently supported by the helper. `--chrome` accepts a local executable
path, not a command such as `flatpak run com.google.Chrome`. Firefox is not currently
supported for helper login; it can still open the TDM dashboard.

The v1.x releases do not provide this helper flow. For an unreleased source
installation, use the helper from the same source
revision. You can download the `tdm-login-helper-release` artifact from that revision's
successful [validation run](https://github.com/rangermix/TwitchDropsMiner/actions/workflows/validation.yml),
unzip the artifact, and extract your platform's archive. Alternatively,
[run the source helper](#run-the-helper-from-source). Do not use a new helper with an
older TDM version that lacks the helper flow.

## Sign in

1. Open your TDM dashboard. In **Settings**, enable **Allow helper connection**.
   It is enabled by default on a new instance.
2. On your desktop or laptop, run the extracted `tdm-login-helper`, or
   `tdm-login-helper.exe` on Windows. Run it in your local desktop session, outside
   the server's SSH session and TDM container.
3. Enter the TDM address shown on the dashboard's **Main** tab, such as
   `http://192.168.1.10:8080`. Use an address reachable from this desktop;
   `localhost` works only when TDM runs on the same computer.
4. Sign into Twitch in the Chrome window opened by the helper and complete any
   Twitch verification there. Wait for the helper to confirm successful acceptance.

You can also supply the address when starting the helper. From the folder containing
the extracted executable, run this in **PowerShell on your Windows desktop**:

```powershell
.\tdm-login-helper.exe --tdm http://192.168.1.10:8080
```

Or in a **local terminal on your macOS or Linux desktop**:

```bash
./tdm-login-helper --tdm http://192.168.1.10:8080
```

Replace the example address with the same TDM root URL you can open from that
desktop, including any custom port. The Chrome login window opens on that desktop.

The helper sends the session automatically; you do not export or upload a file.
After successful acceptance, TDM saves the session and turns **Allow helper connection**
off. The helper closes its Chrome window and deletes its temporary profile. It does
not change your everyday Chrome profile.

If the helper reports that the result is unknown after a connection problem, check
TDM's login status before trying again. A successful response may have been lost on
the network. Reopen helper access only if another login is needed.

## Allow helper connection and account replacement

Turn **Allow helper connection** on when you need to log in again or replace the
account, then repeat the same steps. Turning it off blocks new connections and
invalidates outstanding helper connections. It does not stop automatic renewal.

The setting controls helper access independently of the optional dashboard password.
Anyone who can reach the helper endpoints while it is enabled can attempt to install
or replace the Twitch account. Enable it only when you intend to connect a helper.
A helper connection cannot reveal existing credentials or unlock a protected dashboard.
Use the configured HTTPS address when connecting across an untrusted network; see
[Dashboard access](dashboard-access.md#reverse-proxy-and-https).

## Automatic renewal

TDM normally renews the session about five minutes before the current authentication
context expires. It uses saved state on the miner host and never needs to contact the
original helper desktop. The current Docker build includes Chromium for this work;
[source installations](installation.md#run-from-source) need it on the miner host's
`PATH`.

Temporary renewal failures are retried automatically. Follow the dashboard's status:
when it says a new helper login is required, enable helper access and sign in again.
Twitch may revoke a session, or a long outage may outlast the credentials available
for renewal. Twitch controls these lifetimes, so a successful login does not guarantee
permanent access.

Accepted helper credentials and renewal state are saved in
`data/imported-session.json`. Preserve the data directory across restarts and container
replacement. Keep it private, do not edit the credential file, and never attach it to
an issue. Restarting with the saved data restores renewal even with helper access off.

The miner opens a temporary headless Chromium only when needed and closes it afterwards.
No browser-control port needs to be exposed. Use the supplied Docker init and shutdown
settings so cleanup can finish. Normal helper completion, errors, and graceful cancellation
also remove its desktop profile; a forced kill or operating-system crash can prevent
that cleanup.

## Run the helper from source

Use the same source revision as TDM on your desktop, with Python 3.12 or newer,
[uv](https://docs.astral.sh/uv/), and Google Chrome installed. These commands run
only the login helper; the miner can be on another computer. Run them in a local
terminal on your desktop, from the repository root. Create `env/` only on the first
setup.

On Linux or macOS:

```bash
uv venv env --python 3.12
source env/bin/activate
uv sync --active --locked --python 3.12
python login_helper.py --tdm http://192.168.1.10:8080
```

On Windows PowerShell:

```powershell
uv venv env --python 3.12
. .\env\Scripts\Activate.ps1
uv sync --active --locked --python 3.12
python login_helper.py --tdm http://192.168.1.10:8080
```

Replace the example with your instance's root URL. `--chrome` selects a Chrome
executable on the desktop running the helper, and `--language` selects a translation.
Without `--tdm`, the helper asks for the address interactively.

[All guides](README.md) · [Next: Using the dashboard](usage.md)
