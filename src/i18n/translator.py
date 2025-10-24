from __future__ import annotations

import json
from collections import abc
from typing import TYPE_CHECKING, Any, TypedDict, cast

from src.config import DEFAULT_LANG, LANG_PATH
from src.exceptions import MinerException
from src.utils.json_utils import json_load


if TYPE_CHECKING:
    from typing_extensions import NotRequired


class StatusMessages(TypedDict):
    terminated: str
    watching: str
    goes_online: str
    goes_offline: str
    claimed_drop: str
    no_channel: str
    no_campaign: str


class LoginStatus(TypedDict):
    logged_in: str
    logged_out: str
    logging_in: str
    required: str
    waiting_auth: str


class LoginMessages(TypedDict):
    error_code: str
    unexpected_content: str
    email_code_required: str
    twofa_code_required: str
    incorrect_login_pass: str
    incorrect_email_code: str
    incorrect_twofa_code: str
    status: LoginStatus


class ErrorMessages(TypedDict):
    captcha: str
    no_connection: str
    site_down: str


class GUIStatus(TypedDict):
    name: str
    idle: str
    ready: str
    exiting: str
    terminated: str
    cleanup: str
    gathering: str
    switching: str
    fetching_inventory: str
    fetching_campaigns: str
    adding_campaigns: str


class GUITabs(TypedDict):
    main: str
    inventory: str
    settings: str
    help: str


class GUILoginForm(TypedDict):
    name: str
    labels: str
    request: str
    username: str
    password: str
    twofa_code: str
    button: str
    oauth_prompt: str
    oauth_activate: str
    oauth_confirm: str


class GUIWebsocket(TypedDict):
    name: str
    websocket: str
    initializing: str
    connected: str
    disconnected: str
    connecting: str
    disconnecting: str
    reconnecting: str


class GUIProgress(TypedDict):
    name: str
    drop: str
    game: str
    campaign: str
    remaining: str
    drop_progress: str
    campaign_progress: str
    no_drop: str
    return_to_auto: str
    manual_mode_info: str


class GUIChannels(TypedDict):
    name: str
    online: str
    pending: str
    offline: str
    no_channels: str
    no_channels_for_games: str
    channel_count: str
    channel_count_plural: str
    viewers: str


class GUIInvStatus(TypedDict):
    active: str
    expired: str
    upcoming: str
    claimed: str


class GUIInventory(TypedDict):
    no_campaigns: str
    status: GUIInvStatus
    starts: str
    ends: str
    claimed_drops: str


class GUISettingsGeneral(TypedDict):
    name: str
    dark_mode: str


class GUISettings(TypedDict):
    general: str
    dark_mode: str
    reload: str
    reload_campaigns: str
    games_to_watch: str
    games_help: str
    search_games: str
    select_all: str
    deselect_all: str
    selected_games: str
    available_games: str
    no_games_selected: str
    no_games_match: str
    all_games_selected: str
    actions: str
    connection_quality: str
    minimum_refresh: str


class GUIHelpLinks(TypedDict):
    name: str


class GUIHelp(TypedDict):
    links: GUIHelpLinks
    how_it_works: str
    how_it_works_text: str
    getting_started: str
    getting_started_text: str
    about: str
    about_text: str
    how_to_use: str
    how_to_use_items: list[str]
    features: str
    features_items: list[str]
    important_notes: str
    important_notes_items: list[str]
    github_repo: str


class GUIHeader(TypedDict):
    title: str
    language: str
    initializing: str
    auto_mode: str
    manual_mode: str
    connected: str
    disconnected: str


class GUIMessages(TypedDict):
    output: str
    status: GUIStatus
    tabs: GUITabs
    login: GUILoginForm
    websocket: GUIWebsocket
    progress: GUIProgress
    channels: GUIChannels
    inventory: GUIInventory
    settings: GUISettings
    help: GUIHelp
    header: GUIHeader


class Translation(TypedDict):
    language_name: str
    english_name: str
    status: StatusMessages
    login: LoginMessages
    error: ErrorMessages
    gui: GUIMessages


# Load English translation from JSON file (single source of truth)
def _load_english_translation() -> Translation:
    """Load the English translation from lang/English.json.

    This is the fallback translation used when other translations are missing keys.
    """
    english_path = LANG_PATH / "English.json"
    try:
        with open(english_path, "r", encoding="utf-8") as f:
            return cast(Translation, json.load(f))
    except Exception as e:
        raise MinerException(
            f"Failed to load English translation from {english_path}: {e}"
        ) from e


# Module-level English translation (loaded once at import time)
_english_translation = _load_english_translation()


class Translator:
    def __init__(self) -> None:
        self._langs: list[str] = []
        # start with English translation (loaded from JSON)
        self._translation: Translation = _english_translation.copy()
        self._translation["language_name"] = DEFAULT_LANG
        # load available languages from JSON files by reading language_name field
        for filepath in LANG_PATH.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "language_name" in data:
                        self._langs.append(data["language_name"])
                    else:
                        # fallback to filename if language_name is missing
                        self._langs.append(filepath.stem)
            except Exception:
                # if we can't read the file, skip it
                continue
        self._langs.sort()
        # ensure DEFAULT_LANG is first in the list
        if DEFAULT_LANG in self._langs:
            self._langs.remove(DEFAULT_LANG)
        self._langs.insert(0, DEFAULT_LANG)

    @property
    def languages(self) -> abc.Iterable[str]:
        return iter(self._langs)

    @property
    def current(self) -> str:
        return self._translation["language_name"]

    def set_language(self, language: str):
        if language not in self._langs:
            raise ValueError("Unrecognized language")
        elif self._translation["language_name"] == language:
            # same language as loaded selected
            return
        elif language == DEFAULT_LANG:
            # default language selected - use English from JSON
            self._translation = _english_translation.copy()
            self._translation["language_name"] = DEFAULT_LANG
        else:
            # find the JSON file with matching language_name field
            for filepath in LANG_PATH.glob("*.json"):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("language_name") == language:
                            self._translation = json_load(filepath, _english_translation)
                            return
                except Exception:
                    continue
            # if we can't find a matching file, raise an error
            raise ValueError(f"Cannot find translation file for language: {language}")

    def __call__(self, *path: str) -> str:
        if not path:
            raise ValueError("Language path expected")
        v: Any = self._translation
        try:
            for key in path:
                v = v[key]
        except KeyError as err:
            # this can only really happen for the default translation
            raise MinerException(
                f"{self.current} translation is missing the '{' -> '.join(path)}' translation key"
            ) from err
        return str(v)


_ = Translator()
