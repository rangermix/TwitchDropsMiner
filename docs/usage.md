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
| Points | Channel points of idle watching: pause button, listed channels with balance and last gain, the channel points log, and the idle watching settings |
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

### Prefer channels you follow

**Settings → Mining → Prefer channels I follow** is available in v2.6.0 and later
and is off by default. When on, TDM looks up live channels your Twitch account
follows, including channels outside each game's top directory results, and
verifies their drop eligibility with Twitch. The
preference breaks ties between channels with the same mining priority, campaign
allowlist priority, and currently eligible drops. Your Mining list, temporary
queue order and reward settings still take precedence. A manually chosen channel
is protected from the follow tie-break while it stays selected. TDM checks up to
100 live followed channels each time it reloads channels.

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
Successful claims refresh the queue. In v2.5.0, each queued reward shows available artwork
and the same progress as Inventory; a reward named like its drop is listed once.

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

Starting in v2.6.0, select **ⓘ** next to the queue title to see which rules currently
set its order. Games outside the temporary order use the default rules.

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

TDM waits for Twitch to report the campaign as linked before automatically claiming
completed game rewards. The mining override applies only to watching. Completed rewards
waiting for a link stay visible in Inventory and leave the Wanted Drops Queue. TDM can
claim them after an inventory refresh reports the link, up to 24 hours after the
campaign ends. Drops containing only Twitch badges or emotes can be claimed without
linking.

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

## Idle watching

**Points -> Idle watching channels** lists Twitch channels (lowercase
logins, at most 20) that TDM watches when nothing can be mined. Idle watching runs only
when no drop can be mined and only inside mining hours. A listed channel that a drop can
use goes to drop mining instead.

- Each live listed channel gets one visit of up to 7 minutes per stream, to count toward
  its watch streak. Finished visits are kept in `data/idle_visits.json` together with
  the account they belong to, so a restart or a new login with the same account does not
  repeat them. Switching to another account drops the previous account's visits and
  shares, so switching back can repeat visits and resend a share whose outcome was
  unknown. If the file is damaged, TDM leaves it as it is and neither saves visits nor
  shares watch streaks until the file is fixed or deleted and TDM restarts.
- Twitch counts a stream that starts less than 30 minutes after the channel's last
  stream ended as the same stream. When such a restart follows a visit that got its
  watch streak bonus, TDM skips the visit. Otherwise the restart gets a visit, also
  when TDM did not see the channel go offline. TDM remembers this only while it runs.
- A listed channel where the account is banned is skipped. After a timeout ends, TDM
  tries the channel again without waiting for the next idle start.
- After the visits, TDM watches the first live listed channel in priority order for
  channel points. The `idle_priority` setting picks the order, for the visits and for
  this pick: `list` (default, the order of the list), `points_low` or `points_high`
  (lowest or highest balance first; a channel whose balance TDM has not read yet comes
  last), or `subscribed` (channels with an active points multiplier, such as a
  subscription, first, largest multiplier first). Ties keep the list order. With the
  balance orders TDM keeps watching its channel while it stays live, until another
  live listed channel's balance is more than 1000 points lower (`points_low`) or higher
  (`points_high`); a visit to a new stream still goes first.
- `idle_streak_first` (on by default) makes the watch streak visits. Turned off, TDM
  makes no visits and watches the first channel in priority order.
- `streak_recovery` (on by default): a visit that ran its full 7 minutes without a watch
  streak bonus gets up to 2 more visits in the same stream, after every other channel's
  first visit and at least 10 minutes apart. A watch streak bonus ends them. TDM cannot
  see whether a streak is at risk, so this is a second try, not a recovery Twitch
  confirms. Twitch's own recovery (5 minutes of a new live stream within 24 hours of the
  missed one) is covered by the visit every new stream gets. TDM remembers recovery
  visits only while it runs.
- A listed channel may stream a game with drops that is not in your Mining list. Twitch
  can then credit progress on those drops while TDM idle watches the channel, and TDM
  claims a finished drop as it does every claimable drop in the inventory.
- **Claim channel points bonus while idle watching** (on by default) claims the channel
  points bonus while idle watching. Twitch creates a bonus only on the channel being
  watched, so on the other live listed channels TDM only claims a bonus that was left
  open, for example while you watched that channel in a browser. TDM never claims on a
  channel that is not listed, is offline, has banned the account or that a drop can
  use, and never while mining drops, while paused or outside mining hours. A claim
  counts only when Twitch returns it. After a failed claim on the
  watched channel, TDM reads its points again on the next idle steps (about every 30
  seconds) and retries at most twice per watched channel; after that it waits for
  Twitch's next bonus message or another channel. A successful claim allows two retries
  again. A failed claim on another listed channel is retried by the next points read.
- About every 5 minutes while idle watching, TDM reads the channel points of the other
  live listed channels in one request: balance, active multipliers and an open bonus,
  which it claims. A failed or partial read waits for the next one.
- **Pause channel points** (the button on the **Points** tab, or `POST /api/points/pause`
  with `{"paused": true}` or `false`) stops idle watching and its watch until resumed or
  until TDM restarts. Drop mining is not affected. The status line of the tab shows
  **Active**, **Paused** or **Inactive** (idle watching is not running). If the request
  fails, the button keeps its state and the tab shows an error.
- The **Points** tab lists the listed channels with their live state, the last known
  balance and the last gain (amount, reason and time); a dash means TDM has not seen a
  balance or a gain yet. Below it, the channel points log keeps its own lines (gains,
  claims, visits, shares, pause), separate from the main console and kept for the
  current run. The settings of this section sit under the log and save when changed; a
  failed save shows an error and restores the previous value.
- **Share watch streaks while idle watching** (off by default) shares a watch streak
  once Twitch marks it shareable, once per milestone, for live listed channels. The
  check runs about every 5 minutes and right after a watch streak message. Twitch posts
  a visible watch streak notice in the streamer's chat under your name, and a share may
  give bonus channel points. Sharing happens only while idle watching, never while
  mining drops, while paused or outside mining hours.
  - TDM records each share in `data/idle_visits.json` before it sends it (the newest 100
    are kept), so a restart or a new login with the same account does not send it again.
    A share that cannot be recorded is not sent; a later check tries again once the file
    can be written.
  - A share whose outcome is unknown (no reply, a timeout, a server error, or a reply
    without the shared milestone) is not sent again. Only a reply with an error code and
    a null milestone counts as a refusal; a refused share is tried once more, also after
    a restart.
- `claim_moments` (on by default) claims a community moment on a live listed channel
  once per moment. It costs nothing and is not visible to others.
- `follow_raids` (off by default) joins a raid that starts on the idle-watched channel,
  once per raid. Joining is visible to the streamer. TDM does not watch the raid target:
  it keeps watching its listed channels. Twitch may refuse the request with an integrity
  check error; TDM logs the failure and does not retry it.
- `predictions_enabled` (off by default) bets channel points once per prediction on live
  listed channels, by default 6 seconds before the prediction closes, using the vote
  counts seen last. Bets spend points and are visible to the streamer. The timing uses the
  host clock against Twitch's start time, so keep the clock synchronised: a clock that
  is more than a few seconds off bets too early on early counts, or not at all.
  - Once means one bet per prediction while TDM runs. TDM never sends a second bet on a
    prediction, also not after a failed or unanswered bet. It places no bet on a
    prediction that was locked, ended or canceled, or that Twitch reported the account
    already bet on while TDM listened (for example a bet placed in a browser). TDM keeps
    this in memory only: after a restart or a new login it can bet again on a prediction
    that is still open, because Twitch reports a bet only at the moment it is placed.
  - `prediction_strategy` picks the outcome: `smart` (default) takes the two outcomes
    with the most users and, when their user split is within 20 percentage points, bets
    on the one of the two with fewer points, otherwise on the outcome with the most users; `most_voted` picks the most
    users, `high_odds` the fewest points (the largest payout per point), `favourite` the
    most points placed, `fixed` outcome number `prediction_fixed_option` (1 to 10, default
    1, counted in the order Twitch lists the outcomes; fewer outcomes skip the bet). A tie
    picks the first outcome.
  - `prediction_timing` sets when the bet is placed: `before_close` (default)
    `prediction_delay_seconds` (default 6, 0 to 3600) before the window closes,
    `after_open` that many seconds after the prediction opens, `percent`
    `prediction_timer_percent` (default 50, 1 to 99) percent of the window after it opens.
    No mode bets later than 1 second before the window closes, and a time already past
    means a bet at once. The time is set when TDM first sees the prediction; changing the
    option later does not move a bet that is already waiting.
  - Three filters skip a bet and write a line to the points log. All are off at their
    default. `prediction_min_balance` (0 to 10000000, default 0) skips while the channel
    balance is below it. `prediction_min_voters` (0 to 1000000, default 0) skips unless at
    least that many users voted over all outcomes. `prediction_min_odds` (1 to 100,
    default 1, two decimals) skips unless the chosen outcome pays at least that many
    points per point staked, which is all points divided by the points of the chosen
    outcome. An outcome without points counts as infinite odds; when no outcome has
    points the odds are unknown and the bet is skipped. The filters use the vote counts
    seen last, when the bet is due.
  - The stake is `prediction_percent` (default 5) of the channel's balance, rounded
    down, at most `prediction_max_points` (default 1000) and never more than the balance.
    No bet is placed below 10 points, with an unknown balance, or when the prediction is
    no longer open, or when Twitch sent a prediction without a usable start time or
    length. Each such skip and each result (won, lost, refunded) goes to the channel
    points log; a bet that may not be placed at that moment (paused, option off,
    channel offline or usable for drops) is not logged there. A bet without an answer from Twitch (a timeout, a lost connection, or a server
    error such as "service timeout") is logged as possibly placed. The result of every
    bet TDM sent is logged when Twitch sends one. No strategy is known to beat the other
    viewers.
- `join_chat` (off by default) joins the chat of the idle-watched channel over Twitch's
  chat websocket with your session, and leaves it when TDM switches channels, stops idle
  watching or pauses channel points. TDM never sends chat messages. Twitch lists no
  channel points for chat, so this only shows you in the chat's viewer list. While
  joined, Twitch sends TDM every chat message of that channel, which TDM reads and
  drops; on a busy channel that is steady network traffic. A lost
  connection is retried with a growing wait of at most 5 minutes; the wait starts over
  only after a connection stayed up for a minute. When Twitch refuses the chat login,
  TDM does not try again until the account or its session token changes, or until
  `join_chat` is turned off and on again.
- The bonus claim, raids, moments, predictions and chat run the same checks again right
  before each request: only while idle watching, only on live listed channels where the
  account is not banned, and never on a channel that drops can use, while channel points
  are paused or outside mining hours. Raids and chat use only the watched channel;
  claims, moments and predictions use every live listed channel. TDM follows the raid,
  moment and prediction events only while idle watching and only for the options that
  are on.
- While idle watching, TDM sends a stream lookup for listed channels it does not track
  yet, a channel points read when it starts watching a channel, one batched channel
  points read of the other live listed channels about every 5 minutes, and a watch
  streak read about every 5 minutes when sharing is on, and one account login read
  when it first joins a chat. The normal watch loop also sends its drop
  progress request for the watched channel every minute, and each newly tracked idle
  channel gets a ban status check. The hourly channel reload usually drops the idle
  channels (an idle channel streaming a wanted game stays tracked), so the stream lookup
  and the channel points read run again after it.
- TDM tracks at most 198 channels, drop and idle channels together, because the channel
  points topic takes one websocket slot, also with an empty idle list. Idle channels
  over that limit are skipped with a warning and retried the next time idle watching
  starts. The raid, moment and prediction events use the room that is left; when it runs
  out, some of them are not followed and the channel points log says so once.

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
