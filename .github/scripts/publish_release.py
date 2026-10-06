"""Publish a verified tag only after its native helper assets are complete."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path

import tomllib
from prepare_helper_release import HelperRelease


class ReleasePublisher:
    def __init__(self, repository: str, version: str, prerelease: bool, notes: Path, assets: Path):
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
            raise ValueError("Invalid release version")
        self.repository, self.version, self.prerelease, self.notes = repository, version, prerelease, notes
        self.assets = assets
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
        if release.get("draft") is not True or release.get("tag_name") != self.tag:
            raise ValueError("Release is already published or has a different tag; refusing to modify it")

    def local_assets(self) -> dict[str, tuple[int, str]]:
        expected = {f"tdm-login-helper-{self.version}-{platform}.tar.gz" for platform in HelperRelease.PLATFORMS}
        paths = list(self.assets.iterdir())
        if {path.name for path in paths} != expected | {"SHA256SUMS"}:
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
        for platform in HelperRelease.PLATFORMS:
            HelperRelease.validate_archive(self.assets / f"tdm-login-helper-{self.version}-{platform}.tar.gz", platform)
        return metadata

    def require_asset_names(self, release: dict, expected: set[str], *, complete: bool = False) -> None:
        assets = release.get("assets", [])
        names = [asset.get("name") for asset in assets]
        if len(names) != len(set(names)) or not set(names) <= expected or (complete and set(names) != expected):
            raise ValueError("Remote release asset set is incomplete or unexpected")

    def publish(self) -> None:
        metadata = self.local_assets()
        pages = json.loads(self._gh("api", self.endpoint + "?per_page=100", "--paginate", "--slurp"))
        matches = [row for page in pages for row in page if row.get("tag_name") == self.tag]
        if len(matches) > 1:
            raise ValueError("Multiple releases have the requested tag")
        draft = {"tag_name": self.tag, "name": f"Release {self.version}",
                 "body": self.notes.read_text(encoding="utf-8"), "draft": True, "prerelease": self.prerelease}
        if matches:
            self.require_draft(matches[0])
            self.require_asset_names(matches[0], set(metadata))
            release = self._api(f"{self.endpoint}/{matches[0]['id']}")
            self.require_draft(release)
            self.require_asset_names(release, set(metadata))
            release = self._api(f"{self.endpoint}/{release['id']}", draft, method="PATCH")
        else:
            release = self._api(self.endpoint, draft, method="POST")
        self.require_draft(release)
        endpoint = f"{self.endpoint}/{release['id']}"
        current = self._api(endpoint)
        self.require_draft(current)
        self.require_asset_names(current, set(metadata))
        self._gh("release", "upload", self.tag,
                 *(str(self.assets / name) for name in sorted(metadata)),
                 "--repo", self.repository, "--clobber")
        verified = self._api(endpoint)
        self.require_draft(verified)
        self.require_asset_names(verified, set(metadata), complete=True)
        for asset in verified["assets"]:
            if asset.get("state") != "uploaded" or (asset.get("size"), asset.get("digest")) != metadata[asset["name"]]:
                raise ValueError("Remote release asset size or digest does not match")
        self._api(endpoint, {"draft": False, "prerelease": self.prerelease}, method="PATCH")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository")
    parser.add_argument("--version", required=True)
    parser.add_argument("--prerelease", choices=("true", "false"), default="false")
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        ReleasePublisher.verify_versions(Path.cwd(), args.version)
        if not args.verify_only:
            if not args.repository or args.notes is None or args.assets is None:
                raise ValueError("Repository, notes and assets are required for publication")
            ReleasePublisher(args.repository, args.version, args.prerelease == "true", args.notes, args.assets).publish()
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Cannot publish release: {error}\n")
