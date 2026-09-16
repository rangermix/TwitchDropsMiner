# Contributing to Twitch Drops Miner

Contributions are welcome: bug reports, feature requests, code, tests, documentation,
and translations. This guide defines the contribution workflow for people and coding
agents. Agents must read it before starting work and follow the same requirements.

## About the repository

Twitch Drops Miner automatically discovers eligible timed Twitch Drops, selects live
channels, and tracks and claims rewards without downloading stream video or audio.
It is an asynchronous Python application with a FastAPI and Socket.IO web dashboard,
Twitch OAuth login, persistent local data, and Docker support.

- Python 3.12 or newer; CI currently uses Python 3.12.
- Node.js 24 for JavaScript behavior tests invoked by pytest.
- `uv` for dependency management; dependencies are declared in `pyproject.toml` and
  locked in `uv.lock`.
- The default branch and pull request target are `main`.
- The project uses the [MIT license](./LICENSE). Preserve existing license notices
  and attribution, and contribute only material you have the right to share.

| Location | Purpose |
| --- | --- |
| `src/models/`, `src/services/`, `src/core/` | Domain models, business logic, and miner state machine |
| `src/api/`, `src/auth/`, `src/websocket/` | Twitch API, OAuth, and event connections |
| `src/web/`, `web/` | Dashboard backend, optional dashboard authentication, and frontend |
| `src/config/`, `src/utils/` | Configuration and shared utilities |
| `src/drop_history.py` | Claimed-drop history, filtering, statistics, and export |
| `src/i18n/`, `lang/` | Translation schema and locale files |
| `tests/` | Backend, integration, regression, and frontend behavior tests |
| `.github/workflows/`, `.github/scripts/` | Validation, releases, and contributor automation |

Read [README.md](./README.md) for setup and user behavior, and
[AGENTS.md](./AGENTS.md) for architecture and detailed implementation constraints.
The current scope excludes multiple accounts, channel-points mining, unlinked
campaign mining, and a desktop GUI. Discuss proposed scope changes before implementing
them; opening a feature request does not itself approve a change in scope.

## Raising an issue

Search [existing issues](https://github.com/rangermix/TwitchDropsMiner/issues) and
[pull requests](https://github.com/rangermix/TwitchDropsMiner/pulls), including closed
ones, before opening a new issue. Add relevant evidence to an existing report when it
describes the same problem. Use one issue per distinct problem and a descriptive title,
such as `Bug: queue keeps an expired reward` or `Feature: filter campaigns by game`.

### Bug reports

Include enough information for someone else to reproduce the problem:

- Application version or source commit, installation method, OS, and browser when
  relevant. For Docker, include the image tag and digest if available.
- Exact steps, expected behavior, actual behavior, and how often it occurs.
- Whether it also happens on the latest release, if you can safely check, and the last
  known working version if this is a regression.
- Relevant settings and campaign/drop/channel identifiers or public URLs. Include
  timestamps and timezone for campaign availability or progress problems.
- Minimal redacted logs, tracebacks, screenshots, or sample responses. Explain any
  troubleshooting already tried and its result.

For mining problems, check that the game account is linked, the reward is earned by
watching, the campaign is active, the channel participates, and your game selection or
ignore rules do not exclude it. Mention simultaneous manual Twitch viewing. Distinguish
what the miner displays from what Twitch's own inventory reports; a local display issue
and missing server-side progress may have different causes.

Never attach `cookies.jar`, `data/web_auth.json`, an entire data directory, OAuth/device
codes, session cookies, passwords, Telegram bot tokens, or unredacted settings/logs.
Verbose output and network captures can contain credentials; inspect them before sharing.

### Feature requests, questions, and other contributions

- **Feature request:** describe the user problem, a concrete use case, proposed
  behavior, alternatives, and observable acceptance criteria. Note compatibility,
  privacy, performance, or scope implications where relevant.
- **Question or support request:** state the goal, what you tried, and which documentation
  was unclear. Include environment details when they affect the answer.
- **Documentation or translation issue:** identify the file, page, language, or UI
  location, the current wording, and the suggested correction with context.

Use an available issue template if one is offered; otherwise use these headings in a
normal issue. Small, clear fixes can go directly to a PR. Discuss substantial features,
architectural changes, and significant refactoring with the maintainer first. Coding
agents must obtain the user's permission before significant refactoring, as required by
the agent instructions.

For suspected security vulnerabilities, use GitHub private vulnerability reporting if
it is available for this repository. Otherwise ask the maintainer for a private reporting
channel without posting exploit details or secrets in a public issue.

## Development setup

Work from the repository root. Use the `env/` virtual environment and activate it before
every Python command. Create it only if it does not already exist.

On Linux or macOS:

```bash
uv venv env --python 3.12  # first setup only
source env/bin/activate
uv sync --active --extra dev --locked --python 3.12
```

On Windows PowerShell:

```powershell
uv venv env --python 3.12  # first setup only
. .\env\Scripts\Activate.ps1
uv sync --active --extra dev --locked --python 3.12
```

`--active` selects the activated `env/` instead of creating a separate `.venv/`.
`--locked` checks that the committed lockfile is current without silently changing it.
`--python 3.12` keeps the interpreter aligned with CI during creation and synchronization.
For an intentional dependency change, update `pyproject.toml`, run `uv lock`, review
the lockfile diff, and sync again. Do not upgrade unrelated packages.

From the activated environment, run `python main.py` and open
<http://localhost:8080> for manual testing. `python main.py -vvv` enables verbose logs.
Running the application can contact Twitch and claim rewards; use your own intended
test account and data, and keep automated tests isolated from live services.

## Creating a pull request

### 1. Start from current main and keep the change focused

Fork the repository if you do not have write access, then create a topic branch from
the latest canonical `main`. Check your working tree first and preserve unrelated local
changes; never discard someone else's work to make a branch clean.

For a fork, configure the canonical repository as `upstream` once:

```bash
git remote add upstream https://github.com/rangermix/TwitchDropsMiner.git
git fetch upstream
git switch -c fix/short-description upstream/main
```

If `origin` already points to the canonical repository, use `origin` in place of
`upstream` and skip adding the remote. Use a descriptive topic branch; Codex-created
branches use `codex/` as their prefix. Do not work directly on `main`.

Before requesting final review, and again before merging if `main` has advanced,
fetch and integrate the latest canonical `main` into your topic branch:

```bash
git fetch upstream
git merge upstream/main
```

Rebasing is also acceptable for a branch you own. Do not rewrite a shared branch
without coordinating with its collaborators; if an authorized rebase needs a force
push, use `--force-with-lease`. Resolve conflicts deliberately, inspect the resulting
diff, and rerun affected checks after integration. Record the base and tested head
commit IDs in the PR so reviewers can identify what was validated.

Keep each PR about one coherent change. Avoid unrelated cleanup, formatting, dependency
upgrades, generated files, local data, and credentials. Link a related issue when one
exists; use closing keywords only when the PR fully resolves that issue.

### 2. Implement with the repository's constraints

- Follow DRY and the existing object-oriented backend architecture. Keep business
  logic in its owning model/service rather than duplicating it in API or UI handlers.
- Preserve asynchronous behavior; avoid blocking the event loop and handle cancellation,
  network failures, retries, and persistent state consistently with nearby code.
- Add unit tests for backend code changes. For frontend behavior changes, add automated
  coverage where practical; explain any gap and provide a reproducible manual check.
- Update `README.md` and the canonical `AGENTS.md` with the relevant behavior or
  workflow changes. Keep `CLAUDE.md` and `GEMINI.md` as relative symbolic links to
  `AGENTS.md`; do not replace them with duplicated text. Put agent-specific guidance
  in clearly named sections of `AGENTS.md`. Update this guide when the contribution
  workflow changes.
- For UI or console text changes, update the English source, every affected locale in
  `lang/`, and the TypedDict translation schema when keys change. Preserve key and
  placeholder parity. Build translated UI using `textContent` and safe DOM APIs;
  allowlist intentional links and create link nodes explicitly.
- Preserve account eligibility, campaign/drop timing, prerequisites, ignore rules,
  authentication boundaries, and credential redaction. Cache recovery must preserve
  credentials and settings. Consult the detailed contracts in the agent instructions.
- Changes to cached frontend assets (`app.js`, `styles.css`, or existing auth assets)
  require a version/cache-key bump through the release workflow before deployment.
  Flag that requirement in the PR; coordinate the release with the maintainer.

### 3. Test the change and protect against regressions

For a bug fix, first reproduce the failure and add a regression test that fails on the
old behavior and passes with the fix whenever practical. Test public behavior and
meaningful outcomes, not just internal implementation details. New functionality needs
both normal and failure-path coverage, including relevant boundary cases.

Use mocked Twitch/Telegram/network responses and temporary storage. Tests must not
need real credentials, claim real drops, or send real notifications. Existing tests
in `tests/` show the repository's conventions; frontend tests use Node.js through the
shared `tests/javascript_helpers.py` helper.

Run focused tests while developing, then run the code-change baseline from the
activated environment before declaring a code PR ready:

```bash
python -m ruff check src/
python -m mypy src/
python -m pytest tests/
uv lock --check
git diff --check
```

Mypy currently reports results without blocking CI. Report any diagnostics, distinguish
existing issues from regressions, and fix new problems introduced by the PR. A green
workflow does not prove that Mypy was clean. Node.js 24 must be on `PATH`; investigate
skipped frontend tests rather than claiming they passed.

Also run these release-script contract tests with Bash and GNU `grep`/`sed` on `PATH`.
The scripts use `grep -P` and GNU-style `sed -i`; macOS's default utilities are not
sufficient. On macOS, select GNU tools or use a Linux container/VM. On Windows, use
a compatible Git Bash or WSL environment with those tools available:

```bash
bash .github/scripts/test/test_validate_semver.sh
bash .github/scripts/test/test_github_output.sh
bash .github/scripts/test/test_extract_version.sh
```

Select additional regression checks from the actual impact of the change:

| Changed area | Evidence to include |
| --- | --- |
| Mining, inventory, or channel selection | Eligibility and timing boundaries, prerequisites, ignored drops, claim behavior, and neighboring selection paths |
| Settings or persistence | Save/reload round trips, defaults/migration, invalid input, and failure handling |
| Dashboard or translations | Relevant Node/DOM tests, locale schema and placeholder checks, and a browser check of the affected flow |
| Authentication or API access | Unauthorized requests, origin/CSRF handling, session expiry/revocation, and absence of leaked credentials |
| Dependencies, Docker, or CI/release scripts | Lock/version consistency, relevant script tests, and Docker build/workflow validation |
| Documentation only | Accurate commands and paths, working local links, Markdown structure, canonical agent guidance, and valid instruction symlinks |

For documentation-only changes, the last row replaces the local code/test baseline;
do not invent unit tests for prose. Normal PR CI still runs. For any unrun or unavailable
check, state exactly what is missing and why. Mocked tests do not establish live Twitch
progress; report live verification only if it was actually performed.

The [validation workflow](./.github/workflows/validation.yml) runs Ruff, advisory Mypy,
pytest, language JSON validation, lockfile checks, release-script tests, and Docker
builds for both supported image architectures. Required CI must pass on the final PR
revision before merge. After review fixes or main integration, rerun the checks affected
by those changes; do not rely on results from a superseded revision.

### 4. Obtain an independent adversarial review

Every PR needs an adversarial review before it is ready to merge, including documentation
changes. Use a human reviewer or a separate review agent that did not author the change;
an author's own reread is useful but does not satisfy independent review. For
agent-authored work, delegate this review to a separate review agent when available,
or request an independent human review. If neither is available, keep the PR in draft
and state that review is pending.

Give the reviewer the goal, relevant issue/acceptance criteria, final diff, repository
instructions, and actual test evidence. Ask them to challenge assumptions and look for
counterexamples: incorrect behavior, missed edge cases, regressions, security/privacy
problems, inadequate tests, scope creep, and misleading documentation. Scale the review
to the change; documentation review should check instructions and claims against the
repository rather than require unrelated runtime tests.

Record the reviewer identity or agent role, reviewed revision, findings with file/line
references where useful, and how each finding was handled. Fix actionable findings and
rerun affected tests; give a reason and evidence for any finding you reject. Unresolved
correctness or security findings block readiness. Have the reviewer recheck substantive
fixes and any later changes that affect their conclusions. An agent's review provides
review evidence; it does not replace maintainer approval or GitHub review requirements.

### 5. Submit clear evidence and follow through

Inspect `git status` and the diff, stage only the intended files with `git add`, and
commit with a concise message describing the change. Push the topic branch to your
fork or writable remote (normally `git push -u origin HEAD`). On GitHub, open a pull
request with `rangermix/TwitchDropsMiner` as the base repository, `main` as the base
branch, and your topic branch as the compare branch. You can open a draft early to
collect feedback and CI results while tests or independent review are still pending.

Use the [PR template](./.github/pull_request_template.md). Explain the original problem,
resulting behavior, scope, linked issues, test commands/results, regression coverage,
adversarial review, and remaining limitations. Include before/after screenshots for
visible UI changes when useful. Mark work in progress as a draft; do not describe a
partial implementation or unavailable check as complete.

Before requesting merge, confirm:

- [ ] The branch incorporates the latest canonical `main`; conflicts are resolved.
- [ ] The diff is focused and contains no secrets, local data, or unrelated changes.
- [ ] Backend unit tests and applicable frontend/regression checks are included.
- [ ] Required checks pass for the final revision; skips and limitations are recorded.
- [ ] README, agent instructions, translations, and release notes/requirements are
  updated where applicable under the rules above.
- [ ] An independent adversarial review is recorded and blocking findings are resolved.
- [ ] The PR description matches the final implementation and includes evidence.

Respond to review feedback respectfully and support technical disagreements with
reproducible evidence. Maintainers decide when to merge and release. Contributor credit
is updated automatically after merge; preserve the README contributor table and markers.
Release automation updates `src/version.py`, `pyproject.toml`, and `uv.lock` together.
Do not publish releases, change workflow trust boundaries, or bypass required checks as
part of an ordinary contribution.

## Additional requirements for coding agents

Read this guide and the applicable agent instructions before planning, editing, testing,
or reviewing. Pass these requirements to any delegated implementation or review agent.
The checklist is a completion requirement, not an optional suggestion.

Inspect the current checkout and preserve existing user changes. Stay within the
authorized task; a request to edit files does not by itself authorize publishing a PR,
merging, or releasing. Do not weaken tests, remove security checks, or alter this policy
merely to make your current task appear complete.

In the final handoff or PR, report what changed, actual validation results, adversarial
review outcome, and any unfinished work. If a required check or review cannot be
completed, state the gap and do not claim merge readiness. Never fabricate test runs,
reviewer approval, live behavior, or CI success.
