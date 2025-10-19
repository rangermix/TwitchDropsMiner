"""GraphQL operations for Twitch API interactions."""

from __future__ import annotations

from .constants import GQLOperation


GQL_OPERATIONS: dict[str, GQLOperation] = {
    # returns stream information for a particular channel
    "GetStreamInfo": GQLOperation(
        "VideoPlayerStreamInfoOverlayChannel",
        "198492e0857f6aedead9665c81c5a06d67b25b58034649687124083ff288597d",
        variables={
            "channel": ...,  # channel login
        },
    ),
    # can be used to claim channel points
    "ClaimCommunityPoints": GQLOperation(
        "ClaimCommunityPoints",
        "46aaeebe02c99afdf4fc97c7c0cba964124bf6b0af229395f1f6d1feed05b3d0",
        variables={
            "input": {
                "claimID": ...,  # points claim_id
                "channelID": ...,  # channel ID as a str
            },
        },
    ),
    # can be used to claim a drop
    "ClaimDrop": GQLOperation(
        "DropsPage_ClaimDropRewards",
        "a455deea71bdc9015b78eb49f4acfbce8baa7ccbedd28e549bb025bd0f751930",
        variables={
            "input": {
                "dropInstanceID": ...,  # drop claim_id
            },
        },
    ),
    # returns current state of points (balance, claim available) for a particular channel
    "ChannelPointsContext": GQLOperation(
        "ChannelPointsContext",
        "374314de591e69925fce3ddc2bcf085796f56ebb8cad67a0daa3165c03adc345",
        variables={
            "channelLogin": ...,  # channel login
        },
    ),
    # returns all in-progress campaigns
    "Inventory": GQLOperation(
        "Inventory",
        "d86775d0ef16a63a33ad52e80eaff963b2d5b72fada7c991504a57496e1d8e4b",
        variables={
            "fetchRewardCampaigns": False,
        },
    ),
    # returns current state of drops (current drop progress)
    "CurrentDrop": GQLOperation(
        "DropCurrentSessionContext",
        "4d06b702d25d652afb9ef835d2a550031f1cf762b193523a92166f40ea3d142b",
        variables={
            "channelID": ...,  # watched channel ID as a str
            "channelLogin": "",  # always empty string
        },
    ),
    # returns all available campaigns
    "Campaigns": GQLOperation(
        "ViewerDropsDashboard",
        "5a4da2ab3d5b47c9f9ce864e727b2cb346af1e3ea8b897fe8f704a97ff017619",
        variables={
            "fetchRewardCampaigns": False,
        },
    ),
    # returns extended information about a particular campaign
    "CampaignDetails": GQLOperation(
        "DropCampaignDetails",
        "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
        variables={
            "channelLogin": ...,  # user login
            "dropID": ...,  # campaign ID
        },
    ),
    # returns drops available for a particular channel
    "AvailableDrops": GQLOperation(
        "DropsHighlightService_AvailableDrops",
        "9a62a09bce5b53e26e64a671e530bc599cb6aab1e5ba3cbd5d85966d3940716f",
        variables={
            "channelID": ...,  # channel ID as a str
        },
    ),
    # retuns stream playback access token
    "PlaybackAccessToken": GQLOperation(
        "PlaybackAccessToken",
        "ed230aa1e33e07eebb8928504583da78a5173989fadfb1ac94be06a04f3cdbe9",
        variables={
            "isLive": True,
            "isVod": False,
            "login": ...,  # channel login
            "platform": "web",
            "playerType": "site",
            "vodID": "",
        },
    ),
    # returns live channels for a particular game
    "GameDirectory": GQLOperation(
        "DirectoryPage_Game",
        "98a996c3c3ebb1ba4fd65d6671c6028d7ee8d615cb540b0731b3db2a911d3649",
        variables={
            "limit": 30,  # limit of channels returned
            "slug": ...,  # game slug
            "imageWidth": 50,
            "includeCostreaming": False,
            "options": {
                "broadcasterLanguages": [],
                "freeformTags": None,
                "includeRestricted": ["SUB_ONLY_LIVE"],
                "recommendationsContext": {"platform": "web"},
                "sort": "RELEVANCE",  # also accepted: "VIEWER_COUNT"
                "systemFilters": [],
                "tags": [],
                "requestID": "JIRA-VXP-2397",
            },
            "sortTypeIsRecency": False,
        },
    ),
    "SlugRedirect": GQLOperation(  # can be used to turn game name -> game slug
        "DirectoryGameRedirect",
        "1f0300090caceec51f33c5e20647aceff9017f740f223c3c532ba6fa59f6b6cc",
        variables={
            "name": ...,  # game name
        },
    ),
    "NotificationsView": GQLOperation(  # unused, triggers notifications "update-summary"
        "OnsiteNotifications_View",
        "e8e06193f8df73d04a1260df318585d1bd7a7bb447afa058e52095513f2bfa4f",
        variables={
            "input": {},
        },
    ),
    "NotificationsList": GQLOperation(  # unused
        "OnsiteNotifications_ListNotifications",
        "11cdb54a2706c2c0b2969769907675680f02a6e77d8afe79a749180ad16bfea6",
        variables={
            "cursor": "",
            "displayType": "VIEWER",
            "language": "en",
            "limit": 10,
            "shouldLoadLastBroadcast": False,
        },
    ),
    "NotificationsDelete": GQLOperation(
        "OnsiteNotifications_DeleteNotification",
        "13d463c831f28ffe17dccf55b3148ed8b3edbbd0ebadd56352f1ff0160616816",
        variables={
            "input": {
                "id": "",  # ID of the notification to delete
            }
        },
    ),
}
