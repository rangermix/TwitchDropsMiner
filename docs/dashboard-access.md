# Dashboard access

The dashboard is available on port 8080 and has no password by default. Optional
password protection is separate from Twitch login and does not interrupt mining.
Use it before making the dashboard accessible beyond your trusted home network.

## Enable a password

In **Settings → Dashboard password**, enter and confirm a password of 8–1024
characters, then select **Enable password protection**. No username is needed.
Enabling protection immediately locks out other browsers; mining continues in the
background.

The password protects the dashboard, its ordinary API, and live updates. The login
page, authentication status, and `/healthz` remain available without a password.
The login helper has its own **Allow helper connection** setting; dashboard password
protection does not close helper access. See [Twitch login](authentication.md#allow-helper-connection-and-account-replacement).

## Sessions and password changes

- A normal login uses a browser session cookie. **Remember me for 30 days** keeps a
  persistent cookie with a fixed 30-day expiry. All sessions have a maximum server
  lifetime of 30 days and survive miner restarts.
- Browser session-restore features may preserve session cookies. On a shared device,
  select **Log out** to revoke the session explicitly.
- **Change password** requires the current password and signs out other sessions.
  The browser making the change receives a new session.
- **Disable protection and clear password** requires the current password and removes
  the password and sessions. The dashboard becomes accessible without a password again.

Login attempts are limited to five per minute per client IP and 30 per minute overall.
After too many attempts, wait before retrying. A temporary request error on the login
page can be retried with **Log in** without reloading the page.

## Reverse proxy and HTTPS

For remote access to a miner on your own home hardware, use HTTPS through your reverse
proxy to protect passwords, cookies, and helper uploads. Configure the password on a
trusted network before exposing the dashboard.

Set the miner's `PUBLIC_BASE_URL` environment variable to the exact root address you
open in the browser, for example:

```text
PUBLIC_BASE_URL=https://drops.example.com
```

The included [Compose file](../docker-compose.yml) has a commented example. Set the
value under `environment`, replace the example hostname, and recreate the container:

```bash
docker compose up -d --build
```

The setting accepts one absolute `http://` or `https://` root URL, with an optional
port and trailing slash. It does not accept credentials, subpaths, query strings,
fragments, wildcard hosts, or multiple URLs. Use a normal hostname, dotted-decimal
IPv4 address, or bracketed IPv6 address.

Continue opening the dashboard at that configured address. It controls which browser
origin can write settings and connect for live updates. When it uses HTTPS, dashboard
cookies are marked Secure even if the proxy connects to TDM over HTTP or rewrites the
Host header. This setting does not supply TLS or enable hosting under a URL subpath.

### Forwarded headers and client addresses

Leaving `PUBLIC_BASE_URL` unset or empty uses the address and scheme seen in each
request. In that configuration, preserve the original Host header and configure
Uvicorn to trust forwarded protocol and client-IP headers only from your proxy.
Set `FORWARDED_ALLOW_IPS` to its exact IP or a dedicated proxy subnet; do not use `*`
as a default.

`PUBLIC_BASE_URL` does not trust forwarded headers or restore client IPs. Configure
trusted forwarding separately if you need individual client addresses. Otherwise,
clients hidden behind one proxy share its per-IP login limit.

The browser sends the required `X-TDM-Request: 1` header on API writes automatically.
Custom clients must include it too. Socket.IO connections do not require that header,
but still require an allowed origin. Do not disable these checks to work around an
incorrect proxy address.

## Recover a forgotten password

Recovery requires access to the miner host:

1. Stop the miner and restrict network access to its dashboard port.
2. Delete only `data/web_auth.json` from its persistent data directory. In Docker,
   this is `/app/data/web_auth.json` inside the container, backed by your data mount.
3. Restart the miner and set a new dashboard password in Settings.
4. Restore remote access after protection is enabled.

This resets dashboard protection without deleting Twitch cookies or other settings.
Keep the data directory private and use only one miner process per data directory.
A malformed authentication file stops startup instead of silently disabling protection.
**Clear All Cache** preserves the dashboard password and sessions.

[All guides](README.md) · [Troubleshooting](troubleshooting.md)
