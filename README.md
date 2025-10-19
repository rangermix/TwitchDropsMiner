# Twitch Drops Miner

> **Note:** This is a fork of [DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner). See [Acknowledgments](#acknowledgments) for credits to the original author and contributors.

> **Disclaimer:** This fork is heavily maintained and developed using AI-assisted coding (Claude Code). While functional, the codebase may reflect "vibe coding" patterns and AI-generated conventions. Use at your own discretion and always review changes before deploying.

This application allows you to AFK mine timed Twitch drops, without having to worry about switching channels when the one you were watching goes offline, claiming the drops, or even receiving the stream data itself. This helps you save on bandwidth and hassle.

### Features:

- Stream-less drop mining - save on bandwidth.
- Game priority and exclusion lists, allowing you to focus on mining what you want, in the order you want, and ignore what you don't want.
- Sharded websocket connection, allowing for tracking up to `199` channels at the same time.
- Automatic drop campaigns discovery based on linked accounts (requires you to do [account linking](https://www.twitch.tv/drops/campaigns) yourself though).
- Stream tags and drop campaign validation, to ensure you won't end up mining a stream that can't earn you the drop.
- Automatic channel stream switching, when the one you were currently watching goes offline, as well as when a channel streaming a higher priority game goes online.
- Login session is saved in a cookies file, so you don't need to login every time.
- Mining is automatically started as new campaigns appear, and stopped when the last available drops have been mined.

### Usage:

The application is designed for Docker deployment, making it easy to run on any platform:

**Using pre-built images (Recommended):**

```bash
# Pull and run from Docker Hub
docker pull rangermix/twitch-drops-miner:latest
docker run -d -p 8080:8080 -v $(pwd)/data:/app/data rangermix/twitch-drops-miner:latest
```

Or use docker-compose with the pre-built image, Create a docker-compose.yml file with:

```yaml
services:
  twitch-drops-miner:
    image: rangermix/twitch-drops-miner:latest
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

**Building locally:**

```bash
# Build and run with docker-compose
docker-compose up -d

# Or build and run manually
docker build -t twitch-drops-miner .
docker run -d -p 8080:8080 -v $(pwd)/data:/app/data twitch-drops-miner
```

**Running from Source:**

```bash
# Install Python 3.10+ and dependencies
pip install -e .

# Run the application
python main.py

# Access the web interface at http://localhost:8080
```

**Using the Application:**

- Open the web interface in your browser at `http://localhost:8080`
- Login/connect the miner to your Twitch account using the OAuth device code flow
- After a successful login, the app will fetch all available campaigns and games you can mine drops for
- Select and add games to the Watching List on the Settings tab, then press `Reload` to start processing
- The miner will fetch applicable streams and start mining automatically
- You can manually switch to a different channel as needed
- Make sure to link your Twitch account to game accounts on the [campaigns page](https://www.twitch.tv/drops/campaigns)

**Important Docker notes:**
- All persistent data (cookies, settings, logs) is stored in the `data/` directory
- Login uses OAuth device code flow - you'll be given a code to enter at twitch.tv/activate
- Browser notifications supported (requires permission)
- Health checks included
- Configure timezone with `TZ` environment variable
- Pre-built images are automatically published to Docker Hub via GitHub Actions
- Available tags:
  - `latest` - Latest stable release
  - `1.0.0`, `1.0`, `1` - Semantic versioning tags (major.minor.patch)
  - `1.0.0-rc.1` - Pre-release versions (tagged with exact version only)
- Multi-platform support: linux/amd64, linux/arm64


### Screenshots:

The application features a modern web-based interface accessible from any browser on your network.

### Notes:

> [!WARNING]  
> Due to how Twitch handles the drop progression on their side, watching a stream in the browser (or by any other means) on the same account that is actively being used by the miner, will usually cause the miner to misbehave, reporting false progress and getting stuck mining the current drop.  
> 
> Using the same account to watch other streams during mining is thus discouraged, in order to avoid any problems arising from it.

> [!NOTE]  
> The source code requires Python 3.10 or higher to run.

### Running from Source:

For development or customization:

```bash
# Install Python 3.10+
# Create virtual environment (recommended)
python -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate

# Install dependencies
pip install -e .

# Run the application
python main.py
```

### Support This Project

If you find this project useful, please consider supporting my work:

<div align="center">

[![Buy me a coffee](https://i.imgur.com/cL95gzE.png)](
    https://buymeacoffee.com/rangermix
)

</div>

You can also support the original author [@DevilXD](https://github.com/DevilXD) at [buymeacoffee.com/DevilXD](https://www.buymeacoffee.com/DevilXD) or [Patreon](https://www.patreon.com/bePatron?u=26937862).

### Project goals:

Twitch Drops Miner (TDM for short) has been designed with a couple of simple goals in mind:

**What TDM is:**
- **Twitch Drops focused** - Automatic mining of timed Twitch drops
- **Easy to use** - Simple web interface accessible from any browser
- **Reliable** - Designed to run continuously with minimal attention needed
- **Efficient** - Minimal interactions with Twitch, respecting their service
- **Docker-ready** - Easy deployment on any platform or server

This is a web-only application designed for Docker deployment and headless operation.

### Acknowledgments:

This project is a fork of the excellent [TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner) created by [@DevilXD](https://github.com/DevilXD). Huge thanks to DevilXD for creating and maintaining this amazing tool, and to all the contributors who have helped improve it over time.

**Original Project:** [DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner)
**Original Author:** [@DevilXD](https://github.com/DevilXD)

### Original Project Credits:

<!---
Note: The translations credits are sorted alphabetically, based on their English language name.
When adding a new entry, please ensure to insert it in the correct place in the second section.
Non-translations related credits should be added to the first section instead.

Note: When adding a new credits line below, please add two trailing spaces at the end
of the previous line, if they aren't already there. Doing so ensures proper markdown
rendering on Github. In short: Each credits line should end with two trailing spaces,
placed past the period character at the end.

• Last line can have the two trailing spaces omitted.
• Please ensure your editor won't trim the trailing spaces upon saving the file.
• Please ensure to leave a single empty new line at the end of the file.
-->

@guihkx - For the CI script, CI maintenance, and everything related to Linux builds.  
@kWAYTV - For the implementation of the dark mode theme.  

@Bamboozul - For the entirety of the Arabic (العربية) translation.  
@Suz1e - For the entirety of the Chinese (简体中文) translation and revisions.  
@wwj010 - For the Chinese (简体中文) translation corrections and revisions.  
@zhangminghao1989 - For the Chinese (简体中文) translation corrections and revisions.  
@Ricky103403 - For the entirety of the Traditional Chinese (繁體中文) translation.  
@LusTerCsI - For the Traditional Chinese (繁體中文) translation corrections and revisions.  
@nwvh - For the entirety of the Czech (Čeština) translation.  
@Kjerne - For the entirety of the Danish (Dansk) translation.  
@roobini-gamer - For the entirety of the French (Français) translation.  
@Calvineries - For the French (Français) translation revisions.  
@ThisIsCyreX - For the entirety of the German (Deutsch) translation.  
@Eriza-Z - For the entirety of the Indonesian translation.  
@casungo - For the entirety of the Italian (Italiano) translation.  
@ShimadaNanaki - For the entirety of the Japanese (日本語) translation.  
@Patriot99 - For the Polish (Polski) translation and revisions (co-authored with @DevilXD).  
@zarigata - For the entirety of the Portuguese (Português) translation.  
@Sergo1217 - For the entirety of the Russian (Русский) translation.  
@kilroy98 - For the Russian (Русский) translation corrections and revisions.  
@Shofuu - For the entirety of the Spanish (Español) translation and revisions.  
@alikdb - For the entirety of the Turkish (Türkçe) translation.  
@Nollasko - For the entirety of the Ukrainian (Українська) translation and revisions.  
@kilroy98 - For the Ukrainian (Українська) translation corrections and revisions.  
