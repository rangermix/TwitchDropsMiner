"""Upload verified helper assets to a draft, then publish the complete release."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from prepare_helper_release import HelperRelease


class HelperPublisher:
    def __init__(self, repository: str, version: str, prerelease: bool, assets: Path, notes: Path):
        # Reuse the preparer's filename/version contract; publication never extracts archives.
        self.version = HelperRelease(assets, assets, version).version
        self.repository, self.prerelease, self.assets, self.notes = repository, prerelease, assets, notes
        self.tag = f"v{self.version}"
        self.endpoint = f"repos/{repository}/releases"

    @staticmethod
    def _gh(*args: str, payload: dict | None = None) -> str:
        return subprocess.run(
            ["gh", *args], input=json.dumps(payload) if payload is not None else None,
            check=True, capture_output=True, text=True,
        ).stdout

    def _api(self, endpoint: str, payload: dict | None = None, *, method: str = "GET"):
        args = ["api", endpoint, "--method", method]
        if payload is not None:
            args.extend(["--input", "-"])
        return json.loads(self._gh(*args, payload=payload))

    def _local_assets(self) -> dict[str, tuple[int, str]]:
        expected = {f"tdm-login-helper-{self.version}-{p}.tar.gz" for p in HelperRelease.PLATFORMS}
        paths = list(self.assets.iterdir())
        if {p.name for p in paths} != expected | {"SHA256SUMS"}:
            raise ValueError("Expected exactly four archives and SHA256SUMS")
        metadata = {}
        for path in paths:
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Expected nonempty regular release assets")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            metadata[path.name] = (path.stat().st_size, f"sha256:{digest}")
        expected_lines = {f"{metadata[name][1][7:]}  {name}" for name in expected}
        lines = (self.assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if len(lines) != len(expected) or set(lines) != expected_lines:
            raise ValueError("Release archive checksum mismatch")
        return metadata

    def _require_draft(self, release: dict) -> None:
        if release.get("draft") is not True or release.get("tag_name") != self.tag:
            raise ValueError("Release is already published or has a different tag; refusing to modify it")

    def publish(self) -> None:
        metadata = self._local_assets()
        body = self.notes.read_text(encoding="utf-8")
        # Paginated lookup distinguishes absence from a failed/auth-denied API request.
        pages = json.loads(self._gh("api", self.endpoint + "?per_page=100", "--paginate", "--slurp"))
        matches = [release for page in pages for release in page if release.get("tag_name") == self.tag]
        if len(matches) > 1:
            raise ValueError("Multiple releases have the requested tag")
        draft = {"tag_name": self.tag, "name": f"Release {self.version}",
                 "body": body, "draft": True, "prerelease": self.prerelease}
        if matches:
            self._require_draft(matches[0])
            release = self._api(f"{self.endpoint}/{matches[0]['id']}")
            self._require_draft(release)
            release = self._api(f"{self.endpoint}/{release['id']}", draft, method="PATCH")
        else:
            release = self._api(self.endpoint, draft, method="POST")
        self._require_draft(release)
        endpoint = f"{self.endpoint}/{release['id']}"
        self._gh("release", "upload", self.tag,
                 *(str(self.assets / name) for name in sorted(metadata)),
                 "--repo", self.repository, "--clobber")
        verified = self._api(endpoint)
        self._require_draft(verified)
        assets = verified.get("assets", [])
        if len(assets) != len(metadata) or {a.get("name") for a in assets} != set(metadata):
            raise ValueError("Remote release asset set is incomplete or unexpected")
        for asset in assets:
            if asset.get("state") != "uploaded" or (
                asset.get("size"), asset.get("digest")
            ) != metadata[asset["name"]]:
                raise ValueError("Remote release asset size or digest does not match")
        self._api(endpoint, {"draft": False, "prerelease": self.prerelease}, method="PATCH")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--prerelease", required=True, choices=("true", "false"))
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--notes", required=True, type=Path)
    args = parser.parse_args()
    try:
        HelperPublisher(args.repository, args.version, args.prerelease == "true", args.assets, args.notes).publish()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Cannot publish helper release: {error}\n")
    print("Published release after verifying all five uploaded assets.")
