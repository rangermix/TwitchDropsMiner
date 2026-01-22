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
