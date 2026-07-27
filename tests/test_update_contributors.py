from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "update_contributors.py"
)
SPEC = importlib.util.spec_from_file_location("update_contributors", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ContributorReadmeUpdater = MODULE.ContributorReadmeUpdater
PullRequestContribution = MODULE.PullRequestContribution


def contribution(
    *,
    login: str = "octocat",
    pull_request_number: int = 42,
) -> PullRequestContribution:
    return PullRequestContribution(
        login=login,
        profile_url=f"https://github.com/{login}",
        pull_request_number=pull_request_number,
        pull_request_url=(
            "https://github.com/rangermix/TwitchDropsMiner/"
            f"pull/{pull_request_number}"
        ),
    )


def managed_readme(*entries: str, newline: str = "\n") -> str:
    lines = [
        "# Project",
        "",
        ContributorReadmeUpdater.START_MARKER,
        *entries,
        ContributorReadmeUpdater.END_MARKER,
        "",
    ]
    return newline.join(lines)


def test_adds_first_contributor_and_preserves_newlines() -> None:
    original = managed_readme(newline="\r\n")

    updated = ContributorReadmeUpdater().update_text(original, contribution())

    assert (
        "- [@octocat](https://github.com/octocat) — "
        "[#42](https://github.com/rangermix/TwitchDropsMiner/pull/42)"
    ) in updated
    assert "\r\n" in updated
    assert "\n" not in updated.replace("\r\n", "")


def test_appends_another_pr_to_existing_contributor() -> None:
    original = managed_readme(
        "- [@octocat](https://github.com/octocat) — "
        "[#41](https://github.com/rangermix/TwitchDropsMiner/pull/41)"
    )

    updated = ContributorReadmeUpdater().update_text(original, contribution())

    assert updated.count("- [@octocat]") == 1
    assert (
        "[#41](https://github.com/rangermix/TwitchDropsMiner/pull/41), "
        "[#42](https://github.com/rangermix/TwitchDropsMiner/pull/42)"
    ) in updated


def test_repeated_pr_event_is_idempotent() -> None:
    updater = ContributorReadmeUpdater()
    updated = updater.update_text(managed_readme(), contribution())

    assert updater.update_text(updated, contribution()) == updated


def test_adds_different_contributors_on_separate_lines() -> None:
    updater = ContributorReadmeUpdater()
    updated = updater.update_text(managed_readme(), contribution())
    updated = updater.update_text(
        updated,
        contribution(login="hubot", pull_request_number=43),
    )

    assert updated.count("\n- [@") == 2
    assert "- [@octocat]" in updated
    assert "- [@hubot]" in updated


@pytest.mark.parametrize(
    "readme",
    [
        "# No markers\n",
        f"{ContributorReadmeUpdater.END_MARKER}\n{ContributorReadmeUpdater.START_MARKER}\n",
        (
            f"{ContributorReadmeUpdater.START_MARKER}\n"
            f"{ContributorReadmeUpdater.START_MARKER}\n"
            f"{ContributorReadmeUpdater.END_MARKER}\n"
        ),
    ],
)
def test_rejects_missing_duplicate_or_reversed_markers(readme: str) -> None:
    with pytest.raises(ValueError):
        ContributorReadmeUpdater().update_text(readme, contribution())


@pytest.mark.parametrize(
    ("login", "profile_url", "pr_number", "pr_url"),
    [
        (
            "octocat](unsafe",
            "https://github.com/octocat",
            42,
            "https://github.com/rangermix/TwitchDropsMiner/pull/42",
        ),
        (
            "octocat",
            "http://github.com/octocat",
            42,
            "https://github.com/rangermix/TwitchDropsMiner/pull/42",
        ),
        (
            "octocat",
            "https://github.com/octocat",
            0,
            "https://github.com/rangermix/TwitchDropsMiner/pull/0",
        ),
        (
            "octocat",
            "https://github.com/octocat",
            42,
            "https://github.com/example/repo/pull/42)",
        ),
    ],
)
def test_rejects_unsafe_or_invalid_metadata(
    login: str,
    profile_url: str,
    pr_number: int,
    pr_url: str,
) -> None:
    with pytest.raises(ValueError):
        PullRequestContribution(
            login=login,
            profile_url=profile_url,
            pull_request_number=pr_number,
            pull_request_url=pr_url,
        )
