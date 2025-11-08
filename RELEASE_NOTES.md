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
