"""Source-release guard and immutable published releases after helper retirement."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish_release", ROOT / ".github/scripts/publish_release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def publisher(tmp_path, monkeypatch, *, existing=None, remote=None):
    notes = tmp_path / "notes.md"
    notes.write_text("Release notes")
    obj = module.ReleasePublisher("owner/repo", "2.0.2", False, notes)
    calls = []
    result = {"id": 1, "tag_name": "v2.0.2", "draft": True, "assets": []}

    def gh(*args, payload=None):
        calls.append((args, payload))
        if "--paginate" in args:
            return json.dumps([[existing] if existing else []])
        return json.dumps(remote or result)

    monkeypatch.setattr(obj, "_gh", gh)
    return obj, calls


def test_publication_creates_draft_and_verifies_it_before_publishing(tmp_path, monkeypatch):
    obj, calls = publisher(tmp_path, monkeypatch)
    obj.publish()
    writes = [payload for args, payload in calls if payload is not None]
    assert writes[0]["draft"] is True and writes[-1]["draft"] is False
    assert all("upload" not in args for args, payload in calls)


@pytest.mark.parametrize("existing", [
    {"id": 1, "tag_name": "v2.0.2", "draft": False},
    {"id": 1, "tag_name": "v2.0.2", "draft": True, "assets": [{"name": "legacy-helper"}]},
])
def test_published_or_unexpected_asset_release_is_never_modified(tmp_path, monkeypatch, existing):
    obj, calls = publisher(tmp_path, monkeypatch, existing=existing)
    with pytest.raises(ValueError):
        obj.publish()
    assert len(calls) == 1 and calls[0][1] is None


def test_failed_draft_verification_cannot_publish(tmp_path, monkeypatch):
    obj, calls = publisher(tmp_path, monkeypatch, remote={"id": 1, "tag_name": "wrong", "draft": True})
    with pytest.raises(ValueError):
        obj.publish()
    assert not any(payload and payload.get("draft") is False for args, payload in calls)


def test_source_version_consistency_guard():
    from src.version import __version__

    module.ReleasePublisher.verify_versions(ROOT, __version__)
    with pytest.raises(ValueError):
        module.ReleasePublisher.verify_versions(ROOT, "9.9.9")


def test_workflow_keeps_exact_tag_and_final_job_publishing_boundary():
    workflow = (ROOT / ".github/workflows/github-release.yml").read_text()
    assert 'refs/tags/v${RELEASE_VERSION}^{commit}' in workflow
    assert '"$TAG_COMMIT" != "$SOURCE_COMMIT"' in workflow
    assert "--verify-only" in workflow
    assert workflow.index("contents: write") > workflow.index("create-github-release:")
    assert "login-helper.yml" not in workflow and "download-artifact" not in workflow
    assert not (ROOT / "login_helper.py").exists()
