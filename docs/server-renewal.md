# Experimental server renewal

Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118). This source
feature is still undergoing expiry-crossing verification; it is not a released fix.
Two live Python-helper runs in Alpine Chromium 152 obtained distinct tokens, passed
account/Inventory/Campaigns validation, and extended the SDK cookie's expiry. The second
run used only the first server run's saved replacement in a new container/profile.
This does not yet prove sustained operation across the original seed's expiry.

The local computer is needed for initial Twitch login and export. Afterwards a separate
helper on your home server runs headless Chromium only during renewal. Ordinary miner
operations remain Python HTTP. The core Dockerfile still uses Alpine, and no Python
dependencies are added. The optional helper image adds Chromium as an OS package.

## Initial connection

1. Follow [manual export and import](../README.md#manual-local-browser-export-and-import-experimental),
   adding `--server-seed "$HOME/tdm-server-seed.json"` to the export command. Import the
   ordinary `tdm-session.json` into TDM. Keep dashboard protection enabled.
2. Download the renewal connection from TDM's login panel. Transfer that file and the
   server seed privately to the home server; neither belongs in Git, an issue or chat.
3. On the server, store them as `renewal-state/renewal.json` and
   `renewal-state/server-seed.json`. Give the directory mode `700` and both files `600`.
   Run only one helper for this state directory. It replaces the seed file as it renews.
4. Check the connection file's `endpoint` is reachable **from the helper**. It must be
   HTTPS, or literal loopback HTTP. For a helper sharing the miner's container network,
   change only `endpoint` to `http://127.0.0.1:8080/api/session/renew` (use the miner's
   internal port if different). Preserve the downloaded account and credential fields.
   A public HTTPS endpoint also works if reachable from the server; redirects are refused.

You can close the local browser after exporting. Do not run the local-browser renewal
helper at the same time. A new connection download replaces the old helper credential.

## Start on the server

Build both images from the same source checkout:

```bash
docker build -t tdm:local .
docker build -f Dockerfile.renewal --build-arg TDM_IMAGE=tdm:local -t tdm-renewal:local .
```

Run the miner using that core image with `TDM_SESSION_IMPORT=1`, persistent data and
dashboard protection, as described in the README. For a miner container named `tdm`:

```bash
docker run -d --name tdm-renewal --init --stop-timeout 20 --restart unless-stopped \
  --network container:tdm --shm-size=256m \
  --mount type=bind,source="$PWD/renewal-state",target=/state \
  tdm-renewal:local
```

Replace `tdm` with the actual miner container name. No browser or DevTools port is
published. The optional image runs Chromium with `--no-sandbox` inside the helper
container. `--init` reaps browser descendants; the stop grace allows bounded target and
process cleanup when Docker sends SIGTERM. A native server with Chromium installed can instead run:

```bash
source env/bin/activate
python -m src.auth.server_renewal \
  --seed /private/path/server-seed.json --connection /private/path/renewal.json \
  --chromium /usr/bin/chromium
```

Add `--once` for one validated delivery and exit. Otherwise renewal happens immediately,
then normally five minutes before the accepted token expires. Successful output contains
only `event`, `expires_at` and `generation`; confirm that TDM's session generation advances.
Transient failures retry with bounded delay. Chromium closes between attempts.

## Persistence and recovery

The helper independently checks the intended account and protected catalog after
Chromium closes. Only then does it atomically save the new context and SDK cookie, and
deliver the context using the account-bound renewal credential. Failed validation or
saving preserves the prior seed. A restarted helper reads the server's saved replacement;
it does not contact the local computer or copy its profile again.

The tested replacement SDK cookies had about 24 hours remaining. This is observed
behavior, not a guaranteed lifetime. Keep the helper running: a sufficiently long outage,
expired SDK state, OAuth revocation or a changed Twitch check can require a new local
login/export. `SESSION_SDK_EXPIRED` stops attempts before launching Chromium. Invalid
pairing or a wrong account also stops the command; Docker's restart policy may restart it.
TDM waits when no valid context remains. It never treats a rejected token as successful
renewal. Longer operation and automatic-claim evidence are still pending.
