# Native helper and integrated renewal checkpoint

Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).
Implementation: `cf6262fc0b61b108ec00159e352daeaf7c3f41f9`; Windows test decoding:
`2bc7e123de777a38d4927a32aeeada60c7da632a`. This is an unreleased branch checkpoint.
All times below are UTC on 26 September 2026.

Latest native cleanup checkpoint: `71456d32a7621f1d45c5c13a21813a3d395cd981`.
The server worker is unchanged by this desktop-helper fix.

## Build and regression evidence

- The full local suite passed **641 tests and two subtests** with Python 3.12 and
  Node 24 before the subsequent test-only encoding correction. Ruff, Mypy (70 source
  files), lock consistency and whitespace checks passed. All three release-script
  contract suites passed under GNU/Linux.
- Independent adversarial review cleared native cleanup/receipt recovery (104 focused
  tests), authenticated task cleanup (66 focused tests), and dashboard status/gate races
  (64 focused tests), after reproducing and fixing the findings. This is agent review,
  not maintainer approval or release authorization.
- The first Windows native job exposed UTF-8 output decoded using cp1252 by two CLI
  tests. Explicit UTF-8 capture fixed the tests without changing helper behavior.
  Independent review reproduced the decode failure and cleared the narrow fix; 104
  local native-focused tests passed.
- [Workflow 36242547624](https://github.com/rangermix/TwitchDropsMiner/actions/runs/36242547624)
  succeeded at `2bc7e12` for Linux x64, Windows x64, macOS x64 and macOS ARM64. Packaged
  smoke covers startup without Python on PATH, translations, and real HTTP admission
  refusal. Its no-local-files assertion applies to that refusal path, which does not
  launch Chrome. The downloaded archives contain the executable and LICENSE, preserving
  executable permission. Actual Twitch login on every platform is not established.
- Final Alpine images built for ARM64 (`6e9a11aa0733`) and AMD64 (`90167f08f404`).
  Both include Chromium; Python runtime dependency manifests are unchanged.

### Installed-Chrome validation follow-up

The packaged test was extended to admit a short-lived local connection, launch actual
installed Chrome, verify its owned DevTools process, reach a login timeout without
credentials, and check temporary-file cleanup before the test harness removes its own
directory. Linux uses Xvfb for its display. This does not establish authenticated login.

The initial Windows and Linux runs found remaining temporary files; Windows passed an
unchanged diagnostic rerun, while Linux consistently retained `.com.google.Chrome.*`
and `com.google.Chrome.chrome_chrome_url_fetcher_*` entries outside the already-removed
profile. Filename-only diagnostics identified the auxiliary-file escape.

At `71456d3`, Chrome's `TMPDIR`, `TMP` and `TEMP` are scoped to a private subdirectory
inside the owned profile. A new regression failed before the fix and passed afterward,
verifying auxiliary-file removal, preservation of unrelated files and unchanged parent
environment. The full suite passed **642 tests and two subtests**; source Ruff, Mypy,
lock consistency, both Docker architecture builds and independent native review passed.

[Workflow 36244392374](https://github.com/rangermix/TwitchDropsMiner/actions/runs/36244392374)
passed the extended packaged browser check on Linux x64, Windows x64, macOS x64 and
macOS ARM64. Local and independent macOS ARM64 rebuild checks also passed. These results
cover the helper's normal login-timeout cleanup. The test harness's separate 90-second
forced-kill fallback can leave detached Chrome processes and is not covered by a
successful-run cleanup claim; this remains a nonblocking test-harness limitation.

## Desktop and dashboard evidence

- The macOS ARM64 executable opened installed Chrome using its owned temporary profile
  and an explicit loopback debugging port. Earlier launch/cancel checks removed the
  owned profile; the fresh login trial also cleaned up after its ten-minute timeout.
  No `tdm-login-*` temporary directory remained after that timeout.
- Fresh login through this executable is **pending**. Native desktop control returned
  `cgWindowNotFound`; the requested manual sign-in did not finish within the trial.
  Earlier native-login/export proof does not establish the new direct handoff.
- Mocked API browser checks covered desktop 1440×1000 and mobile 390×844, admission
  changes, failed-save rollback, simulated acceptance/closure and Chinese rendering.
  The Chinese screenshot was regenerated through the real language selector and mocked
  settings events, correcting an inconsistent fixture rather than product code.
- The final image's actual dashboard at an isolated development origin exposed the
  helper-only login instructions. A real checkbox change persisted across reload and
  `/api/helper/connect` returned 403 while disabled; restoring the checkbox reopened
  admission. Dashboard password protection was off. Browser console checks were clean.
- Against the authenticated integration instance, `/api/session`, `/api/settings`,
  `/api/status`, `/api/auth/status`, and actual Socket.IO `initial_state` were checked
  against its private OAuth, integrity and SDK values in memory. None appeared in those
  responses. This observation is scoped to these responses, not a universal audit.

## Integrated server acceptance and persistence

This separate test used existing authorized test-account state transferred directly in
memory. It did not create local seed/connection exports or use the fresh desktop trial.
Mining was disabled on the integration instance to avoid competing with the existing
miner; its result is authentication/catalog evidence, not new mining-progress evidence.

- At **12:28:24**, the production helper API validated the incoming account/catalog,
  obtained and validated a replacement from the server's Chromium, saved **generation 1**,
  and atomically closed helper admission. The private session file mode was `0600`.
- At **12:29:37**, an independent production transport validated the same account,
  Inventory, and **172 Campaigns** using the saved context.
- A restart restored the session, renewal SDK state and closed admission; a new helper
  connection was rejected. The container was then replaced with the final `2bc7e12`
  image using the same Docker volume, preserving generation and expiry. Authentication,
  worker, SDK and core-client source hashes match the checkout. There is no environment
  flag, password prerequisite, sidecar, or desktop connection in this test.
- The accepted token expires at **15:28:22.900** (`1790436502.9`). Its approximately
  three-hour lifetime differs from earlier one/two-hour observations; the worker uses
  the supplied expiry rather than a fixed lifetime.
- The normal worker's five-minute-early renewal and independent protected requests
  after the original token's actual expiry are **pending**. The observer changes no
  clock, interval, stored session or worker behavior. It was restarted after final-image
  replacement, with the same generation-1 baseline and expiry.

Historical standalone-worker proof, including the original SDK-cookie expiry crossing,
remains in [the earlier record](2026-09-25-sdk-cookie-renewal.md). It is not substituted
for the two pending integrated fresh-login and real-expiry checks above. No release,
multi-day reliability or universal network/browser compatibility is claimed.
