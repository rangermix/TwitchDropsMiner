# Streamlink client-integrity (experimental)

Around 2026-09-18 Twitch put `currentUser.dropCampaigns` behind a
`Client-Integrity` header. The miner keeps working on campaigns already in your
inventory, but it can no longer discover new ones, which is what
[rangermix#118](https://github.com/rangermix/TwitchDropsMiner/issues/118) and
[DevilXD#1165](https://github.com/DevilXD/TwitchDropsMiner/issues/1165) track.

A token minted by POSTing to `/integrity` over plain HTTP is rejected. Only one
produced by a real browser passes. Streamlink already solves this for its own
Twitch plugin, so this branch reuses `TwitchClientIntegrity.acquire` instead of
driving Chromium directly.

Ported from Donistr's patch for DevilXD's codebase, posted in
[#1165](https://github.com/DevilXD/TwitchDropsMiner/issues/1165#issuecomment-5828653655).

## What changed

- `src/auth/integrity.py` — mints a token in a subprocess (Streamlink runs on
  trio, which cannot share a thread with our asyncio loop).
- `_AuthState` holds the token, renews it 15 minutes before expiry, and attaches
  it only to requests that ask for it. Acquisition failure is not fatal: the
  miner keeps running on its inventory and retries after 5 minutes.
- Only the three gated operations (`Campaigns`, `CampaignDetails`, `ClaimDrop`) send the
  header. Everything else is untouched.
- The default client is now `WEB`, because integrity tokens are bound to the
  client id that minted them.

## Setup

`WEB` has no device-code flow, so the access token has to come from a browser
session. In a browser logged into Twitch: DevTools → Application → Cookies →
`https://www.twitch.tv` → copy `auth-token`, then:

```bash
read -rsp 'auth-token: ' TDM_WEB_AUTH_TOKEN && export TDM_WEB_AUTH_TOKEN
docker compose up -d --build
```

`read -s` keeps the token off the screen and out of shell history, and nothing
is written to disk except `data/cookies.jar`, where the miner saves the session
on first run. Later runs don't need the variable. Without a saved session or
the variable, the miner stops with an error saying so. Twitch login cookies
last about a year, but signing out of that browser session revokes it.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TDM_WEB_AUTH_TOKEN` | — | Browser `auth-token`, needed for the first run |
| `TDM_CHROMIUM_PATH` | `/usr/bin/chromium-browser` | Browser Streamlink drives |
| `TDM_INTEGRITY_HEADLESS` | `0` | Leave off — see below |

The Docker image installs Chromium and Xvfb for this. Chromium is only
launched during acquisition and exits afterwards.

The compose service needs `shm_size: 512m`, because Chromium crashes on
Docker's default 64 MB `/dev/shm`. The bundled `docker-compose.yml` sets it.

## Headful, not headless

Twitch issues a token to headless Chromium but then rejects it on the gated
operations, exactly as if none were sent. The same Chromium run headful under a
virtual display passes. Measured 2026-09-25 from a residential connection, same
account and device id, fresh browser profile each time, no SDK-cookie seed:

| Chromium | `dropCampaigns` |
| --- | --- |
| headless (`--headless=new`, Streamlink's default) | `failed integrity check`, 3/3 runs |
| headful under `xvfb-run` | 149 campaigns, no errors, 3/3 runs |
| no `Client-Integrity` header (control) | `failed integrity check` |

So acquisition runs headful and is wrapped in `xvfb-run` whenever there is no
`DISPLAY`. When Twitch does reject a token, the miner logs a warning and keeps
mining its inventory rather than failing.

## Verified

2026-09-25, local Docker, residential connection: with the campaign catalog
loading (34 eligible campaigns), the miner watched a Rainbow Six Siege channel
and Twitch's own Inventory — queried separately, not the miner's display —
went from no campaign in progress to 1 and then 3 minutes watched.

## Don't run another watcher on the same account

Twitch credits drop progress to one stream at a time per account. Anything else
watching on that account — a channel-points miner, a browser tab, the app —
can hold that slot, and the miner then watches without earning anything. Every
watch event still returns HTTP 204, so nothing looks wrong.

This cost a long debugging session: with a points miner running elsewhere on the
account, 17 minutes of accepted watch events earned nothing, and Twitch
credited the miner within 90 seconds of the points miner stopping.

## Known limits

- Only verified from one residential connection. rangermix reports Docker
  browsers failing on a VPS, so hosting IP ranges may behave differently.
- Token lifetime varies: the first tokens were issued for about 14 hours,
  every one since a fresh login for 1 hour. Renewal is due 15 minutes before
  expiry; a live renewal is not yet observed.
- Donistr reported not knowing how long such a session stays valid, and that
  remains untested here.
- rangermix's `codex/twitch-browser-login` branch solves the same problem with
  a seeded SDK cookie and a separate helper container. If that ships, prefer it
  — it does not need a browser inside the miner image.
