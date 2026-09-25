# Server SDK Renewal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** After an initial local login and export, renew accepted Twitch integrity contexts on the server without the user's browser or further interaction.

**Architecture:** Export a narrowly scoped SDK cookie alongside the existing manual import bundle. An optional Python helper launches a private, temporary headless Chromium process, obtains fresh proof through Twitch's SDK, validates identity and protected catalog access, and persists the replacement SDK cookie atomically. Reuse the existing account-bound renewal delivery and scheduling; keep the core miner's image, API and Python dependencies unchanged.

**Tech Stack:** Existing Python/aiohttp/CDP, the existing renewal endpoint, optional server Chromium, pytest with isolated protocol peers. No Puppeteer or new Python package is required by the implementation.

**Task 1 checkpoint, 25 September:** 462 tests and two subtests passed with Node 24;
Ruff, Mypy (67 source files), lock validation and all three release-script suites passed.
The separate `review_android_checkpoint` agent approved the seed/export code after
filesystem-alias, malformed-protocol and test-isolation findings were reproduced and
fixed. This approval does not cover server renewal or the pending live expiry checks.

**Tasks 2/3 checkpoint, 25 September:** 486 tests and two subtests passed; Ruff,
Mypy (68 source files), lock validation and local core/helper arm64 and amd64 Docker builds passed.
The separate review agent approved the implementation after real subprocess regressions
reproduced and fixed process-group and SIGTERM cleanup failures. Two live fresh-profile
Python issuances and a protected API delivery passed. Sustained expiry tests remain
running; see the evidence note for exact boundaries. Task 4 remains in progress.

## Acceptance criteria

- One initial export supplies both the ordinary import bundle and a private server seed.
- Only the SDK cookie for the exact `k.twitchcdn.net` host is included; never export every cookie or profile.
- Expired integrity context may seed issuance while its OAuth and SDK state remain valid; expired SDK state fails explicitly.
- A fresh token must match an observed successful issuance response, differ from the previous context, validate the expected account and pass Inventory plus Campaigns.
- Persist only validated replacements, retain safe diagnostics and clean up every browser process/profile after success, failure or cancellation.
- Use the existing protected renewal endpoint without broadening its scope or exposing credentials through dashboard reads/logs.
- Test repeated server renewal, restart persistence, actual expiry crossing and real TDM operations. Keep the goal open while those checks are pending.

## Task 1: Private seed and one-time export

Create `src/auth/server_seed.py` with strict `SDKCookie` and `ServerSeed` value objects.
The serialized seed contains version 1, an existing `SessionBundle`, and only SDK-cookie
value/expiry. Browser cookie parameters are constructed from fixed name/domain/path and
security attributes. Use `PrivateSessionFile` for atomic owner-only storage.

Extend `BrowserExporter` with `capture_seed()` while preserving `capture()` behavior.
Capture the SDK cookie from the same owned target after successful context correlation.
Add `export --server-seed PATH`; the ordinary `--output` remains compatible with the
current dashboard import. Test absent/expired/foreign cookies, malformed input, secret
redaction, protocol cleanup and unchanged old exports before implementing behavior.

Run `python -m pytest tests/test_server_seed.py tests/test_session_helper.py` from the
activated environment. Update README/AGENTS and obtain independent review before the
first verified commit/push checkpoint.

## Task 2: Browser lifecycle and SDK issuance

Create `src/auth/server_renewal.py`. A bounded browser owner launches configured local
Chromium with a private temporary profile and loopback DevTools port, then closes it on
all exits. A protocol client imports only the SDK cookie and substitutes a minimal
document at Twitch's fixed campaign origin. Load the fixed Twitch SDK and use its patched
fetch. Correlate network response, token and expiry; reject cached, mismatched, failed or
unchanged issuance. Independently validate identity/catalog using `SessionTransport`.

Use fresh isolated protocol peers and mocked process creation for automated tests in
`tests/test_server_renewal.py`; exercise cancellation, startup/SDK timeouts, invalid
cookies, mismatched issuance, rejected catalog and missing replacement cookie. Live
tests must remain separate and use only the authorized disposable account/containers.

## Task 3: Persistence, loop and optional container

Give `RenewalLoop` a structural context-source interface, preserving existing helper
behavior. The server source reads a private seed, obtains and verifies a replacement,
then atomically saves the renewed cookie/context. Reuse `RenewalSender` account binding,
expiry checking, credential handling and retry rules. Add a server helper CLI accepting
seed, renewal connection and Chromium executable paths. Credential data never appears
in arguments or output.

Provide a small optional Dockerfile based on the locally built core image, adding only
Chromium at the OS level. Keep the ordinary Dockerfile and dependency manifests unchanged.
Document the initial transfer and server-only command, loopback/HTTPS destination rules,
private persistent storage and recovery when authorization or SDK state expires.

## Task 4: Verification and completion audit

Run focused tests, Ruff, Mypy, the complete Python/Node suite, lock validation, release
script contracts and applicable Docker builds. Integrate current main before final
review. Obtain separate adversarial review of implementation and live evidence.

Keep the existing autonomous prototype experiment running to its recorded expiry gate.
Test the Python helper separately before substituting it into a mining instance. Prove
its accepted renewal with the original source browser stopped, then verify repeated
delivery, persisted-cookie reuse and operations after expiry. A successful timestamped
token response alone, or an account-progress count without appropriate attribution,
must not be treated as full completion. Commit and push verified checkpoints; leave
#118 open until all remaining scope has evidence.
