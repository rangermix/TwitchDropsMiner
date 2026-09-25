# Server renewal from a one-time browser SDK cookie export

Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).
This follows the [failed HTTP renewal probes](2026-09-25-server-only-renewal.md).
These are live experiments, not a shipped unattended-renewal feature.

## Candidate and evidence boundary

A one-time export of the authenticated request context plus Twitch's `KP_UIDz-ssn`
cookie for `k.twitchcdn.net` enabled new, accepted integrity tokens in a Docker browser.
The browser need not run on the user's computer after export. Copying all browser
storage or all Twitch cookies was unnecessary in the tested configuration.

The server uses the actual browser SDK to issue a new token. This differs from replaying
previous SDK request headers, which failed. The experiment was informed by
[Streamlink's browser SDK flow](https://github.com/streamlink/streamlink/blob/2812e7bdc2e6a7f3602c4e7d245a48992b7de756/src/streamlink/plugins/twitch.py#L581),
but tests authenticated Drops operations independently of Streamlink playback support.

The source was the dedicated native Chrome test profile, authenticated earlier by the
user. Cookie export briefly opened that profile without navigating to Twitch; it was
closed before server probes. The original exported integrity token was already expired.
The test account and all private context remained on the user's home hardware. Only
counts, times, header/cookie names and validation booleans are reported here.

## Controlled comparisons

Chrome 153.0.8010.47 ran in Docker. Each fresh-profile comparison used the same OAuth
and client/device context. The browser loaded the SDK in a minimal Twitch-origin document,
issued a token and submitted the miner's Inventory and Campaigns queries with that token.

| Server browser setup | Inventory | Campaigns |
| --- | --- | --- |
| Fresh headed profile, authenticated SDK issuance | Valid | Integrity rejected |
| Fresh headed profile, anonymous SDK issuance then authenticated query | Valid | Integrity rejected |
| Fresh profile with Puppeteer-Stealth defaults | Valid | Integrity rejected |
| Copied Local Storage, IndexedDB and Session Storage; no cookies | Valid | Integrity rejected |
| Copied storage and 14 scoped cookies including the SDK cookie | Valid | 149 campaigns |
| Fresh profile with the 14 cookies; no copied storage | Valid | 149 campaigns |
| Fresh profile with only `KP_UIDz-ssn` | Valid | 149 campaigns |
| Fresh profile with the other 13 cookies, excluding `KP_UIDz-ssn` | Valid | Integrity rejected |
| Fresh headless profile with only `KP_UIDz-ssn` | Valid | 149 campaigns |
| Restarted server browser with its persisted state; no further import | Valid | 149 campaigns |
| Two successive fresh profiles seeded only with the preceding server-issued SDK cookie | Valid | 149 campaigns each |

The later comparisons correlate the returned token and expiration with an observed
HTTP 200 POST `/integrity` response, excluding cache and service-worker responses.
New tokens differ from the original imported token; repeated server runs also compare
against the previous server token. A run ID and private content digest bind the saved
context to its specific successful result, preventing reuse of stale output from a
failed run.

The last comparison uses the server's replacement SDK cookie to seed the next fresh
profile, then repeats with that profile's replacement cookie. Both rounds issue new
accepted integrity tokens, with the SDK cookie expiry advancing to approximately
24 hours after each round. This demonstrates a server-produced continuation seed;
the sustained expiry test below still matters for longevity.

Independent Python HTTP inside Alpine 3.24.2 validated the renewed context as the same
account and WEB client, then returned valid Inventory and 149 Campaigns. The ordinary
TDM import endpoint also accepted a headless-generated context and resumed inventory
retrieval. Thus success is not limited to requests inside the browser.

## Sustained test in progress

At 04:09 UTC on 25 September, a separate headless Docker helper imported the SDK cookie
once and began automatically delivering validated contexts to the Alpine miner. The
first accepted delivery advanced the provider to generation 7. Its integrity token
expires at 05:09 UTC. The helper will renew five minutes before expiry and restart its
server browser for each issuance using the same server profile, without another import.

The original SDK cookie expires at 07:32 UTC. The server obtained a different SDK cookie
with approximately 24 hours remaining. The sustained run is scheduled through 07:34 UTC
to check that renewal continues after the original seed's expiry. This is a pending
check, not a completed longevity claim. Longer operation and recovery from loss or
revocation of the SDK state remain unverified.

The miner selected eligible campaigns and entered its watching state. An independent
Inventory observer recorded three World of Warcraft rewards advancing from 124 to 126
Twitch-reported minutes between 04:11 and 04:13 UTC, using the server-generated generation
7 context. This establishes actual account progress and successful progress reads, not
local extrapolation. No other local test miner was running, and the SDK probes never
opened a player, but possible account activity on other devices was not controlled;
these observations alone do not attribute the progress exclusively to this miner.
Progress across the next renewal and automatic claims are still pending. A changed
claimed-benefit count alone will not establish a miner claim; that needs a matching
successful ClaimDrop outcome from the miner.

## Deployment implication

This candidate needs a server-side browser for SDK execution. The core miner can remain
on Alpine with its current Python dependencies, using HTTP for ordinary operations.
The experimental helper runs separately and exits its browser between renewals. A
production export format, renewal service and failure/recovery integration have not
been implemented. The existing local-browser helper remains a separate intermediate
feature and still requires the user's browser.

Do not publish session exports, SDK cookie values, request headers, browser profiles or
pairing credentials. The SDK cookie is authentication-sensitive state and must be stored
and transferred privately, just like the OAuth context.
