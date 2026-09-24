# Browser session portability investigation

Date: 24 September 2026. Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).
Application source tested: `5ba50dce9dbdf69864522aa199c5e17c9f48abf7`.

## Question and result

Can a user finish Twitch login on a working desktop browser, then import authentication
into a TDM installation whose Docker browser cannot complete fresh login?

The read-only experiment supports this as a candidate, provided the transfer includes
the matching authenticated request context. Twitch cookies alone did not restore campaign
access. A context captured from the working desktop browser did restore it, including
through ordinary Python HTTP inside Docker without sending browser cookies.

This is not a shipped import feature. The current application continues to execute its
browser-authenticated requests in the attached browser.

## Method and observations

The source was the dedicated native macOS ARM64 Chrome 153 test profile in which the user
had completed fresh login. The destination was official Chrome 153 in the existing
`selenium/standalone-chrome:4.49.0-20260909` Docker experiment, with a separate initially
unauthenticated profile. Both environments used the same home network. No third-party
browser service received the session.

1. Copy the 15 cookies applicable to `twitch.tv`/`www.twitch.tv` from the dedicated source
   profile to the destination. Do not transfer local storage or request headers.
2. Navigate the destination to Twitch's campaign page and capture a new authenticated
   GraphQL request carrying a nonempty `Client-Integrity` header. Validate account identity
   and issue the application's Inventory and Campaigns operations.
3. Reload and repeat, clearing old performance events before navigation. The campaign
   failure repeats with the destination's own context.
4. Capture the complete matching context from an authenticated source-browser request.
   Compare the same two read-only operations in the source browser, Docker browser, native
   Python HTTP, and Python HTTP inside Docker. The HTTP comparisons also use the source
   browser's user agent and Twitch origin/referer. They do not send a Cookie header.

| Transport and request context | Identity | Inventory | Campaigns |
| --- | --- | --- | --- |
| Docker Chrome, imported Twitch cookies, destination's own integrity context | Same account validated | Valid | `failed integrity check` |
| Native Chrome, source context (control) | Previously validated source account | Valid | 126, no GraphQL errors |
| Docker Chrome, complete source context | Same imported account | Valid | 126, no GraphQL errors |
| Native Python HTTP, complete source context | Source token retained | Valid | 126, no GraphQL errors |
| Docker Python HTTP, complete source context | Source token retained | Valid | 126, no GraphQL errors |

The retained context uses the browser service's header allowlist: authorization, web
client ID, client integrity, client version, client session ID, device ID, and language
where present. Headers are transferred together, not synthesized or combined across
accounts. Credentials are kept in memory or private browser profiles; only counts,
booleans, and a recognized error classification are reported. These probes do not claim
rewards or generate watch events.

The earlier native full-miner progress result (0 to 4 server-reported minutes) is separate
evidence. It does not prove mining through imported authentication.

## Implications and remaining checks

- A local login helper or a narrowly scoped browser extension could collect and transfer
  the necessary Twitch authentication context to the user's own TDM instance. Exporting
  every browser cookie is unnecessary for the two successful HTTP operations tested here.
- Any import must retain the WEB client identity, validate the intended account and both
  inventory operations, preserve valid Android credentials, and protect credentials from
  logs and unauthenticated dashboard access.
- The context's usable lifetime and renewal are unverified. A one-time successful import
  is insufficient for unattended operation; an implementation needs a tested renewal
  route or an explicit request for the user to reconnect their local browser.
- A different server/network, browser/miner restart with imported state, other authenticated
  operations, and Twitch-side mining progress remain untested for this route.
- These observations do not isolate Twitch's detection signals or establish that every
  Docker browser setup fails. Keep the tracking issue open.

## Alternative browser candidates

- [docker-stealthy-auto-browse](https://github.com/psyb0t/docker-stealthy-auto-browse)
  combines Camoufox, a virtual display, OS input, an API, and noVNC. Its
  [Dockerfile at the inspected revision](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/6afa03f6ec41f14b4856866161a649a54c3d61d0/Dockerfile)
  pins the browser archive to `152.0.4-beta.28`; an older Firefox 135 comment in that file
  does not describe the pinned archive. This setup has not been tested against Twitch here.
- The linked [browserless/chrome Docker Hub image](https://hub.docker.com/r/browserless/chrome)
  is the old v1 image. Current Browserless documentation distinguishes
  [open-source hosting from Cloud/Enterprise features](https://docs.browserless.io/enterprise/open-source):
  its advertised stealth routes are not included in the open-source image.
- [Puppeteer-Stealth](https://github.com/berstend/puppeteer-extra/tree/master/packages/puppeteer-extra-plugin-stealth)
  is another test candidate. Its own documentation acknowledges remaining detection paths;
  generic detector-test results do not prove Twitch login or campaign access.

These browser alternatives have been researched, not live-validated in this investigation.
