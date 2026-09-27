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

## Browser-assisted automatic renewal

The running CLI renewal loop captured and delivered three distinct new integrity
contexts without a second manual export or upload. The provider advanced from generation
1 to generations 2, 3 and 4. The persisted credential is a hash; the connection file stayed
private on the local computer. Each replacement passed WEB identity validation and both
Inventory and Campaigns before the destination committed it.

A separate read-only provider process inside the browser-free Docker instance used
generation 2 and again validated identity and returned 129 campaigns. Private in-memory
comparison confirmed a different integrity token and a later expiry than the manual
bundle. The helper's accepted expiry timestamps increased across all three replacements,
which also had to pass the provider's distinct-token and replay checks.

For this live observation the helper used `--renew-before 3550`, producing renewals
about 51 seconds apart. This is an accelerated scheduling test, not proof of a full
normal-hour cycle. Controlled-clock tests cover the default five-minute lead, transient
failures, short remaining validity, and revocation. After rebuilding and restarting the
destination with the final review fixes, the existing saved pairing and session restored.
Restarting the helper with the default 300-second lead delivered generation 5 without
new pairing or a manual upload. An independent provider query inside that rebuilt
container again validated identity and returned 129 campaigns. Its integrity token was
different from the manual export and its expiry was later. This verifies restart and
the immediate default-loop renewal, not its next full-length scheduled cycle. Cross-network portability, local browser sign-out
recovery, long unattended runs and imported mining progress remain unverified.

The prior conclusion that the user gate was satisfied was too broad. These tests proved
browser-assisted replacement while a local signed-in browser remained available. The
required deployment must renew on the TDM server after one export with the user's
computer/browser off. That requirement is not met by this implementation. The
[server-only renewal investigation](2026-09-25-server-only-renewal.md) records follow-up
failures and the corrected acceptance criteria. Code checkpoint approval did not prove
this missing system behavior.

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

## Automatic checkpoint validation

The final implementation passed Ruff, Mypy (66 source files), lock consistency, the
Node 24 full suite (438 tests and two subtests), whitespace checks and a local ARM64
Docker build. Current `origin/main` is contained in the branch. The release-script tests
passed at the manual checkpoint; their code and inputs did not change afterward.

Independent automatic-path review identified three additional races/boundaries: dashboard
authorization changing while pairing waited for the state lock, partial HTTP response
reads, and sleeping beyond a short remaining validity. Failing regressions reproduced
all three before fixes. Management rechecks authorization inside the lock, response
reading continues to EOF with a cumulative bound, and scheduling accounts for remaining
validity. The independent reviewer approved the corrected automatic checkpoint after
55 backend tests, one Node 24 UI test and the whitespace check passed. This approval
covers the checkpoint, not a release.

The actual dashboard rendered accepted expiry and the paired renewal status with the
connection download and disconnect controls. DOM tests exercised both actions, safe
credential handling, disabled states and visible failures. Connection creation in the
live experiment used the authenticated production API; the real browser download action
was not exercised. No version-release workflow or production deployment was performed.
