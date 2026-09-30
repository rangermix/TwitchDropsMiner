# Release Notes - v2.1.0

Twitch sign-in now happens inside the TDM dashboard using Chromium included in the
Docker image. The separate desktop login helper is retired.

## Sign in from the dashboard

- When Twitch login is needed, TDM displays its temporary container browser with
  Twitch's sign-in page. Complete any email or two-factor verification, then select
  **Finish sign in**. TDM verifies the session and returns to the normal dashboard.
- Initial sign-in and automatic session renewal use the container's own browsers.
  No desktop browser installation, helper download, pairing ticket, or additional
  VNC port is required.
- **Log out of Twitch** at the bottom of Settings replaces **Allow helper
  connection**. Logging out clears the saved Twitch session and opens a fresh login
  browser while preserving miner settings and dashboard password protection.
- Login controls, instructions, and recovery messages are available in all 20
  supported languages.

## Private browser access

- The browser viewer uses the dashboard's password protection and origin checks.
  Password setup is also available before signing in to Twitch.
- The interactive browser runs under a separate container user. TDM checks that
  private data and logs are inaccessible to that user before starting the viewer.
  Temporary browser profiles and display processes are removed after each attempt.
- Browser retry, logout, container restart, and saved-session migration have
  regression coverage. A saved session from v2.0 remains usable on upgrade.

## Updating

Update to `rangermix/twitch-drops-miner:2.1.0`, preserving your existing data volume.
Set Docker's **TZ** to the timezone of your home internet connection; a mismatch can
cause Twitch to reject browser login. Existing working sessions do not require a
new sign-in.

The standard Docker image includes Chromium and the display components. Its data
and log mounts must enforce Linux directory permissions; a mount that ignores
private permissions cannot open the login viewer. Docker named volumes are an
alternative on such hosts. Source installations need the documented Linux browser
environment. See the [login guide](https://github.com/rangermix/TwitchDropsMiner/wiki/Authentication)
and [dashboard access guide](https://github.com/rangermix/TwitchDropsMiner/wiki/Dashboard-access).

This replaces the desktop-helper path discussed in
[#130](https://github.com/rangermix/TwitchDropsMiner/issues/130),
[#129](https://github.com/rangermix/TwitchDropsMiner/issues/129), and
[#135](https://github.com/rangermix/TwitchDropsMiner/issues/135).

# Release Notes - v2.0.2

The desktop login helper now supports native Firefox and Chromium alongside Chrome,
with a consistent sign-in flow and translated explanations for helper errors.

## Browser support and sign-in

- Automatic selection searches **Chrome → Chromium → Firefox**, using the first
  installed browser. It does not switch browsers after a launch or login failure.
- Select a browser with `--browser chrome`, `--browser chromium` or
  `--browser firefox`. Use `--chrome`, `--chromium` or `--firefox` to specify its
  local executable. Firefox, including ESR, requires version **143 or newer**.
- All three browsers open normally for sign-in. Complete Twitch login and any
  email or two-factor verification, then close all windows of the helper's browser
  instance. On macOS, quit that instance. **Keep the helper open:** it reopens the
  same temporary profile to verify and send the session. Wait for its success message.
- Firefox capture handles Twitch's URL fragments, avoiding a timeout that could
  otherwise occur after the browser showed a signed-in campaign page.
- The helper leaves everyday browser profiles untouched and removes its owned
  temporary profile when finished. Firefox derivatives have not been validated;
  Flatpak and Snap launchers remain unsupported.

Firefox support addresses [#129](https://github.com/rangermix/TwitchDropsMiner/issues/129).
Chromium/Firefox fallback also provides alternatives when Chrome is absent
([#135](https://github.com/rangermix/TwitchDropsMiner/issues/135)).

## Error explanations and recovery

- Helper error codes now include explanations and known recovery steps in all
  20 languages, covering browser discovery, login, capture, server verification,
  uncertain results and cleanup.
- Expired/invalid helper connections and a busy miner have distinct diagnostics.
  Unknown or ambiguous upload results still use receipt recovery without sending
  the session a second time; check TDM before retrying.
- Guidance distinguishes the desktop login browser from Chromium on the miner
  host. Improved explanations do not establish a fix for every server startup,
  renewal or upload failure. See the
  [error reference](https://github.com/rangermix/TwitchDropsMiner/wiki/Troubleshooting#helper-error-code-reference).

## Updating

Update the miner to `rangermix/twitch-drops-miner:2.0.2` and replace your desktop
helper with the matching **2.0.2 executable**. Updating only the miner does not
update the helper. Native archives cover Linux x64, Windows x64, macOS ARM64 and
macOS x64; `SHA256SUMS` contains their checksums.

Preserve your existing data volume. A working saved Twitch session does not need
another login. Run the helper on your desktop or laptop even when TDM runs on a
headless home server or NAS; the miner still uses its own headless Chromium for
renewal. See the [login guide](https://github.com/rangermix/TwitchDropsMiner/wiki/Authentication).

# Release Notes - v2.0.1

This patch improves login-helper cleanup and makes a server-browser startup failure
actionable instead of reporting an unknown login result.

## Login helper fixes

- On Windows, temporary Chrome profiles containing read-only files can now be
  removed. Cleanup stays within the helper's owned profile and preserves unrelated
  files, symlink targets and junction targets.
- If Chrome removes a temporary child during cleanup, the helper retries while the
  profile still exists instead of incorrectly reporting successful deletion.
- A confirmed failure to start Chromium on the miner host now reports
  `SESSION_HELPER_SERVER_BROWSER`. Missing or ambiguous acknowledgements still use
  result recovery without uploading credentials again. Persistent cleanup failures
  remain visible.

These address reproducible causes of the errors reported in
[#128](https://github.com/rangermix/TwitchDropsMiner/issues/128); they do not establish
that every reported login failure has the same cause.

## Setup and recovery guidance

- Clarifies native Chrome discovery on Linux and that Flatpak Chrome and Firefox
  are not currently supported login-helper backends
  ([#130](https://github.com/rangermix/TwitchDropsMiner/issues/130),
  [#129](https://github.com/rangermix/TwitchDropsMiner/issues/129)).
- Explains safe recovery for malformed `data/web_auth.json` dashboard-password
  state ([#132](https://github.com/rangermix/TwitchDropsMiner/issues/132)). Startup
  continues to fail closed; this release does not reset credentials automatically.
- Explains that the dashboard's **Connected** indicator is its connection to TDM,
  not confirmation of Twitch authentication, and lists useful login diagnostics.

## Updating

Update the miner to `rangermix/twitch-drops-miner:2.0.1` and download the matching
**2.0.1 login helper** for your desktop OS and CPU from this release. The helper
changes require replacing the helper executable; updating only Docker is not enough.
Preserve the existing data volume. A working saved Twitch session does not require
another login. Native downloads cover Windows x64, Linux x64, macOS ARM64 and macOS
x64, with archive checksums in `SHA256SUMS`.

# Release Notes - v2.0.0

TDM now uses a desktop login helper for new Twitch logins and renews the session on
the miner host. This is a major release because the previous device-code login and
manual session-import interfaces have been replaced.

## Upgrade from v1.x

1. Stop TDM and back up its existing data directory privately. Keep the same
   `/app/data` volume, including `cookies.jar`, settings, and drop history.
2. Update to `rangermix/twitch-drops-miner:2.0.0` and recreate the container with the
   same data mount. Include `--init --stop-timeout 30 --shm-size 256m`, or use the
   updated Compose configuration.
3. A working Android session is restored automatically. If you used Smart TV login
   in v1.3.1/v1.3.2, your session expired, or TDM asks you to sign in, download the
   **2.0.0 login helper** for your desktop from this release.
4. Enable **Settings → Allow helper connection**, run the helper, enter your TDM
   address, and sign into Twitch in its Chrome window. Wait for confirmed success.

The helper sends the session directly, closes Chrome, and removes its temporary
profile. TDM turns helper access off after acceptance and handles renewal itself;
your desktop can be shut down. Reopen helper access and repeat the login only when
TDM asks for a new session or you want to change accounts.

## Compatibility changes

- Fresh device-code authorization, manual JSON session uploads, and the old pairing
  and standalone renewal endpoints are retired. `TDM_SESSION_IMPORT` is no longer
  used. Remove old browser sidecars and remote-browser configuration.
- The standard Docker image remains Alpine-based and now includes Chromium for
  automatic renewal. Only the dashboard port is exposed; mining requests remain in
  Python. The bundled browser increases the image size.
- Source installations need `chromium` or `chromium-browser` on the miner host's
  `PATH`. Run the source miner on Linux or macOS; on Windows, use Docker Desktop
  with Linux containers. The login helper runs natively on Windows.
- Google Chrome must be installed on the helper desktop. Native helper downloads
  are provided for Windows x64, Linux x64, macOS Apple Silicon, and macOS Intel.

## Other changes

- Accepted helper sessions and renewal state survive restarts. Switching accounts
  clears the previous account's active work and derived state.
- Transient failures of known read-only Twitch queries are retried with bounded
  delays; ambiguous mutations are not replayed.
- The README is shorter, with detailed user guides maintained in `docs/` and the
  [GitHub wiki](https://github.com/rangermix/TwitchDropsMiner/wiki).

See [installation](https://github.com/rangermix/TwitchDropsMiner/wiki/Installation)
and [login and recovery](https://github.com/rangermix/TwitchDropsMiner/wiki/Authentication)
for full instructions. Authentication work is tracked in
[#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).

# Release Notes - v1.3.2

Fixes dashboard connections behind HTTPS reverse proxies with the optional
`PUBLIC_BASE_URL=https://drops.example.com` environment setting. API writes and both
Socket.IO transports validate against that public origin, and HTTPS public URLs set
Secure session cookies even when the backend connection is HTTP or Host is rewritten.

The README and Compose example document configuration and unchanged behavior when the
setting is absent. Existing CSRF and session checks remain enforced. This setting does
not enable forwarded-header trust or change client-IP rate limiting; trusting a proxy
for client IPs remains a separate, explicitly scoped configuration.

Addresses [#106](https://github.com/rangermix/TwitchDropsMiner/issues/106). The missing
Twitch login prompt in that report followed a rejected live dashboard connection;
Socket.IO does not require the `X-TDM-Request` header used for ordinary API writes.

# Release Notes - v1.3.1

Fixes the `KeyError: 'device_code'` crash during fresh Twitch login by using the
Smart TV device authorization client. Existing users may need to authorize the
miner once again at `twitch.tv/activate`; subsequent runs reuse the saved session.

Channel pages continue to use the public Twitch website so the client change does
not break watch-event endpoint discovery. Regression tests cover login, cookie
migration and restoration, and beacon discovery through watch-event submission.

Resolves [#109](https://github.com/rangermix/TwitchDropsMiner/issues/109) through
[#110](https://github.com/rangermix/TwitchDropsMiner/pull/110), with thanks to
[@3lb0z0](https://github.com/3lb0z0) for the client fix.

# Release Notes - v1.3.0

This update brings a massive quality-of-life boost to Twitch Drops Miner, introducing a new drop history tracker, Telegram notifications, and enhanced security features. We've also streamlined the UI to make managing your watch list easier and more reliable than ever.

### 📈 New Features
- **Drop History & CSV Export**: Never lose track of your loot! A new History tab records every claimed drop, complete with stats and the ability to export your data to CSV for easy spreadsheet viewing.
- **Telegram Notifications**: Stay updated on the go—you can now receive instant alerts on Telegram whenever a new drop is successfully claimed.
- **Dashboard Security**: Added an optional password protection layer for your dashboard to keep your settings and mining activity private.
- **Manual Priority Ordering**: You can now manually reorder your "Games to Watch" list using an editable input field to ensure your favorite campaigns get the attention they deserve.

### 🎮 UI & UX Improvements
- **Smarter Game Management**: Adding games is now more intuitive with "smart resolution" that corrects casing automatically, plus new confirmation modals to prevent accidental changes.
- **Selection Safety**: The "Select All" and "Deselect All" actions are now protected, preventing accidental data loss when managing your watch list.
- **Enhanced Input**: Added support for the Enter key when adding games and improved visual styling for priority numbers.

### 🐛 Bug Fixes
- **Special Events Support**: Fixed an issue where Special Events and IRL campaigns weren't being mined correctly.
- **Channel Logic**: Added "dependency-aware" ignore rules and replaced unwatchable fallback channels to ensure the miner stays focused on active, valid streams.
- **Data Cleanup**: Added a "Clear All Cache" recovery action and fixed a bug where repeated "no-campaign" logs cluttered the console.
- **Filtering & Inventory**: Resolved multiple issues regarding inventory synchronization and visibility, ensuring your progress is always accurately tracked.

### 📚 Other Improvements
- **Performance & Stability**: Updated CI workflows to Node 24 and improved internal data handling for the "wanted drop" queue.
- **Localization**: Updated translation keys across all 20 supported languages to ensure the new features are accessible to our global community.
- **Documentation**: Updated contribution guidelines and recognized our wonderful contributors for their hard work on these features!

# Release Notes - v1.2.6

This patch release makes inventory filtering more predictable and keeps version metadata synchronized across source and packaged installations.

### 🐛 Inventory Filter Fixes
- **Finished Campaigns**: Fully claimed campaigns now stay hidden until the **Finished** filter is selected.
- **Not Linked Filtering**: **Not Linked** now narrows the selected campaign statuses instead of broadening them, so combinations such as **Active + Not Linked** behave as expected.
- **Live Completion Updates**: Campaigns disappear as soon as their final drop is claimed instead of waiting for an inventory reload.
- **Settings Compatibility**: Existing saved filter settings migrate safely without unexpectedly restricting the inventory.

### ⚙️ Release Reliability
- **Consistent Versions**: Release automation now updates and validates `src/version.py`, `pyproject.toml`, and `uv.lock` together before publishing a tag or Docker image.
- **Expanded Validation**: Release-script contracts and frontend filter behavior now run in continuous integration.

### 🔗 Issues and Pull Requests
- Resolved [#51](https://github.com/rangermix/TwitchDropsMiner/issues/51) and [#52](https://github.com/rangermix/TwitchDropsMiner/issues/52) in [#79](https://github.com/rangermix/TwitchDropsMiner/pull/79).
- PR #79 supersedes the earlier, closed [#60](https://github.com/rangermix/TwitchDropsMiner/pull/60).
- Release-version consistency and rollback safety were fixed in [#80](https://github.com/rangermix/TwitchDropsMiner/pull/80).

### 🙌 Contributors
- [@rangermix](https://github.com/rangermix) — implementation, migration, tests, and release maintenance.
- [@SimpliAj](https://github.com/SimpliAj) — original Inventory-filter proposal in PR #60.

# Release Notes - v1.2.5

This update brings a major boost to drop progress reliability, a fresh look for your inventory, and significant improvements to the mobile experience. We’ve also streamlined our development pipeline to ensure faster and more stable future updates.

### 🎨 Web Dashboard Improvements
- **Inventory List View**: You can now choose between the classic masonry grid or a new horizontal row layout for your campaigns. Head over to the **Settings** tab to toggle the "Inventory List View" and see your campaign info and drops organized side-by-side.
- **Mobile Responsiveness**: We’ve overhauled the layout for phones and small tablets. The dashboard now gracefully stacks headers, tab bars, and panels, ensuring you can manage your drops on the go without any annoying horizontal scrolling.

### 🛠️ Core Functionality
- **Restored Drop Progress**: We’ve updated how watch events are sent to Twitch. By switching to a direct Spade POST method, we’ve bypassed the broken GraphQL mutation, ensuring your watch time is correctly counted toward those drops again!
- **Streamlined Data Handling**: Removed stale Spade payload caching in the `Stream` module to keep data fresh and prevent potential tracking issues.

### 🐛 Bug Fixes
- **Settings Persistence**: Fixed an issue where the "Inventory List View" checkbox would reset itself due to API model validation; your preferences will now save correctly.
- **UI Scaling**: Fixed overlapping text and squeezed elements on small screens, specifically in the OAuth and campaign card sections.

### 📚 Maintenance & Credits
- **Contributor Automation**: Added new testing and automation tools to speed up our development process and ensure higher code quality.
- **Special Thanks**: A shoutout to **Fengqing Liu** for their contributions to this release!

# Release Notes - v1.2.4

This update ensures compatibility with the latest Twitch systems and modernizes our environment requirements for a smoother experience. We’ve updated essential game directory hashes and bumped our Python requirements to keep the miner running at peak performance.

### 🔄 System Updates
- **Game Directory Hash**: We’ve updated the internal hash for the Game Directory to ensure the miner stays synced with the latest Twitch data, preventing potential connection issues.

### ⚙️ Infrastructure
- **Python Version Requirement**: To take advantage of the latest performance improvements and security patches, we have updated the minimum Python requirement from 3.10 to 3.12. Please make sure to update your local environment to continue using the miner!

### 📚 Documentation
- **README Updates**: The README file has been refreshed to reflect the new Python 3.12+ requirement, making it easier for new users to get started with the correct setup.

# Release Notes - v1.2.3

This update introduces a highly requested custom game management feature and addresses critical stability issues to ensure your drops keep rolling in smoothly. We've also bolstered the application's security and refined our internal processing logic for a more reliable experience.

### 🎮 Game Management
- **Add Custom Games**: You can now manually add your favorite games to the "Games to Watch" list directly from the Settings tab! Use the new search input to find and link any game you want to farm.
- **UI Polish**: We’ve cleaned up the alignment of the games filter search bar to make your settings page look sharper and easier to navigate.

### 🐛 Bug Fixes
- **Twitch Watch Events**: We’ve updated the GQL integration to ensure watch events are tracked correctly, preventing drops from getting stuck.
- **Security Hardening**: Replaced `innerHTML` with safe DOM APIs throughout the frontend to protect the app against XSS vulnerabilities.
- **Rendering Fixes**: Addressed regressions in the UI to ensure everything displays exactly as it should after our security updates.

### ⚙️ Performance & Internal Improvements
- **Optimized Agent Logic**: Simplified our AI agent instructions and updated the Gemini model to provide more efficient and accurate performance.
- **Maintenance**: Cleaned up project configuration files to keep our repository tidy and efficient.

# Release Notes - v1.2.2

This release brings significant enhancements to the core efficiency of the Twitch Drops Miner! We focused on unifying and smartening up the logic that selects games and tracks drops, ensuring your mining time is spent more effectively.

### 🎮 Mining Efficiency & Automation

We've overhauled the core systems responsible for identifying and tracking drops, making the Miner much more reliable and intelligent.

-   **Unified Drop Logic**: The processes for selecting the next game to watch and tracking the expected drop progress have been merged into a single, cohesive system. This means the Miner is now smarter and more consistent about identifying active campaigns, reducing unnecessary stream switching and ensuring maximum drop uptime.

### 📚 Under the Hood Improvements

These changes are focused on stability and future development, but they ensure your application runs smoothly.

-   **Refactored Settings Manager**: The internal framework for managing your configurations and settings has been completely refactored. This update significantly improves the stability and reliability of your saved preferences and prepares the Miner for more advanced customization options in upcoming releases.
-   **Code Maintenance**: General code cleanup and formatting were performed to improve overall code quality and maintainability.

# Release Notes - v1.2.1

We've released v1.2.1 focused entirely on making your drop mining more reliable and robust. This crucial stability update ensures the Miner continues to track drops consistently, even when Twitch makes minor, behind-the-scenes adjustments to its tracking URLs.

### 🐛 Bug Fixes

-   **Enhanced Drop Tracking Stability**: We've relaxed the internal logic (regex) used to identify official Twitch drop tracking URLs.
    *   **Why you'll like it**: This means the Miner is now much more resilient to minor changes on Twitch's platform. If Twitch slightly alters how their drop URLs look, your mining won't break, ensuring consistent and reliable drop accumulation.

### 📚 Under the Hood Improvements

-   **Internal Configuration Cleanup**: Removed unnecessary internal developer configurations and identifiers. This keeps the application code tidy and focused on core drop mining functionality.

# Release Notes - v1.2.0

This release brings a major overhaul to the dashboard, making drop management much cleaner and more intuitive. We've also introduced automated update checks and powerful new filtering options to help you prioritize your farming efforts!

### 🌍 Dashboard Overhaul & Drop Prioritization

We completely redesigned the core dashboard elements to make managing your wanted drops faster and more visually appealing.

-   **Wanted Drops Queue Redesign**: The 'Wanted Drops Queue' now features a beautiful, responsive card-based masonry layout. Organizing your priority drops is easier and looks fantastic!
-   **New Benefit Filters**: Stop mining clutter! You can now easily filter your drop lists based on the specific type of reward you want (e.g., Item, Badge, Currency, etc.) directly in the settings.
-   **Game Grouping**: Drops in the Wanted Queue are now automatically grouped by their associated game, giving you a clearer, organized overview of what you are currently mining for.
-   **Smarter Inventory Cards**: Inventory cards now support variable heights, resulting in a cleaner dashboard layout and better use of screen space.

### ⚙️ Utility & Infrastructure

We added crucial quality-of-life features to keep you informed and provide more flexibility.

-   **Automated Update Checker**: The app now features a persistent footer displaying the current version. It automatically checks GitHub for the latest release and alerts you instantly with a visible indicator and link if an update is available. No more manual checking!
-   **Proxy Support**: Added initial support and verification logic for using proxies, enhancing flexibility for advanced users who require customized network setups.

### 📚 Maintenance & General Improvements

-   **Translation Updates**: We have updated and improved several translations across the application for better localization and accuracy.
-   **Repository Links**: Updated internal and external links to the correct repository owner.
-   **UI Polish**: Minor alignment and styling fixes, including updating the favicon to use a transparent background.

# Release Notes - v1.1.6

We've released a small but important update focusing on quality of life and core application stability. This version ensures better synchronization reliability and adds a helpful visual indicator for easier navigation.

### ✨ Quality of Life Improvements
This small visual tweak makes managing your drops much smoother!

- **New Tab Icon (Favicon)**: We've added a custom favicon to the browser tab bar! Now you can easily identify your Twitch Drops Miner instance among the dozens of browser tabs you inevitably have open. No more hunting for the right window!

### 🐛 Bug Fixes
- **Core Synchronization Stability**: Implemented an important sync fix to improve the reliability and accuracy of drop tracking. This ensures the application maintains better stability, especially during long mining sessions.

# Release Notes - v1.1.5

Version 1.1.5 delivers significant advancements in network configuration and inventory management, introducing robust proxy support for advanced users and powerful new filters to help you sort through your earned drops faster than ever.

### 🌍 Advanced Connectivity & Infrastructure

This release introduces major improvements for users needing specialized network configurations, making your mining setup more flexible and reliable.

*   **Full Proxy Support**: You can now configure and utilize proxies within the application. This is ideal for managing multiple accounts or ensuring connection stability.
*   **Proxy Verification**: New built-in verification ensures your configured proxy is working correctly *before* you start mining, saving you valuable time and troubleshooting effort.

### 🎁 Inventory & Filtering Upgrades

We’ve made it easier to focus on the drops that matter most to you by adding granular filtering options to your inventory view.

*   **Drop Benefit Type Filters**: You can now filter your earned drops based on their specific benefit type (e.g., currency, in-game items, beta access, etc.). Quickly find exactly what you’ve earned without scrolling through unrelated loot!

### 📚 Setup & Configuration

These changes primarily benefit users running the application via Docker or `docker-compose`.

*   **Improved Docker Compose Example**: The provided example configuration has been updated to include necessary logging features and timezone settings, ensuring easier setup and better debugging capabilities right out of the box.

# Release Notes - v1.1.4

release automation fix only.

We've completely overhauled the Inventory tab in v1.1.3, introducing powerful new filtering options and significant visual upgrades to help you track your drops faster and more efficiently. This update makes managing your active campaigns and identifying required linking actions much clearer.

### 🎮 Advanced Tracking & Filtering

This release introduces comprehensive tools to help you quickly sort through your Twitch drops inventory.

-   **Advanced Game Filtering**: Need to find all drops for a specific game? Our new multi-select dropdown allows you to filter campaigns by game title. It features live search, easy tag removal, and keyboard navigation for speed.
-   **Campaign Status Filters**: Quickly narrow down your view using new status filters, including **Active**, **Not Linked**, **Upcoming**, **Expired**, and **Finished**. Filter selections are now saved across sessions.

### 🌍 Inventory Visual Overhaul

We've packed more critical information directly onto your inventory cards, making them cleaner and easier to read at a glance.

-   **Account Linking Status**: Campaign cards now feature clear visual badges in the top right corner. See **LINKED** (green) or **NOT LINKED** (orange) instantly. If an account isn't linked, the badge and a new button provide a direct link to the setup page.
-   **Detailed Benefit Display**: Rewards are no longer displayed as a simple grid of icons. Each benefit is now listed on its own line, showing the associated icon, the full name, and the item type (e.g., *In-Game Currency (CURRENCY)*).
-   **Game Icons & Timing**: We added game box art icons next to the campaign title for faster visual identification. Below the status, you'll find contextual timing information (start or end time) formatted correctly for your local timezone.

### 🐛 Bug Fixes

-   **Log Persistence**: Fixed an issue related to log file permissions, ensuring that persistent logging is reliable and your historical data is saved correctly across restarts.

### 📚 Performance & Infrastructure

-   **Slimmer Docker Image**: For users running the Miner via Docker, we have optimized the underlying base image, reducing the overall size to less than 1/10th of the previous version. This means faster deployments and less resource usage!
-   **Improved Logging**: Console logs now include the date and timezone, making it significantly easier for users and developers to debug and track events accurately.

# Release Notes - v1.1.3

We've completely overhauled the Inventory tab in v1.1.3, introducing powerful new filtering options and significant visual upgrades to help you track your drops faster and more efficiently. This update makes managing your active campaigns and identifying required linking actions much clearer.

### 🎮 Advanced Tracking & Filtering

This release introduces comprehensive tools to help you quickly sort through your Twitch drops inventory.

-   **Advanced Game Filtering**: Need to find all drops for a specific game? Our new multi-select dropdown allows you to filter campaigns by game title. It features live search, easy tag removal, and keyboard navigation for speed.
-   **Campaign Status Filters**: Quickly narrow down your view using new status filters, including **Active**, **Not Linked**, **Upcoming**, **Expired**, and **Finished**. Filter selections are now saved across sessions.

### 🌍 Inventory Visual Overhaul

We've packed more critical information directly onto your inventory cards, making them cleaner and easier to read at a glance.

-   **Account Linking Status**: Campaign cards now feature clear visual badges in the top right corner. See **LINKED** (green) or **NOT LINKED** (orange) instantly. If an account isn't linked, the badge and a new button provide a direct link to the setup page.
-   **Detailed Benefit Display**: Rewards are no longer displayed as a simple grid of icons. Each benefit is now listed on its own line, showing the associated icon, the full name, and the item type (e.g., *In-Game Currency (CURRENCY)*).
-   **Game Icons & Timing**: We added game box art icons next to the campaign title for faster visual identification. Below the status, you'll find contextual timing information (start or end time) formatted correctly for your local timezone.

### 🐛 Bug Fixes

-   **Log Persistence**: Fixed an issue related to log file permissions, ensuring that persistent logging is reliable and your historical data is saved correctly across restarts.

### 📚 Performance & Infrastructure

-   **Slimmer Docker Image**: For users running the Miner via Docker, we have optimized the underlying base image, reducing the overall size to less than 1/10th of the previous version. This means faster deployments and less resource usage!
-   **Improved Logging**: Console logs now include the date and timezone, making it significantly easier for users and developers to debug and track events accurately.

# Release Notes - v1.1.2

This release focuses heavily on international accessibility, bringing comprehensive language support to the entire application. We've also included several minor fixes and internal cleanups to keep the miner running smoothly.

### 🌍 Localization & Language

We've completely overhauled the translation system to ensure a seamless experience for users worldwide.

- **Full Language Coverage**: Thanks to a massive effort, the application now features full, high-quality translations across all screens and features.
- **Settings Clarity**: We fixed a small issue where translations in the General Settings menu were not displaying correctly, ensuring your preferences are clear no matter your chosen language.

### 🐛 Bug Fixes

A few minor issues have been squashed to improve overall stability and user experience.

- **Repository Link**: We corrected the link pointing to the project repository within the application, ensuring users can easily find the source code or support page.

### 📚 Quality of Life & Maintenance

- **Branding Update**: Implemented minor internal name changes for better consistency.
- **Code Optimization**: Removed several pieces of unused code structure, helping to keep the application lean and efficient.

# Release Notes - v1.1.1

We're excited to roll out v1.1.1, a major update focused entirely on making Twitch Drops Miner accessible to users around the globe by introducing comprehensive internationalization (i18n) support and dynamic language switching. This release also brings crucial bug fixes for stability and cleaner code under the hood.

## For 1.1.0

### 🌍 Global Language Support (i18n)

This update introduces full multi-language support, allowing you to use the Web GUI in your preferred language without needing a browser translation tool.

- **Dynamic Language Switching**: You can now instantly switch the language of the Web GUI from the settings panel, and the application will update all text immediately without requiring a restart.
- **Comprehensive GUI Translation**: Nearly every piece of text, setting, button, and label in the application now supports native translation, including the specific "Games to Watch" settings.
- **Improved Selector Placement**: The language selector is now conveniently located in the top-right banner area for easy access.

### 🐛 Bug Fixes

We squashed several annoying issues to ensure a smoother, more reliable experience, especially when dealing with the new language features.

- **Language Persistence Fixed**: Previously, your selected language might not have saved correctly after closing and reopening the application. Your language setting will now persist across sessions.
- **Special Character Handling**: Fixed an issue where game names containing special characters (like accents or symbols) were not being properly handled or displayed, ensuring accurate drop mining visibility.
- **Help Tab Duplication**: Resolved a bug where switching languages caused content in the Help tab to duplicate, leading to messy, repeated text.

### 📚 Code Cleanup & Optimization

While these changes are mostly internal, they result in a faster, more stable application and set the groundwork for future features.

- **Modernized Translation Engine**: We completely refactored the internal translation system (Translator class) to be faster, cleaner, and easier to maintain.
- **Client Code Cleanup**: Removed unused or legacy code components, including the `ReloadRequest` exception, leading to a lighter, more efficient client application.

## For 1.1.1

Workflow fix and upgrade
