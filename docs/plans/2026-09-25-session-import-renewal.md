# Session import and automatic renewal implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement manual local-browser export and TDM import, then automatically renew the imported context; prove login, authenticated integrity-dependent campaign access, and access using a distinct automatically renewed token.

**Architecture:** A local helper observes a dedicated Chrome profile over loopback DevTools and exports only a matching, successfully used Twitch request context with its observed expiry. An opt-in imported-session provider validates identity and both catalog operations before atomically replacing its private state. The dashboard accepts manual files and creates a revocable, account-bound renewal credential; the helper uses that credential over HTTPS (or loopback HTTP) to refresh the same provider before expiry.

**Tech Stack:** Python 3.12, aiohttp, existing FastAPI/Socket.IO dashboard, Chrome DevTools Protocol, pytest and Node DOM tests. No new runtime dependency or third-party browser service.

## Design and boundaries

- Continue in `codex/twitch-browser-login`; preserve valid Android sessions and their cookie jar. Browser import is explicitly enabled with `TDM_SESSION_IMPORT=1` and is mutually exclusive with the existing direct browser configuration.
- A versioned JSON bundle contains the complete allowlisted headers, user agent, capture time and observed integrity expiry. No browser-wide cookie export, password, arbitrary destination, or JavaScript is accepted. Parse and transport errors expose stable codes only.
- Save state atomically with owner-only permissions. Validate the WEB client, account identity, Inventory, and Campaigns before replacing a working bundle. Pin the account after first acceptance, reject account changes and older/replayed contexts, and revalidate persisted state at startup. No authenticated request may use an expired or known-rejected context.
- Manual import and renewal management require enabled dashboard password protection and an authenticated session, plus existing origin and write-header checks. Renewal has a dedicated narrowly scoped bearer credential; its exact endpoint may bypass the dashboard cookie, but retains origin/write-header guards, body bounds, and its own authorization. Never expose renewal secrets in status, Socket.IO, logs, URLs or validation errors. Support rotation/revocation.
- Bind first acceptance to any already validated miner account. Refresh auth-state consumers and reconnect websocket subscriptions when OAuth credentials change. A replacement validates outside the state lock, then rechecks account binding, revision, expiry, replay and renewal-credential generation before commit, so revocation and newer imports cannot be overwritten by a delayed validation.
- The helper creates and closes its own tab in a dedicated local profile, correlates the issuance token with an authenticated successful campaign request, and writes an export file with mode 0600. An automatic loop captures a fresh context before expiry, validates it at the destination, retries transient failures with bounded delay, and does not silently switch account or destination.
- User-facing text is translated in all locales. The local browser must remain available for renewal; losing it pauses imported operations when the old context expires. This feature does not solve fresh Docker-browser login or expand supported hosting beyond personal home hardware.

## Task 1: bundle and imported transport

**Files:** create `src/auth/session_bundle.py`, `src/auth/imported_session.py`, `tests/test_imported_session.py`; modify `src/auth/browser_session.py` only to share catalog validation and header names where warranted.

1. Add failing tests for strict parsing, expiration, header injection, private persistence, identity/catalog rejection, same-account replacement, replay rejection, restart, and expiry waiting/shutdown. Run `source env/bin/activate && python -m pytest tests/test_imported_session.py -q` and confirm the missing implementation fails.
2. Implement an immutable secret-redacted bundle, bounded HTTP transport to fixed Twitch endpoints, and the atomic imported-session provider. Preserve prior good state on validation/persistence failure. Repeat the focused tests until passing.
3. Add regression coverage for actual HTTP headers, redirects, no Cookie header, and exception redaction using an isolated test server or transport seam.

## Task 2: manual export and application import

**Files:** create `src/auth/session_helper.py`, `src/web/session_api.py`, `tests/test_session_helper.py`, `tests/test_session_api.py`, `tests/test_session_import_ui.py`; modify `src/core/client.py`, `src/auth/auth_state.py`, `src/web/app.py`, `src/web/auth.py`, `src/web/managers/login.py`, `web/index.html`, `web/static/app.js`, `src/i18n/translator.py`, and all `lang/*.json`.

1. Write failing helper tests with a fake DevTools stream: match issuance and successful authenticated request, reject incomplete/foreign context, bound time and memory, and close only the owned tab. Implement `python -m src.auth.session_helper export --browser http://127.0.0.1:9228 --output session.json`.
2. Write API/auth integration regressions: unauthorized/foreign-origin/malformed/oversized import, failed catalog leaves state intact, expected account and Android preservation. Implement status/import routes and provider selection. Refuse ambiguous simultaneous browser modes.
3. Add upload UI, waiting/expired/accepted status and translated messages. Add DOM tests for text safety, file handling and visible failures. Run focused auth/API/UI tests and locale checks.
4. Use the real local dedicated browser to export a file and manually import through the application. Verify token identity and integrity-dependent campaign response inside the destination container, with no browser there. Record only sanitized metadata.
5. Update README/AGENTS and evidence note. Independently review and push a verified manual-path checkpoint.

## Task 3: automatic renewal

**Files:** extend imported session, helper, API and UI files/tests from Tasks 1–2.

1. Test account-bound credential generation, hashed persistence, rotation/revocation, bearer-only endpoint scope, foreign origins, replay and concurrent replacement. Implement connection-file download and disconnect controls.
2. Test the renewal scheduler using a controlled clock, including early renewal, short remaining validity, transient failure, helper restart, account changes and destination restrictions. Implement `python -m src.auth.session_helper renew --browser ... --connection connection.json`.
3. Verify a running automatic loop produces a different integrity token, the destination accepts it, and actual authenticated Campaigns succeeds under that new token without manual export/import. Record matching fingerprints only in private evidence if needed, not token contents. Prefer checking continued operation after the first token's advertised expiry too; distinguish that from an accelerated scheduling test.

## Task 4: completion audit

1. Integrate current `origin/main` before final review; preserve unrelated work and rerun affected checks after integration.
2. Run Ruff, Mypy, full pytest including Node tests, `uv lock --check`, release contract tests, `git diff --check`, Docker build and rendered import/renewal UI verification. Cached asset deployment still requires the normal version-release workflow; no release is implied by this task.
3. Obtain separate adversarial review of the final implementation and live evidence. Fix findings, rerun affected checks, then commit/push completed checkpoints and update #118 without closing unrelated remaining work.
4. Audit the explicit exit gate against current evidence: imported login validated; imported token passes protected campaign access; a distinct token obtained and delivered by the automatic loop passes the same access check. Leave the goal active if any gate remains unproved.
