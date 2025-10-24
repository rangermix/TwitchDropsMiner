from __future__ import annotations

from collections import abc
from typing import TYPE_CHECKING, Any, TypedDict

from src.config import DEFAULT_LANG, LANG_PATH
from src.exceptions import MinerException
from src.utils.json_utils import json_load, json_save


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


class LoginMessages(TypedDict):
    error_code: str
    unexpected_content: str
    email_code_required: str
    twofa_code_required: str
    incorrect_login_pass: str
    incorrect_email_code: str
    incorrect_twofa_code: str


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
    logging_in: str
    logged_in: str
    logged_out: str
    request: str
    required: str
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
    features: str
    important_notes: str
    github_repo: str


class GUIHeader(TypedDict):
    title: str
    language: str
    initializing: str
    auto_mode: str
    manual_mode: str


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
    language_name: NotRequired[str]
    english_name: str
    status: StatusMessages
    login: LoginMessages
    error: ErrorMessages
    gui: GUIMessages


default_translation: Translation = {
    "english_name": "English",
    "status": {
        "terminated": "\nApplication Terminated.\nClose the window to exit the application.",
        "watching": "Watching: {channel}",
        "goes_online": "{channel} goes ONLINE, switching...",
        "goes_offline": "{channel} goes OFFLINE, switching...",
        "claimed_drop": "Claimed drop: {drop}",
        "no_channel": "No available channels to watch. Waiting for an ONLINE channel...",
        "no_campaign": "No active campaigns to mine drops for. Waiting for an active campaign...",
    },
    "login": {
        "unexpected_content": (
            "Unexpected content type returned, usually due to being redirected. "
            "Do you need to login for internet access?"
        ),
        "error_code": "Login error code: {error_code}",
        "incorrect_login_pass": "Incorrect username or password.",
        "incorrect_email_code": "Incorrect email code.",
        "incorrect_twofa_code": "Incorrect 2FA code.",
        "email_code_required": "Email code required. Check your email.",
        "twofa_code_required": "2FA token required.",
    },
    "error": {
        "captcha": "Your login attempt was denied by CAPTCHA.\nPlease try again in 12+ hours.",
        "site_down": "Twitch is down, retrying in {seconds} seconds...",
        "no_connection": "Cannot connect to Twitch, retrying in {seconds} seconds...",
    },
    "gui": {
        "output": "Output",
        "status": {
            "name": "Status",
            "idle": "Idle",
            "ready": "Ready",
            "exiting": "Exiting...",
            "terminated": "Terminated",
            "cleanup": "Cleaning up channels...",
            "gathering": "Gathering channels...",
            "switching": "Switching the channel...",
            "fetching_inventory": "Fetching inventory...",
            "fetching_campaigns": "Fetching campaigns...",
            "adding_campaigns": "Adding campaigns to inventory... {counter}",
        },
        "tabs": {
            "main": "Main",
            "inventory": "Inventory",
            "settings": "Settings",
            "help": "Help",
        },
        "login": {
            "name": "Login Form",
            "labels": "Status:\nUser ID:",
            "logged_in": "Logged in",
            "logged_out": "Logged out",
            "logging_in": "Logging in...",
            "required": "Login required",
            "request": "Please log in to continue.",
            "username": "Username",
            "password": "Password",
            "twofa_code": "2FA code (optional)",
            "button": "Login",
            "oauth_prompt": "Enter this code at:",
            "oauth_activate": "Twitch Activate",
            "oauth_confirm": "I've entered the code",
        },
        "websocket": {
            "name": "Websocket Status",
            "websocket": "Websocket #{id}:",
            "initializing": "Initializing...",
            "connected": "Connected",
            "disconnected": "Disconnected",
            "connecting": "Connecting...",
            "disconnecting": "Disconnecting...",
            "reconnecting": "Reconnecting...",
        },
        "progress": {
            "name": "Campaign Progress",
            "drop": "Drop:",
            "game": "Game:",
            "campaign": "Campaign:",
            "remaining": "{time} remaining",
            "drop_progress": "Progress:",
            "campaign_progress": "Progress:",
            "no_drop": "No active drop",
            "return_to_auto": "Return to Auto Mode",
            "manual_mode_info": "Manual Mode: Mining",
        },
        "channels": {
            "name": "Channels",
            "online": "ONLINE  ✔",
            "pending": "OFFLINE ⏳",
            "offline": "OFFLINE ❌",
            "no_channels": "No channels tracked yet...",
            "no_channels_for_games": "No channels found for selected games...",
            "channel_count": "channel",
            "channel_count_plural": "channels",
            "viewers": "viewers",
        },
        "inventory": {
            "no_campaigns": "No campaigns loaded yet...",
            "status": {
                "active": "Active ✔",
                "upcoming": "Upcoming ⏳",
                "expired": "Expired ❌",
                "claimed": "Claimed ✔",
            },
            "starts": "Starts: {time}",
            "ends": "Ends: {time}",
            "claimed_drops": "claimed",
        },
        "settings": {
            "general": {
                "name": "General",
                "dark_mode": "Dark mode: ",
            },
            "reload": "Reload",
            "games_to_watch": "Games to Watch",
            "games_help": "Select games to watch. Order matters - drag to reorder priority (top = highest priority).",
            "search_games": "Search games...",
            "select_all": "Select All",
            "deselect_all": "Deselect All",
            "selected_games": "Selected Games (drag to reorder)",
            "available_games": "Available Games",
            "no_games_selected": "No games selected. Check games below to add them.",
            "no_games_match": "No games match your search.",
            "all_games_selected": "All games are selected or no games available.",
            "actions": "Actions",
            "connection_quality": "Connection Quality:",
            "minimum_refresh": "Minimum Refresh Interval (minutes):",
        },
        "help": {
            "links": {
                "name": "Useful Links",
            },
            "how_it_works": "How It Works",
            "how_it_works_text": (
                "Every several seconds, the application pretends to watch a particular stream "
                "by fetching stream metadata - this is enough to advance the drops. "
                "Note that this completely bypasses the need to download "
                "any actual stream of video and sound. "
                "To keep the status (ONLINE or OFFLINE) of the channels up-to-date, "
                "there's a websocket connection established that receives events about streams "
                "going up or down, or updates regarding the current number of viewers."
            ),
            "getting_started": "Getting Started",
            "getting_started_text": (
                "1. Login to the application.\n"
                "2. Ensure your Twitch account is linked to all campaigns "
                "you're interested in mining.\n"
                "3. If you're interested in mining everything possible, "
                'change the Priority Mode to anything other than "Priority list only" '
                'and press on "Reload".\n'
                '4. If you want to mine specific games first, use the "Priority" list '
                "to set up an ordered list of games of your choice. "
                "Games from the top of the list will be attempted to be mined first, "
                "before the ones lower down the list.\n"
                '5. Keep the "Priority mode" selected as "Priority list only", '
                "to avoid mining games that are not on the priority list. "
                "Or not - it's up to you.\n"
                '6. Use the "Exclude" list to tell the application '
                "which games should never be mined.\n"
                "7. Changing the contents of either of the lists, or changing "
                'the "Priority mode", requires you to press on "Reload" '
                "for the changes to take an effect."
            ),
            "about": "About Twitch Drops Miner",
            "about_text": "This application automatically mines timed Twitch drops without downloading stream data.",
            "how_to_use": "How to Use",
            "features": "Features",
            "important_notes": "Important Notes",
            "github_repo": "GitHub Repository",
        },
        "header": {
            "title": "Twitch Drops Miner",
            "language": "Language:",
            "initializing": "Initializing...",
            "auto_mode": "AUTO",
            "manual_mode": "MANUAL",
        },
    },
}


class Translator:
    def __init__(self) -> None:
        self._langs: list[str] = []
        # start with (and always copy) the default translation
        self._translation: Translation = default_translation.copy()
        # if we're in dev, update the template English.json file
        default_langpath = LANG_PATH.joinpath(f"{DEFAULT_LANG}.json")
        json_save(default_langpath, default_translation)
        self._translation["language_name"] = DEFAULT_LANG
        # load available translation names
        for filepath in LANG_PATH.glob("*.json"):
            self._langs.append(filepath.stem)
        self._langs.sort()
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
            # default language selected - use the memory value
            self._translation = default_translation.copy()
        else:
            self._translation = json_load(
                LANG_PATH.joinpath(f"{language}.json"), default_translation
            )
            if "language_name" in self._translation:
                raise ValueError("Translations cannot define 'language_name'")
        self._translation["language_name"] = language

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
