# Native helper release CI implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish the four tested native helper archives with normal versioned GitHub releases, update current setup documentation, and merge the reviewed branch into main.

**Architecture:** Reuse the read-only native build workflow from the GitHub release workflow. Resolve and verify the release tag against the dispatched source commit before building; collect exactly four archives from that run, validate their contents, give them versioned names, and generate SHA-256 checksums before the publishing job can run. PR builds exercise the same packaging path without release permission.

**Tech Stack:** GitHub Actions reusable workflows, PyInstaller, Python standard-library tar/CLI/checksum utilities, pytest and existing shell release tests.

## 1. Reproduce the missing release path

- Add `tests/test_helper_release.py`: exercise real archive preparation, missing/extra/unsafe artifacts, permissions and checksums; check the release workflow's dependencies and exact-tag guard against temporary Git repositories.
- Run focused tests first and confirm that archive preparation and release integration fail because they do not exist yet.

## 2. Connect existing builds to releases

- Add `.github/scripts/prepare_helper_release.py`, keeping archive validation and naming in one class and never extracting downloaded archives.
- Add `workflow_call` with an exact source ref to `.github/workflows/login-helper.yml`; preserve the four existing platform builds and actual-Chrome smoke checks. Collect and validate artifacts in a final read-only packaging job, also used by PRs.
- Update `.github/workflows/github-release.yml`: validate branch/package/lock versions and matching tag, call the native workflow, download the prepared artifact from the same run, verify checksums, and attach all four archives plus `SHA256SUMS` to the release. Only the publishing job receives write permission.
- Add release-note download instructions without rewriting historical release notes or changing a published tag.

## 3. Align current documentation and deployment files

- Update `README.md`, `AGENTS.md`, `CONTRIBUTING.md` and `docs/server-renewal.md` for release assets, platform selection, extraction and the three-step helper login.
- Remove retired Docker browser/standalone-renewal recipes from the deployable surface. Retain the timestamped investigation records and shared session library/tests.
- Keep the unreleased-to-users boundary explicit until a versioned release containing this flow is published; a merge alone does not publish a release or bump the frontend cache key.

## 4. Validate and merge

- Run focused red/green tests, final Ruff/Mypy/pytest/lock checks with Node 24, GNU/Linux release-script contracts, workflow syntax/action validation and local documentation checks.
- Obtain independent adversarial review of release trust, artifact completeness, current auth behavior and documentation. Fix and recheck findings.
- Commit and push verified checkpoints; open a PR using the repository template, attach it to this chat, and wait for final-revision validation and all native platforms to pass.
- Confirm current main is incorporated, merge without bypassing checks, and verify the resulting main revision. Do not dispatch a new version release or invent its version as part of this merge request.

## Merge review follow-up

The independent application review reproduced a transient imported-session HTTP failure
escaping the existing GQL retry path. The focused fix adds bounded, cancellable retries
only for exact persisted read operations, retains successful partial-batch results and
stops stale work after account replacement. Production-dispatch regressions cover HTTP
503, timeout, exhaustion, mutations/raw/unknown requests and cancellation. The fix must
pass independent re-review and the final baseline before merge.

The review also found the retired Docker login overlay still advertised an invocation
whose environment variables TDM ignores. The overlay and its browser image recipe, plus
the retired standalone renewal Dockerfile, are removed; historical evidence is retained.

The release review found that the upload action could publish prereleases before asset
upload completed and replace existing public files on reruns. Publication now uses
`.github/scripts/publish_helper_release.py`: refuse public targets before writes, create
or resume a draft, upload the exact five local assets, verify the complete remote set
and digests, then publish. Tests simulate stable/prerelease upload failures, draft
recovery, corrupt or missing remote assets, and a public rerun with no writes.

Final CI log inspection exposed six advisory Mypy errors with aiohttp 3.14.3 and Mypy
2.3.1, despite clean locked-environment checks and a green job. Reproduced all six in
an isolated dependency environment. The DevTools transport annotation now uses only
its required structural interface, keeping runtime behavior and locked dependencies
unchanged. Verify both typing environments and re-review before the final CI/merge.
