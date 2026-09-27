# Helper-assisted login implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make a native helper the only fresh Twitch login flow: choose TDM, log into local Chrome, send verified state directly, and let TDM renew independently with no local exported files.

**Architecture:** Always initialize the imported session provider, retaining valid saved Android sessions until an explicit helper login replaces them. An atomically persisted `allow_helper_connection` setting defaults to true and controls admission and credential replacement, including a final check after asynchronous validation; successful login closes it automatically. A short-lived, in-memory helper connection delivers a combined session/SDK seed to TDM, where one managed renewal task owns the persisted state and launches Chromium only while renewing.

**Tech Stack:** Existing Python/aiohttp/FastAPI, Chrome DevTools, Alpine Chromium, PyInstaller native builds, pytest and Node frontend tests.

## Authoritative requirements

- Remove `TDM_SESSION_IMPORT` and the mandatory dashboard-password prerequisite. Optional dashboard authentication continues to protect ordinary dashboard routes, but helper admission is governed by the explicit helper setting.
- The helper prompts for or accepts a selected TDM root URL; checks admission before opening Chrome; opens Twitch login in an owned TDM profile; observes a verified session; sends it from memory; waits for server validation and reports a fixed success/failure result.
- Do not write a local session bundle, SDK seed, connection file, credential log, or retained helper profile. Dispose the owned temporary profile after success, failure, or cancellation; do not touch regular Chrome profiles.
- Native executable build and smoke-test coverage for Linux, macOS and Windows. No local Python installation is needed for the binaries.
- Fresh login uses the helper only; remove device-code, credential-form, direct remote-browser and manual-import UI/API entry points.
- Default `allow_helper_connection=true`; persist false only as part of successful login. Reject new connections and manual credential replacements while false, including already connected/in-flight helpers; normal validated server renewal remains available.
- Existing Android cookie data is preserved. An explicit accepted helper login becomes authoritative and safely refreshes authenticated consumers and account-derived runtime state.
- Persist session and SDK cookie together in TDM data storage. Renewal uses the saved state after restart and does not depend on a desktop browser or dashboard password.

## Protocol shared by helper and server

`POST /api/helper/connect`, body `{}`, header `X-TDM-Request: 1`:

```json
{"version":1,"connection":"<short-lived random token>","expires_at":1234567890}
```

The connection is memory-only, expires after 10 minutes, and cannot read existing credentials. No redirects or unsolicited browser-origin requests are accepted.

`POST /api/helper/session`, body is the existing version-1 `ServerSeed` envelope, headers `X-TDM-Request: 1` and `Authorization: Bearer <connection>`:

```json
{"success":true,"session":{"state":"ready","user_id":42,"generation":1,"expires_at":1234567890},"allow_helper_connection":false}
```

The server independently checks Twitch identity/catalog and server renewal before acknowledging success. Errors contain fixed diagnostic codes, never submitted values. Connection and mutation gates are rechecked before persistence. Native requests need no dashboard cookie. Existing origin, request-header, payload-limit and optional dashboard guards remain on other routes.

## Task 1: Gate, atomic state and integrated renewal

Files: `src/auth/imported_session.py`, new `src/auth/helper_connection.py`, `src/web/session_api.py`, `src/web/auth.py`, `src/config/settings.py`, `src/web/managers/settings.py`, `src/web/app.py`, `src/core/client.py`, `Dockerfile`.

1. Add failing tests in `tests/test_helper_connection.py` and `tests/test_helper_api.py` for admission without password/environment variables, disabled/expired/concurrent connections, toggle races, unsuccessful upload preserving state, automatic persistent closure, secret-free responses, and renewal while closed.
2. Run the focused tests and record the expected missing-feature failures.
3. Implement a session-envelope-backed admission controller (settings mirrors its flag; flag, epoch, seed and receipt commit atomically) and a single persisted session/SDK envelope. Reuse the tested `SDKIssuer` and account/catalog validation; no caller-supplied renewal destination.
4. Integrate startup/shutdown renewal ownership into the miner, retaining valid legacy saved login but eliminating fresh device login. Include Chromium in the standard Alpine image so uploaded state is sufficient for unattended renewal.
5. Verify focused and adjacent tests; update README/AGENTS and commit/push the verified checkpoint.

## Task 2: Native direct-connect helper

Files: new `src/auth/login_helper.py`, `login_helper.py`, helper packaging files and workflow, `src/auth/session_helper.py`, `src/config/paths.py` if bundling requires it; new `tests/test_login_helper.py`.

1. Add failing tests covering URL selection, preflight rejection before browser startup, platform Chrome discovery, owned launch/profile cleanup, login wait, capture/send/ack sequence, timeouts, response validation, redirects and absence of local export writes.
2. Implement object-oriented client/launcher orchestration using the existing exporter and shared protocol. Prompt for a URL when none is supplied, show the selected destination, then open Twitch login automatically.
3. Remove the old local export/renew CLI path from the normal workflow. Keep capture/SDK primitives reusable.
4. Package and smoke-test native binaries on Linux/macOS/Windows using a pinned PyInstaller build dependency; test full flow on the available native browser and Docker server.
5. Obtain independent spec and code-quality review; commit/push only verified helper files.

## Task 3: Dashboard helper-only flow and translations

Files: `web/index.html`, `web/static/app.js`, replace `web/static/session-import.js` behavior, `src/web/managers/login.py`, `src/i18n/translator.py`, all locale JSONs; relevant UI/settings tests.

1. Add failing UI behavior tests for helper-only login instructions/status, the default-on setting, persisted updates, automatic closing reflected in the UI, and removal of manual credential/file/device-code entry.
2. Implement concise instructions and a copyable instance URL; the native helper itself performs the upload. Render translations with textContent/DOM creation.
3. Translate all changed strings and keep schema/placeholder parity. Keep optional dashboard-password settings separate from helper permission.
4. Verify focused Node/pytest and rendered browser behavior, then independently review and commit/push.

## Task 4: Integration and completion audit

- Run Ruff, Mypy, the complete test suite, locale checks, `uv lock --check`, release-script tests with GNU utilities, Docker ARM64/AMD64 builds, and native helper build jobs.
- Validate the UI on a new isolated development origin. Follow the repository version/cache-key release workflow before deployment to existing clients; do not publish a release or merge without authorization.
- Run the actual helper against an isolated Docker TDM: admission, native login, memory-only upload, server acceptance, persisted false setting, rejected later helper, successful server renewal with local Chrome closed, and restart using Docker data only.
- Verify no raw credentials appear in dashboard HTTP/socket responses or diagnostic output. Verify rejection and setting races against real API entry points.
- Obtain separate adversarial review of the final changes and resolve findings. Record tested commits and precise platform/live-proof limits.
- Update README, AGENTS and the existing tracking issue as authorized; keep unreleased status truthful. Mark the goal complete only when all explicit deliverables are evidenced.

## Implemented validation checkpoints

These entries record the order of implementation. Earlier pending items are superseded
by the completed live checks below and the [current evidence record](../notes/2026-09-26-native-helper-integration.md).

- Atomic v2 admission/seed/receipt envelope; toggles invalidate ticket epochs, including
  true→false→true. The successful-POST receipt survives closure/restart for ten minutes.
- Initial server proof accepts a fresh shorter lifetime; strict ordinary renewal is retained.
- Independent review found old-account fan-out children and socket callbacks surviving
  cancellation. Production-path regressions failed first; cleanup now cancels and awaits
  children, callbacks and tracked channel checks before identity replacement. Re-review
  independently passed 66 focused tests with no remaining blocker in that scope.
- Native review found cancellation during close and ambiguous upload acknowledgements.
  New regressions failed first; cleanup is shielded and bounded, ordinary POSIX termination
  is translated to cancellation, and receipt recovery covers network/5xx/malformed replies.
  Definite rejection/redirect handling remains distinct. Re-review and rebuild follow.
- macOS ARM64 executable built and passed startup without Python on PATH plus actual
  installed-Chrome launch/cancel/profile cleanup. The corrected source requires a rebuild.
  Cross-platform workflow and new integrated live proof are still pending.

- Full baseline after compatibility-test updates: 628 passed and 2 subtests, using
  Python 3.12 with Node 24. Source Ruff and Mypy passed; uv lock check and diff check
  passed; all three release-script contract suites passed in a GNU/Linux container.
- ARM64 and AMD64 Alpine images built. Both report Chromium 152.0.7977.82; the isolated
  ARM64 development instance at localhost:18081 starts healthy with no environment flag
  or dashboard password and admission true. Its real native-helper login is waiting for
  the user because desktop control cannot see the owned Chrome window (cgWindowNotFound).
- Corrected macOS ARM64 executable rebuilt after cancellation/receipt fixes and passed
  packaged startup, translation, admission and no-export-files smoke. Independent native
  re-review passed 104 focused tests; backend lifecycle re-review passed 66 focused tests.

- Dashboard review found stale login/setting HTTP replies and misleading transient-renewal
  recovery text. Regression fixes isolate authoritative gate events, invalidate older
  status reads, preserve pending checkbox choice, clear expired imported identity without
  disturbing generation-zero Android login, and classify retry vs login-required in the
  backend. Independent re-review passed 64 focused tests with no blocking findings.
- Updated whole-suite run passed 640 tests and 2 subtests under Node 24. Rendered dashboard
  checks used mocked APIs at 1440x1000 and 390x844, including real checkbox interaction,
  failure rollback, simulated acceptance/closure and translated rendering. This is UI
  evidence, separate from authenticated live login.
- Separate real integrated-server acceptance at 2026-09-26 12:28:24 UTC used existing
  authorized test account state transferred only in memory. TDM issued/validated its own
  replacement, saved generation 1 and atomically closed admission. This does not establish
  fresh native-helper handoff. The issued context expires at 15:28:22 UTC; normal scheduled
  renewal and post-expiry checks remain pending.
- Final pre-commit baseline passed 641 tests and 2 subtests, Ruff, Mypy (70 source
  files), lock consistency and whitespace checks. The Chinese mobile screenshot was
  regenerated through the actual language selector and mocked settings events; selected
  language, saved language and rendered text now agree. No product fix was needed.
- The later `71456d3` cleanup checkpoint passed 642 tests and two subtests, both Docker
  architecture builds, independent review, and actual installed-Chrome packaged smoke
  on Linux x64, Windows x64 and macOS ARM64/x64.
- At 2026-09-26 18:37:01 UTC, fresh login and email verification through the packaged
  macOS ARM64 helper completed direct handoff to an empty TDM instance. Server acceptance,
  automatic gate closure, private Docker-only state, owned browser/profile cleanup and
  independent protected account/catalog requests passed; restart retained the same state.
- Separately, the integrated worker persisted generation 2 during ordinary renewal and
  independent protected requests passed at 15:28:29.926 UTC, after the original
  15:28:22.900 expiry. The token and SDK cookie changed while admission stayed closed.
  These are separate fresh-login and expiry test paths, not a single continuous run;
  authenticated login on every OS, multi-day reliability and release remain outside this
  completed implementation checkpoint.
