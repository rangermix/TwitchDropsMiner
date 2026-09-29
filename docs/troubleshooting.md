# Troubleshooting

Start with the status and console output on the **Main** tab. Compare progress with
your [Twitch inventory](https://www.twitch.tv/drops/inventory), since a dashboard
display and Twitch's recorded progress can differ.

## The helper cannot connect or open a browser

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
- Install Chrome, Chromium or Firefox 143+ on the helper desktop. Automatic selection
  searches Chrome → Chromium → Firefox, stopping at the first installed browser.
  `--browser chrome`, `--browser chromium` or `--browser firefox` selects one explicitly.
  `--chrome`, `--chromium` and `--firefox` select local executables.
  `--tdm` selects the miner address. See [browser/version guidance](authentication.md#choose-a-browser).
- On Linux, use native `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`, `firefox` or
  `firefox-esr` on `PATH`. Firefox ESR must also be at least version 143. Flatpak
  and Snap launchers are unsupported; executable options cannot take shell commands.
- If you use a reverse proxy, use its configured root URL and follow
  [Dashboard access](dashboard-access.md#reverse-proxy-and-https).

Sign into Twitch in the browser window the helper opens, then close all windows of
that instance (on macOS, quit it). Leave the helper open and let the browser reopen
for verification. This sequence applies to all three supported browsers.

If Twitch says **browser not supported** only in the helper's Firefox, use the
updated helper with ordinary Firefox sign-in. Complete login and Twitch verification,
then close all windows of the helper-opened Firefox (on macOS, quit that instance).
The helper will reopen its temporary profile for verification. Older source builds
enabled browser automation during manual sign-in, which can affect Twitch's browser
checks. Also test ordinary Firefox on the same desktop; if it fails too, update the
browser and follow Twitch's troubleshooting guidance.

If Firefox is signed in and shows campaigns but **Checking the Twitch session** ends
with `SESSION_CAPTURE_TIMEOUT`, update the helper executable to a build containing
the Firefox capture fix. Earlier helper builds with Firefox support could ignore
Twitch's campaign requests because their network-event URLs included a fragment.
Updating only the miner or browser does not add this helper fix. If it persists with
the updated helper, report the helper build, Firefox version and exact code without
session data.

## Helper error code reference

The updated helper prints a translated explanation after each error code. The
table below gives the meaning and a useful next step. These explanations require
an updated helper executable, not just an updated miner. Codes beginning with
`SESSION_` come from the helper's fixed diagnostics, not arbitrary server text.

| Code | Meaning and next step |
| --- | --- |
| `SESSION_HELPER_DESTINATION` | Invalid miner address. Supply an HTTP(S) root URL and port, without a path, credentials, query or fragment. |
| `SESSION_HELPER_DISABLED` | Admission is closed. Enable **Settings → Allow helper connection**, then restart the helper. |
| `SESSION_HELPER_EXPIRED` | The ten-minute connection expired, the miner restarted, or a setting change invalidated it. Check admission, restart the helper and finish login within ten minutes. |
| `SESSION_HELPER_BUSY` | The miner has too many pending helper connections. Close duplicate helpers and wait up to ten minutes for old tickets to expire. |
| `SESSION_HELPER_CHROME_MISSING` | Chrome was not found or the specified file is missing. Install native Chrome, use `--chrome` with its executable, or select Firefox. |
| `SESSION_HELPER_CHROMIUM_MISSING` | Chromium was not found or the specified file is missing. Install native Chromium or use `--chromium` with its executable. |
| `SESSION_HELPER_FIREFOX_MISSING` | Firefox was not found or the specified file is missing. Install native Firefox 143+ or use `--firefox` with its executable. |
| `SESSION_HELPER_BROWSER_MISSING` | No supported browser was found. Install native Chrome, Chromium or Firefox 143+ on the desktop running the helper. |
| `SESSION_HELPER_FIREFOX_VERSION` | Firefox lacks required capture support. Update to 143 or newer, including for ESR, or use Chrome. |
| `SESSION_HELPER_FIREFOX_LOGIN` | Firefox exited without retaining a Twitch login. Restart the helper, finish sign-in and any verification, then quit only the helper-opened Firefox instance. |
| `SESSION_HELPER_LOGIN_MISSING` | Chrome or Chromium exited without retaining a Twitch login. Restart the helper, finish sign-in and verification, then quit only the helper-opened browser instance. |
| `SESSION_HELPER_BROWSER` | The desktop browser could not start or exited too soon. Use a desktop session, check its executable and available resources, and keep the helper's window open. |
| `SESSION_HELPER_BROWSER_OWNER` | The helper could not verify its own browser process and temporary profile. Use a native executable rather than a sandbox launcher. Do not attach it to an ordinary browser profile. |
| `SESSION_HELPER_LOGIN_TIMEOUT` | The login and browser-close step did not finish before the connection deadline. Restart the helper, complete Twitch login and verification, then close all helper browser windows (on macOS, quit that instance). Leave the helper open. |
| `SESSION_HELPER_NETWORK` | Communication with TDM failed. Check the root URL, port, network and HTTPS certificate from the helper desktop. Do not disable certificate verification. |
| `SESSION_HELPER_REDIRECT` | The selected endpoint redirected the request. Use the final root URL, including `https://` if required. The helper never forwards credentials through redirects. |
| `SESSION_HELPER_RESPONSE` | TDM returned an invalid protocol response. Check matching helper/miner versions and that the proxy forwards `/api/helper/*` correctly. |
| `SESSION_HELPER_REJECTED` | TDM or its proxy rejected the request without a more specific recognized code. Check versions, admission and timestamped miner logs; this code alone does not identify the cause. |
| `SESSION_HELPER_SERVER_BROWSER` | Chromium could not start on the **miner host**. For source installs, put `chromium` or `chromium-browser` on the miner process's `PATH`. For Docker, check the image, resources and logs; the official image includes Chromium. |
| `SESSION_HELPER_RESULT_UNKNOWN` | The upload acknowledgement could not be recovered. The session may already be saved: check Twitch login on the Main tab before retrying. The helper does not resend the credential upload. |
| `SESSION_HELPER_BROWSER_CLEANUP` | Browser shutdown could not be confirmed. Check TDM's login state before retrying and close only the helper's browser window. |
| `SESSION_HELPER_PROFILE_CLEANUP` | Temporary-profile deletion failed, for example because of file locks or permissions. Check TDM first, close the helper browser and keep remaining files private. Never delete your ordinary browser profile. |
| `SESSION_BROWSER_ADDRESS` | The internal browser endpoint was not a valid loopback address. Use an unmodified, matching helper and report persistent failures; this is not the `--tdm` address. |
| `SESSION_BROWSER_PROTOCOL` | Browser control or response decoding failed. Keep its window open, update the browser and use matching helper/miner versions. Report persistent failures with versions and the code. |
| `SESSION_CAPTURE_TIMEOUT` | No issued Twitch proof used by a successful authenticated campaign request was captured before the deadline. Verify login and Twitch access, then retry with an updated browser. |
| `SESSION_CAPTURE_LIMIT` | Capture exceeded a bounded request/body limit. Restart the helper; if repeated, report versions and the code. |
| `SESSION_EXPIRED`, `SESSION_SDK_EXPIRED` | Captured session or renewal-cookie proof expired. Check the computer clock and restart login promptly. |
| `SESSION_REPLAY` | Renewal did not produce a distinct, sufficiently fresh proof. Restart the helper; repeated failures need investigation rather than editing credentials. |
| `SESSION_FORMAT`, `SESSION_SDK_SEED` | Captured session/bootstrap data failed validation. Use matching versions and a fresh login; do not edit credential files. |
| `SESSION_SDK_COOKIE` | A valid, sufficiently long-lived renewal cookie was unavailable. Retry with an updated browser/helper and check Twitch connectivity. No guaranteed workaround is known. |
| `SESSION_SDK_PAGE` | The isolated validation page did not match the expected request. Update the browser/helper; report repeated failures. No guaranteed workaround is known. |
| `SESSION_SDK_TIMEOUT` | Twitch's browser SDK did not finish within the deadline. Check Twitch connectivity and retry later. No guaranteed workaround is known. |
| `SESSION_SDK_ISSUANCE` | Twitch validation failed or the observed network proof did not match the returned proof. Update browser/helper and check connectivity. No guaranteed workaround is known. |
| `SESSION_AUTH`, `SESSION_IDENTITY`, `SESSION_CATALOG` | Twitch rejected credentials, identity validation failed, or campaign access could not be verified. Check login and Twitch Drops access in the helper browser. No guaranteed workaround is known. |
| `SESSION_ACCOUNT_MISMATCH` | The validated account changed. Restart and keep the same Twitch account throughout login. |
| `SESSION_REQUEST` | The helper's validation request to Twitch failed. Check Twitch connectivity and retry after any outage. |
| `SESSION_HELPER_FAILED` | An unexpected internal error occurred. Check TDM before retrying; report helper/browser versions, desktop OS and the code without credentials. |

If a session upload has already started, an unknown result, interruption or cleanup
error is not proof that installation failed. Verify the miner's Twitch login first.

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
  local browser profile. It does not establish whether TDM accepted the login.
  The v2.0.1 helper handles read-only files in an owned Windows profile and retries
  when a child file disappears during deletion. Persistent locks or permission
  failures still report an error. Keep remaining temporary profiles private.

The fixes above require the updated helper executable as well as the miner;
the v2.0.0 helper does not contain them. For a remaining problem, report the helper archive name, desktop
OS and browser version, miner installation and image tag/digest, exact error code,
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
on the miner process's `PATH`. The helper's desktop browser is separate.
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
