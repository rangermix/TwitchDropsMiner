"""Internationalization (i18n) package for Twitch Drops Miner."""

from __future__ import annotations

from .translator import (
    ErrorMessages,
    GUIChannels,
    GUIHeader,
    GUIHelp,
    GUIHelpLinks,
    GUIInventory,
    GUIInvStatus,
    GUILoginForm,
    GUIMessages,
    GUIProgress,
    GUISettings,
    GUISettingsGeneral,
    GUIStatus,
    GUITabs,
    GUIWebsocket,
    LoginMessages,
    StatusMessages,
    Translation,
    Translator,
    _,
    default_translation,
)


__all__ = [
    "StatusMessages",
    "LoginMessages",
    "ErrorMessages",
    "GUIStatus",
    "GUITabs",
    "GUILoginForm",
    "GUIWebsocket",
    "GUIProgress",
    "GUIChannels",
    "GUIInvStatus",
    "GUIInventory",
    "GUISettingsGeneral",
    "GUISettings",
    "GUIHelpLinks",
    "GUIHelp",
    "GUIHeader",
    "GUIMessages",
    "Translation",
    "default_translation",
    "Translator",
    "_",
]
