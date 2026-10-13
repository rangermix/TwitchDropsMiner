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
| Settings | Game priorities, reward types, ignored names, language, login access, and notifications |
| Help | A quick explanation of mining and Telegram setup |

## Choose games and priorities

In **Settings → Games to Watch**, put the games you most want first. Drag a game to
reorder it, or enter a whole-number priority. **1** is highest. Numbers beyond the
list's bounds move the game to the nearest end; blank or fractional values leave its
position unchanged.

Search for a game and press **Enter** or select **Add Game**. An exact name or unique
partial match selects an available game. If several names match, use a more specific
search. You can also confirm adding a manually entered name. Select **Reload** after
adding games to refresh the miner's selection.

**Select All** keeps your existing priority order and manual entries, then adds missing
games. **Deselect All** asks for confirmation. You can cancel a confirmation with
**Escape**. Your choices are saved for future runs.

**Mining Benefits** controls which reward types the miner targets: items, badges,
emotes, and other rewards. These choices are separate from the display filters on
the Inventory tab.

## Channels and manual selection

TDM chooses among live channels that can earn your selected rewards. Game priorities,
campaign participation, and channel eligibility all affect the choice.

You can select an eligible channel from the Main tab. Switching to a different game
while already watching enters **Manual Mode**, which keeps that game as the target.
If the channel becomes unavailable, TDM tries another eligible channel for that game.
Use **Return to Auto Mode** to resume normal game priorities. Manual mode also ends
when its game has no remaining eligible drops or no suitable channel is available.

### Special Events and IRL campaigns

Include the campaign's category in **Games to Watch**. These campaigns can progress
on their listed participating channels even when a channel streams another category
or lacks the drops-enabled flag. Channels must still be live and eligible. A campaign
without an enabled participating-channel list requires the usual matching category.

Participants streaming categories outside Games to Watch have the lowest automatic
priority. If the watched participant goes offline or becomes ineligible, another
eligible participant can take over.

## Inventory and the wanted queue

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

## Account-link override

In Settings, **Allow mining campaigns reported as Not Linked** is off by default.
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

In **Settings → Ignored Drop Keywords**, enter one literal part of a drop name per
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

Use **Reload** after changing the games you want or when you need to refresh campaign
selection. **Settings → Clear All Cache** discards derived campaign and channel data,
then reloads it from Twitch. It preserves Twitch login, settings, and dashboard
password protection.

Clearing cache is useful for recovery; it cannot correct inaccurate campaign details
returned by Twitch. If progress or eligibility still looks wrong, follow
[Troubleshooting](troubleshooting.md).

[All guides](README.md) · [Telegram notifications](notifications.md)
