# Twitch browser login implementation plan

> Execution uses the repository contribution policy, test-first changes, verified commit/push checkpoints, and separate adversarial review.

**Goal:** Preserve working Android sessions and replace unavailable fresh device login with an interactive browser controlled by TDM, tracked in #118.

**Architecture:** Keep the default Android HTTP/GraphQL path for existing sessions. Isolate browser lifecycle, persistent login state, and authenticated requests behind a browser session service. A browser session must be validated against Twitch's actual campaign/inventory operations, not merely accepted because OAuth validation succeeds. Docker must expose a protected interactive browser for user login/2FA and preserve its profile.

**Tech stack:** Python asyncio/aiohttp WebDriver transport, existing FastAPI/Socket.IO dashboard, Selenium Google Chrome container with Xvfb/noVNC, Docker. A separate browser container preserves the Alpine miner and supports ARM64. Firefox is a comparison candidate, not an implemented transport.

## Checkpoint 1: restore existing sessions and consolidate reports

Files: `src/core/client.py`, `tests/test_twitch_auth.py`, `README.md`, `AGENTS.md`.

1. Add a regression demonstrating that default Android host/domain cookies are reused, without a device-code request, and survive restart. Run it against the Smart TV default and observe replacement of the token.
2. Restore `ClientType.ANDROID_APP`; retain explicit legacy device-flow tests and watch/beacon regression coverage.
3. Add the prominent README outage warning and explain the difference between existing Android sessions, Smart TV credentials, and fresh login.
4. Create #118 with evidence boundaries and acceptance criteria. Redirect #109, #112, #114 and #117; close open duplicates without claiming the outage is fixed. Keep unrelated channel-discovery issues open.
5. Run focused auth/watch tests, Ruff/Mypy, locale checks, full tests, lock/diff checks and release-script contracts. Obtain independent review, then commit and push this completed checkpoint.

## Checkpoint 2: controlled browser login and operation boundary

Likely files: `src/auth/browser_session.py`, `src/auth/auth_state.py`, `src/core/client.py`, `src/api/gql_client.py`, corresponding tests, browser dependencies and Docker configuration.

1. Write failure-path tests for browser startup, cancellation, invalid/mismatched identities, protected credential storage, session restore, and absence of secrets in UI/logs.
2. Implement an object-oriented browser service that controls a persistent actual browser. Let the user authenticate on Twitch, with no passwords passed through TDM forms.
3. Use the browser's own successful request context for web-client operations. Keep the Android session independent. Validate identity, inventory and campaign access before advertising successful login.
4. Add a usable interactive Docker login surface and document installation, persistence, restart, and recovery. Do not expose an unauthenticated debugging port publicly.
5. Test browser launch and controlled local fixtures first, then actual Twitch login when a user completes authentication. Confirm campaign eligibility and Twitch-side progress before resolving #118. Record any live blockers precisely.

## Completion evidence

Run the contribution baseline and applicable Docker/browser/frontend/locale regressions on the final revision. Obtain independent adversarial review. Preserve #118 as open while fresh-session live proof is missing; mocked tests and a `Watching` message are not sufficient. Do not merge or release implicitly.
