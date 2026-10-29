# Using the dashboard

After TDM restores your session or accepts a [browser login](authentication.md), wait
for it to discover campaigns. Choose the games and rewards you want, then leave the
miner running while it selects eligible channels and tracks progress.

Link the correct game account to receive in-game rewards. TDM skips campaigns
reported as Not Linked by default, except for Twitch badge/emote campaigns. Check the campaign
on [Twitch Drops](https://www.twitch.tv/drops/campaigns) for eligibility and linking
instructions. Avoid watching Twitch manually with the same account while TDM runs;
simultaneous viewing can disrupt progress.

## Find your way around

| Tab | Purpose |
| --- | --- |
| Main | Login status, the current campaign and drop, wanted rewards, console output, and channels |
| Inventory | Campaigns and rewards, with status, benefit, and game filters |
| History | Locally recorded claims, statistics, and CSV export |
| Settings | Grouped into sub-tabs: **General** (theme, connection, proxy), **Mining** (reward types, mining hours, account-link override, ignored names), **Games** (priorities and reload), **Notifications** (Discord, Telegram), and **Account & Maintenance** (dashboard password, cache, diagnostics, Twitch logout) |
| Help | A quick explanation of mining and Telegram setup |

Use Left/Right arrows, Home or End to switch Settings sub-tabs from the tab bar.

## Choose games and priorities

In **Settings → Games → Mining list**, put the games you most want first. Drag a game to
reorder it, or enter a whole-number priority. **1** is highest. Numbers beyond the
list's bounds move the game to the nearest end; blank or fractional values leave its
position unchanged.

Search for a game and press **Enter** or select **Add Game**. An exact name or unique
partial match selects an available game. If several names match, use a more specific
search. You can also confirm adding a manually entered name. Select **Reload Campaigns**
beside **Add Game** after adding games to refresh the miner's selection.

**Select All** keeps your existing priority order and manual entries, then adds missing
games. **Deselect All** asks for confirmation. You can cancel a confirmation with
**Escape**. Your choices are saved for future runs.

Each game shows a **LINKED** or **NOT LINKED** badge, based on the campaigns in your
inventory. Linking belongs to the game account, so a game counts as linked when any of
its campaigns is. Games without a known campaign, such as manually added names, show
no badge. **Available Games** lists linked games first, and your selected games stay in
priority order. The dropdown next to the search filters the available games by link
status; **Not Linked** only shows games known to be unlinked, so games without a badge
are hidden by both **Linked** and **Not Linked**. **Select Linked** adds every linked
game that is not yet selected, keeping your existing order.

Version 2.3.0 adds **Priority list only**, enabled by default; it now sits in
**Settings → Games** above the game list.
Leave it enabled to mine only games in **Mining list**. Disable it to prefer
those games, then mine other eligible games whenever no selected game can currently
be progressed. Other games stay out of your saved list, so you can keep a short list
of favorites. With an empty list, disabling this option allows all eligible games.
Fallback games with campaigns ending soonest are preferred. Account linking, reward
types, ignored names, mining hours and other eligibility rules still apply. Manual
channel selection retains its existing behavior.

## Reward types

**Settings → Mining → Reward types** lists the four reward types. Untick a type to stop mining
it. These mining choices are separate from the Inventory tab's display filters.

Ordering is optional and disabled by default. Enable **Prefer reward types in this
order**, then drag the rows or enter a position number, as in the Mining list. Changes
save immediately. Higher rows come first among currently earnable rewards; a reward
locked behind prerequisites does not yet receive its preferred type rank. Within the
same type, earlier reward deadlines come first, followed by the existing game order.
Selected games always take precedence over additional fallback games. The queue uses
the same order while keeping upcoming and sequential rewards visible. This does not
reorder your saved game list.

An unticked type stays in the list, marked as not mined. Ordering does not re-enable
a disabled type or bypass account linking, campaign times, prerequisites, ignored
rewards or participating-channel requirements. Twitch may progress multiple rewards
simultaneously; this setting selects the preferred channel and campaign.

- **Game reward:** game or publisher rewards (Twitch calls these `DIRECT_ENTITLEMENT`).
- **Chat badge:** badges for Twitch chat.
- **Chat emote:** emotes for Twitch chat.
- **Other reward:** a type reported by Twitch that this version does not recognize.

## Channels and manual selection

TDM chooses among live channels that can earn your selected rewards. Game priorities,
campaign participation, and channel eligibility all affect the choice.

You can select an eligible channel from the Main tab. Switching to a different game
while already watching enters **Manual Mode**, which keeps that game as the target.
If the channel becomes unavailable, TDM tries another eligible channel for that game.
Use **Return to Auto Mode** to resume normal game priorities. Manual mode also ends
when its game has no remaining eligible drops or no suitable channel is available.

### Special Events and IRL campaigns

Include the campaign's category in **Mining list**. These campaigns can progress
on their listed participating channels even when a channel streams another category
or lacks the drops-enabled flag. Channels must still be live and eligible. A campaign
without an enabled participating-channel list requires the usual matching category.

Participants streaming categories outside Mining list have the lowest automatic
priority within their mining group. When fallback is enabled, eligible selected
campaigns still take precedence over games outside your saved list. If the watched
participant goes offline or becomes ineligible, another
eligible participant can take over.

## Inventory and the wanted queue

Select a campaign's overview or arrow to expand its rewards and progress. The arrow
supports Enter and Space. Campaign names link directly to Twitch; long game and campaign
names are shortened, with the full name shown on hover. The card and list buttons beside
**Clear Filters** switch the Inventory layout and remember your choice. List view offers
compact rows with artwork, names and dates. Opening a card does not move cards between
columns. In card mode the arrow sits below the linkage badge, with account/mining
actions beside the dates; open campaigns stay open while progress updates arrive. Each
column stacks its own cards, so opening a card only moves the cards below it. On
phone-width screens game and campaign names can use two lines.

For linked games not already selected, **Add to mining list** appends the game at
the end of your saved priority list. Existing priorities and manual entries keep their
order. The button disappears after the settings save succeeds; a failed save shows an
error and leaves the list unchanged. Linking and adding a game do not bypass reward
eligibility or guarantee progress. Use **Settings → Games** to reorder or remove selected games.

Inventory's **Active**, **Upcoming**, and **Expired** filters include campaigns with
any selected status. **Account link → All / Linked / Not Linked** narrows those
results using Twitch-reported link status. All adds no link restriction; Linked and
Not Linked select their respective states. This display filter does not change mining
eligibility or the account-link override. Fully claimed campaigns stay hidden until **Finished** is selected.
Use the benefit filters and game search to narrow the display further.

Zero-minute subscription rewards are omitted from Inventory and the Wanted Drops
Queue because they cannot be earned by watching. The queue also omits individually
expired rewards and rewards the miner cannot target. Upcoming and sequential rewards
can remain visible, so a queued reward is not necessarily earnable immediately.
Successful claims refresh the queue.

### Change the queue order temporarily

On **Main**, drag a game card in the Wanted Drops Queue to mine it sooner. On a touch
screen, press and hold a card first, then drag it. This order takes precedence over
the Mining list, reward-type order and fallback rules, but it only reorders games that
are already queued. Games that join the queue later are placed after the games you
ordered.

The order is temporary: restarting the miner, selecting **Reset to Mining list
order**, or changing **Mining list** (in Settings or with **Add to mining list** in
Inventory) returns to the saved order. To change the default order permanently, use
**Settings → Games → Mining list**.

## Account-link override

In **Settings → Mining**, **Allow mining campaigns reported as Not Linked** is off by default.
Enable it to attempt mining when Twitch reports a campaign as unlinked. Disabling
it restores the default account-link requirement. Changes are saved and refresh
game selection. The override leaves Twitch-reported link badges and Inventory
filters unchanged; all other campaign, drop, channel, priority, and ignore rules
still apply.

This option does not connect accounts or guarantee Twitch progress, claims, or
in-game rewards. TDM can display estimated minutes even without confirmed Twitch
progress. Check your [Twitch Drops inventory](https://www.twitch.tv/drops/inventory)
and link the correct game account to receive rewards. Do not unlink accounts just
to troubleshoot a reported status disagreement.

## Ignore unwanted reward names

In **Settings → Mining → Ignored Drop Keywords**, enter one literal part of a drop name per
line. Matching ignores case; these are plain substrings, not regular expressions.
Blank lines and surrounding whitespace are removed, and duplicate entries are combined.
The list is empty by default.

A matching unclaimed reward and unclaimed rewards that depend on it are ignored.
Prerequisite-only branches with no remaining wanted reward are skipped, but a
prerequisite shared by an allowed reward can still be mined. The dashboard distinguishes
ignored and skipped rewards from claimed rewards.

These rules control what TDM intentionally targets. Twitch may still award simultaneous
progress to an ignored reward while another reward advances.

## Drop history

The **History** tab records successful claims made by the miner in
`data/drop_history.json`. Claims that were already completed on Twitch are not backfilled.

Filter by game name or a **claimed on or after** date, browse the pages, and view
per-game and per-month statistics. The date filter begins at midnight UTC on the
selected day; displayed claim times use your browser's local timezone.

**Export CSV** downloads the current filtered view. The file supports Unicode game
names and includes a UTF-8 byte-order mark for spreadsheet compatibility.
**Clear** deletes all local history, including records hidden by a filter. It does
not change your Twitch account or take away rewards.

## Refreshing and clearing cache

Use **Reload Campaigns** (in the Inventory filter bar or beside the Mining list's **Add
Game** button) after changing the games you want or when you need to refresh campaign
selection. **Settings → Account & Maintenance → Clear All Cache** discards derived
campaign and channel data,
then reloads it from Twitch. It preserves Twitch login, settings, and dashboard
password protection.

Clearing cache is useful for recovery; it cannot correct inaccurate campaign details
returned by Twitch. If progress or eligibility still looks wrong, follow
[Troubleshooting](troubleshooting.md).

[All guides](README.md) · [Telegram notifications](notifications.md)

## Mining hours

Version 2.3.0 adds **Settings → Mining → Mining Hours**. **All day (24/7)** is the default.
Choose **Custom time range** and enter start and end times to watch only during that
window each day. The start is included and the end is excluded. A range such as
22:00–06:00 crosses midnight; equal start and end times mean all day.

Times use the local timezone of the device running TDM, not the dashboard browser.
For Docker, configure `TZ` to your home internet connection’s timezone, as described
in the [installation guide](installation.md). Clock or daylight-saving changes are
rechecked at least once per minute; normal start/end boundaries are scheduled directly.

Outside the window, the status shows **Outside mining hours** and watching stops.
The miner stays signed in. Reloads, cache clearing, and mining-policy recalculations
wait until the next window, when TDM fetches fresh inventory and resumes selection.
Changing the hours takes effect immediately after a successful save. Blank or invalid
times are rejected; save failures keep the previous schedule and show an error.
Malformed saved schedules are rejected at startup rather than silently mining all day.

This setting pauses watching, not Twitch reward claims: already earned rewards can
still be claimed through existing notifications or work already underway. It does
not guarantee Twitch-side progress. Reload the dashboard after upgrading to load the
versioned frontend controls.

## Pause mining

Select **Pause** in the status bar and choose **For 10 minutes**, **For 1 hour**,
**Until tomorrow**, or **Until I resume**. Watching stops right away and the status
shows **Paused**, or **Paused until** the resume time. A timed pause resumes by itself;
10 minutes and 1 hour use elapsed time, so clock corrections cannot change their length.
**Until tomorrow** ends at the next midnight in the miner's local time, like mining hours.
Select **Resume** to continue early. Resuming fetches fresh inventory, and mining hours
still apply afterwards.

Use it while you watch Twitch with the same account, so the two do not compete.
The pause is not saved: restarting TDM resumes mining. Already-earned rewards can
still be claimed while paused. Automations can call
`POST /api/pause` with `{"paused": true, "duration": "10m"}` (`"1h"`, `"tomorrow"`,
or omit `duration` to pause until resumed) and `{"paused": false}` to resume; the
request needs the `X-TDM-Request: 1` header and, with password protection, a
dashboard session.
