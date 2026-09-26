# Integrated server renewal

Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118). This branch
is experimental and unreleased. Use the [helper-assisted login](../README.md#helper-assisted-login-experimental)
flow once on your desktop: run the helper, enter your TDM address, log into Twitch,
and wait for success. There is no seed file to transfer, renewal connection to download,
sidecar to configure, mandatory dashboard password, or environment flag to enable.

## What TDM stores and runs

The helper sends its captured session and Twitch SDK cookie directly from memory.
TDM verifies identity and catalog access, issues and verifies a replacement using its
own Chromium, then atomically writes `data/imported-session.json`. This private file
contains the accepted session, SDK cookie, account/generation and helper admission
state. Docker's existing `/app/data` volume is the only persistent exported-session
storage. Keep it across container replacement; do not publish or attach it to reports.

The standard Docker image remains Alpine and adds Chromium as an operating-system
package. There are no new Python runtime dependencies. TDM launches headless Chromium
with an owned temporary profile during SDK issuance and closes it afterwards. Ordinary
mining requests continue through Python HTTP. No browser or DevTools port is exposed.
The included Compose configuration enables an init process and 30-second stop grace.
For direct `docker run`, use `--init --stop-timeout 30` and the ordinary data volume.
Source-run TDM needs `chromium` or `chromium-browser` available on its PATH for renewal.

## Admission, scheduling and recovery

**Allow helper connection** defaults on for an unconfigured instance. Successful login
closes it automatically. Turn it on only when making a new connection or replacing an
account. Turning it off invalidates outstanding helper connections as well as blocking
new ones. This setting does not stop the renewal worker or require dashboard auth.
The optional dashboard password still controls ordinary dashboard access. A helper
connection only authorizes an attempted login while admission is open; it cannot export
existing credentials or unlock the dashboard.

Renewal normally runs five minutes before the accepted integrity token expires and
validates the same account, inventory and campaigns before saving a distinct replacement.
Transient failures retry with bounded delay. After restart, the worker uses the saved
server state; it never contacts the original desktop. Your desktop, Chrome and helper
can all be closed after successful login. If Twitch revokes the OAuth session or the
SDK cookie expires during an extended outage, TDM may need a new helper login. Session
lifetimes are controlled by Twitch; one successful export does not promise permanent access.

The helper confirms server acceptance before reporting success. A network/proxy failure
can hide an otherwise successful response, so it checks a short-lived receipt without
uploading credentials again. If it cannot confirm the result, it reports that the result
is unknown and directs you to TDM. Reopen admission only if another login is actually needed.
The desktop profile is deleted after normal completion, errors, or graceful cancellation.
An OS crash or forced kill can leave that temporary profile behind.

## Evidence boundary

The earlier separate server helper passed scheduled renewal, requests after actual
integrity-token expiry, renewal beyond the original SDK-cookie expiry, and restart
with the native browser closed on one home network. See the
[timestamped record](notes/2026-09-25-sdk-cookie-renewal.md). These results establish
that server-side issuance can work in that environment, not that every refactoring,
platform or future Twitch change works. The integrated flow requires its own live
handoff, renewal and restart checks; [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118)
records that validation. The old manual import/pair/renew HTTP routes and local export
CLI are removed from this branch; old connection files cannot be used with it.
