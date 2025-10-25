# Release Notes - v1.1.0

We're excited to roll out v1.1.0, a major update focused entirely on making Twitch Drops Miner accessible to users around the globe by introducing comprehensive internationalization (i18n) support and dynamic language switching. This release also brings crucial bug fixes for stability and cleaner code under the hood.

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
