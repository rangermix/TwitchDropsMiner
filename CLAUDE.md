See AGENTS.md for shared repository instructions and current validation coverage,
including README, contributor and release automation, inventory/watch-drop filtering,
case-insensitive channel visibility, watch-drop count and expiry semantics, immediate claim
refresh behavior, frontend asset versioning, full-locale translation schema and placeholder
parity, and synchronized Docker action runtime pins.
The Settings **Clear All Cache** action uses `POST /api/cache/clear` to discard local
campaign, channel, and other derived miner state while preserving OAuth login and settings,
then reloads from Twitch. It is diagnostic recovery, not a Twitch metadata correction.
