# Installation

Run TDM on your own computer or home server with Docker. The published v2.1.0 image
includes the dashboard's Twitch login browser and automatic session renewal.
No desktop or display is needed on the server. Older images use their release's
authentication instructions.

## Docker

Pull and start the published image:

```bash
docker pull rangermix/twitch-drops-miner:2.1.0
docker run -d \
  --name twitch-drops-miner --init --stop-timeout 30 --shm-size 256m \
  -p 8080:8080 -e TZ=Australia/Sydney \
  -v "${PWD}/data:/app/data" \
  --restart unless-stopped \
  rangermix/twitch-drops-miner:2.1.0
```

Open [http://localhost:8080](http://localhost:8080). From another computer on your home
network, use the miner host's LAN address, such as `http://192.168.1.10:8080`.

The data mount keeps settings and login credentials outside the container. The init
process and shutdown timeout let TDM close its temporary renewal browser when stopped.
Only the dashboard port is needed; do not publish a browser or browser-control port.

The data and log mounts must enforce private Linux directory permissions. If your
host bind mount ignores them, use a Docker named volume, for example
`-v twitch-drops-miner-data:/app/data` in place of the data bind mount above. Preserve
that volume when updating. Copy existing data from a private backup while TDM is
stopped before changing mounts; a new empty volume does not contain the old login
or settings.

To build a source checkout, run `docker build -t twitch-drops-miner .` from the
repository root and use `twitch-drops-miner` as the image in the run command above.

To retain file logs outside the container, also mount `./logs:/app/logs`. You can read
the container's console output with:

```bash
docker logs --tail 100 twitch-drops-miner
```

## Docker Compose

Check that Compose is installed with `docker compose version`. Choose the published
image for a release installation, or build a source checkout if you want to run its
code. Commands below use Bash; run Compose commands from the folder containing your
chosen Compose file.

### Published image

For a new installation, create a dedicated folder and save the following as
`compose.yaml`. No Git clone or local image build is needed. Set `TZ` to match the
timezone of your home internet connection before starting:

```yaml
services:
  twitch-drops-miner:
    image: rangermix/twitch-drops-miner:2.1.0
    container_name: twitch-drops-miner
    init: true
    stop_grace_period: 30s
    shm_size: 256m
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      TZ: Australia/Sydney
    restart: unless-stopped
```

Start it from that folder:

```bash
docker compose pull && docker compose up -d
```

This example pins `2.1.0`, like the Docker quick start. Change the image tag to choose
another release, or use `latest` to select the current stable image whenever you
pull and recreate. The restart policy does not update images automatically.

Compose stores data in `./data` and file logs in `./logs`, relative to `compose.yaml`.
Keep this folder and its mounts when updating. The [directory-permission requirements](#docker)
apply to both mounts; use Docker named volumes if your host cannot enforce them.
The init process, 30-second shutdown grace period, shared memory, and timezone support
the integrated browser. Only port 8080 is published.

Open [http://localhost:8080](http://localhost:8080), or `http://YOUR-SERVER:8080` from
another device on your home network, and follow [Twitch login](authentication.md).
To protect access or use your own reverse proxy, follow
[Dashboard access](dashboard-access.md).

### Build a source checkout

The repository's [docker-compose.yml](../docker-compose.yml) builds source locally.
For a new checkout:

```bash
git clone https://github.com/rangermix/TwitchDropsMiner.git && cd TwitchDropsMiner
```

Edit `TZ` in `docker-compose.yml` to match your home internet connection, then build
and start from the repository root:

```bash
docker compose up -d --build
```

The included file keeps data in `./data` and logs in `./logs` and includes the same
browser settings as the published-image example. The default branch may contain
unreleased changes. To build a particular revision, check it out before starting.
Keep the published-image `compose.yaml` in a separate folder: Compose prefers that
filename over `docker-compose.yml` when both are present.

### Manage the service

Run these from the same Compose project folder:

| Action | Command |
| --- | --- |
| Check status | `docker compose ps` |
| Follow recent console logs | `docker compose logs --tail 100 -f twitch-drops-miner` |
| Stop mining | `docker compose stop` |
| Start the stopped container | `docker compose start` |

See [Compose update steps](#updating-with-docker-compose) to apply a new image or
source revision. Keep the same data/log mounts and project configuration. Do not use
`docker compose down -v` for updates; it can delete Compose-managed data volumes.

### Switch an existing docker run installation to Compose

Use the published-image configuration above, but set its data mount to the exact
directory or named volume used by your old container. Inspect the existing mount
with the [Docker update instructions](#published-image-docker-run) before changing
anything. An absolute bind path avoids accidentally selecting a new `./data` folder.
For an existing named volume, declare it under top-level `volumes:` with
`external: true` and `name:` set to its exact Docker volume name, then reference that
volume key in the service's `/app/data` mount.

Preserve your container name, ports, timezone, network configuration, and any custom
mounts or environment variables such as `PUBLIC_BASE_URL`. Back up data privately
while the miner is stopped and copy any unmounted logs you need before removing the
old container. From the folder containing the prepared `compose.yaml`, run:

```bash
docker compose pull &&
docker stop --timeout 30 twitch-drops-miner &&
docker rm twitch-drops-miner &&
docker compose up -d
```

Adapt the container name if you changed it. Compose creates its own managed container
after the old one is removed; reusing the same mount retains the saved data. Do not
run the old and new miners against the same data directory at the same time.

For more Compose options, see the [Docker Compose documentation](https://docs.docker.com/compose/intro/compose-application-model/).

## Run from source

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). The miner host also
needs a Chromium executable named `chromium` or `chromium-browser` on its `PATH` for
automatic renewal.

Use Docker for new interactive login. The integrated desktop requires a separate
unprivileged browser user and private miner data/log permissions; the image configures
these automatically. Source requirements are listed in the [login guide](authentication.md#dashboard-access-and-source-installations).
From the repository root:

```bash
uv venv env --python 3.12
source env/bin/activate
uv sync --active --locked --python 3.12
python main.py
```

Create `env/` only on the first setup. Activate it before later Python commands;
`--active` keeps dependency installation in that environment. Open
[http://localhost:8080](http://localhost:8080) after starting the miner. The source
process must stay running for mining and automatic renewal to continue.

If you plan to modify the application, use the development setup in
[CONTRIBUTING.md](../CONTRIBUTING.md), which includes the additional development tools.

## Persistent data and upgrades

Keep the same data directory when updating or recreating the miner. Docker uses
`/app/data` inside the container; the examples above mount it from `./data`. A source
installation uses `data/` in the repository.

The directory contains settings, Twitch credentials, local drop history, and optional
dashboard password state. Keep it private and back it up privately while the miner is
stopped. Run only one miner process per data directory.

Review the release notes before updating.

### Published image (`docker run`)

Use these Bash commands for a container created with `docker run`. Run them from the
same folder used at installation: `${PWD}/data` must resolve to the existing data
directory. If you are unsure, inspect the mounts before removing the old container:

```bash
docker inspect --format '{{json .Mounts}}' twitch-drops-miner
```

Find the mount whose `Destination` is `/app/data`. For a bind mount, reuse its
`Source` path; for a named volume, reuse its `Name` as
`-v VOLUME_NAME:/app/data`. Do not substitute a new empty directory or volume.
Keep your existing container name, timezone, ports, additional mounts (such as logs),
network options, and environment variables (such as `PUBLIC_BASE_URL`) when adapting
the example below. Files stored only inside the old container, including unmounted
logs, are removed with it; copy any you need before removal.

```bash
docker pull rangermix/twitch-drops-miner:latest &&
docker stop --timeout 30 twitch-drops-miner &&
docker rm twitch-drops-miner &&
docker run -d \
  --name twitch-drops-miner --init --stop-timeout 30 --shm-size 256m \
  -p 8080:8080 -e TZ=Australia/Sydney \
  -v "${PWD}/data:/app/data" \
  --restart unless-stopped \
  rangermix/twitch-drops-miner:latest
```

`latest` selects the current stable published image at pull time. To select a
specific release, replace `latest` in both commands with its version tag.
Pulling downloads the image; recreating the container applies it. Restarting alone
does not update the image, and `--restart unless-stopped` is not an automatic updater.
The `&&` separators stop the sequence if a command fails, so a failed pull leaves
the existing container running. Reusing the data mount preserves stored settings,
Twitch credentials, drop history, and dashboard password state.

### Updating with Docker Compose

For the [published-image Compose setup](#published-image), change the `image:` tag
in `compose.yaml` to the desired release first, or keep `latest` if that is what you
selected. From that Compose project's directory, run:

```bash
docker compose pull && docker compose up -d
```

Compose pulls the selected image and recreates the service when its image or
configuration changes, preserving its mounts. There is no need to remove the service
manually. A version tag such as `2.1.0` stays on that release until you change it.

For the repository's Compose file, run this from your original Git clone on the
branch you intend to update:

```bash
git pull --ff-only && docker compose up -d --build
```

This builds the updated checkout and recreates the service when needed, retaining
its configured mounts. A branch such as `main` may include unreleased changes.
If you downloaded a source archive or checked out a release tag, update to your
chosen source revision first and retain the same data/log paths before rebuilding.

Keep the same project, mounts, and environment configuration. Do not use
`docker compose down -v` during an update; it can delete Compose-managed data volumes.

### Source installation

Stop the miner, update the checkout, activate `env/`, run
`uv sync --active --locked --python 3.12`, and start it again.

Preserve `data/cookies.jar`. Working Android sessions are reused automatically.
Smart TV sessions from v1.3.1/v1.3.2, expired sessions, and signed-out installations
use the [dashboard browser](authentication.md#existing-installations). Do not delete all data to solve a login problem.

[All guides](README.md) · [Next: Twitch login](authentication.md)
