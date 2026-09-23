# Explicit dashboard public URL

## Goal and design

Resolve issue #106 for HTTPS reverse proxies without requiring forwarded-header
trust. Add the optional startup environment variable `PUBLIC_BASE_URL`, representing
one public HTTP(S) origin. A shared `DashboardOrigin` policy supplies the expected
browser origin and cookie Secure flag to the existing HTTP/Socket.IO guards and
authentication responses. Read the variable once when constructing the application.

Accept a root URL with an optional port/trailing slash; normalize scheme, hostname,
and default ports. Reject credentials, paths, query strings, fragments, malformed
authorities, and lists/wildcards at startup without echoing the submitted value.
An absent or empty variable preserves the current request-derived behavior.

The configured origin is authoritative even if a proxy rewrites Host or forwards
HTTP internally. Keep CSRF markers, Fetch Metadata checks, session enforcement,
revocation, and rate limits. Do not change ASGI scheme/client or trust any additional
forwarded headers. Client-IP forwarding remains a separate Uvicorn configuration.
This does not add subpath hosting, TLS termination, or multiple public origins.

## Implementation and verification sequence

1. Add isolated regression tests in `tests/test_web_public_url.py` using production
   auth/API/Socket.IO middleware and Uvicorn's proxy middleware, with temporary data.
   Prove the HTTPS-public/HTTP-backend handshake failure before implementing the fix.
2. Add `src/web/origin.py` and integrate it into `src/web/auth.py` and
   `src/web/app.py`. Test valid/invalid configuration, polling and WebSocket
   transports, secure cookie creation/deletion, unchanged defaults, hostile origins,
   missing markers, session revocation, and unchanged proxy-IP trust.
3. Update README, AGENTS, Compose example, and v1.3.2 release notes. Run the full
   contribution baseline, release-script contracts, Compose validation, and a
   browser smoke test through a local reverse proxy with isolated mocked miner state.
4. Commit and push verified work, create a draft PR, attach it to the task, and
   request independent adversarial review. Resolve findings, integrate any new main
   commits, record tested/reviewed SHAs, and wait for final CI before merge.
5. Dispatch the patch release workflow, verify GitHub release and multiarchitecture
   Docker publication, then update issue #106 with the diagnosis and configuration.
   The issue was already closed by its reporter; retain completed status.

## Proof boundary

Tests and browser checks use local temporary state and simulated Twitch login data.
They establish dashboard connectivity/authentication behavior, not live Twitch mining
or deployment on the reporter's server. The public URL does not restore client IPs;
without separately trusted IP forwarding, clients share the proxy's per-IP login limit.
