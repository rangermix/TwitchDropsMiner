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
- A fresh authenticated GraphQL integrity response advertised about one hour of validity
  (details below). Actual expiry enforcement and renewal remain unverified. A one-time
  successful import is insufficient for unattended operation; an implementation needs a
  tested renewal route or an explicit request to reconnect the user's local browser.
- A different server/network, browser/miner restart with imported state, other authenticated
  operations, and Twitch-side mining progress remain untested for this route.
- These observations do not isolate Twitch's detection signals or establish that every
  Docker browser setup fails. Keep the tracking issue open.

## Export lifetime and browser isolation

At 10:08:48 UTC on 24 September, a disposable tab in the dedicated authenticated native
Chrome profile observed HTTP 200 from `https://gql.twitch.tv/integrity`. Its `expiration`
field was 11:08:46.855 UTC, leaving 3599 seconds when measured. The returned token used
the `v4.local` format. An in-memory comparison confirmed that exact token appeared in
`Client-Integrity` on a subsequent authenticated GraphQL request whose response returned
126 campaigns, HTTP 200, and no campaign GraphQL errors. Only the equality boolean,
expiry metadata, status, and count were retained; no token was included in the report.
Earlier issuance samples also advertised about 3600 seconds. This is evidence of the
issuer's stated lifetime for a working authenticated context, not a timed test of
when exported requests stop working or proof that every integrity credential lasts an
hour. The unauthenticated Browserless login page received a different, approximately
16-hour integrity expiry; that did not enable login.

The source profile's auth cookie had about 395 days remaining at 09:41 UTC, while OAuth
validation returned `expires_in: 0`. Neither establishes how long the complete exported
bundle remains usable. Twitch can invalidate credentials before their advertised expiry;
see its [token validation guidance](https://dev.twitch.tv/docs/authentication/validate-tokens/).
Plan renewal around the short-lived authenticated context, with less time available if
export happens after issuance. Do not promise an hour of uninterrupted imported mining.

A live GET of `https://www.twitch.tv/login` returned HTTP 200 and
`X-Frame-Options: SAMEORIGIN`. TDM therefore cannot embed this login page in a cross-origin
iframe. Independently, the [same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)
prevents a normal TDM page from reading Twitch's DOM, cookies, or storage, including in
a popup. An explicitly permitted browser extension or a local browser helper could
collect the necessary matching context and send it to the user's own authenticated TDM
instance. That remains a design candidate; export/import and automatic renewal are not
implemented. A normal OAuth callback is not itself proof of access to the protected
web-client operations needed here.

## Alternative browsers: fresh-login tests

The following tests used the user-authorized test account in fresh profiles on the same
Docker Desktop host and home network. They entered credentials on Twitch's real page
through each browser's control interface. All ran with a virtual display, reported
`navigator.webdriver` false, and returned **“Your browser is not currently supported.”**
None produced an `auth-token` cookie or reached email verification. No mailbox access
was needed. No third-party hosted browser service received credentials.

| Setup | Configuration | Observed result |
| --- | --- | --- |
| `psyb0t/stealthy-auto-browse`, Camoufox 152.0.4 beta.28 | ARM64; default browser fingerprint configuration; API with OS-level text input | Unsupported-browser message; login POST HTTP 400; no authenticated cookie |
| Chrome 153 + Puppeteer-Stealth | ARM64; official Chrome in `selenium/standalone-chrome:4.49.0-20260909`; Selenium disabled; Puppeteer Core 25.12.0, Extra 3.3.6, Stealth 2.11.2 defaults | Unsupported-browser message; login POST HTTP 400 / Twitch `5025`; no authenticated cookie |
| Browserless 2.56.7 + Chrome 153 | Current self-hosted Chrome image under AMD64 emulation; `headless: false`, `stealth: true`; built-in `@zorilla/puppeteer-extra` 2.0.2 and stealth plugin 2.0.1 | Unsupported-browser message; login POST HTTP 400 / Twitch `5025`; no authenticated cookie |

Pinned image digests:

- Camoufox bundle: `psyb0t/stealthy-auto-browse@sha256:65c114229b0cff362100c9725e444a94b4405e55bad261cb1a12bdaf62561cc2`.
- Browserless: `ghcr.io/browserless/chrome@sha256:e6cd08568a576bb66c4fce6cd52d90c5637d52826f807e5859c22210fd2fcd80`.

The Puppeteer and Browserless stealth defaults presented a Windows Chrome 153 user agent;
Camoufox presented Linux Firefox 152. This records the tested configurations, not a claim
that those user agents caused the rejection. The earlier successful native login used
the dedicated source profile in a separate test; these results do not exhaust all
fingerprint, account, timing, browser-version, architecture, or network combinations.

### Source and feature distinctions

- [docker-stealthy-auto-browse](https://github.com/psyb0t/docker-stealthy-auto-browse)
  combines Camoufox, a virtual display, OS input, an API, and noVNC. Its
  [Dockerfile at the inspected revision](https://github.com/psyb0t/docker-stealthy-auto-browse/blob/6afa03f6ec41f14b4856866161a649a54c3d61d0/Dockerfile)
  pins the browser archive to `152.0.4-beta.28`; an older Firefox 135 comment in that file
  does not describe the pinned archive. The test above used that archive.
- The linked [browserless/chrome Docker Hub image](https://hub.docker.com/r/browserless/chrome)
  is the old v1 image. Current Browserless documentation distinguishes
  [open-source hosting from Cloud/Enterprise features](https://docs.browserless.io/enterprise/open-source).
  However, inspecting the current image and its
  [v2.56.7 launcher](https://github.com/browserless/browserless/blob/v2.56.7/src/browsers/browsers.cdp.ts)
  confirmed that a basic Puppeteer stealth launch option is included; that is the mode
  tested here. The broader Cloud/Enterprise offerings were not tested.
- [Puppeteer-Stealth](https://github.com/berstend/puppeteer-extra/tree/master/packages/puppeteer-extra-plugin-stealth)
  was tested with its default evasions. Its documentation acknowledges remaining detection paths;
  generic detector-test results do not prove Twitch login or campaign access.

These failures leave Docker fresh login unresolved. The next viable candidate is a local
browser helper or extension with renewal, subject to the portability limits above. Keep
#118 open; neither imported-session mining nor unattended renewal has been established.
