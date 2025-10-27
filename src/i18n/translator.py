from __future__ import annotations

import json
import logging
from typing import TypedDict, cast

from src.config import DEFAULT_LANG, LANG_PATH


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
    general: GUISettingsGeneral
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


class Translator:
    def __init__(self) -> None:
        self.logger: logging.Logger = logging.getLogger("TwitchDropsMiner.i18n.Translator")
        self._langs: dict[str, Translation] = {}
        self.current_language: str
        self.t: Translation
        # load available languages from JSON files by reading language_name field
        for filepath in LANG_PATH.glob("*.json"):
            with filepath.open("r", encoding="utf-8") as json_file:
                try:
                    loaded_translation: Translation = json.load(json_file)
                    self._langs[loaded_translation["language_name"]] = loaded_translation
                except Exception as e:
                    # if we can't read the file, skip it
                    self.logger.warning(f"Failed to load language file {filepath}: {e}")
                    continue
        self._langs = dict(sorted(self._langs.items()))
        self.set_language(DEFAULT_LANG)

    def get_languages(self) -> list[str]:
        return list(self._langs.keys())

    def set_language(self, language: str):
        if language not in self._langs:
            raise ValueError(f"Unrecognized language {language}")

        self.current_language = language
        self.t = cast(Translation, self._langs.get(language))


_ = Translator()
