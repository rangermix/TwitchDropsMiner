#!/usr/bin/env python3
"""
Script to add missing translation keys to all language files.
Adds English text as placeholders where translations are missing.
"""

import json
import os
from pathlib import Path

# Translations to add/update for each language
# Format: {language_code: {key_path: translation}}
TRANSLATIONS = {
    "English": {
        # Already updated, this is our reference
    },
    "Simplified Chinese": {
        "gui.login.oauth_prompt": "在此网站输入代码：",
        "gui.login.oauth_activate": "Twitch 激活",
        "gui.login.oauth_confirm": "我已输入代码",
        "gui.progress.no_drop": "无活跃掉宝",
        "gui.progress.return_to_auto": "返回自动模式",
        "gui.progress.manual_mode_info": "手动模式：正在挖掘",
        "gui.channels.no_channels": "尚未跟踪任何频道...",
        "gui.channels.no_channels_for_games": "所选游戏未找到频道...",
        "gui.channels.channel_count": "频道",
        "gui.channels.channel_count_plural": "频道",
        "gui.channels.viewers": "观众",
        "gui.inventory.no_campaigns": "尚未加载任何活动...",
        "gui.inventory.claimed_drops": "已领取",
        "gui.settings.general": "常规设置",
        "gui.settings.dark_mode": "深色模式",
        "gui.settings.reload_campaigns": "重新加载活动",
        "gui.help.about": "关于 Twitch 掉宝矿工",
        "gui.help.about_text": "此应用程序可在不下载流数据的情况下自动挖掘定时 Twitch 掉宝。",
        "gui.help.how_to_use": "使用方法",
        "gui.help.how_to_use_items": [
            "使用您的 Twitch 账号登录（OAuth 设备代码流程）",
            "在 <a href=\"https://www.twitch.tv/drops/campaigns\" target=\"_blank\">twitch.tv/drops/campaigns</a> 关联您的账号",
            "矿工将自动发现活动并开始挖掘",
            "在设置中配置优先游戏以关注您想要的内容",
            "在主界面和库存选项卡中监控进度"
        ],
        "gui.help.features": "功能",
        "gui.help.features_items": [
            "无流挖掘 - 节省带宽",
            "游戏优先级和排除列表",
            "同时跟踪最多 199 个频道",
            "自动切换频道",
            "实时进度跟踪"
        ],
        "gui.help.important_notes": "重要提示",
        "gui.help.important_notes_items": [
            "挖掘时请勿在同一账号上观看流",
            "保护好您的 cookies.jar 文件",
            "需要关联游戏账号才能掉宝"
        ],
        "gui.help.github_repo": "GitHub 仓库",
        "gui.header.language": "语言：",
        "gui.header.initializing": "初始化中...",
        "gui.header.connected": "已连接",
        "gui.header.disconnected": "已断开",
    },
}

# Default English translations for all languages
DEFAULT_TRANSLATIONS = {
    "gui.login.user_id_label": "User ID:",
    "gui.login.waiting_auth": "Waiting for authentication...",
    "gui.login.oauth_prompt": "Enter this code at:",
    "gui.login.oauth_activate": "Twitch Activate",
    "gui.login.oauth_confirm": "I've entered the code",
    "gui.progress.no_drop": "No active drop",
    "gui.progress.return_to_auto": "Return to Auto Mode",
    "gui.progress.manual_mode_info": "Manual Mode: Mining",
    "gui.channels.no_channels": "No channels tracked yet...",
    "gui.channels.no_channels_for_games": "No channels found for selected games...",
    "gui.channels.channel_count": "channel",
    "gui.channels.channel_count_plural": "channels",
    "gui.channels.viewers": "viewers",
    "gui.inventory.no_campaigns": "No campaigns loaded yet...",
    "gui.inventory.claimed_drops": "claimed",
    "gui.settings.general": "General Settings",
    "gui.settings.dark_mode": "Dark Mode",
    "gui.settings.reload_campaigns": "Reload Campaigns",
    "gui.help.about": "About Twitch Drops Miner",
    "gui.help.about_text": "This application automatically mines timed Twitch drops without downloading stream data.",
    "gui.help.how_to_use": "How to Use",
    "gui.help.how_to_use_items": [
        "Login using your Twitch account (OAuth device code flow)",
        "Link your accounts at <a href=\"https://www.twitch.tv/drops/campaigns\" target=\"_blank\">twitch.tv/drops/campaigns</a>",
        "The miner will automatically discover campaigns and start mining",
        "Configure priority games in Settings to focus on what you want",
        "Monitor progress in the Main and Inventory tabs"
    ],
    "gui.help.features": "Features",
    "gui.help.features_items": [
        "Stream-less drop mining - saves bandwidth",
        "Game priority and exclusion lists",
        "Tracks up to 199 channels simultaneously",
        "Automatic channel switching",
        "Real-time progress tracking"
    ],
    "gui.help.important_notes": "Important Notes",
    "gui.help.important_notes_items": [
        "Do not watch streams on the same account while mining",
        "Keep your cookies.jar file secure",
        "Requires linked game accounts for drops"
    ],
    "gui.help.github_repo": "GitHub Repository",
    "gui.header.language": "Language:",
    "gui.header.initializing": "Initializing...",
    "gui.header.connected": "Connected",
    "gui.header.disconnected": "Disconnected",
}


def set_nested_value(data, key_path, value):
    """Set a value in a nested dictionary using dot notation."""
    keys = key_path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def get_nested_value(data, key_path, default=None):
    """Get a value from a nested dictionary using dot notation."""
    keys = key_path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def update_language_file(file_path):
    """Update a language file with missing translations."""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    language_name = data.get("english_name", "Unknown")
    print(f"Updating {language_name}...")

    # Get language-specific translations or use defaults
    lang_translations = TRANSLATIONS.get(language_name, {})

    updated = False
    for key_path, default_value in DEFAULT_TRANSLATIONS.items():
        # Check if translation exists
        current_value = get_nested_value(data, key_path)

        # Use language-specific translation if available, otherwise use default
        new_value = lang_translations.get(key_path, default_value)

        # Only update if missing or if we have a language-specific translation
        if current_value is None or (key_path in lang_translations):
            set_nested_value(data, key_path, new_value)
            updated = True
            print(f"  - Updated {key_path}")

    if updated:
        # Write back with proper formatting
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"  ✓ Saved {language_name}")
    else:
        print(f"  ✓ {language_name} already up to date")

    return updated


def main():
    lang_dir = Path(__file__).parent / "lang"

    if not lang_dir.exists():
        print(f"Error: Language directory not found: {lang_dir}")
        return

    print("Updating language files...")
    print("=" * 60)

    total_updated = 0
    for lang_file in sorted(lang_dir.glob("*.json")):
        if update_language_file(lang_file):
            total_updated += 1
        print()

    print("=" * 60)
    print(f"Updated {total_updated} language files")
    print()
    print("Note: Some languages have English placeholders.")
    print("Please translate these to the appropriate language.")


if __name__ == "__main__":
    main()
