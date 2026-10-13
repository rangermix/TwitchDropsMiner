"""Preserve source/tag verification and immutable releases with native assets."""

import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_source_version_consistency_guard(monkeypatch):
    from src.version import __version__

    monkeypatch.syspath_prepend(str(ROOT / ".github/scripts"))
    module = importlib.import_module("publish_release")
    module.ReleasePublisher.verify_versions(ROOT, __version__)
    with pytest.raises(ValueError):
        module.ReleasePublisher.verify_versions(ROOT, "9.9.9")


def test_workflow_keeps_exact_tag_and_final_job_publishing_boundary():
    workflow = (ROOT / ".github/workflows/github-release.yml").read_text()
    assert 'refs/tags/v${RELEASE_VERSION}^{commit}' in workflow
    assert '"$TAG_COMMIT" != "$SOURCE_COMMIT"' in workflow
    assert "--verify-only" in workflow
    assert workflow.index("contents: write") > workflow.index("create-github-release:")
    assert "needs: [prepare-release, build-helpers]" in workflow
    assert "source-ref: ${{ needs.prepare-release.outputs.sha }}" in workflow
    assert "--assets release-assets" in workflow
    assert (ROOT / "login_helper.py").exists()
