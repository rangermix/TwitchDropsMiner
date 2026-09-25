# Server-only integrity renewal remains unresolved

Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).
This corrects the completion boundary of the earlier
[browser-assisted renewal checkpoint](2026-09-25-session-import-renewal.md).

## Required behavior

The user exports an authenticated session once, then can turn off their computer and
browser. TDM must renew usable integrity tokens on the server. Passing means a distinct
server-issued token authenticates as the same account and passes protected Campaigns,
including continued operation after the imported context expires. HTTP 200 from the
issuance endpoint or a token with a future expiry is insufficient.

## Live evidence, 25 September 2026

Both probes ran as Python HTTP requests inside the existing browser-free Alpine 3.24.2
TDM test container. Requests used the authorized test account and fixed Twitch endpoints.
No browser or local helper participated in issuance or campaign requests during the
probes. The dedicated source Chrome process was stopped and its loopback DevTools port
had no listener. No production credential state was replaced by these probes.

| Probe | Baseline | Server-issued result |
| --- | --- | --- |
| Existing export, now expired | OAuth identity still valid; old integrity context rejected | `/integrity` returned HTTP 200, a distinct token and approximately 3600 seconds validity; protected catalog validation failed with `failed integrity check` |
| Fresh one-time export including issuance context | After stopping the source browser, the imported context returned valid Inventory and 152 Campaigns | Replaying the captured issuance request returned HTTP 200, a distinct token and approximately 3600 seconds validity; campaign access again failed integrity validation |

Operation-level checks confirmed that Inventory still returned valid data for both
server-issued tokens, while Campaigns specifically returned `failed integrity check`.

For the second experiment, the dedicated native browser was briefly restarted solely
for a new one-time capture. Its successful issuance response was correlated with the
exact token used in a successful authenticated Campaigns request. The captured issuance
request included the browser's `x-kpsdk-*` headers as well as its ordinary client and
authentication context. The browser was then stopped before transferring the private
capture to the Docker probe. HTTP/2 pseudoheaders and transport-managed headers were
omitted when replaying through HTTP/1.1. No tokens or header values are published.

These results show that the tested HTTP renewal routes cannot replace the current
browser-assisted helper. They do not establish that every browser-free approach is
impossible, nor isolate whether replay rejection depends on proof freshness, transport,
request binding, or another server-side condition. No speculative renewal path was
added to production code.

## Consequence for the feature

Manual import and the local helper remain working intermediate features. The helper
requires the user's browser and therefore does not meet unattended server operation.
A browser extension or userscript can simplify initial export, but it cannot by itself
remove that renewal dependency. Any server-side browser alternative must independently
prove fresh accepted integrity issuance; passing fresh-login or old-token reuse tests
would not establish it.

The prior claim that all user gates were complete is withdrawn. Server-only renewal,
long unattended operation and imported-session mining progress remain unresolved.
The main Dockerfile and dependency manifests remain unchanged. This investigation
changes documentation only; the previously verified application tests are not evidence
of server-only renewal.

Follow-up: the [SDK cookie experiment](2026-09-25-sdk-cookie-renewal.md) subsequently
produced accepted tokens in a server browser from a one-time seed. That result does not
change the failed HTTP replay evidence above; sustained operation is being tested.
