# Agent Instructions


## Repository Instructions

This file is the canonical harness for AI agents working in this repository.
`CLAUDE.md` and `GEMINI.md` are relative symbolic links to `AGENTS.md` so every agent
reads the same guidance. Maintain all shared and agent-specific instructions here.

## Communication

- Use simple, minimal, clear language in all communication.
- Do not post an issue or PR comment just because you inspected or reviewed it.
  Comment only to request additional information or report an actual state change,
  such as starting work, merging a PR, or shipping a fix in a release.
- When changes are required, list clear action items. Use bullet points when more
  than one change is required.

## Mandatory Contribution Workflow

Every coding agent MUST read [CONTRIBUTING.md](./CONTRIBUTING.md) before planning,
editing, testing, or reviewing changes and MUST follow its applicable requirements.
It is the repository's contribution policy, not optional background reading.

- Use its PR checklist as completion criteria, including integration with current
  `main` for PRs, unit and regression testing, and independent adversarial review.
- Pass this policy to delegated implementation and review agents. For agent-authored
  work, use a separate adversarial review agent when available, or an independent human.
- Report actual validation and review results and any missing checks in the handoff
  or PR. Never claim completion of checks that did not run or approval not received.
- Do not declare a PR ready to merge while required checks or review are missing or
  blocking findings remain. Document the gap and keep an incomplete PR in draft.
- Visual dashboard changes require current screenshots in the PR description, with
  personal information hidden as described in CONTRIBUTING.md. Prefer rendering them
  from mock or sample data; never use screenshots of a real account without masking.
  "Before" images come from the base branch without any PR change. If you cannot attach
  images, host already-redacted images on a separate screenshots-only branch of your
  fork such as `pr-screenshots` (example in CONTRIBUTING.md; never the topic branch;
  pushed images cannot be reliably removed), or state that
  screenshots are pending, keep the PR in draft and ask the user to attach them.

## Development Guidelines

### Issue triage

- `.github/ISSUE_TEMPLATE/bug_report.yml` requires the running app version,
  installation method, hosting environment, dashboard browser/device, reproduction
  steps, expected/actual behavior, redacted evidence, and troubleshooting results.
  Keep the form simple; allow an explanation when evidence is unavailable or browser
  details do not apply. The chooser disables blank issues and preserves a separate
  feature/question/documentation template.
- Read the issue and its comments before asking for missing information. Tailor the
  request to unresolved gaps, avoid duplicating unanswered requests, and distinguish
  feature requests, retired version-specific paths, and confirmed recoveries from
  current defects. Never request credentials, authentication files, whole data
  directories, or unredacted network captures. Preserve the home-hosting support scope.

1. **Testing**:
   - Always add unit tests for backend changes.
   - Frontend changes should have tests if possible.

2. **Code Style & Architecture**:
   - **DRY (Don't Repeat Yourself)**: Codebase must follow DRY principle.
   - **OOP (Object-Oriented Programming)**: Required for all backend code.

3. **Refactoring**:
   - You are authorized to refactor code to align with DRY/OOP principles.
   - **Permission Required**: You MUST ask for user permission before significant refactoring.

4. **Localization (i18n)**:
   - Update translation files if there are changes to UI text or console messages.
   - Frontend translation rendering must use safe DOM construction. Do not inject translated strings with non-clearing `innerHTML`; allowlist any intentional links and build them as DOM nodes.

5. **Documentation**:
   - Update documentation where the change belongs. Update `README.md` only when its
     user-facing overview, setup, login, migration, or links need to change. Put detailed
     behavior, implementation explanations, and troubleshooting in the relevant `docs/`
     guide. Update `AGENTS.md` when agent instructions or architectural constraints
     change; code changes do not require edits to either file automatically.
   - Keep `README.md` short and focused on ordinary users: setup, login, migration,
     and links. Detailed public instructions belong in `docs/`, the source for the
     GitHub wiki. Developer contribution policy belongs in `CONTRIBUTING.md`.
   - Personal development plans, investigation notes, local verification records,
     and captures belong in `.dev-notes/`, which Git and Docker builds ignore.
     Never commit them or put them under `docs/`; this overrides skill templates
     that suggest committing `docs/plans/` or `docs/notes/`. Keep credentials out of
     public documentation, PRs, issues, and logs. Report concise, redacted validation
     results in the PR without publishing personal working records.
   - Keep `CLAUDE.md` and `GEMINI.md` as relative symbolic links to `AGENTS.md`; do not replace them with duplicated text. Put any agent-specific instructions in clearly named sections of `AGENTS.md`.

## Project Overview

Twitch Drops Miner is a Python application that automatically mines timed Twitch drops without downloading stream data. It uses Twitch's GraphQL API and websocket connections to simulate watching streams while tracking drop progress.

**Key Characteristics:**

- Python 3.12+ required
- Web-based GUI using FastAPI and Socket.IO
- Async/await architecture with asyncio
- Session persistence via cookies
- No stream video/audio download (bandwidth-efficient)
- Docker-ready for easy deployment

## Architecture


The application now uses a clean `src/` package structure with clear separation of concerns.

### Project Structure

```text
src/
├── models/          # Domain models (Game, Channel, Campaign, Drop, Benefit)
├── config/          # Configuration (constants, paths, operations, settings, client_info)
├── utils/           # Pure utilities (string, JSON, async helpers, rate_limiter, backoff)
├── i18n/            # Translation system (Translator class, TypedDict schemas)
├── auth/            # Authentication (auth_state for OAuth and token management)
├── api/             # External API (HTTP client, GraphQL client)
├── websocket/       # Real-time updates (websocket connection, pool)
├── web/             # Web GUI (app, gui_manager, api/)
│   └── managers/    # Individual UI managers (status, console, channels, campaigns, inventory, login, settings, cache, broadcaster)
├── services/        # Business logic services (channel, inventory, watch, maintenance, message_handlers)
├── core/            # Core client (Twitch client)
├── drop_history.py  # Claimed drop history store (JSON persistence + CSV/JSON export)
├── exceptions.py    # Custom exceptions
├── version.py       # Version string
└── __main__.py      # Entry point

lang/                # Translation JSON files (20 languages)
├── English.json     # Default/fallback translations
├── Español.json
├── Français.json
├── Deutsch.json
└── ...              # 16 more languages
```

### Core Components

**main.py** - Simple launcher:

- Runs the `src` package as a module using `runpy.run_module("src")`
- All application logic is now in `src/__main__.py`

**src/__main__.py** - Entry point:

- Parses command-line arguments
- Initializes Settings, Twitch client, and WebGUIManager
- Starts the FastAPI web server (uvicorn on port 8080)
- Runs the main asyncio event loop
- Handles signals (SIGINT, SIGTERM on Linux) and exit codes

**src/core/client.py** - Central client (`Twitch` class):

- State machine: IDLE, INVENTORY_FETCH, GAMES_UPDATE, CHANNELS_CLEANUP, CHANNELS_FETCH, CHANNEL_SWITCH, EXIT
- Composes `_AuthState`, `HTTPClient`, and `GQLClient`
- Delegates to service layer for business logic
- Drop progress monitoring via periodic "watch" payloads
- Manages WebsocketPool and maintenance tasks

**src/services/** - Business logic layer (fully implemented):

- `ChannelService`: Channel management and selection logic
- `InventoryService`: Campaign and drop inventory operations
- `WatchService`: Drop mining watch payload logic
- `MaintenanceService`: Periodic maintenance tasks
- `MessageHandlerService`: Websocket message routing and handling


**src/models/channel.py** - Channel and Stream:

- `Channel` class: Twitch channel with online/offline status
- `Stream` class: Active stream with game, viewers, drop status
- Stream URL fetching and validation
- ACL-based vs directory channels
- `Channel.banned` accepts an error-free, matching-channel `user.self.banStatus`
  object with boolean `isPermanent`. Both true and false mean an active ban; false
  identifies a temporary ban. Null explicitly clears a ban. Missing, malformed or
  errored responses retain known state. A temporary ban expires at a valid, timezone-aware
  `expiresAt`; absent or invalid expiry waits for a confirmed null status. Permanent
  bans ignore expiry. This account-specific field comes from the separate raw
  `ChannelBanStatus` query to Twitch GraphQL, not broadcast status or watch failures.
- `ChannelService.filter_banned_channels()` checks every discovery/reload candidate in
  batches of 20 with a ten-second timeout, drains tasks on cancellation/exit, and
  removes confirmed bans before sorting and the tracking cap. Preserve existing
  persisted stream reads and safe-read retry rules. Optional failures do not classify
  unknown channels as banned. Watch eligibility and transport also reject known bans,
  including manual selection and special-category ACLs. Explicit unbans take effect at
  the next discovery/reload check; temporary expiry queues discovery automatically.
  Retain known bans across rediscovery failures and synchronize watched/manual objects
  by channel ID, preserve known bans on optional failures, and keep delayed online checks
  from redisplaying a banned channel. Regression coverage: `tests/test_banned_channels.py`.
  Keep confirmed bans and expiry callbacks across cache recovery until expiry or a
  confirmed null response. Cancel callbacks and clear account-owned ban state on
  identity replacement and shutdown.

**src/models/campaign.py** - Drop campaigns:

- `DropsCampaign`: Campaign with game, timeframe, allowed channels
- Time-based eligibility and progress tracking
- Special Events (`509663`) and IRL (`509672`) are identified by `Game.is_special()`.
  Their campaigns can progress across categories only on live channels in a non-empty,
  enabled ACL. Without an ACL, the streamed category must still match. Explicit
  `ignore_channel_status=True` checks retain their discovery-only status bypass.
- `WatchService.can_watch()` requires the campaign's game in `wanted_games`, a live
  channel, and `campaign.can_earn(channel)`. Special categories bypass the channel's
  drops-enabled flag; regular campaigns still require it. Account eligibility, campaign
  and drop timing, prerequisites, claims, and ignore rules remain enforced.
- Channel priority still uses the streamed category; channels outside `wanted_games`
  retain `MAX_INT` fallback priority. Preserve special-category eligibility when changing
  watch selection; do not reintroduce an unconditional campaign/channel game equality gate.
- `WatchService.should_switch()` allows replacement of an unwatchable current channel
  before comparing priorities. Healthy streams keep the existing priority rules; tied
  `MAX_INT` participants must still take over after an offline or ineligible stream.

**src/models/drop.py** - Drop types:

- `TimedDrop`: Drops with minute requirements and progress
- `BaseDrop`: Base class with claim logic
- Precondition chains for sequential drops

**src/web/gui_manager.py** - Web GUI:

- `WebGUIManager`: Main GUI coordinator
- Composes individual managers for different UI concerns (status, console, channels, campaigns, inventory, login, settings, cache)
- Uses `WebSocketBroadcaster` for real-time Socket.IO updates
- Pure asyncio, no tkinter dependency

**src/web/app.py** - FastAPI application:

- Inventory uses keyboard-operable campaign toggles with `aria-expanded`/`aria-controls`
  and page-local expansion state preserved through progress/settings renders. List view
  keeps compact artwork/title/date rows; card view and saved view preference remain.
  Linked games absent from Mining list have a translated add action, appending to
  the current priority order with case-insensitive duplicate protection. Save only the
  games field and check HTTP/application results. Inventory additions and generic
  Settings saves share an ordered queue with confirmed/draft watch lists and replayable
  add/remove/reorder intentions, so unrelated autosaves cannot undo an addition and
  failures retain later edits. Only initial loads/socket updates replace the watch list;
  partial-save replies preserve it. Disable duplicate submissions and keep actions
  outside the campaign toggle. Coverage:
  `tests/test_inventory_watch_controls.py`. Asset changes need the release cache-key bump.

- REST API endpoints: `/api/status`, `/api/channels`, `/api/campaigns`, `/api/settings`, `/api/session`, `/api/session/finish`, `/api/session/retry`, `/api/session/logout`, `/api/session/vnc`, `/api/reload`, `/api/cache/clear`, `/api/close`, `/api/version`, `/api/history`, `/api/history/export.csv`, `/api/history/stats`
- Socket.IO server for real-time bi-directional communication
- Serves static web frontend from `web/` directory
- Integrates with WebGUIManager via `set_managers()`
- The Settings **Clear All Cache** action discards local campaign, channel, and other
  derived miner state, preserves OAuth login and settings, and then reloads from Twitch.
  It is a recovery and diagnostic action, not a correction for Twitch campaign metadata.
- **Dump diagnosis data** calls dashboard-protected `POST /api/diagnostics`; retain
  origin/CSRF guards, a 4-KiB body limit, allowlisted browser metadata and fixed redacted
  validation/storage errors. There is no upload or file-download route. `Diagnostics`
  in `src/diagnostics.py` owns per-client capture, strict snapshot projection and atomic
  saves in `DATA_DIR/diagnostics` (`/app/data/diagnostics` in Docker). Keep the directory
  0700 and files 0600 on POSIX, reject symlinks, drain cancelled writer threads while
  holding the admission lock, and enforce the ten-second cooldown. Retain ten generated
  files, each at most 8 MiB, with explicit snapshot/truncation counts.
- Capture responses from the miner HTTP transport, authenticated GraphQL/OAuth
  transport, SDK integrity exchange, Twitch websocket, Telegram, version checks and
  proxy verification. Sanitize before retaining any payload; keep two recent samples
  per API operation plus counts. API body capture is capped at 256 KiB, with bounded
  nodes/depth/arrays. New response reads must also be bounded. Non-JSON bodies and
  rejected authentication responses retain only metadata. Never record request headers,
  cookies, tokens, passwords, SDK/browser profiles, verification data, signed URLs or
  formatted logs/tracebacks. Unknown keys/free text and identities use HMAC aliases;
  exports use fresh per-dump aliases. Project snapshot booleans/numbers strictly rather
  than trusting raw model annotations. Clear capture state on account replacement;
  remove the warning-location handler on shutdown. Regression coverage:
  `tests/test_diagnostics.py` and `tests/test_diagnostics_frontend.py`.
- `serve_index()` replaces the `__APP_VERSION__` placeholder in local CSS/JavaScript URLs
  with the application version and serves `/` with `Cache-Control: no-cache`
- Any `app.js` or `styles.css` change requires an application version bump through the release
  workflow before deployment so existing clients receive a new asset cache key
- Now Watching offers a page-local thumbnail toggle, off on each page load. Only an
  explicit opt-in loads a JPEG; channel events refresh it at most once per minute.
  Disabling it clears the image source, and losing the watched channel resets the toggle.
  Keep translated labels, native keyboard activation and `aria-pressed` state in sync.
  The dashboard does not play stream video or audio.
  Use canonical channel `login`/`url` for thumbnails and Twitch links; `name` is a display
  label and may contain localized characters. Keep the card title translated.
  Regression: `test_stream_preview.py`.
- The dashboard header keeps brand, translated tabs and account controls in one row
  from 1280 CSS pixels. Narrower screens give navigation a separate row; screens through
  768 pixels also group account controls and allow account text to wrap. Desktop
  account/renewal text may shorten with ellipsis, but its full translated text remains
  in the DOM and title.
  Update both titles on state changes, including logout and renewal errors. Check all
  20 locales, dashboard logout controls, keyboard tab navigation and narrow viewports
  in a rendered browser. `test_header_frontend.py` and `test_browser_login_panel.py`
  cover full tooltip text and removal of stale account/renewal information.

**src/websocket/pool.py** - WebSocket management:

- Sharded connections (up to 50 topics per socket, max 199 channels)
- Topics: User.Drops, User.Notifications, Channel.StreamState, Channel.StreamUpdate
- Automatic reconnection with exponential backoff
- Message routing to registered callbacks

**src/config/settings.py** - Application settings:

- Games to watch list (auto-populated from available campaigns if empty)
- Available from v2.3.0, `priority_list_only` defaults to true. Disable it to append
  other eligible games to the runtime wanted queue without changing saved Mining list. Both modes use
  the same watch-drop, claim, expiry, mineability, benefit and campaign eligibility
  guards; omit empty campaigns and games. Fallback games sort by earliest remaining
  campaign end, then game name. Selected campaigns take precedence over fallback,
  including Special Events/IRL ACL channels streaming another category. Use
  `ChannelService.get_mining_priority()` for discovery and automatic switching;
  preserve `get_priority()` and strict-mode streamed-category ordering. Failed
  settings saves restore the previous mode and must not trigger policy refresh.
- Successful unclaimed-to-claimed transitions request a queued games update so
  claiming the last wanted reward also releases campaigns with unwanted rewards
  remaining. Failed or repeated claims must not enqueue duplicate updates.
- `tests/test_priority_fallback.py` and `tests/test_priority_fallback_frontend.py`
  cover both modes and their boundaries.
- Games can also be added manually from the web settings search box. Exact and
  unique partial matches resolve to available game names; ambiguous matches do not
  add a game. Confirmations support keyboard focus and Escape. Select All preserves
  priority order and manual entries, and manual confirmation uses current settings.
- The Main page Wanted Drops Queue offers a temporary, runtime-only order:
  `TemporaryGameOrder` (`src/services/game_order.py`) owned by `Twitch.game_order`,
  set through `POST /api/wanted-order` (`{"games": [...]}`, empty list resets; bounded
  and normalized, validation errors never echo input) and broadcast as
  `game_order_update`/`initial_state.game_order`. It only reorders already queued
  games, never adds or removes one, and is applied as the final stable queue sort and
  as the first element of `get_mining_priority()` and active-campaign selection while
  active (inactive keys are unchanged). Rank a channel only by wanted campaigns it
  can currently earn, retaining the special-category or drops-enabled guard. Never
  grant its streamed category's temporary rank without an eligible campaign.
  Restart, Reset, or a successfully saved
  Mining list change clears it; the saved Mining list remains the permanent default.
  Queue cards are drag-and-drop only (press and hold first on touch screens; there is
  no keyboard path by design), keep their top-left `#n` badge and show the Mining
  list's ☰ drag cue; links inside a card cannot start a drag. Shared drag handlers ignore drags that did not start on a row
  of the same list, move a row only once the pointer passes the target's middle (rows
  of different heights must not flip back and forth) and clear `draggedElement` on
  drag end. Touch drags auto-scroll near the screen edge. Games joining the queue
  later rank after the ordered ones. A failed save re-requests the queue. Defer row
  rebuilds from touchstart through the pending hold and active drag; early release,
  scrolling and cancellation apply the newest deferred tree. Cancelled hold timers
  must not activate a later gesture or a detached row. Render optimistically and
  restore the previous order with translated
  `gui.wanted.save_error` on failure. Coverage: `tests/test_wanted_queue_order.py`
  and `tests/test_wanted_queue_frontend.py`.
- Mining list supports drag ordering and editable integer priority numbers. Clamp valid
  ranks to the list bounds; reject blank/fractional values without changing settings.
  Keep priority and remove-control labels translated and accessible. Regression tests in
  `tests/test_game_priority.py` cover order, bounds, invalid inputs, and persistence calls.
- Mining list shows account-link status per game. Linking belongs to the game account,
  so `getGameLinkStatus()` treats a game as linked when any campaign for it reports
  `linked: true` (case-insensitive by `game_name`); games without a known campaign have
  no badge and count as unknown, not unlinked. Available Games sort linked first, the
  All / Linked / Not Linked dropdown filters on known status (Not Linked excludes
  unknown games), and Select Linked appends only missing linked games, keeping the
  existing order and manual entries and ignoring search/filter like Select All.
  Keep filter actions and selected-game rows wrapping, and game panels shrinkable,
  so all controls remain reachable on narrow screens.
  Re-render the lists only when the link-status signature changes (inventory batch,
  campaign add/clear), so inventory updates while mining never interrupt a drag or a
  priority edit. Build badges with safe DOM APIs and keep the five `gui.settings`
  strings (`select_linked`, `game_linked`, `game_not_linked`, `game_linked_title`,
  `game_not_linked_title`) in every locale and in `GUISettings`. Coverage lives in
  `tests/test_account_link_frontend.py` and `tests/test_game_priority.py`.
- Connection quality multiplier
- Language selection
- Proxy support (including verification)
- Logging and dump flags from command-line arguments
- Persistence to JSON file (`settings.json`) in DATA_DIR
- `SettingsManager.update_settings()` validates every field before writing any: a
  `ValueError` leaves settings unchanged, prints no change lines, and skips save,
  broadcast and refresh. `InventoryFilterSettings.validate_update()` is shared with
  the API model. A failed `save()` restores every field from a pre-apply snapshot;
  change lines, language switching and broadcasts run only after a successful save.
  Regressions: `test_rejected_update_writes_prints_and_saves_nothing` and
  `test_failed_save_restores_every_field_and_defers_effects`.
- Drop-name ignore list (`drop_name_blacklist`), empty by default. Entries are literal,
  case-insensitive substrings entered one per line; whitespace and blanks are removed and
  duplicates are casefolded while preserving the first spelling/order.
- Inventory filter bar: search, **Reload Campaigns**, Clear Filters and view buttons on
  top; translated Status and Rewards groups of chip toggles (native checkboxes kept
  focusable, styles scoped to `.inventory-filters`) and the Account link select below.
  **Reload Campaigns** lives there and beside the Mining list's Add Game button, not in
  Settings → Actions; both `.reload-campaigns-btn` buttons share one `/api/reload` request.
- Inventory filters (Status, Benefit Type, Game Search); Active/Upcoming/Expired use
  OR semantics; All / Linked / Not Linked narrows the result using Twitch-reported
  linkage, independently of mining eligibility. Finished opts claimed campaigns in.
  Persist `inventory_filters.link_status`; migrate the previous `show_only_not_linked`
  checkbox before merging defaults. The older `show_not_linked` key stays ignored.
- `allow_unlinked_campaigns` defaults false. Only explicit boolean true bypasses the
  campaign account-link gate; preserve raw `linked` metadata, badge/emote eligibility,
  timing, ACL/category, Mining list, prerequisites, claims, and ignore checks.
  Changes trigger game selection refresh. Keep the warning and save-failure feedback
  translated and safely rendered. Estimated minutes do not prove Twitch progress.
- Zero-minute subscription rewards are omitted from Inventory and Wanted Drops Queue;
  individually expired and non-mineable rewards are omitted from the queue without hiding
  upcoming or sequential rewards; successful claims refresh the queue immediately; the
  actively watched channel remains visible while game settings are changing
- Consecutive identical no-active-campaign console prompts are collapsed until another
  console message appears
- The dashboard pause (`POST /api/pause`, `Twitch.set_paused`) is runtime-only and closes
  the same `is_mining_time()` gate as mining hours, so every watch/refresh guard applies.
  Durations are `10m`, `1h` (monotonic elapsed time, unaffected by wall-clock or DST
  changes), `tomorrow` (next miner-local midnight) or none (until resumed). The schedule
  loop ends timed pauses and wakes for them. Project fixed-duration resume times from
  the remaining elapsed time; a clock correction must not expire or extend the pause.
  Status shows Paused/Paused until. Restarting clears the pause; already-earned reward
  claims remain available, and mining hours still apply when a pause ends.
  Increment the server pause revision only for state changes and send it in API/socket
  state with a random, runtime-only instance ID created once per Twitch client, never
  derived from account data. Ignore older replies/broadcasts, including delayed initial
  snapshots from the same instance. A new instance's initial snapshot resets revision
  tracking and invalidates pending replies from the previous instance. Validate present
  instance IDs before mutation and retain the fallback for older servers without IDs
  or revisions. Show only translated, fixed pause errors,
  never remote response or exception text. Preserve deliberate focus moves while a
  pause request is pending. Arrow keys, Home and End navigate the duration menu;
  Escape restores the button and Tab closes the menu without trapping focus.
  Regressions: `tests/test_pause.py` and
  `tests/test_pause_frontend.py`.
- Mining hours default to `always`; `range` uses the miner host's local time (Docker `TZ`),
  inclusive start/exclusive end, supports overnight windows, and treats equal times as all day.
  Validate complete `mode`/`start`/`end` objects before live changes or loading saved values;
  failed persistence restores the previous schedule. The UI uses a dedicated save with
  required time inputs and translated errors. Never silently default an invalid range.
  The schedule task enforces closing and reopening, wakes on policy edits, resamples wall
  time at least every minute, and is cancelled and awaited during authentication changes
  and run exit. Gate watch selection, playlist/Spade freshness, and progress polling too.
  Defer pending inventory/policy/cache requests while closed without setting a busy-loop
  event; fetch fresh inventory on reopening. Existing websocket claims remain available.
  Regressions live in `tests/test_mining_hours.py` and `tests/test_mining_hours_frontend.py`.
- Telegram drop notifications use a bot token stored server-side. The web API/socket
  never echoes the stored token: `get_settings()` returns only a `telegram_configured`
  flag and a masked placeholder, console logs mask the value, and a submitted value equal
  to the mask (or empty) leaves the stored credential untouched.
- `mask_url_password()` (`src/utils`) replaces a proxy URL's password with a mask in
  `get_settings()`, console change logs, the startup log and websocket connect logs.
  `SettingsManager.resolve_proxy()` restores the stored URL only when the exact masked
  form is submitted (settings saves and verify-proxy); any other value containing the
  mask is rejected before any other setting changes, so the stored password never
  reaches another host and the mask is never saved as a password. Set Proxy
  (`saveProxy()`) saves only `proxy`, checks the reply and restores the previous value
  with translated `gui.settings.proxy_save_error`; generic autosaves never send `proxy`.
  Coverage: `tests/test_proxy_frontend.py`. Verify-proxy and Test Telegram return fixed failure text;
  Telegram logs only exception class names because aiohttp errors can embed the bot URL.
- Telegram alerts originate in the shared `BaseDrop.claim()` successful unclaimed-to-claimed
  transition, covering websocket, startup, and inventory-refresh claims without duplicate
  alerts for repeated events. Telegram failures must not change a successful Twitch claim.
- The Telegram form reuses the saved token when its input is blank. Clearing the chat ID
  and saving disables alerts. Test Connection waits for settings persistence before showing
  success; HTTP, network, and application save failures must remain visible as errors.
- Discord alerts also originate in the shared `BaseDrop.claim()` transition and must
  preserve Telegram, history and successful Twitch claims on notification failure.
  Claim embeds may show the first usable benefit image from HTTPS
  `static-cdn.jtvnw.net`, with no credentials/query/fragment, a 2048-character bound
  and text-only fallback. Never download artwork on the miner or forward arbitrary
  hosts, signed URLs or user data. Keep all bundle reward names and image fields intact
  through embed text escaping. `tests/test_discord_reward_images.py` covers this path.
  `DiscordNotifier` accepts only `https://discord.com/api/webhooks/ID/TOKEN`, refuses
  redirects, disables mentions, bounds transport to ten seconds, and never logs URLs,
  response bodies or exception text. Diagnostics retain only status/outcome metadata.
  Settings expose a mask/configured flag, reuse a blank URL, and use the explicit
  `discord_remove_webhook` action to delete it and disable alerts. Validate before
  mutation; restore the previous webhook on save failure. API validation errors must
  not echo submitted settings. Test Connection reports success only after saving.
  Preserve localized safe Help rendering and `tests/test_discord_*` coverage.
- `tests/test_telegram_frontend.py`, `tests/test_telegram_api.py`, and
  `tests/test_telegram_integration.py` cover translated Help rendering, stored credentials,
  failed saves, disabling, all shared claim paths, and transport failures without sending
  real Telegram messages. Keep Telegram UI result strings in every locale.

Drop-name ignore policy is dependency-aware: a matching unclaimed drop and its dependent
branches are ignored dynamically. Prerequisite-only branches with no mineable reward are
skipped, while shared prerequisites required by an allowed reward remain mineable. Ignored
and skipped drops are never counted as claimed. Twitch can still award simultaneous
progress to an ignored drop while the miner intentionally targets another reward.

### State Machine Flow

1. **IDLE** - Waiting for campaigns or user action
2. **INVENTORY_FETCH** - Fetch campaigns from GraphQL, claim completed drops
3. **GAMES_UPDATE** - Determine wanted games based on priority/exclude lists
4. **CHANNELS_CLEANUP** - Remove channels not streaming wanted games
5. **CHANNELS_FETCH** - Discover channels via ACL lists or game directories
6. **CHANNEL_SWITCH** - Select best channel to watch based on priority/ACL
7. Loop between CHANNEL_SWITCH and periodic INVENTORY_FETCH (hourly)

### Authentication and integrated browser login

- The temporary interactive Chromium profile disables password saving before startup.
  Its bundled completion extension observes only Twitch login-cookie presence, closes
  the owned profile's windows normally, and never exports cookie values. Treat this as
  a completion hint: retain all capture/PID/account/catalog checks and manual Finish.
  Grant cookie host access to both `*://twitch.tv/*` and `*://www.twitch.tv/*`
  so root-domain events and non-Secure cookies' HTTP event URLs are visible. Keep
  lookups on HTTPS and do not broaden access to subdomain wildcards or unrelated hosts.
  Cookie scope and presence trigger completion regardless of its Secure attribute;
  they are not proof of a valid session. Test domain-wide and host-only cookies in real
  Chromium with synthetic data, including Secure and non-Secure domain cookies.
  Disable extensions during capture. Clipboard transfers are explicit, bounded to
  65,536 characters, page-local and cleared on send/panel close/viewer disconnect; remote
  clipboard events are ignored unless the panel is open. Never log or persist them.
  Keep preference-content and launch-argument tests portable: assert POSIX file modes
  only on POSIX and mock UID lookup in argument-only tests; retain real Linux isolation tests.
- SDK integrity issuance uses a separate header contract with a fresh 32-character
  client request ID and canonical X-Device-Id, while renewed GraphQL contexts retain
  the original allowlisted headers and never store that request ID. Keep downstream
  catalog validation mandatory; mocked issuance does not prove Twitch accepts a proof.
- `OwnedChromium` uses `xvfb-run` when available for server SDK issuance and renewal,
  with a private authenticated display (1280×800) and ordinary 1200×760 browser window.
  Docker includes `xvfb-run` and `xauth`. Never reuse the interactive VNC display or
  disable X authentication. Keep profiles owner-only, CDP on the private pipe (POSIX)
  or loopback (Windows), and process-group cleanup on failure/cancellation. A page
  session ends when Chromium reports it detached, and all pipe sessions share command
  ids. Keep Xauthority inside the owned temporary profile
  so forced shutdown removes it too. Hosts without the runner retain the headless fallback.
  On Linux that fallback sets `OwnedChromium.headless_fallback` and logs the translated
  `login.headless_renewal` warning (install `xvfb`/`xauth` or use Docker) on every start.
  `SessionController._phase()` relabels a `CATALOG`/`integrity` failure at
  `issued_validation` or `renewal_validation` as reason `headless_fallback` when the
  issuer reports it; `captured_validation`, macOS and Windows keep their labels.
  `tests/test_session_controller.py` and `tests/test_server_renewal.py` cover this.
  A valid integrity response alone does not prove account/catalog acceptance.

- Helper admission preserves allowlisted validation codes without printing remote text.
  Catalog rejection logs contain only fixed error categories and field-shape labels.
  Preserve redaction, strict catalog validation, and lost-ack receipt reconciliation.
  `SessionDiagnostic` owns fixed code/stage/reason/operation labels and separate dashboard
  and upstream HTTP statuses. Keep `/api/helper/*`'s existing `detail` strings and add only
  sanitized metadata; helper/browser/renewal status exposes the same diagnosis. Distinguish
  captured-session validation, server issuance, issued-session validation and persistence.
  Unknown upstream causes stay `unknown`; never expose arbitrary text, values or exception
  messages. Generic/transport/5xx failures can have an unknown post-upload outcome; query
  the receipt without repeating the upload. A newer attempt must clear older diagnostics.
  Keep transient renewal retries and cancellation semantics. CLI diagnostics use the
  invariant `SESSION_DIAGNOSTIC` machine marker with JSON; existing guidance stays translated.
  Tests in `test_session_diagnostics.py` and `test_helper_diagnostics.py` cover redaction,
  code completeness, admission phases, storage, cancellation and renewal errors.

- `ImportedSession` owns validated WEB contexts and reads persisted formats 1/2/3.
  Accepted browser state takes precedence over valid saved Android cookies. Fresh,
  expired or rejected sessions wait for the integrated container browser. Do not
  restore legacy cookies after explicit logout. Version 3 atomically persists a
  logged-out marker with cleared session/SDK state; successful login clears it.
- `ContainerLogin` owns one temporary attempt with a 15-minute interactive timeout.
  Launch ordinary Chromium at Twitch sign in without remote automation, close its
  window normally to flush storage, then reopen the owned profile for CDP capture.
  Verify the browser PID before reading credentials. Finish sign in uses EWMH close;
  the window X works too. Profiles and all owned processes must be drained through
  cancellation, startup failures, timeout and shutdown. Never attach another profile.
- `SessionController` validates the captured account and catalog, independently issues
  a server context, revalidates the same account and atomically installs the seed.
  Account replacement and logout use `Twitch.authentication_change()` to cancel AND
  await miner, watch, maintenance, websocket callbacks and tracked channel/UI tasks.
  Clear old topics and derived account state before resuming. Epoch/revision checks
  prevent delayed login or renewal from reinstalling credentials after logout.
- The renewal worker rotates SDK state, normally five minutes before expiry, with
  bounded retries and same-account/freshness validation. Mining GraphQL stays in
  Python. Preserve safe-read retry rules; never replay ambiguous mutations.
- On POSIX, `OwnedChromium` (renewal/SDK issuance, run as root) uses
  `--remote-debugging-pipe` and never opens a DevTools TCP port: CDP has no
  authentication, and the `tdm-browser` login UID shares loopback. A small exec
  launcher remaps the pipe to fds 3/4 immediately before Chromium, after the private
  `xvfb-run` wrapper when present. The wrapper may overwrite/close fd 3; carry pipe
  ends through it on collision-safe descriptors at or above 64, closing parent copies
  and partial allocations on failure. Preserve the owned process group. Xvfb and the wrapper
  also inherit copies, so a Chromium crash reaches EOF once the wrapper cleans up.
  `DevToolsPipe` multiplexes flattened `Target.attachToTarget` sessions with
  NUL-delimited messages, bounds each session's backlog to 16 MiB by bytes (one read
  can carry hundreds of small events), and closes its target and fds on every exit.
  Once a protocol reader ends, new commands must fail with `BROWSER_PROTOCOL`
  before sending, including after target detachment.
  Windows keeps the loopback port (no `pass_fds`). The login capture Chromium keeps
  its port because it runs as `tdm-browser` with a profile that UID already owns.
  `tests/test_browser_pipe_wrapper.py` covers descriptor-closing wrappers and partial
  allocation cleanup. `tests/test_server_renewal.py` checks launch arguments and descriptors, and, with a
  local Chromium on Linux, that the browser process group opens no TCP listener.
  Python validation runs on Ubuntu 24.04 and 26.04 to retain both display-wrapper
  behaviors; do not replace this with one moving ubuntu-latest label.
- `SessionAPI` exposes sanitized status and finish/retry/logout actions, plus optional
  desktop-helper enable/cancel actions guarded by dashboard authentication and CSRF.
  There is no general session upload/export or renewal HTTP route. The binary
  `/api/session/vnc` WebSocket connects only to the current attempt’s loopback VNC
  listener, requires the exact dashboard origin and optional dashboard session, and
  rechecks authorization/attempt/state while connected. Cap viewers and input frames.
  All login HTTP actions retain CSRF/origin guards and fixed redacted error codes.
- The interactive desktop runs as dedicated UID/GID `tdm-browser`, with supplementary
  groups cleared and only PATH/timezone/locale environment inherited. Protect miner
  data and logs with mode 0700 and verify it took effect before starting VNC; fail
  closed on mounts that ignore chmod or give the browser ownership. Keep temporary
  display/profile directories owned by the browser UID. Root-only SDK profiles remain
  unreadable to it. Retain tests for file-access isolation and pre-login password setup.
- Docker includes Chromium, Xvfb, openbox, x11vnc, xdotool, noVNC and timezone data.
  Set `TZ` to the home internet connection’s timezone. Use init, shared memory and
  a cleanup grace period. Only the dashboard port is published. Display numbers are
  allocated by Xvfb to avoid stale fixed-display locks after engine restarts.
- The frontend shows the VNC sign in page when Twitch is logged out, keeps verification
  visible until cleanup, then restores the normal dashboard. Settings ends with Twitch
  logout. The dashboard-password form is reparented into the sign in screen while
  logged out, preserving its single form and listeners. Embedded login stays the default;
  desktop helpers are an explicit fallback. Detailed user
  guidance lives in `docs/authentication.md`; private tests and credentials stay ignored.
- `SessionAPI.viewer()` drains its bridge tasks, closes the VNC connection and releases
  its viewer slot on disconnect or cancellation. Its final WebSocket close must tolerate
  `WebSocketDisconnect`, including Starlette's conversion of transport `OSError`.
  Preserve regression coverage for abrupt viewer closure, task cancellation and
  propagation of unexpected close errors; disconnecting a viewer does not end login.
- Preserve matching WEB client/device/token/integrity/user-agent for imported requests,
  `Channel.url` on WEB for beacon discovery, locale/schema parity and safe DOM rendering.
  Never include session, SDK, password or verification data in dashboard status/logs.
- `LoginFormManager` may publish the account's Twitch avatar as an optional `avatar_url`
  in login status. It is fetched once per account change through the authenticated
  `currentUser` GQL raw query (`GQLRawQuery`), accepted only when it is an `https://`
  URL, and rendered by the frontend as a CSS background on `#user-avatar` with the
  initial-letter fallback preserved. It must never be sourced from or expose credentials.
  Avatar requests are optional, bounded to ten seconds, attempted once per account
  context, and accepted only for the matching `currentUser.id`. Track and cancel them
  when login clears; cancel AND await them before identity replacement and shutdown.
  Keep exception details out of avatar logs. Preserve responsive wrapping of header
  navigation and account controls at tablet widths.
- `HelperConnections` owns only temporary admission and receipts. It must call the
  shared `SessionController.accept()`; never add a second renewal worker or bypass
  current account validation, authentication-change draining, or logged-out persistence.
  Dashboard enable cancels and awaits the container attempt, then opens a ten-minute
  window for one helper. Native `/api/helper/connect` accepts an empty JSON object
  without a pairing code and issues the first client a scoped ticket;
  `/api/helper/session` submits once and `/api/helper/result` recovers
  a lost acknowledgement. These exact method/path pairs bypass dashboard cookies
  but retain origin/Fetch Metadata/request-header checks and bounded bodies. Upload
  and result routes require the issued bearer ticket. Admission is closed by default,
  requires an explicit dashboard action, and never uses a persistent enable flag.
  Atomically recheck the live window after parsing before issuing the one ticket.
  Recheck admission expiry and initiating dashboard authorization before installation.
  Cancel, logout, shutdown, replacement login and new admission invalidate stale work.
  In-memory receipts are intentionally lost on restart; never replay ambiguous uploads.
  Keep tickets out of URLs, CLI arguments, logs, status, broadcasts and storage.
  Document that the first reachable helper is admitted during the trusted-network window.
  `tests/test_helper_admission.py` and `tests/test_desktop_helper_api.py` cover these boundaries.
- The desktop helper verifies HTTPS using the helper computer's trust store by default.
  `--insecure` is an explicit, per-run opt-out only for `HelperHTTP` requests to the
  selected TDM instance. Show the translated `helper.insecure_warning` before connecting
  whenever enabled, including failed attempts. Never persist the opt-out, retry TLS
  failures with verification disabled, alter global SSL defaults, or change browser,
  Twitch validation, miner, admission, redirect or receipt-reconciliation checks.
  Keep `helper.insecure_help` and `helper.insecure_warning` in every locale.
  `PrivateResolver` limits `--insecure` to `https://` destinations whose host resolves
  only to 10/8, 172.16/12, 192.168/16, 127/8, ::1, fc00::/7 or fe80::/10 (IPv4-mapped
  forms are unwrapped). 100.64/10 stays excluded because ISP customers share it.
  `HelperHTTP` vets once before connecting, then the connector resolves only the vetted
  host to those pinned addresses. Refusals use the fixed `HELPER_INSECURE_TLS` code
  without echoing the URL, after the warning. Do not widen the address list or apply
  the opt-out elsewhere without user approval. Coverage: `tests/test_helper_tls.py` and
  `tests/test_helper_insecure_tls.py`, both in the four-platform helper test list.
- Keep the released helper instructions and download links aligned with the four build
  targets: Windows x64, Linux x64, macOS arm64, and macOS x64. Users choose the helper
  computer's platform and match the miner release. The login screen's **Use desktop
  helper** action shows only the dashboard URL; do not reintroduce a pairing-code or
  dashboard-password prompt in the helper. **Return to embedded browser** revokes access.
  Explain the ten-minute, first-helper admission window and retain the embedded browser
  as the default. On macOS, users must quit the helper-owned browser instance to flush
  its profile. Keep the release-ready README, installation examples, login guide, and
  generated release-note instructions consistent with these behaviors.
- The helper's optional `--ca-file PATH` adds CA certificates for its TDM connection
  only, through `HelperHTTP.trust()`: system trust via truststore plus the file's
  certificates. Verification and hostname checks stay on. It is mutually exclusive
  with `--insecure`. Only certificates passing OpenSSL's CA check (normally `CA:TRUE`)
  are loaded, because truststore passes only those to Windows/macOS verification;
  missing, unreadable, non-PEM, key-only or server-only files fail before connecting
  with the fixed `HELPER_CA_FILE` code. Never print the path or file contents; the CLI
  prints only the fixed `ca_file_in_use` note, for HTTPS destinations.
  `tests/test_helper_ca_file.py` runs real handshakes and is in the native helper
  workflow's four-platform test list; keep its certificates within Apple's TLS rules
  (serverAuth, short validity) and generated through config files that macOS LibreSSL
  also accepts.

### Dashboard authentication

- `src/web/auth.py` owns optional password-only dashboard protection, separate from Twitch
  OAuth and ordinary settings. It defaults off and stores a salted scrypt hash and SHA-256
  session-token digests in `data/web_auth.json` using atomic replacement; corrupt state must
  fail closed. Never expose these credentials in settings, broadcasts, validation errors,
  logs, or cache operations. Use one miner process per data directory.
- `AuthMiddleware` guards FastAPI and the outer Socket.IO ASGI app. Login resources,
  auth status, and `/healthz` are public when enabled. Login actions and the VNC
  viewer require the dashboard session when protection is enabled. Unsafe HTTP requests require
  `X-TDM-Request: 1`; writes and Socket.IO reject foreign origins. `DashboardOrigin` in
  `src/web/origin.py` owns the optional `PUBLIC_BASE_URL` startup configuration: one
  absolute HTTP(S) root URL supplies the allowed browser origin and cookie scheme even
  behind an HTTP backend or rewritten Host. Normalize host/scheme/default ports and IP
  serialization to browser origins; reject ambiguous short/octal/hex IPv4 forms and
  credentials, paths, queries, fragments, wildcard/list origins, and malformed values
  without echoing them. Unset/empty preserves request-derived behavior. This configuration
  must not change ASGI scheme/client or trust forwarded headers; client-IP forwarding
  still requires explicit trusted proxies. HTTPS public URLs enable Secure cookies on
  set and delete. Preserve CSRF, Fetch Metadata, session/revocation, and rate-limit checks.
  `tests/test_web_public_url.py` covers both Socket.IO transports, production environment
  wiring, cookie lifecycles, hostile origins, and separate proxy-IP trust.
- `DashboardOrigin.trusted_host()` blocks DNS rebinding before any other check, for HTTP
  and websockets, with fixed `403 invalid_host` (only liveness-only `/healthz` is exempt;
  the rejection warning is logged at most once a minute). Accept only IP literals, `localhost`,
  `*.localhost`, single-label names, `.local`/`.home.arpa`/`.internal`, the
  `PUBLIC_BASE_URL` host and the optional comma-separated `ALLOWED_HOSTS` names (no
  schemes, ports or wildcards; invalid values stop startup without echo). Remove one
  trailing DNS dot from both configured and request hosts for this check, while preserving
  browser origin serialization and its strict origin checks. Never derive
  trust from the Host header alone. Private responses other than `/session-viewer`
  carry a CSP (`script-src 'self' https://cdn.socket.io`, `frame-ancestors 'none'`);
  every response, static files included, carries `nosniff`. The CDN Socket.IO script
  keeps its SRI hash. `HelperAPI` keeps its own
  rate-limit bucket so helper probes cannot exhaust password logins. Regression
  coverage: `tests/test_security_hardening.py`.
- Default cookies are HttpOnly, SameSite=Strict session cookies. Remember me adds a fixed
  30-day Max-Age; server sessions also expire after 30 days and survive restarts. Logout
  revokes the current session; password changes require the current password and revoke
  other sessions. Disabling auth requires the current password and clears all credentials.
- `AuthSocketServer` rechecks authorization on events and broadcasts, disconnects revoked
  sessions, and schedules idle connections to close at expiry. Enabling auth must evict
  already connected anonymous clients before subsequent private broadcasts.
- `web/static/auth.js` owns login/settings behavior and adds the same-origin write header.
  A failed initial auth-status request must leave login available for retry without a
  reload; settings controls stay disabled until auth state is known.
  Keep all UI strings in `gui.auth` across all locales and render them using textContent.
  Local auth assets use the release version cache key; bump through the release workflow
  before deploying changes to existing auth assets, as with app.js and styles.css.
- `tests/test_web_auth.py` and `tests/test_web_auth_frontend.py` cover access control,
  credential persistence, cookie lifetimes, CSRF, rate limiting, revocation, and UI errors.
  The idle socket-expiry regression controls the auth wall clock and captures the
  scheduled callback. Preserve its remaining-lifetime, disconnect, and cleanup assertions;
  do not replace them with millisecond session lifetimes or fixed wall-clock sleeps.
  Docker checks `/healthz`, not the protected `/api/status`. Recovery is local: stop the
  miner, restrict access, remove only `data/web_auth.json`, restart and set a new password.

### Drop Mining Mechanism

- `Channel.send_watch()` polls the lowest-bandwidth HLS playlist and sends HEAD requests
  for every new media segment, without downloading stream audio or video. `WatchService`
  polls about every ten seconds, within the rolling playlist window.
- Deduplicate successful segments in a bounded 256-entry cache per channel/broadcast;
  retain it and the cached playlist URL across same-broadcast metadata refreshes. Reset
  on a new broadcast; account replacement drains watch tasks and derived channel state.
  Retry failed segments, refresh expired playlist URLs, and stop stale batches when the
  selected channel or broadcast changes or goes offline. Bound the entire poll and
  each request; propagate cancellation and process exit. Never log signed URLs or bodies.
- Spade minute-watched telemetry remains auxiliary and runs at most once per 59 seconds.
  Its HTTP 204 response and a Watching label do not prove Twitch has credited progress.
  Keep fallback CurrentDrop queries and estimated-minute bumps on the existing minute
  cadence when changing playlist polling, including across restarts/channel events.
- `WatchService.watch_loop` schedules progress queries with its monotonic deadline,
  independently of the GUI's remaining-seconds value. That value represents total reward
  time and does not count down on the server. Keep successful-watch, current-broadcast,
  mining-hours and cancellation guards. The real `CampaignProgressManager` regression
  covers positive remaining time, exact query cadence, failed watches and stale streams.
- Twitch reports authoritative progress through User.Drops websocket events and GraphQL
  Inventory/CurrentDrop. Distinguish those readings from dashboard estimates and mocks.
- `DropsCampaign.progress_revision` advances on every `TimedDrop.update_minutes()`
  observation, including unchanged values, and before awaiting notification work on a
  successful claim transition. Sibling progress shares this revision. CurrentDrop polling
  snapshots drop identity and campaign revision before awaiting Twitch; discard replies
  superseded by observations, claims or model replacement/removal without estimating a
  minute. Snapshot the fallback campaign before the request too; null, errored or timed-out
  replies may estimate only against that same campaign and unchanged revision. Fresh
  polls can still correct progress downward. Keep this separate from estimated-minute
  bumps. Regression coverage: `tests/test_stale_progress.py`.
  Playlist/transport, lifecycle, and controlled-clock regressions live in
  `tests/test_playlist_watch.py`, `tests/test_watch_transport.py`, and
  `tests/test_watch_poll_cadence.py`.

### GraphQL Operations

Persisted operations are defined in `src/config/operations.py` as `GQL_OPERATIONS`; raw GraphQL payloads such as `sendSpadeEvents` use `GQLQuery`:

- **Inventory** - Fetch in-progress campaigns and claimed benefits
- **Campaigns** - List available active/upcoming campaigns
- **CampaignDetails** - Detailed drop info for a campaign
- **GameDirectory** - Find live streams for a game with drops enabled
- **GetStreamInfo** - Check if channel is online and get stream details
- **CurrentDrop** - Query currently mined drop progress
- **ClaimDrop** - Claim a completed drop
- **AvailableDrops** - Check which campaigns a channel qualifies for (badge validation)
- **NotificationsDelete** - Delete Twitch notifications

### Channel Selection Priority

1. Selected channel (if user clicked one)
2. ACL-based channels over directory channels
3. Game priority order (from settings)
4. Viewer count (descending)
5. Maximum 199 channels tracked simultaneously

### Maintenance Task

Runs in background to trigger:

- Channel cleanup when drops start/end (based on time_triggers)
- Inventory reload every ~60 minutes

### Translation System

**Architecture:**

- All translations stored as JSON files in `lang/` directory (20 languages supported)
- English (`lang/English.json`) is the single source of truth and fallback language
- Strongly typed with TypedDict schema defined in `src/i18n/translator.py`
- Translator class (`src/i18n/translator.py`) handles language loading and fallback
- Singleton instance `_` available via `from src.i18n import _`

**Supported Languages:**

- English, Dansk (Danish), Deutsch (German), Español (Spanish), Français (French)
- Magyar (Hungarian), Indonesian, Italiano (Italian), Nederlandse (Dutch), Polski (Polish), Português (Portuguese)
- Română (Romanian), Türkçe (Turkish), Čeština (Czech)
- Русский (Russian), Українська (Ukrainian), العربية (Arabic)
- 日本語 (Japanese), 简体中文 (Simplified Chinese), 繁體中文 (Traditional Chinese)

**Translation Structure:**

```python
Translation = {
    "language_name": str,      # Display name of language
    "english_name": str,       # English name of language
    "status": StatusMessages,  # Console status messages
    "login": LoginMessages,    # Login-related messages
    "error": ErrorMessages,    # Error messages
    "gui": GUIMessages        # All web GUI text (tabs, settings, help, etc.)
}
```

**Usage:**

```python
from src.i18n import _

# Access translations
status_text = _.t["gui"]["status"]["idle"]  # Returns "Idle"
login_text = _.t["login"]["status"]["logged_in"]  # Returns "Logged in"
```

**Language Persistence:**

- Language selection persisted in `settings.json` (DATA_DIR)
- Dynamic language switching supported in web GUI
- Changes take effect immediately without restart

## Key Files

- **src/config/constants.py** - Core enums (State, WebsocketTopic), logging config, type aliases
- **src/config/operations.py** - GraphQL operation definitions (GQL_OPERATIONS)
- **src/config/paths.py** - Path management and Docker environment detection
- **src/config/client_info.py** - Twitch client info (Client-Id, User-Agent)
- **src/config/settings.py** - Application settings with JSON persistence
- **src/exceptions.py** - Custom exceptions (MinerException, ExitRequest, RequestException, RequestInvalid, WebsocketClosed, LoginException, CaptchaRequired, GQLException)
- **src/drop_history.py** - Claimed-drop history store (`DropHistory`) with atomic JSON
  persistence, filtering, stats, and CSV export; recorded on every successful drop claim
- **src/utils/** - Helper utilities (string_utils, json_utils, async_helpers, rate_limiter, backoff)
- **src/i18n/** - Internationalization package with TypedDict schema and Translator class
  - **translator.py** - Translator class with typed translation schema (Translation TypedDict)
  - **__init__.py** - Exports translation types and `_` (Translator instance)
- **lang/** - Translation JSON files for 20 languages (English.json is the single source of truth)
- **src/version.py** - Version string
- **src/web/app.py** - FastAPI application with REST API and Socket.IO
- **src/web/managers/cache.py** - ImageCache for campaign artwork caching
- **web/** - Frontend assets (index.html, static/app.js, static/styles.css)

## Development Commands

**IMPORTANT: Always activate the virtual environment first!**

The project uses a virtual environment located at `env/`. All Python commands must be run within this environment:

```bash
# Activate the virtual environment (required before any Python commands)
source env/bin/activate
```

### Running the Application

```bash
# Run from source (remember to activate venv first!)
source env/bin/activate && python main.py

# With verbose logging (stackable: -vv, -vvv)
source env/bin/activate && python main.py -v

# Create data dump for debugging
source env/bin/activate && python main.py --dump

# Access the web interface at http://localhost:8080
```

### Development Setup

The application requires:

- Python 3.12+
- Virtual environment at `env/` (must be activated before running commands)
- Dependencies from `pyproject.toml` (includes FastAPI, uvicorn, Socket.IO)
- Node.js 24 for frontend behavior tests

Docker deployment from a source checkout:

```bash
# Build and run the included Compose configuration
docker compose up -d --build

# Access at http://localhost:8080
```

Keep the README's Docker update commands aligned with `docs/installation.md`.
Published-image updates pull before stopping/removing the old container, stop on
command failures, and recreate with the same data mount and custom runtime options.
Retain `--init`, the 30-second shutdown grace period, `--shm-size 256m`, and the
user's timezone. Explain that `latest` and restart policies do not update running
containers automatically. Keep the README and installation guide's published-image
`compose.yaml` examples synchronized, including browser settings and persistent
data/log mounts. Pinned-image Compose updates must explain changing the image tag
before pulling. The included `docker-compose.yml` builds the checkout; use
`docker compose pull` for the published-image configuration without `build:`.
Keep those configurations in separate folders because `compose.yaml` takes
precedence. Migration from `docker run` must preserve the exact data mount, remove
the stopped old container before Compose creates its replacement, and avoid running
two miners against the same data or using `down -v` to update.

## Testing

### Automated Tests

The project includes a test suite in the `tests/` directory:

```bash
# Activate virtual environment and run tests
source env/bin/activate && python -m pytest tests/
```

The suite covers settings and proxy behavior, inventory-filter behavior, API filtering,
GraphQL watch events, batched channel discovery, full-locale translation schema and
placeholder consistency, frontend DOM safety, case-insensitive channel filtering,
watch-drop count and expiry semantics, immediate claim refresh behavior, consecutive
no-campaign console collapsing, contributor README automation, and the claimed-drop
history store with CSV export and API endpoints. Frontend behavior tests
share their JavaScript extraction helper and use Node.js;
the validation workflow provisions Node 24 before running pytest. It also runs the release
script contract tests under `.github/scripts/test/`. Ignore-list coverage includes
normalization and settings persistence, dependency pruning, the combined expiry/ignore
Wanted Queue guard, watch selection, truthful ignored/skipped inventory state, translated
placeholder parity, and frontend rendering. Changes to `web/static/app.js` or
`web/static/styles.css` still require the release workflow to bump the application version
and asset cache key before deployment.

`tests/test_special_game_watch.py` covers Special Events and IRL across streamed categories,
missing category/drops flags, offline and nonparticipating channels, disabled or absent ACLs,
Mining list selection, campaign/drop eligibility, active-campaign selection, and fallback
priority and failover. It uses mocked Twitch state and does not verify live Twitch progress.

Session persistence tests retain POSIX 0600/0700 assertions only on POSIX; Windows
credential files inherit their destination directory ACLs, so mode bits cannot prove
Windows privacy. Keep those directories private. Owned Chromium cleanup uses process
groups on POSIX and terminate/kill on Windows without requiring SIGKILL there; retain
both platform branches and graceful/forced-stop regression coverage. Firefox URL
allowlist tests need a realistic capture deadline so scheduling delay does not mask
protocol rejection; preserve rejection and target-cleanup assertions.

### Continuous Integration

- The validation workflow runs `ruff check src/` without automatic fixes. CI must
  verify the submitted source rather than repair one job's checkout while other
  jobs build unchanged files. Preserve fail-fast behavior and do not make Ruff
  advisory. `tests/test_ci_lint_verification.py` executes the actual workflow step
  against clean, fixable and invalid fixtures, checking status and file preservation.

- `.github/workflows/validation.yml` runs Ruff, Mypy, the Python test suite, language
  JSON validation, `uv lock --check`, release-script tests, and Docker build validation
  for pull requests and pushes to `main`.
- Docker validation and release workflows pin the Node-24-native Docker Buildx v4.3.0
  and Build Push v7.3.0 action commits. Update both workflows together when changing
  either action so validation and release builds use the same trusted versions.
- `.github/workflows/contributors.yml` credits the human author of each pull request
  merged into `main`, including linked pull request numbers in the alphabetically sorted
  Contributors table in `README.md`.
- The contributor workflow runs with write access through `pull_request_target`. It must
  only check out and execute trusted code from the default branch; never fetch or run
  pull request head code in that workflow.
- Keep the contributor table header and the `<!-- contributors:start -->` and
  `<!-- contributors:end -->` markers in `README.md`; the updater fails closed if the
  table header or either marker is missing, duplicated, or malformed.
- `.github/workflows/wiki.yml` publishes the eight public `docs/` guides to the GitHub
  wiki after relevant `main` pushes or manual runs on `main`. Keep `publish_wiki.py`'s
  explicit allowlist, relative-link rewriting, source-file links, and generated sidebar.
  Reject missing files, symlinks, and links to private or unpublished paths before
  writing output. Preserve unrelated wiki pages and history; never force-push.
  Check out the triggering SHA without stored checkout credentials, export before
  exposing `PUBLISHER_TOKEN`, and skip obsolete runs when the documentation, publisher,
  or workflow differs from current canonical `main`. Unrelated main commits must not
  suppress publication. Never run publishing with PR code or credentials
  available to a PR. `tests/test_wiki_publication.py` covers export boundaries, links,
  idempotence, isolated Git publication, and workflow trust. Keep private `.dev-notes/`
  outside Git, the Docker build context, and the wiki publication surface.
- `.github/workflows/version-release.yml` is the release entry point. It must provision
  `uv`, update `src/version.py`, `pyproject.toml`, and `uv.lock` together, and validate all
  three before creating a release branch or tag. Release notes read `GEMINI_API_KEY`
  from the step environment and send it as a header, never as an argument or URL.
  Preserve existing data-volume/session migration instructions. Prepare versioned
  frontend cache keys through the authorized release workflow before deployment.
  Keep current Docker/helper examples and Settings-tab paths aligned in release
  documentation. Distinguish historical release behavior from current features and
  disclose host-name migration requirements without claiming live Twitch acceptance.
- `.github/workflows/github-release.yml` verifies the existing tag against the
  dispatched source SHA and checks branch/package/lock/source version equality.
  Only the final publishing job has `contents: write`. `publish_release.py` creates
  or resumes a draft and refuses to modify published releases. Native helper builds
  cover Windows x64, Linux x64, macOS arm64 and macOS x64 from the verified source SHA.
  Validate archive contents and checksums before upload, and remote size/digests before
  publication. PR jobs never publish. The helper asks only for the dashboard URL.
- Keep browser/control listeners private; do not revive standalone sidecar URLs or
  unrestricted credential-upload routes. The standard Alpine image owns default login
  and renewal; an admitted desktop helper supplies only an initial validated session.


### Manual Testing

1. Run with `-vvv` for maximum verbosity (levels: -v, -vv, -vvv, -vvvv)
2. Check log files in `./logs/` directory
3. Monitor web GUI console output and browser developer tools

## Web GUI Architecture

The application uses a web-based interface accessible via browser:

### Web GUI Components

**src/web/gui_manager.py** - WebGUIManager class:

- Managers: StatusManager, ConsoleOutputManager, ChannelListManager, CampaignProgressManager, InventoryManager, LoginFormManager, SettingsManager, CacheManager
- Uses WebSocketBroadcaster to push real-time updates to connected clients via Socket.IO
- Pure async/await implementation

**src/web/app.py** - FastAPI application:

- REST API endpoints: `/api/status`, `/api/channels`, `/api/campaigns`, `/api/settings`, `/api/session`, `/api/session/finish`, `/api/session/retry`, `/api/session/logout`, `/api/session/vnc`, `/api/reload`, `/api/cache/clear`, `/api/close`, `/api/version`, `/api/history`, `/api/history/export.csv`, `/api/history/stats`
- Socket.IO server for real-time bi-directional communication
- Serves static web frontend from `web/` directory
- Integrates with WebGUIManager via `set_managers()`

**web/** - Frontend assets:

- `index.html` - Single-page application layout with tabs (Main, Inventory, History, Settings, Help)
  - Settings is split into sub-tabs (General, Mining, Games, Notifications, Account &
    Maintenance): each `.settings-section` lives in exactly one `#settings-panel-*` with a
    matching `[data-settings-tab]` button; `switchSettingsTab()` toggles `hidden`.
    Keep one tab stop and support Left/Right, Home and End keys. Settings save failures
    must remain visible when the originating panel is hidden; retain inline feedback
    and show a global error notification in that case. Gate the Games panel's full-height
    layout with `@supports selector(:has(*))`: above 1024 CSS pixels its lists stretch
    to the footer (300px minimum); unsupported browsers keep the fixed-height fallback.
- `static/app.js` - Socket.IO client, real-time UI updates, API calls, Inventory Filtering and Drop History logic
- `static/styles.css` - Responsive design with dark mode support

### Communication Protocol

**Server → Client (Socket.IO events):**

- `initial_state` - Full state on connect
- `status_update` - Status bar changes
- `console_output` - New log lines
- `channel_add/update/remove` - Channel list changes
- `drop_progress` - Drop mining progress
- `campaign_add` - New campaign added
- `login_required` - Prompt for credentials
- `settings_updated` - Settings changed

**Client → Server:**

- REST API for actions (login, settings, channel selection)
- Socket.IO for connection management

### Docker Integration

**src/config/paths.py:**

- Detects Docker environment via `DOCKER_ENV` env var or `/.dockerenv` file
- Docker: Uses `/app` for code, `/app/data` for persistent storage
- Development: Uses `<project_root>/data` for persistent storage
- All user data (cookies, settings, cache, logs) stored in DATA_DIR
- Provides `_resource_path()` helper for locating bundled resources

**Dockerfile:**

- Uses floating `python:alpine` and `alpine:latest` base tags, including Chromium for
  internal SDK renewal. Upgrade Alpine packages during builds; security rebuilds must
  pull current base images and bypass cached package-install layers.
  Copy noVNC browser assets from a separate stage; the application's authenticated VNC
  proxy replaces websockify, so its unused server dependencies stay out of the runtime.
- Installs dependencies from `pyproject.toml`, then removes installed pip and its vendored
  modules; Python's bundled `ensurepip` bootstrap wheel remains. Dependency changes
  require rebuilding the image.
- Exposes port 8080
- Health check on the public `/healthz` endpoint

**docker-compose.yml:**

- Volume mounts `./data:/app/data` for persistence
- Port mapping `8080:8080`
- Auto-restart policy
- Timezone configuration

### Key Design Decisions

- **WebSocket for real-time** - Socket.IO chosen for reliability (fallback to polling)
- **Single-page app** - Simpler than full framework (React/Vue), fast load times
- **Direct Docker support** - Environment detection, proper path handling
- **Integrated Twitch authentication** - Sign in through the container browser in the
  dashboard, then validate and renew the saved session. Preserve valid saved Android
  sessions during migration.

## Project Scope

This is a hobby project for personal use on the user's own hardware and home network.
Support is limited to that setup and is provided on a best-effort basis. VPS, cloud,
other third-party hosting environments, and services operated for other users are
outside the support scope. Remote dashboard access and Docker support do not expand
that deployment scope. Keep the README disclaimer and contribution guidance consistent
with this policy when reviewing proposals or documenting deployment options.

**Supported:**

- ✅ Web GUI - browser-based interface with advanced filtering
- ✅ Docker deployment - containerized on the user's own home hardware
- ✅ Remote access - access to the user's home-hosted instance
- ✅ Headless operation - no display server required

**NOT supported:**

- Multi-account support
- Channel points mining
- Desktop GUI

### Claimed Drop History

`DropHistory` records successful claims locally in `data/drop_history.json`, deduplicated
by drop ID. The History tab provides game/date filters, pagination, statistics, CSV export,
and confirmed local deletion. Date-only filters mean midnight UTC; aware timestamps
preserve their instant. CSV attachment names use UTF-8 percent encoding with an ASCII
fallback. History text is defined in `gui.history` for every locale and rendered as text.
Tests cover persistence, filtering, Unicode exports, offsets, and translated UI behavior.

- Existing-session admission accepts only a bounded sole `token` field through
  dashboard-authenticated, same-origin `POST /api/session/token`. Never echo inputs.
  Validate WEB identity before fresh SDK bootstrap and revalidate account/catalog
  before atomic installation; logout and dashboard revocation invalidate admission.
  Expired SDK cookies are not reused: validate the saved OAuth identity and bootstrap
  a new owned context. OAuth identity alone never marks a session ready.
- Native noVNC lives in a same-origin owned iframe, fixed to `/api/session/vnc` with
  reconnection disabled. Parent messages contain fixed viewer events only. Override
  native clipboard logging, bound transfers, ignore remote text while the panel is
  closed, and clear text on send/close/disconnect. Preserve attempt teardown.
- Settings notification and game action button groups wrap at narrow widths so translated controls
  remain inside the viewport, including German at 320 CSS pixels.
- Inventory overview places linkage badges in a reserved top-right grid cell, outside
  the disclosure button and action group. Keep long translated labels and game names
  from overlapping. Link account and Add to mining list share outline styling. Visible
  Mining list naming preserves the `games_to_watch` API/persistence key. Reward labels
  describe game rewards, Twitch chat badges/emotes and unrecognized Other rewards.

- Inventory campaign names/dates appear once in the overview; equal campaign/drop
  headings are not repeated. The native disclosure button has an accessible campaign
  name and sits below the static linkage badge in card mode. Actions share the dates
  row. Reserve equal overview geometry for linked/unlinked/no-action campaigns; never
  animate or imply clickability on the linkage status badge. Clicking non-interactive
  overview space toggles the card; links and buttons keep their own actions. Card mode
  deals cards round-robin into fixed column stacks (`inventoryColumnCount()` uses the
  grid width and `--inventory-card-min`; re-rendered on resize, one column in list
  view), so opening a card only moves cards below it and never reflows others between
  columns. Game/campaign names stay single-line with ellipsis and a full-text title.
  Card/list view buttons beside Clear Filters (translated
  `gui.inventory.card_view`/`list_view`, `aria-pressed`) replace the former Settings
  checkbox and persist `inventory_list_view` through the shared settings save queue. At
  480 CSS pixels and below the same layout keeps the badge top-right with the arrow
  under it, using a 104px control column, smaller artwork, wrapping badges and names
  clamped to two lines.

- Keep the public reward-type and ordering guide in `docs/usage.md#reward-types`,
  within the existing eight-guide wiki publication surface.
- `reward_priority` defaults to `[]` (disabled); enabled values must be an exact
  permutation of BADGE, EMOTE, DIRECT_ENTITLEMENT, UNKNOWN. `RewardPriority` owns
  normalization and shared type/deadline ranking. Preserve selected-game precedence
  over fallback games and all campaign/drop eligibility and mining-benefit filters.
  Saved game order is unchanged. Failed priority saves restore the prior policy and
  do not request a selection refresh. SettingsManager validates reward priority before
  any field changes and includes it in the full settings rollback snapshot.
  Cover selection and persistence boundaries in
  `test_reward_priority.py` and translated form behavior in its frontend companion.
  The Settings UI is one **Reward types** list (`renderRewardPriority()`): each row
  holds its `mining_benefits` checkbox (optimistic `state.settings` update; `saveSettings()`
  sends that state, never DOM-derived defaults) and, when ordering is enabled, the
  Mining-list drag handle plus a position number input as the keyboard/touch
  alternative. Ordering and benefit changes autosave; there is no Save button. Unticked
  types stay listed as not mined. Non-authoritative replies keep local
  `mining_benefits`/`reward_priority`. Shared drag handlers only move rows within their
  own list. Priority saves restore the replaced control's focus only if the user has
  not navigated elsewhere while waiting; track focus, pointer and Tab events through
  success and failure, remove listeners, and never focus a disabled rank input.
  Give reward checkboxes stable IDs and preserve the currently focused list control
  across synchronous re-renders, including a checkbox reached with Tab during a save.
  Game priority inputs, remove buttons and available-game checkboxes also use stable
  IDs so settings replies preserve the user's current game-list focus. Leave focus
  outside the lists untouched.
  Defer rank-change saves to the next task so a blur-triggered change cannot remove
  the browser's pending Tab target before native focus navigation completes.

- Reward ranking uses currently earnable drops, including prerequisite guards. Keep
  game, nested campaign and drop queue order consistent with channel and active-campaign
  selection; selected Special Events/IRL campaigns still outrank fallback campaigns
  sharing their live channel. Upcoming/sequential queue entries remain visible.
