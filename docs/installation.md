# Installation

Run Twitch Drops Miner on your own computer or headless home server or NAS. Docker
is the simplest option: the image includes Chromium for automatic session renewal,
which runs without a desktop or display on the server. Run the separate login helper
on your desktop or laptop with Google Chrome installed. See
[which machine runs each part](authentication.md#headless-home-server-or-nas).

## Choose a version

Use the [release notes](https://github.com/rangermix/TwitchDropsMiner/releases) to
choose a TDM version. Keep TDM and its login helper on the same version.

Version 2.0 introduced helper login and Chromium for server renewal in the Docker
image. The example below pins patch version 2.0.1 so updates are deliberate.

The `latest` Docker tag is the latest published image; it may predate changes in these
source guides. Native helper archives are available only on releases that provide
them. To use an unreleased change, build TDM from the corresponding checkout with
Compose or run it from source, and use the helper from that same revision. The
[login guide](authentication.md#download-the-helper) covers matching build artifacts
when a release does not yet contain helper downloads.

## Docker

Run this from the directory where you want to keep TDM's data:

```bash
docker run -d \
  --name twitch-drops-miner --init --stop-timeout 30 --shm-size 256m \
  -p 8080:8080 \
  -v "${PWD}/data:/app/data" \
  --restart unless-stopped \
  rangermix/twitch-drops-miner:2.0.1
```

Open [http://localhost:8080](http://localhost:8080). From another computer on your home
network, use the miner host's LAN address, such as `http://192.168.1.10:8080`.

The data mount keeps settings and login credentials outside the container. The init
process and shutdown timeout let TDM close its temporary renewal browser when stopped.
Only the dashboard port is needed; do not publish a browser or browser-control port.

To retain file logs outside the container, also mount `./logs:/app/logs`. You can read
the container's console output with:

```bash
docker logs --tail 100 twitch-drops-miner
```

## Docker Compose

Clone or download the source revision you intend to run. From its repository root,
build and start the included [docker-compose.yml](../docker-compose.yml):

```bash
docker compose up -d --build
```

The supplied configuration stores data in `./data` and logs in `./logs`, exposes port
8080, and includes the init process and shutdown grace period. It builds the current
checkout instead of pulling a published image. Adjust its timezone for your home
server if needed.

Open the dashboard and follow [Twitch login](authentication.md). To protect access
or use your own reverse proxy, follow [Dashboard access](dashboard-access.md).

## Run from source

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). The miner host also
needs a Chromium executable named `chromium` or `chromium-browser` on its `PATH` for
automatic renewal. Google Chrome on the helper desktop does not satisfy that server
requirement when the desktop and miner are different machines.

Run the source miner on Linux or macOS. On Windows, use Docker Desktop with Linux
containers for the miner; the desktop login helper runs natively on Windows.
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

For a published Docker image, pull the version you want and recreate the container
with the same data mount. For Compose, update the checkout and run
`docker compose up -d --build`. For source, stop the miner, update the checkout,
activate `env/`, run `uv sync --active --locked --python 3.12`, and start it again.
Review the release notes before updating.

Preserve `data/cookies.jar`. Working Android sessions are reused automatically.
Smart TV sessions from v1.3.1/v1.3.2, expired sessions, and signed-out installations
need the [login helper](authentication.md#existing-installations) after updating to
the helper-based flow. Do not delete all data to solve a login problem.

[All guides](README.md) · [Next: Twitch login](authentication.md)
