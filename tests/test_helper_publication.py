"""Exercise release publication ordering with a simulated GitHub CLI, never GitHub writes."""

import copy
import hashlib
import importlib
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeGitHub:
    def __init__(self, *, draft=None, fail_upload=False, corrupt=None):
        self.release = None if draft is None else {
            "id": 42, "tag_name": "v1.4.0-rc.1", "draft": draft, "assets": [],
        }
        self.calls = []
        self.fail_upload = fail_upload
        self.corrupt = corrupt

    def run(self, args, *, check, capture_output, text, input=None):
        assert args[0] == "gh" and check and capture_output and text
        self.calls.append((args, input))
        if args[1:3] == ["release", "upload"]:
            assert self.release["draft"] is True
            paths = args[4:args.index("--repo")]
            for path in paths:
                path = Path(path)
                self.release["assets"] = [a for a in self.release["assets"] if a["name"] != path.name]
                self.release["assets"].append({
                    "name": path.name, "state": "uploaded", "size": path.stat().st_size,
                    "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                })
                if self.fail_upload:
                    raise subprocess.CalledProcessError(1, args, stderr="simulated failed upload")
            return subprocess.CompletedProcess(args, 0, "", "")
        assert args[1] == "api"
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        if "--paginate" in args:
            # Include a non-matching page to ensure lookup does not assume page one.
            result = [[{"tag_name": "v0.9.0", "draft": False}], [self.release] if self.release else []]
        elif method == "POST":
            assert self.release is None
            self.release = {"id": 42, "assets": [], **json.loads(input)}
            assert self.release["draft"] is True
            result = self.release
        elif method == "PATCH":
            self.release.update(json.loads(input))
            result = self.release
        else:
            result = copy.deepcopy(self.release)
            if self.corrupt == "missing":
                result["assets"].pop()
            elif self.corrupt == "digest":
                result["assets"][0]["digest"] = "sha256:" + "0" * 64
            elif self.corrupt == "extra":
                result["assets"].append({"name": "unexpected.zip"})
            elif self.corrupt == "state":
                result["assets"][0]["state"] = "starter"
        return subprocess.CompletedProcess(args, 0, json.dumps(result), "")

    @property
    def publications(self):
        return [json.loads(body) for _, body in self.calls if body and json.loads(body).get("draft") is False]


@pytest.fixture
def publisher(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / ".github/scripts"))
    module = importlib.import_module("publish_helper_release")
    assets = tmp_path / "assets"
    assets.mkdir()
    manifest = []
    for platform in module.HelperRelease.PLATFORMS:
        path = assets / f"tdm-login-helper-1.4.0-rc.1-{platform}.tar.gz"
        path.write_bytes(b"test archive " + platform.encode())
        manifest.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    (assets / "SHA256SUMS").write_text("".join(manifest))
    notes = tmp_path / "notes.md"
    notes.write_text("Test release notes\n")

    def make(fake, *, prerelease=True):
        monkeypatch.setattr(module.subprocess, "run", fake.run)
        return module.HelperPublisher("owner/repo", "1.4.0-rc.1", prerelease, assets, notes)
    return make, assets


@pytest.mark.parametrize("prerelease", [True, False])
def test_release_is_published_only_after_all_five_assets_verify(publisher, prerelease):
    make, _ = publisher
    github = FakeGitHub()
    make(github, prerelease=prerelease).publish()
    assert len(github.release["assets"]) == 5
    assert github.release["draft"] is False
    assert github.release["prerelease"] is prerelease
    assert len(github.publications) == 1
    assert json.loads(github.calls[-1][1])["draft"] is False
    assert github.calls[-2][0][1] == "api"  # Remote verification precedes publication.


@pytest.mark.parametrize("prerelease", [True, False])
def test_failed_upload_stays_draft_and_rerun_can_complete(publisher, prerelease):
    make, _ = publisher
    github = FakeGitHub(fail_upload=True)
    with pytest.raises(subprocess.CalledProcessError):
        make(github, prerelease=prerelease).publish()
    assert github.release["draft"] is True
    assert len(github.release["assets"]) == 1
    assert not github.publications
    github.fail_upload = False
    make(github, prerelease=prerelease).publish()
    assert github.release["draft"] is False
    assert len(github.release["assets"]) == 5
    assert len(github.publications) == 1


def test_published_rerun_refuses_before_any_write(publisher):
    make, _ = publisher
    github = FakeGitHub(draft=False)
    before = copy.deepcopy(github.release)
    with pytest.raises(ValueError, match="already published"):
        make(github).publish()
    assert github.release == before
    assert len(github.calls) == 1
    assert "--paginate" in github.calls[0][0]


@pytest.mark.parametrize("corrupt", ["missing", "digest", "extra", "state"])
def test_invalid_remote_asset_set_remains_draft(publisher, corrupt):
    make, _ = publisher
    github = FakeGitHub(corrupt=corrupt)
    with pytest.raises(ValueError, match="asset"):
        make(github).publish()
    assert github.release["draft"] is True
    assert not github.publications


def test_changed_local_archive_aborts_before_github_access(publisher):
    make, assets = publisher
    next(assets.glob("*.tar.gz")).write_bytes(b"changed archive")
    github = FakeGitHub()
    with pytest.raises(ValueError, match="checksum"):
        make(github).publish()
    assert not github.calls
