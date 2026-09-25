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
expires at 05:09 UTC. The helper renews five minutes before expiry and restarts its
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
Further progress and accepted claim-path evidence are recorded below. A changed
claimed-benefit count alone does not establish a miner claim; the active miner's own
result and the distinction between a new claim and an already-claimed response matter.

## Deployment implication

This candidate needs a server-side browser for SDK execution. The core miner can remain
on Alpine with its current Python dependencies, using HTTP for ordinary operations.
The experimental helper runs separately and exits its browser between renewals. A
Python implementation is now available experimentally in this branch; see the
[setup instructions](../server-renewal.md). The existing local-browser helper remains
a separate intermediate feature and still requires the user's browser.

## Python helper implementation checkpoint

The new helper uses the project's existing aiohttp/CDP dependencies and an optional
Alpine image with Chromium 152.0.7977.82. It starts a private temporary browser, seeds
only the SDK cookie, correlates the new token with a real uncached issuance response,
then closes the browser before independent Python identity/Inventory/Campaigns checks.
Only validated replacement state is saved, using the existing owner-only atomic file
writer. Scoped delivery and scheduling reuse the existing renewal endpoint and loop.

Two independent `linux/arm64` container runs passed live validation. The first started
with an expired imported integrity context. The second used only the first server run's
persisted SDK cookie in another fresh browser profile, before that server context expired.
This proves restart and seed continuation, not a second expiry crossing. Both produced distinct accepted tokens and
rotated the SDK cookie with roughly 24 hours remaining. The source profile remained
closed, with no listener on its former DevTools port. A packaged `--once` run then
delivered a validated replacement through the production protected SessionAPI, advancing
an isolated catalog-only target to generation 2. That target runs no mining/watch loop,
so it does not interfere with the original sustained mining experiment.

At 05:01 UTC the packaged Python helper started its normal unattended loop against that
target, accepted generation 3, and scheduled renewal five minutes before its 06:01 UTC
expiry. The completed cycle is recorded below.

At 05:04 UTC the original prototype performed its next scheduled renewal with the local
browser still closed. The miner accepted generation 8, the new token returned 149
campaigns, and its observed expiry advanced to 06:04 UTC. The response was confirmed
on the network. This establishes the normal five-minute-lead renewal.

At 05:09:57 UTC, after generation 7's observed expiry, independent Python validation
using generation 8 still passed identity, Inventory and 149 Campaigns. Twitch reported
183 watched minutes for the three tracked rewards, up from 124 initially. The first
unclaimed reward requires 240 minutes, so no new claim is expected yet. These remain
account-progress observations with the attribution limitation above. The original SDK
cookie's 07:32 UTC expiry remains pending; the packaged helper's normal cycle subsequently
passed as follows.

## Packaged scheduled cycle and actual miner operations

At 05:56:10 UTC the packaged Python helper performed its default scheduled renewal,
advancing its catalog-only target from generation 3 to 4. The integrity token differed,
its expiry advanced to 06:56:08 UTC, and the SDK cookie rotated with an extended expiry.
The atomically saved server seed matched the context accepted by the target. No local
browser or new export was involved.

At 06:01:23 UTC, 17 seconds after generation 3's advertised expiry, an independent
Python request inside the target validated generation 4's account and Inventory and
returned 147 Campaigns. The helper had no remaining Chromium process or temporary
browser profile. This proves the packaged helper's real default cycle and accepted
protected operations after the previous integrity context expired. It does not yet
cross the original SDK cookie's 07:32 UTC expiry.

Separately, the original prototype delivered generation 9 to the actual miner at
05:59:31 UTC and scheduled its next renewal for 06:54 UTC. After both that delivery
and the packaged post-expiry check passed, a packaged Python `--once` run used a copy
of the continuous Python helper's server-produced seed in a separate state directory. It reused
the miner's existing pairing, preserving the prototype's credential. At 06:01:38 UTC
the actual miner accepted generation 10, with expiry at 07:01:35 UTC; the new seed
matched that delivery and the SDK-cookie expiry advanced again.

At 06:01:41 UTC a separate Python process in the running miner container loaded its
accepted generation 10 state into the production `ImportedSession` class. This
independent probe passed Inventory, Campaigns, GetStreamInfo and CurrentDrop; the
active watch/claim loop remained running separately.
The catalog contained 147 campaigns, CurrentDrop was present, and the three tracked
rewards reported 235 watched minutes. The first unclaimed reward requires 240 minutes.
Progress remains an account observation, without exclusive earning attribution.

The actual miner therefore has mixed test provenance: generations 7–9 came from the
prototype; generation 10 came from the packaged Python helper. The original prototype's
server profile and the Python helpers' separate seed files are not shared. Its later
issuance after the original SDK-cookie expiry remains a separate scheduled gate.
Code hashes from the running helper and the actual miner's authentication, GraphQL,
core and claim modules matched source revision `01a87ce`; the live checks did not use
an older implementation of those paths.

At 06:06:35 UTC the active miner logged `Claimed drop` and `Recorded drop` for Cuddly
Blue Grrgle while generation 10 was installed. Its history recorded the same reward
and timestamp. At 06:07:44 UTC the independent production-provider probe in that
container still passed its four read operations and found the reward claimed at
240 minutes; the other two rewards had reached 241 minutes.

This establishes the active miner's accepted claim path with the Python-delivered
context. The raw ClaimDrop response was not logged. `BaseDrop._claim()` accepts both
`ELIGIBLE_FOR_ALL` and `DROP_INSTANCE_ALREADY_CLAIMED`, so the success messages and
history do not distinguish those statuses or establish which client claimed first.
Other-device activity remains uncontrolled; exclusive earning and first-claim
attribution are not asserted.

The packaged `linux/amd64` image also issued a distinct token and passed independent
identity/Inventory/Campaigns validation using a copied server replacement seed in a fresh
profile. That input integrity context was still valid; this is cross-architecture
issuance proof, not an additional expiry crossing. Its replacement SDK cookie rotated.

The implementation passed 486 tests plus two subtests, Ruff, Mypy for all 68 source
files and lock validation. A separate adversarial review reproduced and then approved
fixes for surviving browser descendants and SIGTERM bypassing cleanup. Regression
tests cover real process groups/signals and repeated cancellation. A live Docker stop
during browser activity observed five browser processes and one private profile before
termination; the helper exited with code 143 in 0.26 seconds and left no private profile.
The documented command uses Docker init and a 20-second stop grace.

Core and optional helper images built successfully for both `linux/arm64` and
`linux/amd64`. No Python dependency or ordinary Dockerfile change was needed. The
original SDK-cookie expiry gate and live initial-export verification remain pending;
longer operation, live outage recovery and exclusive earning/claim attribution are
unverified. This checkpoint does not close #118.

Do not publish session exports, SDK cookie values, request headers, browser profiles or
pairing credentials. The SDK cookie is authentication-sensitive state and must be stored
and transferred privately, just like the OAuth context.
