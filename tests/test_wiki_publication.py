"""Exercise public-guide export and publication without contacting GitHub."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/publish_wiki.py"
PAGES = {
    "README.md": "Home", "installation.md": "Installation",
    "authentication.md": "Authentication", "usage.md": "Using-TDM",
    "dashboard-access.md": "Dashboard-access", "notifications.md": "Telegram-notifications",
    "troubleshooting.md": "Troubleshooting", "credits.md": "Credits",
}
WIKI = "https://github.com/owner/repo/wiki"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "docs").mkdir(parents=True)
    for name in PAGES:
        (root / "docs" / name).write_text(f"# {PAGES[name]}\n", encoding="utf-8")
    (root / "README.md").write_text("# Repository\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("# Contributions\n", encoding="utf-8")
    return root


@pytest.fixture
def wiki(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    return importlib.import_module("publish_wiki")


def test_export_cli_produces_only_the_public_pages_and_sidebar(source, tmp_path):
    output = tmp_path / "export"
    result = subprocess.run([
        sys.executable, str(SCRIPT), "export", "--source", str(source),
        "--repository", "owner/repo", "--output", str(output),
    ], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert {p.name for p in output.iterdir()} == {p + ".md" for p in PAGES.values()} | {"_Sidebar.md"}


def test_rewrites_guide_links_anchors_references_and_repository_files(wiki, source):
    (source / "docs/README.md").write_text(
        '[Install](./installation.md#docker "Setup")\n'
        '[Login][auth]\n[auth]: <authentication.md#fresh-login> "Login"\n'
        '[Source](../README.md#about) [Contribute](../CONTRIBUTING.md)\n'
        '[This section](#heading) [External](https://example.test/a?q=x#y)\n'
        '[Encoded](%75sage.md#games) [Home](README.md)\n', encoding="utf-8",
    )
    home = wiki.WikiExporter(source, "owner/repo").render()["Home.md"]
    assert f'({WIKI}/Installation#docker "Setup")' in home
    assert f'<{WIKI}/Authentication#fresh-login>' in home
    assert "(https://github.com/owner/repo/blob/main/README.md#about)" in home
    assert "(https://github.com/owner/repo/blob/main/CONTRIBUTING.md)" in home
    assert "(#heading)" in home and "(https://example.test/a?q=x#y)" in home
    assert f"({WIKI}/Using-TDM#games)" in home and f"({WIKI}/Home)" in home


def test_generated_pages_link_to_main_sources_and_explain_pull_request_edits(wiki, source):
    pages = wiki.WikiExporter(source, "owner/repo").render()
    for name, page in PAGES.items():
        assert f"[docs/{name}](https://github.com/owner/repo/blob/main/docs/{name})" in pages[page + ".md"]
        assert "open a pull request" in pages[page + ".md"]


def test_markdown_examples_are_unchanged_and_parenthesized_assets_work(wiki, source):
    (source / "image (1).png").write_bytes(b"fake image")
    (source / "docs/README.md").write_text(
        '`[example](missing.md)`\n```md\n[example](../.dev-notes/private.md)\n```\n'
        '~~~markdown\n[example](missing.md)\n~~~\n'
        '![Screenshot](<../image (1).png>)\n', encoding="utf-8",
    )
    home = wiki.WikiExporter(source, "owner/repo").render()["Home.md"]
    assert "`[example](missing.md)`" in home
    assert "[example](../.dev-notes/private.md)" in home
    assert "<https://github.com/owner/repo/blob/main/image%20%281%29.png>" in home


@pytest.mark.parametrize("target", [
    "missing.md", "../missing.md", "../../outside.md", "/etc/passwd",
    "../.dev-notes/private.md", "notes/private.md", "plans/private.md", "%2F..%2FREADME.md",
    "unpublished.md", "../.git/config", "..%2f..%2foutside.md", "file:///etc/passwd",
    "javascript:alert(1)", "//other.test/file.md", "installation.md?raw=1",
])
def test_invalid_links_abort_before_output_mutation(wiki, source, tmp_path, target):
    (source / "docs/README.md").write_text(f"[Invalid]({target})\n", encoding="utf-8")
    for name in ("notes", "plans"):
        (source / "docs" / name).mkdir()
        (source / "docs" / name / "private.md").write_text("private", encoding="utf-8")
    (source / "docs/unpublished.md").write_text("private", encoding="utf-8")
    (source / ".dev-notes").mkdir()
    (source / ".dev-notes/private.md").write_text("private", encoding="utf-8")
    output = tmp_path / "export"
    output.mkdir()
    (output / "Home.md").write_text("old wiki", encoding="utf-8")
    with pytest.raises(wiki.WikiError):
        wiki.WikiExporter(source, "owner/repo").export(output)
    assert list(output.iterdir()) == [output / "Home.md"]
    assert (output / "Home.md").read_text() == "old wiki"


@pytest.mark.parametrize("unsafe", ["missing", "file-symlink", "docs-symlink", "linked-symlink"])
def test_missing_or_symlink_sources_are_rejected_before_output_exists(wiki, source, tmp_path, unsafe):
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    if unsafe == "missing":
        (source / "docs/credits.md").unlink()
    elif unsafe == "file-symlink":
        (source / "docs/credits.md").unlink()
        (source / "docs/credits.md").symlink_to(outside)
    elif unsafe == "docs-symlink":
        (source / "docs").rename(source / "real-docs")
        (source / "docs").symlink_to(source / "real-docs", target_is_directory=True)
    else:
        (source / "public.md").symlink_to(outside)
        (source / "docs/README.md").write_text("[Secret](../public.md)", encoding="utf-8")
    output = tmp_path / "export"
    with pytest.raises(wiki.WikiError):
        wiki.WikiExporter(source, "owner/repo").export(output)
    assert not output.exists()


def test_export_ignores_personal_files_and_preserves_unrelated_wiki_pages(wiki, source, tmp_path):
    for name in ("notes", "plans"):
        (source / "docs" / name).mkdir()
        (source / "docs" / name / "private.md").write_text("do not publish", encoding="utf-8")
    (source / ".dev-notes").mkdir()
    (source / ".dev-notes/private.md").write_text("do not publish", encoding="utf-8")
    output = tmp_path / "wiki"
    output.mkdir()
    (output / "Community.md").write_text("keep this page", encoding="utf-8")
    exporter = wiki.WikiExporter(source, "owner/repo")
    assert len(exporter.export(output)) == 9
    assert exporter.export(output) == ()
    assert (output / "Community.md").read_text() == "keep this page"
    assert len(list(output.iterdir())) == 10
    assert "do not publish" not in "".join(p.read_text() for p in output.iterdir())
    sidebar = (output / "_Sidebar.md").read_text()
    assert all(f"{WIKI}/{page}" in sidebar for page in PAGES.values())


def test_managed_destination_symlink_is_rejected_without_changing_any_file(wiki, source, tmp_path):
    output = tmp_path / "wiki"
    output.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    (output / "Home.md").write_text("original", encoding="utf-8")
    (output / "Credits.md").symlink_to(outside)
    with pytest.raises(wiki.WikiError):
        wiki.WikiExporter(source, "owner/repo").export(output)
    assert (output / "Home.md").read_text() == "original"
    assert outside.read_text() == "private"


def git(*args):
    return subprocess.run(["git", *map(str, args)], capture_output=True, text=True, check=True).stdout.strip()


def commit(path, message):
    git("-C", path, "add", "--all")
    git("-C", path, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", message)
    return git("-C", path, "rev-parse", "HEAD")


@pytest.fixture
def local_repositories(wiki, source, tmp_path, monkeypatch):
    canonical = tmp_path / "canonical.git"
    git("init", "--bare", "--initial-branch=main", canonical)
    git("-C", source, "init", "--initial-branch=main")
    for name in (".github/scripts/publish_wiki.py", ".github/workflows/wiki.yml"):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original publication code\n", encoding="utf-8")
    source_sha = commit(source, "Initial public guides")
    git("-C", source, "remote", "add", "origin", canonical)
    git("-C", source, "push", "origin", "main")
    previous = tmp_path / "previous-source"
    git("clone", canonical, previous)
    remote = tmp_path / "wiki.git"
    git("init", "--bare", remote)
    seed = tmp_path / "initial-wiki"
    git("clone", remote, seed)
    (seed / "Community.md").write_text("community", encoding="utf-8")
    commit(seed, "Initial wiki")
    git("-C", seed, "push", "origin", "HEAD")
    original_run, calls = subprocess.run, []

    def local_run(args, **kwargs):
        calls.append(list(args))
        destinations = {
            "https://github.com/owner/repo.git": str(canonical),
            "https://github.com/owner/repo.wiki.git": str(remote),
        }
        return original_run([destinations.get(arg, arg) for arg in args], **kwargs)

    monkeypatch.setattr(wiki.subprocess, "run", local_run)
    return source, previous, source_sha, remote, calls


@pytest.mark.parametrize("changed_path", [
    "docs/usage.md", ".github/scripts/publish_wiki.py", ".github/workflows/wiki.yml",
])
def test_obsolete_rerun_cannot_roll_back_a_newer_wiki(wiki, local_repositories, tmp_path, monkeypatch, changed_path):
    source, previous, old_sha, remote, calls = local_repositories
    old_export = tmp_path / "old-export"
    wiki.WikiExporter(previous, "owner/repo").export(old_export)
    (source / changed_path).write_text("# New canonical content\n", encoding="utf-8")
    latest_sha = commit(source, "Change publication inputs")
    git("-C", source, "push", "origin", "main")
    new_export = tmp_path / "new-export"
    wiki.WikiExporter(source, "owner/repo").export(new_export)
    monkeypatch.chdir(source)
    assert wiki.WikiPublisher("owner/repo", new_export, tmp_path / "latest-wiki", latest_sha).publish()
    published_head = git("--git-dir", remote, "rev-parse", "HEAD")
    monkeypatch.chdir(previous)
    stale = wiki.WikiPublisher("owner/repo", old_export, tmp_path / "stale-wiki", old_sha)
    assert not stale.publish()
    assert not (tmp_path / "stale-wiki").exists()
    assert stale.obsolete is True
    assert git("--git-dir", remote, "rev-parse", "HEAD") == published_head
    wiki_clones = [call for call in calls if "clone" in call and "https://github.com/owner/repo.wiki.git" in call]
    assert len(wiki_clones) == 1


def test_readme_only_main_commit_does_not_suppress_current_docs(wiki, local_repositories, tmp_path, monkeypatch):
    source, previous, old_sha, remote, calls = local_repositories
    (source / "README.md").write_text("Contributor automation changed the README.\n", encoding="utf-8")
    latest_sha = commit(source, "Update contributor credit only")
    assert latest_sha != old_sha
    git("-C", source, "push", "origin", "main")
    exported = tmp_path / "export"
    wiki.WikiExporter(previous, "owner/repo").export(exported)
    monkeypatch.chdir(previous)
    publisher = wiki.WikiPublisher("owner/repo", exported, tmp_path / "wiki", old_sha)
    assert publisher.publish()
    assert publisher.obsolete is False
    assert git("-C", previous, "rev-parse", "HEAD") == old_sha
    assert "# Using-TDM" in git("--git-dir", remote, "show", "HEAD:Using-TDM.md")
    assert any("https://github.com/owner/repo.git" in call and "fetch" in call for call in calls)


def test_source_sha_mismatch_stops_before_fetch_or_wiki_access(wiki, local_repositories, tmp_path):
    source, _previous, _source_sha, _remote, calls = local_repositories
    exported = tmp_path / "export"
    wiki.WikiExporter(source, "owner/repo").export(exported)
    with pytest.raises(wiki.WikiError, match="source checkout"):
        wiki.WikiPublisher("owner/repo", exported, tmp_path / "wiki", "a" * 40, source=source).publish()
    assert len(calls) == 1 and "rev-parse" in calls[0]
    assert not (tmp_path / "wiki").exists()


def test_canonical_main_fetch_failure_stops_without_wiki_mutation(wiki, local_repositories, tmp_path, monkeypatch):
    source, _previous, source_sha, _remote, calls = local_repositories
    exported = tmp_path / "export"
    wiki.WikiExporter(source, "owner/repo").export(exported)
    original_run, attempts = wiki.subprocess.run, []

    def fail_fetch(args, **kwargs):
        attempts.append(list(args))
        if "fetch" in args:
            raise subprocess.CalledProcessError(1, args, stderr="private remote diagnostic")
        return original_run(args, **kwargs)

    monkeypatch.setattr(wiki.subprocess, "run", fail_fetch)
    with pytest.raises(wiki.WikiError) as error:
        wiki.WikiPublisher("owner/repo", exported, tmp_path / "wiki", source_sha, source=source).publish()
    assert "private" not in str(error.value)
    assert len([args for args in attempts if "fetch" in args]) == 1
    assert not any("clone" in args for args in calls)
    assert not (tmp_path / "wiki").exists()


def test_local_git_publication_preserves_pages_and_second_run_does_not_commit(wiki, local_repositories, tmp_path):
    source, _previous, source_sha, remote, calls = local_repositories
    exported = tmp_path / "export"
    wiki.WikiExporter(source, "owner/repo").export(exported)
    assert wiki.WikiPublisher("owner/repo", exported, tmp_path / "first", source_sha, source=source).publish()
    head = git("--git-dir", remote, "rev-parse", "HEAD")
    assert not wiki.WikiPublisher("owner/repo", exported, tmp_path / "second", source_sha, source=source).publish()
    assert git("--git-dir", remote, "rev-parse", "HEAD") == head
    assert (tmp_path / "second/Community.md").read_text() == "community"
    assert len([call for call in calls if "push" in call]) == 1
    assert not any("--force" in item for call in calls for item in call)
    clone = next(call for call in calls if "clone" in call)
    assert "credential.helper=!gh auth git-credential" in clone
    assert "https://github.com/owner/repo.wiki.git" in clone


@pytest.mark.parametrize("invalid", ["missing", "symlink", "unexpected"])
def test_invalid_export_stops_publisher_before_any_git_operation(wiki, source, tmp_path, monkeypatch, invalid):
    exported = tmp_path / "export"
    wiki.WikiExporter(source, "owner/repo").export(exported)
    if invalid == "missing":
        (exported / "Credits.md").unlink()
    elif invalid == "symlink":
        (exported / "Credits.md").unlink()
        (exported / "Credits.md").symlink_to(source / "README.md")
    else:
        (exported / "private.md").write_text("secret", encoding="utf-8")
    calls = []
    monkeypatch.setattr(wiki.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(wiki.WikiError):
        wiki.WikiPublisher("owner/repo", exported, tmp_path / "clone", "a" * 40).publish()
    assert calls == []
    assert not (tmp_path / "clone").exists()


def test_git_failure_is_redacted_and_never_force_retried(wiki, source, tmp_path, monkeypatch):
    exported = tmp_path / "export"
    wiki.WikiExporter(source, "owner/repo").export(exported)
    calls = []

    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args, stderr="private token in a remote failure")

    monkeypatch.setattr(wiki.subprocess, "run", fail)
    with pytest.raises(wiki.WikiError) as error:
        wiki.WikiPublisher("owner/repo", exported, tmp_path / "clone", "a" * 40).publish()
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_workflow_scopes_credentials_to_main_publication_after_validation():
    workflow = (ROOT / ".github/workflows/wiki.yml").read_text()
    assert "pull_request" not in workflow and "workflow_dispatch:" in workflow
    assert "branches: [main]" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.repository == 'rangermix/TwitchDropsMiner'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "contents: read" in workflow and "contents: write" not in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert workflow.index("publish_wiki.py export") < workflow.index("secrets.PUBLISHER_TOKEN")
    assert workflow.count("secrets.PUBLISHER_TOKEN") == 1
    assert "GH_TOKEN: ${{ secrets.PUBLISHER_TOKEN }}" in workflow
    assert "publish_wiki.py publish" in workflow
    assert '--source . --source-sha "$GITHUB_SHA"' in workflow


def test_checked_in_public_guides_are_publishable(wiki):
    pages = wiki.WikiExporter(ROOT, "rangermix/TwitchDropsMiner").render()
    assert set(pages) == {name + ".md" for name in PAGES.values()} | {"_Sidebar.md"}
