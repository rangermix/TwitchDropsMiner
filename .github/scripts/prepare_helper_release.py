"""Validate this run's native archives and prepare versioned release assets."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import tarfile
from pathlib import Path


class HelperRelease:
    PLATFORMS = ("linux-x64", "macos-arm64", "macos-x64", "windows-x64")

    def __init__(self, artifacts: Path, output: Path, version: str):
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version):
            raise ValueError("Invalid release version")
        self.artifacts, self.output, self.version = artifacts, output, version

    @staticmethod
    def validate_archive(path: Path, platform: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Expected a regular archive: {path.name}")
        executable = "tdm-login-helper.exe" if platform == "windows-x64" else "tdm-login-helper"
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 2 or {item.name for item in members} != {executable, "LICENSE"}:
                raise ValueError(f"Unexpected archive contents: {path.name}")
            for item in members:
                if not item.isfile() or item.size <= 0:
                    raise ValueError(f"Expected nonempty regular archive members: {path.name}")
                if item.name == executable and platform != "windows-x64" and not item.mode & 0o111:
                    raise ValueError(f"Missing executable permission: {path.name}")

    def prepare(self) -> None:
        expected = {f"tdm-login-helper-{platform}.tar.gz" for platform in self.PLATFORMS}
        if {path.name for path in self.artifacts.iterdir()} != expected:
            raise ValueError("Expected exactly the four native helper archives")
        for platform in self.PLATFORMS:
            self.validate_archive(self.artifacts / f"tdm-login-helper-{platform}.tar.gz", platform)
        # Validate the entire set before creating output; never extract downloaded files.
        self.output.mkdir(parents=True, exist_ok=False)
        checksums = []
        for platform in self.PLATFORMS:
            source = self.artifacts / f"tdm-login-helper-{platform}.tar.gz"
            destination = self.output / f"tdm-login-helper-{self.version}-{platform}.tar.gz"
            shutil.copyfile(source, destination)
            with destination.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            checksums.append(f"{digest}  {destination.name}\n")
        (self.output / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        HelperRelease(args.artifacts, args.output, args.version).prepare()
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Cannot prepare native release: {error}\n")
    print("Prepared four versioned helper archives and SHA256SUMS.")
