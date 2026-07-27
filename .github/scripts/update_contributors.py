"""Add merged pull request contributors to the managed README section."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class PullRequestContribution:
    """Validated metadata for one merged pull request contribution."""

    login: str
    profile_url: str
    pull_request_number: int
    pull_request_url: str

    _LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

    def __post_init__(self) -> None:
        if not self._LOGIN_PATTERN.fullmatch(self.login):
            raise ValueError(f"Invalid GitHub login: {self.login!r}")
        if self.pull_request_number < 1:
            raise ValueError("Pull request number must be positive")
        self._validate_url(self.profile_url, "profile")
        self._validate_url(self.pull_request_url, "pull request")

    @staticmethod
    def _validate_url(value: str, label: str) -> None:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.path
            or any(character in value for character in ("\n", "\r", ")"))
        ):
            raise ValueError(f"Invalid {label} URL: {value!r}")

    @property
    def pull_request_link(self) -> str:
        return f"[#{self.pull_request_number}]({self.pull_request_url})"

    @property
    def readme_entry(self) -> str:
        return f"- [@{self.login}]({self.profile_url}) — {self.pull_request_link}"


class ContributorReadmeUpdater:
    """Update contributor entries between explicit README markers."""

    START_MARKER = "<!-- contributors:start -->"
    END_MARKER = "<!-- contributors:end -->"
    ENTRY_PATTERN = re.compile(
        r"^- \[@(?P<login>[A-Za-z0-9-]+)\]\(https://[^)]+\) — .+$"
    )

    def update_file(self, readme_path: Path, contribution: PullRequestContribution) -> bool:
        original = readme_path.read_text(encoding="utf-8")
        updated = self.update_text(original, contribution)
        if updated == original:
            return False

        with readme_path.open("w", encoding="utf-8", newline="") as readme:
            readme.write(updated)
        return True

    def update_text(self, text: str, contribution: PullRequestContribution) -> str:
        lines = text.splitlines()
        start_index, end_index = self._marker_indexes(lines)
        managed_lines = lines[start_index + 1 : end_index]

        if any(contribution.pull_request_link in line for line in managed_lines):
            return text

        for relative_index, line in enumerate(managed_lines):
            match = self.ENTRY_PATTERN.fullmatch(line)
            if match and match.group("login").casefold() == contribution.login.casefold():
                absolute_index = start_index + 1 + relative_index
                lines[absolute_index] = f"{line}, {contribution.pull_request_link}"
                return self._join_lines(lines, text)

        lines.insert(end_index, contribution.readme_entry)
        return self._join_lines(lines, text)

    def _marker_indexes(self, lines: list[str]) -> tuple[int, int]:
        if lines.count(self.START_MARKER) != 1 or lines.count(self.END_MARKER) != 1:
            raise ValueError("README must contain exactly one contributor marker pair")

        start_index = lines.index(self.START_MARKER)
        end_index = lines.index(self.END_MARKER)
        if start_index >= end_index:
            raise ValueError("README contributor markers are out of order")
        return start_index, end_index

    @staticmethod
    def _join_lines(lines: list[str], original: str) -> str:
        newline = "\r\n" if "\r\n" in original else "\n"
        updated = newline.join(lines)
        if original.endswith(("\n", "\r")):
            updated += newline
        return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add a merged pull request contributor to README.md"
    )
    parser.add_argument("--readme", type=Path, required=True)
    parser.add_argument("--login", required=True)
    parser.add_argument("--profile-url", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--pr-url", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    contribution = PullRequestContribution(
        login=args.login,
        profile_url=args.profile_url,
        pull_request_number=args.pr_number,
        pull_request_url=args.pr_url,
    )
    changed = ContributorReadmeUpdater().update_file(args.readme, contribution)
    print("Contributor credit added." if changed else "Contributor credit already present.")


if __name__ == "__main__":
    main()
