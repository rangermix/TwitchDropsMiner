# Telegram Review Fixes Implementation Plan

**Goal:** Resolve PR #18 review findings with regression coverage before merge.

**Architecture:** Keep Telegram credentials server-side and use the existing masked settings API. Deliver notifications from the shared successful claim transition so websocket and inventory claims behave consistently, and repeat events do not send duplicate alerts. Frontend tests execute the real JavaScript functions using the existing Node test helpers.

**Tech Stack:** Python 3.12, asyncio, FastAPI, aiohttp, pytest, vanilla JavaScript, Node.js.

## 1. Regression tests

- Add `tests/test_telegram_frontend.py` for full translation application, saved-token test/save, and HTTP/network save failures (including saving after a successful connection test).
- Extend `tests/test_telegram_integration.py` to exercise real claim methods for direct/inventory and websocket claims, repeat events, and notifier failure isolation. Cover saved-credential API fallback and the notifier transport without sending actual messages.
- Run `source env/bin/activate && python -m pytest tests/test_telegram_frontend.py tests/test_telegram_integration.py -q` and confirm failures match the reviewed bugs.

## 2. Focused fixes

- Pass translations explicitly to the Help builder in `web/static/app.js`.
- Permit an empty token when configured, validate settings responses, propagate save failures, and await save completion after testing. Use locale strings for Telegram UI results.
- Move notification handling from `src/services/message_handlers.py` into the shared drop claim transition in `src/models/drop.py`, retaining failure isolation and one notification per transition.
- Keep the settings API masked, and cover blank chat ID behavior so users can disable notifications.
- Re-run focused tests until they pass.

## 3. Documentation and validation

- Document Telegram setup, stored credentials, disabling, and test coverage in README and all agent instruction files.
- Run the full pytest suite, Ruff, Mypy, lockfile validation, release-script contracts, JavaScript syntax check, and `git diff --check`.
- Obtain an independent review, commit and push fixes to the PR branch, wait for GitHub checks, and merge only the reviewed passing head under the user's existing authorization.
