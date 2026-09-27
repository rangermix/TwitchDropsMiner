"""Release archives and workflow boundaries, without publishing any release."""

import fnmatch
import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/prepare_helper_release.py"
PLATFORMS = ("linux-x64", "macos-arm64", "macos-x64", "windows-x64")


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def make_archive(directory, platform, *, member_name=None, mode=0o755, link=False, empty=False):
    executable = "tdm-login-helper.exe" if platform == "windows-x64" else "tdm-login-helper"
    path = directory / f"tdm-login-helper-{platform}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, contents in ((member_name or executable, b"" if empty else b"packaged binary"), ("LICENSE", b"MIT license")):
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            member.mode = mode
            if link and name != "LICENSE":
                member.type = tarfile.SYMTYPE
                member.linkname = "/outside"
            archive.addfile(member, io.BytesIO(contents))
    return path


@pytest.fixture
def archives(tmp_path):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    for platform in PLATFORMS:
        make_archive(directory, platform)
    return directory


def prepare(archives, output, version="1.4.0-rc.1"):
    return subprocess.run([sys.executable, str(SCRIPT), "--artifacts", str(archives),
        "--output", str(output), "--version", version], capture_output=True, text=True)


def test_release_archives_are_complete_versioned_and_checksummed(archives, tmp_path):
    output = tmp_path / "release"
    result = prepare(archives, output)
    assert result.returncode == 0, result.stderr
    expected = {f"tdm-login-helper-1.4.0-rc.1-{p}.tar.gz" for p in PLATFORMS}
    assert {p.name for p in output.iterdir()} == expected | {"SHA256SUMS"}
    for line in (output / "SHA256SUMS").read_text().splitlines():
        digest, filename = line.split("  ")
        assert filename in expected
        expected.remove(filename)
        assert digest == hashlib.sha256((output / filename).read_bytes()).hexdigest()
    assert not expected
    for platform in PLATFORMS:
        assert (archives / f"tdm-login-helper-{platform}.tar.gz").read_bytes() == (
            output / f"tdm-login-helper-1.4.0-rc.1-{platform}.tar.gz").read_bytes()


@pytest.mark.parametrize("failure", ["missing", "extra", "unsafe_member", "link", "permissions", "invalid_tar", "empty"])
def test_invalid_release_set_fails_before_creating_output(archives, tmp_path, failure):
    path = archives / "tdm-login-helper-linux-x64.tar.gz"
    if failure == "missing":
        path.unlink()
    elif failure == "extra":
        (archives / "unexpected.json").write_text("not an allowed artifact")
    elif failure == "invalid_tar":
        path.write_bytes(b"invalid archive")
    else:
        make_archive(archives, "linux-x64", member_name="../session.json" if failure == "unsafe_member" else None,
                     link=failure == "link", mode=0o644 if failure == "permissions" else 0o755,
                     empty=failure == "empty")
    output = tmp_path / "release"
    result = prepare(archives, output)
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("version", ["../escape", "1.2.3\nunsafe", "", "main"])
def test_release_version_cannot_escape_output(archives, tmp_path, version):
    result = prepare(archives, tmp_path / "release", version)
    assert result.returncode != 0
    assert not (tmp_path / "release").exists()


def test_release_publisher_requires_all_native_builds_and_exact_source():
    release = workflow("github-release.yml")
    native = workflow("login-helper.yml")
    assert release["permissions"] == {"contents": "read"}
    assert native["permissions"] == {"contents": "read"}
    assert "workflow_call" in native["on"]
    assert "pull_request_target" not in native["on"]
    build = release["jobs"]["build-helpers"]
    assert build["uses"] == "./.github/workflows/login-helper.yml"
    assert build["with"]["source-ref"] == "${{ needs.prepare-release.outputs.sha }}"
    publish = release["jobs"]["create-github-release"]
    assert set(publish["needs"]) == {"prepare-release", "build-helpers"}
    assert publish["permissions"] == {"contents": "write"}
    release_step = next(step for step in publish["steps"] if "publish_helper_release.py" in step.get("run", ""))
    assert release_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert '--prerelease "$IS_PRERELEASE" --assets release-assets --notes release_notes.md' in release_step["run"]
    assert {item["platform"] for item in native["jobs"]["build"]["strategy"]["matrix"]["include"]} == set(PLATFORMS)
    assert native["jobs"]["prepare-assets"]["needs"] == "build"
    download = next(step for step in native["jobs"]["prepare-assets"]["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    pattern = download["with"]["pattern"]
    assert all(fnmatch.fnmatch(f"tdm-login-helper-{platform}", pattern) for platform in PLATFORMS)
    assert not fnmatch.fnmatch("tdm-login-helper-release", pattern)
    for name in ("build", "prepare-assets"):
        checkout = next(step for step in native["jobs"][name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "${{ inputs.source-ref || github.sha }}"
    validation = workflow("validation.yml")
    assert validation["jobs"]["native-helper"]["uses"] == build["uses"]
    assert validation["jobs"]["native-helper"]["with"]["source-ref"] == "${{ github.sha }}"


@pytest.mark.parametrize("tag_state", ["matching", "missing", "different"])
def test_release_tag_guard_rejects_missing_or_different_source(tmp_path, tag_state):
    release = workflow("github-release.yml")
    step = next(step for step in release["jobs"]["prepare-release"]["steps"] if step.get("id") == "revision")
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)
    git("init")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "first")
    if tag_state != "missing":
        git("tag", "v1.4.0")
    if tag_state == "different":
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "second")
    result = subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], cwd=tmp_path,
        env={**os.environ, "RELEASE_VERSION": "1.4.0", "GITHUB_OUTPUT": str(tmp_path / "output")},
        capture_output=True, text=True)
    assert (result.returncode == 0) == (tag_state == "matching"), result.stdout + result.stderr
    if tag_state == "matching":
        assert (tmp_path / "output").read_text().strip() == f"sha={git('rev-parse', 'HEAD').stdout.strip()}"
