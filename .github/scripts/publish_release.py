"""Publish a source release from a verified tag without desktop helper assets."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path


class ReleasePublisher:
    def __init__(self, repository: str, version: str, prerelease: bool, notes: Path):
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
            raise ValueError("Invalid release version")
        self.repository, self.version, self.prerelease, self.notes = repository, version, prerelease, notes
        self.tag = f"v{version}"
        self.endpoint = f"repos/{repository}/releases"

    @staticmethod
    def verify_versions(root: Path, version: str) -> None:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        package = [row for row in lock["package"] if row["name"] == project["name"].lower().replace("_", "-")]
        source = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "src/version.py").read_text())
        if source is None or source[1] != version or project["version"] != version or len(package) != 1 or package[0]["version"] != version:
            raise ValueError("Branch, package, lock and source versions must match")

    @staticmethod
    def _gh(*args: str, payload: dict | None = None) -> str:
        return subprocess.run(["gh", *args], input=json.dumps(payload) if payload is not None else None,
                              check=True, capture_output=True, text=True).stdout

    def _api(self, endpoint: str, payload: dict | None = None, *, method: str = "GET"):
        args = ["api", endpoint, "--method", method]
        if payload is not None:
            args.extend(["--input", "-"])
        return json.loads(self._gh(*args, payload=payload))

    def require_draft(self, release: dict) -> None:
        if release.get("draft") is not True or release.get("tag_name") != self.tag or release.get("assets"):
            raise ValueError("Refusing a published, mismatched or unexpected asset release")

    def publish(self) -> None:
        pages = json.loads(self._gh("api", self.endpoint + "?per_page=100", "--paginate", "--slurp"))
        matches = [row for page in pages for row in page if row.get("tag_name") == self.tag]
        if len(matches) > 1:
            raise ValueError("Multiple releases have the requested tag")
        draft = {"tag_name": self.tag, "name": f"Release {self.version}",
                 "body": self.notes.read_text(encoding="utf-8"), "draft": True, "prerelease": self.prerelease}
        if matches:
            self.require_draft(matches[0])
            release = self._api(f"{self.endpoint}/{matches[0]['id']}")
            self.require_draft(release)
            release = self._api(f"{self.endpoint}/{release['id']}", draft, method="PATCH")
        else:
            release = self._api(self.endpoint, draft, method="POST")
        self.require_draft(release)
        endpoint = f"{self.endpoint}/{release['id']}"
        self.require_draft(self._api(endpoint))
        self._api(endpoint, {"draft": False, "prerelease": self.prerelease}, method="PATCH")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository")
    parser.add_argument("--version", required=True)
    parser.add_argument("--prerelease", choices=("true", "false"), default="false")
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        ReleasePublisher.verify_versions(Path.cwd(), args.version)
        if not args.verify_only:
            if not args.repository or args.notes is None:
                raise ValueError("Repository and notes are required for publication")
            ReleasePublisher(args.repository, args.version, args.prerelease == "true", args.notes).publish()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Cannot publish release: {error}\n")
