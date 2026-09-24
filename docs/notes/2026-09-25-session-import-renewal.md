# Manual session import and renewal evidence

Tracking: [#118](https://github.com/rangermix/TwitchDropsMiner/issues/118).
Continuation of the [24 September portability experiments](2026-09-24-browser-session-portability.md).

## Manual path

On 25 September 2026 (Australia/Sydney), the new application helper exported a private
JSON file from the existing dedicated native Chrome profile. It correlated an integrity
issuance response with the exact header used by a successful authenticated campaign
request. The export file was selected and submitted through the actual TDM dashboard
upload UI in an isolated Docker source build with `TDM_SESSION_IMPORT=1`.

The container had a fresh data directory and no browser. Dashboard password protection
was enabled. The UI reported a verified session and logged-in status. TDM's full startup
then fetched inventory and campaign data; with no games selected it remained idle.

A subsequent read-only check instantiated the application imported-session provider from
the saved private file inside that container, revalidated identity and both catalog
operations, and issued Inventory/Campaigns again. It returned 129 campaigns without
campaign errors. A private in-memory comparison confirmed that the saved bundle matched
the manually exported bundle exactly. Only boolean results, counts, generation and expiry
metadata are retained in the report; no credentials or account identifiers are published.

This proves the manual application's export/upload/validation/request path on the same
home network. It does not prove another network or imported-session mining progress.
The UI was inspected in the browser; DOM tests cover authentication gating, visible
failures, upload size bounds, write headers, text safety and clearing the selected file.

## Remaining exit gate

The user requires automatic renewal in addition to manual login and integrity success.
Automatic renewal is not yet implemented or verified at this checkpoint. Completion
requires a running automatic helper loop to obtain and deliver a distinct new integrity
token and the destination to pass authenticated campaign access with that token. Mocked
tests, restarting with the same token, or a second manual upload do not meet that gate.

## Manual checkpoint validation

Ruff and Mypy passed (65 source files); the Node 24 full suite passed 423 tests and two
subtests. The lock check, all three Linux release-script contract suites, whitespace
check, and local ARM64 Docker build passed. Current `origin/main` was already contained
in the branch. No release or production deployment was performed.

An independent adversarial review found shutdown/expiry races, reuse of rejected
contexts, late expected-account binding, deep JSON parse errors, and a stale proxy
setting. Regression tests cover the fixes, including preserving successful mutation
results while retrying only definitively rejected batch entries. The reviewer rechecked all eight findings (including conservative mutation replay
classification and malformed response rows) and approved the manual checkpoint after
40 independent focused tests passed. A rebuilt container also restored the saved
manual session and showed verified/logged-in status without another upload.
