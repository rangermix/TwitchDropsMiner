# Installation

Run TDM on your own computer or home server with Docker. The current source build
includes the dashboard’s Twitch login browser and automatic session renewal.
No desktop or display is needed on the server. Until a release includes this flow,
build this checkout with Compose or the Dockerfile below; older published images
still use their release’s authentication instructions.

## Docker

From the source repository root:

```bash
docker build -t twitch-drops-miner .
docker run -d \
  --name twitch-drops-miner --init --stop-timeout 30 --shm-size 256m \
  -p 8080:8080 -e TZ=Australia/Sydney \
  -v "${PWD}/data:/app/data" \
  --restart unless-stopped \
  twitch-drops-miner
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
checkout instead of pulling a published image. Set `TZ` to match the timezone of your home internet connection. A mismatch can
cause Twitch to reject browser login.

Open the dashboard and follow [Twitch login](authentication.md). To protect access
or use your own reverse proxy, follow [Dashboard access](dashboard-access.md).

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

For a published Docker image, pull the version you want and recreate the container
with the same data mount. For Compose, update the checkout and run
`docker compose up -d --build`. For source, stop the miner, update the checkout,
activate `env/`, run `uv sync --active --locked --python 3.12`, and start it again.
Review the release notes before updating.

Preserve `data/cookies.jar`. Working Android sessions are reused automatically.
Smart TV sessions from v1.3.1/v1.3.2, expired sessions, and signed-out installations
use the [dashboard browser](authentication.md#existing-installations). Do not delete all data to solve a login problem.

[All guides](README.md) · [Next: Twitch login](authentication.md)
