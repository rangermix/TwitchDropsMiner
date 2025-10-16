"""Client configuration for Twitch API interactions."""

from __future__ import annotations

import random
from yarl import URL


class ClientInfo:
    """Client configuration including URL, ID, and User-Agent."""

    def __init__(self, client_url: URL, client_id: str, user_agents: str | list[str]) -> None:
        self.CLIENT_URL: URL = client_url
        self.CLIENT_ID: str = client_id
        self.USER_AGENT: str
        if isinstance(user_agents, list):
            self.USER_AGENT = random.choice(user_agents)
        else:
            self.USER_AGENT = user_agents

    def __iter__(self):
        return iter((self.CLIENT_URL, self.CLIENT_ID, self.USER_AGENT))


class ClientType:
    """Predefined client configurations for different Twitch platforms."""

    WEB = ClientInfo(
        URL("https://www.twitch.tv"),
        "kimne78kx3ncx6brgo4mv6wki5h1ko",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
    )
    MOBILE_WEB = ClientInfo(
        URL("https://m.twitch.tv"),
        "r8s4dac0uhzifbpu9sjdiwzctle17ff",
        [
            # Chrome versioning is done fully on android only,
            # other platforms only use the major version
            (
                "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Linux; Android 16; SM-A205U) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Linux; Android 16; SM-A102U) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Linux; Android 16; SM-G960U) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Linux; Android 16; SM-N960U) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Linux; Android 16; LM-Q720) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Linux; Android 16; LM-X420) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.7204.158 Mobile Safari/537.36"
            ),
        ]
    )
    ANDROID_APP = ClientInfo(
        URL("https://www.twitch.tv"),
        "kd1unb4b3q4t58fwlpcbzcbnm76a8fp",
        [
            (
                "Dalvik/2.1.0 (Linux; U; Android 16; SM-S911B Build/TP1A.220624.014) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
            (
                "Dalvik/2.1.0 (Linux; U; Android 16; SM-S938B Build/BP2A.250605.031) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
            (
                "Dalvik/2.1.0 (Linux; Android 16; SM-X716N Build/UP1A.231005.007) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
            (
                "Dalvik/2.1.0 (Linux; U; Android 15; SM-G990B Build/AP3A.240905.015.A2) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
            (
                "Dalvik/2.1.0 (Linux; U; Android 15; SM-G970F Build/AP3A.241105.008) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
            (
                "Dalvik/2.1.0 (Linux; U; Android 15; SM-A566E Build/AP3A.240905.015.A2) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
            (
                "Dalvik/2.1.0 (Linux; U; Android 14; SM-X306B Build/UP1A.231005.007) "
                "tv.twitch.android.app/25.3.0/2503006"
            ),
        ]
    )
    SMARTBOX = ClientInfo(
        URL("https://android.tv.twitch.tv"),
        "ue6666qo983tsx6so1t0vnawi233wa",
        (
            "Mozilla/5.0 (Linux; Android 7.1; Smart Box C1) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
    )
